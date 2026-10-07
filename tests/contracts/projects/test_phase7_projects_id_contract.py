# -*- coding: utf-8 -*-
"""Phase 7 P7-A3 项目中心实体 ID 归一化回归守卫测试。

背景（真实缺陷，已由真实浏览器实测复现）：
项目中心默认落地页 `/static/pages/projects.html` 首屏抛
`TypeError: Cannot read properties of undefined (reading 'slice')`
（`projects-controller.js` 渲染路径 `S${(p.id.slice(-1) || '1')}`）。

根因：契约与黄金夹具对项目列表的稳定实体 ID 字段命名为 `project_id`
（见 `docs/contracts/PROJECTS-HUB-INTERFACE-CATALOG.yaml` 的 `list_projects.response_200`
与 `docs/fixtures/projects-hub-list-active.json`），而控制器在 API 摄取处直接
`state.projects = list;`，导致渲染期 `p.id` 为 `undefined`。

修复口径：在**摄取边界**归一化（`project_id` -> 前端内部 `id`），渲染路径不变。

本用例为**纯 Python 契约测试**（不启动浏览器），断言摄取边界确实做了归一化。
对修复前的文件本用例会**确定失败**。
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONTROLLER_JS = REPO_ROOT / "web" / "js" / "controllers" / "projects-controller.js"
CATALOG_YAML = REPO_ROOT / "docs" / "contracts" / "PROJECTS-HUB-INTERFACE-CATALOG.yaml"
LIST_FIXTURE = REPO_ROOT / "docs" / "fixtures" / "projects-hub-list-active.json"

# 摄取锚点：`load()` 内解析列表响应的那一行
_INGEST_ANCHOR = "const list = data?.projects || data;"
_INGEST_WINDOW = 900


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少预期文件: {path}"
    return path.read_text(encoding="utf-8")


def assert_normalizes_project_id(source: str) -> None:
    """断言摄取边界把契约字段 `project_id` 归一化为前端内部 `id`。

    独立可复现：对修复前的 `projects-controller.js` 文本调用本函数必抛 AssertionError。
    """
    anchor = source.find(_INGEST_ANCHOR)
    assert anchor != -1, (
        "未定位到项目列表摄取锚点 `%s`；若摄取位置被重构，请同步更新本守卫测试。" % _INGEST_ANCHOR
    )
    region = source[anchor : anchor + _INGEST_WINDOW]

    # 核心断言：摄取赋值必须经映射产出 `id`，而不是裸赋 list
    assert re.search(r"state\.projects\s*=\s*list\.map\(", region), (
        "项目列表摄取未做实体 ID 归一化：后端契约返回 `project_id`，"
        "裸赋 `state.projects = list` 会使渲染路径 `p.id` 为 undefined，"
        "进而抛 TypeError 并导致 `/static/pages/projects.html` 首屏卡片全空（Phase 7 实测缺陷）。"
    )
    assert "project_id" in region, (
        "归一化映射未读取契约字段 `project_id`（见 docs/contracts/PROJECTS-HUB-INTERFACE-CATALOG.yaml）。"
    )
    assert re.search(r"id\s*:\s*p\.id\s*\|\|\s*p\.project_id", region), (
        "归一化未采用 `id: p.id || p.project_id` 口径：需兼容既有 `id` 字段并优先保留契约 `project_id`。"
    )


def test_projects_hub_contract_declares_project_id_as_stable_id():
    """前置事实：契约与黄金夹具都以 `project_id` 作为项目稳定实体 ID。"""
    catalog = _read(CATALOG_YAML)
    assert "list_projects" in catalog, "接口目录应包含 list_projects 接口"
    assert "project_id: string" in catalog, "接口目录中项目稳定 ID 字段应为 project_id"

    fixture = json.loads(_read(LIST_FIXTURE))
    projects = fixture.get("projects")
    assert isinstance(projects, list) and projects, "黄金夹具应包含非空 projects 数组"
    assert "project_id" in projects[0], "黄金夹具的项目对象应以 project_id 为稳定 ID 字段"
    assert "id" not in projects[0], (
        "黄金夹具不应含 `id` 字段：这正是前端必须做归一化映射的原因（若夹具后续新增 id，请复核本测试口径）。"
    )


def test_projects_controller_normalizes_project_id_at_ingest():
    """核心断言：`projects-controller.js` 摄取边界必须把 `project_id` 归一化为 `id`。"""
    assert_normalizes_project_id(_read(CONTROLLER_JS))


def test_projects_controller_render_path_consumes_project_id():
    """前置事实：渲染路径确实按项目 `id` 取值，故归一化是必要条件。

    崩溃点：`S${(p.id.slice(-1) || '1')}`（`p` 为项目对象，`p.id` 为 undefined 时 `.slice` 抛 TypeError）。
    """
    js = _read(CONTROLLER_JS)
    assert re.search(r"p\.id\.slice\(", js), (
        "渲染路径应存在 `p.id.slice(...)` 取值（本缺陷的崩溃点）；"
        "若渲染改用了其他字段，请同步复核摄取归一化口径。"
    )


# ---------------------------------------------------------------------------
# 扩散面：同一摄取语义在其余前端入口的复现位置
# 每个断言都针对「契约字段 project_id -> 内部字段 id」的归一化，缺一即回退。
# ---------------------------------------------------------------------------

HOME_JS = REPO_ROOT / "web" / "js" / "controllers" / "home-controller.js"
WORKSHOP_HTML = REPO_ROOT / "web" / "pages" / "workshop.html"
TELEMETRY_JS = REPO_ROOT / "web" / "js" / "core" / "hardware-telemetry.js"
EPISODE_JS = REPO_ROOT / "web" / "js" / "modules" / "episode-pipeline.js"
CANVAS_LIST_JS = REPO_ROOT / "web" / "js" / "modules" / "canvas-list.js"
ASSET_MANAGER_JS = REPO_ROOT / "web" / "js" / "modules" / "asset-manager.js"


def _assert_contains(source: str, pattern: str, label: str) -> None:
    assert re.search(pattern, source), (
        f"{label} 未做 project_id -> id 归一化：后端契约与黄金夹具的稳定实体 ID 字段为 "
        f"`project_id`（docs/contracts/PROJECTS-HUB-INTERFACE-CATALOG.yaml、"
        f"docs/fixtures/projects-hub-list-active.json），前端若按 `id` 取值将得到 undefined。"
    )


def test_home_controller_normalizes_project_id_at_ingest():
    """首页 `/static/pages/index.html`：列表摄取与新建响应都必须归一化。

    实测缺陷：卡片 `data-project-id=""`、`onclick=V2Home.selectProject('')`，
    点击后 localStorage 无 `workspace_project_id`，项目选中彻底失效。
    """
    js = _read(HOME_JS)
    _assert_contains(js, r"state\.projects\s*=\s*list\.map\(", "home-controller 列表摄取")
    _assert_contains(js, r"id\s*:\s*p\.id\s*\|\|\s*p\.project_id", "home-controller 列表摄取")
    _assert_contains(js, r"id\s*:\s*raw\.id\s*\|\|\s*raw\.project_id", "home-controller 新建响应")


def test_workshop_html_normalizes_project_id_in_catalog():
    """工坊页项目目录摄取：`p.id` 判空会丢弃全部真实项目，回退到内置演示工程。"""
    html = _read(WORKSHOP_HTML)
    _assert_contains(html, r"p\.id\s*\|\|\s*p\.project_id", "workshop 项目目录摄取")
    _assert_contains(html, r"projObj\.id\s*\|\|\s*projObj\.project_id", "workshop 单项目摄取")


def test_hardware_telemetry_normalizes_project_id_for_calendar():
    """项目排期弹窗：未归一化时排期条 `data-project-calendar-project` 为空，点击跳转静默失效。"""
    js = _read(TELEMETRY_JS)
    _assert_contains(js, r"project\.id\s*\|\|\s*project\.project_id", "hardware-telemetry 排期归一化")


def test_episode_pipeline_normalizes_project_id():
    """剧集流水线页：未归一化时项目下拉与项目名回退全部失效。"""
    js = _read(EPISODE_JS)
    _assert_contains(js, r"item\.id\s*\|\|\s*item\.project_id", "episode-pipeline 列表摄取")
    _assert_contains(js, r"pObj\.id\s*\|\|\s*pObj\.project_id", "episode-pipeline 单项目摄取")


def test_canvas_list_normalize_project_covers_project_id():
    """画布列表页：未归一化时项目行 data-project-id 变成字符串 "undefined"。"""
    js = _read(CANVAS_LIST_JS)
    _assert_contains(js, r"value\.id\s*\|\|\s*value\.project_id", "canvas-list normalizeProject")


def test_asset_manager_normalizes_project_id():
    """素材库管理页：未归一化时 activeProjectId 为空、项目看板不加载。"""
    js = _read(ASSET_MANAGER_JS)
    _assert_contains(js, r"id\s*:\s*item\.id\s*\|\|\s*item\.project_id", "asset-manager 列表摄取")
    _assert_contains(js, r"data\.project\.id\s*\|\|\s*data\.project\.project_id", "asset-manager 新建响应")


def test_home_controller_create_path_does_not_call_out_of_scope_helper():
    """既有缺陷回归守卫：home-controller.js 不得调用他模块作用域的 updateNavPillsProject()。

    事实：`updateNavPillsProject` 仅定义于 `projects-controller.js` 的 `WorkbenchProjects` 模块内部（:740），
    在 `home-controller.js` 的作用域中**不可见**。原代码在 `handleCreateProject()` 的 res.ok 分支内调用它，
    抛 `ReferenceError` 后被外层 `catch` 吞掉，导致随后的 `renderProjectsList()` **永不执行**，
    新建项目卡片不出现（真实浏览器实测：card 数恒为 1；本地 localStorage 却已写入）。

    本文件自身的等价辅助函数为 `updateNavPills(targetId)`（:365）。
    """
    js = _read(HOME_JS)
    # 剥离行注释与块注释后再断言，避免把说明文字误判为调用
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    assert "function updateNavPillsProject" not in code, (
        "home-controller.js 不应自行定义 updateNavPillsProject；本守卫断言的是「不得调用他模块作用域符号」。"
    )
    assert not re.search(r"(?<![\w$.])updateNavPillsProject\s*\(", code), (
        "home-controller.js 不得调用 updateNavPillsProject()：该标识符不在本文件作用域内，"
        "会抛 ReferenceError 并被外层 catch 吞掉，导致新建后 renderProjectsList() 不执行（Phase 7 实测缺陷）。"
    )
    # 正向：应调用本文件内的等价辅助函数
    assert re.search(r"(?<![\w$.])updateNavPills\s*\(\s*created\.id\s*\)", js), (
        "home-controller.js 新建路径应调用本文件内的 updateNavPills(created.id) 以同步顶栏胶囊导航。"
    )
