# -*- coding: utf-8 -*-
"""C4-AC5 未归属收件箱契约回归。

覆盖：收件箱只返回当前认证账户自己拥有、且没有 project_id 的资产；
匿名 401；其他账户看不到别人的资产也不能移动别人的资产；移动成功后
资产带 project_id 并从收件箱消失；CAS 过期 409；已归属资产拒绝再移动；
注册/列表绝不自动归属；只读盘点工具不改数据。

无主资产（metadata.workflow_owner 缺失）口径：只进入本地实例管理员
（role == 'admin'）的收件箱并标记 source_unknown=true / origin_label='来源未知'；
非 admin 账户（含 editor/governor）列表不可见、移动与不存在同形 404；
管理员移动无主资产时在同一次 CAS 写入内回填 workflow_owner，CAS 409 不回填。

移动接口 CAS 按资产自身版本：收件箱每项带 asset_version（资产 version 计数），
请求体传 expected_asset_version（缺省时兼容旧字段 expected_version，同义）；
其他资产变动推高库 revision 不影响本资产移动，资产自身变动则 409 VERSION_CONFLICT。
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from gw.api.app import create_app
from gw.core.asset_visibility import asset_owner_key
from gw.core.auth import require_authenticated

REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_SCRIPT = REPO_ROOT / "tools" / "c4_acceptance" / "unassigned_inbox_inventory.py"

OWNER_A = {"Authorization": "Bearer c4-owner-a", "X-User-Role": "editor"}
OWNER_B = {"Authorization": "Bearer c4-owner-b", "X-User-Role": "editor"}
GOVERNOR_A = {"Authorization": "Bearer c4-owner-a", "X-User-Role": "governor"}
GOVERNOR_B = {"Authorization": "Bearer c4-owner-b", "X-User-Role": "governor"}
ADMIN = {"Authorization": "Bearer c4-admin", "X-User-Role": "admin"}
READONLY_A = {"Authorization": "Bearer c4-owner-a", "X-User-Role": "readonly"}

INBOX = "/api/assets/unassigned"
MOVE = "/api/assets/{asset_id}/move-to-project"


def _move(client, asset_id: str, headers, payload: dict):
    return client.post(MOVE.format(asset_id=asset_id), headers=headers, json=payload)


def _owner_key(token: str) -> str:
    """与 start.bat 桥接一致的资产归属键（同 core.asset_visibility 口径）。"""
    return asset_owner_key(require_authenticated(f"Bearer {token}", "editor"))


def _register_asset(token: str, asset_id: str, name: str, *, project_id: str | None = None) -> str:
    """按 god_workflow 桥接的真实登记路径构造带可证明归属的工作流资产。"""
    from gw.asset_registry import repository as asset_repo

    metadata = {
        "workflow_owner": _owner_key(token),
        "source_context": {"project_id": project_id} if project_id else {},
    }
    asset_repo.register_asset_record(asset_id, name, "image", metadata=metadata)
    return asset_id


def _import_ownerless(client, name: str = "无主导入.png") -> str:
    """按真实导入路径构造无 workflow_owner 的无主资产。"""
    imported = client.post("/api/asset-registry/assets/import", headers=OWNER_A,
                           json={"items": [{"name": name, "kind": "image"}]})
    assert imported.status_code == 200, imported.text
    return imported.json()["asset_ids"][0]


def _create_project(client, name: str = "C4 收件项目", headers=None) -> str:
    response = client.post("/api/asset-registry/projects", headers=headers or OWNER_A,
                           json={"name": name, "project_type": "film"})
    assert response.status_code == 201, response.text
    return response.json()["project"]["project_id"]


def _inbox(client, headers=OWNER_A):
    return client.get(INBOX, headers=headers)


def _inbox_asset_version(client, asset_id: str, headers=OWNER_A) -> int:
    """读取收件箱中某资产的 asset_version（移动接口按资产 CAS 的比对基准）。"""
    items = _inbox(client, headers=headers).json()["assets"]
    return next(a["asset_version"] for a in items if a["asset_id"] == asset_id)


def _detail(response) -> dict:
    assert "detail" in response.json(), response.text
    return response.json()["detail"]


# ---------------------------------------------------------------------------
# 认证边界（先于参数校验）
# ---------------------------------------------------------------------------


def test_inbox_and_move_reject_anonymous(client):
    assert client.get(INBOX).status_code == 401
    assert client.post("/api/assets/ast-c4-a1/move-to-project", json={}).status_code == 401


def test_inbox_rejects_invalid_token(client):
    response = client.get(INBOX, headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401
    assert _detail(response)["code"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# 收件箱列表：只看自己的、只看未归属的
# ---------------------------------------------------------------------------


def test_inbox_lists_only_own_unassigned_assets(client):
    mine_unassigned = [_register_asset("c4-owner-a", f"ast-c4-a{index}", f"产物{index}.png")
                       for index in (1, 2)]
    project_id = _create_project(client)
    _register_asset("c4-owner-a", "ast-c4-a3", "已归属.png", project_id=project_id)
    _register_asset("c4-owner-b", "ast-c4-b1", "他人产物.png")

    response = _inbox(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(a["asset_id"] for a in body["assets"]) == sorted(mine_unassigned)
    assert body["total"] == 2
    assert body["data_status"] == "ok"
    assert all(not a.get("project_id") and not a.get("project_ids") for a in body["assets"])

    other = _inbox(client, headers=OWNER_B)
    assert other.status_code == 200
    assert [a["asset_id"] for a in other.json()["assets"]] == ["ast-c4-b1"]


def test_inbox_hides_ownerless_imported_assets_from_non_admin(client):
    """无 workflow_owner 的导入资产对非 admin 账户完全不可见。"""
    _import_ownerless(client, "手工导入.png")
    assert _inbox(client).json()["assets"] == []
    assert _inbox(client, headers=OWNER_B).json()["assets"] == []


# ---------------------------------------------------------------------------
# 无主资产：只进管理员收件箱，标记来源未知；非 admin 不可见不可移动
# ---------------------------------------------------------------------------


def test_non_admin_roles_never_see_or_move_ownerless_assets(client):
    """editor 与 governor 都看不到无主资产，移动与不存在同形 404。"""
    ownerless_id = _import_ownerless(client, "来源未知.png")
    _register_asset("c4-owner-b", "ast-c4-own-b", "自己产出.png")
    editor_project = _create_project(client, "B的项目", headers=OWNER_B)
    governor_project = _create_project(client, "治理项目", headers=GOVERNOR_B)

    # 列表只含自己的有主资产，绝不含无主资产。
    for headers in (OWNER_A, OWNER_B, GOVERNOR_A, GOVERNOR_B):
        assets = _inbox(client, headers=headers).json()["assets"]
        assert all(a["asset_id"] != ownerless_id for a in assets), headers

    # 即使对目标项目有写权限，非 admin 移动无主资产也一律 404 ASSET_NOT_FOUND。
    for headers, project_id in ((OWNER_B, editor_project), (GOVERNOR_B, governor_project)):
        stolen = client.post(MOVE.format(asset_id=ownerless_id), headers=headers,
                             json={"project_id": project_id, "expected_version": 1})
        assert stolen.status_code == 404, stolen.text
        assert _detail(stolen)["code"] == "ASSET_NOT_FOUND"

    # 拒绝即无副作用：无主资产仍未归属、仍未回填 owner。
    record = client.get(f"/api/asset-registry/assets/{ownerless_id}", headers=ADMIN).json()["asset"]
    assert not record.get("project_id") and not record.get("project_ids")
    assert not record.get("metadata", {}).get("workflow_owner")


def test_admin_inbox_lists_ownerless_assets_as_source_unknown(client):
    """admin 收件箱包含自己的有主资产与无主资产；字段按归属区分。"""
    ownerless_id = _import_ownerless(client, "来源未知.png")
    owned_id = _register_asset("c4-admin", "ast-c4-adm1", "管理员产物.png")

    body = _inbox(client, headers=ADMIN).json()
    by_id = {a["asset_id"]: a for a in body["assets"]}
    assert set(by_id) == {ownerless_id, owned_id}

    unknown = by_id[ownerless_id]
    assert unknown["source_unknown"] is True
    assert unknown["origin_label"] == "来源未知"

    owned = by_id[owned_id]
    assert owned["source_unknown"] is False
    assert owned["origin_label"] is None


def test_admin_moves_ownerless_asset_backfills_owner_atomically(client):
    """admin 移动无主资产：同一次 CAS 写入回填 workflow_owner，收件箱消失。"""
    ownerless_id = _import_ownerless(client, "待认领.png")
    project_id = _create_project(client, "管理员收编项目", headers=ADMIN)
    asset_version = _inbox_asset_version(client, ownerless_id, headers=ADMIN)

    moved = client.post(MOVE.format(asset_id=ownerless_id), headers=ADMIN,
                        json={"project_id": project_id, "expected_asset_version": asset_version})
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["moved"] is True and body["project_id"] == project_id
    assert body["asset"]["metadata"]["workflow_owner"] == _owner_key("c4-admin")
    assert body["asset"]["source_unknown"] is False
    assert body["asset"]["origin_label"] is None

    # 持久化回读：归属键被回填为管理员，项目投影就位，收件箱不再出现。
    readback = client.get(f"/api/asset-registry/assets/{ownerless_id}", headers=ADMIN)
    assert readback.status_code == 200, readback.text
    record = readback.json()["asset"]
    assert record["metadata"]["workflow_owner"] == _owner_key("c4-admin")
    assert record["project_id"] == project_id
    assert project_id in record["project_ids"]
    assert all(a["asset_id"] != ownerless_id for a in _inbox(client, headers=ADMIN).json()["assets"])


def test_admin_moves_ownerless_asset_with_none_metadata(client):
    """metadata 为 None 的旧数据形态无主资产：admin 移动同样成功并回填 owner。"""
    from gw.asset_registry import repository as asset_repo

    ownerless_id = _import_ownerless(client, "空metadata.png")

    def force_none_metadata(raw: dict) -> None:
        raw["assets"][ownerless_id]["metadata"] = None

    asset_repo.state().mutate(force_none_metadata)
    assert _inbox(client, headers=ADMIN).json()["assets"][0]["asset_id"] == ownerless_id

    project_id = _create_project(client, "收编None元数据项目", headers=ADMIN)
    asset_version = _inbox_asset_version(client, ownerless_id, headers=ADMIN)

    moved = client.post(MOVE.format(asset_id=ownerless_id), headers=ADMIN,
                        json={"project_id": project_id, "expected_asset_version": asset_version})
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["moved"] is True and body["project_id"] == project_id
    assert body["asset"]["metadata"] == {"workflow_owner": _owner_key("c4-admin")}
    assert body["asset"]["source_unknown"] is False

    readback = client.get(f"/api/asset-registry/assets/{ownerless_id}", headers=ADMIN).json()["asset"]
    assert readback["metadata"] == {"workflow_owner": _owner_key("c4-admin")}
    assert readback["project_id"] == project_id
    assert all(a["asset_id"] != ownerless_id for a in _inbox(client, headers=ADMIN).json()["assets"])


def test_admin_move_cas_conflict_does_not_backfill_owner(client):
    """CAS 过期 409 时整笔不落盘：不回填 workflow_owner，也不写项目归属。"""
    ownerless_id = _import_ownerless(client, "并发认领.png")
    project_id = _create_project(client, "并发项目", headers=ADMIN)
    asset_version = _inbox_asset_version(client, ownerless_id, headers=ADMIN)

    conflict = client.post(MOVE.format(asset_id=ownerless_id), headers=ADMIN,
                           json={"project_id": project_id, "expected_asset_version": asset_version + 99})
    assert conflict.status_code == 409, conflict.text
    assert _detail(conflict)["code"] == "VERSION_CONFLICT"

    record = client.get(f"/api/asset-registry/assets/{ownerless_id}", headers=ADMIN).json()["asset"]
    assert not record.get("metadata", {}).get("workflow_owner")
    assert not record.get("project_id") and not record.get("project_ids")

    inbox = _inbox(client, headers=ADMIN).json()["assets"]
    assert [a["asset_id"] for a in inbox] == [ownerless_id]
    assert inbox[0]["source_unknown"] is True


def test_new_unassigned_assets_appear_naturally(client):
    assert _inbox(client).json()["total"] == 0
    _register_asset("c4-owner-a", "ast-c4-late", "稍后产出.png")
    assets = _inbox(client).json()["assets"]
    assert [a["asset_id"] for a in assets] == ["ast-c4-late"]


# ---------------------------------------------------------------------------
# 移动到项目：成功、回读、离开收件箱
# ---------------------------------------------------------------------------


def test_move_to_project_success_and_readback(client):
    asset_id = _register_asset("c4-owner-a", "ast-c4-m1", "待归位.png")
    project_id = _create_project(client)
    asset_version = _inbox_asset_version(client, asset_id)
    revision = _inbox(client).json()["revision"]

    moved = client.post(MOVE.format(asset_id=asset_id), headers=OWNER_A,
                        json={"project_id": project_id, "expected_asset_version": asset_version})
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["moved"] is True and body["project_id"] == project_id
    assert body["asset"]["project_id"] == project_id
    assert body["asset"]["asset_version"] == asset_version + 1
    assert body["revision"] > asset_version

    remaining = _inbox(client).json()
    assert remaining["total"] == 0
    assert all(a["asset_id"] != asset_id for a in remaining["assets"])

    readback = client.get(f"/api/asset-registry/assets/{asset_id}", headers=OWNER_A)
    assert readback.status_code == 200, readback.text
    record = readback.json()["asset"]
    assert record["project_id"] == project_id
    assert project_id in record["project_ids"]


def test_move_cas_stale_version_conflicts(client):
    asset_id = _register_asset("c4-owner-a", "ast-c4-c1", "并发改动.png")
    project_id = _create_project(client)
    asset_version = _inbox_asset_version(client, asset_id)

    conflict = client.post(MOVE.format(asset_id=asset_id), headers=OWNER_A,
                           json={"project_id": project_id, "expected_asset_version": asset_version + 99})
    assert conflict.status_code == 409, conflict.text
    detail = _detail(conflict)
    assert detail["code"] == "VERSION_CONFLICT"
    assert detail["current_version"] == asset_version

    assert [a["asset_id"] for a in _inbox(client).json()["assets"]] == [asset_id]


def test_move_per_asset_cas_ignores_other_assets_changes(client):
    """按资产 CAS：其他资产变动推高库 revision，不影响本资产按 asset_version 移动成功。"""
    asset_a = _register_asset("c4-owner-a", "ast-c4-cas-a", "资产A.png")
    _register_asset("c4-owner-a", "ast-c4-cas-b", "资产B.png")
    project_id = _create_project(client)
    asset_version = _inbox_asset_version(client, asset_a)
    revision = _inbox(client).json()["revision"]

    # 只改资产 B（走真实更新接口）：库 revision 变化，资产 A 的 version 不变。
    updated = client.patch("/api/asset-registry/assets/ast-c4-cas-b", headers=OWNER_A,
                           json={"name": "资产B改名.png"})
    assert updated.status_code == 200, updated.text
    assert _inbox(client).json()["revision"] > revision
    assert _inbox_asset_version(client, asset_a) == asset_version

    moved = client.post(MOVE.format(asset_id=asset_a), headers=OWNER_A,
                        json={"project_id": project_id, "expected_asset_version": asset_version})
    assert moved.status_code == 200, moved.text
    assert moved.json()["moved"] is True and moved.json()["project_id"] == project_id


def test_move_cas_conflict_when_asset_changed_no_owner_backfill(client):
    """资产自身被改动后按旧 asset_version 移动：409 VERSION_CONFLICT，无回填也无归属。"""
    from gw.asset_registry import repository as asset_repo

    ownerless_id = _import_ownerless(client, "资产自身变更.png")
    project_id = _create_project(client, "冲突认领项目", headers=ADMIN)
    asset_version = _inbox_asset_version(client, ownerless_id, headers=ADMIN)

    # 只改资产 A 自身：item version 自增，按旧 asset_version 移动必须冲突。
    asset_repo.update_asset(ownerless_id, {"name": "无主资产被改.png"})
    conflict = client.post(MOVE.format(asset_id=ownerless_id), headers=ADMIN,
                           json={"project_id": project_id, "expected_asset_version": asset_version})
    assert conflict.status_code == 409, conflict.text
    detail = _detail(conflict)
    assert detail["code"] == "VERSION_CONFLICT"
    assert detail["asset_id"] == ownerless_id
    assert detail["current_version"] == asset_version + 1

    # 整笔不落盘：不回填 workflow_owner，也不写项目归属，资产仍在收件箱。
    record = client.get(f"/api/asset-registry/assets/{ownerless_id}", headers=ADMIN).json()["asset"]
    assert not record.get("metadata", {}).get("workflow_owner")
    assert not record.get("project_id") and not record.get("project_ids")
    assert [a["asset_id"] for a in _inbox(client, headers=ADMIN).json()["assets"]] == [ownerless_id]


def test_move_legacy_expected_version_still_works_per_asset(client):
    """旧字段 expected_version 兼容：新字段缺省时按同一资产级版本语义生效。"""
    asset_id = _register_asset("c4-owner-a", "ast-c4-legacy", "旧字段.png")
    project_id = _create_project(client)
    asset_version = _inbox_asset_version(client, asset_id)

    # 旧字段的过期值同样触发资产级 409。
    stale = client.post(MOVE.format(asset_id=asset_id), headers=OWNER_A,
                        json={"project_id": project_id, "expected_version": asset_version + 5})
    assert stale.status_code == 409, stale.text
    assert _detail(stale)["code"] == "VERSION_CONFLICT"

    # 新旧字段同传以 expected_asset_version 为准：旧字段给过期值也成功。
    moved = client.post(MOVE.format(asset_id=asset_id), headers=OWNER_A,
                        json={"project_id": project_id, "expected_asset_version": asset_version,
                              "expected_version": asset_version + 5})
    assert moved.status_code == 200, moved.text
    assert moved.json()["moved"] is True and moved.json()["project_id"] == project_id


@pytest.mark.parametrize("payload", [
    {"expected_version": 1},
    {"expected_asset_version": 1},
    {"project_id": "prj-0002", "expected_version": 0},
    {"project_id": "prj-0002", "expected_version": "x"},
    {"project_id": "prj-0002", "expected_asset_version": 0},
    {"project_id": "prj-0002", "expected_asset_version": "x"},
    {"project_id": "", "expected_version": 1},
])
def test_move_validates_required_body(client, payload):
    asset_id = _register_asset("c4-owner-a", "ast-c4-v1", "参数校验.png")
    response = client.post(MOVE.format(asset_id=asset_id), headers=OWNER_A, json=payload)
    assert response.status_code == 400, response.text
    assert _detail(response)["code"] == "INVALID_REQUEST"
    assert _inbox(client).json()["total"] == 1


def test_move_rejects_already_assigned_assets(client):
    project_id = _create_project(client)
    projection = _register_asset("c4-owner-a", "ast-c4-p1", "工作流投影.png", project_id=project_id)
    linked = _register_asset("c4-owner-a", "ast-c4-p2", "项目关联.png")
    linked_result = client.post(f"/api/asset-registry/projects/{project_id}/assets", headers=OWNER_A,
                                json={"asset_ids": [linked]})
    assert linked_result.status_code == 200, linked_result.text

    # 两种"已归属"形态都要拒绝：工作流投影（单数 project_id）与项目关联（project_ids）。
    for asset_id, expected_single, expected_ids in (
        (projection, project_id, None),
        (linked, None, [project_id]),
    ):
        response = client.post(MOVE.format(asset_id=asset_id), headers=OWNER_A,
                               json={"project_id": project_id, "expected_version": 1})
        assert response.status_code == 409, response.text
        assert _detail(response)["code"] == "ASSET_ALREADY_ASSIGNED"
        # 拒绝即无副作用：归属保持原样，不被改写到新项目。
        record = client.get(f"/api/asset-registry/assets/{asset_id}", headers=OWNER_A).json()["asset"]
        assert record.get("project_id") == expected_single
        assert record.get("project_ids") == expected_ids


# ---------------------------------------------------------------------------
# 权限：只有资产所有者且对目标项目有写权限才能移动
# ---------------------------------------------------------------------------


def test_other_account_cannot_see_or_move_foreign_assets(client):
    asset_id = _register_asset("c4-owner-a", "ast-c4-x1", "他人不可见.png")
    b_project = _create_project(client, "B的项目", headers=OWNER_B)

    assert _inbox(client, headers=OWNER_B).json()["assets"] == []

    # B 对自己的项目有写权限，但不是资产所有者：同形 404 不泄露他人资产存在性。
    stolen = client.post(MOVE.format(asset_id=asset_id), headers=OWNER_B,
                         json={"project_id": b_project, "expected_version": 1})
    assert stolen.status_code == 404, stolen.text
    assert _detail(stolen)["code"] == "ASSET_NOT_FOUND"
    assert [a["asset_id"] for a in _inbox(client).json()["assets"]] == [asset_id]


def test_move_requires_write_access_on_target_project(client):
    asset_id = _register_asset("c4-owner-a", "ast-c4-w1", "跨项目移动.png")
    b_project = _create_project(client, "B的项目", headers=OWNER_B)
    _register_asset("c4-owner-a", "ast-c4-w2", "只读角色资产.png")

    # 资产所有者（编辑者）对他人项目没有写权限：与不存在同形 404，不泄露项目存在性。
    denied = client.post(MOVE.format(asset_id=asset_id), headers=OWNER_A,
                         json={"project_id": b_project, "expected_version": 1})
    assert denied.status_code == 404, denied.text
    assert _detail(denied)["code"] == "PROJECT_NOT_FOUND"
    assert [a["asset_id"] for a in _inbox(client).json()["assets"]] == ["ast-c4-w1", "ast-c4-w2"]

    # 只读角色即使拥有资产也没有写权限。
    readonly = client.post(MOVE.format(asset_id="ast-c4-w2"), headers=READONLY_A,
                           json={"project_id": b_project, "expected_version": 1})
    assert readonly.status_code == 403, readonly.text


def test_governance_role_can_move_own_asset_into_any_active_project(client):
    """治理角色对任意活跃项目有写权限；资产仍必须是自己所有。"""
    asset_id = _register_asset("c4-owner-a", "ast-c4-g1", "治理移动.png")
    foreign_project = _create_project(client)
    asset_version = _inbox_asset_version(client, asset_id)
    moved = client.post(MOVE.format(asset_id=asset_id), headers=GOVERNOR_A,
                        json={"project_id": foreign_project, "expected_asset_version": asset_version})
    assert moved.status_code == 200, moved.text
    assert moved.json()["asset"]["project_id"] == foreign_project
    assert _inbox(client).json()["total"] == 0


def test_move_rejects_archived_or_trashed_target_project(client):
    asset_id = _register_asset("c4-owner-a", "ast-c4-r1", "只读项目.png")
    project_id = _create_project(client, "即将归档")
    archived = client.request("DELETE", f"/api/asset-registry/projects/{project_id}",
                              headers=GOVERNOR_A, json={"expected_version": 1})
    assert archived.status_code == 200, archived.text

    response = client.post(MOVE.format(asset_id=asset_id), headers=OWNER_A,
                           json={"project_id": project_id, "expected_version": 1})
    assert response.status_code == 403, response.text
    assert _detail(response)["code"] == "PROJECT_READ_ONLY"
    assert [a["asset_id"] for a in _inbox(client).json()["assets"]] == [asset_id]


# ---------------------------------------------------------------------------
# 绝不自动归属
# ---------------------------------------------------------------------------


def test_no_auto_assignment_ever(client):
    """注册、列举收件箱都不改变归属，也不创建或猜测项目。"""
    asset_id = _register_asset("c4-owner-a", "ast-c4-n1", "永不自动归属.png")
    projects_before = client.get("/api/asset-registry/projects", headers=OWNER_A).json()["projects"]

    assert _inbox(client).status_code == 200
    assert _inbox(client).json()["total"] == 1

    record = client.get(f"/api/asset-registry/assets/{asset_id}", headers=OWNER_A).json()["asset"]
    assert not record.get("project_id")
    assert not record.get("project_ids")

    projects_after = client.get("/api/asset-registry/projects", headers=OWNER_A).json()["projects"]
    assert projects_after == projects_before


# ---------------------------------------------------------------------------
# 只读盘点工具：数量、ID 清单、只读、JSON 输出
# ---------------------------------------------------------------------------


def _load_inventory_module():
    spec = importlib.util.spec_from_file_location("unassigned_inbox_inventory", INVENTORY_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inventory_counts_unassigned_assets_and_is_read_only(client):
    from gw.asset_registry import repository as asset_repo

    _register_asset("c4-owner-a", "ast-c4-i1", "盘点1.png")
    _register_asset("c4-owner-a", "ast-c4-i2", "盘点2.png", project_id="prj-0002")
    _register_asset("c4-owner-b", "ast-c4-i3", "盘点3.png")
    imported = client.post("/api/asset-registry/assets/import", headers=OWNER_A,
                           json={"items": [{"name": "无主导入.png", "kind": "image"}]})
    ownerless_id = imported.json()["asset_ids"][0]

    before = copy.deepcopy(asset_repo.state().read())
    module = _load_inventory_module()
    result = module.inventory_unassigned_assets()
    after = asset_repo.state().read()

    assert after == before, "盘点绝不能修改注册表数据"
    assert result["read_only"] is True
    assert result["total_assets"] == 4
    assert result["unassigned_count"] == 3
    assert result["unassigned_asset_ids"] == sorted(["ast-c4-i1", "ast-c4-i3", ownerless_id])
    assert result["assigned_count"] == 1
    assert result["unassigned_with_owner_count"] == 2
    assert result["unassigned_ownerless_count"] == 1
    assert result["revision"] == before["revision"]


def test_inventory_main_outputs_json(client):
    import io
    from contextlib import redirect_stdout

    _register_asset("c4-owner-a", "ast-c4-j1", "主入口.png")
    module = _load_inventory_module()
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = module.main()
    assert code == 0
    payload = json.loads(buffer.getvalue())
    assert payload["unassigned_asset_ids"] == ["ast-c4-j1"]
    assert payload["unassigned_count"] == 1


def test_inventory_on_empty_registry(client):
    module = _load_inventory_module()
    result = module.inventory_unassigned_assets()
    assert result["unassigned_count"] == 0
    assert result["unassigned_asset_ids"] == []
    assert result["total_assets"] == 0
