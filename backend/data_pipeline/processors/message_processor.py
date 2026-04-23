from __future__ import annotations

import asyncio

from services.ai_service.facade import generate_combined_ai_analysis

from data_pipeline.constants import RAW_MESSAGE_TABLE
from data_pipeline.processors.base import ProcessorBase, row_to_dict
from data_pipeline.processors.conversation_processor import ConversationProcessor
from data_pipeline.storage import (
    upsert_analytics_event,
    upsert_legacy_sentiment_record,
    upsert_sentiment_log,
)
from data_pipeline.utils import metric_date_for, parse_timestamp, safe_float


class MessageProcessor(ProcessorBase):
    async def process(self, raw_record: dict) -> dict:
        message_id = str(raw_record.get("canonical_message_id") or "").strip()
        payload = dict(raw_record.get("payload") or {})
        metadata = dict(raw_record.get("metadata") or {})
        message = await self.fetch_message_with_attachments(message_id) if message_id else {}
        if not message:
            occurred_at = parse_timestamp(raw_record.get("occurred_at"))
            analytics_event = await upsert_analytics_event(
                self.db,
                {
                    "company_id": raw_record.get("company_id", ""),
                    "raw_table": RAW_MESSAGE_TABLE,
                    "raw_id": raw_record.get("id", ""),
                    "event_kind": str(metadata.get("action") or "message_observed").strip().lower(),
                    "event_source": raw_record.get("source", ""),
                    "entity_type": "message",
                    "entity_id": message_id,
                    "conversation_id": str(metadata.get("conversation_id") or ""),
                    "customer_id": str(metadata.get("customer_id") or ""),
                    "metric_date": metric_date_for(occurred_at),
                    "occurred_at": occurred_at,
                    "payload": payload,
                },
            )
            return {"analytics_event": analytics_event}

        conversation = row_to_dict(
            await self.db.fetchrow(
                "SELECT * FROM conversations WHERE id=$1 LIMIT 1",
                message.get("conversation_id", ""),
            )
        )
        customer = {}
        if conversation.get("customer_id"):
            customer = row_to_dict(
                await self.db.fetchrow(
                    "SELECT * FROM customers WHERE id=$1 LIMIT 1",
                    conversation.get("customer_id", ""),
                )
            )

        sentiment_score = safe_float(message.get("sentiment_score"))
        sentiment_confidence = safe_float(message.get("sentiment_confidence"))
        sentiment_label = str(message.get("sentiment_emotion") or "").strip().lower()
        intent_type = str(message.get("intent_type") or "").strip().lower()
        conversation_sentiment = {}

        if (
            str(message.get("sender_type") or "").strip().lower() == "customer"
            and str(message.get("content") or "").strip()
            and (sentiment_score is None or not sentiment_label or not intent_type)
        ):
            combined = {}
            try:
                combined = await asyncio.wait_for(
                    generate_combined_ai_analysis(
                        customer_message=message.get("content", ""),
                        conversation_context=await self.fetch_conversation_messages(
                            message.get("conversation_id", ""),
                            limit=20,
                        ),
                        customer_info=customer or None,
                        company_id=raw_record.get("company_id", ""),
                        db=self.db,
                    ),
                    timeout=8.0,
                )
            except Exception:
                combined = {}
            sentiment = dict(combined.get("sentiment") or {})
            conversation_sentiment = dict(combined.get("conversation_sentiment") or {})
            intent = dict(combined.get("intent") or {})
            sentiment_score = sentiment_score if sentiment_score is not None else safe_float(sentiment.get("score"))
            sentiment_confidence = (
                sentiment_confidence if sentiment_confidence is not None else safe_float(sentiment.get("confidence"))
            )
            sentiment_label = (
                sentiment_label
                or str(sentiment.get("emotion") or sentiment.get("sentiment_label") or "neutral").strip().lower()
            )
            intent_type = intent_type or str(intent.get("intent") or "").strip().lower()
            await self.db.execute(
                "UPDATE messages SET "
                "sentiment_score=COALESCE(sentiment_score,$1),"
                "sentiment_emotion=CASE WHEN COALESCE(sentiment_emotion,'')='' THEN $2 ELSE sentiment_emotion END,"
                "sentiment_confidence=COALESCE(sentiment_confidence,$3),"
                "intent_type=CASE WHEN COALESCE(intent_type,'')='' THEN $4 ELSE intent_type END,"
                "intent_confidence=COALESCE(intent_confidence,$5) "
                "WHERE id=$6",
                sentiment_score,
                sentiment_label or "neutral",
                sentiment_confidence,
                intent_type,
                safe_float(intent.get("confidence")),
                message["id"],
            )
            if conversation_sentiment and conversation.get("id"):
                conversation_score = safe_float(conversation_sentiment.get("score"))
                conversation_label = (
                    str(
                        conversation_sentiment.get("sentiment_label")
                        or conversation_sentiment.get("label")
                        or conversation_sentiment.get("emotion")
                        or sentiment_label
                        or "neutral"
                    )
                    .strip()
                    .lower()
                )
                await self.db.execute(
                    "UPDATE conversations SET sentiment_score=$1,sentiment_label=$2,updated_at=NOW() WHERE id=$3",
                    conversation_score,
                    conversation_label,
                    conversation["id"],
                )
            message = await self.fetch_message_with_attachments(message["id"])

        sentiment_log = {}
        if str(message.get("content") or "").strip() and (
            sentiment_score is not None or sentiment_label or intent_type
        ):
            sentiment_log = await upsert_sentiment_log(
                self.db,
                {
                    "company_id": raw_record.get("company_id", ""),
                    "raw_table": RAW_MESSAGE_TABLE,
                    "raw_id": raw_record.get("id", ""),
                    "source": raw_record.get("source", ""),
                    "entity_type": "message",
                    "entity_id": message.get("id", ""),
                    "conversation_id": message.get("conversation_id", ""),
                    "message_id": message.get("id", ""),
                    "sentiment_label": sentiment_label or "neutral",
                    "sentiment_score": sentiment_score,
                    "emotion": sentiment_label or "neutral",
                    "confidence": sentiment_confidence,
                    "intent_type": intent_type,
                    "analyzed_text": message.get("content", ""),
                    "occurred_at": parse_timestamp(message.get("created_at")),
                },
            )
            await upsert_legacy_sentiment_record(
                self.db,
                {
                    "company_id": raw_record.get("company_id", ""),
                    "entity_id": message.get("conversation_id", ""),
                    "entity_type": "conversation",
                    "analyzed_text": message.get("content", ""),
                    "sentiment_score": safe_float(
                        (conversation_sentiment or {}).get("score"),
                        sentiment_score,
                    ),
                    "sentiment_label": str(
                        (conversation_sentiment or {}).get("sentiment_label")
                        or (conversation_sentiment or {}).get("emotion")
                        or sentiment_label
                        or "neutral"
                    ),
                    "confidence": sentiment_confidence,
                },
            )

        sender_type = str(message.get("sender_type") or "").strip().lower()
        if sender_type == "customer":
            event_kind = "message_received"
        elif sender_type == "ai":
            event_kind = "ai_response_generated"
        elif sender_type == "agent":
            event_kind = "message_sent"
        else:
            event_kind = "system_message_observed"
        analytics_event = await upsert_analytics_event(
            self.db,
            {
                "company_id": raw_record.get("company_id", ""),
                "raw_table": RAW_MESSAGE_TABLE,
                "raw_id": raw_record.get("id", ""),
                "event_kind": event_kind,
                "event_source": raw_record.get("source", ""),
                "entity_type": "message",
                "entity_id": message.get("id", ""),
                "conversation_id": message.get("conversation_id", ""),
                "customer_id": conversation.get("customer_id", ""),
                "metric_date": metric_date_for(parse_timestamp(message.get("created_at"))),
                "occurred_at": parse_timestamp(message.get("created_at")),
                "payload": {
                    "sender_type": sender_type,
                    "sender_name": message.get("sender_name", ""),
                    "has_attachments": bool(message.get("attachments")),
                    "intent_type": intent_type,
                    "sentiment_label": sentiment_label or "neutral",
                },
            },
        )
        conversation_results = await ConversationProcessor(self.db).process(
            raw_record,
            message=message,
            conversation=conversation,
        )
        return {
            "message": message,
            "sentiment_log": sentiment_log,
            "analytics_event": analytics_event,
            **conversation_results,
        }
