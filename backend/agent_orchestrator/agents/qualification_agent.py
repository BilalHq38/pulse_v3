from __future__ import annotations

import logging
from typing import Any

from agent_orchestrator.agents.base import BaseAgent, WorkflowContextProtocol
from agent_orchestrator.schemas import AgentName, AgentRunResult, WorkflowKind
from services.ai_service.facade import generate_lead_score
from services.ai_service.llm_tracking import has_llm_budget_remaining

logger = logging.getLogger(__name__)


class QualificationAgent(BaseAgent):
    name = AgentName.QUALIFICATION

    async def execute(self, context: WorkflowContextProtocol) -> AgentRunResult:
        capture = dict(getattr(context.agent_outputs, "capture", {}) or {})
        customer = dict((capture.get("customer") or {}) or {})
        lead = dict((capture.get("lead") or {}) or {})
        structured_lead = dict((capture.get("structured_lead") or lead) or {})
        if not structured_lead and context.workflow_kind == WorkflowKind.MESSAGE:
            structured_lead = self._message_as_lead_candidate(context, capture, customer)

        lifecycle_stage = str(customer.get("lifecycle_stage") or "").strip().lower()

        qualification_hint = dict(capture.get("qualification_hint") or {})
        ready_for_scoring = qualification_hint.get("ready_for_scoring") is True
        next_question = str(qualification_hint.get("next_question") or "").strip()

        # Message workflows must not run qualification scoring in the response
        # path. Support always responds first; background tasks update
        # qualification/scoring after delivery.
        if context.workflow_kind == WorkflowKind.MESSAGE and lifecycle_stage != "customer":
            logger.info(
                "qualification_scoring_deferred workflow_id=%s company_id=%s reason=message_response_first missing_fields=%s",
                context.workflow_id,
                context.company_id,
                list(qualification_hint.get("missing_fields") or []),
            )
            ready_label = "ready_for_background_scoring" if ready_for_scoring else "in_discovery"
            payload = {
                "score": 0,
                "grade": ready_label,
                "phase": str(structured_lead.get("phase") or "awareness"),
                "classification": "scoring_deferred",
                "lead_status": str(structured_lead.get("status") or "new"),
                "route_to_support": True,
                "reasoning": "Qualification/scoring runs after response delivery.",
                "next_action": "continue_conversation",
                "structured_lead": structured_lead,
                "qualification_hint_for_ai": next_question,
                "adaptive_question": "",
                "missing_fields": list(qualification_hint.get("missing_fields") or []),
                "completed_fields": list(qualification_hint.get("completed_fields") or []),
                "ready_for_scoring": ready_for_scoring,
                "qualification_scoring_called": False,
            }
            return AgentRunResult(agent_name=self.name, payload=payload)

        if context.workflow_kind == WorkflowKind.MESSAGE and lifecycle_stage == "customer":
            logger.info(
                "qualification_scoring_skipped workflow_id=%s company_id=%s reason=existing_customer",
                context.workflow_id,
                context.company_id,
            )
            payload = {
                "score": 95,
                "grade": "customer",
                "phase": "retention",
                "classification": "customer_support",
                "lead_status": "customer",
                "route_to_support": True,
                "reasoning": "Existing customer conversation routed directly to support.",
                "next_action": "Respond to customer",
                "adaptive_question": "",
                "qualification_scoring_called": False,
            }
            return AgentRunResult(agent_name=self.name, payload=payload)

        if not has_llm_budget_remaining():
            logger.info(
                "qualification_scoring_skipped workflow_id=%s company_id=%s reason=budget_exhausted",
                context.workflow_id,
                context.company_id,
            )
            payload = {
                "score": 0,
                "grade": "deferred",
                "phase": str(structured_lead.get("phase") or "awareness"),
                "classification": "scoring_deferred_budget_exhausted",
                "lead_status": str(structured_lead.get("status") or lifecycle_stage or "new"),
                "route_to_support": bool(
                    context.workflow_kind == WorkflowKind.MESSAGE or getattr(context.request, "auto_support", True)
                ),
                "reasoning": "Lead scoring deferred because the message AI budget was exhausted.",
                "next_action": "Continue support conversation; score lead asynchronously later.",
                "structured_lead": structured_lead,
                "ready_for_scoring": True,
                "adaptive_question": "",
                "qualification_scoring_called": False,
            }
            return AgentRunResult(agent_name=self.name, payload=payload)

        logger.info(
            "qualification_scoring_executed workflow_id=%s company_id=%s workflow_kind=%s",
            context.workflow_id,
            context.company_id,
            context.workflow_kind.value,
        )
        score_result = await generate_lead_score(
            structured_lead,
            db=context.db,
            company_id=context.company_id,
        )
        unified_intent = dict(score_result.get("intent") or capture.get("intent") or {})
        if unified_intent.get("intent"):
            context.global_memory.shared_context["latest_intent"] = str(unified_intent.get("intent") or "")
        classification = self._classify(
            int(score_result.get("score", 0) or 0),
            context.workflow_kind,
            lifecycle_stage or str(structured_lead.get("status") or "new"),
        )
        payload = {
            "score": int(score_result.get("score", 0) or 0),
            "grade": str(score_result.get("grade") or ""),
            "phase": str(score_result.get("phase") or structured_lead.get("phase") or "awareness"),
            "classification": classification,
            "lead_status": str(structured_lead.get("status") or lifecycle_stage or "new"),
            "route_to_support": bool(
                context.workflow_kind == WorkflowKind.MESSAGE or getattr(context.request, "auto_support", True)
            ),
            "reasoning": str(score_result.get("reasoning") or ""),
            "next_action": str(score_result.get("next_action") or ""),
            "nurture_message": str(score_result.get("nurture_message") or ""),
            "intent": unified_intent,
            "structured_lead": structured_lead,
            "ready_for_scoring": True,
            "missing_fields": list(score_result.get("missing_fields") or []),
            "completed_fields": list(qualification_hint.get("completed_fields") or []),
            "adaptive_question": "",
            "qualification_scoring_called": True,
        }
        return AgentRunResult(agent_name=self.name, payload=payload)

    def _message_as_lead_candidate(
        self,
        context: WorkflowContextProtocol,
        capture: dict[str, Any],
        customer: dict[str, Any],
    ) -> dict[str, Any]:
        structured_event = dict(capture.get("structured_event") or {})
        contact = dict(structured_event.get("contact") or {})
        return {
            "company_id": context.company_id,
            "id": structured_event.get("lead_id", ""),
            "name": contact.get("name", ""),
            "email": contact.get("email", ""),
            "phone": contact.get("phone", ""),
            "source": structured_event.get("source", structured_event.get("channel", "message")),
            "status": customer.get("lifecycle_stage", "new"),
            "notes": structured_event.get("message_text", ""),
            "customer_company_name": customer.get("customer_company_name", ""),
        }

    def _classify(
        self,
        score: int,
        workflow_kind: WorkflowKind,
        lead_status: str,
    ) -> str:
        status = str(lead_status or "").strip().lower()
        if workflow_kind == WorkflowKind.MESSAGE:
            return "support_existing_customer" if status == "customer" else "support_inquiry"
        if score >= 80:
            return "sales_hot"
        if score >= 60:
            return "sales_warm"
        return "sales_cold"

    async def fallback(
        self,
        context: WorkflowContextProtocol,
        error: Exception,
    ) -> AgentRunResult:
        capture = dict(getattr(context.agent_outputs, "capture", {}) or {})
        structured_lead = dict((capture.get("structured_lead") or capture.get("structured_event") or {}))
        score = 40
        if structured_lead.get("email"):
            score += 15
        if structured_lead.get("phone"):
            score += 15
        if str(structured_lead.get("message_text") or structured_lead.get("notes") or "").strip():
            score += 10
        payload = {
            "score": min(score, 90),
            "grade": "warm" if score >= 60 else "cold",
            "phase": "awareness",
            "classification": "fallback_review",
            "lead_status": str(structured_lead.get("status") or "new"),
            "route_to_support": bool(
                context.workflow_kind == WorkflowKind.MESSAGE or getattr(context.request, "auto_support", True)
            ),
            "reasoning": "Fallback qualification was used because the scoring agent failed.",
            "next_action": "Review manually",
            "structured_lead": structured_lead,
            "adaptive_question": "",
        }
        return AgentRunResult(
            agent_name=self.name,
            payload=payload,
            used_fallback=True,
            warnings=[str(error)],
        )
