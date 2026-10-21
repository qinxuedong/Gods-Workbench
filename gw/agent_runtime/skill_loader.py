"""四级 Skill 静态装载器。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MountedSkill:
    """已激活的 Skill 定义。"""

    level: str
    skill_id: str
    enabled: bool
    entrypoint: str


class SkillLoader:
    """加载平台内置、领域专家、项目级与用户临时 Skill。"""

    _LEVELS = ("platform", "domain_expert", "project", "user_temporary")

    def load(self, persona_id: str) -> dict[str, MountedSkill]:
        """返回稳定顺序的四级 Skill 映射；不访问文件系统。"""
        persona = persona_id.strip().casefold()
        if not persona:
            raise ValueError("persona_id 不能为空")
        return {
            "platform": MountedSkill(
                "platform",
                "platform-core",
                True,
                "gw/agents/platform_core.py",
            ),
            "domain_expert": MountedSkill(
                "domain_expert",
                f"domain-{persona}",
                True,
                "gw/agents/domain.py",
            ),
            "project": MountedSkill(
                "project",
                f"project-{persona}",
                True,
                "gw/agents/project_skills.py",
            ),
            "user_temporary": MountedSkill(
                "user_temporary",
                f"user-temp-{persona}",
                True,
                "gw/agents/user_skills.py",
            ),
        }


__all__ = ["MountedSkill", "SkillLoader"]
