from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

from shared.config import (
    ai_service_retry_attempts,
    ai_service_timeout_seconds,
    allow_local_ai_fallback,
    service_name,
    service_urls,
)
from shared.service_client import ServiceClient, build_internal_headers
from services.ai_service.embedding_service import (
    batch_store_embeddings,
    generate_embedding,
    index_knowledge_base,
    search_similar_embeddings,
    store_embedding,
)
from services.ai_service.llm_client import (
    # Re-exported for legacy direct imports; no confirmed external callers in current grep.
    call_gemini,
    call_gemini_json,
    call_model_json,
    call_model_text,
    call_with_engines,
    engine_supports_vision,
    get_active_llm_engine as _local_get_active_llm_engine,
    get_active_llm_engines as _local_get_active_llm_engines,
    get_provider_runtime_info,
    validate_live_engine,
)
from services.ai_service.memory_service import (
    generate_daily_ai_summary as _local_generate_daily_ai_summary,
    summarize_conversation as _local_summarize_conversation,
    summarize_customer_interaction as _local_summarize_customer_interaction,
    update_customer_memory as _local_update_customer_memory,
)
from services.ai_service.rag import get_company_knowledge as _local_get_company_knowledge
from services.ai_service.response_generator import (
    auto_score_and_nurture_lead as _local_auto_score_and_nurture_lead,
    # Used by: routers.customers customer profile enrichment.
    build_safe_lead_ai_context,
    build_system_prompt,
    calculate_churn_risk,
    generate_ai_response as _local_generate_ai_response,
    generate_combined_ai_analysis as _local_generate_combined_ai_analysis,
    generate_lead_score as _local_generate_lead_score,
    generate_nurture_message as _local_generate_nurture_message,
    generate_product_description as _local_generate_product_description,
)
from services.ai_service.common import latest_customer_message
from services.ai_service.common import (
    DailySummaryResult,
    InteractionSummaryResult,
    IntentResult,
    LeadScoreResult,
    SentimentResult,
    _json_safe,
    utc_now_iso,
)
from services.ai_service.sentiment import (
    analyze_local_sentiment,
    analyze_sentiment as _local_analyze_sentiment,
    build_sentiment_gate,
    normalize_sentiment_score,
    sentiment_to_percentage,
    should_auto_escalate,
)
from services.ai_service.intent import classify_intent as _local_classify_intent

logger = logging.getLogger(__name__)
AI_BASE_URL = service_urls().ai
AI_CLIENT = ServiceClient(
    AI_BASE_URL,
    timeout=ai_service_timeout_seconds(),
    retry_attempts=ai_service_retry_attempts(),
    service_name="ai-service",
)

_INTERNAL_AI_USER_ID = "ai-service-internal"


def _prefer_local_impl() -> bool:
    # Enforce a strict service boundary: local AI execution is disabled by default
    # and can only be enabled intentionally for ai-service emergency scenarios.
    return False


def _allow_local_fallback() -> bool:
    return allow_local_ai_fallback() and service_name("").strip().lower() == "ai-service"


async def _call_remote_ai(
    method: str,
    path: str,
    *,
    company_id: str = "",
    user_id: str = "",
    user_role: str = "",
    json_body: dict[str, Any] | None = None,
) -> Any:
    resolved_company_id = (company_id or "").strip()
    resolved_user_id = (user_id or "").strip() or _INTERNAL_AI_USER_ID
    resolved_user_role = (user_role or "").strip() or ("company_agent" if resolved_company_id else "super_admin")
    safe_json_body = _json_safe(json_body) if json_body is not None else None
    return await AI_CLIENT.request(
        method,
        path,
        headers=build_internal_headers(
            company_id=resolved_company_id,
            user_id=resolved_user_id,
            user_role=resolved_user_role,
        ),
        json=safe_json_body,
    )


def _safe_sentiment_default(text: str = "") -> dict:
    result = SentimentResult(
        score=0.02,
        emotion="neutral",
        confidence=0.2,
        sentiment_label="neutral",
        keywords=[],
        emotion_breakdown={
            "joy": 0.02,
            "anger": 0.0,
            "sadness": 0.0,
            "fear": 0.0,
            "surprise": 0.01,
        },
    ).model_dump()
    result["normalized_score"] = normalize_sentiment_score(float(result.get("score", 0) or 0))
    result["percentage"] = sentiment_to_percentage(float(result.get("score", 0) or 0))
    result["label"] = (
        "Negative" if result["percentage"] < 40 else "Positive" if result["percentage"] > 60 else "Neutral"
    )
    result["source"] = "unavailable"
    result["text"] = text
    return result


def _sentiment_result_usable(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    try:
        score = float(payload.get("score"))
    except Exception:
        return False
    return abs(score) >= 0.01


def _safe_intent_default(text: str = "") -> dict:
    result = IntentResult(
        intent="general_question",
        confidence=0.0,
        entities={},
        urgency="low",
    ).model_dump()
    result["source"] = "unavailable"
    result["text"] = text
    return result


def _safe_ai_reply_default(message_text: str, customer_info: dict | None = None) -> str:
    text = " ".join(str(message_text or "").split()).strip()
    name = str((customer_info or {}).get("name") or "there").strip() or "there"
    lower = text.lower()
    if any(token in lower for token in ("refund", "cancel", "complaint", "angry", "issue", "problem", "broken")):
        return (
            f"I understand, {name}. I can help with this right now. "
            "Share your order or account detail and I will guide the fastest resolution path."
        )
    if any(token in lower for token in ("price", "buy", "recommend", "product", "available")):
        return (
            f"Thanks, {name}. I can help with product options. "
            "Which product or service are you most interested in? I can pull up the most relevant options."
        )
    if text:
        return (
            f"Thanks, {name}. I received your message. "
            "I can help with product details, support issues, and next steps. "
            "Tell me the key outcome you want and I will take it from there."
        )
    return "Thanks for reaching out. I am here to help with support, products, and next steps."


def _safe_lead_score_default(lead_data: dict) -> dict:
    result = LeadScoreResult(
        score=0,
        grade="cold",
        reasoning="AI service unavailable. Lead was not scored; review manually.",
        next_action="Review lead manually after AI provider is available.",
        phase="awareness",
    ).model_dump()
    result.update(
        {
            "grade": "deferred",
            "scoring_status": "failed",
            "error_type": "ai_service_unavailable",
            "error_reason": "AI service unavailable",
            "fallback_used": True,
        }
    )
    return result


def _safe_nurture_default(lead_data: dict, stage: str) -> dict:
    lead_name = str(lead_data.get("name", "there") or "there").strip() or "there"
    templates = [
        f"Hi {lead_name}, thanks for your interest. I'm following up to see what matters most to you.",
        f"Hi {lead_name}, I wanted to check in and see if you want a quick recommendation or more details.",
        f"Hi {lead_name}, just touching base to help with the next step whenever you're ready.",
    ]
    template_index = int(hashlib.sha1(f"{lead_name}:{stage}".encode("utf-8")).hexdigest(), 16) % len(templates)
    return {"message": templates[template_index], "stage": stage}


def _safe_memory_update_default(
    customer_id: str,
    previous_summary: str,
    recent_sentiment: dict,
) -> dict:
    overall = str((recent_sentiment or {}).get("emotion") or "neutral")
    if overall not in {"negative", "neutral", "positive", "mixed"}:
        overall = "neutral"
    return {
        "customer_id": customer_id,
        "summary": previous_summary or "No memory yet.",
        "key_facts": [],
        "overall_sentiment": overall,
        "sentiment_reasoning": "AI service unavailable.",
        "updated_at": utc_now_iso(),
    }


def _safe_interaction_summary_default(
    messages: list,
    customer_info: dict | None,
) -> dict:
    customer_name = (customer_info or {}).get("name", "Unknown customer")
    return InteractionSummaryResult(
        summary=f"Conversation with {customer_name}: {len(messages)} messages.",
        resolution_status="in_progress",
    ).model_dump()


def _safe_daily_summary_default(date_str: str, interactions: list) -> dict:
    total = len(interactions)
    return {
        **DailySummaryResult(
            total_interactions=total,
            overall_sentiment="neutral",
            summary_text=f"{total} interactions on {date_str}.",
        ).model_dump(),
        "date": date_str,
    }


def _safe_product_description_default(
    name: str,
    product_title: str = "",
    product_type: str = "",
    category: str = "",
    price: str = "",
    price_currency: str = "USD",
) -> str:
    descriptor = product_title or product_type or category or "product"
    templates = [
        f"{name} is a thoughtfully designed {descriptor} made to fit your needs.",
        f"{name} offers a clean, reliable take on a {descriptor}, with practical value built in.",
        f"{name} brings a refined {descriptor} experience with a balanced, customer-friendly feel.",
    ]
    index = int(hashlib.sha1(f"{name}:{descriptor}:{price_currency}".encode("utf-8")).hexdigest(), 16) % len(templates)
    return templates[index]


def _fallback_reason(exc: Exception) -> str:
    if hasattr(exc, "detail") and getattr(exc, "detail"):
        return str(getattr(exc, "detail"))
    return exc.__class__.__name__


async def analyze_sentiment(text: str, db=None, company_id: str = "", **kwargs) -> dict:
    if _prefer_local_impl():
        return await _local_analyze_sentiment(text, db=db, company_id=company_id, **kwargs)
    try:
        result = await _call_remote_ai(
            "POST",
            "/api/ai/analyze",
            company_id=company_id,
            json_body={"text": text, "company_id": company_id},
        )
        sentiment = result.get("sentiment", {}) if isinstance(result, dict) else {}
        if not _sentiment_result_usable(sentiment):
            raise ValueError("Remote sentiment payload was empty or zero-scored")
        return sentiment
    except Exception as exc:
        logger.warning(
            "ai_service sentiment remote failed company_id=%s error=%s",
            company_id or "",
            exc.__class__.__name__,
        )
        return await _local_analyze_sentiment(
            text,
            db=db,
            company_id=company_id,
            **kwargs,
        )


async def classify_intent(text: str, db=None, company_id: str = "", **kwargs) -> dict:
    if _prefer_local_impl():
        return await _local_classify_intent(text, db=db, company_id=company_id, **kwargs)
    try:
        return await _call_remote_ai(
            "POST",
            "/api/ai/classify",
            company_id=company_id,
            json_body={
                "text": text,
                "company_id": company_id,
                "conversation_context": kwargs.get("conversation_context", []),
                "previous_intent": kwargs.get("previous_intent", ""),
            },
        )
    except Exception as exc:
        logger.warning(
            "ai_service intent remote failed company_id=%s error=%s",
            company_id or "",
            exc.__class__.__name__,
        )
        return await _local_classify_intent(
            text,
            db=db,
            company_id=company_id,
            **kwargs,
        )


async def generate_ai_response(
    conversation_context: list,
    customer_info: dict | None = None,
    knowledge_context: str = "",
    company_id: str | None = None,
    db=None,
    long_term_summary: str = "",
    historical_sentiment: str = "",
    actor_user_id: str = "",
    **kwargs,
):
    if _prefer_local_impl():
        return await _local_generate_ai_response(
            conversation_context,
            customer_info=customer_info,
            knowledge_context=knowledge_context,
            company_id=company_id,
            db=db,
            long_term_summary=long_term_summary,
            historical_sentiment=historical_sentiment,
            actor_user_id=actor_user_id,
            **kwargs,
        )
    try:
        latest_message = kwargs.get("message") or latest_customer_message(conversation_context)
        response = await _call_remote_ai(
            "POST",
            "/api/ai/respond",
            company_id=company_id or (customer_info or {}).get("company_id", ""),
            user_id=actor_user_id,
            json_body={
                "message": latest_message,
                "company_id": company_id or (customer_info or {}).get("company_id", ""),
                "conversation_context": conversation_context,
                "customer": customer_info or {},
                "knowledge_context": knowledge_context,
                "long_term_summary": long_term_summary,
                "historical_sentiment": historical_sentiment,
                "actor_user_id": actor_user_id,
                "conversation_id": kwargs.get("conversation_id", ""),
                "channel": kwargs.get("channel", "web_chat"),
                "system_prompt": kwargs.get("system_prompt", ""),
                "extra_context": kwargs.get("extra_context", ""),
                "company_info": kwargs.get("company_info") or {},
                "context_package": kwargs.get("context_package") or {},
            },
        )
        return {
            "response": response.get("reply", ""),
            "attachments": response.get("attachments", []),
            "product_images": response.get("product_images", []),
            "product_ids": response.get("product_ids", []),
            "provider": response.get("engine", "").split(":", 1)[0] if response.get("engine") else "",
            "model_name": response.get("engine", "").split(":", 1)[1] if ":" in response.get("engine", "") else "",
            "confidence": float(response.get("confidence", 0.9) or 0.0),
            "llm_id": str(response.get("llm_id", "") or ""),
            "agent_id": str(response.get("agent_id", "") or ""),
            "agent_type": str(response.get("agent_type", "") or ""),
            "conversation_stage": str(response.get("conversation_stage", "") or ""),
            "next_action": str(response.get("next_action", "") or ""),
            "intent_shift": bool(response.get("intent_shift")),
            "conversation_sentiment": dict(response.get("conversation_sentiment") or {}),
        }
    except Exception as exc:
        logger.warning(
            "ai_service response remote failed company_id=%s error=%s",
            company_id or (customer_info or {}).get("company_id", ""),
            exc.__class__.__name__,
        )
        return await _local_generate_ai_response(
            conversation_context,
            customer_info=customer_info,
            knowledge_context=knowledge_context,
            company_id=company_id,
            db=db,
            long_term_summary=long_term_summary,
            historical_sentiment=historical_sentiment,
            actor_user_id=actor_user_id,
            **kwargs,
        )


async def generate_combined_ai_analysis(
    customer_message: str,
    conversation_context: list,
    customer_info: dict | None = None,
    company_id: str | None = None,
    db=None,
    long_term_summary: str = "",
    historical_sentiment: str = "",
    knowledge_context: str = "",
    **kwargs,
) -> dict:
    if _prefer_local_impl():
        return await _local_generate_combined_ai_analysis(
            customer_message,
            conversation_context,
            customer_info=customer_info,
            company_id=company_id,
            db=db,
            long_term_summary=long_term_summary,
            historical_sentiment=historical_sentiment,
            knowledge_context=knowledge_context,
            **kwargs,
        )
    try:
        return await _call_remote_ai(
            "POST",
            "/api/ai/combined",
            company_id=company_id or (customer_info or {}).get("company_id", ""),
            user_id=kwargs.get("actor_user_id", ""),
            json_body={
                "customer_message": customer_message,
                "company_id": company_id or (customer_info or {}).get("company_id", ""),
                "conversation_context": conversation_context,
                "customer": customer_info or {},
                "lead": kwargs.get("lead") or {},
                "knowledge_context": knowledge_context,
                "long_term_summary": long_term_summary,
                "historical_sentiment": historical_sentiment,
                "actor_user_id": kwargs.get("actor_user_id", ""),
                "conversation_id": kwargs.get("conversation_id", ""),
                "channel": kwargs.get("channel", "web_chat"),
            },
        )
    except Exception as exc:
        logger.warning(
            "ai_service combined analysis remote failed company_id=%s error=%s",
            company_id or (customer_info or {}).get("company_id", ""),
            exc.__class__.__name__,
        )
        return await _local_generate_combined_ai_analysis(
            customer_message,
            conversation_context,
            customer_info=customer_info,
            company_id=company_id,
            db=db,
            long_term_summary=long_term_summary,
            historical_sentiment=historical_sentiment,
            knowledge_context=knowledge_context,
            **kwargs,
        )


async def generate_lead_score(
    lead_data: dict,
    db=None,
    company_id: str = "",
    count_against_budget: bool = True,
) -> dict:
    safe_lead = build_safe_lead_ai_context(lead_data, include_next_action=False)
    if _prefer_local_impl():
        return await _local_generate_lead_score(
            safe_lead,
            db=db,
            company_id=company_id,
            count_against_budget=count_against_budget,
        )
    try:
        return await _call_remote_ai(
            "POST",
            "/api/ai/score-lead",
            company_id=company_id or lead_data.get("company_id", ""),
            json_body={"lead": safe_lead, "company_id": company_id or lead_data.get("company_id", "")},
        )
    except Exception as exc:
        logger.warning(
            "ai_service lead score remote failed company_id=%s error=%s",
            company_id or lead_data.get("company_id", ""),
            exc.__class__.__name__,
        )
        if _allow_local_fallback():
            return await _local_generate_lead_score(
                safe_lead,
                db=db,
                company_id=company_id,
                count_against_budget=count_against_budget,
            )
        return _safe_lead_score_default(safe_lead)


async def generate_nurture_message(
    lead_data: dict,
    stage: str,
    company_context: str = "",
    db=None,
    company_id: str = "",
) -> dict:
    safe_lead = build_safe_lead_ai_context(lead_data, include_next_action=True)
    if _prefer_local_impl():
        return await _local_generate_nurture_message(
            safe_lead,
            stage,
            company_context=company_context,
            db=db,
            company_id=company_id,
        )
    try:
        return await _call_remote_ai(
            "POST",
            "/api/ai/nurture",
            company_id=company_id or lead_data.get("company_id", ""),
            json_body={
                "lead": safe_lead,
                "stage": stage,
                "company_id": company_id or lead_data.get("company_id", ""),
                "company_context": company_context,
            },
        )
    except Exception as exc:
        logger.warning(
            "ai_service nurture remote failed company_id=%s error=%s",
            company_id or lead_data.get("company_id", ""),
            exc.__class__.__name__,
        )
        if _allow_local_fallback():
            return await _local_generate_nurture_message(
                safe_lead,
                stage,
                company_context=company_context,
                db=db,
                company_id=company_id,
            )
        return _safe_nurture_default(safe_lead, stage)


async def auto_score_and_nurture_lead(lead_data: dict, db=None, company_id: str | None = None) -> dict:
    safe_lead = build_safe_lead_ai_context(lead_data, include_next_action=True)
    if _prefer_local_impl():
        return await _local_auto_score_and_nurture_lead(safe_lead, db=db, company_id=company_id)
    try:
        return await _call_remote_ai(
            "POST",
            "/api/ai/lead-auto",
            company_id=company_id or lead_data.get("company_id", ""),
            json_body={"lead": safe_lead, "company_id": company_id or lead_data.get("company_id", "")},
        )
    except Exception as exc:
        logger.warning(
            "ai_service auto nurture remote failed company_id=%s error=%s",
            company_id or lead_data.get("company_id", ""),
            exc.__class__.__name__,
        )
        if _allow_local_fallback():
            return await _local_auto_score_and_nurture_lead(safe_lead, db=db, company_id=company_id)
        score = _safe_lead_score_default(safe_lead)
        nurture = _safe_nurture_default(safe_lead, score.get("phase", "awareness"))
        return {**score, "nurture_message": nurture["message"]}


async def update_customer_memory(
    customer_id: str,
    new_messages: list,
    previous_summary: str,
    recent_sentiment: dict,
    db=None,
    company_id: str = "",
) -> dict:
    if _prefer_local_impl():
        return await _local_update_customer_memory(
            customer_id,
            new_messages,
            previous_summary,
            recent_sentiment,
            db=db,
            company_id=company_id,
        )
    try:
        return await _call_remote_ai(
            "POST",
            "/api/ai/memory/update",
            company_id=company_id,
            json_body={
                "customer_id": customer_id,
                "company_id": company_id,
                "messages": new_messages,
                "previous_summary": previous_summary,
                "recent_sentiment": recent_sentiment,
            },
        )
    except Exception as exc:
        logger.warning(
            "ai_service memory update remote failed company_id=%s error=%s",
            company_id or "",
            exc.__class__.__name__,
        )
        if _allow_local_fallback():
            return await _local_update_customer_memory(
                customer_id,
                new_messages,
                previous_summary,
                recent_sentiment,
                db=db,
                company_id=company_id,
            )
        return _safe_memory_update_default(customer_id, previous_summary, recent_sentiment)


async def summarize_conversation(messages: list, db=None, company_id: str = "") -> str:
    if _prefer_local_impl():
        return await _local_summarize_conversation(messages, db=db, company_id=company_id)
    try:
        result = await _call_remote_ai(
            "POST",
            "/api/ai/summary/conversation",
            company_id=company_id,
            json_body={"messages": messages, "company_id": company_id},
        )
        return str(result.get("summary") or "")
    except Exception as exc:
        logger.warning(
            "ai_service conversation summary remote failed company_id=%s error=%s",
            company_id or "",
            exc.__class__.__name__,
        )
        if _allow_local_fallback():
            return await _local_summarize_conversation(messages, db=db, company_id=company_id)
        return "Unable to summarize."


async def summarize_customer_interaction(
    messages: list, customer_info: dict | None = None, db=None, company_id: str = ""
) -> dict:
    return await _local_summarize_customer_interaction(messages, customer_info, db=db, company_id=company_id)


async def generate_daily_ai_summary(date_str: str, interactions: list, db=None, company_id: str = "") -> dict:
    return await _local_generate_daily_ai_summary(date_str, interactions, db=db, company_id=company_id)


async def generate_product_description(
    name: str,
    product_title: str = "",
    product_type: str = "",
    category: str = "",
    price: str = "",
    price_currency: str = "USD",
    images: list | None = None,
    engines: Optional[list[dict]] = None,
    company_id: str = "",
    db=None,
) -> str:
    if _prefer_local_impl():
        return await _local_generate_product_description(
            name,
            company_id=company_id,
            product_title=product_title,
            product_type=product_type,
            category=category,
            price=price,
            price_currency=price_currency,
            images=images,
            engines=engines,
            db=db,
        )
    try:
        result = await _call_remote_ai(
            "POST",
            "/api/ai/product-description",
            company_id=company_id,
            json_body={
                "name": name,
                "product_title": product_title,
                "product_type": product_type,
                "category": category,
                "price": price,
                "price_currency": price_currency,
                "images": images or [],
                "company_id": company_id,
            },
        )
        return str(result.get("description") or "")
    except Exception as exc:
        logger.warning(
            "ai_service product description remote failed name=%s error=%s",
            name,
            exc.__class__.__name__,
        )
        if _allow_local_fallback():
            return await _local_generate_product_description(
                name,
                company_id=company_id,
                product_title=product_title,
                product_type=product_type,
                category=category,
                price=price,
                price_currency=price_currency,
                images=images,
                engines=engines,
                db=db,
            )
        return _safe_product_description_default(
            name,
            product_title=product_title,
            product_type=product_type,
            category=category,
            price=price,
            price_currency=price_currency,
        )


async def get_company_knowledge(db, company_id: str | None = None, current_query: str = "", top_k: int = 5) -> str:
    if _prefer_local_impl():
        return await _local_get_company_knowledge(db, company_id=company_id, current_query=current_query, top_k=top_k)
    try:
        result = await _call_remote_ai(
            "POST",
            "/api/ai/knowledge",
            company_id=company_id or "",
            json_body={"company_id": company_id or "", "query": current_query},
        )
        return str(result.get("knowledge") or "")
    except Exception as exc:
        logger.warning(
            "ai_service knowledge remote failed company_id=%s error=%s",
            company_id or "",
            exc.__class__.__name__,
        )
        if _allow_local_fallback():
            return await _local_get_company_knowledge(
                db, company_id=company_id, current_query=current_query, top_k=top_k
            )
        return ""


async def get_active_llm_engines(db, company_id: str = "") -> list[dict]:
    if _prefer_local_impl():
        return await _local_get_active_llm_engines(db, company_id=company_id)
    try:
        result = await _call_remote_ai("GET", "/api/ai/llm-engines", company_id=company_id)
        if isinstance(result, list):
            return [dict(item) for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            engines = result.get("engines") or []
            if isinstance(engines, list):
                return [dict(item) for item in engines if isinstance(item, dict)]
        return []
    except Exception as exc:
        logger.warning(
            "ai_service engines remote failed error=%s",
            exc.__class__.__name__,
        )
        if _allow_local_fallback():
            return await _local_get_active_llm_engines(db, company_id=company_id)
        return []


async def get_active_llm_engine(db, company_id: str = "") -> Optional[dict]:
    if _prefer_local_impl():
        return await _local_get_active_llm_engine(db, company_id=company_id)
    try:
        runtime = await _call_remote_ai("GET", "/api/ai/runtime", company_id=company_id)
        engine = runtime.get("engine", {}) if isinstance(runtime, dict) else {}
        return dict(engine) if engine else None
    except Exception as exc:
        logger.warning(
            "ai_service active engine remote failed error=%s",
            exc.__class__.__name__,
        )
        if _allow_local_fallback():
            return await _local_get_active_llm_engine(db, company_id=company_id)
        return None


__all__ = [
    "analyze_local_sentiment",
    "analyze_sentiment",
    "auto_score_and_nurture_lead",
    "batch_store_embeddings",
    "build_sentiment_gate",
    "build_system_prompt",
    "calculate_churn_risk",
    "call_gemini",
    "call_gemini_json",
    "call_model_json",
    "call_model_text",
    "call_with_engines",
    "classify_intent",
    "engine_supports_vision",
    "generate_ai_response",
    "generate_combined_ai_analysis",
    "generate_daily_ai_summary",
    "generate_embedding",
    "generate_lead_score",
    "generate_nurture_message",
    "generate_product_description",
    "get_active_llm_engine",
    "get_active_llm_engines",
    "get_company_knowledge",
    "get_provider_runtime_info",
    "index_knowledge_base",
    "normalize_sentiment_score",
    "search_similar_embeddings",
    "should_auto_escalate",
    "store_embedding",
    "summarize_conversation",
    "summarize_customer_interaction",
    "update_customer_memory",
    "validate_live_engine",
]
