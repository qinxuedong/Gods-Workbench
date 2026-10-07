# -*- coding: utf-8 -*-
"""Phase 10A 素材库契约测试。

覆盖 docs/contracts/ASSET-LIBRARY-INTERFACE-CATALOG.yaml（p10a-frozen-1）的 3 个端点：

- GET  /api/asset-library            空库语义 / 稳定 ID 结构 / 401
- POST /api/asset-library/libraries  201 / CAS 409 / 重名 409 / 403 / 400
- POST /api/asset-library/categories 201 / 404 / 重名 409 / 父库 CAS 409 / 403

隔离策略：每个用例通过 reset 夹具重建**独立**服务实例，避免跨用例状态泄漏。
"""

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.api import routes_asset_library as routes
from gw.asset_library.service import AssetLibraryService

EDITOR = {"X-User-Role": "editor", "Authorization": "Bearer cleanroom-test"}
READONLY = {"X-User-Role": "readonly", "Authorization": "Bearer cleanroom-test"}


@pytest.fixture()
def api_client(monkeypatch) -> TestClient:
    """为每个用例注入全新素材库服务实例，保证用例间互不影响。"""
    service = AssetLibraryService(seed_golden_fixture=False)
    monkeypatch.setattr(routes, "default_asset_library_service", service)
    app = create_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def seeded_client(monkeypatch) -> TestClient:
    """注入带黄金夹具基准库的服务实例（library_default / category_image）。"""
    service = AssetLibraryService(seed_golden_fixture=True)
    monkeypatch.setattr(routes, "default_asset_library_service", service)
    app = create_app()
    with TestClient(app) as client:
        yield client


def test_get_empty_library_returns_no_fabricated_assets(api_client: TestClient):
    """空库必须返回 libraries: [] 与 active_library_id: null，不得伪造素材。"""
    res = api_client.get("/api/asset-library", headers=EDITOR)
    assert res.status_code == 200
    library = res.json()["library"]
    assert library["libraries"] == []
    assert library["active_library_id"] is None
    assert library["version"] == 1


def test_get_library_requires_authentication(api_client: TestClient):
    """未认证访问必须 401，且返回标准错误包。"""
    res = api_client.get("/api/asset-library")
    assert res.status_code == 401
    assert res.json()["detail"]["code"].lower() == "unauthorized"


def test_create_library_returns_stable_id_and_increments_version(api_client: TestClient):
    """创建素材库返回 201 + 稳定 library_id，顶层目录版本递增。"""
    res = api_client.post("/api/asset-library/libraries", json={"name": "角色参考库"}, headers=EDITOR)
    assert res.status_code == 201
    body = res.json()
    assert body["library"]["library_id"] == "library_0001"
    assert body["library"]["version"] == 1
    assert body["library"]["categories"] == []
    assert body["asset_library"]["version"] == 2
    assert body["asset_library"]["active_library_id"] == "library_0001"

    # 读回确认已持久到快照（同实例内存）
    readback = api_client.get("/api/asset-library", headers=EDITOR).json()["library"]
    assert [lib["library_id"] for lib in readback["libraries"]] == ["library_0001"]


def test_create_library_duplicate_name_conflicts(api_client: TestClient):
    """重名（含大小写/空白差异）必须 409 DUPLICATE_LIBRARY_NAME。"""
    assert api_client.post("/api/asset-library/libraries", json={"name": "角色库"}, headers=EDITOR).status_code == 201
    res = api_client.post("/api/asset-library/libraries", json={"name": "  角色库  "}, headers=EDITOR)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "DUPLICATE_LIBRARY_NAME"


def test_create_library_cas_conflict(api_client: TestClient):
    """顶层目录 expected_version 不一致必须 409 VERSION_CONFLICT。"""
    res = api_client.post(
        "/api/asset-library/libraries",
        json={"name": "并发库", "expected_version": 99},
        headers=EDITOR,
    )
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "VERSION_CONFLICT"
    assert detail["expected_version"] == 99
    assert detail["current_version"] == 1


def test_create_library_blank_name_rejected(api_client: TestClient):
    """全空白名称必须失败关闭（400）。"""
    res = api_client.post("/api/asset-library/libraries", json={"name": "   "}, headers=EDITOR)
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "INVALID_REQUEST"


def test_create_library_readonly_role_forbidden(api_client: TestClient):
    """只读角色创建素材库必须 403。"""
    res = api_client.post("/api/asset-library/libraries", json={"name": "只读库"}, headers=READONLY)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "FORBIDDEN"


def test_create_category_requires_existing_library(api_client: TestClient):
    """父库不存在必须 404 LIBRARY_NOT_FOUND。"""
    res = api_client.post(
        "/api/asset-library/categories",
        json={"library_id": "library_missing", "name": "图片", "type": "image"},
        headers=EDITOR,
    )
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "LIBRARY_NOT_FOUND"


def test_create_category_success_and_parent_version_increment(api_client: TestClient):
    """创建分类返回 201 + 稳定 category_id，父库版本递增。"""
    created = api_client.post("/api/asset-library/libraries", json={"name": "角色库"}, headers=EDITOR).json()
    library_id = created["library"]["library_id"]

    res = api_client.post(
        "/api/asset-library/categories",
        json={"library_id": library_id, "name": "正面肖像", "type": "image", "expected_version": 1},
        headers=EDITOR,
    )
    assert res.status_code == 201
    body = res.json()
    assert body["category"]["category_id"] == "category_0001"
    assert body["category"]["library_id"] == library_id
    assert body["category"]["type"] == "image"
    assert body["category"]["version"] == 1
    assert body["category"]["items"] == []
    assert body["library"]["version"] == 2


def test_create_category_parent_cas_conflict(seeded_client: TestClient):
    """父库 expected_version 不匹配必须 409，且不得写入分类。"""
    res = seeded_client.post(
        "/api/asset-library/categories",
        json={"library_id": "library_default", "name": "新分类", "type": "image", "expected_version": 1},
        headers=EDITOR,
    )
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "VERSION_CONFLICT"
    assert detail["expected_version"] == 1
    assert detail["current_version"] == 2

    # 冲突后读回：分类数不变
    snapshot = seeded_client.get("/api/asset-library", headers=EDITOR).json()["library"]
    categories = snapshot["libraries"][0]["categories"]
    assert [c["category_id"] for c in categories] == ["category_image"]


def test_create_category_duplicate_name_conflicts(seeded_client: TestClient):
    """同库分类重名必须 409 DUPLICATE_CATEGORY_NAME。"""
    res = seeded_client.post(
        "/api/asset-library/categories",
        json={"library_id": "library_default", "name": " 图片 ", "type": "image", "expected_version": 2},
        headers=EDITOR,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "DUPLICATE_CATEGORY_NAME"


def test_create_category_invalid_type_rejected(seeded_client: TestClient):
    """type 不在枚举内必须失败关闭（400 INVALID_REQUEST）。"""
    res = seeded_client.post(
        "/api/asset-library/categories",
        json={"library_id": "library_default", "name": "音频", "type": "audio"},
        headers=EDITOR,
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "INVALID_REQUEST"


def test_seeded_library_uses_stable_ids(seeded_client: TestClient):
    """黄金夹具种子必须使用稳定 ID，且不含 id/pid/cid 等别名。"""
    library = seeded_client.get("/api/asset-library", headers=EDITOR).json()["library"]
    assert library["active_library_id"] == "library_default"
    lib = library["libraries"][0]
    assert lib["library_id"] == "library_default"
    assert "id" not in lib and "pid" not in lib
    category = lib["categories"][0]
    assert category["category_id"] == "category_image"
    assert "cid" not in category


def test_unsupported_asset_library_methods_still_unavailable(seeded_client: TestClient):
    """B4 已接入的路径仍只开放契约声明的方法，其他方法保持不可用。"""
    for method, path in [
        ("get", "/api/asset-library/libraries/library_default"),
        ("get", "/api/asset-library/categories/category_image"),
        ("get", "/api/asset-library/items/batch"),
        ("get", "/api/asset-library/workflows/upload"),
    ]:
        res = getattr(seeded_client, method)(path, headers=EDITOR)
        assert res.status_code in {404, 405}, f"{method.upper()} {path} 不应可用"
