from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder

from agent_orchestrator.schemas import (
    AgentRunResult,
    AsyncJobStatus,
    GlobalMemory,
    WorkflowKind,
    WorkflowOutputs,
    WorkflowResponse,
    WorkflowRouteDecision,
    WorkflowStatus,
)
from core.utils import make_id
from shared.cache import get_cache_client

_WORKFLOWS_TABLE = "agent_orchestrator.workflows"
_WORKFLOW_EXECUTIONS_TABLE = "agent_orchestrator.workflow_executions"
_WORKFLOW_TRANSITIONS_TABLE = "agent_orchestrator.workflow_transitions"


class WorkflowStateStore:
    def __init__(self, db) -> None:
        self.db = db
        self.cache = get_cache_client(namespace="agent_orchestrator_state")

    def _cache_key(self, workflow_id: str) -> str:
        return f"workflow:{workflow_id}"

    async def create_workflow(
        self,
        *,
        workflow_kind: WorkflowKind,
        company_id: str,
        trace_id: str,
        entity_type: str,
        entity_id: str,
        conversation_id: str = "",
        customer_id: str = "",
        lead_id: str = "",
        channel: str = "",
        source: str = "",
        requested_by: str = "",
        input_payload: dict[str, Any] | None = None,
        workflow_id: str = "",
    ) -> tuple[str, bool]:
        resolved_workflow_id = str(workflow_id or make_id())
        encoded_input_payload = jsonable_encoder(input_payload or {})
        inserted = await self.db.fetchval(
            f"INSERT INTO {_WORKFLOWS_TABLE}("
            "id,company_id,trace_id,workflow_kind,status,entity_type,entity_id,conversation_id,"
            "customer_id,lead_id,channel,source,current_agent,routing_mode,requested_by,input_payload,"
            "shared_context,final_output,error,created_at,updated_at"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'','rule_based',$13,$14,'{}'::jsonb,'{}'::jsonb,'',NOW(),NOW()) "  # noqa: E501
            "ON CONFLICT DO NOTHING RETURNING id",
            resolved_workflow_id,
            company_id,
            trace_id,
            workflow_kind.value,
            WorkflowStatus.PENDING.value,
            entity_type,
            entity_id,
            conversation_id,
            customer_id,
            lead_id,
            channel,
            source,
            requested_by,
            encoded_input_payload,
        )
        if inserted:
            return str(inserted), True
        existing = await self.db.fetchval(
            f"SELECT id FROM {_WORKFLOWS_TABLE} "
            "WHERE id=$1 OR (company_id=$2 AND workflow_kind=$3 AND entity_type=$4 AND entity_id=$5) "
            "ORDER BY created_at DESC LIMIT 1",
            resolved_workflow_id,
            company_id,
            workflow_kind.value,
            entity_type,
            entity_id,
        )
        return str(existing or resolved_workflow_id), False

    async def get_record(self, workflow_id: str) -> dict[str, Any]:
        row = await self.db.fetchrow(f"SELECT * FROM {_WORKFLOWS_TABLE} WHERE id=$1 LIMIT 1", workflow_id)
        return dict(row) if row else {}

    async def find_existing_message_workflow(
        self,
        *,
        company_id: str,
        message_id: str,
    ) -> dict[str, Any]:
        if not company_id or not message_id:
            return {}
        row = await self.db.fetchrow(
            f"SELECT * FROM {_WORKFLOWS_TABLE} "
            "WHERE company_id=$1 AND workflow_kind=$2 AND entity_type='conversation_message' "
            "AND entity_id=$3 AND status<>$4 "
            "ORDER BY created_at DESC LIMIT 1",
            company_id,
            WorkflowKind.MESSAGE.value,
            message_id,
            WorkflowStatus.FAILED.value,
        )
        return dict(row) if row else {}

    async def update_after_agent(
        self,
        *,
        workflow_id: str,
        current_agent: str,
        result: AgentRunResult,
        route: WorkflowRouteDecision | None,
        intent: str = "",
        lead_status: str = "",
    ) -> None:
        effective_route = route or WorkflowRouteDecision()
        record = await self.db.fetchrow(
            f"SELECT shared_context, final_output FROM {_WORKFLOWS_TABLE} WHERE id=$1 LIMIT 1",
            workflow_id,
        )
        payload = dict(record) if record else {}
        shared_context = dict(payload.get("shared_context") or {})
        final_output = dict(payload.get("final_output") or {})
        agent_outputs = dict(shared_context.get("agent_outputs") or {})
        agent_outputs[result.agent_name.value] = jsonable_encoder(dict(result.payload or {}))
        shared_context["agent_outputs"] = agent_outputs
        shared_context["last_route"] = jsonable_encoder(effective_route.model_dump(mode="json"))
        final_output.update({result.agent_name.value: jsonable_encoder(dict(result.payload or {}))})
        encoded_shared_context = jsonable_encoder(shared_context)
        encoded_final_output = jsonable_encoder(final_output)
        await self.db.execute(
            f"UPDATE {_WORKFLOWS_TABLE} SET current_agent=$1,status=$2,routing_mode=$3,intent=$4,lead_status=$5,"
            "shared_context=$6,final_output=$7,error='',updated_at=NOW() WHERE id=$8",
            current_agent,
            WorkflowStatus.RUNNING.value,
            effective_route.decision_mode,
            intent,
            lead_status,
            encoded_shared_context,
            encoded_final_output,
            workflow_id,
        )

    async def append_execution(
        self,
        *,
        workflow_id: str,
        company_id: str,
        trace_id: str,
        result: AgentRunResult,
        route: WorkflowRouteDecision | None,
        input_payload: dict[str, Any] | None = None,
        attempt: int = 0,
    ) -> None:
        effective_route = route or WorkflowRouteDecision()
        encoded_route = jsonable_encoder(effective_route.model_dump(mode="json"))
        encoded_input_payload = jsonable_encoder(input_payload or {})
        encoded_output_payload = jsonable_encoder(result.payload or {})
        await self.db.execute(
            f"INSERT INTO {_WORKFLOW_EXECUTIONS_TABLE}("
            "id,workflow_id,company_id,trace_id,agent_name,status,routing_decision,input_payload,"
            "output_payload,error,duration_ms,attempt,started_at,completed_at"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,NOW(),NOW())",
            make_id(),
            workflow_id,
            company_id,
            trace_id,
            result.agent_name.value,
            result.status,
            encoded_route,
            encoded_input_payload,
            encoded_output_payload,
            result.error,
            result.duration_ms,
            attempt,
        )

    async def append_transition(
        self,
        *,
        workflow_id: str,
        company_id: str,
        trace_id: str,
        from_agent: str,
        to_agent: str,
        route: WorkflowRouteDecision,
        snapshot: dict[str, Any],
    ) -> None:
        encoded_snapshot = jsonable_encoder(snapshot or {})
        await self.db.execute(
            f"INSERT INTO {_WORKFLOW_TRANSITIONS_TABLE}("
            "id,workflow_id,company_id,trace_id,from_agent,to_agent,decision_reason,decision_mode,state_snapshot,created_at"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())",
            make_id(),
            workflow_id,
            company_id,
            trace_id,
            from_agent,
            to_agent,
            route.reason,
            route.decision_mode,
            encoded_snapshot,
        )

    async def mark_waiting(
        self,
        *,
        workflow_id: str,
        async_jobs: list[AsyncJobStatus],
        current_agent: str,
    ) -> None:
        record = await self.db.fetchrow(f"SELECT shared_context FROM {_WORKFLOWS_TABLE} WHERE id=$1 LIMIT 1", workflow_id)
        shared_context = dict((dict(record).get("shared_context") if record else {}) or {})
        shared_context["async_jobs"] = [job.model_dump(mode="json") for job in async_jobs]
        await self.db.execute(
            f"UPDATE {_WORKFLOWS_TABLE} SET status=$1,current_agent=$2,shared_context=$3,updated_at=NOW() WHERE id=$4",
            WorkflowStatus.WAITING.value,
            current_agent,
            jsonable_encoder(shared_context),
            workflow_id,
        )

    async def complete_workflow(
        self,
        *,
        workflow_id: str,
        final_output: WorkflowOutputs,
    ) -> None:
        await self.db.execute(
            f"UPDATE {_WORKFLOWS_TABLE} SET status=$1,final_output=$2,completed_at=NOW(),updated_at=NOW() WHERE id=$3",
            WorkflowStatus.COMPLETED.value,
            jsonable_encoder(final_output.model_dump(mode="json")),
            workflow_id,
        )

    async def fail_workflow(self, *, workflow_id: str, error: str) -> None:
        await self.db.execute(
            f"UPDATE {_WORKFLOWS_TABLE} SET status=$1,error=$2,updated_at=NOW() WHERE id=$3",
            WorkflowStatus.FAILED.value,
            str(error or "")[:2000],
            workflow_id,
        )

    async def build_response(
        self,
        *,
        record: dict[str, Any],
        route: WorkflowRouteDecision | None,
        global_memory: GlobalMemory,
    ) -> WorkflowResponse:
        shared_context = dict(record.get("shared_context") or {})
        outputs = WorkflowOutputs.model_validate(shared_context.get("agent_outputs") or {})
        response = WorkflowResponse(
            workflow_id=str(record.get("id") or ""),
            trace_id=str(record.get("trace_id") or ""),
            workflow_kind=WorkflowKind(str(record.get("workflow_kind") or WorkflowKind.MESSAGE.value)),
            status=WorkflowStatus(str(record.get("status") or WorkflowStatus.PENDING.value)),
            current_agent=str(record.get("current_agent") or ""),
            route=route or WorkflowRouteDecision.model_validate(shared_context.get("last_route") or {}),
            global_memory=global_memory,
            agent_outputs=outputs,
            async_jobs=[
                AsyncJobStatus.model_validate(item)
                for item in (shared_context.get("async_jobs") or [])
                if isinstance(item, dict)
            ],
            error=str(record.get("error") or ""),
        )
        await self.cache.set_json(
            self._cache_key(response.workflow_id),
            response.model_dump(mode="json"),
            ttl_seconds=3600,
        )
        return response
