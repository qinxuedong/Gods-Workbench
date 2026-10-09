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

"""配置驱动的 Chat Completions 调用与端到端输出吞吐计量。"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Dict, Optional, Tuple

from gw.core.errors import CleanroomException
from gw.settings import execution_config

TIMEOUT_SECONDS = 30.0
MAX_OUTPUT_TOKENS = 512
MAX_CONCURRENT_REQUESTS = 2
_USAGE_SOURCE = "chat_completions.usage.completion_tokens"
_CHAT_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)


def _metric(
    *,
    provider_id: Optional[str],
    model: Optional[str],
    output_tokens: Optional[int] = None,
    elapsed_seconds: Optional[float] = None,
    tokens_per_second: Optional[float] = None,
    unavailable_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """仅构造受控字段，不携带上游 raw 或凭据。"""
    return {
        "kind": "end_to_end_output_tokens_per_second",
        "provider_id": provider_id,
        "model": model,
        "output_tokens": output_tokens,
        "elapsed_seconds": elapsed_seconds,
        "tokens_per_second": tokens_per_second,
        "usage_source": _USAGE_SOURCE,
        "unavailable_reason": unavailable_reason,
    }


def _fail(
    status_code: int,
    code: str,
    message: str,
    endpoint: str,
    *,
    reason: str,
    provider_id: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """发出不含上游内容的标准错误，并为吞吐空值给出明确原因。"""
    metric = _metric(
        provider_id=provider_id,
        model=model,
        unavailable_reason=reason,
    )
    extra = {
        "endpoint": endpoint,
        "unavailable": status_code >= 500,
        "data_status": "not_integrated" if status_code >= 500 else "invalid_request",
        "metrics": metric,
    }
    raise CleanroomException(
        status_code=status_code,
        code=code,
        message=message,
        extra=extra,
        expose_extra_fields={"endpoint", "unavailable", "data_status"},
    )


def error_response(error: CleanroomException):
    """回送标准错误包并仅附带本模块构造的空指标，不暴露任意异常内容。"""
    from fastapi.responses import JSONResponse

    payload = error.to_envelope().model_dump(exclude_none=True)
    metrics = error.extra.get("metrics") if isinstance(error.extra, dict) else None
    if isinstance(metrics, dict) and set(metrics) == {
        "kind", "provider_id", "model", "output_tokens", "elapsed_seconds",
        "tokens_per_second", "usage_source", "unavailable_reason",
    }:
        payload["detail"]["metrics"] = metrics
    return JSONResponse(status_code=error.status_code, content=payload)


def public_configuration() -> dict:
    """只返回服务端 Provider 配置摘要，不探测网络、不泄露 URL/key 环境变量名。"""
    return execution_config.public_configuration()


def resolve_agent_model(provider_id: Optional[str], model: Optional[str]) -> dict[str, str]:
    """Resolve an allowlisted, credential-ready model without exposing URL or secret."""
    from gw.settings.probes import guard_provider_url

    config = _resolve_config(
        {"provider_id": provider_id, "model": model},
        "/api/agent/runs",
    )
    guard_provider_url(config["base_url"], allow_lan=True)
    return {"provider_id": config["provider_id"], "model": config["model"]}


def _resolve_config(payload: Dict[str, Any], endpoint: str) -> Dict[str, str]:
    raw = payload or {}
    # 客户端提供 URL/密钥不得成为执行配置来源，即使这些字段是空值也拒绝。
    forbidden = {
        "base_url", "api_key", "apikey", "api_keys", "key", "token",
        "authorization", "access_token", "client_secret", "password",
        "credential", "credentials",
    }
    if any(str(key).strip().casefold().replace("-", "_") in forbidden for key in raw):
        _fail(
            400,
            "INVALID_REQUEST",
            "Provider 地址与凭据必须由服务端运行配置引用，不能随请求提交",
            endpoint,
            reason="client_provider_configuration_rejected",
        )

    raw_provider_id = raw.get("provider_id")
    if raw_provider_id is not None and not isinstance(raw_provider_id, str):
        _fail(400, "INVALID_REQUEST", "provider_id 必须是字符串", endpoint, reason="invalid_provider_selection")
    provider_id = raw_provider_id.strip() if isinstance(raw_provider_id, str) else None
    if provider_id == "":
        provider_id = None

    try:
        provider = execution_config.resolve_provider(provider_id, capability="chat")
    except execution_config.ProviderSelectionRequired:
        _fail(
            400,
            "CHAT_PROVIDER_SELECTION_REQUIRED",
            "当前有多个服务端 Chat Provider，请明确选择一个配置项",
            endpoint,
            reason="provider_selection_required",
        )
    except execution_config.RuntimeConfigError:
        _fail(
            503,
            "CHAT_NOT_INTEGRATED",
            "服务端 Provider 运行配置无效，未向外部发送消息",
            endpoint,
            reason="runtime_configuration_invalid",
        )
    if provider is None:
        if provider_id:
            _fail(
                400,
                "CHAT_PROVIDER_NOT_CONFIGURED",
                "所选 Provider 未配置 Chat Completions 运行参数",
                endpoint,
                reason="provider_not_configured",
            )
        _fail(
            503,
            "CHAT_NOT_INTEGRATED",
            "没有可用的服务端 Chat Provider 配置，未向外部发送消息",
            endpoint,
            reason="provider_not_configured",
        )

    api_key = execution_config.provider_api_key(provider)
    if not api_key:
        _fail(
            503,
            "CHAT_NOT_INTEGRATED",
            "所选 Provider 的服务端凭据引用尚未配置，未向外部发送消息",
            endpoint,
            reason="provider_credential_unavailable",
            provider_id=provider.provider_id,
            model=provider.default_model,
        )

    requested_model = raw.get("model")
    if requested_model is not None and not isinstance(requested_model, str):
        _fail(
            400,
            "INVALID_REQUEST",
            "model 必须是字符串",
            endpoint,
            reason="invalid_model_selection",
            provider_id=provider.provider_id,
            model=provider.default_model,
        )
    requested_model = requested_model.strip() if isinstance(requested_model, str) else ""
    if not requested_model:
        model = provider.default_model
    elif requested_model in provider.models:
        model = requested_model
    elif len(provider.models) == 1:
        # 单模型服务端配置是权威值；忽略旧前端遗留的展示模型标签，不按其路由。
        model = provider.default_model
    else:
        _fail(
            400,
            "CHAT_MODEL_NOT_CONFIGURED",
            "请求模型不属于该 Provider 的服务端允许列表",
            endpoint,
            reason="model_not_configured",
            provider_id=provider.provider_id,
            model=provider.default_model,
        )

    return {
        "provider_id": provider.provider_id,
        "base_url": provider.base_url,
        "api_key": api_key,
        "model": model,
    }


def _completion_url(base_url: str) -> str:
    return execution_config.api_endpoint_url(base_url, "/v1/chat/completions")


def _httpx(endpoint: str, config: Dict[str, str]):
    try:
        import httpx  # type: ignore
    except Exception:
        _fail(
            503,
            "CHAT_NOT_INTEGRATED",
            "未安装 httpx，对话 Provider 调用能力不可用",
            endpoint,
            reason="http_client_unavailable",
            provider_id=config["provider_id"],
            model=config["model"],
        )
    return httpx


def _extract_reply(raw: Any) -> Tuple[str, Optional[str]]:
    """只提取 Chat Completions 标准 choices 内容；不透传上游原始响应。"""
    if not isinstance(raw, dict):
        return "", None
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "", None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return "", None
    content = message.get("content")
    if isinstance(content, str):
        return content, raw.get("model") if isinstance(raw.get("model"), str) else None
    # 仅请求纯文本对话；若上游返回多段内容，只提取明确标记的文本块。
    if isinstance(content, list):
        text_parts = [
            part.get("text") for part in content
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
        ]
        if text_parts:
            return "".join(text_parts), raw.get("model") if isinstance(raw.get("model"), str) else None
    return "", raw.get("model") if isinstance(raw.get("model"), str) else None


def _build_metrics(raw: Any, started_at: float, completed_at: float, provider_id: str, model: str) -> dict:
    elapsed_raw = completed_at - started_at
    elapsed_seconds = (
        elapsed_raw if isinstance(elapsed_raw, (int, float)) and math.isfinite(elapsed_raw) and elapsed_raw > 0
        else None
    )

    usage = raw.get("usage") if isinstance(raw, dict) else None
    if not isinstance(usage, dict) or "completion_tokens" not in usage:
        output_tokens = None
        usage_reason = "usage_missing"
    else:
        count = usage.get("completion_tokens")
        if type(count) is int and count >= 0:
            output_tokens = count
            usage_reason = None
        else:
            output_tokens = None
            usage_reason = "usage_invalid"

    if usage_reason:
        unavailable_reason = usage_reason
        tokens_per_second = None
    elif elapsed_seconds is None:
        unavailable_reason = "elapsed_invalid"
        tokens_per_second = None
    else:
        try:
            rate = output_tokens / elapsed_seconds
        except OverflowError:
            rate = math.inf
        if not math.isfinite(rate) or rate < 0:
            unavailable_reason = "rate_invalid"
            tokens_per_second = None
        else:
            unavailable_reason = None
            tokens_per_second = rate

    return _metric(
        provider_id=provider_id,
        model=model,
        output_tokens=output_tokens,
        elapsed_seconds=elapsed_seconds,
        tokens_per_second=tokens_per_second,
        unavailable_reason=unavailable_reason,
    )


def complete_messages(
    payload: Dict[str, Any],
    messages: list[dict[str, str]],
    *,
    timeout_seconds: int = int(TIMEOUT_SECONDS),
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
    output_schema: dict[str, Any] | None = None,
    endpoint: str = "/api/agent/model-invocation",
    include_agent_usage: bool = False,
) -> Dict[str, Any]:
    """按服务端 Provider 配置发送完整 typed messages，供受信任的后台Agent使用。

    与旧单轮 chat 共用 provider选择、secret引用、URL guard、连接限制和错误脱敏；
    调用方不能提交 URL、凭据或超出硬上限的生成限制。
    """
    from gw.settings.probes import guard_provider_url

    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= int(TIMEOUT_SECONDS):
        _fail(400, "INVALID_REQUEST", "timeout_seconds 超出Agent调用边界", endpoint, reason="invalid_timeout")
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8192:
        _fail(400, "INVALID_REQUEST", "max_output_tokens 超出Agent调用边界", endpoint, reason="invalid_output_limit")
    if not isinstance(messages, list) or not messages or len(messages) > 64:
        _fail(400, "INVALID_REQUEST", "messages 数量不符合Agent调用边界", endpoint, reason="invalid_messages")
    normalized_messages: list[dict[str, str]] = []
    allowed_roles = {"system", "developer", "user", "assistant", "tool"}
    for message in messages:
        if not isinstance(message, dict) or set(message) - {"role", "content", "name"}:
            _fail(400, "INVALID_REQUEST", "messages 结构不符合Agent调用边界", endpoint, reason="invalid_messages")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or role not in allowed_roles or not isinstance(content, str):
            _fail(400, "INVALID_REQUEST", "messages 内容不符合Agent调用边界", endpoint, reason="invalid_messages")
        item = {"role": role, "content": content}
        name = message.get("name")
        if name is not None:
            if not isinstance(name, str) or len(name) > 64:
                _fail(400, "INVALID_REQUEST", "messages name 不符合Agent调用边界", endpoint, reason="invalid_messages")
            item["name"] = name
        normalized_messages.append(item)
    if output_schema is not None and not isinstance(output_schema, dict):
        _fail(400, "INVALID_REQUEST", "output_schema 必须是对象", endpoint, reason="invalid_output_schema")

    config = _resolve_config(payload or {}, endpoint)
    try:
        base_url = guard_provider_url(config["base_url"], allow_lan=True)
    except CleanroomException as exc:
        metric = _metric(
            provider_id=config["provider_id"],
            model=config["model"],
            unavailable_reason="provider_url_rejected",
        )
        raise CleanroomException(
            exc.status_code, exc.code, exc.message,
            extra={"endpoint": endpoint, "metrics": metric},
            expose_extra_fields={"endpoint"},
        ) from None

    if not _CHAT_SLOTS.acquire(blocking=False):
        _fail(
            503,
            "CHAT_CAPACITY_EXCEEDED",
            "对话并发已达服务端上限，请稍后由用户重新发起；未自动重试",
            endpoint,
            reason="concurrency_limit",
            provider_id=config["provider_id"],
            model=config["model"],
        )

    try:
        httpx = _httpx(endpoint, config)
        body: Dict[str, Any] = {
            "model": config["model"],
            "messages": normalized_messages,
            "max_completion_tokens": max_output_tokens,
            "stream": False,
        }
        if output_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_response",
                    "strict": True,
                    "schema": output_schema,
                },
            }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Gods-Workbench/cleanroom",
            "Authorization": "Bearer %s" % config["api_key"],
        }
        started_at = time.monotonic()
        try:
            from gw.settings.probes import is_lan_provider_url
            client_options = {"timeout": float(timeout_seconds), "follow_redirects": False}
            if is_lan_provider_url(base_url):
                client_options["trust_env"] = False
            with httpx.Client(**client_options) as client:
                response = client.post(_completion_url(base_url), json=body, headers=headers)
            # httpx 非流式 post 在此返回时已经读完响应体；立即采样单调时钟。
            completed_at = time.monotonic()
        except Exception:
            _fail(
                503,
                "CHAT_PROVIDER_FAILED",
                "连接对话上游失败或超时，未返回任何回复；不会自动重试付费请求",
                endpoint,
                reason="upstream_error",
                provider_id=config["provider_id"],
                model=config["model"],
            )

        if response.status_code >= 400:
            _fail(
                503,
                "CHAT_PROVIDER_FAILED",
                "上游返回错误状态 %d，未返回任何回复；不会自动重试付费请求" % response.status_code,
                endpoint,
                reason="upstream_error",
                provider_id=config["provider_id"],
                model=config["model"],
            )
        try:
            raw = response.json()
        except Exception:
            _fail(
                503,
                "CHAT_PROVIDER_FAILED",
                "上游返回无法解析的响应，未返回任何回复；不会自动重试付费请求",
                endpoint,
                reason="provider_response_invalid",
                provider_id=config["provider_id"],
                model=config["model"],
            )

        reply, _model_echo = _extract_reply(raw)
        metrics = _build_metrics(raw, started_at, completed_at, config["provider_id"], config["model"])
        result = {
            "reply": reply,
            "provider_id": config["provider_id"],
            "model": config["model"],
            "status_code": response.status_code,
            "metrics": metrics,
            "data_status": "ok",
            "data_gaps": [] if metrics["unavailable_reason"] is None else [metrics["unavailable_reason"]],
        }
        if include_agent_usage:
            choices = raw.get("choices") if isinstance(raw, dict) else None
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            provider_usage = raw.get("usage") if isinstance(raw, dict) else None
            if not isinstance(provider_usage, dict):
                provider_usage = {}
            prompt_tokens = provider_usage.get("prompt_tokens", provider_usage.get("input_tokens"))
            completion_tokens = provider_usage.get("completion_tokens", provider_usage.get("output_tokens"))
            total_tokens = provider_usage.get("total_tokens")
            token_values = (prompt_tokens, completion_tokens)
            valid_pair = all(type(value) is int and value >= 0 for value in token_values)
            if valid_pair and total_tokens is None:
                total_tokens = prompt_tokens + completion_tokens
            if valid_pair and type(total_tokens) is int and total_tokens >= 0:
                result["agent_usage"] = {
                    "input_tokens": prompt_tokens,
                    "output_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                }
                result["agent_usage_unavailable_reason"] = None
            else:
                result["agent_usage"] = None
                result["agent_usage_unavailable_reason"] = "Provider did not report valid input, output, and total token counts."
            raw_id = raw.get("id") if isinstance(raw, dict) else None
            result["agent_upstream_request_id"] = raw_id if isinstance(raw_id, str) else None
            finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
            if message.get("tool_calls"):
                finish_reason = "tool_calls"
            result["agent_finish_reason"] = finish_reason if isinstance(finish_reason, str) else "unknown"
        return result
    finally:
        _CHAT_SLOTS.release()


def complete(payload: Dict[str, Any], *, agent: bool = False) -> Dict[str, Any]:
    """调用单轮真实 Chat Completions；维持已有 `/api/chat` 兼容行为。"""
    endpoint = "/api/chat/agent" if agent else "/api/chat"
    message = str((payload or {}).get("message") or "")
    # The legacy chat path retains its 512-token bound; only the typed agent
    # entry point may use the separately validated agent output budget.
    result = complete_messages(
        payload or {},
        [{"role": "user", "content": message}],
        timeout_seconds=int(TIMEOUT_SECONDS),
        max_output_tokens=MAX_OUTPUT_TOKENS,
        endpoint=endpoint,
    )
    result["agent"] = bool(agent)
    return result
