import logging
from datetime import datetime
from decimal import Decimal
from urllib.parse import parse_qs
from typing import Any

import socketio

from shared.auth.jwt import decode_token, is_valid_company_id
from shared.config import (
    gateway_allowed_origins,
    is_origin_allowed,
    rate_limit_redis_url,
    socket_rate_limit_requests,
    socket_rate_limit_window_seconds,
)
from shared.utils.rate_limit import build_rate_limiter

logger = logging.getLogger(__name__)

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=gateway_allowed_origins(),
    logger=False,
    engineio_logger=False,
)

connected_users: dict[str, str] = {}
socket_sessions: dict[str, dict] = {}
socket_db: Any | None = None
conversation_company_cache: dict[str, str] = {}
socket_connection_limiter = build_rate_limiter(
    max_requests=socket_rate_limit_requests(),
    window_seconds=socket_rate_limit_window_seconds(),
    redis_url=rate_limit_redis_url(),
    key_prefix="pulse:socket:connect",
)


def set_socket_db(db) -> None:
    global socket_db
    socket_db = db


async def close_socket_resources() -> None:
    try:
        await socket_connection_limiter.close()
    except Exception:
        logger.exception("Socket limiter shutdown failed")


async def _resolve_conversation_company_id(conversation_id: str) -> str:
    conversation_id = str(conversation_id or "").strip()
    if not conversation_id:
        return ""
    cached = conversation_company_cache.get(conversation_id, "")
    if cached:
        return cached
    if socket_db is None:
        return ""
    try:
        row = await socket_db.fetchrow(
            "SELECT company_id FROM conversations WHERE id=$1 LIMIT 1",
            conversation_id,
        )
    except Exception:
        return ""
    company_id = str((dict(row) if row else {}).get("company_id") or "").strip()
    if company_id:
        conversation_company_cache[conversation_id] = company_id
    return company_id


def _json_safe(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _extract_socket_token(environ: dict, auth) -> str:
    def normalize_token(value) -> str:
        candidate = str(value or "").strip()
        if candidate.lower().startswith("bearer "):
            return candidate.split(" ", 1)[1].strip()
        return candidate

    if isinstance(auth, dict):
        token = normalize_token(auth.get("token"))
        if token:
            return token
    auth_header = str(environ.get("HTTP_AUTHORIZATION") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    query_string = str(environ.get("QUERY_STRING") or "")
    query_params = parse_qs(query_string, keep_blank_values=True)
    return normalize_token((query_params.get("token") or [""])[0])


def _anonymous_session() -> dict:
    return {"sub": "", "company_id": "", "role": "", "anonymous": True}


@sio.event
async def connect(sid, environ, auth):
    client_ip = str(environ.get("REMOTE_ADDR") or "unknown").strip()
    allowed, _ = await socket_connection_limiter.allow(f"socket:{client_ip}")
    if not allowed:
        logger.warning("Socket rate limit hit ip=%s", client_ip)
        return False

    origin = str(environ.get("HTTP_ORIGIN") or "").strip()
    if origin and not is_origin_allowed(origin):
        logger.warning("Socket rejected due to origin policy sid=%s", sid)
        return False

    token = _extract_socket_token(environ, auth)
    if not token:
        socket_sessions[sid] = _anonymous_session()
        logger.info("Socket connected sid=%s anonymous=true", sid)
        return True

    payload = decode_token(token)
    company_id = str((payload or {}).get("company_id") or "").strip()
    if (
        not payload
        or not payload.get("sub")
        or not payload.get("role")
        or (payload.get("role") != "super_admin" and not is_valid_company_id(company_id))
    ):
        logger.warning("Socket rejected due to invalid auth sid=%s", sid)
        return False

    payload["anonymous"] = False
    socket_sessions[sid] = payload
    connected_users[payload["sub"]] = sid
    if company_id and payload.get("role") != "super_admin":
        await sio.enter_room(sid, f"company_{company_id}")
    logger.info("Socket connected sid=%s user_id=%s", sid, payload["sub"])
    return True


@sio.event
async def disconnect(sid):
    payload = socket_sessions.pop(sid, {})
    user_id = str(payload.get("sub") or "").strip()
    if user_id and connected_users.get(user_id) == sid:
        del connected_users[user_id]
    logger.info("Socket disconnected sid=%s", sid)


@sio.event
async def join(sid, data):
    payload = socket_sessions.get(sid)
    if not payload:
        await sio.disconnect(sid)
        return
    if payload.get("anonymous"):
        return
    company_id = str(payload.get("company_id") or "").strip()
    if company_id and payload.get("role") != "super_admin":
        await sio.enter_room(sid, f"company_{company_id}")
    connected_users[payload["sub"]] = sid


@sio.event
async def join_conversation(sid, data):
    payload = socket_sessions.get(sid)
    if not payload:
        await sio.disconnect(sid)
        return
    if payload.get("anonymous"):
        return
    convo_id = str((data or {}).get("conversation_id") or "").strip()
    if convo_id:
        company_id = str(payload.get("company_id") or "").strip()
        convo_company_id = await _resolve_conversation_company_id(convo_id)
        if payload.get("role") != "super_admin" and (not convo_company_id or convo_company_id != company_id):
            logger.warning(
                "Socket rejected conversation join sid=%s user_id=%s conversation_id=%s",
                sid,
                payload.get("sub", ""),
                convo_id,
            )
            await sio.disconnect(sid)
            return
        await sio.enter_room(sid, f"convo_{convo_id}")
        logger.info(
            "Socket joined conversation sid=%s user_id=%s company_id=%s conversation_id=%s socket_room=%s",
            sid,
            payload.get("sub", ""),
            company_id,
            convo_id,
            f"convo_{convo_id}",
        )


@sio.event
async def leave_conversation(sid, data):
    payload = socket_sessions.get(sid)
    if not payload or payload.get("anonymous"):
        return
    convo_id = str((data or {}).get("conversation_id") or "").strip()
    if convo_id:
        await sio.leave_room(sid, f"convo_{convo_id}")


async def emit_new_message(conversation_id: str, message: dict):
    try:
        company_id = await _resolve_conversation_company_id(conversation_id)
        message_id = str((message or {}).get("id") or "").strip()
        logger.info(
            "Socket emit new_message conversation_id=%s company_id=%s message_id=%s socket_room=%s emitted_event=new_message frontend_expected_event=new_message",
            conversation_id,
            company_id,
            message_id,
            f"convo_{conversation_id}",
        )
        await sio.emit(
            "new_message",
            _json_safe({"conversation_id": conversation_id, "message": message}),
            room=f"convo_{conversation_id}",
        )
        if company_id:
            logger.info(
                "Socket emit conversation_updated conversation_id=%s company_id=%s message_id=%s socket_room=%s emitted_event=conversation_updated frontend_expected_event=conversation_updated",
                conversation_id,
                company_id,
                message_id,
                f"company_{company_id}",
            )
            await sio.emit(
                "conversation_updated",
                {"conversation_id": conversation_id},
                room=f"company_{company_id}",
            )
    except Exception as e:
        logger.error("Socket emit error: %s", e)


async def emit_message_updated(conversation_id: str, message: dict):
    try:
        company_id = await _resolve_conversation_company_id(conversation_id)
        await sio.emit(
            "message_updated",
            _json_safe({"conversation_id": conversation_id, "message": message}),
            room=f"convo_{conversation_id}",
        )
        if company_id:
            await sio.emit(
                "conversation_updated",
                {"conversation_id": conversation_id},
                room=f"company_{company_id}",
            )
    except Exception as e:
        logger.error("Socket emit error: %s", e)


async def emit_message_deleted(conversation_id: str, message_id: str):
    try:
        company_id = await _resolve_conversation_company_id(conversation_id)
        await sio.emit(
            "message_deleted",
            {"conversation_id": conversation_id, "message_id": message_id},
            room=f"convo_{conversation_id}",
        )
        if company_id:
            await sio.emit(
                "conversation_updated",
                {"conversation_id": conversation_id},
                room=f"company_{company_id}",
            )
    except Exception as e:
        logger.error("Socket emit error: %s", e)


async def emit_message_reaction_updated(conversation_id: str, reaction: dict):
    try:
        company_id = await _resolve_conversation_company_id(conversation_id)
        await sio.emit(
            "message_reaction_updated",
            _json_safe({"conversation_id": conversation_id, "reaction": reaction}),
            room=f"convo_{conversation_id}",
        )
        if company_id:
            await sio.emit(
                "conversation_updated",
                {"conversation_id": conversation_id},
                room=f"company_{company_id}",
            )
    except Exception as e:
        logger.error("Socket emit error: %s", e)
