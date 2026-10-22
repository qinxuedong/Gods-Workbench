"""20ms Persona 极速路由器。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .skill_loader import MountedSkill, SkillLoader
from .sniffer import ContextSniffer, SniffResult


class PersonaInputError(ValueError):
    """路由输入不满足稳定契约。"""


@dataclass(frozen=True, slots=True)
class PersonaRouteResult:
    """一次 Persona 路由与 Skill 挂载结果。"""

    protocol_version: str
    sniff: SniffResult
    skills: dict[str, MountedSkill]
    elapsed_ms: float


class PersonaRouter:
    """组合嗅探与四级 Skill 装载，输出打点后的路由结果。"""

    def __init__(self, sniffer: ContextSniffer | None = None) -> None:
        self._sniffer = sniffer or ContextSniffer()

    def route(self, prompt: Any, context: Any) -> PersonaRouteResult:
        """返回身份、四级 Skill 与路由耗时；失败关闭而不是静默误路由。"""
        started = time.perf_counter()
        if not isinstance(prompt, str):
            raise PersonaInputError("prompt 必须是字符串")
        if not isinstance(context, dict) or not isinstance(
            context.get("surface"),
            str,
        ):
            raise PersonaInputError("context.surface 必须是字符串")

        sniff = self._sniffer.sniff(prompt, context)
        skills = SkillLoader().load(sniff.persona_id)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return PersonaRouteResult(
            protocol_version="persona-router.v1",
            sniff=sniff,
            skills=skills,
            elapsed_ms=elapsed_ms,
        )


__all__ = ["PersonaInputError", "PersonaRouteResult", "PersonaRouter"]
