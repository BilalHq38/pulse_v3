from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from shared.auth.dependencies import get_current_user
from shared.config import service_urls
from shared.schemas.contracts import RespondRequest
from shared.service_client import ServiceClient, build_internal_headers

router = APIRouter()
ai_client = ServiceClient(service_urls().ai)


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
