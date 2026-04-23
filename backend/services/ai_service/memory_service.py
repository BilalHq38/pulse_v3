from __future__ import annotations

import json
import logging

from core.utils import make_id
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


def _render_messages(messages: list) -> str:
    return "\n".join([f"{message.get('sender_type', '?')}: {message.get('content', '')}" for message in messages])


def _safe_json_loads(value: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


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
    row = await db.fetchrow(
        "SELECT * FROM context_memories "
        "WHERE company_id=$1 AND entity_id=$2 AND memory_type=$3 "
        "AND ($4='' OR convo_id=$4) "
        "ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST LIMIT 1",
        company_id,
        entity_id,
        memory_type,
        convo_id,
    )
    if not row:
        return {}
    payload = dict(row)
    payload["memory_data"] = _safe_json_loads(payload.get("memory_content", ""))
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
    if not db or not company_id or not entity_id:
        return ""
    payload = json.dumps(_json_safe(content), ensure_ascii=True)
    memory_id = make_id()
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
) -> str:
    safe_state = dict(state or {})
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
                "UPDATE customers SET long_term_summary=$1,historical_sentiment=$2,updated_at=NOW() WHERE id=$3",
                payload["summary"],
                payload["overall_sentiment"],
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
        )
    except Exception:
        return "Unable to summarize."


async def summarize_customer_interaction(
    messages: list, customer_info: dict | None = None, db=None, company_id: str = ""
) -> dict:
    if not messages:
        return InteractionSummaryResult().model_dump()
    customer_name = (customer_info or {}).get("name", "Unknown customer")
    rendered_messages = _render_messages(messages)
    prompt = (
        "You are a CRM interaction summarizer.\n"
        f"Task: analyze one customer support conversation for {customer_name}.\n"
        "Input format:\n"
        "- conversation: chronological labeled turns\n"
        "- customer_name: display name only for reference\n"
        "Output format: return ONLY valid JSON with this schema:\n"
        '{"summary":"2-3 sentence summary","topics":["list","of","topics"],'
        '"sentiment_label":"positive|negative|neutral|mixed","key_questions":["questions"],'
        '"products_discussed":["products"],"resolution_status":"resolved|unresolved|escalated|in_progress"}\n'
        "Rules:\n"
        "- Keep topics and products grounded in the conversation.\n"
        "- resolution_status must reflect the current end state, not an optimistic guess.\n"
        f"\nconversation:\n{truncate_text_for_tokens(rendered_messages, 2500)}"
    )
    try:
        return await call_model_json(
            prompt,
            InteractionSummaryResult,
            engine=await _resolve_engine_for_request(db=db, company_id=company_id),
        )
    except Exception:
        return InteractionSummaryResult(
            summary=f"Conversation with {customer_name}: {len(messages)} messages.",
            resolution_status="in_progress",
        ).model_dump()


async def generate_daily_ai_summary(date_str: str, interactions: list, db=None, company_id: str = "") -> dict:
    if not interactions:
        return {
            **DailySummaryResult(total_interactions=0, summary_text="No interactions recorded.").model_dump(),
            "date": date_str,
        }
    total = len(interactions)
    digest = "\n".join(
        [
            f"{index + 1}. {item.get('customer_name', '?')} | {item.get('sentiment_label', '?')} | {item.get('resolution_status', '?')} | {item.get('summary', '')}"  # noqa: E501
            for index, item in enumerate(interactions[:50])
        ]
    )
    prompt = (
        "You are an AI operations analyst for a CRM workspace.\n"
        f"Task: create a daily summary for all customer interactions on {date_str}.\n"
        "Input format:\n"
        "- total_interactions: integer count\n"
        "- digest: compact list of customer interactions with sentiment and status\n"
        "Output format: return ONLY valid JSON with this schema:\n"
        '{"total_interactions":'
        f'{total},"top_topics":["5 topics"],"overall_sentiment":"positive|negative|neutral|mixed",'
        '"highlight_issues":["issues"],"recommendations":["3-5 actions"],"summary_text":"3-4 sentence executive summary"}\n'
        "Rules:\n"
        "- recommendations must be operationally actionable.\n"
        "- highlight_issues should focus on repeated friction, escalations, or process gaps.\n"
        f"\ntotal_interactions: {total}\n"
        f"\ndigest:\n{truncate_text_for_tokens(digest, 3500)}"
    )
    try:
        return {
            **await call_model_json(
                prompt,
                DailySummaryResult,
                engine=await _resolve_engine_for_request(db=db, company_id=company_id, use_pro=True),
                use_pro=True,
            ),
            "date": date_str,
        }
    except Exception:
        return {
            **DailySummaryResult(
                total_interactions=total,
                summary_text=f"{total} interactions on {date_str}.",
            ).model_dump(),
            "date": date_str,
        }


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
