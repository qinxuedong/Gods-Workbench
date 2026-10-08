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
    profile_ids: Mapping[str, str]


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
    monkeypatch.setenv("GW_AGENT_MAX_MODEL_CALLS_PER_RUN", "2")

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
        profiles = integration.config_summary()["profiles"]
        profile_ids = {item["mode"]: item["config_snapshot_id"] for item in profiles}
        yield Harness(client, integration, project.project_id, principal, session_token, gateway, profile_ids)
    session.reset_stores()


def _create_payload(host_agent: Harness, **overrides: Any) -> dict[str, Any]:
    mode = overrides.get("mode", "approval")
    return {
        "project_id": host_agent.project_id,
        "user_goal": "生成一段可审阅的剧本草稿",
        "provider_id": "fake-provider",
        "model": "fake-model",
        "mode": mode,
        "config_snapshot_id": host_agent.profile_ids[mode],
        "input_artifact_refs": [],
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
        json=_create_payload(host_agent),
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AGENT_NOT_INTEGRATED"
    assert _run_count(integration) == 0
    with integration.database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM host_agent_create_keys").fetchone()[0] == 0
    assert host_agent.gateway.calls == []


def test_agent_config_exposes_stable_content_addressed_profiles(host_agent: Harness):
    with host_agent.integration.database.connect() as db:
        before = db.execute("SELECT COUNT(*) FROM host_agent_profiles").fetchone()[0]
    first = host_agent.client.get("/api/agent/config")
    assert first.status_code == 200
    assert first.json()["ready"] is True
    profiles = first.json()["profiles"]
    assert {(item["provider_id"], item["model"], item["mode"]) for item in profiles} == {
        ("fake-provider", "fake-model", "approval"),
        ("fake-provider", "fake-model", "automatic"),
    }
    for item in profiles:
        assert item["config_snapshot_id"].startswith("cfg:")
        assert item["version"] == item["config_snapshot_id"]
        assert set(item) >= {"config_snapshot_id", "version", "provider_id", "model", "mode"}
        assert "pass_score" not in item
        assert "stage_score_weights" not in item

    restarted = _build_secondary_integration(host_agent)
    after_restart = restarted.config_summary()
    assert {
        (item["config_snapshot_id"], item["provider_id"], item["model"], item["mode"])
        for item in after_restart["profiles"]
    } == {
        (item["config_snapshot_id"], item["provider_id"], item["model"], item["mode"])
        for item in profiles
    }
    with restarted.database.connect() as db:
        count = db.execute("SELECT COUNT(*) FROM host_agent_profiles").fetchone()[0]
    assert before == 0
    assert count == before
    assert host_agent.gateway.calls == []


def test_agent_create_requires_explicit_inputs_and_fails_closed_on_source_refs(host_agent: Harness):
    payload = _create_payload(host_agent)
    without_profile = dict(payload)
    without_profile.pop("config_snapshot_id")
    missing_profile = host_agent.client.post("/api/agent/runs", json=without_profile)
    assert missing_profile.status_code == 400
    assert missing_profile.json()["detail"]["code"] == "INVALID_REQUEST"

    without_refs = dict(payload)
    without_refs.pop("input_artifact_refs")
    missing_refs = host_agent.client.post("/api/agent/runs", json=without_refs)
    assert missing_refs.status_code == 400
    assert missing_refs.json()["detail"]["code"] == "INVALID_REQUEST"

    unsupported_refs = host_agent.client.post(
        "/api/agent/runs",
        json=_create_payload(
            host_agent,
            input_artifact_refs=["artifact:outside-project"],
            idempotency_key="agent-source-ref-rejected-key",
            request_id="agent-source-ref-rejected-request",
        ),
    )
    assert unsupported_refs.status_code == 400
    assert unsupported_refs.json()["detail"]["code"] == "AGENT_INPUT_ARTIFACT_REFS_UNSUPPORTED"
    assert _run_count(host_agent.integration) == 0
    with host_agent.integration.database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM host_agent_create_keys").fetchone()[0] == 0
    assert host_agent.gateway.calls == []


def test_agent_create_rejects_unknown_and_mismatched_profiles(host_agent: Harness):
    unknown = host_agent.client.post(
        "/api/agent/runs",
        json=_create_payload(host_agent, config_snapshot_id="cfg:client-invented"),
    )
    assert unknown.status_code == 400
    assert unknown.json()["detail"]["code"] == "AGENT_CONFIG_PROFILE_UNAVAILABLE"

    mismatched = host_agent.client.post(
        "/api/agent/runs",
        json=_create_payload(host_agent, config_snapshot_id=host_agent.profile_ids["automatic"]),
    )
    assert mismatched.status_code == 400
    assert mismatched.json()["detail"]["code"] == "AGENT_CONFIG_PROFILE_MISMATCH"
    assert _run_count(host_agent.integration) == 0
    with host_agent.integration.database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM host_agent_create_keys").fetchone()[0] == 0
    assert host_agent.gateway.calls == []


def test_agent_configuration_route_applies_only_current_profiles_to_paused_runs(host_agent: Harness):
    created = host_agent.client.post("/api/agent/runs", json=_create_payload(host_agent))
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]

    def apply(profile_id: str, expected_version: int, key: str):
        return host_agent.client.post(
            f"/api/agent/runs/{run_id}/configuration",
            json={
                "config_snapshot_id": profile_id,
                "expected_version": expected_version,
                "idempotency_key": key,
                "request_id": f"request:{key}",
            },
        )

    unknown = apply("cfg:client-invented", 0, "agent-config-unknown-key")
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "CAPABILITY_UNSUPPORTED"

    configured_provider = host_agent.integration.configuration_provider
    host_agent.integration.configuration_provider = lambda: {
        "configured": False,
        "default_provider_id": None,
        "providers": [],
    }
    expired = apply(host_agent.profile_ids["automatic"], 0, "agent-config-expired-key")
    assert expired.status_code == 422
    assert expired.json()["detail"]["code"] == "CAPABILITY_UNSUPPORTED"
    host_agent.integration.configuration_provider = configured_provider

    not_paused = apply(host_agent.profile_ids["automatic"], 0, "agent-config-running-key")
    assert not_paused.status_code == 409
    assert not_paused.json()["detail"]["code"] == "REVISION_CONFLICT"

    pause = host_agent.client.post(
        f"/api/agent/runs/{run_id}/pause",
        json={
            "expected_version": 0,
            "idempotency_key": "agent-config-pause-key",
            "request_id": "agent-config-pause-request",
        },
    )
    assert pause.status_code == 200
    applied = apply(host_agent.profile_ids["automatic"], pause.json()["version"], "agent-config-apply-key")
    assert applied.status_code == 200, applied.text
    run = applied.json()
    assert run["run_id"] == run_id
    assert run["job_id"] == host_agent.integration.job_id_for_run(run_id)
    assert run["status"] == "paused"
    assert run["version"] == pause.json()["version"] + 1
    assert run["state"] == "paused"
    assert run["agent_status"] == "paused"
    assert run["poll_hint"] == f"/api/jobs/{run['job_id']}"

    host_agent.integration.configuration_provider = lambda: {
        "configured": False,
        "default_provider_id": None,
        "providers": [],
    }
    replay = apply(host_agent.profile_ids["automatic"], pause.json()["version"], "agent-config-apply-key")
    assert replay.status_code == 200, replay.text
    assert replay.json()["version"] == run["version"]

    changed_replay = apply(host_agent.profile_ids["automatic"], run["version"], "agent-config-apply-key")
    assert changed_replay.status_code == 409
    assert changed_replay.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

    expired_new_mutation = apply(host_agent.profile_ids["automatic"], run["version"], "agent-config-expired-new-key")
    assert expired_new_mutation.status_code == 422
    assert expired_new_mutation.json()["detail"]["code"] == "CAPABILITY_UNSUPPORTED"
    host_agent.integration.configuration_provider = configured_provider
    assert host_agent.gateway.calls == []


def test_agent_cookie_routes_create_idempotently_and_page_events(host_agent: Harness):
    client = host_agent.client
    payload = _create_payload(host_agent)

    configuration = client.get("/api/agent/config")
    assert configuration.status_code == 200
    assert configuration.json()["ready"] is True
    assert configuration.json()["capabilities"]["max_model_calls_per_run"] == 2
    assert configuration.json()["capabilities"]["cost_state"] == "unknown"

    created = client.post("/api/agent/runs", json=payload)
    assert created.status_code == 202
    run = created.json()
    assert run["job_id"].startswith("job_agent_")
    assert run["job_id"] != run["run_id"]
    assert run["project_id"] == host_agent.project_id
    assert run["version"] == 0
    assert run["status"] == "queued"
    assert run["state"] == "accepted"
    assert run["poll_hint"] == f"/api/jobs/{run['job_id']}"
    assert run["agent_poll_hint"] == {
        "status_uri": f"/api/agent/runs/{run['run_id']}",
        "events_uri": f"/api/agent/runs/{run['run_id']}/events",
        "suggested_interval_ms": 2000,
    }
    assert run["cost_ledger"]["state"] == "unknown"

    duplicate = client.post("/api/agent/runs", json=payload)
    assert duplicate.status_code == 202
    assert duplicate.json()["run_id"] == run["run_id"]
    assert duplicate.json()["job_id"] == run["job_id"]
    assert duplicate.json()["poll_hint"] == run["poll_hint"]
    assert _run_count(host_agent.integration) == 1

    configured_provider = host_agent.integration.configuration_provider
    host_agent.integration.configuration_provider = lambda: {
        "configured": False,
        "default_provider_id": None,
        "providers": [],
    }
    retry_after_profile_expiry = client.post("/api/agent/runs", json=payload)
    assert retry_after_profile_expiry.status_code == 202
    assert retry_after_profile_expiry.json()["run_id"] == run["run_id"]
    host_agent.integration.configuration_provider = configured_provider

    conflicting = client.post(
        "/api/agent/runs",
        json=_create_payload(host_agent, user_goal="用途不同的第二个请求"),
    )
    assert conflicting.status_code == 409
    assert host_agent.gateway.calls == []

    pause = client.post(
        f"/api/agent/runs/{run['run_id']}/pause",
        json={
            "expected_version": 0,
            "idempotency_key": "agent-pause-key-0001",
            "request_id": "agent-pause-request-0001",
        },
    )
    assert pause.status_code == 200
    assert pause.json()["status"] == "paused"

    resumed = client.post(
        f"/api/agent/runs/{run['run_id']}/resume",
        json={
            "expected_version": pause.json()["version"],
            "idempotency_key": "agent-resume-key-0002",
            "request_id": "agent-resume-request-0002",
        },
    )
    assert resumed.status_code == 200
    cancelled = client.post(
        f"/api/agent/runs/{run['run_id']}/cancel",
        json={
            "expected_version": resumed.json()["version"],
            "idempotency_key": "agent-cancel-key-0001",
            "request_id": "agent-cancel-request-0001",
        },
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    first_page = client.get(f"/api/agent/runs/{run['run_id']}/events?after_version=0&limit=1")
    assert first_page.status_code == 200
    first_data = first_page.json()
    assert first_data["events"]
    assert first_data["next_cursor"] is not None
    assert len({event["event_id"] for event in first_data["events"]}) == len(first_data["events"])
    cursor = first_data["cursor"]
    second_page = client.get(
        f"/api/agent/runs/{run['run_id']}/events?after_version=0&limit=1&cursor={cursor}"
    )
    assert second_page.status_code == 200
    second_data = second_page.json()
    second_ids = {event["event_id"] for event in second_data["events"]}
    first_ids = {event["event_id"] for event in first_data["events"]}
    assert second_ids
    assert not second_ids.intersection(first_ids)
    assert second_data["next_cursor"] is not None
    third_page = client.get(
        f"/api/agent/runs/{run['run_id']}/events?after_version=0&limit=1&cursor={second_data['cursor']}"
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
        response = client.get(f"/api/agent/runs/{run['run_id']}{suffix}")
        assert response.status_code == 200
        assert expected_key in response.json()

    invalid_cas = client.post(
        f"/api/agent/runs/{run['run_id']}/resume",
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


def test_app_lifespan_cleans_up_when_agent_startup_fails(monkeypatch: pytest.MonkeyPatch):
    from gw.api import agent_integration
    from gw.api.app import create_app
    from gw.asset_registry import index_jobs

    calls = []

    async def fail_start(app):
        calls.append("start")
        raise RuntimeError("synthetic agent startup failure")

    async def stop(app):
        calls.append("stop")

    def shutdown_index_jobs():
        calls.append("shutdown_index_jobs")

    monkeypatch.setenv("GW_DISABLE_AGENT_INTEGRATION", "0")
    monkeypatch.setattr(agent_integration, "start_agent_integration", fail_start)
    monkeypatch.setattr(agent_integration, "stop_agent_integration", stop)
    monkeypatch.setattr(index_jobs, "shutdown", shutdown_index_jobs)

    with pytest.raises(RuntimeError, match="synthetic agent startup failure"):
        with TestClient(create_app()):
            pytest.fail("startup failure must prevent serving requests")

    assert calls == ["start", "stop", "shutdown_index_jobs"]


def test_app_lifespan_preserves_startup_error_when_stop_fails(monkeypatch: pytest.MonkeyPatch):
    from gw.api import agent_integration
    from gw.api.app import create_app
    from gw.asset_registry import index_jobs

    calls = []

    async def fail_start(app):
        calls.append("start")
        raise ValueError("synthetic startup failure")

    async def fail_stop(app):
        calls.append("stop")
        raise RuntimeError("synthetic cleanup failure")

    def shutdown_index_jobs():
        calls.append("shutdown_index_jobs")

    monkeypatch.setenv("GW_DISABLE_AGENT_INTEGRATION", "0")
    monkeypatch.setattr(agent_integration, "start_agent_integration", fail_start)
    monkeypatch.setattr(agent_integration, "stop_agent_integration", fail_stop)
    monkeypatch.setattr(index_jobs, "shutdown", shutdown_index_jobs)

    with pytest.raises(ValueError, match="synthetic startup failure") as raised:
        with TestClient(create_app()):
            pytest.fail("startup failure must prevent serving requests")

    assert calls == ["start", "stop", "shutdown_index_jobs"]
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert str(raised.value.__cause__) == "synthetic cleanup failure"


def test_agent_write_contract_requires_cas_and_exact_review_binding(host_agent: Harness):
    blank_goal = host_agent.client.post(
        "/api/agent/runs",
        json=_create_payload(host_agent, user_goal="   "),
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


def _episode_text_content(stage: str) -> str:
    import json

    payloads = {
        "full_script": {
            "source_brief_version_id": "brief-source-v1",
            "source_outline_version_id": "outline-source-v1",
            "title": "Contract Test Episode",
            "scenes": [{
                "scene_id": "scene-1",
                "title": "Opening",
                "time": "Day",
                "location": "Studio",
                "content": "A concise synthetic scene.",
            }],
        },
        "storyboard_text": {
            "source_full_script_version_id": "script-source-v1",
            "source_scene_script_version_id": "scene-script-source-v1",
            "shots": [{"shot_id": "shot-1", "scene_id": "scene-1", "description": "Wide shot."}],
        },
        "delivery_check": {
            "checked_artifacts": [{
                "stage": "full_script",
                "artifact_id": "script-artifact",
                "version_id": "script-version",
            }],
            "verified_requirements": ["Synthetic contract requirement."],
            "summary": "Synthetic delivery check.",
        },
    }
    return json.dumps({"schema_version": 1, "stage": stage, "content": payloads[stage]}, sort_keys=True)


def _create_export_test_run(host_agent: Harness, mode: str) -> str:
    import uuid

    suffix = uuid.uuid4().hex
    response = host_agent.client.post(
        "/api/agent/runs",
        json=_create_payload(
            host_agent,
            mode=mode,
            idempotency_key=f"agent-export-{mode}-run-{suffix}",
            request_id=f"agent-export-{mode}-request-{suffix}",
        ),
    )
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def _seed_export_test_records(
    integration: Any,
    run_id: str,
    *,
    mode: str,
    records: list[dict[str, Any]],
    current_labels: list[str],
    released_labels: list[str],
    duplicate_attempt_labels: tuple[str, ...] = (),
) -> dict[str, str]:
    import asyncio
    from datetime import datetime, timezone

    from gw.agent_runtime.events import make_event
    from gw.agent_runtime.models import (
        ApprovalDecision,
        ApprovalRecord,
        ArtifactVersionInput,
        AttemptKind,
        AttemptStatus,
        Identifier,
        ReviewRecord,
        RunStatus,
        StageAttempt,
        StageId,
        Version,
    )

    async def seed() -> dict[str, str]:
        service = integration.service
        repository = service.repository
        run_identifier = Identifier(run_id)
        run = await repository.read_run_by_id(run_identifier)
        assert run is not None
        state = await repository.read_runtime_state(run_identifier)
        assert state is not None
        state["mode"] = mode
        config = state["config_snapshot"]
        review_ids: dict[str, str] = {}
        artifacts: dict[str, Any] = {}
        attempts: list[Any] = []
        reviews: list[Any] = []
        approvals: list[Any] = []
        now = datetime.now(timezone.utc)

        for index, record in enumerate(records):
            label = record["label"]
            stage = StageId(record["stage"])
            content = _episode_text_content(stage.value)
            artifact = await service.artifacts.create_version(ArtifactVersionInput(
                artifact_id=Identifier(f"artifact-{run_id[:8]}-{label}"),
                parent_version_id=None,
                content=content,
                metadata={"run_id": run_id, "runtime_stage": stage.value},
                source_refs=[],
                idempotency_key=Identifier(f"export-artifact-key-{run_id[:8]}-{label}"),
            ))
            review_id = Identifier(f"review-{run_id[:8]}-{label}")
            score = float(record.get("score", 9.5))
            exact_score = record.get("exact_score", str(score))
            program_passed = record.get("program_passed", True)
            review = ReviewRecord.model_validate({
                "schema_version": 1,
                "review_id": review_id,
                "run_id": run_identifier,
                "artifact": artifact,
                "rule_version": config["scoring_rule_version"],
                "dimension_scores": {"quality": {"score": 9.5, "evidence": ["Synthetic evidence."]}},
                "program_validation_passed": program_passed,
                "program_checks": [{
                    "check_id": f"check-{label}",
                    "passed": program_passed,
                    "message": "Synthetic export eligibility check.",
                }],
                "overall_score": score,
                "overall_score_decimal": exact_score,
                "conclusion": record.get("conclusion", "pass"),
                "created_at": now,
            })
            attempt = StageAttempt(
                schema_version=1,
                stage_attempt_id=Identifier(f"attempt-{run_id[:8]}-{label}"),
                run_id=run_identifier,
                stage=stage,
                kind=AttemptKind.initial,
                attempt_number=index + 1,
                input_artifact_version_ids=[],
                status=AttemptStatus.succeeded,
                invocation_ids=[],
                output_artifact_version_id=artifact.version_id,
                review_id=review_id,
                created_at=now,
            )
            attempts.append(attempt)
            reviews.append(review)
            review_ids[label] = review_id.root
            artifacts[label] = artifact

            decision = record.get("approval")
            if decision:
                approvals.append(ApprovalRecord(
                    schema_version=1,
                    approval_id=Identifier(f"approval-{run_id[:8]}-{label}"),
                    run_id=run_identifier,
                    review_id=Identifier(record.get("approval_review_id", review_id.root)),
                    artifact_version_id=Identifier(record.get("approval_version_id", artifact.version_id.root)),
                    decision=ApprovalDecision(decision),
                    actor_id=Identifier("synthetic-reviewer"),
                    reason="Synthetic exact-version approval.",
                    expected_version=run.version,
                    created_at=now,
                ))

        for label in duplicate_attempt_labels:
            original = next(attempt for attempt in attempts if review_ids[label] == attempt.review_id.root)
            attempts.append(original.model_copy(update={"stage_attempt_id": Identifier(f"duplicate-{original.stage_attempt_id.root}")}))

        stage_by_label = {record["label"]: StageId(record["stage"]) for record in records}
        state["current_artifacts_by_stage"] = {
            stage_by_label[label].value: artifacts[label].version_id.root
            for label in current_labels
        }
        state["released_versions"] = [artifacts[label].version_id.root for label in released_labels]
        lease = await repository.claim_lease(run_identifier, service.owner_id, service.lease_seconds)
        assert lease is not None
        next_run = run.model_copy(update={
            "status": RunStatus.succeeded,
            "current_stage": None,
            "version": Version(run.version.root + 1),
        })
        try:
            await repository.commit_transition(
                run_identifier,
                run.version.root,
                next_run,
                [make_event(run_identifier, next_run.version.root, "test.export.fixture", "Seed exact export evidence for a contract test.")],
                {},
                lease=lease,
                runtime_state=state,
                attempts=attempts,
                reviews=reviews,
                approvals=approvals,
            )
        finally:
            await repository.release_lease(lease)
        return review_ids

    return asyncio.run(seed())


def test_reviews_report_automatic_formats_from_current_released_attempts(host_agent: Harness, monkeypatch: pytest.MonkeyPatch):
    from gw.agent_runtime.models import StageId

    run_id = _create_export_test_run(host_agent, "automatic")
    review_ids = _seed_export_test_records(
        host_agent.integration,
        run_id,
        mode="automatic",
        records=[
            {"label": "old-script", "stage": StageId.full_script.value},
            {"label": "script", "stage": StageId.full_script.value},
            {"label": "storyboard", "stage": StageId.storyboard_text.value},
            {"label": "delivery", "stage": StageId.delivery_check.value},
        ],
        current_labels=["script", "storyboard", "delivery"],
        # A released historical version still cannot advertise an export.
        released_labels=["old-script", "script", "storyboard", "delivery"],
    )
    serializer_calls: list[str] = []

    async def record_serializer_call(*args, **kwargs):
        serializer_calls.append("called")
        raise AssertionError("reviews must not generate complete export payloads")

    with monkeypatch.context() as export_probe:
        export_probe.setattr(host_agent.integration.service.artifact_exporter, "export", record_serializer_call)
        response = host_agent.client.get(f"/api/agent/runs/{run_id}/reviews")
    assert response.status_code == 200, response.text
    items = {item["review"]["review_id"]: item for item in response.json()["reviews"]}
    assert items[review_ids["script"]]["export_formats"] == ["markdown"]
    assert items[review_ids["storyboard"]]["export_formats"] == ["csv"]
    assert items[review_ids["delivery"]]["export_formats"] == ["exact-json"]
    assert items[review_ids["old-script"]]["export_formats"] == []
    assert items[review_ids["script"]]["approval"] is None
    assert serializer_calls == []

    for label, format_name in (
        ("script", "markdown"),
        ("storyboard", "csv"),
        ("delivery", "exact-json"),
    ):
        review = items[review_ids[label]]["review"]
        exported = host_agent.client.get(
            f"/api/agent/runs/{run_id}/export",
            params={
                "format": format_name,
                "review_id": review["review_id"],
                "artifact_version_id": review["artifact"]["version_id"],
            },
        )
        assert exported.status_code == 200, exported.text
        assert exported.content

    historical = items[review_ids["old-script"]]["review"]
    rejected = host_agent.client.get(
        f"/api/agent/runs/{run_id}/export",
        params={
            "format": "markdown",
            "review_id": historical["review_id"],
            "artifact_version_id": historical["artifact"]["version_id"],
        },
    )
    assert rejected.status_code != 200


def test_reviews_require_manual_exact_approval_and_program_pass(host_agent: Harness):
    from gw.agent_runtime.models import StageId

    run_id = _create_export_test_run(host_agent, "approval")
    review_ids = _seed_export_test_records(
        host_agent.integration,
        run_id,
        mode="approval",
        records=[
            {"label": "approved-script", "stage": StageId.full_script.value, "approval": "approve"},
            {"label": "unapproved-storyboard", "stage": StageId.storyboard_text.value},
            {
                "label": "invalid-delivery",
                "stage": StageId.delivery_check.value,
                "program_passed": False,
                "approval": "override",
            },
        ],
        current_labels=["approved-script", "unapproved-storyboard", "invalid-delivery"],
        # Manual approvals remain the release authority even if this list is empty.
        released_labels=[],
    )
    response = host_agent.client.get(f"/api/agent/runs/{run_id}/reviews")
    assert response.status_code == 200, response.text
    items = {item["review"]["review_id"]: item for item in response.json()["reviews"]}
    assert items[review_ids["approved-script"]]["export_formats"] == ["markdown"]
    assert items[review_ids["unapproved-storyboard"]]["export_formats"] == []
    assert items[review_ids["invalid-delivery"]]["export_formats"] == []

    missing_score_run = _create_export_test_run(host_agent, "approval")
    missing_score_ids = _seed_export_test_records(
        host_agent.integration,
        missing_score_run,
        mode="approval",
        records=[{
            "label": "missing-score-script",
            "stage": StageId.full_script.value,
            "exact_score": None,
            "approval": "approve",
        }],
        current_labels=["missing-score-script"],
        released_labels=[],
    )
    missing_score_response = host_agent.client.get(f"/api/agent/runs/{missing_score_run}/reviews")
    assert missing_score_response.status_code == 200, missing_score_response.text
    missing_score_item = next(
        item for item in missing_score_response.json()["reviews"]
        if item["review"]["review_id"] == missing_score_ids["missing-score-script"]
    )
    assert missing_score_item["export_formats"] == []


def test_reviews_require_automatic_release_and_unique_durable_stage_attempt(host_agent: Harness):
    from gw.agent_runtime.models import StageId

    unreleased_run = _create_export_test_run(host_agent, "automatic")
    unreleased_ids = _seed_export_test_records(
        host_agent.integration,
        unreleased_run,
        mode="automatic",
        records=[{"label": "unreleased-script", "stage": StageId.full_script.value}],
        current_labels=["unreleased-script"],
        released_labels=[],
    )
    unreleased_response = host_agent.client.get(f"/api/agent/runs/{unreleased_run}/reviews")
    assert unreleased_response.status_code == 200, unreleased_response.text
    unreleased_item = next(
        item for item in unreleased_response.json()["reviews"]
        if item["review"]["review_id"] == unreleased_ids["unreleased-script"]
    )
    assert unreleased_item["export_formats"] == []

    low_score_run = _create_export_test_run(host_agent, "automatic")
    low_score_ids = _seed_export_test_records(
        host_agent.integration,
        low_score_run,
        mode="automatic",
        records=[{
            "label": "low-score-script",
            "stage": StageId.full_script.value,
            "score": 8.0,
        }],
        current_labels=["low-score-script"],
        released_labels=["low-score-script"],
    )
    low_score_response = host_agent.client.get(f"/api/agent/runs/{low_score_run}/reviews")
    assert low_score_response.status_code == 200, low_score_response.text
    low_score_item = next(
        item for item in low_score_response.json()["reviews"]
        if item["review"]["review_id"] == low_score_ids["low-score-script"]
    )
    assert low_score_item["export_formats"] == []

    duplicate_run = _create_export_test_run(host_agent, "automatic")
    duplicate_ids = _seed_export_test_records(
        host_agent.integration,
        duplicate_run,
        mode="automatic",
        records=[{"label": "duplicate-script", "stage": StageId.full_script.value}],
        current_labels=["duplicate-script"],
        released_labels=["duplicate-script"],
        duplicate_attempt_labels=("duplicate-script",),
    )
    duplicate_response = host_agent.client.get(f"/api/agent/runs/{duplicate_run}/reviews")
    assert duplicate_response.status_code == 200, duplicate_response.text
    duplicate_item = next(
        item for item in duplicate_response.json()["reviews"]
        if item["review"]["review_id"] == duplicate_ids["duplicate-script"]
    )
    assert duplicate_item["export_formats"] == []


def _build_secondary_integration(host_agent: Harness):
    from gw.api.agent_integration import build_agent_integration

    current = host_agent.integration
    return build_agent_integration(
        gateway=host_agent.gateway,
        database=current.database,
        projects=current.projects,
        model_resolver=current.model_resolver,
        configuration_provider=current.configuration_provider,
        start_worker=False,
    )


def _client_with_host_session(integration: Any, token: str):
    from gw.api.app import create_app
    from gw.core.session import session_cookie_name

    client = TestClient(
        create_app(agent_integration_factory=integration),
        base_url="http://localhost",
        client=("127.0.0.1", 50002),
        headers={"Origin": "http://localhost"},
    )
    client.cookies.set(session_cookie_name(), token)
    return client


@pytest.mark.parametrize("limit_value", [None, "", "0", "not-a-number", "-1", " 2"])
def test_agent_requires_valid_server_call_limit_before_ready_or_queueing(
    host_agent: Harness,
    monkeypatch: pytest.MonkeyPatch,
    limit_value: str | None,
):
    if limit_value is None:
        monkeypatch.delenv("GW_AGENT_MAX_MODEL_CALLS_PER_RUN", raising=False)
    else:
        monkeypatch.setenv("GW_AGENT_MAX_MODEL_CALLS_PER_RUN", limit_value)

    integration = _build_secondary_integration(host_agent)
    with _client_with_host_session(integration, host_agent.session_token) as client:
        configuration = client.get("/api/agent/config")
        assert configuration.status_code == 200
        assert configuration.json()["ready"] is False
        assert configuration.json()["configured"] is False
        assert configuration.json()["reason"] == "model_call_limit_missing_or_invalid"

        response = client.post(
            "/api/agent/runs",
            json=_create_payload(
                host_agent,
                idempotency_key="agent-no-budget-create-key",
                request_id="agent-no-budget-create-request",
            ),
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "AGENT_NOT_INTEGRATED"

    assert _run_count(integration) == 0
    with integration.database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM host_agent_create_keys").fetchone()[0] == 0
    assert host_agent.gateway.calls == []


def test_unknown_cost_budget_enforces_per_run_boundary_across_restart(host_agent: Harness):
    import asyncio

    from gw.api.agent_integration import UnknownCostBudget
    from gw.agent_runtime.models import BudgetReservationRequest, BudgetState, Identifier

    database = host_agent.integration.database
    first_process_budget = UnknownCostBudget(database, max_model_calls_per_run=2)

    def request(index: int):
        return BudgetReservationRequest(
            operation_id=Identifier(f"episode:budget-run-1:stage-{index}:writer:digest-{index}"),
            upper_bound_amount=None,
            currency=None,
            unknown_reason="Test quota is explicit; provider price is unknown.",
        )

    first = asyncio.run(first_process_budget.reserve(request(1)))
    second = asyncio.run(first_process_budget.reserve(request(2)))
    assert first.state is BudgetState.allowed and first.amount is None
    assert second.state is BudgetState.allowed and second.amount is None

    restarted_budget = UnknownCostBudget(database, max_model_calls_per_run=2)
    third = asyncio.run(restarted_budget.reserve(request(3)))
    assert third.state is BudgetState.denied
    assert third.amount is None

    # A repeated reservation for the same operation consumes no additional slot.
    duplicate = asyncio.run(restarted_budget.reserve(request(1)))
    assert duplicate.state is BudgetState.allowed
    assert duplicate.amount is None
    with database.connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM host_agent_budget_ledger WHERE run_id='budget-run-1' AND state IN ('reserved_unknown','unknown')"
        ).fetchone()[0] == 2
    assert host_agent.gateway.calls == []


def test_unknown_cost_budget_concurrent_reservations_do_not_exceed_run_limit(host_agent: Harness):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from gw.api.agent_integration import UnknownCostBudget
    from gw.agent_runtime.models import BudgetReservationRequest, BudgetState, Identifier

    budget = UnknownCostBudget(host_agent.integration.database, max_model_calls_per_run=2)

    def reserve(index: int) -> BudgetState:
        request = BudgetReservationRequest(
            operation_id=Identifier(f"episode:budget-concurrent:stage-{index}:writer:digest-{index}"),
            upper_bound_amount=None,
            currency=None,
            unknown_reason="Concurrent quota test; provider price is unknown.",
        )
        return asyncio.run(budget.reserve(request)).state

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(reserve, range(12)))

    assert outcomes.count(BudgetState.allowed) == 2
    assert outcomes.count(BudgetState.denied) == 10
    with host_agent.integration.database.connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM host_agent_budget_ledger WHERE run_id='budget-concurrent' AND state IN ('reserved_unknown','unknown')"
        ).fetchone()[0] == 2
    assert host_agent.gateway.calls == []


def test_archived_project_keeps_existing_agent_runs_read_only(host_agent: Harness):
    import asyncio
    import hashlib

    from gw.agent_runtime.models import Identifier
    from gw.core.auth import AuthContext

    created = host_agent.client.post(
        "/api/agent/runs",
        json=_create_payload(
            host_agent,
            idempotency_key="agent-archive-existing-run-key",
            request_id="agent-archive-existing-run-request",
        ),
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]

    project = host_agent.integration.projects.get_project(host_agent.project_id)
    archived = host_agent.integration.projects.archive_project(host_agent.project_id, project.version)
    assert archived.archived_at is not None

    status_read = host_agent.client.get(f"/api/agent/runs/{run_id}")
    events_read = host_agent.client.get(f"/api/agent/runs/{run_id}/events")
    assert status_read.status_code == 200, status_read.text
    assert events_read.status_code == 200, events_read.text
    assert status_read.json()["run_id"] == run_id

    mutation = host_agent.client.post(
        f"/api/agent/runs/{run_id}/pause",
        json={
            "expected_version": status_read.json()["version"],
            "idempotency_key": "agent-archive-pause-key",
            "request_id": "agent-archive-pause-request",
        },
    )
    assert mutation.status_code == 403

    new_run = host_agent.client.post(
        "/api/agent/runs",
        json=_create_payload(
            host_agent,
            idempotency_key="agent-archive-new-run-key",
            request_id="agent-archive-new-run-request",
        ),
    )
    assert new_run.status_code == 403
    assert _run_count(host_agent.integration) == 1
    with host_agent.integration.database.connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM host_agent_create_keys WHERE idempotency_key='agent-archive-new-run-key'"
        ).fetchone()[0] == 0

    auth = AuthContext(
        role=host_agent.principal["role"],
        subject=host_agent.principal["user_id"],
        mode="local_account",
        identity_domain="local_account",
    )
    fingerprint = hashlib.sha256(host_agent.session_token.encode("utf-8")).hexdigest()

    async def adapter_checks():
        context = await host_agent.integration.context_for_run(
            auth, fingerprint, run_id, "agent-archive-read-context", edit_required=False,
        )
        can_read = await host_agent.integration.use_case.identity.authorize(
            context, Identifier("agent.run.read"), [Identifier(host_agent.project_id)],
        )
        can_mutate = await host_agent.integration.use_case.identity.authorize(
            context, Identifier("agent.run.pause"), [Identifier(host_agent.project_id)],
        )
        can_dispatch = await host_agent.integration.authorize_background(
            Identifier(host_agent.project_id),
            Identifier(host_agent.principal["user_id"]),
            Identifier(run_id),
        )
        return can_read, can_mutate, can_dispatch

    assert asyncio.run(adapter_checks()) == (True, False, False)
    assert host_agent.gateway.calls == []


@pytest.mark.parametrize("mapping", ["unique", "ambiguous", "unmatched"])
def test_legacy_budget_rows_backfill_only_from_unique_invocation_owner(
    host_agent: Harness,
    mapping: str,
):
    import asyncio

    from gw.api.agent_integration import UnknownCostBudget
    from gw.agent_runtime.models import BudgetReservationRequest, BudgetState, Identifier

    def create_run(label: str) -> str:
        response = host_agent.client.post(
            "/api/agent/runs",
            json=_create_payload(
                host_agent,
                idempotency_key=f"legacy-budget-{label}-key",
                request_id=f"legacy-budget-{label}-request",
            ),
        )
        assert response.status_code == 202, response.text
        return response.json()["run_id"]

    run_a = create_run("a")
    run_b = create_run("b")
    operation_id = f"call:legacy-{mapping}-hash"
    database = host_agent.integration.database
    with database.connect() as db:
        db.execute("DROP TABLE host_agent_budget_ledger")
        db.execute(
            "CREATE TABLE host_agent_budget_ledger ("
            "operation_id TEXT PRIMARY KEY, state TEXT NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO host_agent_budget_ledger(operation_id,state,reason,updated_at) "
            "VALUES(?, 'unknown', 'legacy amount unknown', '2026-10-07T00:00:00+00:00')",
            (operation_id,),
        )

        owners = [run_a] if mapping == "unique" else [run_a, run_b] if mapping == "ambiguous" else []
        for index, run_id in enumerate(owners):
            db.execute(
                "INSERT INTO invocations(invocation_id,run_id,idempotency_key,request_fingerprint,record_json,result_json) "
                "VALUES(?,?,?,?,?,NULL)",
                (
                    f"legacy-invocation-{mapping}-{index}",
                    run_id,
                    operation_id,
                    "0" * 64,
                    "{}",
                ),
            )

    migrated = _build_secondary_integration(host_agent)
    with database.connect() as db:
        row = db.execute(
            "SELECT run_id FROM host_agent_budget_ledger WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
    expected_owner = run_a if mapping == "unique" else None
    assert row["run_id"] == expected_owner

    # A second composition simulates reopening the same durable database.
    restarted = _build_secondary_integration(host_agent)
    with database.connect() as db:
        restarted_row = db.execute(
            "SELECT run_id FROM host_agent_budget_ledger WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
    assert restarted_row["run_id"] == expected_owner

    budget = restarted.service.invocation_dispatcher.budget

    def reserve(run_id: str, index: int) -> BudgetState:
        request = BudgetReservationRequest(
            operation_id=Identifier(f"episode:{run_id}:migration-{index}:writer:digest-{index}"),
            upper_bound_amount=None,
            currency=None,
            unknown_reason="Migration test; provider price is unknown.",
        )
        return asyncio.run(budget.reserve(request)).state

    if mapping == "unique":
        assert reserve(run_b, 1) is BudgetState.allowed
        assert reserve(run_b, 2) is BudgetState.allowed
        assert reserve(run_b, 3) is BudgetState.denied
        assert reserve(run_a, 1) is BudgetState.allowed
        assert reserve(run_a, 2) is BudgetState.denied
    else:
        # Ambiguous and unmatched legacy calls stay unscoped and conservatively
        # consume a slot for each run instead of being assigned speculatively.
        token = budget.bind_run(Identifier(run_a))
        try:
            replay = asyncio.run(budget.reserve(BudgetReservationRequest(
                operation_id=Identifier(operation_id),
                upper_bound_amount=None,
                currency=None,
                unknown_reason="Legacy operation remains unscoped.",
            )))
        finally:
            budget.reset_run(token)
        assert replay.state is BudgetState.allowed
        with database.connect() as db:
            legacy_rows = db.execute(
                "SELECT operation_id,run_id FROM host_agent_budget_ledger "
                "WHERE operation_id=? OR operation_id=?",
                (operation_id, f"{operation_id}:{run_a}"),
            ).fetchall()
        assert len(legacy_rows) == 2
        assert next(row for row in legacy_rows if row["operation_id"] == operation_id)["run_id"] is None
        assert next(row for row in legacy_rows if row["operation_id"] == f"{operation_id}:{run_a}")["run_id"] == run_a
        assert reserve(run_a, 1) is BudgetState.denied
        assert reserve(run_b, 1) is BudgetState.allowed
        assert reserve(run_b, 2) is BudgetState.denied
    assert host_agent.gateway.calls == []


def test_migrated_legacy_operation_replay_reuses_the_unique_row_and_respects_limit(host_agent: Harness):
    import asyncio

    from gw.api.agent_integration import UnknownCostBudget
    from gw.agent_runtime.models import (
        BudgetOutcomeState,
        BudgetReservationRequest,
        BudgetSettlement,
        BudgetState,
        Identifier,
    )

    created = host_agent.client.post(
        "/api/agent/runs",
        json=_create_payload(
            host_agent,
            idempotency_key="legacy-replay-run-key",
            request_id="legacy-replay-run-request",
        ),
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]
    operation_id = "call:legacy-replay-hash"
    released_operation_id = "call:legacy-release-hash"
    database = host_agent.integration.database
    with database.connect() as db:
        db.execute("DROP TABLE host_agent_budget_ledger")
        db.execute(
            "CREATE TABLE host_agent_budget_ledger ("
            "operation_id TEXT PRIMARY KEY, state TEXT NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO host_agent_budget_ledger(operation_id,state,reason,updated_at) "
            "VALUES(?, 'unknown', 'legacy amount unknown', '2026-10-07T00:00:00+00:00')",
            (operation_id,),
        )
        db.execute(
            "INSERT INTO host_agent_budget_ledger(operation_id,state,reason,updated_at) "
            "VALUES(?, 'reserved_unknown', 'legacy unspent reservation', '2026-10-07T00:00:00+00:00')",
            (released_operation_id,),
        )
        db.execute(
            "INSERT INTO invocations(invocation_id,run_id,idempotency_key,request_fingerprint,record_json,result_json) "
            "VALUES(?,?,?,?,?,NULL)",
            ("legacy-invocation-replay", run_id, operation_id, "0" * 64, "{}"),
        )
        db.execute(
            "INSERT INTO invocations(invocation_id,run_id,idempotency_key,request_fingerprint,record_json,result_json) "
            "VALUES(?,?,?,?,?,NULL)",
            ("legacy-invocation-release", run_id, released_operation_id, "1" * 64, "{}"),
        )

    integration = _build_secondary_integration(host_agent)
    budget: UnknownCostBudget = integration.service.invocation_dispatcher.budget
    operation = Identifier(operation_id)
    reservation = BudgetReservationRequest(
        operation_id=operation,
        upper_bound_amount=None,
        currency=None,
        unknown_reason="Legacy replay reservation; monetary cost is unknown.",
    )
    settlement = BudgetSettlement(
        operation_id=operation,
        outcome_state=BudgetOutcomeState.unknown,
        actual_amount=None,
        currency=None,
        unknown_reason="Provider pricing is unavailable.",
    )

    token = budget.bind_run(Identifier(run_id))
    try:
        assert asyncio.run(budget.reserve(reservation)).state is BudgetState.allowed
        assert asyncio.run(budget.reserve(reservation)).state is BudgetState.allowed
        asyncio.run(budget.settle(settlement))
        asyncio.run(budget.settle(settlement))
        assert asyncio.run(budget.release_unspent(Identifier(released_operation_id))).state is BudgetState.allowed
    finally:
        budget.reset_run(token)

    with database.connect() as db:
        rows = db.execute(
            "SELECT operation_id,run_id,state FROM host_agent_budget_ledger "
            "WHERE operation_id=? OR operation_id=?",
            (operation_id, f"{operation_id}:{run_id}"),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["operation_id"] == operation_id
    assert rows[0]["run_id"] == run_id
    assert rows[0]["state"] == "unknown"

    with database.connect() as db:
        released_rows = db.execute(
            "SELECT operation_id,run_id,state FROM host_agent_budget_ledger "
            "WHERE operation_id=? OR operation_id=?",
            (released_operation_id, f"{released_operation_id}:{run_id}"),
        ).fetchall()
    assert len(released_rows) == 1
    assert released_rows[0]["operation_id"] == released_operation_id
    assert released_rows[0]["run_id"] == run_id
    assert released_rows[0]["state"] == "released_unknown"

    def reserve_new(index: int) -> BudgetState:
        request = BudgetReservationRequest(
            operation_id=Identifier(f"episode:{run_id}:post-migration-{index}:writer:digest-{index}"),
            upper_bound_amount=None,
            currency=None,
            unknown_reason="New call after legacy replay; monetary cost is unknown.",
        )
        return asyncio.run(budget.reserve(request)).state

    assert reserve_new(1) is BudgetState.allowed
    assert reserve_new(2) is BudgetState.denied
    assert host_agent.gateway.calls == []


def test_agent_job_id_is_distinct_durable_and_polled_through_workbench_route(host_agent: Harness):
    from fastapi.testclient import TestClient

    from gw.api.app import create_app
    from gw.core.session import session_cookie_name

    created = host_agent.client.post("/api/agent/runs", json=_create_payload(host_agent))
    assert created.status_code == 202, created.text
    accepted = created.json()
    job_id = accepted["job_id"]
    run_id = accepted["run_id"]
    assert job_id.startswith("job_agent_")
    assert job_id != run_id
    assert accepted["state"] == "accepted"
    assert accepted["agent_status"] == "queued"
    assert accepted["poll_hint"] == f"/api/jobs/{job_id}"
    assert accepted["agent_poll_hint"] == {
        "status_uri": f"/api/agent/runs/{run_id}",
        "events_uri": f"/api/agent/runs/{run_id}/events",
        "suggested_interval_ms": 2000,
    }

    with host_agent.integration.database.connect() as db:
        row = db.execute(
            "SELECT job_id,run_id FROM host_agent_create_keys WHERE idempotency_key=?",
            (_create_payload(host_agent)["idempotency_key"],),
        ).fetchone()
    assert row["job_id"] == job_id
    assert row["run_id"] == run_id

    polled = host_agent.client.get(accepted["poll_hint"])
    assert polled.status_code == 200, polled.text
    assert polled.json()["job_id"] == job_id
    assert polled.json()["run_id"] == run_id
    assert polled.json()["agent_status"] == "queued"
    assert polled.json()["state"] == "accepted"
    assert polled.json()["poll_hint"] == accepted["poll_hint"]
    assert polled.json()["agent_poll_hint"] == accepted["agent_poll_hint"]

    restarted = _build_secondary_integration(host_agent)
    with TestClient(
        create_app(agent_integration_factory=restarted),
        base_url="http://localhost",
        client=("127.0.0.1", 50004),
        headers={"Origin": "http://localhost"},
    ) as after_restart:
        after_restart.cookies.set(session_cookie_name(), host_agent.session_token)
        recovered = after_restart.get(f"/api/jobs/{job_id}")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["job_id"] == job_id
    assert recovered.json()["run_id"] == run_id


def test_agent_job_poll_requires_cookie_and_hides_unknown_and_foreign_jobs(host_agent: Harness):
    from fastapi.testclient import TestClient

    from gw.api.app import create_app
    from gw.core import local_accounts
    from gw.core.session import session_cookie_name

    created = host_agent.client.post(
        "/api/agent/runs",
        json=_create_payload(host_agent, idempotency_key="agent-job-acl-key", request_id="agent-job-acl-create"),
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["job_id"]
    unauthenticated = TestClient(
        create_app(agent_integration_factory=host_agent.integration),
        base_url="http://localhost",
        client=("127.0.0.1", 50005),
    )
    assert unauthenticated.get(f"/api/jobs/{job_id}").status_code == 401
    unauthenticated.close()

    salt = b"agent-job-acl-salt"
    password = "OtherAgent2026"
    with local_accounts.database() as db:
        db.execute(
            "INSERT INTO local_users(user_id,username,salt,password_hash,role) VALUES(?,?,?,?,?)",
            ("usr-agent-job-outsider", "agentoutsider", salt, local_accounts.password_hash(password, salt), "editor"),
        )
    _, outsider_token = local_accounts.login("agentoutsider", password, "agent-job-acl")
    outsider = TestClient(
        create_app(agent_integration_factory=host_agent.integration),
        base_url="http://localhost",
        client=("127.0.0.1", 50006),
        headers={"Origin": "http://localhost"},
    )
    outsider.cookies.set(session_cookie_name(), outsider_token)
    foreign = outsider.get(f"/api/jobs/{job_id}")
    unknown = outsider.get("/api/jobs/job_agent_unknown")
    assert foreign.status_code == unknown.status_code == 404
    foreign_detail = foreign.json()["detail"]
    unknown_detail = unknown.json()["detail"]
    for field in ("code", "message", "retryable", "retry_after", "dispatch_state"):
        assert foreign_detail[field] == unknown_detail[field]
    assert job_id not in foreign.text
    assert job_id not in unknown.text
    assert foreign_detail["request_id"] != job_id
    outsider.close()


def test_agent_run_status_projection_and_terminal_poll_hint_contract():
    from types import SimpleNamespace

    from gw.api.routes_god_canvas import agent_run_job_payload
    import yaml

    catalog_path = Path(__file__).resolve().parents[3] / "docs/contracts/AGENT-WORKBENCH-INTERFACE-CATALOG.yaml"
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    expected = catalog["task_status_projection"]["agent_to_workbench"]
    terminal = set(catalog["task_status_projection"]["terminal_agent_statuses"])
    status_contract = next(
        item for item in catalog["interfaces"] if item["name"] == "get_agent_job_status"
    )
    assert status_contract["path"] == "/api/jobs/{job_id}"
    assert status_contract["errors"][404].startswith("任务不存在或当前主体无权读取")
    assert catalog["task_status_projection"]["preserve_agent_status"] is True
    for agent_status, workbench_status in expected.items():
        run = SimpleNamespace(
            status=SimpleNamespace(value=agent_status),
            run_id=SimpleNamespace(root="run-projection-test"),
            version=SimpleNamespace(root=7),
            current_stage=SimpleNamespace(value="storyboard"),
        )
        payload = agent_run_job_payload("job_agent_projection", run)
        assert payload["state"] == workbench_status
        assert payload["agent_status"] == agent_status
        assert payload["run_id"] == "run-projection-test"
        assert payload["version"] == 7
        assert payload["stage"] == "storyboard"
        assert payload["agent_poll_hint"] == {
            "status_uri": "/api/agent/runs/run-projection-test",
            "events_uri": "/api/agent/runs/run-projection-test/events",
            "suggested_interval_ms": 2000,
        }
        assert payload["poll_hint"] == (
            None if agent_status in terminal else "/api/jobs/job_agent_projection"
        )


def test_agent_job_id_migration_backfills_legacy_create_keys_once(host_agent: Harness):
    database = host_agent.integration.database
    db = database.transaction()
    db.execute("DROP TABLE host_agent_create_keys")
    db.execute(
        "CREATE TABLE host_agent_create_keys ("
        "project_id TEXT NOT NULL, actor_id TEXT NOT NULL, identity_domain TEXT NOT NULL, "
        "idempotency_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL, request_json TEXT NOT NULL, "
        "run_id TEXT, PRIMARY KEY(project_id,actor_id,identity_domain,idempotency_key))"
    )
    db.execute(
        "INSERT INTO host_agent_create_keys(project_id,actor_id,identity_domain,idempotency_key,request_fingerprint,request_json,run_id) "
        "VALUES('legacy-project','legacy-actor','local_account','legacy-key','fingerprint','{}','legacy-run')"
    )
    database.close_commit(db)

    migrated = _build_secondary_integration(host_agent)
    job_id = migrated.job_id_for_run("legacy-run")
    migrated_again = _build_secondary_integration(host_agent)
    assert job_id.startswith("job_agent_")
    assert job_id != "legacy-run"
    assert migrated_again.job_id_for_run("legacy-run") == job_id


def test_agent_legacy_create_fingerprint_replays_same_durable_job_and_run(host_agent: Harness):
    import hashlib
    import json

    idempotency_key = "agent-legacy-fingerprint-replay"
    payload = _create_payload(host_agent, idempotency_key=idempotency_key, request_id="legacy-create-first")
    created = host_agent.client.post("/api/agent/runs", json=payload)
    assert created.status_code == 202, created.text
    first = created.json()

    # This is the exact HEAD-era fingerprint canonical object: config_snapshot_id
    # and input_artifact_refs were persisted but omitted from the digest.
    old_canonical = {
        "project_id": payload["project_id"],
        "user_goal": payload["user_goal"],
        "provider_id": payload["provider_id"],
        "model": payload["model"],
        "mode": payload["mode"],
        "actor_id": host_agent.principal["user_id"],
        "identity_domain": "local_account",
    }
    old_fingerprint = hashlib.sha256(json.dumps(
        old_canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    with host_agent.integration.database.connect() as db:
        db.execute(
            "UPDATE host_agent_create_keys SET request_fingerprint=? "
            "WHERE project_id=? AND actor_id=? AND identity_domain=? AND idempotency_key=?",
            (
                old_fingerprint,
                payload["project_id"],
                host_agent.principal["user_id"],
                "local_account",
                idempotency_key,
            ),
        )

    replay_payload = {**payload, "request_id": "legacy-create-replay"}
    replay = host_agent.client.post("/api/agent/runs", json=replay_payload)
    assert replay.status_code == 202, replay.text
    assert replay.json()["run_id"] == first["run_id"]
    assert replay.json()["job_id"] == first["job_id"]
    assert _run_count(host_agent.integration) == 1

    changed = host_agent.client.post(
        "/api/agent/runs",
        json={**replay_payload, "request_id": "legacy-create-conflict", "user_goal": "different stored request"},
    )
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert _run_count(host_agent.integration) == 1
    assert host_agent.gateway.calls == []


def test_agent_legacy_host_schema_migration_serializes_concurrent_initialization(
    host_agent: Harness,
    monkeypatch: pytest.MonkeyPatch,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread

    database = host_agent.integration.database
    db = database.transaction()
    db.execute("DROP TABLE host_agent_create_keys")
    db.execute(
        "CREATE TABLE host_agent_create_keys ("
        "project_id TEXT NOT NULL, actor_id TEXT NOT NULL, identity_domain TEXT NOT NULL, "
        "idempotency_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL, request_json TEXT NOT NULL, "
        "run_id TEXT, PRIMARY KEY(project_id,actor_id,identity_domain,idempotency_key))"
    )
    db.execute(
        "INSERT INTO host_agent_create_keys(project_id,actor_id,identity_domain,idempotency_key,request_fingerprint,request_json,run_id) "
        "VALUES('legacy-project','legacy-actor','local_account','legacy-key','legacy-fingerprint','{}','legacy-run')"
    )
    db.execute("DROP TABLE host_agent_budget_ledger")
    db.execute(
        "CREATE TABLE host_agent_budget_ledger ("
        "operation_id TEXT PRIMARY KEY, state TEXT NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    db.execute(
        "INSERT INTO host_agent_budget_ledger(operation_id,state,reason,updated_at) "
        "VALUES('episode:legacy-run:stage:writer:digest','unknown','legacy','2026-10-08T00:00:00+00:00')"
    )
    database.close_commit(db)

    first_budget_columns_read = Event()
    release_first_migration = Event()
    second_connection_opened = Event()
    second_budget_columns_read = Event()
    original_connect = database.connect

    class ObservedConnection:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self.connection.__exit__(exc_type, exc_value, traceback)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, sql, parameters=()):
            cursor = self.connection.execute(sql, parameters)
            normalized = " ".join(sql.strip().upper().split())
            if normalized == "PRAGMA TABLE_INFO(HOST_AGENT_BUDGET_LEDGER)":
                rows = cursor.fetchall()
                thread_name = current_thread().name
                if thread_name.endswith("_0"):
                    first_budget_columns_read.set()
                    if not release_first_migration.wait(timeout=10):
                        raise TimeoutError("test did not release the first host-schema migration")
                elif thread_name.endswith("_1"):
                    second_budget_columns_read.set()
                return _BufferedRows(rows)
            return cursor

    class _BufferedRows:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    def tracked_connect():
        connection = original_connect()
        if current_thread().name.endswith("_1"):
            second_connection_opened.set()
        return ObservedConnection(connection)

    monkeypatch.setattr(database, "connect", tracked_connect)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-host-migrate") as pool:
        first = pool.submit(_build_secondary_integration, host_agent)
        second = None
        try:
            assert first_budget_columns_read.wait(timeout=5)
            second = pool.submit(_build_secondary_integration, host_agent)
            assert second_connection_opened.wait(timeout=5)
            # The second initializer has started while the first holds its schema
            # transaction. It must not inspect the old budget schema before the
            # first commits; this wait is one-sided and cannot deadlock on the lock.
            assert not second_budget_columns_read.wait(timeout=0.5)
        finally:
            release_first_migration.set()
        first_result = first.result(timeout=15)
        assert first_result is not None
        assert second is not None
        second_result = second.result(timeout=15)
        assert second_result is not None

    with database.connect() as migrated:
        create_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(host_agent_create_keys)").fetchall()}
        budget_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(host_agent_budget_ledger)").fetchall()}
        create_key = migrated.execute(
            "SELECT job_id FROM host_agent_create_keys WHERE idempotency_key='legacy-key'"
        ).fetchone()
        ledger = migrated.execute(
            "SELECT run_id FROM host_agent_budget_ledger WHERE operation_id='episode:legacy-run:stage:writer:digest'"
        ).fetchone()
    assert "job_id" in create_columns
    assert "run_id" in budget_columns
    assert create_key["job_id"].startswith("job_agent_")
    assert ledger["run_id"] == "legacy-run"

