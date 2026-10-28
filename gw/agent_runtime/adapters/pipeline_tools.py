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

"""MVP2-T4 前段生产能力以 Agent 工具形式注册（剧本/分镜；样片无入口不注册）。

核查结论（截至基线 ``origin/master`` 4c207e4 / 5b21de3，QIN-446 票面要求）：
- ``gw/`` 内除 ``gw/agent_runtime/policy.py`` 的 ``CapabilityRegistry.register_tool``
  定义处外没有任何 ``register_tool(`` / ``ToolRegistration(`` 调用点，
  剧本/分镜/样片均未注册为工具 → 本模块按票面新注册；
- ``animatic``（样片/动态预览）在 ``gw/`` 内检索（``animatic``、``样片``）无
  任何生成入口 → 按票面**不注册** ``animatic_generate``，待样片能力落地后另行登记。

边界约束：
- 生产服务一律注入（协议见 ``StageCandidateService``）；本模块不导入
  ``gw.agent_episode``，不选择 provider，不触发真实付费调用；
- 同一 ``idempotency_key`` 的重复调用只触发一次底层服务调用（进程内备忘）；
- 返回值经 ``sanitize_tool_result`` 清洗：绝对路径（POSIX / Windows 盘符 /
  UNC 三种风格）一律脱敏，凭据类字段整体剔除。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from jsonschema import Draft202012Validator

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode, ToolCostState, ToolRiskLevel
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


@dataclass
class PipelineToolServices:
    """注册时注入的服务集合；测试以 fake 记录调用参数替换真实实现。"""

    stage_candidate: StageCandidateService


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
    "is_absolute_path_string",
    "register_pipeline_tools",
    "sanitize_tool_result",
]
