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

"""QIN-592: 图片版本子资源归属校验契约测试。

验收标准覆盖：
- AC1: B 读 A 的私有版本媒体 -> 404 ASSET_NOT_FOUND，不泄露源图/版本信息；A 读 -> 200 原图字节。
- AC2: B 调 POST /api/media-proxy 代理 -> 404 ASSET_NOT_FOUND，不返回 proxy_url；A 调 -> ready + proxy_url 可取回 content。
- AC3: B PATCH 版本改名/隐藏 -> 404，A 侧数据不变；A PATCH -> 200 生效。
- AC4: B DELETE 版本 -> 404，A 侧版本不减；A DELETE -> 200 且版本删除。
- AC5: 公共素材版本与项目成员授权不受影响，拒绝只发生在跨主体时。
- AC6: 全部拒绝统一 404 ASSET_NOT_FOUND，格式为 {"detail": {"code": "...", "message": "..."}}，禁止返回 403。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gw.asset_registry import repository as repo
from gw.core import storage
from gw.core.asset_visibility import asset_owner_key, current_asset_owner
from gw.core.auth import require_authenticated

AUTH_A = {"Authorization": "Bearer owner-a-token", "X-User-Role": "editor"}
AUTH_B = {"Authorization": "Bearer owner-b-token", "X-User-Role": "editor"}
AUTH_ADMIN = {"Authorization": "Bearer owner-admin-token", "X-User-Role": "admin"}


def _owner_key(token: str) -> str:
    ctx = require_authenticated(token, "editor")
    return asset_owner_key(ctx)


def _create_sample_png(path: Path) -> Path:
    img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    path.write_bytes(buf.getvalue())
    return path


@pytest.fixture
def asset_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed_dir))

    storage._ALLOWED_ROOTS_STATE_CACHE = None
    storage._ALLOWED_ROOTS_STATE_PATH_CACHE = None
    repo._STATE_CACHE = None
    repo._STATE_PATH_CACHE = None

    png_path = _create_sample_png(allowed_dir / "sample.png")

    # 创建私有素材 (归属 A)
    owner_a = _owner_key(AUTH_A["Authorization"])
    created_a = repo.import_assets(
        [
            {
                "name": "private_a.png",
                "kind": "image",
                "workflow_owner": owner_a,
                "metadata": {"workflow_owner": owner_a},
            }
        ],
        "image",
        None,
    )
    asset_id_a = created_a["asset_ids"][0]

    # 给 A 创建一个版本 (在 A 的所有者上下文中创建)
    token = current_asset_owner.set(owner_a)
    try:
        v_res_a = repo.create_image_version(
            asset_id_a, str(png_path), expected_version=None
        )
    finally:
        current_asset_owner.reset(token)
    version_id_a = v_res_a["version"]["version_id"]

    # 创建公共素材 (无 workflow_owner)
    created_pub = repo.import_assets(
        [{"name": "public.png", "kind": "image"}],
        "image",
        None,
    )
    asset_id_pub = created_pub["asset_ids"][0]
    v_res_pub = repo.create_image_version(
        asset_id_pub, str(png_path), expected_version=None
    )
    version_id_pub = v_res_pub["version"]["version_id"]

    return {
        "asset_id_a": asset_id_a,
        "version_id_a": version_id_a,
        "asset_id_pub": asset_id_pub,
        "version_id_pub": version_id_pub,
        "png_bytes": png_path.read_bytes(),
        "png_path": png_path,
        "owner_a": owner_a,
    }


def test_ac1_read_version_media_visibility(
    client: TestClient, asset_fixture: dict[str, Any]
):
    asset_id = asset_fixture["asset_id_a"]
    version_id = asset_fixture["version_id_a"]

    # B 请求 A 的私有版本媒体 -> 404 ASSET_NOT_FOUND
    res_b = client.get(
        f"/api/asset-registry/assets/{asset_id}/image-versions/{version_id}/media",
        headers=AUTH_B,
    )
    assert res_b.status_code == 404
    detail = res_b.json().get("detail", {})
    assert detail.get("code") == "ASSET_NOT_FOUND"
    assert detail.get("message") == f"素材 {asset_id} 不存在"
    assert asset_fixture["png_bytes"] not in res_b.content

    # A 请求 A 的私有版本媒体 -> 200 原图字节
    res_a = client.get(
        f"/api/asset-registry/assets/{asset_id}/image-versions/{version_id}/media",
        headers=AUTH_A,
    )
    assert res_a.status_code == 200
    assert res_a.content == asset_fixture["png_bytes"]


def test_ac2_media_proxy_visibility(client: TestClient, asset_fixture: dict[str, Any]):
    asset_id = asset_fixture["asset_id_a"]
    version_id = asset_fixture["version_id_a"]

    # 1. B 调 POST /api/media-proxy 与 GET /api/media-proxy 代理 A 的私有版本 -> 404 ASSET_NOT_FOUND
    res_b_post = client.post(
        "/api/media-proxy",
        headers=AUTH_B,
        json={
            "asset_id": asset_id,
            "version_id": version_id,
            "profile": "preview",
            "width": 320,
        },
    )
    assert res_b_post.status_code == 404
    assert res_b_post.json() == {
        "detail": {"code": "ASSET_NOT_FOUND", "message": f"素材 {asset_id} 不存在"}
    }

    res_b_get = client.get(
        "/api/media-proxy",
        headers=AUTH_B,
        params={
            "asset_id": asset_id,
            "version_id": version_id,
            "profile": "preview",
            "width": 320,
        },
    )
    assert res_b_get.status_code == 404
    assert res_b_get.json() == {
        "detail": {"code": "ASSET_NOT_FOUND", "message": f"素材 {asset_id} 不存在"}
    }

    # 2. A 调 POST /api/media-proxy 纯真实 HTTP 请求（无手动上下文注入） -> 200 ready 并可取回内容
    res_a_post = client.post(
        "/api/media-proxy",
        headers=AUTH_A,
        json={
            "asset_id": asset_id,
            "version_id": version_id,
            "profile": "preview",
            "width": 320,
        },
    )
    assert res_a_post.status_code == 200, res_a_post.text
    body_a_post = res_a_post.json()
    assert body_a_post["availability"] == "ready"
    assert body_a_post["asset_id"] == asset_id
    assert body_a_post["version_id"] == version_id
    proxy_url_post = body_a_post["proxy_url"]
    assert proxy_url_post.startswith("/api/media-proxy/content?")

    res_content = client.get(proxy_url_post, headers=AUTH_A)
    assert res_content.status_code == 200
    assert res_content.content

    # 3. A 调 GET /api/media-proxy 纯真实 HTTP 请求 -> 200 ready
    res_a_get = client.get(
        "/api/media-proxy",
        headers=AUTH_A,
        params={
            "asset_id": asset_id,
            "version_id": version_id,
            "profile": "preview",
            "width": 320,
        },
    )
    assert res_a_get.status_code == 200, res_a_get.text
    assert res_a_get.json()["availability"] == "ready"
    assert res_a_get.json()["version_id"] == version_id


def test_ac3_patch_version_visibility(
    client: TestClient, asset_fixture: dict[str, Any]
):
    asset_id = asset_fixture["asset_id_a"]
    version_id = asset_fixture["version_id_a"]

    # B 尝试修改 label
    res_b_label = client.patch(
        f"/api/asset-registry/assets/{asset_id}/image-versions/{version_id}",
        headers=AUTH_B,
        json={"label": "hacked_by_b"},
    )
    assert res_b_label.status_code == 404
    assert res_b_label.json() == {
        "detail": {"code": "ASSET_NOT_FOUND", "message": f"素材 {asset_id} 不存在"}
    }

    # B 尝试修改 hidden
    res_b_hidden = client.patch(
        f"/api/asset-registry/assets/{asset_id}/image-versions/{version_id}",
        headers=AUTH_B,
        json={"hidden": True},
    )
    assert res_b_hidden.status_code == 404
    assert res_b_hidden.json() == {
        "detail": {"code": "ASSET_NOT_FOUND", "message": f"素材 {asset_id} 不存在"}
    }

    # A 验证数据未被 B 改动
    res_a_list = client.get(
        f"/api/asset-registry/assets/{asset_id}/image-versions",
        headers=AUTH_A,
    )
    assert res_a_list.status_code == 200
    v_record = res_a_list.json()["versions"][0]
    assert v_record["label"] is None
    assert v_record["hidden"] is False

    # A 正常修改
    res_a_patch = client.patch(
        f"/api/asset-registry/assets/{asset_id}/image-versions/{version_id}",
        headers=AUTH_A,
        json={"label": "legit_label", "hidden": True},
    )
    assert res_a_patch.status_code == 200
    assert res_a_patch.json()["version"]["label"] == "legit_label"
    assert res_a_patch.json()["version"]["hidden"] is True


def test_ac4_delete_version_visibility(
    client: TestClient, asset_fixture: dict[str, Any]
):
    asset_id = asset_fixture["asset_id_a"]
    version_id = asset_fixture["version_id_a"]

    # B 尝试删除 A 的版本
    res_b_del = client.delete(
        f"/api/asset-registry/assets/{asset_id}/image-versions/{version_id}",
        headers=AUTH_B,
    )
    assert res_b_del.status_code == 404
    assert res_b_del.json() == {
        "detail": {"code": "ASSET_NOT_FOUND", "message": f"素材 {asset_id} 不存在"}
    }

    # A 验证版本仍然存在
    res_a_list = client.get(
        f"/api/asset-registry/assets/{asset_id}/image-versions",
        headers=AUTH_A,
    )
    assert res_a_list.status_code == 200
    assert len(res_a_list.json()["versions"]) == 1

    # A 正常删除
    res_a_del = client.delete(
        f"/api/asset-registry/assets/{asset_id}/image-versions/{version_id}",
        headers=AUTH_A,
    )
    assert res_a_del.status_code == 200
    assert res_a_del.json()["deleted"] is True

    # 删除后版本列表为空
    res_a_list2 = client.get(
        f"/api/asset-registry/assets/{asset_id}/image-versions",
        headers=AUTH_A,
    )
    assert len(res_a_list2.json()["versions"]) == 0


def test_ac5_public_asset_and_project_membership(
    client: TestClient, asset_fixture: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    asset_id_pub = asset_fixture["asset_id_pub"]
    version_id_pub = asset_fixture["version_id_pub"]

    # 1. 公共素材对 B 仍可读媒体
    res_b_media = client.get(
        f"/api/asset-registry/assets/{asset_id_pub}/image-versions/{version_id_pub}/media",
        headers=AUTH_B,
    )
    assert res_b_media.status_code == 200
    assert res_b_media.content == asset_fixture["png_bytes"]

    # B 可以 PATCH
    res_b_patch = client.patch(
        f"/api/asset-registry/assets/{asset_id_pub}/image-versions/{version_id_pub}",
        headers=AUTH_B,
        json={"label": "public_label"},
    )
    assert res_b_patch.status_code == 200
    assert res_b_patch.json()["version"]["label"] == "public_label"

    # B 可以 DELETE
    res_b_del = client.delete(
        f"/api/asset-registry/assets/{asset_id_pub}/image-versions/{version_id_pub}",
        headers=AUTH_B,
    )
    assert res_b_del.status_code == 200
    assert res_b_del.json()["deleted"] is True

    # 2. 项目成员授权逻辑回归验证：
    # 构造带有 project_id 的素材（无 workflow_owner），验证同项目成员可读、非项目成员 404
    created_proj = repo.import_assets(
        [{"name": "proj_asset.png", "kind": "image", "project_id": "proj_allowed"}],
        "image",
        None,
    )
    proj_asset_id = created_proj["asset_ids"][0]
    v_proj = repo.create_image_version(
        proj_asset_id, str(asset_fixture["png_path"]), expected_version=None
    )
    v_proj_id = v_proj["version"]["version_id"]

    import gw.projects_hub.service as proj_mod

    ctx_a = require_authenticated(AUTH_A["Authorization"], "editor")
    owner_key_a = proj_mod.owner_key_for_context(ctx_a)

    class MockProjectsService:
        def can_read_project(
            self, pid: str, owner_key: str | None = None, role: str | None = None
        ):
            # 仅允许 AUTH_A 主体访问 proj_allowed
            return pid == "proj_allowed" and owner_key == owner_key_a

    monkeypatch.setattr(
        proj_mod.default_projects_service,
        "can_read_project",
        MockProjectsService().can_read_project,
    )

    # AUTH_A 是项目成员，访问该版本媒体 -> 200
    res_member = client.get(
        f"/api/asset-registry/assets/{proj_asset_id}/image-versions/{v_proj_id}/media",
        headers=AUTH_A,
    )
    assert res_member.status_code == 200

    # AUTH_B 不是项目成员，访问该版本媒体 -> 404 ASSET_NOT_FOUND
    res_non_member = client.get(
        f"/api/asset-registry/assets/{proj_asset_id}/image-versions/{v_proj_id}/media",
        headers=AUTH_B,
    )
    assert res_non_member.status_code == 404
    assert res_non_member.json()["detail"]["code"] == "ASSET_NOT_FOUND"


def test_ac6_error_envelope_consistency(
    client: TestClient, asset_fixture: dict[str, Any]
):
    """验证全部跨主体拒绝统一返回 404 ASSET_NOT_FOUND，禁止返回 403，结构保持标准包裹。"""
    asset_id = asset_fixture["asset_id_a"]
    version_id = asset_fixture["version_id_a"]

    endpoints = [
        ("GET", f"/api/asset-registry/assets/{asset_id}/image-versions/{version_id}/media", None),
        ("POST", "/api/media-proxy", {"asset_id": asset_id, "version_id": version_id, "profile": "preview", "width": 320}),
        ("PATCH", f"/api/asset-registry/assets/{asset_id}/image-versions/{version_id}", {"label": "bad"}),
        ("DELETE", f"/api/asset-registry/assets/{asset_id}/image-versions/{version_id}", None),
    ]

    for method, path, json_data in endpoints:
        if method == "GET":
            res = client.get(path, headers=AUTH_B)
        elif method == "POST":
            res = client.post(path, headers=AUTH_B, json=json_data)
        elif method == "PATCH":
            res = client.patch(path, headers=AUTH_B, json=json_data)
        elif method == "DELETE":
            res = client.delete(path, headers=AUTH_B)
        else:
            raise ValueError(f"Unsupported method {method}")

        assert res.status_code == 404, f"{method} {path} should return 404, got {res.status_code}"
        body = res.json()
        assert "detail" in body, f"{method} {path} missing 'detail' key: {body}"
        assert body["detail"]["code"] == "ASSET_NOT_FOUND"
        assert f"素材 {asset_id} 不存在" in body["detail"]["message"]
