"""LangGraph executor for injected, domain-neutral runtime capabilities."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from gw.agent_runtime.models import ArtifactRef, ConfigSnapshot, Identifier, StageId
from gw.agent_runtime.policy import ORCHESTRATION_ACTIONS, CapabilityRegistry, OrchestrationProposal, ReviewDraft, RuntimePolicy


class RuntimeGraphState(TypedDict, total=False):
    stage: str
    writer_role: str
    reviewer_role: str
    actor_id: Identifier
    execution_input: Mapping[str, Any]
    writer_result: Any
    artifact_input: Any
    artifact: ArtifactRef
    artifact_content: str
    review_draft: ReviewDraft
    review: Any
    gate: Any
    error: str
    artifact_factory: Any
    runtime_context: Any
    score_config: ConfigSnapshot
    existing_candidate: bool
    orchestrator_role: str
    orchestration_proposal: OrchestrationProposal
    routed_action: str


ArtifactFactory = Callable[[Any], Awaitable[tuple[ArtifactRef, str]]]


class LangGraphRuntimeExecutor:
    """Actual LangGraph state-machine execution with explicit injected capabilities.

    The graph only coordinates call order and trusted role selection. Episode rules,
    host calls, persistence truth, and approval eligibility stay outside this graph.
    """

    def __init__(self, registry: CapabilityRegistry, policy: RuntimePolicy) -> None:
        self.registry = registry
        self.policy = policy
        graph = StateGraph(RuntimeGraphState)
        graph.add_node("validate_action", self._validate_action)
        graph.add_node("execute_writer", self._execute_writer)
        graph.add_node("persist_candidate", self._persist_candidate)
        graph.add_node("run_reviewer", self._run_reviewer)
        graph.add_node("apply_gate", self._apply_gate)
        graph.add_edge(START, "validate_action")
        graph.add_edge("validate_action", "execute_writer")
        graph.add_edge("execute_writer", "persist_candidate")
        graph.add_edge("persist_candidate", "run_reviewer")
        graph.add_edge("run_reviewer", "apply_gate")
        graph.add_edge("apply_gate", END)
        self.graph = graph.compile()

        planner = StateGraph(RuntimeGraphState)
        planner.add_node("invoke_orchestrator", self._invoke_orchestrator)
        for action in ("clarify", "select_capability", "create", "review", "revise", "rollback"):
            planner.add_node(f"action_{action}", self._route_action)
        planner.add_edge(START, "invoke_orchestrator")
        planner.add_conditional_edges(
            "invoke_orchestrator",
            self._proposal_route,
            {action: f"action_{action}" for action in ("clarify", "select_capability", "create", "review", "revise", "rollback")},
        )
        for action in ("clarify", "select_capability", "create", "review", "revise", "rollback"):
            planner.add_edge(f"action_{action}", END)
        self.orchestration_graph = planner.compile()

    async def execute_stage_once(
        self,
        *,
        stage: str,
        actor_id: Identifier,
        writer_role: str,
        reviewer_role: str,
        execution_input: Mapping[str, Any],
        artifact_factory: ArtifactFactory,
        runtime_context: Mapping[str, Any] | None = None,
        score_config: ConfigSnapshot | None = None,
        existing_candidate: tuple[ArtifactRef, str] | None = None,
    ) -> RuntimeGraphState:
        initial: dict[str, Any] = {
            "stage": stage,
            "actor_id": actor_id,
            "writer_role": writer_role,
            "reviewer_role": reviewer_role,
            "execution_input": dict(execution_input),
            "artifact_factory": artifact_factory,
            "runtime_context": dict(runtime_context or {}),
            "score_config": score_config,
        }
        if existing_candidate is not None:
            initial.update({"artifact": existing_candidate[0], "artifact_content": existing_candidate[1], "existing_candidate": True})
        return await self._invoke_graph(self.graph, {
            **initial,
        })

    async def propose_stage_action(
        self,
        *,
        stage: str,
        actor_id: Identifier,
        orchestrator_role: str,
        execution_input: Mapping[str, Any],
        runtime_context: Mapping[str, Any],
    ) -> RuntimeGraphState:
        return await self._invoke_graph(self.orchestration_graph, {
            "stage": stage,
            "actor_id": actor_id,
            "orchestrator_role": orchestrator_role,
            "execution_input": dict(execution_input),
            "runtime_context": dict(runtime_context),
        })

    @staticmethod
    async def _invoke_graph(graph: Any, state: Mapping[str, Any]) -> RuntimeGraphState:
        """Never inherit ambient LangSmith callbacks into user-content execution."""
        from langsmith.run_helpers import tracing_context

        with tracing_context(enabled=False, parent=False):
            return await graph.ainvoke(dict(state), config={"callbacks": []})

    async def _validate_action(self, state: RuntimeGraphState) -> dict[str, Any]:
        stage = state.get("stage")
        if not stage:
            raise ValueError("A stage is required for a write/review graph action.")
        self.registry.get_role(state["writer_role"])
        self.registry.get_role(state["reviewer_role"])
        return {"error": ""}

    async def _execute_writer(self, state: RuntimeGraphState) -> dict[str, Any]:
        if state.get("existing_candidate"):
            return {}
        await self._assert_dispatch_allowed(state, state["writer_role"])
        result = await self.registry.invoke_role(
            state["writer_role"],
            state["actor_id"],
            stage=state["stage"],
            input=dict(state["execution_input"]),
            _runtime_context=state.get("runtime_context"),
        )
        return {"writer_result": result}

    async def _persist_candidate(self, state: RuntimeGraphState) -> dict[str, Any]:
        if state.get("existing_candidate"):
            return {}
        await self._assert_dispatch_allowed(state, state["writer_role"])
        value = state["writer_result"]
        if not hasattr(value, "content") or not isinstance(value.content, str):
            raise TypeError("A registered writer must return an ArtifactVersionInput-like object with text content.")
        # Artifact ownership is injected by the runtime service, never by the graph/model.
        factory = state.get("artifact_factory")
        if factory is None:
            raise RuntimeError("No artifact repository factory was injected.")
        artifact, content = await factory(value)
        return {"artifact_input": value, "artifact": artifact, "artifact_content": content}

    async def _run_reviewer(self, state: RuntimeGraphState) -> dict[str, Any]:
        await self._assert_dispatch_allowed(state, state["reviewer_role"])
        draft = await self.registry.invoke_role(
            state["reviewer_role"],
            state["actor_id"],
            stage=state["stage"],
            artifact=state["artifact"],
            content=state["artifact_content"],
            input=dict(state["execution_input"]),
            _runtime_context=state.get("runtime_context"),
        )
        if not isinstance(draft, ReviewDraft):
            raise TypeError("A registered reviewer must return ReviewDraft; model self-reported score/conclusion is not authoritative.")
        return {"review_draft": draft}

    async def _apply_gate(self, state: RuntimeGraphState) -> dict[str, Any]:
        review, gate = self.policy.make_review(
            Identifier(state["execution_input"]["run_id"]),
            state["artifact"],
            state["review_draft"],
            stage=StageId(state["stage"]),
            config=state.get("score_config"),
        )
        return {"review": review, "gate": gate}

    async def _invoke_orchestrator(self, state: RuntimeGraphState) -> dict[str, Any]:
        role = state.get("orchestrator_role")
        if not role:
            raise ValueError("An orchestrator role is required for the planning graph.")
        await self._assert_dispatch_allowed(state, role)
        proposal = await self.registry.invoke_role(
            role,
            state["actor_id"],
            stage=state["stage"],
            input=dict(state["execution_input"]),
            _runtime_context=state.get("runtime_context"),
        )
        if not isinstance(proposal, OrchestrationProposal):
            raise TypeError("The orchestrator must return a structured OrchestrationProposal.")
        if proposal.action not in ORCHESTRATION_ACTIONS:
            raise ValueError("The orchestrator proposed an unsupported action.")
        return {"orchestration_proposal": proposal}

    @staticmethod
    def _proposal_route(state: RuntimeGraphState) -> str:
        proposal = state.get("orchestration_proposal")
        if not isinstance(proposal, OrchestrationProposal):
            raise TypeError("The planner graph has no validated proposal to route.")
        return proposal.action

    @staticmethod
    async def _route_action(state: RuntimeGraphState) -> dict[str, Any]:
        proposal = state.get("orchestration_proposal")
        if not isinstance(proposal, OrchestrationProposal):
            raise TypeError("The planner action branch has no proposal.")
        return {"routed_action": proposal.action}

    @staticmethod
    async def _assert_dispatch_allowed(state: RuntimeGraphState, role: str) -> None:
        context = state.get("runtime_context") or {}
        guard = context.get("assert_dispatch_allowed") if isinstance(context, Mapping) else None
        if guard is not None:
            result = guard(role)
            if hasattr(result, "__await__"):
                await result
