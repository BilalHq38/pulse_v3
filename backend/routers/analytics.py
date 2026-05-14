"""routers/analytics.py — PostgreSQL."""

import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from services.ai_service.facade import generate_daily_ai_summary
from core.utils import make_id, format_response_minutes, parse_dt, sentiment_score_to_csat
from services.db_helpers import (
    r,
    rs,
    get_current_user_flexible,
    get_sentiment_metrics,
    get_average_response_minutes,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _db(req):
    return req.app.state.db


@router.get("/analytics/overview")
async def analytics_overview(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")

    total = await db.fetchval("SELECT COUNT(*) FROM conversations WHERE company_id=$1", cid)
    ai_handled = await db.fetchval("SELECT COUNT(*) FROM conversations WHERE company_id=$1 AND ai_handled=TRUE", cid)
    open_conversations = await db.fetchval("SELECT COUNT(*) FROM conversations WHERE company_id=$1 AND status='open'", cid)
    total_leads = await db.fetchval("SELECT COUNT(*) FROM leads WHERE company_id=$1", cid)
    hot_leads = await db.fetchval("SELECT COUNT(*) FROM leads WHERE company_id=$1 AND grade='hot'", cid)
    total_customers = await db.fetchval(
        "SELECT COUNT(*) FROM customers WHERE company_id=$1 AND lifecycle_stage='customer'", cid
    )
    total_products = await db.fetchval("SELECT COUNT(*) FROM company_products WHERE company_id=$1", cid)
    total_tickets = await db.fetchval("SELECT COUNT(*) FROM tickets WHERE company_id=$1", cid)
    open_tickets = await db.fetchval("SELECT COUNT(*) FROM tickets WHERE company_id=$1 AND status='open'", cid)
    resolved_tickets = await db.fetchval(
        "SELECT COUNT(*) FROM tickets WHERE company_id=$1 AND status='resolved'", cid
    )
    sentiment = await get_sentiment_metrics(db, cid)
    avg_resp = await get_average_response_minutes(db, cid)
    channel_counts = await db.fetch(
        "SELECT channel, COUNT(*) AS cnt FROM conversations WHERE company_id=$1 "
        "AND channel IN ('whatsapp','instagram','facebook','web_chat') GROUP BY channel",
        cid,
    )

    total = int(total or 0)
    ai_handled = int(ai_handled or 0)
    channel_distribution = {ch: 0 for ch in ["whatsapp", "instagram", "facebook", "web_chat"]}
    for row in channel_counts or []:
        channel_distribution[str(row["channel"])] = int(row["cnt"] or 0)

    return {
        "total_conversations": total,
        "open_conversations": int(open_conversations or 0),
        "total_leads": int(total_leads or 0),
        "hot_leads": int(hot_leads or 0),
        "total_customers": int(total_customers or 0),
        "total_products": int(total_products or 0),
        "total_tickets": int(total_tickets or 0),
        "open_tickets": int(open_tickets or 0),
        "resolved_tickets": int(resolved_tickets or 0),
        "ai_resolution_rate": round((ai_handled / total * 100) if total else 0, 1),
        "avg_response_time": format_response_minutes(avg_resp),
        "avg_response_minutes": avg_resp,
        "csat_score": sentiment["csat_score"],
        "nps_score": sentiment["nps_score"],
        "sentiment_distribution": {
            "positive": sentiment["positive"],
            "neutral": sentiment["neutral"],
            "negative": sentiment["negative"],
        },
        "channel_distribution": channel_distribution,
    }


@router.get("/analytics/conversations")
async def analytics_conversations(request: Request, days: int = 30):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    try:
        rows = await db.fetch(
            "SELECT metric_date AS date,COUNT(*) AS count,SUM(CASE WHEN ai_handled THEN 1 ELSE 0 END) AS ai_handled "
            "FROM conversation_metrics WHERE company_id=$1 GROUP BY metric_date ORDER BY date DESC LIMIT $2",
            cid,
            days,
        )
    except Exception:
        rows = []
    if not rows:
        rows = await db.fetch(
            "SELECT DATE(created_at) AS date,COUNT(*) AS count,SUM(CASE WHEN ai_handled THEN 1 ELSE 0 END) AS ai_handled "  # noqa: E501
            "FROM conversations WHERE company_id=$1 GROUP BY DATE(created_at) ORDER BY date DESC LIMIT $2",
            cid,
            days,
        )
    ordered_rows = list(reversed(rows))
    return [
        {
            "date": str(row["date"]),
            "total": int(row["count"] or 0),
            "ai_handled": int(row["ai_handled"] or 0),
            "human_handled": int(row["count"] or 0) - int(row["ai_handled"] or 0),
        }
        for row in ordered_rows
    ]


@router.get("/analytics/leads")
async def analytics_leads(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    try:
        latest_metrics = await db.fetch(
            "SELECT DISTINCT ON (lead_id) lead_id,status,source,grade "
            "FROM lead_metrics WHERE company_id=$1 "
            "ORDER BY lead_id, metric_date DESC, updated_at DESC",
            cid,
        )
    except Exception:
        latest_metrics = []
    if latest_metrics:
        status_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        grade_counts: dict[str, int] = {}
        for row in latest_metrics:
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
            source_counts[row["source"]] = source_counts.get(row["source"], 0) + 1
            grade_counts[row["grade"]] = grade_counts.get(row["grade"], 0) + 1
        return {"by_status": status_counts, "by_source": source_counts, "by_grade": grade_counts}
    sr = await db.fetch("SELECT status,COUNT(*) AS count FROM leads WHERE company_id=$1 GROUP BY status", cid)
    so = await db.fetch("SELECT source,COUNT(*) AS count FROM leads WHERE company_id=$1 GROUP BY source", cid)
    gr = await db.fetch("SELECT grade,COUNT(*) AS count FROM leads WHERE company_id=$1 GROUP BY grade", cid)
    return {
        "by_status": {r["status"]: r["count"] for r in sr},
        "by_source": {r["source"]: r["count"] for r in so},
        "by_grade": {r["grade"]: r["count"] for r in gr},
    }


@router.get("/analytics/sentiment")
async def analytics_sentiment(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    try:
        rows = await db.fetch(
            "SELECT DATE(occurred_at) AS date,AVG(sentiment_score) AS avg_score,COUNT(*) AS count "
            "FROM sentiment_logs WHERE company_id=$1 AND sentiment_score IS NOT NULL "
            "GROUP BY DATE(occurred_at) ORDER BY date DESC LIMIT 30",
            cid,
        )
    except Exception:
        rows = []
    if not rows:
        rows = await db.fetch(
            "SELECT DATE(created_at) AS date,AVG(sentiment_score) AS avg_score,COUNT(*) AS count "
            "FROM messages WHERE company_id=$1 AND sentiment_score IS NOT NULL "
            "GROUP BY DATE(created_at) ORDER BY date DESC LIMIT 30",
            cid,
        )
    return [
        {
            "date": str(row["date"]),
            "avg_sentiment": round(float(row["avg_score"] or 0), 2),
            "volume": int(row["count"] or 0),
        }
        for row in reversed(rows)
    ]


@router.get("/analytics/agents")
async def analytics_agents(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    agents = rs(
        await db.fetch("SELECT id,name,role,status FROM users WHERE company_id=$1 AND role='company_agent'", cid)
    )
    stats = []
    for agent in agents:
        cc = (
            await db.fetchval(
                "SELECT COUNT(*) FROM conversations WHERE company_id=$1 AND assigned_to=$2", cid, agent["id"]
            )
            or 0
        )
        res = (
            await db.fetchval(
                "SELECT COUNT(*) FROM conversations WHERE company_id=$1 AND assigned_to=$2 AND status='resolved'",
                cid,
                agent["id"],
            )
            or 0
        )
        rm = await get_average_response_minutes(db, cid, assigned_to=agent["id"])
        sr = (
            r(
                await db.fetchrow(
                    "SELECT AVG(sentiment_score) AS avg_score,COUNT(*) AS count FROM conversations WHERE company_id=$1 AND assigned_to=$2 AND sentiment_score IS NOT NULL",  # noqa: E501
                    cid,
                    agent["id"],
                )
            )
            or {}
        )
        stats.append(
            {
                "id": agent["id"],
                "name": agent["name"],
                "role": agent["role"],
                "conversations_handled": cc,
                "resolved": res,
                "resolution_rate": round((res / cc * 100) if cc else 0, 1),
                "avg_response_time": format_response_minutes(rm),
                "avg_response_minutes": rm,
                "csat": sentiment_score_to_csat(float(sr.get("avg_score") or 0), int(sr.get("count") or 0)),
                "status": agent.get("status", "active"),
            }
        )
    return stats


@router.get("/analytics/customer-summaries")
async def analytics_customer_summaries(
    request: Request, date: Optional[str] = None, limit: int = Query(default=50, ge=1, le=500)
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    if date:
        parsed_date = parse_dt(f"{date}T00:00:00+00:00")
        if not parsed_date:
            raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")
        return rs(
            await db.fetch(
                "SELECT * FROM customer_interaction_summaries WHERE company_id=$1 AND summary_date=$2 ORDER BY created_at DESC LIMIT $3",  # noqa: E501
                cid,
                parsed_date.date(),
                limit,
            )
        )
    return rs(
        await db.fetch(
            "SELECT * FROM customer_interaction_summaries WHERE company_id=$1 ORDER BY created_at DESC LIMIT $2",
            cid,
            limit,
        )
    )


@router.get("/analytics/daily-summaries")
async def analytics_daily_summaries(request: Request, limit: int = Query(default=30, ge=1, le=365)):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    return rs(
        await db.fetch(
            "SELECT * FROM daily_summaries WHERE company_id=$1 ORDER BY summary_date DESC LIMIT $2", cid, limit
        )
    )


@router.post("/analytics/daily-summary/generate")
async def generate_daily_summary_endpoint(request: Request, date: Optional[str] = None):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    target_date_str = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target_date_dt = parse_dt(f"{target_date_str}T00:00:00+00:00")
    if not target_date_dt:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")
    interactions = rs(
        await db.fetch(
            "SELECT * FROM customer_interaction_summaries WHERE company_id=$1 AND summary_date=$2",
            cid,
            target_date_dt.date(),
        )
    )
    summary = await generate_daily_ai_summary(target_date_str, interactions, db=db)
    summary_id = make_id()
    await db.execute(
        "INSERT INTO daily_summaries(id,company_id,summary_date,summary_text,total_interactions,total_messages,avg_sentiment,escalations,ai_handled_count,generated_at,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW()) ON CONFLICT(company_id,summary_date) DO UPDATE SET summary_text=$4,total_interactions=$5,total_messages=$6,avg_sentiment=$7,escalations=$8,ai_handled_count=$9,generated_at=NOW()",  # noqa: E501
        summary_id,
        cid,
        target_date_dt.date(),
        str(summary.get("summary") or summary.get("summary_text") or ""),
        int(summary.get("total_interactions", len(interactions))),
        int(summary.get("total_messages", 0)),
        float(summary.get("avg_sentiment", 0)),
        int(summary.get("escalations", 0)),
        int(summary.get("ai_handled_count", 0)),
    )
    return r(
        await db.fetchrow(
            "SELECT * FROM daily_summaries WHERE company_id=$1 AND summary_date=$2", cid, target_date_dt.date()
        )
    )


@router.get("/analytics/reports")
async def list_analytics_reports(
    request: Request, report_type: Optional[str] = None, limit: int = Query(default=50, ge=1, le=500)
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    if report_type:
        return rs(
            await db.fetch(
                "SELECT * FROM analytics_reports WHERE company_id=$1 AND report_type=$2 ORDER BY generated_at DESC LIMIT $3",  # noqa: E501
                cid,
                report_type,
                limit,
            )
        )
    return rs(
        await db.fetch(
            "SELECT * FROM analytics_reports WHERE company_id=$1 ORDER BY generated_at DESC LIMIT $2", cid, limit
        )
    )


@router.post("/analytics/reports")
async def create_analytics_report(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    report_id = make_id()
    await db.execute(
        "INSERT INTO analytics_reports(id,company_id,report_type,period,generated_at,created_at) VALUES($1,$2,$3,$4,NOW(),NOW())",  # noqa: E501
        report_id,
        cid,
        body.get("report_type", ""),
        body.get("period", ""),
    )
    data = body.get("report_data", {})
    if data:
        await db.executemany(
            "INSERT INTO analytics_report_data(report_id,company_id,field_name,field_value) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",  # noqa: E501
            [(report_id, cid, k, str(v)) for k, v in data.items()],
        )
    return r(await db.fetchrow("SELECT * FROM analytics_reports WHERE id=$1", report_id))


@router.get("/analytics/reports/{report_id}/metrics")
async def list_report_metrics(report_id: str, request: Request):
    db = _db(request)
    await get_current_user_flexible(request)
    return rs(await db.fetch("SELECT * FROM metrics WHERE report_id=$1 ORDER BY recorded_at DESC LIMIT 100", report_id))


@router.post("/analytics/reports/{report_id}/metrics")
async def add_report_metric(report_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    metric_id = make_id()
    await db.execute(
        "INSERT INTO metrics(id,report_id,company_id,metric_name,metric_value,unit,recorded_at,created_at) VALUES($1,$2,$3,$4,$5,$6,NOW(),NOW())",  # noqa: E501
        metric_id,
        report_id,
        cid,
        body.get("metric_name", ""),
        float(body.get("metric_value", 0)),
        body.get("unit", "count"),
    )
    return r(await db.fetchrow("SELECT * FROM metrics WHERE id=$1", metric_id))
