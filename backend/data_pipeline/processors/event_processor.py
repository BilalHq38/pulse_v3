from __future__ import annotations

from data_pipeline.constants import RAW_EVENT_TABLE
from data_pipeline.processors.base import ProcessorBase
from data_pipeline.storage import upsert_analytics_event
from data_pipeline.utils import metric_date_for, parse_timestamp


class EventProcessor(ProcessorBase):
    async def process(self, raw_record: dict) -> dict:
        payload = dict(raw_record.get("payload") or {})
        metadata = dict(raw_record.get("metadata") or {})
        occurred_at = parse_timestamp(raw_record.get("occurred_at"))
        event_kind = (
            str(metadata.get("event_kind") or raw_record.get("event_type") or raw_record.get("source") or "event")
            .strip()
            .lower()
        )
        entity_type = str(metadata.get("entity_type") or payload.get("entity_type") or "event").strip().lower()
        entity_id = str(
            metadata.get("entity_id")
            or payload.get("entity_id")
            or raw_record.get("external_id")
            or raw_record.get("event_id")
            or ""
        ).strip()
        analytics_event = await upsert_analytics_event(
            self.db,
            {
                "company_id": raw_record.get("company_id", ""),
                "raw_table": RAW_EVENT_TABLE,
                "raw_id": raw_record.get("id", ""),
                "event_kind": event_kind,
                "event_source": raw_record.get("source", ""),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "conversation_id": str(metadata.get("conversation_id") or payload.get("conversation_id") or ""),
                "lead_id": str(metadata.get("lead_id") or payload.get("lead_id") or ""),
                "customer_id": str(metadata.get("customer_id") or payload.get("customer_id") or ""),
                "metric_date": metric_date_for(occurred_at),
                "occurred_at": occurred_at,
                "payload": {
                    "event_type": raw_record.get("event_type", ""),
                    "event_id": raw_record.get("event_id", ""),
                    "external_id": raw_record.get("external_id", ""),
                    "metadata": metadata,
                    "payload": payload,
                },
            },
        )
        return {"analytics_event": analytics_event}
