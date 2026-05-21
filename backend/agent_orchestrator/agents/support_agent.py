from __future__ import annotations

import hashlib
import logging

from agent_orchestrator.agents.base import BaseAgent, WorkflowContextProtocol
from agent_orchestrator.repository import (
    fetch_company_ai_threshold,
    fetch_customer,
)
from agent_orchestrator.schemas import AgentName, AgentRunResult, WorkflowKind
from services.ai_service.facade import (
    should_auto_escalate,
)
from services.ai_service.llm_tracking import log_llm_reuse
from services.ai_service.response_safety import sanitize_ai_response_for_delivery
from services.db_helpers import (
    AI_API_EXHAUSTED_MANUAL_MESSAGE,
    auto_disable_ai_after_failure_fallback,
    is_ai_api_exhaustion_payload,
)

logger = logging.getLogger(__name__)


async def _is_repetitive_response(
    db, conversation_id: str, proposed_response: str
) -> bool:
    recent = await db.fetch(
        """
        SELECT content FROM messages
        WHERE conversation_id = $1 AND sender_type = 'ai'
        ORDER BY created_at DESC
        LIMIT 5
        """,
        conversation_id,
    )
    proposed_clean = proposed_response.strip().lower()
    for row in recent:
        if row["content"].strip().lower() == proposed_clean:
            return True
    return False


def _is_manual_ai_draft_request(request) -> bool:
    metadata = dict(getattr(request, "metadata", {}) or {})
    sources = {
        str(getattr(request, "source", "") or "").strip(),
        str(metadata.get("source") or "").strip(),
    }
    return "manual_ai_respond" in sources


def _manual_ai_safe_fallback_draft() -> str:
    return "Hi, thanks for reaching out. Let me check this and get back to you shortly."


def _company_name_for_safety(request, customer: dict) -> str:
    metadata = dict(getattr(request, "metadata", {}) or {})
    return (
        str(metadata.get("company_name") or metadata.get("business_name") or "").strip()
        or str((customer or {}).get("company_name") or (customer or {}).get("company") or "").strip()
        or "this business"
    )


class SupportAgent(BaseAgent):
    name = AgentName.SUPPORT

    async def execute(self, context: WorkflowContextProtocol) -> AgentRunResult:
        if context.workflow_kind == WorkflowKind.LEAD:
            return await self._handle_lead_support(context)
        return await self._handle_message_support(context)

    async def _handle_message_support(
        self,
        context: WorkflowContextProtocol,
    ) -> AgentRunResult:
        request = context.request
        manual_draft_mode = _is_manual_ai_draft_request(request)
        capture = dict(getattr(context.agent_outputs, "capture", {}) or {})
        qualification = dict(getattr(context.agent_outputs, "qualification", {}) or {})
        # Attempt to load the customer from capture, otherwise fetch from the DB.
        # If the fetch fails (e.g. missing columns like "tags"), log the error and
        # continue with an empty customer to avoid crashing. This prevents
        # knowledge fetch errors due to non-existent DB columns from bubbling up to
        # the user-facing agent.
        try:
            customer = dict(capture.get("customer") or {}) or await fetch_customer(
                context.db, getattr(request, "customer_id", "")
            )
        except Exception as exc:
            logger.warning(
                "fetch_customer_failed workflow_id=%s customer_id=%s error=%s",
                context.workflow_id,
                str(getattr(request, "customer_id", "") or ""),
                exc,
            )
            customer = dict(capture.get("customer") or {})

        latest_text_raw = str(getattr(request, "message_text", "") or "").strip()
        conversation_id = str(getattr(request, "conversation_id", "") or "").strip()
        if context.db and conversation_id:
            ai_disabled = await context.db.fetchval(
                "SELECT COALESCE(ai_auto_paused,FALSE) OR COALESCE(ai_disabled_until > NOW(),FALSE) "
                "FROM conversations WHERE id = $1",
                conversation_id,
            )
            if ai_disabled:
                return self._static_fallback_response(latest_text_raw, customer)
        if not latest_text_raw:
            logger.info(
                "support_response_generation_skipped workflow_id=%s message_id=%s reason=empty_message",
                context.workflow_id,
                str(getattr(request, "message_id", "") or ""),
            )
            return AgentRunResult(
                agent_name=self.name,
                payload={
                    "response": "",
                    "confidence": 0.0,
                    "confidence_threshold": 0.7,
                    "attachments": [],
                    "product_images": [],
                    "product_ids": [],
                    "provider": "none",
                    "model_name": "",
                    "llm_id": "",
                    "knowledge_context": "",
                    "deliver_response": False,
                    "escalate": False,
                    "escalation_reason": "",
                    "next_action": "ignore_empty_message",
                    "qualification": qualification,
                    "reused_prefetched_response": False,
                    "rag_called": False,
                },
            )

        # Extract qualification hint - passed to AI as context, NOT as response.
        qualification_hint_for_ai = ""
        missing = list(qualification.get("missing_fields") or [])
        if missing and not qualification.get("ready_for_scoring", True):
            qualification_hint_for_ai = (
                f"Naturally collect this information if it fits the conversation: {', '.join(missing)}"
            )
        conversation_history = list(getattr(request, "conversation_context", []) or []) or list(
            context.global_memory.conversation_history
        )
        latest_text = str(getattr(request, "message_text", "") or "").strip()
        sentiment = dict(capture.get("sentiment") or {})
        intent = dict(capture.get("intent") or {})
        sentiment_gate = dict(capture.get("sentiment_gate") or {})
        threshold = await fetch_company_ai_threshold(context.db, context.company_id)
        knowledge_context = str(getattr(request, "knowledge_context", "") or "").strip()
        prefetched = dict(capture.get("prefetched_support_response") or {})
        prefetched_response = str(prefetched.get("response") or "").strip()
        prefetched_invalid = bool(prefetched.get("invalid") or (prefetched.get("api_error") and not prefetched_response))
        # If there is a valid prefetched response, reuse it but merge in the latest context
        if prefetched_response and not prefetched_invalid:
            retrieved_knowledge = str(prefetched.get("retrieved_knowledge") or "").strip()
            if not knowledge_context:
                knowledge_context = retrieved_knowledge
            context_package = {
                "system_prompt": str(prefetched.get("system_prompt") or ""),
                "conversation_history": conversation_history[-80:],
                "customer_profile": {
                    "name": customer.get("name"),
                    "lifecycle_stage": customer.get("lifecycle_stage"),
                    "language": customer.get("preferred_language") or customer.get("language"),
                },
                "retrieved_knowledge": retrieved_knowledge,
                "qualification_hint": qualification_hint_for_ai,
                "customer_message": latest_text,
            }
            # Preserve the original prefetched result
            ai_result = dict(prefetched)
            reused_prefetched_response = True
            # Inject system_prompt and retrieved knowledge into the prefetched result so downstream logic
            # has access to the latest context. Without this, prefetched responses return stale canned replies.
            try:
                ai_result["system_prompt"] = context_package.get("system_prompt")
                ai_result["retrieved_knowledge"] = context_package.get("retrieved_knowledge")
                ai_result["qualification_hint"] = context_package.get("qualification_hint")
            except Exception:
                # If context_package is not available or missing keys, ignore silently
                pass
            logger.info(
                "support_prefetched_response_reused workflow_id=%s message_id=%s conversation_id=%s company_id=%s provider=%s model=%s reused_prefetched_response=true",
                context.workflow_id,
                str(getattr(request, "message_id", "") or ""),
                str(getattr(request, "conversation_id", "") or ""),
                context.company_id,
                str(prefetched.get("provider") or ""),
                str(prefetched.get("model_name") or ""),
            )
            log_llm_reuse(
                agent_name=self.name.value,
                function_name="_handle_message_support",
                call_purpose="support_response",
                provider=str(prefetched.get("provider") or ""),
                model=str(prefetched.get("model_name") or ""),
            )
        else:
            reused_prefetched_response = False
            if prefetched:
                logger.warning(
                    "support_prefetched_response_not_reused workflow_id=%s message_id=%s conversation_id=%s company_id=%s keys=%s api_error=%s one_llm_call_enforced=true",
                    context.workflow_id,
                    str(getattr(request, "message_id", "") or ""),
                    str(getattr(request, "conversation_id", "") or ""),
                    context.company_id,
                    sorted(prefetched.keys()),
                    bool(prefetched.get("api_error")),
                )
            ai_result = dict(prefetched)
            if not ai_result:
                ai_result = {
                    "response": "",
                    "confidence": 0.0,
                    "attachments": [],
                    "product_images": [],
                    "product_ids": [],
                    "provider": "none",
                    "model_name": "",
                    "next_action": "manual_review",
                    "fallback_used": True,
                    "fallback_reason": "missing_prefetched_unified_response",
                }
        ai_response_text = str(ai_result.get("response") or "").strip()
        if (
            ai_response_text
            and context.db
            and conversation_id
            and not ai_result.get("api_error")
            and await _is_repetitive_response(context.db, conversation_id, ai_response_text)
        ):
            logger.info(
                "support_repetitive_response_retry_skipped workflow_id=%s conversation_id=%s reason=one_llm_call_enforced",
                context.workflow_id,
                conversation_id,
            )
        provider_failure_after_generation = bool(
            ai_result.get("api_error")
            and is_ai_api_exhaustion_payload(ai_result)
        )
        if manual_draft_mode and not ai_response_text and not provider_failure_after_generation:
            ai_result["response"] = _manual_ai_safe_fallback_draft()
            ai_result["confidence"] = ai_result.get("confidence") or 0.0
            ai_result["fallback_used"] = True
            ai_result["fallback_reason"] = "empty_model_response"
            ai_response_text = str(ai_result.get("response") or "").strip()
        if ai_response_text:
            safe_response_text, safety_blocked, safety_issues = sanitize_ai_response_for_delivery(
                ai_response_text,
                company_name=_company_name_for_safety(request, customer),
            )
            ai_result["response"] = safe_response_text
            ai_result["safety_validated"] = True
            ai_result["safety_blocked"] = safety_blocked
            ai_result["safety_issues"] = safety_issues
            ai_response_text = safe_response_text
            if safety_blocked:
                ai_result["fallback_used"] = True
                ai_result["fallback_reason"] = "response_safety_blocked"
        confidence = float(ai_result.get("confidence", 0.0) or 0.0)
        static_fallback_served = bool(ai_result.get("static_fallback_served"))
        provider_failure = bool(
            ai_result.get("api_error")
            and is_ai_api_exhaustion_payload(ai_result)
        )
        escalate_for_signal = should_auto_escalate(
            latest_text,
            sentiment=sentiment,
            intent=intent,
        )
        low_confidence = confidence < threshold
        escalate = bool(
            not sentiment_gate.get("ai_response_allowed", True) or escalate_for_signal
        )
        escalation_reason = ""
        if not sentiment_gate.get("ai_response_allowed", True):
            escalation_reason = "Sentiment safety gate blocked autonomous response."
        elif escalate_for_signal:
            escalation_reason = "Intent and sentiment indicate a human handoff is safer."
        elif low_confidence:
            escalation_reason = f"AI confidence {confidence:.2f} is below threshold {threshold:.2f}."
        if provider_failure:
            escalate = True
            escalation_reason = AI_API_EXHAUSTED_MANUAL_MESSAGE
            if context.db and conversation_id:
                await auto_disable_ai_after_failure_fallback(
                    context.db,
                    context.company_id,
                    conversation_id,
                    {
                        **ai_result,
                        "api_error": True,
                        "fallback_used": True,
                        "static_fallback_served": True,
                        "review_reason": AI_API_EXHAUSTED_MANUAL_MESSAGE,
                    },
                    channel=str(getattr(request, "channel", "") or ""),
                    trace_id=str(getattr(request, "trace_id", "") or ""),
                )
                logger.warning(
                    "AI disabled for conversation %s due to provider failure", conversation_id
                )
            return self._static_fallback_response(latest_text, customer, ai_result)
        if ai_result.get("llm_budget_exhausted"):
            escalate = False
            escalation_reason = ""
        payload = {
            "response": str(ai_result.get("response") or ""),
            "confidence": confidence,
            "confidence_threshold": threshold,
            "attachments": list(ai_result.get("attachments") or []),
            "product_images": list(ai_result.get("product_images") or []),
            "product_ids": list(ai_result.get("product_ids") or []),
            "provider": str(ai_result.get("provider") or ""),
            "model_name": str(ai_result.get("model_name") or ""),
            "llm_id": str(ai_result.get("llm_id") or ""),
            "knowledge_context": knowledge_context,
            "manual_draft": manual_draft_mode,
            "requires_review": bool(escalate or low_confidence),
            "review_reason": escalation_reason,
            "deliver_response": (
                bool(ai_result.get("response")) and not provider_failure
                if manual_draft_mode
                else bool(ai_result.get("response")) and not escalate and not provider_failure
            ),
            "escalate": escalate,
            "escalation_reason": escalation_reason,
            "next_action": str(ai_result.get("next_action") or ("manual_review" if escalate else "send_response")),
            "qualification": qualification,
            "reused_prefetched_response": reused_prefetched_response,
            "rag_called": bool(ai_result.get("rag_called")),
            "llm_budget_exhausted": bool(ai_result.get("llm_budget_exhausted")),
            "api_error": bool(ai_result.get("api_error")),
            "provider_error": dict(ai_result.get("provider_error") or {}),
            "error_type": str(ai_result.get("error_type") or ""),
            "error_reason": str(ai_result.get("error_reason") or ""),
            "degraded": bool(ai_result.get("degraded")),
            "fallback_used": bool(ai_result.get("fallback_used")),
            "fallback_reason": str(ai_result.get("fallback_reason") or ""),
            "static_fallback_served": static_fallback_served,
            "safety_validated": bool(ai_result.get("safety_validated", bool(ai_result.get("response")))),
            "safety_blocked": bool(ai_result.get("safety_blocked")),
            "safety_issues": list(ai_result.get("safety_issues") or []),
        }
        return AgentRunResult(agent_name=self.name, payload=payload)

    def _static_fallback_response(
        self,
        message_text: str,
        customer: dict,
        source_result: dict | None = None,
    ) -> AgentRunResult:
        name = customer.get("name") or "there"
        source = dict(source_result or {})
        provider_error = dict(source.get("provider_error") or {})
        error_type = str(source.get("error_type") or provider_error.get("error_type") or "provider_failure")
        fallback_reason = str(source.get("fallback_reason") or error_type or "provider_failure")
        payload = {
            "response": (
                f"Hi {name}, I'm having trouble connecting right now. "
                "A team member will follow up with you shortly. Thank you for your patience."
            ),
            "provider": "static_fallback",
            "confidence": 0.0,
            "confidence_threshold": 0.7,
            "requires_review": True,
            "review_reason": AI_API_EXHAUSTED_MANUAL_MESSAGE,
            "fallback_used": True,
            "fallback_reason": fallback_reason,
            "static_fallback_served": True,
            "deliver_response": True,
            "api_error": True,
            "provider_error": provider_error,
            "error_type": error_type,
            "error_reason": str(source.get("error_reason") or provider_error.get("error_reason") or ""),
        }
        return AgentRunResult(agent_name=self.name, payload=payload)

    async def _handle_lead_support(
        self,
        context: WorkflowContextProtocol,
    ) -> AgentRunResult:
        request = context.request
        if not getattr(request, "auto_support", True):
            return AgentRunResult(
                agent_name=self.name,
                status="skipped",
                payload={
                    "response": "",
                    "deliver_response": False,
                    "escalate": False,
                    "next_action": "analytics_only",
                },
            )
        capture = dict(getattr(context.agent_outputs, "capture", {}) or {})
        qualification = dict(getattr(context.agent_outputs, "qualification", {}) or {})
        lead = dict(qualification.get("structured_lead") or capture.get("structured_lead") or {})
        stage = str(qualification.get("phase") or lead.get("phase") or "awareness")
        prefetched_nurture = str(qualification.get("nurture_message") or "").strip()
        if prefetched_nurture:
            return AgentRunResult(
                agent_name=self.name,
                payload={
                    "response": prefetched_nurture,
                    "stage": stage,
                    "deliver_response": True,
                    "escalate": False,
                    "next_action": "queue_nurture",
                    "knowledge_context": str(getattr(request, "knowledge_context", "") or ""),
                    "reused_prefetched_response": True,
                },
            )
        payload = {
            "response": "",
            "stage": stage,
            "deliver_response": False,
            "escalate": False,
            "next_action": "review",
            "knowledge_context": str(getattr(request, "knowledge_context", "") or ""),
            "reused_prefetched_response": False,
        }
        return AgentRunResult(agent_name=self.name, payload=payload)

    async def fallback(
        self,
        context: WorkflowContextProtocol,
        error: Exception,
    ) -> AgentRunResult:
        if context.workflow_kind == WorkflowKind.LEAD:
            capture = dict(getattr(context.agent_outputs, "capture", {}) or {})
            structured = dict(capture.get("structured_lead") or {})
            name = str(structured.get("name") or "there").strip() or "there"
            payload = {
                "response": f"Hi {name}, thanks for your interest. A specialist will reach out shortly.",
                "stage": "awareness",
                "deliver_response": True,
                "escalate": False,
                "next_action": "queue_nurture",
                "api_error": True,
            }
            return AgentRunResult(
                agent_name=self.name,
                payload=payload,
                used_fallback=True,
                warnings=[str(error)],
            )

        request = context.request
        latest_text = str(getattr(request, "message_text", "") or "").strip()
        name = (
            str((context.global_memory.identity_context.get("customer") or {}).get("name") or "there").strip()
            or "there"
        )
        lower = latest_text.lower()
        if any(token in lower for token in ("refund", "cancel", "complaint", "issue", "problem")):
            response = (
                f"I understand, {name}. A human teammate is reviewing this now. "
                "Share any order or account reference if available."
            )
        elif any(token in lower for token in ("price", "product", "buy", "quote", "demo")):
            response = f"Thanks, {name}. A specialist will help with the best option for you shortly."
        else:
            seeds = (latest_text or name or context.company_id or context.workflow_id).encode("utf-8")
            variant = int(hashlib.sha1(seeds).hexdigest(), 16) % 2
            response = (
                f"Thanks, {name}. We received your message and a team member will reply soon."
                if variant == 0
                else f"Your message is in queue, {name}. A human agent will pick this up shortly."
            )
        payload = {
            "response": response,
            "confidence": 0.0,
            "confidence_threshold": 0.7,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "provider": "fallback",
            "model_name": "manual-handoff",
            "llm_id": "",
            "knowledge_context": "",
            "deliver_response": False,
            "escalate": True,
            "escalation_reason": "Support agent fallback activated after upstream failure.",
            "next_action": "manual_review",
            "api_error": True,
        }
        return AgentRunResult(
            agent_name=self.name,
            payload=payload,
            used_fallback=True,
            warnings=[str(error)],
        )
