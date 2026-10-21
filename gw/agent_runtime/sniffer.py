"""多态身份上下文嗅探器。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SniffResult:
    """一次稳定身份判定结果。"""

    persona_id: str
    confidence: float
    signals: tuple[str, ...]


class ContextSniffer:
    """依据输入特征与编辑上下文确定智能体身份。"""

    _CONTEXT_PERSONAS = {
        "screenplay": "screenwriter",
        "storyboard": "director",
        "production": "director",
        "assets": "asset_steward",
        "settings": "workbench_guide",
    }
    _PROMPT_PERSONAS = {
        "screenwriter": ("对白", "剧本", "人物", "独白", "重写", "screenplay"),
        "director": ("分镜", "镜头", "机位", "推镜", "节奏", "导演", "storyboard"),
        "asset_steward": ("素材", "资产", "立绘", "导入", "重复", "清理", "asset"),
        "workbench_guide": ("设置", "备份", "恢复", "参数", "健康检查", "存储", "settings"),
    }

    def sniff(
        self,
        prompt: object,
        context: object,
    ) -> SniffResult:
        """返回确定性身份判定；输入异常时回退为向导身份。"""
        if not isinstance(prompt, str) or not isinstance(context, dict):
            return SniffResult("workbench_guide", 0.15, ("fallback:invalid_input",))

        text = prompt.strip().casefold()
        surface = context.get("surface")
        signals: list[str] = [f"prompt:{text[:24]}" for _ in (text,)] if text else []

        if isinstance(surface, str):
            persona = self._CONTEXT_PERSONAS.get(surface.casefold())
            if persona:
                signals.append(f"context:{surface.casefold()}")
                return SniffResult(persona, 0.9, tuple(signals))

        matches: list[tuple[float, str]] = []
        for persona_id, keywords in self._PROMPT_PERSONAS.items():
            score = sum(1 for keyword in keywords if keyword in text)
            if score:
                matches.append((float(score), persona_id))
        if matches:
            score, persona_id = max(matches, key=lambda item: (item[0], item[1]))
            signals.append(f"keywords:{persona_id}")
            return SniffResult(persona_id, min(0.85, 0.65 + score * 0.05), tuple(signals))

        signals.append("fallback:unknown")
        return SniffResult("workbench_guide", 0.15, tuple(signals))


__all__ = ["ContextSniffer", "SniffResult"]
