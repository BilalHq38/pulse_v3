import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.identity_service.app.db.database import Base

try:
    from pgvector.sqlalchemy import Vector
except Exception:  # pragma: no cover - optional dependency guard
    Vector = None


def utcnow():
    return datetime.now(timezone.utc)


def _vector_enabled() -> bool:
    return (os.environ.get("IDENTITY_USE_VECTOR", "false") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


_EMBEDDING_COLUMN_TYPE = Vector(384) if (_vector_enabled() and Vector is not None) else JSONB


def _default_tenant_id() -> str:
    configured = (os.environ.get("DEFAULT_TENANT_ID") or "").strip()
    return configured or "demo_tenant"


class UnifiedCustomer(Base):
    __tablename__ = "unified_customers"

    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=True, index=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    profile_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    primary_identity: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_phone_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    primary_email_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    embedding_vector: Mapped[list[float] | None] = mapped_column(_EMBEDDING_COLUMN_TYPE, nullable=True)
    signal_profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    consent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("consent_ledger.consent_id"), nullable=True
    )

    mappings: Mapped[list["IdentityMapping"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    fingerprints: Mapped[list["DeviceFingerprint"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class IdentityMapping(Base):
    __tablename__ = "identity_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "platform", "platform_user_id", name="uq_identity_mapping_platform_user"),
    )

    mapping_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=True, index=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("unified_customers.customer_id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(32), index=True)
    platform_user_id: Mapped[str] = mapped_column(Text)
    platform_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_primary_platform: Mapped[bool] = mapped_column(Boolean, default=False)

    customer: Mapped[UnifiedCustomer] = relationship(back_populates="mappings")


class DeviceFingerprint(Base):
    __tablename__ = "device_fingerprints"

    fingerprint_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("unified_customers.customer_id", ondelete="CASCADE"), index=True
    )
    fingerprint_hash: Mapped[str] = mapped_column(String(128), index=True)
    signals_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    match_count: Mapped[int] = mapped_column(Integer, default=1)

    customer: Mapped[UnifiedCustomer] = relationship(back_populates="fingerprints")


class ConsentLedger(Base):
    __tablename__ = "consent_ledger"

    consent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    platform_user_id: Mapped[str] = mapped_column(Text, index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    consent_given: Mapped[bool] = mapped_column(Boolean, default=True)
    consent_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    consent_version: Mapped[str] = mapped_column(String(64))
    consent_method: Mapped[str] = mapped_column(String(32))
    consent_token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    granular_consent: Mapped[dict] = mapped_column(JSONB, default=dict)
    data_retention_days: Mapped[int] = mapped_column(Integer, default=365)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResolutionAuditLog(Base):
    __tablename__ = "resolution_audit_log"

    resolution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unified_customers.customer_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    input_signals: Mapped[dict] = mapped_column(JSONB, default=dict)
    score_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict)
    match_type: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    decision: Mapped[str] = mapped_column(Text)
    decision_reason: Mapped[str] = mapped_column(Text)
    processing_ms: Mapped[int] = mapped_column(Integer, default=0)
    merge_performed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProfileMergeHistory(Base):
    __tablename__ = "profile_merge_history"

    merge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    source_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unified_customers.customer_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    target_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unified_customers.customer_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    merge_reason: Mapped[str] = mapped_column(Text)
    merged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    merged_by: Mapped[str] = mapped_column(String(32), default="auto")


class ReviewQueue(Base):
    __tablename__ = "review_queue"

    review_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    resolution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resolution_audit_log.resolution_id", ondelete="CASCADE"),
        index=True,
    )
    source_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unified_customers.customer_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    candidate_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unified_customers.customer_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    source: Mapped[str] = mapped_column(String(64), default="internal", index=True)
    reason: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str] = mapped_column(String(32), default="review")
    score_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict)
    independent_signals: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdentityEvent(Base):
    __tablename__ = "identity_events"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_identity_events_tenant_idempotency"),)

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    aggregate_customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(191), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class EventOutbox(Base):
    __tablename__ = "event_outbox"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_event_outbox_tenant_idempotency"),)

    outbox_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identity_events.event_id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    stream_name: Mapped[str] = mapped_column(String(128), default="identity.events")
    idempotency_key: Mapped[str | None] = mapped_column(String(191), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class IdentityHistory(Base):
    __tablename__ = "identity_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, default=_default_tenant_id)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("unified_customers.customer_id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(Text)
    data_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ConsentRecord(Base):
    __tablename__ = "consent_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, default=_default_tenant_id)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("unified_customers.customer_id", ondelete="SET NULL"), nullable=True, index=True
    )
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
