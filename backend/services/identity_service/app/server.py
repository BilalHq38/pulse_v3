from __future__ import annotations

import logging
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.config import gateway_allowed_origins, is_production

from services.identity_service.app.db.database import close_db_engine, get_db, init_db_schema
from services.identity_service.app.db.models import IdentityMapping, UnifiedCustomer
from services.identity_service.app.db.schemas import (
    AccuracyReportResponse,
    AdminLoginRequest,
    AdminLoginResponse,
    ConsentGrantRequest,
    GranularConsent,
    ConsentRevokeRequest,
    ConsentResponse,
    ConsentStatusResponse,
    EnrichRequest,
    FingerprintMatchRequest,
    FingerprintMatchResult,
    FingerprintRegisterRequest,
    IdentityProfileResponse,
    MergeRequest,
    PublicUnificationRequest,
    PublicUnificationResponse,
    ResolveRequest,
    ResolveResponse,
    ReviewDecisionRequest,
    ReviewQueueItemResponse,
    SplitRequest,
)
from services.identity_service.app.security.security import (
    authenticate_admin,
    create_admin_token,
    get_current_admin_user,
    get_tenant_context,
    require_internal_admin,
    validate_identity_security_configuration,
)
from services.identity_service.app.services.cache import close_redis, enforce_rate_limit, init_redis
from services.identity_service.app.services.events import (
    close_event_publisher,
    event_system_snapshot,
    init_event_publisher,
    start_event_retry_worker,
)
from services.identity_service.app.services.identity import (
    accuracy_report,
    auto_detect_review_candidates,
    consent_status,
    delete_fingerprint,
    enrich_identity,
    get_identity_profile,
    grant_consent,
    list_review_queue,
    match_fingerprint,
    merge_customers,
    purge_customer,
    register_fingerprint,
    resolve_identity,
    resolve_review_item,
    revoke_consent,
    split_customer,
    submit_public_unification,
)
from services.identity_service.app.services.observability import (
    configure_structured_logging,
    increment_counter,
    observe_histogram,
    snapshot_metrics,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
load_dotenv(PROJECT_ROOT / ".env", override=False)
configure_structured_logging()
logger = logging.getLogger(__name__)


def _identity_allowed_origins() -> list[str]:
    configured: list[str] = []
    for raw in (
        os.environ.get("CORS_ORIGINS", ""),
        os.environ.get("GATEWAY_ALLOWED_ORIGINS", ""),
    ):
        configured.extend(origin.strip() for origin in raw.split(",") if origin.strip())
    origins = [origin for origin in (configured or gateway_allowed_origins()) if origin and origin != "*"]
    return list(dict.fromkeys(origins or gateway_allowed_origins()))


def _validate_identity_cors_settings() -> None:
    if not is_production():
        return
    origins = _identity_allowed_origins()
    if not origins:
        raise RuntimeError("CORS_ORIGINS must be explicitly configured in production")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _validate_identity_cors_settings()
    validate_identity_security_configuration()
    await init_db_schema()
    await init_redis()
    await init_event_publisher()
    await start_event_retry_worker()
    yield
    await close_event_publisher()
    await close_redis()
    await close_db_engine()


app = FastAPI(title="identity_service", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_identity_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@app.middleware("http")
async def request_observability_middleware(request: Request, call_next):
    started = perf_counter()
    request_id = (request.headers.get("X-Request-ID") or secrets.token_hex(8)).strip()
    tenant_id = (request.headers.get("X-Tenant-ID") or "unknown").strip() or "unknown"
    method = request.method.upper()
    path = request.url.path
    status_code = 500

    try:
        response = await call_next(request)
        status_code = int(response.status_code)
        response.headers.setdefault("X-Request-ID", request_id)
        return response
    except Exception:
        increment_counter(
            "identity.http.errors",
            labels={"tenant_id": tenant_id, "method": method, "path": path},
        )
        raise
    finally:
        duration_ms = round((perf_counter() - started) * 1000.0, 2)
        labels = {
            "tenant_id": tenant_id,
            "method": method,
            "path": path,
            "status_class": f"{max(1, int(status_code // 100))}xx",
        }
        increment_counter("identity.http.requests", labels=labels)
        observe_histogram("identity.http.latency_ms", duration_ms, labels=labels)
        logger.info(
            "identity request complete",
            extra={
                "tenant_id": tenant_id,
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "request_id": request_id,
            },
        )


class InternalIdentityResolveRequest(BaseModel):
    platform: str = "pulse_customer"
    platform_user_id: str
    phone_number: str | None = None
    email_address: str | None = None
    full_name: str | None = None
    username: str | None = None
    profile_picture_phash: str | None = None
    language: str | None = None
    locale: str | None = None
    device_signals: dict[str, Any] = Field(default_factory=dict)
    typing_speed: float | None = None
    message_patterns: list[str] = Field(default_factory=list)
    session_timing: list[float] = Field(default_factory=list)
    cookie_id: str | None = None
    consent_version: str = "v1.0"
    consent_method: Literal["checkbox", "oauth", "verbal_recorded"] = "checkbox"


class InternalIdentityMergeRequest(BaseModel):
    customer_ids: list[str] = Field(default_factory=list)
    merge_reason: str = "manual_merge"


class InternalIdentitySplitRequest(BaseModel):
    profile_id: str | None = None
    customer_id: str
    mapping_ids: list[str] = Field(default_factory=list)
    fingerprint_ids: list[str] = Field(default_factory=list)
    split_reason: str = "manual_split"


class SuggestionResolveRequest(BaseModel):
    action: Literal["accept", "reject", "keep_separate"] = "accept"
    notes: str | None = None
    suggestion_id: str | None = None
    resolution_id: str | None = None


def _normalize_platform(platform: str | None) -> str:
    normalized = (platform or "pulse_customer").strip().lower()
    if normalized in {"whatsapp", "facebook", "instagram", "web_chat", "pulse_customer"}:
        return normalized
    return "pulse_customer"


async def _ensure_active_consent_token(
    db: AsyncSession,
    tenant_id: str,
    *,
    platform_user_id: str,
    platform: str,
    consent_version: str,
    consent_method: Literal["checkbox", "oauth", "verbal_recorded"],
) -> str:
    existing = await consent_status(db, tenant_id, platform_user_id)
    for row in existing:
        if row.platform == platform and row.consent_active and row.consent_token:
            return row.consent_token

    granted = await grant_consent(
        db,
        tenant_id,
        ConsentGrantRequest(
            platform_user_id=platform_user_id,
            platform=platform,
            consent_version=consent_version,
            consent_method=consent_method,
            granular_consent=GranularConsent(
                consent_device_tracking=True,
                consent_behavioral_analysis=True,
                consent_cross_platform_link=True,
                consent_profile_picture=True,
                consent_data_retention_days=365,
            ),
        ),
    )
    return granted.consent_token


def _flatten_numeric(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return sum(_flatten_numeric(item) for item in value.values())
    if isinstance(value, list):
        return sum(_flatten_numeric(item) for item in value)
    return 0.0


def _profile_summary(profile: IdentityProfileResponse) -> dict[str, Any]:
    primary_email = ""
    primary_phone = ""
    display_name = profile.primary_name or ""
    members = []
    for mapping in profile.mappings:
        if not primary_email and mapping.email:
            primary_email = mapping.email
        if not primary_phone and mapping.phone:
            primary_phone = mapping.phone
        if not display_name:
            display_name = mapping.name or mapping.platform_username or mapping.platform_user_id
        members.append(
            {
                "customer_id": mapping.mapping_id,
                "mapping_id": mapping.mapping_id,
                "platform_user_id": mapping.platform_user_id,
                "platform": mapping.platform,
                "name": mapping.name or profile.primary_name or mapping.platform_username or mapping.platform_user_id,
                "email": mapping.email or "",
                "phone": mapping.phone or "",
                "match_method": "identity",
                "is_primary": mapping.is_primary_platform,
            }
        )

    platforms = sorted({item.get("platform", "") for item in members if item.get("platform")})
    return {
        "id": profile.customer_id,
        "display_name": display_name or f"Identity {profile.customer_id[:8]}",
        "primary_email": primary_email,
        "primary_phone": primary_phone,
        "platforms_used": ", ".join(platforms) if platforms else "unknown",
        "member_count": len(members),
        "members": members,
        "total_interactions": len(members) + len(profile.fingerprints),
        "lifetime_value": 0,
        "all_conversations": [],
        "confidence_score": profile.profile_confidence,
        "profile_confidence": profile.profile_confidence,
    }


async def _resolve_split_mapping_ids(
    db: AsyncSession,
    tenant_id: str,
    *,
    target_profile_id: str,
    candidate_customer_id: str,
) -> list[str]:
    safe_profile_id = str(target_profile_id or "").strip()
    safe_candidate = str(candidate_customer_id or "").strip()
    if not safe_profile_id or not safe_candidate:
        return []
    try:
        target_profile_uuid = uuid.UUID(safe_profile_id)
    except ValueError:
        return []

    resolved_mapping_ids: list[str] = []

    try:
        direct_mapping_uuid = uuid.UUID(safe_candidate)
    except ValueError:
        direct_mapping_uuid = None

    if direct_mapping_uuid is not None:
        direct_mapping = await db.execute(
            select(IdentityMapping.mapping_id).where(
                IdentityMapping.tenant_id == tenant_id,
                IdentityMapping.customer_id == target_profile_uuid,
                IdentityMapping.mapping_id == direct_mapping_uuid,
            )
        )
        resolved_mapping_ids.extend(str(item) for item in direct_mapping.scalars().all())

    if resolved_mapping_ids:
        return list(dict.fromkeys(resolved_mapping_ids))

    candidate_rows = await db.execute(
        select(IdentityMapping.mapping_id).where(
            IdentityMapping.tenant_id == tenant_id,
            IdentityMapping.customer_id == target_profile_uuid,
            IdentityMapping.platform == "pulse_customer",
            IdentityMapping.platform_user_id == safe_candidate,
        )
    )
    resolved_mapping_ids.extend(str(item) for item in candidate_rows.scalars().all())
    return list(dict.fromkeys(resolved_mapping_ids))


async def _resolve_with_auto_consent(
    db: AsyncSession,
    tenant_id: str,
    payload: InternalIdentityResolveRequest,
) -> tuple[ResolveResponse, IdentityProfileResponse]:
    platform = _normalize_platform(payload.platform)
    consent_token = await _ensure_active_consent_token(
        db,
        tenant_id,
        platform_user_id=payload.platform_user_id,
        platform=platform,
        consent_version=payload.consent_version,
        consent_method=payload.consent_method,
    )
    resolved = await resolve_identity(
        db,
        tenant_id,
        ResolveRequest(
            platform=platform,
            platform_user_id=payload.platform_user_id,
            consent_token=consent_token,
            phone_number=payload.phone_number,
            email_address=payload.email_address,
            full_name=payload.full_name,
            username=payload.username,
            profile_picture_phash=payload.profile_picture_phash,
            language=payload.language,
            locale=payload.locale,
            device_signals=payload.device_signals,
            typing_speed=payload.typing_speed,
            message_patterns=payload.message_patterns,
            session_timing=payload.session_timing,
            cookie_id=payload.cookie_id,
        ),
    )
    profile = await get_identity_profile(db, tenant_id, resolved.customer_id)
    return resolved, profile


@app.get("/api/health")
async def healthcheck():
    return {
        "status": "ok",
        "service": "identity_service",
        "version": "2.0.0",
        "self_service_mode": _is_truthy(os.environ.get("IDENTITY_SELF_SERVICE_MODE")),
    }


@app.get("/api/metrics")
@app.get("/metrics")
async def metrics_snapshot():
    return {
        "service": "identity_service",
        "metrics": snapshot_metrics(),
        "events": await event_system_snapshot(),
    }


@app.post("/api/unification/public", response_model=PublicUnificationResponse)
async def public_unification_submission(
    payload: PublicUnificationRequest,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(
        tenant.tenant_id,
        limit_per_minute=max(10, int(os.environ.get("PUBLIC_UNIFICATION_RATE_LIMIT_PER_MINUTE", "120"))),
    )
    return await submit_public_unification(db, tenant.tenant_id, payload)


@app.post("/api/identity/resolve")
async def identity_resolve_compat(
    payload: InternalIdentityResolveRequest,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    resolved, profile = await _resolve_with_auto_consent(db, tenant.tenant_id, payload)
    return {
        "customer_id": resolved.customer_id,
        "confidence_score": resolved.confidence_score,
        "match_type": resolved.match_type,
        "is_new_user": resolved.is_new_user,
        "merge_performed": resolved.merge_performed,
        "review_required": resolved.review_required,
        "profile": _profile_summary(profile),
    }


@app.post("/api/identity/unify")
async def identity_unify_compat(
    payload: InternalIdentityResolveRequest,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    resolved, profile = await _resolve_with_auto_consent(db, tenant.tenant_id, payload)
    return {
        "status": "ok",
        "customer_id": resolved.customer_id,
        "profile": _profile_summary(profile),
        "review_required": resolved.review_required,
    }


@app.get("/api/identity/profiles")
async def identity_list_profiles_compat(
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    result = await db.execute(
        select(UnifiedCustomer.customer_id)
        .where(
            UnifiedCustomer.tenant_id == tenant.tenant_id,
            UnifiedCustomer.is_active.is_(True),
        )
        .order_by(UnifiedCustomer.updated_at.desc())
        .limit(200)
    )
    profile_ids = [str(item) for item in result.scalars().all()]
    output = []
    for profile_id in profile_ids:
        try:
            profile = await get_identity_profile(db, tenant.tenant_id, profile_id)
        except HTTPException:
            continue
        summary = _profile_summary(profile)
        output.append(summary)
    return output


@app.get("/api/identity/profiles/{profile_id}")
async def identity_get_profile_compat(
    profile_id: str,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    profile = await get_identity_profile(db, tenant.tenant_id, profile_id)
    return _profile_summary(profile)


@app.get("/api/identity/customer/{customer_id}")
async def identity_profile_for_customer_compat(
    customer_id: str,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    result = await db.execute(
        select(IdentityMapping)
        .where(
            IdentityMapping.tenant_id == tenant.tenant_id,
            IdentityMapping.platform == "pulse_customer",
            IdentityMapping.platform_user_id == customer_id,
        )
        .order_by(IdentityMapping.linked_at.desc())
        .limit(1)
    )
    mapping = result.scalar_one_or_none()
    if mapping is None:
        return {"unified": False, "customer_id": customer_id}

    profile = await get_identity_profile(db, tenant.tenant_id, str(mapping.customer_id))
    summary = _profile_summary(profile)
    return {
        "unified": summary.get("member_count", 0) >= 2,
        "profile": summary,
    }


@app.post("/api/identity/merge")
async def identity_merge_compat(
    payload: InternalIdentityMergeRequest,
    _admin=Depends(require_internal_admin),
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    _ = _admin
    await enforce_rate_limit(tenant.tenant_id)
    raw_customer_ids = [str(item).strip() for item in payload.customer_ids if str(item).strip()]
    if len(raw_customer_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 customer IDs required for merging")

    resolved_customer_ids: list[str] = []
    for customer_id in raw_customer_ids:
        direct_profile_id = ""
        try:
            direct_profile = await get_identity_profile(db, tenant.tenant_id, customer_id)
            direct_profile_id = str(direct_profile.customer_id)
        except HTTPException as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise

        if direct_profile_id:
            if direct_profile_id not in resolved_customer_ids:
                resolved_customer_ids.append(direct_profile_id)
            continue

        resolved, _profile = await _resolve_with_auto_consent(
            db,
            tenant.tenant_id,
            InternalIdentityResolveRequest(
                platform="pulse_customer",
                platform_user_id=customer_id,
            ),
        )
        if resolved.customer_id not in resolved_customer_ids:
            resolved_customer_ids.append(resolved.customer_id)

    if len(resolved_customer_ids) < 2:
        raise HTTPException(status_code=400, detail="No distinct identities available for merge")

    target_customer_id = resolved_customer_ids[0]
    for source_customer_id in resolved_customer_ids[1:]:
        if source_customer_id == target_customer_id:
            continue
        await merge_customers(
            db,
            tenant.tenant_id,
            MergeRequest(
                source_customer_id=source_customer_id,
                target_customer_id=target_customer_id,
                merge_reason=payload.merge_reason,
            ),
            merged_by="manual",
        )

    profile = await get_identity_profile(db, tenant.tenant_id, target_customer_id)
    return _profile_summary(profile)


@app.post("/api/identity/split")
async def identity_split_compat(
    payload: InternalIdentitySplitRequest,
    _admin=Depends(require_internal_admin),
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    _ = _admin
    await enforce_rate_limit(tenant.tenant_id)
    target_profile_id = (payload.profile_id or payload.customer_id or "").strip()
    if not target_profile_id:
        raise HTTPException(status_code=400, detail="profile_id or customer_id is required")

    mapping_ids = [str(item).strip() for item in payload.mapping_ids if str(item).strip()]
    fingerprint_ids = [str(item).strip() for item in payload.fingerprint_ids if str(item).strip()]
    if not mapping_ids and not fingerprint_ids:
        candidate = (payload.customer_id or "").strip()
        if payload.profile_id and candidate and candidate != payload.profile_id:
            mapping_ids = await _resolve_split_mapping_ids(
                db,
                tenant.tenant_id,
                target_profile_id=target_profile_id,
                candidate_customer_id=candidate,
            )

    if not mapping_ids and not fingerprint_ids:
        raise HTTPException(status_code=400, detail="mapping_ids or fingerprint_ids required for split")

    return await split_customer(
        db,
        tenant.tenant_id,
        SplitRequest(
            customer_id=target_profile_id,
            identity_mapping_ids=mapping_ids,
            fingerprint_ids=fingerprint_ids,
            split_reason=payload.split_reason,
        ),
    )


@app.get("/api/identity/suggestions")
async def identity_suggestions_compat(
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    queue = await list_review_queue(db, tenant.tenant_id)
    pending = [item for item in queue if item.status == "pending"]

    customer_ids = {item.source_customer_id for item in pending if item.source_customer_id}
    customer_ids.update(item.candidate_customer_id for item in pending if item.candidate_customer_id)

    customer_lookup: dict[str, UnifiedCustomer] = {}
    if customer_ids:
        customer_rows = (
            (
                await db.execute(
                    select(UnifiedCustomer)
                    .options(selectinload(UnifiedCustomer.mappings))
                    .where(
                        UnifiedCustomer.tenant_id == tenant.tenant_id,
                        UnifiedCustomer.customer_id.in_(list(customer_ids)),
                    )
                )
            )
            .scalars()
            .all()
        )
        customer_lookup = {str(row.customer_id): row for row in customer_rows}

    output = []
    for item in pending:
        source = customer_lookup.get(item.source_customer_id)
        candidate = customer_lookup.get(item.candidate_customer_id or "")
        source_mapping = source.mappings[0] if source and source.mappings else None
        candidate_mapping = candidate.mappings[0] if candidate and candidate.mappings else None

        score_breakdown = item.score_breakdown or {}
        score_value = score_breakdown.get("total_score")
        if score_value is None:
            score_value = score_breakdown.get("confidence")
        if score_value is None:
            score_value = _flatten_numeric(score_breakdown)
        try:
            score = min(max(float(score_value), 0.0), 1.0)
        except (TypeError, ValueError):
            score = 0.0

        reasons = item.independent_signals or []
        reason_text = (
            ", ".join([str(part).strip() for part in reasons if str(part).strip()]) or item.reason or "identity_review"
        )

        output.append(
            {
                "id": item.resolution_id,
                "suggestion_id": item.resolution_id,
                "resolution_id": item.resolution_id,
                "review_id": item.review_id,
                "source": item.source,
                "customer_id_a": item.source_customer_id,
                "customer_id_b": item.candidate_customer_id,
                "name_a": (source.primary_name if source else "")
                or (source_mapping.name if source_mapping else "")
                or (source_mapping.platform_username if source_mapping else "")
                or (f"Customer {str(item.source_customer_id)[:8]}" if item.source_customer_id else "Unknown"),
                "email_a": (source_mapping.email if source_mapping else "") or "",
                "phone_a": (source_mapping.phone if source_mapping else "") or "",
                "company_a": "",
                "customer_company_name_a": "",
                "name_b": (candidate.primary_name if candidate else "")
                or (candidate_mapping.name if candidate_mapping else "")
                or (candidate_mapping.platform_username if candidate_mapping else "")
                or (f"Customer {item.candidate_customer_id[:8]}" if item.candidate_customer_id else "Unknown"),
                "email_b": (candidate_mapping.email if candidate_mapping else "") or "",
                "phone_b": (candidate_mapping.phone if candidate_mapping else "") or "",
                "company_b": "",
                "customer_company_name_b": "",
                "match_score": score,
                "match_reasons": reason_text,
                "status": item.status,
                "created_at": item.created_at,
            }
        )

    output.sort(key=lambda row: float(row.get("match_score", 0.0)), reverse=True)
    return output[:100]


@app.post("/api/identity/suggestions/{suggestion_id}/resolve")
async def identity_resolve_suggestion_compat(
    suggestion_id: str,
    payload: SuggestionResolveRequest,
    _admin=Depends(require_internal_admin),
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    _ = _admin
    await enforce_rate_limit(tenant.tenant_id)
    action_map = {
        "accept": "approve_merge",
        "reject": "reject",
        "keep_separate": "keep_separate",
    }
    # Compat contract: frontend callers may send `suggestion_id`, while the
    # underlying identity service resolves review items by `resolution_id`.
    # Accept both names and normalize them to the same target here.
    target_resolution_id = (
        str(payload.resolution_id or "").strip()
        or str(payload.suggestion_id or "").strip()
        or str(suggestion_id or "").strip()
    )
    result = await resolve_review_item(
        db,
        tenant.tenant_id,
        target_resolution_id,
        ReviewDecisionRequest(
            action=action_map[payload.action],
            notes=payload.notes,
        ),
    )
    return {
        "status": "ok",
        "suggestion_id": target_resolution_id,
        "resolution_id": target_resolution_id,
        **result,
    }


@app.post("/api/identity/auto-detect")
async def identity_auto_detect_compat(
    _admin=Depends(require_internal_admin),
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    _ = _admin
    await enforce_rate_limit(tenant.tenant_id)
    result = await auto_detect_review_candidates(db, tenant.tenant_id)
    return {"status": "ok", **result}


@app.post("/api/v1/auth/login", response_model=AdminLoginResponse)
async def admin_login(payload: AdminLoginRequest):
    admin = authenticate_admin(payload.email, payload.password)
    if admin is None:
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    token, expires_in = create_admin_token(admin)
    return AdminLoginResponse(access_token=token, expires_in=expires_in, email=admin.email)


@app.post("/api/consent/grant", response_model=ConsentResponse)
@app.post("/consent/grant", response_model=ConsentResponse)
async def consent_grant(
    payload: ConsentGrantRequest,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    return await grant_consent(db, tenant.tenant_id, payload)


@app.post("/api/consent/revoke", response_model=ConsentResponse)
@app.post("/consent/revoke", response_model=ConsentResponse)
async def consent_revoke(
    payload: ConsentRevokeRequest,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    return await revoke_consent(
        db, tenant.tenant_id, payload.platform_user_id, payload.platform, payload.purge_linked_data
    )


@app.get("/api/consent/status/{platform_user_id}", response_model=list[ConsentStatusResponse])
@app.get("/consent/status/{platform_user_id}", response_model=list[ConsentStatusResponse])
async def consent_status_endpoint(
    platform_user_id: str,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    return await consent_status(db, tenant.tenant_id, platform_user_id)


@app.delete("/api/consent/purge/{customer_id}")
@app.delete("/consent/purge/{customer_id}")
async def consent_purge(
    customer_id: str,
    tenant=Depends(get_tenant_context),
    admin=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    _ = admin
    return await purge_customer(db, tenant.tenant_id, customer_id)


@app.post("/api/v1/identity/resolve", response_model=ResolveResponse)
async def identity_resolve(
    payload: ResolveRequest,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    return await resolve_identity(db, tenant.tenant_id, payload)


@app.post("/api/v1/identity/enrich")
async def identity_enrich(
    payload: EnrichRequest,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    return await enrich_identity(db, tenant.tenant_id, payload)


@app.get("/api/v1/identity/{customer_id}", response_model=IdentityProfileResponse)
async def identity_profile(
    customer_id: str,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    return await get_identity_profile(db, tenant.tenant_id, customer_id)


@app.post("/api/v1/identity/merge")
async def identity_merge(
    payload: MergeRequest,
    tenant=Depends(get_tenant_context),
    admin=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    _ = admin
    await enforce_rate_limit(tenant.tenant_id)
    return await merge_customers(db, tenant.tenant_id, payload, merged_by="manual")


@app.post("/api/v1/identity/split")
async def identity_split(
    payload: SplitRequest,
    tenant=Depends(get_tenant_context),
    admin=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    _ = admin
    await enforce_rate_limit(tenant.tenant_id)
    return await split_customer(db, tenant.tenant_id, payload)


@app.post("/api/v1/fingerprint/register")
async def fingerprint_register(
    payload: FingerprintRegisterRequest,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    return await register_fingerprint(db, tenant.tenant_id, payload)


@app.post("/api/v1/fingerprint/match", response_model=list[FingerprintMatchResult])
async def fingerprint_match(
    payload: FingerprintMatchRequest,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    return await match_fingerprint(db, tenant.tenant_id, payload)


@app.delete("/api/v1/fingerprint/{fingerprint_id}")
async def fingerprint_delete(
    fingerprint_id: str,
    tenant=Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(tenant.tenant_id)
    return await delete_fingerprint(db, tenant.tenant_id, fingerprint_id)


@app.get("/api/admin/review-queue", response_model=list[ReviewQueueItemResponse])
@app.get("/admin/review-queue", response_model=list[ReviewQueueItemResponse])
async def admin_review_queue(
    tenant=Depends(get_tenant_context),
    admin=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    _ = admin
    await enforce_rate_limit(tenant.tenant_id)
    return await list_review_queue(db, tenant.tenant_id)


@app.post("/api/admin/resolve/{resolution_id}")
@app.post("/admin/resolve/{resolution_id}")
async def admin_resolve_review(
    resolution_id: str,
    payload: ReviewDecisionRequest,
    tenant=Depends(get_tenant_context),
    admin=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    _ = admin
    await enforce_rate_limit(tenant.tenant_id)
    return await resolve_review_item(db, tenant.tenant_id, resolution_id, payload)


@app.get("/api/admin/accuracy-report", response_model=AccuracyReportResponse)
@app.get("/admin/accuracy-report", response_model=AccuracyReportResponse)
async def admin_accuracy_report(
    tenant=Depends(get_tenant_context),
    admin=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    _ = admin
    await enforce_rate_limit(tenant.tenant_id)
    return await accuracy_report(db, tenant.tenant_id)
