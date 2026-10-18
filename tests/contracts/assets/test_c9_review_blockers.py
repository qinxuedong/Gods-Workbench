# -*- coding: utf-8 -*-
"""QIN-189 / C9-T5：审阅阻塞、下一步和批准失效契约。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


def _target(version_id: str = "asset-c9-0001-v001", version: int = 1) -> dict:
    return {
        "project_id": "project-c9-0001",
        "entity_id": "entity-c9-0001",
        "episode_id": "EP01",
        "scene_id": "SC01",
        "shot_id": "SC01_SH001",
        "canvas_id": "canvas-c9-0001",
        "job_id": "job-c9-0001",
        "asset_id": "asset-c9-0001",
        "version_id": version_id,
        "version": version,
        "media_type": "image",
        "source": {
            "provider": "comfyui",
            "source_url": "http://localhost:8188/view/asset-c9-0001-v001",
            "workflow_artifact_id": "artifact-workflow-c9-0001",
        },
    }


def _create(client: TestClient, **extra: object) -> dict:
    payload = {"title": "C9 单镜头", **_target(), "expected_version": 1, **extra}
    response = client.post("/api/asset-reviews/sessions", headers=AUTH, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["session"]


def _approve(client: TestClient, session: dict, decision: str = "approved") -> dict:
    response = client.put(
        f"/api/asset-reviews/sessions/{session['session_id']}/approval",
        headers=AUTH,
        json={
            "decision": decision,
            "version_id": session["version_id"],
            "version": session["version"],
            "expected_version": session["expected_version"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _deliver(client: TestClient, session: dict, approval: dict) -> dict:
    response = client.post(
        f"/api/asset-reviews/sessions/{session['session_id']}/delivery",
        headers=AUTH,
        json={
            "title": "C9 交付",
            "decision_id": approval["approval"]["decision_id"],
            "version_id": session["version_id"],
            "version": session["version"],
            "text_artifact_id": "artifact-text-c9-0001",
            "settings_artifact_id": "artifact-settings-c9-0001",
            "media_artifact_id": "artifact-media-c9-0001",
            "expected_version": approval["session"]["expected_version"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["delivery"]


def test_c9_version_change_invalidates_approval_and_delivery_with_next_action():
    with TestClient(create_app()) as client:
        session = _create(client)
        approval = _approve(client, session)
        delivery = _deliver(client, session, approval)

        before = client.get(
            f"/api/asset-reviews/sessions/{session['session_id']}", headers=AUTH
        ).json()
        newer = client.post(
            "/api/asset-reviews/sessions",
            headers=AUTH,
            json={
                **_target("asset-c9-0001-v002", version=2),
                "title": "C9 单镜头 v002",
                "expected_version": before["revision"],
            },
        )
        assert newer.status_code == 201, newer.text

        old = client.get(
            f"/api/asset-reviews/sessions/{session['session_id']}", headers=AUTH
        ).json()
        assert old["session"]["closure_status"] == "blocked"
        assert old["session"]["blocker"]["code"] == "APPROVAL_INVALIDATED"
        assert old["session"]["next_action"] == "review"
        assert old["approvals"][0]["closure_status"] == "blocked"
        assert old["approvals"][0]["blocker"]["code"] == "APPROVAL_INVALIDATED"
        assert old["approvals"][0]["next_action"] == "review"
        assert old["delivery"]["delivery_id"] == delivery["delivery_id"]
        assert old["delivery"]["closure_status"] == "blocked"
        assert old["delivery"]["blocker"]["code"] == "APPROVAL_INVALIDATED"
        assert old["delivery"]["next_action"] == "review"
        assert old["delivery"]["version_id"] == "asset-c9-0001-v001"


@pytest.mark.parametrize(
    ("provider_status", "projection"),
    [
        ("pending", ("blocked", "PROVIDER_PENDING", "wait_for_provider")),
        ("failed", ("blocked", "PROVIDER_FAILED", "regenerate_version")),
        ("unknown", ("unknown", "PROVIDER_UNKNOWN", "reconcile_unknown_job")),
    ],
)
def test_c9_provider_states_expose_blocker_and_safe_next_action(provider_status, projection):
    with TestClient(create_app()) as client:
        session = _create(client, provider_status=provider_status)
        assert (
            session["closure_status"],
            session["blocker"]["code"],
            session["next_action"],
        ) == projection
        response = client.put(
            f"/api/asset-reviews/sessions/{session['session_id']}/approval",
            headers=AUTH,
            json={
                "decision": "approved",
                "version_id": session["version_id"],
                "version": session["version"],
                "expected_version": session["expected_version"],
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "INVALID_STATE_TRANSITION"


def test_c9_changes_requested_is_blocked_and_delivery_cannot_claim_success():
    with TestClient(create_app()) as client:
        session = _create(client)
        approval = _approve(client, session, decision="changes_requested")
        assert approval["approval"]["closure_status"] == "blocked"
        assert approval["approval"]["next_action"] == "regenerate_version"
        assert approval["session"]["closure_status"] == "blocked"
        assert approval["session"]["next_action"] == "regenerate_version"
        assert approval["approval"]["blocker"]["code"] == "INVALID_STATE_TRANSITION"

        delivery = client.post(
            f"/api/asset-reviews/sessions/{session['session_id']}/delivery",
            headers=AUTH,
            json={
                "decision_id": approval["approval"]["decision_id"],
                "version_id": session["version_id"],
                "version": session["version"],
                "text_artifact_id": "artifact-text-c9-0001",
                "settings_artifact_id": "artifact-settings-c9-0001",
                "media_artifact_id": "artifact-media-c9-0001",
                "expected_version": approval["session"]["expected_version"],
            },
        )
        assert delivery.status_code == 409
        assert delivery.json()["detail"]["code"] == "INVALID_STATE_TRANSITION"


def test_c9_approved_delivery_is_closed_without_pending_action():
    with TestClient(create_app()) as client:
        session = _create(client)
        approval = _approve(client, session)
        delivery = _deliver(client, session, approval)
        detail = client.get(
            f"/api/asset-reviews/sessions/{session['session_id']}", headers=AUTH
        ).json()
        assert detail["session"]["closure_status"] == "closed"
        assert detail["session"]["blocker"] is None
        assert detail["session"]["next_action"] is None
        assert delivery["closure_status"] == "closed"
        assert delivery["blocker"] is None
        assert delivery["next_action"] is None
