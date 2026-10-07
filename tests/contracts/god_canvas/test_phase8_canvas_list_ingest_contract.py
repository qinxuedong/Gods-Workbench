# -*- coding: utf-8 -*-
"""Phase 8 P8-A2 画布列表摄取契约守卫（纯 Python，不启动浏览器）。

背景（真实浏览器 + 真实 HTTP 实测，2026-09-21）

`/static/canvas-list.html` 的画布列表在真实后端下**恒定加载失败**：

1. `canvas-list/api.js` 的 `listCanvases()` 请求 `/api/canvases` 时**未携带** `project_id`。
   `docs/contracts/CANVAS-INTERFACE-CATALOG.yaml` 的 `list_canvases.request_query.project_id`
   为**必填**，后端据此返回 `400 INVALID_REQUEST`（重放原文见 P8-A2 报告 §3.2）。
2. `canvas-list.js` 的 `loadAll()` 把 `listProjects()` 与 `listCanvases()` **并发**发出，
   而 `project_id` 只有在项目列表返回之后才能确定，逻辑上不可能带上该参数。
3. 画布响应摄取未按契约字段归一化：契约 `response_200` 与黄金夹具使用
   `canvas_id` / `project_id` / `mode`，而渲染路径读取 `id` / `project` / `kind`，
   缺少归一化时卡片 `data-canvas-id` 退化为字符串 `"undefined"`。

修复口径：**先取项目、再按 `project_id` 取画布**，并在**摄取边界**归一化契约字段。

本守卫为纯 Python 文本断言；对修复前的文件**确定失败**（见 P8-A2 报告 §6）。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CANVAS_LIST_JS = REPO_ROOT / "web" / "js" / "modules" / "canvas-list.js"
CANVAS_LIST_API_JS = REPO_ROOT / "web" / "js" / "modules" / "canvas-list" / "api.js"
CONTRACT_YAML = REPO_ROOT / "docs" / "contracts" / "CANVAS-INTERFACE-CATALOG.yaml"


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少预期文件: {path}"
    return path.read_text(encoding="utf-8")


def assert_list_canvases_sends_project_id(source: str) -> None:
    """`listCanvases` 必须把契约必填的 `project_id` 附到查询串上。"""
    anchor = source.find("listCanvases(")
    assert anchor != -1, "未在 canvas-list/api.js 中找到 listCanvases()；若被重构请同步本守卫。"
    region = source[anchor : anchor + 900]
    assert re.search(r"listCanvases\s*\(\s*projectId", region), (
        "listCanvases() 未接收 projectId 形参：契约 list_canvases 的 request_query.project_id 为必填，"
        "缺少该参数时后端按契约返回 400 INVALID_REQUEST，画布列表恒定加载失败。"
    )
    assert re.search(r"/api/canvases\?project_id=", region), (
        "listCanvases() 未把 project_id 放进查询串（期望 '/api/canvases?project_id='）："
        "契约要求按 project_id 过滤，缺失即 400。"
    )
    assert "encodeURIComponent(pid)" in region, (
        "listCanvases() 未对 project_id 做 encodeURIComponent 编码，存在查询串注入与编码缺陷。"
    )


def assert_load_all_is_sequential_with_project_id(source: str) -> None:
    """`loadAll()` 必须先取项目、再按已确定 project_id 取画布，不得并发缺参。"""
    anchor = source.find("async function loadAll()")
    assert anchor != -1, "未找到 loadAll()；若被重构请同步本守卫。"
    end = source.find("\nfunction ", anchor + 1)
    region = source[anchor : end if end != -1 else anchor + 4000]

    assert not re.search(r"Promise\.all\(\s*\[[^\]]*listCanvases", region, flags=re.S), (
        "loadAll() 仍把 listProjects() 与 listCanvases() 并发发出：project_id 只有在项目列表返回后"
        "才能确定，并发时画布请求必然缺参 400（Phase 8 实测缺陷）。"
    )
    assert re.search(r"await\s+canvasListApi\(\)\.listProjects\(\)", region), (
        "loadAll() 未先行 await listProjects()：必须先确定 project_id 才能请求画布列表。"
    )
    assert re.search(r"await\s+canvasListApi\(\)\.listCanvases\(\s*currentProjectId\s*\)", region), (
        "loadAll() 未以已确定的 currentProjectId 调用 listCanvases(...)：契约要求携带 project_id。"
    )
    # 顺序：listProjects 必须在 listCanvases 之前
    assert region.find("listProjects()") < region.find("listCanvases("), (
        "loadAll() 中 listCanvases() 出现在 listProjects() 之前，无法获得 project_id。"
    )


def assert_canvas_ingest_normalizes_contract_fields(source: str) -> None:
    """画布摄取必须把契约字段 canvas_id / project_id / mode 归一化为内部字段。"""
    anchor = source.find("function normalizeCanvas(")
    assert anchor != -1, (
        "未找到 normalizeCanvas()：契约 list_canvases.response_200 与黄金夹具使用 canvas_id，"
        "而渲染路径读取 id，缺少摄取归一化会使 data-canvas-id 变成 'undefined'。"
    )
    region = source[anchor : anchor + 900]
    assert re.search(r"value\.id\s*\|\|\s*value\.canvas_id", region), (
        "normalizeCanvas() 未按 'value.id || value.canvas_id' 归一化稳定实体 ID。"
    )
    assert re.search(r"value\.project\s*\|\|\s*value\.project_id", region), (
        "normalizeCanvas() 未按 'value.project || value.project_id' 归一化所属项目。"
    )
    assert re.search(r"value\.kind\s*\|\|\s*value\.mode", region), (
        "normalizeCanvas() 未按 'value.kind || value.mode' 归一化画布模式。"
    )
    assert re.search(r"\.map\(\s*normalizeCanvas\s*\)", source), (
        "画布列表摄取未调用 normalizeCanvas()：契约字段不会进入渲染路径。"
    )


def test_contract_declares_project_id_as_required_query():
    """前置事实：契约把 list_canvases 的 project_id 声明为请求查询参数。"""
    catalog = _read(CONTRACT_YAML)
    assert "list_canvases" in catalog, "契约应声明 list_canvases 接口"
    block = catalog[catalog.find("list_canvases") :]
    assert "request_query" in block and "project_id: string" in block, (
        "契约 list_canvases 应以 request_query.project_id: string 声明必填查询参数。"
    )


def test_contract_declares_canvas_id_as_stable_id():
    """前置事实：契约 response_200 与黄金夹具都以 canvas_id 作为画布稳定实体 ID。"""
    catalog = _read(CONTRACT_YAML)
    block = catalog[catalog.find("response_200") :]
    assert "canvas_id: string" in block, (
        "契约 response_200 的画布稳定 ID 字段应为 canvas_id（本缺陷需要摄取归一化的原因）。"
    )


def test_canvas_list_api_sends_project_id():
    """`canvas-list/api.js` 必须携带契约必填的 project_id。"""
    assert_list_canvases_sends_project_id(_read(CANVAS_LIST_API_JS))


def test_canvas_list_load_all_is_sequential():
    """`canvas-list.js` 的 loadAll() 必须先项目后画布。"""
    assert_load_all_is_sequential_with_project_id(_read(CANVAS_LIST_JS))


def test_canvas_list_normalizes_contract_fields_at_ingest():
    """`canvas-list.js` 必须在摄取边界归一化 canvas_id / project_id / mode。"""
    assert_canvas_ingest_normalizes_contract_fields(_read(CANVAS_LIST_JS))
