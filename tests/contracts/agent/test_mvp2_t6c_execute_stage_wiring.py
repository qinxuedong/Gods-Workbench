"""QIN-455（MVP2-T6c）：执行当前阶段时接线生产能力工具与产物写回契约。

覆盖票面 AC：
- AC1 门禁已 approved（或 free 工具）时，``execute_current_stage`` 调用已注册
  mock 工具并记录入参；
- AC2 产物写回契约调用点存在，可断言每次阶段尝试恰好调用一次，未注入时明确 skip；
- AC3 工具失败时不丢已批准的产物引用：产物版本仍可读、门禁台账仍为 approved；
- AC4 全链路返回值与运行时状态不含绝对路径与凭据，且测试不连外网。
"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gw.agent_episode.gates import GateKind, GateState
from gw.agent_episode.scoring import STAGE_WEIGHTS
from gw.agent_runtime.models import (
    FinishReason,
    Identifier,
    MessageRole,
    ModelCapabilities,
    ModelInvocationRequest,
    ModelInvocationResult,
    StageId,
    ToolCostState,
    Usage,
)
from gw.agent_runtime.policy import ToolRegistration

_PASSWORD = "Test2026"
_ORIGIN_HEADERS = {"Origin": "http://localhost"}

# 阶段工具登记：与 T4/T5 注册契约同名同 schema 形状，测试内以 mock handler 替换实现。
_STAGE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["run_id", "stage", "idempotency_key"],
    "properties": {
        "run_id": {"type": "string", "minLength": 1},
        "stage": {"type": "string", "enum": ["full_script", "scene_script"]},
        "idempotency_key": {"type": "string", "minLength": 8},
        "instructions": {"type": "string", "maxLength": 4000},
    },
}


class WritingGateway:
    """确定性模型替身：只为 brief / outline_characters / full_script 给出合法产物。"""

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
        properties = (request.output_schema or {}).get("properties", {})
        if "action" in properties:
            payload: dict[str, Any] = {
                "action": "create",
                "capability": None,
                "target_stage": None,
                "question": None,
            }
        elif "artifact_content" in properties:
            context = json.loads(request.messages[-1].content)
            payload = {
                "artifact_content": _creator_payload(
                    StageId(context["stage"]), context
                ),
                "revision_responses": [],
            }
        elif "dimension_scores" in properties:
            context = json.loads(request.messages[-1].content)
            stage = StageId(context["stage"])
            payload = {
                "dimension_scores": {
                    dimension: {
                        "score": 10,
                        "evidence": [f"合成证据：{dimension} 已按阶段契约核对。"],
                    }
                    for dimension in STAGE_WEIGHTS[stage]
                },
                "findings": [],
                "summary": "合成评审判定该阶段产物通过全部确定性检查。",
                "rollback_proposal": None,
            }
        else:
            raise AssertionError("未知的模型输出 schema，测试替身拒绝猜测。")
        return ModelInvocationResult(
            content=json.dumps(payload, ensure_ascii=False),
            tool_calls=[],
            finish_reason=FinishReason.stop,
            upstream_request_id=Identifier(
                f"fake-upstream-{len(self.invocations)}"
            ),
            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
            usage_unavailable_reason=None,
        )


def _creator_payload(stage: StageId, context: dict[str, Any]) -> dict[str, Any]:
    if stage is StageId.brief:
        return {
            "creative_goal": "验证阶段能力工具与产物写回契约的接线。",
            "intended_audience": "内部合同测试读者",
            "format_description": "一段简洁的合成文本简报。",
            "constraints": ["只使用合成测试内容。"],
            "acceptance_criteria": ["阶段产物通过确定性程序检查。"],
            "duration_request": None,
            "clarifying_questions": [],
        }
    if stage is StageId.outline_characters:
        return {
            "source_brief_version_id": context["input_refs"]["brief"]["version_id"],
            "logline": "合成主角在单一场景内完成一次验证任务。",
            "sections": [
                {
                    "section_id": "S01",
                    "title": "开场",
                    "summary": "合成主角进入场景并说明任务目标。",
                    "causal_purpose": "建立阶段接线所需的因果链。",
                }
            ],
            "characters": [
                {
                    "character_id": "CH01",
                    "name": "合成主角",
                    "motivation": "完成一次可验证的阶段接线。",
                    "description": "仅存在于合同测试中的角色。",
                    "arc": "从不确定到验证通过。",
                }
            ],
            "relationships": [],
        }
    if stage is StageId.full_script:
        return {
            "source_brief_version_id": context["input_refs"]["brief"]["version_id"],
            "source_outline_version_id": context["input_refs"]["outline_characters"][
                "version_id"
            ],
            "title": "合成测试剧本",
            "scenes": [
                {
                    "scene_id": "SC01",
                    "title": "开场",
                    "time": "日",
                    "location": "室内",
                    "content": "合成主角说明本次验证的目标并确认结果。",
                    "character_ids": ["CH01"],
                    "outline_section_ids": ["S01"],
                }
            ],
        }
    raise AssertionError(f"测试替身不覆盖阶段 {stage.value}。")


def _new_app(tmp_path, monkeypatch, gateway: WritingGateway):
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
        Path("agent/runtime.sqlite3"),
        trusted_root=paths.data_root,
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
        raise AssertionError("阶段接线测试禁止任何网络连接。")

    original_socket = socket.socket

    class NoNetworkSocket(original_socket):
        def connect(self, address: Any) -> None:
            blocked(address)

        def connect_ex(self, address: Any) -> int:
            blocked(address)

    monkeypatch.setattr(socket, "socket", NoNetworkSocket)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def _open_client(app) -> TestClient:
    return TestClient(
        app,
        base_url="http://localhost",
        headers=_ORIGIN_HEADERS,
        client=("127.0.0.1", 50000),
    )


def _create_run(client: TestClient) -> tuple[str, str]:
    setup = client.post(
        "/api/asset-auth/local/setup",
        json={"username": "stage-wiring-owner", "password": _PASSWORD},
    )
    assert setup.status_code == 201, setup.text
    created = client.post(
        "/api/asset-registry/projects",
        json={"name": "Stage wiring fixture", "project_type": "film"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["project"]["project_id"]

    profiles = client.get("/api/agent/config").json()["profiles"]
    profile_id = next(
        item["config_snapshot_id"] for item in profiles if item["mode"] == "automatic"
    )
    response = client.post(
        "/api/agent/runs",
        json={
            "project_id": project_id,
            "user_goal": "完成一次合成简报并验证阶段能力工具接线。",
            "provider_id": "fake",
            "model": "fake-model",
            "mode": "automatic",
            "config_snapshot_id": profile_id,
            "input_artifact_refs": [],
            "idempotency_key": "stage-wiring-create-1",
            "request_id": "request:stage-wiring-create-1",
        },
    )
    assert response.status_code == 202, response.text
    return project_id, response.json()["run_id"]


def _advance_stages(client: TestClient, run_id: str, stages: int) -> None:
    """每次 worker 推进一个阶段；自动模式通过后进入下一阶段。"""
    integration = client.app.state.agent_integration
    for _ in range(stages):
        client.portal.call(integration.worker.run_once)


def _register_stage_tool(service, *, name: str, handler, cost_state=ToolCostState.free):
    service.registry.register_tool(
        ToolRegistration(
            name=name,
            handler=handler,
            input_schema=_STAGE_TOOL_SCHEMA,
            cost_state=cost_state,
            idempotency_supported=True,
        )
    )


def _approved_gate(client: TestClient, run_id: str, gate_id: str = "gate:script"):
    service = client.app.state.agent_integration.service
    gate = service.gate_store.create(
        gate_id=Identifier(gate_id),
        run_id=Identifier(run_id),
        kind=GateKind.script,
        request_id="request:stage-wiring-gate",
    )
    return service.gate_store.transition(
        gate_id=gate.gate_id,
        target=GateState.approved,
        actor_id=Identifier("actor:stage-wiring-owner"),
        reason="用户批准剧本阶段门禁。",
        expected_version=0,
        request_id="request:stage-wiring-gate-approve",
    )


def _runtime_state(client: TestClient, run_id: str) -> dict[str, Any]:
    return client.portal.call(
        client.app.state.agent_integration.repository.read_runtime_state,
        Identifier(run_id),
    )


def _assert_no_paths_or_credentials(payload: Any) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    assert not re.search(r"[A-Za-z]:[\\/]", serialized)
    assert "\\\\" not in serialized.replace("\\\\n", "")
    assert not re.search(
        r"(?i)api[-_ ]?key|access[-_ ]?token|credential|password|secret|bearer\s+\S+",
        serialized,
    )


def test_execute_stage_invokes_registered_tool_and_writeback_once_per_attempt(
    tmp_path, monkeypatch
) -> None:
    gateway = WritingGateway()
    app = _new_app(tmp_path, monkeypatch, gateway)
    with _open_client(app) as client:
        _deny_network(monkeypatch)
        _, run_id = _create_run(client)
        service = client.app.state.agent_integration.service
        tool_calls: list[dict[str, Any]] = []
        writebacks: list[dict[str, Any]] = []

        async def record_tool(arguments: dict[str, Any]) -> dict[str, str]:
            tool_calls.append(dict(arguments))
            return {"status": "accepted"}

        _register_stage_tool(service, name="script_generate", handler=record_tool)
        service.artifact_writeback = writebacks.append

        _advance_stages(client, run_id, 3)

        assert [call["stage"] for call in tool_calls] == ["full_script"]
        assert tool_calls[0]["run_id"] == run_id
        assert tool_calls[0]["idempotency_key"].startswith("stage-tool:")
        assert "script_generate" in tool_calls[0]["idempotency_key"]

        # AC2：每次阶段尝试恰好写回一次，且写回的是该次尝试的真实产物版本。
        assert len(writebacks) == 3
        assert len({item["task_id"] for item in writebacks}) == 3
        assert [item["stage"] for item in writebacks] == [
            "brief",
            "outline_characters",
            "full_script",
        ]
        assert all(item["artifact_version_id"] for item in writebacks)
        assert writebacks[-1]["gate"] == "pass"

        state = _runtime_state(client, run_id)
        assert state["stage_capabilities"]["full_script"]["invoked"] == [
            {
                "tool": "script_generate",
                "stage": "full_script",
                "idempotency_key": tool_calls[0]["idempotency_key"],
            }
        ]
        assert state["stage_artifact_writeback"]["status"] == "written"

        page = client.portal.call(service.list_events, Identifier(run_id), None, 100)
        assert "stage.capability.recorded" in {item.event_type for item in page.events}

        run = client.portal.call(
            client.app.state.agent_integration.repository.read_run_by_id,
            Identifier(run_id),
        )
        _assert_no_paths_or_credentials(run.model_dump(mode="json"))
        _assert_no_paths_or_credentials(state)
        _assert_no_paths_or_credentials([item.model_dump(mode="json") for item in page.events])


def test_metered_tool_is_skipped_until_stage_gate_is_approved(
    tmp_path, monkeypatch
) -> None:
    app = _new_app(tmp_path, monkeypatch, WritingGateway())
    with _open_client(app) as client:
        _deny_network(monkeypatch)
        _, run_id = _create_run(client)
        service = client.app.state.agent_integration.service
        tool_calls: list[dict[str, Any]] = []

        async def record_tool(arguments: dict[str, Any]) -> dict[str, str]:
            tool_calls.append(dict(arguments))
            return {"status": "accepted"}

        _register_stage_tool(
            service,
            name="script_generate",
            handler=record_tool,
            cost_state=ToolCostState.metered,
        )

        _advance_stages(client, run_id, 3)

        assert tool_calls == []
        state = _runtime_state(client, run_id)
        assert {
            "tool": "script_generate",
            "reason": "gate_not_approved",
        } in state["stage_capabilities"]["full_script"]["skipped"]
        assert state["stage_capabilities"]["full_script"]["invoked"] == []


def test_approved_stage_gate_authorizes_one_metered_tool_call(
    tmp_path, monkeypatch
) -> None:
    app = _new_app(tmp_path, monkeypatch, WritingGateway())
    with _open_client(app) as client:
        _deny_network(monkeypatch)
        _, run_id = _create_run(client)
        service = client.app.state.agent_integration.service
        tool_calls: list[dict[str, Any]] = []
        gate = _approved_gate(client, run_id)

        async def record_tool(arguments: dict[str, Any]) -> dict[str, str]:
            tool_calls.append(dict(arguments))
            return {"status": "accepted"}

        _register_stage_tool(
            service,
            name="script_generate",
            handler=record_tool,
            cost_state=ToolCostState.metered,
        )

        _advance_stages(client, run_id, 3)

        assert [call["stage"] for call in tool_calls] == ["full_script"]
        state = _runtime_state(client, run_id)
        assert len(state["stage_capabilities"]["full_script"]["invoked"]) == 1
        assert service.gate_store.get(gate.gate_id) == gate


def test_tool_failure_keeps_approved_artifact_reference_and_gate_ledger(
    tmp_path, monkeypatch
) -> None:
    app = _new_app(tmp_path, monkeypatch, WritingGateway())
    with _open_client(app) as client:
        _deny_network(monkeypatch)
        _, run_id = _create_run(client)
        integration = client.app.state.agent_integration
        service = integration.service
        writebacks: list[dict[str, Any]] = []
        gate = _approved_gate(client, run_id)

        async def failing_tool(_arguments: dict[str, Any]) -> dict[str, str]:
            raise RuntimeError("合成工具故障")

        _register_stage_tool(service, name="script_generate", handler=failing_tool)
        service.artifact_writeback = writebacks.append

        _advance_stages(client, run_id, 3)

        state = _runtime_state(client, run_id)
        assert state["stage_capabilities"]["full_script"]["failed"] == [
            {"tool": "script_generate", "code": "UPSTREAM_UNAVAILABLE"}
        ]
        assert state["stage_capabilities"]["full_script"]["invoked"] == []

        # AC3：已批准产物引用与门禁台账都不因工具失败而丢失。
        script_version_id = state["current_artifacts_by_stage"]["full_script"]
        artifact = client.portal.call(
            service.artifacts.get_version,
            Identifier(writebacks[-1]["artifact_id"]),
            Identifier(script_version_id),
        )
        assert artifact is not None
        assert artifact.version_id.root == script_version_id
        assert client.portal.call(
            service.artifacts.read_content,
            artifact.artifact_id,
            artifact.version_id,
        )
        stored_gate = service.gate_store.get(gate.gate_id)
        assert stored_gate is not None
        assert stored_gate.state is GateState.approved
        assert stored_gate.version.root == 1

        run = client.portal.call(integration.repository.read_run_by_id, Identifier(run_id))
        assert run.status.value != "failed"


def test_writeback_port_absence_is_an_explicit_skip(tmp_path, monkeypatch) -> None:
    app = _new_app(tmp_path, monkeypatch, WritingGateway())
    with _open_client(app) as client:
        _deny_network(monkeypatch)
        _, run_id = _create_run(client)

        _advance_stages(client, run_id, 1)

        state = _runtime_state(client, run_id)
        assert state["stage_artifact_writeback"] == {
            "status": "skipped",
            "reason": "writeback_port_not_configured",
        }
