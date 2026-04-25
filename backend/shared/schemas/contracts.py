from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    """Unified API response envelope used across all services."""

    success: bool = True
    data: Any = None
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""

    @classmethod
    def ok(cls, data: Any = None, *, meta: dict[str, Any] | None = None, trace_id: str = "") -> "ApiResponse":
        return cls(success=True, data=data, meta=meta or {}, trace_id=trace_id)

    @classmethod
    def fail(
        cls, error: str, *, status_code: int = 400, meta: dict[str, Any] | None = None, trace_id: str = ""
    ) -> "ApiResponse":
        return cls(success=False, error=error, meta={"status_code": status_code, **(meta or {})}, trace_id=trace_id)


class AnalyzeRequest(BaseModel):
    text: str
    company_id: str = ""
    conversation_context: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalyzeResponse(BaseModel):
    sentiment: dict[str, Any]
    intent: dict[str, Any]
    escalation: bool = False


class RespondRequest(BaseModel):
    message: str
    company_id: str = ""
    conversation_context: list[dict[str, Any]] = Field(default_factory=list)
    customer: dict[str, Any] = Field(default_factory=dict)
    channel: str = "web_chat"
    knowledge_context: str = ""
    long_term_summary: str = ""
    historical_sentiment: str = ""
    actor_user_id: str = ""
    conversation_id: str = ""


class RespondResponse(BaseModel):
    reply: str
    confidence: float = 0.0
    sentiment: dict[str, Any] | None = None
    intent: dict[str, Any] | None = None
    conversation_sentiment: dict[str, Any] | None = None
    engine: str = ""
    llm_id: str = ""
    agent_id: str = ""
    agent_type: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    product_images: list[dict[str, Any]] = Field(default_factory=list)
    product_ids: list[str] = Field(default_factory=list)
    conversation_stage: str = ""
    next_action: str = ""
    intent_shift: bool = False


class LeadScoreRequest(BaseModel):
    lead: dict[str, Any]
    company_id: str = ""


class LeadScoreResponse(BaseModel):
    score: int
    grade: str = ""
    reasoning: str = ""
    next_action: str = ""
    phase: str = ""
    nurture_message: str = ""


class CombinedRequest(BaseModel):
    customer_message: str
    company_id: str = ""
    channel: str = "web_chat"
    conversation_context: list[dict[str, Any]] = Field(default_factory=list)
    customer: dict[str, Any] = Field(default_factory=dict)
    knowledge_context: str = ""
    long_term_summary: str = ""
    historical_sentiment: str = ""
    actor_user_id: str = ""
    conversation_id: str = ""


class CombinedResponse(BaseModel):
    sentiment: dict[str, Any]
    conversation_sentiment: dict[str, Any] = Field(default_factory=dict)
    intent: dict[str, Any]
    ai_response: dict[str, Any]


class NurtureRequest(BaseModel):
    lead: dict[str, Any]
    stage: str = "awareness"
    company_id: str = ""
    company_context: str = ""


class MemoryUpdateRequest(BaseModel):
    customer_id: str
    company_id: str = ""
    previous_summary: str = ""
    recent_sentiment: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list)


class ConversationSummaryRequest(BaseModel):
    company_id: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)


class InteractionSummaryRequest(BaseModel):
    company_id: str = ""
    customer: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list)


class DailySummaryRequest(BaseModel):
    date: str
    company_id: str = ""
    interactions: list[dict[str, Any]] = Field(default_factory=list)


class KnowledgeRequest(BaseModel):
    company_id: str = ""
    query: str = ""
    exclude_product_ids: list[str] = Field(default_factory=list)


class ProductDescriptionRequest(BaseModel):
    name: str
    product_title: str = ""
    product_type: str = ""
    category: str = ""
    price: str = ""
    price_currency: str = "USD"
    images: list[str] = Field(default_factory=list)


class SendEmailRequest(BaseModel):
    to_email: str
    subject: str
    body: str
    html_body: str = ""
    company_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
