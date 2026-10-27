"""MVP1-T5a 命名与重复建议规则的纯函数契约。"""

from __future__ import annotations

import builtins
import os

import pytest

from gw.asset_registry.governance_rules import (
    NamingRules,
    build_governance_suggestions,
    find_duplicate_groups,
    group_similar_names,
    normalize_filename,
    validate_name,
)


RULES = NamingRules(
    prefix="EP01",
    separator="_",
    forbidden_characters=':/\\*?"<>|',
    max_length=48,
    version_suffix_pattern=r"_v\d{3}$",
    suggested_version_suffix="v001",
)


def test_naming_rule_prefix_separator_forbidden_length_and_version_positive_cases():
    """每项已配置规则都接受符合规范的名称。"""
    assessment = validate_name("EP01_scene_v007.mp4", RULES)

    assert assessment.violations == ()
    assert assessment.suggested_name is None


@pytest.mark.parametrize(
    "name, code",
    [
        ("scene_v001.mp4", "PREFIX"),
        ("EP01-scene_v001.mp4", "SEPARATOR"),
        ("EP01_bad:name_v001.mp4", "FORBIDDEN_CHARACTER"),
        ("EP01_a_name_that_is_far_too_long_for_this_rule_v001.mp4", "MAX_LENGTH"),
        ("EP01_scene_v2.mp4", "VERSION_SUFFIX"),
    ],
)
def test_each_naming_rule_returns_violation_and_suggestion(name: str, code: str):
    assessment = validate_name(name, RULES)

    assert code in {violation.code for violation in assessment.violations}
    assert assessment.suggested_name
    assert ":" not in assessment.suggested_name
    if code == "VERSION_SUFFIX":
        assert assessment.suggested_name.endswith("_v001.mp4")
    if code == "MAX_LENGTH":
        assert len(assessment.suggested_name) <= RULES.max_length


def test_name_normalization_groups_fullwidth_case_and_copy_suffix_variants():
    names = [
        "镜头01.mp4",
        "镜头01(1).mp4",
        "镜头01_副本.mp4",
        "镜头０１.MP4",
    ]

    assert {normalize_filename(name) for name in names} == {"镜头01.mp4"}
    groups = group_similar_names(names)
    assert len(groups) == 1
    assert len(groups[0].members) == len(names)
    assert set(groups[0].members) == set(names)


def test_name_normalization_groups_whitespace_and_version_copy_suffixes():
    names = ["Scene 01.MP4", "scene_01_v2.mp4", "SCENE-01.mp4"]

    assert len(group_similar_names(names)) == 1


def test_duplicate_groups_use_hash_and_distinct_paths():
    records = [
        ("a/镜头01.mp4", 100, "ABC123"),
        ("b/镜头01_副本.mp4", 200, "abc123"),
        ("c/镜头01.mp4", 100, "other-hash"),
    ]

    groups = find_duplicate_groups(records)

    assert len(groups) == 1
    assert groups[0].content_hash == "abc123"
    assert [member.path for member in groups[0].members] == [
        "a/镜头01.mp4",
        "b/镜头01_副本.mp4",
    ]


def test_same_name_with_different_hash_is_only_a_name_suggestion():
    suggestions = build_governance_suggestions(
        [("clip.mp4", 100, "hash-a"), ("clip.mp4", 100, "hash-b")],
        NamingRules(),
    )

    assert suggestions.duplicate_content_groups == ()
    assert len(suggestions.similar_name_groups) == 1
    assert suggestions.similar_name_groups[0].members == ("clip.mp4", "clip.mp4")


def test_suggestions_are_stable_when_input_order_changes():
    records = [
        ("b/Scene 01_v2.mp4", 100, "same"),
        ("a/scene_01.mp4", 100, "same"),
        ("c/scene_02.mp4", 100, "other"),
    ]

    first = build_governance_suggestions(records, NamingRules())
    second = build_governance_suggestions(reversed(records), NamingRules())

    assert first == second


def test_rules_and_suggestions_do_not_call_filesystem_io(monkeypatch):
    """分析规则仅使用输入值；禁止打开、枚举、移动或删除任何文件。"""
    def fail_io(*_args, **_kwargs):
        raise AssertionError("纯函数不得访问文件系统")

    monkeypatch.setattr(builtins, "open", fail_io)
    for name in (
        "listdir",
        "scandir",
        "stat",
        "lstat",
        "walk",
        "remove",
        "rename",
        "replace",
        "mkdir",
        "makedirs",
        "unlink",
    ):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, fail_io)

    assessment = validate_name("EP01_scene_v001.mp4", RULES)
    groups = group_similar_names(["镜头01.mp4", "镜头01(1).mp4"])
    duplicates = find_duplicate_groups(
        [("a.mp4", 1, "same"), ("b.mp4", 2, "same")]
    )
    suggestions = build_governance_suggestions(
        [("a.mp4", 1, "same"), ("b.mp4", 2, "same")], NamingRules()
    )

    assert assessment.violations == ()
    assert len(groups) == len(duplicates) == 1
    assert len(suggestions.duplicate_content_groups) == 1
