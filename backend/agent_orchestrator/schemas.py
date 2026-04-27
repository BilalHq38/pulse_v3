from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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
