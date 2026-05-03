from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GranularConsent(BaseModel):
    consent_device_tracking: bool = False
    consent_behavioral_analysis: bool = False
    consent_cross_platform_link: bool = True
    consent_profile_picture: bool = False
    consent_data_retention_days: int = 365


class ConsentGrantRequest(BaseModel):
    platform_user_id: str
    platform: str
    consent_version: str
    consent_method: Literal["checkbox", "oauth", "verbal_recorded"]
    ip_address: str | None = None
    granular_consent: GranularConsent = Field(default_factory=GranularConsent)


class ConsentRevokeRequest(BaseModel):
    platform_user_id: str
    platform: str
    reason: str | None = None
    purge_linked_data: bool = False


class ConsentResponse(BaseModel):
    consent_id: str
    consent_token: str
    consent_given: bool
    platform_user_id: str
    platform: str
    status: str
    consent_timestamp: datetime
    revoked_at: datetime | None = None
    granular_consent: GranularConsent


class ConsentStatusResponse(BaseModel):
    platform_user_id: str
    platform: str
    consent_active: bool
    consent_token: str | None = None
    consent_version: str | None = None
    granted_at: datetime | None = None
    revoked_at: datetime | None = None
    granular_consent: dict[str, Any] = Field(default_factory=dict)


class ResolveRequest(BaseModel):
    platform: str
    platform_user_id: str
    consent_token: str
    phone_number: str | None = None
    email_address: str | None = None
    oauth_token_identity: str | None = None
    full_name: str | None = None
    username: str | None = None
    profile_picture_phash: str | None = None
    profile_picture_url: str | None = None
    description: str | None = None
    bio: str | None = None
    company_name: str | None = None
    language: str | None = None
    locale: str | None = None
    device_fingerprint_id: str | None = None
    device_signals: dict[str, Any] = Field(default_factory=dict)
    ip_address: str | None = None
    typing_speed: float | None = None
    message_patterns: list[str] = Field(default_factory=list)
    session_timing: list[float] = Field(default_factory=list)
    cookie_id: str | None = None


class PublicUnificationRequest(BaseModel):
    platform: str = "web_chat"
    platform_user_id: str
    phone_number: str | None = None
    email_address: str | None = None
    full_name: str | None = None
    username: str | None = None
    profile_picture_phash: str | None = None
    profile_picture_url: str | None = None
    description: str | None = None
    bio: str | None = None
    company_name: str | None = None
    language: str | None = None
    locale: str | None = None
    device_signals: dict[str, Any] = Field(default_factory=dict)
    typing_speed: float | None = None
    message_patterns: list[str] = Field(default_factory=list)
    session_timing: list[float] = Field(default_factory=list)
    cookie_id: str | None = None


class PublicUnificationResponse(BaseModel):
    review_id: str
    resolution_id: str
    tenant_id: str
    status: str
    source: str
    source_customer_id: str
    candidate_customer_id: str | None = None
    requires_admin_review: bool = True
    message: str = "Unification request captured for admin review"


class ResolveResponse(BaseModel):
    customer_id: str
    confidence_score: float
    match_type: Literal["deterministic", "probabilistic", "high_confidence_probabilistic", "ai_vector"]
    is_new_user: bool
    platforms_linked: list[str]
    merge_performed: bool
    consent_verified: bool
    data_sources_used: list[str]
    resolved_at: datetime
    review_required: bool = False
    review_status: str | None = None
    audit_resolution_id: str
    decision_reason: str
    score_breakdown: dict[str, Any] = Field(default_factory=dict)


class EnrichRequest(BaseModel):
    customer_id: str
    platform: str | None = None
    platform_user_id: str | None = None
    platform_username: str | None = None
    phone_number: str | None = None
    email_address: str | None = None
    full_name: str | None = None
    username: str | None = None
    profile_picture_phash: str | None = None
    profile_picture_url: str | None = None
    description: str | None = None
    bio: str | None = None
    company_name: str | None = None
    language: str | None = None
    locale: str | None = None
    device_signals: dict[str, Any] = Field(default_factory=dict)
    typing_speed: float | None = None
    message_patterns: list[str] = Field(default_factory=list)
    session_timing: list[float] = Field(default_factory=list)
    cookie_id: str | None = None


class MergeRequest(BaseModel):
    source_customer_id: str
    target_customer_id: str
    merge_reason: str


class SplitRequest(BaseModel):
    customer_id: str
    identity_mapping_ids: list[str] = Field(default_factory=list)
    fingerprint_ids: list[str] = Field(default_factory=list)
    split_reason: str


class FingerprintRegisterRequest(BaseModel):
    customer_id: str | None = None
    platform_user_id: str
    platform: str
    consent_token: str
    signals: dict[str, Any] = Field(default_factory=dict)


class FingerprintMatchRequest(BaseModel):
    customer_id: str | None = None
    fingerprint_hash: str | None = None
    signals: dict[str, Any] = Field(default_factory=dict)


class FingerprintMatchResult(BaseModel):
    customer_id: str
    fingerprint_id: str
    exact_match: bool
    match_score: float
    matched_signals: list[str] = Field(default_factory=list)


class AdminLoginRequest(BaseModel):
    email: str
    password: str


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    email: str


class ReviewDecisionRequest(BaseModel):
    action: Literal["approve_merge", "reject", "keep_separate"]
    notes: str | None = None


class IdentityMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mapping_id: str
    platform: str
    platform_user_id: str
    platform_username: str | None = None
    phone: str | None = None
    email: str | None = None
    name: str | None = None
    fingerprint: str | None = None
    confidence: float
    confidence_score: float | None = None
    linked_at: datetime
    is_primary_platform: bool


class DeviceFingerprintOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fingerprint_id: str
    fingerprint_hash: str
    first_seen: datetime
    last_seen: datetime
    match_count: int
    signals_json: dict[str, Any]


class IdentityProfileResponse(BaseModel):
    customer_id: str
    tenant_id: str
    is_active: bool
    profile_confidence: float
    primary_name: str | None = None
    primary_phone_hash: str | None = None
    primary_email_hash: str | None = None
    signal_profile: dict[str, Any] = Field(default_factory=dict)
    consent_id: str | None = None
    created_at: datetime
    updated_at: datetime
    mappings: list[IdentityMappingOut] = Field(default_factory=list)
    fingerprints: list[DeviceFingerprintOut] = Field(default_factory=list)
    merge_history: list[dict[str, Any]] = Field(default_factory=list)


class ReviewQueueItemResponse(BaseModel):
    review_id: str
    resolution_id: str
    source_customer_id: str | None = None
    candidate_customer_id: str | None = None
    status: str
    source: str = "internal"
    reason: str
    recommended_action: str
    independent_signals: list[str]
    score_breakdown: dict[str, Any]
    created_at: datetime
    resolved_at: datetime | None = None
    review_notes: str | None = None


class AccuracyReportResponse(BaseModel):
    tenant_id: str
    total_resolutions: int
    deterministic_matches: int
    probabilistic_matches: int
    ai_vector_matches: int
    review_queue_pending: int
    auto_merges: int
    manual_merges: int
    splits: int
    precision_proxy: float
    recall_proxy: float
    false_positive_proxy: float
    f1_proxy: float


class IdentityResolveRequest(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)


class IdentityResolveResponse(BaseModel):
    customer_id: str
    confidence: float = Field(ge=0.0, le=1.0)
