# -*- coding: utf-8 -*-
"""C12-AC3 OpenFilm timeline_manifest 契约测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gw.api.app import create_app

A = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
R = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "readonly"}


def _pipeline(client: TestClient) -> str:
    response = client.post(
        "/api/episode-pipelines",
        headers=A,
        json={"project_id": "proj-timeline", "title": "C12 timeline"},
    )
    assert response.status_code == 201
    return response.json()["pipeline_id"]


def _manifest_payload() -> dict:
    return {
        "tracks": [
            {
                "track_id": "video-01",
                "kind": "video",
                "clips": [
                    {
                        "shot_id": "SC01_SH001",
                        "asset_id": "asset-shot-0001",
                        "at": 0.0,
                        "duration": 2.5,
                        "overrides": {
                            "src": "/assets/asset-shot-0001.png#t=0,2.5",
                            "alt": "SC01_SH001",
                        },
                    },
                    {
                        "shot_id": "SC01_SH002",
                        "asset_id": "asset-shot-0002",
                        "at": 2.5,
                        "duration": 3.0,
                        "overrides": {
                            "src": "/assets/asset-shot-0002.png#t=0,3",
                        },
                    },
                ],
            }
        ]
    }


def test_timeline_manifest_requires_auth_and_write_access():
    with TestClient(create_app()) as client:
        unauthenticated = client.get("/api/episode-pipelines/ep-0001/timeline-manifest")
        assert unauthenticated.status_code == 401

        pipeline_id = _pipeline(client)
        readonly = client.post(
            f"/api/episode-pipelines/{pipeline_id}/timeline-manifest",
            headers=R,
            json=_manifest_payload(),
        )
        assert readonly.status_code == 403


def test_timeline_manifest_initializes_persists_and_reads_back():
    with TestClient(create_app()) as client:
        pipeline_id = _pipeline(client)
        created = client.post(
            f"/api/episode-pipelines/{pipeline_id}/timeline-manifest",
            headers=A,
            json=_manifest_payload(),
        )
        assert created.status_code == 201
        body = created.json()
        manifest = body["timeline_manifest"]
        assert manifest["manifest_id"].startswith("timeline-")
        assert manifest["pipeline_id"] == pipeline_id
        assert manifest["project_id"] == "proj-timeline"
        assert manifest["version"] == 1
        assert [clip["shot_id"] for clip in manifest["tracks"][0]["clips"]] == [
            "SC01_SH001",
            "SC01_SH002",
        ]
        assert manifest["tracks"][0]["clips"][0]["at"] == 0.0
        assert manifest["tracks"][0]["clips"][0]["duration"] == 2.5
        assert manifest["tracks"][0]["clips"][0]["overrides"]["src"].endswith("#t=0,2.5")

        reread = client.get(
            f"/api/episode-pipelines/{pipeline_id}/timeline-manifest",
            headers=A,
        )
        assert reread.status_code == 200
        assert reread.json()["timeline_manifest"] == manifest


def test_timeline_manifest_update_requires_cas_and_rejects_stale_version():
    with TestClient(create_app()) as client:
        pipeline_id = _pipeline(client)
        created = client.post(
            f"/api/episode-pipelines/{pipeline_id}/timeline-manifest",
            headers=A,
            json=_manifest_payload(),
        )
        assert created.status_code == 201

        payload = _manifest_payload()
        payload["tracks"][0]["clips"][0]["duration"] = 4.0
        updated = client.put(
            f"/api/episode-pipelines/{pipeline_id}/timeline-manifest",
            headers=A,
            json={**payload, "expected_version": 1},
        )
        assert updated.status_code == 200
        assert updated.json()["timeline_manifest"]["version"] == 2
        assert updated.json()["timeline_manifest"]["tracks"][0]["clips"][0]["duration"] == 4.0

        missing = client.put(
            f"/api/episode-pipelines/{pipeline_id}/timeline-manifest",
            headers=A,
            json=payload,
        )
        assert missing.status_code == 400
        assert missing.json()["detail"]["code"] == "EXPECTED_VERSION_REQUIRED"

        stale = client.put(
            f"/api/episode-pipelines/{pipeline_id}/timeline-manifest",
            headers=A,
            json={**payload, "expected_version": 1},
        )
        assert stale.status_code == 409
        detail = stale.json()["detail"]
        assert detail["code"] == "VERSION_CONFLICT"
        assert detail["expected_version"] == 1
        assert detail["current_version"] == 2


def test_timeline_manifest_rejects_ambiguous_shots_and_invalid_timing():
    with TestClient(create_app()) as client:
        pipeline_id = _pipeline(client)
        invalid = _manifest_payload()
        invalid["tracks"][0]["clips"][0]["duration"] = 0
        response = client.post(
            f"/api/episode-pipelines/{pipeline_id}/timeline-manifest",
            headers=A,
            json=invalid,
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_TIMELINE_MANIFEST"

        duplicate = _manifest_payload()
        duplicate["tracks"][0]["clips"][1]["shot_id"] = "SC01_SH001"
        response = client.post(
            f"/api/episode-pipelines/{pipeline_id}/timeline-manifest",
            headers=A,
            json=duplicate,
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_TIMELINE_MANIFEST"
