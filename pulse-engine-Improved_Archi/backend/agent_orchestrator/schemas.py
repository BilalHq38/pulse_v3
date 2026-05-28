from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class WorkflowKind(str, Enum):
    MESSAGE = "message"
    LEAD = "lead"


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentName(str, Enum):
    CAPTURE = "capture"
    QUALIFICATION = "qualification"
    SUPPORT = "support"
    ANALYTICS = "analytics"


class ContactProfile(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""


class GlobalMemory(BaseModel):
    memory_key: str = ""
    company_id: str = ""
    conversation_id: str = ""
    customer_id: str = ""
    lead_id: str = ""
    identity_context: dict[str, Any] = Field(default_factory=dict)
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_context: str = ""
    summary: str = ""
    shared_context: dict[str, Any] = Field(default_factory=dict)


class WorkflowRouteDecision(BaseModel):
    current_agent: str = ""
    next_agent: str = ""
    decision_mode: str = "rule_based"
    reason: str = ""
    qualifiers: list[str] = Field(default_factory=list)


class AgentRunResult(BaseModel):
    agent_name: AgentName
    status: str = "completed"
    payload: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: str = ""
    used_fallback: bool = False
    duration_ms: float = 0.0


class WorkflowOutputs(BaseModel):
    capture: dict[str, Any] = Field(default_factory=dict)
    qualification: dict[str, Any] = Field(default_factory=dict)
    support: dict[str, Any] = Field(default_factory=dict)
    analytics: dict[str, Any] = Field(default_factory=dict)


class AsyncJobStatus(BaseModel):
    name: str
    queued: bool = False
    job_id: str = ""
    status: str = "queued"


class MessageWorkflowRequest(BaseModel):
    workflow_id: str = ""
    trace_id: str = ""
    company_id: str
    conversation_id: str = ""
    customer_id: str = ""
    lead_id: str = ""
    message_id: str = ""
    external_message_id: str = ""
    provider_event_id: str = ""
    idempotency_key: str = ""
    idempotency_strength: str = ""
    provider_timestamp: str = ""
    channel: str = "web_chat"
    source: str = ""
    message_text: str = ""
    sender_name: str = ""
    sender_contact: str = ""
    actor_user_id: str = ""
    actor_user_role: str = ""
    conversation_context: list[dict[str, Any]] = Field(default_factory=list)
    customer: dict[str, Any] = Field(default_factory=dict)
    lead: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    knowledge_context: str = ""
    run_async_analytics: bool = True
    auto_support: bool = True
    # When True, the support agent skips its own AI response generation and
    # returns a metadata-only payload. Used by the web-chat path when the
    # caller wants to source the response from `services.conversation_engine`
    # while keeping the rest of the workflow (capture/qualification/analytics)
    # intact.
    suppress_response_generation: bool = False

    @model_validator(mode="after")
    def _has_idempotency_material(self):
        metadata = dict(self.metadata or {})
        has_external = bool(
            self.message_id
            or self.idempotency_key
            or self.external_message_id
            or self.provider_event_id
            or metadata.get("external_message_id")
            or metadata.get("provider_event_id")
            or metadata.get("idempotency_key")
            or metadata.get("message_id")
        )
        has_legacy_material = bool(self.conversation_id and self.sender_contact)
        if not has_external and not has_legacy_material:
            raise ValueError(
                "Message workflow requires message_id, external/provider id, idempotency_key, "
                "or legacy conversation_id + sender_contact material"
            )
        if not self.idempotency_strength:
            self.idempotency_strength = "strong" if has_external else "unstable"
        return self


class LeadWorkflowRequest(BaseModel):
    workflow_id: str = ""
    trace_id: str = ""
    company_id: str
    lead_id: str = ""
    conversation_id: str = ""
    customer_id: str = ""
    source: str = ""
    raw_message: str = ""
    actor_user_id: str = ""
    actor_user_role: str = ""
    lead: dict[str, Any] = Field(default_factory=dict)
    customer: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    knowledge_context: str = ""
    run_async_analytics: bool = True
    auto_support: bool = True


class WorkflowResponse(BaseModel):
    workflow_id: str
    trace_id: str = ""
    workflow_kind: WorkflowKind
    status: WorkflowStatus
    current_agent: str = ""
    route: WorkflowRouteDecision = Field(default_factory=WorkflowRouteDecision)
    global_memory: GlobalMemory = Field(default_factory=GlobalMemory)
    agent_outputs: WorkflowOutputs = Field(default_factory=WorkflowOutputs)
    async_jobs: list[AsyncJobStatus] = Field(default_factory=list)
    error: str = ""
