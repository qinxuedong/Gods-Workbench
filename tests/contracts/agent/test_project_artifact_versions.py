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


def test_project_artifacts_require_live_cookie_session(host_agent: Harness):
    client = host_agent.client
    client.cookies.clear()
    response = client.get(f"/api/agent/projects/{host_agent.project_id}/artifacts",
                          headers={"Authorization": "Bearer someone-else"})
    assert response.status_code in (401, 403)


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
