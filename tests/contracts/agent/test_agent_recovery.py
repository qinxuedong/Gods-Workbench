"""Host-boundary recovery, authorization-race, and tracing regressions for AgentRuns."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from gw.agent_episode.scoring import STAGE_WEIGHTS
from gw.agent_runtime.graph import LangGraphRuntimeExecutor
from gw.agent_runtime.models import (
    ApprovalRecord,
    ArtifactSourceRef,
    ArtifactVersionInput,
    DispatchState,
    FinishReason,
    Identifier,
    MessageRole,
    ModelCapabilities,
    ModelInvocationRequest,
    ModelInvocationResult,
    RunStatus,
    StageId,
    Usage,
)
from gw.agent_runtime.policy import CapabilityRegistry, RuntimePolicy
from gw.agent_runtime.service import AgentRuntimeService


PASSWORD = "Test2026"
ORIGIN_HEADERS = {"Origin": "http://localhost"}


class FakeGateway:
    """Typed model seam that returns deterministic text and never calls a provider."""

    def __init__(
        self, *, unknown_first: bool = False, block_first: bool = False,
        block_third: bool = False,
    ) -> None:
        self.unknown_first = unknown_first
        self.block_first = block_first
        self.block_third = block_third
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.third_started = threading.Event()
        self.release_third = threading.Event()
        self._lock = threading.Lock()
        self._calls: list[ModelInvocationRequest] = []

    @property
    def calls(self) -> list[ModelInvocationRequest]:
        with self._lock:
            return list(self._calls)

    async def describe_capabilities(self, model_ref: Identifier) -> ModelCapabilities:
        return ModelCapabilities(
            model_ref=model_ref,
            supported_roles=list(MessageRole),
            structured_output=True,
            tool_calls=False,
            streaming=False,
            multimodal=False,
            lookup=False,
            cancellation=False,
            usage_reporting=True,
            max_context_tokens=None,
        )

    async def invoke(self, request: ModelInvocationRequest) -> ModelInvocationResult:
        with self._lock:
            self._calls.append(request)
            call_number = len(self._calls)

        if call_number == 1 and self.block_first:
            self.first_started.set()
            while not self.release_first.is_set():
                await asyncio.sleep(0.005)
        if call_number == 3 and self.block_third:
            self.third_started.set()
            while not self.release_third.is_set():
                await asyncio.sleep(0.005)
        if call_number == 1 and self.unknown_first:
            raise RuntimeError("synthetic unknown provider outcome")

        properties = (request.output_schema or {}).get("properties", {})
        if "action" in properties:
            payload: dict[str, Any] = {
                "action": "create",
                "capability": None,
                "target_stage": None,
                "question": None,
            }
        elif "artifact_content" in properties:
            payload = {
                "artifact_content": {
                    "creative_goal": "Complete the synthetic recovery fixture.",
                    "intended_audience": "test reader",
                    "format_description": "A concise text brief.",
                    "constraints": ["Use synthetic test content only."],
                    "acceptance_criteria": ["The requested goal is stated clearly."],
                    "duration_request": None,
                    "clarifying_questions": [],
                },
                "revision_responses": [],
            }
        elif "dimension_scores" in properties:
            payload = {
                "dimension_scores": {
                    dimension: {"score": 10, "evidence": [f"Synthetic evidence for {dimension}."]}
                    for dimension in STAGE_WEIGHTS[StageId.brief]
                },
                "findings": [],
                "summary": "Synthetic review passed all deterministic checks.",
                "rollback_proposal": None,
            }
        else:
            raise AssertionError("FakeGateway received an unrecognized structured output schema")

        return ModelInvocationResult(
            content=json.dumps(payload, ensure_ascii=False),
            tool_calls=[],
            finish_reason=FinishReason.stop,
            upstream_request_id=Identifier(f"fake-upstream-{call_number}"),
            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
            usage_unavailable_reason=None,
        )


def _new_app(tmp_path, monkeypatch, gateway: FakeGateway):
    data_root = tmp_path / "data"
    auth_db = tmp_path / "auth" / "auth.sqlite3"
    video_root = tmp_path / "video"
    for path in (data_root, auth_db.parent, video_root):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(data_root))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(auth_db))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(video_root))
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_DISABLE_AGENT_INTEGRATION", "0")
    monkeypatch.setenv("GW_AGENT_MAX_MODEL_CALLS_PER_RUN", "16")

    from gw.api.agent_integration import build_agent_integration
    from gw.api.app import create_app
    from gw.core.runtime_paths import resolve_runtime_paths

    paths = resolve_runtime_paths()

    def model_resolver(provider_id: str | None, model: str | None) -> dict[str, str]:
        return {"provider_id": provider_id or "fake", "model": model or "fake-model"}

    def configuration_provider() -> dict[str, Any]:
        return {
            "default_provider_id": "fake",
            "providers": [{"provider_id": "fake", "configured": True, "models": ["fake-model"]}],
        }

    return create_app(agent_integration_factory=lambda: build_agent_integration(
        gateway=gateway,
        runtime_paths=paths,
        model_resolver=model_resolver,
        configuration_provider=configuration_provider,
        start_worker=False,
    ))


def _open_client(app) -> TestClient:
    return TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
        headers=ORIGIN_HEADERS,
    )


def _setup_owner_and_project(client: TestClient) -> tuple[str, str]:
    setup = client.post(
        "/api/asset-auth/local/setup",
        json={"username": "agent-owner", "password": PASSWORD},
    )
    assert setup.status_code == 201, setup.text
    session_cookie = client.cookies.get("gw_session")
    assert isinstance(session_cookie, str) and session_cookie

    created = client.post(
        "/api/asset-registry/projects",
        json={"name": "Agent recovery fixture", "project_type": "film"},
    )
    assert created.status_code == 201, created.text
    return session_cookie, created.json()["project"]["project_id"]


def _create_run(
    client: TestClient, project_id: str, *, key: str = "agent-recovery-create-1",
    mode: str = "approval",
) -> str:
    profiles = client.get("/api/agent/config").json()["profiles"]
    profile_id = next(item["config_snapshot_id"] for item in profiles if item["mode"] == mode)
    response = client.post("/api/agent/runs", json={
        "project_id": project_id,
        "user_goal": "Complete a synthetic recovery test brief.",
        "provider_id": "fake",
        "model": "fake-model",
        "mode": mode,
        "config_snapshot_id": profile_id,
        "input_artifact_refs": [],
        "idempotency_key": key,
        "request_id": f"request:{key}",
    })
    assert response.status_code == 202, response.text
    payload = response.json()
    assert isinstance(payload.get("job_id"), str) and payload["job_id"].startswith("job_agent_")
    assert payload["job_id"] != payload["run_id"]
    assert payload["poll_hint"] == f"/api/jobs/{payload['job_id']}"
    assert payload["status"] == "queued"
    assert payload["version"] == 0
    return payload["run_id"]


def _run_worker_once(client: TestClient) -> None:
    integration = client.app.state.agent_integration
    client.portal.call(integration.worker.run_once)


def test_execution_authorizer_accepts_run_bound_and_legacy_callbacks() -> None:
    async def verify() -> None:
        project_id = Identifier("project:synthetic")
        actor_id = Identifier("actor:synthetic")
        run_id = Identifier("run:synthetic")
        registry = CapabilityRegistry()

        legacy_calls: list[tuple[str, str]] = []

        def legacy_authorizer(project: Identifier, actor: Identifier) -> bool:
            legacy_calls.append((project.root, actor.root))
            return True

        legacy_service = AgentRuntimeService(
            object(), object(), registry, execution_authorizer=legacy_authorizer,
        )
        assert await legacy_service._execution_is_authorized(project_id, actor_id, run_id)
        assert legacy_calls == [(project_id.root, actor_id.root)]

        bound_calls: list[tuple[str, str, str | None]] = []

        async def bound_authorizer(project: Identifier, actor: Identifier, run: Identifier | None = None) -> bool:
            await asyncio.sleep(0)
            bound_calls.append((project.root, actor.root, run.root if run else None))
            return True

        bound_service = AgentRuntimeService(
            object(), object(), registry, execution_authorizer=bound_authorizer,
        )
        assert await bound_service._execution_is_authorized(project_id, actor_id, run_id)
        assert bound_calls == [(project_id.root, actor_id.root, run_id.root)]

    anyio.run(verify)


def test_each_langgraph_invoke_disables_ambient_langsmith_tracing(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

    from langsmith.run_helpers import get_tracing_context, tracing_context

    class ProbeGraph:
        def __init__(self) -> None:
            self.observations: list[tuple[dict[str, Any], dict[str, Any]]] = []

        async def ainvoke(self, state: dict[str, Any], *, config: dict[str, Any]) -> dict[str, Any]:
            self.observations.append((dict(get_tracing_context()), dict(config)))
            return state

    async def verify() -> None:
        executor = LangGraphRuntimeExecutor(CapabilityRegistry(), RuntimePolicy())
        stage_graph = ProbeGraph()
        planner_graph = ProbeGraph()
        executor.graph = stage_graph
        executor.orchestration_graph = planner_graph

        async def unused_artifact_factory(_value):
            raise AssertionError("the fake graph must not invoke capabilities")

        with tracing_context(enabled=True, project_name="synthetic-ambient", tags=["synthetic"]):
            await executor.execute_stage_once(
                stage=StageId.brief.value,
                actor_id=Identifier("actor:synthetic"),
                writer_role="writer",
                reviewer_role="reviewer",
                execution_input={"fixture": "synthetic"},
                artifact_factory=unused_artifact_factory,
            )
            await executor.propose_stage_action(
                stage=StageId.brief.value,
                actor_id=Identifier("actor:synthetic"),
                orchestrator_role="orchestrator",
                execution_input={"fixture": "synthetic"},
                runtime_context={},
            )

        observations = stage_graph.observations + planner_graph.observations
        assert len(observations) == 2
        for tracing, config in observations:
            assert config.get("callbacks") == []
            assert tracing.get("enabled") is False
            assert tracing.get("parent") is None

    anyio.run(verify)


def test_review_cas_race_and_approval_survive_app_recreation(tmp_path, monkeypatch) -> None:
    gateway = FakeGateway()
    app = _new_app(tmp_path, monkeypatch, gateway)
    with _open_client(app) as client:
        session_cookie, project_id = _setup_owner_and_project(client)
        run_id = _create_run(client, project_id)
        _run_worker_once(client)

        run_payload = client.get(f"/api/agent/runs/{run_id}").json()
        assert run_payload["status"] == "waiting_review"
        reviews_response = client.get(f"/api/agent/runs/{run_id}/reviews")
        assert reviews_response.status_code == 200, reviews_response.text
        review = reviews_response.json()["reviews"][0]["review"]
        base = {
            "expected_version": run_payload["version"],
            "review_id": review["review_id"],
            "artifact_version_id": review["artifact"]["version_id"],
            "reason": "synthetic concurrent reviewer decision",
            "request_id": "request:review-race",
        }
        requests = [
            {**base, "decision": "approve", "idempotency_key": "review-race-approve"},
            {**base, "decision": "revise", "idempotency_key": "review-race-revise"},
        ]
        barrier = threading.Barrier(3)

        def submit(payload: dict[str, Any]):
            barrier.wait(timeout=5)
            return client.post(f"/api/agent/runs/{run_id}/review", json=payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, payload) for payload in requests]
            barrier.wait(timeout=5)
            responses = [future.result(timeout=10) for future in futures]

        assert sorted(response.status_code for response in responses) == [200, 409], [
            (response.status_code, response.text) for response in responses
        ]
        success_payload = next(response.json() for response in responses if response.status_code == 200)
        assert success_payload["run"]["run_id"] == run_id
        with sqlite3.connect(tmp_path / "data" / "agent" / "runtime.sqlite3") as db:
            rows = db.execute(
                "SELECT approval_json FROM approvals WHERE run_id=?", (run_id,),
            ).fetchall()
        decisions = [ApprovalRecord.model_validate_json(row[0]) for row in rows]
        assert len(decisions) == 1
        accepted = decisions[0]
        assert accepted.review_id.root == review["review_id"]
        assert accepted.artifact_version_id.root == review["artifact"]["version_id"]
        assert accepted.reason.root == "synthetic concurrent reviewer decision"
        assert accepted.decision.value in {"approve", "reject"}
        # The v1 review endpoint exposes reviewer conclusions on ReviewRecord.
        # Human rejection is separately queryable through its durable run event;
        # the current `approval` projection intentionally returns null for reject.
        if accepted.decision.value == "approve":
            assert success_payload["approval"]["approval_id"] == accepted.approval_id.root
        else:
            assert success_payload["approval"] is None

    reopened_app = _new_app(tmp_path, monkeypatch, gateway)
    with _open_client(reopened_app) as reopened:
        reopened.cookies.set("gw_session", session_cookie)
        run_response = reopened.get(f"/api/agent/runs/{run_id}")
        assert run_response.status_code == 200, run_response.text
        records = reopened.get(f"/api/agent/runs/{run_id}/reviews").json()["reviews"]
        assert len(records) == 1
        assert records[0]["review"]["conclusion"] in {"pass", "revise", "block"}
        if accepted.decision.value == "reject":
            event_response = reopened.get(
                f"/api/agent/runs/{run_id}/events",
                params={"after_version": run_payload["version"]},
            )
            assert event_response.status_code == 200, event_response.text
            rejection_events = [
                event for event in event_response.json()["events"]
                if event["event_type"] == "approval.rejected.revision_queued"
            ]
            assert any(
                event["approval_id"] == accepted.approval_id.root
                for event in rejection_events
            )
        with sqlite3.connect(tmp_path / "data" / "agent" / "runtime.sqlite3") as db:
            rows = db.execute(
                "SELECT approval_json FROM approvals WHERE run_id=?", (run_id,),
            ).fetchall()
        recovered = [ApprovalRecord.model_validate_json(row[0]) for row in rows]
        assert len(recovered) == 1
        assert recovered[0].approval_id == accepted.approval_id
        assert recovered[0].decision is accepted.decision
        assert recovered[0].reason.root == "synthetic concurrent reviewer decision"


def test_read_only_get_does_not_rebind_revoked_session_after_relogin(tmp_path, monkeypatch) -> None:
    gateway = FakeGateway()
    app = _new_app(tmp_path, monkeypatch, gateway)
    with _open_client(app) as client:
        old_cookie, project_id = _setup_owner_and_project(client)
        run_id = _create_run(client, project_id, key="agent-read-no-rebind-1")
        integration = client.app.state.agent_integration
        binding = integration.get_binding(run_id)
        original_fingerprint = hashlib.sha256(old_cookie.encode("utf-8")).hexdigest()
        assert binding["session_fingerprint"] == original_fingerprint

        logout = client.post("/api/asset-auth/logout")
        assert logout.status_code == 204, logout.text
        login = client.post(
            "/api/asset-auth/local/login",
            json={"username": "agent-owner", "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        new_cookie = client.cookies.get("gw_session")
        assert isinstance(new_cookie, str) and new_cookie != old_cookie

        status = client.get(f"/api/agent/runs/{run_id}")
        assert status.status_code == 200, status.text
        assert status.json()["run_id"] == run_id

        binding_after_read = integration.get_binding(run_id)
        assert binding_after_read["session_fingerprint"] == original_fingerprint
        still_revoked = client.portal.call(
            integration.authorize_background,
            Identifier(project_id),
            Identifier(binding["actor_id"]),
            Identifier(run_id),
        )
        assert still_revoked is False
        assert gateway.calls == []


def test_unknown_model_send_is_not_replayed_after_app_recreation(tmp_path, monkeypatch) -> None:
    gateway = FakeGateway(unknown_first=True)
    app = _new_app(tmp_path, monkeypatch, gateway)
    with _open_client(app) as client:
        session_cookie, project_id = _setup_owner_and_project(client)
        run_id = _create_run(client, project_id, key="agent-recovery-unknown-1")
        _run_worker_once(client)
        integration = client.app.state.agent_integration
        run = client.portal.call(
            integration.repository.read_run_by_id, Identifier(run_id),
        )
        assert run.status is RunStatus.paused
        assert len(gateway.calls) == 1

        database_path = tmp_path / "data" / "agent" / "runtime.sqlite3"
        raw_db = database_path.read_bytes()
        assert session_cookie.encode("utf-8") not in raw_db
        binding = integration.get_binding(run_id)
        assert binding["session_fingerprint"] == hashlib.sha256(session_cookie.encode("utf-8")).hexdigest()

    reopened_app = _new_app(tmp_path, monkeypatch, gateway)
    with _open_client(reopened_app) as reopened:
        reopened.cookies.set("gw_session", session_cookie)
        integration = reopened.app.state.agent_integration
        reopened.portal.call(integration.worker.run_once)
        run = reopened.portal.call(
            integration.repository.read_run_by_id, Identifier(run_id),
        )
        assert run.status is RunStatus.paused
        assert len(gateway.calls) == 1

        pending = reopened.portal.call(
            integration.repository.incomplete_invocations, Identifier(run_id),
        )
        assert len(pending) == 1
        assert pending[0].dispatch_state is DispatchState.unknown


@pytest.mark.parametrize("authority_change", ["logout", "role_downgrade", "project_archive"])
def test_authority_revocation_during_planner_blocks_next_dispatch(tmp_path, monkeypatch, authority_change) -> None:
    gateway = FakeGateway(block_first=True)
    app = _new_app(tmp_path, monkeypatch, gateway)
    with _open_client(app) as client:
        _, project_id = _setup_owner_and_project(client)
        run_id = _create_run(client, project_id, key=f"agent-revoke-{authority_change}-1")
        integration = client.app.state.agent_integration

        worker_future = client.portal.start_task_soon(integration.worker.run_once)
        assert gateway.first_started.wait(timeout=10), "planner dispatch did not reach the fake gateway"
        assert len(gateway.calls) == 1

        try:
            if authority_change == "logout":
                revoked = client.post("/api/asset-auth/logout")
                assert revoked.status_code == 204, revoked.text
            elif authority_change == "role_downgrade":
                with sqlite3.connect(os.environ["GW_LOCAL_AUTH_DB"]) as db:
                    db.execute("UPDATE local_users SET role='readonly' WHERE username='agent-owner'")
            else:
                archived = client.request(
                    "DELETE",
                    f"/api/asset-registry/projects/{project_id}",
                    json={"expected_version": 1},
                )
                assert archived.status_code == 200, archived.text
        finally:
            gateway.release_first.set()

        worker_future.result(timeout=10)
        run = client.portal.call(
            integration.repository.read_run_by_id, Identifier(run_id),
        )
        assert run.status is RunStatus.paused
        assert len(gateway.calls) == 1


@pytest.mark.parametrize("authority_change", ["logout", "role_downgrade", "project_archive"])
def test_authority_revocation_while_reviewer_waits_blocks_automatic_release(
    tmp_path, monkeypatch, authority_change,
) -> None:
    gateway = FakeGateway(block_third=True)
    app = _new_app(tmp_path, monkeypatch, gateway)
    with _open_client(app) as client:
        _, project_id = _setup_owner_and_project(client)
        run_id = _create_run(
            client, project_id, key=f"agent-review-revoke-{authority_change}-1", mode="automatic",
        )
        integration = client.app.state.agent_integration
        worker_future = client.portal.start_task_soon(integration.worker.run_once)
        assert gateway.third_started.wait(timeout=10), "reviewer dispatch did not reach the fake gateway"
        assert len(gateway.calls) == 3
        assert "dimension_scores" in (gateway.calls[2].output_schema or {}).get("properties", {})

        try:
            if authority_change == "logout":
                revoked = client.post("/api/asset-auth/logout")
                assert revoked.status_code == 204, revoked.text
            elif authority_change == "role_downgrade":
                with sqlite3.connect(os.environ["GW_LOCAL_AUTH_DB"]) as db:
                    db.execute("UPDATE local_users SET role='readonly' WHERE username='agent-owner'")
            else:
                archived = client.request(
                    "DELETE",
                    f"/api/asset-registry/projects/{project_id}",
                    json={"expected_version": 1},
                )
                assert archived.status_code == 200, archived.text
        finally:
            gateway.release_third.set()

        worker_future.result(timeout=10)
        run = client.portal.call(integration.repository.read_run_by_id, Identifier(run_id))
        state = client.portal.call(integration.repository.read_runtime_state, Identifier(run_id)) or {}
        assert run.status is RunStatus.paused
        assert len(gateway.calls) == 3
        assert state.get("mode") == "automatic"
        assert state.get("released_versions", []) == []
        assert client.portal.call(integration.repository.get_reviews, Identifier(run_id)) == []
        with sqlite3.connect(tmp_path / "data" / "agent" / "runtime.sqlite3") as db:
            approvals = db.execute("SELECT approval_json FROM approvals WHERE run_id=?", (run_id,)).fetchall()
        assert approvals == []


def test_stale_source_while_reviewer_waits_blocks_automatic_release(tmp_path, monkeypatch) -> None:
    gateway = FakeGateway(block_third=True)
    app = _new_app(tmp_path, monkeypatch, gateway)
    with _open_client(app) as client:
        _, project_id = _setup_owner_and_project(client)
        run_id = _create_run(client, project_id, key="agent-review-source-stale-1", mode="automatic")
        integration = client.app.state.agent_integration
        source = client.portal.call(
            integration.service.artifacts.create_version,
            ArtifactVersionInput(
                artifact_id=Identifier(f"source-{run_id}"),
                parent_version_id=None,
                content="Synthetic source for reviewer currentness coverage.",
                metadata={"run_id": run_id, "runtime_stage": "source_fixture"},
                source_refs=[],
                idempotency_key=Identifier(f"source-fixture:{run_id}"),
            ),
        )
        state = client.portal.call(integration.repository.read_runtime_state, Identifier(run_id)) or {}
        state["initial_artifact_refs"] = [ArtifactSourceRef(
            artifact_id=source.artifact_id, version_id=source.version_id,
        ).model_dump(mode="json")]
        with sqlite3.connect(tmp_path / "data" / "agent" / "runtime.sqlite3") as db:
            db.execute(
                "UPDATE runs SET state_json=? WHERE run_id=?",
                (json.dumps(state, ensure_ascii=False, sort_keys=True), run_id),
            )

        worker_future = client.portal.start_task_soon(integration.worker.run_once)
        assert gateway.third_started.wait(timeout=10), "reviewer dispatch did not reach the fake gateway"
        assert len(gateway.calls) == 3
        attempts = client.portal.call(integration.repository.get_attempts, Identifier(run_id))
        assert len(attempts) == 1
        assert [item.root for item in attempts[0].input_artifact_version_ids] == [source.version_id.root]

        try:
            client.portal.call(
                integration.service.artifacts.move_to_trash,
                source.artifact_id,
                source.revision.root,
            )
        finally:
            gateway.release_third.set()

        worker_future.result(timeout=10)
        run = client.portal.call(integration.repository.read_run_by_id, Identifier(run_id))
        current_state = client.portal.call(integration.repository.read_runtime_state, Identifier(run_id)) or {}
        assert run.status is not RunStatus.succeeded
        assert current_state.get("released_versions", []) == []
        assert client.portal.call(integration.repository.get_reviews, Identifier(run_id)) == []


def test_fenced_lease_while_reviewer_waits_blocks_automatic_release(tmp_path, monkeypatch) -> None:
    gateway = FakeGateway(block_third=True)
    app = _new_app(tmp_path, monkeypatch, gateway)
    with _open_client(app) as client:
        _, project_id = _setup_owner_and_project(client)
        run_id = _create_run(client, project_id, key="agent-review-fenced-lease-1", mode="automatic")
        integration = client.app.state.agent_integration
        worker_future = client.portal.start_task_soon(integration.worker.run_once)
        assert gateway.third_started.wait(timeout=10), "reviewer dispatch did not reach the fake gateway"
        assert len(gateway.calls) == 3

        with sqlite3.connect(tmp_path / "data" / "agent" / "runtime.sqlite3") as db:
            row = db.execute(
                "SELECT owner_id,generation FROM leases WHERE run_id=?", (run_id,),
            ).fetchone()
            assert row is not None
            db.execute(
                "UPDATE leases SET owner_id=?,generation=? WHERE run_id=?",
                ("runtime:test-fencer", row[1] + 1, run_id),
            )

        gateway.release_third.set()
        worker_future.result(timeout=10)
        run = client.portal.call(integration.repository.read_run_by_id, Identifier(run_id))
        state = client.portal.call(integration.repository.read_runtime_state, Identifier(run_id)) or {}
        assert run.status is not RunStatus.succeeded
        assert state.get("released_versions", []) == []
        assert client.portal.call(integration.repository.get_reviews, Identifier(run_id)) == []

