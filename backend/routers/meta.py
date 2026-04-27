"""Meta / WhatsApp Cloud API management routes."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from core.utils import make_id
from services.ai_service.common import _extract_data_url_payload
from services.db_helpers import get_current_user_flexible, require_roles, r, rs
from services.meta_service import (
    encrypt_meta_secret,
    get_meta_config,
    list_meta_configs,
    mask_secret,
    meta_api_request,
    sync_meta_templates,
)

router = APIRouter()


def _db(req: Request):
    return req.app.state.db


class MetaConfigUpsert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    channel: Literal["whatsapp", "facebook", "instagram"] = "whatsapp"
    config_name: str = Field(default="default", min_length=1, max_length=100)
    provider_mode: Literal["cloud_api", "on_premises"] = "cloud_api"
    api_version: str = Field(default="v21.0", max_length=20)
    app_id: str = Field(default="", max_length=255)
    app_secret: str = Field(default="", max_length=2000)
    access_token: str = Field(default="", max_length=4000)
    verify_token: str = Field(default="", max_length=255)
    webhook_secret: str = Field(default="", max_length=2000)
    phone_number_id: str = Field(default="", max_length=255)
    business_account_id: str = Field(default="", max_length=255)
    system_user_id: str = Field(default="", max_length=255)
    catalog_id: str = Field(default="", max_length=255)
    credit_limit_per_day: int = Field(default=1000, ge=1, le=1_000_000)
    requests_per_minute: int = Field(default=120, ge=1, le=10_000)
    is_default: bool = True
    is_active: bool = True


class TemplateSendRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    to: str = Field(..., min_length=3, max_length=64)
    template_name: str = Field(..., min_length=1, max_length=255)
    language: str = Field(default="en_US", min_length=2, max_length=16)
    components: list[dict[str, Any]] = Field(default_factory=list)
    config_id: str = Field(default="", max_length=255)


class MediaSendRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    to: str = Field(..., min_length=3, max_length=64)
    media_type: Literal["image", "document", "video", "audio"] = "image"
    media_url: str = Field(default="", max_length=2000)
    data_url: str = Field(default="", max_length=8_500_000)
    caption: str = Field(default="", max_length=1024)
    filename: str = Field(default="", max_length=255)
    config_id: str = Field(default="", max_length=255)


class BusinessProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    about: Optional[str] = Field(default=None, max_length=512)
    address: Optional[str] = Field(default=None, max_length=512)
    description: Optional[str] = Field(default=None, max_length=512)
    email: Optional[str] = Field(default=None, max_length=255)
    profile_picture_url: Optional[str] = Field(default=None, max_length=2000)
    vertical: Optional[str] = Field(default=None, max_length=100)
    websites: Optional[list[str]] = Field(default=None, max_length=2)
    config_id: str = Field(default="", max_length=255)


class CatalogProductUpsert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    retailer_id: str = Field(..., min_length=1, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=5000)
    price: str = Field(default="", max_length=64)
    currency: str = Field(default="USD", min_length=3, max_length=8)
    availability: str = Field(default="in stock", max_length=64)
    image_url: str = Field(default="", max_length=2000)
    url: str = Field(default="", max_length=2000)
    config_id: str = Field(default="", max_length=255)


async def _sync_legacy_channel_settings(db, company_id: str, payload: MetaConfigUpsert) -> None:
    row = r(
        await db.fetchrow(
            "SELECT id FROM channel_settings WHERE company_id=$1 AND channel=$2 LIMIT 1",
            company_id,
            payload.channel,
        )
    )
    values = {
        "display_name": {
            "whatsapp": "WhatsApp Business",
            "facebook": "Facebook Messenger",
            "instagram": "Instagram",
        }.get(payload.channel, payload.channel.title()),
        "enabled": payload.is_active,
        "phone_number_id": payload.phone_number_id,
        "phone_number": "",
        "page_id": payload.business_account_id
        if payload.channel == "whatsapp"
        else payload.business_account_id or payload.catalog_id,
        "verify_token": payload.verify_token,
        "updated_at": "NOW()",
    }
    if row:
        await db.execute(
            "UPDATE channel_settings SET display_name=$1, enabled=$2, phone_number_id=$3, phone_number=$4, page_id=$5, verify_token=$6, updated_at=NOW() "  # noqa: E501
            "WHERE id=$7",
            values["display_name"],
            values["enabled"],
            values["phone_number_id"],
            values["phone_number"],
            values["page_id"],
            values["verify_token"],
            row["id"],
        )
        return
    await db.execute(
        "INSERT INTO channel_settings(id,company_id,channel,display_name,enabled,phone_number_id,phone_number,page_id,verify_token,created_at,updated_at) "  # noqa: E501
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW())",
        make_id(),
        company_id,
        payload.channel,
        values["display_name"],
        values["enabled"],
        values["phone_number_id"],
        values["phone_number"],
        values["page_id"],
        values["verify_token"],
    )


async def _sync_whatsapp_channel(db, company_id: str, config_id: str, payload: MetaConfigUpsert) -> None:
    if payload.channel != "whatsapp" or not payload.phone_number_id:
        return
    existing = await db.fetchval(
        "SELECT id FROM whatsapp_channels WHERE company_id=$1 AND phone_number_id=$2 LIMIT 1",
        company_id,
        payload.phone_number_id,
    )
    if existing:
        await db.execute(
            "UPDATE whatsapp_channels SET meta_config_id=$1, channel_name=$2, business_account_id=$3, is_default=$4, is_active=$5, updated_at=NOW() "  # noqa: E501
            "WHERE id=$6",
            config_id,
            payload.config_name,
            payload.business_account_id,
            payload.is_default,
            payload.is_active,
            existing,
        )
        return
    await db.execute(
        "INSERT INTO whatsapp_channels(id,company_id,meta_config_id,channel_name,phone_number_id,business_account_id,is_default,is_active,created_at,updated_at) "  # noqa: E501
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,NOW(),NOW())",
        make_id(),
        company_id,
        config_id,
        payload.config_name,
        payload.phone_number_id,
        payload.business_account_id,
        payload.is_default,
        payload.is_active,
    )


def _masked_config_response(config: dict) -> dict:
    data = dict(config)
    if data.get("access_token"):
        data["access_token_masked"] = mask_secret(data["access_token"])
    if data.get("app_secret"):
        data["app_secret_masked"] = mask_secret(data["app_secret"])
    if data.get("webhook_secret"):
        data["webhook_secret_masked"] = mask_secret(data["webhook_secret"])
    data.pop("access_token", None)
    data.pop("app_secret", None)
    data.pop("webhook_secret", None)
    return data


@router.get("/whatsapp/meta/health")
async def meta_health(request: Request):
    db = _db(request)
    current_user = await get_current_user_flexible(request)
    company_id = (current_user.get("company_id", "") or "").strip()
    configs = await list_meta_configs(db, company_id, channel="whatsapp")
    usage_last_hour = await db.fetchval(
        "SELECT COALESCE(SUM(request_count), 0) FROM meta_api_usage WHERE company_id=$1 AND recorded_at >= NOW() - INTERVAL '1 hour'",  # noqa: E501
        company_id,
    )
    templates = await db.fetchval(
        "SELECT COUNT(*) FROM meta_message_templates WHERE company_id=$1",
        company_id,
    )
    return {
        "status": "ok",
        "configured": bool(configs),
        "active_configs": len([cfg for cfg in configs if cfg.get("is_active")]),
        "requests_last_hour": int(usage_last_hour or 0),
        "templates_synced": int(templates or 0),
    }


@router.get("/whatsapp/meta/configs")
async def get_meta_configs(
    request: Request,
    channel: str = Query("whatsapp"),
):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    return await list_meta_configs(db, current_user.get("company_id", ""), channel=channel)


@router.post("/whatsapp/meta/configs")
async def create_meta_config(payload: MetaConfigUpsert, request: Request):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    config_id = make_id()
    if payload.is_default:
        await db.execute(
            "UPDATE tenant_meta_config SET is_default=FALSE, updated_at=NOW() WHERE company_id=$1 AND channel=$2",
            company_id,
            payload.channel,
        )
    await db.execute(
        "INSERT INTO tenant_meta_config(id,company_id,channel,config_name,provider_mode,api_version,app_id,app_secret_enc,"  # noqa: E501
        "access_token_enc,access_token_last4,verify_token,webhook_secret_enc,phone_number_id,business_account_id,system_user_id,"
        "catalog_id,credit_limit_per_day,requests_per_minute,is_default,is_active,created_at,updated_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,NOW(),NOW())",
        config_id,
        company_id,
        payload.channel,
        payload.config_name.strip(),
        payload.provider_mode,
        payload.api_version.strip(),
        payload.app_id.strip(),
        encrypt_meta_secret(payload.app_secret),
        encrypt_meta_secret(payload.access_token),
        (payload.access_token or "").strip()[-4:],
        payload.verify_token.strip(),
        encrypt_meta_secret(payload.webhook_secret),
        payload.phone_number_id.strip(),
        payload.business_account_id.strip(),
        payload.system_user_id.strip(),
        payload.catalog_id.strip(),
        payload.credit_limit_per_day,
        payload.requests_per_minute,
        payload.is_default,
        payload.is_active,
    )
    await _sync_legacy_channel_settings(db, company_id, payload)
    await _sync_whatsapp_channel(db, company_id, config_id, payload)
    return _masked_config_response(await get_meta_config(db, company_id, config_id=config_id, include_secrets=True))


@router.put("/whatsapp/meta/configs/{config_id}")
async def update_meta_config(config_id: str, payload: MetaConfigUpsert, request: Request):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    existing = await get_meta_config(db, company_id, config_id=config_id, include_secrets=True)
    if payload.is_default:
        await db.execute(
            "UPDATE tenant_meta_config SET is_default=FALSE, updated_at=NOW() WHERE company_id=$1 AND channel=$2 AND id<>$3",  # noqa: E501
            company_id,
            payload.channel,
            config_id,
        )
    await db.execute(
        "UPDATE tenant_meta_config SET channel=$1, config_name=$2, provider_mode=$3, api_version=$4, app_id=$5, "
        "app_secret_enc=$6, access_token_enc=$7, access_token_last4=$8, verify_token=$9, webhook_secret_enc=$10, "
        "phone_number_id=$11, business_account_id=$12, system_user_id=$13, catalog_id=$14, credit_limit_per_day=$15, "
        "requests_per_minute=$16, is_default=$17, is_active=$18, updated_at=NOW() WHERE id=$19 AND company_id=$20",
        payload.channel,
        payload.config_name.strip(),
        payload.provider_mode,
        payload.api_version.strip(),
        payload.app_id.strip(),
        encrypt_meta_secret(payload.app_secret)
        if payload.app_secret
        else encrypt_meta_secret(existing.get("app_secret", "")),
        encrypt_meta_secret(payload.access_token)
        if payload.access_token
        else encrypt_meta_secret(existing.get("access_token", "")),
        ((payload.access_token or existing.get("access_token", "")) or "")[-4:],
        payload.verify_token.strip(),
        encrypt_meta_secret(payload.webhook_secret)
        if payload.webhook_secret
        else encrypt_meta_secret(existing.get("webhook_secret", "")),
        payload.phone_number_id.strip(),
        payload.business_account_id.strip(),
        payload.system_user_id.strip(),
        payload.catalog_id.strip(),
        payload.credit_limit_per_day,
        payload.requests_per_minute,
        payload.is_default,
        payload.is_active,
        config_id,
        company_id,
    )
    await _sync_legacy_channel_settings(db, company_id, payload)
    await _sync_whatsapp_channel(db, company_id, config_id, payload)
    return _masked_config_response(await get_meta_config(db, company_id, config_id=config_id, include_secrets=True))


@router.delete("/whatsapp/meta/configs/{config_id}")
async def delete_meta_config(config_id: str, request: Request):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    deleted = await db.fetchval(
        "DELETE FROM tenant_meta_config WHERE id=$1 AND company_id=$2 RETURNING id",
        config_id,
        company_id,
    )
    if not deleted:
        raise HTTPException(404, "Meta configuration not found")
    await db.execute(
        "DELETE FROM whatsapp_channels WHERE meta_config_id=$1 AND company_id=$2",
        config_id,
        company_id,
    )
    return {"ok": True, "id": config_id}


@router.get("/whatsapp/meta/templates")
async def list_templates(request: Request, config_id: str = Query(default=""), sync: bool = Query(default=False)):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    if sync:
        config = await get_meta_config(db, company_id, config_id=config_id, include_secrets=True)
        return await sync_meta_templates(db, company_id, config)
    return rs(
        await db.fetch(
            "SELECT * FROM meta_message_templates WHERE company_id=$1 ORDER BY updated_at DESC LIMIT 200",
            company_id,
        )
    )


@router.post("/whatsapp/meta/templates/sync")
async def sync_templates(request: Request, config_id: str = Query(default="")):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    config = await get_meta_config(db, company_id, config_id=config_id, include_secrets=True)
    templates = await sync_meta_templates(db, company_id, config)
    return {"ok": True, "count": len(templates), "templates": templates}


@router.post("/whatsapp/meta/messages/template")
async def send_template_message(payload: TemplateSendRequest, request: Request):
    db = _db(request)
    current_user = await get_current_user_flexible(request)
    company_id = (current_user.get("company_id", "") or "").strip()
    config = await get_meta_config(db, company_id, config_id=payload.config_id, include_secrets=True)
    phone_number_id = (config.get("phone_number_id") or "").strip()
    if not phone_number_id:
        raise HTTPException(400, "phone_number_id is required for WhatsApp template sends")
    result = await meta_api_request(
        db,
        company_id,
        config,
        method="POST",
        path=f"{phone_number_id}/messages",
        channel="whatsapp",
        metric_name="template_message",
        billable_units=1,
        json_body={
            "messaging_product": "whatsapp",
            "to": payload.to.strip(),
            "type": "template",
            "template": {
                "name": payload.template_name.strip(),
                "language": {"code": payload.language.strip()},
                "components": payload.components,
            },
        },
    )
    return {"ok": True, "result": result}


@router.post("/whatsapp/meta/messages/media")
async def send_media_message(payload: MediaSendRequest, request: Request):
    db = _db(request)
    current_user = await get_current_user_flexible(request)
    company_id = (current_user.get("company_id", "") or "").strip()
    config = await get_meta_config(db, company_id, config_id=payload.config_id, include_secrets=True)
    phone_number_id = (config.get("phone_number_id") or "").strip()
    if not phone_number_id:
        raise HTTPException(400, "phone_number_id is required for WhatsApp media sends")

    media_ref: dict[str, str]
    if payload.data_url:
        parsed = _extract_data_url_payload(payload.data_url)
        if not parsed:
            raise HTTPException(400, "Unsupported media data URL")
        mime_type, raw_bytes, _ = parsed
        upload = await meta_api_request(
            db,
            company_id,
            config,
            method="POST",
            path=f"{phone_number_id}/media",
            channel="whatsapp",
            metric_name="media_upload",
            files={
                "file": (payload.filename or f"upload.{mime_type.split('/')[-1]}", raw_bytes, mime_type),
                "messaging_product": (None, "whatsapp"),
            },
        )
        media_id = str(upload.get("id") or "").strip()
        if not media_id:
            raise HTTPException(502, "Meta media upload did not return an id")
        media_ref = {"id": media_id}
    elif payload.media_url:
        media_ref = {"link": payload.media_url.strip()}
    else:
        raise HTTPException(400, "media_url or data_url is required")

    result = await meta_api_request(
        db,
        company_id,
        config,
        method="POST",
        path=f"{phone_number_id}/messages",
        channel="whatsapp",
        metric_name="media_message",
        billable_units=1,
        json_body={
            "messaging_product": "whatsapp",
            "to": payload.to.strip(),
            "type": payload.media_type,
            payload.media_type: {
                **media_ref,
                **({"caption": payload.caption.strip()} if payload.caption else {}),
                **(
                    {"filename": payload.filename.strip()}
                    if payload.filename and payload.media_type == "document"
                    else {}
                ),
            },
        },
    )
    return {"ok": True, "result": result}


@router.get("/whatsapp/meta/business-profile")
async def get_business_profile(request: Request, config_id: str = Query(default="")):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    config = await get_meta_config(db, company_id, config_id=config_id, include_secrets=True)
    phone_number_id = (config.get("phone_number_id") or "").strip()
    if not phone_number_id:
        raise HTTPException(400, "phone_number_id is required")
    return await meta_api_request(
        db,
        company_id,
        config,
        method="GET",
        path=f"{phone_number_id}/whatsapp_business_profile",
        channel="whatsapp",
        metric_name="business_profile_read",
        params={"fields": "about,address,description,email,profile_picture_url,websites,vertical"},
    )


@router.put("/whatsapp/meta/business-profile")
async def update_business_profile(payload: BusinessProfileUpdate, request: Request):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    config = await get_meta_config(db, company_id, config_id=payload.config_id, include_secrets=True)
    phone_number_id = (config.get("phone_number_id") or "").strip()
    if not phone_number_id:
        raise HTTPException(400, "phone_number_id is required")
    body = payload.model_dump(exclude_none=True)
    body.pop("config_id", None)
    return await meta_api_request(
        db,
        company_id,
        config,
        method="POST",
        path=f"{phone_number_id}/whatsapp_business_profile",
        channel="whatsapp",
        metric_name="business_profile_update",
        json_body=body,
    )


@router.get("/whatsapp/meta/catalogs")
async def list_catalogs(request: Request, config_id: str = Query(default="")):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    config = await get_meta_config(db, company_id, config_id=config_id, include_secrets=True)
    business_account_id = (config.get("business_account_id") or "").strip()
    if not business_account_id:
        raise HTTPException(400, "business_account_id is required")
    return await meta_api_request(
        db,
        company_id,
        config,
        method="GET",
        path=f"{business_account_id}/owned_product_catalogs",
        channel="whatsapp",
        metric_name="catalog_list",
    )


@router.get("/whatsapp/meta/catalogs/{catalog_id}/products")
async def list_catalog_products(catalog_id: str, request: Request, config_id: str = Query(default="")):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    config = await get_meta_config(db, company_id, config_id=config_id, include_secrets=True)
    return await meta_api_request(
        db,
        company_id,
        config,
        method="GET",
        path=f"{catalog_id}/products",
        channel="whatsapp",
        metric_name="catalog_products_read",
    )


@router.post("/whatsapp/meta/catalogs/{catalog_id}/products")
async def upsert_catalog_product(catalog_id: str, payload: CatalogProductUpsert, request: Request):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    config = await get_meta_config(db, company_id, config_id=payload.config_id, include_secrets=True)
    body = payload.model_dump()
    body.pop("config_id", None)
    return await meta_api_request(
        db,
        company_id,
        config,
        method="POST",
        path=f"{catalog_id}/products",
        channel="whatsapp",
        metric_name="catalog_product_upsert",
        json_body=body,
    )


@router.get("/whatsapp/meta/usage")
async def get_meta_usage(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
):
    db = _db(request)
    current_user = await require_roles(request, ["admin", "super_admin"])
    company_id = (current_user.get("company_id", "") or "").strip()
    rows = rs(
        await db.fetch(
            "SELECT channel, endpoint, metric_name, DATE(recorded_at) AS usage_day, "
            "SUM(request_count) AS request_count, SUM(unit_count) AS unit_count, SUM(billable_units) AS billable_units "
            "FROM meta_api_usage WHERE company_id=$1 AND recorded_at >= NOW() - ($2::text || ' days')::interval "
            "GROUP BY channel, endpoint, metric_name, DATE(recorded_at) "
            "ORDER BY usage_day DESC, channel, endpoint",
            company_id,
            days,
        )
    )
    return {
        "days": days,
        "totals": {
            "requests": sum(int(row.get("request_count") or 0) for row in rows),
            "units": sum(int(row.get("unit_count") or 0) for row in rows),
            "billable_units": sum(int(row.get("billable_units") or 0) for row in rows),
        },
        "rows": rows,
    }
