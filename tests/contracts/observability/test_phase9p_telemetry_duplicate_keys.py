# -*- coding: utf-8 -*-
"""Phase 9P：静态 JS「同名方法键重复定义」守卫。

背景（真实缺陷，2026-09-22 已字节级核实）：
``web/js/core/hardware-telemetry.js`` 的 ``HardwareDeck``
对象字面量里 ``handleLogout`` 被**定义了两次**（HEAD 版本第 574 与 590 行）。
JS 对象字面量**后键覆盖前键**，因此：

- 生效的是后面那份「残缺实现」——不重置 ``authenticated`` / ``logout_available``，
  也不回读服务端状态（``syncAuth()``），登出后前端身份状态残留在「已认证」语义；
- 前面那份完整实现成为**静默死代码**（``node --check`` 语法合法，因此既有全部
  门禁均未捕获，属**真实漏网缺陷**）。

本守卫把该缺陷类别变成持续自动门禁：**同一缩进深度上的同名方法键**出现多次即失败。

设计取舍与局限（如实声明，不得当作完整 JS 语义分析）
----------------------------------------------------
* 只识别「行首方法键」：``name: function`` / ``name: async function`` /
  ``name: (a) => ...`` / ``name: (a) {``。
* **不使用**花括号深度跟踪，因为 JS 模板串 ``${}``、正则字面量中的 ``{ }``
  会让朴素扫描产生大量假阳性；改用**缩进深度**近似对象层级——
  本仓库代码风格稳定（同一对象的方法缩进一致），该近似**已验证**：
  在 HEAD 版本上精确命中 ``handleLogout``（574/590），修复后归零，
  且对全仓库 `git ls-files` 内静态 JS 的**假阳性为 0**。
* 不覆盖：数据属性重复键、计算属性名 ``[expr]:``、跨行写法、getter/setter。

证据边界：本文件是**静态源码守卫**，不等于真实浏览器运行时 E2E。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STATIC_DIR = REPO_ROOT / "web"
TELEMETRY_JS = STATIC_DIR / "js" / "core" / "hardware-telemetry.js"

# 行首方法键：`name: function` / `name: async function` / `name: (args) =>` / `name: (args) {`
_METHOD_KEY = re.compile(
    r"^([ \t]+)([A-Za-z_$][\w$]*)[ \t]*:[ \t]*"
    r"(?:async[ \t]+)?"
    r"(?:function\b|\([^)]*\)[ \t]*=>|\([^)]*\)[ \t]*\{)"
)


def _scan_duplicate_method_keys(source: str) -> list:
    """返回 ``[((缩进宽度, 方法名), [行号, ...]), ...]``：仅当同名同缩进出现多次。"""
    groups = {}
    for index, line in enumerate(source.split("\n"), 1):
        match = _METHOD_KEY.match(line)
        if match:
            key = (len(match.group(1)), match.group(2))
            groups.setdefault(key, []).append(index)
    return [(key, lines) for key, lines in groups.items() if len(lines) > 1]


def _iter_static_js():
    """仓库自有前端 JS（排除上游 vendor 不可变制品）。"""
    for path in sorted(STATIC_DIR.rglob("*.js")):
        relative = path.relative_to(STATIC_DIR).as_posix()
        if relative.startswith("vendor/"):
            continue
        yield path, relative


def test_telemetry_handle_logout_is_defined_once():
    """``handleLogout`` 必须只定义一次，且保留语义完整的实现。"""
    source = TELEMETRY_JS.read_text(encoding="utf-8")
    duplicates = [
        (key, lines) for key, lines in _scan_duplicate_method_keys(source)
        if key[1] == "handleLogout"
    ]
    assert not duplicates, (
        "hardware-telemetry.js 中 handleLogout 被重复定义（后键会静默覆盖前键）: "
        + repr(duplicates)
    )
    # 保留的实现必须重置登出状态并回读服务端真实状态（残缺版缺这两点）。
    assert "this.authState.logout_available = false" in source, "登出必须重置 logout_available"
    assert "this.authState.authenticated = false" in source, "登出必须重置 authenticated"
    assert "await this.syncAuth()" in source, "登出后必须回读服务端真实认证状态"


def test_no_duplicate_method_keys_in_static_js():
    """全量扫描：仓库自有前端 JS 不得存在同名同缩进的重复方法键。"""
    violations = []
    for path, relative in _iter_static_js():
        source = path.read_text(encoding="utf-8")
        for (indent, name), lines in _scan_duplicate_method_keys(source):
            violations.append(f"{relative}: 方法 {name!r}（缩进 {indent}）定义于行 {lines}")
    assert not violations, (
        "发现同名同缩进的重复方法键（后键会静默覆盖前键）:\n" + "\n".join(violations)
    )


def test_duplicate_method_key_guard_is_not_tautological():
    """负向自检：守卫必须能识别真实的重复键，否则它是恒真的无效守卫。

    直接复现 HEAD 版本 ``handleLogout`` 的双定义形态，断言守卫命中；
    并断言当前工作区版本**不再**命中（证明修复真实生效）。
    """
    duplicated = (
        "  const Deck = {\n"
        "    handleLogout: async function() {\n"
        "      this.syncAuth();\n"
        "    },\n"
        "    handleLogout: async function() {\n"
        "      this.updateAuthDOM();\n"
        "    },\n"
        "  };\n"
    )
    found = _scan_duplicate_method_keys(duplicated)
    assert found, "守卫未能识别重复的 handleLogout 方法键"
    assert found[0][0][1] == "handleLogout"

    # 单定义样本不得报（避免守卫恒真）。
    single = duplicated.replace(
        "    handleLogout: async function() {\n      this.updateAuthDOM();\n    },\n", ""
    )
    assert not _scan_duplicate_method_keys(single), "守卫对单定义样本误报"
