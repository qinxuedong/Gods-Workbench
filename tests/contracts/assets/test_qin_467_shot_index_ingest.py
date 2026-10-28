"""QIN-467（GitHub #428）：入库即建镜头检索索引，并提供产品化重建入口。

覆盖 T7/F2 的三层根因：
1. 入库（JSON 批量登记与后台索引任务提交）写入镜头行与索引元数据（AC1/AC2）；
2. 镜头标签与摘要进入资产检索文档，镜头级文本可被 `/search/shots` 召回并带
   可跳转时间戳（AC1）；
3. `POST /api/asset-registry/search-index/rebuild` 提供产品化重建，不再依赖手工
   调私有函数；写入与重建失败一律 fail closed 且错误码明确（AC2/AC3）。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs
from gw.asset_registry import repository as repo
from gw.core import storage
from gw.core.sqlite_vec_engine import SQLiteVecEngine

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
READ_ONLY = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "viewer"}
IMPORT_API = "/api/asset-registry/assets/import"
SHOT_API = "/api/asset-registry/search/shots"
REBUILD_API = "/api/asset-registry/search-index/rebuild"

SEGMENTS = [
    {"start_sec": 0.0, "end_sec": 6.24, "tags": ["日出", "晨光"], "summary": "红日破晓"},
    {"start_sec": 6.24, "end_sec": 21.05, "tags": ["大海", "海浪"], "summary": "浪花拍打沙滩"},
    {"start_sec": 21.05, "end_sec": 35.0, "tags": ["沙滩"], "summary": "沙滩脚印"},
]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _index_path():
    return storage.data_root() / repo.ASSET_SEARCH_INDEX_FILENAME


def _shot_rows(asset_id: str) -> List[tuple]:
    connection = sqlite3.connect(_index_path())
    try:
        return connection.execute(
            """SELECT shot_index, start_sec, end_sec FROM mvp1_shots
               WHERE asset_id=? ORDER BY shot_index""",
            (asset_id,),
        ).fetchall()
    finally:
        connection.close()


def _index_metadata() -> tuple | None:
    connection = sqlite3.connect(_index_path())
    try:
        return connection.execute(
            "SELECT model_id, model_version, dimensions FROM mvp1_index_metadata WHERE id=1"
        ).fetchone()
    finally:
        connection.close()


def _video_item(name: str, segments: list[dict]) -> dict:
    return {
        "name": name,
        "kind": "video",
        "duration": 35.0,
        "tags": ["风景"],
        "metadata": {"duration": 35.0, "video_segments": segments},
    }


def _import(client: TestClient, name: str = "海边日出.mp4", segments=SEGMENTS) -> str:
    response = client.post(
        IMPORT_API, headers=AUTH, json={"items": [_video_item(name, segments)]}
    )
    assert response.status_code == 200, response.text
    return response.json()["asset_ids"][0]


class _FakeState:
    """把注册表真源替换成内存 dict，验证后台提交的落盘写索引行为。"""

    def __init__(self, raw: Dict[str, Any]) -> None:
        self._raw = raw

    def read(self) -> Dict[str, Any]:
        return self._raw

    def mutate(self, callback):
        return callback(self._raw)


def _scanner(monkeypatch, raw: Dict[str, Any]) -> index_jobs.IndexJobs:
    scanner = object.__new__(index_jobs.IndexJobs)
    monkeypatch.setattr(scanner, "_check", lambda *_args: None)
    monkeypatch.setattr(repo, "state", lambda: _FakeState(raw))
    return scanner


def _commit_files(segments: list[dict]) -> List[dict]:
    return [
        {
            "root_id": "root-1",
            "storage_root_id": "root-1",
            "display_path": "source.mov",
            "canonical_relpath": "source.mov",
            "name": "source.mov",
            "size_bytes": 5,
            "metadata": {"video_segments": segments},
        }
    ]


# ---------------------------------------------------------------------------
# AC1：入库即建镜头索引
# ---------------------------------------------------------------------------


def test_import_writes_shot_rows_and_honest_index_metadata(client: TestClient):
    """JSON 批量登记完成后镜头行与索引元数据真实落盘，不再恒为 0 / 空。"""
    asset_id = _import(client)

    assert _index_path().is_file()
    assert _shot_rows(asset_id) == [
        (0, 0.0, 6.24),
        (1, 6.24, 21.05),
        (2, 21.05, 35.0),
    ]
    assert _index_metadata() == (None, None, 384)

    # 镜头标签进入资产检索文档：仅出现在镜头标签里的词也能召回该资产。
    with SQLiteVecEngine(_index_path()) as engine:
        assert asset_id in {match.asset_id for match in engine.search_fts("晨光")}


def test_imported_shots_are_reachable_by_shot_search(client: TestClient):
    """入库后镜头级检索可命中并返回可跳转时间戳（AC1 主路径）。"""
    asset_id = _import(client)

    response = client.get(SHOT_API, params={"q": "浪花", "limit": "10"}, headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_status"] == "ok"
    assert body["shot_count"] >= 1
    top = body["items"][0]
    assert top["asset_id"] == asset_id
    assert top["granularity"] == "shot"
    assert top["shot_index"] == 1
    assert top["start_sec"] == pytest.approx(6.24)
    assert top["end_sec"] == pytest.approx(21.05)
    assert top["content_hash"]


def test_shot_search_reports_vector_track_degraded_without_embedding_model(
    client: TestClient,
):
    """未登记嵌入模型时如实报告向量轨降级，不把空向量轨伪装成双轨正常。"""
    _import(client)
    body = client.get(SHOT_API, params={"q": "浪花"}, headers=AUTH).json()
    assert body["vector_track_degraded"] is True


def test_background_index_commit_builds_shot_index(monkeypatch):
    """后台索引任务提交（试点路径）同样写入镜头行，不再只写资产文档。"""
    raw: Dict[str, Any] = {"assets": {}, "revision": 0}
    scanner = _scanner(monkeypatch, raw)

    result = scanner._commit(
        {"job_id": "qin-467-commit", "attempt": 1, "_request": {"kind": "reindex"}},
        _commit_files(SEGMENTS),
    )

    assert result["indexed"] == 1
    asset_id = next(iter(raw["assets"]))
    assert _shot_rows(asset_id) == [
        (0, 0.0, 6.24),
        (1, 6.24, 21.05),
        (2, 21.05, 35.0),
    ]
    assert _index_metadata() == (None, None, 384)


def test_commit_is_idempotent_and_refreshes_resegmented_shots(monkeypatch):
    """二次提交不重复累积镜头行；重切分后旧镜头行被整组替换（AC2 幂等）。"""
    raw: Dict[str, Any] = {"assets": {}, "revision": 0}
    scanner = _scanner(monkeypatch, raw)

    def _run(attempt: int, segments: list[dict]) -> None:
        scanner._commit(
            {
                "job_id": "qin-467-idempotent",
                "attempt": attempt,
                "_request": {"kind": "reindex"},
            },
            _commit_files(segments),
        )

    _run(1, SEGMENTS)
    asset_id = next(iter(raw["assets"]))
    assert len(_shot_rows(asset_id)) == 3

    _run(2, SEGMENTS)
    assert len(_shot_rows(asset_id)) == 3

    _run(3, SEGMENTS[:1])
    assert _shot_rows(asset_id) == [(0, 0.0, 6.24)]


# ---------------------------------------------------------------------------
# AC2/AC3：产品化重建入口
# ---------------------------------------------------------------------------


def test_rebuild_api_rebuilds_from_registry_and_is_idempotent(client: TestClient):
    """删除索引文件后经 HTTP 重建得到同一结果，重复调用幂等且不回显绝对路径。"""
    asset_id = _import(client)
    _index_path().unlink()

    first = client.post(REBUILD_API, headers=AUTH)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["rebuilt"] is True
    assert body["documents"] == 1
    assert body["shots"] == 3
    assert body["skipped_segments"] == 0
    assert body["index_file"] == repo.ASSET_SEARCH_INDEX_FILENAME
    assert "/" not in body["index_file"] and "\\" not in body["index_file"]
    assert _shot_rows(asset_id) == [
        (0, 0.0, 6.24),
        (1, 6.24, 21.05),
        (2, 21.05, 35.0),
    ]

    second = client.post(REBUILD_API, headers=AUTH)
    assert second.status_code == 200, second.text
    assert second.json() == body
    assert _shot_rows(asset_id) == [
        (0, 0.0, 6.24),
        (1, 6.24, 21.05),
        (2, 21.05, 35.0),
    ]


def test_rebuild_api_requires_edit_role(client: TestClient):
    """未认证 401、只读角色 403，且不触发任何重建。"""
    anonymous = client.post(REBUILD_API)
    assert anonymous.status_code == 401
    readonly = client.post(REBUILD_API, headers=READ_ONLY)
    assert readonly.status_code == 403
    assert not _index_path().exists()


# ---------------------------------------------------------------------------
# AC2：失败 fail closed
# ---------------------------------------------------------------------------


class _FailingWriteEngine(SQLiteVecEngine):
    """文档写入必然失败的引擎替身，用于验证失败回滚与错误码。"""

    def batch_upsert_documents(self, documents, **kwargs):  # noqa: ANN001
        raise RuntimeError("注入索引写入失败")


def test_import_write_failure_is_fail_closed(client: TestClient, monkeypatch):
    """索引写入失败时入库整体中止，注册表不留下半成功记录。"""
    original_engine = SQLiteVecEngine
    monkeypatch.setattr(
        "gw.core.sqlite_vec_engine.SQLiteVecEngine", _FailingWriteEngine
    )
    response = client.post(
        IMPORT_API, headers=AUTH, json={"items": [_video_item("海边日落.mp4", SEGMENTS)]}
    )
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "SEARCH_INDEX_WRITE_FAILED"

    # 只撤销本用例的引擎替身，不触碰用例夹具的环境隔离。
    monkeypatch.setattr("gw.core.sqlite_vec_engine.SQLiteVecEngine", original_engine)
    listing = client.get("/api/asset-registry/assets", headers=AUTH)
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 0


def test_rebuild_failure_is_fail_closed_and_keeps_old_index(
    client: TestClient, monkeypatch
):
    """重建失败返回 SEARCH_INDEX_REBUILD_FAILED，旧索引与镜头行完好无损。"""
    asset_id = _import(client)
    before = _shot_rows(asset_id)

    monkeypatch.setattr(
        "gw.core.sqlite_vec_engine.SQLiteVecEngine", _FailingWriteEngine
    )
    response = client.post(REBUILD_API, headers=AUTH)
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "SEARCH_INDEX_REBUILD_FAILED"

    assert _index_path().is_file()
    assert _shot_rows(asset_id) == before
    assert not _index_path().with_name(
        _index_path().stem + ".part" + _index_path().suffix
    ).exists()
