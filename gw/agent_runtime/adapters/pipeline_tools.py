"""MVP2-T4 前段生产工具注册（剧本 / 分镜 / 样片）。

把前段生成能力以 Agent 工具形式挂到 `CapabilityRegistry`：handler 只调用**注入的
service**（测试用 fake，不触达真实 provider），并在返回前把产物清洗为不含绝对路径、
不含凭据字段的安全结构。

样片（animatic）：截至 2026-10-28 的 master 后端没有明确的样片生成入口（仅有
`web/js/controllers/production-controller.js` 的播放与展示逻辑），因此 **不注册**
`animatic_generate`；待后端出现明确生成入口再补注册。
"""

from __future__ import annotations

import ntpath
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode, ToolCostState, ToolRiskLevel
from gw.agent_runtime.policy import CapabilityRegistry, ToolRegistration

# 幂等键最短长度：与 input_schema 的 minLength 保持一致，handler 直调时也生效。
MIN_IDEMPOTENCY_KEY_LENGTH = 8

SCRIPT_STAGES: tuple[str, ...] = ("full_script", "scene_script")
STORYBOARD_STAGES: tuple[str, ...] = ("storyboard_text",)

# (工具名, services 键, 允许的 stage)
PIPELINE_TOOLS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("script_generate", "script", SCRIPT_STAGES),
    ("storyboard_generate", "storyboard", STORYBOARD_STAGES),
)

# 绝对路径判定不依赖 `os.path.isabs`：POSIX 宿主上 `os.path.isabs` 认不出 Windows
# 盘符与 UNC，跨平台字符串必须按形态统一拦截（原 PR #371 审核 WARN 返工项）。
_ROOTED_PREFIXES = ("\\", "/")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")

# 凭据键名按「规范化后包含」匹配，覆盖 `access_token` / `refresh-token` 等变体。
_NON_KEY_CHARS_RE = re.compile(r"[^a-z0-9]")
CREDENTIAL_KEY_TOKENS: tuple[str, ...] = (
    "apikey",
    "accesskey",
    "privatekey",
    "secret",
    "token",
    "password",
    "passwd",
    "passphrase",
    "credential",
    "authorization",
    "bearer",
    "sessionkey",
)


class PipelineGenerationService(Protocol):
    """前段生成 service 端口：由宿主注入实现，handler 不做任何 provider 选择。"""

    async def generate(
        self,
        *,
        run_id: str,
        stage: str,
        instructions: str | None,
        idempotency_key: str,
    ) -> Any: ...


def _is_absolute_path(value: str) -> bool:
    """按字符串形态判定绝对路径，覆盖 POSIX 根、Windows 盘符与 UNC。"""
    candidate = value.strip()
    if candidate.startswith(_ROOTED_PREFIXES):
        return True
    if _WINDOWS_DRIVE_RE.match(candidate):
        return True
    return bool(ntpath.splitdrive(candidate)[0])


def _is_credential_key(key: str) -> bool:
    normalized = _NON_KEY_CHARS_RE.sub("", key.lower())
    return any(token in normalized for token in CREDENTIAL_KEY_TOKENS)


def _sanitize_response(value: Any, *, request_id: str) -> Any:
    """递归清洗工具返回值：绝对路径与凭据字段一律失败关闭。"""
    if isinstance(value, str):
        if _is_absolute_path(value):
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "Tool results must not contain filesystem paths.",
                request_id=request_id,
            )
        return value
    if isinstance(value, Mapping):
        sanitized: dict[Any, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise DomainError(
                    ErrorCode.VALIDATION_FAILED,
                    "Tool result keys must be strings.",
                    request_id=request_id,
                )
            if _is_credential_key(raw_key):
                raise DomainError(
                    ErrorCode.VALIDATION_FAILED,
                    "Tool results must not carry credential fields.",
                    request_id=request_id,
                )
            if _is_absolute_path(raw_key):
                raise DomainError(
                    ErrorCode.VALIDATION_FAILED,
                    "Tool results must not contain filesystem paths.",
                    request_id=request_id,
                )
            sanitized[raw_key] = _sanitize_response(item, request_id=request_id)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_response(item, request_id=request_id) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise DomainError(
        ErrorCode.VALIDATION_FAILED,
        "Tool results must be JSON-safe values.",
        request_id=request_id,
    )


class _PipelineToolHandler:
    """按 (run_id, idempotency_key) 进程内去重，并把 service 异常收口为领域错误。"""

    def __init__(self, *, tool_name: str, service: PipelineGenerationService) -> None:
        self._tool_name = tool_name
        self._service = service
        self._cache: dict[tuple[str, str], Any] = {}

    async def __call__(self, arguments: Mapping[str, Any]) -> Any:
        run_id = str(arguments.get("run_id", ""))
        idempotency_key = str(arguments.get("idempotency_key", ""))
        if len(idempotency_key) < MIN_IDEMPOTENCY_KEY_LENGTH:
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "A pipeline tool call requires an idempotency key of at least 8 characters.",
                request_id=run_id or self._tool_name,
            )
        stage = str(arguments.get("stage", ""))
        instructions = arguments.get("instructions")
        cache_key = (run_id, idempotency_key)
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            raw = await self._service.generate(
                run_id=run_id,
                stage=stage,
                instructions=instructions if isinstance(instructions, str) else None,
                idempotency_key=idempotency_key,
            )
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "The pipeline generation service is unavailable.",
                request_id=run_id or self._tool_name,
            ) from exc
        sanitized = _sanitize_response(raw, request_id=run_id or self._tool_name)
        self._cache[cache_key] = sanitized
        return sanitized


def _input_schema(stages: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["run_id", "stage", "idempotency_key"],
        "properties": {
            "run_id": {"type": "string"},
            "stage": {"enum": list(stages)},
            "idempotency_key": {
                "type": "string",
                "minLength": MIN_IDEMPOTENCY_KEY_LENGTH,
            },
            "instructions": {"type": "string", "maxLength": 4000},
        },
    }


def register_pipeline_tools(
    registry: CapabilityRegistry,
    *,
    services: Mapping[str, PipelineGenerationService],
) -> tuple[str, ...]:
    """注册前段生成工具；返回本次实际注册的工具名。

    仅注册 `services` 中显式提供了实现的工具（依赖注入，缺失即不注册，绝不回落到
    真实 provider）；未登记的 service 键（含 `animatic`）直接报错，不静默忽略。
    """
    known_keys = {service_key for _, service_key, _ in PIPELINE_TOOLS}
    unknown_keys = sorted(set(services) - known_keys)
    if unknown_keys:
        raise ValueError(f"Unknown pipeline service keys: {unknown_keys}")

    registered: list[str] = []
    for tool_name, service_key, stages in PIPELINE_TOOLS:
        service = services.get(service_key)
        if service is None:
            continue
        registry.register_tool(
            ToolRegistration(
                name=tool_name,
                handler=_PipelineToolHandler(tool_name=tool_name, service=service),
                input_schema=_input_schema(stages),
                risk_level=ToolRiskLevel.medium,
                cost_state=ToolCostState.metered,
                idempotency_supported=True,
            )
        )
        registered.append(tool_name)
    return tuple(registered)
