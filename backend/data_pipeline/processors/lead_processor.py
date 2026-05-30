from __future__ import annotations

from data_pipeline.constants import RAW_LEAD_TABLE
from data_pipeline.processors.base import ProcessorBase, row_to_dict
from data_pipeline.storage import upsert_analytics_event, upsert_lead_metrics
from data_pipeline.utils import (
    metric_date_for,
    normalize_email,
    normalize_phone,
    parse_timestamp,
    safe_int,
)


def _deterministic_lead_score(lead_data: dict) -> dict:
    score = safe_int(lead_data.get("score"), 0)
    if score <= 0:
        score = 40
        if lead_data.get("email") and lead_data.get("phone"):
            score += 20
        elif lead_data.get("email") or lead_data.get("phone"):
            score += 10
        if lead_data.get("name"):
            score += 5
        # Sentiment adjustment: satisfied customers convert at higher rates;
        # dissatisfied customers need recovery actions before scoring them warm.
        sentiment = str(lead_data.get("sentiment_label") or lead_data.get("sentiment") or "").strip().lower()
        if sentiment in {"positive", "satisfied", "happy"}:
            score += 10
        elif sentiment in {"negative", "dissatisfied", "angry", "frustrated"}:
            score -= 15
    score = max(0, min(100, score))
    grade = str(lead_data.get("grade") or "").strip().lower()
    if grade not in {"hot", "warm", "cold"}:
        grade = "hot" if score >= 80 else "warm" if score >= 60 else "cold"
    sentiment_note = ""
    raw_sentiment = str(lead_data.get("sentiment_label") or lead_data.get("sentiment") or "").strip().lower()
    if raw_sentiment in {"negative", "dissatisfied", "angry", "frustrated"}:
        sentiment_note = " Negative sentiment detected — prioritise issue resolution before nurturing."
    return {
        "score": score,
        "grade": grade,
        "phase": str(lead_data.get("phase") or "awareness").strip().lower(),
        "reasoning": f"Local lead metric snapshot; LLM scoring is not run from inbound message pipeline.{sentiment_note}",
        "next_action": str(lead_data.get("next_action") or "Review lead activity"),
    }


class LeadProcessor(ProcessorBase):
    async def process(self, raw_record: dict) -> dict:
        payload = dict(raw_record.get("payload") or {})
        metadata = dict(raw_record.get("metadata") or {})
        lead_id = str(raw_record.get("canonical_lead_id") or payload.get("id") or "").strip()
        lead_row = {}
        if lead_id:
            lead_row = row_to_dict(await self.db.fetchrow("SELECT * FROM leads WHERE id=$1 LIMIT 1", lead_id))

        lead_data = {**payload, **lead_row}
        lead_data["company_id"] = raw_record.get("company_id", lead_data.get("company_id", ""))
        lead_data["id"] = lead_id or str(lead_data.get("id") or "")
        lead_data["email"] = normalize_email(lead_data.get("email"))
        lead_data["phone"] = normalize_phone(lead_data.get("phone"))
        lead_data["name"] = str(lead_data.get("name") or "").strip()
        lead_data["source"] = str(lead_data.get("source") or raw_record.get("source") or "lead").strip().lower()
        lead_data["status"] = str(lead_data.get("status") or "new").strip().lower()
        lead_data["phase"] = str(lead_data.get("phase") or "awareness").strip().lower()
        lead_data["grade"] = str(lead_data.get("grade") or "cold").strip().lower()

        duplicate_count = 0
        duplicate_filters = []
        duplicate_args = [raw_record.get("company_id", "")]
        if lead_data["email"]:
            duplicate_filters.append(f"LOWER(email)=${len(duplicate_args) + 1}")
            duplicate_args.append(lead_data["email"])
        if lead_data["phone"]:
            duplicate_filters.append(f"phone=${len(duplicate_args) + 1}")
            duplicate_args.append(lead_data["phone"])
        if duplicate_filters:
            exclusion = ""
            if lead_data["id"]:
                exclusion = f" AND id<>${len(duplicate_args) + 1}"
                duplicate_args.append(lead_data["id"])
            duplicate_count = int(
                await self.db.fetchval(
                    f"SELECT COUNT(*) FROM leads WHERE company_id=$1 AND ({' OR '.join(duplicate_filters)}){exclusion}",
                    *duplicate_args,
                )
                or 0
            )
        converted_count = 0
        if lead_data["id"]:
            converted_count = int(
                await self.db.fetchval(
                    "SELECT COUNT(*) FROM customers WHERE company_id=$1 AND lead_id=$2",
                    raw_record.get("company_id", ""),
                    lead_data["id"],
                )
                or 0
            )
        is_converted = bool(metadata.get("is_converted")) or converted_count > 0
        ai_score = _deterministic_lead_score(lead_data)
        occurred_at = parse_timestamp(raw_record.get("occurred_at"))
        metric = await upsert_lead_metrics(
            self.db,
            {
                "company_id": raw_record.get("company_id", ""),
                "lead_id": lead_data.get("id") or raw_record.get("dedupe_key", ""),
                "metric_date": metric_date_for(occurred_at),
                "source": lead_data.get("source", ""),
                "status": lead_data.get("status", ""),
                "phase": lead_data.get("phase", ""),
                "grade": lead_data.get("grade", ""),
                "name": lead_data.get("name", ""),
                "email": lead_data.get("email", ""),
                "phone": lead_data.get("phone", ""),
                "current_score": safe_int(lead_data.get("score"), 0),
                "recommended_score": safe_int(
                    ai_score.get("score"),
                    safe_int(lead_data.get("score"), 0),
                ),
                "recommended_grade": str(ai_score.get("grade") or lead_data.get("grade") or ""),
                "scoring_reason": str(ai_score.get("reasoning") or lead_data.get("scoring_reason") or ""),
                "next_action": str(ai_score.get("next_action") or lead_data.get("next_action") or ""),
                "duplicate_count": duplicate_count,
                "is_duplicate": duplicate_count > 0,
                "is_converted": is_converted,
                "payload": {
                    "raw_source": raw_record.get("source", ""),
                    "action": metadata.get("action", "lead_snapshot"),
                    "current_phase": lead_data.get("phase", ""),
                    "recommended_phase": ai_score.get("phase", ""),
                    "raw_payload": payload,
                },
            },
        )
        event_kind = str(metadata.get("action") or "lead_snapshot").strip().lower()
        analytics_event = await upsert_analytics_event(
            self.db,
            {
                "company_id": raw_record.get("company_id", ""),
                "raw_table": RAW_LEAD_TABLE,
                "raw_id": raw_record.get("id", ""),
                "event_kind": event_kind,
                "event_source": raw_record.get("source", ""),
                "entity_type": "lead",
                "entity_id": lead_data.get("id") or raw_record.get("dedupe_key", ""),
                "lead_id": lead_data.get("id") or raw_record.get("dedupe_key", ""),
                "metric_date": metric_date_for(occurred_at),
                "occurred_at": occurred_at,
                "payload": {
                    "status": metric.get("status", ""),
                    "phase": metric.get("phase", ""),
                    "grade": metric.get("grade", ""),
                    "recommended_score": metric.get("recommended_score", 0),
                    "is_duplicate": metric.get("is_duplicate", False),
                    "is_converted": metric.get("is_converted", False),
                },
            },
        )
        return {
            "lead_metrics": metric,
            "analytics_event": analytics_event,
            "ai_score": ai_score,
        }
