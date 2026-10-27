"""MVP1-T3：镜头元数据、内存关键帧和重格式代理决策。"""

from __future__ import annotations

import threading
from pathlib import Path

from gw.asset_registry import index_jobs, repository
from gw.core import storage
from gw.core.errors import CleanroomException
from gw.media import keyframe_extractor, proxy, scene_detector


def _scenes() -> list[dict]:
    common = {
        "width": 3840,
        "height": 2160,
        "codec": "prores",
        "bit_rate": 120_000_000,
        "container": "mov",
        "source_url": None,
        "tags": [],
        "thumbnail": None,
    }
    return [
        {**common, "start_sec": 0.0, "end_sec": 2.0, "duration_sec": 2.0},
        {**common, "start_sec": 2.0, "end_sec": 4.0, "duration_sec": 2.0},
    ]


def test_index_scan_decodes_each_keyframe_in_memory_and_keeps_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    video = root / "source.mov"
    video.write_bytes(b"video")
    original_files = set(root.iterdir())
    decoded_at: list[float] = []
    scenes = _scenes()

    monkeypatch.setattr(scene_detector, "detect_scenes", lambda _path: scenes)

    def decode(_path, at_sec):
        decoded_at.append(at_sec)
        return b"in-memory-png"

    monkeypatch.setattr(keyframe_extractor, "decode_keyframe_to_memory", decode)

    scanner = object.__new__(index_jobs.IndexJobs)
    scanner.condition = threading.Condition(threading.RLock())
    monkeypatch.setattr(scanner, "_checkpoint", lambda *_args: None)
    files = scanner._scan(
        "mvp1-t3-test",
        1,
        {"root-1": root},
        {
            "roots": ["root-1"],
            "scopes": [
                {
                    "root_id": "root-1",
                    "prefix": "",
                    "storage_root_id": "root-1",
                }
            ],
            "hash_files": False,
            "max_files": 10,
        },
    )

    assert len(files) == 1
    item = files[0]
    assert decoded_at == [1.0, 3.0]
    assert item["duration"] == 4.0
    assert item["metadata"]["video_segments"] == scenes
    assert item["metadata"]["video_metadata"] == {
        "width": 3840,
        "height": 2160,
        "duration_sec": 4.0,
        "codec": "prores",
        "bit_rate": 120_000_000,
        "container": "mov",
        "source_url": None,
        "scene_detection_status": "complete",
        "keyframe_status": "decoded_in_memory",
    }
    assert not any(isinstance(value, bytes) for value in item.values())
    assert set(root.iterdir()) == original_files


def test_index_scan_records_keyframe_degradation_without_frame_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    scenes = _scenes()
    monkeypatch.setattr(scene_detector, "detect_scenes", lambda _path: scenes)

    def fail_decode(_path, _at_sec):
        raise CleanroomException(503, "KEYFRAME_EXTRACTION_FAILED", "解码失败")

    monkeypatch.setattr(keyframe_extractor, "decode_keyframe_to_memory", fail_decode)

    metadata, duration = index_jobs._video_index_data(video)

    assert duration == 4.0
    assert metadata["video_segments"] == scenes
    assert metadata["video_metadata"]["scene_detection_status"] == "complete"
    assert metadata["video_metadata"]["keyframe_status"] == "unavailable"
    assert metadata["video_metadata"]["reason_code"] == "KEYFRAME_EXTRACTION_FAILED"
    assert set(tmp_path.iterdir()) == {video}


def test_commit_persists_scanned_video_metadata(monkeypatch) -> None:
    raw = {"assets": {}, "revision": 0}

    class FakeState:
        def mutate(self, callback):
            return callback(raw)

    scanner = object.__new__(index_jobs.IndexJobs)
    monkeypatch.setattr(scanner, "_check", lambda *_args: None)
    monkeypatch.setattr(repository, "state", lambda: FakeState())
    monkeypatch.setattr(repository, "_asset_sequence_start", lambda _raw: 1)
    monkeypatch.setattr(repository, "_upsert_asset_search_documents", lambda _assets: [])
    monkeypatch.setattr(storage, "now_iso", lambda: "2026-10-27T00:00:00+00:00")
    monkeypatch.setattr(scene_detector, "detect_scenes", lambda _path: _scenes())
    monkeypatch.setattr(
        keyframe_extractor, "decode_keyframe_to_memory", lambda *_args: b"frame"
    )
    monkeypatch.setattr(
        repository,
        "_bump",
        lambda current: current.__setitem__("revision", current["revision"] + 1)
        or current["revision"],
    )
    metadata, duration = index_jobs._video_index_data(Path("source.mov"))
    job = {
        "job_id": "mvp1-t3-test",
        "attempt": 1,
        "_request": {"kind": "reindex"},
    }

    result = scanner._commit(
        job,
        [
            {
                "root_id": "root-1",
                "storage_root_id": "root-1",
                "display_path": "source.mov",
                "canonical_relpath": "source.mov",
                "name": "source.mov",
                "size_bytes": 5,
                "duration": duration,
                "metadata": metadata,
            }
        ],
    )

    assert result["indexed"] == 1
    record = raw["assets"]["ast_0001"]
    assert record["metadata"] == metadata
    assert record["duration"] == duration


def test_720p_proxy_decision_targets_only_heavy_video_sources() -> None:
    assert not proxy.requires_720p_proxy(
        {
            "width": 1920,
            "height": 1080,
            "codec": "h264",
            "container": "mp4",
            "bit_rate": 25_000_000,
        }
    )
    assert proxy.requires_720p_proxy(
        {"width": 3840, "height": 2160, "codec": "h264", "container": "mp4"}
    )
    assert proxy.requires_720p_proxy(
        {"width": 1920, "height": 1080, "codec": "prores_ks", "container": "mov"}
    )
    assert proxy.requires_720p_proxy(
        {
            "width": 1920,
            "height": 1080,
            "codec": "h264",
            "container": "mov",
            "bit_rate": 100_000_000,
        }
    )


def test_build_proxy_uses_720p_only_for_heavy_video(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.mov"
    source.write_bytes(b"video")
    output_dir = tmp_path / "proxy"
    output_dir.mkdir()
    ffmpeg_calls: list[tuple[int, int | None]] = []

    monkeypatch.setattr(proxy, "_source", lambda _payload: (source, None, None))
    monkeypatch.setattr(proxy, "_profile", lambda _payload: ("preview", 1280))
    monkeypatch.setattr(
        proxy,
        "_probe_video_metadata",
        lambda _path: {
            "width": 3840,
            "height": 2160,
            "codec": "prores",
            "container": "mov",
            "bit_rate": 120_000_000,
        },
    )
    monkeypatch.setattr(proxy, "_output_dir", lambda: output_dir)
    monkeypatch.setattr(proxy, "_identity", lambda *_args: "a" * 32)

    def fake_ffmpeg(_path, width, target, *, target_height=None):
        ffmpeg_calls.append((width, target_height))
        target.write_bytes(b"proxy")
        return {
            "path": target,
            "media_type": "video/mp4",
            "width": width,
            "height": target_height,
        }

    monkeypatch.setattr(proxy, "_ffmpeg_proxy", fake_ffmpeg)

    result = proxy.build_proxy({"path": str(source)})

    assert ffmpeg_calls == [(1280, 720)]
    assert result["width"] == 1280
    assert result["height"] == 720
