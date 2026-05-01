from __future__ import annotations

import asyncio
import logging

from agent_orchestrator.schemas import (
    LeadWorkflowRequest,
    MessageWorkflowRequest,
    WorkflowKind,
    WorkflowOutputs,
    WorkflowResponse,
    WorkflowRouteDecision,
    WorkflowStatus,
)
from core.utils import make_id
from shared.config import (
    ai_fallback_timeout_seconds,
    ai_service_retry_attempts,
    ai_service_timeout_seconds,
    orchestrator_request_timeout_seconds,
    service_name,
    service_urls,
)
from shared.service_client import ServiceClient, build_internal_headers
from services.ai_service.facade import (
    build_sentiment_gate,
    generate_ai_response,
    generate_combined_ai_analysis,
    generate_lead_score,
    generate_nurture_message,
    get_company_knowledge,
    should_auto_escalate,
)
from agent_orchestrator.repository import fetch_company_ai_threshold

logger = logging.getLogger(__name__)

ORCHESTRATOR_CLIENT = ServiceClient(
    service_urls().orchestrator,
    timeout=ai_service_timeout_seconds(),
    retry_attempts=ai_service_retry_attempts(),
    service_name="agent-orchestrator",
)


def _service_role(payload: MessageWorkflowRequest | LeadWorkflowRequest) -> str:
    return str(payload.actor_user_role or "company_agent").strip() or "company_agent"


def _service_user(payload: MessageWorkflowRequest | LeadWorkflowRequest) -> str:
    return str(payload.actor_user_id or "agent-orchestrator-proxy").strip() or "agent-orchestrator-proxy"


def _safe_message_workflow_defaults(
    payload: MessageWorkflowRequest,
    *,
    reason: str,
) -> WorkflowResponse:
    safe_trace_id = str(payload.trace_id or "").strip().replace("-", "") or make_id().replace("-", "")
    return WorkflowResponse(
        workflow_id=str(payload.workflow_id or "").strip() or make_id(),
        trace_id=safe_trace_id,
        workflow_kind=WorkflowKind.MESSAGE,
        status=WorkflowStatus.COMPLETED,
        current_agent="support",
        route=WorkflowRouteDecision(
            current_agent="support",
            next_agent="support",
            decision_mode="timeout_fallback",
            reason=reason,
        ),
        agent_outputs=WorkflowOutputs(
            capture={
                "structured_event": {
                    "entity_type": "message",
                    "message_id": payload.message_id,
                    "conversation_id": payload.conversation_id,
                    "source": payload.source or payload.channel,
                    "channel": payload.channel,
                    "message_text": payload.message_text,
                },
                # Neutral placeholders so webhook DB updates never write NULL to NOT NULL conversation columns.
                "sentiment": {"score": 0.0, "emotion": "neutral", "confidence": 0.0},
                "conversation_sentiment": {"score": 0.0, "sentiment_label": "neutral", "label": "neutral"},
                "intent": {"intent": ""},
            },
            support={
                "response": "",
                "confidence": 0.0,
                "deliver_response": False,
                "escalate": True,
                "escalation_reason": reason,
                "next_action": "manual_review",
                "api_error": True,
            },
            analytics={"queued": False},
        ),
        error=reason,
    )


async def orchestrate_message_workflow(
    payload: MessageWorkflowRequest,
    *,
    authorization: str = "",
    db=None,
) -> WorkflowResponse:
    if service_name("").strip().lower() == "agent-orchestrator-service":
        raise RuntimeError("Use the local orchestrator engine from inside the orchestrator service")
    try:
        response = await asyncio.wait_for(
            ORCHESTRATOR_CLIENT.request(
                "POST",
                "/api/orchestrator/workflows/messages",
                headers=build_internal_headers(
                    authorization=authorization,
                    company_id=payload.company_id,
                    user_id=_service_user(payload),
                    user_role=_service_role(payload),
                ),
                json=payload.model_dump(mode="json"),
            ),
            timeout=orchestrator_request_timeout_seconds(),
        )
        return WorkflowResponse.model_validate(response)
    except asyncio.TimeoutError:
        logger.warning(
            "Orchestrator message workflow timed out company_id=%s conversation_id=%s trace_id=%s timeout_seconds=%s",
            payload.company_id,
            payload.conversation_id,
            payload.trace_id,
            orchestrator_request_timeout_seconds(),
        )
        if db is None:
            return _safe_message_workflow_defaults(
                payload,
                reason="Orchestrator timed out and no local fallback database context was available.",
            )
        try:
            return await asyncio.wait_for(
                _message_workflow_fallback(payload, db=db),
                timeout=ai_fallback_timeout_seconds(),
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Message workflow fallback timed out company_id=%s conversation_id=%s trace_id=%s timeout_seconds=%s",
                payload.company_id,
                payload.conversation_id,
                payload.trace_id,
                ai_fallback_timeout_seconds(),
            )
        except Exception:
            logger.exception(
                "Message workflow fallback failed after orchestrator timeout company_id=%s conversation_id=%s trace_id=%s",
                payload.company_id,
                payload.conversation_id,
                payload.trace_id,
            )
        return _safe_message_workflow_defaults(
            payload,
            reason="Message orchestration timed out and the local AI fallback could not complete in time.",
        )
    except Exception as exc:
        if db is None:
            raise
        logger.warning(
            "Orchestrator message workflow request failed company_id=%s conversation_id=%s trace_id=%s error=%s; trying local AI fallback",
            payload.company_id,
            payload.conversation_id,
            payload.trace_id,
            exc,
        )
        try:
            return await _message_workflow_fallback(payload, db=db)
        except Exception as fallback_exc:
            logger.warning(
                "Local AI message workflow fallback failed (e.g. LLM unavailable) company_id=%s conversation_id=%s trace_id=%s error=%s",
                payload.company_id,
                payload.conversation_id,
                payload.trace_id,
                fallback_exc,
            )
            return _safe_message_workflow_defaults(
                payload,
                reason="Orchestrator unavailable and local AI calls failed; inbound message is still stored for human review.",
            )


async def orchestrate_lead_workflow(
    payload: LeadWorkflowRequest,
    *,
    authorization: str = "",
    db=None,
) -> WorkflowResponse:
    if service_name("").strip().lower() == "agent-orchestrator-service":
        raise RuntimeError("Use the local orchestrator engine from inside the orchestrator service")
    try:
        response = await ORCHESTRATOR_CLIENT.request(
            "POST",
            "/api/orchestrator/workflows/leads",
            headers=build_internal_headers(
                authorization=authorization,
                company_id=payload.company_id,
                user_id=_service_user(payload),
                user_role=_service_role(payload),
            ),
            json=payload.model_dump(mode="json"),
        )
        return WorkflowResponse.model_validate(response)
    except Exception as exc:
        logger.warning(
            "Orchestrator lead workflow request failed company_id=%s lead_id=%s customer_id=%s conversation_id=%s error=%s; trying local fallback=%s",
            payload.company_id,
            payload.lead_id,
            payload.customer_id,
            payload.conversation_id,
            exc,
            db is not None,
        )
        if db is None:
            raise
        return await _lead_workflow_fallback(payload, db=db)


async def _message_workflow_fallback(
    payload: MessageWorkflowRequest,
    *,
    db,
) -> WorkflowResponse:
    combined = await generate_combined_ai_analysis(
        customer_message=payload.message_text,
        conversation_context=payload.conversation_context,
        customer_info=payload.customer,
        company_id=payload.company_id,
        db=db,
        knowledge_context=payload.knowledge_context,
        conversation_id=payload.conversation_id,
        actor_user_id=payload.actor_user_id,
        channel=payload.channel,
    )
    sentiment = dict(combined.get("sentiment") or {})
    conversation_sentiment = dict(combined.get("conversation_sentiment") or {})
    intent = dict(combined.get("intent") or {})
    sentiment_gate = build_sentiment_gate(payload.message_text, sentiment)
    threshold = await fetch_company_ai_threshold(db, payload.company_id)
    support = dict(combined.get("ai_response") or {})
    if not support.get("response"):
        support = await generate_ai_response(
            payload.conversation_context,
            payload.customer,
            company_id=payload.company_id,
            db=db,
            knowledge_context=payload.knowledge_context,
            actor_user_id=payload.actor_user_id,
            conversation_id=payload.conversation_id,
            channel=payload.channel,
            observed_sentiment=sentiment,
            observed_intent=intent,
        )
    lead_candidate = {
        "company_id": payload.company_id,
        "name": str((payload.customer or {}).get("name") or payload.sender_name or ""),
        "email": str((payload.customer or {}).get("email") or ""),
        "phone": str((payload.customer or {}).get("phone") or payload.sender_contact or ""),
        "source": payload.source or payload.channel,
        "status": str((payload.customer or {}).get("lifecycle_stage") or "new"),
        "notes": payload.message_text,
    }
    qualification = await generate_lead_score(
        lead_candidate,
        db=db,
        company_id=payload.company_id,
    )
    confidence = float(support.get("confidence", 0.0) or 0.0)
    escalate = bool(
        not sentiment_gate.get("ai_response_allowed", True)
        or should_auto_escalate(payload.message_text, sentiment=sentiment, intent=intent)
        or confidence < threshold
    )
    support_payload = {
        "response": str(support.get("response") or ""),
        "confidence": confidence,
        "confidence_threshold": threshold,
        "attachments": list(support.get("attachments") or []),
        "product_images": list(support.get("product_images") or []),
        "product_ids": list(support.get("product_ids") or []),
        "provider": str(support.get("provider") or ""),
        "model_name": str(support.get("model_name") or ""),
        "llm_id": str(support.get("llm_id") or ""),
        "deliver_response": bool(support.get("response")) and not escalate,
        "escalate": escalate,
        "escalation_reason": "" if not escalate else "Fallback orchestration requested manual review.",
        "next_action": "manual_review" if escalate else "send_response",
    }
    return WorkflowResponse(
        workflow_id=make_id(),
        trace_id=payload.trace_id.replace("-", "") or make_id().replace("-", ""),
        workflow_kind=WorkflowKind.MESSAGE,
        status=WorkflowStatus.COMPLETED,
        current_agent="support",
        route=WorkflowRouteDecision(
            current_agent="support",
            next_agent="analytics",
            decision_mode="fallback",
            reason="Fallback orchestration used local AI service calls.",
        ),
        agent_outputs=WorkflowOutputs(
            capture={
                "structured_event": {
                    "entity_type": "message",
                    "message_id": payload.message_id,
                    "conversation_id": payload.conversation_id,
                    "source": payload.source or payload.channel,
                    "channel": payload.channel,
                    "message_text": payload.message_text,
                },
                "sentiment": sentiment,
                "conversation_sentiment": conversation_sentiment,
                "intent": intent,
                "sentiment_gate": sentiment_gate,
            },
            qualification={
                "score": int(qualification.get("score", 0) or 0),
                "grade": str(qualification.get("grade") or ""),
                "phase": str(qualification.get("phase") or "awareness"),
                "classification": "fallback_review",
                "lead_status": str(lead_candidate.get("status") or "new"),
                "route_to_support": True,
                "reasoning": str(qualification.get("reasoning") or ""),
                "next_action": str(qualification.get("next_action") or ""),
            },
            support=support_payload,
            analytics={"queued": bool(payload.run_async_analytics)},
        ),
    )


async def _lead_workflow_fallback(
    payload: LeadWorkflowRequest,
    *,
    db,
) -> WorkflowResponse:
    lead = dict(payload.lead or {})
    if not payload.knowledge_context:
        payload = payload.model_copy(
            update={
                "knowledge_context": await get_company_knowledge(
                    db,
                    company_id=payload.company_id,
                    current_query=str(lead.get("notes") or lead.get("name") or ""),
                    top_k=3,
                )
            }
        )
    qualification = await generate_lead_score(
        lead,
        db=db,
        company_id=payload.company_id,
    )
    support = {}
    if payload.auto_support:
        support = await generate_nurture_message(
            lead,
            str(qualification.get("phase") or lead.get("phase") or "awareness"),
            company_context=payload.knowledge_context,
            db=db,
            company_id=payload.company_id,
        )
    return WorkflowResponse(
        workflow_id=make_id(),
        trace_id=payload.trace_id.replace("-", "") or make_id().replace("-", ""),
        workflow_kind=WorkflowKind.LEAD,
        status=WorkflowStatus.COMPLETED,
        current_agent="support" if payload.auto_support else "qualification",
        route=WorkflowRouteDecision(
            current_agent="support" if payload.auto_support else "qualification",
            next_agent="analytics",
            decision_mode="fallback",
            reason="Fallback lead orchestration used local AI service calls.",
        ),
        agent_outputs=WorkflowOutputs(
            capture={
                "structured_event": {
                    "entity_type": "lead",
                    "lead_id": payload.lead_id or str(lead.get("id") or ""),
                    "source": payload.source or lead.get("source", "lead"),
                },
                "structured_lead": lead,
            },
            qualification={
                "score": int(qualification.get("score", 0) or 0),
                "grade": str(qualification.get("grade") or ""),
                "phase": str(qualification.get("phase") or "awareness"),
                "classification": "fallback_review",
                "lead_status": str(lead.get("status") or "new"),
                "route_to_support": bool(payload.auto_support),
                "reasoning": str(qualification.get("reasoning") or ""),
                "next_action": str(qualification.get("next_action") or ""),
                "structured_lead": lead,
            },
            support={
                "response": str(support.get("message") or ""),
                "stage": str(support.get("stage") or ""),
                "deliver_response": bool(support.get("message")),
                "next_action": "queue_nurture" if support.get("message") else "review",
            },
            analytics={"queued": bool(payload.run_async_analytics)},
        ),
    )
