from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

logger = logging.getLogger(__name__)

from agent_orchestrator.engine import build_orchestrator_engine
from agent_orchestrator.schemas import LeadWorkflowRequest, MessageWorkflowRequest
from shared.auth.dependencies import get_current_user
from shared.tracing import current_trace_context

router = APIRouter()


def _trace_id() -> str:
    trace = current_trace_context()
    return trace.trace_id if trace else ""


def _json_preview(obj: object, max_len: int = 600) -> str:
    try:
        s = json.dumps(obj, default=str)
    except Exception:
        s = str(obj)
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s


@router.get("/orchestrator/executions")
async def list_workflow_executions(
    request: Request,
    limit: int = Query(30, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """Recent rule-based agent steps for the tenant (workflow_executions)."""
    db = request.app.state.db
    cid = str(current_user.get("company_id") or "").strip()
    role = str(current_user.get("role") or "").strip().lower()
    if role != "super_admin" and not cid:
        raise HTTPException(status_code=403, detail="Company required")
    if not cid:
        return {"executions": []}
    rows = await db.fetch(
        """
        SELECT e.id, e.workflow_id, e.trace_id, e.agent_name, e.status, e.routing_decision, e.output_payload,
               e.error, e.duration_ms, e.started_at, e.completed_at,
               w.workflow_kind, w.status AS workflow_status
        FROM agent_orchestrator.workflow_executions e
        LEFT JOIN agent_orchestrator.workflows w ON w.id = e.workflow_id
        WHERE e.company_id = $1
        ORDER BY e.started_at DESC
        LIMIT $2
        """,
        cid,
        limit,
    )
    executions = []
    for row in rows:
        d = dict(row)
        rd = d.get("routing_decision")
        if not isinstance(rd, dict):
            rd = {}
        op = d.get("output_payload")
        err = d.get("error") or ""
        st = d.get("started_at")
        ct = d.get("completed_at")
        executions.append(
            {
                "id": d.get("id"),
                "workflow_id": d.get("workflow_id"),
                "trace_id": d.get("trace_id"),
                "agent_name": d.get("agent_name"),
                "status": d.get("status"),
                "routing_decision": rd,
                "output_summary": _json_preview(op if op is not None else {}),
                "error": (err or "")[:500],
                "duration_ms": float(d.get("duration_ms") or 0),
                "started_at": st.isoformat() if st else None,
                "completed_at": ct.isoformat() if ct else None,
                "workflow_kind": d.get("workflow_kind"),
                "workflow_status": d.get("workflow_status"),
            }
        )
    return {"executions": executions}


@router.post("/orchestrator/workflows/messages")
async def run_message_workflow(
    payload: MessageWorkflowRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = request.app.state.db
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
    engine = build_orchestrator_engine(db)
    response = await engine.get_workflow(workflow_id)
    company_id = current_user.get("company_id", "")
    if company_id and response.global_memory.company_id and response.global_memory.company_id != company_id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return response
