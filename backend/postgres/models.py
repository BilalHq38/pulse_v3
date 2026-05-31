from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    perm_scope: Mapped[str] = mapped_column(String(64), default="company", nullable=False)
    perm_all: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_name: Mapped[str] = mapped_column(String(255), default="", nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    industry: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="active", nullable=False)
    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    subscription_status: Mapped[str] = mapped_column(String(32), default="inactive", nullable=False)
    billing_status: Mapped[str] = mapped_column(String(32), default="inactive", nullable=False)
    stripe_subscription_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password: Mapped[str] = mapped_column(Text, default="", nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(64), default="admin", nullable=False)
    role_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("roles.id"), nullable=True, index=True)
    sub_role: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="active", nullable=False)
    avatar: Mapped[str] = mapped_column(Text, default="", nullable=False)
    company_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("companies.id"), nullable=True, index=True)
    phone: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    plan_selected: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    billing_status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    auth_provider: Mapped[str] = mapped_column(String(32), default="email", nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class LoginHistory(Base):
    __tablename__ = "login_history"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), default="", nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    user_agent: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_activity: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    __table_args__ = (Index("ix_login_sessions_user_active", "user_id", "is_active"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    device: Mapped[str] = mapped_column(Text, default="", nullable=False)
    session_token: Mapped[str] = mapped_column(String(255), default="", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), default="active", nullable=False)
    login_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    logout_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class AuthLog(Base):
    __tablename__ = "auth_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    device: Mapped[str] = mapped_column(Text, default="", nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email: Mapped[str] = mapped_column(String(320), default="", nullable=False, index=True)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class LeadStatus(Base):
    __tablename__ = "lead_statuses"
    __table_args__ = (
        Index("ix_lead_statuses_company_status", "company_id", "status_name"),
        Index("ix_lead_statuses_company_order", "company_id", "order_index"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    status_name: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (Index("ix_sources_company_name", "company_id", "source_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    platform: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_company_stage", "company_id", "lifecycle_stage"),
        Index("ix_customers_company_segment", "company_id", "segment"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    lead_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), default="", nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    company: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    channels: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    segment: Mapped[str] = mapped_column(String(64), default="general", nullable=False)
    avatar: Mapped[str] = mapped_column(Text, default="", nullable=False)
    lifecycle_stage: Mapped[str] = mapped_column(String(64), default="lead", nullable=False, index=True)
    lifetime_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_sentiment: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    recent_tickets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    complaint_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    days_since_last_contact: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_conversations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class CustomerProfile(Base):
    __tablename__ = "customer_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    behavioral_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    engagement_level: Mapped[str] = mapped_column(String(64), default="general", nullable=False)
    last_interaction: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_company_status", "company_id", "status"),
        Index("ix_leads_company_source", "company_id", "source"),
        Index("ix_leads_company_grade", "company_id", "grade"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), default="", nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    company: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(64), default="new", nullable=False)
    status_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    score: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    grade: Mapped[str] = mapped_column(String(32), default="warm", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    assigned_to: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    assigned_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    activities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    scoring_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    next_action: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Purchase(Base):
    __tablename__ = "purchases"
    __table_args__ = (Index("ix_purchases_company_date", "company_id", "purchase_date"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(16), default="USD", nullable=False)
    product_category: Mapped[str] = mapped_column(String(64), default="general", nullable=False)
    product_details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    purchase_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (
        Index("ix_channels_company_platform", "company_id", "platform"),
        Index("ix_channels_company_name", "company_id", "channel_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    channel_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    channel_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    platform: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class TicketStatus(Base):
    __tablename__ = "ticket_statuses"
    __table_args__ = (Index("ix_ticket_statuses_company_status", "company_id", "status_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    status_name: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    color_code: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_company_status", "company_id", "status"),
        Index("ix_conversations_company_channel", "company_id", "channel"),
        Index("ix_conversations_customer", "customer_id"),
        Index("ix_conversations_last_message_at", "last_message_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    customer_avatar: Mapped[str] = mapped_column(Text, default="", nullable=False)
    channel: Mapped[str] = mapped_column(String(64), default="web_chat", nullable=False)
    channel_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="open", nullable=False)
    priority: Mapped[str] = mapped_column(String(32), default="medium", nullable=False)
    assigned_to: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    assigned_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    ai_handled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ai_auto_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ai_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_paused_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ai_paused_error_type: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    ai_paused_provider: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    ai_paused_model: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    ai_paused_scope: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    ai_disabled_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sentiment_label: Mapped[str] = mapped_column(String(64), default="neutral", nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    escalation_notice: Mapped[str] = mapped_column(Text, default="", nullable=False)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_to: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    escalated_to_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation", "conversation_id"),
        Index("ix_messages_company_created", "company_id", "created_at"),
        Index("ix_messages_sender_type", "sender_type"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sender_type: Mapped[str] = mapped_column(String(32), default="agent", nullable=False)
    sender_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    sender_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), default="", nullable=False, index=True)
    attachments: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    sentiment: Mapped[object | None] = mapped_column(JSON, nullable=True)
    intent: Mapped[object | None] = mapped_column(JSON, nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        Index("ix_tickets_company_status", "company_id", "status"),
        Index("ix_tickets_company_priority", "company_id", "priority"),
        Index("ix_tickets_conversation", "conversation_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_number: Mapped[str] = mapped_column(String(128), default="", nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    priority: Mapped[str] = mapped_column(String(32), default="medium", nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="general", nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="open", nullable=False)
    status_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    assigned_to: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    assigned_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    resolution: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sla_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    notes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ConversationLog(Base):
    __tablename__ = "conversation_logs"
    __table_args__ = (Index("ix_conversation_logs_convo_logged", "convo_id", "logged_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    convo_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    logged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class MessageAttachment(Base):
    __tablename__ = "message_attachments"
    __table_args__ = (Index("ix_message_attachments_message", "message_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    file_type: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    file_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    file_name: Mapped[str] = mapped_column(Text, default="", nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    storage_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    thumbnail_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    provider_media_id: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    raw_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    image_analysis_status: Mapped[str] = mapped_column(String(32), default="skipped", nullable=False, index=True)
    image_analysis_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_detected_objects: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    image_ocr_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_analysis_model: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    image_analysis_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class CompanySetting(Base):
    __tablename__ = "company_settings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(255), default="", nullable=False, index=True)
    industry: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    language: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ai_confidence_threshold: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    ai_static_fallback_message: Mapped[str] = mapped_column(
        Text,
        default="Thanks for your message. A team member will respond shortly.",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class LeadScore(Base):
    __tablename__ = "lead_scores"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    lead_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    grade: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, default="", nullable=False)
    next_action: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class LeadTag(Base):
    __tablename__ = "lead_tags"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    lead_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    tag_name: Mapped[str] = mapped_column(String(128), default="", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class LLMEngine(Base):
    __tablename__ = "llm_engines"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(255), default="", nullable=False, index=True)
    api_endpoint: Mapped[str] = mapped_column(Text, default="", nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048, nullable=False)
    version: Mapped[str] = mapped_column(String(64), default="current", nullable=False)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class AIAgent(Base):
    __tablename__ = "ai_agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    llm_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    mcp_server_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    agent_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    provider: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class AISession(Base):
    __tablename__ = "ai_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    convo_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class TrainingData(Base):
    __tablename__ = "training_data"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    data_category: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    input_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    output_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ContextMemory(Base):
    __tablename__ = "context_memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    convo_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    memory_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    memory_content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class AIEmbedding(Base):
    __tablename__ = "ai_embeddings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    memory_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    embedding_model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embedding_vector: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class SentimentAnalysis(Base):
    __tablename__ = "sentiment_analyses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    sentiment_label: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class MCPServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text, default="", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), default="active", nullable=False)
    region: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class MCPClient(Base):
    __tablename__ = "mcp_clients"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    server_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    client_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    client_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    platform: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_connected: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class APIEndpoint(Base):
    __tablename__ = "api_endpoints"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    server_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    endpoint_path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    http_method: Mapped[str] = mapped_column(String(16), default="POST", nullable=False)
    endpoint_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    request_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    response_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ClientRequest(Base):
    __tablename__ = "client_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    endpoint_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    convo_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    response_status: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class WebhookHandler(Base):
    __tablename__ = "webhook_handlers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    webhook_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    verification_token_ref: Mapped[str] = mapped_column(Text, default="", nullable=False)
    handler_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    last_triggered: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    handler_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class AnalyticsReport(Base):
    __tablename__ = "analytics_reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    report_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(128), default="", nullable=False, index=True)
    metric_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class SocialAccount(Base):
    __tablename__ = "social_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    account_handle: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    access_token_ref: Mapped[str] = mapped_column(Text, default="", nullable=False)
    page_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    app_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    phone_number_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class SocialPost(Base):
    __tablename__ = "social_posts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    post_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    post_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    engagement_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comments_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sentiment: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class JourneyTracking(Base):
    __tablename__ = "journey_tracking"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    lead_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    purchase_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    rating: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comments: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class SystemLog(Base):
    __tablename__ = "system_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class GenericDocument(Base):
    __tablename__ = "generic_documents"
    __table_args__ = (
        UniqueConstraint("collection_name", "id", name="uq_generic_documents_collection_id"),
        Index("ix_generic_documents_collection", "collection_name"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
