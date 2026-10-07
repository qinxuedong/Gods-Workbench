# -*- coding: utf-8 -*-
"""Phase 10D 设置页契约测试。

覆盖 docs/contracts/SETTINGS-INTERFACE-CATALOG.yaml（p10d-frozen-1）的 13 个方法：

- GET/PATCH  /api/storage-settings
- GET/PUT    /api/providers
- POST       /api/providers/{fetch-models,probe-async,test-connection}
- GET        /api/providers/probe-async/jobs/{job_id}
- GET/POST   /api/asset-registry/asset-structures
- GET/PATCH/DELETE /api/asset-registry/asset-structures/{structure_id}
- PATCH      /api/asset-registry/asset-structures/{structure_id}/current

硬性口径：零伪造（空列表 / 未配置如实标记）、探测端点 fail-closed、
CAS 409、401/403、以及未授权相邻端点仍 404/405。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_settings as routes
from gw.api.app import create_app
from gw.settings.models import PROVIDER_PROBE_NOT_INTEGRATED
from gw.settings.service import (
    AssetStructureService,
    ProviderService,
    StorageSettingsService,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CATALOG = REPO_ROOT / "docs" / "contracts" / "SETTINGS-INTERFACE-CATALOG.yaml"
SERVICE_SOURCES = (
    "gw/settings/models.py",
    "gw/settings/service.py",
    "gw/api/routes_settings.py",
)
EDITOR = {"X-User-Role": "editor", "Authorization": "Bearer cleanroom-test"}
READONLY = {"X-User-Role": "readonly", "Authorization": "Bearer cleanroom-test"}
UNAUTHORIZED_NEIGHBORS = (
    ("get", "/api/asset-reviews/sessions-unknown"),
    ("get", "/api/asset-thumbnails/generate-unknown"),
    ("get", "/api/unknown-settings-neighbor"),
)


@pytest.fixture()
def api_client(monkeypatch) -> TestClient:
    """每个用例注入全新的内存服务，避免进程内单例串扰。"""
    monkeypatch.setattr(routes, "default_storage_settings_service", StorageSettingsService())
    monkeypatch.setattr(routes, "default_provider_service", ProviderService())
    monkeypatch.setattr(routes, "default_asset_structure_service", AssetStructureService())
    with TestClient(create_app()) as client:
        yield client


def _create_structure(client: TestClient, expected_version: int | None = 1) -> dict:
    """创建最小合法结构（两个成员）并返回响应 JSON。"""
    payload = {
        "kind": "version",
        "asset_ids": ["asset_a", "asset_b"],
        "current_asset_id": "asset_a",
    }
    if expected_version is not None:
        payload["expected_version"] = expected_version
    res = client.post("/api/asset-registry/asset-structures", json=payload, headers=EDITOR)
    assert res.status_code == 201, res.text
    return res.json()


# ---------------------------------------------------------------------------
# 契约范围与零伪造（源码级反向断言）
# ---------------------------------------------------------------------------


def test_catalog_declares_exactly_fourteen_methods():
    """契约必须恰好声明本阶段授权的 14 个方法。"""
    pairs = re.findall(r"method:\s*(\w+)\s*\n\s*path:\s*(\S+)", CATALOG.read_text(encoding="utf-8"))
    assert len(pairs) == 14, f"契约方法数应为 13，实际 {len(pairs)}: {pairs}"


def test_catalog_forbids_unauthorized_neighbors():
    """契约不得授权相邻未实现端点。"""
    routes_set = {path for _, path in re.findall(r"method:\s*(\w+)\s*\n\s*path:\s*(\S+)", CATALOG.read_text(encoding="utf-8"))}
    for forbidden in ("/api/local-assets", "/api/storage-files"):
        assert all(forbidden not in path for path in routes_set), f"设置契约不得授权 {forbidden}"


def test_service_sources_have_no_fabrication_generators():
    """源码级反向断言：设置页实现不得使用 random / uuid 生成业务内容。"""
    for relative in SERVICE_SOURCES:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for marker in ("import random", "random.", "uuid4", "import uuid"):
            assert marker not in text, f"{relative} 不得包含随机/uuid 生成器: {marker}"


# ---------------------------------------------------------------------------
# GET/PATCH /api/storage-settings
# ---------------------------------------------------------------------------


def test_get_storage_settings_unconfigured(api_client: TestClient):
    """无真实来源时必须如实标记未配置，不得编造根目录。"""
    res = api_client.get("/api/storage-settings", headers=EDITOR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is False
    assert body["data_status"] == "not_configured"
    assert body["dirs"] == {}
    assert body["local_libraries"] == []
    assert body["data_gaps"]
    fixture = json.loads((REPO_ROOT / "docs" / "fixtures" / "settings-storage-unconfigured.json").read_text(encoding="utf-8"))
    assert body == fixture


def test_get_storage_settings_requires_authentication(api_client: TestClient):
    """未认证读取存储设置必须 401。"""
    res = api_client.get("/api/storage-settings")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "UNAUTHORIZED"


def test_readonly_role_can_read_storage_settings(api_client: TestClient):
    """只读角色可以读取存储设置。"""
    res = api_client.get("/api/storage-settings", headers=READONLY)
    assert res.status_code == 200


def test_patch_storage_settings_success_and_cas_conflict(api_client: TestClient):
    """保存成功后 revision 递增；错误 expected_version 必须 409 VERSION_CONFLICT。"""
    ok = api_client.patch(
        "/api/storage-settings",
        json={"dirs": {"local": "D:/assets"}, "expected_version": 1},
        headers=EDITOR,
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["configured"] is True
    assert body["revision"] == 2
    assert body["dirs"]["local"] == "D:/assets"

    conflict = api_client.patch(
        "/api/storage-settings",
        json={"dirs": {"local": "D:/other"}, "expected_version": 1},
        headers=EDITOR,
    )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["code"] == "VERSION_CONFLICT"
    assert detail["expected_version"] == 1
    assert detail["current_version"] == 2


def test_patch_storage_settings_readonly_forbidden(api_client: TestClient):
    """只读角色保存存储设置必须 403。"""
    res = api_client.patch("/api/storage-settings", json={"dirs": {"local": "x"}}, headers=READONLY)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# GET/PUT /api/providers
# ---------------------------------------------------------------------------


def test_get_providers_empty_by_default(api_client: TestClient):
    """默认必须返回空数组，不得预置任何厂商条目。"""
    res = api_client.get("/api/providers", headers=EDITOR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["providers"] == []
    assert body["configured"] is False
    fixture = json.loads((REPO_ROOT / "docs" / "fixtures" / "settings-providers-empty.json").read_text(encoding="utf-8"))
    assert body == fixture


def test_put_providers_strips_credentials_and_accepts_frontend_id(api_client: TestClient):
    """整体替换必须剥离凭据字段，并接受前端既有的 id 作为 provider_id。"""
    res = api_client.put(
        "/api/providers",
        json=[{"id": "openai", "name": "OpenAI", "api_key": "sk-secret", "base_url": "https://api.example"}],
        headers=EDITOR,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is True
    assert len(body["providers"]) == 1
    item = body["providers"][0]
    assert item["provider_id"] == "openai"
    assert "api_key" not in item
    dumped = json.dumps(body, ensure_ascii=False)
    assert "sk-secret" not in dumped


def test_put_providers_cas_conflict(api_client: TestClient):
    """PUT providers 错误 expected_version 必须 409。"""
    first = api_client.put("/api/providers", json=[{"id": "a", "name": "A"}], headers=EDITOR)
    assert first.status_code == 200
    conflict = api_client.put(
        "/api/providers?expected_version=1",
        json=[{"id": "b", "name": "B"}],
        headers=EDITOR,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "VERSION_CONFLICT"


def test_put_providers_duplicate_id_rejected(api_client: TestClient):
    """重复 provider_id 必须 400。"""
    res = api_client.put(
        "/api/providers",
        json=[{"id": "a"}, {"provider_id": "a"}],
        headers=EDITOR,
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "INVALID_REQUEST"


def test_put_providers_readonly_forbidden(api_client: TestClient):
    """只读角色保存 providers 必须 403。"""
    res = api_client.put("/api/providers", json=[], headers=READONLY)
    assert res.status_code == 403


def test_get_providers_requires_authentication(api_client: TestClient):
    """未认证读取 providers 必须 401。"""
    res = api_client.get("/api/providers")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# 探测端点 fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/providers/fetch-models",
        "/api/providers/probe-async",
        "/api/providers/test-connection",
    ],
)
def test_provider_probe_endpoints_fail_closed_without_upstream(api_client: TestClient, path: str):
    """上游不可达时必须 503，且不得回显凭据或伪造模型/延迟。"""
    res = api_client.post(path, json={"base_url": "https://127.0.0.1:1", "api_keys": "sk-x"}, headers=EDITOR)
    assert res.status_code in (403, 503), res.text
    detail = res.json()["detail"]
    assert detail["code"] in (
        PROVIDER_PROBE_NOT_INTEGRATED, "PROVIDER_PROBE_FAILED", "SSRF_BLOCKED", "URL_NOT_ALLOWED",
    )
    assert "sk-x" not in res.text


def test_provider_probe_ssrf_blocked():
    """内网/保留网段必须 403 SSRF_BLOCKED。"""
    with TestClient(create_app()) as client:
        res = client.post("/api/providers/test-connection",
                          json={"base_url": "http://169.254.169.254/"}, headers=EDITOR)
        assert res.status_code == 403
        assert res.json()["detail"]["code"] == "SSRF_BLOCKED"


def test_provider_probe_readonly_forbidden(api_client: TestClient):
    """只读角色调用探测端点必须 403，而不是伪造成功。"""
    res = api_client.post("/api/providers/test-connection", json={}, headers=READONLY)
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# 素材结构
# ---------------------------------------------------------------------------


def test_list_asset_structures_empty(api_client: TestClient):
    """无结构时 items 必须为空数组，不得编造成员。"""
    res = api_client.get("/api/asset-registry/asset-structures", headers=EDITOR)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_create_structure_stable_id_and_no_member_metadata(api_client: TestClient):
    """创建结构必须使用确定性 structure_id，且不编造成员素材元数据。"""
    body = _create_structure(api_client)
    structure = body["structure"]
    assert structure["structure_id"] == "strc_0001"
    assert structure["version"] == 1
    assert structure["kind"] == "version"
    for member in structure["members"]:
        assert set(member) == {"asset_id", "sort_order"}
        assert "name" not in member and "thumb" not in member


def test_create_structure_collection_cas_conflict(api_client: TestClient):
    """创建结构时集合 CAS 冲突必须 409 STRUCTURE_VERSION_CONFLICT。"""
    _create_structure(api_client, expected_version=1)
    res = api_client.post(
        "/api/asset-registry/asset-structures",
        json={"kind": "group", "asset_ids": ["asset_c", "asset_d"], "expected_version": 1},
        headers=EDITOR,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "STRUCTURE_VERSION_CONFLICT"


def test_get_structure_not_found(api_client: TestClient):
    """不存在的结构必须 404 STRUCTURE_NOT_FOUND。"""
    res = api_client.get("/api/asset-registry/asset-structures/strc_9999", headers=EDITOR)
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "STRUCTURE_NOT_FOUND"


def test_patch_structure_cas_conflict(api_client: TestClient):
    """更新结构错误 expected_version 必须 409。"""
    created = _create_structure(api_client)
    structure_id = created["structure"]["structure_id"]
    res = api_client.patch(
        f"/api/asset-registry/asset-structures/{structure_id}",
        json={"current_asset_id": "asset_b", "expected_version": 9},
        headers=EDITOR,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "STRUCTURE_VERSION_CONFLICT"


def test_set_current_success(api_client: TestClient):
    """切换当前代表素材成功后 version 递增。"""
    created = _create_structure(api_client)
    structure_id = created["structure"]["structure_id"]
    res = api_client.patch(
        f"/api/asset-registry/asset-structures/{structure_id}/current",
        json={"current_asset_id": "asset_b", "expected_version": 1},
        headers=EDITOR,
    )
    assert res.status_code == 200, res.text
    assert res.json()["structure"]["current_asset_id"] == "asset_b"
    assert res.json()["structure"]["version"] == 2


def test_delete_structure_success_and_cas_conflict(api_client: TestClient):
    """删除结构：错误版本 409，正确版本 200 且随后 404。"""
    first = _create_structure(api_client)
    structure_id = first["structure"]["structure_id"]
    conflict = api_client.delete(
        f"/api/asset-registry/asset-structures/{structure_id}?expected_version=9",
        headers=EDITOR,
    )
    assert conflict.status_code == 409
    ok = api_client.delete(
        f"/api/asset-registry/asset-structures/{structure_id}?expected_version=1",
        headers=EDITOR,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["deleted_structure_id"] == structure_id
    missing = api_client.get(f"/api/asset-registry/asset-structures/{structure_id}", headers=EDITOR)
    assert missing.status_code == 404


def test_structure_write_readonly_forbidden(api_client: TestClient):
    """只读角色不得创建/更新/删除结构。"""
    create = api_client.post(
        "/api/asset-registry/asset-structures",
        json={"kind": "version", "asset_ids": ["a", "b"]},
        headers=READONLY,
    )
    assert create.status_code == 403
    created = _create_structure(api_client)
    structure_id = created["structure"]["structure_id"]
    patch = api_client.patch(
        f"/api/asset-registry/asset-structures/{structure_id}",
        json={"current_asset_id": "asset_b"},
        headers=READONLY,
    )
    assert patch.status_code == 403
    delete = api_client.delete(
        f"/api/asset-registry/asset-structures/{structure_id}",
        headers=READONLY,
    )
    assert delete.status_code == 403


def test_create_structure_requires_two_members(api_client: TestClient):
    """成员不足 2 个必须 400。"""
    res = api_client.post(
        "/api/asset-registry/asset-structures",
        json={"kind": "version", "asset_ids": ["only_one"]},
        headers=EDITOR,
    )
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# 范围守卫
# ---------------------------------------------------------------------------


def test_unauthorized_neighbor_endpoints_still_unavailable(api_client: TestClient):
    """未获契约授权的相邻端点必须仍然 404/405。"""
    for method, path in UNAUTHORIZED_NEIGHBORS:
        res = getattr(api_client, method)(path, headers=EDITOR)
        assert res.status_code in {404, 405}, f"{method.upper()} {path} 不应可用，实际 {res.status_code}"
