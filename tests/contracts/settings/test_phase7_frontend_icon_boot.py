"""Phase 7 前端首屏图标渲染回归守卫测试。

针对 Phase 6 补正复核中发现的既有缺陷：
`/static/api-settings.html` 首屏 HTML 含大量 `data-lucide` 占位，但
`api-settings.js` 的 boot 路径（`window.onload`）未调用 `lucide.createIcons()`，
导致首屏图标全部不渲染（实测 `svg.lucide` 计数为 0）。

本用例为**纯 Python 契约测试**（不启动浏览器），断言 boot 路径确实触发了图标渲染，
从而在源码层面防止该缺陷回归。对修复前的文件本用例会失败。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
API_SETTINGS_JS = REPO_ROOT / "web" / "js" / "modules" / "api-settings.js"
API_SETTINGS_HTML = REPO_ROOT / "web" / "embeds" / "api-settings.html"


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少预期文件: {path}"
    return path.read_text(encoding="utf-8")


def _extract_arrow_block(source: str, anchor_index: int) -> str:
    """从 anchor 起定位第一个 `{`，用花括号配平截取该代码块本体（含两端花括号）。"""
    start = source.find("{", anchor_index)
    assert start != -1, "未能在 window.onload 附近定位函数体起始 `{`"
    depth = 0
    for i in range(start, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError("window.onload 函数体花括号未能配平")


def test_api_settings_html_has_lucide_placeholders():
    """前置事实：api-settings.html 首屏确实包含 data-lucide 占位（缺陷的真实前提）。"""
    html = _read(API_SETTINGS_HTML)
    placeholders = re.findall(r"data-lucide=", html)
    assert len(placeholders) > 0, "api-settings.html 应包含 data-lucide 图标占位"
    # 必须引用本地 vendored lucide 运行时，否则 createIcons 无实现可用
    assert "vendor/js/lucide.js" in html, "api-settings.html 必须引用本地 lucide 运行时"


def test_api_settings_boot_path_renders_icons():
    """核心断言：boot 路径（window.onload）内必须调用 refreshIcons()/createIcons()。

    修复前：window.onload 块内无任何 createIcons/refreshIcons 调用 -> 断言失败（可复现缺陷）。
    修复后：boot 末尾调用 refreshIcons() -> 断言通过。
    """
    js = _read(API_SETTINGS_JS)

    # 1. 必须存在 refreshIcons 定义（说明渲染入口存在），且其实现调用 lucide.createIcons()
    assert "function refreshIcons" in js, "api-settings.js 应定义 refreshIcons() 渲染入口"
    assert "createIcons()" in js, "refreshIcons() 应通过 lucide.createIcons() 渲染图标"

    # 2. 定位并截取 window.onload 引导块函数体
    anchor = js.find("window.onload")
    assert anchor != -1, "api-settings.js 应存在 window.onload 首屏引导块"
    boot_block = _extract_arrow_block(js, anchor)

    # 3. 核心断言：boot 块内必须出现 refreshIcons()（或显式 createIcons()）调用
    assert re.search(r"\brefreshIcons\s*\(\s*\)\s*;", boot_block) or re.search(
        r"\bcreateIcons\s*\(", boot_block
    ), (
        "api-settings.js 的 boot 路径 (window.onload) 未调用 refreshIcons()/createIcons()；"
        "首屏 data-lucide 占位将不会被替换为 svg（Phase 6 复核发现的历史缺陷会回归）"
    )
