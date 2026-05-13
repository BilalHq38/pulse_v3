"""
email_campaign_service.routes — HTTP surface for bulk email campaigns.

Endpoints (all mounted under ``/api`` by the service app factory):
  - GET    /campaigns                          list campaigns for the tenant
  - POST   /campaigns/preview                  resolve a filter dict to a recipient count
  - POST   /campaigns                          create a draft campaign
  - GET    /campaigns/{id}                     fetch a campaign + stats
  - GET    /campaigns/{id}/recipients          paginated recipient list
  - POST   /campaigns/{id}/send                start async dispatch
  - DELETE /campaigns/{id}                     delete (cascades recipients)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from services.db_helpers import (
    get_company_id,
    get_current_user_flexible,
    r,
    rs,
)
from services.email_campaign_service.service import (
    CampaignAIQuotaExceededError,
    CampaignAIUnavailableError,
    campaign_ai_quota_message,
    create_campaign,
    generate_campaign_copy,
    generate_html_email_body,
    resolve_recipients,
    schedule_campaign_send,
    update_campaign,
)
from shared.database import company_context

logger = logging.getLogger(__name__)
router = APIRouter()


def _db(request: Request):
    return request.app.state.db


def _manual_ai_response(message: str, *, ai_enabled: bool) -> dict:
    return {
        "message": message,
        "ai_enabled": ai_enabled,
        "manual_required": True,
    }


async def _ensure_company_ai_available(db, company_id: str) -> None:
    async with company_context(db, company_id):
        enabled = await db.fetchval(
            "SELECT COALESCE(ai_enabled, TRUE) FROM company_settings WHERE company_id=$1 LIMIT 1",
            company_id,
        )
    if enabled is False:
        raise CampaignAIQuotaExceededError(campaign_ai_quota_message())


@router.get("/campaigns")
async def list_campaigns(
    request: Request,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not cid:
        raise HTTPException(status_code=403, detail="Company context required")

    sql = "SELECT * FROM email_campaigns WHERE company_id=$1"
    args: list = [cid]
    if status:
        args.append(status)
        sql += f" AND status=${len(args)}"
    args.append(limit)
    sql += f" ORDER BY created_at DESC LIMIT ${len(args)}"
    return rs(await db.fetch(sql, *args))


@router.post("/campaigns/preview")
async def preview_campaign(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not cid:
        raise HTTPException(status_code=403, detail="Company context required")

    payload = await request.json()
    filters = dict(payload.get("filters") or {})
    recipients = await resolve_recipients(db, company_id=cid, filters=filters)
    return {
        "count": len(recipients),
        "sample": recipients[:10],
    }


@router.post("/campaigns")
async def create_campaign_route(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not cid:
        raise HTTPException(status_code=403, detail="Company context required")

    payload = await request.json()
    name = str(payload.get("name") or "").strip()
    subject = str(payload.get("subject") or "").strip()
    body = str(payload.get("body") or "").strip()
    html_body = str(payload.get("html_body") or "").strip()
    filters = dict(payload.get("filters") or {})

    if not subject:
        raise HTTPException(status_code=400, detail="Subject is required")
    if not (body or html_body):
        raise HTTPException(status_code=400, detail="Body or html_body is required")

    try:
        campaign = await create_campaign(
            db,
            company_id=cid,
            name=name or subject,
            subject=subject,
            body=body,
            html_body=html_body,
            filters=filters,
            created_by=str(cu.get("sub") or cu.get("email") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Campaign creation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create campaign")

    # Optional: send immediately if the client requested it.
    if payload.get("send_now") and campaign.get("total_recipients"):
        schedule_campaign_send(request.app, campaign["id"], cid)
        campaign["status"] = "queued"

    return campaign


@router.put("/campaigns/{campaign_id}")
async def update_campaign_route(campaign_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not cid:
        raise HTTPException(status_code=403, detail="Company context required")

    payload = await request.json()
    name = str(payload.get("name") or "").strip()
    subject = str(payload.get("subject") or "").strip()
    body = str(payload.get("body") or "").strip()
    html_body = str(payload.get("html_body") or "").strip()
    filters = dict(payload.get("filters") or {})

    if not subject:
        raise HTTPException(status_code=400, detail="Subject is required")
    if not (body or html_body):
        raise HTTPException(status_code=400, detail="Body or html_body is required")

    try:
        return await update_campaign(
            db,
            campaign_id=campaign_id,
            company_id=cid,
            name=name or subject,
            subject=subject,
            body=body,
            html_body=html_body,
            filters=filters,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Campaign update failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update campaign") from exc


@router.post("/campaigns/generate")
async def generate_campaign_route(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not cid:
        raise HTTPException(status_code=403, detail="Company context required")

    payload = await request.json()
    try:
        await _ensure_company_ai_available(db, cid)
        return await generate_campaign_copy(
            db,
            company_id=cid,
            product_id=str(payload.get("product_id") or "").strip(),
            campaign_goal=str(payload.get("campaign_goal") or "").strip(),
            audience_description=str(payload.get("audience_description") or "").strip(),
            tone=str(payload.get("tone") or "").strip(),
            call_to_action=str(payload.get("call_to_action") or "").strip(),
            offer_details=str(payload.get("offer_details") or "").strip(),
            extra_context=str(payload.get("extra_context") or "").strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CampaignAIQuotaExceededError as exc:
        logger.warning("Campaign copy generation unavailable company_id=%s reason=%s", cid, exc)
        raise HTTPException(
            status_code=503,
            detail=_manual_ai_response(str(exc), ai_enabled=False),
        ) from exc
    except CampaignAIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=_manual_ai_response(str(exc), ai_enabled=True)) from exc
    except Exception as exc:
        logger.exception("Campaign copy generation failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=_manual_ai_response(
                "AI/API issue: campaign copy could not be generated. Please write or paste the response manually.",
                ai_enabled=True,
            ),
        ) from exc


@router.post("/campaigns/generate-html-body")
async def generate_campaign_html_body_route(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not cid:
        raise HTTPException(status_code=403, detail="Company context required")

    payload = await request.json()
    try:
        await _ensure_company_ai_available(db, cid)
        return await generate_html_email_body(
            db,
            company_id=cid,
            product_id=str(payload.get("product_id") or "").strip(),
            description=str(payload.get("description") or "").strip(),
            current_body=str(payload.get("current_body") or "").strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CampaignAIQuotaExceededError as exc:
        logger.warning("HTML email body generation unavailable company_id=%s reason=%s", cid, exc)
        raise HTTPException(
            status_code=503,
            detail=_manual_ai_response(str(exc), ai_enabled=False),
        ) from exc
    except CampaignAIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=_manual_ai_response(str(exc), ai_enabled=True)) from exc
    except Exception as exc:
        logger.exception("HTML email body generation failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=_manual_ai_response(
                "AI/API issue: HTML email body could not be generated. Please write or paste the response manually.",
                ai_enabled=True,
            ),
        ) from exc


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not cid:
        raise HTTPException(status_code=403, detail="Company context required")

    campaign = r(
        await db.fetchrow(
            "SELECT * FROM email_campaigns WHERE id=$1 AND company_id=$2",
            campaign_id,
            cid,
        )
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.get("/campaigns/{campaign_id}/recipients")
async def list_campaign_recipients(
    campaign_id: str,
    request: Request,
    status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not cid:
        raise HTTPException(status_code=403, detail="Company context required")

    owner = await db.fetchrow(
        "SELECT id FROM email_campaigns WHERE id=$1 AND company_id=$2",
        campaign_id,
        cid,
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Campaign not found")

    sql = "SELECT * FROM email_campaign_recipients WHERE campaign_id=$1"
    args: list = [campaign_id]
    if status:
        args.append(status)
        sql += f" AND status=${len(args)}"
    args.extend([limit, offset])
    sql += f" ORDER BY created_at LIMIT ${len(args) - 1} OFFSET ${len(args)}"
    return rs(await db.fetch(sql, *args))


@router.post("/campaigns/{campaign_id}/send")
async def send_campaign(campaign_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not cid:
        raise HTTPException(status_code=403, detail="Company context required")

    campaign = r(
        await db.fetchrow(
            "SELECT * FROM email_campaigns WHERE id=$1 AND company_id=$2",
            campaign_id,
            cid,
        )
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.get("status") in {"sending", "completed"}:
        return {"id": campaign_id, "status": campaign["status"]}
    if not int(campaign.get("total_recipients") or 0):
        raise HTTPException(status_code=400, detail="Campaign has no recipients")

    await db.execute(
        "UPDATE email_campaigns SET status='queued' WHERE id=$1 AND status NOT IN ('sending','completed')",
        campaign_id,
    )
    schedule_campaign_send(request.app, campaign_id, cid)
    return {"id": campaign_id, "status": "queued"}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = get_company_id(cu)
    if not cid:
        raise HTTPException(status_code=403, detail="Company context required")

    result = await db.execute(
        "DELETE FROM email_campaigns WHERE id=$1 AND company_id=$2",
        campaign_id,
        cid,
    )
    return {"deleted": True, "result": str(result)}


routers = (router,)
