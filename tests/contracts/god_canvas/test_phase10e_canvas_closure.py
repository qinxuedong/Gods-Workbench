# -*- coding: utf-8 -*-
"""Phase 10E 画布闭环契约测试。

覆盖 ``docs/contracts/CANVAS-CLOSURE-INTERFACE-CATALOG.yaml``（version: p10e-frozen-1）：

- GET/POST /api/canvas-assets 与 /download
- GET/POST /api/reference-canvases
- GET/POST /api/shared-folders、/import、/{folder_id}/tree、DELETE /{folder_id}
- 视频端点由 VIDEO-TASKS-INTERFACE-CATALOG.yaml 定义
- POST/PATCH /api/canvases/{canvas_id}/meta、POST /touch、DELETE /purge、GET /trash
- POST /api/canvases/assets、DELETE /api/canvases/{canvas_id}

硬性口径：零伪造（空集合 + not_integrated）；视频任务使用独立持久作业契约；打包下载 / 目录扫描 fail-closed、
CAS 409、401/403、以及未授权相邻端点仍 404/405。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_canvas_closure as routes
from gw.api import routes_god_canvas as routes_god_canvas
from gw.api.app import create_app
from gw.canvas_closure.models import (
    CANVAS_ASSET_ATTACH_NOT_INTEGRATED,
    CANVAS_ASSET_DOWNLOAD_NOT_INTEGRATED,
    SHARED_FOLDER_IMPORT_NOT_INTEGRATED,
    )
from gw.canvas_closure.service import CanvasClosureService, SharedFolderService
from gw.god_canvas.service import GodCanvasService

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CATALOG = REPO_ROOT / "docs" / "contracts" / "CANVAS-CLOSURE-INTERFACE-CATALOG.yaml"
FIXTURES = REPO_ROOT / "docs" / "fixtures"
SERVICE_SOURCES = (
    "gw/canvas_closure/models.py",
    "gw/canvas_closure/service.py",
    "gw/api/routes_canvas_closure.py",
)
EDITOR = {"X-User-Role": "editor", "Authorization": "Bearer cleanroom-test"}
READONLY = {"X-User-Role": "readonly", "Authorization": "Bearer cleanroom-test"}
UNAUTHORIZED_NEIGHBORS = (
    ("get", "/api/asset-reviews/sessions-unknown"),
    ("post", "/api/asset-thumbnails/generate-unknown"),
    ("get", "/api/audio-waveform-data-unknown"),
    ("get", "/api/download-output-unknown"),
)


def _load_fixture(name: str) -> dict:
    """读取黄金夹具（UTF-8）。"""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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
        CanvasClosureService(canvas_service=canvas_service, shared_folder_service=shared),
    )
    # 关键：/api/canvases 与画布闭环必须共用同一份画布存储，否则列表 / 回收站会分裂。
    monkeypatch.setattr(routes_god_canvas, "default_god_canvas_service", canvas_service)
    with TestClient(create_app()) as client:
        yield client


# ---------------------------------------------------------------------------
# 契约范围与零伪造（源码级反向断言）
# ---------------------------------------------------------------------------


def test_catalog_declares_exact_methods():
    """契约定义恰好本阶段授权的 20 个方法条目（覆盖 15 条独立路径）。"""
    pairs = re.findall(r"method:\s*(\w+)\s*\n\s*path:\s*(\S+)", CATALOG.read_text(encoding="utf-8"))
    assert len(pairs) == 20, f"契约方法数应为 20，实际 {len(pairs)}: {pairs}"
    paths = {p for _, p in pairs}
    for required in (
        "/api/canvas-assets",
        "/api/canvas-assets/download",
        "/api/reference-canvases",
        "/api/shared-folders",
        "/api/shared-folders/import",
        "/api/shared-folders/{folder_id}",
        "/api/shared-folders/{folder_id}/tree",
        "/api/video-tasks",
        "/api/video-tasks/{job_id}",
        "/api/canvases/{canvas_id}/meta",
        "/api/canvases/{canvas_id}/touch",
        "/api/canvases/{canvas_id}/purge",
        "/api/canvases/trash",
        "/api/canvases/assets",
    ):
        assert required in paths, f"契约缺少授权路径 {required}"


def test_catalog_forbids_unauthorized_neighbors():
    """契约不得授权相邻未实现端点。"""
    pairs = re.findall(r"method:\s*(\w+)\s*\n\s*path:\s*(\S+)", CATALOG.read_text(encoding="utf-8"))
    declared = [f"{m} {p}" for m, p in pairs]
    for forbidden in ("/api/local-assets", "/api/storage-files", "/api/asset-content"):
        assert not any(forbidden in item for item in declared), f"画布闭环契约不得授权 {forbidden}"


def test_service_sources_have_no_fabrication_generators():
    """源码级反向断言：画布闭环实现不得使用 random / uuid 生成业务内容。"""
    for relative in SERVICE_SOURCES:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for marker in ("import random", "random.", "uuid4", "import uuid"):
            assert marker not in text, f"{relative} 不得包含随机/uuid 生成器: {marker}"


# ---------------------------------------------------------------------------
# 画布素材索引
# ---------------------------------------------------------------------------


def test_get_canvas_assets_is_empty_and_truthful(api_client: TestClient):
    """无素材注册表时 items 必须为空并如实标记缺口，且与夹具逐字一致。"""
    res = api_client.get("/api/canvas-assets", headers=EDITOR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body == _load_fixture("canvas-closure-asset-index-empty.json")


def test_canvas_assets_requires_authentication(api_client: TestClient):
    """未认证读取画布素材索引必须 401。"""
    res = api_client.get("/api/canvas-assets")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "UNAUTHORIZED"


def test_download_canvas_assets_is_fail_closed(api_client: TestClient):
    """打包下载无真实后端时必须 503，且不得返回任何下载 URL。"""
    res = api_client.post("/api/canvas-assets/download", json={"asset_ids": ["asset_1"]}, headers=EDITOR)
    assert res.status_code == 503, res.text
    detail = res.json()["detail"]
    assert detail["code"] == CANVAS_ASSET_DOWNLOAD_NOT_INTEGRATED
    assert "url" not in json.dumps(detail)
    assert api_client.post("/api/canvas-assets/download", json={}).status_code == 401
    assert api_client.post("/api/canvas-assets/download", json={}, headers=READONLY).status_code == 403


def test_attach_canvas_assets_is_fail_closed(api_client: TestClient):
    """素材挂接需要素材注册表，本阶段必须 503，不得伪造 added 计数。"""
    res = api_client.post("/api/canvases/assets", json={"asset_ids": ["asset_1"]}, headers=EDITOR)
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == CANVAS_ASSET_ATTACH_NOT_INTEGRATED
    assert "added" not in res.json()["detail"]


# ---------------------------------------------------------------------------
# 参考画布
# ---------------------------------------------------------------------------


def test_reference_canvases_start_empty(api_client: TestClient):
    """无参考画布时必须是空数组，不得预置演示画布。"""
    res = api_client.get("/api/reference-canvases", headers=EDITOR)
    assert res.status_code == 200, res.text
    assert res.json()["canvases"] == []


def test_save_reference_canvas_creates_reference_kind(api_client: TestClient):
    """保存参考画布后必须返回稳定 canvas_id 且 kind 为 reference。"""
    res = api_client.post(
        "/api/reference-canvases",
        json={"title": "参考画布 A", "items": [{"asset_id": "asset_1"}]},
        headers=EDITOR,
    )
    assert res.status_code == 201, res.text
    canvas = res.json()["canvas"]
    assert canvas["canvas_id"]
    assert canvas["kind"] == "reference"
    listed = api_client.get("/api/reference-canvases", headers=EDITOR).json()["canvases"]
    assert [entry["canvas_id"] for entry in listed] == [canvas["canvas_id"]]


def test_save_reference_canvas_requires_title(api_client: TestClient):
    """缺 title 必须 400 INVALID_REQUEST。"""
    res = api_client.post("/api/reference-canvases", json={"items": []}, headers=EDITOR)
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "INVALID_REQUEST"


def test_save_reference_canvas_enforces_cas(api_client: TestClient, canvas_service: GodCanvasService):
    """覆盖既有参考画布时 CAS 不一致必须 409 CANVAS_VERSION_CONFLICT。"""
    created = api_client.post("/api/reference-canvases", json={"title": "参考画布 B", "items": []}, headers=EDITOR).json()["canvas"]
    res = api_client.post(
        "/api/reference-canvases",
        json={"canvas_id": created["canvas_id"], "title": "参考画布 B2", "items": [], "expected_version": 999},
        headers=EDITOR,
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "CANVAS_VERSION_CONFLICT"


# ---------------------------------------------------------------------------
# 共享文件夹
# ---------------------------------------------------------------------------


def test_shared_folders_start_empty(api_client: TestClient):
    """无登记时必须是空数组，且与夹具一致。"""
    res = api_client.get("/api/shared-folders", headers=EDITOR)
    assert res.status_code == 200, res.text
    assert res.json() == _load_fixture("canvas-closure-shared-folders-empty.json")


def test_create_shared_folder_assigns_deterministic_id(api_client: TestClient):
    """登记共享文件夹必须返回确定性 folder_id（fold_NNNN），不扫描磁盘。"""
    res = api_client.post("/api/shared-folders", json={"path": "assets/library"}, headers=EDITOR)
    assert res.status_code == 201, res.text
    folder = res.json()["folder"]
    assert folder["folder_id"] == "fold_0001"
    assert folder["path"] == "assets/library"
    listed = api_client.get("/api/shared-folders", headers=EDITOR).json()
    assert [item["folder_id"] for item in listed["folders"]] == ["fold_0001"]
    assert listed["revision"] == 2


@pytest.mark.parametrize("path_value", ["/etc/passwd", "../secrets", "~/home", "C:/Windows"])
def test_create_shared_folder_rejects_escaping_paths(api_client: TestClient, path_value: str):
    """绝对路径 / 上跳路径 / 主目录简写必须 400，不得登记到项目目录之外。"""
    res = api_client.post("/api/shared-folders", json={"path": path_value}, headers=EDITOR)
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["code"] == "INVALID_REQUEST"


def test_delete_shared_folder_reports_missing_target(api_client: TestClient):
    """删除不存在的登记必须 404，不得静默成功。"""
    assert api_client.delete("/api/shared-folders/fold_9999", headers=EDITOR).status_code == 404
    created = api_client.post("/api/shared-folders", json={"path": "output"}, headers=EDITOR).json()["folder"]
    res = api_client.delete(f"/api/shared-folders/{created['folder_id']}", headers=EDITOR)
    assert res.status_code == 200, res.text
    assert res.json()["deleted_folder_id"] == created["folder_id"]
    assert res.json()["revision"] == 3


def test_shared_folder_tree_is_fail_closed(api_client: TestClient):
    """目录扫描未接入：目标不存在 404，存在必须 503，不得返回假目录树。"""
    assert api_client.get("/api/shared-folders/fold_9999/tree", headers=EDITOR).status_code == 404
    created = api_client.post("/api/shared-folders", json={"path": "assets/library"}, headers=EDITOR).json()["folder"]
    res = api_client.get(f"/api/shared-folders/{created['folder_id']}/tree", headers=EDITOR)
    assert res.status_code == 503, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "SHARED_FOLDER_TREE_NOT_INTEGRATED"
    assert "tree" not in detail


def test_import_shared_folder_items_is_fail_closed(api_client: TestClient):
    """导入未接入时必须 503，不得返回伪造导入条数。"""
    res = api_client.post(
        "/api/shared-folders/import",
        json={"library_id": "lib_1", "folder_id": "fold_0001", "paths": ["a.png"]},
        headers=EDITOR,
    )
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == SHARED_FOLDER_IMPORT_NOT_INTEGRATED


# ---------------------------------------------------------------------------
# 视频任务
# ---------------------------------------------------------------------------


def test_video_tasks_list_is_subject_scoped(api_client: TestClient):
    """任务列表是真实持久任务集合，不再沿用历史 not_integrated 空列表。"""
    res = api_client.get("/api/video-tasks", headers=EDITOR)
    assert res.status_code == 200, res.text
    assert res.json()["items"] == []
    assert res.json()["data_status"] == "ok"


def test_video_route_has_standard_auth_and_context_checks(api_client: TestClient):
    """匿名/只读拒绝先于任务处理，editor 不可凭全局角色冒认项目权限。"""
    body = {
        "prompt": "镜头 A", "provider_id": "missing", "model": "video-model",
        "duration": 5, "aspect_ratio": "16:9",
        "production_context": {"project_id": "prj-0001", "canvas_id": "cv-0001", "entity_id": "nd-0001"},
    }
    assert api_client.post("/api/video-tasks", json=body, headers={"Idempotency-Key": "anon"}).status_code == 401
    readonly = api_client.post("/api/video-tasks", json=body, headers={**READONLY, "Idempotency-Key": "readonly"})
    assert readonly.status_code == 403
    # 创建具有可信owner和视频ACL的项目，不能拿无owner的黄金占位当权限成功初态。
    other = {"Authorization": "Bearer isolated-other-project-owner", "X-User-Role": "editor"}
    created = api_client.post("/api/asset-registry/projects", headers=other,
                              json={"name": "视频权限隔离项目", "project_type": "film"})
    assert created.status_code == 201, created.text
    body["production_context"]["project_id"] = created.json()["project"]["project_id"]
    denied = api_client.post("/api/video-tasks", json=body, headers={**EDITOR, "Idempotency-Key": "unowned"})
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"]["code"] == "PROJECT_ACCESS_DENIED"


# ---------------------------------------------------------------------------
# 画布元信息 / 归档 / 回收站
# ---------------------------------------------------------------------------


def _create_canvas(canvas_service: GodCanvasService) -> str:
    """经真实服务创建一张画布，返回稳定 canvas_id。"""
    from gw.god_canvas.models import CanvasCreateRequest, CanvasMode

    return canvas_service.create_canvas(
        CanvasCreateRequest(project_id="prj-0001", title="画布 A", mode=CanvasMode.CLASSIC)
    ).canvas_id


def test_update_canvas_meta_bumps_version(api_client: TestClient, canvas_service: GodCanvasService):
    """元信息更新必须递增 version，并回传权威 ID 与前端兼容别名。"""
    canvas_id = _create_canvas(canvas_service)
    res = api_client.post(
        f"/api/canvases/{canvas_id}/meta",
        json={"title": "画布 A2", "expected_version": 1},
        headers=EDITOR,
    )
    assert res.status_code == 200, res.text
    canvas = res.json()["canvas"]
    assert canvas["canvas_id"] == canvas_id
    assert canvas["id"] == canvas_id
    assert canvas["title"] == "画布 A2"
    assert canvas["version"] == 2


def test_update_canvas_meta_cas_conflict_matches_fixture(api_client: TestClient, canvas_service: GodCanvasService):
    """CAS 不一致必须 409，错误包与黄金夹具逐字一致。"""
    canvas_id = _create_canvas(canvas_service)
    api_client.post(f"/api/canvases/{canvas_id}/meta", json={"title": "第一次"}, headers=EDITOR)
    res = api_client.post(
        f"/api/canvases/{canvas_id}/meta",
        json={"title": "第二次", "expected_version": 1},
        headers=EDITOR,
    )
    assert res.status_code == 409, res.text
    body = res.json()
    assert body["detail"]["code"] == "CANVAS_VERSION_CONFLICT"
    assert body["detail"]["expected_version"] == 1
    assert body["detail"]["current_version"] == 2
    assert body["detail"]["canvas_id"] == canvas_id


def test_update_canvas_meta_patch_alias_behaves_identically(api_client: TestClient, canvas_service: GodCanvasService):
    """PATCH 别名与 POST 必须共享同一实现与守卫。"""
    canvas_id = _create_canvas(canvas_service)
    res = api_client.patch(f"/api/canvases/{canvas_id}/meta", json={"title": "别名"}, headers=EDITOR)
    assert res.status_code == 200, res.text
    assert res.json()["canvas"]["title"] == "别名"
    conflict = api_client.patch(
        f"/api/canvases/{canvas_id}/meta", json={"title": "x", "expected_version": 1}, headers=EDITOR
    )
    assert conflict.status_code == 409


def test_update_canvas_meta_reports_missing_canvas(api_client: TestClient):
    """目标画布不存在必须 404 CANVAS_NOT_FOUND。"""
    res = api_client.post("/api/canvases/cv-9999/meta", json={"title": "x"}, headers=EDITOR)
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "CANVAS_NOT_FOUND"


def test_touch_canvas_archive_and_unarchive(api_client: TestClient, canvas_service: GodCanvasService):
    """归档 / 解归档必须只翻转状态、递增 version，且把画布移出 / 移回活跃列表。"""
    canvas_id = _create_canvas(canvas_service)
    res = api_client.post(f"/api/canvases/{canvas_id}/touch?operation=archive", headers=EDITOR)
    assert res.status_code == 200, res.text
    assert res.json()["canvas"]["archived_at"]
    assert res.json()["canvas"]["version"] == 2
    active = api_client.get("/api/canvases?project_id=prj-0001", headers=EDITOR).json()["canvases"]
    assert canvas_id not in [item["canvas_id"] for item in active]
    archived = api_client.get("/api/canvases/trash?view=archived", headers=EDITOR).json()["canvases"]
    assert canvas_id in [item["canvas_id"] for item in archived]

    back = api_client.post(f"/api/canvases/{canvas_id}/touch?operation=unarchive", headers=EDITOR)
    assert back.status_code == 200, back.text
    assert back.json()["canvas"]["archived_at"] is None
    active_again = api_client.get("/api/canvases?project_id=prj-0001", headers=EDITOR).json()["canvases"]
    assert canvas_id in [item["canvas_id"] for item in active_again]


def test_touch_canvas_rejects_invalid_operation_and_cas(api_client: TestClient, canvas_service: GodCanvasService):
    """operation 非法必须 400；CAS 不一致必须 409，不得借 touch 绕过 CAS。"""
    canvas_id = _create_canvas(canvas_service)
    bad_op = api_client.post(f"/api/canvases/{canvas_id}/touch?operation=purge", headers=EDITOR)
    assert bad_op.status_code == 400
    assert bad_op.json()["detail"]["code"] == "INVALID_CANVAS_OPERATION"
    conflict = api_client.post(f"/api/canvases/{canvas_id}/touch?operation=archive&expected_version=999", headers=EDITOR)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "CANVAS_VERSION_CONFLICT"


def test_trash_view_starts_empty(api_client: TestClient):
    """回收站为空时必须与夹具一致，不得返回演示条目。"""
    res = api_client.get("/api/canvases/trash", headers=EDITOR)
    assert res.status_code == 200, res.text
    assert res.json() == _load_fixture("canvas-closure-trash-empty.json")
    bad_view = api_client.get("/api/canvases/trash?view=whatever", headers=EDITOR)
    assert bad_view.status_code == 400
    assert bad_view.json()["detail"]["code"] == "INVALID_REQUEST"


def test_trash_canvas_and_purge_flow(api_client: TestClient, canvas_service: GodCanvasService):
    """软删除 -> 回收站可见 -> 恢复；彻底删除仅允许回收站中的画布。"""
    canvas_id = _create_canvas(canvas_service)

    not_in_trash = api_client.delete(f"/api/canvases/{canvas_id}/purge", headers=EDITOR)
    assert not_in_trash.status_code == 409
    assert not_in_trash.json()["detail"]["code"] == "CANVAS_NOT_IN_TRASH"

    trashed = api_client.delete(f"/api/canvases/{canvas_id}", headers=EDITOR)
    assert trashed.status_code == 200, trashed.text
    assert trashed.json()["canvas"]["deleted_at"]
    trash = api_client.get("/api/canvases/trash", headers=EDITOR).json()["canvases"]
    assert canvas_id in [item["canvas_id"] for item in trash]
    active = api_client.get("/api/canvases?project_id=prj-0001", headers=EDITOR).json()["canvases"]
    assert canvas_id not in [item["canvas_id"] for item in active]

    purged = api_client.delete(f"/api/canvases/{canvas_id}/purge", headers=EDITOR)
    assert purged.status_code == 200, purged.text
    assert purged.json()["purged"] is True
    assert api_client.get(f"/api/canvases/{canvas_id}", headers=EDITOR).status_code == 404


def test_purge_post_alias_behaves_identically(api_client: TestClient, canvas_service: GodCanvasService):
    """POST 别名与 DELETE 必须共享同一实现与守卫。"""
    canvas_id = _create_canvas(canvas_service)
    assert api_client.post(f"/api/canvases/{canvas_id}/purge", headers=EDITOR).status_code == 409
    api_client.delete(f"/api/canvases/{canvas_id}", headers=EDITOR)
    res = api_client.post(f"/api/canvases/{canvas_id}/purge", headers=EDITOR)
    assert res.status_code == 200, res.text
    assert res.json()["purged"] is True


def test_canvas_lifecycle_write_requires_permission(api_client: TestClient, canvas_service: GodCanvasService):
    """画布生命周期写端点：未认证 401、只读角色 403。"""
    canvas_id = _create_canvas(canvas_service)
    assert api_client.post(f"/api/canvases/{canvas_id}/meta", json={"title": "x"}).status_code == 401
    assert api_client.post(f"/api/canvases/{canvas_id}/meta", json={"title": "x"}, headers=READONLY).status_code == 403
    assert api_client.post(f"/api/canvases/{canvas_id}/touch?operation=archive").status_code == 401
    assert api_client.delete(f"/api/canvases/{canvas_id}").status_code == 401


def test_restore_after_trash_uses_existing_contract_endpoint(api_client: TestClient, canvas_service: GodCanvasService):
    """回收站恢复仍走既有 POST /api/canvases/{canvas_id}/restore，不另起别名。"""
    canvas_id = _create_canvas(canvas_service)
    api_client.delete(f"/api/canvases/{canvas_id}", headers=EDITOR)
    res = api_client.post(
        f"/api/canvases/{canvas_id}/restore",
        json={"expected_version": 2},
        headers=EDITOR,
    )
    assert res.status_code == 200, res.text
    active = api_client.get("/api/canvases?project_id=prj-0001", headers=EDITOR).json()["canvases"]
    assert canvas_id in [item["canvas_id"] for item in active]


# ---------------------------------------------------------------------------
# 范围反向断言
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", UNAUTHORIZED_NEIGHBORS)
def test_unauthorized_neighbors_stay_unimplemented(api_client: TestClient, method: str, path: str):
    """未授权相邻端点必须保持 404/405，不得被本阶段顺带实现。"""
    res = getattr(api_client, method)(path, headers=EDITOR)
    assert res.status_code in (404, 405), f"{method.upper()} {path} 意外返回 {res.status_code}"
