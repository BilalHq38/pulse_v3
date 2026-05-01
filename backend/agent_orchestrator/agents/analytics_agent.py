from __future__ import annotations

import logging
from datetime import datetime, timezone

from agent_orchestrator.agents.base import BaseAgent, WorkflowContextProtocol
from agent_orchestrator.repository import (
    average_response_minutes,
    fetch_conversation,
    fetch_customer,
    fetch_lead,
    fetch_messages_for_day,
)
from agent_orchestrator.schemas import AgentName, AgentRunResult, WorkflowKind
from core.utils import parse_dt
from data_pipeline.storage import (
    upsert_analytics_event,
    upsert_conversation_metrics,
    upsert_customer_interaction_summary,
    upsert_lead_metrics,
)
from services.ai_service.facade import summarize_customer_interaction
from services.ai_service.llm_tracking import has_llm_budget_remaining
from shared.config import ai_analytics_llm_enabled, ai_analytics_summary_min_messages

logger = logging.getLogger(__name__)


class AnalyticsAgent(BaseAgent):
    name = AgentName.ANALYTICS

    async def execute(self, context: WorkflowContextProtocol) -> AgentRunResult:
        if context.workflow_kind == WorkflowKind.LEAD:
            return await self._process_lead(context)
        return await self._process_message(context)

    async def _process_message(self, context: WorkflowContextProtocol) -> AgentRunResult:
        request = context.request
        capture = dict(getattr(context.agent_outputs, "capture", {}) or {})
        support = dict(getattr(context.agent_outputs, "support", {}) or {})
        conversation = await fetch_conversation(context.db, getattr(request, "conversation_id", ""))
        conversation_id = conversation.get("id", "") or getattr(request, "conversation_id", "")
        occurred_at = parse_dt(datetime.now(timezone.utc))
        daily_messages = await fetch_messages_for_day(context.db, conversation_id, occurred_at)
        sentiment_values = [
            float(item["sentiment_score"]) for item in daily_messages if item.get("sentiment_score") is not None
        ]
        latest_signal = capture.get("sentiment") or capture.get("conversation_sentiment") or {}
        metrics = await upsert_conversation_metrics(
            context.db,
            {
                "company_id": context.company_id,
                "conversation_id": conversation_id,
                "metric_date": occurred_at.date(),
                "channel": conversation.get("channel", getattr(request, "channel", "web_chat")),
                "status": conversation.get("status", "open"),
                "customer_id": conversation.get("customer_id", getattr(request, "customer_id", "")),
                "ai_handled": bool(conversation.get("ai_handled", True)),
                "escalated": bool(support.get("escalate")),
                "total_messages": len(daily_messages),
                "customer_messages": sum(
                    1 for item in daily_messages if str(item.get("sender_type") or "").lower() == "customer"
                ),
                "agent_messages": sum(
                    1 for item in daily_messages if str(item.get("sender_type") or "").lower() == "agent"
                ),
                "ai_messages": sum(1 for item in daily_messages if str(item.get("sender_type") or "").lower() == "ai"),
                "system_messages": sum(
                    1 for item in daily_messages if str(item.get("sender_type") or "").lower() == "system"
                ),
                "unread_count": int(conversation.get("unread_count") or 0),
                "avg_sentiment": round(sum(sentiment_values) / len(sentiment_values), 4) if sentiment_values else 0.0,
                "latest_sentiment_label": str(
                    latest_signal.get("sentiment_label")
                    or latest_signal.get("label")
                    or latest_signal.get("emotion")
                    or "neutral"
                ),
                "latest_sentiment_score": latest_signal.get("score"),
                "latest_intent_type": str(((capture.get("intent") or {}).get("intent") or "")),
                "first_message_at": daily_messages[0].get("created_at") if daily_messages else None,
                "last_message_at": daily_messages[-1].get("created_at") if daily_messages else None,
                "response_time_minutes": average_response_minutes(daily_messages),
                "payload": {
                    "workflow_id": context.workflow_id,
                    "trace_id": context.trace_id,
                    "support_next_action": support.get("next_action", ""),
                },
            },
        )
        customer = await fetch_customer(
            context.db, conversation.get("customer_id", getattr(request, "customer_id", ""))
        )
        recent_messages = list(context.global_memory.conversation_history) or daily_messages
        summary = {}
        request_metadata = dict(getattr(request, "metadata", {}) or {})
        explicit_analytics_mode = bool(request_metadata.get("analytics_llm"))
        scheduled_summary = bool(request_metadata.get("daily_summary"))
        analytics_enabled = bool(ai_analytics_llm_enabled() or explicit_analytics_mode or scheduled_summary)
        threshold_met = len(recent_messages) >= ai_analytics_summary_min_messages()
        terminal_status = str(conversation.get("status") or "").strip().lower() in {"closed", "resolved"}
        should_summarize = bool(
            customer
            and analytics_enabled
            and (terminal_status or explicit_analytics_mode or scheduled_summary or threshold_met)
        )
        skip_reason = ""
        if not customer:
            skip_reason = "no_customer"
        elif not analytics_enabled:
            skip_reason = "disabled"
        elif not should_summarize:
            skip_reason = "threshold_not_met"
        elif not has_llm_budget_remaining():
            skip_reason = "budget_exhausted"
            should_summarize = False
        if customer and should_summarize:
            ai_summary = await summarize_customer_interaction(
                recent_messages,
                customer,
                db=context.db,
                company_id=context.company_id,
            )
            summary = await upsert_customer_interaction_summary(
                context.db,
                {
                    "company_id": context.company_id,
                    "customer_id": customer.get("id", ""),
                    "customer_name": customer.get("name", ""),
                    "conversation_id": conversation_id,
                    "summary_date": occurred_at.date(),
                    "summary_text": str(ai_summary.get("summary", "")),
                    "total_messages": int(ai_summary.get("total_messages", len(recent_messages))),
                    "avg_sentiment": float(ai_summary.get("avg_sentiment", metrics.get("avg_sentiment", 0.0))),
                    "escalated": bool(support.get("escalate")),
                    "ai_handled": bool(conversation.get("ai_handled", True)),
                },
            )
            analytics_llm_called = True
        elif customer:
            logger.info(
                "analytics_summary_skipped workflow_id=%s conversation_id=%s company_id=%s message_count=%s status=%s reason=%s",
                context.workflow_id,
                conversation_id,
                context.company_id,
                len(recent_messages),
                str(conversation.get("status") or "open"),
                skip_reason or "unknown",
            )
            analytics_llm_called = False
        else:
            analytics_llm_called = False
        analytics_event = await upsert_analytics_event(
            context.db,
            {
                "company_id": context.company_id,
                "raw_table": "agent_orchestrator",
                "raw_id": context.workflow_id,
                "event_kind": "agent_message_workflow",
                "event_source": str(getattr(request, "source", "") or getattr(request, "channel", "web_chat")),
                "entity_type": "conversation",
                "entity_id": conversation_id,
                "conversation_id": conversation_id,
                "customer_id": conversation.get("customer_id", customer.get("id", "")),
                "metric_date": occurred_at.date(),
                "occurred_at": occurred_at,
                "payload": {
                    "workflow_id": context.workflow_id,
                    "trace_id": context.trace_id,
                    "intent": (capture.get("intent") or {}).get("intent", ""),
                    "deliver_response": bool(support.get("deliver_response")),
                    "escalated": bool(support.get("escalate")),
                },
            },
        )
        return AgentRunResult(
            agent_name=self.name,
            payload={
                "conversation_metrics": metrics,
                "customer_summary": summary,
                "analytics_event": analytics_event,
                "analytics_llm_called": analytics_llm_called,
            },
        )

    async def _process_lead(self, context: WorkflowContextProtocol) -> AgentRunResult:
        request = context.request
        capture = dict(getattr(context.agent_outputs, "capture", {}) or {})
        qualification = dict(getattr(context.agent_outputs, "qualification", {}) or {})
        support = dict(getattr(context.agent_outputs, "support", {}) or {})
        lead = dict(qualification.get("structured_lead") or capture.get("structured_lead") or {})
        if not lead:
            lead = await fetch_lead(context.db, getattr(request, "lead_id", ""))
        occurred_at = parse_dt(datetime.now(timezone.utc))
        metric = await upsert_lead_metrics(
            context.db,
            {
                "company_id": context.company_id,
                "lead_id": lead.get("id", getattr(request, "lead_id", "")),
                "metric_date": occurred_at.date(),
                "source": str(lead.get("source") or getattr(request, "source", "lead")),
                "status": str(lead.get("status") or "new"),
                "phase": str(qualification.get("phase") or lead.get("phase") or "awareness"),
                "grade": str(qualification.get("grade") or lead.get("grade") or "cold"),
                "name": str(lead.get("name") or ""),
                "email": str(lead.get("email") or ""),
                "phone": str(lead.get("phone") or ""),
                "current_score": int(lead.get("score", 0) or 0),
                "recommended_score": int(qualification.get("score", 0) or 0),
                "recommended_grade": str(qualification.get("grade") or ""),
                "scoring_reason": str(qualification.get("reasoning") or ""),
                "next_action": str(support.get("next_action") or qualification.get("next_action") or ""),
                "duplicate_count": 0,
                "is_duplicate": False,
                "is_converted": str(lead.get("status") or "").strip().lower() == "converted",
                "payload": {
                    "workflow_id": context.workflow_id,
                    "trace_id": context.trace_id,
                    "classification": qualification.get("classification", ""),
                    "nurture_message": support.get("response", ""),
                },
            },
        )
        analytics_event = await upsert_analytics_event(
            context.db,
            {
                "company_id": context.company_id,
                "raw_table": "agent_orchestrator",
                "raw_id": context.workflow_id,
                "event_kind": "agent_lead_workflow",
                "event_source": str(lead.get("source") or getattr(request, "source", "lead")),
                "entity_type": "lead",
                "entity_id": lead.get("id", getattr(request, "lead_id", "")),
                "lead_id": lead.get("id", getattr(request, "lead_id", "")),
                "customer_id": getattr(request, "customer_id", ""),
                "metric_date": occurred_at.date(),
                "occurred_at": occurred_at,
                "payload": {
                    "workflow_id": context.workflow_id,
                    "trace_id": context.trace_id,
                    "score": qualification.get("score", 0),
                    "grade": qualification.get("grade", ""),
                    "classification": qualification.get("classification", ""),
                    "support_message_generated": bool(support.get("response")),
                },
            },
        )
        return AgentRunResult(
            agent_name=self.name,
            payload={
                "lead_metrics": metric,
                "analytics_event": analytics_event,
            },
        )

    async def fallback(
        self,
        context: WorkflowContextProtocol,
        error: Exception,
    ) -> AgentRunResult:
        return AgentRunResult(
            agent_name=self.name,
            payload={
                "warning": "Analytics agent fallback activated.",
                "error": str(error),
            },
            used_fallback=True,
            warnings=[str(error)],
        )
