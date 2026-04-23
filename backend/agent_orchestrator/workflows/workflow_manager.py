from __future__ import annotations

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
from shared.database import create_detached_task
from shared.metrics import increment_counter, timed_metric


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
        return await self._run_workflow(
            workflow_kind=WorkflowKind.MESSAGE,
            request=request,
            entity_type="conversation_message",
            entity_id=request.message_id or request.conversation_id or make_id(),
        )

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

    async def _run_workflow(
        self,
        *,
        workflow_kind: WorkflowKind,
        request: MessageWorkflowRequest | LeadWorkflowRequest,
        entity_type: str,
        entity_id: str,
    ) -> WorkflowResponse:
        trace_id = str(getattr(request, "trace_id", "") or make_id()).replace("-", "")
        workflow_id = await self.state_store.create_workflow(
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
            input_payload=request.model_dump(),
            workflow_id=getattr(request, "workflow_id", "") or "",
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

        previous_agent: AgentName | None = None
        route = self.router.next_agent(context, previous_agent)
        last_response: WorkflowResponse | None = None
        while route.next_agent:
            next_agent = AgentName(route.next_agent)
            if next_agent == AgentName.ANALYTICS and getattr(request, "run_async_analytics", True):
                job_id = f"agent-orchestrator:analytics:{workflow_id}"
                create_detached_task(
                    run_workflow_analytics(db=context.db, workflow_id=workflow_id),
                    name="agent-orchestrator-analytics",
                    job_id=job_id,
                    idempotency_key=job_id,
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

    async def _execute_agent_by_name(
        self,
        context: WorkflowRuntimeContext,
        agent_name: AgentName,
        *,
        route=None,
    ) -> WorkflowResponse:
        agent = self.registry.get(agent_name)
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
            input_payload=context.request.model_dump(),
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
