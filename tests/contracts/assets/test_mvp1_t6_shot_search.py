"""MVP1-T6：镜头级检索与时间戳跳转契约（AC4）。

覆盖：查询词元与镜头词面加权（中文二元组）、镜头段落收敛（非法段落跳过且
不伪造时间戳）、资产级下钻为镜头级命中并带起止时间戳、无可下钻镜头时退化
为资产级命中、以及 `GET /api/asset-registry/search/shots` 的认证/参数/索引
缺失/真实索引命中四类边界。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.api import routes_asset_registry
from gw.asset_registry import repository as repo
from gw.asset_registry.hybrid_search import (
    HybridAssetSearcher,
    normalize_shot_records,
    shot_lexical_hits,
    shot_query_tokens,
)
from gw.core import storage
from gw.core.sqlite_vec_engine import IndexMetadata, FTSMatch, ShotVectorMatch, SQLiteVecEngine

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
SHOT_API = "/api/asset-registry/search/shots"

VIDEO_SEGMENTS = [
    {"start_sec": 0.0, "end_sec": 6.24, "tags": ["日出", "晨光"], "summary": "红日破晓"},
    {"start_sec": 6.24, "end_sec": 21.05, "tags": ["大海", "海浪"], "summary": "浪花拍打沙滩"},
    {"start_sec": 21.05, "end_sec": 35.0, "tags": ["沙滩", "足迹"], "summary": "沙滩脚印"},
]


class _StubEngine:
    """只实现 FTS 轨的引擎替身，用于验证下钻与排序不依赖真实索引。"""

    def __init__(
        self,
        matches: list[FTSMatch],
        shot_vectors: list[ShotVectorMatch] | None = None,
    ) -> None:
        self._matches = matches
        self._shot_vectors = shot_vectors or []
        self.last_vector_search_degraded = False
        self.last_shot_vector_search_degraded = False
        self.vector_calls = 0
        self.fts_limits = []
        self.shot_vector_limits = []

    def search_fts(self, query: str, *, limit: int) -> list[FTSMatch]:
        self.fts_limits.append(limit)
        return list(self._matches)[:limit]

    def search_vectors(self, query_vector, *, limit: int):
        self.vector_calls += 1
        return []

    def search_shot_vectors(self, query_vector, *, limit: int):
        self.shot_vector_limits.append(limit)
        return list(self._shot_vectors)[:limit]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _import_video(client: TestClient, *, segments=VIDEO_SEGMENTS) -> str:
    payload = {
        "name": "海边日出.mp4",
        "kind": "video",
        "frame_rate": 25.0,
        "duration": 35.0,
        "tags": ["风景", "海滨"],
        "metadata": {"frame_rate": 25.0, "duration": 35.0, "video_segments": segments},
    }
    response = client.post(
        "/api/asset-registry/assets/import", headers=AUTH, json={"items": [payload]}
    )
    assert response.status_code == 200, response.text
    return response.json()["asset_ids"][0]


def _build_index(documents: list[dict]) -> Path:
    """在隔离数据根下建立真实检索索引文件（文档级，镜头行留空）。"""
    db_path = storage.data_root() / repo.ASSET_SEARCH_INDEX_FILENAME
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteVecEngine(db_path) as engine:
        engine.batch_upsert_documents(documents)
    return db_path


# ---------------------------------------------------------------------------
# 纯函数：词元、词面加权与镜头收敛
# ---------------------------------------------------------------------------


def test_shot_query_tokens_split_cjk_bigrams_and_latin_words():
    """中文取二元组、拉丁取整词，去重保序并跳过单字符噪声。"""
    tokens = shot_query_tokens("海边 Sunset 日落")
    assert "海边" in tokens
    assert "sunset" in tokens
    assert "日落" in tokens
    assert len(tokens) == len(set(tokens))


def test_shot_lexical_hits_counts_matching_tokens_only():
    """命中数只统计真出现在标签或摘要中的词元。"""
    tokens = shot_query_tokens("大海 日落")
    tags = ("大海", "海浪")
    assert shot_lexical_hits(tokens, tags=tags, summary="浪花拍打沙滩") >= 1
    assert shot_lexical_hits(tokens, tags=("日出",), summary="红日破晓") == 0
    assert shot_lexical_hits(tokens, tags=(), summary="") == 0


def test_normalize_shot_records_skips_invalid_segments_without_faking_time():
    """非法段落直接跳过，序号按合法段落连续编号，不补默认时间戳。"""
    records = normalize_shot_records(
        [
            {"start_sec": 0, "end_sec": 2, "tags": ["a"]},
            {"start_sec": 2, "end_sec": 2, "tags": ["空片段"]},
            {"start_sec": "3", "end_sec": 4, "tags": ["字符串时间"]},
            {"start_sec": float("nan"), "end_sec": 6, "tags": ["NaN"]},
            {"start_sec": 6, "end_sec": 9, "tags": ["b"], "summary": "第二段"},
            "not-a-segment",
        ]
    )
    assert [record["shot_index"] for record in records] == [0, 1]
    assert records[1]["start_sec"] == 6.0
    assert records[1]["end_sec"] == 9.0
    assert records[1]["summary"] == "第二段"
    assert normalize_shot_records(None) == []
    assert normalize_shot_records("bad") == []


def test_search_shots_boosts_matching_shot_and_keeps_timestamps():
    """同一资产内命中镜头得分更高，且必带可跳转的起止时间戳。"""
    engine = _StubEngine([FTSMatch(asset_id="ast_0001", score=1.0)])
    searcher = HybridAssetSearcher(engine)
    shots = {
        "ast_0001": [
            {"start_sec": 0.0, "end_sec": 6.24, "tags": ["日出"], "summary": "红日破晓"},
            {"start_sec": 6.24, "end_sec": 21.05, "tags": ["大海"], "summary": "浪花拍打沙滩"},
        ]
    }
    results = searcher.search_shots(
        "大海", limit=5, shot_provider=lambda asset_id: shots.get(asset_id, [])
    )
    assert [hit.granularity for hit in results.hits] == ["shot", "shot"]
    top = results.hits[0]
    assert top.start_sec == 6.24 and top.end_sec == 21.05
    assert top.lexical_hits >= 1
    assert top.score > results.hits[1].score
    assert results.vector_track_degraded is False
    assert engine.vector_calls == 0


def test_search_shots_falls_back_to_asset_hit_without_registered_shots():
    """没有登记镜头的资产退化为资产级命中，绝不伪造时间戳。"""
    engine = _StubEngine([FTSMatch(asset_id="ast_0002", score=1.0)])
    results = HybridAssetSearcher(engine).search_shots(
        "角色参考", limit=5, shot_provider=lambda asset_id: []
    )
    assert len(results.hits) == 1
    hit = results.hits[0]
    assert hit.granularity == "asset"
    assert hit.shot_index is None
    assert hit.start_sec is None and hit.end_sec is None
    assert hit.score == pytest.approx(hit.asset_score)


def test_search_shots_respects_limit_and_deterministic_order():
    """结果受 limit 约束，排序按得分降序、资产与镜头序稳定。"""
    engine = _StubEngine(
        [FTSMatch(asset_id="ast_0002", score=1.0), FTSMatch(asset_id="ast_0001", score=0.9)]
    )
    segments = [
        {"start_sec": 0.0, "end_sec": 1.0, "tags": ["大海"]},
        {"start_sec": 1.0, "end_sec": 2.0, "tags": ["大海"]},
    ]
    results = HybridAssetSearcher(engine).search_shots(
        "大海", limit=3, shot_provider=lambda asset_id: segments
    )
    assert len(results.hits) == 3
    first_asset = results.hits[0].asset_id
    assert first_asset == "ast_0002"
    assert [hit.shot_index for hit in results.hits[:2]] == [0, 1]


def test_visual_vectors_recall_without_lexical_match_and_cap_candidates():
    engine = _StubEngine(
        [],
        [ShotVectorMatch("ast_visual", 0, 4.0, 8.0, 0.01, (1.0, 0.0))],
    )
    results = HybridAssetSearcher(engine).search_shots(
        "完全无关的查询",
        query_vector=[1.0, 0.0],
        limit=1,
        candidate_limit=500,
        shot_provider=lambda asset_id: [
            {"start_sec": 4.0, "end_sec": 8.0, "tags": [], "summary": ""}
        ],
    )
    assert len(results.hits) == 1
    assert results.hits[0].asset_id == "ast_visual"
    assert results.hits[0].granularity == "shot"
    assert results.hits[0].lexical_hits == 0
    assert results.hits[0].vector_distance == pytest.approx(0.01)
    assert engine.fts_limits == [100]
    assert engine.shot_vector_limits == [100]


def test_visual_search_folds_adjacent_duplicates_and_mmr_prefers_diversity():
    vectors = [
        ShotVectorMatch("asset-a", 0, 0.0, 2.0, 0.01, (1.0, 0.0)),
        ShotVectorMatch("asset-a", 1, 2.0, 4.0, 0.02, (0.995, 0.1)),
        ShotVectorMatch("asset-b", 0, 0.0, 2.0, 0.03, (0.9, 0.435)),
        ShotVectorMatch("asset-c", 0, 0.0, 2.0, 0.04, (0.0, 1.0)),
    ]
    segments = {
        "asset-a": [
            {"start_sec": 0.0, "end_sec": 2.0},
            {"start_sec": 2.0, "end_sec": 4.0},
        ],
        "asset-b": [{"start_sec": 0.0, "end_sec": 2.0}],
        "asset-c": [{"start_sec": 0.0, "end_sec": 2.0}],
    }
    results = HybridAssetSearcher(_StubEngine([], vectors)).search_shots(
        "无词面命中",
        query_vector=[1.0, 0.0],
        limit=3,
        shot_provider=lambda asset_id: segments.get(asset_id, []),
    )
    assert [(hit.asset_id, hit.shot_index) for hit in results.hits] == [
        ("asset-a", 0),
        ("asset-c", 0),
        ("asset-b", 0),
    ]


# ---------------------------------------------------------------------------
# HTTP 契约
# ---------------------------------------------------------------------------


def test_shot_search_requires_authentication(client: TestClient):
    """未认证一律 401，且先于参数校验。"""
    response = client.get(SHOT_API, params={"q": "大海"})
    assert response.status_code == 401


def test_shot_search_rejects_blank_and_oversized_query(client: TestClient):
    """空检索词与超长检索词返回 400 INVALID_QUERY。"""
    blank = client.get(SHOT_API, params={"q": "   "}, headers=AUTH)
    assert blank.status_code == 400
    assert blank.json()["detail"]["code"] == "INVALID_QUERY"
    too_long = client.get(SHOT_API, params={"q": "海" * 4097}, headers=AUTH)
    assert too_long.status_code == 400
    assert too_long.json()["detail"]["code"] == "INVALID_QUERY"


def test_shot_search_rejects_out_of_range_limit(client: TestClient):
    """limit 越界或非整数返回 400 INVALID_LIMIT。"""
    for limit in ("0", "51", "abc"):
        response = client.get(SHOT_API, params={"q": "大海", "limit": limit}, headers=AUTH)
        assert response.status_code == 400, limit
        assert response.json()["detail"]["code"] == "INVALID_LIMIT"


def test_shot_search_reports_missing_index_as_unavailable(client: TestClient):
    """索引未建立时如实 503，不返回伪造空结果。"""
    response = client.get(SHOT_API, params={"q": "大海"}, headers=AUTH)
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "SEARCH_INDEX_NOT_READY"
    assert detail["data_status"] == "not_integrated"


def test_shot_vector_toggle_defaults_off_and_retains_stored_vectors(client: TestClient):
    """开关默认关闭；关闭只停用轨道，已存帧向量仍保留。"""
    db_path = storage.data_root() / repo.ASSET_SEARCH_INDEX_FILENAME
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteVecEngine(db_path, dimensions=4) as engine:
        if not engine.vector_enabled:
            pytest.skip("sqlite-vec 当前不可用")
        engine.batch_upsert_shots(
            [{"asset_id": "ast_keep", "root_id": "root_keep", "shot_index": 0,
              "start_sec": 0.0, "end_sec": 2.0, "content_hash": "c" * 64}]
        )
        assert engine.replace_shot_embeddings(
            asset_id="ast_keep",
            shot_index=0,
            content_hash="c" * 64,
            samples=[{"sample_index": 0, "sample_sec": 1.0, "embedding": [1, 0, 0, 0]}],
        )

    status = client.get("/api/asset-registry/shot-vectors/status", headers=AUTH)
    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert status.json()["shot_vector_count"] == 1

    enabled = client.post(
        "/api/asset-registry/shot-vectors/settings", headers=AUTH, json={"enabled": True}
    )
    disabled = client.post(
        "/api/asset-registry/shot-vectors/settings", headers=AUTH, json={"enabled": False}
    )
    assert enabled.status_code == disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["shot_vector_count"] == 1


def test_shot_vector_job_is_accepted_as_async_root_scoped_work(
    client: TestClient, monkeypatch
):
    calls = []

    class _Jobs:
        def submit(self, kind, payload, actor, key):
            calls.append((kind, payload, key))
            return {
                "job_id": "job_index_00000001",
                "job": {"status": "queued"},
                "poll_hint": {"url": "/api/asset-registry/workspace-jobs/job_index_00000001"},
            }

    monkeypatch.setattr(
        routes_asset_registry,
        "get_shot_vector_status",
        lambda authorization=None, x_user_role="editor": {"enabled": True, "ready": True},
    )
    monkeypatch.setattr(routes_asset_registry.index_jobs, "service", lambda: _Jobs())
    response = client.post(
        "/api/asset-registry/shot-vectors/jobs",
        headers={**AUTH, "Idempotency-Key": "shot-vectors-root-test"},
        json={"root_id": "root-visible"},
    )
    assert response.status_code == 202
    assert response.json()["job_id"] == "job_index_00000001"
    assert calls == [
        ("shot_vectors", {"roots": ["root-visible"]}, "shot-vectors-root-test")
    ]


def test_shot_vector_job_rejects_disabled_track(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        routes_asset_registry,
        "get_shot_vector_status",
        lambda authorization=None, x_user_role="editor": {"enabled": False, "ready": True},
    )
    response = client.post(
        "/api/asset-registry/shot-vectors/jobs",
        headers=AUTH,
        json={"root_id": "root-visible"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SHOT_VECTOR_TRACK_DISABLED"


def test_model_readiness_fails_closed_without_local_optional_dependencies(monkeypatch):
    monkeypatch.setattr(repo.importlib.util, "find_spec", lambda _name: None)
    status = repo.shot_vector_model_status()
    assert status["ready"] is False
    assert status["dependencies_ready"] is False
    assert set(status["missing_dependencies"]) == {
        "torch",
        "transformers",
        "huggingface_hub",
    }


def test_missing_local_model_skips_frame_decode_without_blocking_base_index(
    monkeypatch,
):
    class _VectorEngine:
        vector_enabled = True

    monkeypatch.setattr(repo, "shot_vector_model_status", lambda: {"ready": False})
    monkeypatch.setattr(
        repo,
        "_asset_local_video_path",
        lambda _asset: pytest.fail("模型未就绪时不得解码视频帧"),
    )
    result = repo.refresh_asset_shot_vectors(
        _VectorEngine(),
        {
            "asset_id": "ast_missing_model",
            "metadata": {
                "video_segments": [
                    {"start_sec": 0, "end_sec": 2},
                    {"start_sec": 2, "end_sec": 4},
                ]
            },
        },
    )
    assert result == {"shots": 0, "frames": 0, "failed": 2}


def test_refresh_shot_vectors_uses_injected_decoder_and_deduplicates_rgb_frames(
    monkeypatch,
):
    class _VectorEngine:
        vector_enabled = True

        def __init__(self):
            self.samples = []

        def switch_embedding_model(self, **_kwargs):
            pass

        def replace_shot_embeddings(self, **values):
            self.samples = values["samples"]
            return True

    class _Embedder:
        model_id = "OFA-Sys/chinese-clip-vit-base-patch16"
        model_version = "test-model-v1"

        def __init__(self):
            self.frames = []

        def encode_image(self, frame):
            self.frames.append(frame)
            return [1.0] + [0.0] * 511

    def png(color):
        buffer = BytesIO()
        Image.new("RGB", (2, 2), color).save(buffer, format="PNG")
        return buffer.getvalue()

    encoded_frames = {
        0.8: png((255, 0, 0)),
        2.0: png((255, 0, 0)),
        3.2: png((0, 0, 255)),
    }
    decoded_at = []
    monkeypatch.setattr(repo, "_asset_local_video_path", lambda _asset: Path("video.mp4"))
    engine = _VectorEngine()
    embedder = _Embedder()

    result = repo.refresh_asset_shot_vectors(
        engine,
        {
            "asset_id": "ast_visual_vectors",
            "root_id": "root-test",
            "metadata": {"video_segments": [{"start_sec": 0.0, "end_sec": 4.0}]},
        },
        embedder=embedder,
        frame_decoder=lambda _source, sample_sec: (
            decoded_at.append(round(sample_sec, 1)) or encoded_frames[round(sample_sec, 1)]
        ),
    )

    assert decoded_at == [0.8, 2.0, 3.2]
    assert len(embedder.frames) == 2
    assert [sample["sample_index"] for sample in engine.samples] == [0, 2]
    assert [sample["sample_sec"] for sample in engine.samples] == [0.8, 3.2]
    assert result == {"shots": 1, "frames": 2, "failed": 0}


def test_shot_search_returns_shot_hit_with_jump_timestamp(client: TestClient):
    """真实索引命中后返回可跳转镜头：命中镜头排在首位并带起止时间戳。"""
    asset_id = _import_video(client)
    _build_index(
        [
            {
                "asset_id": asset_id,
                "title": "海边日出.mp4",
                "content": "video 风景 海滨 日出 晨光 大海 海浪 沙滩 足迹",
            }
        ]
    )
    response = client.get(SHOT_API, params={"q": "大海", "limit": "10"}, headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_status"] == "ok"
    assert body["granularity"] == "shot"
    assert body["shot_count"] >= 1
    top = body["items"][0]
    assert top["asset_id"] == asset_id
    assert top["granularity"] == "shot"
    assert top["shot_index"] == 1
    assert top["start_sec"] == pytest.approx(6.24)
    assert top["end_sec"] == pytest.approx(21.05)
    assert "大海" in top["tags"]
    assert top["lexical_hits"] >= 1
    assert top["name"] == "海边日出.mp4"
    # 未命中的镜头仍然可跳转，但排序必须落在命中镜头之后。
    others = [item for item in body["items"] if item["shot_index"] != 1]
    assert others and others[0]["score"] <= top["score"]


@pytest.mark.parametrize(
    ("root_id", "root_availability", "removed_root_ids", "relative_path"),
    [
        ("root-removed", "available", {"root-removed"}, "secret.mp4"),
        ("root-marker", "removed", set(), "secret.mp4"),
        ("active-root", "available", set(), ".hidden/secret.mp4"),
    ],
)
def test_shot_search_filters_vector_only_candidates_from_excluded_sources(
    client: TestClient,
    monkeypatch,
    root_id: str,
    root_availability: str,
    removed_root_ids: set[str],
    relative_path: str,
):
    """纯向量候选须过滤已移除来源根与扫描排除路径，不能泄露镜头时间戳。"""
    from gw.asset_registry.embedder import DEFAULT_MODEL_ID
    from gw.core import sqlite_vec_engine

    asset_id = "ast_removed_vector"
    snapshot = {
        "assets": {
            asset_id: {
                "asset_id": asset_id,
                "id": asset_id,
                "name": "已移除素材.mp4",
                "kind": "video",
                "root_id": root_id,
                "storage_root_id": root_id,
                "root_availability": root_availability,
                "canonical_relpath": relative_path,
                "display_path": relative_path,
                "archived": False,
                "hidden": False,
                "metadata": {
                    "video_segments": [
                        {"start_sec": 0.0, "end_sec": 2.0, "tags": [], "summary": ""}
                    ]
                },
            }
        }
    }
    monkeypatch.setattr(repo, "readonly_registry_snapshot", lambda: snapshot)
    monkeypatch.setattr(repo, "_removed_root_ids_for_search", lambda: removed_root_ids)
    monkeypatch.setattr(repo, "shot_vector_enabled", lambda: True)
    monkeypatch.setattr(
        routes_asset_registry,
        "_shot_vector_model_status",
        lambda: {"ready": True, "model_version": "test-model-v1"},
    )
    monkeypatch.setattr(
        routes_asset_registry, "_encode_shot_query", lambda *_args, **_kwargs: [1.0, 0.0]
    )

    class _VectorOnlyEngine:
        dimensions = 2
        vector_enabled = True
        last_vector_search_degraded = False
        last_shot_vector_search_degraded = False

        def __init__(self, _db_path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def index_metadata(self):
            return IndexMetadata(DEFAULT_MODEL_ID, "test-model-v1", 2, "float32")

        def search_fts(self, _query, *, limit):
            return []

        def search_vectors(self, _query_vector, *, limit):
            return []

        def search_shot_vectors(self, _query_vector, *, limit):
            return [ShotVectorMatch(asset_id, 0, 0.0, 2.0, 0.01, (1.0, 0.0))]

        def list_shots(self, *, asset_id):
            return []

    monkeypatch.setattr(sqlite_vec_engine, "SQLiteVecEngine", _VectorOnlyEngine)
    db_path = storage.data_root() / repo.ASSET_SEARCH_INDEX_FILENAME
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()

    response = client.get(SHOT_API, params={"q": "无词面命中的查询"}, headers=AUTH)

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["shot_count"] == 0


def test_shot_search_degrades_to_asset_hit_for_assets_without_segments(client: TestClient):
    """无镜头登记（如图片）的资产以资产级命中返回，start_sec 为空。"""
    response = client.post(
        "/api/asset-registry/assets/import",
        headers=AUTH,
        json={"items": [{"name": "角色参考.png", "kind": "image", "tags": ["角色"]}]},
    )
    assert response.status_code == 200, response.text
    asset_id = response.json()["asset_ids"][0]
    _build_index([{"asset_id": asset_id, "title": "角色参考.png", "content": "image 角色"}])
    hit = client.get(SHOT_API, params={"q": "角色"}, headers=AUTH)
    assert hit.status_code == 200, hit.text
    body = hit.json()
    assert body["shot_count"] == 0
    assert body["items"][0]["granularity"] == "asset"
    assert body["items"][0]["start_sec"] is None
