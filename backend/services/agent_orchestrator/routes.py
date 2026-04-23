from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from agent_orchestrator.bootstrap import bootstrap_agent_orchestrator
from agent_orchestrator.engine import build_orchestrator_engine
from agent_orchestrator.schemas import LeadWorkflowRequest, MessageWorkflowRequest
from shared.auth.dependencies import get_current_user
from shared.tracing import current_trace_context

router = APIRouter()


def _trace_id() -> str:
    trace = current_trace_context()
    return trace.trace_id if trace else ""


@router.post("/orchestrator/workflows/messages")
async def run_message_workflow(
    payload: MessageWorkflowRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    await bootstrap_agent_orchestrator(db)
    engine = build_orchestrator_engine(db)
    effective_payload = payload.model_copy(
        update={
            "company_id": current_user.get("company_id", "") or payload.company_id,
            "actor_user_id": payload.actor_user_id or current_user.get("sub", ""),
            "actor_user_role": payload.actor_user_role or current_user.get("role", ""),
            "trace_id": payload.trace_id or _trace_id(),
        }
    )
    return await engine.run_message_workflow(effective_payload)


@router.post("/orchestrator/workflows/leads")
async def run_lead_workflow(
    payload: LeadWorkflowRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    await bootstrap_agent_orchestrator(db)
    engine = build_orchestrator_engine(db)
    effective_payload = payload.model_copy(
        update={
            "company_id": current_user.get("company_id", "") or payload.company_id,
            "actor_user_id": payload.actor_user_id or current_user.get("sub", ""),
            "actor_user_role": payload.actor_user_role or current_user.get("role", ""),
            "trace_id": payload.trace_id or _trace_id(),
        }
    )
    return await engine.run_lead_workflow(effective_payload)


@router.get("/orchestrator/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
    await bootstrap_agent_orchestrator(db)
    engine = build_orchestrator_engine(db)
    response = await engine.get_workflow(workflow_id)
    company_id = current_user.get("company_id", "")
    if company_id and response.global_memory.company_id and response.global_memory.company_id != company_id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return response
