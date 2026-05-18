"""routers/conversations.py - PostgreSQL version."""

import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from agent_orchestrator.agents.adaptive_qualification import _update_qualification_silently
from agent_orchestrator.schemas import MessageWorkflowRequest
from channel_layer.channel_identity import (
    company_default_phone_region,
    normalize_email as normalize_channel_email,
    normalize_whatsapp_phone,
    resolve_outbound_recipient as resolve_channel_outbound_recipient,
)
from channel_layer.router import get_outbound_router
from channel_layer.schemas import ChannelType

from services.ai_service.facade import (
    build_sentiment_gate,
    summarize_conversation,
    wait_for_ai_response_timing,
)
from services.agent_orchestrator.facade import orchestrate_message_workflow
from core.socket import emit_message_deleted, emit_message_updated, emit_new_message
from core.utils import make_id, now_ts
from shared.tracing import current_trace_context
from shared.webhook_task_runner import create_safe_detached_task
from services.db_helpers import (
    AI_API_EXHAUSTED_MANUAL_MESSAGE,
    _notify_agents_handoff,
    conversation_ai_auto_paused,
    disable_company_ai_after_api_exhaustion,
    escalate_conversation_to_human,
    fetch_messages_with_attachments,
    get_company_id,
    get_current_user_flexible,
    get_or_create_contact_conversation,
    get_or_create_customer_from_contact,
    is_company_ai_enabled,
    is_ai_api_exhaustion_payload,
    persist_ai_session_record,
    persist_chat_history,
    persist_user_ai_memory,
    r,
    refresh_conversation_rollup,
    normalize_attachment_row,
    rs,
    save_message_attachments,
)
from services.messaging_service import _persist_outbound_message_state
from services.lead_stage_service import apply_message_stage_transition
from services.media_storage import serve_stored_media

from shared.usage_guard import (
    conversation_limit_completed_message,
    conversation_limit_status,
    raise_conversation_limit_completed,
    reserve_conversation_usage,
)

logger = logging.getLogger(__name__)
router = APIRouter()

async def _reserve_outbound_or_raise(db, company_id: str, *, channel: str, idempotency_key: str) -> None:
    reservation = await reserve_conversation_usage(
        db,
        company_id,
        channel=channel,
        idempotency_key=idempotency_key,
    )
    if reservation == "denied":
        raise_conversation_limit_completed(await conversation_limit_status(db, company_id))

CONVERSATION_UPDATE_FIELDS = {
    "subject",
    "status",
    "priority",
    "assigned_to",
    "assigned_to_name",
    "channel",
    "customer_name",
    "ai_handled",
    "ai_auto_paused",
    "ai_paused_at",
    "ai_paused_reason",
    "ai_paused_error_type",
    "ai_paused_provider",
    "ai_paused_model",
    "ai_paused_scope",
    "sentiment_score",
    "sentiment_label",
    "last_message",
    "last_message_at",
    "unread_count",
    "page_url",
    "session_id",
    "escalated_to",
    "escalated_to_name",
    "escalated_at",
    "resolution_notes",
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


def _resolve_conversation_recipient(channel: str, convo: dict, customer: dict) -> str:
    normalized = str(channel or "").strip().lower()
    identity = resolve_channel_outbound_recipient(normalized, convo, customer)
    if identity.is_valid:
        return identity.canonical_value
    logger.warning(
        "Failed to resolve conversation recipient channel=%s conversation_id=%s customer_id=%s raw=%s reason=%s",
        normalized,
        convo.get("id", ""),
        customer.get("id", ""),
        identity.raw_value,
        identity.reason,
    )
    if normalized == "whatsapp":
        return str(convo.get("channel_id") or customer.get("phone") or "").strip()
    return ""


def _email_sender_label(company_name: str, fallback: str = "") -> str:
    base = " ".join(str(company_name or fallback or "Business").replace("\r", " ").replace("\n", " ").split()).strip()
    return f"Message from {base or 'Business'}"


def _clean_email_subject(subject: str, company_name: str) -> str:
    value = re.sub(r"\s+", " ", str(subject or "").replace("\r", " ").replace("\n", " ")).strip()
    value = re.sub(r"\s*\([^)]*(?:inbox|channel|_)[^)]*\)\s*", " ", value, flags=re.IGNORECASE).strip()
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^(?:re:\s*)+", "", value, flags=re.IGNORECASE).strip()
    if not value or re.search(r"\binbox_[a-z0-9_+-]+\b", value, re.IGNORECASE):
        return _email_sender_label(company_name)
    return value


def _reply_email_subject(subject: str, company_name: str) -> str:
    cleaned = _clean_email_subject(subject, company_name)
    default_subject = _email_sender_label(company_name)
    return cleaned if cleaned == default_subject else f"Re: {cleaned}"


async def _company_email_sender_name(db, company_id: str) -> str:
    if not company_id:
        return _email_sender_label("")
    company_name = str(
        await db.fetchval("SELECT NULLIF(BTRIM(name), '') FROM companies WHERE id=$1 LIMIT 1", company_id) or ""
    )
    return _email_sender_label(company_name)


async def _mark_outbound_message_failed(
    db,
    *,
    company_id: str,
    db_message_id: str,
    error: str = "",
) -> None:
    scoped_company_id = str(company_id or "").strip()
    local_message_id = str(db_message_id or "").strip()
    if not db or not scoped_company_id or not local_message_id:
        return
    try:
        await _persist_outbound_message_state(
            db,
            company_id=scoped_company_id,
            db_message_id=local_message_id,
            delivery_status="failed",
        )
    except Exception as exc:
        logger.warning(
            "Failed to mark outbound message failed company_id=%s message_id=%s error=%s update_error=%s",
            scoped_company_id,
            local_message_id,
            error,
            exc,
        )


async def _send_outbound_via_channel_layer(
    *,
    db,
    company_id: str,
    channel: str,
    recipient_id: str,
    content: str,
    conversation_id: str,
    attachments: Optional[list] = None,
    db_message_id: str = "",
    metadata: Optional[dict] = None,
    subject: str = "",
) -> tuple[bool, str]:
    channel_type = _channel_type_from_name(channel)
    if not channel_type:
        error = f"Unsupported outbound channel: {channel}"
        await _mark_outbound_message_failed(db, company_id=company_id, db_message_id=db_message_id, error=error)
        return False, error

    recipient = str(recipient_id or "").strip()
    selected_recipient = recipient
    if channel_type == ChannelType.WHATSAPP:
        default_region = await company_default_phone_region(db, company_id)
        identity = normalize_whatsapp_phone(recipient, default_region=default_region)
        if not identity.is_valid:
            error = (
                "Invalid WhatsApp phone number. Save the contact number in full international format "
                "or set the tenant default phone region in Company Settings."
            )
            logger.warning(
                "Invalid WhatsApp outbound recipient trace_id=%s company_id=%s conversation_id=%s customer_id=%s "
                "channel=%s message_id=%s raw_identity=%s normalized_identity=%s selected_outbound_recipient=%s reason=%s",
                str((metadata or {}).get("trace_id") or ""),
                company_id,
                conversation_id,
                str((metadata or {}).get("customer_id") or ""),
                channel,
                db_message_id,
                recipient,
                identity.canonical_value,
                selected_recipient,
                identity.reason,
            )
            await _mark_outbound_message_failed(db, company_id=company_id, db_message_id=db_message_id, error=error)
            return False, error
        recipient = identity.canonical_value
        selected_recipient = recipient
    elif channel_type == ChannelType.EMAIL:
        identity = normalize_channel_email(recipient)
        if not identity.is_valid:
            error = "Invalid recipient email address"
            await _mark_outbound_message_failed(db, company_id=company_id, db_message_id=db_message_id, error=error)
            return False, error
        recipient = identity.canonical_value
        selected_recipient = recipient
    if not recipient:
        error = "Missing outbound recipient"
        await _mark_outbound_message_failed(db, company_id=company_id, db_message_id=db_message_id, error=error)
        return False, error

    message_metadata = dict(metadata or {})
    if not message_metadata.get("trace_id"):
        message_metadata["trace_id"] = _trace_id_from_context()
    logger.info(
        "Outbound recipient resolved trace_id=%s company_id=%s conversation_id=%s customer_id=%s channel=%s "
        "message_id=%s raw_identity=%s normalized_identity=%s selected_outbound_recipient=%s",
        message_metadata.get("trace_id", ""),
        company_id,
        conversation_id,
        str(message_metadata.get("customer_id") or ""),
        channel,
        db_message_id,
        recipient_id,
        recipient,
        selected_recipient,
    )

    result = await get_outbound_router().send_to_channel(
        tenant_id=str(company_id or "").strip(),
        channel_type=channel_type,
        external_user_id=recipient,
        content=content,
        db=db,
        subject=str(subject or "").strip(),
        metadata=message_metadata,
        conversation_id=conversation_id,
        attachments=attachments or [],
        db_message_id=db_message_id,
    )
    if result.success:
        await _persist_outbound_message_state(
            db,
            company_id=company_id,
            db_message_id=db_message_id,
            delivery_status="sent",
            external_message_id=str(result.external_message_id or ""),
        )
    else:
        await _persist_outbound_message_state(
            db,
            company_id=company_id,
            db_message_id=db_message_id,
            delivery_status="failed",
        )
    return bool(result.success), str(result.error or "")


_INBOX_VISIBILITY_SCHEMA_READY = False


async def _ensure_inbox_visibility_schema(db) -> None:
    global _INBOX_VISIBILITY_SCHEMA_READY
    if _INBOX_VISIBILITY_SCHEMA_READY:
        return
    statements = (
        "ALTER TABLE customer_channels ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE customer_channels ADD COLUMN IF NOT EXISTS is_visible BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE customer_channels ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "ALTER TABLE customer_channels ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "ALTER TABLE customer_social_profiles ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE customer_social_profiles ADD COLUMN IF NOT EXISTS is_visible BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE customer_social_profiles ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "ALTER TABLE customer_social_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "CREATE INDEX IF NOT EXISTS idx_customer_channels_visible_channel ON customer_channels(channel, is_active, is_visible)",
        "CREATE INDEX IF NOT EXISTS idx_customer_social_profiles_visible_platform ON customer_social_profiles(platform, is_active, is_visible)",
    )
    for statement in statements:
        await db.execute(statement)
    _INBOX_VISIBILITY_SCHEMA_READY = True


async def _ensure_visible_social_conversations(db, company_id: str) -> None:
    scoped_company_id = str(company_id or "").strip()
    if not scoped_company_id:
        return
    await _ensure_inbox_visibility_schema(db)
    rows = await db.fetch(
        "SELECT c.id AS customer_id,c.name AS customer_name,c.avatar,cc.channel,"
        "COALESCE(csp.profile_id,'') AS channel_id "
        "FROM customers c "
        "JOIN customer_channels cc ON cc.customer_id=c.id "
        "LEFT JOIN customer_social_profiles csp ON csp.customer_id=c.id AND csp.platform=cc.channel "
        "WHERE c.company_id=$1 AND cc.channel IN ('facebook','instagram') "
        "AND cc.is_active=TRUE AND cc.is_visible=TRUE "
        "AND COALESCE(csp.is_active, TRUE)=TRUE AND COALESCE(csp.is_visible, TRUE)=TRUE "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM conversations x "
        "  WHERE x.company_id=c.company_id AND x.customer_id=c.id AND x.channel=cc.channel "
        "  AND x.status=ANY($2)"
        ") "
        "ORDER BY c.updated_at DESC LIMIT 200",
        scoped_company_id,
        ["open", "pending", "escalated"],
    )
    for row in rows or []:
        await db.execute(
            "INSERT INTO conversations(id,company_id,customer_id,customer_name,customer_avatar,channel,channel_id,"
            "subject,status,priority,assigned_to,assigned_name,ai_handled,sentiment_score,sentiment_label,"
            "message_count,last_message,last_message_at,unread_count,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,'open','medium','','',TRUE,0,'neutral',0,'',NOW(),0,NOW(),NOW()) "
            "ON CONFLICT DO NOTHING",
            make_id(),
            scoped_company_id,
            row["customer_id"],
            row["customer_name"] or "Unknown Contact",
            row["avatar"] or "",
            row["channel"],
            row["channel_id"] or "",
            f"New {format_channel_name(row['channel'])} conversation",
        )


def _float_or_none(value) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _message_preview(content: str, attachments: Optional[list], sender_type: str) -> str:
    text = (content or "").strip()
    if text:
        return text[:100]
    normalized = [item for item in (attachments or []) if isinstance(item, dict)]
    if not normalized:
        return ""
    has_image = any(str(item.get("type") or item.get("file_type") or "").lower() == "image" for item in normalized)
    if has_image:
        return "Received image" if sender_type == "customer" else "Sent image"
    has_video = any(str(item.get("type") or item.get("file_type") or "").lower() == "video" for item in normalized)
    if has_video:
        return "Received video" if sender_type == "customer" else "Sent video"
    return "Received attachment" if sender_type == "customer" else "Sent attachment"


async def _load_message_with_attachments(db, message_id: str) -> dict:
    msg = r(await db.fetchrow("SELECT * FROM messages WHERE id=$1", message_id)) or {}
    if not msg:
        return {}
    rows = await db.fetch(
        "SELECT * FROM message_attachments WHERE message_id=$1 ORDER BY created_at ASC",
        message_id,
    )
    msg["attachments"] = [normalize_attachment_row(dict(row)) for row in rows]
    return msg


async def _load_scoped_conversation(db, convo_id: str, current_user: dict) -> dict:
    cid = get_company_id(current_user)
    if cid:
        convo = r(
            await db.fetchrow(
                "SELECT * FROM conversations WHERE id=$1 AND company_id=$2 LIMIT 1",
                convo_id,
                cid,
            )
        )
    else:
        convo = r(await db.fetchrow("SELECT * FROM conversations WHERE id=$1 LIMIT 1", convo_id))
    if not convo:
        raise HTTPException(404, "Conversation not found")
    return convo


async def _channel_statuses_for_conversations(db, company_id: str, current_user: dict, conversations: list[dict]) -> dict:
    from services.messaging_service import get_channel_connection_status

    channels = {
        str((convo or {}).get("channel") or "").strip().lower()
        for convo in conversations or []
        if str((convo or {}).get("channel") or "").strip()
    }
    statuses = {}
    for channel_name in channels:
        try:
            statuses[channel_name] = await get_channel_connection_status(
                db,
                company_id=company_id,
                channel=channel_name,
                user_id=str(current_user.get("sub") or ""),
            )
        except Exception as exc:
            statuses[channel_name] = {
                "connected": False,
                "status": "error",
                "error": f"{format_channel_name(channel_name)} status could not be checked.",
            }
            logger.warning(
                "Channel status lookup failed company_id=%s channel=%s error=%s",
                company_id,
                channel_name,
                exc,
            )
    return statuses


def format_channel_name(channel_name: str) -> str:
    return str(channel_name or "channel").replace("_", " ").strip().title()


def _decorate_conversation_channel_status(conversation: dict, statuses: dict) -> dict:
    item = dict(conversation or {})
    channel_name = str(item.get("channel") or "").strip().lower()
    status = dict(statuses.get(channel_name) or {"connected": True, "status": "ready", "error": ""})
    item["channel_connected"] = bool(status.get("connected"))
    item["channel_status"] = str(status.get("status") or ("ready" if item["channel_connected"] else "not_connected"))
    item["channel_error"] = str(status.get("error") or "")
    item["channel_connection"] = status
    return item


@router.get("/conversations")
async def list_conversations(
    request: Request,
    status: Optional[str] = None,
    channel: Optional[str] = None,
    inbox_filter: Optional[str] = None,
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    sql = "SELECT c.*, ARRAY(SELECT tag FROM conversation_tags WHERE conversation_id=c.id) AS tags FROM conversations c"
    args = []
    where = []
    if cid:
        where.append(f"c.company_id=${len(args) + 1}")
        args.append(cid)
    else:
        return []
    await _ensure_visible_social_conversations(db, cid)
    if status:
        where.append(f"c.status=${len(args) + 1}")
        args.append(status)
    if channel:
        where.append(f"c.channel=${len(args) + 1}")
        args.append(channel)
    normalized_filter = str(inbox_filter or "").strip().lower().replace("-", "_")
    aliases = {
        "incoming": "incoming_messages",
        "incoming_messages": "incoming_messages",
        "active": "active_conversations",
        "active_conversations": "active_conversations",
        "pending": "pending_replies",
        "pending_replies": "pending_replies",
        "ai": "ai_chats",
        "ai_chat": "ai_chats",
        "ai_chats": "ai_chats",
        "human": "human_chats",
        "human_chat": "human_chats",
        "human_chats": "human_chats",
        "unread": "unread",
    }
    normalized_filter = aliases.get(normalized_filter, "")
    active_statuses = ["open", "pending", "escalated"]
    if normalized_filter in {"active_conversations", "pending_replies", "ai_chats", "human_chats"}:
        where.append(f"c.status=ANY(${len(args) + 1})")
        args.append(active_statuses)
    if normalized_filter == "incoming_messages":
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        where.append(
            "EXISTS (SELECT 1 FROM messages m "
            "WHERE m.company_id=c.company_id AND m.conversation_id=c.id "
            f"AND m.sender_type='customer' AND m.created_at >= ${len(args) + 1})"
        )
        args.append(today_start)
    elif normalized_filter == "pending_replies":
        where.append(
            "(SELECT m.sender_type FROM messages m "
            "WHERE m.company_id=c.company_id AND m.conversation_id=c.id "
            "ORDER BY m.created_at DESC LIMIT 1)='customer'"
        )
    elif normalized_filter == "ai_chats":
        where.append("c.ai_handled=TRUE")
    elif normalized_filter == "human_chats":
        where.append("c.ai_handled=FALSE")
    elif normalized_filter == "unread":
        where.append("c.unread_count > 0")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.last_message_at DESC LIMIT 500"
    conversations = rs(await db.fetch(sql, *args))
    statuses = await _channel_statuses_for_conversations(db, cid, cu, conversations)
    return [_decorate_conversation_channel_status(convo, statuses) for convo in conversations]


@router.post("/conversations/start")
async def start_conversation(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    body = await request.json()
    phone = (body.get("phone", "") or "").strip()
    if not phone:
        raise HTTPException(400, "Phone is required")
    name = (body.get("name", "") or "").strip()
    channel = body.get("channel") or "web_chat"
    source = body.get("source") or "inbox_start"
    customer = await get_or_create_customer_from_contact(db, name, phone, cu, channel=channel)
    convo = await get_or_create_contact_conversation(
        db,
        customer,
        channel,
        source,
        cu,
        channel_id=customer.get("phone") or phone,
    )
    return {"conversation": convo, "customer": customer}


@router.post("/conversations/start-outbound")
async def start_outbound_conversation(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    body = await request.json()
    channel = (body.get("channel", "") or "").strip().lower()
    if channel not in ("whatsapp", "facebook", "instagram", "email"):
        raise HTTPException(400, "Channel must be whatsapp, facebook, instagram, or email")
    name = (body.get("name", "") or "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    initial_message = (body.get("initial_message", "") or "").strip()
    if not initial_message:
        raise HTTPException(400, "Initial message is required")

    if channel == "whatsapp":
        contact_ref = (body.get("phone", "") or "").strip()
        if not contact_ref:
            raise HTTPException(400, "Phone number is required for WhatsApp")
        identity = normalize_whatsapp_phone(
            contact_ref,
            default_region=await company_default_phone_region(db, cid),
        )
        if not identity.is_valid:
            raise HTTPException(
                400,
                "Invalid WhatsApp phone number. Save the contact number in full international format "
                "or set the tenant default phone region in Company Settings.",
            )
        contact_ref = identity.canonical_value
        recipient_id = ""
    else:
        recipient_id = (body.get("recipient_id", "") or "").strip()
        if not recipient_id:
            raise HTTPException(400, f"{channel.capitalize()} recipient ID is required")
        contact_ref = recipient_id

    customer = await get_or_create_customer_from_contact(
        db,
        name,
        contact_ref if channel == "whatsapp" else "",
        cu,
        email=contact_ref if channel == "email" else "",
        channel=channel,
        channel_profile_id=recipient_id if channel in {"facebook", "instagram"} else "",
    )
    convo = await get_or_create_contact_conversation(
        db,
        customer,
        channel,
        body.get("source", f"inbox_{channel}"),
        cu,
        channel_id=(customer.get("phone") or contact_ref) if channel == "whatsapp" else contact_ref,
    )

    msg_id = make_id()
    await _reserve_outbound_or_raise(
        db,
        cid,
        channel=channel,
        idempotency_key=f"api:{cid}:msg:{msg_id}",
    )
    company_sender_name = await _company_email_sender_name(db, cid) if channel == "email" else ""
    message_sender_name = company_sender_name or cu.get("name", "") or cu.get("email", "")
    await db.execute(
        "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,"
        "delivery_status,read,created_at) "
        "VALUES($1,$2,$3,$4,'agent',$5,$6,'sending',FALSE,NOW())",
        msg_id,
        cu.get("company_id", ""),
        convo["id"],
        initial_message,
        cu.get("sub", ""),
        message_sender_name,
    )

    outbound_recipient = contact_ref if channel == "whatsapp" else recipient_id
    sent, error = await _send_outbound_via_channel_layer(
        db=db,
        company_id=cid,
        channel=channel,
        recipient_id=outbound_recipient,
        content=initial_message,
        conversation_id=convo["id"],
        db_message_id=msg_id,
        metadata={
            "source": "start_outbound",
            "actor_user_id": cu.get("sub", ""),
            "actor_user_role": cu.get("role", ""),
            "trace_id": _trace_id_from_context(),
            "customer_id": str(customer.get("id") or ""),
            "raw_sender_id": str(contact_ref or recipient_id or ""),
            "normalized_sender_id": outbound_recipient,
            "selected_outbound_recipient": outbound_recipient,
        },
        subject=_email_sender_label(company_sender_name.replace("Message from ", "", 1)) if channel == "email" else "",
    )
    await db.execute(
        "UPDATE conversations SET last_message=$1,last_message_at=NOW(),updated_at=NOW(),message_count=message_count+1,ai_handled=FALSE "
        "WHERE id=$2",
        initial_message[:100],
        convo["id"],
    )
    await persist_chat_history(
        db,
        convo,
        {
            "id": msg_id,
            "conversation_id": convo["id"],
            "content": initial_message,
            "sender_type": "agent",
            "sender_name": message_sender_name,
            "created_at": now_ts(),
        },
    )
    updated_convo = r(
        await db.fetchrow(
            "SELECT * FROM conversations WHERE id=$1 AND company_id=$2",
            convo["id"],
            cid,
        )
    )
    return {
        "conversation": updated_convo,
        "customer": customer,
        "outbound_sent": bool(sent),
        "outbound_error": str(error or "").strip(),
        "channel": channel,
    }


@router.put("/conversations/{convo_id}")
async def update_conversation(convo_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    body = await request.json()
    body.pop("_id", None)
    safe_body = {k: v for k, v in body.items() if k in CONVERSATION_UPDATE_FIELDS}
    if body and not safe_body:
        raise HTTPException(400, "No valid fields provided")
    safe_body["updated_at"] = now_ts()
    tags = body.pop("tags", None)
    if safe_body:
        columns = list(safe_body.keys())
        set_parts = ", ".join(f"{k}=${i + 3}" for i, k in enumerate(columns))
        values = [safe_body[col] for col in columns]
        await db.execute(
            f"UPDATE conversations SET {set_parts} WHERE id=$1 AND company_id=$2",
            convo_id,
            cid,
            *values,
        )
    if tags is not None:
        await db.execute("DELETE FROM conversation_tags WHERE conversation_id=$1", convo_id)
        if tags:
            await db.executemany(
                "INSERT INTO conversation_tags(conversation_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                [(convo_id, t) for t in tags],
            )
    await db.execute(
        "INSERT INTO conversation_logs(id,company_id,convo_id,user_id,action_type,field_name,logged_at,created_at) "
        "VALUES($1,$2,$3,$4,'update','multiple_fields',NOW(),NOW())",
        make_id(),
        cid,
        convo_id,
        cu.get("sub", ""),
    )
    updated = r(await db.fetchrow("SELECT * FROM conversations WHERE id=$1 AND company_id=$2", convo_id, cid))
    statuses = await _channel_statuses_for_conversations(db, cid, cu, [updated])
    return _decorate_conversation_channel_status(updated, statuses)


@router.put("/conversations/{convo_id}/mark-read")
async def mark_conversation_read(convo_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not await db.fetchval("SELECT id FROM conversations WHERE id=$1 AND company_id=$2", convo_id, cid):
        raise HTTPException(404, "Conversation not found")
    await db.execute(
        "UPDATE messages SET read=TRUE WHERE conversation_id=$1 AND company_id=$2 AND read=FALSE",
        convo_id,
        cid,
    )
    await db.execute(
        "UPDATE conversations SET unread_count=0,updated_at=NOW() WHERE id=$1 AND company_id=$2",
        convo_id,
        cid,
    )
    return {"status": "marked_read", "conversation_id": convo_id}


@router.put("/conversations/{convo_id}/toggle-ai")
async def toggle_ai_mode(convo_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    body = await request.json()
    enable_ai = body.get("enable_ai", True)
    convo = r(
        await db.fetchrow(
            "SELECT * FROM conversations WHERE id=$1 AND company_id=$2 LIMIT 1",
            convo_id,
            cid,
        )
    )
    if not convo:
        raise HTTPException(404, "Conversation not found")

    escalation_notice = None
    if not enable_ai:
        escalation = await escalate_conversation_to_human(
            db,
            convo_id,
            convo.get("company_id", ""),
            convo.get("customer_name", "Customer"),
            convo.get("channel", "web_chat"),
            agent_id=cu.get("sub", ""),
            agent_name=cu.get("name", "Human Agent"),
            automatic=False,
        )
        escalation_notice = escalation["escalation_notice"]
        await emit_new_message(convo_id, escalation["message"])
    else:
        await db.execute(
            "UPDATE conversations SET ai_handled=TRUE,status='open',escalation_notice=NULL,escalated_at=NULL,"
            "escalated_to='',escalated_to_name='',ai_auto_paused=FALSE,ai_paused_at=NULL,ai_paused_reason='',"
            "ai_paused_error_type='',ai_paused_provider='',ai_paused_model='',ai_paused_scope='',updated_at=NOW() "
            "WHERE id=$1 AND company_id=$2",
            convo_id,
            cid,
        )
        reac_id = make_id()
        message_text = "AI assistant re-activated. Automatic replies resumed."
        await db.execute(
            "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,read,created_at) "  # noqa: E501
            "VALUES($1,$2,$3,$4,'system','system','System',FALSE,NOW())",
            reac_id,
            convo.get("company_id", ""),
            convo_id,
            message_text,
        )
        await emit_new_message(
            convo_id,
            {
                "id": reac_id,
                "conversation_id": convo_id,
                "content": message_text,
                "sender_type": "system",
                "created_at": str(now_ts()),
            },
        )
    updated = r(
        await db.fetchrow(
            "SELECT * FROM conversations WHERE id=$1 AND company_id=$2",
            convo_id,
            cid,
        )
    )
    statuses = await _channel_statuses_for_conversations(db, cid, cu, [updated])
    return {
        "conversation": _decorate_conversation_channel_status(updated, statuses),
        "escalation_notice": escalation_notice,
    }


@router.delete("/conversations/{convo_id}")
async def delete_conversation(convo_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu) or ""
    if not await db.fetchval("SELECT id FROM conversations WHERE id=$1 AND company_id=$2", convo_id, cid):
        raise HTTPException(404, "Conversation not found")
    await db.execute("DELETE FROM messages WHERE conversation_id=$1", convo_id)
    await db.execute("DELETE FROM tickets WHERE conversation_id=$1", convo_id)
    await db.execute("DELETE FROM conversations WHERE id=$1", convo_id)
    try:
        from core.socket import sio

        await sio.emit(
            "conversation_updated",
            {"conversation_id": convo_id, "deleted": True},
            room=None,
        )
    except Exception as e:
        logger.error(f"Socket emit error: {e}")
    return {"status": "deleted", "conversation_id": convo_id}


@router.get("/conversations/{convo_id}/messages")
async def get_messages(convo_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    await _load_scoped_conversation(db, convo_id, cu)
    return await fetch_messages_with_attachments(db, convo_id, limit=500)


@router.get("/conversations/attachments/media/{company_id}/{filename}")
async def get_conversation_attachment_media(company_id: str, filename: str) -> FileResponse:
    return serve_stored_media(category="message-attachments", company_id=company_id, filename=filename)


@router.post("/conversations/{convo_id}/messages")
async def send_message(convo_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    body = await request.json()
    content = (body.get("content", "") or "").strip()
    sender_type = body.get("sender_type", "agent")
    attachments = body.get("attachments", []) if isinstance(body.get("attachments", []), list) else []
    convo = await _load_scoped_conversation(db, convo_id, cu)
    if not content and not attachments:
        raise HTTPException(400, "Message content or an attachment is required")

    company_id = cu.get("company_id", "") or convo.get("company_id", "")
    if attachments and convo.get("channel") in ("facebook", "instagram"):
        raise HTTPException(
            400,
            f"Image sending is not supported for {convo.get('channel')} in this version",
        )
    msg_id = make_id()
    sent_score = None
    sent_emotion = None
    sent_conf = None
    intent_type = None
    intent_conf = None
    sentiment = {}
    intent = {}
    conversation_sentiment = {}
    sentiment_gate = build_sentiment_gate(content, sentiment)
    trace_id = _trace_id_from_context()
    outbound_delivered = True
    outbound_error = ""
    outbound_channel_name = str(convo.get("channel") or "")
    is_outbound_agent_message = sender_type == "agent" and outbound_channel_name in ("whatsapp", "facebook", "instagram", "email")
    if sender_type == "agent":
        await _reserve_outbound_or_raise(
            db,
            company_id,
            channel=str(convo.get("channel") or "web_chat"),
            idempotency_key=f"api:{company_id}:msg:{msg_id}",
        )
    company_sender_name = await _company_email_sender_name(db, company_id) if outbound_channel_name == "email" else ""
    message_sender_name = (
        company_sender_name
        if sender_type == "agent" and outbound_channel_name == "email"
        else (cu.get("name", "") if sender_type != "customer" else convo.get("customer_name", ""))
    ) or cu.get("email", "")

    await db.execute(
        "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,"
        "sentiment_score,sentiment_emotion,sentiment_confidence,intent_type,intent_confidence,delivery_status,read,created_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,FALSE,NOW())",
        msg_id,
        company_id,
        convo_id,
        content,
        sender_type,
        cu["sub"] if sender_type != "customer" else convo.get("customer_id", ""),
        message_sender_name,
        sent_score,
        sent_emotion,
        sent_conf,
        intent_type,
        intent_conf,
        "sending" if is_outbound_agent_message else "pending",
    )
    saved_attachments = await save_message_attachments(db, msg_id, attachments)
    preview = _message_preview(content, saved_attachments, sender_type)
    await persist_chat_history(
        db,
        convo,
        {
            "id": msg_id,
            "conversation_id": convo_id,
            "content": content,
            "sender_type": sender_type,
            "sender_name": message_sender_name,
            "attachments": saved_attachments,
            "created_at": now_ts(),
        },
    )
    await db.execute(
        "UPDATE conversations SET last_message=$1,last_message_at=NOW(),updated_at=NOW(),message_count=message_count+1,"
        "unread_count=unread_count+$2,ai_handled=CASE WHEN $4 THEN FALSE ELSE ai_handled END WHERE id=$3",
        preview,
        1 if sender_type == "customer" else 0,
        convo_id,
        sender_type == "agent",
    )
    if sender_type == "customer":
        res = await reserve_conversation_usage(
            db,
            company_id,
            channel=str(convo.get("channel") or "web_chat"),
            idempotency_key=f"api:{company_id}:msg:{msg_id}",
        )
        if res == "denied":
            logger.info(
                "Inbound customer message accepted without usage increment because conversation limit is exhausted company_id=%s conversation_id=%s message_id=%s",
                company_id,
                convo_id,
                msg_id,
            )
    workflow = None
    support_plan: dict = {}
    capture: dict = {}
    intent: dict = {}
    outbound_limit_exhausted = False
    if sender_type == "customer":
        limit_state = await conversation_limit_status(db, company_id)
        outbound_limit_exhausted = not bool(limit_state.get("allowed", True))
        if outbound_limit_exhausted:
            logger.info(
                "Skipping customer-triggered AI workflow because conversation outbound limit is exhausted company_id=%s conversation_id=%s limit=%s used=%s",
                company_id,
                convo_id,
                limit_state.get("limit"),
                limit_state.get("used"),
            )
            sentiment_gate = {
                **sentiment_gate,
                "ai_response_allowed": False,
                "rate_limit_exhausted": True,
                "message": conversation_limit_completed_message(
                    int(limit_state.get("limit") or 0),
                    limit_state.get("used"),
                ),
            }
        else:
            try:
                cust_full = r(
                    await db.fetchrow(
                        "SELECT * FROM customers WHERE id=$1 LIMIT 1",
                        convo.get("customer_id", ""),
                    )
                )
                msgs_history = await fetch_messages_with_attachments(
                    db,
                    convo_id,
                    limit=500,
                    since_days=10,
                    company_id=company_id,
                    customer_id=str(convo.get("customer_id") or ""),
                    include_linked_profiles=True,
                )
                workflow = await orchestrate_message_workflow(
                    MessageWorkflowRequest(
                        trace_id=trace_id,
                        company_id=company_id,
                        conversation_id=convo_id,
                        customer_id=convo.get("customer_id", ""),
                        message_id=msg_id,
                        channel=str(convo.get("channel") or "web_chat"),
                        source=str(convo.get("channel") or "conversation"),
                        message_text=content,
                        sender_name=str(convo.get("customer_name") or ""),
                        actor_user_id=cu.get("sub", ""),
                        actor_user_role=cu.get("role", ""),
                        conversation_context=msgs_history,
                        customer=cust_full or {},
                        metadata={"source": "send_message", "trace_id": trace_id},
                    ),
                    authorization=request.headers.get("authorization") or request.headers.get("Authorization", ""),
                    db=db,
                )
                capture = workflow.agent_outputs.capture
                support_plan = workflow.agent_outputs.support
                sentiment = dict(capture.get("sentiment") or {})
                conversation_sentiment = dict(capture.get("conversation_sentiment") or {})
                intent = dict(capture.get("intent") or {})
                sentiment_gate = dict(capture.get("sentiment_gate") or {}) or build_sentiment_gate(content, sentiment)
                sent_score = _float_or_none(sentiment.get("score"))
                sent_emotion = str(sentiment.get("emotion", "neutral"))
                sent_conf = _float_or_none(sentiment.get("confidence"))
                intent_type = str(intent.get("intent", ""))
                intent_conf = _float_or_none(intent.get("confidence"))
                await db.execute(
                    "UPDATE messages SET sentiment_score=$1,sentiment_emotion=$2,sentiment_confidence=$3,"
                    "intent_type=$4,intent_confidence=$5 WHERE id=$6",
                    sent_score,
                    sent_emotion,
                    sent_conf,
                    intent_type,
                    intent_conf,
                    msg_id,
                )
                conversation_score = _float_or_none((conversation_sentiment or {}).get("score"))
                conversation_label = str(
                    (conversation_sentiment or {}).get("sentiment_label")
                    or (conversation_sentiment or {}).get("label")
                    or (conversation_sentiment or {}).get("emotion")
                    or sent_emotion
                    or "neutral"
                )
                await db.execute(
                    "UPDATE conversations SET sentiment_score=$1,sentiment_label=$2 WHERE id=$3",
                    conversation_score,
                    conversation_label,
                    convo_id,
                )
                if not sentiment_gate.get("ai_response_allowed", True):
                    for tag in ["toxic", "escalating"]:
                        await db.execute(
                            "INSERT INTO conversation_tags(conversation_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                            convo_id,
                            tag,
                        )
                    if dict(sentiment_gate.get("risk_flags") or {}).get("possible_hate_speech"):
                        await db.execute(
                            "INSERT INTO conversation_tags(conversation_id,tag) VALUES($1,'possible-hate-speech') ON CONFLICT DO NOTHING",  # noqa: E501
                            convo_id,
                        )
            except Exception as e:
                logger.error(f"Message orchestration failed: {e}")
    message = await _load_message_with_attachments(db, msg_id)
    await emit_new_message(convo_id, message)
    if sender_type in {"agent", "customer"}:
        try:
            await apply_message_stage_transition(
                db,
                company_id=company_id,
                customer_id=str(convo.get("customer_id") or ""),
                message_text=content,
                direction="outbound" if sender_type == "agent" else "inbound",
                source="message_sent" if sender_type == "agent" else "customer_reply",
                event_id=msg_id,
                changed_by_user_id=cu.get("sub", "") if sender_type == "agent" else "",
            )
        except Exception as exc:
            logger.warning(
                "conversation lead stage transition failed conversation_id=%s message_id=%s sender_type=%s: %s",
                convo_id,
                msg_id,
                sender_type,
                exc,
            )

    # Ingest into ETL pipeline (fire-and-forget)
    try:
        from data_pipeline.ingestion.raw_store import capture_raw_message

        create_safe_detached_task(
            db,
            capture_raw_message(
                db,
                conversation=dict(convo),
                message=dict(
                    message
                    or {
                        "id": msg_id,
                        "content": content,
                        "sender_type": sender_type,
                        "company_id": company_id,
                        "conversation_id": convo_id,
                    }
                ),
                source=str(convo.get("channel") or "web_chat"),
                metadata={"action": "message_sent", "trace_id": trace_id},
            ),
            name=f"etl-capture-message-{msg_id}",
            idempotency_key=f"etl:msg:{company_id}:{msg_id}",
            company_id=company_id,
            channel=str(convo.get("channel") or ""),
            trace_id=trace_id,
            event_id=msg_id,
            source_queue="etl",
        )
    except Exception as exc:
        logger.warning("ETL message capture dispatch failed msg_id=%s: %s", msg_id, exc)

    if sender_type == "agent" and convo.get("channel") in ("whatsapp", "facebook", "instagram", "email"):
        cust = (
            r(
                await db.fetchrow(
                    "SELECT id,phone,email FROM customers WHERE id=$1",
                    convo.get("customer_id", ""),
                )
            )
            or {}
        )
        outbound_channel = outbound_channel_name
        recipient_id = _resolve_conversation_recipient(outbound_channel, convo, cust)
        outbound_attachments = saved_attachments if outbound_channel == "whatsapp" else []
        outbound_subject = ""
        if outbound_channel == "email":
            outbound_subject = _reply_email_subject(str(convo.get("subject") or ""), company_sender_name.replace("Message from ", "", 1))
        sent, error = await _send_outbound_via_channel_layer(
            db=db,
            company_id=company_id,
            channel=outbound_channel,
            recipient_id=recipient_id,
            content=content,
            conversation_id=convo_id,
            attachments=outbound_attachments,
            db_message_id=msg_id,
            subject=outbound_subject,
            metadata={
                "source": "send_message",
                "actor_user_id": cu.get("sub", ""),
                "actor_user_role": cu.get("role", ""),
                "trace_id": trace_id,
                "customer_id": str(convo.get("customer_id") or ""),
                "raw_sender_id": str(convo.get("channel_id") or ""),
                "normalized_sender_id": recipient_id,
                "selected_outbound_recipient": recipient_id,
            },
        )
        outbound_delivered = bool(sent)
        outbound_error = str(error or "").strip()
        if not sent:
            logger.warning(
                "Outbound send failed for conversation %s channel=%s: %s",
                convo_id,
                outbound_channel,
                error,
            )
        message = await _load_message_with_attachments(db, msg_id)

    ai_response = None
    cust_full = None
    if (
        sender_type == "customer"
        and not outbound_limit_exhausted
        and convo.get("ai_handled", True)
        and not conversation_ai_auto_paused(convo)
        and await is_company_ai_enabled(db, company_id)
    ):
        try:
            result = dict(support_plan or {})
            cust_full = r(
                await db.fetchrow(
                    "SELECT * FROM customers WHERE id=$1 LIMIT 1",
                    convo.get("customer_id", ""),
                )
            )
            if not result:
                result = {
                    "response": "",
                    "confidence": 0.0,
                    "deliver_response": False,
                    "escalate": True,
                    "escalation_reason": "Orchestrator response was unavailable for this conversation.",
                    "next_action": "manual_review",
                    "api_error": True,
                }

            if (
                result.get("api_error")
                and not result.get("static_fallback_served")
                and (is_ai_api_exhaustion_payload(result) or not result.get("response"))
            ):
                sys_msg = AI_API_EXHAUSTED_MANUAL_MESSAGE
                await disable_company_ai_after_api_exhaustion(
                    db,
                    company_id,
                    conversation_id=convo_id,
                    reason=str(result.get("error_reason") or result.get("error_type") or "AI service unavailable"),
                    error_type=str(result.get("error_type") or ""),
                    provider=str(result.get("provider") or ""),
                    model=str(result.get("model_name") or ""),
                )
                escalation = await escalate_conversation_to_human(
                    db,
                    convo_id,
                    company_id,
                    (cust_full or {}).get("name", "Customer"),
                    convo.get("channel", "web_chat"),
                    reason=sys_msg,
                    automatic=True,
                )
                await emit_new_message(convo_id, escalation["message"])
                return {
                    "message": message,
                    "ai_response": None,
                    "sentiment_analysis": sentiment_gate,
                }

            if result.get("escalate"):
                escalation = await escalate_conversation_to_human(
                    db,
                    convo_id,
                    company_id,
                    (cust_full or {}).get("name", "Customer"),
                    convo.get("channel", "web_chat"),
                    reason=str(
                        result.get("escalation_reason")
                        or "AI responses paused after detecting a human handoff request or critical issue."
                    ),
                    automatic=True,
                )
                await emit_new_message(convo_id, escalation["message"])
                create_safe_detached_task(
                    db,
                    _notify_agents_handoff(
                        db,
                        None,
                        {},
                        company_id,
                        convo_id,
                        (cust_full or {}).get("name", "Customer"),
                        float(result.get("confidence", 0.0) or 0.0),
                    ),
                    name=f"notify-handoff-{convo_id}",
                    company_id=company_id,
                    channel=str(convo.get("channel") or ""),
                    trace_id=trace_id,
                    event_id=msg_id,
                )
                return {
                    "message": message,
                    "ai_response": None,
                    "sentiment_analysis": sentiment_gate,
                }

            if result.get("deliver_response") and result.get("response"):
                ai_started_at = time.monotonic()
                await wait_for_ai_response_timing(content, ai_started_at)
                ai_id = make_id()
                ai_reservation = await reserve_conversation_usage(
                    db,
                    company_id,
                    channel=str(convo.get("channel") or "web_chat"),
                    idempotency_key=f"api:{company_id}:ai:{ai_id}",
                )
                if ai_reservation == "denied":
                    limit_state = await conversation_limit_status(db, company_id)
                    logger.info(
                        "AI auto-response blocked by conversation limit company_id=%s conversation_id=%s limit=%s used=%s",
                        company_id,
                        convo_id,
                        limit_state.get("limit"),
                        limit_state.get("used"),
                    )
                    return {
                        "message": message,
                        "ai_response": None,
                        "sentiment_analysis": {
                            **sentiment_gate,
                            "ai_response_allowed": False,
                            "rate_limit_exhausted": True,
                            "message": conversation_limit_completed_message(
                                int(limit_state.get("limit") or 0),
                                limit_state.get("used"),
                            ),
                        },
                    }
                outbound_channel = str(convo.get("channel") or "")
                ai_sender_name = company_sender_name if outbound_channel == "email" else "AI Assistant"
                ai_delivery_status = "sending" if outbound_channel in ("whatsapp", "facebook", "instagram", "email") else "pending"
                await db.execute(
                    "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,"
                    "ai_confidence,delivery_status,read,created_at) "
                    "VALUES($1,$2,$3,$4,'ai','ai-assistant',$5,$6,$7,FALSE,NOW())",
                    ai_id,
                    company_id,
                    convo_id,
                    result["response"],
                    ai_sender_name,
                    float(result.get("confidence", 0.0) or 0.0),
                    ai_delivery_status,
                )
                ai_attachments = await save_message_attachments(
                    db,
                    ai_id,
                    result.get("attachments") or result.get("product_images") or [],
                )
                await persist_chat_history(
                    db,
                    convo,
                    {
                        "id": ai_id,
                        "conversation_id": convo_id,
                        "content": result["response"],
                        "sender_type": "ai",
                        "sender_name": ai_sender_name,
                        "attachments": ai_attachments,
                        "created_at": now_ts(),
                    },
                )
                await persist_user_ai_memory(db, cu, result["response"], "ai")
                create_safe_detached_task(
                    db,
                    persist_ai_session_record(
                        db,
                        company_id,
                        convo_id,
                        content,
                        result["response"],
                        {
                            "confidence": float(result.get("confidence", 0.0) or 0.0),
                            "source": "send_message",
                            "llm_id": result.get("llm_id", ""),
                            "agent_id": result.get("agent_id", ""),
                            "intent_name": result.get("intent_name", ""),
                            "channel": convo.get("channel", ""),
                        },
                    ),
                    name=f"persist-ai-session-{convo_id}",
                    company_id=company_id,
                    channel=str(convo.get("channel") or ""),
                    trace_id=trace_id,
                    event_id=ai_id,
                )
                await db.execute(
                    "UPDATE conversations SET last_message=$1,last_message_at=NOW(),message_count=message_count+1,ai_handled=TRUE "  # noqa: E501
                    "WHERE id=$2",
                    _message_preview(result["response"], ai_attachments, "ai"),
                    convo_id,
                )
                ai_response = await _load_message_with_attachments(db, ai_id)
                await emit_new_message(convo_id, ai_response)
                if outbound_channel in ("whatsapp", "facebook", "instagram", "email"):
                    recipient_id = _resolve_conversation_recipient(
                        outbound_channel,
                        convo,
                        cust_full or {},
                    )
                    if recipient_id:
                        outbound_attachments = ai_attachments if outbound_channel == "whatsapp" else []
                        ai_subject = ""
                        if outbound_channel == "email":
                            ai_subject = _reply_email_subject(str(convo.get("subject") or ""), company_sender_name.replace("Message from ", "", 1))
                        sent, error = await _send_outbound_via_channel_layer(
                            db=db,
                            company_id=company_id,
                            channel=outbound_channel,
                            recipient_id=recipient_id,
                            content=result["response"],
                            conversation_id=convo_id,
                            attachments=outbound_attachments,
                            db_message_id=ai_id,
                            subject=ai_subject,
                            metadata={
                                "source": "send_message_ai",
                                "actor_user_id": cu.get("sub", ""),
                                "actor_user_role": cu.get("role", ""),
                                "trace_id": trace_id,
                                "customer_id": str(convo.get("customer_id") or ""),
                                "raw_sender_id": str(convo.get("channel_id") or ""),
                                "normalized_sender_id": recipient_id,
                                "selected_outbound_recipient": recipient_id,
                            },
                        )
                        if not sent:
                            logger.warning(
                                "AI outbound send failed for conversation %s channel=%s: %s",
                                convo_id,
                                outbound_channel,
                                error,
                            )
                    else:
                        await _persist_outbound_message_state(
                            db,
                            company_id=company_id,
                            db_message_id=ai_id,
                            delivery_status="failed",
                        )
                lead_for_qualification = dict(capture.get("lead") or {})
                if not lead_for_qualification and cust_full:
                    lead_for_qualification = {"id": str(cust_full.get("lead_id") or "")}
                create_safe_detached_task(
                    db,
                    _update_qualification_silently(
                        db,
                        company_id,
                        lead_for_qualification,
                        content,
                        intent,
                    ),
                    name=f"qualification-update-{convo_id}",
                    company_id=company_id,
                    channel=str(convo.get("channel") or ""),
                    trace_id=trace_id,
                    event_id=ai_id,
                )
            else:
                escalation = await escalate_conversation_to_human(
                    db,
                    convo_id,
                    company_id,
                    (cust_full or {}).get("name", "Customer"),
                    convo.get("channel", "web_chat"),
                    reason=str(
                        result.get("escalation_reason")
                        or f"AI confidence was too low ({float(result.get('confidence', 0.0) or 0.0):.0%}). Human review is required."  # noqa: E501
                    ),
                    automatic=True,
                )
                await emit_new_message(convo_id, escalation["message"])
                create_safe_detached_task(
                    db,
                    _notify_agents_handoff(
                        db,
                        None,
                        {},
                        company_id,
                        convo_id,
                        (cust_full or {}).get("name", "Customer"),
                        float(result.get("confidence", 0.0) or 0.0),
                    ),
                    name=f"notify-handoff-{convo_id}",
                    company_id=company_id,
                    channel=str(convo.get("channel") or ""),
                    trace_id=trace_id,
                    event_id=msg_id,
                )
        except Exception as e:
            logger.error(f"AI response failed: {e}")
    return {
        "message": message,
        "ai_response": ai_response,
        "sentiment_analysis": sentiment_gate if sender_type == "customer" else None,
        "outbound_delivered": outbound_delivered if sender_type == "agent" else None,
        "outbound_error": outbound_error if sender_type == "agent" and outbound_error else "",
    }


@router.put("/conversations/{convo_id}/messages/{message_id}")
async def edit_message(convo_id: str, message_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    convo = r(
        await db.fetchrow(
            "SELECT id FROM conversations WHERE id=$1 AND company_id=$2 LIMIT 1",
            convo_id,
            cid,
        )
    )
    if not convo:
        raise HTTPException(404, "Conversation not found")
    body = await request.json()
    content = (body.get("content", "") or "").strip()
    if not content:
        raise HTTPException(400, "Message content cannot be empty")
    msg = r(
        await db.fetchrow(
            "SELECT * FROM messages WHERE id=$1 AND conversation_id=$2 LIMIT 1",
            message_id,
            convo_id,
        )
    )
    if not msg:
        raise HTTPException(404, "Message not found")
    if msg.get("sender_type") == "system":
        raise HTTPException(400, "System messages cannot be edited")
    await db.execute(
        "UPDATE messages SET content=$1,edited_at=NOW() WHERE id=$2",
        content,
        message_id,
    )
    updated = r(await db.fetchrow("SELECT * FROM messages WHERE id=$1", message_id))
    await refresh_conversation_rollup(db, convo_id)
    await emit_message_updated(convo_id, updated)
    return {"message": updated}


@router.delete("/conversations/{convo_id}/messages/{message_id}")
async def delete_message(convo_id: str, message_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    convo = r(
        await db.fetchrow(
            "SELECT id FROM conversations WHERE id=$1 AND company_id=$2 LIMIT 1",
            convo_id,
            cid,
        )
    )
    if not convo:
        raise HTTPException(404, "Conversation not found")
    msg = r(
        await db.fetchrow(
            "SELECT * FROM messages WHERE id=$1 AND conversation_id=$2 LIMIT 1",
            message_id,
            convo_id,
        )
    )
    if not msg:
        raise HTTPException(404, "Message not found")
    if msg.get("sender_type") == "system":
        raise HTTPException(400, "System messages cannot be deleted")
    await db.execute("DELETE FROM messages WHERE id=$1", message_id)
    await refresh_conversation_rollup(db, convo_id)
    await emit_message_deleted(convo_id, message_id)
    return {"status": "deleted", "message_id": message_id}


async def _persist_manual_ai_system_alert(
    db,
    *,
    company_id: str,
    convo_id: str,
    message: str,
) -> dict:
    if not company_id or not convo_id:
        return {}
    sys_id = make_id()
    text = str(message or "AI response withheld for manual review.").strip()
    await db.execute(
        "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,is_alert,read,created_at) "
        "VALUES($1,$2,$3,$4,'system','system','System',TRUE,FALSE,NOW())",
        sys_id,
        company_id,
        convo_id,
        text,
    )
    await db.execute(
        "UPDATE conversations SET ai_handled=FALSE,status='open',escalation_notice=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
        text,
        convo_id,
        company_id,
    )
    sys_message = r(await db.fetchrow("SELECT * FROM messages WHERE id=$1", sys_id))
    await emit_new_message(convo_id, sys_message)
    return sys_message


def _manual_ai_safe_fallback_draft() -> str:
    return "Hi, thanks for reaching out. Let me check this and get back to you shortly."


def _build_manual_ai_draft_payload(result: dict, convo_id: str, threshold: float = 0.7) -> dict:
    payload = dict(result or {})
    confidence = float(payload.get("confidence", 0.0) or 0.0)
    confidence_threshold = float(payload.get("confidence_threshold", threshold) or threshold)
    review_reason = str(
        payload.get("review_reason")
        or payload.get("escalation_reason")
        or payload.get("error_reason")
        or ""
    ).strip()
    fallback_reason = str(payload.get("fallback_reason") or "").strip()
    if not fallback_reason and payload.get("fallback_used"):
        fallback_reason = str(payload.get("error_type") or "").strip()
    draft = str(payload.get("response") or payload.get("draft") or "").strip()
    if not draft:
        draft = _manual_ai_safe_fallback_draft()
        fallback_reason = fallback_reason or "empty_model_response"
        review_reason = review_reason or "AI returned an empty response. A safe editable fallback draft was provided."
        payload["fallback_used"] = True

    requires_review = bool(
        payload.get("requires_review")
        or payload.get("escalate")
        or confidence < confidence_threshold
        or payload.get("api_error")
        or payload.get("fallback_used")
    )
    if requires_review and not review_reason and confidence < confidence_threshold:
        review_reason = "Low confidence; review before sending."

    attachments = list(payload.get("attachments") or payload.get("product_images") or [])
    return {
        "status": "draft_ready",
        "conversation_id": convo_id,
        "draft": draft,
        "response": draft,
        "confidence": confidence,
        "confidence_threshold": confidence_threshold,
        "requires_review": requires_review,
        "reason": review_reason,
        "review_reason": review_reason,
        "attachments": attachments,
        "product_images": list(payload.get("product_images") or []),
        "provider": str(payload.get("provider") or ""),
        "model_name": str(payload.get("model_name") or ""),
        "llm_id": str(payload.get("llm_id") or ""),
        "api_error": bool(payload.get("api_error")),
        "error_type": str(payload.get("error_type") or ""),
        "error_reason": str(payload.get("error_reason") or ""),
        "provider_error": dict(payload.get("provider_error") or {}),
        "fallback_used": bool(payload.get("fallback_used")),
        "fallback_reason": fallback_reason,
        "static_fallback_served": bool(payload.get("static_fallback_served")),
        "manual_draft_nonce": str(payload.get("manual_draft_nonce") or ""),
    }


async def _process_manual_ai_response_background(
    db,
    *,
    convo_id: str,
    current_user: dict,
    authorization: str = "",
    trace_id: str = "",
) -> None:
    cu = dict(current_user or {})
    cid = get_company_id(cu)
    company_id = cid
    try:
        convo = r(
            await db.fetchrow(
                "SELECT * FROM conversations WHERE id=$1 AND company_id=$2 LIMIT 1",
                convo_id,
                cid,
            )
        )
        if not convo:
            logger.warning("Queued manual AI response skipped; conversation not found conversation_id=%s", convo_id)
            return
        company_id = cu.get("company_id", "") or convo.get("company_id", "")
        await _run_manual_ai_response_workflow(
            db,
            convo_id=convo_id,
            current_user=cu,
            authorization=authorization,
            trace_id=trace_id,
            convo=convo,
            draft_only=True,
        )
    except HTTPException as exc:
        logger.warning(
            "Queued manual AI response withheld company_id=%s conversation_id=%s status_code=%s detail=%s",
            company_id,
            convo_id,
            exc.status_code,
            exc.detail,
        )
        if company_id and exc.status_code != 404:
            await _persist_manual_ai_system_alert(
                db,
                company_id=company_id,
                convo_id=convo_id,
                message=str(exc.detail or "AI response withheld for manual review."),
            )
    except Exception as exc:
        logger.exception("Queued manual AI response failed company_id=%s conversation_id=%s error=%s", company_id, convo_id, exc)
        if company_id:
            await disable_company_ai_after_api_exhaustion(
                db,
                company_id,
                conversation_id=convo_id,
                reason=str(exc) or "AI response failed",
                error_type=type(exc).__name__,
            )
            await _persist_manual_ai_system_alert(
                db,
                company_id=company_id,
                convo_id=convo_id,
                message="AI response failed. Please respond manually.",
            )


async def _run_manual_ai_response_workflow(
    db,
    *,
    convo_id: str,
    current_user: dict,
    authorization: str,
    trace_id: str,
    convo: dict,
    draft_only: bool = False,
) -> dict:
    cu = dict(current_user or {})
    company_id = cu.get("company_id", "") or convo.get("company_id", "")
    if conversation_ai_auto_paused(convo):
        if draft_only:
            return _build_manual_ai_draft_payload(
                {
                    "response": _manual_ai_safe_fallback_draft(),
                    "confidence": 0.0,
                    "confidence_threshold": 0.7,
                    "requires_review": True,
                    "review_reason": convo.get("ai_paused_reason") or AI_API_EXHAUSTED_MANUAL_MESSAGE,
                    "api_error": True,
                    "error_reason": convo.get("ai_paused_reason") or AI_API_EXHAUSTED_MANUAL_MESSAGE,
                    "fallback_used": True,
                },
                convo_id,
                0.7,
            )
        raise HTTPException(409, convo.get("ai_paused_reason") or AI_API_EXHAUSTED_MANUAL_MESSAGE)
    trace_id = trace_id or _trace_id_from_context()
    msgs_history = await fetch_messages_with_attachments(
        db,
        convo_id,
        limit=500,
        since_days=10,
        company_id=company_id,
        customer_id=str(convo.get("customer_id") or ""),
        include_linked_profiles=True,
    )
    cust = r(await db.fetchrow("SELECT * FROM customers WHERE id=$1 LIMIT 1", convo.get("customer_id", "")))
    last_cust_msg_record = next(
        (dict(m or {}) for m in reversed(msgs_history) if (m or {}).get("sender_type") == "customer"),
        {},
    )
    last_cust_msg = str(last_cust_msg_record.get("content") or "").strip() or "(manual trigger)"
    last_cust_msg_id = str(last_cust_msg_record.get("id") or "").strip()
    sender_contact = _resolve_conversation_recipient(str(convo.get("channel") or "web_chat"), convo, cust or {})
    manual_draft_nonce = make_id()
    manual_idempotency_key = (
        f"manual_ai_draft:{company_id}:{convo_id}:{trace_id or manual_draft_nonce}:{manual_draft_nonce}"
    )
    request_metadata = {
        "source": "manual_ai_respond",
        "manual_draft": True,
        "manual_draft_nonce": manual_draft_nonce,
        "trace_id": trace_id,
        "message_id": manual_idempotency_key,
        "external_message_id": "",
        "provider_event_id": manual_idempotency_key,
        "idempotency_key": manual_idempotency_key,
        "raw_sender_id": str(convo.get("channel_id") or sender_contact or ""),
        "normalized_sender_id": sender_contact,
        "selected_outbound_recipient": sender_contact,
        "customer_id": str(convo.get("customer_id") or ""),
    }
    logger.info(
        "manual_ai_draft_request company_id=%s conversation_id=%s manual_draft_nonce=%s message_id=%s last_customer_message_id=%s",
        company_id,
        convo_id,
        manual_draft_nonce,
        manual_idempotency_key,
        last_cust_msg_id,
    )
    logger.info(
        "Manual AI response workflow request trace_id=%s company_id=%s conversation_id=%s customer_id=%s "
        "channel=%s message_id=%s raw_identity=%s normalized_identity=%s selected_outbound_recipient=%s",
        trace_id,
        company_id,
        convo_id,
        str(convo.get("customer_id") or ""),
        str(convo.get("channel") or "web_chat"),
        manual_idempotency_key,
        request_metadata["raw_sender_id"],
        sender_contact,
        sender_contact,
    )
    workflow = await orchestrate_message_workflow(
        MessageWorkflowRequest(
            trace_id=trace_id,
            company_id=company_id,
            conversation_id=convo_id,
            customer_id=convo.get("customer_id", ""),
            message_id=manual_idempotency_key,
            external_message_id="",
            provider_event_id=manual_idempotency_key,
            idempotency_key=manual_idempotency_key,
            channel=str(convo.get("channel") or "web_chat"),
            source="manual_ai_respond",
            message_text=last_cust_msg,
            sender_name=str((cust or {}).get("name") or convo.get("customer_name") or ""),
            sender_contact=sender_contact,
            actor_user_id=cu.get("sub", ""),
            actor_user_role=cu.get("role", ""),
            conversation_context=msgs_history,
            customer=cust or {},
            metadata=request_metadata,
        ),
        authorization=authorization,
        db=db,
    )
    result = dict(workflow.agent_outputs.support or {})
    result["manual_draft_nonce"] = manual_draft_nonce
    if (
        result.get("api_error")
        and not result.get("static_fallback_served")
        and (is_ai_api_exhaustion_payload(result) or not result.get("response"))
    ):
        sys_msg = AI_API_EXHAUSTED_MANUAL_MESSAGE
        await disable_company_ai_after_api_exhaustion(
            db,
            company_id,
            conversation_id=convo_id,
            reason=str(result.get("error_reason") or result.get("error_type") or "AI service unavailable"),
            error_type=str(result.get("error_type") or ""),
            provider=str(result.get("provider") or ""),
            model=str(result.get("model_name") or ""),
        )
        if draft_only:
            result["response"] = str(result.get("response") or _manual_ai_safe_fallback_draft())
            result["requires_review"] = True
            result["review_reason"] = sys_msg
            result["fallback_used"] = True
            draft_payload = _build_manual_ai_draft_payload(
                result,
                convo_id,
                float(result.get("confidence_threshold", 0.7) or 0.7),
            )
            logger.warning(
                "manual_ai_draft_generated company_id=%s conversation_id=%s confidence=%.2f requires_review=%s provider=%s model=%s api_error=%s",
                company_id,
                convo_id,
                float(draft_payload.get("confidence", 0.0) or 0.0),
                bool(draft_payload.get("requires_review")),
                draft_payload.get("provider", ""),
                draft_payload.get("model_name", ""),
                True,
            )
            return draft_payload
        sys_id = make_id()
        await db.execute(
            "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,is_alert,read,created_at) "
            "VALUES($1,$2,$3,$4,'system','system','System',TRUE,FALSE,NOW())",
            sys_id,
            company_id,
            convo_id,
            sys_msg,
        )
        await db.execute(
            "UPDATE conversations SET ai_handled=FALSE,status='open',escalation_notice=$1,updated_at=NOW() WHERE id=$2 AND company_id=$3",
            sys_msg,
            convo_id,
            company_id,
        )
        sys_message = r(await db.fetchrow("SELECT * FROM messages WHERE id=$1", sys_id))
        await emit_new_message(convo_id, sys_message)
        return sys_message
    if draft_only and result.get("static_fallback_served") and str(result.get("provider") or "") == "static_fallback":
        await disable_company_ai_after_api_exhaustion(
            db,
            company_id,
            conversation_id=convo_id,
            reason=str(result.get("review_reason") or result.get("error_reason") or AI_API_EXHAUSTED_MANUAL_MESSAGE),
            error_type=str(result.get("error_type") or "static_fallback"),
            provider=str(result.get("provider") or ""),
            model=str(result.get("model_name") or ""),
        )
    if draft_only:
        draft_payload = _build_manual_ai_draft_payload(
            result,
            convo_id,
            float(result.get("confidence_threshold", 0.7) or 0.7),
        )
        logger.info(
            "manual_ai_draft_generated company_id=%s conversation_id=%s confidence=%.2f requires_review=%s provider=%s model=%s",
            company_id,
            convo_id,
            float(draft_payload.get("confidence", 0.0) or 0.0),
            bool(draft_payload.get("requires_review")),
            draft_payload.get("provider", ""),
            draft_payload.get("model_name", ""),
        )
        return draft_payload
    # For manual AI responses, do not block on low confidence or escalations.
    # If the AI returned a response, allow it to be delivered for human review/editing
    # even when confidence is below the threshold. Only block if the response is empty.
    if not result.get("response"):
        raise HTTPException(409, "AI response withheld for manual review.")
    ai_id = make_id()
    outbound_channel = str(convo.get("channel") or "")
    await _reserve_outbound_or_raise(
        db,
        company_id,
        channel=outbound_channel or "web_chat",
        idempotency_key=f"api:{company_id}:manual-ai:{ai_id}",
    )
    company_sender_name = await _company_email_sender_name(db, company_id) if outbound_channel == "email" else ""
    ai_sender_name = company_sender_name if outbound_channel == "email" else "AI Assistant"
    ai_delivery_status = "sending" if outbound_channel in ("whatsapp", "facebook", "instagram", "email") else "pending"
    await db.execute(
        "INSERT INTO messages(id,company_id,conversation_id,content,sender_type,sender_id,sender_name,"
        "ai_confidence,delivery_status,read,created_at) "
        "VALUES($1,$2,$3,$4,'ai','ai-assistant',$5,$6,$7,FALSE,NOW())",
        ai_id,
        company_id,
        convo_id,
        result["response"],
        ai_sender_name,
        float(result.get("confidence", 0.0) or 0.0),
        ai_delivery_status,
    )
    ai_attachments = await save_message_attachments(
        db,
        ai_id,
        result.get("attachments") or result.get("product_images") or [],
    )
    await persist_chat_history(
        db,
        convo,
        {
            "id": ai_id,
            "conversation_id": convo_id,
            "content": result["response"],
            "sender_type": "ai",
            "sender_name": ai_sender_name,
            "attachments": ai_attachments,
            "created_at": now_ts(),
        },
    )
    create_safe_detached_task(
        db,
        persist_ai_session_record(
            db,
            company_id,
            convo_id,
            last_cust_msg,
            result["response"],
            {
                "confidence": float(result.get("confidence", 0.0) or 0.0),
                "source": "ai_respond",
                "llm_id": result.get("llm_id", ""),
                "agent_id": result.get("agent_id", ""),
                "intent_name": result.get("intent_name", ""),
                "channel": convo.get("channel", ""),
            },
        ),
        name=f"persist-ai-session-{convo_id}",
        company_id=company_id,
        channel=str(convo.get("channel") or ""),
        trace_id=trace_id,
        event_id=ai_id,
    )
    await db.execute(
        "UPDATE conversations SET last_message=$1,last_message_at=NOW(),updated_at=NOW(),message_count=message_count+1 WHERE id=$2",  # noqa: E501
        _message_preview(result["response"], ai_attachments, "ai"),
        convo_id,
    )
    ai_msg = await _load_message_with_attachments(db, ai_id)
    await emit_new_message(convo_id, ai_msg)
    if outbound_channel in ("whatsapp", "facebook", "instagram", "email"):
        recipient_id = _resolve_conversation_recipient(outbound_channel, convo, cust or {})
        if recipient_id:
            outbound_attachments = ai_attachments if outbound_channel == "whatsapp" else []
            ai_subject = ""
            if outbound_channel == "email":
                ai_subject = _reply_email_subject(str(convo.get("subject") or ""), company_sender_name.replace("Message from ", "", 1))
            sent, error = await _send_outbound_via_channel_layer(
                db=db,
                company_id=company_id,
                channel=outbound_channel,
                recipient_id=recipient_id,
                content=result["response"],
                conversation_id=convo_id,
                attachments=outbound_attachments,
                db_message_id=ai_id,
                subject=ai_subject,
                metadata={
                    "source": "manual_ai_respond",
                    "actor_user_id": cu.get("sub", ""),
                    "actor_user_role": cu.get("role", ""),
                    "trace_id": trace_id,
                    "customer_id": str(convo.get("customer_id") or ""),
                    "raw_sender_id": str(convo.get("channel_id") or ""),
                    "normalized_sender_id": recipient_id,
                    "selected_outbound_recipient": recipient_id,
                },
            )
            if not sent:
                logger.warning(
                    "Manual AI outbound send failed for conversation %s channel=%s: %s",
                    convo_id,
                    outbound_channel,
                    error,
                )
        else:
            await _persist_outbound_message_state(
                db,
                company_id=company_id,
                db_message_id=ai_id,
                delivery_status="failed",
            )
    return ai_msg


@router.post("/conversations/{convo_id}/ai-respond")
async def trigger_ai_response(convo_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    convo = r(
        await db.fetchrow(
            "SELECT * FROM conversations WHERE id=$1 AND company_id=$2 LIMIT 1",
            convo_id,
            cid,
        )
    )
    if not convo:
        raise HTTPException(404, "Conversation not found")
    trace_id = _trace_id_from_context()
    return await _run_manual_ai_response_workflow(
        db,
        convo_id=convo_id,
        current_user=dict(cu or {}),
        authorization=request.headers.get("authorization") or request.headers.get("Authorization", ""),
        trace_id=trace_id,
        convo=convo,
        draft_only=True,
    )


@router.post("/conversations/{convo_id}/summarize")
async def get_conversation_summary(convo_id: str, request: Request):
    db = _db(request)
    await get_current_user_flexible(request)
    messages = rs(
        await db.fetch(
            "SELECT * FROM messages WHERE conversation_id=$1 ORDER BY created_at LIMIT 50",
            convo_id,
        )
    )
    return {"summary": await summarize_conversation(messages)}


@router.get("/conversations/{convo_id}/logs")
async def list_conversation_logs(convo_id: str, request: Request):
    db = _db(request)
    await get_current_user_flexible(request)
    return rs(
        await db.fetch(
            "SELECT * FROM conversation_logs WHERE convo_id=$1 ORDER BY logged_at DESC LIMIT 200",
            convo_id,
        )
    )


@router.get("/messages/{message_id}/attachments")
async def list_message_attachments(message_id: str, request: Request):
    db = _db(request)
    await get_current_user_flexible(request)
    rows = rs(await db.fetch("SELECT * FROM message_attachments WHERE message_id=$1", message_id))
    return [normalize_attachment_row(row) for row in rows]


@router.post("/communications/email/send")
async def send_email_message(request: Request):
    from services.email_service import send_tenant_email_async

    db = _db(request)
    current_user = await get_current_user_flexible(request)
    body = await request.json()
    to_email = (body.get("to_email", "") or "").strip()
    subject = (body.get("subject", "") or "").strip()
    content = (body.get("body", "") or "").strip()
    html_body = (body.get("html_body", "") or "").strip()
    if not to_email:
        raise HTTPException(400, "Recipient email is required")
    if not subject:
        raise HTTPException(400, "Email subject is required")
    if not content:
        raise HTTPException(400, "Email body is required")
    company_id = get_company_id(current_user)
    await send_tenant_email_async(
        db,
        company_id,
        to_email=to_email,
        subject=subject,
        body=content,
        html_body=html_body,
        raise_on_failure=True,
    )
    return {"status": "sent", "to_email": to_email}
