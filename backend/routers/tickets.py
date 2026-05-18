"""routers/tickets.py + knowledge_base — PostgreSQL."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from core.utils import make_id, now_ts
from models.reference_data import resolve_company_reference_id
from services.db_helpers import r, rs, get_current_user_flexible, get_company_id

logger = logging.getLogger(__name__)
router = APIRouter()

TICKET_UPDATE_FIELDS = {
    "subject",
    "description",
    "priority",
    "category",
    "status",
    "status_id",
    "assigned_to",
    "assigned_name",
    "resolution",
    "sla_deadline",
    "resolved_at",
}

KNOWLEDGE_BASE_UPDATE_FIELDS = {
    "title",
    "content",
    "category",
    "key_points",
    "is_prebuilt",
    "ai_context_enabled",
    "author_name",
}


def _db(req):
    return req.app.state.db


PREBUILT_AI_ARTICLES = [
    {
        "title": "Brand Voice Playbook",
        "category": "ai",
        "content": "Use a clear, helpful, confident tone. Keep replies concise, answer directly, and avoid jargon unless the customer uses it first.",  # noqa: E501
        "key_points": "Used as AI context; can be used directly; can be modified for company tone; can be upgraded for advanced brand rules.",  # noqa: E501
        "tags": ["prebuilt", "ai-context", "brand-voice"],
    },
    {
        "title": "Lead Qualification Checklist",
        "category": "sales",
        "content": "Identify budget, decision-maker, timeline, use case, and urgency before recommending the next step or demo.",  # noqa: E501
        "key_points": "Used as AI context; ready to use immediately; edit based on your sales process; extend with custom qualification rules.",  # noqa: E501
        "tags": ["prebuilt", "ai-context", "sales"],
    },
    {
        "title": "Customer Support De-escalation Guide",
        "category": "troubleshooting",
        "content": "Acknowledge frustration, restate the problem, avoid blame, offer the next concrete step, and escalate when risk or toxicity is detected.",  # noqa: E501
        "key_points": "Used as AI context; directly usable for support; update for your support policy; customize for strict escalation rules.",  # noqa: E501
        "tags": ["prebuilt", "ai-context", "de-escalation"],
    },
]


async def _ensure_prebuilt_ai_articles(db, company_id: str):
    existing_titles = {
        row["title"]
        for row in await db.fetch(
            "SELECT title FROM knowledge_base WHERE company_id=$1 AND is_prebuilt=TRUE",
            company_id,
        )
    }
    for article in PREBUILT_AI_ARTICLES:
        if article["title"] in existing_titles:
            continue
        doc_id = make_id()
        await db.execute(
            "INSERT INTO knowledge_base(id,company_id,title,content,category,key_points,is_prebuilt,ai_context_enabled,author_id,author_name,views,created_at,updated_at) "  # noqa: E501
            "VALUES($1,$2,$3,$4,$5,$6,TRUE,TRUE,'system','Pulse Engine',0,NOW(),NOW())",
            doc_id,
            company_id,
            article["title"],
            article["content"],
            article["category"],
            article["key_points"],
        )
        for tag in article["tags"]:
            await db.execute(
                "INSERT INTO knowledge_base_tags(kb_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                doc_id,
                tag,
            )


@router.get("/tickets")
async def list_tickets(
    request: Request,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    category: Optional[str] = None,
    assigned_to: Optional[str] = None,
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    sql = "SELECT * FROM tickets WHERE company_id=$1"
    args = [cid]
    for col, val in [
        ("status", status),
        ("priority", priority),
        ("category", category),
        ("assigned_to", assigned_to),
    ]:
        if val:
            args.append(val)
            sql += f" AND {col}=${len(args)}"
    sql += " ORDER BY created_at DESC LIMIT 500"
    tickets = rs(await db.fetch(sql, *args))
    for ticket in tickets:
        ticket["notes"] = rs(
            await db.fetch(
                "SELECT * FROM ticket_notes WHERE ticket_id=$1 ORDER BY created_at",
                ticket["id"],
            )
        )
    return tickets


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    ticket = r(
        await db.fetchrow(
            "SELECT * FROM tickets WHERE id=$1 AND company_id=$2 LIMIT 1",
            ticket_id,
            cid,
        )
    )
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    ticket["notes"] = rs(
        await db.fetch(
            "SELECT * FROM ticket_notes WHERE ticket_id=$1 ORDER BY created_at",
            ticket_id,
        )
    )
    return ticket


@router.post("/tickets")
async def create_ticket(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    subject = str(body.get("subject", "") or "").strip()
    if not subject:
        raise HTTPException(400, "subject is required")
    ticket_id = make_id()
    ticket_number = f"TKT-{str(uuid.uuid4())[:8].upper()}"
    status_id = await resolve_company_reference_id(
        db, cid, "ticket_statuses", "status_name", "open", {"color_code": "#10b981"}
    )
    priority = body.get("priority", "medium")
    sla = datetime.now(timezone.utc) + timedelta(hours=24 if priority == "high" else 48)
    await db.execute(
        "INSERT INTO tickets(id,ticket_number,conversation_id,customer_id,company_id,subject,description,priority,category,status,status_id,assigned_to,assigned_name,resolution,sla_deadline,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'open',$10,$11,$12,'',$13,NOW(),NOW())",  # noqa: E501
        ticket_id,
        ticket_number,
        body.get("conversation_id", ""),
        body.get("customer_id", ""),
        cid,
        subject,
        body.get("description", ""),
        priority,
        body.get("category", "general"),
        status_id,
        cu["sub"],
        cu.get("name", ""),
        sla,
    )
    return r(await db.fetchrow("SELECT * FROM tickets WHERE id=$1", ticket_id))


@router.put("/tickets/{ticket_id}")
async def update_ticket(ticket_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    body.pop("_id", None)
    if "status" in body:
        body["status_id"] = await resolve_company_reference_id(
            db,
            cid,
            "ticket_statuses",
            "status_name",
            body["status"],
            {"color_code": "#94a3b8"},
        )
    safe_body = {k: v for k, v in body.items() if k in TICKET_UPDATE_FIELDS}
    if body and not safe_body:
        raise HTTPException(400, "No valid fields provided")
    safe_body["updated_at"] = now_ts()
    if safe_body.get("status") == "resolved":
        safe_body["resolved_at"] = now_ts()
    columns = list(safe_body.keys())
    set_parts = ", ".join(f"{k}=${i + 3}" for i, k in enumerate(columns))
    values = [safe_body[col] for col in columns]
    await db.execute(
        f"UPDATE tickets SET {set_parts} WHERE id=$1 AND company_id=$2",
        ticket_id,
        cid,
        *values,
    )
    ticket = r(await db.fetchrow("SELECT * FROM tickets WHERE id=$1", ticket_id))
    if ticket:
        ticket["notes"] = rs(
            await db.fetch(
                "SELECT * FROM ticket_notes WHERE ticket_id=$1 ORDER BY created_at",
                ticket_id,
            )
        )
    return ticket


@router.post("/tickets/{ticket_id}/notes")
async def add_ticket_note(ticket_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    content = str(body.get("content", "") or "").strip()
    if not content:
        raise HTTPException(400, "content is required")
    if not await db.fetchval("SELECT id FROM tickets WHERE id=$1 AND company_id=$2", ticket_id, cid):
        raise HTTPException(404, "Ticket not found")
    note_id = make_id()
    await db.execute(
        "INSERT INTO ticket_notes(id,company_id,ticket_id,content,author_id,author_name,created_at) VALUES($1,$2,$3,$4,$5,$6,NOW())",  # noqa: E501
        note_id,
        cid,
        ticket_id,
        content,
        cu["sub"],
        cu.get("name", ""),
    )
    await db.execute("UPDATE tickets SET updated_at=NOW() WHERE id=$1", ticket_id)
    return r(await db.fetchrow("SELECT * FROM ticket_notes WHERE id=$1", note_id))


# ── Knowledge Base ─────────────────────────────────────────────
@router.get("/knowledge-base")
async def list_kb_docs(request: Request, category: Optional[str] = None, search: Optional[str] = None):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    await _ensure_prebuilt_ai_articles(db, cid)
    sql = "SELECT k.*,ARRAY(SELECT tag FROM knowledge_base_tags WHERE kb_id=k.id) AS tags FROM knowledge_base k WHERE k.company_id=$1"  # noqa: E501
    args = [cid]
    if category:
        args.append(category)
        sql += f" AND k.category=${len(args)}"
    if search:
        args.append(f"%{search}%")
        title_idx = len(args)
        args.append(f"%{search}%")
        content_idx = len(args)
        sql += f" AND (k.title ILIKE ${title_idx} OR k.content ILIKE ${content_idx})"
    sql += " ORDER BY k.updated_at DESC LIMIT 200"
    return rs(await db.fetch(sql, *args))


@router.get("/knowledge-base/{doc_id}")
async def get_kb_doc(doc_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    doc = r(
        await db.fetchrow(
            "SELECT * FROM knowledge_base WHERE id=$1 AND company_id=$2 LIMIT 1",
            doc_id,
            cid,
        )
    )
    if not doc:
        raise HTTPException(404, "Document not found")
    doc["tags"] = [row["tag"] for row in await db.fetch("SELECT tag FROM knowledge_base_tags WHERE kb_id=$1", doc_id)]
    return doc


@router.post("/knowledge-base")
async def create_kb_doc(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    title = str(body.get("title", "") or "").strip()
    content = str(body.get("content", "") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    if not content:
        raise HTTPException(400, "content is required")
    doc_id = make_id()
    await db.execute(
        "INSERT INTO knowledge_base(id,company_id,title,content,category,key_points,is_prebuilt,ai_context_enabled,author_id,author_name,views,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,0,NOW(),NOW())",  # noqa: E501
        doc_id,
        cid,
        title,
        content,
        body.get("category", "general"),
        body.get("key_points", ""),
        bool(body.get("is_prebuilt", False)),
        bool(body.get("ai_context_enabled", True)),
        cu["sub"],
        cu.get("name", ""),
    )
    for tag in body.get("tags") or []:
        await db.execute(
            "INSERT INTO knowledge_base_tags(kb_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
            doc_id,
            tag,
        )
    return r(await db.fetchrow("SELECT * FROM knowledge_base WHERE id=$1", doc_id))


@router.put("/knowledge-base/{doc_id}")
async def update_kb_doc(doc_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    body = await request.json()
    body.pop("_id", None)
    tags = body.pop("tags", None)
    safe_body = {k: v for k, v in body.items() if k in KNOWLEDGE_BASE_UPDATE_FIELDS}
    if body and not safe_body:
        raise HTTPException(400, "No valid fields provided")
    safe_body["updated_at"] = now_ts()
    columns = list(safe_body.keys())
    set_parts = ", ".join(f"{k}=${i + 3}" for i, k in enumerate(columns))
    values = [safe_body[col] for col in columns]
    await db.execute(
        f"UPDATE knowledge_base SET {set_parts} WHERE id=$1 AND company_id=$2",
        doc_id,
        cid,
        *values,
    )
    if tags is not None:
        await db.execute("DELETE FROM knowledge_base_tags WHERE kb_id=$1", doc_id)
        if tags:
            await db.executemany(
                "INSERT INTO knowledge_base_tags(kb_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                [(doc_id, t) for t in tags],
            )
    return r(await db.fetchrow("SELECT * FROM knowledge_base WHERE id=$1", doc_id))


@router.delete("/knowledge-base/{doc_id}")
async def delete_kb_doc(doc_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    doc = r(
        await db.fetchrow(
            "SELECT id,is_prebuilt FROM knowledge_base WHERE id=$1 AND company_id=$2 LIMIT 1",
            doc_id,
            cid,
        )
    )
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.get("is_prebuilt"):
        raise HTTPException(
            403,
            "Prebuilt knowledge base articles cannot be deleted. Edit the article or disable AI context instead.",
        )
    await db.execute("DELETE FROM knowledge_base WHERE id=$1 AND company_id=$2", doc_id, cid)
    return {"status": "deleted"}
