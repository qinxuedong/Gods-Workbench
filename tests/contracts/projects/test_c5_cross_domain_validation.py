"""C5-T7 跨域完整验证。

这些测试把项目、资产、画布、工作流和任务入口放进同一条隔离链路，
验证资源回读仍携带同一项目上下文，并确认跨项目主体无法通过显式上下文
横向读取或写入。外部执行器保持关闭，任务只验证到本地 503 边界。
"""

from __future__ import annotations

from tests.contracts.projects.c5_permission_fixtures import headers, seed


def _error_code(response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


def _asset_items(response) -> list[dict[str, object]]:
    payload = response.json()
    return payload.get("assets", payload.get("items", []))


def test_c5_owner_cross_domain_flow_preserves_project_identity(client, monkeypatch):
    """C5-AC6/AC9：项目到资源、任务边界的回读保持同一项目上下文。"""

    ids = seed(client, "active")
    owner = headers("owner")
    project_id = str(ids["project_id"])

    project = client.get(f"/api/projects/{project_id}", headers=owner)
    assert project.status_code == 200, project.text
    assert project.json()["project"]["project_id"] == project_id

    assets = client.get(
        "/api/asset-registry/assets",
        headers=owner,
        params={"project_id": project_id},
    )
    assert assets.status_code == 200, assets.text
    assert any(item["asset_id"] == ids["asset_id"] for item in _asset_items(assets))

    canvas = client.get(f"/api/canvases/{ids['canvas_id']}", headers=owner)
    assert canvas.status_code == 200, canvas.text
    canvas_view = canvas.json().get("canvas", canvas.json())
    assert canvas_view["project_id"] == project_id

    workflow = client.get(
        f"/api/god_workflow/documents/{ids['workflow_id']}", headers=owner
    )
    assert workflow.status_code == 200, workflow.text
    workflow_view = workflow.json().get("workflow", workflow.json())
    source_context = workflow_view.get("metadata", {}).get("source_context", {})
    assert source_context["project_id"] == project_id
    assert source_context["canvas_id"] == ids["canvas_id"]

    # 本地执行器未配置时只到明确的 503 边界，不伪造 job_id 或远端副作用。
    monkeypatch.delenv("GW_COMFYUI_URL", raising=False)
    execution = client.post(
        "/api/god_workflow/tasks/execute",
        headers={**owner, "Idempotency-Key": "c5-t7-project-chain"},
        json={
            "workflow_id": ids["workflow_id"],
            "expected_version": ids["workflow_version"],
            "target": "local_comfy",
            "source_context": {"project_id": project_id, "canvas_id": ids["canvas_id"]},
        },
    )
    assert execution.status_code == 503, execution.text
    assert _error_code(execution) == "WORKFLOW_EXECUTOR_UNAVAILABLE"


def test_c5_cross_project_subject_cannot_traverse_explicit_context(client):
    """C5-AC3/AC4/AC5/AC7/AC8：跨项目主体在显式上下文入口统一失败关闭。"""

    ids = seed(client, "active", action="asset_list_all", role="cross_project_editor")
    cross_project_editor = headers("cross_project_editor")
    target_project_id = str(ids["project_id"])

    asset_list = client.get(
        "/api/asset-registry/assets",
        headers=cross_project_editor,
        params={"project_id": target_project_id},
    )
    assert asset_list.status_code == 404, asset_list.text
    assert _error_code(asset_list) == "PROJECT_NOT_FOUND"

    canvas_list = client.get(
        "/api/canvases",
        headers=cross_project_editor,
        params={"project_id": target_project_id},
    )
    assert canvas_list.status_code == 404, canvas_list.text
    assert _error_code(canvas_list) == "PROJECT_NOT_FOUND"

    workflow_read = client.get(
        f"/api/god_workflow/documents/{ids['workflow_id']}",
        headers=cross_project_editor,
    )
    assert workflow_read.status_code == 404, workflow_read.text

    project_write = client.patch(
        f"/api/asset-registry/projects/{target_project_id}",
        headers=cross_project_editor,
        json={"name": "cross-project-probe", "expected_version": 99999},
    )
    assert project_write.status_code == 404, project_write.text
    assert _error_code(project_write) == "PROJECT_NOT_FOUND"
