"""C5-T5 工作流项目上下文门禁契约。"""

from __future__ import annotations

from tests.contracts.projects.c5_permission_fixtures import headers, seed


def _error_code(response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


def test_workflow_project_read_is_shared_but_write_is_project_scoped(client):
    """项目读授权可跨主体读取工作流，写入仍需项目写权限。"""

    ids = seed(client, "active", action="workflow_write", role="owner")
    read = client.get(
        f"/api/god_workflow/documents/{ids['workflow_id']}",
        headers=headers("read_grantee_editor"),
    )
    assert read.status_code == 200, read.text

    write = client.put(
        f"/api/god_workflow/documents/{ids['workflow_id']}",
        headers=headers("read_grantee_editor"),
        json={
            "expected_version": ids["workflow_version"],
            "workflow": {"1": {"class_type": "LoadImage", "inputs": {}}},
        },
    )
    assert write.status_code == 404, write.text
    assert _error_code(write) in {"WORKFLOW_NOT_FOUND", "PROJECT_NOT_FOUND"}


def test_workflow_project_writer_sees_read_only_state_before_cas(client):
    """项目归档后，原写者写工作流得到 PROJECT_READ_ONLY。"""

    ids = seed(client, "archived", action="workflow_write", role="owner")
    response = client.put(
        f"/api/god_workflow/documents/{ids['workflow_id']}",
        headers=headers("owner"),
        json={
            "expected_version": 999,
            "workflow": {"1": {"class_type": "LoadImage", "inputs": {}}},
        },
    )
    assert response.status_code == 403, response.text
    assert _error_code(response) == "PROJECT_READ_ONLY"


def test_workflow_bridge_requires_an_authorized_project_context(client):
    """资产桥接只能在可写项目上下文中执行。"""

    ids = seed(client, "active", action="workflow_bridge", role="owner")
    response = client.post(
        "/api/god_workflow/asset-hub/reconcile",
        headers=headers("read_grantee_editor"),
        json={},
    )
    assert response.status_code == 404, response.text
    assert _error_code(response) in {"WORKFLOW_NOT_FOUND", "PROJECT_NOT_FOUND"}
