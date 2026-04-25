"""routers/leads.py — PostgreSQL version."""

import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from agent_orchestrator.schemas import LeadWorkflowRequest
from channel_layer.router import get_outbound_router
from channel_layer.schemas import ChannelType
from services.ai_service.facade import (
    generate_nurture_message,
    get_company_knowledge,
)
from services.agent_orchestrator.facade import orchestrate_lead_workflow
from core.socket import emit_new_message
from core.utils import make_id, now_ts, normalize_reference_key
from models.reference_data import resolve_company_reference_id
from shared.database import create_detached_task
from shared.tracing import current_trace_context
from services.db_helpers import (
    r,
    rs,
    get_current_user_flexible,
    require_roles,
    get_company_id,
    convert_lead_to_customer_state,
    create_notification,
    record_system_log,
    get_or_create_contact_conversation,
    get_or_create_customer_from_contact,
    persist_chat_history,
)

logger = logging.getLogger(__name__)
router = APIRouter()

LEAD_UPDATE_FIELDS = {
    "name",
    "email",
    "phone",
    "source",
    "source_id",
    "status",
    "status_id",
    "score",
    "grade",
    "phase",
    "notes",
    "assigned_to",
    "assigned_name",
    "customer_company_name",
    "next_action",
    "scoring_reason",
    "priority",
}


def _db(req):
    return req.app.state.db


def _trace_id_from_context() -> str:
    try:
        context = current_trace_context()
    except Exception:
        return ""
    return str(getattr(context, "trace_id", "") or "").strip()


def _channel_type_from_name(channel: str) -> Optional[ChannelType]:
    mapping = {
        "whatsapp": ChannelType.WHATSAPP,
        "facebook": ChannelType.FACEBOOK,
        "instagram": ChannelType.INSTAGRAM,
        "web_chat": ChannelType.WEB_CHAT,
        "email": ChannelType.EMAIL,
    }
    return mapping.get(str(channel or "").strip().lower())


def _resolve_lead_recipient(channel: str, customer: dict, lead: dict) -> str:
    normalized = str(channel or "").strip().lower()
    lead_social_profiles = lead.get("social_profiles") if isinstance(lead.get("social_profiles"), dict) else {}
    customer_social_profiles = (
        customer.get("social_profiles") if isinstance(customer.get("social_profiles"), dict) else {}
    )
    if normalized == "whatsapp":
        return str(customer.get("phone") or lead.get("phone") or "").strip()
    if normalized in {"facebook", "instagram"}:
        return str(
            lead.get("channel_recipient_id")
            or lead.get("external_recipient_id")
            or lead.get("channel_user_id")
            or customer.get("channel_recipient_id")
            or customer.get("external_recipient_id")
            or customer_social_profiles.get(normalized)
            or lead_social_profiles.get(normalized)
            or ""
        ).strip()
    if normalized == "web_chat":
        return str(customer.get("email") or customer.get("id") or lead.get("email") or "").strip()
    if normalized == "email":
        return str(customer.get("email") or lead.get("email") or "").strip().lower()
    return ""


async def _send_outbound_via_channel_layer(
    *,
    db,
    company_id: str,
    channel: str,
    recipient_id: str,
    content: str,
    conversation_id: str,
    db_message_id: str,
    metadata: dict | None = None,
    subject: str = "",
) -> tuple[bool, str]:
    channel_type = _channel_type_from_name(channel)
    if not channel_type:
        return False, f"Unsupported outbound channel: {channel}"

    recipient = str(recipient_id or "").strip()
    if not recipient:
        return False, "Missing outbound recipient"

    message_metadata = dict(metadata or {})
    if not message_metadata.get("trace_id"):
        message_metadata["trace_id"] = _trace_id_from_context()

    result = await get_outbound_router().send_to_channel(
        tenant_id=str(company_id or "").strip(),
        channel_type=channel_type,
        external_user_id=recipient,
        content=content,
        subject=str(subject or "").strip(),
        db=db,
        metadata=message_metadata,
        conversation_id=conversation_id,
        db_message_id=db_message_id,
    )
    return bool(result.success), str(result.error or "")


async def _capture_lead_snapshot(
    db,
    lead: dict | None,
    *,
    source: str,
    action: str,
    extra_metadata: dict | None = None,
) -> None:
    if not lead:
        return
    try:
        from data_pipeline.ingestion.raw_store import capture_raw_lead

        await capture_raw_lead(
            db,
            company_id=str(lead.get("company_id") or "").strip(),
            lead=lead,
            source=source,
            metadata={"action": action, **dict(extra_metadata or {})},
        )
    except Exception as exc:
        logger.warning(
            "lead pipeline capture failed lead_id=%s action=%s error=%s",
            lead.get("id", ""),
            action,
            exc,
        )


def _serialize_lead(lead: dict | None) -> dict | None:
    if not lead:
        return lead
    lead["company"] = lead.get("customer_company_name", "")
    return lead


def _normalize_lead_channel(lead: dict) -> str:
    preferred = str(lead.get("source") or "").strip().lower()
    if preferred in {"whatsapp", "facebook", "instagram", "web_chat", "email"}:
        return preferred
    channels = lead.get("channels") or []
    for channel in channels:
        key = str(channel if isinstance(channel, str) else channel.get("channel", "")).strip().lower()
        if key in {"whatsapp", "facebook", "instagram", "web_chat", "email"}:
            return key
    if lead.get("email"):
        return "email"
    return "whatsapp" if lead.get("phone") else "web_chat"


async def _create_email_only_customer(db, lead: dict, current_user: dict) -> dict:
    cid = get_company_id(current_user)
    customer_id = make_id()
    email = (lead.get("email") or "").strip().lower()
    await db.execute(
        "INSERT INTO customers(id,company_id,lead_id,name,email,phone,customer_company_name,segment,avatar,lifecycle_stage,lifetime_value,avg_sentiment,recent_tickets,complaint_count,days_since_last_contact,total_conversations,created_at,updated_at) "  # noqa: E501
        "VALUES($1,$2,$3,$4,$5,'',$6,'general','','lead',0,0,0,0,0,0,NOW(),NOW())",
        customer_id,
        cid,
        lead.get("id", ""),
        (lead.get("name") or "").strip() or "Unknown",
        email,
        lead.get("customer_company_name", "") or lead.get("company", ""),
    )
    return r(
        await db.fetchrow(
            "SELECT * FROM customers WHERE id=$1 AND company_id=$2 LIMIT 1",
            customer_id,
            cid,
        )
    )


async def _resolve_lead_customer(db, lead: dict, current_user: dict, *, channel: str = "") -> dict:
    cid = get_company_id(current_user)
    phone = (lead.get("phone") or "").strip()
    email = (lead.get("email") or "").strip().lower()
    customer = None
    if phone:
        customer = r(
            await db.fetchrow(
                "SELECT * FROM customers WHERE company_id=$1 AND phone=$2 LIMIT 1",
                cid,
                phone,
            )
        )
    if not customer and email:
        customer = r(
            await db.fetchrow(
                "SELECT * FROM customers WHERE company_id=$1 AND email=$2 LIMIT 1",
                cid,
                email,
            )
        )
    if customer:
        updates = []
        args = []
        if lead.get("name") and customer.get("name") != lead.get("name"):
            updates.append(f"name=${len(args) + 1}")
            args.append(lead["name"])
        if phone and customer.get("phone") != phone:
            updates.append(f"phone=${len(args) + 1}")
            args.append(phone)
        if email and customer.get("email") != email:
            updates.append(f"email=${len(args) + 1}")
            args.append(email)
        if lead.get("customer_company_name") and customer.get("customer_company_name") != lead.get(
            "customer_company_name"
        ):
            updates.append(f"customer_company_name=${len(args) + 1}")
            args.append(lead["customer_company_name"])
        if updates:
            args.append(customer["id"])
            await db.execute(
                f"UPDATE customers SET {', '.join(updates)},updated_at=NOW() WHERE id=${len(args)}",
                *args,
            )
            customer = r(
                await db.fetchrow(
                    "SELECT * FROM customers WHERE id=$1 AND company_id=$2",
                    customer["id"],
                    cid,
                )
            )
        return customer
    if phone:
        return await get_or_create_customer_from_contact(db, lead.get("name", ""), phone, current_user)
    normalized_channel = str(channel or "").strip().lower()
    if normalized_channel == "email" and email:
        return await _create_email_only_customer(db, lead, current_user)
    raise HTTPException(
        400,
        "Lead needs a phone number to open or send a chat message."
        if normalized_channel != "email"
        else "Email address is required to send an email message.",
    )


async def _resolve_lead_conversation(
    db,
    lead: dict,
    current_user: dict,
    *,
    channel: str | None = None,
) -> tuple[dict, dict]:
    resolved_channel = str(channel or _normalize_lead_channel(lead)).strip().lower()
    customer = await _resolve_lead_customer(db, lead, current_user, channel=resolved_channel)
    conversation = await get_or_create_contact_conversation(
        db,
        customer,
        resolved_channel,
        "lead_profile",
        current_user,
    )
    return customer, conversation


async def _send_lead_nurture_message(
    db,
    lead: dict,
    nurture_message: dict,
    current_user: dict,
    *,
    requested_channel: str = "",
) -> dict:
    channel = str(requested_channel or _normalize_lead_channel(lead)).strip().lower()
    if channel not in {"whatsapp", "facebook", "instagram", "web_chat", "email"}:
        raise HTTPException(400, "Unsupported nurture channel")

    customer, conversation = await _resolve_lead_conversation(db, lead, current_user, channel=channel)
    content = (nurture_message.get("message") or "").strip()
    if not content:
        raise HTTPException(400, "Nurture message is empty.")

    channel = str(conversation.get("channel") or channel).strip().lower()
    recipient_id = _resolve_lead_recipient(channel, customer, lead)
    msg_id = make_id()
    await db.execute(
        "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,read,created_at) "
        "VALUES($1,$2,$3,$4,'agent',$5,$6,FALSE,NOW())",
        msg_id,
        conversation.get("company_id", get_company_id(current_user)),
        conversation["id"],
        content,
        current_user.get("sub", ""),
        current_user.get("name", ""),
    )
    if channel in {"whatsapp", "facebook", "instagram", "email"}:
        if not recipient_id:
            await db.execute(
                "DELETE FROM messages WHERE id=$1 AND company_id=$2",
                msg_id,
                conversation.get("company_id", get_company_id(current_user)),
            )
            if channel == "whatsapp":
                raise HTTPException(400, "Phone number is required to send a WhatsApp message.")
            if channel == "email":
                raise HTTPException(400, "Email address is required to send an email message.")
            raise HTTPException(
                400,
                f"{channel.capitalize()} recipient ID is required to send this message.",
            )
        sent, error = await _send_outbound_via_channel_layer(
            db=db,
            company_id=conversation.get("company_id", get_company_id(current_user)),
            channel=channel,
            recipient_id=recipient_id,
            content=content,
            conversation_id=conversation["id"],
            db_message_id=msg_id,
            metadata={
                "source": "lead_nurture",
                "lead_id": lead.get("id", ""),
                "actor_user_id": current_user.get("sub", ""),
                "actor_user_role": current_user.get("role", ""),
                "trace_id": _trace_id_from_context(),
            },
            subject=(
                f"Pulse Engine follow up for {lead.get('name') or customer.get('name') or 'lead'}"
                if channel == "email"
                else ""
            ),
        )
        if not sent:
            await db.execute(
                "DELETE FROM messages WHERE id=$1 AND company_id=$2",
                msg_id,
                conversation.get("company_id", get_company_id(current_user)),
            )
            raise HTTPException(502, error or f"Failed to send {channel} message")
    await persist_chat_history(
        db,
        conversation,
        {
            "id": msg_id,
            "conversation_id": conversation["id"],
            "content": content,
            "sender_type": "agent",
            "sender_name": current_user.get("name", ""),
            "created_at": now_ts(),
        },
    )
    await db.execute(
        "UPDATE conversations SET last_message=$1,last_message_at=NOW(),updated_at=NOW(),message_count=message_count+1 WHERE id=$2",  # noqa: E501
        content[:100],
        conversation["id"],
    )
    await db.execute(
        "UPDATE lead_nurture_messages SET sent=TRUE WHERE id=$1 AND lead_id=$2",
        nurture_message["id"],
        lead["id"],
    )
    await db.execute(
        "INSERT INTO lead_activities(id,lead_id,company_id,type,content,stage,created_at) VALUES($1,$2,$3,'nurture_sent',$4,$5,NOW())",  # noqa: E501
        make_id(),
        lead["id"],
        get_company_id(current_user),
        content,
        nurture_message.get("phase") or lead.get("status", "new"),
    )
    message = r(await db.fetchrow("SELECT * FROM messages WHERE id=$1", msg_id))
    await emit_new_message(conversation["id"], message)
    return {"conversation_id": conversation["id"], "message": message, "channel": channel}


@router.get("/leads")
async def list_leads(
    request: Request,
    status: Optional[str] = None,
    grade: Optional[str] = None,
    source: Optional[str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    sql = "SELECT * FROM leads WHERE company_id=$1"
    args: list = [cid]
    if status:
        args.append(status)
        sql += f" AND status=${len(args)}"
    if grade:
        args.append(grade)
        sql += f" AND grade=${len(args)}"
    if source:
        args.append(source)
        sql += f" AND source=${len(args)}"
    if tag:
        args.append(tag.lower())
        sql += f" AND EXISTS (SELECT 1 FROM lead_tags lt WHERE lt.lead_id = leads.id AND LOWER(lt.tag) = ${len(args)})"
    if search:
        args.append(f"%{search}%")
        name_idx = len(args)
        args.append(f"%{search}%")
        email_idx = len(args)
        args.append(f"%{search}%")
        company_idx = len(args)
        sql += (
            f" AND (name ILIKE ${name_idx} OR email ILIKE ${email_idx} OR customer_company_name ILIKE ${company_idx})"
        )
    args.append(limit)
    sql += f" ORDER BY created_at DESC LIMIT ${len(args)}"
    leads = rs(await db.fetch(sql, *args))
    if not leads:
        return leads

    # Batch-load related records while staying on the single request-bound DB connection.
    lead_ids = [lead["id"] for lead in leads]
    activities_rows = await db.fetch(
        "SELECT * FROM lead_activities WHERE lead_id = ANY($1::text[]) ORDER BY created_at",
        lead_ids,
    )
    nurture_rows = await db.fetch(
        "SELECT * FROM lead_nurture_messages WHERE lead_id = ANY($1::text[]) ORDER BY created_at",
        lead_ids,
    )
    channel_rows = await db.fetch(
        "SELECT lead_id, channel FROM lead_channels WHERE lead_id = ANY($1::text[])",
        lead_ids,
    )
    tag_rows = await db.fetch(
        "SELECT lead_id, tag FROM lead_tags WHERE lead_id = ANY($1::text[])",
        lead_ids,
    )

    activities_map: dict[str, list] = {}
    for row in activities_rows:
        activities_map.setdefault(row["lead_id"], []).append(dict(row))

    nurture_map: dict[str, list] = {}
    for row in nurture_rows:
        nurture_map.setdefault(row["lead_id"], []).append(dict(row))

    channels_map: dict[str, list] = {}
    for row in channel_rows:
        channels_map.setdefault(row["lead_id"], []).append(row["channel"])

    tags_map: dict[str, list] = {}
    for row in tag_rows:
        tags_map.setdefault(row["lead_id"], []).append(row["tag"])

    for lead in leads:
        lid = lead["id"]
        lead["activities"] = activities_map.get(lid, [])
        lead["nurture_messages"] = nurture_map.get(lid, [])
        lead["channels"] = channels_map.get(lid, [])
        lead["tags"] = tags_map.get(lid, [])
        _serialize_lead(lead)
    return leads


@router.get("/leads/{lead_id}")
async def get_lead(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1", lead_id, cid))
    if not lead:
        raise HTTPException(404, "Lead not found")
    lead["activities"] = rs(
        await db.fetch(
            "SELECT * FROM lead_activities WHERE lead_id=$1 ORDER BY created_at",
            lead_id,
        )
    )
    lead["nurture_messages"] = rs(
        await db.fetch(
            "SELECT * FROM lead_nurture_messages WHERE lead_id=$1 ORDER BY created_at",
            lead_id,
        )
    )
    lead["channels"] = [
        row["channel"] for row in await db.fetch("SELECT channel FROM lead_channels WHERE lead_id=$1", lead_id)
    ]
    return _serialize_lead(lead)


@router.post("/leads")
async def create_lead(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    body = await request.json()
    if "company" in body and "customer_company_name" not in body:
        body["customer_company_name"] = body.pop("company")
    cid = cu.get("company_id", "")
    email = (body.get("email", "") or "").strip().lower()
    phone = (body.get("phone", "") or "").strip()
    name = (body.get("name", "") or "").strip()
    if not any([name, email, phone]):
        raise HTTPException(400, "Lead name, email, or phone is required")
    status = body.get("status", "new")
    source = body.get("source", "web_chat")
    status_id = await resolve_company_reference_id(
        db,
        cid,
        "lead_statuses",
        "status_name",
        status,
        {"description": "Lead status", "order_index": 999},
    )
    source_id = await resolve_company_reference_id(
        db,
        cid,
        "sources",
        "source_name",
        source,
        {"source_type": "channel", "platform": normalize_reference_key(source)},
    )
    lead_id = make_id()
    await db.execute(
        "INSERT INTO leads(id,company_id,name,email,phone,customer_company_name,source,source_id,status,status_id,score,grade,phase,notes,assigned_to,assigned_name,scoring_reason,next_action,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,50,'warm','awareness',$11,$12,$13,'','',NOW(),NOW())",  # noqa: E501
        lead_id,
        cid,
        name,
        email,
        phone,
        body.get("customer_company_name", body.get("company", "")),
        source,
        source_id,
        status,
        status_id,
        body.get("notes", ""),
        cu["sub"],
        cu.get("name", ""),
    )
    from core.socket import sio, connected_users

    await create_notification(
        db,
        sio,
        connected_users,
        cu,
        "Lead created",
        f"{body.get('name', '')} added to pipeline",
        "lead",
    )
    created = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid))
    await _capture_lead_snapshot(
        db,
        created,
        source=source,
        action="lead_created",
    )
    return _serialize_lead(created)


@router.put("/leads/{lead_id}")
async def update_lead(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    if "company" in body and "customer_company_name" not in body:
        body["customer_company_name"] = body.pop("company")
    body.pop("_id", None)
    if "status" in body:
        body["status_id"] = await resolve_company_reference_id(
            db,
            cid,
            "lead_statuses",
            "status_name",
            body["status"],
            {"description": "Lead status", "order_index": 999},
        )
    if "source" in body:
        body["source_id"] = await resolve_company_reference_id(
            db,
            cid,
            "sources",
            "source_name",
            body["source"],
            {
                "source_type": "channel",
                "platform": normalize_reference_key(body["source"]),
            },
        )
    safe_body = {k: v for k, v in body.items() if k in LEAD_UPDATE_FIELDS}
    if body and not safe_body:
        raise HTTPException(400, "No valid fields provided")
    safe_body["updated_at"] = now_ts()
    columns = list(safe_body.keys())
    set_parts = ", ".join(f"{k}=${i + 3}" for i, k in enumerate(columns))
    values = [safe_body[col] for col in columns]
    await db.execute(
        f"UPDATE leads SET {set_parts} WHERE id=$1 AND company_id=$2",
        lead_id,
        cid,
        *values,
    )
    updated = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid))
    await _capture_lead_snapshot(
        db,
        updated,
        source=str((updated or {}).get("source") or body.get("source") or "lead"),
        action="lead_updated",
    )
    return _serialize_lead(updated)


@router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    await db.execute("DELETE FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid)
    return {"status": "deleted"}


@router.post("/leads/{lead_id}/score")
async def score_lead(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1", lead_id, cid))
    if not lead:
        raise HTTPException(404, "Lead not found")
    workflow = await orchestrate_lead_workflow(
        LeadWorkflowRequest(
            company_id=cid,
            lead_id=lead_id,
            lead=lead,
            source=str(lead.get("source") or "lead"),
            actor_user_id=cu.get("sub", ""),
            actor_user_role=cu.get("role", ""),
            auto_support=False,
        ),
        authorization=request.headers.get("authorization") or request.headers.get("Authorization", ""),
        db=db,
    )
    result = workflow.agent_outputs.qualification
    await db.execute(
        "UPDATE leads SET score=$1,grade=$2,scoring_reason=$3,next_action=$4,updated_at=NOW() WHERE id=$5",
        result.get("score", 50),
        result.get("grade", "warm"),
        result.get("reasoning", ""),
        result.get("next_action", ""),
        lead_id,
    )
    updated = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid))
    await _capture_lead_snapshot(
        db,
        updated,
        source=str((updated or {}).get("source") or "lead"),
        action="lead_scored",
    )
    return _serialize_lead(updated)


@router.post("/leads/{lead_id}/auto-score")
async def auto_score_lead(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1", lead_id, cid))
    if not lead:
        raise HTTPException(404, "Lead not found")
    workflow = await orchestrate_lead_workflow(
        LeadWorkflowRequest(
            company_id=cid,
            lead_id=lead_id,
            lead=lead,
            source=str(lead.get("source") or "lead"),
            actor_user_id=cu.get("sub", ""),
            actor_user_role=cu.get("role", ""),
            auto_support=True,
        ),
        authorization=request.headers.get("authorization") or request.headers.get("Authorization", ""),
        db=db,
    )
    qualification = workflow.agent_outputs.qualification
    support = workflow.agent_outputs.support
    result = {
        **qualification,
        "nurture_message": str(support.get("response") or ""),
    }
    await db.execute(
        "UPDATE leads SET score=$1,grade=$2,phase=$3,scoring_reason=$4,next_action=$5,updated_at=NOW() WHERE id=$6",
        result["score"],
        result["grade"],
        result.get("phase", "awareness"),
        result.get("reasoning", ""),
        result.get("next_action", ""),
        lead_id,
    )
    if result.get("nurture_message"):
        await db.execute(
            "INSERT INTO lead_activities(id,lead_id,company_id,type,content,stage,created_at) VALUES($1,$2,$3,'auto_nurture',$4,$5,NOW())",  # noqa: E501
            make_id(),
            lead_id,
            cid,
            result["nurture_message"],
            result.get("phase", "awareness"),
        )
        await db.execute(
            "INSERT INTO lead_nurture_messages(id,lead_id,company_id,message,phase,sent,created_at) VALUES($1,$2,$3,$4,$5,FALSE,NOW())",  # noqa: E501
            make_id(),
            lead_id,
            cid,
            result["nurture_message"],
            result.get("phase", "awareness"),
        )
    updated = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid))
    await _capture_lead_snapshot(
        db,
        updated,
        source=str((updated or {}).get("source") or "lead"),
        action="lead_auto_scored",
    )
    return {
        "lead": _serialize_lead(updated),
        "ai_result": result,
    }


@router.post("/leads/{lead_id}/nurture")
async def nurture_lead(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1", lead_id, cid))
    if not lead:
        raise HTTPException(404, "Lead not found")
    company_context = await get_company_knowledge(db, cid)
    result = await generate_nurture_message(lead, lead.get("status", "new"), company_context, db=db)
    await db.execute(
        "INSERT INTO lead_activities(id,lead_id,company_id,type,content,stage,created_at) VALUES($1,$2,$3,'nurture',$4,$5,NOW())",  # noqa: E501
        make_id(),
        lead_id,
        cid,
        result["message"],
        result["stage"],
    )
    await db.execute(
        "INSERT INTO lead_nurture_messages(id,lead_id,company_id,message,phase,sent,created_at) VALUES($1,$2,$3,$4,$5,FALSE,NOW())",  # noqa: E501
        make_id(),
        lead_id,
        cid,
        result["message"],
        result["stage"],
    )
    return result


@router.post("/leads/{lead_id}/conversation")
async def open_lead_conversation(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1", lead_id, cid))
    if not lead:
        raise HTTPException(404, "Lead not found")
    customer, conversation = await _resolve_lead_conversation(db, lead, cu)
    return {"conversation": conversation, "customer": customer}


@router.post("/leads/{lead_id}/nurture-messages/{message_id}/send")
async def send_lead_nurture_message(lead_id: str, message_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1", lead_id, cid))
    if not lead:
        raise HTTPException(404, "Lead not found")
    nurture_message = r(
        await db.fetchrow(
            "SELECT * FROM lead_nurture_messages WHERE id=$1 AND lead_id=$2 AND company_id=$3 LIMIT 1",
            message_id,
            lead_id,
            cid,
        )
    )
    if not nurture_message:
        raise HTTPException(404, "Nurture message not found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    requested_channel = str(body.get("channel") or "").strip().lower()
    result = await _send_lead_nurture_message(
        db,
        lead,
        nurture_message,
        cu,
        requested_channel=requested_channel,
    )
    refreshed = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid))
    if refreshed:
        refreshed["activities"] = rs(
            await db.fetch(
                "SELECT * FROM lead_activities WHERE lead_id=$1 ORDER BY created_at",
                lead_id,
            )
        )
        refreshed["nurture_messages"] = rs(
            await db.fetch(
                "SELECT * FROM lead_nurture_messages WHERE lead_id=$1 ORDER BY created_at",
                lead_id,
            )
        )
        refreshed["channels"] = [
            row["channel"] for row in await db.fetch("SELECT channel FROM lead_channels WHERE lead_id=$1", lead_id)
        ]
        _serialize_lead(refreshed)
    return {
        "status": "sent",
        "conversation_id": result["conversation_id"],
        "channel": result.get("channel", requested_channel),
        "lead": refreshed,
    }


@router.post("/leads/{lead_id}/convert-to-customer")
async def convert_lead_to_customer(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1", lead_id, cid))
    if not lead:
        raise HTTPException(404, "Lead not found")
    await _capture_lead_snapshot(
        db,
        lead,
        source=str(lead.get("source") or "lead"),
        action="lead_converted",
        extra_metadata={"is_converted": True},
    )
    customer = await convert_lead_to_customer_state(db, lead, cu)
    from core.socket import sio, connected_users

    await create_notification(
        db,
        sio,
        connected_users,
        cu,
        "Lead converted",
        f"{lead.get('name', 'Lead')} upgraded to customer",
        "customer",
    )
    create_detached_task(
        db.execute(
            "INSERT INTO journey_tracking(id,company_id,user_id,lead_id,customer_id,phase,started_at,completed_at,created_at) VALUES($1,$2,$3,$4,$5,'conversion',NOW(),NOW(),NOW())",  # noqa: E501
            make_id(),
            cid,
            cu.get("sub", ""),
            lead_id,
            customer.get("id", ""),
        )
    )
    create_detached_task(
        record_system_log(
            db,
            cu,
            "convert_lead",
            "lead",
            lead_id,
            {"customer_id": customer.get("id", "")},
        )
    )
    return {"status": "converted", "customer": customer, "lead_id": lead_id}


@router.post("/leads/auto-nurture-all")
async def auto_nurture_all_leads(request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin"])
    cid = cu.get("company_id", "")
    leads = rs(
        await db.fetch(
            "SELECT * FROM leads WHERE company_id=$1 AND status=ANY($2) LIMIT 50",
            cid,
            ["new", "contacted", "qualified"],
        )
    )
    results = []
    for lead in leads:
        try:
            workflow = await orchestrate_lead_workflow(
                LeadWorkflowRequest(
                    company_id=cid,
                    lead_id=lead.get("id", ""),
                    lead=lead,
                    source=str(lead.get("source") or "lead"),
                    actor_user_id=cu.get("sub", ""),
                    actor_user_role=cu.get("role", ""),
                    auto_support=True,
                ),
                authorization=request.headers.get("authorization") or request.headers.get("Authorization", ""),
                db=db,
            )
            qualification = workflow.agent_outputs.qualification
            support = workflow.agent_outputs.support
            result = {
                **qualification,
                "nurture_message": str(support.get("response") or ""),
            }
            await db.execute(
                "UPDATE leads SET score=$1,grade=$2,phase=$3,scoring_reason=$4,next_action=$5,updated_at=NOW() WHERE id=$6",  # noqa: E501
                result["score"],
                result["grade"],
                result.get("phase", "awareness"),
                result.get("reasoning", ""),
                result.get("next_action", ""),
                lead["id"],
            )
            if result.get("nurture_message"):
                await db.execute(
                    "INSERT INTO lead_activities(id,lead_id,company_id,type,content,stage,created_at) VALUES($1,$2,$3,'auto_nurture',$4,$5,NOW())",  # noqa: E501
                    make_id(),
                    lead["id"],
                    cid,
                    result["nurture_message"],
                    result.get("phase", "awareness"),
                )
                await db.execute(
                    "INSERT INTO lead_nurture_messages(id,lead_id,company_id,message,phase,sent,created_at) VALUES($1,$2,$3,$4,$5,FALSE,NOW())",  # noqa: E501
                    make_id(),
                    lead["id"],
                    cid,
                    result["nurture_message"],
                    result.get("phase", "awareness"),
                )
            refreshed = r(
                await db.fetchrow(
                    "SELECT * FROM leads WHERE id=$1 AND company_id=$2",
                    lead["id"],
                    cid,
                )
            )
            await _capture_lead_snapshot(
                db,
                refreshed,
                source=str((refreshed or {}).get("source") or lead.get("source") or "lead"),
                action="lead_auto_nurtured",
            )
            results.append(
                {
                    "lead_id": lead["id"],
                    "name": lead["name"],
                    "score": result["score"],
                    "grade": result["grade"],
                    "status": "nurtured",
                }
            )
        except Exception as e:
            logger.error(f"Auto-nurture failed for {lead['id']}: {e}")
            results.append(
                {
                    "lead_id": lead["id"],
                    "name": lead["name"],
                    "status": "failed",
                    "error": str(e),
                }
            )
    return {"total_processed": len(results), "results": results}
