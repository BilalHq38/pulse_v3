from __future__ import annotations

import logging

from agent_orchestrator.schemas import AgentName, WorkflowKind, WorkflowRouteDecision

logger = logging.getLogger(__name__)


def _log_route(context, decision: WorkflowRouteDecision) -> WorkflowRouteDecision:
    logger.info(
        "agent_route_selected workflow_id=%s current_agent=%s next_agent=%s decision_mode=%s reason=%s qualifiers=%s",
        str(getattr(context, "workflow_id", "") or ""),
        decision.current_agent,
        decision.next_agent,
        decision.decision_mode,
        decision.reason,
        decision.qualifiers,
    )
    return decision


class AgentRouter:
    def __init__(self) -> None:
        self._sequences: dict[WorkflowKind, list[AgentName]] = {
            WorkflowKind.MESSAGE: [
                AgentName.CAPTURE,
                AgentName.QUALIFICATION,
                AgentName.SUPPORT,
                AgentName.ANALYTICS,
            ],
            WorkflowKind.LEAD: [
                AgentName.CAPTURE,
                AgentName.QUALIFICATION,
                AgentName.SUPPORT,
                AgentName.ANALYTICS,
            ],
        }

    def register_sequence(self, workflow_kind: WorkflowKind, sequence: list[AgentName]) -> None:
        if not sequence:
            raise ValueError("Agent sequence must not be empty")
        self._sequences[workflow_kind] = list(sequence)

    def next_agent(
        self,
        context,
        previous_agent: AgentName | None,
    ) -> WorkflowRouteDecision:
        sequence = list(self._sequences.get(context.workflow_kind, []))
        if previous_agent is None:
            return _log_route(context, WorkflowRouteDecision(
                current_agent="",
                next_agent=sequence[0].value if sequence else "",
                decision_mode="rule_based",
                reason="Workflow started.",
            ))
        if previous_agent not in sequence:
            return _log_route(context, WorkflowRouteDecision(
                current_agent=previous_agent.value,
                next_agent="",
                decision_mode="rule_based",
                reason="No sequence registered for this agent.",
            ))
        current_index = sequence.index(previous_agent)
        candidate = sequence[current_index + 1] if current_index + 1 < len(sequence) else None
        if candidate is None:
            return _log_route(context, WorkflowRouteDecision(
                current_agent=previous_agent.value,
                next_agent="",
                decision_mode="rule_based",
                reason="Workflow sequence completed.",
            ))
        if candidate == AgentName.SUPPORT:
            qualification = dict(getattr(context.agent_outputs, "qualification", {}) or {})
            capture = dict(getattr(context.agent_outputs, "capture", {}) or {})
            route_to_support = bool(
                qualification.get("route_to_support") or context.workflow_kind == WorkflowKind.MESSAGE
            )
            if not route_to_support:
                candidate = AgentName.ANALYTICS
            return _log_route(context, WorkflowRouteDecision(
                current_agent=previous_agent.value,
                next_agent=candidate.value,
                decision_mode="hybrid",
                reason=(
                    f"Intent={((capture.get('intent') or {}).get('intent') or 'unknown')} "
                    f"classification={qualification.get('classification', 'unknown')}"
                ),
                qualifiers=[
                    str((capture.get("intent") or {}).get("intent") or ""),
                    str(qualification.get("classification") or ""),
                ],
            ))
        if candidate == AgentName.ANALYTICS:
            support = dict(getattr(context.agent_outputs, "support", {}) or {})
            return _log_route(context, WorkflowRouteDecision(
                current_agent=previous_agent.value,
                next_agent=candidate.value,
                decision_mode="hybrid",
                reason=(
                    "Workflow routed to analytics after support."
                    if previous_agent == AgentName.SUPPORT
                    else f"Lead status={support.get('next_action', 'analytics')}"
                ),
                qualifiers=[str(support.get("next_action") or "")],
            ))
        return _log_route(context, WorkflowRouteDecision(
            current_agent=previous_agent.value,
            next_agent=candidate.value,
            decision_mode="rule_based",
            reason=f"Sequence advanced from {previous_agent.value} to {candidate.value}.",
        ))
