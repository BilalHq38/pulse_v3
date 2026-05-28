from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from services.identity_service.app.db.models import (
    ConsentLedger,
    DeviceFingerprint,
    IdentityMapping,
    MergedProfileRecord,
    ProfileMergeHistory,
    ResolutionAuditLog,
    ReviewQueue,
    UnifiedCustomer,
)
from services.identity_service.app.db.schema import (
    AccuracyReportResponse,
    ConsentGrantRequest,
    ConsentResponse,
    ConsentStatusResponse,
    DeviceFingerprintOut,
    EnrichRequest,
    FingerprintMatchRequest,
    FingerprintMatchResult,
    FingerprintRegisterRequest,
    IdentityMappingOut,
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
from services.identity_service.app.security.security import anonymize_ip, hash_with_tenant_salt, issue_consent_token
from services.identity_service.app.services.cache import delete_keys, get_json, set_json
from services.identity_service.app.services.events import emit_identity_event
from services.identity_service.app.services.observability import increment_counter, observe_histogram
from services.identity_service.app.core.embeddings import embedding_service
from shared.config import service_urls
from shared.service_client import build_internal_headers
from services.identity_service.app.utils.utils import (
    build_embedding_text,
    build_fingerprint_hash,
    cosine_similarity,
    fuzzy_name_similarity,
    ip_subnet,
    normalize_email,
    normalize_phone,
    normalize_text,
    partial_signal_match_ratio,
    profile_picture_distance,
    score_to_confidence,
    serialize_any,
    typing_speed_within_range,
    username_similarity,
    utcnow,
    vector_to_pg_literal,
)

logger = logging.getLogger(__name__)
PUBLIC_UNIFICATION_SOURCE = "public_unification"
_CUSTOMER_SERVICE_BASE_URL = service_urls().customer.rstrip("/")
_REALTIME_RELAY_TIMEOUT_SECONDS = max(
    1.0,
    float(os.environ.get("IDENTITY_REALTIME_RELAY_TIMEOUT_SECONDS", "5") or 5),
)


def _vector_enabled() -> bool:
    return (os.environ.get("IDENTITY_USE_VECTOR", "false") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass
class CandidateScore:
    customer: UnifiedCustomer
    score: int = 0
    match_type: str = "probabilistic"
    data_sources_used: list[str] = field(default_factory=list)
    independent_signals: list[str] = field(default_factory=list)
    breakdown: dict[str, Any] = field(default_factory=dict)
    decision_reason: str = "new_user"


def _customer_cache_key(tenant_id: str, customer_id: str | uuid.UUID) -> str:
    return f"tenant:{tenant_id}:customer:{customer_id}"


def _mapping_snapshot(mapping: IdentityMapping) -> dict[str, Any]:
    return {
        "mapping_id": str(mapping.mapping_id),
        "customer_id": str(mapping.customer_id),
        "platform": mapping.platform,
        "platform_user_id": mapping.platform_user_id,
        "platform_username": mapping.platform_username,
        "phone": mapping.phone,
        "email": mapping.email,
        "name": mapping.name,
        "confidence": mapping.confidence_score if mapping.confidence_score is not None else mapping.confidence,
        "is_primary_platform": mapping.is_primary_platform,
        "linked_at": mapping.linked_at.isoformat() if mapping.linked_at else None,
    }


def _platforms_from_snapshots(items: list[dict[str, Any]]) -> list[str]:
    return sorted({str(item.get("platform") or "").strip() for item in items if str(item.get("platform") or "").strip()})


async def _emit_event_safe(
    *,
    tenant_id: str,
    event_type: str,
    payload: dict[str, Any],
    aggregate_customer_id: str | None = None,
    idempotency_key: str | None = None,
) -> None:
    try:
        await emit_identity_event(
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload,
            aggregate_customer_id=aggregate_customer_id,
            idempotency_key=idempotency_key,
            dispatch_now=True,
        )
        await _emit_realtime_socket_event_safe(
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload,
            aggregate_customer_id=aggregate_customer_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        increment_counter(
            "identity.events.emit_errors",
            labels={"tenant_id": tenant_id, "event_type": event_type},
        )
        logger.warning(
            "identity event emission skipped tenant_id=%s event_type=%s error=%s",
            tenant_id,
            event_type,
            exc,
        )


def _socket_event_name_for(event_type: str) -> str:
    normalized = str(event_type or "").strip().lower()
    if normalized == "identity.merged":
        return "identity_merged"
    if normalized == "identity.split":
        return "identity_split"
    if normalized == "identity.resolved":
        return "identity_resolved"
    return ""


async def _emit_realtime_socket_event_safe(
    *,
    tenant_id: str,
    event_type: str,
    payload: dict[str, Any],
    aggregate_customer_id: str | None = None,
    idempotency_key: str | None = None,
) -> None:
    socket_event_name = _socket_event_name_for(event_type)
    if not socket_event_name or not _CUSTOMER_SERVICE_BASE_URL:
        return
    try:
        headers = build_internal_headers(
            company_id=tenant_id,
            user_id="identity-service",
            user_role="admin",
        )
        async with httpx.AsyncClient(timeout=_REALTIME_RELAY_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{_CUSTOMER_SERVICE_BASE_URL}/api/internal/socket/identity-event",
                headers=headers,
                json={
                    "event_name": socket_event_name,
                    "tenant_id": tenant_id,
                    "aggregate_customer_id": aggregate_customer_id,
                    "event_type": event_type,
                    "idempotency_key": idempotency_key,
                    "payload": payload,
                },
            )
        if response.status_code >= 400:
            logger.warning(
                "identity realtime relay failed tenant_id=%s event_type=%s status=%s",
                tenant_id,
                event_type,
                response.status_code,
            )
            return
        logger.info(
            "identity realtime relay delivered tenant_id=%s event_type=%s socket_event=%s",
            tenant_id,
            event_type,
            socket_event_name,
        )
    except Exception as exc:
        logger.warning(
            "identity realtime relay skipped tenant_id=%s event_type=%s error=%s",
            tenant_id,
            event_type,
            exc,
        )


async def _build_response_with_event(
    *,
    db: AsyncSession,
    tenant_id: str,
    customer_id: uuid.UUID,
    audit: ResolutionAuditLog,
    is_new_user: bool,
    merge_performed: bool,
    platforms_linked: list[str],
    data_sources_used: list[str],
    review_required: bool,
    review_status: str | None,
    request_payload: ResolveRequest,
) -> ResolveResponse:
    response = await _build_response(
        db,
        customer_id,
        audit,
        is_new_user,
        merge_performed,
        platforms_linked,
        data_sources_used,
        review_required,
        review_status,
    )

    increment_counter(
        "identity.resolve.responses",
        labels={
            "tenant_id": tenant_id,
            "match_type": response.match_type,
            "review_required": str(bool(response.review_required)).lower(),
        },
    )
    observe_histogram(
        "identity.resolve.processing_ms",
        float(audit.processing_ms or 0),
        labels={"tenant_id": tenant_id, "match_type": response.match_type},
    )

    await _emit_event_safe(
        tenant_id=tenant_id,
        event_type="identity.resolved",
        aggregate_customer_id=response.customer_id,
        idempotency_key=f"identity.resolved:{response.audit_resolution_id}",
        payload={
            "customer_id": response.customer_id,
            "audit_resolution_id": response.audit_resolution_id,
            "match_type": response.match_type,
            "confidence_score": response.confidence_score,
            "is_new_user": response.is_new_user,
            "merge_performed": response.merge_performed,
            "review_required": response.review_required,
            "review_status": response.review_status,
            "decision_reason": response.decision_reason,
            "platform": request_payload.platform,
            "platform_user_id": request_payload.platform_user_id,
            "resolved_at": response.resolved_at.isoformat(),
        },
    )

    return response


async def grant_consent(db: AsyncSession, tenant_id: str, payload: ConsentGrantRequest) -> ConsentResponse:
    await db.execute(
        update(ConsentLedger)
        .where(
            ConsentLedger.tenant_id == tenant_id,
            ConsentLedger.platform == payload.platform,
            ConsentLedger.platform_user_id == payload.platform_user_id,
            ConsentLedger.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow(), consent_given=False)
    )
    consent = ConsentLedger(
        tenant_id=tenant_id,
        platform_user_id=payload.platform_user_id,
        platform=payload.platform,
        consent_given=True,
        consent_version=payload.consent_version,
        consent_method=payload.consent_method,
        consent_token=issue_consent_token(),
        ip_hash=anonymize_ip(payload.ip_address),
        granular_consent=payload.granular_consent.model_dump(),
        data_retention_days=payload.granular_consent.consent_data_retention_days,
    )
    db.add(consent)
    await db.commit()
    await db.refresh(consent)
    increment_counter(
        "identity.consent.granted",
        labels={"tenant_id": tenant_id, "platform": payload.platform},
    )
    logger.info(
        "identity consent granted tenant_id=%s platform=%s platform_user_id=%s consent_id=%s",
        tenant_id,
        payload.platform,
        payload.platform_user_id,
        consent.consent_id,
    )
    return ConsentResponse(
        consent_id=str(consent.consent_id),
        consent_token=consent.consent_token,
        consent_given=True,
        platform_user_id=consent.platform_user_id,
        platform=consent.platform,
        status="granted",
        consent_timestamp=consent.consent_timestamp,
        revoked_at=consent.revoked_at,
        granular_consent=payload.granular_consent,
    )


async def revoke_consent(
    db: AsyncSession, tenant_id: str, platform_user_id: str, platform: str, purge_linked_data: bool = False
) -> ConsentResponse:
    consent = await _get_active_consent(db, tenant_id, platform_user_id, platform)
    if consent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active consent not found")
    consent.revoked_at = utcnow()
    consent.consent_given = False
    await db.commit()
    increment_counter(
        "identity.consent.revoked",
        labels={"tenant_id": tenant_id, "platform": platform},
    )
    logger.info(
        "identity consent revoked tenant_id=%s platform=%s platform_user_id=%s consent_id=%s purge_linked_data=%s",
        tenant_id,
        platform,
        platform_user_id,
        consent.consent_id,
        bool(purge_linked_data),
    )

    if purge_linked_data:
        mapping = await _get_identity_mapping(db, tenant_id, platform, platform_user_id)
        if mapping is not None:
            await purge_customer(db, tenant_id, mapping.customer_id)

    return ConsentResponse(
        consent_id=str(consent.consent_id),
        consent_token=consent.consent_token,
        consent_given=False,
        platform_user_id=consent.platform_user_id,
        platform=consent.platform,
        status="revoked",
        consent_timestamp=consent.consent_timestamp,
        revoked_at=consent.revoked_at,
        granular_consent=consent.granular_consent,
    )


async def consent_status(db: AsyncSession, tenant_id: str, platform_user_id: str) -> list[ConsentStatusResponse]:
    result = await db.execute(
        select(ConsentLedger)
        .where(ConsentLedger.tenant_id == tenant_id, ConsentLedger.platform_user_id == platform_user_id)
        .order_by(ConsentLedger.consent_timestamp.desc())
    )
    rows = result.scalars().all()
    active_count = 0
    for row in rows:
        if row.consent_given and row.revoked_at is None:
            active_count += 1
    logger.info(
        "identity consent status tenant_id=%s platform_user_id=%s total_rows=%s active_rows=%s",
        tenant_id,
        platform_user_id,
        len(rows),
        active_count,
    )
    return [
        ConsentStatusResponse(
            platform_user_id=row.platform_user_id,
            platform=row.platform,
            consent_active=row.consent_given and row.revoked_at is None,
            consent_token=row.consent_token if row.revoked_at is None else None,
            consent_version=row.consent_version,
            granted_at=row.consent_timestamp,
            revoked_at=row.revoked_at,
            granular_consent=row.granular_consent,
        )
        for row in rows
    ]


async def purge_customer(db: AsyncSession, tenant_id: str, customer_id: uuid.UUID | str) -> dict[str, Any]:
    customer_uuid = _to_uuid(customer_id)
    customer = await _get_customer(db, tenant_id, customer_uuid)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    consent_ids = []
    if customer.consent_id:
        consent_ids.append(customer.consent_id)

    mapping_result = await db.execute(
        select(IdentityMapping.platform, IdentityMapping.platform_user_id).where(
            IdentityMapping.tenant_id == tenant_id, IdentityMapping.customer_id == customer_uuid
        )
    )
    mappings = mapping_result.all()

    await db.execute(
        delete(DeviceFingerprint).where(
            DeviceFingerprint.tenant_id == tenant_id, DeviceFingerprint.customer_id == customer_uuid
        )
    )
    await db.execute(
        delete(IdentityMapping).where(
            IdentityMapping.tenant_id == tenant_id, IdentityMapping.customer_id == customer_uuid
        )
    )
    await db.execute(
        delete(UnifiedCustomer).where(
            UnifiedCustomer.tenant_id == tenant_id, UnifiedCustomer.customer_id == customer_uuid
        )
    )
    await db.execute(
        update(ConsentLedger)
        .where(
            ConsentLedger.tenant_id == tenant_id,
            ConsentLedger.platform_user_id.in_([item.platform_user_id for item in mappings]),
        )
        .values(revoked_at=utcnow(), consent_given=False)
    )
    await db.commit()

    cache_keys = [_customer_cache_key(tenant_id, customer_uuid)]
    if customer.primary_phone_hash:
        cache_keys.append(f"tenant:{tenant_id}:phone:{customer.primary_phone_hash}")
    if customer.primary_email_hash:
        cache_keys.append(f"tenant:{tenant_id}:email:{customer.primary_email_hash}")
    for mapping in mappings:
        cache_keys.append(f"tenant:{tenant_id}:platform:{mapping.platform}:{mapping.platform_user_id}")
    await delete_keys(cache_keys)

    return {"customer_id": str(customer_uuid), "purged": True, "consent_ids": [str(item) for item in consent_ids]}


async def register_fingerprint(
    db: AsyncSession, tenant_id: str, payload: FingerprintRegisterRequest
) -> DeviceFingerprintOut:
    consent = await validate_consent(db, tenant_id, payload.platform_user_id, payload.platform, payload.consent_token)
    if not consent.granular_consent.get("consent_device_tracking"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device tracking consent not granted")

    fingerprint_hash = build_fingerprint_hash(payload.signals)
    if not fingerprint_hash:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unable to build fingerprint")

    customer_id = _to_uuid(payload.customer_id) if payload.customer_id else None
    if customer_id is None:
        mapping = await _get_identity_mapping(db, tenant_id, payload.platform, payload.platform_user_id)
        if mapping is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Customer context not found for fingerprint registration"
            )
        customer_id = mapping.customer_id

    result = await db.execute(
        select(DeviceFingerprint).where(
            DeviceFingerprint.tenant_id == tenant_id,
            DeviceFingerprint.customer_id == customer_id,
            DeviceFingerprint.fingerprint_hash == fingerprint_hash,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        row.last_seen = utcnow()
        row.match_count += 1
        row.signals_json = payload.signals
        await db.commit()
        await db.refresh(row)
        return _fingerprint_out(row)

    fingerprint = DeviceFingerprint(
        tenant_id=tenant_id,
        customer_id=customer_id,
        fingerprint_hash=fingerprint_hash,
        signals_json=payload.signals,
    )
    db.add(fingerprint)
    await db.commit()
    await db.refresh(fingerprint)
    return _fingerprint_out(fingerprint)


async def match_fingerprint(
    db: AsyncSession, tenant_id: str, payload: FingerprintMatchRequest
) -> list[FingerprintMatchResult]:
    fingerprint_hash = payload.fingerprint_hash or build_fingerprint_hash(payload.signals)
    if not fingerprint_hash:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No fingerprint content provided")

    result = await db.execute(
        select(DeviceFingerprint).where(
            DeviceFingerprint.tenant_id == tenant_id,
            DeviceFingerprint.fingerprint_hash == fingerprint_hash,
        )
    )
    exact = result.scalars().all()
    if exact:
        return [
            FingerprintMatchResult(
                customer_id=str(row.customer_id),
                fingerprint_id=str(row.fingerprint_id),
                exact_match=True,
                match_score=1.0,
                matched_signals=list(payload.signals.keys()) if payload.signals else [],
            )
            for row in exact
        ]

    result = await db.execute(select(DeviceFingerprint).where(DeviceFingerprint.tenant_id == tenant_id))
    candidates = result.scalars().all()
    matches: list[FingerprintMatchResult] = []
    for candidate in candidates:
        ratio, matched_signals = partial_signal_match_ratio(payload.signals, candidate.signals_json)
        if ratio >= 0.75:
            matches.append(
                FingerprintMatchResult(
                    customer_id=str(candidate.customer_id),
                    fingerprint_id=str(candidate.fingerprint_id),
                    exact_match=False,
                    match_score=round(ratio, 4),
                    matched_signals=matched_signals,
                )
            )
    matches.sort(key=lambda item: item.match_score, reverse=True)
    return matches[:10]


async def delete_fingerprint(db: AsyncSession, tenant_id: str, fingerprint_id: str):
    target_id = _to_uuid(fingerprint_id)
    await db.execute(
        delete(DeviceFingerprint).where(
            DeviceFingerprint.tenant_id == tenant_id,
            DeviceFingerprint.fingerprint_id == target_id,
        )
    )
    await db.commit()
    return {"deleted": True, "fingerprint_id": fingerprint_id}


async def get_identity_profile(db: AsyncSession, tenant_id: str, customer_id: str) -> IdentityProfileResponse:
    cache_key = _customer_cache_key(tenant_id, customer_id)
    cached = await get_json(cache_key)
    if cached:
        return IdentityProfileResponse(**cached)

    customer = await _get_customer(db, tenant_id, _to_uuid(customer_id), with_related=True)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    merge_result = await db.execute(
        select(ProfileMergeHistory)
        .where(
            ProfileMergeHistory.tenant_id == tenant_id,
            (ProfileMergeHistory.source_customer_id == customer.customer_id)
            | (ProfileMergeHistory.target_customer_id == customer.customer_id),
        )
        .order_by(ProfileMergeHistory.merged_at.desc())
    )
    history_rows = merge_result.scalars().all()
    merged_result = await db.execute(
        select(MergedProfileRecord)
        .where(
            MergedProfileRecord.tenant_id == tenant_id,
            (MergedProfileRecord.unified_customer_id == customer.customer_id)
            | (MergedProfileRecord.source_customer_id == customer.customer_id)
            | (MergedProfileRecord.target_customer_id == customer.customer_id),
        )
        .order_by(MergedProfileRecord.created_at.desc())
    )
    merged_rows = merged_result.scalars().all()
    merged_profiles = [
        {
            "record_id": str(row.record_id),
            "unified_customer_id": str(row.unified_customer_id) if row.unified_customer_id else None,
            "source_customer_id": str(row.source_customer_id) if row.source_customer_id else None,
            "target_customer_id": str(row.target_customer_id) if row.target_customer_id else None,
            "source_platforms": row.source_platforms or [],
            "target_platforms": row.target_platforms or [],
            "original_identities": row.original_identities or {},
            "unified_identity_mapping": row.unified_identity_mapping or {},
            "merge_history": row.merge_history or {},
            "merge_reason": row.merge_reason,
            "merged_by": row.merged_by,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in merged_rows
    ]

    profile = IdentityProfileResponse(
        customer_id=str(customer.customer_id),
        tenant_id=customer.tenant_id,
        is_active=customer.is_active,
        profile_confidence=customer.profile_confidence,
        primary_name=customer.primary_name,
        primary_phone_hash=customer.primary_phone_hash,
        primary_email_hash=customer.primary_email_hash,
        signal_profile=customer.signal_profile or {},
        consent_id=str(customer.consent_id) if customer.consent_id else None,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
        mappings=[_mapping_out(item) for item in customer.mappings],
        fingerprints=[_fingerprint_out(item) for item in customer.fingerprints],
        merge_history=[
            {
                "merge_id": str(row.merge_id),
                "source_customer_id": str(row.source_customer_id) if row.source_customer_id else None,
                "target_customer_id": str(row.target_customer_id) if row.target_customer_id else None,
                "merge_reason": row.merge_reason,
                "merged_at": row.merged_at,
                "merged_by": row.merged_by,
            }
            for row in history_rows
        ],
        merged_profiles=merged_profiles,
    )
    await set_json(cache_key, profile.model_dump(mode="json"), ttl_seconds=300)
    return profile


async def merge_customers(
    db: AsyncSession,
    tenant_id: str,
    payload: MergeRequest,
    merged_by: str = "manual",
) -> dict[str, Any]:
    source_id = _to_uuid(payload.source_customer_id)
    target_id = _to_uuid(payload.target_customer_id)
    if source_id == target_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Source and target cannot match")

    source = await _get_customer(db, tenant_id, source_id, with_related=True)
    target = await _get_customer(db, tenant_id, target_id, with_related=True)
    if source is None or target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merge customer not found")

    source_identity_snapshot = [_mapping_snapshot(item) for item in list(source.mappings or [])]
    target_identity_snapshot = [_mapping_snapshot(item) for item in list(target.mappings or [])]
    source_platforms = _platforms_from_snapshots(source_identity_snapshot)
    target_platforms = _platforms_from_snapshots(target_identity_snapshot)

    if source.primary_phone_hash and not target.primary_phone_hash:
        target.primary_phone_hash = source.primary_phone_hash
    if source.primary_email_hash and not target.primary_email_hash:
        target.primary_email_hash = source.primary_email_hash
    if source.primary_name and not target.primary_name:
        target.primary_name = source.primary_name
    if source.signal_profile:
        target.signal_profile = {**source.signal_profile, **(target.signal_profile or {})}
    target.profile_confidence = max(target.profile_confidence, source.profile_confidence)

    await db.execute(
        update(IdentityMapping)
        .where(IdentityMapping.tenant_id == tenant_id, IdentityMapping.customer_id == source_id)
        .values(customer_id=target_id)
    )
    await db.execute(
        update(DeviceFingerprint)
        .where(DeviceFingerprint.tenant_id == tenant_id, DeviceFingerprint.customer_id == source_id)
        .values(customer_id=target_id)
    )
    source.is_active = False

    merge_record = ProfileMergeHistory(
        tenant_id=tenant_id,
        source_customer_id=source_id,
        target_customer_id=target_id,
        merge_reason=payload.merge_reason,
        merged_by=merged_by,
    )
    db.add(merge_record)
    db.add(
        MergedProfileRecord(
            tenant_id=tenant_id,
            unified_customer_id=target_id,
            source_customer_id=source_id,
            target_customer_id=target_id,
            source_platforms=source_platforms,
            target_platforms=target_platforms,
            original_identities={
                "source": source_identity_snapshot,
                "target": target_identity_snapshot,
            },
            unified_identity_mapping={
                "customer_id": str(target_id),
                "primary_name": target.primary_name,
                "primary_phone_hash": target.primary_phone_hash,
                "primary_email_hash": target.primary_email_hash,
                "platforms": sorted(set(source_platforms + target_platforms)),
            },
            merge_history={
                "source_customer_id": str(source_id),
                "target_customer_id": str(target_id),
                "merge_reason": payload.merge_reason,
                "merged_by": merged_by,
                "merged_at": utcnow().isoformat(),
            },
            merge_reason=payload.merge_reason,
            merged_by=merged_by,
        )
    )
    await db.commit()

    await delete_keys(
        [
            _customer_cache_key(tenant_id, payload.source_customer_id),
            _customer_cache_key(tenant_id, payload.target_customer_id),
        ]
    )

    await _emit_event_safe(
        tenant_id=tenant_id,
        event_type="identity.merged",
        aggregate_customer_id=str(target_id),
        idempotency_key=f"identity.merged:{merge_record.merge_id}",
        payload={
            "merge_id": str(merge_record.merge_id),
            "source_customer_id": payload.source_customer_id,
            "target_customer_id": payload.target_customer_id,
            "merge_reason": payload.merge_reason,
            "merged_by": merged_by,
            "merged_at": merge_record.merged_at.isoformat(),
        },
    )

    increment_counter("identity.merge.completed", labels={"tenant_id": tenant_id, "merged_by": merged_by})
    return {
        "merged": True,
        "source_customer_id": payload.source_customer_id,
        "target_customer_id": payload.target_customer_id,
    }


async def split_customer(db: AsyncSession, tenant_id: str, payload: SplitRequest) -> dict[str, Any]:
    customer = await _get_customer(db, tenant_id, _to_uuid(payload.customer_id), with_related=True)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    if not payload.identity_mapping_ids and not payload.fingerprint_ids:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nothing selected for split")

    mapping_ids = {_to_uuid(item) for item in payload.identity_mapping_ids}
    fingerprint_ids = {_to_uuid(item) for item in payload.fingerprint_ids}

    matched_mapping_ids: set[uuid.UUID] = set()
    matched_fingerprint_ids: set[uuid.UUID] = set()

    if mapping_ids:
        mapping_result = await db.execute(
            select(IdentityMapping.mapping_id).where(
                IdentityMapping.tenant_id == tenant_id,
                IdentityMapping.customer_id == customer.customer_id,
                IdentityMapping.mapping_id.in_(mapping_ids),
            )
        )
        matched_mapping_ids = set(mapping_result.scalars().all())
        if not matched_mapping_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Selected identity mappings do not belong to the requested profile",
            )

    if fingerprint_ids:
        fingerprint_result = await db.execute(
            select(DeviceFingerprint.fingerprint_id).where(
                DeviceFingerprint.tenant_id == tenant_id,
                DeviceFingerprint.customer_id == customer.customer_id,
                DeviceFingerprint.fingerprint_id.in_(fingerprint_ids),
            )
        )
        matched_fingerprint_ids = set(fingerprint_result.scalars().all())
        if not matched_fingerprint_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Selected fingerprints do not belong to the requested profile",
            )

    new_customer = UnifiedCustomer(
        tenant_id=tenant_id,
        profile_confidence=0.5,
        primary_name=customer.primary_name,
        signal_profile=dict(customer.signal_profile or {}),
        consent_id=customer.consent_id,
    )
    db.add(new_customer)
    await db.flush()

    if matched_mapping_ids:
        await db.execute(
            update(IdentityMapping)
            .where(IdentityMapping.tenant_id == tenant_id, IdentityMapping.mapping_id.in_(matched_mapping_ids))
            .values(customer_id=new_customer.customer_id)
        )
    if matched_fingerprint_ids:
        await db.execute(
            update(DeviceFingerprint)
            .where(DeviceFingerprint.tenant_id == tenant_id, DeviceFingerprint.fingerprint_id.in_(matched_fingerprint_ids))
            .values(customer_id=new_customer.customer_id)
        )

    moved_mapping_count = len(matched_mapping_ids)
    moved_fingerprint_count = len(matched_fingerprint_ids)
    if moved_mapping_count + moved_fingerprint_count <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No identity signals were moved during split",
        )

    split_record = ProfileMergeHistory(
        tenant_id=tenant_id,
        source_customer_id=customer.customer_id,
        target_customer_id=new_customer.customer_id,
        merge_reason=f"split:{payload.split_reason}",
        merged_by="manual",
    )
    db.add(split_record)
    await db.commit()
    await delete_keys(
        [
            _customer_cache_key(tenant_id, payload.customer_id),
            _customer_cache_key(tenant_id, new_customer.customer_id),
        ]
    )

    await _emit_event_safe(
        tenant_id=tenant_id,
        event_type="identity.split",
        aggregate_customer_id=str(new_customer.customer_id),
        idempotency_key=f"identity.split:{split_record.merge_id}",
        payload={
            "split_id": str(split_record.merge_id),
            "source_customer_id": str(customer.customer_id),
            "new_customer_id": str(new_customer.customer_id),
            "split_reason": payload.split_reason,
            "mapping_ids": [str(item) for item in matched_mapping_ids],
            "fingerprint_ids": [str(item) for item in matched_fingerprint_ids],
            "split_at": split_record.merged_at.isoformat(),
        },
    )

    increment_counter("identity.split.completed", labels={"tenant_id": tenant_id})
    return {
        "split": True,
        "source_customer_id": payload.customer_id,
        "new_customer_id": str(new_customer.customer_id),
        "moved_mapping_count": moved_mapping_count,
        "moved_fingerprint_count": moved_fingerprint_count,
        "moved_mapping_ids": [str(item) for item in matched_mapping_ids],
        "moved_fingerprint_ids": [str(item) for item in matched_fingerprint_ids],
    }


async def list_review_queue(db: AsyncSession, tenant_id: str) -> list[ReviewQueueItemResponse]:
    result = await db.execute(
        select(ReviewQueue).where(ReviewQueue.tenant_id == tenant_id).order_by(ReviewQueue.created_at.desc())
    )
    rows = result.scalars().all()
    return [
        ReviewQueueItemResponse(
            review_id=str(row.review_id),
            resolution_id=str(row.resolution_id),
            source_customer_id=str(row.source_customer_id) if row.source_customer_id else None,
            candidate_customer_id=str(row.candidate_customer_id) if row.candidate_customer_id else None,
            status=row.status,
            source=str(row.source or "internal"),
            reason=row.reason,
            recommended_action=row.recommended_action,
            independent_signals=row.independent_signals,
            score_breakdown=row.score_breakdown,
            created_at=row.created_at,
            resolved_at=row.resolved_at,
            review_notes=row.review_notes,
        )
        for row in rows
    ]


async def resolve_review_item(
    db: AsyncSession,
    tenant_id: str,
    resolution_id: str,
    payload: ReviewDecisionRequest,
) -> dict[str, Any]:
    result = await db.execute(
        select(ReviewQueue).where(
            ReviewQueue.tenant_id == tenant_id,
            ReviewQueue.resolution_id == _to_uuid(resolution_id),
        )
    )
    review = result.scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")

    review.status = payload.action
    review.review_notes = payload.notes
    review.resolved_at = utcnow()

    merge_result = None
    if payload.action == "approve_merge" and review.candidate_customer_id:
        if not review.source_customer_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Review item no longer has a source profile available for merge",
            )
        merge_result = await merge_customers(
            db,
            tenant_id,
            MergeRequest(
                source_customer_id=str(review.source_customer_id),
                target_customer_id=str(review.candidate_customer_id),
                merge_reason=f"review_approved:{review.reason}",
            ),
            merged_by="manual",
        )
    else:
        await db.commit()

    return {
        "review_id": str(review.review_id),
        "resolution_id": resolution_id,
        "status": review.status,
        "merge_result": merge_result,
    }


async def accuracy_report(db: AsyncSession, tenant_id: str) -> AccuracyReportResponse:
    total_resolutions = await _count(
        db, select(func.count()).select_from(ResolutionAuditLog).where(ResolutionAuditLog.tenant_id == tenant_id)
    )
    deterministic_matches = await _count(
        db,
        select(func.count())
        .select_from(ResolutionAuditLog)
        .where(
            ResolutionAuditLog.tenant_id == tenant_id,
            ResolutionAuditLog.match_type == "deterministic",
        ),
    )
    probabilistic_matches = await _count(
        db,
        select(func.count())
        .select_from(ResolutionAuditLog)
        .where(
            ResolutionAuditLog.tenant_id == tenant_id,
            ResolutionAuditLog.match_type == "probabilistic",
        ),
    )
    ai_vector_matches = await _count(
        db,
        select(func.count())
        .select_from(ResolutionAuditLog)
        .where(
            ResolutionAuditLog.tenant_id == tenant_id,
            ResolutionAuditLog.match_type == "ai_vector",
        ),
    )
    review_queue_pending = await _count(
        db,
        select(func.count())
        .select_from(ReviewQueue)
        .where(ReviewQueue.tenant_id == tenant_id, ReviewQueue.status == "pending"),
    )
    auto_merges = await _count(
        db,
        select(func.count())
        .select_from(ProfileMergeHistory)
        .where(
            ProfileMergeHistory.tenant_id == tenant_id,
            ProfileMergeHistory.merged_by == "auto",
        ),
    )
    manual_merges = await _count(
        db,
        select(func.count())
        .select_from(ProfileMergeHistory)
        .where(
            ProfileMergeHistory.tenant_id == tenant_id,
            ProfileMergeHistory.merged_by == "manual",
        ),
    )
    splits = await _count(
        db,
        select(func.count())
        .select_from(ProfileMergeHistory)
        .where(
            ProfileMergeHistory.tenant_id == tenant_id,
            ProfileMergeHistory.merge_reason.like("split:%"),
        ),
    )

    precision_proxy = round((auto_merges + manual_merges) / max(auto_merges + manual_merges + splits, 1), 4)
    recall_proxy = round((deterministic_matches + manual_merges) / max(total_resolutions, 1), 4)
    false_positive_proxy = round(splits / max(auto_merges + manual_merges + splits, 1), 4)
    f1_proxy = round(
        2 * precision_proxy * recall_proxy / max(precision_proxy + recall_proxy, 0.0001),
        4,
    )
    return AccuracyReportResponse(
        tenant_id=tenant_id,
        total_resolutions=total_resolutions,
        deterministic_matches=deterministic_matches,
        probabilistic_matches=probabilistic_matches,
        ai_vector_matches=ai_vector_matches,
        review_queue_pending=review_queue_pending,
        auto_merges=auto_merges,
        manual_merges=manual_merges,
        splits=splits,
        precision_proxy=precision_proxy,
        recall_proxy=recall_proxy,
        false_positive_proxy=false_positive_proxy,
        f1_proxy=f1_proxy,
    )


def _first_customer_phone(customer: UnifiedCustomer) -> str | None:
    for mapping in customer.mappings or []:
        normalized = normalize_phone(mapping.phone)
        if normalized:
            return normalized
    profile = customer.signal_profile or {}
    for key in ("phone", "phone_number", "primary_phone", "whatsapp_phone", "mobile"):
        normalized = normalize_phone(profile.get(key))
        if normalized:
            return normalized
    return normalize_phone(customer.primary_identity)


def _first_customer_email(customer: UnifiedCustomer) -> str | None:
    for mapping in customer.mappings or []:
        normalized = normalize_email(mapping.email)
        if normalized:
            return normalized
    profile = customer.signal_profile or {}
    for key in ("email", "email_address", "primary_email"):
        normalized = normalize_email(profile.get(key))
        if normalized:
            return normalized
    return normalize_email(customer.primary_identity)


def _hydrate_candidate_hashes(customer: UnifiedCustomer, tenant_id: str) -> tuple[str | None, str | None]:
    phone = _first_customer_phone(customer)
    email = _first_customer_email(customer)
    phone_hash = customer.primary_phone_hash or hash_with_tenant_salt(tenant_id, phone)
    email_hash = customer.primary_email_hash or hash_with_tenant_salt(tenant_id, email)
    if phone_hash and not customer.primary_phone_hash:
        customer.primary_phone_hash = phone_hash
    if email_hash and not customer.primary_email_hash:
        customer.primary_email_hash = email_hash
    return phone_hash, email_hash


async def auto_detect_review_candidates(
    db: AsyncSession,
    tenant_id: str,
    *,
    max_suggestions: int = 50,
) -> dict[str, Any]:
    customers = await _load_probabilistic_candidates(db, tenant_id)
    for customer in customers:
        _hydrate_candidate_hashes(customer, tenant_id)
    logger.info("identity auto-detect candidates loaded tenant_id=%s total_customers=%s", tenant_id, len(customers))
    if len(customers) < 2:
        return {"new_suggestions": 0, "processed_customers": len(customers)}

    existing_result = await db.execute(
        select(
            ReviewQueue.source_customer_id,
            ReviewQueue.candidate_customer_id,
            ReviewQueue.status,
        ).where(
            ReviewQueue.tenant_id == tenant_id,
            ReviewQueue.status.in_(["pending", "monitor"]),
        )
    )
    existing_pairs = {
        tuple(
            sorted(
                [
                    str(row.source_customer_id),
                    str(row.candidate_customer_id),
                ]
            )
        )
        for row in existing_result.all()
        if row.candidate_customer_id
    }

    new_suggestions = 0
    for customer in customers:
        if new_suggestions >= max_suggestions:
            break

        primary_mapping = customer.mappings[0] if customer.mappings else None
        latest_fingerprint = customer.fingerprints[-1] if customer.fingerprints else None
        signal_profile = customer.signal_profile or {}
        source_phone = _first_customer_phone(customer)
        source_email = _first_customer_email(customer)
        source_phone_hash, source_email_hash = _hydrate_candidate_hashes(customer, tenant_id)
        payload = ResolveRequest(
            platform=(primary_mapping.platform if primary_mapping else "pulse_customer") or "pulse_customer",
            platform_user_id=(primary_mapping.platform_user_id if primary_mapping else str(customer.customer_id))
            or str(customer.customer_id),
            consent_token="auto_detect",
            phone_number=source_phone,
            email_address=source_email,
            full_name=(customer.primary_name or (primary_mapping.name if primary_mapping else "")) or None,
            username=(
                (primary_mapping.platform_username if primary_mapping else "") or signal_profile.get("username") or None
            ),
            profile_picture_phash=signal_profile.get("profile_picture_phash"),
            profile_picture_url=signal_profile.get("profile_picture_url"),
            description=signal_profile.get("description"),
            bio=signal_profile.get("bio"),
            company_name=signal_profile.get("company_name"),
            language=signal_profile.get("language"),
            locale=signal_profile.get("locale"),
            device_signals=latest_fingerprint.signals_json if latest_fingerprint else {},
            typing_speed=signal_profile.get("typing_speed"),
            message_patterns=signal_profile.get("message_patterns") or [],
            session_timing=signal_profile.get("session_timing") or [],
            cookie_id=signal_profile.get("cookie_id"),
        )

        best_candidate: CandidateScore | None = None
        best_pair: tuple[str, str] | None = None
        for candidate in customers:
            if candidate.customer_id == customer.customer_id:
                continue
            pair = tuple(sorted([str(customer.customer_id), str(candidate.customer_id)]))
            if pair in existing_pairs:
                continue
            scored = await _score_candidate(
                db,
                tenant_id,
                candidate,
                payload,
                source_phone_hash,
                source_email_hash,
                latest_fingerprint.fingerprint_hash if latest_fingerprint else None,
            )
            scored.breakdown.pop("_has_anchor", None)
            has_strong_anchor = any(
                signal in scored.independent_signals
                for signal in ("phone", "email", "channel_identity")
            ) and scored.score >= 450
            if not has_strong_anchor and (scored.score < 200 or len(scored.independent_signals) < 2):
                continue
            if best_candidate is None or scored.score > best_candidate.score:
                best_candidate = scored
                best_pair = pair

        if best_candidate is None or best_pair is None:
            continue

        input_signals = {
            "source": "auto_detect",
            "customer_id": str(customer.customer_id),
            "platform": payload.platform,
            "platform_user_id": payload.platform_user_id,
            "phone_hash": source_phone_hash,
            "email_hash": source_email_hash,
            "full_name": payload.full_name,
            "username": payload.username,
            "fingerprint_hash": latest_fingerprint.fingerprint_hash if latest_fingerprint else None,
        }
        score_payload = {
            "source": "auto_detect",
            "total_score": best_candidate.score,
            "confidence": score_to_confidence(best_candidate.score),
            "matched_fields": best_candidate.independent_signals,
            "match_reasons": best_candidate.independent_signals,
            "auto_detect": best_candidate.breakdown,
        }
        audit = await _write_audit(
            db,
            tenant_id,
            customer.customer_id,
            input_signals,
            score_payload,
            match_type=best_candidate.match_type,
            confidence=score_to_confidence(best_candidate.score),
            decision="review_required",
            decision_reason=best_candidate.decision_reason or "auto_detect_candidate",
            merge_performed=False,
            processing_ms=0,
        )
        await _create_review_item(
            db,
            tenant_id,
            audit.resolution_id,
            customer.customer_id,
            best_candidate.customer.customer_id,
            best_candidate.decision_reason or "auto_detect_candidate",
            score_payload,
            best_candidate.independent_signals,
            status="pending",
            source="auto_detect",
        )
        existing_pairs.add(best_pair)
        new_suggestions += 1

    logger.info(
        "identity auto-detect completed tenant_id=%s processed_customers=%s new_suggestions=%s",
        tenant_id,
        len(customers),
        new_suggestions,
    )
    return {"new_suggestions": new_suggestions, "processed_customers": len(customers)}


async def submit_public_unification(
    db: AsyncSession,
    tenant_id: str,
    payload: PublicUnificationRequest,
) -> PublicUnificationResponse:
    started = perf_counter()
    platform = normalize_text(payload.platform) or "web_chat"
    platform_user_id = str(payload.platform_user_id or "").strip()
    if not platform_user_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="platform_user_id is required")

    resolve_payload = ResolveRequest(
        platform=platform,
        platform_user_id=platform_user_id,
        consent_token="public_unification",
        phone_number=payload.phone_number,
        email_address=payload.email_address,
        full_name=payload.full_name,
            username=payload.username,
            profile_picture_phash=payload.profile_picture_phash,
            profile_picture_url=payload.profile_picture_url,
            description=payload.description,
            bio=payload.bio,
            company_name=payload.company_name,
            language=payload.language,
            locale=payload.locale,
        device_signals=payload.device_signals,
        typing_speed=payload.typing_speed,
        message_patterns=payload.message_patterns,
        session_timing=payload.session_timing,
        cookie_id=payload.cookie_id,
    )

    phone_hash = hash_with_tenant_salt(tenant_id, normalize_phone(resolve_payload.phone_number))
    email_hash = hash_with_tenant_salt(tenant_id, normalize_email(resolve_payload.email_address))
    fingerprint_hash = build_fingerprint_hash(resolve_payload.device_signals)

    source_mapping = await _get_identity_mapping(db, tenant_id, platform, platform_user_id)
    source_customer_id: uuid.UUID
    if source_mapping is not None:
        source_customer_id = source_mapping.customer_id
    else:
        source_customer = UnifiedCustomer(
            tenant_id=tenant_id,
            profile_confidence=0.0,
            primary_name=normalize_text(resolve_payload.full_name),
            primary_phone_hash=phone_hash,
            primary_email_hash=email_hash,
            signal_profile=_build_signal_profile(resolve_payload, fingerprint_hash),
        )
        db.add(source_customer)
        await db.flush()

        db.add(
            IdentityMapping(
                tenant_id=tenant_id,
                customer_id=source_customer.customer_id,
                platform=platform,
                platform_user_id=platform_user_id,
                platform_username=resolve_payload.username,
                confidence=0.0,
                is_primary_platform=True,
            )
        )
        if fingerprint_hash and resolve_payload.device_signals:
            db.add(
                DeviceFingerprint(
                    tenant_id=tenant_id,
                    customer_id=source_customer.customer_id,
                    fingerprint_hash=fingerprint_hash,
                    signals_json=resolve_payload.device_signals,
                )
            )
        await db.commit()
        source_customer_id = source_customer.customer_id

    best_candidate: CandidateScore | None = None
    candidates = await _load_probabilistic_candidates(db, tenant_id)
    for candidate in candidates:
        if candidate.customer_id == source_customer_id:
            continue
        scored = await _score_candidate(
            db,
            tenant_id,
            candidate,
            resolve_payload,
            phone_hash,
            email_hash,
            fingerprint_hash,
        )
        scored.breakdown.pop("_has_anchor", None)
        if best_candidate is None or scored.score > best_candidate.score:
            best_candidate = scored

    candidate_customer_id: uuid.UUID | None = None
    decision_reason = "public_submission_pending_review"
    score_breakdown: dict[str, Any] = {
        "source": PUBLIC_UNIFICATION_SOURCE,
        "platform": platform,
    }
    independent_signals: list[str] = ["public_submission"]
    confidence = 0.05
    match_type = "probabilistic"
    if best_candidate and best_candidate.score >= 100:
        candidate_customer_id = best_candidate.customer.customer_id
        decision_reason = best_candidate.decision_reason or "public_candidate_detected"
        score_breakdown = {
            "source": PUBLIC_UNIFICATION_SOURCE,
            "candidate": best_candidate.breakdown,
        }
        independent_signals = best_candidate.independent_signals or ["public_submission"]
        confidence = score_to_confidence(best_candidate.score)
        match_type = best_candidate.match_type

    input_signals = {
        "source": PUBLIC_UNIFICATION_SOURCE,
        "platform": platform,
        "platform_user_id": platform_user_id,
        "phone_hash": phone_hash,
        "email_hash": email_hash,
        "full_name": normalize_text(resolve_payload.full_name),
        "username": normalize_text(resolve_payload.username),
        "fingerprint_hash": fingerprint_hash,
    }

    audit = await _write_audit(
        db,
        tenant_id,
        source_customer_id,
        input_signals,
        score_breakdown,
        match_type=match_type,
        confidence=confidence,
        decision="review_required",
        decision_reason=decision_reason,
        merge_performed=False,
        processing_ms=int((perf_counter() - started) * 1000),
    )

    review_item = await _create_review_item(
        db,
        tenant_id,
        audit.resolution_id,
        source_customer_id,
        candidate_customer_id,
        decision_reason,
        score_breakdown,
        independent_signals,
        status="pending",
        source=PUBLIC_UNIFICATION_SOURCE,
    )

    await _cache_customer_lookups(
        db,
        tenant_id,
        source_customer_id,
        platform,
        platform_user_id,
        phone_hash,
        email_hash,
    )

    return PublicUnificationResponse(
        review_id=str(review_item.review_id),
        resolution_id=str(review_item.resolution_id),
        tenant_id=tenant_id,
        status="pending",
        source=PUBLIC_UNIFICATION_SOURCE,
        source_customer_id=str(source_customer_id),
        candidate_customer_id=str(candidate_customer_id) if candidate_customer_id else None,
        requires_admin_review=True,
        message="Unification request captured. Awaiting admin approval.",
    )


async def resolve_identity(db: AsyncSession, tenant_id: str, payload: ResolveRequest) -> ResolveResponse:
    start = perf_counter()
    increment_counter("identity.resolve.requests", labels={"tenant_id": tenant_id})
    logger.info(
        "identity resolution started tenant_id=%s platform=%s platform_user_id=%s",
        tenant_id,
        payload.platform,
        payload.platform_user_id,
    )
    consent = await validate_consent(db, tenant_id, payload.platform_user_id, payload.platform, payload.consent_token)

    phone_hash = hash_with_tenant_salt(tenant_id, normalize_phone(payload.phone_number))
    email_hash = hash_with_tenant_salt(tenant_id, normalize_email(payload.email_address))
    oauth_hash = hash_with_tenant_salt(tenant_id, payload.oauth_token_identity)
    fingerprint_hash = payload.device_fingerprint_id or build_fingerprint_hash(payload.device_signals)
    strong_sources = []
    candidate_ids: set[uuid.UUID] = set()

    cache_hits = []
    if phone_hash:
        cached_phone = await get_json(f"tenant:{tenant_id}:phone:{phone_hash}")
        if cached_phone:
            candidate_ids.add(_to_uuid(cached_phone["customer_id"]))
            cache_hits.append("phone")
    if email_hash:
        cached_email = await get_json(f"tenant:{tenant_id}:email:{email_hash}")
        if cached_email:
            candidate_ids.add(_to_uuid(cached_email["customer_id"]))
            cache_hits.append("email")
    cached_platform = await get_json(f"tenant:{tenant_id}:platform:{payload.platform}:{payload.platform_user_id}")
    if cached_platform:
        candidate_ids.add(_to_uuid(cached_platform["customer_id"]))
        cache_hits.append("platform_user_id")

    strong_match_sources = await _lookup_deterministic_matches(
        db,
        tenant_id,
        payload.platform,
        payload.platform_user_id,
        phone_hash,
        email_hash,
        oauth_hash,
    )
    for source_name, customer_ids in strong_match_sources.items():
        if customer_ids:
            strong_sources.append(source_name)
            candidate_ids.update(customer_ids)

    input_signals = {
        "platform": payload.platform,
        "platform_user_id": payload.platform_user_id,
        "phone_hash": phone_hash,
        "email_hash": email_hash,
        "oauth_hash": oauth_hash,
        "full_name": normalize_text(payload.full_name),
        "username": normalize_text(payload.username),
        "fingerprint_hash": fingerprint_hash,
        "locale": payload.locale,
        "language": payload.language,
        "ip_subnet": ip_subnet(payload.ip_address),
    }

    if len(candidate_ids) > 1:
        customer = await _create_customer_profile(
            db, tenant_id, payload, consent, fingerprint_hash, phone_hash, email_hash, score=0.55
        )
        audit = await _write_audit(
            db,
            tenant_id,
            customer.customer_id,
            input_signals,
            {"conflict_sources": strong_sources},
            match_type="probabilistic",
            confidence=0.55,
            decision="review_required",
            decision_reason="conflicting_strong_signals",
            merge_performed=False,
            processing_ms=int((perf_counter() - start) * 1000),
        )
        await _create_review_item(
            db,
            tenant_id,
            audit.resolution_id,
            customer.customer_id,
            None,
            "conflicting_strong_signals",
            {"conflict_sources": strong_sources},
            ["conflict"],
        )
        logger.info(
            "identity resolution flagged for review tenant_id=%s customer_id=%s reason=%s candidate_count=%s",
            tenant_id,
            customer.customer_id,
            "conflicting_strong_signals",
            len(candidate_ids),
        )
        return await _build_response_with_event(
            db=db,
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            audit=audit,
            is_new_user=True,
            merge_performed=False,
            platforms_linked=[payload.platform],
            data_sources_used=["conflict"],
            review_required=True,
            review_status="pending",
            request_payload=payload,
        )

    if len(candidate_ids) == 1:
        customer_id = next(iter(candidate_ids))
        customer = await _get_customer(db, tenant_id, customer_id, with_related=True)
        if customer is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cached customer missing")
        await _apply_customer_updates(
            db, customer, payload, consent, fingerprint_hash, phone_hash, email_hash, is_new_platform=True
        )
        audit = await _write_audit(
            db,
            tenant_id,
            customer.customer_id,
            input_signals,
            {"sources": strong_sources + cache_hits},
            match_type="deterministic",
            confidence=score_to_confidence(1000, deterministic=True),
            decision="matched_existing",
            decision_reason=(strong_sources + cache_hits)[0]
            if (strong_sources or cache_hits)
            else "deterministic_match",
            merge_performed=True,
            processing_ms=int((perf_counter() - start) * 1000),
        )
        await _cache_customer_lookups(
            db, tenant_id, customer.customer_id, payload.platform, payload.platform_user_id, phone_hash, email_hash
        )
        logger.info(
            "identity resolution matched existing deterministic tenant_id=%s customer_id=%s sources=%s",
            tenant_id,
            customer.customer_id,
            list(dict.fromkeys(strong_sources + cache_hits)),
        )
        return await _build_response_with_event(
            db=db,
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            audit=audit,
            is_new_user=False,
            merge_performed=True,
            platforms_linked=_platforms(customer),
            data_sources_used=list(dict.fromkeys(strong_sources + cache_hits)),
            review_required=False,
            review_status=None,
            request_payload=payload,
        )

    candidates = await _load_probabilistic_candidates(db, tenant_id)
    best: CandidateScore | None = None
    for candidate in candidates:
        scored = await _score_candidate(db, tenant_id, candidate, payload, phone_hash, email_hash, fingerprint_hash)
        if best is None or scored.score > best.score:
            best = scored

    if best and best.score < 500 and len(best.independent_signals) >= 2:
        vector_candidate = await _vector_search_boost(db, tenant_id, payload, candidates)
        if vector_candidate and (best is None or vector_candidate.score > best.score):
            best = vector_candidate

    # Auto-merge: score ≥ 500 + ≥ 3 independent signals + at least one
    # anchor signal (phone or email) to prevent false-positive merges from
    # behavioral signals alone.
    _has_anchor = best.breakdown.pop("_has_anchor", False) if best else False
    if best and best.score >= 500 and len(best.independent_signals) >= 3 and _has_anchor:
        await _apply_customer_updates(
            db, best.customer, payload, consent, fingerprint_hash, phone_hash, email_hash, is_new_platform=True
        )
        audit = await _write_audit(
            db,
            tenant_id,
            best.customer.customer_id,
            input_signals,
            best.breakdown,
            match_type=best.match_type,
            confidence=score_to_confidence(best.score),
            decision="high_confidence_merge",
            decision_reason=best.decision_reason,
            merge_performed=True,
            processing_ms=int((perf_counter() - start) * 1000),
        )
        await _cache_customer_lookups(
            db, tenant_id, best.customer.customer_id, payload.platform, payload.platform_user_id, phone_hash, email_hash
        )
        logger.info(
            "identity resolution merged high-confidence match tenant_id=%s customer_id=%s score=%s signals=%s",
            tenant_id,
            best.customer.customer_id,
            best.score,
            best.independent_signals,
        )
        return await _build_response_with_event(
            db=db,
            tenant_id=tenant_id,
            customer_id=best.customer.customer_id,
            audit=audit,
            is_new_user=False,
            merge_performed=True,
            platforms_linked=_platforms(best.customer),
            data_sources_used=best.data_sources_used,
            review_required=False,
            review_status=None,
            request_payload=payload,
        )

    customer = await _create_customer_profile(
        db,
        tenant_id,
        payload,
        consent,
        fingerprint_hash,
        phone_hash,
        email_hash,
        score=score_to_confidence(best.score if best else 0),
    )
    match_type = "probabilistic"
    review_required = False
    review_status = None
    decision = "new_user_created"
    decision_reason = "new_user"
    breakdown = {}
    data_sources_used: list[str] = []

    if best and best.score >= 200:
        review_required = True
        review_status = "pending"
        decision = "review_required"
        decision_reason = best.decision_reason
        breakdown = best.breakdown
        data_sources_used = best.data_sources_used
        match_type = best.match_type
    elif best and best.score >= 100 and len(best.independent_signals) >= 2:
        review_required = True
        review_status = "monitor"
        decision = "possible_match_monitor"
        decision_reason = best.decision_reason
        breakdown = best.breakdown
        data_sources_used = best.data_sources_used

    audit = await _write_audit(
        db,
        tenant_id,
        customer.customer_id,
        input_signals,
        breakdown,
        match_type=match_type,
        confidence=score_to_confidence(best.score if best else 0),
        decision=decision,
        decision_reason=decision_reason,
        merge_performed=False,
        processing_ms=int((perf_counter() - start) * 1000),
    )

    if review_required:
        await _create_review_item(
            db,
            tenant_id,
            audit.resolution_id,
            customer.customer_id,
            best.customer.customer_id if best else None,
            decision_reason,
            breakdown,
            best.independent_signals if best else [],
            status=review_status,
        )

    await _cache_customer_lookups(
        db, tenant_id, customer.customer_id, payload.platform, payload.platform_user_id, phone_hash, email_hash
    )
    logger.info(
        "identity resolution created profile tenant_id=%s customer_id=%s review_required=%s review_status=%s",
        tenant_id,
        customer.customer_id,
        review_required,
        review_status,
    )
    return await _build_response_with_event(
        db=db,
        tenant_id=tenant_id,
        customer_id=customer.customer_id,
        audit=audit,
        is_new_user=True,
        merge_performed=False,
        platforms_linked=[payload.platform],
        data_sources_used=data_sources_used,
        review_required=review_required,
        review_status=review_status,
        request_payload=payload,
    )


async def enrich_identity(db: AsyncSession, tenant_id: str, payload: EnrichRequest) -> dict[str, Any]:
    customer = await _get_customer(db, tenant_id, _to_uuid(payload.customer_id), with_related=True)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    proxy_payload = ResolveRequest(
        platform=payload.platform or (customer.mappings[0].platform if customer.mappings else "web"),
        platform_user_id=payload.platform_user_id
        or (customer.mappings[0].platform_user_id if customer.mappings else str(customer.customer_id)),
        consent_token="enrichment",
        phone_number=payload.phone_number,
        email_address=payload.email_address,
        full_name=payload.full_name,
        username=payload.username or payload.platform_username,
        profile_picture_phash=payload.profile_picture_phash,
        profile_picture_url=payload.profile_picture_url,
        description=payload.description,
        bio=payload.bio,
        company_name=payload.company_name,
        language=payload.language,
        locale=payload.locale,
        device_signals=payload.device_signals,
        typing_speed=payload.typing_speed,
        message_patterns=payload.message_patterns,
        session_timing=payload.session_timing,
        cookie_id=payload.cookie_id,
    )
    await _apply_customer_updates(
        db,
        customer,
        proxy_payload,
        None,
        build_fingerprint_hash(payload.device_signals),
        hash_with_tenant_salt(tenant_id, normalize_phone(payload.phone_number)),
        hash_with_tenant_salt(tenant_id, normalize_email(payload.email_address)),
        is_new_platform=bool(payload.platform and payload.platform_user_id),
    )
    return {"enriched": True, "customer_id": payload.customer_id}


async def validate_consent(
    db: AsyncSession,
    tenant_id: str,
    platform_user_id: str,
    platform: str,
    consent_token: str,
) -> ConsentLedger:
    consent = await _get_active_consent(db, tenant_id, platform_user_id, platform)
    if consent is None or consent.consent_token != consent_token:
        increment_counter(
            "identity.consent.validation_failed",
            labels={"tenant_id": tenant_id, "platform": platform, "reason": "missing_or_invalid"},
        )
        logger.warning(
            "identity consent validation failed tenant_id=%s platform=%s platform_user_id=%s reason=missing_or_invalid",
            tenant_id,
            platform,
            platform_user_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Consent missing or invalid")
    if not consent.granular_consent.get("consent_cross_platform_link"):
        increment_counter(
            "identity.consent.validation_failed",
            labels={"tenant_id": tenant_id, "platform": platform, "reason": "cross_platform_disabled"},
        )
        logger.warning(
            "identity consent validation failed tenant_id=%s platform=%s platform_user_id=%s reason=cross_platform_disabled",  # noqa: E501
            tenant_id,
            platform,
            platform_user_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-platform consent not granted")
    increment_counter(
        "identity.consent.validation_succeeded",
        labels={"tenant_id": tenant_id, "platform": platform},
    )
    return consent


async def _lookup_deterministic_matches(
    db: AsyncSession,
    tenant_id: str,
    platform: str,
    platform_user_id: str,
    phone_hash: str | None,
    email_hash: str | None,
    oauth_hash: str | None,
) -> dict[str, set[uuid.UUID]]:
    result: dict[str, set[uuid.UUID]] = {
        "platform_user_id": set(),
        "phone": set(),
        "email": set(),
        "oauth": set(),
    }

    mapping = await _get_identity_mapping(db, tenant_id, platform, platform_user_id)
    if mapping is not None:
        result["platform_user_id"].add(mapping.customer_id)

    if phone_hash:
        rows = await db.execute(
            select(UnifiedCustomer.customer_id).where(
                UnifiedCustomer.tenant_id == tenant_id,
                UnifiedCustomer.primary_phone_hash == phone_hash,
                UnifiedCustomer.is_active.is_(True),
            )
        )
        result["phone"] = {row[0] for row in rows.all()}
    if email_hash:
        rows = await db.execute(
            select(UnifiedCustomer.customer_id).where(
                UnifiedCustomer.tenant_id == tenant_id,
                UnifiedCustomer.primary_email_hash == email_hash,
                UnifiedCustomer.is_active.is_(True),
            )
        )
        result["email"] = {row[0] for row in rows.all()}
    if oauth_hash:
        rows = await db.execute(
            select(UnifiedCustomer.customer_id).where(
                UnifiedCustomer.tenant_id == tenant_id,
                UnifiedCustomer.signal_profile["oauth_hash"].astext == oauth_hash,
                UnifiedCustomer.is_active.is_(True),
            )
        )
        result["oauth"] = {row[0] for row in rows.all()}
    return result


async def _load_probabilistic_candidates(db: AsyncSession, tenant_id: str) -> list[UnifiedCustomer]:
    result = await db.execute(
        select(UnifiedCustomer)
        .options(selectinload(UnifiedCustomer.mappings), selectinload(UnifiedCustomer.fingerprints))
        .where(UnifiedCustomer.tenant_id == tenant_id, UnifiedCustomer.is_active.is_(True))
        .limit(200)
    )
    return result.scalars().all()


async def _score_candidate(
    db: AsyncSession,
    tenant_id: str,
    customer: UnifiedCustomer,
    payload: ResolveRequest,
    phone_hash: str | None,
    email_hash: str | None,
    fingerprint_hash: str | None,
) -> CandidateScore:
    candidate = CandidateScore(customer=customer)
    profile = customer.signal_profile or {}
    payload_phone = normalize_phone(payload.phone_number)
    payload_email = normalize_email(payload.email_address)

    if phone_hash and customer.primary_phone_hash == phone_hash:
        candidate.score += 500
        candidate.data_sources_used.append("phone")
        candidate.independent_signals.append("phone")
        candidate.breakdown["phone"] = 500
        candidate.decision_reason = "phone_hash_exact_match"
    if email_hash and customer.primary_email_hash == email_hash:
        candidate.score += 500
        candidate.data_sources_used.append("email")
        candidate.independent_signals.append("email")
        candidate.breakdown["email"] = 500
        candidate.decision_reason = "email_hash_exact_match"

    if "phone" not in candidate.independent_signals and payload_phone:
        for mapping in customer.mappings or []:
            if normalize_phone(mapping.phone) == payload_phone:
                candidate.score += 450
                candidate.data_sources_used.append("phone")
                candidate.independent_signals.append("phone")
                candidate.breakdown["phone_normalized"] = 450
                candidate.decision_reason = "phone_normalized_match"
                break

    if "email" not in candidate.independent_signals and payload_email:
        for mapping in customer.mappings or []:
            if normalize_email(mapping.email) == payload_email:
                candidate.score += 450
                candidate.data_sources_used.append("email")
                candidate.independent_signals.append("email")
                candidate.breakdown["email_normalized"] = 450
                candidate.decision_reason = "email_normalized_match"
                break

    if payload.platform and payload.platform_user_id:
        for mapping in customer.mappings or []:
            if (
                normalize_text(mapping.platform) == normalize_text(payload.platform)
                and str(mapping.platform_user_id or "").strip() == str(payload.platform_user_id or "").strip()
            ):
                candidate.score += 500
                candidate.data_sources_used.append("channel_identity")
                candidate.independent_signals.append("channel_identity")
                candidate.breakdown["channel_identity"] = {
                    "platform": payload.platform,
                    "score": 500,
                }
                candidate.decision_reason = "channel_identity_match"
                break

    latest_fingerprint = customer.fingerprints[-1] if customer.fingerprints else None
    if fingerprint_hash and latest_fingerprint and latest_fingerprint.fingerprint_hash == fingerprint_hash:
        candidate.score += 200
        candidate.data_sources_used.append("device_fp")
        candidate.independent_signals.append("device_fingerprint")
        candidate.breakdown["device_fingerprint"] = 200
        candidate.decision_reason = "device_fingerprint_match"
    elif payload.device_signals and latest_fingerprint:
        ratio, matched = partial_signal_match_ratio(payload.device_signals, latest_fingerprint.signals_json)
        if ratio >= 0.75:
            candidate.score += 200
            candidate.data_sources_used.append("device_fp")
            candidate.independent_signals.append("device_fingerprint")
            candidate.breakdown["device_fingerprint"] = {"score": 200, "ratio": round(ratio, 4), "matched": matched}
            candidate.decision_reason = "device_fingerprint_partial_match"

    name_similarity = fuzzy_name_similarity(payload.full_name, customer.primary_name)
    if name_similarity >= 0.92:
        candidate.score += 100
        candidate.data_sources_used.append("name")
        candidate.independent_signals.append("name")
        candidate.breakdown["name_similarity"] = round(name_similarity, 4)
        candidate.decision_reason = (
            candidate.decision_reason if candidate.decision_reason != "new_user" else "name_similarity_match"
        )

    username_score = username_similarity(payload.username, profile.get("username"))
    if username_score >= 0.90:
        candidate.score += 60
        candidate.data_sources_used.append("username")
        candidate.independent_signals.append("username")
        candidate.breakdown["username_similarity"] = round(username_score, 4)

    pic_distance = profile_picture_distance(payload.profile_picture_phash, profile.get("profile_picture_phash"))
    if pic_distance is not None and pic_distance <= 8:
        candidate.score += 80
        candidate.data_sources_used.append("profile_picture")
        candidate.independent_signals.append("profile_picture")
        candidate.breakdown["profile_picture"] = {"distance": pic_distance, "score": 80}

    payload_picture_url = normalize_text(payload.profile_picture_url)
    profile_picture_url = normalize_text(profile.get("profile_picture_url"))
    if payload_picture_url and profile_picture_url and payload_picture_url == profile_picture_url:
        candidate.score += 80
        candidate.data_sources_used.append("profile_picture")
        if "profile_picture" not in candidate.independent_signals:
            candidate.independent_signals.append("profile_picture")
        candidate.breakdown["profile_picture_url"] = 80

    description_left = normalize_text(payload.description) or normalize_text(payload.bio)
    description_right = normalize_text(profile.get("description")) or normalize_text(profile.get("bio"))
    description_score = username_similarity(description_left, description_right)
    if description_score >= 0.80:
        candidate.score += 40
        candidate.data_sources_used.append("description")
        candidate.independent_signals.append("description")
        candidate.breakdown["description_similarity"] = round(description_score, 4)

    company_score = fuzzy_name_similarity(payload.company_name, profile.get("company_name"))
    if company_score >= 0.88:
        candidate.score += 50
        candidate.data_sources_used.append("company")
        candidate.independent_signals.append("company")
        candidate.breakdown["company_similarity"] = round(company_score, 4)

    if ip_subnet(payload.ip_address) and ip_subnet(payload.ip_address) == profile.get("ip_subnet"):
        candidate.score += 40
        candidate.data_sources_used.append("ip")
        candidate.independent_signals.append("ip_subnet")
        candidate.breakdown["ip_subnet"] = 40

    if (
        payload.locale
        and payload.language
        and payload.locale == profile.get("locale")
        and payload.language == profile.get("language")
    ):
        candidate.score += 30
        candidate.data_sources_used.append("locale")
        candidate.independent_signals.append("locale")
        candidate.breakdown["locale"] = 30

    if payload.session_timing and profile.get("session_timing"):
        session_similarity = cosine_similarity(payload.session_timing, profile.get("session_timing"))
        if session_similarity >= 0.85:
            candidate.score += 25
            candidate.data_sources_used.append("session_timing")
            candidate.independent_signals.append("session_timing")
            candidate.breakdown["session_timing"] = round(session_similarity, 4)

    if typing_speed_within_range(payload.typing_speed, profile.get("typing_speed")):
        candidate.score += 20
        candidate.data_sources_used.append("typing_speed")
        candidate.independent_signals.append("typing_speed")
        candidate.breakdown["typing_speed"] = 20

    if payload.message_patterns and profile.get("message_patterns"):
        joined_left = " ".join(payload.message_patterns)
        joined_right = " ".join(profile.get("message_patterns"))
        style_similarity = username_similarity(joined_left, joined_right)
        if style_similarity >= 0.80:
            candidate.score += 15
            candidate.data_sources_used.append("message_style")
            candidate.independent_signals.append("message_style")
            candidate.breakdown["message_style"] = round(style_similarity, 4)

    candidate.data_sources_used = list(dict.fromkeys(candidate.data_sources_used))
    candidate.independent_signals = list(dict.fromkeys(candidate.independent_signals))

    # Safety gate: auto-merge requires at least one strong anchor signal
    # (phone or email hash) to prevent false-positive merges from behavioral
    # signals alone.  Set a flag so resolve_identity can check.
    has_anchor = "phone" in candidate.independent_signals or "email" in candidate.independent_signals
    candidate.breakdown["_has_anchor"] = has_anchor

    if candidate.score >= 500 and len(candidate.independent_signals) >= 3:
        candidate.match_type = "high_confidence_probabilistic" if has_anchor else "probabilistic"

    return candidate


async def _vector_search_boost(
    db: AsyncSession,
    tenant_id: str,
    payload: ResolveRequest,
    candidates: list[UnifiedCustomer],
) -> CandidateScore | None:
    if not _vector_enabled():
        return None

    embedding_text = build_embedding_text(payload.model_dump())
    if not embedding_text:
        return None
    vector = await embedding_service.encode(embedding_text)
    pg_literal = vector_to_pg_literal(vector)
    if not pg_literal:
        return None
    try:
        result = await db.execute(
            text(
                """
                SELECT customer_id::text AS customer_id,
                       1 - (embedding_vector <=> CAST(:embedding AS vector)) AS similarity
                FROM unified_customers
                WHERE tenant_id = :tenant_id
                  AND embedding_vector IS NOT NULL
                  AND is_active = true
                ORDER BY embedding_vector <=> CAST(:embedding AS vector)
                LIMIT 5
                """
            ),
            {"tenant_id": tenant_id, "embedding": pg_literal},
        )
        rows = result.mappings().all()
        if not rows:
            return None
        customer_map = {str(item.customer_id): item for item in candidates}
        for row in rows:
            similarity = float(row["similarity"])
            if similarity >= 0.92 and row["customer_id"] in customer_map:
                base = await _score_candidate(
                    db, tenant_id, customer_map[row["customer_id"]], payload, None, None, None
                )
                base.breakdown.pop("_has_anchor", None)
                base.score += 150
                base.match_type = "ai_vector"
                base.data_sources_used.append("ai_vector")
                base.independent_signals.append("ai_vector")
                base.breakdown["ai_vector"] = round(similarity, 4)
                base.decision_reason = "vector_similarity_boost"
                return base
    except Exception:
        return None
    return None


async def _create_customer_profile(
    db: AsyncSession,
    tenant_id: str,
    payload: ResolveRequest,
    consent: ConsentLedger,
    fingerprint_hash: str | None,
    phone_hash: str | None,
    email_hash: str | None,
    score: float,
) -> UnifiedCustomer:
    normalized_phone = normalize_phone(payload.phone_number)
    normalized_email = normalize_email(payload.email_address)
    normalized_name = normalize_text(payload.full_name)
    normalized_username = normalize_text(payload.username)
    signal_profile = _build_signal_profile(payload, fingerprint_hash)
    vector = await embedding_service.encode(build_embedding_text(payload.model_dump()))
    customer = UnifiedCustomer(
        tenant_id=tenant_id,
        profile_confidence=score,
        primary_identity=normalized_email or normalized_phone or payload.platform_user_id,
        primary_name=normalized_name,
        primary_phone_hash=phone_hash,
        primary_email_hash=email_hash,
        embedding_vector=vector,
        signal_profile=signal_profile,
        consent_id=consent.consent_id,
    )
    db.add(customer)
    await db.flush()

    mapping = IdentityMapping(
        tenant_id=tenant_id,
        customer_id=customer.customer_id,
        platform=payload.platform,
        platform_user_id=payload.platform_user_id,
        platform_username=normalized_username,
        phone=normalized_phone,
        email=normalized_email,
        name=normalized_name,
        fingerprint=fingerprint_hash,
        confidence=float(score),
        confidence_score=float(score),
        is_primary_platform=True,
    )
    db.add(mapping)

    if fingerprint_hash and payload.device_signals and consent.granular_consent.get("consent_device_tracking"):
        db.add(
            DeviceFingerprint(
                tenant_id=tenant_id,
                customer_id=customer.customer_id,
                fingerprint_hash=fingerprint_hash,
                signals_json=payload.device_signals,
            )
        )
    await db.commit()
    await db.refresh(customer)
    logger.info(
        "identity profile created tenant_id=%s customer_id=%s platform=%s platform_user_id=%s",
        tenant_id,
        customer.customer_id,
        payload.platform,
        payload.platform_user_id,
    )
    return customer


async def _apply_customer_updates(
    db: AsyncSession,
    customer: UnifiedCustomer,
    payload: ResolveRequest,
    consent: ConsentLedger | None,
    fingerprint_hash: str | None,
    phone_hash: str | None,
    email_hash: str | None,
    is_new_platform: bool,
):
    normalized_phone = normalize_phone(payload.phone_number)
    normalized_email = normalize_email(payload.email_address)
    normalized_name = normalize_text(payload.full_name)
    normalized_username = normalize_text(payload.username)
    customer.updated_at = utcnow()
    if normalized_email and not customer.primary_identity:
        customer.primary_identity = normalized_email
    elif normalized_phone and not customer.primary_identity:
        customer.primary_identity = normalized_phone
    elif payload.platform_user_id and not customer.primary_identity:
        customer.primary_identity = payload.platform_user_id
    if phone_hash and not customer.primary_phone_hash:
        customer.primary_phone_hash = phone_hash
    if email_hash and not customer.primary_email_hash:
        customer.primary_email_hash = email_hash
    if normalized_name:
        customer.primary_name = normalized_name
    signal_profile = {**(customer.signal_profile or {}), **_build_signal_profile(payload, fingerprint_hash)}
    if payload.oauth_token_identity:
        signal_profile["oauth_hash"] = hash_with_tenant_salt(customer.tenant_id, payload.oauth_token_identity)
    customer.signal_profile = signal_profile
    if consent and not customer.consent_id:
        customer.consent_id = consent.consent_id

    vector_payload = build_embedding_text(payload.model_dump())
    if vector_payload:
        customer.embedding_vector = await embedding_service.encode(vector_payload)

    mapping = await _get_identity_mapping(db, customer.tenant_id, payload.platform, payload.platform_user_id)
    if mapping is None:
        db.add(
            IdentityMapping(
                tenant_id=customer.tenant_id,
                customer_id=customer.customer_id,
                platform=payload.platform,
                platform_user_id=payload.platform_user_id,
                platform_username=normalized_username,
                phone=normalized_phone,
                email=normalized_email,
                name=normalized_name,
                fingerprint=fingerprint_hash,
                confidence=customer.profile_confidence,
                confidence_score=customer.profile_confidence,
                is_primary_platform=not customer.mappings,
            )
        )
    else:
        if normalized_username:
            mapping.platform_username = normalized_username
        if normalized_phone:
            mapping.phone = normalized_phone
        if normalized_email:
            mapping.email = normalized_email
        if normalized_name:
            mapping.name = normalized_name
        if fingerprint_hash:
            mapping.fingerprint = fingerprint_hash
        mapping.confidence_score = customer.profile_confidence

    if fingerprint_hash and payload.device_signals:
        result = await db.execute(
            select(DeviceFingerprint).where(
                DeviceFingerprint.tenant_id == customer.tenant_id,
                DeviceFingerprint.customer_id == customer.customer_id,
                DeviceFingerprint.fingerprint_hash == fingerprint_hash,
            )
        )
        existing_fp = result.scalar_one_or_none()
        if existing_fp:
            existing_fp.last_seen = utcnow()
            existing_fp.match_count += 1
            existing_fp.signals_json = payload.device_signals
        else:
            db.add(
                DeviceFingerprint(
                    tenant_id=customer.tenant_id,
                    customer_id=customer.customer_id,
                    fingerprint_hash=fingerprint_hash,
                    signals_json=payload.device_signals,
                )
            )
    await db.commit()
    await db.refresh(customer)
    logger.info(
        "identity profile updated tenant_id=%s customer_id=%s platform=%s platform_user_id=%s is_new_platform=%s",
        customer.tenant_id,
        customer.customer_id,
        payload.platform,
        payload.platform_user_id,
        is_new_platform,
    )


async def _write_audit(
    db: AsyncSession,
    tenant_id: str,
    customer_id: uuid.UUID,
    input_signals: dict[str, Any],
    score_breakdown: dict[str, Any],
    match_type: str,
    confidence: float,
    decision: str,
    decision_reason: str,
    merge_performed: bool,
    processing_ms: int,
) -> ResolutionAuditLog:
    audit = ResolutionAuditLog(
        tenant_id=tenant_id,
        customer_id=customer_id,
        input_signals=serialize_any(input_signals),
        score_breakdown=serialize_any(score_breakdown),
        match_type=match_type,
        confidence=confidence,
        decision=decision,
        decision_reason=decision_reason,
        merge_performed=merge_performed,
        processing_ms=processing_ms,
    )
    db.add(audit)
    await db.commit()
    await db.refresh(audit)
    return audit


async def _create_review_item(
    db: AsyncSession,
    tenant_id: str,
    resolution_id: uuid.UUID,
    source_customer_id: uuid.UUID,
    candidate_customer_id: uuid.UUID | None,
    reason: str,
    score_breakdown: dict[str, Any],
    independent_signals: list[str],
    status: str = "pending",
    source: str = "internal",
):
    item = ReviewQueue(
        tenant_id=tenant_id,
        resolution_id=resolution_id,
        source_customer_id=source_customer_id,
        candidate_customer_id=candidate_customer_id,
        source=source,
        reason=reason,
        recommended_action="review",
        score_breakdown=serialize_any(score_breakdown),
        independent_signals=independent_signals,
        status=status,
    )
    db.add(item)
    await db.commit()
    return item


async def _build_response(
    db: AsyncSession,
    customer_id: uuid.UUID,
    audit: ResolutionAuditLog,
    is_new_user: bool,
    merge_performed: bool,
    platforms_linked: list[str],
    data_sources_used: list[str],
    review_required: bool,
    review_status: str | None,
) -> ResolveResponse:
    return ResolveResponse(
        customer_id=str(customer_id),
        confidence_score=audit.confidence,
        match_type=audit.match_type,
        is_new_user=is_new_user,
        platforms_linked=list(dict.fromkeys(platforms_linked)),
        merge_performed=merge_performed,
        consent_verified=True,
        data_sources_used=list(dict.fromkeys(data_sources_used)),
        resolved_at=audit.created_at,
        review_required=review_required,
        review_status=review_status,
        audit_resolution_id=str(audit.resolution_id),
        decision_reason=audit.decision_reason,
        score_breakdown=audit.score_breakdown,
    )


async def _cache_customer_lookups(
    db: AsyncSession,
    tenant_id: str,
    customer_id: uuid.UUID,
    platform: str,
    platform_user_id: str,
    phone_hash: str | None,
    email_hash: str | None,
):
    payload = {"customer_id": str(customer_id)}
    await set_json(f"tenant:{tenant_id}:platform:{platform}:{platform_user_id}", payload, ttl_seconds=300)
    if phone_hash:
        await set_json(f"tenant:{tenant_id}:phone:{phone_hash}", payload, ttl_seconds=300)
    if email_hash:
        await set_json(f"tenant:{tenant_id}:email:{email_hash}", payload, ttl_seconds=300)


async def _get_active_consent(
    db: AsyncSession,
    tenant_id: str,
    platform_user_id: str,
    platform: str,
) -> ConsentLedger | None:
    result = await db.execute(
        select(ConsentLedger)
        .where(
            ConsentLedger.tenant_id == tenant_id,
            ConsentLedger.platform_user_id == platform_user_id,
            ConsentLedger.platform == platform,
            ConsentLedger.consent_given.is_(True),
            ConsentLedger.revoked_at.is_(None),
        )
        .order_by(ConsentLedger.consent_timestamp.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_identity_mapping(
    db: AsyncSession, tenant_id: str, platform: str, platform_user_id: str
) -> IdentityMapping | None:
    result = await db.execute(
        select(IdentityMapping).where(
            IdentityMapping.tenant_id == tenant_id,
            IdentityMapping.platform == platform,
            IdentityMapping.platform_user_id == platform_user_id,
        )
    )
    return result.scalar_one_or_none()


async def _get_customer(
    db: AsyncSession,
    tenant_id: str,
    customer_id: uuid.UUID,
    with_related: bool = False,
) -> UnifiedCustomer | None:
    stmt = select(UnifiedCustomer).where(
        UnifiedCustomer.tenant_id == tenant_id,
        UnifiedCustomer.customer_id == customer_id,
    )
    if with_related:
        stmt = stmt.options(selectinload(UnifiedCustomer.mappings), selectinload(UnifiedCustomer.fingerprints))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _count(db: AsyncSession, stmt):
    result = await db.execute(stmt)
    return int(result.scalar() or 0)


def _build_signal_profile(payload: ResolveRequest, fingerprint_hash: str | None) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    if payload.username:
        profile["username"] = normalize_text(payload.username)
    if payload.language:
        profile["language"] = payload.language
    if payload.locale:
        profile["locale"] = payload.locale
    if payload.profile_picture_phash:
        profile["profile_picture_phash"] = payload.profile_picture_phash
    if payload.profile_picture_url:
        profile["profile_picture_url"] = str(payload.profile_picture_url).strip()
    if payload.description:
        profile["description"] = normalize_text(payload.description)
    if payload.bio:
        profile["bio"] = normalize_text(payload.bio)
    if payload.company_name:
        profile["company_name"] = normalize_text(payload.company_name)
    if payload.typing_speed is not None:
        profile["typing_speed"] = payload.typing_speed
    if payload.session_timing:
        profile["session_timing"] = payload.session_timing
    if payload.message_patterns:
        profile["message_patterns"] = payload.message_patterns[:10]
    if payload.cookie_id:
        profile["cookie_id"] = payload.cookie_id
    if payload.ip_address:
        profile["ip_subnet"] = ip_subnet(payload.ip_address)
    if fingerprint_hash:
        profile["latest_fingerprint_hash"] = fingerprint_hash
    return profile


def _mapping_out(item: IdentityMapping) -> IdentityMappingOut:
    return IdentityMappingOut(
        mapping_id=str(item.mapping_id),
        platform=item.platform,
        platform_user_id=item.platform_user_id,
        platform_username=item.platform_username,
        phone=item.phone,
        email=item.email,
        name=item.name,
        fingerprint=item.fingerprint,
        confidence=item.confidence,
        confidence_score=item.confidence_score,
        linked_at=item.linked_at,
        is_primary_platform=item.is_primary_platform,
    )


def _fingerprint_out(item: DeviceFingerprint) -> DeviceFingerprintOut:
    return DeviceFingerprintOut(
        fingerprint_id=str(item.fingerprint_id),
        fingerprint_hash=item.fingerprint_hash,
        first_seen=item.first_seen,
        last_seen=item.last_seen,
        match_count=item.match_count,
        signals_json=item.signals_json,
    )


def _platforms(customer: UnifiedCustomer) -> list[str]:
    return [mapping.platform for mapping in customer.mappings] if customer.mappings else []


def _to_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
