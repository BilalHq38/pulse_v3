from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from agent_orchestrator.schemas import AgentName, AgentRunResult


class WorkflowContextProtocol(Protocol):
    company_id: str
    trace_id: str
    workflow_id: str
    workflow_kind: object
    request: object
    db: object
    global_memory: object
    agent_outputs: object


class BaseAgent(ABC):
    name: AgentName

    @abstractmethod
    async def execute(self, context: WorkflowContextProtocol) -> AgentRunResult:
        raise NotImplementedError

    async def fallback(
        self,
        context: WorkflowContextProtocol,
        error: Exception,
    ) -> AgentRunResult | None:
        _ = context
        _ = error
        return None


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        self._agents[agent.name.value] = agent

    def get(self, name: AgentName | str) -> BaseAgent:
        key = name.value if isinstance(name, AgentName) else str(name or "").strip()
        agent = self._agents.get(key)
        if agent is None:
            raise KeyError(f"Agent not registered: {key}")
        return agent

    def names(self) -> list[str]:
        return sorted(self._agents)
