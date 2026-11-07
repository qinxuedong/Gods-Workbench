# -*- coding: utf-8 -*-
"""QIN-567（GitHub #642）画布素材索引接线契约测试。

覆盖 ``GET /api/canvas-assets`` 的 ``items`` 接入素材注册表与画布产物：

- 智能 / 普通画布：拓扑节点 ``refs.asset_id`` join 统一素材注册表；
- 参考画布：``reference_payload.items`` 的真实保存条目投影；
- 未登记的节点引用不编造素材：跳过并如实标记
  ``canvas_asset_node_refs_unresolved_in_registry``；
- 登记了 ``storage_root_id`` / ``stored_name`` 的本机素材按
  ``/api/asset-registry/assets/{asset_id}/media`` 口径回读（与前端
  ``normalizeRegistryAsset`` 同口径），JSON 导入无登记载体时 ``url`` 留空；
- 可见性：跨项目不可见画布的画布、条目与分类计数一并隐藏；
- 认证边界：未认证 401、只读角色可读 200。

硬性口径：三态隔离（每例独立 ``tmp_path`` 数据目录 + 独立内存画布服务），
素材登记走真实导入端点，零伪造。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_canvas_closure as routes
from gw.api import routes_god_canvas as routes_god_canvas
from gw.api.app import create_app
from gw.asset_registry import repository as registry_repo
from gw.canvas_closure.models import (
    GAP_ASSET_INDEX_SOURCE,
    GAP_CANVAS_ASSET_NODE_REFS_UNRESOLVED,
)
from gw.canvas_closure.service import CanvasClosureService, SharedFolderService
from gw.core.auth import require_authenticated
from gw.god_canvas.models import (
    CanvasCreateRequest,
    CanvasNode,
    CanvasTopologyUpdateRequest,
    NodePosition,
    NodeReferences,
)
from gw.god_canvas.service import GodCanvasService
from gw.projects_hub import service as projects
from gw.projects_hub.models import ProjectCreateRequest

EDITOR = {"X-User-Role": "editor", "Authorization": "Bearer cleanroom-test"}
READONLY = {"X-User-Role": "readonly", "Authorization": "Bearer cleanroom-test"}
LEGACY_GAP_MARKER = "canvas_asset_index_source_not_integrated"


@pytest.fixture()
def canvas_service() -> GodCanvasService:
    """全新内存画布服务（空种子），避免跨用例串扰。"""
    return GodCanvasService(seed_golden_fixture=False)


@pytest.fixture()
def api_client(canvas_service: GodCanvasService, monkeypatch) -> TestClient:
    """注入独立画布与共享文件夹服务；不触碰全局单例。"""
    shared = SharedFolderService()
    monkeypatch.setattr(routes, "shared_folders", shared)
    monkeypatch.setattr(
        routes,
        "closure",
        CanvasClosureService(
            canvas_service=canvas_service, shared_folder_service=shared
        ),
    )
    # 关键：画布闭环索引与 /api/canvases 必须共用同一份画布存储。
    monkeypatch.setattr(routes_god_canvas, "default_god_canvas_service", canvas_service)
    with TestClient(create_app()) as client:
        yield client


def _import_asset(client: TestClient, name: str, kind: str) -> str:
    """经真实导入端点登记一条 JSON 素材（无落盘载体），返回稳定 asset_id。"""
    res = client.post(
        "/api/asset-registry/assets/import",
        headers=EDITOR,
        json={"items": [{"name": name, "kind": kind}]},
    )
    assert res.status_code == 200, res.text
    asset_ids = res.json()["asset_ids"]
    assert len(asset_ids) == 1
    return str(asset_ids[0])


def _create_canvas_with_node(
    canvas_service: GodCanvasService,
    *,
    title: str,
    canvas_kind: str,
    node_id: str = "node_0",
    asset_id: str | None = None,
    project_id: str = "prj-0001",
    node_kind: str = "image_asset",
) -> str:
    """经真实画布服务创建画布并写入一个节点（可选素材引用），返回 canvas_id。"""
    created = canvas_service.create_canvas(
        CanvasCreateRequest(project_id=project_id, title=title),
        canvas_kind=canvas_kind,
    )
    refs = NodeReferences(asset_id=asset_id) if asset_id else None
    canvas_service.update_topology(
        created.canvas_id,
        CanvasTopologyUpdateRequest(
            expected_version=1,
            nodes=[
                CanvasNode(
                    entity_id=node_id,
                    kind=node_kind,
                    position=NodePosition(x=0, y=0),
                    refs=refs,
                )
            ],
        ),
    )
    return created.canvas_id


# ---------------------------------------------------------------------------
# 智能 / 普通画布：拓扑节点引用 join 注册表
# ---------------------------------------------------------------------------


def test_smart_canvas_node_assets_join_registry(
    api_client: TestClient, canvas_service: GodCanvasService
):
    """智能画布节点引用已登记素材：items 恰一条真实投影，url 留空不编造。"""
    asset_id = _import_asset(api_client, "角色参考.png", "image")
    canvas_id = _create_canvas_with_node(
        canvas_service,
        title="智能画布A",
        canvas_kind="smart",
        node_id="node_0",
        asset_id=asset_id,
    )
    res = api_client.get("/api/canvas-assets", headers=EDITOR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["data_status"] == "ok"
    assert body["data_gaps"] == []
    assert len(body["items"]) == 1
    # 条目逐字段锁定契约：稳定 ID 拼接、画布元信息、注册表回填与来源标记。
    assert body["items"][0] == {
        "id": f"{canvas_id}::node_0",
        "asset_id": asset_id,
        "canvas_id": canvas_id,
        "canvas_title": "智能画布A",
        "canvas_kind": "smart",
        "name": "角色参考.png",
        "url": "",
        "kind": "image",
        "source": "canvas_node",
    }


def test_classic_canvas_node_assets_join_registry(
    api_client: TestClient, canvas_service: GodCanvasService
):
    """普通画布视频素材引用：类型映射与画布分类同口径，状态 ok。"""
    asset_id = _import_asset(api_client, "场景.mp4", "video")
    canvas_id = _create_canvas_with_node(
        canvas_service,
        title="普通画布B",
        canvas_kind="classic",
        node_id="node_0",
        asset_id=asset_id,
        node_kind="video_asset",
    )
    res = api_client.get("/api/canvas-assets", headers=EDITOR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["data_status"] == "ok"
    assert body["data_gaps"] == []
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["id"] == f"{canvas_id}::node_0"
    assert item["asset_id"] == asset_id
    assert item["canvas_id"] == canvas_id
    assert item["canvas_kind"] == "classic"
    assert item["canvas_title"] == "普通画布B"
    assert item["name"] == "场景.mp4"
    assert item["kind"] == "video"
    assert item["url"] == ""
    assert item["source"] == "canvas_node"


# ---------------------------------------------------------------------------
# 参考画布：reference_payload.items 进入索引
# ---------------------------------------------------------------------------


def test_reference_canvas_payload_items_enter_index(
    api_client: TestClient, canvas_service: GodCanvasService
):
    """参考画布保存条目进入索引：已登记引用回填缺失展示字段，未登记条目保留保存值。"""
    asset_id = _import_asset(api_client, "氛围图.png", "image")
    created = canvas_service.create_canvas(
        CanvasCreateRequest(project_id="prj-0001", title="参考画布C"),
        canvas_kind="reference",
    )
    canvas_id = created.canvas_id
    canvas_service.update_topology(
        canvas_id,
        CanvasTopologyUpdateRequest(
            expected_version=1,
            references={
                "reference_canvas": {
                    "title": "参考画布C",
                    "items": [
                        # 已登记素材：保存时 name/url 为空，由注册表回填名称。
                        {"id": "ref_1", "asset_id": asset_id, "name": "", "url": ""},
                        # 未登记板条：自带 name/url，按保存值原样投影。
                        {
                            "id": "ref_2",
                            "name": "未登记板条",
                            "url": "https://example.com/a.png",
                            "media_kind": "image",
                        },
                    ],
                }
            },
        ),
    )
    res = api_client.get("/api/canvas-assets", headers=EDITOR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["data_status"] == "ok"
    # 未登记 asset_id 的参考条目自带可展示字段，不算解析缺口（缺口只统计节点引用）。
    assert body["data_gaps"] == []
    items = body["items"]
    assert {item["id"] for item in items} == {
        f"{canvas_id}::ref::ref_1",
        f"{canvas_id}::ref::ref_2",
    }
    by_id = {item["id"]: item for item in items}
    ref_1 = by_id[f"{canvas_id}::ref::ref_1"]
    assert ref_1["asset_id"] == asset_id
    assert ref_1["name"] == "氛围图.png"
    assert ref_1["kind"] == "image"
    assert ref_1["canvas_kind"] == "reference"
    assert ref_1["canvas_title"] == "参考画布C"
    assert ref_1["source"] == "reference_item"
    ref_2 = by_id[f"{canvas_id}::ref::ref_2"]
    assert ref_2["asset_id"] == ""
    assert ref_2["name"] == "未登记板条"
    assert ref_2["url"] == "https://example.com/a.png"
    assert ref_2["kind"] == "image"
    assert ref_2["source"] == "reference_item"


# ---------------------------------------------------------------------------
# 未登记节点引用：fail-closed 不编造
# ---------------------------------------------------------------------------


def test_unresolved_node_refs_marked_in_data_gaps(
    api_client: TestClient, canvas_service: GodCanvasService
):
    """未登记的节点引用不编造素材：只保留已登记条目并如实标记解析缺口。"""
    asset_id = _import_asset(api_client, "已登记素材.png", "image")
    created = canvas_service.create_canvas(
        CanvasCreateRequest(project_id="prj-0001", title="混合引用画布"),
        canvas_kind="smart",
    )
    canvas_id = created.canvas_id
    canvas_service.update_topology(
        canvas_id,
        CanvasTopologyUpdateRequest(
            expected_version=1,
            nodes=[
                CanvasNode(
                    entity_id="node_0",
                    kind="image_asset",
                    refs=NodeReferences(asset_id=asset_id),
                ),
                CanvasNode(
                    entity_id="node_1",
                    kind="image_asset",
                    refs=NodeReferences(asset_id="ast_missing"),
                ),
            ],
        ),
    )
    res = api_client.get("/api/canvas-assets", headers=EDITOR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [item["id"] for item in body["items"]] == [f"{canvas_id}::node_0"]
    assert body["items"][0]["asset_id"] == asset_id
    assert body["data_gaps"] == [GAP_CANVAS_ASSET_NODE_REFS_UNRESOLVED]
    # 缺口标记字符串锁死，防止实现口径漂移。
    assert GAP_CANVAS_ASSET_NODE_REFS_UNRESOLVED == (
        "canvas_asset_node_refs_unresolved_in_registry"
    )


def test_stored_upload_media_url_derivation(
    api_client: TestClient, canvas_service: GodCanvasService
):
    """登记落盘载体（stored_name）后按 /media 口径回读，与前端 normalizeRegistryAsset 同口径。"""
    asset_id = _import_asset(api_client, "上传素材.png", "image")
    # 模拟真实上传登记载体：统一注册表 json-state 内补写 stored_name。
    registry_repo.state().mutate(
        lambda raw: raw["assets"][asset_id].update(
            {"stored_name": f"{asset_id}.png"}
        )
    )
    _create_canvas_with_node(
        canvas_service,
        title="回读画布",
        canvas_kind="smart",
        node_id="node_0",
        asset_id=asset_id,
    )
    res = api_client.get("/api/canvas-assets", headers=EDITOR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["data_status"] == "ok"
    assert body["data_gaps"] == []
    assert len(body["items"]) == 1
    item = body["items"][0]
    # 精确字符串相等：/api/asset-registry/assets/{asset_id}/media
    assert item["url"] == f"/api/asset-registry/assets/{asset_id}/media"


# ---------------------------------------------------------------------------
# 可见性与认证边界
# ---------------------------------------------------------------------------


def test_project_invisible_canvas_items_are_hidden(
    api_client: TestClient, canvas_service: GodCanvasService
):
    """他人项目画布对当前编辑者不可见：画布、items 与分类计数一并隐藏。"""
    asset_id = _import_asset(api_client, "跨项目素材.png", "image")
    # owner_key 必须是服务端认证的稳定主体（64 位十六进制），按认证上下文同口径生成。
    other_context = require_authenticated("Bearer other-owner", "editor")
    other_owner = projects.owner_key_for_context(other_context)
    project = projects.default_projects_service.create_project(
        ProjectCreateRequest(name="他人项目", project_type="other"),
        owner_key=other_owner,
    )
    hidden_canvas_id = _create_canvas_with_node(
        canvas_service,
        title="他人画布",
        canvas_kind="smart",
        node_id="node_0",
        asset_id=asset_id,
        project_id=project.project_id,
    )
    # 同项目（prj-0001）可见画布作对照：过滤只隐藏不可见画布，不误伤可见条目。
    visible_canvas_id = _create_canvas_with_node(
        canvas_service,
        title="我的画布",
        canvas_kind="smart",
        node_id="node_0",
        asset_id=asset_id,
    )
    res = api_client.get("/api/canvas-assets", headers=EDITOR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert hidden_canvas_id not in [
        entry["canvas_id"] for entry in body["canvases"]
    ]
    assert visible_canvas_id in [
        entry["canvas_id"] for entry in body["canvases"]
    ]
    assert [item["canvas_id"] for item in body["items"]] == [visible_canvas_id]
    smart_category = next(
        category for category in body["categories"] if category["id"] == "smart"
    )
    assert smart_category["count"] == 1


def test_canvas_assets_auth_boundaries(api_client: TestClient):
    """未认证 401；只读角色可读索引（读端点不拒绝 readonly）。"""
    anonymous = api_client.get("/api/canvas-assets")
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"]["code"] == "UNAUTHORIZED"
    res = api_client.get("/api/canvas-assets", headers=READONLY)
    assert res.status_code == 200, res.text
    assert res.json()["data_status"] == "ok"


def test_no_assets_leaves_legacy_marker_absent(
    api_client: TestClient, canvas_service: GodCanvasService
):
    """有画布但无素材引用：items 空、状态 ok，恒空时代的 not_integrated 缺口标记退场。"""
    canvas_id = _create_canvas_with_node(
        canvas_service,
        title="无引用画布",
        canvas_kind="smart",
        node_id="node_0",
        asset_id=None,
    )
    res = api_client.get("/api/canvas-assets", headers=EDITOR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["items"] == []
    assert body["data_status"] == "ok"
    assert LEGACY_GAP_MARKER not in body["data_gaps"]
    assert body["data_gaps"] == []
    # 画布仍按真实生命周期出现在索引中，分类计数与统计卡口径一致。
    assert canvas_id in [entry["canvas_id"] for entry in body["canvases"]]
    smart_category = next(
        category for category in body["categories"] if category["id"] == "smart"
    )
    assert smart_category["count"] == 1
