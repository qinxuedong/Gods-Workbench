# -*- coding: utf-8 -*-
"""Phase 10C 提示词库契约测试。

覆盖 docs/contracts/PROMPT-LIBRARY-INTERFACE-CATALOG.yaml（p10c-frozen-1）的 7 个端点：

- GET    /api/prompt-libraries                          空库语义 / 稳定 ID 结构 / 401 / 403
- POST   /api/prompt-libraries                          201 / 目录 CAS 409 / 重名 409 / 403 / 400
- PATCH  /api/prompt-libraries/{library_id}             200 / 404 / 重名 409 / CAS 409 / 403
- DELETE /api/prompt-libraries/{library_id}             200 / LIBRARY_NOT_EMPTY 409 / CAS 409 / 404 / 403
- POST   /api/prompt-libraries/categories               201 / 404 / 重名 409 / 父库 CAS 409 / 403
- PATCH  /api/prompt-libraries/categories/{id}          200 / 404 / 重名 409 / CAS 409 / 403
- DELETE /api/prompt-libraries/categories/{id}          200 / CAS 409 / 404 / 403

另含**零伪造**反向断言（空库不得返回演示提示词内容）与
**范围守卫**（未授权的 /api/prompt-libraries/items* 必须仍不可用）。

隔离策略：每个用例通过 reset 夹具重建**独立**服务实例，避免跨用例状态泄漏。
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_prompt_library as routes
from gw.api.app import create_app
from gw.prompt_library.models import PromptLibrarySnapshot
from gw.prompt_library.service import PromptLibraryService

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CATALOG = REPO_ROOT / "docs" / "contracts" / "PROMPT-LIBRARY-INTERFACE-CATALOG.yaml"
SERVICE_SOURCES = (
    "gw/prompt_library/models.py",
    "gw/prompt_library/service.py",
    "gw/api/routes_prompt_library.py",
)

EDITOR = {"X-User-Role": "editor", "Authorization": "Bearer cleanroom-test"}
READONLY = {"X-User-Role": "readonly", "Authorization": "Bearer cleanroom-test"}


@pytest.fixture()
def api_client(monkeypatch) -> TestClient:
    """为每个用例注入全新提示词库服务实例，保证用例间互不影响。"""
    service = PromptLibraryService(seed_structural_fixture=False)
    monkeypatch.setattr(routes, "default_prompt_library_service", service)
    app = create_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def seeded_client(monkeypatch) -> TestClient:
    """注入带结构性基线的服务实例（plib_default / pcat_default，无任何提示词文本）。"""
    service = PromptLibraryService(seed_structural_fixture=True)
    monkeypatch.setattr(routes, "default_prompt_library_service", service)
    app = create_app()
    with TestClient(app) as client:
        yield client


def _create_library(client: TestClient, name: str = "角色提示词库") -> dict:
    """辅助：创建一个提示词库并返回创建响应体。"""
    res = client.post("/api/prompt-libraries", json={"name": name}, headers=EDITOR)
    assert res.status_code == 201, res.text
    return res.json()


def _create_category(client: TestClient, library_id: str, name: str = "视角") -> dict:
    """辅助：在指定库下创建一个分类并返回创建响应体。"""
    res = client.post(
        "/api/prompt-libraries/categories",
        json={"library_id": library_id, "name": name},
        headers=EDITOR,
    )
    assert res.status_code == 201, res.text
    return res.json()


# ---------------------------------------------------------------------------
# 契约范围与零伪造（源码级反向断言）
# ---------------------------------------------------------------------------


def test_catalog_declares_exactly_seven_endpoints():
    """契约必须且只能声明本阶段授权的 7 个端点。"""
    text = CATALOG.read_text(encoding="utf-8")
    pairs = re.findall(r"method:\s*(\w+)\s*\n\s*path:\s*(\S+)", text)
    assert len(pairs) == 7, f"契约端点条数应为 7，实际 {len(pairs)}: {pairs}"


def test_catalog_forbids_prompt_items_and_other_unauthorized_paths():
    """契约不得授权 /api/prompt-libraries/items* 或任何非本阶段端点。"""
    text = CATALOG.read_text(encoding="utf-8")
    routes = {route for _, route in re.findall(r"method:\s*(\w+)\s*\n\s*path:\s*(\S+)", text)}
    allowed = {
        "/api/prompt-libraries",
        "/api/prompt-libraries/{library_id}",
        "/api/prompt-libraries/categories",
        "/api/prompt-libraries/categories/{category_id}",
    }
    assert routes == allowed, f"契约出现未授权路径: {routes - allowed}"
    # B8 单独契约接管条目层级；10C 基础契约仍不夹带条目路径。
    assert all("/items" not in route for route in routes)


def test_catalog_declares_no_fabrication_rule():
    """契约必须显式写明「不得伪造提示词内容」。"""
    text = CATALOG.read_text(encoding="utf-8")
    assert "不得伪造提示词内容" in text, "契约必须显式写明禁止伪造提示词内容"


# 提示词文本字段的**声明**形态（而非文档里说明「禁止这些字段」的措辞）。
_FORBIDDEN_FIELD_RE = re.compile(r"^\s*(positive|negative|scene|items)\s*[:=]", re.MULTILINE)


def test_service_sources_have_no_fabrication_generators():
    """源码级反向断言：服务/模型/路由不得引入随机数或 uuid 生成伪造内容。"""
    for relative in SERVICE_SOURCES:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for marker in ("import random", "random.", "uuid4", "import uuid"):
            assert marker not in text, f"{relative} 不得引入随机/uuid 生成器: {marker}"
        hit = _FORBIDDEN_FIELD_RE.search(text)
        assert hit is None, f"{relative} 不得声明提示词文本字段/条目集合: {hit.group(0) if hit else ''}"


def test_repository_has_no_fabricated_prompt_library_payloads():
    """反向断言：提示词库夹具中不存在任何伪造提示词文本字段。"""
    paths = sorted((REPO_ROOT / "docs" / "fixtures").glob("prompt-library-*.json"))
    assert paths, "提示词库黄金夹具必须存在"
    for path in paths:
        payload = path.read_text(encoding="utf-8")
        for marker in ('"positive"', '"negative"', '"scene"', '"items"'):
            assert marker not in payload, f"{path.name} 不得包含伪造提示词内容字段: {marker}"


# ---------------------------------------------------------------------------
# GET /api/prompt-libraries
# ---------------------------------------------------------------------------


def test_get_empty_library_returns_no_fabricated_content(api_client: TestClient):
    """空库必须返回 libraries: [] 与 active_library_id: null，不得伪造提示词内容。"""
    res = api_client.get("/api/prompt-libraries", headers=EDITOR)
    assert res.status_code == 200
    library = res.json()["library"]
    assert library["libraries"] == []
    assert library["active_library_id"] is None
    assert library["version"] == 1


def test_get_library_requires_authentication(api_client: TestClient):
    """未认证访问必须 401，错误码与 core/errors.py 一致（纯 ASCII 小写后为 unauthorized）。"""
    res = api_client.get("/api/prompt-libraries")
    assert res.status_code == 401
    detail = res.json()["detail"]
    assert detail["code"].lower() == "unauthorized"
    assert detail["code"].isascii(), "401 错误码必须为纯 ASCII"


def test_readonly_role_can_read(api_client: TestClient):
    """只读角色可读提示词库树。"""
    res = api_client.get("/api/prompt-libraries", headers=READONLY)
    assert res.status_code == 200


def test_seeded_library_uses_stable_ids(seeded_client: TestClient):
    """结构性种子必须使用稳定 ID，且不含 id/pid/cid 等别名。"""
    library = seeded_client.get("/api/prompt-libraries", headers=EDITOR).json()["library"]
    assert library["active_library_id"] == "plib_default"
    lib = library["libraries"][0]
    assert lib["library_id"] == "plib_default"
    assert "id" not in lib and "pid" not in lib and "cid" not in lib
    category = lib["categories"][0]
    assert category["category_id"] == "pcat_default"
    assert "cid" not in category and "id" not in category
    assert "items" not in category, "本阶段分类不得携带条目集合"


# ---------------------------------------------------------------------------
# POST /api/prompt-libraries
# ---------------------------------------------------------------------------


def test_create_library_returns_stable_id_and_increments_version(api_client: TestClient):
    """创建提示词库返回 201 + 稳定 library_id，顶层目录版本递增。"""
    body = _create_library(api_client)
    assert body["prompt_library"]["library_id"] == "plib_0001"
    assert body["prompt_library"]["version"] == 1
    assert body["prompt_library"]["categories"] == []
    assert body["library"]["version"] == 2
    assert body["library"]["active_library_id"] == "plib_0001"

    readback = api_client.get("/api/prompt-libraries", headers=EDITOR).json()["library"]
    assert [lib["library_id"] for lib in readback["libraries"]] == ["plib_0001"]


def test_create_library_duplicate_name_conflicts(api_client: TestClient):
    """重名（含大小写/空白差异）必须 409 DUPLICATE_LIBRARY_NAME。"""
    _create_library(api_client, "角色库")
    res = api_client.post("/api/prompt-libraries", json={"name": "  角色库  "}, headers=EDITOR)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "DUPLICATE_LIBRARY_NAME"


def test_create_library_cas_conflict(api_client: TestClient):
    """顶层目录 expected_version 不一致必须 409 VERSION_CONFLICT。"""
    res = api_client.post(
        "/api/prompt-libraries",
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
    res = api_client.post("/api/prompt-libraries", json={"name": "   "}, headers=EDITOR)
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "INVALID_REQUEST"


def test_create_library_readonly_role_forbidden(api_client: TestClient):
    """只读角色创建提示词库必须 403。"""
    res = api_client.post("/api/prompt-libraries", json={"name": "只读库"}, headers=READONLY)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "FORBIDDEN"


def test_create_library_unauthenticated(api_client: TestClient):
    """未认证创建必须 401。"""
    res = api_client.post("/api/prompt-libraries", json={"name": "匿名库"})
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /api/prompt-libraries/{library_id}
# ---------------------------------------------------------------------------


def test_rename_library_success(api_client: TestClient):
    """重命名返回 200，目标库与顶层目录版本递增。"""
    created = _create_library(api_client)
    library_id = created["prompt_library"]["library_id"]

    res = api_client.patch(
        f"/api/prompt-libraries/{library_id}",
        json={"name": "新名称", "expected_version": 1},
        headers=EDITOR,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["prompt_library"]["library_id"] == library_id
    assert body["prompt_library"]["name"] == "新名称"
    assert body["prompt_library"]["version"] == 2
    assert body["library"]["version"] == 3


def test_rename_library_not_found(api_client: TestClient):
    """目标库不存在必须 404 LIBRARY_NOT_FOUND。"""
    res = api_client.patch("/api/prompt-libraries/plib_missing", json={"name": "x"}, headers=EDITOR)
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "LIBRARY_NOT_FOUND"


def test_rename_library_cas_conflict(api_client: TestClient):
    """目标库 expected_version 不匹配必须 409，且不得改名。"""
    created = _create_library(api_client, "原名")
    library_id = created["prompt_library"]["library_id"]

    res = api_client.patch(
        f"/api/prompt-libraries/{library_id}",
        json={"name": "改名", "expected_version": 99},
        headers=EDITOR,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "VERSION_CONFLICT"

    readback = api_client.get("/api/prompt-libraries", headers=EDITOR).json()["library"]
    assert readback["libraries"][0]["name"] == "原名"


def test_rename_library_duplicate_name_conflicts(api_client: TestClient):
    """重命名撞上另一库名必须 409 DUPLICATE_LIBRARY_NAME。"""
    _create_library(api_client, "库一")
    second = _create_library(api_client, "库二")
    library_id = second["prompt_library"]["library_id"]

    res = api_client.patch(
        f"/api/prompt-libraries/{library_id}",
        json={"name": "库一"},
        headers=EDITOR,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "DUPLICATE_LIBRARY_NAME"


def test_rename_library_readonly_role_forbidden(api_client: TestClient):
    """只读角色重命名必须 403。"""
    created = _create_library(api_client)
    library_id = created["prompt_library"]["library_id"]
    res = api_client.patch(f"/api/prompt-libraries/{library_id}", json={"name": "x"}, headers=READONLY)
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/prompt-libraries/{library_id}
# ---------------------------------------------------------------------------


def test_delete_library_success(api_client: TestClient):
    """空库删除返回 200，目录版本递增且库被移除。"""
    created = _create_library(api_client)
    library_id = created["prompt_library"]["library_id"]

    res = api_client.delete(f"/api/prompt-libraries/{library_id}", headers=EDITOR)
    assert res.status_code == 200
    snapshot = res.json()["library"]
    assert snapshot["libraries"] == []
    assert snapshot["active_library_id"] is None
    assert snapshot["version"] == 3


def test_delete_non_empty_library_conflicts(seeded_client: TestClient):
    """含分类的库删除必须 409 LIBRARY_NOT_EMPTY，且树保持不变（禁止静默级联）。"""
    res = seeded_client.delete("/api/prompt-libraries/plib_default", headers=EDITOR)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "LIBRARY_NOT_EMPTY"

    snapshot = seeded_client.get("/api/prompt-libraries", headers=EDITOR).json()["library"]
    assert [lib["library_id"] for lib in snapshot["libraries"]] == ["plib_default"]
    assert [c["category_id"] for c in snapshot["libraries"][0]["categories"]] == ["pcat_default"]


def test_delete_library_cas_conflict(seeded_client: TestClient):
    """目标库 expected_version 不匹配必须 409，且库仍存在。"""
    res = seeded_client.delete("/api/prompt-libraries/plib_default?expected_version=99", headers=EDITOR)
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "VERSION_CONFLICT"

    snapshot = seeded_client.get("/api/prompt-libraries", headers=EDITOR).json()["library"]
    assert [lib["library_id"] for lib in snapshot["libraries"]] == ["plib_default"]


def test_delete_library_not_found(api_client: TestClient):
    """目标库不存在必须 404 LIBRARY_NOT_FOUND。"""
    res = api_client.delete("/api/prompt-libraries/plib_missing", headers=EDITOR)
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "LIBRARY_NOT_FOUND"


def test_delete_library_readonly_role_forbidden(api_client: TestClient):
    """只读角色删除必须 403。"""
    created = _create_library(api_client)
    library_id = created["prompt_library"]["library_id"]
    res = api_client.delete(f"/api/prompt-libraries/{library_id}", headers=READONLY)
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/prompt-libraries/categories
# ---------------------------------------------------------------------------


def test_create_category_requires_existing_library(api_client: TestClient):
    """父库不存在必须 404 LIBRARY_NOT_FOUND。"""
    res = api_client.post(
        "/api/prompt-libraries/categories",
        json={"library_id": "plib_missing", "name": "视角"},
        headers=EDITOR,
    )
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "LIBRARY_NOT_FOUND"


def test_create_category_success_and_parent_version_increment(api_client: TestClient):
    """创建分类返回 201 + 稳定 category_id，父库与目录版本递增。"""
    created = _create_library(api_client)
    library_id = created["prompt_library"]["library_id"]

    res = api_client.post(
        "/api/prompt-libraries/categories",
        json={"library_id": library_id, "name": "分镜", "expected_version": 1},
        headers=EDITOR,
    )
    assert res.status_code == 201
    body = res.json()
    assert body["category"]["category_id"] == "pcat_0001"
    assert body["category"]["library_id"] == library_id
    assert body["category"]["version"] == 1
    assert "items" not in body["category"], "分类响应不得包含条目集合"
    assert body["library"]["version"] == 3
    assert body["library"]["libraries"][0]["version"] == 2


def test_create_category_parent_cas_conflict(seeded_client: TestClient):
    """父库 expected_version 不匹配必须 409，且不得写入分类。"""
    res = seeded_client.post(
        "/api/prompt-libraries/categories",
        json={"library_id": "plib_default", "name": "新分类", "expected_version": 1},
        headers=EDITOR,
    )
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "VERSION_CONFLICT"
    assert detail["expected_version"] == 1
    assert detail["current_version"] == 2

    snapshot = seeded_client.get("/api/prompt-libraries", headers=EDITOR).json()["library"]
    categories = snapshot["libraries"][0]["categories"]
    assert [c["category_id"] for c in categories] == ["pcat_default"]


def test_create_category_duplicate_name_conflicts(seeded_client: TestClient):
    """同库分类重名必须 409 DUPLICATE_CATEGORY_NAME。"""
    res = seeded_client.post(
        "/api/prompt-libraries/categories",
        json={"library_id": "plib_default", "name": " 通用 ", "expected_version": 2},
        headers=EDITOR,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "DUPLICATE_CATEGORY_NAME"


def test_create_category_readonly_role_forbidden(api_client: TestClient):
    """只读角色创建分类必须 403。"""
    created = _create_library(api_client)
    library_id = created["prompt_library"]["library_id"]
    res = api_client.post(
        "/api/prompt-libraries/categories",
        json={"library_id": library_id, "name": "x"},
        headers=READONLY,
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# PATCH /api/prompt-libraries/categories/{category_id}
# ---------------------------------------------------------------------------


def test_rename_category_success(api_client: TestClient):
    """重命名分类返回 200，目标分类与父库版本递增。"""
    created = _create_library(api_client)
    library_id = created["prompt_library"]["library_id"]
    category = _create_category(api_client, library_id)
    category_id = category["category"]["category_id"]

    res = api_client.patch(
        f"/api/prompt-libraries/categories/{category_id}",
        json={"name": "角色", "expected_version": 1},
        headers=EDITOR,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["category"]["category_id"] == category_id
    assert body["category"]["name"] == "角色"
    assert body["category"]["version"] == 2
    assert body["library"]["libraries"][0]["version"] == 3


def test_rename_category_not_found(api_client: TestClient):
    """目标分类不存在必须 404 CATEGORY_NOT_FOUND。"""
    res = api_client.patch(
        "/api/prompt-libraries/categories/pcat_missing",
        json={"name": "x"},
        headers=EDITOR,
    )
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "CATEGORY_NOT_FOUND"


def test_rename_category_cas_conflict(api_client: TestClient):
    """目标分类 expected_version 不匹配必须 409，且不得改名。"""
    created = _create_library(api_client)
    library_id = created["prompt_library"]["library_id"]
    category = _create_category(api_client, library_id, "原名分类")
    category_id = category["category"]["category_id"]

    res = api_client.patch(
        f"/api/prompt-libraries/categories/{category_id}",
        json={"name": "新名分类", "expected_version": 99},
        headers=EDITOR,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "VERSION_CONFLICT"

    snapshot = api_client.get("/api/prompt-libraries", headers=EDITOR).json()["library"]
    assert snapshot["libraries"][0]["categories"][0]["name"] == "原名分类"


def test_rename_category_duplicate_name_conflicts(api_client: TestClient):
    """同库分类改名撞名必须 409 DUPLICATE_CATEGORY_NAME。"""
    created = _create_library(api_client)
    library_id = created["prompt_library"]["library_id"]
    _create_category(api_client, library_id, "分类一")
    second = _create_category(api_client, library_id, "分类二")
    category_id = second["category"]["category_id"]

    res = api_client.patch(
        f"/api/prompt-libraries/categories/{category_id}",
        json={"name": "分类一"},
        headers=EDITOR,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "DUPLICATE_CATEGORY_NAME"


def test_rename_category_readonly_role_forbidden(api_client: TestClient):
    """只读角色重命名分类必须 403。"""
    created = _create_library(api_client)
    library_id = created["prompt_library"]["library_id"]
    category = _create_category(api_client, library_id)
    category_id = category["category"]["category_id"]
    res = api_client.patch(
        f"/api/prompt-libraries/categories/{category_id}",
        json={"name": "x"},
        headers=READONLY,
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/prompt-libraries/categories/{category_id}
# ---------------------------------------------------------------------------


def test_delete_category_success(api_client: TestClient):
    """删除分类返回 200，父库与目录版本递增。"""
    created = _create_library(api_client)
    library_id = created["prompt_library"]["library_id"]
    category = _create_category(api_client, library_id)
    category_id = category["category"]["category_id"]

    res = api_client.delete(f"/api/prompt-libraries/categories/{category_id}", headers=EDITOR)
    assert res.status_code == 200
    snapshot = res.json()["library"]
    assert snapshot["libraries"][0]["categories"] == []
    assert snapshot["libraries"][0]["version"] == 3
    assert snapshot["version"] == 4


def test_delete_category_cas_conflict(api_client: TestClient):
    """目标分类 expected_version 不匹配必须 409，且分类仍存在。"""
    created = _create_library(api_client)
    library_id = created["prompt_library"]["library_id"]
    category = _create_category(api_client, library_id)
    category_id = category["category"]["category_id"]

    res = api_client.delete(
        f"/api/prompt-libraries/categories/{category_id}?expected_version=99",
        headers=EDITOR,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "VERSION_CONFLICT"

    snapshot = api_client.get("/api/prompt-libraries", headers=EDITOR).json()["library"]
    assert [c["category_id"] for c in snapshot["libraries"][0]["categories"]] == [category_id]


def test_delete_category_not_found(api_client: TestClient):
    """目标分类不存在必须 404 CATEGORY_NOT_FOUND。"""
    res = api_client.delete("/api/prompt-libraries/categories/pcat_missing", headers=EDITOR)
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "CATEGORY_NOT_FOUND"


def test_delete_category_readonly_role_forbidden(seeded_client: TestClient):
    """只读角色删除分类必须 403。"""
    res = seeded_client.delete("/api/prompt-libraries/categories/pcat_default", headers=READONLY)
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# 范围变更：条目 CRUD 已由 Phase 12 A3 真实接入
# ---------------------------------------------------------------------------


def test_items_endpoints_now_implemented_with_real_auth_semantics(seeded_client: TestClient):
    """2026-09-26 Phase 12 A3：items* 已按 p12-a3-1 真实接入。

    旧的「一律 503 / 必须不可用」断言已被取代：
    - 未认证 -> 401；
    - 只读角色写操作 -> 403；
    - 编辑角色写入可被读回（详见 test_phase11_b8_prompt_items.py）。
    """
    probes = [
        ("post", "/api/prompt-libraries/items", {"library_id": "plib_default", "name": "x"}),
        ("patch", "/api/prompt-libraries/items/pitem_0001", {"name": "x"}),
        ("delete", "/api/prompt-libraries/items/pitem_0001", None),
        ("post", "/api/prompt-libraries/items/delete", {"ids": ["pitem_0001"]}),
    ]
    for method, path, body in probes:
        request = getattr(seeded_client, method)
        unauth = request(path, json=body) if body is not None else request(path)
        assert unauth.status_code == 401, "%s %s 未认证应 401，实际 %s" % (method.upper(), path, unauth.status_code)
        forbidden = request(path, json=body, headers=READONLY) if body is not None else request(path, headers=READONLY)
        assert forbidden.status_code == 403, "%s %s 只读应 403，实际 %s" % (method.upper(), path, forbidden.status_code)
        assert forbidden.status_code != 503, "items* 不应再返回 503 未接入"


# ---------------------------------------------------------------------------
# 黄金夹具与模型一致性
# ---------------------------------------------------------------------------


def test_fixture_empty_matches_empty_service_snapshot(api_client: TestClient):
    """空库夹具必须与真实空服务快照结构一致。"""
    fixture = json.loads((REPO_ROOT / "docs" / "fixtures" / "prompt-library-empty.json").read_text(encoding="utf-8"))
    validated = PromptLibrarySnapshot.model_validate(fixture["library"])
    assert validated.libraries == [] and validated.active_library_id is None

    live = api_client.get("/api/prompt-libraries", headers=EDITOR).json()["library"]
    assert live == fixture["library"], "空库响应必须与冻结夹具逐字一致（version/active_library_id/libraries）"


def test_fixture_not_empty_deletion_matches_exception():
    """非空库删除夹具错误码必须与 LIBRARY_NOT_EMPTY 语义一致。"""
    content = json.loads(
        (REPO_ROOT / "docs" / "fixtures" / "prompt-library-not-empty-409.json").read_text(encoding="utf-8")
    )
    assert content["detail"]["code"] == "LIBRARY_NOT_EMPTY"


def test_fixture_create_request_matches_model():
    """创建请求夹具必须可被契约模型接受。"""
    from gw.prompt_library.models import PromptLibraryCreateRequest

    payload = json.loads(
        (REPO_ROOT / "docs" / "fixtures" / "prompt-library-create-request.json").read_text(encoding="utf-8")
    )
    req = PromptLibraryCreateRequest.model_validate(payload)
    assert req.name == "角色提示词库"
    assert req.expected_version is None
