"""阶段门写权限必须跟随服务端当前账户角色。"""

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.api import routes_asset_registry, routes_projects
from gw.core.local_accounts import database
from gw.projects_hub import service as projects_service_module
from gw.projects_hub.service import ProjectsService


@pytest.mark.parametrize("demoted_role", ["readonly", "reviewer"])
def test_local_account_owner_role_downgrade_blocks_gate_write_before_cas(tmp_path, monkeypatch, demoted_role):
    """owner降为只读或reviewer后仍可读，但所有写入都先返回403。"""
    data_root = tmp_path / "data"
    monkeypatch.setenv("GW_DATA_DIR", str(data_root))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))

    projects = ProjectsService(seed_golden_fixture=False, persistent=True)
    monkeypatch.setattr(projects_service_module, "default_projects_service", projects)
    monkeypatch.setattr(routes_projects, "default_projects_service", projects)
    monkeypatch.setattr(routes_asset_registry, "default_projects_service", projects)

    with TestClient(
        create_app(),
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
        headers={"Origin": "http://localhost"},
    ) as client:
        setup = client.post(
            "/api/asset-auth/local/setup",
            json={"username": "gate_owner", "password": "Test2026"},
        )
        assert setup.status_code == 201, setup.text
        created = client.post(
            "/api/asset-registry/projects",
            json={
                "name": "角色降级阶段门",
                "project_type": "film",
                "client_request_id": "role-gate-owner-01",
                "gates": [{"code": "script", "name": "剧本审核"}],
            },
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["project"]["project_id"]
        listed = client.get(f"/api/asset-registry/projects/{project_id}/gates")
        assert listed.status_code == 200, listed.text
        gate_id = listed.json()["gates"][0]["gate_id"]

        # 账户角色变更落在认证真源；后续请求仍携带同一服务端会话。
        with database() as db:
            db.execute("UPDATE local_users SET role=?", (demoted_role,))

        readable = client.get(f"/api/asset-registry/project-gates/{gate_id}")
        assert readable.status_code == 200, readable.text
        assert readable.json()["gate"]["state"] == "pending"

        first_write = client.patch(
            f"/api/asset-registry/project-gates/{gate_id}",
            json={"state": "approved", "expected_version": 1},
        )
        same_state_write = client.patch(
            f"/api/asset-registry/project-gates/{gate_id}",
            json={"state": "pending", "expected_version": 1},
        )
        stale_cas_write = client.patch(
            f"/api/asset-registry/project-gates/{gate_id}",
            json={"state": "approved", "expected_version": 999},
        )
        for response in (first_write, same_state_write, stale_cas_write):
            assert response.status_code == 403, response.text
            assert response.json()["detail"]["code"] == "FORBIDDEN"

        unchanged = client.get(f"/api/asset-registry/project-gates/{gate_id}")
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()["gate"]["state"] == "pending"
        assert unchanged.json()["gate"]["version"] == 1
        assert unchanged.json()["gate"]["note"] is None
