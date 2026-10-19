"""System One 标准决策适配器。"""

from __future__ import annotations

import inspect
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from fastapi import APIRouter

from .debiasing import debias_probability_runs, normalize_probabilities, position_bias_drift


class DecisionBackend(Protocol):
    """可插拔的本地、NAS 或远程决策后端。"""

    name: str

    def decide(self, payload: Mapping[str, Any]) -> Mapping[str, Any] | Awaitable[Mapping[str, Any]]: ...


class DecisionInputError(ValueError):
    """标准协议输入不满足约束。"""


class LocalDecisionBackend:
    """不依赖模型的本地适配器，接收上游引擎已经计算出的离散结果。"""

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
            return {"scores": payload.get("scores", {})}
        if decision_type == "boolean" and isinstance(payload.get("value"), bool):
            return {"value": payload["value"]}
        return {}


class RemoteDecisionBackend:
    """通过标准 JSON POST 调用外部 AnyJev/Laya 兼容服务。"""

    name = "remote"

    def __init__(self, endpoint: str, *, transport: Callable[..., Any] | None = None) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("远程决策端点必须使用 http:// 或 https://")
        self.endpoint = endpoint
        self._transport = transport

    async def decide(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._transport is not None:
            result = self._transport(self.endpoint, dict(payload))
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, Mapping):
                raise RuntimeError("远程决策服务返回了非对象 JSON")
            return result

        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(self.endpoint, json=dict(payload))
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, Mapping):
            raise RuntimeError("远程决策服务返回了非对象 JSON")
        return result


class DecisionRouter:
    """把统一协议转换为可替换后端，并整理为稳定响应。"""

    _TYPES = frozenset({"choice", "scoring", "boolean"})

    def __init__(self, backend: DecisionBackend | None = None) -> None:
        self.backend = backend or LocalDecisionBackend()

    async def decide(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(request)
        decision_type = payload.get("decision_type") or payload.get("type")
        if decision_type not in self._TYPES:
            raise DecisionInputError("type 必须是 choice、scoring 或 boolean")
        payload["decision_type"] = decision_type
        backend = self._backend_for(payload)
        raw = backend.decide(payload)
        if inspect.isawaitable(raw):
            raw = await raw
        if not isinstance(raw, Mapping):
            raise RuntimeError("决策后端返回了非对象结果")

        if decision_type == "choice":
            return self._choice_response(payload, raw, backend.name)
        if decision_type == "scoring":
            return self._scoring_response(payload, raw, backend.name)
        return self._boolean_response(payload, raw, backend.name)

    def _backend_for(self, payload: Mapping[str, Any]) -> DecisionBackend:
        if payload.get("provider") == "remote":
            endpoint = payload.get("endpoint")
            if not isinstance(endpoint, str) or not endpoint:
                raise DecisionInputError("remote provider 必须提供 endpoint")
            return RemoteDecisionBackend(endpoint)
        return self.backend

    @staticmethod
    def _choice_ids(options: Any) -> tuple[str, ...]:
        if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
            return ()
        result: list[str] = []
        for option in options:
            if isinstance(option, Mapping):
                value = option.get("id", option.get("value", option.get("label")))
            else:
                value = option
            if value is None or not str(value).strip():
                raise DecisionInputError("choice 选项必须有非空 id")
            result.append(str(value))
        if len(set(result)) != len(result):
            raise DecisionInputError("choice 选项 id 必须唯一")
        return tuple(result)

    def _choice_response(self, payload: Mapping[str, Any], raw: Mapping[str, Any], provider: str) -> dict[str, Any]:
        option_ids = self._choice_ids(payload.get("options"))
        runs = raw.get("samples") or payload.get("samples")
        if runs is not None:
            if not isinstance(runs, Sequence) or isinstance(runs, (str, bytes)):
                raise DecisionInputError("samples 必须是多轮对象结果")
            probabilities = debias_probability_runs(runs)
        else:
            values = raw.get("probabilities") or raw.get("scores") or payload.get("probabilities") or payload.get("scores")
            if values is None:
                if not option_ids:
                    raise DecisionInputError("choice 至少需要 options 或概率结果")
                values = {option_id: 1 for option_id in option_ids}
            if isinstance(values, Mapping):
                probabilities = normalize_probabilities({str(key): value for key, value in values.items()})
            elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                if not option_ids or len(values) != len(option_ids):
                    raise DecisionInputError("choice 概率数量必须与 options 一致")
                probabilities = dict(zip(option_ids, normalize_probabilities(values)))
            else:
                raise DecisionInputError("choice 概率结果必须是对象或数组")
        if not isinstance(probabilities, Mapping):
            if not option_ids or len(probabilities) != len(option_ids):
                raise DecisionInputError("choice 概率数量必须与 options 一致")
            probabilities = dict(zip(option_ids, probabilities))
        if option_ids and set(probabilities) != set(option_ids):
            raise DecisionInputError("choice 概率结果必须覆盖全部 options")
        chosen = max(probabilities, key=probabilities.get)
        drift = position_bias_drift(runs) if runs is not None else (max(probabilities.values()) - min(probabilities.values())) * 100
        return {
            "protocol_version": "system-one.v1",
            "decision_type": "choice",
            "provider": provider,
            "result": {"choice_id": str(chosen)},
            "probabilities": {str(key): float(value) for key, value in probabilities.items()},
            "debias": {"method": "anyjev-cyclic-shift-v1", "position_bias_drift_pct": float(drift)},
        }

    def _scoring_response(self, payload: Mapping[str, Any], raw: Mapping[str, Any], provider: str) -> dict[str, Any]:
        values = raw.get("scores") or payload.get("scores")
        if not isinstance(values, Mapping) or not values:
            values = {
                str(option.get("id", option.get("label"))): option.get("score")
                for option in payload.get("options", ())
                if isinstance(option, Mapping) and option.get("score") is not None
            }
        if not values:
            raise DecisionInputError("scoring 至少需要 scores 或带 score 的 options")
        scores = {str(key): float(value) for key, value in values.items()}
        if any(not math.isfinite(value) or value < 0 or value > 100 for value in scores.values()):
            raise DecisionInputError("scoring 分数必须在 0 到 100 之间")
        return {
            "protocol_version": "system-one.v1",
            "decision_type": "scoring",
            "provider": provider,
            "result": {"scores": scores},
        }

    @staticmethod
    def _boolean_response(payload: Mapping[str, Any], raw: Mapping[str, Any], provider: str) -> dict[str, Any]:
        value = raw.get("value", payload.get("value"))
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
    "LocalDecisionBackend",
    "RemoteDecisionBackend",
    "build_decision_api_router",
]
