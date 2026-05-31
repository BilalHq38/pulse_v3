from __future__ import annotations

from agent_orchestrator.agents import (
    AgentRegistry,
    AnalyticsAgent,
    CaptureAgent,
    QualificationAgent,
)
from agent_orchestrator.memory.store import MemoryStore
from agent_orchestrator.router.agent_router import AgentRouter
from agent_orchestrator.schemas import (
    LeadWorkflowRequest,
    MessageWorkflowRequest,
    WorkflowKind,
    WorkflowResponse,
)
from agent_orchestrator.workflows.state_store import WorkflowStateStore
from agent_orchestrator.workflows.workflow_manager import WorkflowManager


class OrchestratorEngine:
    def __init__(self, db) -> None:
        self.db = db
        self.state_store = WorkflowStateStore(db)
        self.memory_store = MemoryStore(db)
        self.registry = AgentRegistry()
        self.registry.register(CaptureAgent())
        self.registry.register(QualificationAgent())
        self.registry.register(AnalyticsAgent())
        self.router = AgentRouter()
        self.manager = WorkflowManager(
            db=db,
            state_store=self.state_store,
            memory_store=self.memory_store,
            registry=self.registry,
            router=self.router,
        )

    async def run_message_workflow(
        self,
        request: MessageWorkflowRequest,
    ) -> WorkflowResponse:
        return await self.manager.run_message_workflow(request)

    async def run_lead_workflow(self, request: LeadWorkflowRequest) -> WorkflowResponse:
        return await self.manager.run_lead_workflow(request)

    async def resume_analytics(self, workflow_id: str) -> WorkflowResponse:
        return await self.manager.resume_analytics(workflow_id)

    async def get_workflow(self, workflow_id: str) -> WorkflowResponse:
        record = await self.state_store.get_record(workflow_id)
        if not record:
            raise ValueError(f"Workflow not found: {workflow_id}")
        global_memory = await self.memory_store.load_global_memory(
            workflow_kind=WorkflowKind(str(record.get("workflow_kind") or WorkflowKind.MESSAGE.value)),
            company_id=str(record.get("company_id") or ""),
            conversation_id=str(record.get("conversation_id") or ""),
            customer_id=str(record.get("customer_id") or ""),
            lead_id=str(record.get("lead_id") or ""),
        )
        return await self.state_store.build_response(
            record=record,
            route=None,
            global_memory=global_memory,
        )


def build_orchestrator_engine(db) -> OrchestratorEngine:
    return OrchestratorEngine(db)
