from __future__ import annotations

import logging
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

from agent_orchestrator.agents.base import AgentRegistry
from agent_orchestrator.memory.store import MemoryStore
from agent_orchestrator.router.agent_router import AgentRouter
from agent_orchestrator.schemas import (
    AgentName,
    AgentRunResult,
    AsyncJobStatus,
    LeadWorkflowRequest,
    MessageWorkflowRequest,
    WorkflowKind,
    WorkflowOutputs,
    WorkflowResponse,
)
from agent_orchestrator.workflows.jobs import run_workflow_analytics
from agent_orchestrator.workflows.state_store import WorkflowStateStore
from core.utils import make_id
from shared.config import ai_max_embedding_calls_per_message, ai_max_llm_calls_per_message
from shared.metrics import increment_counter, timed_metric
from shared.webhook_task_runner import create_safe_detached_task
from services.ai_service.llm_tracking import get_llm_context, reset_llm_context, set_current_agent, set_llm_context

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkflowRuntimeContext:
    db: Any
    state_store: WorkflowStateStore
    memory_store: MemoryStore
    registry: AgentRegistry
    router: AgentRouter
    workflow_id: str
    trace_id: str
    company_id: str
    workflow_kind: WorkflowKind
    request: MessageWorkflowRequest | LeadWorkflowRequest
    global_memory: Any
    agent_outputs: WorkflowOutputs


class WorkflowManager:
    def __init__(
        self,
        *,
        db,
        state_store: WorkflowStateStore,
        memory_store: MemoryStore,
        registry: AgentRegistry,
        router: AgentRouter,
    ) -> None:
        self.db = db
        self.state_store = state_store
        self.memory_store = memory_store
        self.registry = registry
        self.router = router

    async def run_message_workflow(
        self,
        request: MessageWorkflowRequest,
    ) -> WorkflowResponse:
        message_id, idempotency_strength = self._derive_message_id(request)
        request.message_id = message_id
        request.idempotency_key = message_id
        request.idempotency_strength = idempotency_strength
        request.metadata = {
            **dict(request.metadata or {}),
            "idempotency_strength": idempotency_strength,
            "message_id_unstable_generated": idempotency_strength == "unstable",
        }
        existing = (
            await self.state_store.find_existing_message_workflow(
                company_id=request.company_id,
                message_id=message_id,
            )
            if idempotency_strength != "unstable"
            else {}
        )
        if existing:
            logger.warning(
                "duplicate_message_workflow_attempt message_id=%s conversation_id=%s company_id=%s existing_workflow_id=%s status=%s",
                request.message_id,
                request.conversation_id,
                request.company_id,
                str(existing.get("id") or ""),
                str(existing.get("status") or ""),
            )
            logger.info(
                "workflow_ai_usage_summary company_id=%s conversation_id=%s message_id=%s workflow_id=%s "
                "channel=%s llm_call_count=0 embedding_call_count=0 total_ai_api_call_count=0 "
                "prefetched_response_used=false analytics_queued=false analytics_llm_called=false "
                "qualification_scoring_called=false rag_called=false duplicate_workflow_blocked=true final_status=%s",
                request.company_id,
                request.conversation_id,
                message_id,
                str(existing.get("id") or ""),
                request.channel,
                str(existing.get("status") or ""),
            )
            global_memory = await self.memory_store.load_global_memory(
                workflow_kind=WorkflowKind.MESSAGE,
                company_id=request.company_id,
                conversation_id=getattr(request, "conversation_id", ""),
                customer_id=getattr(request, "customer_id", ""),
                lead_id=getattr(request, "lead_id", ""),
                conversation_context=getattr(request, "conversation_context", []),
                customer=getattr(request, "customer", {}),
                lead=getattr(request, "lead", {}),
            )
            return await self.state_store.build_response(
                record=existing,
                route=None,
                global_memory=global_memory,
            )
        return await self._run_workflow(
            workflow_kind=WorkflowKind.MESSAGE,
            request=request,
            entity_type="conversation_message",
            entity_id=message_id,
        )

    def _derive_message_id(self, request: MessageWorkflowRequest) -> tuple[str, str]:
        metadata = dict(request.metadata or {})
        for candidate in (
            request.message_id,
            request.idempotency_key,
            request.external_message_id,
            request.provider_event_id,
            metadata.get("external_message_id"),
            metadata.get("provider_event_id"),
            metadata.get("message_id"),
        ):
            if str(candidate or "").strip():
                return str(candidate).strip(), "strong"
        normalized_text = re.sub(r"\s+", " ", str(request.message_text or "").strip().lower())
        timestamp_material = (
            request.provider_timestamp
            or metadata.get("timestamp")
            or metadata.get("created_at")
            or metadata.get("sent_at")
            or metadata.get("message_timestamp")
            or ""
        )
        if not str(timestamp_material or "").strip():
            generated = f"internal:{make_id()}"
            logger.warning(
                "message_id_unstable_generated=true company_id=%s conversation_id=%s channel=%s sender_contact=%s",
                request.company_id,
                request.conversation_id,
                request.channel,
                request.sender_contact,
            )
            return generated, "unstable"
        material = "|".join(
            [
                str(request.company_id or ""),
                str(request.conversation_id or ""),
                str(request.channel or ""),
                str(request.sender_contact or ""),
                normalized_text,
                str(timestamp_material or ""),
            ]
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return f"derived:{digest}", "fallback_timestamp"

    async def run_lead_workflow(
        self,
        request: LeadWorkflowRequest,
    ) -> WorkflowResponse:
        return await self._run_workflow(
            workflow_kind=WorkflowKind.LEAD,
            request=request,
            entity_type="lead",
            entity_id=request.lead_id or str((request.lead or {}).get("id") or make_id()),
        )

    async def resume_analytics(self, workflow_id: str) -> WorkflowResponse:
        record = await self.state_store.get_record(workflow_id)
        if not record:
            raise ValueError(f"Workflow not found: {workflow_id}")
        workflow_kind = WorkflowKind(str(record.get("workflow_kind") or WorkflowKind.MESSAGE.value))
        if workflow_kind == WorkflowKind.MESSAGE:
            request = MessageWorkflowRequest.model_validate(dict(record.get("input_payload") or {}))
        else:
            request = LeadWorkflowRequest.model_validate(dict(record.get("input_payload") or {}))
        global_memory = await self.memory_store.load_global_memory(
            workflow_kind=workflow_kind,
            company_id=str(record.get("company_id") or ""),
            conversation_id=str(record.get("conversation_id") or ""),
            customer_id=str(record.get("customer_id") or ""),
            lead_id=str(record.get("lead_id") or ""),
            conversation_context=getattr(request, "conversation_context", [])
            if hasattr(request, "conversation_context")
            else [],
            customer=getattr(request, "customer", {}),
            lead=getattr(request, "lead", {}),
        )
        agent_outputs = WorkflowOutputs.model_validate(
            (dict(record.get("shared_context") or {}).get("agent_outputs") or {})
        )
        context = WorkflowRuntimeContext(
            db=self.db,
            state_store=self.state_store,
            memory_store=self.memory_store,
            registry=self.registry,
            router=self.router,
            workflow_id=workflow_id,
            trace_id=str(record.get("trace_id") or ""),
            company_id=str(record.get("company_id") or ""),
            workflow_kind=workflow_kind,
            request=request,
            global_memory=global_memory,
            agent_outputs=agent_outputs,
        )
        token = set_llm_context(
            message_id=str(getattr(request, "message_id", "") or ""),
            conversation_id=str(getattr(request, "conversation_id", "") or ""),
            workflow_id=workflow_id,
            company_id=str(record.get("company_id") or ""),
            max_calls=ai_max_llm_calls_per_message(),
            max_embedding_calls=ai_max_embedding_calls_per_message(),
        )
        try:
            response = await self._execute_agent_by_name(context, AgentName.ANALYTICS)
            await self.memory_store.save_global_memory(context.global_memory)
            await self.state_store.complete_workflow(
                workflow_id=workflow_id,
                final_output=context.agent_outputs,
            )
            record = await self.state_store.get_record(workflow_id)
            return await self.state_store.build_response(
                record=record,
                route=response.route,
                global_memory=context.global_memory,
            )
        finally:
            reset_llm_context(token)

    async def _run_workflow(
        self,
        *,
        workflow_kind: WorkflowKind,
        request: MessageWorkflowRequest | LeadWorkflowRequest,
        entity_type: str,
        entity_id: str,
    ) -> WorkflowResponse:
        trace_id = str(getattr(request, "trace_id", "") or make_id()).replace("-", "")
        requested_workflow_id = str(getattr(request, "workflow_id", "") or "")
        if workflow_kind == WorkflowKind.MESSAGE:
            workflow_id_seed = f"{request.company_id}:{entity_type}:{entity_id}"
            requested_workflow_id = "message:" + hashlib.sha256(workflow_id_seed.encode("utf-8")).hexdigest()
        workflow_id, created = await self.state_store.create_workflow(
            workflow_kind=workflow_kind,
            company_id=request.company_id,
            trace_id=trace_id,
            entity_type=entity_type,
            entity_id=entity_id,
            conversation_id=getattr(request, "conversation_id", ""),
            customer_id=getattr(request, "customer_id", ""),
            lead_id=getattr(request, "lead_id", ""),
            channel=getattr(request, "channel", ""),
            source=getattr(request, "source", ""),
            requested_by=getattr(request, "actor_user_id", ""),
            input_payload=request.model_dump(mode="json"),
            workflow_id=requested_workflow_id,
        )
        if not created:
            logger.warning(
                "duplicate_workflow_blocked workflow_id=%s entity_type=%s entity_id=%s company_id=%s",
                workflow_id,
                entity_type,
                entity_id,
                request.company_id,
            )
            logger.info(
                "workflow_ai_usage_summary company_id=%s conversation_id=%s message_id=%s workflow_id=%s "
                "channel=%s llm_call_count=0 embedding_call_count=0 total_ai_api_call_count=0 "
                "prefetched_response_used=false analytics_queued=false analytics_llm_called=false "
                "qualification_scoring_called=false rag_called=false duplicate_workflow_blocked=true final_status=existing",
                request.company_id,
                str(getattr(request, "conversation_id", "") or ""),
                entity_id,
                workflow_id,
                str(getattr(request, "channel", "") or ""),
            )
            record = await self.state_store.get_record(workflow_id)
            global_memory = await self.memory_store.load_global_memory(
                workflow_kind=workflow_kind,
                company_id=request.company_id,
                conversation_id=getattr(request, "conversation_id", ""),
                customer_id=getattr(request, "customer_id", ""),
                lead_id=getattr(request, "lead_id", ""),
                conversation_context=getattr(request, "conversation_context", [])
                if hasattr(request, "conversation_context")
                else [],
                customer=getattr(request, "customer", {}),
                lead=getattr(request, "lead", {}),
            )
            return await self.state_store.build_response(
                record=record,
                route=None,
                global_memory=global_memory,
            )
        global_memory = await self.memory_store.load_global_memory(
            workflow_kind=workflow_kind,
            company_id=request.company_id,
            conversation_id=getattr(request, "conversation_id", ""),
            customer_id=getattr(request, "customer_id", ""),
            lead_id=getattr(request, "lead_id", ""),
            conversation_context=getattr(request, "conversation_context", [])
            if hasattr(request, "conversation_context")
            else [],
            customer=getattr(request, "customer", {}),
            lead=getattr(request, "lead", {}),
        )
        context = WorkflowRuntimeContext(
            db=self.db,
            state_store=self.state_store,
            memory_store=self.memory_store,
            registry=self.registry,
            router=self.router,
            workflow_id=workflow_id,
            trace_id=trace_id,
            company_id=request.company_id,
            workflow_kind=workflow_kind,
            request=request,
            global_memory=global_memory,
            agent_outputs=WorkflowOutputs(),
        )
        metadata = dict(getattr(request, "metadata", {}) or {})
        try:
            max_llm_calls = int(metadata.get("max_llm_calls_per_message") or ai_max_llm_calls_per_message())
        except Exception:
            max_llm_calls = ai_max_llm_calls_per_message()
        max_llm_calls = min(max_llm_calls, 1)
        llm_context_token = set_llm_context(
            message_id=str(getattr(request, "message_id", "") or ""),
            conversation_id=str(getattr(request, "conversation_id", "") or ""),
            workflow_id=workflow_id,
            company_id=request.company_id,
            max_calls=max_llm_calls,
            max_embedding_calls=ai_max_embedding_calls_per_message(),
            metadata=metadata,
        )

        try:
            previous_agent: AgentName | None = None
            route = self.router.next_agent(context, previous_agent)
            last_response: WorkflowResponse | None = None
            analytics_queued = False
            while route.next_agent:
                next_agent = AgentName(route.next_agent)
                if next_agent == AgentName.ANALYTICS and getattr(request, "run_async_analytics", True):
                    analytics_queued = True
                    job_id = f"agent-orchestrator:analytics:{workflow_id}"
                    create_safe_detached_task(
                        context.db,
                        run_workflow_analytics(db=context.db, workflow_id=workflow_id),
                        name="agent-orchestrator-analytics",
                        job_id=job_id,
                        idempotency_key=job_id,
                        company_id=request.company_id,
                        channel=workflow_kind.value,
                        trace_id=trace_id,
                        event_id=workflow_id,
                        source_queue="agent_orchestrator",
                    )
                    async_job = AsyncJobStatus(
                        name="analytics",
                        queued=True,
                        job_id=job_id,
                        status="queued",
                    )
                    await self.memory_store.save_global_memory(context.global_memory)
                    await self.state_store.mark_waiting(
                        workflow_id=workflow_id,
                        async_jobs=[async_job],
                        current_agent=next_agent.value,
                    )
                    record = await self.state_store.get_record(workflow_id)
                    return await self.state_store.build_response(
                        record=record,
                        route=route,
                        global_memory=context.global_memory,
                    )
                last_response = await self._execute_agent_by_name(context, next_agent, route=route)
                previous_agent = next_agent
                route = self.router.next_agent(context, previous_agent)

            await self.memory_store.save_global_memory(context.global_memory)
            await self.state_store.complete_workflow(
                workflow_id=workflow_id,
                final_output=context.agent_outputs,
            )
            increment_counter(
                "agent_orchestrator.workflow.completed",
                labels={"workflow_kind": workflow_kind.value},
            )
            record = await self.state_store.get_record(workflow_id)
            return await self.state_store.build_response(
                record=record,
                route=route if last_response is None else last_response.route,
                global_memory=context.global_memory,
            )
        finally:
            llm_context = get_llm_context()
            if llm_context is not None:
                support = dict(getattr(context.agent_outputs, "support", {}) or {})
                analytics = dict(getattr(context.agent_outputs, "analytics", {}) or {})
                qualification = dict(getattr(context.agent_outputs, "qualification", {}) or {})
                capture = dict(getattr(context.agent_outputs, "capture", {}) or {})
                try:
                    final_record = await self.state_store.get_record(workflow_id)
                    final_status = str(final_record.get("status") or "")
                except Exception:
                    final_status = "unknown"
                logger.info(
                    "workflow_llm_call_count workflow_id=%s message_id=%s conversation_id=%s company_id=%s call_count=%s max_calls=%s",
                    workflow_id,
                    llm_context.message_id or "",
                    llm_context.conversation_id or "",
                    request.company_id,
                    llm_context.call_count,
                    llm_context.max_calls,
                )
                logger.info(
                    "workflow_ai_usage_summary company_id=%s conversation_id=%s message_id=%s workflow_id=%s "
                    "channel=%s llm_call_count=%s embedding_call_count=%s total_ai_api_call_count=%s "
                    "prefetched_response_used=%s analytics_queued=%s analytics_llm_called=%s "
                    "qualification_scoring_called=%s rag_called=%s duplicate_workflow_blocked=false final_status=%s",
                    request.company_id,
                    str(getattr(request, "conversation_id", "") or ""),
                    llm_context.message_id or "",
                    workflow_id,
                    str(getattr(request, "channel", "") or ""),
                    llm_context.call_count,
                    llm_context.embedding_call_count,
                    llm_context.total_ai_api_call_count,
                    bool(support.get("reused_prefetched_response")),
                    bool(locals().get("analytics_queued", False)),
                    bool(analytics.get("analytics_llm_called")),
                    bool(qualification.get("qualification_scoring_called")),
                    bool(capture.get("rag_called") or support.get("rag_called")),
                    final_status,
                )
            reset_llm_context(llm_context_token)

    async def _execute_agent_by_name(
        self,
        context: WorkflowRuntimeContext,
        agent_name: AgentName,
        *,
        route=None,
    ) -> WorkflowResponse:
        agent = self.registry.get(agent_name)
        set_current_agent(agent_name.value)
        started = time.perf_counter()
        result: AgentRunResult
        try:
            increment_counter(
                "agent_orchestrator.agent.started",
                labels={"agent": agent_name.value, "workflow_kind": context.workflow_kind.value},
            )
            with timed_metric(
                "agent_orchestrator.agent.duration_ms",
                labels={"agent": agent_name.value, "workflow_kind": context.workflow_kind.value},
            ):
                result = await agent.execute(context)
        except Exception as exc:
            increment_counter(
                "agent_orchestrator.agent.error",
                labels={"agent": agent_name.value, "workflow_kind": context.workflow_kind.value},
            )
            fallback = await agent.fallback(context, exc)
            if fallback is None:
                await self.state_store.fail_workflow(
                    workflow_id=context.workflow_id,
                    error=str(exc),
                )
                raise
            result = fallback
            result.error = str(exc)
        result.duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        setattr(context.agent_outputs, agent_name.value, dict(result.payload or {}))
        await self.memory_store.save_agent_memory(
            workflow_id=context.workflow_id,
            company_id=context.company_id,
            trace_id=context.trace_id,
            agent_name=agent_name.value,
            memory_key="output",
            memory_value=dict(result.payload or {}),
        )
        await self.memory_store.save_global_memory(context.global_memory)
        intent = ""
        if agent_name == AgentName.CAPTURE:
            intent = str(((result.payload.get("intent") or {}).get("intent") or ""))
        lead_status = ""
        if agent_name == AgentName.QUALIFICATION:
            lead_status = str(result.payload.get("lead_status") or "")
        await self.state_store.append_execution(
            workflow_id=context.workflow_id,
            company_id=context.company_id,
            trace_id=context.trace_id,
            result=result,
            route=route,
            input_payload=context.request.model_dump(mode="json"),
        )
        await self.state_store.update_after_agent(
            workflow_id=context.workflow_id,
            current_agent=agent_name.value,
            result=result,
            route=route,
            intent=intent,
            lead_status=lead_status,
        )
        next_route = self.router.next_agent(context, agent_name)
        await self.state_store.append_transition(
            workflow_id=context.workflow_id,
            company_id=context.company_id,
            trace_id=context.trace_id,
            from_agent=agent_name.value,
            to_agent=next_route.next_agent,
            route=next_route,
            snapshot=context.agent_outputs.model_dump(),
        )
        record = await self.state_store.get_record(context.workflow_id)
        return await self.state_store.build_response(
            record=record,
            route=next_route,
            global_memory=context.global_memory,
        )
