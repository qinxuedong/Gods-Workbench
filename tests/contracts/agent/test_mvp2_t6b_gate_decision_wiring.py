"""QIN-454：非免费工具 Gate 请求与用户决策台账接线。"""

from __future__ import annotations

import json
import re
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gw.agent_episode.gates import GateKind, GateState
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    Identifier,
    MessageRole,
    ModelCapabilities,
    ModelInvocationRequest,
    ModelInvocationResult,
    ToolCostState,
)
from gw.agent_runtime.policy import ToolRegistration
from gw.agent_runtime.service import GateDecisionRequest, ProposedAction

_PASSWORD = "Test2026"
_ORIGIN_HEADERS = {"Origin": "http://localhost"}


class FakeGateway:
    """拒绝外部模型调用的测试替身。"""

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
        raise AssertionError("本测试禁止调用模型网关。")


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

    from gw.agent_runtime.storage.sqlite import SQLiteDatabase
    from gw.api.agent_integration import build_agent_integration
    from gw.api.app import create_app
    from gw.core.runtime_paths import resolve_runtime_paths

    paths = resolve_runtime_paths()
    runtime_db = SQLiteDatabase(
        Path("agent/runtime.sqlite3"), trusted_root=paths.data_root
    )

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
            database=runtime_db,
            runtime_paths=paths,
            model_resolver=model_resolver,
            configuration_provider=configuration_provider,
            start_worker=False,
        )
    )


def _deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Gate 接线测试禁止任何网络连接。")

    original_socket = socket.socket

    class NoNetworkSocket(original_socket):
        def connect(self, address: Any) -> None:
            blocked(address)

        def connect_ex(self, address: Any) -> int:
            blocked(address)

    monkeypatch.setattr(socket, "socket", NoNetworkSocket)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def _setup_run(client: TestClient) -> tuple[str, str]:
    setup = client.post(
        "/api/asset-auth/local/setup",
        json={"username": "gate-owner", "password": _PASSWORD},
    )
    assert setup.status_code == 201, setup.text
    created = client.post(
        "/api/asset-registry/projects",
        json={"name": "Gate decision fixture", "project_type": "film"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["project"]["project_id"]

    profiles = client.get("/api/agent/config").json()["profiles"]
    profile_id = next(
        item["config_snapshot_id"] for item in profiles if item["mode"] == "approval"
    )
    response = client.post(
        "/api/agent/runs",
        json={
            "project_id": project_id,
            "user_goal": "验证门禁请求和决策接线。",
            "provider_id": "fake",
            "model": "fake-model",
            "mode": "approval",
            "config_snapshot_id": profile_id,
            "input_artifact_refs": [],
            "idempotency_key": "gate-decision-create-1",
            "request_id": "request:gate-decision-create-1",
        },
    )
    assert response.status_code == 202, response.text
    return project_id, response.json()["run_id"]


def _portal_call(portal: Any, function: Any, *args: Any, **kwargs: Any) -> Any:
    async def invoke() -> Any:
        return await function(*args, **kwargs)

    return portal.call(invoke)


def _decision_request(
    *,
    run_id: str,
    gate_id: Identifier,
    target: GateState,
    expected_version: int,
    suffix: str,
) -> GateDecisionRequest:
    return GateDecisionRequest(
        request_id=f"request:gate-decision-{suffix}",
        run_id=Identifier(run_id),
        gate_id=gate_id,
        target=target,
        reason="确认本次门禁决定。",
        expected_version=expected_version,
    )


def test_metered_tool_proposal_creates_pending_gate_without_invoking_tool(
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
        _, run_id = _setup_run(client)
        integration = client.app.state.agent_integration
        service = integration.service
        actor_id = Identifier(integration.get_binding(run_id)["actor_id"])
        tool_calls: list[Mapping[str, object]] = []

        async def submit_i2v(arguments: Mapping[str, object]) -> dict[str, str]:
            tool_calls.append(arguments)
            return {"status": "submitted"}

        service.registry.register_tool(
            ToolRegistration(
                name="i2v_submit",
                handler=submit_i2v,
                cost_state=ToolCostState.metered,
            )
        )
        record = _portal_call(
            client.portal,
            service.dispatch_proposal,
            actor_id,
            ProposedAction(kind="tool", capability="i2v_submit", arguments={}),
            run_id=Identifier(run_id),
            gate_kind=GateKind.i2v_submit,
        )

        assert record.kind is GateKind.i2v_submit
        assert record.state is GateState.pending
        assert record.version.root == 0
        assert service.gate_store.get(record.gate_id) == record
        assert tool_calls == []
        assert gateway.invocations == []


def test_review_decisions_write_all_gate_states_and_reject_invalid_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _new_app(tmp_path, monkeypatch, FakeGateway())
    with TestClient(
        app,
        base_url="http://localhost",
        headers=_ORIGIN_HEADERS,
        client=("127.0.0.1", 50000),
    ) as client:
        _deny_network(monkeypatch)
        _, run_id = _setup_run(client)
        integration = client.app.state.agent_integration
        service = integration.service
        actor_id = Identifier(integration.get_binding(run_id)["actor_id"])

        records = [
            service.gate_store.create(
                gate_id=Identifier(f"gate:{state.value}"),
                run_id=Identifier(run_id),
                kind=GateKind.script,
                request_id="request:gate-create",
            )
            for state in (
                GateState.approved,
                GateState.changes_requested,
                GateState.rolled_back,
            )
        ]
        for index, (record, target) in enumerate(
            zip(
                records,
                (
                    GateState.approved,
                    GateState.changes_requested,
                    GateState.rolled_back,
                ),
                strict=True,
            )
        ):
            request = _decision_request(
                run_id=run_id,
                gate_id=record.gate_id,
                target=target,
                expected_version=0,
                suffix=str(index),
            )
            updated = _portal_call(
                client.portal,
                service.submit_review_decision,
                request,
                actor_id=actor_id,
            )
            assert updated.state is target
            assert service.gate_store.get(record.gate_id) == updated

        changed = service.gate_store.get(records[1].gate_id)
        assert changed is not None
        request = _decision_request(
            run_id=run_id,
            gate_id=records[1].gate_id,
            target=GateState.approved,
            expected_version=changed.version.root,
            suffix="invalid-transition",
        )
        with pytest.raises(DomainError):
            _portal_call(
                client.portal,
                service.submit_review_decision,
                request,
                actor_id=actor_id,
            )
        assert service.gate_store.get(records[1].gate_id) == changed


def test_gate_decision_rejects_paths_and_credentials_without_persisting_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _new_app(tmp_path, monkeypatch, FakeGateway())
    with TestClient(
        app,
        base_url="http://localhost",
        headers=_ORIGIN_HEADERS,
        client=("127.0.0.1", 50000),
    ) as client:
        _deny_network(monkeypatch)
        _, run_id = _setup_run(client)
        integration = client.app.state.agent_integration
        service = integration.service
        actor_id = Identifier(integration.get_binding(run_id)["actor_id"])
        gate = service.gate_store.create(
            gate_id=Identifier("gate:sensitive-reason"),
            run_id=Identifier(run_id),
            kind=GateKind.i2v_submit,
            request_id="request:gate-create",
        )

        for reason in (r"素材路径 C:\<本地素材库路径>", "api_key=secret-value"):
            request = GateDecisionRequest(
                request_id="request:sensitive-gate-reason",
                run_id=Identifier(run_id),
                gate_id=gate.gate_id,
                target=GateState.approved,
                reason=reason,
                expected_version=0,
            )
            with pytest.raises(DomainError):
                _portal_call(
                    client.portal,
                    service.submit_review_decision,
                    request,
                    actor_id=actor_id,
                )

        stored = service.gate_store.get(gate.gate_id)
        assert stored == gate
        serialized = json.dumps(stored.model_dump(mode="json"), ensure_ascii=False)
        assert not re.search(r"[A-Za-z]:[\\/]", serialized)
        assert not re.search(
            r"(?i)api[-_ ]?key|token|secret|password|credential", serialized
        )
