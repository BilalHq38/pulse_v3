from __future__ import annotations

import os
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from shared.auth.dependencies import get_current_user
from shared.schemas.contracts import (
    AnalyzeRequest,
    AnalyzeResponse,
    CombinedRequest,
    CombinedResponse,
    ConversationSummaryRequest,
    DailySummaryRequest,
    InteractionSummaryRequest,
    KnowledgeRequest,
    LeadScoreRequest,
    LeadScoreResponse,
    MemoryUpdateRequest,
    NurtureRequest,
    ProductDescriptionRequest,
    RespondRequest,
    RespondResponse,
)
from shared.service_client import build_internal_headers
from services.ai_service.customer_client import customer_service_client
from services.ai_service.intent import classify_intent
from services.ai_service.llm_client import (
    get_active_llm_engine,
    get_active_llm_engines,
    get_provider_runtime_info,
    validate_live_engine,
)
from services.ai_service.memory_service import (
    generate_daily_ai_summary,
    summarize_conversation,
    summarize_customer_interaction,
    update_customer_memory,
)
from services.ai_service.rag import build_ai_context
from services.ai_service.response_generator import (
    auto_score_and_nurture_lead,
    generate_ai_response,
    generate_combined_ai_analysis,
    generate_lead_score,
    generate_nurture_message,
    generate_product_description,
)
from services.ai_service.sentiment import (
    analyze_sentiment,
    build_sentiment_gate,
    should_auto_escalate,
)

from shared.usage_guard import check_ai_usage_limit

router = APIRouter(dependencies=[Depends(check_ai_usage_limit)])


@router.post("/ai/analyze", response_model=AnalyzeResponse)
async def analyze_message(
    payload: AnalyzeRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "") or payload.company_id
    sentiment = await analyze_sentiment(payload.text, db=db, company_id=company_id)
    intent = await classify_intent(
        payload.text,
        db=db,
        company_id=company_id,
        conversation_context=payload.conversation_context,
    )
    gate = build_sentiment_gate(payload.text, sentiment)
    return AnalyzeResponse(
        sentiment=sentiment,
        intent=intent,
        escalation=gate.get("risk_flags", {}).get("escalating", False)
        or should_auto_escalate(payload.text, sentiment, intent),
    )


@router.post("/ai/respond", response_model=RespondResponse)
async def respond_to_customer(
    payload: RespondRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "") or payload.company_id
    context = list(payload.conversation_context)
    if payload.message and (not context or context[-1].get("content") != payload.message):
        context.append({"sender_type": "customer", "content": payload.message})
    customer_info = dict(payload.customer)
    customer_id = str(customer_info.get("id") or "").strip()
    if customer_id:
        headers = build_internal_headers(
            authorization=request.headers.get("authorization") or request.headers.get("Authorization", ""),
            company_id=company_id,
            user_id=current_user.get("sub", ""),
            user_role=current_user.get("role", ""),
        )
        try:
            profile = await customer_service_client.request(
                "GET",
                f"/api/customers/{customer_id}/profile",
                headers=headers,
            )
            customer_info.update(profile)
        except Exception:
            pass
    sentiment = await analyze_sentiment(payload.message, db=db, company_id=company_id)
    intent = await classify_intent(
        payload.message,
        db=db,
        company_id=company_id,
        conversation_context=context[-12:],
    )
    result = await generate_ai_response(
        conversation_context=context,
        customer_info=customer_info,
        company_id=company_id,
        db=db,
        knowledge_context=payload.knowledge_context,
        long_term_summary=payload.long_term_summary,
        historical_sentiment=payload.historical_sentiment,
        actor_user_id=payload.actor_user_id or current_user.get("sub", ""),
        conversation_id=payload.conversation_id,
        channel=payload.channel,
        observed_sentiment=sentiment,
        observed_intent=intent,
    )
    return RespondResponse(
        reply=result.get("response", ""),
        response=result.get("response", ""),
        confidence=float(result.get("confidence", 0.0) or 0.0),
        sentiment=sentiment,
        intent=intent,
        conversation_sentiment=result.get("conversation_sentiment") or {},
        engine=f"{result.get('provider', '')}:{result.get('model_name', '')}".strip(":"),
        provider=str(result.get("provider") or ""),
        model_name=str(result.get("model_name") or ""),
        llm_id=str(result.get("llm_id", "") or ""),
        agent_id=str(result.get("agent_id", "") or ""),
        agent_type=str(result.get("agent_type", "") or ""),
        attachments=result.get("attachments", []),
        product_images=result.get("product_images", []),
        product_ids=result.get("product_ids", []),
        conversation_stage=str(result.get("conversation_stage") or ""),
        next_action=str(result.get("next_action") or ""),
        intent_shift=bool(result.get("intent_shift")),
        degraded=bool(result.get("degraded")),
        api_error=bool(result.get("api_error")),
        provider_error=result.get("provider_error") or {},
        error_type=str(result.get("error_type") or ""),
        error_reason=str(result.get("error_reason") or ""),
        fallback_used=bool(result.get("fallback_used")),
    )


@router.post("/ai/combined", response_model=CombinedResponse)
async def combined_ai_analysis(
    payload: CombinedRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "") or payload.company_id
    result = await generate_combined_ai_analysis(
        customer_message=payload.customer_message,
        conversation_context=payload.conversation_context,
        customer_info=payload.customer,
        company_id=company_id,
        db=db,
        knowledge_context=payload.knowledge_context,
        long_term_summary=payload.long_term_summary,
        historical_sentiment=payload.historical_sentiment,
        actor_user_id=payload.actor_user_id or current_user.get("sub", ""),
        conversation_id=payload.conversation_id,
        channel=payload.channel,
    )
    return CombinedResponse(**result)


@router.post("/ai/score-lead", response_model=LeadScoreResponse)
async def score_lead(
    payload: LeadScoreRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "") or payload.company_id
    result = await generate_lead_score(payload.lead, db=db, company_id=company_id)
    return LeadScoreResponse(
        score=int(result.get("score", 0) or 0),
        grade=str(result.get("grade", "")),
        reasoning=str(result.get("reasoning", "")),
        next_action=str(result.get("next_action", "")),
        phase=str(result.get("phase", "")),
        nurture_message=str(result.get("nurture_message", "")),
        scoring_status=str(result.get("scoring_status") or "completed"),
        provider=str(result.get("provider") or ""),
        model_name=str(result.get("model_name") or ""),
        error_type=str(result.get("error_type") or ""),
        error_reason=str(result.get("error_reason") or ""),
        fallback_used=bool(result.get("fallback_used")),
    )


@router.post("/ai/nurture")
async def nurture_lead(
    payload: NurtureRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "") or payload.company_id
    return await generate_nurture_message(
        payload.lead,
        payload.stage,
        company_context=payload.company_context,
        db=db,
        company_id=company_id,
    )


@router.post("/ai/lead-auto")
async def auto_nurture_lead(
    payload: LeadScoreRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "") or payload.company_id
    return await auto_score_and_nurture_lead(payload.lead, db=db, company_id=company_id)


@router.post("/ai/memory/update")
async def refresh_customer_memory(
    payload: MemoryUpdateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "") or payload.company_id
    return await update_customer_memory(
        payload.customer_id,
        payload.messages,
        payload.previous_summary,
        payload.recent_sentiment,
        db=db,
        company_id=company_id,
    )


@router.post("/ai/summary/conversation")
async def summarize_conversation_route(
    payload: ConversationSummaryRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "") or payload.company_id
    return {"summary": await summarize_conversation(payload.messages, db=db, company_id=company_id)}


@router.post("/ai/summary/interaction")
async def summarize_interaction_route(
    payload: InteractionSummaryRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "") or payload.company_id
    return await summarize_customer_interaction(
        payload.messages,
        payload.customer,
        db=db,
        company_id=company_id,
    )


@router.post("/ai/summary/daily")
async def summarize_daily_route(
    payload: DailySummaryRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "") or payload.company_id
    return await generate_daily_ai_summary(
        payload.date,
        payload.interactions,
        db=db,
        company_id=company_id,
    )


@router.post("/ai/knowledge")
async def company_knowledge_route(
    payload: KnowledgeRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "") or payload.company_id
    context = await build_ai_context(
        db,
        company_id=company_id,
        current_query=payload.query,
        exclude_product_ids=payload.exclude_product_ids,
        max_products=3,
    )
    return {
        "knowledge": context["knowledge_text"],
        "products": context["products"],
        "product_ids": context["product_ids"],
        "attachments": context["product_attachments"],
    }


@router.post("/ai/product-description")
async def product_description_route(
    payload: ProductDescriptionRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "") or payload.company_id
    engines = await get_active_llm_engines(db, company_id=company_id)
    return {
        "description": await generate_product_description(
            payload.name,
            product_title=payload.product_title,
            product_type=payload.product_type,
            category=payload.category,
            price=payload.price,
            price_currency=payload.price_currency,
            images=payload.images,
            engines=engines,
        )
    }


@router.get("/ai/runtime")
async def ai_runtime(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    company_id = current_user.get("company_id", "")
    engine = await get_active_llm_engine(db, company_id=company_id)
    engine = dict(engine or {})
    ready, reason = get_provider_runtime_info(engine.get("provider", ""))
    try:
        if engine:
            validate_live_engine(engine, require_vision=False)
    except Exception as exc:
        ready = False
        reason = str(exc)
    return {"ready": ready, "reason": reason, "engine": engine}


_VALIDATE_RESPONSE_MAX_CHARS = 16_000

_MODEL_HEALTH_CACHE: dict = {}
_MODEL_HEALTH_CACHE_TTL = max(5.0, float(os.getenv("AI_MODEL_HEALTH_CACHE_TTL_SECONDS", "30") or 30))

_SAFETY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(kill|murder|suicide|self-harm)\b",
        r"\b(bomb|weapon|exploit|hack)\s+(make|build|create)\b",
        r"\b(credit card|ssn|social security)\s*\d",
        r"(?:^|\s)(password|secret)\s*[:=]\s*\S+",
    ]
]


def _validate_response_safety(text: str) -> dict:
    """Check AI response for unsafe content patterns."""
    issues: list[str] = []
    for pattern in _SAFETY_PATTERNS:
        if pattern.search(text or ""):
            issues.append(f"Matched safety pattern: {pattern.pattern}")
    is_safe = len(issues) == 0
    return {"safe": is_safe, "issues": issues}


@router.get("/ai/model-health")
async def model_health(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Deep health check — verifies actual LLM connectivity with a test prompt.
    Result is cached for 30 seconds to avoid hammering the LLM on every probe.
    """
    now = time.monotonic()
    company_id = current_user.get("company_id", "")
    cache_key = company_id or "_global"
    cached = _MODEL_HEALTH_CACHE.get(cache_key)
    cached_at = _MODEL_HEALTH_CACHE.get(f"{cache_key}:at", 0.0)
    if cached is not None and (now - cached_at) < _MODEL_HEALTH_CACHE_TTL:
        return cached

    db = request.app.state.db
    engine = await get_active_llm_engine(db, company_id=company_id)
    engine = dict(engine or {})
    ready, reason = get_provider_runtime_info(engine.get("provider", ""))
    if not ready:
        result = {"healthy": False, "reason": reason, "engine": engine}
        _MODEL_HEALTH_CACHE[cache_key] = result
        _MODEL_HEALTH_CACHE[f"{cache_key}:at"] = now
        return result
    try:
        validate_live_engine(engine, require_vision=False)
        from services.ai_service.llm_client import call_model_text

        test_result = await call_model_text("Reply with exactly: OK", engine=engine)
        model_responding = bool(test_result and test_result.strip())
        result = {
            "healthy": model_responding,
            "reason": "" if model_responding else "Model returned empty response",
            "engine": engine,
            "test_response": test_result[:50] if test_result else "",
        }
    except Exception as exc:
        result = {"healthy": False, "reason": str(exc), "engine": engine}
    _MODEL_HEALTH_CACHE[cache_key] = result
    _MODEL_HEALTH_CACHE[f"{cache_key}:at"] = now
    return result


@router.post("/ai/validate-response")
async def validate_response(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Validate AI response text for safety before delivery."""
    body = await request.json()
    text = str(body.get("text", "") or "").strip()
    if not text:
        return {"safe": True, "issues": [], "text": ""}
    if len(text) > _VALIDATE_RESPONSE_MAX_CHARS:
        raise HTTPException(
            status_code=400, detail=f"Text exceeds maximum length of {_VALIDATE_RESPONSE_MAX_CHARS} characters"
        )
    result = _validate_response_safety(text)
    return {**result, "text": text if result["safe"] else ""}
