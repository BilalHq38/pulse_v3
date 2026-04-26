from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from core.utils import make_id
from services.db_helpers import r, rs

logger = logging.getLogger(__name__)

META_GRAPH_BASE = (
    os.environ.get("META_GRAPH_API_BASE", "https://graph.facebook.com") or "https://graph.facebook.com"
).rstrip("/")
META_DEFAULT_VERSION = (os.environ.get("META_GRAPH_API_VERSION", "v21.0") or "v21.0").strip()
META_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("META_REQUEST_TIMEOUT_SECONDS", "20") or 20)
META_RETRY_ATTEMPTS = max(1, int(os.environ.get("META_RETRY_ATTEMPTS", "3") or 3))

_HTTP_CLIENT = httpx.AsyncClient(
    timeout=httpx.Timeout(
        META_REQUEST_TIMEOUT_SECONDS,
        connect=min(5.0, META_REQUEST_TIMEOUT_SECONDS),
        read=META_REQUEST_TIMEOUT_SECONDS,
        write=META_REQUEST_TIMEOUT_SECONDS,
        pool=min(5.0, META_REQUEST_TIMEOUT_SECONDS),
    ),
    limits=httpx.Limits(
        max_keepalive_connections=20,
        max_connections=50,
        keepalive_expiry=30.0,
    ),
)


def _credential_cipher() -> Fernet:
    raw_key = (
        os.environ.get("META_CREDENTIALS_ENCRYPTION_KEY", "")
        or os.environ.get("APP_ENCRYPTION_KEY", "")
        or os.environ.get("INTERNAL_SERVICE_SECRET", "")
    ).strip()
    if not raw_key:
        raise RuntimeError("META_CREDENTIALS_ENCRYPTION_KEY or APP_ENCRYPTION_KEY is required")
    if len(raw_key) != 44 or not raw_key.endswith("="):
        raw_key = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest()).decode("utf-8")
    return Fernet(raw_key.encode("utf-8"))


def encrypt_meta_secret(value: str) -> str:
    secret = (value or "").strip()
    if not secret:
        return ""
    return _credential_cipher().encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_meta_secret(value: str) -> str:
    encrypted = (value or "").strip()
    if not encrypted:
        return ""
    try:
        return _credential_cipher().decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise HTTPException(500, "Stored Meta credentials are unreadable") from exc


def mask_secret(value: str, *, prefix: int = 4, suffix: int = 2) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= prefix + suffix:
        return "*" * len(raw)
    return f"{raw[:prefix]}{'*' * max(len(raw) - prefix - suffix, 4)}{raw[-suffix:]}"


def normalize_meta_config(row: dict | None, *, include_secrets: bool = False) -> dict:
    data = dict(row or {})
    access_token = decrypt_meta_secret(data.get("access_token_enc", "")) if data.get("access_token_enc") else ""
    app_secret = decrypt_meta_secret(data.get("app_secret_enc", "")) if data.get("app_secret_enc") else ""
    webhook_secret = decrypt_meta_secret(data.get("webhook_secret_enc", "")) if data.get("webhook_secret_enc") else ""
    data["access_token_configured"] = bool(access_token)
    data["app_secret_configured"] = bool(app_secret)
    data["webhook_secret_configured"] = bool(webhook_secret)
    data["access_token_masked"] = mask_secret(access_token)
    data["app_secret_masked"] = mask_secret(app_secret)
    data["webhook_secret_masked"] = mask_secret(webhook_secret)
    if include_secrets:
        data["access_token"] = access_token
        data["app_secret"] = app_secret
        data["webhook_secret"] = webhook_secret
    data.pop("access_token_enc", None)
    data.pop("app_secret_enc", None)
    data.pop("webhook_secret_enc", None)
    return data


async def get_meta_config(
    db,
    company_id: str,
    *,
    config_id: str = "",
    channel: str = "whatsapp",
    include_secrets: bool = False,
) -> dict:
    company_id = (company_id or "").strip()
    if not company_id:
        raise HTTPException(400, "Company context is required")
    row = None
    if config_id:
        row = await db.fetchrow(
            "SELECT * FROM tenant_meta_config WHERE id=$1 AND company_id=$2 LIMIT 1",
            config_id,
            company_id,
        )
    else:
        row = await db.fetchrow(
            "SELECT * FROM tenant_meta_config "
            "WHERE company_id=$1 AND channel=$2 AND is_active=TRUE "
            "ORDER BY is_default DESC, updated_at DESC LIMIT 1",
            company_id,
            channel,
        )
    config = normalize_meta_config(r(row), include_secrets=include_secrets)
    if not config:
        raise HTTPException(404, "Meta configuration not found")
    return config


async def list_meta_configs(db, company_id: str, *, channel: str = "") -> list[dict]:
    company_id = (company_id or "").strip()
    if not company_id:
        return []
    if channel:
        rows = await db.fetch(
            "SELECT * FROM tenant_meta_config WHERE company_id=$1 AND channel=$2 "
            "ORDER BY channel, is_default DESC, updated_at DESC",
            company_id,
            channel,
        )
    else:
        rows = await db.fetch(
            "SELECT * FROM tenant_meta_config WHERE company_id=$1 ORDER BY channel, is_default DESC, updated_at DESC",
            company_id,
        )
    return [normalize_meta_config(row) for row in rs(rows)]


async def record_meta_usage(
    db,
    company_id: str,
    *,
    meta_config_id: str = "",
    channel: str = "whatsapp",
    endpoint: str = "",
    metric_name: str = "api_call",
    request_count: int = 1,
    unit_count: int = 1,
    billable_units: int = 0,
    status_code: int = 0,
    error_code: str = "",
) -> None:
    await db.execute(
        "INSERT INTO meta_api_usage(id,company_id,meta_config_id,channel,endpoint,metric_name,"
        "request_count,unit_count,status_code,error_code,billable_units,recorded_at,created_at) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW(),NOW())",
        make_id(),
        company_id,
        meta_config_id or None,
        channel,
        endpoint[:255],
        metric_name[:64],
        max(int(request_count or 1), 1),
        max(int(unit_count or 1), 1),
        int(status_code or 0),
        (error_code or "")[:128],
        max(int(billable_units or 0), 0),
    )


async def enforce_meta_quotas(db, company_id: str, config: dict, *, channel: str) -> None:
    per_minute_limit = max(int(config.get("requests_per_minute") or 120), 1)
    daily_limit = max(int(config.get("credit_limit_per_day") or 1000), 1)
    minute_window = datetime.now(timezone.utc) - timedelta(minutes=1)
    day_window = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    recent_calls = await db.fetchval(
        "SELECT COALESCE(SUM(request_count), 0) FROM meta_api_usage "
        "WHERE company_id=$1 AND channel=$2 AND recorded_at >= $3",
        company_id,
        channel,
        minute_window,
    )
    if int(recent_calls or 0) >= per_minute_limit:
        raise HTTPException(429, "Meta API per-minute quota exceeded for this tenant")
    daily_calls = await db.fetchval(
        "SELECT COALESCE(SUM(unit_count), 0) FROM meta_api_usage "
        "WHERE company_id=$1 AND channel=$2 AND recorded_at >= $3",
        company_id,
        channel,
        day_window,
    )
    if int(daily_calls or 0) >= daily_limit:
        raise HTTPException(429, "Meta API daily quota exceeded for this tenant")


def _meta_error_detail(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ("meta_error", "Meta API request failed")
    error = payload.get("error")
    if not isinstance(error, dict):
        return ("meta_error", payload.get("message", "Meta API request failed"))
    code = str(error.get("code") or error.get("error_subcode") or "meta_error")
    message = str(error.get("message") or "Meta API request failed")
    return code, message


def _meta_status_code(response: httpx.Response, payload: Any) -> int:
    code, _ = _meta_error_detail(payload)
    if response.status_code == 401 or code == "190":
        return 401
    if response.status_code == 429 or code in {"4", "613", "80007"}:
        return 429
    if response.status_code >= 500:
        return 503
    return response.status_code


async def meta_api_request(
    db,
    company_id: str,
    config: dict,
    *,
    method: str,
    path: str,
    channel: str = "whatsapp",
    params: dict | None = None,
    json_body: dict | None = None,
    data: dict | None = None,
    files: dict | None = None,
    metric_name: str = "api_call",
    unit_count: int = 1,
    billable_units: int = 0,
) -> dict:
    company_id = (company_id or "").strip()
    if not company_id:
        raise HTTPException(400, "Company context is required")
    normalized = normalize_meta_config(config, include_secrets=True)
    access_token = (normalized.get("access_token") or "").strip()
    if not access_token:
        raise HTTPException(400, "Meta access token is not configured")
    await enforce_meta_quotas(db, company_id, normalized, channel=channel)
    version = (normalized.get("api_version") or META_DEFAULT_VERSION).strip() or META_DEFAULT_VERSION
    url = f"{META_GRAPH_BASE}/{version}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {access_token}"}
    if files is None:
        headers["Content-Type"] = "application/json"

    response: httpx.Response | None = None
    payload: Any = {}
    last_error: Exception | None = None
    for attempt in range(META_RETRY_ATTEMPTS):
        try:
            response = await _HTTP_CLIENT.request(
                method.upper(),
                url,
                headers=headers,
                params=params,
                json=json_body,
                data=data,
                files=files,
            )
            try:
                payload = response.json()
            except Exception:
                payload = {"raw": response.text}
            if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < META_RETRY_ATTEMPTS:
                retry_after = response.headers.get("retry-after", "").strip()
                delay = float(retry_after) if retry_after else min(2**attempt, 4)
                await asyncio.sleep(max(delay, 0.5))
                continue
            break
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            last_error = exc
            if attempt + 1 < META_RETRY_ATTEMPTS:
                await asyncio.sleep(min(2**attempt, 4))
                continue
            raise HTTPException(503, "Meta API is temporarily unavailable") from exc

    if response is None:
        raise HTTPException(503, "Meta API is temporarily unavailable")
    if response.status_code >= 400:
        error_code, detail = _meta_error_detail(payload)
        await record_meta_usage(
            db,
            company_id,
            meta_config_id=normalized.get("id", ""),
            channel=channel,
            endpoint=path,
            metric_name=metric_name,
            request_count=1,
            unit_count=unit_count,
            billable_units=billable_units,
            status_code=response.status_code,
            error_code=error_code,
        )
        raise HTTPException(_meta_status_code(response, payload), detail)

    await record_meta_usage(
        db,
        company_id,
        meta_config_id=normalized.get("id", ""),
        channel=channel,
        endpoint=path,
        metric_name=metric_name,
        request_count=1,
        unit_count=unit_count,
        billable_units=billable_units,
        status_code=response.status_code,
    )
    if isinstance(payload, dict):
        return payload
    return {"data": payload, "error": str(last_error or "")}


async def sync_meta_templates(db, company_id: str, config: dict) -> list[dict]:
    normalized = normalize_meta_config(config)
    business_account_id = (normalized.get("business_account_id") or "").strip()
    if not business_account_id:
        raise HTTPException(400, "business_account_id is required to sync templates")
    payload = await meta_api_request(
        db,
        company_id,
        config,
        method="GET",
        path=f"{business_account_id}/message_templates",
        channel="whatsapp",
        metric_name="template_sync",
    )
    templates = payload.get("data", []) if isinstance(payload, dict) else []
    for template in templates:
        name = str(template.get("name") or "").strip()
        language = str((template.get("language") or "en_US")).strip() or "en_US"
        if not name:
            continue
        existing_id = await db.fetchval(
            "SELECT id FROM meta_message_templates WHERE company_id=$1 AND template_name=$2 AND language=$3 LIMIT 1",
            company_id,
            name,
            language,
        )
        record_id = existing_id or make_id()
        if existing_id:
            await db.execute(
                "UPDATE meta_message_templates SET meta_config_id=$1, external_template_id=$2, category=$3, "
                "template_status=$4, rejection_reason=$5, components_json=$6, last_synced_at=NOW(), updated_at=NOW() "
                "WHERE id=$7 AND company_id=$8",
                normalized.get("id", ""),
                str(template.get("id") or ""),
                str(template.get("category") or "MARKETING"),
                str(template.get("status") or "PENDING"),
                str(template.get("rejected_reason") or ""),
                json.dumps(template.get("components", []), ensure_ascii=True),
                record_id,
                company_id,
            )
        else:
            await db.execute(
                "INSERT INTO meta_message_templates(id,company_id,meta_config_id,external_template_id,template_name,language,"  # noqa: E501
                "category,template_status,rejection_reason,components_json,last_synced_at,created_at,updated_at) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW(),NOW(),NOW())",
                record_id,
                company_id,
                normalized.get("id", ""),
                str(template.get("id") or ""),
                name,
                language,
                str(template.get("category") or "MARKETING"),
                str(template.get("status") or "PENDING"),
                str(template.get("rejected_reason") or ""),
                json.dumps(template.get("components", []), ensure_ascii=True),
            )
    rows = await db.fetch(
        "SELECT * FROM meta_message_templates WHERE company_id=$1 ORDER BY updated_at DESC",
        company_id,
    )
    return rs(rows)


async def fetch_user_profile(
    db,
    company_id: str,
    config: dict,
    *,
    user_id: str,
    channel: str = "whatsapp",
) -> dict:
    """Fetch user profile data (name, profile picture, locale) from Meta Graph API."""
    company_id = (company_id or "").strip()
    user_id = (user_id or "").strip()
    if not company_id or not user_id:
        return {}

    fields = "name,profile_pic,locale,username"
    if channel == "instagram":
        fields = "name,profile_picture_url,username"
    elif channel == "facebook":
        fields = "name,profile_pic,locale"

    try:
        payload = await meta_api_request(
            db,
            company_id,
            config,
            method="GET",
            path=user_id,
            params={"fields": fields},
            channel=channel,
            metric_name="fetch_user_profile",
        )
        profile = {
            "meta_user_id": user_id,
            "name": str(payload.get("name") or "").strip(),
            "profile_picture": str(payload.get("profile_pic") or payload.get("profile_picture_url") or "").strip(),
            "username": str(payload.get("username") or "").strip(),
            "locale": str(payload.get("locale") or "").strip(),
            "channel": channel,
        }
        return profile
    except Exception as exc:
        logger.warning(
            "Meta user profile fetch failed company_id=%s user_id=%s channel=%s: %s",
            company_id,
            user_id,
            channel,
            exc,
        )
        return {"meta_user_id": user_id, "channel": channel}


async def update_message_delivery_status(
    db,
    *,
    company_id: str,
    message_external_id: str,
    status: str,
    channel: str = "whatsapp",
    timestamp: str | datetime = "",
) -> None:
    """Update delivery/read status for a message tracked in the messages table."""
    company_id = (company_id or "").strip()
    message_external_id = (message_external_id or "").strip()
    status = (status or "").strip().lower()
    if not company_id or not message_external_id or not status:
        return

    status_column_map = {
        "sent": "sent_at",
        "delivered": "delivered_at",
        "read": "read_at",
        "failed": "failed_at",
    }
    column = status_column_map.get(status)
    if not column:
        logger.debug(
            "Ignoring unknown delivery status=%s for message=%s",
            status,
            message_external_id,
        )
        return

    try:
        ts_value = datetime.now(timezone.utc)
        if isinstance(timestamp, datetime):
            ts_value = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        else:
            ts_text = str(timestamp or "").strip()
            if ts_text:
                if ts_text.isdigit():
                    try:
                        ts_value = datetime.fromtimestamp(int(ts_text), tz=timezone.utc)
                    except Exception:
                        pass
                else:
                    try:
                        parsed = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
                        ts_value = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                    except Exception:
                        pass
        conversation_id = await db.fetchval(
            f"UPDATE messages SET delivery_status=$1, {column}=$2, updated_at=NOW() "
            "WHERE company_id=$3 AND external_message_id=$4 "
            f"AND ({column} IS NULL) RETURNING conversation_id",
            status,
            ts_value,
            company_id,
            message_external_id,
        )
        if conversation_id:
            message_row = await db.fetchrow(
                "SELECT * FROM messages WHERE company_id=$1 AND external_message_id=$2 LIMIT 1",
                company_id,
                message_external_id,
            )
            if message_row:
                attachments = await db.fetch(
                    "SELECT * FROM message_attachments WHERE message_id=$1 ORDER BY created_at ASC",
                    message_row["id"],
                )
                payload = dict(message_row)
                payload["attachments"] = [
                    {
                        "id": row["id"],
                        "type": row["file_type"],
                        "url": row["file_url"],
                        "name": row["file_name"],
                        "size": row["file_size"],
                    }
                    for row in attachments
                ]
                from core.socket import emit_message_updated

                await emit_message_updated(str(conversation_id), payload)
    except Exception as exc:
        logger.warning(
            "Delivery status update failed company_id=%s msg=%s status=%s: %s",
            company_id,
            message_external_id,
            status,
            exc,
        )


async def process_delivery_status_webhook(
    db,
    *,
    company_id: str,
    channel: str,
    statuses: list[dict],
) -> None:
    """Process delivery/read receipt status updates from Meta webhook payloads."""
    for status_entry in statuses or []:
        msg_id = str((status_entry or {}).get("id") or "").strip()
        status = str((status_entry or {}).get("status") or "").strip()
        timestamp = str((status_entry or {}).get("timestamp") or "").strip()
        if timestamp and timestamp.isdigit():
            try:
                timestamp = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat()
            except Exception:
                pass
        if msg_id and status:
            await update_message_delivery_status(
                db,
                company_id=company_id,
                message_external_id=msg_id,
                status=status,
                channel=channel,
                timestamp=timestamp,
            )
            await record_meta_usage(
                db,
                company_id,
                channel=channel,
                endpoint="delivery_status",
                metric_name=f"status_{status}",
                billable_units=0,
            )
