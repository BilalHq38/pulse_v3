from __future__ import annotations

import logging

from services.ai_service.facade import summarize_customer_interaction

from data_pipeline.constants import RAW_MESSAGE_TABLE
from data_pipeline.processors.base import (
    ProcessorBase,
    compute_average_response_minutes,
    row_to_dict,
)
from data_pipeline.storage import (
    upsert_analytics_event,
    upsert_conversation_metrics,
    upsert_customer_interaction_summary,
)
from data_pipeline.utils import metric_date_for, parse_timestamp, safe_float

logger = logging.getLogger(__name__)


class ConversationProcessor(ProcessorBase):
    async def process(
        self,
        raw_record: dict,
        *,
        message: dict | None = None,
        conversation: dict | None = None,
    ) -> dict:
        message_row = dict(message or {})
        if not message_row:
            message_row = await self.fetch_message_with_attachments(str(raw_record.get("canonical_message_id") or ""))
        conversation_row = dict(conversation or {})
        if not conversation_row and message_row.get("conversation_id"):
            conversation_row = row_to_dict(
                await self.db.fetchrow(
                    "SELECT * FROM conversations WHERE id=$1 LIMIT 1",
                    message_row.get("conversation_id", ""),
                )
            )
        if not conversation_row:
            return {}

        occurred_at = parse_timestamp(message_row.get("created_at") or raw_record.get("occurred_at"))
        metric_date = metric_date_for(occurred_at)
        daily_messages = await self.fetch_messages_for_day(conversation_row["id"], occurred_at)
        sentiment_values = [
            float(value)
            for value in (safe_float(item.get("sentiment_score")) for item in daily_messages)
            if value is not None
        ]
        latest_with_signal = next(
            (item for item in reversed(daily_messages) if item.get("sentiment_emotion") or item.get("intent_type")),
            daily_messages[-1] if daily_messages else message_row,
        )
        avg_sentiment = round(sum(sentiment_values) / len(sentiment_values), 4) if sentiment_values else 0.0
        metrics = await upsert_conversation_metrics(
            self.db,
            {
                "company_id": conversation_row.get("company_id", raw_record.get("company_id", "")),
                "conversation_id": conversation_row["id"],
                "metric_date": metric_date,
                "channel": conversation_row.get("channel", "web_chat"),
                "status": conversation_row.get("status", "open"),
                "customer_id": conversation_row.get("customer_id", ""),
                "ai_handled": bool(conversation_row.get("ai_handled", True)),
                "escalated": str(conversation_row.get("status") or "").strip().lower() == "escalated",
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
                "unread_count": int(conversation_row.get("unread_count") or 0),
                "avg_sentiment": avg_sentiment,
                "latest_sentiment_label": str(
                    latest_with_signal.get("sentiment_emotion") or conversation_row.get("sentiment_label") or "neutral"
                ),
                "latest_sentiment_score": latest_with_signal.get("sentiment_score"),
                "latest_intent_type": str(latest_with_signal.get("intent_type") or ""),
                "first_message_at": daily_messages[0].get("created_at") if daily_messages else None,
                "last_message_at": daily_messages[-1].get("created_at") if daily_messages else None,
                "response_time_minutes": compute_average_response_minutes(daily_messages),
                "payload": {
                    "priority": conversation_row.get("priority", ""),
                    "assigned_to": conversation_row.get("assigned_to", ""),
                    "customer_name": conversation_row.get("customer_name", ""),
                    "raw_message_id": raw_record.get("id", ""),
                },
            },
        )

        summary = {}
        should_refresh_summary = bool(daily_messages) and (
            str(message_row.get("sender_type") or "").lower() in {"ai", "agent"}
            or len(daily_messages) <= 3
            or len(daily_messages) % 5 == 0
        )
        if should_refresh_summary and conversation_row.get("customer_id"):
            try:
                recent_messages = await self.fetch_conversation_messages(conversation_row["id"], limit=50)
                customer = row_to_dict(
                    await self.db.fetchrow(
                        "SELECT * FROM customers WHERE id=$1 LIMIT 1",
                        conversation_row.get("customer_id", ""),
                    )
                )
                ai_summary = await summarize_customer_interaction(
                    recent_messages,
                    customer,
                    db=self.db,
                )
                summary = await upsert_customer_interaction_summary(
                    self.db,
                    {
                        "company_id": conversation_row.get("company_id", ""),
                        "customer_id": conversation_row.get("customer_id", ""),
                        "customer_name": conversation_row.get("customer_name", ""),
                        "conversation_id": conversation_row["id"],
                        "summary_date": metric_date,
                        "summary_text": str(ai_summary.get("summary", "")),
                        "total_messages": int(ai_summary.get("total_messages", len(recent_messages))),
                        "avg_sentiment": safe_float(ai_summary.get("avg_sentiment"), avg_sentiment) or avg_sentiment,
                        "escalated": bool(ai_summary.get("escalated", metrics.get("escalated", False))),
                        "ai_handled": bool(ai_summary.get("ai_handled", conversation_row.get("ai_handled", True))),
                    },
                )
            except Exception as exc:
                logger.warning(
                    "conversation summary refresh failed conversation_id=%s error=%s",
                    conversation_row.get("id", ""),
                    exc,
                )

        analytics_event = await upsert_analytics_event(
            self.db,
            {
                "company_id": conversation_row.get("company_id", raw_record.get("company_id", "")),
                "raw_table": RAW_MESSAGE_TABLE,
                "raw_id": raw_record.get("id", ""),
                "event_kind": "conversation_metrics_updated",
                "event_source": raw_record.get("source", ""),
                "entity_type": "conversation",
                "entity_id": conversation_row.get("id", ""),
                "conversation_id": conversation_row.get("id", ""),
                "customer_id": conversation_row.get("customer_id", ""),
                "metric_date": metric_date,
                "occurred_at": occurred_at,
                "payload": {
                    "status": metrics.get("status", ""),
                    "channel": metrics.get("channel", ""),
                    "total_messages": metrics.get("total_messages", 0),
                    "avg_sentiment": metrics.get("avg_sentiment", 0),
                },
            },
        )
        return {
            "conversation_metrics": metrics,
            "customer_summary": summary,
            "analytics_event": analytics_event,
        }
