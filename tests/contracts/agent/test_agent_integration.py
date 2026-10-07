"""Host HTTP contract tests for the same-process durable Agent integration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient


@dataclass
class FakeGateway:
    """Network-free typed fake; tests assert that read/create paths never invoke it."""

    calls: list[Any]

    async def describe_capabilities(self, model_ref):
        from gw.agent_runtime.models import MessageRole, ModelCapabilities

        return ModelCapabilities(
            model_ref=model_ref,
            supported_roles=list(MessageRole),
            structured_output=True,
            tool_calls=False,
            streaming=False,
            multimodal=False,
            lookup=False,
            cancellation=False,
            usage_reporting=False,
            max_context_tokens=None,
        )

    async def invoke(self, request):
        from gw.agent_runtime.models import FinishReason, ModelInvocationResult

        self.calls.append(request)
        return ModelInvocationResult(
            content="synthetic test response",
            tool_calls=[],
            finish_reason=FinishReason.stop,
            upstream_request_id=None,
            usage=None,
            usage_unavailable_reason=None,
        )


@dataclass
class Harness:
    client: TestClient
    integration: Any
    project_id: str
    principal: Mapping[str, Any]
    session_token: str
    gateway: FakeGateway


@pytest.fixture
def host_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # AGENTS.md: isolate all runtime roots before importing the application.
    data_root = tmp_path / "data"
    auth_db = tmp_path / "auth" / "auth.sqlite3"
    video_root = tmp_path / "video"
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(data_root))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(auth_db))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(video_root))
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_DISABLE_AGENT_INTEGRATION", "0")

    from gw.api.agent_integration import build_agent_integration
    from gw.api.app import create_app
    from gw.core import local_accounts, session
    from gw.core.auth import AuthContext
    from gw.core.runtime_paths import resolve_runtime_paths
    from gw.projects_hub.models import ProjectCreateRequest, ProjectType
    from gw.projects_hub.service import ProjectsService, owner_key_for_context

    session.reset_stores()
    principal, session_token = local_accounts.create_first_admin("agentowner", "AgentTest2026")
    auth = AuthContext(
        role=principal["role"],
        subject=principal["user_id"],
        mode="local_account",
        identity_domain="local_account",
    )
    projects = ProjectsService(seed_golden_fixture=False, persistent=True)
    project = projects.create_project(
        ProjectCreateRequest(name="Agent 集成测试项目", project_type=ProjectType.FILM),
        owner_key=owner_key_for_context(auth),
    )
    gateway = FakeGateway(calls=[])
    config: dict[str, Any] = {
        "configured": True,
        "default_provider_id": "fake-provider",
        "providers": [{"provider_id": "fake-provider", "configured": True, "models": ["fake-model"]}],
    }

    def model_resolver(provider_id: str | None, model: str | None) -> Mapping[str, str]:
        selected_provider = provider_id or "fake-provider"
        selected_model = model or "fake-model"
        if selected_provider != "fake-provider" or selected_model != "fake-model":
            raise ValueError("not in test allowlist")
        return {"provider_id": selected_provider, "model": selected_model}

    integration = build_agent_integration(
        gateway=gateway,
        runtime_paths=resolve_runtime_paths(),
        projects=projects,
        model_resolver=model_resolver,
        configuration_provider=lambda: config,
        start_worker=False,
    )
    # Exercise the supported already-constructed integration injection path.
    app = create_app(agent_integration_factory=integration)
    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
        headers={"Origin": "http://localhost"},
    ) as client:
        from gw.core.session import session_cookie_name

        client.cookies.set(session_cookie_name(), session_token)
        yield Harness(client, integration, project.project_id, principal, session_token, gateway)
    session.reset_stores()


def _create_payload(project_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "user_goal": "生成一段可审阅的剧本草稿",
        "provider_id": "fake-provider",
        "model": "fake-model",
        "mode": "approval",
        "idempotency_key": "agent-create-key-0001",
        "request_id": "agent-create-request-0001",
        **overrides,
    }


def _run_count(integration: Any) -> int:
    with integration.database.connect() as db:
        return int(db.execute("SELECT COUNT(*) FROM runs").fetchone()[0])


def test_agent_config_and_create_fail_closed_without_allowlisted_model(host_agent: Harness):
    integration = host_agent.integration
    integration.configuration_provider = lambda: {
        "configured": False,
        "default_provider_id": None,
        "providers": [],
    }

    config = host_agent.client.get("/api/agent/config")
    assert config.status_code == 200
    assert config.json()["ready"] is False
    assert config.json()["configured"] is False
    assert config.json()["providers"] == []

    response = host_agent.client.post(
        "/api/agent/runs",
        json=_create_payload(host_agent.project_id),
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AGENT_NOT_INTEGRATED"
    assert _run_count(integration) == 0
    with integration.database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM host_agent_create_keys").fetchone()[0] == 0
    assert host_agent.gateway.calls == []


def test_agent_cookie_routes_create_idempotently_and_page_events(host_agent: Harness):
    client = host_agent.client
    payload = _create_payload(host_agent.project_id)

    created = client.post("/api/agent/runs", json=payload)
    assert created.status_code == 202
    run = created.json()
    assert run["job_id"] == run["run_id"]
    assert run["project_id"] == host_agent.project_id
    assert run["version"] == 0
    assert run["status"] == "queued"
    assert run["poll_hint"] == f"/api/agent/runs/{run['job_id']}"
    assert run["cost_ledger"]["state"] == "unknown"

    duplicate = client.post("/api/agent/runs", json=payload)
    assert duplicate.status_code == 202
    assert duplicate.json()["run_id"] == run["run_id"]
    assert _run_count(host_agent.integration) == 1
    assert host_agent.gateway.calls == []

    conflicting = client.post(
        "/api/agent/runs",
        json=_create_payload(host_agent.project_id, user_goal="用途不同的第二个请求"),
    )
    assert conflicting.status_code == 409

    pause = client.post(
        f"/api/agent/runs/{run['job_id']}/pause",
        json={
            "expected_version": 0,
            "idempotency_key": "agent-pause-key-0001",
            "request_id": "agent-pause-request-0001",
        },
    )
    assert pause.status_code == 200
    assert pause.json()["status"] == "paused"

    resumed = client.post(
        f"/api/agent/runs/{run['job_id']}/resume",
        json={
            "expected_version": pause.json()["version"],
            "idempotency_key": "agent-resume-key-0002",
            "request_id": "agent-resume-request-0002",
        },
    )
    assert resumed.status_code == 200
    cancelled = client.post(
        f"/api/agent/runs/{run['job_id']}/cancel",
        json={
            "expected_version": resumed.json()["version"],
            "idempotency_key": "agent-cancel-key-0001",
            "request_id": "agent-cancel-request-0001",
        },
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    first_page = client.get(f"/api/agent/runs/{run['job_id']}/events?after_version=0&limit=1")
    assert first_page.status_code == 200
    first_data = first_page.json()
    assert first_data["events"]
    assert first_data["next_cursor"] is not None
    assert len({event["event_id"] for event in first_data["events"]}) == len(first_data["events"])
    cursor = first_data["cursor"]
    second_page = client.get(
        f"/api/agent/runs/{run['job_id']}/events?after_version=0&limit=1&cursor={cursor}"
    )
    assert second_page.status_code == 200
    second_data = second_page.json()
    second_ids = {event["event_id"] for event in second_data["events"]}
    first_ids = {event["event_id"] for event in first_data["events"]}
    assert second_ids
    assert not second_ids.intersection(first_ids)
    assert second_data["next_cursor"] is not None
    third_page = client.get(
        f"/api/agent/runs/{run['job_id']}/events?after_version=0&limit=1&cursor={second_data['cursor']}"
    )
    assert third_page.status_code == 200
    third_ids = {event["event_id"] for event in third_page.json()["events"]}
    assert third_ids
    assert not third_ids.intersection(first_ids | second_ids)

    for suffix, expected_key in (
        ("", "status"),
        ("/candidate", "candidate"),
        ("/reviews", "reviews"),
        ("/clarifications", "questions"),
        ("/artifacts", "artifacts"),
    ):
        response = client.get(f"/api/agent/runs/{run['job_id']}{suffix}")
        assert response.status_code == 200
        assert expected_key in response.json()

    invalid_cas = client.post(
        f"/api/agent/runs/{run['job_id']}/resume",
        json={
            "expected_version": 0,
            "idempotency_key": "agent-resume-key-0001",
            "request_id": "agent-resume-request-0001",
        },
    )
    assert invalid_cas.status_code == 409


def test_agent_routes_require_live_cookie_session_even_with_bearer(host_agent: Harness):
    from gw.api.app import create_app

    unauthenticated = TestClient(
        create_app(agent_integration_factory=host_agent.integration),
        base_url="http://localhost",
        client=("127.0.0.1", 50001),
    )
    try:
        response = unauthenticated.get(
            "/api/agent/config",
            headers={"Authorization": "Bearer local-test-token"},
        )
        assert response.status_code == 401
    finally:
        unauthenticated.close()


def test_app_lifespan_accepts_callable_integration_factory(host_agent: Harness):
    from gw.api.app import create_app
    from gw.core.session import session_cookie_name

    calls = []

    def factory():
        calls.append("called")
        return host_agent.integration

    app = create_app(agent_integration_factory=factory)
    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50002),
        headers={"Origin": "http://localhost"},
    ) as client:
        client.cookies.set(session_cookie_name(), host_agent.session_token)
        response = client.get("/api/agent/config")
        assert response.status_code == 200
        assert response.json()["ready"] is True
        assert app.state.agent_integration is host_agent.integration
    assert calls == ["called"]


def test_agent_write_contract_requires_cas_and_exact_review_binding(host_agent: Harness):
    blank_goal = host_agent.client.post(
        "/api/agent/runs",
        json=_create_payload(host_agent.project_id, user_goal="   "),
    )
    assert blank_goal.status_code == 400
    assert _run_count(host_agent.integration) == 0

    invalid_cursor = host_agent.client.get("/api/agent/runs/not-a-run/events?cursor=%20")
    assert invalid_cursor.status_code == 400

    missing_mutation = host_agent.client.post(
        "/api/agent/runs/not-a-run/pause",
        json={"idempotency_key": "agent-pause-key-0002", "request_id": "agent-pause-request-0002"},
    )
    assert missing_mutation.status_code == 400
    assert missing_mutation.json()["detail"]["code"] == "INVALID_REQUEST"

    missing_review_binding = host_agent.client.post(
        "/api/agent/runs/not-a-run/review",
        json={
            "decision": "revise",
            "reason": "补充角色动机",
            "expected_version": 0,
            "idempotency_key": "agent-review-key-0001",
            "request_id": "agent-review-request-0001",
        },
    )
    assert missing_review_binding.status_code == 400
    assert missing_review_binding.json()["detail"]["code"] == "INVALID_REQUEST"

    missing_question_binding = host_agent.client.post(
        "/api/agent/runs/not-a-run/clarifications",
        json={
            "answer": "确认",
            "expected_version": 0,
            "idempotency_key": "agent-answer-key-0001",
            "request_id": "agent-answer-request-0001",
        },
    )
    assert missing_question_binding.status_code == 400
    assert missing_question_binding.json()["detail"]["code"] == "INVALID_REQUEST"

