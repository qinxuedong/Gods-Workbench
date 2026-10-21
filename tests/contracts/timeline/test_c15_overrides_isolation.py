"""C15-AC1: 视频原位替换须保留人工 overrides，并通过 timeline CAS 持久化。"""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core.errors import VersionConflictException
from gw.episode_pipeline.service import relink_timeline_video, replace_timeline_clip_with_video


A = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


def _pipeline(client: TestClient) -> str:
    response = client.post(
        "/api/episode-pipelines",
        headers=A,
        json={"project_id": "proj-c15-relink", "title": "C15 relink"},
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
                        "asset_id": "asset-shot-image-0001",
                        "media_type": "image",
                        "at": 0.0,
                        "duration": 2.5,
                        "overrides": {
                            "src": "/assets/asset-shot-image-0001.png#t=0,2.5",
                            "alt": "SC01_SH001",
                            "lut": "golden-hour.cube",
                            "crop": {"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.7},
                            "transition": {"type": "dissolve", "duration": 0.4},
                        },
                    },
                    {
                        "shot_id": "SC01_SH002",
                        "asset_id": "asset-shot-image-0002",
                        "media_type": "image",
                        "at": 2.5,
                        "duration": 3.0,
                        "overrides": {"src": "/assets/asset-shot-image-0002.png#t=0,3"},
                    },
                ],
            }
        ],
        "metadata": {"editor": "user"},
    }


def test_c15_ac1_video_relink_preserves_overrides_and_persists_with_cas():
    with TestClient(create_app()) as client:
        pipeline_id = _pipeline(client)
        created = client.post(
            f"/api/episode-pipelines/{pipeline_id}/timeline-manifest",
            headers=A,
            json=_manifest_payload(),
        )
        assert created.status_code == 201
        original = created.json()["timeline_manifest"]
        original_snapshot = deepcopy(original)
        relink = {
            "shot_id": "SC01_SH001",
            "video_asset_id": "asset-shot-video-0001",
            "video_src": "/assets/asset-shot-video-0001.mp4#t=0,2.5",
        }

        transformed = replace_timeline_clip_with_video(original, **relink)

        # Relink must be a non-mutating transformation; all manual adjustments survive.
        assert original == original_snapshot
        target = transformed["tracks"][0]["clips"][0]
        assert target["shot_id"] == "SC01_SH001"
        assert target["asset_id"] == "asset-shot-video-0001"
        assert target["media_type"] == "video"
        assert target["at"] == 0.0
        assert target["duration"] == 2.5
        assert target["overrides"] == {
            "src": "/assets/asset-shot-video-0001.mp4#t=0,2.5",
            "alt": "SC01_SH001",
            "lut": "golden-hour.cube",
            "crop": {"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.7},
            "transition": {"type": "dissolve", "duration": 0.4},
        }
        assert transformed["tracks"][0]["clips"][1] == original["tracks"][0]["clips"][1]

        persisted = relink_timeline_video(
            pipeline_id,
            **relink,
            expected_version=original["version"],
        )
        assert persisted["timeline_manifest"]["version"] == original["version"] + 1
        assert persisted["timeline_manifest"]["tracks"][0]["clips"][0] == target
        assert persisted["timeline_manifest"]["metadata"] == original["metadata"]

        api_json = client.get(
            f"/api/episode-pipelines/{pipeline_id}/timeline-manifest",
            headers=A,
        ).json()
        assert api_json["timeline_manifest"] == persisted["timeline_manifest"]

        with pytest.raises(VersionConflictException):
            relink_timeline_video(pipeline_id, **relink, expected_version=original["version"])
