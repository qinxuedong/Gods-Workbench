"""主应用实体母库 (Master Catalog) 路由挂载契约验证 (QIN-331).

验证 routes_entities 已正确注册到全局主应用 FastAPI 实例，
确保 /api/entities 及其别名路径不再返回 404。
"""

from fastapi.testclient import TestClient

from gw.api.app import create_app


def test_entities_routes_registered_in_openapi_schema() -> None:
    """验证主应用 OpenAPI 规范中包含实体母库的核心端点路径。"""
    app = create_app()
    openapi_paths = set(app.openapi().get("paths", {}).keys())

    expected_endpoints = {
        "/api/entities",
        "/api/entities/{entity_id}",
        "/api/entities/{entity_id}/archive",
    }
    missing = expected_endpoints - openapi_paths
    assert not missing, f"主应用 OpenAPI 路径中缺失实体母库端点: {missing}"


def test_entities_endpoint_resolves_and_does_not_404(monkeypatch, tmp_path) -> None:
    """验证通过真实主应用请求 /api/entities 不再返回 404。"""
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))

    app = create_app()
    with TestClient(app) as client:
        # 1. 匿名未认证访问：应返回 401 UNAUTHORIZED，绝不是 404 Not Found
        anon_res = client.get("/api/entities")
        assert anon_res.status_code == 401
        assert anon_res.json()["detail"]["code"] == "UNAUTHORIZED"

        # 2. 携带 editor 凭据访问 /api/entities：应正常命中端点并返回 200
        auth_headers = {"Authorization": "Bearer test-user", "X-User-Role": "editor"}
        response = client.get("/api/entities?project_id=proj_test", headers=auth_headers)
        assert response.status_code != 404, "主应用请求 /api/entities 意外返回 404 Not Found，路由未挂载！"
        assert response.status_code == 200
        assert "entities" in response.json()

        # 3. 别名路由 /api/master-catalog/entities 也应正常命中
        alias_res = client.get("/api/master-catalog/entities?project_id=proj_test", headers=auth_headers)
        assert alias_res.status_code != 404
        assert alias_res.status_code == 200
