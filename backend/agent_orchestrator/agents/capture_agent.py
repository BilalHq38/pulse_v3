from __future__ import annotations

import logging
from typing import Any

from agent_orchestrator.agents.base import BaseAgent, WorkflowContextProtocol
from agent_orchestrator.repository import (
    fetch_conversation,
    fetch_customer,
    fetch_lead,
    fetch_recent_messages,
)
from agent_orchestrator.schemas import AgentName, AgentRunResult, WorkflowKind
from services.ai_service.facade import (
    build_sentiment_gate,
    generate_combined_ai_analysis,
)
from services.ai_service.sentiment import analyze_local_sentiment

logger = logging.getLogger(__name__)


class CaptureAgent(BaseAgent):
    name = AgentName.CAPTURE

    async def execute(self, context: WorkflowContextProtocol) -> AgentRunResult:
        request = context.request
        conversation = await fetch_conversation(context.db, getattr(request, "conversation_id", ""))
        customer = dict(getattr(request, "customer", {}) or {}) or await fetch_customer(
            context.db,
            getattr(request, "customer_id", "") or conversation.get("customer_id", ""),
        )
        lead = dict(getattr(request, "lead", {}) or {}) or await fetch_lead(context.db, getattr(request, "lead_id", ""))
        conversation_history = list(getattr(request, "conversation_context", []) or []) or await fetch_recent_messages(
            context.db,
            getattr(request, "conversation_id", ""),
            limit=20,
        )
        context.global_memory.conversation_id = getattr(request, "conversation_id", "") or conversation.get("id", "")
        context.global_memory.customer_id = getattr(request, "customer_id", "") or customer.get("id", "")
        context.global_memory.lead_id = getattr(request, "lead_id", "") or lead.get("id", "")
        context.global_memory.conversation_history = conversation_history
        context.global_memory.identity_context = {
            "conversation": conversation,
            "customer": customer,
            "lead": lead,
        }

        if context.workflow_kind == WorkflowKind.LEAD:
            return await self._capture_lead_event(context, lead, customer)
        return await self._capture_message_event(
            context,
            conversation=conversation,
            customer=customer,
            lead=lead,
            conversation_history=conversation_history,
        )

    async def _capture_message_event(
        self,
        context: WorkflowContextProtocol,
        *,
        conversation: dict[str, Any],
        customer: dict[str, Any],
        lead: dict[str, Any],
        conversation_history: list[dict[str, Any]],
    ) -> AgentRunResult:
        request = context.request
        message_text = str(getattr(request, "message_text", "") or "").strip()
        structured_event = {
            "entity_type": "message",
            "message_id": getattr(request, "message_id", ""),
            "conversation_id": getattr(request, "conversation_id", ""),
            "customer_id": customer.get("id", ""),
            "lead_id": lead.get("id", ""),
            "source": str(getattr(request, "source", "") or getattr(request, "channel", "web_chat")),
            "channel": getattr(request, "channel", conversation.get("channel", "web_chat")),
            "contact": {
                "name": customer.get("name", "") or getattr(request, "sender_name", ""),
                "email": customer.get("email", ""),
                "phone": customer.get("phone", "") or getattr(request, "sender_contact", ""),
            },
            "message_text": message_text,
            "metadata": dict(getattr(request, "metadata", {}) or {}),
        }
        context.global_memory.shared_context["latest_message"] = message_text

        deterministic_reason = ""
        deterministic_sentiment: dict[str, Any] = {}
        qualification_hint: dict[str, Any] = {}
        if not message_text:
            deterministic_reason = "empty_message"

        if deterministic_reason:
            sentiment = deterministic_sentiment or analyze_local_sentiment(message_text)
            intent_name = "general_question"
            intent = {
                "intent": intent_name,
                "confidence": 0.35 if message_text else 0.0,
                "entities": {},
                "urgency": "low",
                "source": "rule",
            }
            sentiment_gate = build_sentiment_gate(message_text, sentiment)
            context.global_memory.shared_context["latest_intent"] = intent_name
            logger.info(
                "capture_llm_skipped workflow_id=%s message_id=%s conversation_id=%s company_id=%s reason=%s",
                context.workflow_id,
                str(getattr(request, "message_id", "") or ""),
                str(getattr(request, "conversation_id", "") or ""),
                context.company_id,
                deterministic_reason,
            )
            return AgentRunResult(
                agent_name=self.name,
                payload={
                    "structured_event": structured_event,
                    "sentiment": sentiment,
                    "conversation_sentiment": dict(sentiment),
                    "intent": intent,
                    "sentiment_gate": sentiment_gate,
                    "prefetched_support_response": {},
                    "customer": customer,
                    "lead": lead,
                    "qualification_hint": qualification_hint,
                    "capture_llm_skipped": True,
                    "capture_skip_reason": deterministic_reason,
                },
            )

        combined = await generate_combined_ai_analysis(
            customer_message=message_text,
            conversation_context=conversation_history,
            customer_info=customer,
            company_id=context.company_id,
            db=context.db,
            knowledge_context=str(getattr(request, "knowledge_context", "") or ""),
            conversation_id=getattr(request, "conversation_id", ""),
            message_id=getattr(request, "message_id", ""),
            actor_user_id=str(getattr(request, "actor_user_id", "") or ""),
            channel=str(getattr(request, "channel", "") or ""),
            source=str(getattr(request, "source", "") or ""),
            metadata=dict(getattr(request, "metadata", {}) or {}),
            lead=lead,
        )
        sentiment = dict(combined.get("sentiment") or {})
        intent = dict(combined.get("intent") or {})
        conversation_sentiment = dict(combined.get("conversation_sentiment") or {})
        qualification_hint = dict(combined.get("qualification_hint") or {})
        sentiment_gate = build_sentiment_gate(message_text, sentiment)
        context.global_memory.shared_context["latest_intent"] = str(intent.get("intent") or "")
        prefetched_support_response = dict(combined.get("ai_response") or {})
        if prefetched_support_response.get("response"):
            logger.info(
                "capture_prefetched_response_available workflow_id=%s message_id=%s conversation_id=%s company_id=%s provider=%s model=%s",
                context.workflow_id,
                str(getattr(request, "message_id", "") or ""),
                str(getattr(request, "conversation_id", "") or ""),
                context.company_id,
                str(prefetched_support_response.get("provider") or ""),
                str(prefetched_support_response.get("model_name") or ""),
            )
        payload = {
            "structured_event": structured_event,
            "sentiment": sentiment,
            "conversation_sentiment": conversation_sentiment,
            "intent": intent,
            "sentiment_gate": sentiment_gate,
            "prefetched_support_response": prefetched_support_response,
            "customer": customer,
            "lead": lead,
            "qualification_hint": qualification_hint,
            "interaction_summary": dict(combined.get("interaction_summary") or {}),
            "rag_called": bool(prefetched_support_response.get("rag_called")),
        }
        return AgentRunResult(agent_name=self.name, payload=payload)

    async def _capture_lead_event(
        self,
        context: WorkflowContextProtocol,
        lead: dict[str, Any],
        customer: dict[str, Any],
    ) -> AgentRunResult:
        request = context.request
        raw_message = str(getattr(request, "raw_message", "") or lead.get("notes") or "").strip()
        intent = {
            "intent": "lead_capture",
            "confidence": 0.0,
            "entities": {},
            "urgency": "low",
            "source": "deferred_to_unified_lead_ai" if raw_message else "rule",
        }
        structured_lead = {
            **lead,
            "company_id": context.company_id,
            "source": str(getattr(request, "source", "") or lead.get("source") or "lead"),
            "status": str(lead.get("status") or "new"),
            "name": str(lead.get("name") or customer.get("name") or ""),
            "email": str(lead.get("email") or customer.get("email") or ""),
            "phone": str(lead.get("phone") or customer.get("phone") or ""),
        }
        if raw_message and not str(structured_lead.get("notes") or "").strip():
            structured_lead["notes"] = raw_message
        context.global_memory.shared_context["latest_intent"] = str(intent.get("intent") or "")
        payload = {
            "structured_event": {
                "entity_type": "lead",
                "lead_id": getattr(request, "lead_id", "") or lead.get("id", ""),
                "conversation_id": getattr(request, "conversation_id", ""),
                "customer_id": getattr(request, "customer_id", "") or customer.get("id", ""),
                "source": structured_lead.get("source", "lead"),
                "contact": {
                    "name": structured_lead.get("name", ""),
                    "email": structured_lead.get("email", ""),
                    "phone": structured_lead.get("phone", ""),
                },
                "metadata": dict(getattr(request, "metadata", {}) or {}),
            },
            "structured_lead": structured_lead,
            "intent": intent,
            "customer": customer,
            "lead": lead,
        }
        return AgentRunResult(agent_name=self.name, payload=payload)

    async def fallback(
        self,
        context: WorkflowContextProtocol,
        error: Exception,
    ) -> AgentRunResult:
        request = context.request
        if context.workflow_kind == WorkflowKind.LEAD:
            lead = dict(getattr(request, "lead", {}) or {})
            payload = {
                "structured_event": {
                    "entity_type": "lead",
                    "lead_id": getattr(request, "lead_id", "") or lead.get("id", ""),
                    "source": str(getattr(request, "source", "") or lead.get("source") or "lead"),
                    "contact": {
                        "name": str(lead.get("name") or ""),
                        "email": str(lead.get("email") or ""),
                        "phone": str(lead.get("phone") or ""),
                    },
                    "metadata": dict(getattr(request, "metadata", {}) or {}),
                },
                "structured_lead": lead,
                "intent": {
                    "intent": "lead_capture",
                    "confidence": 0.0,
                    "entities": {},
                    "urgency": "low",
                    "source": "fallback",
                },
            }
            return AgentRunResult(
                agent_name=self.name,
                payload=payload,
                used_fallback=True,
                warnings=[str(error)],
            )

        message_text = str(getattr(request, "message_text", "") or "").strip()
        sentiment_gate = build_sentiment_gate(message_text, {})
        payload = {
            "structured_event": {
                "entity_type": "message",
                "message_id": getattr(request, "message_id", ""),
                "conversation_id": getattr(request, "conversation_id", ""),
                "source": str(getattr(request, "source", "") or getattr(request, "channel", "web_chat")),
                "channel": getattr(request, "channel", "web_chat"),
                "contact": {
                    "name": getattr(request, "sender_name", ""),
                    "email": "",
                    "phone": getattr(request, "sender_contact", ""),
                },
                "message_text": message_text,
                "metadata": dict(getattr(request, "metadata", {}) or {}),
            },
            "sentiment": {},
            "conversation_sentiment": {},
            "intent": {
                "intent": "general_question",
                "confidence": 0.0,
                "entities": {},
                "urgency": "low",
                "source": "fallback",
            },
            "sentiment_gate": sentiment_gate,
            "prefetched_support_response": {},
        }
        return AgentRunResult(
            agent_name=self.name,
            payload=payload,
            used_fallback=True,
            warnings=[str(error)],
        )
