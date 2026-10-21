"""C8-T6：审阅版本绑定与交付记录回读契约。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gw.api.app import create_app

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


def _target(version_id: str = "asset-c8-0001-v001", version: int = 1) -> dict:
    return {
        "project_id": "project-c8-0001",
        "entity_id": "entity-c8-0001",
        "episode_id": "EP01",
        "scene_id": "SC01",
        "shot_id": "SC01_SH001",
        "canvas_id": "canvas-c8-0001",
        "job_id": "job-c8-0001",
        "asset_id": "asset-c8-0001",
        "version_id": version_id,
        "version": version,
        "media_type": "image",
        "source": {
            "provider": "comfyui",
            "source_url": "http://localhost:8188/view/asset-c8-0001-v001",
            "workflow_artifact_id": "artifact-workflow-c8-0001",
        },
    }


def _create_session(client: TestClient, *, target: dict | None = None) -> dict:
    payload = {"title": "C8 单镜头", **(target or _target()), "expected_version": 1}
    response = client.post("/api/asset-reviews/sessions", headers=AUTH, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["session"]


def test_c8_t6_approval_and_delivery_bind_exact_version_and_all_relations():
    with TestClient(create_app()) as client:
        session = _create_session(client)
        assert session["review_id"] == session["session_id"]
        assert session["version_id"] == "asset-c8-0001-v001"
        assert session["expected_version"] == 1

        comment = client.post(
            f"/api/asset-reviews/sessions/{session['session_id']}/comments",
            headers=AUTH,
            json={
                "body": "保留当前版本",
                "reviewer_id": "account-c8-0001",
                "version_id": session["version_id"],
                "version": 1,
                "expected_version": session["expected_version"],
            },
        )
        assert comment.status_code == 201, comment.text
        comment_data = comment.json()["comment"]
        assert comment_data["review_id"] == session["session_id"]
        assert comment_data["version_id"] == session["version_id"]

        approval = client.put(
            f"/api/asset-reviews/sessions/{session['session_id']}/approval",
            headers=AUTH,
            json={
                "decision": "approved",
                "reviewer_id": "account-c8-0001",
                "version_id": session["version_id"],
                "version": 1,
                "expected_version": comment_data["expected_version"],
            },
        )
        assert approval.status_code == 200, approval.text
        approval_data = approval.json()["approval"]
        assert approval_data["decision_id"].startswith("rd-")
        assert approval_data["decision"] == "approved"
        assert approval_data["next_action"] == "deliver"

        delivery = client.post(
            f"/api/asset-reviews/sessions/{session['session_id']}/delivery",
            headers=AUTH,
            json={
                "title": "C8 交付",
                "decision_id": approval_data["decision_id"],
                "version_id": session["version_id"],
                "version": 1,
                "text_artifact_id": "artifact-text-c8-0001",
                "settings_artifact_id": "artifact-settings-c8-0001",
                "media_artifact_id": "artifact-media-c8-0001",
                "expected_version": approval.json()["session"]["expected_version"],
            },
        )
        assert delivery.status_code == 201, delivery.text
        record = delivery.json()["delivery"]
        assert record["project_id"] == "project-c8-0001"
        assert record["shot_id"] == "SC01_SH001"
        assert record["job_id"] == "job-c8-0001"
        assert record["asset_id"] == "asset-c8-0001"
        assert record["version_id"] == session["version_id"]
        assert record["review_id"] == session["session_id"]
        assert record["decision_id"] == approval_data["decision_id"]
        assert record["delivery_status"] == "delivered"
        assert record["text_artifact_id"] == "artifact-text-c8-0001"

        detail = client.get(
            f"/api/asset-reviews/sessions/{session['session_id']}",
            headers=AUTH,
        )
        assert detail.status_code == 200
        body = detail.json()
        assert body["delivery"]["delivery_id"] == record["delivery_id"]
        assert body["delivery"]["version_id"] == session["version_id"]
        assert body["approvals"][0]["decision_id"] == approval_data["decision_id"]

        # 新版本不会改写旧交付的版本快照，旧批准会明确失效。
        current_revision = body["revision"]
        newer = _target("asset-c8-0001-v002", version=2)
        newer_response = client.post(
            "/api/asset-reviews/sessions",
            headers=AUTH,
            json={
                **newer,
                "title": "C8 单镜头 v002",
                "expected_version": current_revision,
            },
        )
        assert newer_response.status_code == 201, newer_response.text

        old_detail = client.get(
            f"/api/asset-reviews/sessions/{session['session_id']}",
            headers=AUTH,
        ).json()
        assert old_detail["approvals"][0]["review_status"] == "approval_invalidated"
        assert old_detail["delivery"]["delivery_status"] == "superseded"
        assert old_detail["delivery"]["version_id"] == "asset-c8-0001-v001"


def test_c8_t6_rejects_stale_version_and_unapproved_delivery():
    with TestClient(create_app()) as client:
        session = _create_session(client)
        stale = client.put(
            f"/api/asset-reviews/sessions/{session['session_id']}/approval",
            headers=AUTH,
            json={
                "decision": "approved",
                "version_id": "asset-c8-0001-v999",
                "version": 999,
                "expected_version": session["expected_version"],
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"

        delivery = client.post(
            f"/api/asset-reviews/sessions/{session['session_id']}/delivery",
            headers=AUTH,
            json={
                "version_id": session["version_id"],
                "version": 1,
                "text_artifact_id": "artifact-text-c8-0001",
                "settings_artifact_id": "artifact-settings-c8-0001",
                "media_artifact_id": "artifact-media-c8-0001",
                "expected_version": session["expected_version"],
            },
        )
        assert delivery.status_code == 409
        assert delivery.json()["detail"]["code"] in {
            "INVALID_STATE_TRANSITION",
            "VERSION_CONFLICT",
        }


def test_c8_t6_rejects_expected_version_conflict_without_mutating_review():
    with TestClient(create_app()) as client:
        session = _create_session(client)
        response = client.post(
            f"/api/asset-reviews/sessions/{session['session_id']}/comments",
            headers=AUTH,
            json={
                "body": "过期写入",
                "version_id": session["version_id"],
                "version": 1,
                "expected_version": session["expected_version"] - 1,
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "VERSION_CONFLICT"

        detail = client.get(
            f"/api/asset-reviews/sessions/{session['session_id']}",
            headers=AUTH,
        ).json()
        assert detail["comments"] == []


def test_c8_t6_delivery_idempotency_keeps_exact_snapshot():
    with TestClient(create_app()) as client:
        session = _create_session(client)
        approval = client.put(
            f"/api/asset-reviews/sessions/{session['session_id']}/approval",
            headers=AUTH,
            json={
                "decision": "approved",
                "version_id": session["version_id"],
                "version": 1,
                "expected_version": session["expected_version"],
            },
        )
        assert approval.status_code == 200, approval.text
        approval_data = approval.json()["approval"]
        payload = {
            "delivery_id": "dlv-c8-fixed-0001",
            "decision_id": approval_data["decision_id"],
            "version_id": session["version_id"],
            "version": 1,
            "text_artifact_id": "artifact-text-c8-0001",
            "settings_artifact_id": "artifact-settings-c8-0001",
            "media_artifact_id": "artifact-media-c8-0001",
            "expected_version": approval.json()["session"]["expected_version"],
        }
        first = client.post(
            f"/api/asset-reviews/sessions/{session['session_id']}/delivery",
            headers=AUTH,
            json=payload,
        )
        assert first.status_code == 201, first.text
        second = client.post(
            f"/api/asset-reviews/sessions/{session['session_id']}/delivery",
            headers=AUTH,
            json={**payload, "expected_version": 1},
        )
        assert second.status_code == 201, second.text
        assert second.json()["delivery"] == first.json()["delivery"]


def test_c8_t6_changes_requested_exposes_next_action_and_blocks_delivery():
    with TestClient(create_app()) as client:
        session = _create_session(client)
        response = client.put(
            f"/api/asset-reviews/sessions/{session['session_id']}/approval",
            headers=AUTH,
            json={
                "decision": "changes_requested",
                "version_id": session["version_id"],
                "version": 1,
                "expected_version": session["expected_version"],
            },
        )
        assert response.status_code == 200, response.text
        approval = response.json()["approval"]
        assert approval["review_status"] == "changes_requested"
        assert approval["next_action"] == "regenerate_version"
        delivery = client.post(
            f"/api/asset-reviews/sessions/{session['session_id']}/delivery",
            headers=AUTH,
            json={
                "decision_id": approval["decision_id"],
                "version_id": session["version_id"],
                "version": 1,
                "text_artifact_id": "artifact-text-c8-0001",
                "settings_artifact_id": "artifact-settings-c8-0001",
                "media_artifact_id": "artifact-media-c8-0001",
                "expected_version": response.json()["session"]["expected_version"],
            },
        )
        assert delivery.status_code == 409
        assert delivery.json()["detail"]["code"] == "INVALID_STATE_TRANSITION"
