"""Tests for persona routing and context sniffing."""

import pytest

from gw.agent_runtime.persona_router import PersonaInputError, PersonaRouter
from gw.agent_runtime.sniffer import ContextSniffer


def test_context_sniffer_invalid_input():
    sniffer = ContextSniffer()
    result = sniffer.sniff(None, {})
    assert result.persona_id == "workbench_guide"
    assert result.confidence == 0.15
    assert "fallback:invalid_input" in result.signals

    result2 = sniffer.sniff("hello", None)
    assert result2.persona_id == "workbench_guide"
    assert result2.confidence == 0.15
    assert "fallback:invalid_input" in result2.signals


def test_context_sniffer_surface_matching():
    sniffer = ContextSniffer()
    result = sniffer.sniff("some prompt", {"surface": "screenplay"})
    assert result.persona_id == "screenwriter"
    assert result.confidence == 0.9
    assert "context:screenplay" in result.signals
    assert "prompt:some prompt" in result.signals

    result2 = sniffer.sniff("some prompt", {"surface": "STORYBOARD"})
    assert result2.persona_id == "director"
    assert result2.confidence == 0.9
    assert "context:storyboard" in result2.signals


def test_context_sniffer_keyword_matching():
    sniffer = ContextSniffer()
    # keyword match when surface is not matched
    result = sniffer.sniff("重写一段独白", {"surface": "unknown_surface"})
    assert result.persona_id == "screenwriter"
    assert result.confidence == min(0.85, 0.65 + 2 * 0.05)  # 2 keywords: 重写, 独白
    assert "keywords:screenwriter" in result.signals


def test_context_sniffer_fallback():
    sniffer = ContextSniffer()
    result = sniffer.sniff("hello world", {"surface": "unknown_surface"})
    assert result.persona_id == "workbench_guide"
    assert result.confidence == 0.15
    assert "fallback:unknown" in result.signals


def test_persona_router_input_validation():
    router = PersonaRouter()

    with pytest.raises(PersonaInputError, match="prompt 必须是字符串"):
        router.route(None, {"surface": "screenplay"})

    with pytest.raises(PersonaInputError, match="context.surface 必须是字符串"):
        router.route("hello", None)

    with pytest.raises(PersonaInputError, match="context.surface 必须是字符串"):
        router.route("hello", {})

    with pytest.raises(PersonaInputError, match="context.surface 必须是字符串"):
        router.route("hello", {"surface": None})


def test_persona_router_routing(monkeypatch):
    from gw.agent_runtime.skill_loader import MountedSkill

    class StubSkillLoader:
        def load(self, persona_id: str) -> dict[str, MountedSkill]:
            return {
                "platform": MountedSkill("platform", f"platform-{persona_id}", True, "stub"),
                "domain_expert": MountedSkill("domain_expert", f"domain-{persona_id}", True, "stub"),
                "project": MountedSkill("project", f"project-{persona_id}", True, "stub"),
                "user_temporary": MountedSkill("user_temporary", f"user-temp-{persona_id}", True, "stub"),
            }

    monkeypatch.setattr("gw.agent_runtime.persona_router.SkillLoader", StubSkillLoader)

    router = PersonaRouter()
    result = router.route("分镜处理", {"surface": "storyboard"})

    assert result.protocol_version == "persona-router.v1"
    assert result.sniff.persona_id == "director"
    assert result.elapsed_ms > 0

    assert "platform" in result.skills
    assert "domain_expert" in result.skills
    assert "project" in result.skills
    assert "user_temporary" in result.skills

    assert result.skills["domain_expert"].skill_id == "domain-director"
