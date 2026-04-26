from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from core.socket import sio, socket_sessions
from shared.auth.dependencies import get_current_user
from shared.config import service_urls
from shared.schemas.contracts import RespondRequest
from shared.service_client import ServiceClient, build_internal_headers

router = APIRouter()
ai_client = ServiceClient(service_urls().ai)


class IdentitySocketEventRequest(BaseModel):
    event_name: Literal["identity_merged", "identity_split", "identity_resolved"]
    tenant_id: str
    aggregate_customer_id: str | None = None
    event_type: str = ""
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


@router.get("/health/socket")
async def socket_health():
    initialized = bool(sio and getattr(sio, "eio", None))
    return {
        "ok": initialized,
        "socket_io_initialized": initialized,
        "accepting_connections": initialized,
        "async_mode": getattr(sio, "async_mode", ""),
        "active_session_count": len(socket_sessions),
    }


@router.post("/conversations/respond-preview")
async def preview_ai_response(
    payload: RespondRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    headers = build_internal_headers(
        authorization=request.headers.get("authorization") or request.headers.get("Authorization", ""),
        company_id=current_user.get("company_id", ""),
        user_id=current_user.get("sub", ""),
        user_role=current_user.get("role", ""),
    )
    return await ai_client.request(
        "POST",
        "/api/ai/respond",
        headers=headers,
        json=payload.model_dump(),
    )


@router.post("/internal/socket/identity-event")
async def emit_identity_socket_event(
    payload: IdentitySocketEventRequest,
    current_user: dict = Depends(get_current_user),
):
    tenant_id = str(payload.tenant_id or "").strip()
    if not tenant_id:
        return {"status": "ignored", "reason": "tenant_id_required"}

    actor_role = str(current_user.get("role") or "").strip().lower()
    actor_company_id = str(current_user.get("company_id") or "").strip()
    if actor_role != "super_admin" and actor_company_id and actor_company_id != tenant_id:
        return {"status": "ignored", "reason": "cross_tenant_blocked"}

    event_payload = {
        "tenant_id": tenant_id,
        "aggregate_customer_id": payload.aggregate_customer_id,
        "event_type": payload.event_type,
        "idempotency_key": payload.idempotency_key,
        **dict(payload.payload or {}),
    }
    await sio.emit(payload.event_name, event_payload, room=f"company_{tenant_id}")
    return {"status": "ok", "event_name": payload.event_name, "tenant_id": tenant_id}
