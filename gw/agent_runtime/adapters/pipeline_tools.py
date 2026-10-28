# -*- coding: utf-8 -*-
# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""MVP2-T4/T5a 生产能力以 Agent 工具形式注册（剧本、分镜、I2V）。

核查结论（截至基线 ``origin/master`` 4c207e4 / 5b21de3，QIN-446 票面要求）：
- ``gw/`` 内除 ``gw/agent_runtime/policy.py`` 的 ``CapabilityRegistry.register_tool``
  定义处外没有任何 ``register_tool(`` / ``ToolRegistration(`` 调用点，
  剧本/分镜/样片均未注册为工具 → 本模块按票面新注册；
- ``animatic``（样片/动态预览）在 ``gw/`` 内检索（``animatic``、``样片``）无
  任何生成入口 → 按票面**不注册** ``animatic_generate``，待样片能力落地后另行登记。

边界约束：
- 生产服务一律注入；I2V 只有唯一的 ``i2v_submit`` 门禁获批后才可调用；
- 本模块不选择 provider、不读取凭据，真实凭据由服务端视频任务服务管理；
- 同一 ``run_id`` 与 ``idempotency_key`` 的重复调用只触发一次底层调用（进程内备忘）；
- 返回值经 ``sanitize_tool_result`` 清洗：绝对路径（POSIX / Windows 盘符 /
  UNC 三种风格）一律脱敏，凭据类字段整体剔除。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from jsonschema import Draft202012Validator

from gw.agent_episode.gates import GateKind, GateStore, require_gate_for_tool
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode, Identifier, ToolCostState, ToolRiskLevel
from gw.agent_runtime.policy import CapabilityRegistry, ToolRegistration

# 与票面 schema 样例一致：run_id + stage + idempotency_key 必填，
# instructions 为受限长度的可选自然语言补充。
_TOOL_INPUT_SCHEMA: dict[str, Any] = {
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
_STORYBOARD_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["run_id", "stage", "idempotency_key"],
    "properties": {
        "run_id": {"type": "string", "minLength": 1},
        "stage": {"type": "string", "enum": ["storyboard_text"]},
        "idempotency_key": {"type": "string", "minLength": 8},
        "instructions": {"type": "string", "maxLength": 4000},
    },
}
_I2V_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "run_id",
        "idempotency_key",
        "provider_id",
        "model",
        "prompt",
        "duration",
        "aspect_ratio",
        "production_context",
    ],
    "properties": {
        "run_id": {"type": "string", "minLength": 1},
        "idempotency_key": {"type": "string", "minLength": 8},
        "provider_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "model": {"type": "string", "minLength": 1, "maxLength": 256},
        "prompt": {"type": "string", "minLength": 1, "maxLength": 20000},
        "duration": {"type": "number", "exclusiveMinimum": 0, "maximum": 120},
        "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"]},
        "production_context": {
            "type": "object",
            "additionalProperties": False,
            "required": ["project_id", "canvas_id", "entity_id"],
            "properties": {
                "project_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "canvas_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "entity_id": {"type": "string", "minLength": 1, "maxLength": 128},
            },
        },
    },
}

# 三类绝对路径风格的检测（QIN-446 返工项 1：不得只依赖 POSIX 的 os.path.isabs）。
_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:(?:[\\/].*)?$")
_UNC_PREFIXES = ("\\\\", "//")

_CREDENTIAL_KEY_PATTERN = re.compile(
    r"(?:api[-_]?key|token|secret|password|authorization|credential)",
    re.IGNORECASE,
)
_REDACTED_PATH = "<redacted-absolute-path>"


@runtime_checkable
class StageCandidateService(Protocol):
    """注入的前段候选生成服务边界；实现方负责构造领域请求与持久化。"""

    async def generate_stage_candidate(
        self,
        *,
        run_id: str,
        stage: str,
        idempotency_key: str,
        instructions: str | None,
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class VideoGenerationService(Protocol):
    """注入的 I2V 服务边界；真实凭据仅由服务端 VideoTaskService 处理。"""

    def create_generation(
        self, payload: dict[str, Any], key_value: str | None, context: Any
    ) -> Mapping[str, Any]: ...


@dataclass
class PipelineToolServices:
    """注册时注入的服务集合；测试以 fake 记录调用参数替换真实实现。"""

    stage_candidate: StageCandidateService
    video_task: VideoGenerationService | None = None
    gate_store: GateStore | None = None
    video_context: Any | None = None


@dataclass(frozen=True, slots=True)
class _GateCostRegistration:
    """门禁策略读取所需的最小工具注册视图。"""

    cost_state: ToolCostState


_I2V_GATE_REGISTRATION = _GateCostRegistration(ToolCostState.metered)


def is_absolute_path_string(value: str) -> bool:
    """检测字符串是否为绝对路径：POSIX 绝对路径、Windows 盘符或 UNC 风格。"""
    if not isinstance(value, str) or not value:
        return False
    if _WINDOWS_DRIVE_PATTERN.match(value):
        return True
    if any(value.startswith(prefix) for prefix in _UNC_PREFIXES):
        return True
    return value.startswith("/") and len(value) > 1


def sanitize_tool_result(value: Any) -> Any:
    """递归清洗工具返回值：绝对路径脱敏、凭据字段剔除，供 AC5 断言。"""
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _CREDENTIAL_KEY_PATTERN.search(key_text):
                continue
            cleaned[key_text] = sanitize_tool_result(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [sanitize_tool_result(item) for item in value]
    if isinstance(value, str) and is_absolute_path_string(value):
        return _REDACTED_PATH
    return value


def _make_stage_handler(
    services: PipelineToolServices, schema: Mapping[str, Any], memo: dict[str, Any]
) -> Any:
    """构造阶段候选 handler：先验 schema 校验，再幂等去重，最后调注入服务。"""

    async def handler(arguments: Mapping[str, Any]) -> Any:
        payload = dict(arguments or {})
        errors = sorted(
            error.message for error in Draft202012Validator(schema).iter_errors(payload)
        )
        if errors:
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "工具入参未通过 schema 校验",
                request_id=str(payload.get("run_id") or "pipeline-tool"),
            )
        idempotency_key = str(payload["idempotency_key"])
        if idempotency_key in memo:
            return sanitize_tool_result(memo[idempotency_key])
        result = await services.stage_candidate.generate_stage_candidate(
            run_id=str(payload["run_id"]),
            stage=str(payload["stage"]),
            idempotency_key=idempotency_key,
            instructions=payload.get("instructions"),
        )
        memo[idempotency_key] = result
        return sanitize_tool_result(result)

    return handler


def _make_i2v_handler(
    services: PipelineToolServices,
    schema: Mapping[str, Any],
    memo: dict[tuple[str, str], Any],
) -> Any:
    """仅在唯一 i2v_submit 门禁批准后调用注入的 I2V 服务。"""

    async def handler(arguments: Mapping[str, Any]) -> Any:
        payload = dict(arguments or {})
        errors = sorted(
            error.message for error in Draft202012Validator(schema).iter_errors(payload)
        )
        if errors:
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "工具入参未通过 schema 校验",
                request_id=str(payload.get("run_id") or "i2v-tool"),
            )

        run_id = str(payload["run_id"])
        idempotency_key = str(payload["idempotency_key"])
        gate_records = (
            services.gate_store.list_for_run(Identifier(run_id))
            if services.gate_store is not None
            else ()
        )
        i2v_gates = [
            record for record in gate_records if record.kind is GateKind.i2v_submit
        ]
        gate_state = i2v_gates[0].state if len(i2v_gates) == 1 else None
        require_gate_for_tool(
            _I2V_GATE_REGISTRATION,
            gate_state,
            request_id=run_id,
        )

        memo_key = (run_id, idempotency_key)
        if memo_key in memo:
            return sanitize_tool_result(memo[memo_key])
        if services.video_task is None or services.video_context is None:
            raise DomainError(
                ErrorCode.CAPABILITY_UNSUPPORTED,
                "I2V generation service is not configured.",
                request_id="i2v_generate",
            )

        video_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"run_id", "idempotency_key"}
        }
        video_payload["production_context"] = dict(payload["production_context"])
        result = services.video_task.create_generation(
            video_payload,
            idempotency_key,
            services.video_context,
        )
        memo[memo_key] = result
        return sanitize_tool_result(result)

    return handler


def register_pipeline_tools(
    registry: CapabilityRegistry, *, services: PipelineToolServices
) -> None:
    """按票面工具清单注册前段工具；重复注册由 registry 的唯一性约束拒绝。

    幂等备忘按工具隔离：同一 idempotency_key 只在同名工具内去重。
    """
    registry.register_tool(
        ToolRegistration(
            name="script_generate",
            handler=_make_stage_handler(services, _TOOL_INPUT_SCHEMA, {}),
            input_schema=_TOOL_INPUT_SCHEMA,
            risk_level=ToolRiskLevel.medium,
            cost_state=ToolCostState.metered,
            idempotency_supported=True,
        )
    )
    registry.register_tool(
        ToolRegistration(
            name="i2v_generate",
            handler=_make_i2v_handler(services, _I2V_INPUT_SCHEMA, {}),
            required_permissions=(Identifier("video.generate"),),
            input_schema=_I2V_INPUT_SCHEMA,
            risk_level=ToolRiskLevel.high,
            cost_state=ToolCostState.metered,
            idempotency_supported=True,
        )
    )
    registry.register_tool(
        ToolRegistration(
            name="storyboard_generate",
            handler=_make_stage_handler(services, _STORYBOARD_INPUT_SCHEMA, {}),
            input_schema=_STORYBOARD_INPUT_SCHEMA,
            risk_level=ToolRiskLevel.medium,
            cost_state=ToolCostState.metered,
            idempotency_supported=True,
        )
    )


__all__ = [
    "PipelineToolServices",
    "StageCandidateService",
    "VideoGenerationService",
    "is_absolute_path_string",
    "register_pipeline_tools",
    "sanitize_tool_result",
]
