"""Dataclasses exchanged between conversation engine layers.

These are deliberately small Python dataclasses (not Pydantic) — the engine
runs internal-only data through them and the FastAPI layer is the place where
external request/response validation happens. Keeping them lightweight makes
unit-testing layers cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Mode = Literal["reactive", "proactive"]
SourceType = Literal["company_data", "product", "template", "faq", "knowledge_base"]
WorkflowKind = Literal["post_delivery_feedback", "upsell"]


@dataclass
class TurnRequest:
    session_id: str
    company_id: str
    user_message: str
    customer_id: str = ""
    mode: Mode = "reactive"
    workflow_kind: WorkflowKind | None = None
    # Optional anchor for proactive turns — when the engine knows which order
    # this turn relates to, the upsell path can pull related products from
    # product_relationships instead of running an open-ended product search.
    order_id: str = ""
    # Formatted dialogue lines ["User: X", "Assistant: Y", ...] passed from the
    # webhook's messages table. Used as a fallback when ai_conversation_turns
    # is empty (e.g. RLS not configured, first turn).
    extra_history: list[str] = field(default_factory=list)


@dataclass
class ContextChunk:
    source_type: SourceType
    source_id: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 0.0


@dataclass
class RetrievalResult:
    source_type: SourceType
    chunks: list[ContextChunk]
    latency_ms: int = 0
    error: str = ""


@dataclass
class ProductLink:
    product_id: str
    url: str
    name: str = ""
    image_url: str = ""


@dataclass
class TokenUsage:
    prompt: int = 0
    completion: int = 0
    total: int = 0


@dataclass
class ValidationReport:
    ok: bool
    offences: list[str] = field(default_factory=list)


@dataclass
class TurnResult:
    answer: str
    session_id: str
    turn_id: str
    sources_used: list[SourceType]
    product_links: list[ProductLink]
    tokens_used: TokenUsage
    active_template: str
    confidence: float
    error: str = ""
    sentiment: dict[str, Any] = field(default_factory=dict)
    conversation_sentiment: dict[str, Any] = field(default_factory=dict)
    escalation_required: bool = False
