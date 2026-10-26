"""MVP1-T1：SQLiteVecEngine 镜头级索引契约（AC1/AC2/AC6）。

覆盖：镜头表字段契约、按 (asset_id, shot_index) 内容哈希幂等（AC2）、
重复镜头候选只报告不删除（AC6）、索引文件只落在显式给定的 data 根路径（AC1）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gw.core.sqlite_vec_engine import SQLiteVecEngine

HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture()
def engine(tmp_path: Path) -> SQLiteVecEngine:
    db_path = tmp_path / "data" / "asset_registry_search.sqlite3"
    db_path.parent.mkdir(parents=True)
    with SQLiteVecEngine(db_path, dimensions=4) as instance:
        yield instance


def _shot(asset_id: str, shot_index: int, content_hash: str, **overrides) -> dict:
    row = {
        "asset_id": asset_id,
        "root_id": "root-1",
        "shot_index": shot_index,
        "start_sec": shot_index * 2.5,
        "end_sec": shot_index * 2.5 + 2.5,
        "content_hash": content_hash,
    }
    row.update(overrides)
    return row


def test_shot_table_keeps_contract_columns_and_index_location(tmp_path: Path):
    """镜头表字段契约：六个业务列齐备，索引只落在显式 data 根路径（AC1）。"""
    db_path = tmp_path / "data" / "asset_registry_search.sqlite3"
    db_path.parent.mkdir(parents=True)
    with SQLiteVecEngine(db_path, dimensions=4) as instance:
        instance.batch_upsert_shots([_shot("ast_0001", 0, HASH_A)])
        connection = sqlite3.connect(db_path)
        try:
            columns = {
                row[1]: row[2]
                for row in connection.execute("PRAGMA table_info(mvp1_shots)")
            }
        finally:
            connection.close()
    assert set(columns) >= {
        "asset_id",
        "root_id",
        "shot_index",
        "start_sec",
        "end_sec",
        "content_hash",
    }
    assert db_path.is_file()
    # AC1：data 根之外不产生任何索引文件。
    produced = {path.name for path in tmp_path.rglob("*") if path.is_file()}
    assert produced == {"asset_registry_search.sqlite3"}


def test_shot_upsert_is_idempotent_by_asset_and_index(engine: SQLiteVecEngine):
    """同一 (asset_id, shot_index) 重复写入替换不新增，内容哈希一致零副作用（AC2）。"""
    first = _shot("ast_0001", 0, HASH_A)
    assert engine.batch_upsert_shots([first]) == 1
    assert engine.shot_count() == 1

    assert engine.batch_upsert_shots([dict(first)]) == 1
    assert engine.shot_count() == 1

    moved = _shot("ast_0001", 0, HASH_B, start_sec=1.0, end_sec=4.0)
    engine.batch_upsert_shots([moved])
    rows = engine.list_shots(asset_id="ast_0001")
    assert len(rows) == 1
    assert rows[0].content_hash == HASH_B
    assert (rows[0].start_sec, rows[0].end_sec) == (1.0, 4.0)

    batch = [_shot("ast_0001", 0, HASH_A), _shot("ast_0001", 0, HASH_B)]
    engine.batch_upsert_shots(batch)
    rows = engine.list_shots(asset_id="ast_0001")
    assert len(rows) == 1
    assert rows[0].content_hash == HASH_B


def test_list_shots_orders_by_asset_then_index(engine: SQLiteVecEngine):
    engine.batch_upsert_shots(
        [
            _shot("ast_0002", 1, HASH_B),
            _shot("ast_0001", 0, HASH_A),
            _shot("ast_0002", 0, HASH_A),
        ]
    )
    rows = engine.list_shots()
    assert [(row.asset_id, row.shot_index) for row in rows] == [
        ("ast_0001", 0),
        ("ast_0002", 0),
        ("ast_0002", 1),
    ]
    assert all(row.root_id == "root-1" for row in rows)


def test_duplicate_candidates_report_without_deleting(engine: SQLiteVecEngine):
    """同哈希跨资产聚合为候选组；只报告，不自动删除任何行（AC6）。"""
    engine.batch_upsert_shots(
        [
            _shot("ast_0001", 0, HASH_A),
            _shot("ast_0002", 0, HASH_A),
            _shot("ast_0003", 0, HASH_A),
            _shot("ast_0004", 0, HASH_B),
        ]
    )
    groups = engine.shot_duplicate_candidates()
    assert [group.content_hash for group in groups] == [HASH_A]
    assert [member.asset_id for member in groups[0].members] == [
        "ast_0001",
        "ast_0002",
        "ast_0003",
    ]
    assert engine.shot_count() == 4
    # 唯一哈希不进候选组。
    assert engine.list_shots(content_hash=HASH_B)[0].asset_id == "ast_0004"
    # 只有显式删除才移除镜头行。
    assert engine.delete_shots("ast_0001") == 1
    assert engine.shot_count() == 3


def test_delete_shots_only_touches_target_asset(engine: SQLiteVecEngine):
    engine.batch_upsert_shots(
        [_shot("ast_0001", 0, HASH_A), _shot("ast_0001", 1, HASH_B), _shot("ast_0002", 0, HASH_A)]
    )
    assert engine.delete_shots("ast_0001") == 2
    assert engine.delete_shots("ast_0001") == 0
    remaining = engine.list_shots()
    assert [row.asset_id for row in remaining] == ["ast_0002"]


def test_batch_upsert_shots_batching_uses_atomic_batches(engine: SQLiteVecEngine):
    rows = [_shot(f"ast_{number:04d}", 0, HASH_A) for number in range(1, 8)]
    assert engine.batch_upsert_shots(rows, batch_size=3) == 7
    assert engine.shot_count() == 7
    with pytest.raises(ValueError, match="batch_size"):
        engine.batch_upsert_shots(rows, batch_size=0)


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"asset_id": "  "}, "asset_id 不能为空"),
        ({"root_id": 1}, "root_id 必须是字符串"),
        ({"shot_index": -1}, "shot_index 必须是非负整数"),
        ({"shot_index": True}, "shot_index 必须是非负整数"),
        ({"start_sec": -0.5, "end_sec": 1.0}, "镜头时间戳"),
        ({"end_sec": 0.0, "start_sec": 0.0}, "镜头时间戳"),
        ({"content_hash": ""}, "content_hash 不能为空"),
    ],
)
def test_batch_upsert_shots_rejects_invalid_rows(
    engine: SQLiteVecEngine, overrides: dict, message: str
):
    row = _shot("ast_0001", 0, HASH_A)
    row.update(overrides)
    with pytest.raises(ValueError, match=message):
        engine.batch_upsert_shots([row])
    assert engine.shot_count() == 0


def test_index_metadata_roundtrip_and_dimension_guard(engine: SQLiteVecEngine):
    """索引元数据契约：模型 id/版本/维度/量化格式可写可读，维度错配拒绝。"""
    assert engine.index_metadata() is None
    engine.set_index_metadata(
        model_id=None, model_version=None, dimensions=4, quantization="float32"
    )
    metadata = engine.index_metadata()
    assert metadata is not None
    assert (metadata.model_id, metadata.model_version) == (None, None)
    assert metadata.dimensions == 4
    assert metadata.quantization == "float32"

    engine.set_index_metadata(
        model_id="clip-test", model_version="v1", dimensions=4, quantization="int8"
    )
    metadata = engine.index_metadata()
    assert metadata is not None
    assert metadata.model_id == "clip-test"
    assert metadata.quantization == "int8"

    with pytest.raises(ValueError, match="维度不一致"):
        engine.set_index_metadata(
            model_id="clip-test", model_version="v1", dimensions=8, quantization="int8"
        )
    assert engine.index_metadata() is not None
