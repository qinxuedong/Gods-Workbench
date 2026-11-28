# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0

"""与资产检索数据库共址的 ASR 镜头 FTS5 文档。"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from gw.core import storage

_TOKEN = re.compile(r"[^0-9a-zA-Z\u3400-\u4dbf\u4e00-\u9fff]+")


def _database_path() -> Path:
    from gw.asset_registry.repository import ASSET_SEARCH_INDEX_FILENAME

    return storage.data_root() / ASSET_SEARCH_INDEX_FILENAME


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS asr_transcript_segments (
            rowid INTEGER PRIMARY KEY,
            asset_id TEXT NOT NULL,
            segment_idx INTEGER NOT NULL,
            start_sec REAL NOT NULL,
            end_sec REAL NOT NULL,
            text TEXT NOT NULL,
            UNIQUE(asset_id, segment_idx)
        )"""
    )
    connection.execute(
        """CREATE VIRTUAL TABLE IF NOT EXISTS asr_transcript_fts
        USING fts5(text, content='asr_transcript_segments', content_rowid='rowid', tokenize='trigram')"""
    )
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS asr_transcript_segments_ai
        AFTER INSERT ON asr_transcript_segments BEGIN
          INSERT INTO asr_transcript_fts(rowid, text) VALUES (new.rowid, new.text);
        END"""
    )
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS asr_transcript_segments_ad
        AFTER DELETE ON asr_transcript_segments BEGIN
          INSERT INTO asr_transcript_fts(asr_transcript_fts, rowid, text)
          VALUES ('delete', old.rowid, old.text);
        END"""
    )


def _segment_rows(segments: Any) -> list[dict[str, Any]]:
    if not isinstance(segments, list):
        return []
    rows = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        transcripts = segment.get("transcript")
        values = [
            item for item in transcripts
            if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip()
        ] if isinstance(transcripts, list) else []
        text = " ".join(item["text"].strip() for item in values)
        if not text:
            continue
        try:
            start = min(float(item["start_sec"]) for item in values)
            end = max(float(item["end_sec"]) for item in values)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if start < 0 or end <= start:
            continue
        rows.append({
            "segment_idx": index,
            "start_sec": start,
            "end_sec": end,
            "text": text,
        })
    return rows


def replace_asset_transcripts(asset_id: str, video_segments: Any) -> int:
    """按 asset_id 整组事务替换；镜头序号唯一，重复提交不会累积文档。"""
    asset_id = str(asset_id or "").strip()
    if not asset_id:
        raise ValueError("asset_id 不能为空")
    rows = _segment_rows(video_segments)
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as connection:
        _ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM asr_transcript_segments WHERE asset_id = ?", (asset_id,))
        connection.executemany(
            """INSERT INTO asr_transcript_segments(asset_id, segment_idx, start_sec, end_sec, text)
               VALUES (?, ?, ?, ?, ?)""",
            [(asset_id, row["segment_idx"], row["start_sec"], row["end_sec"], row["text"]) for row in rows],
        )
    return len(rows)


def delete_asset_transcripts(asset_id: str) -> None:
    path = _database_path()
    if not path.is_file():
        return
    with _connect(path) as connection:
        _ensure_schema(connection)
        connection.execute("DELETE FROM asr_transcript_segments WHERE asset_id = ?", (str(asset_id),))


def search_transcripts(query: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """FTS5 短语匹配；过短查询使用带参数的 LIKE 兼容 trigram 的 1-2 字查询。"""
    clean_query = str(query or "").strip()
    if not clean_query:
        return []
    path = _database_path()
    if not path.is_file():
        return []
    bounded_limit = max(1, min(int(limit), 200))
    with _connect(path) as connection:
        _ensure_schema(connection)
        if len(clean_query) >= 3:
            safe_phrase = _TOKEN.sub(" ", clean_query).strip()
            if len(safe_phrase) < 3:
                return []
            expression = '"' + safe_phrase.replace('"', '""') + '"'
            rows = connection.execute(
                """SELECT s.asset_id, s.segment_idx, s.start_sec, s.end_sec, s.text
                   FROM asr_transcript_fts AS f
                   JOIN asr_transcript_segments AS s ON s.rowid = f.rowid
                   WHERE asr_transcript_fts MATCH ?
                   ORDER BY bm25(asr_transcript_fts), s.asset_id, s.segment_idx
                   LIMIT ?""",
                (expression, bounded_limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT asset_id, segment_idx, start_sec, end_sec, text
                   FROM asr_transcript_segments WHERE text LIKE ? ESCAPE '\\'
                   ORDER BY asset_id, segment_idx LIMIT ?""",
                ("%" + clean_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", bounded_limit),
            ).fetchall()
    return [dict(row) for row in rows]


def matching_asset_ids(query: str) -> set[str]:
    return {str(row["asset_id"]) for row in search_transcripts(query, limit=200)}
