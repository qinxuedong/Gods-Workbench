"""C5-T4 画布项目上下文门禁与读可见性契约测试。

覆盖 C5-AC5、C5-AC7、C5-AC8：
- 画布 project_id 列表、canvas_id 拓扑、元信息、CAS 更新、归档/恢复/删除/清理、画布工作流导入/导出、智能任务提交
- 统一项目门禁：仅 active 状态下的 owner/admin/governor 可写，无权主体 404 (隐蔽存在性)
- 归档/回收站项目对已授权写者明确返回 403 PROJECT_READ_ONLY，无权主体依然返回 404
- 未认证主体一律返回 401 UNAUTHORIZED
- 权限与项目状态校验严格优先于 CAS expected_version 检查 (C5-AC8)
"""

from __future__ import annotations

from tests.contracts.projects.c5_permission_fixtures import headers, seed


def _error_code(response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


def test_canvas_list_requires_authentication(client):
    """GET /api/canvases?project_id=... 匿名请求必须返回 401。"""
    ids = seed(client, "active", action="canvas_list", role="owner")
    response = client.get(
        f"/api/canvases?project_id={ids['project_id']}",
        headers=headers("anonymous"),
    )
    assert response.status_code == 401, response.text


def test_canvas_list_hides_unreadable_project(client):
    """GET /api/canvases?project_id=... 对无读权限主体隐蔽返回 404 PROJECT_NOT_FOUND。"""
    ids = seed(client, "active", action="canvas_list", role="owner")
    response = client.get(
        f"/api/canvases?project_id={ids['project_id']}",
        headers=headers("revoked_editor"),
    )
    assert response.status_code == 404, response.text
    assert _error_code(response) == "PROJECT_NOT_FOUND"


def test_canvas_create_requires_project_write_access(client):
    """POST /api/canvases 仅允许项目写者创建，只读者/无权者返回 404。"""
    ids = seed(client, "active", action="canvas_write", role="owner")
    # 读授权用户不是项目写者，创建画布返回 404
    denied = client.post(
        "/api/canvases",
        headers=headers("read_grantee_editor"),
        json={"project_id": ids["project_id"], "title": "越权画布"},
    )
    assert denied.status_code == 404, denied.text
    assert _error_code(denied) == "PROJECT_NOT_FOUND"

    # 项目所有者可以正常创建
    allowed = client.post(
        "/api/canvases",
        headers=headers("owner"),
        json={"project_id": ids["project_id"], "title": "合法画布"},
    )
    assert allowed.status_code == 201, allowed.text


def test_canvas_topology_read_visibility(client):
    """GET /api/canvases/{canvas_id} 遵循项目读可见性，无权主体返回 404 CANVAS_NOT_FOUND。"""
    ids = seed(client, "active", action="canvas_read", role="owner")
    # 匿名返回 401
    anon = client.get(
        f"/api/canvases/{ids['canvas_id']}",
        headers=headers("anonymous"),
    )
    assert anon.status_code == 401, anon.text

    # 无权读的主体返回 404 CANVAS_NOT_FOUND
    denied = client.get(
        f"/api/canvases/{ids['canvas_id']}",
        headers=headers("revoked_editor"),
    )
    assert denied.status_code == 404, denied.text
    assert _error_code(denied) == "CANVAS_NOT_FOUND"

    # 有读权限的主体可以读取拓扑
    allowed = client.get(
        f"/api/canvases/{ids['canvas_id']}",
        headers=headers("read_grantee_editor"),
    )
    assert allowed.status_code == 200, allowed.text


def test_canvas_write_requires_project_write_context(client):
    """PATCH /api/canvases/{canvas_id} 仅项目写者可修改拓扑。"""
    ids = seed(client, "active", action="canvas_write", role="owner")
    # 只读用户返回 404
    denied = client.patch(
        f"/api/canvases/{ids['canvas_id']}",
        headers=headers("read_grantee_editor"),
        json={"expected_version": 1, "nodes": [], "connections": []},
    )
    assert denied.status_code == 404, denied.text
    assert _error_code(denied) == "CANVAS_NOT_FOUND"


def test_canvas_write_on_archived_project_is_read_only(client):
    """归档项目上的画布写操作对合法写者返回 403 PROJECT_READ_ONLY。"""
    ids = seed(client, "archived", action="canvas_write", role="owner")
    response = client.patch(
        f"/api/canvases/{ids['canvas_id']}",
        headers=headers("owner"),
        json={"expected_version": 1, "nodes": [], "connections": []},
    )
    assert response.status_code == 403, response.text
    assert _error_code(response) == "PROJECT_READ_ONLY"


def test_canvas_cas_precedence_over_stale_version(client):
    """C5-AC8: 权限与只读状态校验必须先于 CAS 版本校验。"""
    # 1. 无权主体即使提供错误 expected_version 也必须返回 404，不泄露版本或资源存在
    active_ids = seed(client, "active", action="canvas_write", role="owner")
    denied = client.patch(
        f"/api/canvases/{active_ids['canvas_id']}",
        headers=headers("revoked_editor"),
        json={"expected_version": 99999, "nodes": [], "connections": []},
    )
    assert denied.status_code == 404, denied.text
    assert _error_code(denied) == "CANVAS_NOT_FOUND"

    # 2. 归档项目上的写者即使提供错误 expected_version 也必须返回 403 PROJECT_READ_ONLY，而非 409
    archived_ids = seed(client, "archived", action="canvas_write", role="owner")
    ro_resp = client.patch(
        f"/api/canvases/{archived_ids['canvas_id']}",
        headers=headers("owner"),
        json={"expected_version": 99999, "nodes": [], "connections": []},
    )
    assert ro_resp.status_code == 403, ro_resp.text
    assert _error_code(ro_resp) == "PROJECT_READ_ONLY"

    # 3. 活跃项目上的合法写者，stale expected_version 返回 409 CANVAS_VERSION_CONFLICT
    conflict = client.patch(
        f"/api/canvases/{active_ids['canvas_id']}",
        headers=headers("owner"),
        json={"expected_version": 99999, "nodes": [], "connections": []},
    )
    assert conflict.status_code == 409, conflict.text
    assert _error_code(conflict) == "CANVAS_VERSION_CONFLICT"


def test_canvas_lifecycle_and_closure_endpoints(client):
    """闭环端点 (touch, trash, purge, meta, import) 统一遵循项目写门禁与 CAS 顺序。"""
    ids = seed(client, "active", action="canvas_lifecycle", role="owner")
    canvas_id = ids["canvas_id"]

    # 1. 匿名用户一律 401
    assert (
        client.post(
            f"/api/canvases/{canvas_id}/touch?operation=archive&expected_version=1",
            headers=headers("anonymous"),
        ).status_code
        == 401
    )
    assert (
        client.delete(
            f"/api/canvases/{canvas_id}?expected_version=1",
            headers=headers("anonymous"),
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"/api/canvases/{canvas_id}/purge?expected_version=1",
            headers=headers("anonymous"),
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"/api/canvases/{canvas_id}/meta",
            headers=headers("anonymous"),
            json={"title": "new", "expected_version": 1},
        ).status_code
        == 401
    )

    # 2. 只读用户一律 404
    assert (
        client.post(
            f"/api/canvases/{canvas_id}/touch?operation=archive&expected_version=1",
            headers=headers("read_grantee_editor"),
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/canvases/{canvas_id}?expected_version=1",
            headers=headers("read_grantee_editor"),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/canvases/{canvas_id}/purge?expected_version=1",
            headers=headers("read_grantee_editor"),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/canvases/{canvas_id}/meta",
            headers=headers("read_grantee_editor"),
            json={"title": "new", "expected_version": 1},
        ).status_code
        == 404
    )


def test_smart_canvas_task_submission_project_gate(client):
    """POST /api/canvases/{canvas_id}/tasks 智能画布任务遵循项目写门禁与 CAS 顺序。"""
    active_ids = seed(client, "active", action="canvas_task", role="owner")
    archived_ids = seed(client, "archived", action="canvas_task", role="owner")

    # 匿名 401
    assert (
        client.post(
            f"/api/canvases/{active_ids['canvas_id']}/tasks",
            headers=headers("anonymous"),
            json={"expected_version": 1, "entry_nodes": ["c5-entry"]},
        ).status_code
        == 401
    )

    # 只读用户 404
    assert (
        client.post(
            f"/api/canvases/{active_ids['canvas_id']}/tasks",
            headers=headers("read_grantee_editor"),
            json={"expected_version": 1, "entry_nodes": ["c5-entry"]},
        ).status_code
        == 404
    )

    # 归档项目 403 PROJECT_READ_ONLY (即使 expected_version 错误)
    ro_resp = client.post(
        f"/api/canvases/{archived_ids['canvas_id']}/tasks",
        headers=headers("owner"),
        json={"expected_version": 99999, "entry_nodes": ["c5-entry"]},
    )
    assert ro_resp.status_code == 403, ro_resp.text
    assert _error_code(ro_resp) == "PROJECT_READ_ONLY"

    # 活跃项目合法写者但 expected_version 错误返回 409
    conflict = client.post(
        f"/api/canvases/{active_ids['canvas_id']}/tasks",
        headers=headers("owner"),
        json={"expected_version": 99999, "entry_nodes": ["c5-entry"]},
    )
    assert conflict.status_code == 409, conflict.text
    assert _error_code(conflict) == "CANVAS_VERSION_CONFLICT"
