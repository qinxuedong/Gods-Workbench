"""System One 标准决策适配器。"""

from __future__ import annotations

import inspect
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from fastapi import APIRouter

from .debiasing import debias_probability_runs, normalize_probabilities, position_bias_drift


class DecisionBackend(Protocol):
    """可替换的本地或远程决策后端。"""

    name: str

    def decide(self, payload: Mapping[str, Any]) -> Mapping[str, Any] | Awaitable[Mapping[str, Any]]: ...


class DecisionInputError(ValueError):
    """标准协议输入不满足约束。"""


class LocalDecisionBackend:
    """接收本地 CPU 决策引擎已计算的离散结果。"""

    name = "local"

    def decide(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        decision_type = payload["decision_type"]
        if decision_type == "choice":
            return {
                key: payload[key]
                for key in ("samples", "probabilities", "scores")
                if payload.get(key) is not None
            }
        if decision_type == "scoring":
            return {"scores": payload.get("scores")}
        if decision_type == "boolean":
            return {"value": payload.get("value")}
        return {}


class RemoteDecisionBackend:
    """使用服务端配置的 HTTP 端点调用 AnyJev/Laya 兼容服务。"""

    name = "remote"

    def __init__(self, endpoint: str, *, transport: Callable[..., Any] | None = None) -> None:
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            raise ValueError("远程决策端点必须使用 http:// 或 https://")
        self.endpoint = endpoint
        self._transport = transport

    async def decide(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._transport is not None:
            result = self._transport(self.endpoint, dict(payload))
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, Mapping):
                raise RuntimeError("远程决策服务返回了非对象结果")
            return result

        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(self.endpoint, json=dict(payload))
                response.raise_for_status()
                result = response.json()
        except httpx.HTTPError as exc:
            raise RuntimeError("远程决策服务请求失败") from exc
        if not isinstance(result, Mapping):
            raise RuntimeError("远程决策服务返回了非对象结果")
        return result


class KevDecisionBackend:
    """调用显式配置的 Kev System One 服务；未配置时保持失败关闭。"""

    name = "kev-cpu"

    def __init__(self, endpoint: str | None, *, transport: Callable[..., Any] | None = None) -> None:
        if not endpoint:
            raise ValueError("必须通过 KEV_ENDPOINT 显式配置 Kev 推理端点")
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            raise ValueError("KEV_ENDPOINT 必须使用 http:// 或 https://")
        self.endpoint = endpoint
        self._transport = transport

    async def decide(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        decision_type = payload.get("decision_type")
        request: dict[str, Any] = {"state": str(payload.get("prompt") or "")}
        if decision_type == "choice":
            request["questions"] = {
                "decision": {
                    "type": "choice",
                    "criteria": {item["id"]: None for item in payload.get("options", ())},
                }
            }
        elif decision_type == "boolean":
            request["questions"] = {"decision": {"type": "noul"}}
        elif decision_type == "scoring":
            request["questions"] = {"decision": {"type": "score"}}
        else:
            raise DecisionInputError("Kev 后端只支持 choice、scoring 或 boolean")

        if self._transport is not None:
            result = self._transport(self.endpoint, request)
            if inspect.isawaitable(result):
                result = await result
        else:
            import httpx

            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(self.endpoint, json=request)
                    response.raise_for_status()
                    result = response.json()
            except httpx.HTTPError as exc:
                raise RuntimeError("Kev 推理服务请求失败") from exc

        if not isinstance(result, Mapping):
            raise RuntimeError("Kev 推理服务返回了非对象结果")
        if decision_type == "choice":
            probabilities = result.get("answers", {}).get("decision", {}).get("probabilities")
            if not isinstance(probabilities, Mapping):
                raise RuntimeError("Kev 推理服务未返回 choice probabilities")
            return {"probabilities": dict(probabilities)}
        if decision_type == "boolean":
            value = result.get("answers", {}).get("decision", {}).get("noul")
            if not isinstance(value, bool):
                raise RuntimeError("Kev 推理服务未返回 boolean noul")
            return {"value": value}
        score = result.get("answers", {}).get("decision", {}).get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise RuntimeError("Kev 推理服务未返回 score")
        option_count = max(1, len(payload.get("options", ())))
        return {"scores": {"decision": float(score) * 100.0 / float(max(option_count - 1, 1))}}


class DecisionRouter:
    """把统一请求交给本地或已配置的远程后端，并校验稳定响应。"""

    _TYPES = frozenset({"choice", "scoring", "boolean"})

    def __init__(
        self,
        backend: DecisionBackend | None = None,
        *,
        remote_backend: DecisionBackend | None = None,
        kev_backend: DecisionBackend | None = None,
    ) -> None:
        self.backend = backend or LocalDecisionBackend()
        self.remote_backend = remote_backend
        self.kev_backend = kev_backend

    async def decide(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(request)
        decision_type = payload.get("decision_type") or payload.get("type")
        if payload.get("decision_type") and payload.get("type") and payload["decision_type"] != payload["type"]:
            raise DecisionInputError("type 与 decision_type 必须一致")
        if decision_type not in self._TYPES:
            raise DecisionInputError("type 必须是 choice、scoring 或 boolean")
        payload["decision_type"] = decision_type
        backend = self._backend_for(payload)
        try:
            raw = backend.decide(payload)
            if inspect.isawaitable(raw):
                raw = await raw
            return self._response_for(payload, raw, backend.name)
        except (RuntimeError, TimeoutError, OSError) as backend_error:
            if backend.name != "kev-cpu":
                raise
            try:
                fallback = self.backend.decide(payload)
                if inspect.isawaitable(fallback):
                    fallback = await fallback
                return self._response_for(payload, fallback, self.backend.name)
            except Exception as fallback_error:
                raise backend_error from fallback_error

    @classmethod
    def _response_for(
        cls, payload: Mapping[str, Any], raw: Any, provider: str
    ) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise RuntimeError("决策后端返回了非对象结果")
        decision_type = payload["decision_type"]
        if decision_type == "choice":
            return cls._choice_response(payload, raw, provider)
        if decision_type == "scoring":
            return cls._scoring_response(payload, raw, provider)
        return cls._boolean_response(payload, raw, provider)

    def _backend_for(self, payload: Mapping[str, Any]) -> DecisionBackend:
        provider = payload.get("provider", "local")
        if provider == "kev-cpu":
            if self.kev_backend is None:
                raise RuntimeError("Kev 决策后端尚未配置")
            return self.kev_backend
        if provider == "local":
            return self.backend
        if provider == "remote":
            if self.remote_backend is None:
                raise RuntimeError("远程决策后端尚未配置")
            return self.remote_backend
        raise DecisionInputError("provider 必须是 local、remote 或 kev-cpu")

    @staticmethod
    def _choice_ids(options: Any) -> tuple[str, ...]:
        if options is None:
            return ()
        if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
            raise DecisionInputError("choice options 必须是数组")
        result: list[str] = []
        for option in options:
            if isinstance(option, Mapping):
                value = option.get("id", option.get("value", option.get("label")))
            else:
                value = option
            if value is None or not str(value).strip():
                raise DecisionInputError("choice 选项必须有非空 id")
            result.append(str(value).strip())
        if len(set(result)) != len(result):
            raise DecisionInputError("choice 选项 id 必须唯一")
        return tuple(result)

    @classmethod
    def _choice_response(
        cls,
        payload: Mapping[str, Any],
        raw: Mapping[str, Any],
        provider: str,
    ) -> dict[str, Any]:
        option_ids = cls._choice_ids(payload.get("options"))
        runs = raw.get("samples") if raw.get("samples") is not None else payload.get("samples")
        if runs is not None:
            if not isinstance(runs, Sequence) or isinstance(runs, (str, bytes)):
                raise DecisionInputError("samples 必须是多轮决策结果数组")
            try:
                probabilities = debias_probability_runs(runs)
                drift = position_bias_drift(runs)
            except ValueError as exc:
                raise DecisionInputError(str(exc)) from exc
        else:
            values = raw.get("probabilities")
            if values is None:
                values = raw.get("scores")
            if values is None:
                values = payload.get("probabilities")
            if values is None:
                values = payload.get("scores")
            if values is None:
                if not option_ids:
                    raise DecisionInputError("choice 至少需要 options 或概率结果")
                values = {option_id: 1.0 for option_id in option_ids}
            try:
                if isinstance(values, Mapping):
                    probabilities = normalize_probabilities(values)
                elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                    if not option_ids or len(values) != len(option_ids):
                        raise DecisionInputError("choice 概率数量必须与 options 一致")
                    normalized = normalize_probabilities(values)
                    if not isinstance(normalized, tuple):
                        raise DecisionInputError("choice 概率结果格式无效")
                    probabilities = dict(zip(option_ids, normalized))
                else:
                    raise DecisionInputError("choice 概率结果必须是对象或数组")
            except ValueError as exc:
                raise DecisionInputError(str(exc)) from exc
            drift = (max(probabilities.values()) - min(probabilities.values())) * 100.0

        if not isinstance(probabilities, Mapping):
            if not option_ids or len(probabilities) != len(option_ids):
                raise DecisionInputError("choice 概率数量必须与 options 一致")
            probabilities = dict(zip(option_ids, probabilities))
        if option_ids and set(probabilities) != set(option_ids):
            raise DecisionInputError("choice 概率结果必须覆盖全部 options")
        if not probabilities:
            raise DecisionInputError("choice 至少需要一个选项")
        if not all(math.isfinite(float(value)) and 0 <= float(value) <= 1 for value in probabilities.values()):
            raise DecisionInputError("choice 概率必须是 0 到 1 之间的有限数")
        chosen = max(probabilities, key=probabilities.get)
        return {
            "protocol_version": "system-one.v1",
            "decision_type": "choice",
            "provider": provider,
            "result": {"choice_id": str(chosen)},
            "probabilities": {str(key): float(value) for key, value in probabilities.items()},
            "debias": {"method": "anyjev-cyclic-shift-v1", "position_bias_drift_pct": float(drift)},
        }

    @staticmethod
    def _scoring_response(
        payload: Mapping[str, Any],
        raw: Mapping[str, Any],
        provider: str,
    ) -> dict[str, Any]:
        values = raw.get("scores")
        if values is None:
            values = payload.get("scores")
        if values is None:
            values = {
                str(option.get("id", option.get("label"))): option.get("score")
                for option in payload.get("options", ())
                if isinstance(option, Mapping) and option.get("score") is not None
            }
        if not isinstance(values, Mapping) or not values:
            raise DecisionInputError("scoring 至少需要 scores 或带 score 的 options")
        scores: dict[str, float] = {}
        for key, value in values.items():
            score_id = str(key).strip()
            if not score_id or score_id in scores:
                raise DecisionInputError("scoring 选项 id 必须非空且唯一")
            if isinstance(value, bool):
                raise DecisionInputError("scoring 分数必须是 0 到 100 之间的数字")
            try:
                score = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise DecisionInputError("scoring 分数必须是 0 到 100 之间的数字") from exc
            if not math.isfinite(score) or not 0 <= score <= 100:
                raise DecisionInputError("scoring 分数必须是 0 到 100 之间的数字")
            scores[score_id] = score
        return {
            "protocol_version": "system-one.v1",
            "decision_type": "scoring",
            "provider": provider,
            "result": {"scores": scores},
        }

    @staticmethod
    def _boolean_response(
        payload: Mapping[str, Any],
        raw: Mapping[str, Any],
        provider: str,
    ) -> dict[str, Any]:
        value = raw.get("value") if "value" in raw else payload.get("value")
        if not isinstance(value, bool):
            raise DecisionInputError("boolean 必须提供布尔 value")
        return {
            "protocol_version": "system-one.v1",
            "decision_type": "boolean",
            "provider": provider,
            "result": {"value": value},
        }


def build_decision_api_router(endpoint: Callable[..., Any]) -> APIRouter:
    """创建唯一的 System One HTTP 路由载体。"""
    api_router = APIRouter(tags=["system-one"])
    api_router.add_api_route("/v1/systemone", endpoint, methods=["POST"], response_model=None)
    return api_router


__all__ = [
    "DecisionBackend",
    "DecisionInputError",
    "DecisionRouter",
    "KevDecisionBackend",
    "LocalDecisionBackend",
    "RemoteDecisionBackend",
    "build_decision_api_router",
]
