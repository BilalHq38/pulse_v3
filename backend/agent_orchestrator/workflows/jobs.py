from __future__ import annotations


async def run_workflow_analytics(db, workflow_id: str) -> dict:
    from agent_orchestrator.engine import build_orchestrator_engine

    engine = build_orchestrator_engine(db)
    response = await engine.resume_analytics(workflow_id)
    return response.model_dump(mode="json")
