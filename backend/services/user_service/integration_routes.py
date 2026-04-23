from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from shared.auth.dependencies import get_current_user
from shared.service_client import build_internal_headers
from services.user_service.auth_client import auth_service_client

router = APIRouter()


@router.post("/users/session-validate")
async def validate_auth_session(
    payload: dict,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    headers = build_internal_headers(
        authorization=request.headers.get("authorization") or request.headers.get("Authorization", ""),
        company_id=current_user.get("company_id", ""),
        user_id=current_user.get("sub", ""),
        user_role=current_user.get("role", ""),
    )
    return await auth_service_client.request(
        "POST",
        "/api/auth/session",
        headers=headers,
        json={"session_id": payload.get("session_id", "")},
    )
