"""MVP2-T4 前段工具注册契约测试（剧本 / 分镜 / 样片）。

覆盖票面 AC1~AC5 与原 Jules PR #371 审核 WARN 的返工项：
- 走 `CapabilityRegistry.invoke_tool` + 显式预算（metered），不绕过 schema/预算门禁；
- 路径清洗同时拒绝 POSIX 绝对路径、Windows 盘符与 UNC 风格字符串，并递归检查 dict key；
- 凭据字段 fail-closed 抛错（含变体键名），不再静默剥离；
- service 任意异常收口为 `UPSTREAM_UNAVAILABLE`；
- 注册后断言 `animatic_generate` 不存在（后端无样片生成入口）。
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from jsonschema import Draft202012Validator

from gw.agent_runtime.adapters.pipeline_tools import register_pipeline_tools
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    BudgetDecision,
    BudgetState,
    ErrorCode,
    Identifier,
    Reason,
    ToolCostState,
    ToolRiskLevel,
)
from gw.agent_runtime.policy import CapabilityRegistry

ACTOR = Identifier("actor-mvp2-t4")
RUN_ID = "run-mvp2-t4"
IDEMPOTENCY_KEY = "mvp2-t4-key-0001"


class FakePipelineService:
    """网络无关的前段生成服务假件：记录调用参数，返回可控 payload。"""

    def __init__(
        self, payload: dict[str, Any] | None = None, *, error: Exception | None = None
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.payload = payload if payload is not None else _clean_payload()
        self.error = error

    async def generate(
        self,
        *,
        run_id: str,
        stage: str,
        instructions: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "run_id": run_id,
                "stage": stage,
                "instructions": instructions,
                "idempotency_key": idempotency_key,
            }
        )
        if self.error is not None:
            raise self.error
        return dict(self.payload)


def _clean_payload() -> dict[str, Any]:
    """相对路径、无凭据的干净返回值。"""
    return {
        "stage": "full_script",
        "candidate_id": "cand-001",
        "content": "第一场：雨夜码头。",
        "sources": ["artifact:brief-v001"],
        "location": "assets/script/candidate.md",
    }


def _budget() -> BudgetDecision:
    """metered 工具必需的显式允许预算。"""
    return BudgetDecision(
        state=BudgetState.allowed,
        amount=None,
        currency=None,
        reason=Reason("MVP2-T4 前段工具契约测试预算"),
    )


def _services() -> dict[str, FakePipelineService]:
    return {"script": FakePipelineService(), "storyboard": FakePipelineService()}


def _build_registry(services: dict[str, Any]) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    register_pipeline_tools(registry, services=services)
    return registry


def test_ac1_register_pipeline_tools_registers_script_and_storyboard():
    """AC1：注册剧本与分镜工具，且返回已注册工具名。"""
    registry = CapabilityRegistry()
    registered = register_pipeline_tools(registry, services=_services())

    assert set(registered) == {"script_generate", "storyboard_generate"}
    for name in ("script_generate", "storyboard_generate"):
        assert registry.get_tool(name).name == name


def test_ac1_registered_tools_are_medium_metered_and_idempotent():
    """AC1：risk_level=medium、cost_state=metered、支持幂等。"""
    registry = _build_registry(_services())

    for name in ("script_generate", "storyboard_generate"):
        tool = registry.get_tool(name)
        assert tool.risk_level is ToolRiskLevel.medium
        assert tool.cost_state is ToolCostState.metered
        assert tool.idempotency_supported is True


def test_ac1_input_schemas_are_valid_draft_2020_12_with_expected_stages():
    """AC1：schema 通过 Draft 2020-12 校验并绑定票面 stage 枚举。"""
    registry = _build_registry(_services())

    for name, stages in (
        ("script_generate", ["full_script", "scene_script"]),
        ("storyboard_generate", ["storyboard_text"]),
    ):
        schema = dict(registry.get_tool(name).input_schema)
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"run_id", "stage", "idempotency_key"}
        assert schema["properties"]["stage"]["enum"] == stages
        assert schema["properties"]["idempotency_key"]["minLength"] == 8


def test_animatic_generate_is_not_registered():
    """后端无样片生成入口：注册后 `animatic_generate` 必须不存在。"""
    registry = _build_registry(_services())
    with pytest.raises(DomainError) as excinfo:
        registry.get_tool("animatic_generate")
    assert excinfo.value.code is ErrorCode.CAPABILITY_UNSUPPORTED


def test_unknown_service_key_is_rejected():
    """未知 service 键（含 animatic）必须显式失败，不静默忽略。"""
    registry = CapabilityRegistry()
    with pytest.raises(ValueError, match="animatic"):
        register_pipeline_tools(
            registry, services={"script": FakePipelineService(), "animatic": object()}
        )


def test_ac2_handler_invokes_injected_service_via_invoke_tool():
    """AC2：经 invoke_tool 调用注入 service，记录参数且不触达真实 provider。"""
    services = _services()
    registry = _build_registry(services)

    async def verify() -> None:
        result = await registry.invoke_tool(
            "script_generate",
            ACTOR,
            {
                "run_id": RUN_ID,
                "stage": "full_script",
                "idempotency_key": IDEMPOTENCY_KEY,
                "instructions": "保持雨夜基调",
            },
            budget=_budget(),
        )

        assert services["script"].calls == [
            {
                "run_id": RUN_ID,
                "stage": "full_script",
                "instructions": "保持雨夜基调",
                "idempotency_key": IDEMPOTENCY_KEY,
            }
        ]
        assert result == _clean_payload()
        assert services["storyboard"].calls == []

    anyio.run(verify)


def test_ac2_storyboard_handler_invokes_its_own_service():
    """AC2：分镜工具只调用 storyboard service。"""
    services = _services()
    registry = _build_registry(services)

    async def verify() -> None:
        await registry.invoke_tool(
            "storyboard_generate",
            ACTOR,
            {
                "run_id": RUN_ID,
                "stage": "storyboard_text",
                "idempotency_key": IDEMPOTENCY_KEY,
            },
            budget=_budget(),
        )

        assert len(services["storyboard"].calls) == 1
        assert services["script"].calls == []

    anyio.run(verify)


def test_ac3_same_idempotency_key_invokes_service_once():
    """AC3：同一 idempotency_key 重复调用只触发一次底层调用，返回一致结果。"""
    services = _services()
    registry = _build_registry(services)
    arguments = {
        "run_id": RUN_ID,
        "stage": "full_script",
        "idempotency_key": IDEMPOTENCY_KEY,
    }

    async def verify() -> None:
        first = await registry.invoke_tool(
            "script_generate", ACTOR, arguments, budget=_budget()
        )
        second = await registry.invoke_tool(
            "script_generate", ACTOR, arguments, budget=_budget()
        )

        assert len(services["script"].calls) == 1
        assert first == second == _clean_payload()

    anyio.run(verify)


def test_ac4_unknown_tool_raises_capability_unsupported():
    """AC4：注册后取未注册工具仍抛 CAPABILITY_UNSUPPORTED。"""
    registry = _build_registry(_services())

    async def verify() -> None:
        with pytest.raises(DomainError) as excinfo:
            registry.get_tool("unknown")
        assert excinfo.value.code is ErrorCode.CAPABILITY_UNSUPPORTED

        with pytest.raises(DomainError) as invoked:
            await registry.invoke_tool("unknown", ACTOR, {}, budget=_budget())
        assert invoked.value.code is ErrorCode.CAPABILITY_UNSUPPORTED

    anyio.run(verify)


def test_metered_tool_requires_explicit_budget():
    """metered 工具缺预算必须失败关闭（QUOTA_EXCEEDED）。"""
    registry = _build_registry(_services())

    async def verify() -> None:
        with pytest.raises(DomainError) as excinfo:
            await registry.invoke_tool(
                "script_generate",
                ACTOR,
                {
                    "run_id": RUN_ID,
                    "stage": "full_script",
                    "idempotency_key": IDEMPOTENCY_KEY,
                },
            )
        assert excinfo.value.code is ErrorCode.QUOTA_EXCEEDED

    anyio.run(verify)


def test_short_idempotency_key_is_rejected_by_schema():
    """短幂等键在进入 handler 前被 schema 拒绝。"""
    registry = _build_registry(_services())

    async def verify() -> None:
        with pytest.raises(DomainError) as excinfo:
            await registry.invoke_tool(
                "script_generate",
                ACTOR,
                {"run_id": RUN_ID, "stage": "full_script", "idempotency_key": "short"},
                budget=_budget(),
            )
        assert excinfo.value.code is ErrorCode.VALIDATION_FAILED

    anyio.run(verify)


def test_invalid_stage_is_rejected_by_schema():
    """非本工具 stage 不得通过 schema。"""
    registry = _build_registry(_services())

    async def verify() -> None:
        with pytest.raises(DomainError) as excinfo:
            await registry.invoke_tool(
                "storyboard_generate",
                ACTOR,
                {
                    "run_id": RUN_ID,
                    "stage": "full_script",
                    "idempotency_key": IDEMPOTENCY_KEY,
                },
                budget=_budget(),
            )
        assert excinfo.value.code is ErrorCode.VALIDATION_FAILED

    anyio.run(verify)


def test_ac5_rejects_posix_absolute_path_in_value():
    """AC5：POSIX 绝对路径必须失败关闭。"""
    registry = _build_registry({"script": FakePipelineService({"path": "/srv/gw/out.md"})})

    async def verify() -> None:
        with pytest.raises(DomainError) as excinfo:
            await registry.invoke_tool(
                "script_generate",
                ACTOR,
                {
                    "run_id": RUN_ID,
                    "stage": "full_script",
                    "idempotency_key": IDEMPOTENCY_KEY,
                },
                budget=_budget(),
            )
        assert excinfo.value.code is ErrorCode.VALIDATION_FAILED

    anyio.run(verify)


@pytest.mark.parametrize(
    "absolute_path",
    [
        "C:\\Users\\owner\\script.md",
        "C:/Users/owner/script.md",
        "d:\\work\\shot-01.png",
        "\\\\server\\share\\script.md",
        "//server/share/script.md",
        "\\\\?\\C:\\script.md",
        "\\rooted\\without\\drive.md",
    ],
)
def test_ac5_rejects_windows_drive_and_unc_paths(absolute_path: str):
    """AC5（WARN 返工）：Windows 盘符与 UNC 风格路径在 POSIX 宿主上同样被拒。"""
    registry = _build_registry(
        {"script": FakePipelineService({"path": absolute_path})}
    )

    async def verify() -> None:
        with pytest.raises(DomainError) as excinfo:
            await registry.invoke_tool(
                "script_generate",
                ACTOR,
                {
                    "run_id": RUN_ID,
                    "stage": "full_script",
                    "idempotency_key": IDEMPOTENCY_KEY,
                },
                budget=_budget(),
            )
        assert excinfo.value.code is ErrorCode.VALIDATION_FAILED

    anyio.run(verify)


def test_ac5_rejects_absolute_path_in_dict_key():
    """AC5（WARN 返工）：dict key 含绝对路径同样被拒。"""
    registry = _build_registry(
        {"script": FakePipelineService({"/srv/gw/out.md": "candidate"})}
    )

    async def verify() -> None:
        with pytest.raises(DomainError) as excinfo:
            await registry.invoke_tool(
                "script_generate",
                ACTOR,
                {
                    "run_id": RUN_ID,
                    "stage": "full_script",
                    "idempotency_key": IDEMPOTENCY_KEY,
                },
                budget=_budget(),
            )
        assert excinfo.value.code is ErrorCode.VALIDATION_FAILED

    anyio.run(verify)


def test_ac5_rejects_absolute_path_in_nested_lists():
    """AC5：嵌套结构（list / dict）内的绝对路径一律被拒。"""
    registry = _build_registry(
        {
            "script": FakePipelineService(
                {"sources": [{"ref": "artifact:v001"}, {"ref": "\\\\nas\\share\\a.png"}]}
            )
        }
    )

    async def verify() -> None:
        with pytest.raises(DomainError) as excinfo:
            await registry.invoke_tool(
                "script_generate",
                ACTOR,
                {
                    "run_id": RUN_ID,
                    "stage": "full_script",
                    "idempotency_key": IDEMPOTENCY_KEY,
                },
                budget=_budget(),
            )
        assert excinfo.value.code is ErrorCode.VALIDATION_FAILED

    anyio.run(verify)


@pytest.mark.parametrize(
    "credential_key",
    [
        "api_key",
        "API_KEY",
        "access_token",
        "refresh-token",
        "secret",
        "client_secret",
        "password",
        "credential",
        "authorization",
        "private_key",
    ],
)
def test_ac5_credential_fields_fail_closed(credential_key: str):
    """AC5（WARN 返工）：凭据字段（含变体键名）fail-closed 抛错，不静默剥离。"""
    registry = _build_registry(
        {"script": FakePipelineService({credential_key: "dummy-value"})}
    )

    async def verify() -> None:
        with pytest.raises(DomainError) as excinfo:
            await registry.invoke_tool(
                "script_generate",
                ACTOR,
                {
                    "run_id": RUN_ID,
                    "stage": "full_script",
                    "idempotency_key": IDEMPOTENCY_KEY,
                },
                budget=_budget(),
            )
        assert excinfo.value.code is ErrorCode.VALIDATION_FAILED

    anyio.run(verify)


def test_service_failure_is_wrapped_as_upstream_unavailable():
    """service 任意异常收口为 UPSTREAM_UNAVAILABLE，原始异常不外泄。"""
    registry = _build_registry(
        {"script": FakePipelineService(error=RuntimeError("provider boom"))}
    )

    async def verify() -> None:
        with pytest.raises(DomainError) as excinfo:
            await registry.invoke_tool(
                "script_generate",
                ACTOR,
                {
                    "run_id": RUN_ID,
                    "stage": "full_script",
                    "idempotency_key": IDEMPOTENCY_KEY,
                },
                budget=_budget(),
            )
        assert excinfo.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
        assert "provider boom" not in excinfo.value.message

    anyio.run(verify)


def test_failed_calls_are_not_cached_for_idempotency():
    """失败不入幂等缓存：同 key 可在修复后重试，成功后仍只保留一次成功调用。"""
    service = FakePipelineService({"path": "/srv/gw/out.md"})
    registry = _build_registry({"script": service})
    arguments = {
        "run_id": RUN_ID,
        "stage": "full_script",
        "idempotency_key": IDEMPOTENCY_KEY,
    }

    async def verify() -> None:
        with pytest.raises(DomainError):
            await registry.invoke_tool(
                "script_generate", ACTOR, arguments, budget=_budget()
            )
        service.payload = _clean_payload()
        first = await registry.invoke_tool(
            "script_generate", ACTOR, arguments, budget=_budget()
        )
        second = await registry.invoke_tool(
            "script_generate", ACTOR, arguments, budget=_budget()
        )

        assert len(service.calls) == 2
        assert first == second == _clean_payload()

    anyio.run(verify)
