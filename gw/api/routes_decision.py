"""C17-T1 标准 System One 决策协议路由。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gw.decision.router import DecisionInputError, DecisionRouter, build_decision_api_router


default_decision_router = DecisionRouter()


class ChoiceOption(BaseModel):
    """choice 选项的稳定标识与展示文案。"""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., min_length=1, max_length=256)
    label: str | None = Field(None, max_length=2_000)
    score: float | None = Field(None, ge=0, le=100)


class SystemOneRequest(BaseModel):
    """对外统一的三态决策请求。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: Literal["choice", "scoring", "boolean"] = "choice"
    decision_type: Literal["choice", "scoring", "boolean"] | None = None
    prompt: str = Field("", max_length=200_000)
    options: list[ChoiceOption | str] = Field(default_factory=list, max_length=128)
    scores: dict[str, float] | None = None
    probabilities: dict[str, float] | list[float] | None = None
    samples: list[dict[str, float]] | None = Field(None, max_length=32)
    value: bool | None = None
    provider: Literal["local", "remote"] = "local"
    endpoint: str | None = Field(None, max_length=2_000)


async def system_one(payload: SystemOneRequest) -> dict[str, Any] | JSONResponse:
    """执行 typed choice、scoring 或 boolean 决策并返回统一 JSON。"""
    request = payload.model_dump(exclude_none=True)
    request["decision_type"] = payload.decision_type or payload.type
    try:
        return await default_decision_router.decide(request)
    except (DecisionInputError, ValueError, TypeError) as exc:
        return JSONResponse(
            status_code=400,
            content={"detail": {"code": "DECISION_INVALID", "message": str(exc)}},
        )
    except (RuntimeError, TimeoutError) as exc:
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "DECISION_BACKEND_UNAVAILABLE", "message": str(exc)}},
        )


router = build_decision_api_router(system_one)


__all__ = ["ChoiceOption", "SystemOneRequest", "default_decision_router", "router", "system_one"]
