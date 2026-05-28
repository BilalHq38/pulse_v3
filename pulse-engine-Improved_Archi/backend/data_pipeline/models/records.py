from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(slots=True)
class NormalizedEvent:
    company_id: str
    raw_id: str
    source: str
    event_kind: str
    event_id: str
    occurred_at: datetime
    metric_date: date
    entity_type: str = ""
    entity_id: str = ""
    conversation_id: str = ""
    lead_id: str = ""
    customer_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedMessage:
    company_id: str
    raw_id: str
    message_id: str
    conversation_id: str
    source: str
    sender_type: str
    sender_name: str
    content: str
    occurred_at: datetime
    metric_date: date
    customer_id: str = ""
    sentiment_score: float | None = None
    sentiment_label: str = ""
    sentiment_confidence: float | None = None
    intent_type: str = ""
    intent_confidence: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedLeadMetric:
    company_id: str
    raw_id: str
    lead_id: str
    source: str
    status: str
    phase: str
    grade: str
    metric_date: date
    occurred_at: datetime
    name: str = ""
    email: str = ""
    phone: str = ""
    current_score: int = 0
    recommended_score: int = 0
    recommended_grade: str = ""
    scoring_reason: str = ""
    next_action: str = ""
    duplicate_count: int = 0
    is_duplicate: bool = False
    is_converted: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedConversationMetric:
    company_id: str
    conversation_id: str
    metric_date: date
    channel: str
    status: str
    customer_id: str
    ai_handled: bool
    escalated: bool
    total_messages: int
    customer_messages: int
    agent_messages: int
    ai_messages: int
    system_messages: int
    unread_count: int
    avg_sentiment: float
    latest_sentiment_label: str
    latest_sentiment_score: float | None
    latest_intent_type: str
    first_message_at: datetime | None
    last_message_at: datetime | None
    response_time_minutes: float
    payload: dict[str, Any] = field(default_factory=dict)
