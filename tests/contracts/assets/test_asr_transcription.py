"""QIN-658：本机 ASR 时间映射、可选依赖降级与 FTS5 幂等检索。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import transcript_index
from gw.asset_registry import asr_transcription
from gw.asset_registry import index_jobs, repository

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_funasr_millisecond_ranges_normalize_to_video_seconds():
    assert asr_transcription.normalize_asr_segments([
        {"sentence_info": [
            {"start": 1250, "end": 2475, "text": "开始拍摄"},
            {"start": 3000, "end": 3000, "text": "无效区间"},
        ]}
    ]) == [{"start_sec": 1.25, "end_sec": 2.475, "text": "开始拍摄"}]


def test_funasr_word_timestamps_yield_searchable_chinese_text(monkeypatch, tmp_path):
    raw = [{"text": "今 天 来 到 海 边", "timestamp": [
        [150, 390], [390, 630], [690, 870], [870, 1010], [1010, 1170], [1170, 1410],
    ]}]
    segments = asr_transcription.normalize_asr_segments(raw)
    assert segments == [{"start_sec": 0.15, "end_sec": 1.41, "text": "今天来到海边"}]
    scenes = asr_transcription.map_transcripts_to_video_segments(
        [{"start_sec": 0, "end_sec": 2}], segments
    )
    monkeypatch.setattr(transcript_index, "_database_path", lambda: tmp_path / "fts.sqlite3")
    transcript_index.replace_asset_transcripts("asset-video", scenes)
    assert transcript_index.search_transcripts("来到海边")[0]["start_sec"] == 0.15


def test_funasr_status_rejects_missing_feature_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(asr_transcription.storage, "data_root", lambda: tmp_path)
    monkeypatch.setattr(asr_transcription, "_package_installed", lambda _: True)
    monkeypatch.setattr(asr_transcription, "_funasr_feature_backend_available", lambda: False)
    monkeypatch.setattr(asr_transcription.shutil, "which", lambda _: "available")
    model = asr_transcription.model_path("funasr", "paraformer-zh")
    model.mkdir(parents=True)
    (model / "model.pt").touch()
    status = asr_transcription.model_status("funasr", "paraformer-zh")
    assert not status["ready"]
    assert "kaldi-native-fbank" in status["install_hint"]


def test_asr_job_writes_with_registry_revision(monkeypatch, tmp_path):
    asset = {"asset_id": "ast_1", "root_id": "root_1", "display_path": "sample.mp4",
             "version": 1, "metadata": {"video_segments": [{"start_sec": 0, "end_sec": 4}]}}
    registry = {"revision": 7, "assets": {"ast_1": asset}}
    class RegistryState:
        def read(self):
            return registry
    class JobStore:
        def mutate(self, callback):
            callback({"jobs": {"job_1": {}}})
    class FakeService:
        store = JobStore()
        def _checkpoint(self, *_args):
            pass
    updates = []
    def update(asset_id, payload):
        updates.append(payload)
        assert asset_id == "ast_1" and payload["expected_version"] == 7
        return {"asset": asset, "revision": 8}
    monkeypatch.setattr(repository, "state", lambda: RegistryState())
    monkeypatch.setattr(repository, "readonly_registry_snapshot", lambda: registry)
    monkeypatch.setattr(repository, "mounted_asset_hidden_filter", lambda: None)
    monkeypatch.setattr(repository, "visible_asset_from_snapshot", lambda *_a, **_kw: asset)
    monkeypatch.setattr(repository, "_asset_category", lambda _: "video")
    monkeypatch.setattr(repository, "update_asset", update)
    monkeypatch.setattr(index_jobs.storage, "resolve_request_path", lambda *_a, **_kw: tmp_path / "sample.mp4")
    monkeypatch.setattr(asr_transcription, "model_status", lambda *_: {"ready": True})
    monkeypatch.setattr(asr_transcription, "transcribe_video", lambda *_a, **_kw: [
        {"start_sec": 1, "end_sec": 2, "text": "真实对白"}
    ])
    monkeypatch.setattr(transcript_index, "replace_asset_transcripts", lambda *_: 1)
    result = index_jobs.IndexJobs._run_asr_transcription(
        FakeService(), "job_1", 1, {"roots": ["root_1"], "asset_ids": ["ast_1"]}
    )
    assert result["failed"] == 0 and result["indexed_segments"] == 1
    assert updates[0]["video_segments"][0]["transcript"][0]["text"] == "真实对白"


def test_transcript_mapping_uses_absolute_interval_intersections():
    scenes = [
        {"start_sec": 10, "end_sec": 15, "summary": "第一镜头"},
        {"start_sec": 15, "end_sec": 20, "summary": "第二镜头"},
        {"start_sec": 20, "end_sec": 25, "summary": "第三镜头"},
    ]
    mapped = asr_transcription.map_transcripts_to_video_segments(scenes, [
        {"start_sec": 14, "end_sec": 16, "text": "跨镜头对白"},
        {"start_sec": 25, "end_sec": 27, "text": "不相交对白"},
    ])
    assert mapped[0]["transcript"] == [{
        "start_sec": 14.0, "end_sec": 15.0, "text": "跨镜头对白",
    }]
    assert mapped[1]["transcript"] == [{
        "start_sec": 15.0, "end_sec": 16.0, "text": "跨镜头对白",
    }]
    assert mapped[2]["transcript"] == []
    assert scenes[0].get("transcript") is None


def test_no_audio_returns_skip_without_loading_asr(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(asr_transcription, "_extract_audio", lambda _source: None)
    monkeypatch.setattr(
        asr_transcription, "_recognize",
        lambda *_args: (_ for _ in ()).throw(AssertionError("无音轨不应加载模型")),
    )
    assert asr_transcription.transcribe_video(tmp_path / "silent.mp4", engine="funasr", model="paraformer-zh") is None


def test_missing_optional_package_and_weights_report_manual_guidance(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(asr_transcription.storage, "data_root", lambda: tmp_path)
    monkeypatch.setattr(asr_transcription, "_package_installed", lambda _package: False)
    monkeypatch.setattr(asr_transcription.shutil, "which", lambda _executable: None)
    status = asr_transcription.model_status("funasr", "paraformer-zh")
    assert status["ready"] is False
    assert status["dependency_installed"] is False
    assert status["weights_ready"] is False
    assert "不会自动安装" in status["message"]
    assert "<data_root>/models/asr/funasr/paraformer-zh" in status["install_hint"]


def test_transcript_fts_replacement_is_idempotent_and_searchable(monkeypatch, tmp_path: Path):
    database = tmp_path / "asset_registry_search.sqlite3"
    monkeypatch.setattr(transcript_index, "_database_path", lambda: database)
    scenes = [{
        "start_sec": 2,
        "end_sec": 8,
        "transcript": [{"start_sec": 3.5, "end_sec": 5, "text": "blue ocean sunrise"}],
    }]
    assert transcript_index.replace_asset_transcripts("asset-one", scenes) == 1
    assert transcript_index.replace_asset_transcripts("asset-one", scenes) == 1
    matches = transcript_index.search_transcripts("ocean sunrise")
    assert len(matches) == 1
    assert matches[0]["asset_id"] == "asset-one"
    assert matches[0]["segment_idx"] == 0
    assert matches[0]["start_sec"] == 3.5
    updated = [{"start_sec": 2, "end_sec": 8, "transcript": [
        {"start_sec": 4, "end_sec": 6, "text": "quiet forest rain"},
    ]}]
    assert transcript_index.replace_asset_transcripts("asset-one", updated) == 1
    assert transcript_index.search_transcripts("ocean sunrise") == []
    assert transcript_index.search_transcripts("forest rain")[0]["text"] == "quiet forest rain"


def test_asr_status_reports_manual_install_and_model_guidance(client, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(asr_transcription.storage, "data_root", lambda: tmp_path)
    monkeypatch.setattr(asr_transcription, "_package_installed", lambda _package: False)
    monkeypatch.setattr(asr_transcription.shutil, "which", lambda _executable: None)
    response = client.get("/api/asset-registry/asr/status", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert "不会自动安装" in response.json()["message"]


def test_asr_job_is_disabled_until_explicitly_enabled(client, monkeypatch):
    monkeypatch.setattr(asr_transcription, "settings", lambda: {
        "enabled": False, "engine": "funasr", "model": "paraformer-zh",
    })
    response = client.post(
        "/api/asset-registry/asr/jobs",
        headers=AUTH,
        json={"root_id": "root-visible"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ASR_TRACK_DISABLED"
