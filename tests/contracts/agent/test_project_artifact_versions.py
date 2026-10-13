"""C3 AC1/AC2：按项目只读列出 Agent 产物精确版本（项目页“查看 vNNN”入口的数据源）。"""
from __future__ import annotations

import asyncio

from test_agent_integration import Harness, _create_payload, host_agent  # noqa: F401  (复用夹具)


def _seed_version(integration, run_id: str, label: str, content: str):
    from gw.agent_runtime.models import ArtifactVersionInput, Identifier

    async def seed():
        return await integration.service.artifacts.create_version(ArtifactVersionInput(
            artifact_id=Identifier(f"artifact-{run_id[:8]}-{label}"),
            parent_version_id=None,
            content=content,
            metadata={"run_id": run_id, "runtime_stage": "script"},
            source_refs=[],
            idempotency_key=Identifier(f"c3-project-artifact-{run_id[:8]}-{label}"),
        ))

    return asyncio.run(seed())


def _db_snapshot(integration):
    with integration.database.connect() as db:
        return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("runs", "events", "artifacts", "host_agent_bindings")}


def test_project_artifacts_lists_exact_versions_read_only(host_agent: Harness):
    client = host_agent.client
    created = client.post("/api/agent/runs", json=_create_payload(host_agent))
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]
    url = f"/api/agent/projects/{host_agent.project_id}/artifacts"

    empty = client.get(url)
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == [] and empty.json()["data_status"] == "ok"

    artifact = _seed_version(host_agent.integration, run_id, "one", "第一版剧本草稿")
    before = _db_snapshot(host_agent.integration)
    listed = client.get(url)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["project_id"] == host_agent.project_id
    assert body["total"] == 1 and body["truncated"] is False
    item = body["items"][0]
    assert item["run_id"] == run_id
    assert item["version_id"] == artifact.version_id.root
    assert item["revision"] == artifact.revision.root
    assert item["version_label"] == f"v{artifact.revision.root:03d}"
    assert item["content_hash"] == artifact.content_hash.root
    assert item["review_id"] is None and item["export_uri"] is None
    # 只读：读取前后账本不变，不调用模型。
    assert client.get(url).json() == body
    assert _db_snapshot(host_agent.integration) == before
    assert host_agent.gateway.calls == []
    # 与按运行读取的版本目录一致（同一精确版本、同一哈希）。
    per_run = client.get(f"/api/agent/runs/{run_id}/artifacts").json()["artifacts"]
    assert [(a["version_id"], a["content_hash"]) for a in per_run] == [(item["version_id"], item["content_hash"])]


def test_project_artifacts_limit_and_unknown_project(host_agent: Harness):
    client = host_agent.client
    run_id = client.post("/api/agent/runs", json=_create_payload(host_agent)).json()["run_id"]
    for label in ("a", "b", "c"):
        _seed_version(host_agent.integration, run_id, label, f"内容 {label}")
    page = client.get(f"/api/agent/projects/{host_agent.project_id}/artifacts?limit=2").json()
    assert len(page["items"]) == 2 and page["total"] == 3
    assert page["truncated"] is True and page["data_gaps"] == ["artifact_list_truncated"]
    missing = client.get("/api/agent/projects/project-does-not-exist/artifacts")
    assert missing.status_code == 404


def _url(project_id: str, query: str = "") -> str:
    return f"/api/agent/projects/{project_id}/artifacts{query}"


def _fresh_client(host_agent: Harness, port: int, token: str | None = None):
    """新建一个不继承夹具 Cookie 的客户端；token 给定时以 Cookie 会话登录。"""
    from fastapi.testclient import TestClient

    from gw.api.app import create_app
    from gw.core.session import session_cookie_name

    client = TestClient(
        create_app(agent_integration_factory=host_agent.integration),
        base_url="http://localhost",
        client=("127.0.0.1", port),
        headers={"Origin": "http://localhost"},
    )
    client.__enter__()  # 运行 lifespan，挂上 agent integration
    if token is not None:
        client.cookies.set(session_cookie_name(), token)
    return client


def _create_local_user(username: str, password: str, role: str = "editor") -> str:
    from gw.core import local_accounts

    salt = f"{username}-salt".encode()
    with local_accounts.database() as db:
        db.execute(
            "INSERT INTO local_users(user_id,username,salt,password_hash,role) VALUES(?,?,?,?,?)",
            (f"usr-{username}", username, salt, local_accounts.password_hash(password, salt), role),
        )
    _, token = local_accounts.login(username, password, f"{username}-client")
    return token


def _seeded_run(host_agent: Harness, project_id: str | None = None, key: str = "a") -> str:
    overrides = {"idempotency_key": f"c3-artifact-acl-{key}", "request_id": f"c3-artifact-acl-req-{key}"}
    if project_id is not None:
        overrides["project_id"] = project_id
    created = host_agent.client.post("/api/agent/runs", json=_create_payload(host_agent, **overrides))
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]
    _seed_version(host_agent.integration, run_id, key, f"项目 {key} 的剧本")
    return run_id


def test_project_artifacts_cookie_session_lists_own_run(host_agent: Harness):
    """真正走 Cookie 会话（夹具客户端只带 Cookie、不带 Authorization）。"""
    run_id = _seeded_run(host_agent)
    response = host_agent.client.get(_url(host_agent.project_id))
    assert response.status_code == 200, response.text
    assert [item["run_id"] for item in response.json()["items"]] == [run_id]


def test_project_artifacts_reject_bearer_without_cookie_session(host_agent: Harness):
    """只有 Bearer、没有 Cookie 会话 → 拒绝（Bearer 不能代替主机 Cookie 会话）。"""
    client = _fresh_client(host_agent, 50101)
    try:
        response = client.get(_url(host_agent.project_id), headers={"Authorization": "Bearer someone-else"})
        assert response.status_code == 401, response.text
    finally:
        client.__exit__(None, None, None)


def test_project_artifacts_reject_no_credentials(host_agent: Harness):
    _seeded_run(host_agent)
    client = _fresh_client(host_agent, 50102)
    try:
        response = client.get(_url(host_agent.project_id))
        assert response.status_code == 401, response.text
        assert "run_id" not in response.text
    finally:
        client.__exit__(None, None, None)


def test_project_artifacts_reject_expired_session(host_agent: Harness):
    import hashlib

    from gw.core import local_accounts

    _seeded_run(host_agent)
    with local_accounts.database() as db:
        db.execute("UPDATE local_sessions SET expires_at=? WHERE token_hash=?",
                   (1.0, hashlib.sha256(host_agent.session_token.encode()).hexdigest()))
    response = host_agent.client.get(_url(host_agent.project_id))
    assert response.status_code == 401, response.text


def test_project_artifacts_reject_revoked_account(host_agent: Harness):
    """账号被移除（本地账号模型里的“撤销”：删除用户，会话级联删除）→ 拒绝。"""
    from gw.core import local_accounts

    _seeded_run(host_agent)
    with local_accounts.database() as db:
        db.execute("DELETE FROM local_users WHERE user_id=?", (host_agent.principal["user_id"],))
    response = host_agent.client.get(_url(host_agent.project_id))
    assert response.status_code in (401, 403), response.text
    assert "run_id" not in response.text


def test_project_artifacts_role_downgrade_to_readonly_still_reads(host_agent: Harness):
    """角色降为 readonly 仍可读：本接口只读（edit_required=False），已知角色都有读权限；
    本地账号库的 role 受 CHECK 约束，无法降为“无角色”，所以没有“角色撤销 → 403”的可构造用例。"""
    from gw.core import local_accounts

    run_id = _seeded_run(host_agent)
    with local_accounts.database() as db:
        db.execute("UPDATE local_users SET role='readonly' WHERE user_id=?", (host_agent.principal["user_id"],))
    response = host_agent.client.get(_url(host_agent.project_id))
    assert response.status_code == 200, response.text
    assert [item["run_id"] for item in response.json()["items"]] == [run_id]


def test_project_artifacts_hidden_from_another_user(host_agent: Harness):
    from gw.projects_hub.models import ProjectCreateRequest, ProjectType
    from gw.projects_hub.service import owner_key_for_context
    from gw.core.auth import AuthContext

    run_id = _seeded_run(host_agent)
    token = _create_local_user("artifactoutsider", "Outsider2026")
    outsider_auth = AuthContext(role="editor", subject="usr-artifactoutsider", mode="local_account",
                                identity_domain="local_account")
    own = host_agent.integration.projects.create_project(
        ProjectCreateRequest(name="外人自己的项目", project_type=ProjectType.FILM),
        owner_key=owner_key_for_context(outsider_auth),
    )
    client = _fresh_client(host_agent, 50103, token)
    try:
        foreign = client.get(_url(host_agent.project_id))
        assert foreign.status_code == 404, foreign.text
        assert run_id not in foreign.text
        mine = client.get(_url(own.project_id))
        assert mine.status_code == 200, mine.text
        assert mine.json()["items"] == [] and run_id not in mine.text
    finally:
        client.__exit__(None, None, None)


def test_project_artifacts_do_not_leak_across_same_users_projects(host_agent: Harness):
    from gw.projects_hub.models import ProjectCreateRequest, ProjectType
    from gw.projects_hub.service import owner_key_for_context
    from gw.core.auth import AuthContext

    owner_auth = AuthContext(role=host_agent.principal["role"], subject=host_agent.principal["user_id"],
                             mode="local_account", identity_domain="local_account")
    other = host_agent.integration.projects.create_project(
        ProjectCreateRequest(name="同一用户的另一个项目", project_type=ProjectType.FILM),
        owner_key=owner_key_for_context(owner_auth),
    )
    run_a = _seeded_run(host_agent, key="a")
    run_b = _seeded_run(host_agent, project_id=other.project_id, key="b")
    a = host_agent.client.get(_url(host_agent.project_id)).json()
    b = host_agent.client.get(_url(other.project_id)).json()
    assert {item["run_id"] for item in a["items"]} == {run_a}
    assert {item["run_id"] for item in b["items"]} == {run_b}


def test_project_artifacts_limit_boundaries(host_agent: Harness):
    """limit 越界被参数校验拒绝。注意：本应用把 FastAPI 的校验错误统一映射为 400 INVALID_REQUEST
    （不是 FastAPI 默认的 422），这里按实际契约断言。"""
    client = host_agent.client
    _seeded_run(host_agent)
    for bad in ("0", "501"):
        response = client.get(_url(host_agent.project_id, f"?limit={bad}"))
        assert response.status_code == 400, (bad, response.text)
        detail = response.json()["detail"]
        assert detail["code"] == "INVALID_REQUEST"
        assert detail["errors"][0]["loc"] == ["query", "limit"]
    ok = client.get(_url(host_agent.project_id, "?limit=500"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["truncated"] is False and ok.json()["total"] == 1


def test_project_artifacts_export_uri_opens_the_exact_approved_version(host_agent: Harness):
    """“查看 vNNN”打开的就是这一精确版本：export_uri 可直接导出，且与审核目录一致。"""
    from gw.agent_runtime.models import StageId
    from test_agent_integration import _create_export_test_run, _seed_export_test_records

    run_id = _create_export_test_run(host_agent, "approval")
    review_ids = _seed_export_test_records(
        host_agent.integration, run_id, mode="approval",
        records=[{"label": "approved-script", "stage": StageId.full_script.value, "approval": "approve"},
                 {"label": "unapproved-storyboard", "stage": StageId.storyboard_text.value}],
        current_labels=["approved-script", "unapproved-storyboard"], released_labels=[],
    )
    body = host_agent.client.get(f"/api/agent/projects/{host_agent.project_id}/artifacts").json()
    by_review = {item["review_id"]: item for item in body["items"]}
    approved = by_review[review_ids["approved-script"]]
    assert approved["export_formats"] == ["markdown"]
    assert approved["export_uri"].startswith(f"/api/agent/runs/{run_id}/export?format=markdown")
    exported = host_agent.client.get(approved["export_uri"])
    assert exported.status_code == 200, exported.text
    unapproved = by_review[review_ids["unapproved-storyboard"]]
    assert unapproved["export_formats"] == [] and unapproved["export_uri"] is None
    reviews = host_agent.client.get(f"/api/agent/runs/{run_id}/reviews").json()["reviews"]
    exact = {item["review"]["review_id"]: item["review"]["artifact"]["content_hash"] for item in reviews}
    assert approved["content_hash"] == exact[review_ids["approved-script"]]
