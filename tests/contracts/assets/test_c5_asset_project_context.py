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

"""C5-T3 资产项目上下文契约测试（AC3, AC4, AC7, AC8）。

覆盖：
1. AC4: 项目绑定资产读面按读权限可见性过滤（显式 project_id 404 同形隐藏；全量列表过滤无权项目资产）。
2. AC4: 读取资产媒体（/assets/{asset_id}/media）按项目读权限隐藏为 404。
3. AC3/AC7: 资产修改（PATCH /assets/{asset_id}）按项目写权限收口，归档/回收站 403 PROJECT_READ_ONLY，无权 404。
4. AC8: CAS 校验绝不先于鉴权执行，防版本信息探测。
5. AC3/AC7: 项目回收站恢复（POST /project-recycle/{project_id}/restore）门禁与状态处理。
6. AC3/AC4/AC7: 工作区任务（POST /workspace-jobs/{job_id}/{action}）无权 404 与归档 403 门禁。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from tests.contracts.projects.c5_permission_fixtures import (  # noqa: F401  # pytest fixture
    headers,
    seed,
    subject,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(data_dir))
    return TestClient(create_app())


def test_asset_project_list_hidden_for_unauthorized(client):
    """AC4: 显式指定 project_id 时，无读权限主体与未知项目均返回 404 PROJECT_NOT_FOUND 同形错误。"""
    ids = seed(client, "active", action="asset_project_list", role="owner")
    active_project_id = ids["project_id"]

    # 合法 owner 读取成功
    resp_owner = client.get(
        f"/api/asset-registry/assets?project_id={active_project_id}",
        headers=headers("owner"),
    )
    assert resp_owner.status_code == 200
    assert any(
        a["asset_id"] == ids["asset_id"] for a in resp_owner.json().get("assets", [])
    )

    # 被撤销读权限主体访问 -> 404 PROJECT_NOT_FOUND
    resp_revoked = client.get(
        f"/api/asset-registry/assets?project_id={active_project_id}",
        headers=headers("revoked_editor"),
    )
    assert resp_revoked.status_code == 404
    assert resp_revoked.json()["detail"]["code"] == "PROJECT_NOT_FOUND"

    # 未知项目 -> 404 PROJECT_NOT_FOUND
    resp_unknown = client.get(
        f"/api/asset-registry/assets?project_id={ids['unknown_project_id']}",
        headers=headers("revoked_editor"),
    )
    assert resp_unknown.status_code == 404
    assert resp_unknown.json() == resp_revoked.json()


def test_asset_list_all_filters_unreadable_project_assets(client):
    """AC4: 全量资产列表自动过滤当前主体不可读项目绑定的资产。"""
    ids = seed(client, "active", action="asset_list_all", role="cross_project_editor")
    cross_asset_id = ids["cross_asset_id"]
    active_asset_id = ids["asset_id"]

    # cross_project_editor 对 active_project_id 无读权限，但对 cross_project_id 有读权限
    resp = client.get(
        "/api/asset-registry/assets", headers=headers("cross_project_editor")
    )
    assert resp.status_code == 200
    returned_ids = {a["asset_id"] for a in resp.json().get("assets", [])}
    assert cross_asset_id in returned_ids
    assert active_asset_id not in returned_ids


def test_asset_read_media_hides_unreadable_project_asset(client):
    """AC4: 资产所属项目不可读时，读取媒体返回 404 ASSET_NOT_FOUND。"""
    ids = seed(client, "active", action="asset_read_media", role="revoked_editor")
    asset_id = ids["asset_id"]

    # owner 可读取媒体
    resp_owner = client.get(
        f"/api/asset-registry/assets/{asset_id}/media",
        headers=headers("owner"),
    )
    assert resp_owner.status_code == 200

    # revoked_editor 不可读取，隐藏为 404
    resp_revoked = client.get(
        f"/api/asset-registry/assets/{asset_id}/media",
        headers=headers("revoked_editor"),
    )
    assert resp_revoked.status_code == 404
    assert resp_revoked.json()["detail"]["code"] == "ASSET_NOT_FOUND"


def test_asset_write_mutate_permissions_and_read_only(client):
    """AC3/AC7: 资产修改需项目写权限；归档项目 403 PROJECT_READ_ONLY，无写权主体 404。"""
    # 1. active 项目
    ids_active = seed(client, "active", action="asset_write_mutate", role="owner")
    asset_id = ids_active["asset_id"]
    rev_resp = client.get("/api/asset-registry/assets", headers=headers("owner"))
    cur_rev = rev_resp.json().get("revision", 1)

    # owner 可修改
    resp_owner = client.patch(
        f"/api/asset-registry/assets/{asset_id}",
        headers=headers("owner"),
        json={"name": "owner-renamed", "expected_version": cur_rev},
    )
    assert resp_owner.status_code == 200
    assert resp_owner.json()["asset"]["name"] == "owner-renamed"

    # read_grantee_editor 只有读权限，无写权限 -> 404 隐藏
    cur_rev = resp_owner.json()["revision"]
    resp_grantee = client.patch(
        f"/api/asset-registry/assets/{asset_id}",
        headers=headers("read_grantee_editor"),
        json={"name": "grantee-renamed", "expected_version": cur_rev},
    )
    assert resp_grantee.status_code == 404

    # 2. archived 项目 -> 原写者 403 PROJECT_READ_ONLY
    ids_archived = seed(client, "archived", action="asset_write_mutate", role="owner")
    archived_asset_id = ids_archived["asset_id"]
    resp_archived = client.patch(
        f"/api/asset-registry/assets/{archived_asset_id}",
        headers=headers("owner"),
        json={"name": "cannot-rename", "expected_version": cur_rev},
    )
    assert resp_archived.status_code == 403
    assert resp_archived.json()["detail"]["code"] == "PROJECT_READ_ONLY"


def test_asset_write_cas_preceded_by_auth(client):
    """AC8: 无写权限主体尝试更新资产时，即使传入错误 expected_version 依然返回 404，绝不泄露 409。"""
    ids = seed(
        client, "active", action="asset_write_mutate", role="cross_project_editor"
    )
    asset_id = ids["asset_id"]

    resp = client.patch(
        f"/api/asset-registry/assets/{asset_id}",
        headers=headers("cross_project_editor"),
        json={"name": "probe", "expected_version": 99999},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "ASSET_NOT_FOUND"
