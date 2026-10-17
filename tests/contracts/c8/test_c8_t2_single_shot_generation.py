# -*- coding: utf-8 -*-
"""C8-T2 单镜头真实生成与来源绑定契约测试。

测试替身只验证 API 的持久化、幂等和失败关闭边界；C8-AC1 的真实
ComfyUI/RunningHub 运行证据仍需使用真实执行器单独收集。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.api import routes_episode_pipeline_b7 as episode_routes
from gw.core.errors import CleanroomException


AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


def _create_pipeline(client: TestClient, project_id: str = "proj-01") -> str:
    response = client.post(
        "/api/episode-pipelines",
        headers=AUTH,
        json={"project_id": project_id, "title": "C8 单镜头", "seed": "真实镜头"},
    )
    assert response.status_code == 201
    return response.json()["pipeline_id"]


def _payload(project_id: str = "proj-01") -> dict:
    return {
        "workflow_id": "workflow-c8-fixture-01",
        "target": "local_comfy",
        "node_info_list": [
            {"nodeId": "6", "fieldName": "text", "fieldValue": "cinematic single shot"}
        ],
        "source_context": {
            "project_id": project_id,
            "canvas_id": "canvas-c8-fixture-01",
            "entity_id": "entity-c8-fixture-01",
            "episode_id": "EP01",
            "scene_id": "SC01",
        },
    }


def _path(pipeline_id: str, shot_id: str = "SC01_SH001") -> str:
    return f"/api/episode-pipelines/{pipeline_id}/shots/{shot_id}/generate"


def _read_path(pipeline_id: str, shot_id: str = "SC01_SH001") -> str:
    return f"/api/episode-pipelines/{pipeline_id}/shots/{shot_id}"


def test_single_shot_requires_auth_and_idempotency_key():
    with TestClient(create_app()) as client:
        pipeline_id = _create_pipeline(client)
        unauthenticated = client.post(_path(pipeline_id), json=_payload())
        assert unauthenticated.status_code == 401

        missing_key = client.post(_path(pipeline_id), headers=AUTH, json=_payload())
        assert missing_key.status_code == 400
        assert missing_key.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_single_shot_binds_real_job_asset_and_source_after_poll(monkeypatch):
    calls = {"submit": 0, "poll": 0}

    async def fake_execute(request, authorization=None, x_user_role="editor", idempotency_key=None):
        calls["submit"] += 1
        return {
            "job_id": "job-c8-fixture-001",
            "task_id": "job-c8-fixture-001",
            "status": "accepted",
            "target": "local_comfy",
            "remote_task_id": "comfy-prompt-c8-001",
            "remote_id_missing": False,
            "source_context": {"project_id": "proj-01", "canvas_id": "canvas-c8-fixture-01"},
            "data_status": "ok",
            "data_gaps": [],
        }

    async def fake_status(task_id, authorization=None, x_user_role="editor"):
        calls["poll"] += 1
        assert task_id == "job-c8-fixture-001"
        return {
            "job_id": task_id,
            "task_id": task_id,
            "status": "completed",
            "execution_status": "completed",
            "target": "local_comfy",
            "remote_task_id": "comfy-prompt-c8-001",
            "source_context": {"project_id": "proj-01", "canvas_id": "canvas-c8-fixture-01"},
            "outputs": [
                {
                    "asset_id": "asset-c8-fixture-001",
                    "registered": True,
                    "url": "/api/assets/asset-c8-fixture-001/content",
                    "media_type": "video/mp4",
                }
            ],
            "data_status": "ok",
            "data_gaps": [],
        }

    monkeypatch.setattr(episode_routes.workflow_routes, "execute_task", fake_execute)
    monkeypatch.setattr(episode_routes.workflow_routes, "task_status", fake_status)

    with TestClient(create_app()) as client:
        pipeline_id = _create_pipeline(client)
        headers = {**AUTH, "Idempotency-Key": "c8-shot-fixture-001"}
        submitted = client.post(_path(pipeline_id), headers=headers, json=_payload())
        assert submitted.status_code == 202
        shot = submitted.json()["shot"]
        assert shot["project_id"] == "proj-01"
        assert shot["shot_id"] == "SC01_SH001"
        assert shot["job_id"] == "job-c8-fixture-001"
        assert shot["status"] == "queued"
        assert shot["next_action"] == "poll_job"
        assert calls["submit"] == 1

        replay = client.post(_path(pipeline_id), headers=headers, json=_payload())
        assert replay.status_code == 202
        assert replay.json()["shot"]["job_id"] == shot["job_id"]
        assert calls["submit"] == 1

        read = client.get(_read_path(pipeline_id), headers=AUTH)
        assert read.status_code == 200
        current = read.json()["shot"]
        assert calls["poll"] == 1
        assert current["status"] == "completed"
        assert current["next_action"] == "review_source"
        assert current["asset_id"] == "asset-c8-fixture-001"
        assert current["version_id"] == "asset-c8-fixture-001-v001"
        assert current["version"] == 1
        assert current["source"] == {
            "provider": "local_comfy",
            "source_url": "/api/assets/asset-c8-fixture-001/content",
            "workflow_artifact_id": "workflow-c8-fixture-01",
        }

        pipeline = client.get(f"/api/episode-pipelines/{pipeline_id}", headers=AUTH).json()["pipeline"]
        assert pipeline["shots"][0]["version_id"] == "asset-c8-fixture-001-v001"


def test_single_shot_idempotency_conflict_and_unknown_state(monkeypatch):
    async def fake_execute(request, authorization=None, x_user_role="editor", idempotency_key=None):
        return {
            "job_id": "job-c8-unknown-001",
            "task_id": "job-c8-unknown-001",
            "status": "outcome_unknown",
            "execution_status": "unknown",
            "target": "rh_cloud",
            "remote_task_id": None,
            "remote_id_missing": True,
            "source_context": {"project_id": "proj-01"},
            "data_status": "ok",
            "data_gaps": ["remote_task_id_missing"],
        }

    monkeypatch.setattr(episode_routes.workflow_routes, "execute_task", fake_execute)

    with TestClient(create_app()) as client:
        pipeline_id = _create_pipeline(client)
        headers = {**AUTH, "Idempotency-Key": "c8-shot-unknown-001"}
        unknown = client.post(_path(pipeline_id), headers=headers, json=_payload())
        assert unknown.status_code == 202
        shot = unknown.json()["shot"]
        assert shot["status"] == "unknown"
        assert shot["next_action"] == "reconcile_provider"
        assert shot["job_id"] == "job-c8-unknown-001"
        assert "asset_id" not in shot
        assert "version_id" not in shot

        conflict_payload = _payload()
        conflict_payload["node_info_list"][0]["fieldValue"] = "different prompt"
        conflict = client.post(_path(pipeline_id), headers=headers, json=conflict_payload)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_single_shot_missing_remote_identity_never_binds_registered_output(monkeypatch):
    async def fake_execute(request, authorization=None, x_user_role="editor", idempotency_key=None):
        return {
            "job_id": "job-c8-no-remote-001",
            "task_id": "job-c8-no-remote-001",
            "status": "accepted",
            "target": "local_comfy",
            "remote_task_id": None,
            "remote_id_missing": True,
            "data_gaps": ["remote_task_id_missing"],
            "outputs": [{"asset_id": "asset-must-not-bind", "registered": True}],
        }

    monkeypatch.setattr(episode_routes.workflow_routes, "execute_task", fake_execute)

    with TestClient(create_app()) as client:
        pipeline_id = _create_pipeline(client)
        response = client.post(
            _path(pipeline_id),
            headers={**AUTH, "Idempotency-Key": "c8-shot-no-remote-001"},
            json=_payload(),
        )
        assert response.status_code == 202
        shot = response.json()["shot"]
        assert shot["status"] == "unknown"
        assert shot["next_action"] == "reconcile_provider"
        assert shot["job_id"] == "job-c8-no-remote-001"
        assert "asset_id" not in shot
        assert "version_id" not in shot


def test_single_shot_idempotent_replay_ignores_advanced_pipeline_version(monkeypatch):
    calls = {"submit": 0}

    async def fake_execute(request, authorization=None, x_user_role="editor", idempotency_key=None):
        calls["submit"] += 1
        return {
            "job_id": "job-c8-cas-replay-001",
            "task_id": "job-c8-cas-replay-001",
            "status": "accepted",
            "target": "local_comfy",
            "remote_task_id": "comfy-cas-replay-001",
        }

    monkeypatch.setattr(episode_routes.workflow_routes, "execute_task", fake_execute)

    with TestClient(create_app()) as client:
        pipeline_id = _create_pipeline(client)
        headers = {**AUTH, "Idempotency-Key": "c8-shot-cas-replay-001"}
        payload = {**_payload(), "expected_version": 1}
        first = client.post(_path(pipeline_id), headers=headers, json=payload)
        assert first.status_code == 202
        replay = client.post(_path(pipeline_id), headers=headers, json=payload)
        assert replay.status_code == 202
        assert replay.json()["shot"]["job_id"] == "job-c8-cas-replay-001"
        assert calls["submit"] == 1


def test_single_shot_executor_failure_is_explicit_and_not_success(monkeypatch):
    async def unavailable(request, authorization=None, x_user_role="editor", idempotency_key=None):
        raise CleanroomException(503, "WORKFLOW_EXECUTOR_UNAVAILABLE", "ComfyUI 未连接")

    monkeypatch.setattr(episode_routes.workflow_routes, "execute_task", unavailable)

    with TestClient(create_app()) as client:
        pipeline_id = _create_pipeline(client)
        response = client.post(
            _path(pipeline_id),
            headers={**AUTH, "Idempotency-Key": "c8-shot-unavailable-001"},
            json=_payload(),
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "WORKFLOW_EXECUTOR_UNAVAILABLE"

        read = client.get(_read_path(pipeline_id), headers=AUTH)
        assert read.status_code == 200
        shot = read.json()["shot"]
        assert shot["status"] == "waiting"
        assert shot["next_action"] == "configure_executor"
        assert "job_id" not in shot
        assert "asset_id" not in shot
        assert "version_id" not in shot
