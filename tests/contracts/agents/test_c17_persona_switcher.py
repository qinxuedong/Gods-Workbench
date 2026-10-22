"""C17-T4 多态身份嗅探与 20ms Persona 极速路由契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from gw.agent_runtime.persona_router import (
    PersonaInputError,
    PersonaRouter,
)
from gw.agent_runtime.skill_loader import SkillLoader
from gw.agent_runtime.sniffer import ContextSniffer


@pytest.mark.parametrize(
    ("prompt", "context", "expected"),
    [
        ("帮我重写第三幕人物对白", {"surface": "screenplay"}, "screenwriter"),
        ("这组分镜改成俯拍推镜", {"surface": "storyboard"}, "director"),
        ("素材库里找角色立绘并清理重复资产", {"surface": "assets"}, "asset_steward"),
        ("系统设置里如何恢复备份", {"surface": "settings"}, "workbench_guide"),
        ("导演对白和镜头冲突了", {"surface": "production"}, "director"),
    ],
)
def test_sniffer_maps_prompt_and_context_to_stable_persona(
    prompt: str,
    context: dict[str, str],
    expected: str,
) -> None:
    result = ContextSniffer().sniff(prompt, context)

    assert result.persona_id == expected
    assert result.confidence > 0.0
    assert result.signals[-1] == f"context:{context['surface']}"


def test_sniffer_defaults_to_workbench_guide_for_unknown_input() -> None:
    result = ContextSniffer().sniff("  ", {})

    assert result.persona_id == "workbench_guide"
    assert result.confidence == pytest.approx(0.15)


def test_skill_loader_loads_all_four_levels_without_file_access() -> None:
    loader = SkillLoader()

    skills = loader.load("screenwriter")

    assert tuple(skills.keys()) == (
        "platform",
        "domain_expert",
        "project",
        "user_temporary",
    )
    assert all(skill.enabled for skill in skills.values())
    assert loader.load("asset_steward") != skills


@pytest.mark.parametrize(
    ("prompt", "context", "expected"),
    [
        ("写一段人物独白", {"surface": "screenplay"}, "screenwriter"),
        ("设计机位和分镜节奏", {"surface": "storyboard"}, "director"),
        ("导入素材并登记版本", {"surface": "assets"}, "asset_steward"),
        ("启动参数与健康检查怎么配", {"surface": "settings"}, "workbench_guide"),
    ],
)
def test_persona_router_routes_and_mounts_skills_in_under_20ms(
    prompt: str,
    context: dict[str, str],
    expected: str,
) -> None:
    result = PersonaRouter().route(prompt, context)

    assert result.protocol_version == "persona-router.v1"
    assert result.sniff.persona_id == expected
    assert result.skills.keys() == {
        "platform",
        "domain_expert",
        "project",
        "user_temporary",
    }
    assert 0 < result.elapsed_ms < 20


def test_persona_router_benchmark_identity_switches_across_four_personas() -> None:
    cases = [
        ("继续写场景对白", {"surface": "screenplay"}, "screenwriter"),
        ("调整镜头节奏", {"surface": "storyboard"}, "director"),
        ("整理重复素材", {"surface": "assets"}, "asset_steward"),
        ("检查本地存储路径", {"surface": "settings"}, "workbench_guide"),
    ]

    results = [
        PersonaRouter().route(prompt, context) for prompt, context, _ in cases
    ]

    assert [result.sniff.persona_id for result in results] == [case[2] for case in cases]
    assert all(result.elapsed_ms < 20 for result in results)


def test_persona_router_rejects_non_string_prompt_and_missing_context_surface() -> None:
    router = PersonaRouter()

    with pytest.raises(PersonaInputError, match="prompt"):
        router.route(123, {"surface": "screenplay"})

    with pytest.raises(PersonaInputError, match="surface"):
        router.route("写对白", {})


def test_persona_router_skills_use_stable_module_and_ids() -> None:
    result = PersonaRouter().route(
        "重写开头对白",
        {"surface": "screenplay", "project_id": "project-alpha"},
    )

    assert result.sniff.persona_id == "screenwriter"
    assert result.skills["project"].skill_id == "project-screenwriter"
    assert Path(result.skills["platform"].entrypoint).parts[:2] == ("gw", "agents")
