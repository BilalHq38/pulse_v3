from __future__ import annotations

import json
import logging

from core.utils import make_id
from shared.cache import get_cache_client
from services.ai_service.common import (
    DailySummaryResult,
    InteractionSummaryResult,
    MemoryUpdateResult,
    _json_safe,
    truncate_text_for_tokens,
    utc_now_iso,
)
from services.ai_service.llm_client import _resolve_engine_for_request, call_model_json, call_model_text

logger = logging.getLogger(__name__)
_MEMORY_DEDUP_CACHE = get_cache_client(namespace="ai-memory-dedup")
_MEMORY_DEDUP_TABLE_READY = False
_CONVERSATION_SCOPED_MEMORY_TYPES = {"last_ai_response", "shown_products", "conversation_state"}


def _render_messages(messages: list) -> str:
    return "\n".join([f"{message.get('sender_type', '?')}: {message.get('content', '')}" for message in messages])


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _compact_message_text(value, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip()


def _fallback_interaction_summary(messages: list, customer_info: dict | None = None) -> dict:
    customer_name = (customer_info or {}).get("name") or "Unknown customer"
    visible_messages = [item for item in (messages or []) if str(item.get("content") or "").strip()]
    total_messages = len(visible_messages)
    latest_customer = next(
        (
            item
            for item in reversed(visible_messages)
            if str(item.get("sender_type") or "").strip().lower() == "customer"
        ),
        visible_messages[-1] if visible_messages else {},
    )
    latest_text = _compact_message_text(latest_customer.get("content", ""))
    intent_values = [
        str(item.get("intent_type") or "").strip()
        for item in visible_messages
        if str(item.get("intent_type") or "").strip()
    ]
    topics = list(dict.fromkeys(intent_values))[:5]
    scores = [
        _safe_float(item.get("sentiment_score"), 0.0)
        for item in visible_messages
        if item.get("sentiment_score") is not None
    ]
    avg_sentiment = round(sum(scores) / len(scores), 4) if scores else 0.0
    if avg_sentiment > 0.2:
        sentiment_label = "positive"
    elif avg_sentiment < -0.2:
        sentiment_label = "negative"
    else:
        sentiment_label = "neutral"
    lowered = " ".join(str(item.get("content") or "").lower() for item in visible_messages[-5:])
    resolution_status = "in_progress"
    if any(term in lowered for term in ("human", "agent", "escalate")):
        resolution_status = "escalated"
    elif any(term in lowered for term in ("thanks", "thank you", "resolved", "done")):
        resolution_status = "resolved"
    elif latest_customer:
        resolution_status = "unresolved"
    summary_text = (
        f"Conversation with {customer_name}: {total_messages} messages."
        if not latest_text
        else f"Conversation with {customer_name}: latest customer message was '{latest_text}'."
    )
    payload = InteractionSummaryResult(
        summary=summary_text,
        topics=topics,
        sentiment_label=sentiment_label,
        resolution_status=resolution_status,
    ).model_dump()
    payload.update(
        {
            "total_messages": total_messages,
            "avg_sentiment": avg_sentiment,
            "escalated": resolution_status == "escalated",
            "ai_handled": True,
            "source": "local_aggregate",
        }
    )
    return payload


def _fallback_daily_summary(date_str: str, interactions: list) -> dict:
    total_interactions = len(interactions or [])
    total_messages = sum(_safe_int(item.get("total_messages")) for item in interactions or [])
    weighted_sentiment = 0.0
    weight_total = 0
    for item in interactions or []:
        weight = max(_safe_int(item.get("total_messages"), 1), 1)
        weighted_sentiment += _safe_float(item.get("avg_sentiment"), 0.0) * weight
        weight_total += weight
    avg_sentiment = round(weighted_sentiment / weight_total, 4) if weight_total else 0.0
    escalations = sum(1 for item in interactions or [] if item.get("escalated"))
    ai_handled_count = sum(1 for item in interactions or [] if item.get("ai_handled"))
    if avg_sentiment > 0.2:
        overall_sentiment = "positive"
    elif avg_sentiment < -0.2:
        overall_sentiment = "negative"
    else:
        overall_sentiment = "neutral"
    highlight_issues = [f"{escalations} escalated interactions"] if escalations else []
    summary_text = (
        "No interactions recorded."
        if not total_interactions
        else (
            f"{total_interactions} interactions on {date_str}; "
            f"{total_messages} messages, {escalations} escalations, average sentiment {avg_sentiment:.2f}."
        )
    )
    payload = DailySummaryResult(
        total_interactions=total_interactions,
        overall_sentiment=overall_sentiment,
        highlight_issues=highlight_issues,
        summary_text=summary_text,
    ).model_dump()
    payload.update(
        {
            "date": date_str,
            "total_messages": total_messages,
            "avg_sentiment": avg_sentiment,
            "escalations": escalations,
            "ai_handled_count": ai_handled_count,
            "source": "local_aggregate",
        }
    )
    return payload


def _safe_json_loads(value: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _has_conversation_memory_scope(company_id: str, entity_id: str, convo_id: str, memory_type: str) -> bool:
    if memory_type not in _CONVERSATION_SCOPED_MEMORY_TYPES:
        return bool(company_id and entity_id)
    if company_id and entity_id and convo_id:
        return True
    logger.info(
        "personalization_memory_disabled reason=missing_scope company_id_present=%s entity_id_present=%s convo_id_present=%s memory_type=%s",
        bool(company_id),
        bool(entity_id),
        bool(convo_id),
        memory_type,
    )
    return False


async def get_latest_context_memory(
    db,
    company_id: str,
    entity_id: str,
    *,
    memory_type: str,
    convo_id: str = "",
) -> dict:
    if not db or not company_id or not entity_id:
        return {}
    if not _has_conversation_memory_scope(company_id, entity_id, convo_id, memory_type):
        return {}
    convo_filter = "AND convo_id=$4" if memory_type in _CONVERSATION_SCOPED_MEMORY_TYPES else "AND ($4='' OR convo_id=$4)"
    row = await db.fetchrow(
        "SELECT * FROM context_memories "
        "WHERE company_id=$1 AND entity_id=$2 AND memory_type=$3 "
        f"{convo_filter} "
        "ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST LIMIT 1",
        company_id,
        entity_id,
        memory_type,
        convo_id,
    )
    if not row:
        logger.debug(
            "context_memory_read company_id=%s entity_id=%s memory_type=%s convo_id=%s hit=false",
            company_id,
            entity_id,
            memory_type,
            convo_id or "-",
        )
        return {}
    payload = dict(row)
    payload["memory_data"] = _safe_json_loads(payload.get("memory_content", ""))
    logger.debug(
        "context_memory_read company_id=%s entity_id=%s memory_type=%s convo_id=%s hit=true memory_id=%s",
        company_id,
        entity_id,
        memory_type,
        convo_id or "-",
        str(payload.get("id") or ""),
    )
    return payload


async def store_context_memory(
    db,
    company_id: str,
    entity_id: str,
    *,
    entity_type: str = "customer",
    memory_type: str,
    content: dict,
    convo_id: str = "",
    relevance_score: float = 0.8,
) -> str:
    global _MEMORY_DEDUP_TABLE_READY
    if not db or not company_id or not entity_id:
        return ""
    if not _has_conversation_memory_scope(company_id, entity_id, convo_id, memory_type):
        return ""
    memory_id = make_id()
    message_id = str((content or {}).get("message_id") or "").strip()
    if message_id:
        dedupe_key = f"{company_id}:{entity_id}:{convo_id}:{message_id}:{memory_type}"
        if not _MEMORY_DEDUP_TABLE_READY:
            try:
                await db.execute(
                    "CREATE TABLE IF NOT EXISTS context_memory_dedup ("
                    "company_id TEXT NOT NULL, convo_id TEXT NOT NULL DEFAULT '', "
                    "message_id TEXT NOT NULL, memory_type TEXT NOT NULL, memory_id TEXT NOT NULL DEFAULT '', "
                    "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
                    "PRIMARY KEY(company_id, convo_id, message_id, memory_type))"
                )
                _MEMORY_DEDUP_TABLE_READY = True
            except Exception as exc:
                logger.debug("memory dedupe table bootstrap skipped: %s", exc)
        if _MEMORY_DEDUP_TABLE_READY:
            existing_dedupe_id = await db.fetchval(
                "SELECT memory_id FROM context_memory_dedup "
                "WHERE company_id=$1 AND convo_id=$2 AND message_id=$3 AND memory_type=$4 LIMIT 1",
                company_id,
                convo_id,
                message_id,
                memory_type,
            )
            if existing_dedupe_id:
                logger.debug(
                    "memory_write_skipped_duplicate company_id=%s convo_id=%s message_id=%s memory_type=%s existing_memory_id=%s source=dedup_table",
                    company_id,
                    convo_id or "-",
                    message_id,
                    memory_type,
                    str(existing_dedupe_id),
                )
                return str(existing_dedupe_id)
            claimed_id = await db.fetchval(
                "INSERT INTO context_memory_dedup(company_id,convo_id,message_id,memory_type,memory_id,created_at) "
                "VALUES($1,$2,$3,$4,$5,NOW()) "
                "ON CONFLICT(company_id,convo_id,message_id,memory_type) DO NOTHING RETURNING memory_id",
                company_id,
                convo_id,
                message_id,
                memory_type,
                memory_id,
            )
            if not claimed_id:
                existing_dedupe_id = await db.fetchval(
                    "SELECT memory_id FROM context_memory_dedup "
                    "WHERE company_id=$1 AND convo_id=$2 AND message_id=$3 AND memory_type=$4 LIMIT 1",
                    company_id,
                    convo_id,
                    message_id,
                    memory_type,
                )
                if existing_dedupe_id:
                    logger.debug(
                        "memory_write_skipped_duplicate company_id=%s convo_id=%s message_id=%s memory_type=%s existing_memory_id=%s source=dedup_table_race",
                        company_id,
                        convo_id or "-",
                        message_id,
                        memory_type,
                        str(existing_dedupe_id),
                    )
                    return str(existing_dedupe_id)
        try:
            existing_id = await db.fetchval(
                "SELECT id FROM context_memories "
                "WHERE company_id=$1 AND entity_id=$2 AND convo_id=$3 AND memory_type=$4 "
                "AND (memory_content::jsonb ->> 'message_id')=$5 "
                "ORDER BY updated_at DESC NULLS LAST LIMIT 1",
                company_id,
                entity_id,
                convo_id,
                memory_type,
                message_id,
            )
        except Exception as exc:
            logger.debug("memory persistent dedupe lookup skipped: %s", exc)
            existing_id = ""
        if existing_id:
            if _MEMORY_DEDUP_TABLE_READY:
                await db.execute(
                    "UPDATE context_memory_dedup SET memory_id=$5 "
                    "WHERE company_id=$1 AND convo_id=$2 AND message_id=$3 AND memory_type=$4",
                    company_id,
                    convo_id,
                    message_id,
                    memory_type,
                    str(existing_id),
                )
            await _MEMORY_DEDUP_CACHE.set_json(
                dedupe_key,
                {"seen": True, "memory_id": str(existing_id)},
                ttl_seconds=86400,
            )
            logger.debug(
                "memory_write_skipped_duplicate company_id=%s convo_id=%s message_id=%s memory_type=%s existing_memory_id=%s source=db",
                company_id,
                convo_id or "-",
                message_id,
                memory_type,
                str(existing_id),
            )
            return str(existing_id)
        cached_duplicate = await _MEMORY_DEDUP_CACHE.get_json(dedupe_key)
        if isinstance(cached_duplicate, dict):
            logger.debug(
                "memory_write_skipped_duplicate company_id=%s convo_id=%s message_id=%s memory_type=%s existing_memory_id=%s source=cache",
                company_id,
                convo_id or "-",
                message_id,
                memory_type,
                str(cached_duplicate.get("memory_id") or ""),
            )
            return str(cached_duplicate.get("memory_id") or "")
    payload = json.dumps(_json_safe(content), ensure_ascii=True)
    result_id = await db.fetchval(
        "INSERT INTO context_memories "
        "(id, company_id, convo_id, entity_id, entity_type, memory_content, memory_type, relevance_score, created_at, updated_at) "  # noqa: E501
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NOW(),NOW()) "
        "ON CONFLICT (company_id, entity_id, entity_type, memory_type, convo_id) "
        "DO UPDATE SET "
        "  memory_content = EXCLUDED.memory_content, "
        "  relevance_score = EXCLUDED.relevance_score, "
        "  updated_at = NOW() "
        "RETURNING id",
        memory_id,
        company_id,
        convo_id,
        entity_id,
        entity_type,
        payload,
        memory_type,
        relevance_score,
    )
    logger.debug(
        "context_memory_write company_id=%s entity_id=%s entity_type=%s memory_type=%s convo_id=%s memory_id=%s",
        company_id,
        entity_id,
        entity_type,
        memory_type,
        convo_id or "-",
        str(result_id or memory_id),
    )
    if message_id:
        if _MEMORY_DEDUP_TABLE_READY:
            await db.execute(
                "INSERT INTO context_memory_dedup(company_id,convo_id,message_id,memory_type,memory_id,created_at) "
                "VALUES($1,$2,$3,$4,$5,NOW()) "
                "ON CONFLICT(company_id,convo_id,message_id,memory_type) DO UPDATE SET memory_id=EXCLUDED.memory_id",
                company_id,
                convo_id,
                message_id,
                memory_type,
                str(result_id or memory_id),
            )
        await _MEMORY_DEDUP_CACHE.set_json(
            dedupe_key,
            {"seen": True, "memory_id": str(result_id or memory_id)},
            ttl_seconds=86400,
        )
    return str(result_id) if result_id else memory_id


async def get_last_ai_response_memory(db, company_id: str, entity_id: str, *, convo_id: str = "") -> str:
    memory = await get_last_ai_response_context(db, company_id, entity_id, convo_id=convo_id)
    return str(memory.get("response") or "").strip()


async def get_last_ai_response_context(db, company_id: str, entity_id: str, *, convo_id: str = "") -> dict:
    memory = await get_latest_context_memory(
        db, company_id, entity_id, memory_type="last_ai_response", convo_id=convo_id
    )
    return dict(memory.get("memory_data", {}) or {})


async def remember_ai_response(
    db,
    company_id: str,
    entity_id: str,
    response_text: str,
    *,
    convo_id: str = "",
    prompt: str = "",
    intent: dict | None = None,
    sentiment: dict | None = None,
    product_ids: list[str] | None = None,
    response_style: str = "",
    message_id: str = "",
) -> str:
    return await store_context_memory(
        db,
        company_id,
        entity_id,
        memory_type="last_ai_response",
        convo_id=convo_id,
        content={
            "response": response_text,
            "prompt": prompt,
            "intent": intent or {},
            "sentiment": sentiment or {},
            "product_ids": [str(item).strip() for item in (product_ids or []) if str(item).strip()],
            "response_style": response_style,
            "message_id": message_id,
            "created_at": utc_now_iso(),
        },
    )


async def get_last_shown_product_ids(db, company_id: str, entity_id: str, *, convo_id: str = "") -> list[str]:
    memory = await get_latest_context_memory(db, company_id, entity_id, memory_type="shown_products", convo_id=convo_id)
    values = memory.get("memory_data", {}).get("product_ids", [])
    return [str(item).strip() for item in values if str(item).strip()]


async def remember_shown_products(
    db,
    company_id: str,
    entity_id: str,
    product_ids: list[str],
    *,
    convo_id: str = "",
    message_id: str = "",
) -> str:
    if not product_ids:
        return ""
    existing_ids = await get_last_shown_product_ids(db, company_id, entity_id, convo_id=convo_id)
    merged_ids = list(
        dict.fromkeys(
            [
                *[str(item).strip() for item in existing_ids if str(item).strip()],
                *[str(item).strip() for item in product_ids if str(item).strip()],
            ]
        )
    )[:20]
    return await store_context_memory(
        db,
        company_id,
        entity_id,
        memory_type="shown_products",
        convo_id=convo_id,
        content={
            "product_ids": merged_ids,
            "message_id": message_id,
            "created_at": utc_now_iso(),
        },
    )


async def get_conversation_state_memory(db, company_id: str, entity_id: str, *, convo_id: str = "") -> dict:
    memory = await get_latest_context_memory(
        db,
        company_id,
        entity_id,
        memory_type="conversation_state",
        convo_id=convo_id,
    )
    return dict(memory.get("memory_data", {}) or {})


async def remember_conversation_state(
    db,
    company_id: str,
    entity_id: str,
    state: dict,
    *,
    convo_id: str = "",
    message_id: str = "",
) -> str:
    safe_state = dict(state or {})
    if message_id:
        safe_state["message_id"] = message_id
    safe_state["updated_at"] = utc_now_iso()
    return await store_context_memory(
        db,
        company_id,
        entity_id,
        memory_type="conversation_state",
        convo_id=convo_id,
        content=safe_state,
    )


async def update_customer_memory(
    customer_id: str,
    new_messages: list,
    previous_summary: str,
    recent_sentiment: dict,
    db=None,
    company_id: str = "",
) -> dict:
    recent_conversation = _render_messages(new_messages)
    prompt = (
        "You are a CRM memory consolidation model.\n"
        "Task: update a persistent customer memory profile by merging the previous summary with the newest conversation.\n"
        "Input format:\n"
        "- previous_summary: rolling customer memory\n"
        "- recent_conversation: newest labeled conversation turns\n"
        "- recent_sentiment: latest sentiment payload\n"
        "Output format: return ONLY valid JSON that matches the MemoryUpdateResult schema.\n"
        "Rules:\n"
        "- Preserve durable facts such as preferences, products discussed, unresolved issues, commitments, and tone.\n"
        "- Do not invent future actions or facts not supported by the input.\n"
        "- Keep the summary concise but decision-useful for later replies.\n"
        f"\nprevious_summary:\n{truncate_text_for_tokens(previous_summary or '', 900)}\n"
        f"\nrecent_conversation:\n{truncate_text_for_tokens(recent_conversation, 1800)}\n"
        f"\nrecent_sentiment:\n{json.dumps(recent_sentiment or {}, ensure_ascii=True)}"
    )
    try:
        result = await call_model_json(
            prompt,
            MemoryUpdateResult,
            engine=await _resolve_engine_for_request(db=db, company_id=company_id, use_pro=True),
            use_pro=True,
        )
    except Exception:
        result = MemoryUpdateResult(summary=previous_summary or "No memory yet.").model_dump()
    payload = {
        "customer_id": customer_id,
        "summary": result["summary"],
        "key_facts": result.get("key_facts", []),
        "overall_sentiment": result.get("overall_sentiment", "neutral"),
        "sentiment_reasoning": result.get("sentiment_reasoning", ""),
        "updated_at": utc_now_iso(),
    }
    if db and customer_id:
        try:
            await db.execute(
                "UPDATE customers SET long_term_summary=$1,historical_sentiment=$2,updated_at=NOW() "
                "WHERE company_id=$3 AND id=$4",
                payload["summary"],
                payload["overall_sentiment"],
                company_id,
                customer_id,
            )
            if company_id:
                await store_context_memory(
                    db,
                    company_id,
                    customer_id,
                    memory_type="customer_profile",
                    content=payload,
                )
            logger.info(
                "customer_memory_updated company_id=%s customer_id=%s summary_chars=%s key_fact_count=%s",
                company_id or "",
                customer_id,
                len(payload["summary"] or ""),
                len(payload.get("key_facts", []) or []),
            )
        except Exception as exc:
            logger.warning("Failed to persist customer memory: %s", exc)
    return payload


async def summarize_conversation(messages: list, db=None, company_id: str = "") -> str:
    text = "\n".join([f"{m.get('sender_type')}: {m.get('content')}" for m in messages])
    try:
        return await call_model_text(
            "You are a CRM conversation summarizer.\n"
            "Task: produce a brief summary that preserves the customer's goal, the main answer given, and any unresolved follow-up.\n"
            "Output format: return plain text only in 2-3 sentences.\n"
            f"\nconversation:\n{text}",
            engine=await _resolve_engine_for_request(db=db, company_id=company_id),
            call_purpose="conversation_summary",
            function_name="summarize_conversation",
            agent_name="analytics",
            max_provider_attempts=1,
            allow_provider_fallback=False,
        )
    except Exception:
        return "Unable to summarize."


async def summarize_customer_interaction(
    messages: list, customer_info: dict | None = None, db=None, company_id: str = ""
) -> dict:
    return _fallback_interaction_summary(messages, customer_info)


async def generate_daily_ai_summary(date_str: str, interactions: list, db=None, company_id: str = "") -> dict:
    return _fallback_daily_summary(date_str, interactions)


__all__ = [
    "generate_daily_ai_summary",
    "get_conversation_state_memory",
    "get_last_ai_response_context",
    "get_last_ai_response_memory",
    "get_last_shown_product_ids",
    "get_latest_context_memory",
    "remember_conversation_state",
    "remember_ai_response",
    "remember_shown_products",
    "store_context_memory",
    "summarize_conversation",
    "summarize_customer_interaction",
    "update_customer_memory",
]
