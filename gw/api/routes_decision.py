"""C17-T1 标准 System One 决策 API。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gw.decision.router import DecisionInputError, DecisionRouter, build_decision_api_router


default_decision_router = DecisionRouter()


class ChoiceOption(BaseModel):
    """choice 选项的稳定标识、展示文案与可选分数。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=256)
    label: str | None = Field(default=None, max_length=2_000)
    score: float | None = Field(default=None, ge=0, le=100)


class SystemOneRequest(BaseModel):
    """choice、scoring 与 boolean 三种决策请求的公共容器。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["choice", "scoring", "boolean"] = "choice"
    prompt: str = Field(default="", max_length=200_000)
    options: list[ChoiceOption | str] = Field(default_factory=list, max_length=128)
    scores: dict[str, float] | None = None
    probabilities: dict[str, float] | list[float] | None = None
    samples: list[dict[str, float] | list[float]] | None = Field(default=None, max_length=128)
    value: bool | None = None
    provider: Literal["local", "remote"] = "local"


async def system_one(payload: SystemOneRequest) -> dict[str, Any] | JSONResponse:
    """执行标准决策请求，使用统一响应和错误格式。"""
    request = payload.model_dump(exclude_none=True)
    request["decision_type"] = payload.type
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
