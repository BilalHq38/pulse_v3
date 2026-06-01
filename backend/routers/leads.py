"""routers/leads.py — PostgreSQL version."""

import asyncio
import logging
from typing import Optional

from shared.database import _request_conn as _db_request_conn
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from agent_orchestrator.schemas import LeadWorkflowRequest
from channel_layer.channel_identity import company_default_phone_region
from channel_layer.router import get_outbound_router
from channel_layer.schemas import ChannelType
from services.ai_service.facade import (
    generate_nurture_message,
    get_company_knowledge,
)
from services.agent_orchestrator.facade import orchestrate_lead_workflow
from core.socket import emit_new_message
from core.phone_normalization import strict_normalize_to_e164_digits
from core.utils import make_id, now_ts, normalize_reference_key
from models.reference_data import resolve_company_reference_id
from shared.tracing import current_trace_context
from shared.tabular_uploads import parse_tabular_upload, phone_region_from_upload_row, split_multi_value
from shared.usage_guard import conversation_limit_status, raise_conversation_limit_completed, reserve_conversation_usage
from shared.webhook_task_runner import create_safe_detached_task
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
from services.lead_stage_service import (
    apply_message_stage_transition,
    get_lead_stage_history,
    normalize_lead_stage,
    parse_stage_activity,
    transition_lead_stage,
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


def _first_valid_nurture_message(result: dict) -> str:
    def coerce(value) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ("message", "content", "text", "response"):
                text = coerce(value.get(key))
                if text:
                    return text
        return ""

    for key in ("message", "nurture_message", "response"):
        text = coerce((result or {}).get(key))
        if text:
            return text

    for key in ("messages", "nurture_messages"):
        values = (result or {}).get(key)
        if isinstance(values, list):
            for value in values:
                text = coerce(value)
                if text:
                    return text
    return ""


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


def _resolve_lead_conversation_channel_id(channel: str, customer: dict, lead: dict) -> str:
    normalized = str(channel or "").strip().lower()
    if normalized == "whatsapp":
        return str(customer.get("phone") or lead.get("phone") or "").strip()
    if normalized in {"facebook", "instagram"}:
        customer_social_profiles = (
            customer.get("social_profiles") if isinstance(customer.get("social_profiles"), dict) else {}
        )
        lead_social_profiles = lead.get("social_profiles") if isinstance(lead.get("social_profiles"), dict) else {}
        return str(
            customer_social_profiles.get(normalized)
            or lead_social_profiles.get(normalized)
            or lead.get("channel_recipient_id")
            or lead.get("external_recipient_id")
            or ""
        ).strip()
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
    metadata = lead.get("metadata") if isinstance(lead.get("metadata"), dict) else {}
    lead["avatar"] = str(
        lead.get("avatar")
        or metadata.get("avatar")
        or metadata.get("profile_picture_url")
        or metadata.get("provider_avatar_url")
        or ""
    )
    activities = lead.get("activities") if isinstance(lead.get("activities"), list) else []
    for activity in reversed(activities):
        if activity.get("type") == "stage_changed":
            latest_stage = parse_stage_activity(activity)
            lead["last_stage_update"] = latest_stage
            lead["last_stage_update_reason"] = latest_stage.get("reason", "")
            lead["last_stage_update_source"] = latest_stage.get("source", "")
            break
    return lead


async def _load_lead_details(db, lead_id: str, company_id: str) -> dict | None:
    lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1", lead_id, company_id))
    if not lead:
        return None
    lead["activities"] = rs(
        await db.fetch(
            "SELECT * FROM lead_activities WHERE lead_id=$1 AND company_id=$2 ORDER BY created_at",
            lead_id,
            company_id,
        )
    )
    lead["nurture_messages"] = rs(
        await db.fetch(
            "SELECT * FROM lead_nurture_messages WHERE lead_id=$1 AND company_id=$2 ORDER BY created_at",
            lead_id,
            company_id,
        )
    )
    lead["channels"] = [
        row["channel"]
        for row in await db.fetch(
            "SELECT channel FROM lead_channels WHERE lead_id=$1",
            lead_id,
        )
    ]
    lead["tags"] = [
        row["tag"]
        for row in await db.fetch(
            "SELECT tag FROM lead_tags WHERE lead_id=$1",
            lead_id,
        )
    ]
    return _serialize_lead(lead)


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


def _row_has_any(row: dict, *keys: str) -> bool:
    return any(key in row for key in keys)


def _get_first_value(row: dict, *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _normalize_upload_channels(raw_value: str) -> list[str]:
    allowed = {"whatsapp", "email", "facebook", "instagram", "web_chat"}
    channels: list[str] = []
    for item in split_multi_value(raw_value):
        normalized = normalize_reference_key(item).replace("-", "_")
        if normalized in allowed and normalized not in channels:
            channels.append(normalized)
    return channels


def _normalize_upload_tags(raw_value: str) -> list[str]:
    tags: list[str] = []
    for item in split_multi_value(raw_value):
        normalized = normalize_reference_key(item).replace("-", "_")
        if normalized and normalized not in tags:
            tags.append(normalized)
    return tags


async def _find_existing_lead_for_upload(db, company_id: str, *, phone: str = "", email: str = "") -> dict | None:
    if phone:
        lead = r(
            await db.fetchrow(
                "SELECT * FROM leads WHERE company_id=$1 AND phone=$2 LIMIT 1",
                company_id,
                phone,
            )
        )
        if lead:
            return lead
    if email:
        return r(
            await db.fetchrow(
                "SELECT * FROM leads WHERE company_id=$1 AND email=$2 LIMIT 1",
                company_id,
                email,
            )
        )
    return None


async def _sync_lead_links(
    db,
    lead_id: str,
    *,
    tags: list[str] | None = None,
    channels: list[str] | None = None,
) -> None:
    if tags is not None:
        await db.execute("DELETE FROM lead_tags WHERE lead_id=$1", lead_id)
        if tags:
            await db.executemany(
                "INSERT INTO lead_tags(lead_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                [(lead_id, tag) for tag in tags],
            )
    if channels is not None:
        await db.execute("DELETE FROM lead_channels WHERE lead_id=$1", lead_id)
        if channels:
            await db.executemany(
                "INSERT INTO lead_channels(lead_id,channel) VALUES($1,$2) ON CONFLICT DO NOTHING",
                [(lead_id, channel) for channel in channels],
            )


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
        if lead.get("id") and customer.get("lead_id") != lead.get("id"):
            updates.append(f"lead_id=${len(args) + 1}")
            args.append(lead["id"])
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
    normalized_channel = str(channel or "").strip().lower()
    if phone:
        customer = await get_or_create_customer_from_contact(
            db,
            lead.get("name", ""),
            phone,
            current_user,
            email=email,
            channel=normalized_channel,
        )
        if customer and lead.get("id") and customer.get("lead_id") != lead.get("id"):
            await db.execute(
                "UPDATE customers SET lead_id=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
                lead["id"],
                customer["id"],
                cid,
            )
            customer = r(await db.fetchrow("SELECT * FROM customers WHERE id=$1 AND company_id=$2", customer["id"], cid))
        return customer
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
        channel_id=_resolve_lead_conversation_channel_id(resolved_channel, customer, lead),
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
    if channel in {"whatsapp", "facebook", "instagram", "email"} and not recipient_id:
        if channel == "whatsapp":
            raise HTTPException(400, "Phone number is required to send a WhatsApp message.")
        if channel == "email":
            raise HTTPException(400, "Email address is required to send an email message.")
        raise HTTPException(
            400,
            f"{channel.capitalize()} recipient ID is required to send this message.",
        )
    msg_id = make_id()
    company_id = conversation.get("company_id", get_company_id(current_user))
    reservation = await reserve_conversation_usage(
        db,
        company_id,
        channel=channel,
        idempotency_key=f"out:{company_id}:{channel}:lead_nurture:{msg_id}",
    )
    if reservation == "denied":
        raise_conversation_limit_completed(await conversation_limit_status(db, company_id))
    await db.execute(
        "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,read,created_at) "
        "VALUES($1,$2,$3,$4,'agent',$5,$6,FALSE,NOW())",
        msg_id,
        company_id,
        conversation["id"],
        content,
        current_user.get("sub", ""),
        current_user.get("name", ""),
    )
    if channel in {"whatsapp", "facebook", "instagram", "email"}:
        sent, error = await _send_outbound_via_channel_layer(
            db=db,
            company_id=company_id,
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
            subject="",
        )
        if not sent:
            await db.execute(
                "DELETE FROM messages WHERE id=$1 AND company_id=$2",
                msg_id,
                company_id,
            )
            err_detail = str(error or f"Failed to send {channel} message")
            # Map common WHATSAPP_SESSION_NOT_READY errors to user-friendly messages
            if "WHATSAPP_SESSION_NOT_READY" in err_detail:
                err_detail = "WhatsApp is not connected. Please scan the QR code in Settings → Channels → WhatsApp to connect."
            elif "not_configured" in err_detail.lower() or "not configured" in err_detail.lower():
                err_detail = f"{channel.capitalize()} channel is not configured. Go to Settings → Channels to set it up."
            raise HTTPException(400, err_detail)
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
    logger.info(
        "ai_auto_disable_skipped company_id=%s conversation_id=%s trigger=manual_message reason=manual_message_not_escalation actor_type=agent trigger_message_id=%s message_type=text media_type=",
        company_id,
        conversation["id"],
        msg_id,
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
    else:
        # Lead/customer integrity: once a lead has been converted to a customer
        # (order_service marks the row with status='converted'), it stops
        # showing in the default leads list so the same person doesn't appear
        # in both Leads and Customers. Callers that explicitly want converted
        # leads can pass ?status=converted.
        sql += " AND status <> 'converted'"
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
    lead = await _load_lead_details(db, lead_id, cid)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead


@router.post("/leads")
async def create_lead(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    body = await request.json()
    if "company" in body and "customer_company_name" not in body:
        body["customer_company_name"] = body.pop("company")
    cid = cu.get("company_id", "")
    email = (body.get("email", "") or "").strip().lower()
    _raw = (body.get("phone", "") or "").strip()
    phone = ""
    if _raw:
        phone = strict_normalize_to_e164_digits(_raw) or ""
        if not phone:
            raise HTTPException(
                400,
                "Invalid phone number. Use a valid number in E.164 or international form (e.g. +92… or +44…), "
                "or a valid local number for WHATSAPP_DEFAULT_COUNTRY.",
            )
    name = (body.get("name", "") or "").strip()
    if not any([name, email, phone]):
        raise HTTPException(400, "Lead name, email, or phone is required")
    try:
        status = normalize_lead_stage(body.get("status", "new"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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
    from core.socket import sio, connected_users, emit_company_event

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
    await emit_company_event(cid, "lead_created", {"lead_id": lead_id, "lead": _serialize_lead(dict(created or {}))})
    return _serialize_lead(created)


@router.put("/leads/{lead_id}")
async def update_lead(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    current = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1", lead_id, cid))
    if not current:
        raise HTTPException(404, "Lead not found")
    if "company" in body and "customer_company_name" not in body:
        body["customer_company_name"] = body.pop("company")
    requested_status = None
    body.pop("_id", None)
    if "status" in body:
        try:
            requested_status = normalize_lead_stage(body.get("status", "new"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        body.pop("status", None)
        body.pop("status_id", None)
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
    if body and not safe_body and not requested_status:
        raise HTTPException(400, "No valid fields provided")
    if not safe_body and not requested_status:
        raise HTTPException(400, "No valid fields provided")
    if "phone" in safe_body and (safe_body.get("phone") or "").strip():
        _up = str(safe_body["phone"] or "").strip()
        out = strict_normalize_to_e164_digits(_up) or ""
        if not out:
            raise HTTPException(
                400,
                "Invalid phone number. Use a valid number in E.164 or international form (e.g. +92… or +44…), "
                "or a valid local number for WHATSAPP_DEFAULT_COUNTRY.",
            )
        safe_body["phone"] = out
    if safe_body:
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
    if requested_status and requested_status != (current.get("status") or "new"):
        transition = await transition_lead_stage(
            db,
            updated or current,
            requested_status,
            reason="Manual stage update",
            source="manual_update",
            confidence=1.0,
            changed_by_user_id=cu.get("sub", ""),
            event_id=f"manual:{lead_id}:{requested_status}",
            automatic=False,
        )
        updated = transition.get("lead") or r(
            await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid)
        )
    await _capture_lead_snapshot(
        db,
        updated,
        source=str((updated or {}).get("source") or body.get("source") or "lead"),
        action="lead_updated",
    )
    detailed = await _load_lead_details(db, lead_id, cid)
    from core.socket import emit_company_event
    await emit_company_event(cid, "lead_updated", {"lead_id": lead_id})
    return detailed or _serialize_lead(updated)


@router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    await db.execute("DELETE FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid)
    from core.socket import emit_company_event
    await emit_company_event(cid, "lead_deleted", {"lead_id": lead_id})
    return {"status": "deleted"}


@router.put("/leads/{lead_id}/stage")
async def update_lead_stage(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    body = await request.json()
    try:
        new_stage = normalize_lead_stage(body.get("stage") or body.get("status"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1", lead_id, cid))
    if not lead:
        raise HTTPException(404, "Lead not found")
    transition = await transition_lead_stage(
        db,
        lead,
        new_stage,
        reason=str(body.get("reason") or "Manual stage update"),
        source="manual_update",
        confidence=1.0,
        changed_by_user_id=cu.get("sub", ""),
        event_id=str(body.get("event_id") or f"manual:{lead_id}:{new_stage}"),
        automatic=False,
    )
    updated = await _load_lead_details(db, lead_id, cid)
    return {
        "status": "ok",
        "changed": bool(transition.get("changed")),
        "lead": updated,
        "stage_history": await get_lead_stage_history(db, lead_id, cid),
    }


@router.get("/leads/{lead_id}/stage-history")
async def lead_stage_history(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not await db.fetchval("SELECT id FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid):
        raise HTTPException(404, "Lead not found")
    return await get_lead_stage_history(db, lead_id, cid)


@router.post("/leads/bulk-upload")
async def bulk_upload_leads(request: Request, file: UploadFile = File(...)):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    rows = parse_tabular_upload(file.filename or "", await file.read())
    tenant_phone_region = await company_default_phone_region(db, cid)
    summary = {
        "total_rows": len(rows),
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }

    for entry in rows:
        row_number = entry["row_number"]
        row = entry["data"]
        try:
            name = _get_first_value(row, "name", "full_name", "lead_name")
            email = _get_first_value(row, "email", "email_address", "e_mail").lower()
            raw_phone = _get_first_value(row, "phone", "phone_number", "mobile", "mobile_number", "whatsapp")
            company_name = _get_first_value(
                row,
                "company",
                "customer_company_name",
                "company_name",
                "organization",
            )
            status_input = _get_first_value(row, "status", "lead_status")
            source_input = _get_first_value(row, "source", "lead_source")
            notes_value = _get_first_value(row, "notes", "note", "comments")
            tags = _normalize_upload_tags(_get_first_value(row, "tags", "labels"))
            channels = _normalize_upload_channels(_get_first_value(row, "channels", "preferred_channels"))

            if not any([name, email, raw_phone]):
                summary["skipped"] += 1
                continue

            normalized_phone = ""
            if raw_phone:
                phone_region = phone_region_from_upload_row(row) or tenant_phone_region
                normalized_phone = strict_normalize_to_e164_digits(raw_phone, fallback_region=phone_region or None) or ""
                if not normalized_phone:
                    raise HTTPException(
                        400,
                        "Invalid phone number. Use a valid international number such as +1..., +44..., or +92..., "
                        "or include a country/region code for local numbers.",
                    )

            existing = await _find_existing_lead_for_upload(
                db,
                cid,
                phone=normalized_phone,
                email=email,
            )

            if existing:
                update_fields: dict[str, str] = {}
                if name:
                    update_fields["name"] = name
                if email:
                    update_fields["email"] = email
                if normalized_phone:
                    update_fields["phone"] = normalized_phone
                if company_name:
                    update_fields["customer_company_name"] = company_name
                if _row_has_any(row, "notes", "note", "comments"):
                    update_fields["notes"] = notes_value

                if status_input:
                    try:
                        status_input = normalize_lead_stage(status_input)
                    except ValueError as exc:
                        raise HTTPException(400, str(exc)) from exc
                    update_fields["status"] = status_input
                    update_fields["status_id"] = await resolve_company_reference_id(
                        db,
                        cid,
                        "lead_statuses",
                        "status_name",
                        status_input,
                        {"description": "Lead status", "order_index": 999},
                    )
                if source_input:
                    update_fields["source"] = source_input
                    update_fields["source_id"] = await resolve_company_reference_id(
                        db,
                        cid,
                        "sources",
                        "source_name",
                        source_input,
                        {"source_type": "channel", "platform": normalize_reference_key(source_input)},
                    )
                if update_fields:
                    update_fields["updated_at"] = now_ts()
                    columns = list(update_fields.keys())
                    set_parts = ", ".join(f"{key}=${index + 3}" for index, key in enumerate(columns))
                    await db.execute(
                        f"UPDATE leads SET {set_parts} WHERE id=$1 AND company_id=$2",
                        existing["id"],
                        cid,
                        *[update_fields[column] for column in columns],
                    )
                await _sync_lead_links(
                    db,
                    existing["id"],
                    tags=tags if _row_has_any(row, "tags", "labels") else None,
                    channels=channels if _row_has_any(row, "channels", "preferred_channels") else None,
                )
                updated_lead = r(
                    await db.fetchrow(
                        "SELECT * FROM leads WHERE id=$1 AND company_id=$2",
                        existing["id"],
                        cid,
                    )
                )
                await _capture_lead_snapshot(
                    db,
                    updated_lead,
                    source=str((updated_lead or {}).get("source") or source_input or "lead"),
                    action="lead_updated_bulk",
                    extra_metadata={"row_number": row_number},
                )
                summary["updated"] += 1
                continue

            try:
                status_value = normalize_lead_stage(status_input or "new")
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            source_value = source_input or "web_chat"
            status_id = await resolve_company_reference_id(
                db,
                cid,
                "lead_statuses",
                "status_name",
                status_value,
                {"description": "Lead status", "order_index": 999},
            )
            source_id = await resolve_company_reference_id(
                db,
                cid,
                "sources",
                "source_name",
                source_value,
                {"source_type": "channel", "platform": normalize_reference_key(source_value)},
            )

            lead_id = make_id()
            await db.execute(
                "INSERT INTO leads(id,company_id,name,email,phone,customer_company_name,source,source_id,status,status_id,score,grade,phase,notes,assigned_to,assigned_name,scoring_reason,next_action,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,50,'warm','awareness',$11,$12,$13,'','',NOW(),NOW())",  # noqa: E501
                lead_id,
                cid,
                name,
                email,
                normalized_phone,
                company_name,
                source_value,
                source_id,
                status_value,
                status_id,
                notes_value,
                cu["sub"],
                cu.get("name", ""),
            )
            await _sync_lead_links(
                db,
                lead_id,
                tags=tags if _row_has_any(row, "tags", "labels") else None,
                channels=channels if _row_has_any(row, "channels", "preferred_channels") else None,
            )
            created_lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid))
            await _capture_lead_snapshot(
                db,
                created_lead,
                source=source_value,
                action="lead_created_bulk",
                extra_metadata={"row_number": row_number},
            )
            summary["created"] += 1
        except HTTPException as exc:
            summary["errors"].append({"row": row_number, "error": str(exc.detail)})
        except Exception as exc:  # pragma: no cover - defensive import path
            logger.exception("lead bulk upload failed row=%s", row_number)
            summary["errors"].append({"row": row_number, "error": str(exc)})

    return summary


async def _fetch_lead_conversation_context(db, lead_id: str, cid: str) -> dict:
    """Return conversation_history string and last_message for a lead. Used to enrich scoring."""
    result: dict = {"conversation_history": "", "last_message": ""}
    try:
        customer = r(
            await db.fetchrow(
                "SELECT id FROM customers WHERE lead_id=$1 AND company_id=$2 LIMIT 1",
                lead_id, cid,
            )
        )
        if not customer:
            return result
        conv = r(
            await db.fetchrow(
                "SELECT id, channel FROM conversations "
                "WHERE customer_id=$1 AND company_id=$2 ORDER BY last_message_at DESC LIMIT 1",
                customer["id"], cid,
            )
        )
        if not conv:
            return result
        messages = rs(
            await db.fetch(
                "SELECT content, sender_type FROM messages "
                "WHERE conversation_id=$1 AND company_id=$2 "
                "AND COALESCE(TRIM(content), '') != '' "
                "ORDER BY created_at DESC LIMIT 15",
                conv["id"], cid,
            )
        )
        if not messages:
            return result
        lines: list[str] = [f"Channel: {conv.get('channel', 'chat')}"]
        last_customer_msg = ""
        for msg in reversed(messages):
            role = "Customer" if msg["sender_type"] == "customer" else "Agent"
            content = str(msg["content"] or "").strip()[:300]
            lines.append(f"{role}: {content}")
            if msg["sender_type"] == "customer" and not last_customer_msg:
                last_customer_msg = content
        result["conversation_history"] = "\n".join(lines)
        result["last_message"] = last_customer_msg

        # MiniLM buying signal + lead quality from conversation content
        if result["conversation_history"]:
            try:
                from services.conversation_engine.local_ml import (  # noqa: PLC0415
                    classify_buying_signal,
                    classify_lead_quality,
                )
                buying = classify_buying_signal(result["conversation_history"])
                quality = classify_lead_quality(result["conversation_history"])
                result["buying_signal"] = buying.get("signal", "")
                result["buying_score"] = buying.get("buying_score", 0.5)
                result["lead_quality_signal"] = quality.get("quality", "")
                result["lead_quality_score"] = quality.get("quality_score", 0.3)
            except Exception as exc:
                logger.debug("MiniLM buying/quality signal failed lead_id=%s: %s", lead_id, exc)
    except Exception as exc:
        logger.debug("Could not fetch lead conversation for scoring lead_id=%s: %s", lead_id, exc)
    return result


@router.post("/leads/{lead_id}/score")
async def score_lead(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1", lead_id, cid))
    if not lead:
        raise HTTPException(404, "Lead not found")
    conv_ctx = await _fetch_lead_conversation_context(db, lead_id, cid)
    enriched_lead = {**lead}
    if conv_ctx["conversation_history"]:
        enriched_lead["conversation_history"] = conv_ctx["conversation_history"]
    if conv_ctx["last_message"] and not enriched_lead.get("last_message"):
        enriched_lead["last_message"] = conv_ctx["last_message"]
    if conv_ctx.get("buying_signal"):
        enriched_lead["buying_signal"] = conv_ctx["buying_signal"]
    if conv_ctx.get("lead_quality_signal"):
        enriched_lead["lead_quality_signal"] = conv_ctx["lead_quality_signal"]
    workflow = await orchestrate_lead_workflow(
        LeadWorkflowRequest(
            company_id=cid,
            lead_id=lead_id,
            lead=enriched_lead,
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
    # Return the full lead detail (including nurture_messages, activities, etc.)
    # so the frontend can update the selected lead without losing existing data.
    return await _load_lead_details(db, lead_id, cid)


@router.post("/leads/{lead_id}/auto-score")
async def auto_score_lead(lead_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    lead = r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2 LIMIT 1", lead_id, cid))
    if not lead:
        raise HTTPException(404, "Lead not found")
    conv_ctx = await _fetch_lead_conversation_context(db, lead_id, cid)
    enriched_lead = {**lead}
    if conv_ctx["conversation_history"]:
        enriched_lead["conversation_history"] = conv_ctx["conversation_history"]
    if conv_ctx["last_message"] and not enriched_lead.get("last_message"):
        enriched_lead["last_message"] = conv_ctx["last_message"]
    if conv_ctx.get("buying_signal"):
        enriched_lead["buying_signal"] = conv_ctx["buying_signal"]
    if conv_ctx.get("lead_quality_signal"):
        enriched_lead["lead_quality_signal"] = conv_ctx["lead_quality_signal"]
    workflow = await orchestrate_lead_workflow(
        LeadWorkflowRequest(
            company_id=cid,
            lead_id=lead_id,
            lead=enriched_lead,
            source=str(lead.get("source") or "lead"),
            actor_user_id=cu.get("sub", ""),
            actor_user_role=cu.get("role", ""),
            auto_support=True,
        ),
        authorization=request.headers.get("authorization") or request.headers.get("Authorization", ""),
        db=db,
    )
    qualification = workflow.agent_outputs.qualification
    result = {
        **qualification,
        "nurture_message": str(qualification.get("nurture_message") or ""),
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
        "lead": await _load_lead_details(db, lead_id, cid),
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
    company_knowledge = await get_company_knowledge(
        db,
        cid,
        current_query=str(lead.get("notes") or lead.get("name") or ""),
        top_k=5,
    )
    ctx = await _build_lead_nurture_context(db, lead, cid, company_knowledge)
    result = await generate_nurture_message(
        lead,
        lead.get("status", "new"),
        ctx["company_context"],
        db=db,
        company_id=cid,
        conversation_history=ctx["conversation_history"],
    )
    message = _first_valid_nurture_message(result)
    stage = str(result.get("stage") or result.get("phase") or lead.get("status") or "new")
    if not message:
        return {**result, "message": "", "stage": stage}
    existing_message_id = await db.fetchval(
        "SELECT id FROM lead_nurture_messages WHERE lead_id=$1 AND company_id=$2 AND sent=FALSE AND TRIM(message)=$3 LIMIT 1",
        lead_id,
        cid,
        message,
    )
    if existing_message_id:
        return {**result, "message": message, "stage": stage, "message_id": existing_message_id, "duplicate": True}
    message_id = make_id()
    await db.execute(
        "INSERT INTO lead_activities(id,lead_id,company_id,type,content,stage,created_at) VALUES($1,$2,$3,'nurture',$4,$5,NOW())",  # noqa: E501
        make_id(),
        lead_id,
        cid,
        message,
        stage,
    )
    await db.execute(
        "INSERT INTO lead_nurture_messages(id,lead_id,company_id,message,phase,sent,created_at) VALUES($1,$2,$3,$4,$5,FALSE,NOW())",  # noqa: E501
        message_id,
        lead_id,
        cid,
        message,
        stage,
    )
    return {**result, "message": message, "stage": stage, "message_id": message_id}


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


@router.put("/leads/{lead_id}/nurture-messages/{message_id}")
async def update_lead_nurture_message(lead_id: str, message_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    body = await request.json()
    message_text = str(body.get("message") or body.get("content") or "").strip()
    if not message_text:
        raise HTTPException(400, "Nurture message cannot be empty")
    if not await db.fetchval("SELECT id FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid):
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
    phase = str(body.get("phase") or nurture_message.get("phase") or "awareness").strip() or "awareness"
    await db.execute(
        "UPDATE lead_nurture_messages SET message=$1,phase=$2 WHERE id=$3 AND lead_id=$4 AND company_id=$5",
        message_text,
        phase,
        message_id,
        lead_id,
        cid,
    )
    updated_message = r(
        await db.fetchrow(
            "SELECT * FROM lead_nurture_messages WHERE id=$1 AND lead_id=$2 AND company_id=$3 LIMIT 1",
            message_id,
            lead_id,
            cid,
        )
    )
    return {
        "status": "updated",
        "message": updated_message,
        "lead": await _load_lead_details(db, lead_id, cid),
    }


@router.delete("/leads/{lead_id}/nurture-messages/{message_id}")
async def delete_lead_nurture_message(lead_id: str, message_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not await db.fetchval("SELECT id FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid):
        raise HTTPException(404, "Lead not found")
    result = await db.execute(
        "DELETE FROM lead_nurture_messages WHERE id=$1 AND lead_id=$2 AND company_id=$3",
        message_id,
        lead_id,
        cid,
    )
    if str(result).upper().endswith(" 0"):
        raise HTTPException(404, "Nurture message not found")
    return {"status": "deleted", "message_id": message_id, "lead": await _load_lead_details(db, lead_id, cid)}


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
    try:
        await apply_message_stage_transition(
            db,
            company_id=cid,
            lead_id=lead_id,
            message_text=str(nurture_message.get("message") or ""),
            direction="outbound",
            source="message_sent",
            event_id=str((result.get("message") or {}).get("id") or message_id),
            changed_by_user_id=cu.get("sub", ""),
        )
    except Exception as exc:
        logger.warning("lead nurture stage transition failed lead_id=%s message_id=%s: %s", lead_id, message_id, exc)
    refreshed = await _load_lead_details(db, lead_id, cid)
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
    duplicate_customer = None
    duplicate_filters = []
    duplicate_args = [cid]
    if str(lead.get("email") or "").strip():
        duplicate_filters.append(f"LOWER(email)=${len(duplicate_args) + 1}")
        duplicate_args.append(str(lead.get("email") or "").strip().lower())
    if str(lead.get("phone") or "").strip():
        duplicate_filters.append(f"phone=${len(duplicate_args) + 1}")
        duplicate_args.append(str(lead.get("phone") or "").strip())
    if duplicate_filters:
        duplicate_customer = r(
            await db.fetchrow(
                f"SELECT id FROM customers WHERE company_id=$1 AND ({' OR '.join(duplicate_filters)}) LIMIT 1",
                *duplicate_args,
            )
        )
    _req_conn = _db_request_conn.get()
    if _req_conn is not None:
        async with _req_conn.transaction():
            transition = await transition_lead_stage(
                db,
                lead,
                "converted",
                reason="Converted manually by user",
                source="conversion",
                confidence=1.0,
                changed_by_user_id=cu.get("sub", ""),
                event_id=f"conversion:{lead_id}",
                automatic=True,
            )
            lead = transition.get("lead") or r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid)) or lead
            customer = await convert_lead_to_customer_state(db, lead, cu)
    else:
        transition = await transition_lead_stage(
            db,
            lead,
            "converted",
            reason="Converted manually by user",
            source="conversion",
            confidence=1.0,
            changed_by_user_id=cu.get("sub", ""),
            event_id=f"conversion:{lead_id}",
            automatic=True,
        )
        lead = transition.get("lead") or r(await db.fetchrow("SELECT * FROM leads WHERE id=$1 AND company_id=$2", lead_id, cid)) or lead
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
    create_safe_detached_task(
        db,
        db.execute(
            "INSERT INTO journey_tracking(id,company_id,user_id,lead_id,customer_id,phase,started_at,completed_at,created_at) VALUES($1,$2,$3,$4,$5,'conversion',NOW(),NOW(),NOW())",  # noqa: E501
            make_id(),
            cid,
            cu.get("sub", ""),
            lead_id,
            customer.get("id", ""),
        ),
        name=f"lead-conversion-journey-{lead_id}",
        company_id=cid,
        channel="lead",
        trace_id=_trace_id_from_context(),
        event_id=lead_id,
    )
    create_safe_detached_task(
        db,
        record_system_log(
            db,
            cu,
            "convert_lead",
            "lead",
            lead_id,
            {"customer_id": customer.get("id", "")},
        ),
        name=f"lead-conversion-log-{lead_id}",
        company_id=cid,
        channel="lead",
        trace_id=_trace_id_from_context(),
        event_id=lead_id,
    )
    refreshed = await _load_lead_details(db, lead_id, cid)
    return {
        "status": "converted",
        "customer": customer,
        "lead_id": lead_id,
        "lead": refreshed,
        "conversion_status": "linked_existing_customer" if duplicate_customer else "converted",
    }


async def _build_lead_nurture_context(db, lead: dict, cid: str, company_knowledge: str) -> dict:
    """Build rich context for nurture generation.

    Returns a dict with two separate sections:
      - company_context: company knowledge + lead notes (what the company offers)
      - conversation_history: recent messages + activity history (how to continue naturally)
    """
    lead_id = lead.get("id", "")

    company_parts: list[str] = []
    if company_knowledge:
        company_parts.append(company_knowledge)
    if lead.get("notes"):
        company_parts.append(f"Lead notes: {lead.get('notes')}")

    conversation_parts: list[str] = []

    try:
        customer = r(
            await db.fetchrow(
                "SELECT id, name, email, phone FROM customers WHERE lead_id=$1 AND company_id=$2 LIMIT 1",
                lead_id,
                cid,
            )
        )
        if customer:
            conv = r(
                await db.fetchrow(
                    "SELECT id, channel, last_message, last_message_at FROM conversations "
                    "WHERE customer_id=$1 AND company_id=$2 ORDER BY last_message_at DESC LIMIT 1",
                    customer["id"],
                    cid,
                )
            )
            if conv:
                messages = rs(
                    await db.fetch(
                        "SELECT content, sender_type, created_at FROM messages "
                        "WHERE conversation_id=$1 AND company_id=$2 "
                        "AND COALESCE(TRIM(content), '') != '' "
                        "ORDER BY created_at DESC LIMIT 10",
                        conv["id"],
                        cid,
                    )
                )
                if messages:
                    history_lines = []
                    for msg in reversed(messages):
                        role = "Customer" if msg["sender_type"] == "customer" else "Agent"
                        history_lines.append(f"  {role}: {str(msg['content'] or '').strip()[:200]}")
                    conversation_parts.append(
                        f"Recent conversation ({conv.get('channel', 'chat')}):\n" + "\n".join(history_lines)
                    )
    except Exception as exc:
        logger.debug("Could not fetch lead conversation context lead_id=%s: %s", lead_id, exc)

    try:
        activities = rs(
            await db.fetch(
                "SELECT type, content, stage, created_at FROM lead_activities "
                "WHERE lead_id=$1 AND company_id=$2 AND type != 'auto_nurture' "
                "ORDER BY created_at DESC LIMIT 5",
                lead_id,
                cid,
            )
        )
        if activities:
            act_lines = [
                f"  [{a.get('type', '')}] {str(a.get('content') or '').strip()[:120]}"
                for a in reversed(activities) if a.get("content")
            ]
            if act_lines:
                conversation_parts.append("Lead activity history:\n" + "\n".join(act_lines))
    except Exception as exc:
        logger.debug("Could not fetch lead activities lead_id=%s: %s", lead_id, exc)

    return {
        "company_context": "\n\n".join(company_parts),
        "conversation_history": "\n\n".join(conversation_parts),
    }


async def _nurture_single_lead(db, lead: dict, cid: str, company_knowledge: str) -> dict:
    """Generate and persist a nurture message for one lead. Replace any unsent drafts."""
    lead_id = lead["id"]
    try:
        # Replace existing unsent drafts with fresh ones (don't block on pending messages)
        await db.execute(
            "UPDATE lead_nurture_messages SET sent=TRUE, phase='replaced' "
            "WHERE lead_id=$1 AND company_id=$2 AND sent=FALSE",
            lead_id,
            cid,
        )

        ctx = await _build_lead_nurture_context(db, lead, cid, company_knowledge)
        result = await generate_nurture_message(
            lead,
            lead.get("status", "new"),
            ctx["company_context"],
            db=db,
            company_id=cid,
            conversation_history=ctx["conversation_history"],
        )
        message = _first_valid_nurture_message(result)
        stage = str(result.get("stage") or result.get("phase") or lead.get("status") or "new")
        message_id = ""
        if message:
            message_id = make_id()
            await db.execute(
                "INSERT INTO lead_activities(id,lead_id,company_id,type,content,stage,created_at) "
                "VALUES($1,$2,$3,'auto_nurture',$4,$5,NOW())",
                make_id(), lead_id, cid, message, stage,
            )
            await db.execute(
                "INSERT INTO lead_nurture_messages(id,lead_id,company_id,message,phase,sent,created_at) "
                "VALUES($1,$2,$3,$4,$5,FALSE,NOW())",
                message_id, lead_id, cid, message, stage,
            )
        return {
            "lead_id": lead_id,
            "name": lead.get("name", ""),
            "message_id": message_id,
            "status": "nurtured" if message_id else "skipped_empty",
        }
    except Exception as exc:
        logger.error("Auto-nurture failed for lead_id=%s: %s", lead_id, exc)
        return {
            "lead_id": lead_id,
            "name": lead.get("name", ""),
            "status": "failed",
            "error": str(exc)[:200],
        }


@router.post("/leads/auto-nurture-all")
async def auto_nurture_all_leads(request: Request):
    db = _db(request)
    cu = await require_roles(request, ["admin"])
    cid = cu.get("company_id", "")

    # Include ALL eligible leads regardless of pending draft state
    # Existing unsent drafts will be replaced with fresh context-aware messages
    leads = rs(
        await db.fetch(
            """
            SELECT *
            FROM leads l
            WHERE l.company_id=$1
              AND l.status=ANY($2)
            ORDER BY l.updated_at DESC
            LIMIT 20
            """,
            cid,
            ["new", "contacted", "qualified"],
        )
    )

    if not leads:
        return {"total_processed": 0, "results": [], "message": "No eligible leads found."}

    # Fetch company knowledge once (shared across all leads for this batch)
    company_knowledge = await get_company_knowledge(db=db, company_id=cid, current_query="", top_k=5)

    # Process leads concurrently (max 3 at a time to stay within LLM rate limits)
    semaphore = asyncio.Semaphore(3)

    async def _bounded(lead: dict) -> dict:
        async with semaphore:
            return await _nurture_single_lead(db, lead, cid, company_knowledge)

    results = list(await asyncio.gather(*[_bounded(lead) for lead in leads]))

    nurtured = sum(1 for r in results if r.get("status") == "nurtured")
    return {
        "total_processed": len(results),
        "total_nurtured": nurtured,
        "results": results,
    }
