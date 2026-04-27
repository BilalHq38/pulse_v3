from __future__ import annotations

from services.ai_service.facade import generate_daily_ai_summary

from data_pipeline.utils import parse_timestamp, safe_float, safe_int


async def generate_daily_rollup(
    db,
    *,
    company_id: str,
    summary_date: str,
) -> dict:
    target_date = parse_timestamp(f"{summary_date}T00:00:00+00:00").date()
    interactions = [
        dict(row)
        for row in await db.fetch(
            "SELECT * FROM customer_interaction_summaries WHERE company_id=$1 AND summary_date=$2 "
            "ORDER BY created_at ASC",
            company_id,
            target_date,
        )
    ]
    conversation_metrics = [
        dict(row)
        for row in await db.fetch(
            "SELECT * FROM conversation_metrics WHERE company_id=$1 AND metric_date=$2 ORDER BY updated_at DESC",
            company_id,
            target_date,
        )
    ]
    summary = await generate_daily_ai_summary(
        target_date.isoformat(),
        interactions,
        db=db,
    )
    total_messages = safe_int(
        summary.get("total_messages"),
        sum(safe_int(item.get("total_messages")) for item in interactions)
        or sum(safe_int(item.get("total_messages")) for item in conversation_metrics),
    )
    total_interactions = safe_int(
        summary.get("total_interactions"),
        len(interactions) or len(conversation_metrics),
    )
    avg_sentiment = (
        safe_float(
            summary.get("avg_sentiment"),
            (
                (sum(float(item.get("avg_sentiment") or 0.0) for item in interactions) / len(interactions))
                if interactions
                else (
                    (
                        sum(float(item.get("avg_sentiment") or 0.0) for item in conversation_metrics)
                        / len(conversation_metrics)
                    )
                    if conversation_metrics
                    else 0.0
                )
            ),
        )
        or 0.0
    )
    escalations = safe_int(
        summary.get("escalations"),
        sum(1 for item in interactions if item.get("escalated"))
        or sum(1 for item in conversation_metrics if item.get("escalated")),
    )
    ai_handled_count = safe_int(
        summary.get("ai_handled_count"),
        sum(1 for item in interactions if item.get("ai_handled"))
        or sum(1 for item in conversation_metrics if item.get("ai_handled")),
    )
    daily_summary_id = str(
        await db.fetchval(
            "SELECT id FROM daily_summaries WHERE company_id=$1 AND summary_date=$2 LIMIT 1",
            company_id,
            target_date,
        )
        or ""
    )
    if daily_summary_id:
        await db.execute(
            "UPDATE daily_summaries SET summary_text=$1,total_interactions=$2,total_messages=$3,"
            "avg_sentiment=$4,escalations=$5,ai_handled_count=$6,generated_at=NOW() "
            "WHERE id=$7",
            str(summary.get("summary") or summary.get("summary_text") or ""),
            total_interactions,
            total_messages,
            avg_sentiment,
            escalations,
            ai_handled_count,
            daily_summary_id,
        )
    else:
        from core.utils import make_id

        daily_summary_id = make_id()
        await db.execute(
            "INSERT INTO daily_summaries("
            "id,company_id,summary_date,summary_text,total_interactions,total_messages,avg_sentiment,"
            "escalations,ai_handled_count,generated_at,created_at"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW())",
            daily_summary_id,
            company_id,
            target_date,
            str(summary.get("summary") or summary.get("summary_text") or ""),
            total_interactions,
            total_messages,
            avg_sentiment,
            escalations,
            ai_handled_count,
        )
    row = await db.fetchrow(
        "SELECT * FROM daily_summaries WHERE id=$1 LIMIT 1",
        daily_summary_id,
    )
    return dict(row) if row else {}
