"""MVP1-T6：镜头级检索与时间戳跳转契约（AC4）。

覆盖：查询词元与镜头词面加权（中文二元组）、镜头段落收敛（非法段落跳过且
不伪造时间戳）、资产级下钻为镜头级命中并带起止时间戳、无可下钻镜头时退化
为资产级命中、以及 `GET /api/asset-registry/search/shots` 的认证/参数/索引
缺失/真实索引命中四类边界。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import repository as repo
from gw.asset_registry.hybrid_search import (
    HybridAssetSearcher,
    normalize_shot_records,
    shot_lexical_hits,
    shot_query_tokens,
)
from gw.core import storage
from gw.core.sqlite_vec_engine import FTSMatch, SQLiteVecEngine

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
SHOT_API = "/api/asset-registry/search/shots"

VIDEO_SEGMENTS = [
    {"start_sec": 0.0, "end_sec": 6.24, "tags": ["日出", "晨光"], "summary": "红日破晓"},
    {"start_sec": 6.24, "end_sec": 21.05, "tags": ["大海", "海浪"], "summary": "浪花拍打沙滩"},
    {"start_sec": 21.05, "end_sec": 35.0, "tags": ["沙滩", "足迹"], "summary": "沙滩脚印"},
]


class _StubEngine:
    """只实现 FTS 轨的引擎替身，用于验证下钻与排序不依赖真实索引。"""

    def __init__(self, matches: list[FTSMatch]) -> None:
        self._matches = matches
        self.last_vector_search_degraded = False
        self.vector_calls = 0

    def search_fts(self, query: str, *, limit: int) -> list[FTSMatch]:
        return list(self._matches)[:limit]

    def search_vectors(self, query_vector, *, limit: int):  # pragma: no cover - 不应被调用
        self.vector_calls += 1
        raise AssertionError("未提供查询向量时不得走向量轨")


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
