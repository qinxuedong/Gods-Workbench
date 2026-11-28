"""QIN-657：VLM 关键帧描述、异步入口与 FTS5 回写契约。"""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace
from pathlib import Path

import pytest
from PIL import Image

from gw.api import routes_asset_registry
from gw.asset_registry import repository as repo
from gw.asset_registry import video_descriptions
from gw.asset_registry.index_jobs import IndexJobs
from gw.core import storage
from gw.core.errors import CleanroomException
from gw.core.sqlite_vec_engine import SQLiteVecEngine
from gw.settings import chat as chat_runtime

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


def _jpeg(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(output, format="JPEG")
    return output.getvalue()


def test_video_description_settings_default_to_manual_and_validate_values():
    settings = video_descriptions.get_settings()
    assert settings["auto_enabled"] is False
    assert settings["provider_id"] == ""
    assert settings["model"] == ""

    saved = video_descriptions.set_settings(
        {
            "auto_enabled": False,
            "provider_id": "provider-local",
            "model": "vision-model",
            "prompt": "  只描述镜头内可见内容  ",
        }
    )
    assert saved["prompt"] == "只描述镜头内可见内容"
    with pytest.raises(CleanroomException) as exc:
        video_descriptions.validate_settings(
            {"auto_enabled": "true", "provider_id": "", "model": "", "prompt": ""}
        )
    assert exc.value.code == "INVALID_REQUEST"


def test_vlm_gateway_rejects_remote_image_urls_before_provider_lookup():
    with pytest.raises(CleanroomException) as exc:
        chat_runtime.complete_messages(
            {},
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "描述镜头"},
                        {"type": "image_url", "image_url": {"url": "https://example.invalid/frame.jpg"}},
                    ],
                }
            ],
            endpoint="/api/asset-registry/video-descriptions/jobs",
        )
    assert exc.value.code == "INVALID_REQUEST"


def test_vlm_gateway_accepts_only_bounded_inline_jpeg_and_forwards_it(monkeypatch):
    captured = {}
    response_body = {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, *, json, headers):
            captured.update(url=url, body=json, headers=headers)
            return SimpleNamespace(status_code=200, json=lambda: response_body)

    monkeypatch.setattr(
        chat_runtime,
        "_resolve_config",
        lambda *_args: {
            "provider_id": "provider-local",
            "model": "vision-model",
            "api_key": "server-secret",
            "base_url": "https://provider.example/v1",
        },
    )
    monkeypatch.setattr("gw.settings.probes.guard_provider_url", lambda value, **_kwargs: value)
    monkeypatch.setattr("gw.settings.probes.is_lan_provider_url", lambda _value: False)
    monkeypatch.setattr(chat_runtime, "_httpx", lambda *_args: SimpleNamespace(Client=lambda **_kwargs: Client()))
    image_url = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xffframe").decode("ascii")

    result = chat_runtime.complete_messages(
        {"provider_id": "provider-local", "model": "vision-model"},
        [
            {"role": "system", "content": "只返回 JSON"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "描述镜头"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
        endpoint="/api/asset-registry/video-descriptions/jobs",
    )

    assert result["reply"] == "ok"
    assert captured["body"]["messages"][1]["content"][1]["image_url"]["url"] == image_url
    assert captured["headers"]["Authorization"] == "Bearer server-secret"


def test_contact_sheet_uses_three_samples_and_deduplicates_rgb_frames(tmp_path: Path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    sampled = []

    def decode(_source: Path, seconds: float) -> bytes:
        sampled.append(seconds)
        return _jpeg((20, 40, 60))

    sheet = video_descriptions._contact_sheet(
        source,
        {"start_sec": 0.0, "end_sec": 4.0},
        decode,
    )
    with Image.open(io.BytesIO(sheet)) as image:
        assert image.size == (320, 180)
    assert sampled == pytest.approx([0.8, 2.0, 3.2])

    short = video_descriptions._sample_times({"start_sec": 7.0, "end_sec": 10.0})
    assert short == [8.5]
    with pytest.raises(video_descriptions.VideoDescriptionError) as exc:
        video_descriptions._contact_sheet(
            source,
            {"start_sec": 0.0, "end_sec": 2.0},
            lambda *_args: b"not-an-image",
        )
    assert exc.value.code == "KEYFRAME_EXTRACTION_FAILED"


def test_process_asset_calls_gateway_per_shot_and_keeps_failed_shot_values(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    version = video_descriptions._file_version(source)
    asset = {
        "asset_id": "asset_vlm_1",
        "id": "asset_vlm_1",
        "version": 3,
        "scan_version": version,
        "metadata": {
            "video_segments": [
                {"start_sec": 0.0, "end_sec": 4.0, "summary": "旧摘要一", "tags": ["旧标签"]},
                {"start_sec": 4.0, "end_sec": 6.0, "summary": "旧摘要二", "tags": ["旧标签"]},
            ]
        },
    }
    calls = []
    writes = []
    replies = iter(
        [
            {"reply": '{"summary":"夜色中的灯塔","tags":["灯塔"," 夜景 ","灯塔"]}'},
            {"reply": "not-json"},
        ]
    )

    monkeypatch.setattr(repo, "_asset_local_video_path", lambda _asset: source)
    monkeypatch.setattr(repo, "readonly_registry_snapshot", lambda: {"assets": {asset["asset_id"]: asset}})
    monkeypatch.setattr(repo, "visible_asset_from_snapshot", lambda snapshot, asset_id: snapshot["assets"].get(asset_id))

    def complete_messages(payload, messages, **kwargs):
        calls.append((payload, messages, kwargs))
        return next(replies)

    def update_descriptions(asset_id, *, expected_version, expected_scan_version, updates):
        writes.append((asset_id, expected_version, expected_scan_version, updates))
        for index, description in updates.items():
            asset["metadata"]["video_segments"][index].update(description)
        return True

    monkeypatch.setattr("gw.settings.chat.complete_messages", complete_messages)
    monkeypatch.setattr(repo, "update_video_segment_descriptions", update_descriptions)
    decoded = []

    def frame_decoder(_source: Path, seconds: float) -> bytes:
        decoded.append(seconds)
        return _jpeg((int(seconds * 20) % 255, 30, 60))

    result = video_descriptions.process_asset(
        asset,
        provider_id="provider-local",
        model="vision-model",
        prompt="只说画面",
        frame_decoder=frame_decoder,
        checkpoint=lambda: None,
    )

    assert result["generated"] == 1
    assert result["failed"] == 1
    assert result["errors"][0]["error_code"] == "VLM_OUTPUT_INVALID"
    assert decoded == pytest.approx([0.8, 2.0, 3.2, 5.0])
    assert len(calls) == 2
    assert calls[0][0] == {"provider_id": "provider-local", "model": "vision-model"}
    assert calls[0][1][0]["role"] == "system"
    assert '键必须严格为 summary 和 tags' in calls[0][1][0]["content"]
    content = calls[0][1][1]["content"]
    assert content[0] == {"type": "text", "text": "只说画面"}
    image_url = content[1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(image_url.split(",", 1)[1]).startswith(b"\xff\xd8\xff")
    assert calls[0][2]["endpoint"] == "/api/asset-registry/video-descriptions/jobs"
    assert writes[0][0:2] == ("asset_vlm_1", 3)
    assert writes[0][3] == {0: {"summary": "夜色中的灯塔", "tags": ["灯塔", "夜景"]}}
    assert asset["metadata"]["video_segments"][1]["summary"] == "旧摘要二"


def test_process_asset_stops_automatic_work_when_setting_is_disabled(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    version = video_descriptions._file_version(source)
    asset = {
        "asset_id": "asset_vlm_auto",
        "version": 1,
        "scan_version": version,
        "metadata": {"video_segments": [{"start_sec": 0.0, "end_sec": 2.0}]},
    }
    monkeypatch.setattr(repo, "_asset_local_video_path", lambda _asset: source)
    monkeypatch.setattr(repo, "readonly_registry_snapshot", lambda: {"assets": {asset["asset_id"]: asset}})
    monkeypatch.setattr(repo, "visible_asset_from_snapshot", lambda snapshot, asset_id: snapshot["assets"].get(asset_id))
    monkeypatch.setattr(video_descriptions, "get_settings", lambda: {"auto_enabled": False})
    monkeypatch.setattr(
        "gw.settings.chat.complete_messages",
        lambda *_args, **_kwargs: pytest.fail("自动打标关闭后不得调用模型"),
    )
    monkeypatch.setattr(
        repo,
        "update_video_segment_descriptions",
        lambda *_args, **_kwargs: pytest.fail("自动打标关闭后不得写入"),
    )

    result = video_descriptions.process_asset(
        asset,
        provider_id="provider-local",
        model="vision-model",
        prompt="只说画面",
        frame_decoder=lambda *_args: _jpeg((0, 0, 0)),
        checkpoint=lambda: None,
        is_auto=True,
    )
    assert result["generated"] == 0
    assert result["errors"][0]["error_code"] == "VLM_AUTO_DISABLED"


def test_auto_vlm_queue_skips_unchanged_and_hidden_assets(monkeypatch):
    current_version = {"size_bytes": 12, "mtime_ns": 20, "ino": 3, "dev": 1}
    changed_version = {"size_bytes": 13, "mtime_ns": 21, "ino": 3, "dev": 1}
    assets = {
        "asset_new": {
            "asset_id": "asset_new",
            "root_id": "root_visible",
            "canonical_relpath": "new/clip.mp4",
            "metadata": {"video_segments": [{"start_sec": 0, "end_sec": 2}]},
        },
        "asset_old": {
            "asset_id": "asset_old",
            "root_id": "root_visible",
            "canonical_relpath": "old/clip.mp4",
            "metadata": {"video_segments": [{"start_sec": 0, "end_sec": 2}]},
        },
        "asset_hidden": {
            "asset_id": "asset_hidden",
            "root_id": "root_hidden",
            "canonical_relpath": "hidden/clip.mp4",
            "metadata": {"video_segments": [{"start_sec": 0, "end_sec": 2}]},
        },
    }
    monkeypatch.setattr(
        video_descriptions,
        "get_settings",
        lambda: {
            "auto_enabled": True,
            "provider_id": "provider-local",
            "model": "vision-model",
            "prompt": "只看可见内容",
        },
    )
    monkeypatch.setattr(repo, "readonly_registry_snapshot", lambda: {"assets": assets})
    monkeypatch.setattr(
        repo,
        "visible_asset_from_snapshot",
        lambda _snapshot, asset_id: None if asset_id == "asset_hidden" else assets.get(asset_id),
    )
    submitted = []
    index_jobs = object.__new__(IndexJobs)

    def submit(kind, payload, actor, key):
        submitted.append((kind, payload, actor, key))
        return {"job_id": "job_auto_description"}

    index_jobs.submit = submit
    result = index_jobs._enqueue_auto_video_descriptions(
        {"job_id": "job_scan", "_actor": "local-user"},
        [
            {"scan_version": changed_version},
            {"scan_version": current_version},
            {"scan_version": changed_version},
        ],
        [
            {"index": 0, "root_id": "root_visible", "canonical_relpath": "new/clip.mp4", "want_video": True},
            {"index": 1, "root_id": "root_visible", "canonical_relpath": "old/clip.mp4", "want_video": True},
            {"index": 2, "root_id": "root_hidden", "canonical_relpath": "hidden/clip.mp4", "want_video": True},
        ],
        {
            ("root_visible", "new/clip.mp4"): {},
            ("root_visible", "old/clip.mp4"): {
                "scan_version": current_version,
                "metadata": {"video_metadata": {"scene_detection_status": "complete"}},
            },
            ("root_hidden", "hidden/clip.mp4"): {},
        },
    )

    assert result["queued"] == 1
    assert len(submitted) == 1
    assert submitted[0][0] == "video_descriptions"
    assert submitted[0][1]["asset_ids"] == ["asset_new"]
    assert submitted[0][1]["trigger"] == "auto"
    assert submitted[0][2] == "local-user"


def test_repository_updates_fts_and_shot_hash_without_replacing_vector_rows(
    tmp_path: Path, monkeypatch
):
    data_root = tmp_path / "data"
    data_root.mkdir()
    source = tmp_path / "library" / "clip.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")
    old_segment = {"start_sec": 0.0, "end_sec": 3.0, "summary": "旧画面", "tags": ["旧标签"]}
    asset = {
        "asset_id": "asset_fts_vlm",
        "id": "asset_fts_vlm",
        "name": "clip.mp4",
        "kind": "video",
        "root_id": "root_vlm",
        "display_path": "clip.mp4",
        "canonical_relpath": "clip.mp4",
        "version": 4,
        "scan_version": video_descriptions._file_version(source),
        "metadata": {"video_segments": [old_segment]},
    }
    snapshot = {"assets": {asset["asset_id"]: asset}}

    class RegistryState:
        def mutate(self, callback):
            return callback(snapshot)

    monkeypatch.setattr(storage, "data_root", lambda: data_root)
    monkeypatch.setattr(repo, "state", lambda: RegistryState())
    monkeypatch.setattr(repo, "readonly_registry_snapshot", lambda: snapshot)
    monkeypatch.setattr(repo, "mounted_asset_hidden_filter", lambda: None)
    monkeypatch.setattr(
        repo,
        "visible_asset_from_snapshot",
        lambda current, asset_id, **_kwargs: current["assets"].get(asset_id),
    )
    monkeypatch.setattr(repo, "_asset_local_video_path", lambda _asset: source)

    db_path = data_root / repo.ASSET_SEARCH_INDEX_FILENAME
    with SQLiteVecEngine(db_path) as engine:
        engine.upsert_document(asset_id=asset["asset_id"], title=asset["name"], content="旧画面 旧标签")
        engine._db.execute(
            "UPDATE c18_documents SET vector_hash=? WHERE asset_id=?",
            ("preserve-vector-hash", asset["asset_id"]),
        )
        old_hash = repo.shot_content_hash(old_segment, repo.shot_source_key(asset))
        engine.batch_upsert_shots(
            [{
                "asset_id": asset["asset_id"],
                "root_id": asset["root_id"],
                "shot_index": 0,
                "start_sec": 0.0,
                "end_sec": 3.0,
                "content_hash": old_hash,
            }]
        )
        row = engine._db.execute(
            "SELECT row_id FROM mvp1_shots WHERE asset_id=? AND shot_index=0",
            (asset["asset_id"],),
        ).fetchone()
        engine._db.execute(
            "INSERT INTO mvp1_shot_embedding_rows(shot_row_id,sample_index,sample_sec,content_hash) VALUES(?,?,?,?)",
            (int(row["row_id"]), 0, 1.0, old_hash),
        )
        engine._db.commit()

    updated = repo.update_video_segment_descriptions(
        asset["asset_id"],
        expected_version=4,
        expected_scan_version=asset["scan_version"],
        updates={0: {"summary": "蓝色灯塔照亮海面", "tags": ["灯塔", "夜景"]}},
    )
    assert updated is True
    assert asset["version"] == 5
    assert asset["metadata"]["video_segments"][0]["summary"] == "蓝色灯塔照亮海面"

    with SQLiteVecEngine(db_path) as engine:
        assert [match.asset_id for match in engine.search_fts("蓝色灯塔")] == [asset["asset_id"]]
        document = engine._db.execute(
            "SELECT vector_hash FROM c18_documents WHERE asset_id=?",
            (asset["asset_id"],),
        ).fetchone()
        assert document["vector_hash"] == "preserve-vector-hash"
        shot = engine.list_shots(asset_id=asset["asset_id"])[0]
        assert shot.content_hash == repo.shot_content_hash(
            asset["metadata"]["video_segments"][0], repo.shot_source_key(asset)
        )
        assert engine._db.execute("SELECT COUNT(*) FROM mvp1_shot_embedding_rows").fetchone()[0] == 1


def test_settings_route_does_not_persist_unavailable_provider(client, monkeypatch):
    video_descriptions.set_settings(
        {"auto_enabled": False, "provider_id": "", "model": "", "prompt": "旧提示"}
    )

    def unavailable(_provider_id, _model):
        raise CleanroomException(400, "VLM_PROVIDER_UNAVAILABLE", "平台不可用")

    monkeypatch.setattr(video_descriptions, "validate_provider_selection", unavailable)
    response = client.put(
        "/api/asset-registry/video-descriptions/settings",
        headers=AUTH,
        json={"auto_enabled": False, "provider_id": "missing", "model": "vision", "prompt": "新提示"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VLM_PROVIDER_UNAVAILABLE"
    assert video_descriptions.get_settings()["provider_id"] == ""
    assert video_descriptions.get_settings()["prompt"] == "旧提示"


def test_manual_video_description_job_uses_existing_async_queue(client, monkeypatch):
    video_descriptions.set_settings(
        {
            "auto_enabled": False,
            "provider_id": "provider-local",
            "model": "vision-model",
            "prompt": "只说画面",
        }
    )
    monkeypatch.setattr(video_descriptions, "validate_provider_selection", lambda *_args: None)
    captured = []

    class Jobs:
        def submit(self, kind, payload, actor, key):
            captured.append((kind, payload, actor.role, key))
            return {
                "job_id": "job_index_00000057",
                "job": {"status": "queued"},
                "poll_hint": {"url": "/api/asset-registry/workspace-jobs/job_index_00000057"},
            }

    monkeypatch.setattr(routes_asset_registry.index_jobs, "service", lambda: Jobs())
    response = client.post(
        "/api/asset-registry/video-descriptions/jobs",
        headers={**AUTH, "Idempotency-Key": "qin657-root-job"},
        json={"root_id": "root_visible"},
    )
    assert response.status_code == 202
    assert response.json()["job_id"] == "job_index_00000057"
    assert response.json()["poll_hint"]["url"].endswith("job_index_00000057")
    assert captured[0][0] == "video_descriptions"
    assert captured[0][1] == {
        "roots": ["root_visible"],
        "asset_ids": [],
        "provider_id": "provider-local",
        "model": "vision-model",
        "prompt": "只说画面",
        "trigger": "manual",
    }
    assert captured[0][2:] == ("editor", "qin657-root-job")


def test_manual_video_description_job_rejects_hidden_assets_before_queueing(client, monkeypatch):
    video_descriptions.set_settings(
        {
            "auto_enabled": False,
            "provider_id": "provider-local",
            "model": "vision-model",
            "prompt": "只说画面",
        }
    )
    monkeypatch.setattr(video_descriptions, "validate_provider_selection", lambda *_args: None)
    monkeypatch.setattr(
        repo,
        "readonly_registry_snapshot",
        lambda: {"assets": {"asset_hidden": {"asset_id": "asset_hidden", "root_id": "root_hidden"}}},
    )
    monkeypatch.setattr(repo, "visible_asset_from_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        routes_asset_registry.index_jobs,
        "service",
        lambda: pytest.fail("不可见素材不得进入 VLM 队列"),
    )

    response = client.post(
        "/api/asset-registry/video-descriptions/jobs",
        headers=AUTH,
        json={"asset_id": "asset_hidden"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ASSET_NOT_FOUND"
