"""C4-AC7/AC8 项目显式读授权（owner 决定模型）契约测试。

覆盖口径（对齐 agent_tasks 规格）：
1. 撤销后目标用户立即 403，且项目从两条列表路由消失；
2. 被撤销用户创建的分享链接立即失效（公开 meta/access 404）；
3. 其他用户的同类分享不受影响；
4. governor/admin 被撤销后仍可经全局角色读取；
5. 非 owner 非管理员（editor/reviewer/非owner governor）管理读授权一律 403；
6. legacy 项目（无记录）默认对所有已认证用户可读（显式撤销除外）；
   新建项目为 explicit，授权前他人不可读，授权后可读；
7. 匿名一律 401；审计与读取视图只出现 owner_key 12 位前缀，不落明文主体。
"""

from __future__ import annotations

import hashlib
import json
import re

from fastapi.testclient import TestClient

OWNER_TOKEN = "c4-owner-token"
OTHER_TOKEN = "c4-other-token"
THIRD_TOKEN = "c4-third-token"
GOV_TOKEN = "c4-gov-token"
ADMIN_TOKEN = "c4-admin-token"
READONLY_TOKEN = "c4-readonly-token"

OWNER = {"Authorization": "Bearer " + OWNER_TOKEN, "X-User-Role": "editor"}
OTHER = {"Authorization": "Bearer " + OTHER_TOKEN, "X-User-Role": "editor"}
THIRD = {"Authorization": "Bearer " + THIRD_TOKEN, "X-User-Role": "editor"}
GOV = {"Authorization": "Bearer " + GOV_TOKEN, "X-User-Role": "governor"}
ADMIN = {"Authorization": "Bearer " + ADMIN_TOKEN, "X-User-Role": "admin"}
READONLY = {"Authorization": "Bearer " + READONLY_TOKEN, "X-User-Role": "readonly"}

GRANTS_BASE = "/api/asset-registry/projects/{pid}/read-grants"


def local_subject(token: str) -> str:
    """local 认证模式下 subject 由服务端按 Bearer 摘要生成（见 core/auth.py）。"""
    return "local:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


def _create_project(client: TestClient, name: str = "C4 读授权项目") -> str:
    response = client.post(
        "/api/asset-registry/projects", headers=OWNER,
        json={"name": name, "project_type": "film"},
    )
    assert response.status_code == 201, response.text
    return response.json()["project"]["project_id"]


def _create_asset(client: TestClient, name: str = "c4-review.png") -> str:
    response = client.post(
        "/api/asset-registry/assets/import", headers=OWNER,
        json={"items": [{"name": name, "kind": "image"}]},
    )
    assert response.status_code == 200, response.text
    return response.json()["asset_ids"][0]


def _link_asset(client: TestClient, project_id: str, asset_id: str) -> None:
    response = client.post(
        f"/api/asset-registry/projects/{project_id}/assets", headers=OWNER,
        json={"asset_ids": [asset_id]},
    )
    assert response.status_code == 200, response.text


def _share(client: TestClient, asset_id: str, headers: dict) -> str:
    response = client.post(
        "/api/asset-reviews/shares", headers=headers,
        json={"asset_ids": [asset_id], "title": "C4 分享"},
    )
    assert response.status_code == 201, response.text
    return response.json()["token"]


def _grant(client: TestClient, project_id: str, token: str, headers: dict = OWNER):
    return client.post(
        GRANTS_BASE.format(pid=project_id), headers=headers,
        json={"subject": local_subject(token)},
    )


def _revoke(client: TestClient, project_id: str, token: str, headers: dict = OWNER):
    return client.post(
        GRANTS_BASE.format(pid=project_id) + "/revoke", headers=headers,
        json={"subject": local_subject(token)},
    )


def _service():
    from gw.api import routes_projects
    return routes_projects.default_projects_service


def _make_legacy(project_id: str) -> None:
    """模拟迁移前的存量项目：删除其 read_access 记录（=> legacy_open）。"""
    service = _service()
    with service._operation() as (root, state, _):
        state.setdefault("read_access", {}).pop(project_id, None)
        service._commit_state(root, state)


def _audit() -> list:
    service = _service()
    with service._operation() as (_, state, __):
        return list(state.get("read_audit") or [])


def _ids(payload: dict) -> list:
    return [item["project_id"] for item in payload.get("projects") or []]


# ---------------------------------------------------------------------------
# 1. 撤销立即生效：详情 403 + 列表过滤
# ---------------------------------------------------------------------------

def test_revoke_immediately_blocks_detail_and_both_lists(client: TestClient):
    project_id = _create_project(client)

    # 新建项目默认 explicit：未授权的已认证用户不可读。
    blocked = client.get(f"/api/projects/{project_id}", headers=OTHER)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "PROJECT_READ_FORBIDDEN"

    granted = _grant(client, project_id, OTHER_TOKEN)
    assert granted.status_code == 200, granted.text
    assert client.get(f"/api/projects/{project_id}", headers=OTHER).status_code == 200
    assert project_id in _ids(client.get("/api/projects", headers=OTHER).json())
    assert project_id in _ids(client.get("/api/asset-registry/projects", headers=OTHER).json())

    revoked = _revoke(client, project_id, OTHER_TOKEN)
    assert revoked.status_code == 200, revoked.text
    blocked_again = client.get(f"/api/projects/{project_id}", headers=OTHER)
    assert blocked_again.status_code == 403
    assert blocked_again.json()["detail"]["code"] == "PROJECT_READ_FORBIDDEN"
    assert project_id not in _ids(client.get("/api/projects", headers=OTHER).json())
    assert project_id not in _ids(client.get("/api/asset-registry/projects", headers=OTHER).json())
    # owner 始终可见
    assert project_id in _ids(client.get("/api/projects", headers=OWNER).json())


# ---------------------------------------------------------------------------
# 2/3. 撤销只失效被撤销者创建的分享；他人分享不受影响
# ---------------------------------------------------------------------------

def test_revocation_invalidates_only_revoked_creator_share_links(client: TestClient):
    project_id = _create_project(client)
    asset_id = _create_asset(client)
    _link_asset(client, project_id, asset_id)
    assert _grant(client, project_id, OTHER_TOKEN).status_code == 200
    assert _grant(client, project_id, THIRD_TOKEN).status_code == 200

    token_other = _share(client, asset_id, OTHER)
    token_third = _share(client, asset_id, THIRD)
    assert client.get(f"/api/public/shares/{token_other}").status_code == 200
    assert client.get(f"/api/public/shares/{token_third}").status_code == 200

    assert _revoke(client, project_id, OTHER_TOKEN).status_code == 200

    revoked_meta = client.get(f"/api/public/shares/{token_other}")
    assert revoked_meta.status_code == 404
    assert revoked_meta.json()["detail"]["code"] == "SHARE_NOT_FOUND"
    revoked_access = client.post(f"/api/public/shares/{token_other}/access", json={})
    assert revoked_access.status_code == 404
    assert revoked_access.json()["detail"]["code"] == "SHARE_NOT_FOUND"

    # 其他用户的分享照常可用。
    assert client.get(f"/api/public/shares/{token_third}").status_code == 200
    third_access = client.post(f"/api/public/shares/{token_third}/access", json={})
    assert third_access.status_code == 200, third_access.text


def test_share_survives_until_creator_loses_any_project_read(client: TestClient):
    """素材归属多个项目时，任一所属项目失去读权限即 404；恢复授权后可再用。"""
    project_a = _create_project(client, "C4 项目 A")
    project_b = _create_project(client, "C4 项目 B")
    asset_id = _create_asset(client)
    _link_asset(client, project_a, asset_id)
    _link_asset(client, project_b, asset_id)
    assert _grant(client, project_a, OTHER_TOKEN).status_code == 200
    assert _grant(client, project_b, OTHER_TOKEN).status_code == 200
    token = _share(client, asset_id, OTHER)
    assert client.get(f"/api/public/shares/{token}").status_code == 200

    assert _revoke(client, project_b, OTHER_TOKEN).status_code == 200
    assert client.get(f"/api/public/shares/{token}").status_code == 404

    assert _grant(client, project_b, OTHER_TOKEN).status_code == 200
    assert client.get(f"/api/public/shares/{token}").status_code == 200


def test_unlinked_asset_share_not_affected_by_grants(client: TestClient):
    """未关联任何项目的素材分享不参与读授权判定（legacy 行为保持）。"""
    asset_id = _create_asset(client)
    token = _share(client, asset_id, OTHER)
    assert client.get(f"/api/public/shares/{token}").status_code == 200


# ---------------------------------------------------------------------------
# 4. governor/admin 撤销不影响全局角色读取
# ---------------------------------------------------------------------------

def test_governor_and_admin_keep_read_after_revocation(client: TestClient):
    project_id = _create_project(client)
    # explicit 项目：治理角色经全局角色即可读。
    assert client.get(f"/api/projects/{project_id}", headers=GOV).status_code == 200
    assert client.get(f"/api/projects/{project_id}", headers=ADMIN).status_code == 200

    assert _revoke(client, project_id, GOV_TOKEN).status_code == 200
    assert _revoke(client, project_id, ADMIN_TOKEN).status_code == 200

    assert client.get(f"/api/projects/{project_id}", headers=GOV).status_code == 200
    assert client.get(f"/api/projects/{project_id}", headers=ADMIN).status_code == 200
    assert client.get(f"/api/projects/{project_id}", headers=OTHER).status_code == 403

    view = client.get(GRANTS_BASE.format(pid=project_id), headers=OWNER).json()
    gov_prefix = hashlib.sha256(("local_dev\0" + local_subject(GOV_TOKEN)).encode("utf-8")).hexdigest()[:12]
    assert gov_prefix in view["revoked"]
    assert view["mode"] == "explicit"


# ---------------------------------------------------------------------------
# 5. 只有 owner 与全局 admin 可管理读授权
# ---------------------------------------------------------------------------

def test_only_owner_and_global_admin_manage_read_grants(client: TestClient):
    project_id = _create_project(client)
    subject = {"subject": local_subject(THIRD_TOKEN)}
    for headers in (OTHER, GOV, READONLY):
        assert client.get(GRANTS_BASE.format(pid=project_id), headers=headers).status_code == 403
        assert client.post(GRANTS_BASE.format(pid=project_id), headers=headers, json=subject).status_code == 403
        assert client.post(GRANTS_BASE.format(pid=project_id) + "/revoke", headers=headers, json=subject).status_code == 403
        assert client.post(GRANTS_BASE.format(pid=project_id) + "/explicit", headers=headers).status_code == 403

    # owner 与全局 admin（非 owner）都可读视图并执行授权。
    assert client.get(GRANTS_BASE.format(pid=project_id), headers=ADMIN).status_code == 200
    assert _grant(client, project_id, THIRD_TOKEN, headers=ADMIN).status_code == 200
    assert client.get(f"/api/projects/{project_id}", headers=THIRD).status_code == 200
    assert _revoke(client, project_id, THIRD_TOKEN, headers=ADMIN).status_code == 200
    assert client.get(f"/api/projects/{project_id}", headers=THIRD).status_code == 403


# ---------------------------------------------------------------------------
# 6. legacy 默认可读；explicit 单向切换；owner 不可被 grant/revoke；撤销幂等
# ---------------------------------------------------------------------------

def test_legacy_project_open_by_default_and_revocable(client: TestClient):
    legacy = _create_project(client, "C4 存量项目")
    _make_legacy(legacy)
    view = client.get(GRANTS_BASE.format(pid=legacy), headers=ADMIN)
    assert view.status_code == 200, view.text
    assert view.json()["mode"] == "legacy_open"
    assert client.get(f"/api/projects/{legacy}", headers=OTHER).status_code == 200

    assert _revoke(client, legacy, OTHER_TOKEN, headers=ADMIN).status_code == 200
    assert client.get(f"/api/projects/{legacy}", headers=OTHER).status_code == 403
    assert _grant(client, legacy, OTHER_TOKEN, headers=ADMIN).status_code == 200
    assert client.get(f"/api/projects/{legacy}", headers=OTHER).status_code == 200


def test_legacy_project_switches_to_explicit_one_way(client: TestClient):
    legacy = _create_project(client, "C4 存量项目 2")
    _make_legacy(legacy)
    assert client.get(f"/api/projects/{legacy}", headers=THIRD).status_code == 200
    switched = client.post(GRANTS_BASE.format(pid=legacy) + "/explicit", headers=ADMIN)
    assert switched.status_code == 200, switched.text
    assert switched.json()["mode"] == "explicit"
    # 切换后未授权用户立即失去读取（无切换回 legacy 的路径）。
    assert client.get(f"/api/projects/{legacy}", headers=THIRD).status_code == 403
    again = client.post(GRANTS_BASE.format(pid=legacy) + "/explicit", headers=ADMIN)
    assert again.status_code == 200
    assert client.get(f"/api/projects/{legacy}", headers=THIRD).status_code == 403


def test_owner_cannot_be_granted_or_revoked_and_revoke_is_idempotent(client: TestClient):
    project_id = _create_project(client)
    owner_self = _revoke(client, project_id, OWNER_TOKEN)
    assert owner_self.status_code == 400
    assert owner_self.json()["detail"]["code"] == "INVALID_REQUEST"
    assert _grant(client, project_id, OWNER_TOKEN).status_code == 400

    assert _revoke(client, project_id, OTHER_TOKEN).status_code == 200
    first = client.get(GRANTS_BASE.format(pid=project_id), headers=OWNER).json()
    assert _revoke(client, project_id, OTHER_TOKEN).status_code == 200
    second = client.get(GRANTS_BASE.format(pid=project_id), headers=OWNER).json()
    assert second["revoked"] == first["revoked"]
    assert second["version"] == first["version"]
    assert client.get(f"/api/projects/{project_id}", headers=OTHER).status_code == 403


def test_unknown_project_returns_404_on_read_grant_routes(client: TestClient):
    assert client.get(GRANTS_BASE.format(pid="prj-missing"), headers=ADMIN).status_code == 404
    missing = _grant(client, "prj-missing", OTHER_TOKEN, headers=ADMIN)
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "PROJECT_NOT_FOUND"


# ---------------------------------------------------------------------------
# 7. 匿名一律 401；审计与视图只含脱敏 owner_key 前缀
# ---------------------------------------------------------------------------

def test_anonymous_gets_401_and_audit_is_redacted(client: TestClient):
    project_id = _create_project(client)
    assert client.get(f"/api/projects/{project_id}").status_code == 401
    assert client.get("/api/projects").status_code == 401
    assert client.get(GRANTS_BASE.format(pid=project_id)).status_code == 401
    assert client.post(GRANTS_BASE.format(pid=project_id), json={"subject": "x"}).status_code == 401
    assert client.post(GRANTS_BASE.format(pid=project_id) + "/revoke", json={"subject": "x"}).status_code == 401
    assert client.post(GRANTS_BASE.format(pid=project_id) + "/explicit").status_code == 401

    assert _grant(client, project_id, OTHER_TOKEN).status_code == 200
    assert _revoke(client, project_id, OTHER_TOKEN).status_code == 200
    assert client.post(GRANTS_BASE.format(pid=project_id) + "/explicit", headers=OWNER).status_code == 200

    legacy = _create_project(client, "C4 审计存量项目")
    _make_legacy(legacy)
    assert client.post(GRANTS_BASE.format(pid=legacy) + "/explicit", headers=OWNER).status_code == 200
    audit = _audit()
    assert audit, "grant/revoke/explicit 应留下审计记录"
    blob = json.dumps(audit, ensure_ascii=False)
    for token in (OWNER_TOKEN, OTHER_TOKEN):
        assert token not in blob
        assert local_subject(token) not in blob
    for entry in audit:
        assert set(entry) == {"event", "project_id", "actor", "target", "at"}
        assert entry["event"] in {"project.read_granted", "project.read_revoked", "project.read_mode_explicit"}
        assert re.fullmatch(r"[0-9a-f]{12}", entry["actor"])
        assert entry["target"] is None or re.fullmatch(r"[0-9a-f]{12}", entry["target"])
    events = [entry["event"] for entry in audit]
    assert "project.read_granted" in events and "project.read_revoked" in events
    assert "project.read_mode_explicit" in events

    view = client.get(GRANTS_BASE.format(pid=project_id), headers=OWNER).json()
    for key in view["grants"] + view["revoked"]:
        assert re.fullmatch(r"[0-9a-f]{12}", key)
    assert set(view) == {"project_id", "mode", "grants", "revoked", "version"}


def test_read_grants_view_roundtrip_after_grant(client: TestClient):
    project_id = _create_project(client)
    assert _grant(client, project_id, OTHER_TOKEN).status_code == 200
    view = client.get(GRANTS_BASE.format(pid=project_id), headers=OWNER).json()
    assert view["project_id"] == project_id
    assert view["mode"] == "explicit"
    assert len(view["grants"]) == 1 and len(view["revoked"]) == 0
    # 授权键为完整 owner_key 的 12 位前缀，无法还原明文主体。
    expected_prefix = hashlib.sha256(
        ("local_dev\0" + local_subject(OTHER_TOKEN)).encode("utf-8")
    ).hexdigest()[:12]
    assert view["grants"] == [expected_prefix]


def test_asset_registry_project_list_requires_login(client: TestClient):
    """秦先生裁定：GET /api/asset-registry/projects 匿名 401；登录后按读授权过滤。"""
    project_id = _create_project(client, "C4 列表登录")
    for url in ("/api/asset-registry/projects", "/api/asset-registry/projects?archived=false"):
        anon = client.get(url)
        assert anon.status_code == 401, anon.text
        assert "projects" not in anon.json()
    owner_list = client.get("/api/asset-registry/projects?archived=false", headers=OWNER)
    assert owner_list.status_code == 200
    assert project_id in _ids(owner_list.json())
    other_list = client.get("/api/asset-registry/projects?archived=false", headers=OTHER)
    assert other_list.status_code == 200
    assert project_id not in _ids(other_list.json())
    assert project_id in _ids(client.get("/api/asset-registry/projects", headers=ADMIN).json())
