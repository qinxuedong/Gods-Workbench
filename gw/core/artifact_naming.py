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

"""Gods-Workbench 统一资源产物与分集分镜命名系统工具库 (GNS-2026)。

严格遵守 docs/governance/GLOBAL-NAMING-SPEC.md：
- 双层解耦：物理存储安全名与面向用户/NLE语义导出名解耦；
- 工业化分镜：分集 (EPxx / SxxEPxx)、场次 (SCxx)、镜头 (SHxxx)；
- 三位补齐版本：v001, v002；
- 封闭修饰词白名单：raw, master, proxy, preview, thumb, sprite, waveform, approved, part；
- 零外部依赖，恪守 KISS 原则。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from typing import Any, Optional

#: 封闭修饰词字典
MODIFIER_WHITELIST: frozenset[str] = frozenset(
    {"raw", "master", "proxy", "preview", "thumb", "sprite", "waveform", "approved", "part"}
)

#: 正则表达式模式
_RE_VERSION = re.compile(r"^v([0-9]+)$", re.IGNORECASE)
_RE_EPISODE_ONLY = re.compile(r"^EP([0-9]{2,})$", re.IGNORECASE)
_RE_SEASON_EPISODE = re.compile(r"^S([0-9]{2,})(?:EP|E)([0-9]{2,})$", re.IGNORECASE)
_RE_SCENE = re.compile(r"^SC([0-9]{2,})$", re.IGNORECASE)
_RE_SHOT = re.compile(r"^SH([0-9]{3,})$", re.IGNORECASE)
_RE_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")

#: 存储层非法字符集（包括 Windows 设备保留与常见路径注入符）
_RE_DANGEROUS_CHARS = re.compile(r'[\\/:*?"<>| \t\r\n\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
)


class NamingValidationError(ValueError):
    """命名规范校验失败异常。"""


def format_version(version: int | str) -> str:
    """格式化版本号为三位补零的标准小写字符串 (如 v001, v010, v105)。"""
    if isinstance(version, int):
        if version < 1:
            raise NamingValidationError(f"版本序号必须为正整数: {version}")
        return f"v{version:03d}"
    
    val = str(version).strip()
    match = _RE_VERSION.match(val)
    if match:
        num = int(match.group(1))
        return f"v{num:03d}"
    
    # 纯数字字符串兼容
    if val.isdigit():
        num = int(val)
        if num < 1:
            raise NamingValidationError(f"版本序号必须为正整数: {val}")
        return f"v{num:03d}"
    
    raise NamingValidationError(f"非法版本格式: '{version}'，必须为正整数或 v001 格式")


def format_episode(episode: int | str, season: Optional[int | str] = None) -> str:
    """格式化分集标记 (如 EP01, EP120, S01EP01)。"""
    ep_num: int
    if isinstance(episode, int):
        ep_num = episode
    else:
        ep_str = str(episode).strip()
        match_only = _RE_EPISODE_ONLY.match(ep_str)
        if match_only:
            ep_num = int(match_only.group(1))
        elif ep_str.isdigit():
            ep_num = int(ep_str)
        else:
            match_se = _RE_SEASON_EPISODE.match(ep_str)
            if match_se:
                s_num = int(match_se.group(1))
                e_num = int(match_se.group(2))
                return f"S{s_num:02d}EP{e_num:02d}"
            raise NamingValidationError(f"非法分集格式: '{episode}'")
    
    if ep_num < 1:
        raise NamingValidationError(f"分集序号必须为正整数: {episode}")
    
    ep_tag = f"EP{ep_num:03d}" if ep_num >= 100 else f"EP{ep_num:02d}"

    if season is not None:
        sea_num: int
        if isinstance(season, int):
            sea_num = season
        else:
            sea_str = str(season).strip().lstrip("Ss")
            if sea_str.isdigit():
                sea_num = int(sea_str)
            else:
                raise NamingValidationError(f"非法季号格式: '{season}'")
        if sea_num < 1:
            raise NamingValidationError(f"季序号必须为正整数: {season}")
        return f"S{sea_num:02d}{ep_tag}"

    return ep_tag


def format_scene(scene: int | str) -> str:
    """格式化场次标记 (如 SC01, SC15)。"""
    num: int
    if isinstance(scene, int):
        num = scene
    else:
        s_str = str(scene).strip()
        match = _RE_SCENE.match(s_str)
        if match:
            num = int(match.group(1))
        elif s_str.isdigit():
            num = int(s_str)
        else:
            raise NamingValidationError(f"非法场次格式: '{scene}'")
    if num < 1:
        raise NamingValidationError(f"场次序号必须为正整数: {scene}")
    return f"SC{num:02d}"


def format_shot(shot: int | str) -> str:
    """格式化镜头标记 (如 SH001, SH045)。"""
    num: int
    if isinstance(shot, int):
        num = shot
    else:
        s_str = str(shot).strip()
        match = _RE_SHOT.match(s_str)
        if match:
            num = int(match.group(1))
        elif s_str.isdigit():
            num = int(s_str)
        else:
            raise NamingValidationError(f"非法镜头格式: '{shot}'")
    if num < 1:
        raise NamingValidationError(f"镜头序号必须为正整数: {shot}")
    return f"SH{num:03d}"


def format_artifact_filename(
    name: str,
    *,
    version: int | str = 1,
    scene: Optional[int | str] = None,
    shot: Optional[int | str] = None,
    episode: Optional[int | str] = None,
    season: Optional[int | str] = None,
    project_slug: Optional[str] = None,
    modifier: Optional[str] = None,
    extension: Optional[str] = None,
    ext: Optional[str] = None,
) -> str:
    """按照 GNS-2026 规范拼装语义展示与导出文件名。
    
    公式：[PROJ_][分集_][场次_][镜头_]<主体描述>_<版本号>[_<修饰词>].<扩展名>
    """
    clean_name = str(name).strip().replace(" ", "-")
    if not clean_name:
        raise NamingValidationError("主体描述 (name) 不能为空")
    if not _RE_SAFE_SEGMENT.match(clean_name):
        raise NamingValidationError(f"主体描述包含非法字符: '{name}'，仅限英文字母、数字、短横线与下划线")

    resolved_ext = ext if ext is not None else (extension if extension is not None else "mp4")
    clean_ext = str(resolved_ext).lstrip(".").strip().lower()
    if not clean_ext:
        raise NamingValidationError("扩展名不能为空")

    version_tag = format_version(version)

    parts: list[str] = []

    # 1. 项目前缀 (可选)
    if project_slug:
        slug = str(project_slug).strip().replace(" ", "-")
        if not slug.startswith("PRJ_"):
            parts.append(f"PRJ_{slug}")
        else:
            parts.append(slug)

    # 2. 分集前缀 (可选)
    if episode is not None:
        parts.append(format_episode(episode, season=season))

    # 3. 场次前缀 (可选)
    if scene is not None:
        parts.append(format_scene(scene))

    # 4. 镜头前缀 (可选)
    if shot is not None:
        parts.append(format_shot(shot))

    # 5. 主体描述
    parts.append(clean_name)

    # 6. 版本号
    parts.append(version_tag)

    # 7. 修饰词 (可选，受封闭字典约束)
    if modifier:
        mod = str(modifier).strip().lower()
        if mod not in MODIFIER_WHITELIST:
            raise NamingValidationError(
                f"修饰词 '{modifier}' 不在封闭白名单中: {sorted(MODIFIER_WHITELIST)}"
            )
        parts.append(mod)

    return "_".join(parts) + f".{clean_ext}"


def parse_artifact_filename(filename: str) -> dict[str, Any]:
    """解析符合 GNS 规范的产物文件名并拆解元数据。"""
    pure = PurePosixPath(filename)
    extension = pure.suffix.lstrip(".").lower()
    stem = pure.stem

    segments = stem.split("_")
    if len(segments) < 2:
        raise NamingValidationError(f"文件名结构不符合 GNS 规范: '{filename}'")

    result: dict[str, Any] = {
        "extension": extension,
        "project_slug": None,
        "season": None,
        "episode": None,
        "scene": None,
        "shot": None,
        "name": None,
        "version": None,
        "modifier": None,
    }

    cursor = 0
    total = len(segments)

    # 解析 PRJ_ 前缀
    if segments[cursor] == "PRJ" and cursor + 1 < total:
        result["project_slug"] = segments[cursor + 1]
        cursor += 2

    # 解析分集 (EPxx 或 SxxEPxx)
    if cursor < total:
        seg = segments[cursor]
        m_se = _RE_SEASON_EPISODE.match(seg)
        m_ep = _RE_EPISODE_ONLY.match(seg)
        if m_se:
            result["season"] = int(m_se.group(1))
            result["episode"] = int(m_se.group(2))
            cursor += 1
        elif m_ep:
            result["episode"] = int(m_ep.group(1))
            cursor += 1

    # 解析场次 (SCxx)
    if cursor < total:
        m_sc = _RE_SCENE.match(segments[cursor])
        if m_sc:
            result["scene"] = int(m_sc.group(1))
            cursor += 1

    # 解析镜头 (SHxxx)
    if cursor < total:
        m_sh = _RE_SHOT.match(segments[cursor])
        if m_sh:
            result["shot"] = int(m_sh.group(1))
            cursor += 1

    remaining = segments[cursor:]
    if not remaining:
        raise NamingValidationError(f"缺失主体描述与版本号: '{filename}'")

    # 检查末尾是否为修饰词
    if len(remaining) >= 2 and remaining[-1].lower() in MODIFIER_WHITELIST:
        result["modifier"] = remaining[-1].lower()
        remaining = remaining[:-1]

    # 检查倒数第一项是否为版本号
    if remaining and _RE_VERSION.match(remaining[-1]):
        result["version"] = remaining[-1].lower()
        remaining = remaining[:-1]
    else:
        raise NamingValidationError(f"未找到合法的版本号标记 (如 v001): '{filename}'")

    if not remaining:
        raise NamingValidationError(f"缺失主体描述: '{filename}'")

    result["name"] = "_".join(remaining)
    return result


def sanitize_storage_filename(filename: str, fallback_id: Optional[str] = None) -> str:
    """清洗文件名，确保物理落盘 100% 纯 ASCII、无空格、无路径遍历字符。
    
    若文件名包含非 ASCII 字符或非法字符，则清洗为安全形式；
    若清洗后为空或命中系统保留名，则回退到 fallback_id 或标准 hash 替代名。
    """
    pure = PurePosixPath(filename)
    stem = pure.stem
    ext = pure.suffix.lower().lstrip(".")

    # 移除非 ASCII 字符（Unicode 归一化）
    normalized = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    # 替换危险字符与空格为下划线
    cleaned_stem = _RE_DANGEROUS_CHARS.sub("_", normalized)
    # 收敛连续下划线
    cleaned_stem = re.sub(r"_{2,}", "_", cleaned_stem).strip("._")

    # 检查是否为 Windows 保留字或空
    if not cleaned_stem or cleaned_stem.upper() in _WINDOWS_RESERVED_NAMES:
        if fallback_id:
            cleaned_stem = str(fallback_id).strip()
        else:
            cleaned_stem = "artifact"

    final_ext = f".{ext}" if ext and _RE_SAFE_SEGMENT.match(ext) else ""
    return f"{cleaned_stem}{final_ext}"


def validate_artifact_filename(filename: str) -> tuple[bool, Optional[str]]:
    """便捷校验文件名是否符合 GNS 规范，返回 (True, None) 或 (False, 原因)。"""
    try:
        parse_artifact_filename(filename)
        return True, None
    except NamingValidationError as exc:
        return False, str(exc)
