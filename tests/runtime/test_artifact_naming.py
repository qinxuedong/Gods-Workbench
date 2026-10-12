# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""单元测试：统一资源产物与分集分镜命名工具库 (GNS-2026)。"""

import pytest

from gw.core.artifact_naming import (
    MODIFIER_WHITELIST,
    NamingValidationError,
    format_artifact_filename,
    format_episode,
    format_scene,
    format_shot,
    format_version,
    parse_artifact_filename,
    sanitize_storage_filename,
    validate_artifact_filename,
)


def test_format_version_padding() -> None:
    assert format_version(1) == "v001"
    assert format_version(10) == "v010"
    assert format_version(105) == "v105"
    assert format_version("v1") == "v001"
    assert format_version("v002") == "v002"
    assert format_version("99") == "v099"

    with pytest.raises(NamingValidationError):
        format_version(0)
    with pytest.raises(NamingValidationError):
        format_version("invalid")


def test_format_episode_and_season() -> None:
    assert format_episode(1) == "EP01"
    assert format_episode(99) == "EP99"
    assert format_episode(105) == "EP105"

    # 带季号
    assert format_episode(1, season=1) == "S01EP01"
    assert format_episode(12, season=2) == "S02EP12"
    assert format_episode("EP03", season="S01") == "S01EP03"
    assert format_episode("S02EP05") == "S02EP05"

    with pytest.raises(NamingValidationError):
        format_episode(0)
    with pytest.raises(NamingValidationError):
        format_episode("invalid")


def test_format_scene_and_shot() -> None:
    assert format_scene(1) == "SC01"
    assert format_scene(15) == "SC15"
    assert format_scene("SC02") == "SC02"

    assert format_shot(1) == "SH001"
    assert format_shot(45) == "SH045"
    assert format_shot("SH120") == "SH120"

    with pytest.raises(NamingValidationError):
        format_scene(-1)
    with pytest.raises(NamingValidationError):
        format_shot(0)


def test_format_artifact_filename_combinations() -> None:
    # 基础文本产物
    assert format_artifact_filename("script", version=1, ext="md") == "script_v001.md"

    # 单集剧本
    assert (
        format_artifact_filename("script", episode=1, version=1, ext="md")
        == "EP01_script_v001.md"
    )

    # 完整镜头视频（含分集、场次、镜头、修饰词）
    assert (
        format_artifact_filename(
            "hero",
            episode=1,
            scene=2,
            shot=5,
            version=1,
            modifier="raw",
            extension="mp4",
        )
        == "EP01_SC02_SH005_hero_v001_raw.mp4"
    )

    # 带项目缩写与多季编码
    assert (
        format_artifact_filename(
            "battle",
            project_slug="cyber",
            season=1,
            episode=3,
            scene=1,
            shot=12,
            version=2,
            modifier="proxy",
            extension="mp4",
        )
        == "PRJ_cyber_S01EP03_SC01_SH012_battle_v002_proxy.mp4"
    )

    # 修饰词白名单校验
    for mod in MODIFIER_WHITELIST:
        res = format_artifact_filename("test", version=1, modifier=mod, extension="png")
        assert res == f"test_v001_{mod}.png"

    # 非法修饰词拒绝
    with pytest.raises(NamingValidationError, match="不在封闭白名单中"):
        format_artifact_filename("test", version=1, modifier="unapproved_mod")

    # 非法主体名拒绝（包含空格或特殊字符）
    with pytest.raises(NamingValidationError, match="包含非法字符"):
        format_artifact_filename("hero/villain", version=1)


def test_parse_artifact_filename_roundtrip() -> None:
    filename = "PRJ_cyber_S01EP03_SC02_SH005_hero-action_v002_raw.mp4"
    parsed = parse_artifact_filename(filename)

    assert parsed["project_slug"] == "cyber"
    assert parsed["season"] == 1
    assert parsed["episode"] == 3
    assert parsed["scene"] == 2
    assert parsed["shot"] == 5
    assert parsed["name"] == "hero-action"
    assert parsed["version"] == "v002"
    assert parsed["modifier"] == "raw"
    assert parsed["extension"] == "mp4"

    # 简单文件解析
    simple = parse_artifact_filename("script_v001.md")
    assert simple["name"] == "script"
    assert simple["version"] == "v001"
    assert simple["modifier"] is None
    assert simple["extension"] == "md"

    # 无法解析的命名
    with pytest.raises(NamingValidationError):
        parse_artifact_filename("invalid.mp4")


def test_sanitize_storage_filename() -> None:
    # 过滤中文与空格
    assert sanitize_storage_filename("测试 分镜 01.mp4") == "01.mp4"
    assert sanitize_storage_filename("my hero shot.png") == "my_hero_shot.png"

    # 路径遍历与非法字符清除
    assert sanitize_storage_filename("../../dangerous:file?.mp4") == "dangerous_file.mp4"

    # Windows 设备保留名回退
    assert sanitize_storage_filename("CON.mp4", fallback_id="ast_123") == "ast_123.mp4"
    assert sanitize_storage_filename("NUL.txt", fallback_id="ast_456") == "ast_456.txt"

    # 空名称回退
    assert sanitize_storage_filename("???", fallback_id="ast_999") == "ast_999"


def test_validate_artifact_filename() -> None:
    valid, err = validate_artifact_filename("EP01_SC01_SH001_hero_v001_raw.mp4")
    assert valid is True
    assert err is None

    invalid, err = validate_artifact_filename("just_a_video.mp4")
    assert invalid is False
    assert err is not None
