"""MVP1-T1：从 JSON asset_registry 真源完整重建检索索引（AC10/AC1/AC2）。

覆盖：删除索引文件后重建与重建前逐行一致（AC10）、索引只落三态 data 根
（AC1）、重建按内容哈希幂等（AC2）、失败保留旧索引、非法段落跳过计数。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from gw.asset_registry import repository
from gw.core import storage
from gw.core.sqlite_vec_engine import SQLiteVecEngine


def _asset_record(
    asset_id: str,
    name: str,
    *,
    metadata: dict | None = None,
    root_id: str | None = "root-1",
) -> dict:
    record = {
        "asset_id": asset_id,
        "id": asset_id,
        "name": name,
        "kind": "video",
        "archived": False,
        "hidden": False,
        "version": 1,
        "created_at": "2026-10-26T00:00:00+00:00",
        "updated_at": "2026-10-26T00:00:00+00:00",
        "tags": ["mvp1t1token"],
        "relations": [],
        "display_path": None,
        "metadata": dict(metadata or {}),
    }
    if root_id is not None:
        record["root_id"] = root_id
    return record


def _write_assets(records: list[dict]) -> None:
    raw = repository.state().read()
    for record in records:
        raw["assets"][record["asset_id"]] = record
    repository.state().write(raw)


def _index_path() -> Path:
    return storage.data_root() / repository.ASSET_SEARCH_INDEX_FILENAME


def _snapshot(path: Path) -> tuple:
    connection = sqlite3.connect(path)
    try:
        documents = connection.execute(
            "SELECT asset_id, title, content, vector_hash FROM c18_documents ORDER BY asset_id"
        ).fetchall()
        shots = connection.execute(
            """SELECT asset_id, root_id, shot_index, start_sec, end_sec, content_hash
               FROM mvp1_shots ORDER BY asset_id, shot_index"""
        ).fetchall()
        metadata = connection.execute(
            """SELECT model_id, model_version, dimensions, quantization
               FROM mvp1_index_metadata WHERE id=1"""
        ).fetchall()
    finally:
        connection.close()
    return documents, shots, metadata


def test_rebuild_restores_identical_index_from_registry_truth():
    """删除 SQLite 索引文件后重建，文档/镜头/元数据逐行一致（AC10）。"""
    segments = [
        {"start_sec": 0.0, "end_sec": 2.5, "tags": ["夜景"], "summary": "开场"},
        {"start_sec": 2.5, "end_sec": 5.0, "tags": [], "summary": ""},
        {"broken": True},
    ]
    _write_assets(
        [
            _asset_record(
                "ast_0001",
                "mvp1t1镜头片段甲",
                metadata={"video_segments": segments},
            ),
            _asset_record("ast_0002", "mvp1t1文档乙", metadata={}),
        ]
    )

    first = repository.rebuild_search_index()
    assert first["documents"] == 2
    assert first["shots"] == 2
    assert first["skipped_segments"] == 1
    snapshot_before = _snapshot(Path(first["path"]))

    os.remove(first["path"])
    second = repository.rebuild_search_index()
    assert _snapshot(Path(second["path"])) == snapshot_before

    with SQLiteVecEngine(second["path"]) as engine:
        assert {match.asset_id for match in engine.search_fts("mvp1t1token")} == {
            "ast_0001",
            "ast_0002",
        }
        assert engine.shot_duplicate_candidates() == []


def test_rebuild_is_idempotent_by_content_hash():
    """同真源重复重建结果一致，镜头行按内容哈希幂等不重复（AC2）。"""
    _write_assets(
        [
            _asset_record(
                "ast_0001",
                "mvp1t1重复重建",
                metadata={
                    "video_segments": [
                        {"start_sec": 0.0, "end_sec": 1.5, "tags": ["甲"], "summary": "s"}
                    ]
                },
            )
        ]
    )
    first = repository.rebuild_search_index()
    second = repository.rebuild_search_index()
    assert first["shots"] == second["shots"] == 1
    assert _snapshot(Path(first["path"])) == _snapshot(Path(second["path"]))
    assert repository.search_index_status()["shots"] == 1


def test_rebuild_writes_only_under_data_root(tmp_path: Path, monkeypatch):
    """索引文件只落三态 data 根，允许根源目录零新增文件（AC1）。"""
    source = tmp_path / "source-root"
    source.mkdir()
    (source / "mvp1t1素材.txt").write_text("样例", encoding="utf-8")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(source))

    _write_assets([_asset_record("ast_0001", "mvp1t1落点", metadata={})])
    result = repository.rebuild_search_index()

    data_root = storage.data_root()
    assert Path(result["path"]) == _index_path()
    assert _index_path().parent == data_root
    produced = {path.name for path in source.rglob("*")}
    assert produced == {"mvp1t1素材.txt"}


def test_rebuild_excludes_invisible_assets():
    _write_assets(
        [
            _asset_record("ast_0001", "mvp1t1可见甲", metadata={}),
            _asset_record(
                "ast_0002",
                "mvp1t1隐藏乙",
                metadata={"workflow_owner": "someone-else"},
            ),
        ]
    )
    result = repository.rebuild_search_index()
    assert result["documents"] == 1
    with SQLiteVecEngine(result["path"]) as engine:
        assert [match.asset_id for match in engine.search_fts("mvp1t1")] == ["ast_0001"]


def test_rebuild_sets_honest_index_metadata():
    """元数据如实记录：未接入嵌入模型则模型字段为空，维度与量化取引擎实况。"""
    _write_assets([_asset_record("ast_0001", "mvp1t1元数据", metadata={})])
    result = repository.rebuild_search_index()
    with SQLiteVecEngine(result["path"], dimensions=384) as engine:
        expected_quantization = "float32" if engine.vector_enabled else None
        metadata = engine.index_metadata()
    assert metadata is not None
    assert metadata.model_id is None
    assert metadata.model_version is None
    assert metadata.dimensions == 384
    assert metadata.quantization == expected_quantization

    status = repository.search_index_status()
    assert status["metadata"] == {
        "model_id": None,
        "model_version": None,
        "dimensions": 384,
        "quantization": expected_quantization,
    }


def test_rebuild_failure_preserves_previous_index(monkeypatch):
    """重建失败时清理临时文件并保留旧索引，不产生半成品。"""
    _write_assets([_asset_record("ast_0001", "mvp1t1旧索引", metadata={})])
    first = repository.rebuild_search_index()
    snapshot_before = _snapshot(Path(first["path"]))

    real_engine = SQLiteVecEngine

    class FailingEngine(real_engine):
        def batch_upsert_documents(self, documents, **kwargs):
            raise RuntimeError("注入重建失败")

    monkeypatch.setattr(
        "gw.core.sqlite_vec_engine.SQLiteVecEngine", FailingEngine
    )
    with pytest.raises(RuntimeError, match="注入重建失败"):
        repository.rebuild_search_index()

    # monkeypatch 在测试结束时自动还原；此处断言旧索引完好、无半成品临时文件。
    assert _index_path().is_file()
    assert _snapshot(_index_path()) == snapshot_before
    assert not (_index_path().parent / "asset_registry_search.part.sqlite3").exists()


def test_search_index_status_reports_missing_file():
    status = repository.search_index_status()
    assert status["exists"] is False
    assert status["documents"] == 0
    assert status["shots"] == 0
    assert status["metadata"] is None
    assert status["path"] == str(_index_path())


def test_shot_content_hash_is_stable_and_content_bound():
    base = {"start_sec": 0.0, "end_sec": 2.5, "tags": ["夜景"], "summary": "开场"}
    same = {"end_sec": 2.5, "summary": "开场", "start_sec": 0.0, "tags": ["夜景"]}
    assert repository.shot_content_hash(base) == repository.shot_content_hash(same)

    shifted = dict(base, end_sec=3.0)
    retagged = dict(base, tags=["白天"])
    resummarized = dict(base, summary="结尾")
    assert repository.shot_content_hash(base) != repository.shot_content_hash(shifted)
    assert repository.shot_content_hash(base) != repository.shot_content_hash(retagged)
    assert repository.shot_content_hash(base) != repository.shot_content_hash(resummarized)
