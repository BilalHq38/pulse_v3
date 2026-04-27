from __future__ import annotations

import hashlib

from agent_orchestrator.agents.base import BaseAgent, WorkflowContextProtocol
from agent_orchestrator.repository import (
    fetch_company_ai_threshold,
    fetch_customer,
)
from agent_orchestrator.schemas import AgentName, AgentRunResult, WorkflowKind
from services.ai_service.facade import (
    generate_ai_response,
    generate_nurture_message,
    get_company_knowledge,
    should_auto_escalate,
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
        capture = dict(getattr(context.agent_outputs, "capture", {}) or {})
        qualification = dict(getattr(context.agent_outputs, "qualification", {}) or {})
        customer = dict(capture.get("customer") or {}) or await fetch_customer(
            context.db, getattr(request, "customer_id", "")
        )

        # Onboarding flow takes precedence for new_customer lifecycle stage.
        lifecycle = str(customer.get("lifecycle_stage") or "").strip().lower()
        latest_text_raw = str(getattr(request, "message_text", "") or "").strip()
        if lifecycle == "new_customer" and latest_text_raw:
            try:
                from services.ai_service.onboarding_flows import advance_onboarding

                onboarding = await advance_onboarding(
                    context.db,
                    customer_id=str(customer.get("id") or ""),
                    company_id=context.company_id,
                    message_text=latest_text_raw,
                    customer_name=str(customer.get("name") or ""),
                )
            except Exception:
                onboarding = {}
            if onboarding.get("response"):
                payload = {
                    "response": str(onboarding.get("response") or ""),
                    "confidence": 0.95,
                    "confidence_threshold": 0.7,
                    "attachments": [],
                    "product_images": [],
                    "product_ids": [],
                    "provider": "onboarding_flow",
                    "model_name": onboarding.get("step", "onboarding"),
                    "llm_id": "",
                    "knowledge_context": "",
                    "deliver_response": True,
                    "escalate": False,
                    "escalation_reason": "",
                    "next_action": "send_response",
                    "source": "onboarding_flow",
                    "onboarding_step": onboarding.get("step", ""),
                    "onboarding_complete": bool(onboarding.get("complete")),
                    "qualification": qualification,
                }
                return AgentRunResult(agent_name=self.name, payload=payload)

        # Adaptive qualification question takes precedence over RAG if the
        # qualification agent flagged that scoring is deferred.
        adaptive_question = str(qualification.get("adaptive_question") or "").strip()
        if adaptive_question and not qualification.get("ready_for_scoring", True):
            payload = {
                "response": adaptive_question,
                "confidence": 0.9,
                "confidence_threshold": 0.7,
                "attachments": [],
                "product_images": [],
                "product_ids": [],
                "provider": "adaptive_qualification",
                "model_name": "adaptive-qualification",
                "llm_id": "",
                "knowledge_context": "",
                "deliver_response": True,
                "escalate": False,
                "escalation_reason": "",
                "next_action": "ask_adaptive_question",
                "source": "adaptive_qualification",
                "qualification": qualification,
            }
            return AgentRunResult(agent_name=self.name, payload=payload)
        conversation_history = list(getattr(request, "conversation_context", []) or []) or list(
            context.global_memory.conversation_history
        )
        latest_text = str(getattr(request, "message_text", "") or "").strip()
        sentiment = dict(capture.get("sentiment") or {})
        intent = dict(capture.get("intent") or {})
        sentiment_gate = dict(capture.get("sentiment_gate") or {})
        threshold = await fetch_company_ai_threshold(context.db, context.company_id)
        knowledge_context = str(getattr(request, "knowledge_context", "") or "").strip()
        if not knowledge_context:
            knowledge_context = await get_company_knowledge(
                context.db,
                company_id=context.company_id,
                current_query=latest_text,
                top_k=3,
            )
        prefetched = dict(capture.get("prefetched_support_response") or {})
        ai_result = prefetched or await generate_ai_response(
            conversation_history,
            customer,
            company_id=context.company_id,
            db=context.db,
            knowledge_context=knowledge_context,
            actor_user_id=str(getattr(request, "actor_user_id", "") or ""),
            conversation_id=str(getattr(request, "conversation_id", "") or ""),
            channel=str(getattr(request, "channel", "") or "web_chat"),
            observed_sentiment=sentiment,
            observed_intent=intent,
        )
        confidence = float(ai_result.get("confidence", 0.0) or 0.0)
        escalate_for_signal = should_auto_escalate(
            latest_text,
            sentiment=sentiment,
            intent=intent,
        )
        escalate = bool(
            not sentiment_gate.get("ai_response_allowed", True) or escalate_for_signal or confidence < threshold
        )
        escalation_reason = ""
        if not sentiment_gate.get("ai_response_allowed", True):
            escalation_reason = "Sentiment safety gate blocked autonomous response."
        elif escalate_for_signal:
            escalation_reason = "Intent and sentiment indicate a human handoff is safer."
        elif confidence < threshold:
            escalation_reason = f"AI confidence {confidence:.2f} is below threshold {threshold:.2f}."
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
            "deliver_response": bool(ai_result.get("response")) and not escalate,
            "escalate": escalate,
            "escalation_reason": escalation_reason,
            "next_action": "manual_review" if escalate else "send_response",
            "qualification": qualification,
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
        company_context = str(getattr(request, "knowledge_context", "") or "").strip()
        if not company_context:
            company_context = await get_company_knowledge(
                context.db,
                company_id=context.company_id,
                current_query=str(lead.get("notes") or lead.get("name") or ""),
                top_k=3,
            )
        nurture = await generate_nurture_message(
            lead,
            stage,
            company_context=company_context,
            db=context.db,
            company_id=context.company_id,
        )
        payload = {
            "response": str(nurture.get("message") or ""),
            "stage": str(nurture.get("stage") or stage),
            "deliver_response": bool(nurture.get("message")),
            "escalate": False,
            "next_action": "queue_nurture" if nurture.get("message") else "review",
            "knowledge_context": company_context,
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
                "Please share any order or account reference if available."
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
