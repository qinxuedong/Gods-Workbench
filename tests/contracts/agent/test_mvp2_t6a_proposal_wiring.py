"""QIN-453：规划结果接线、fail closed 与本地台账契约。"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gw.agent_episode.gates import GateKind, GateState, GateStore
from gw.agent_runtime.models import (
    FinishReason,
    Identifier,
    MessageRole,
    ModelCapabilities,
    ModelInvocationRequest,
    ModelInvocationResult,
)
from gw.agent_runtime.policy import (
    OrchestrationProposal,
    ToolRegistration,
)
from gw.agent_runtime.storage.sqlite import SQLiteDatabase

_PASSWORD = "Test2026"
_ORIGIN_HEADERS = {"Origin": "http://localhost"}


class FakeGateway:
    """规划器由测试替身接线；任何模型调用都视为测试失败。"""

    def __init__(self) -> None:
        self.invocations: list[ModelInvocationRequest] = []

    async def describe_capabilities(self, model_ref: Identifier) -> ModelCapabilities:
        return ModelCapabilities(
            model_ref=model_ref,
            supported_roles=list(MessageRole),
            structured_output=True,
            tool_calls=False,
            streaming=False,
            multimodal=False,
            lookup=False,
            cancellation=False,
            usage_reporting=True,
            max_context_tokens=None,
        )

    async def invoke(self, request: ModelInvocationRequest) -> ModelInvocationResult:
        self.invocations.append(request)
        raise AssertionError("本测试不得调用模型网关。")


def _new_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: FakeGateway):
    data_root = tmp_path / "data"
    auth_db = tmp_path / "auth" / "auth.sqlite3"
    video_root = tmp_path / "video"
    for path in (data_root, auth_db.parent, video_root):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(data_root))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(auth_db))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(video_root))
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_DISABLE_AGENT_INTEGRATION", "0")
    monkeypatch.setenv("GW_AGENT_MAX_MODEL_CALLS_PER_RUN", "16")

    from gw.api.agent_integration import build_agent_integration
    from gw.api.app import create_app
    from gw.core.runtime_paths import resolve_runtime_paths

    paths = resolve_runtime_paths()

    def model_resolver(provider_id: str | None, model: str | None) -> dict[str, str]:
        return {"provider_id": provider_id or "fake", "model": model or "fake-model"}

    def configuration_provider() -> dict[str, Any]:
        return {
            "default_provider_id": "fake",
            "providers": [
                {"provider_id": "fake", "configured": True, "models": ["fake-model"]}
            ],
        }

    return create_app(
        agent_integration_factory=lambda: build_agent_integration(
            gateway=gateway,
            runtime_paths=paths,
            model_resolver=model_resolver,
            configuration_provider=configuration_provider,
            start_worker=False,
        )
    )


def _deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("规划接线测试禁止任何网络连接。")

    original_socket = socket.socket

    class NoNetworkSocket(original_socket):
        def connect(self, address: Any) -> None:
            blocked(address)

        def connect_ex(self, address: Any) -> int:
            blocked(address)

    monkeypatch.setattr(socket, "socket", NoNetworkSocket)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def _setup_project(client: TestClient) -> str:
    setup = client.post(
        "/api/asset-auth/local/setup",
        json={"username": "proposal-owner", "password": _PASSWORD},
    )
    assert setup.status_code == 201, setup.text
    created = client.post(
        "/api/asset-registry/projects",
        json={"name": "Proposal wiring fixture", "project_type": "film"},
    )
    assert created.status_code == 201, created.text
    return created.json()["project"]["project_id"]


def _create_run(client: TestClient, project_id: str) -> str:
    profiles = client.get("/api/agent/config").json()["profiles"]
    profile_id = next(
        item["config_snapshot_id"] for item in profiles if item["mode"] == "approval"
    )
    response = client.post(
        "/api/agent/runs",
        json={
            "project_id": project_id,
            "user_goal": "验证规划提案接线，不执行生产工具。",
            "provider_id": "fake",
            "model": "fake-model",
            "mode": "approval",
            "config_snapshot_id": profile_id,
            "input_artifact_refs": [],
            "idempotency_key": "proposal-wiring-create-1",
            "request_id": "request:proposal-wiring-create-1",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def _connect_fake_planner(integration: Any, proposal: OrchestrationProposal):
    calls: list[dict[str, Any]] = []

    async def propose_stage_action(**arguments: Any) -> dict[str, Any]:
        calls.append(arguments)
        return {"orchestration_proposal": proposal}

    integration.service.graph.propose_stage_action = propose_stage_action
    return calls


def test_valid_fake_planner_proposal_is_recorded_and_readable_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = FakeGateway()
    app = _new_app(tmp_path, monkeypatch, gateway)
    with TestClient(
        app,
        base_url="http://localhost",
        headers=_ORIGIN_HEADERS,
        client=("127.0.0.1", 50000),
    ) as client:
        _deny_network(monkeypatch)
        project_id = _setup_project(client)
        run_id = _create_run(client, project_id)
        integration = client.app.state.agent_integration
        question = "请确认本阶段文本的目标受众。"
        proposal = OrchestrationProposal(
            action="clarify",
            capability=None,
            target_stage=None,
            question=question,
        )
        planner_calls = _connect_fake_planner(integration, proposal)

        client.portal.call(integration.worker.run_once)

        state = client.portal.call(
            integration.repository.read_runtime_state, Identifier(run_id)
        )
        page = client.portal.call(
            integration.service.list_events, Identifier(run_id), None, 100
        )
        returned = client.get(f"/api/agent/runs/{run_id}")
        assert returned.status_code == 200, returned.text
        event_types = [event.event_type for event in page.events]
        assert "orchestration.proposal.recorded" in event_types
        assert "orchestration.clarification.requested" in event_types
        assert state is not None
        question_record = state["clarification_record"]["questions"][0]
        assert question_record["text"] == question
        assert len(planner_calls) == 1
        assert gateway.invocations == []

        proposal_fields = integration.service._proposal_payload(proposal)
        assert set(proposal_fields) == {
            "action",
            "capability",
            "target_stage",
            "question",
        }
        assert set(question_record) == {"question_id", "text"}
        public_fields = json.dumps(
            {"proposal": proposal_fields, "clarification": question_record},
            ensure_ascii=False,
        )
        assert not re.search(r"(?i)api[-_]?key|token|secret|password|authorization|credential", public_fields)
        assert not re.search(r"[A-Za-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+", public_fields)


def test_invalid_fake_planner_action_fails_closed_before_tool_or_gate_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = FakeGateway()
    app = _new_app(tmp_path, monkeypatch, gateway)
    with TestClient(
        app,
        base_url="http://localhost",
        headers=_ORIGIN_HEADERS,
        client=("127.0.0.1", 50000),
    ) as client:
        _deny_network(monkeypatch)
        project_id = _setup_project(client)
        run_id = _create_run(client, project_id)
        integration = client.app.state.agent_integration
        gate_id = Identifier("gate:proposal-wiring")
        gate_store = GateStore(
            SQLiteDatabase(tmp_path / "gates.sqlite3", trusted_root=tmp_path)
        )
        gate_store.create(
            gate_id=gate_id,
            run_id=Identifier(run_id),
            kind=GateKind.i2v_submit,
            request_id="gate-create:proposal-wiring",
        )
        tool_calls: list[dict[str, Any]] = []

        async def mutate_gate(arguments: dict[str, Any]) -> dict[str, bool]:
            tool_calls.append(dict(arguments))
            gate_store.transition(
                gate_id=gate_id,
                target=GateState.approved,
                actor_id=Identifier("actor:proposal-wiring-test"),
                reason="测试检测到错误派发。",
                expected_version=0,
                request_id="gate-transition:proposal-wiring",
            )
            return {"ok": True}

        integration.service.registry.register_tool(
            ToolRegistration(
                name="i2v_generate",
                handler=mutate_gate,
                input_schema={"type": "object", "additionalProperties": False},
            )
        )
        invalid = OrchestrationProposal(
            action="i2v_generate",
            capability="i2v_generate",
        )
        planner_calls = _connect_fake_planner(integration, invalid)

        client.portal.call(integration.worker.run_once)

        state = client.portal.call(
            integration.repository.read_runtime_state, Identifier(run_id)
        )
        page = client.portal.call(
            integration.service.list_events, Identifier(run_id), None, 100
        )
        attempts = client.portal.call(
            integration.repository.get_attempts, Identifier(run_id)
        )
        event_types = [event.event_type for event in page.events]
        assert len(planner_calls) == 1
        assert "orchestration.proposal.recorded" not in event_types
        assert not any("gate" in event_type for event_type in event_types)
        assert not any("tool" in event_type for event_type in event_types)
        assert state is not None and "pending_plan_proposal" not in state
        assert attempts == []
        assert tool_calls == []
        assert gateway.invocations == []
        gate = gate_store.get(gate_id)
        assert gate is not None
        assert gate.state is GateState.pending
        assert gate.version.root == 0
        assert gate.history == ()


def test_planner_path_and_credential_text_is_rejected_before_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = FakeGateway()
    app = _new_app(tmp_path, monkeypatch, gateway)
    with TestClient(
        app,
        base_url="http://localhost",
        headers=_ORIGIN_HEADERS,
        client=("127.0.0.1", 50000),
    ) as client:
        _deny_network(monkeypatch)
        project_id = _setup_project(client)
        run_id = _create_run(client, project_id)
        integration = client.app.state.agent_integration
        unsafe_text = r"X:\fixture\asset.txt api_key=fixture-key-marker"
        proposal = OrchestrationProposal(action="clarify", question=unsafe_text)
        planner_calls = _connect_fake_planner(integration, proposal)

        client.portal.call(integration.worker.run_once)

        state = client.portal.call(
            integration.repository.read_runtime_state, Identifier(run_id)
        )
        page = client.portal.call(
            integration.service.list_events, Identifier(run_id), None, 100
        )
        event_types = [event.event_type for event in page.events]
        assert len(planner_calls) == 1
        assert "orchestration.proposal.recorded" not in event_types
        assert state is not None
        assert "pending_plan_proposal" not in state
        assert "clarification_record" not in state
        persisted = json.dumps(
            {"state": state, "events": [event.model_dump(mode="json") for event in page.events]},
            ensure_ascii=False,
        )
        assert unsafe_text not in persisted
        assert "fixture-key-marker" not in persisted
        assert gateway.invocations == []
