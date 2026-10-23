# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""单文件 SQLite FTS5 / sqlite-vec 存储引擎。"""

from __future__ import annotations

import hashlib
import importlib
import logging
import math
import sqlite3
import struct
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_SEARCH_LIMIT = 1000
MAX_QUERY_LENGTH = 4096


@dataclass(frozen=True, slots=True)
class FTSMatch:
    """FTS5 关键词轨命中。"""

    asset_id: str
    score: float


@dataclass(frozen=True, slots=True)
class VectorMatch:
    """sqlite-vec 向量轨命中。"""

    asset_id: str
    distance: float


def _load_sqlite_vec(connection: sqlite3.Connection) -> bool:
    """若 Python 环境提供官方 sqlite-vec 绑定，则安全地短暂开放扩展加载。"""
    module = importlib.import_module("sqlite_vec")
    enable_load_extension = getattr(connection, "enable_load_extension", None)
    if not callable(enable_load_extension):
        raise RuntimeError("当前 SQLite 不支持加载 sqlite-vec 扩展")
    enable_load_extension(True)
    try:
        module.load(connection)
    finally:
        enable_load_extension(False)
    return True


def _serialize_vector(vector: Sequence[float], dimensions: int) -> bytes:
    if isinstance(vector, (str, bytes, bytearray)):
        raise ValueError("向量必须是数值序列")
    try:
        values = tuple(float(value) for value in vector)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("向量必须只包含有限数值") from exc
    if len(values) != dimensions:
        raise ValueError(f"向量维度必须为 {dimensions}")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("向量必须只包含有限数值")
    return struct.pack(f"<{dimensions}f", *values)


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x1100 <= codepoint <= 0x11FF
        or 0x2E80 <= codepoint <= 0x2FFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0x31F0 <= codepoint <= 0x31FF
        or 0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def _search_units(text: str) -> list[tuple[bool, str]]:
    units: list[tuple[bool, str]] = []
    index = 0
    while index < len(text):
        character = text[index]
        if _is_cjk(character):
            end = index + 1
            while end < len(text) and _is_cjk(text[end]):
                end += 1
            units.append((True, text[index:end]))
            index = end
        elif character.isalnum():
            end = index + 1
            while end < len(text) and text[end].isalnum() and not _is_cjk(text[end]):
                end += 1
            units.append((False, text[index:end].casefold()))
            index = end
        else:
            index += 1
    return units


def _fts_index_text(text: str) -> str:
    tokens: list[str] = []
    for is_cjk_unit, value in _search_units(text):
        if not is_cjk_unit:
            tokens.append(value)
            continue
        tokens.extend(value)
        tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
    return " ".join(tokens)


def _fts_query(query: str) -> str:
    _validate_query(query)
    terms: list[str] = []
    for is_cjk_unit, value in _search_units(query.strip()):
        if is_cjk_unit and len(value) > 1:
            terms.extend(value[index : index + 2] for index in range(len(value) - 1))
        else:
            terms.append(value)
    # OR 保留多词查询的召回率；每个词项都显式引用，不能注入 FTS5 语法。
    return " OR ".join(f'"{term}"' for term in dict.fromkeys(terms))


def _validate_query(query: str) -> str:
    if not isinstance(query, str):
        raise ValueError("query 必须是字符串")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"query 长度不得超过 {MAX_QUERY_LENGTH} 个字符")
    return query


class SQLiteVecEngine:
    """在一个显式 SQLite 文件中维护 FTS5 与可选 sqlite-vec 索引。

    `db_path` 必须是绝对路径，测试可使用 `:memory:`。扩展未安装或无法加载时，
    向量检索关闭而 FTS5 保持可用；此时不会把未索引的向量伪装为已存储。
    """

    _DOCUMENTS = "c18_documents"
    _FTS = "c18_documents_fts"
    _VECTORS = "c18_vectors"

    def __init__(self, db_path: str | Path, *, dimensions: int = 384) -> None:
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
            raise ValueError("向量维度必须是正整数")
        self.dimensions = dimensions
        self.vector_enabled = False
        self.vector_error: str | None = None
        self._vector_search_state = threading.local()
        self._lock = threading.RLock()
        self._closed = False

        if isinstance(db_path, str) and db_path == ":memory:":
            sqlite_path = ":memory:"
        else:
            path = Path(db_path)
            if not path.is_absolute():
                raise ValueError("SQLite 数据库路径必须是绝对路径")
            if not path.parent.is_dir():
                raise ValueError("SQLite 数据库父目录必须已存在")
            sqlite_path = str(path)

        self._db = sqlite3.connect(
            sqlite_path, timeout=10, isolation_level=None, check_same_thread=False
        )
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=10000")
        try:
            self._initialize_schema()
        except Exception:
            self._db.close()
            self._closed = True
            raise

    def _initialize_schema(self) -> None:
        self._db.execute(
            f"""CREATE TABLE IF NOT EXISTS {self._DOCUMENTS} (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                vector_hash TEXT
            )"""
        )
        self._db.execute(
            f"""CREATE VIRTUAL TABLE IF NOT EXISTS {self._FTS} USING fts5(
                asset_id UNINDEXED, title, content, tokenize='unicode61'
            )"""
        )
        try:
            _load_sqlite_vec(self._db)
        except Exception as exc:
            self.vector_error = f"sqlite-vec unavailable: {type(exc).__name__}"
            logger.warning("sqlite-vec 未能加载，检索降级为 FTS5：%s", self.vector_error)
            return

        try:
            self._db.execute(
                f"""CREATE VIRTUAL TABLE IF NOT EXISTS {self._VECTORS}
                    USING vec0(embedding float[{self.dimensions}])"""
            )
            schema = self._db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (self._VECTORS,),
            ).fetchone()
            if not schema or f"float[{self.dimensions}]" not in str(schema[0]):
                raise RuntimeError("现有 sqlite-vec 索引维度与配置不匹配")
        except Exception as exc:
            self.vector_error = f"sqlite-vec schema unavailable: {type(exc).__name__}"
            logger.warning("sqlite-vec 索引不可用，检索降级为 FTS5：%s", self.vector_error)
            return
        self.vector_enabled = True
        self.vector_error = None

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("SQLite 检索引擎已关闭")

    @staticmethod
    def _validate_limit(limit: int) -> int:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_SEARCH_LIMIT
        ):
            raise ValueError(f"limit 必须在 1 到 {MAX_SEARCH_LIMIT} 之间")
        return limit

    def upsert_document(
        self,
        *,
        asset_id: str,
        title: str,
        content: str,
        embedding: Sequence[float] | None = None,
    ) -> bool:
        """写入或替换一条资产文档；返回本次是否写入了向量索引。"""
        self._ensure_open()
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ValueError("asset_id 不能为空")
        if not isinstance(title, str) or not isinstance(content, str):
            raise ValueError("title 与 content 必须是字符串")
        validated_embedding = (
            _serialize_vector(embedding, self.dimensions)
            if embedding is not None
            else None
        )
        vector_blob = validated_embedding if self.vector_enabled else None
        vector_hash = hashlib.sha256(vector_blob).hexdigest() if vector_blob else None

        with self._lock:
            self._ensure_open()
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    f"SELECT row_id FROM {self._DOCUMENTS} WHERE asset_id=?",  # noqa: S608
                    (asset_id,),
                ).fetchone()
                if row:
                    row_id = int(row["row_id"])
                    self._db.execute(
                        f"UPDATE {self._DOCUMENTS} SET title=?,content=?,vector_hash=? WHERE row_id=?",  # noqa: S608
                        (title, content, vector_hash, row_id),
                    )
                    self._db.execute(
                        f"DELETE FROM {self._FTS} WHERE rowid=?", (row_id,)  # noqa: S608
                    )
                else:
                    cursor = self._db.execute(
                        f"INSERT INTO {self._DOCUMENTS}(asset_id,title,content,vector_hash) VALUES(?,?,?,?)",
                        (asset_id, title, content, vector_hash),
                    )
                    row_id = int(cursor.lastrowid)

                self._db.execute(
                    f"INSERT INTO {self._FTS}(rowid,asset_id,title,content) VALUES(?,?,?,?)",
                    (
                        row_id,
                        asset_id,
                        _fts_index_text(title),
                        _fts_index_text(content),
                    ),
                )
                if self.vector_enabled:
                    self._db.execute(
                        f"DELETE FROM {self._VECTORS} WHERE rowid=?", (row_id,)  # noqa: S608
                    )
                    if vector_blob is not None:
                        self._db.execute(
                            f"INSERT INTO {self._VECTORS}(rowid,embedding) VALUES(?,?)",
                            (row_id, vector_blob),
                        )
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return vector_blob is not None

    def delete_document(self, asset_id: str) -> bool:
        """从文档与可用索引中删除一条资产。"""
        self._ensure_open()
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ValueError("asset_id 不能为空")
        with self._lock:
            self._ensure_open()
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    f"SELECT row_id FROM {self._DOCUMENTS} WHERE asset_id=?",  # noqa: S608
                    (asset_id,),
                ).fetchone()
                if not row:
                    self._db.commit()
                    return False
                row_id = int(row["row_id"])
                self._db.execute(
                    f"DELETE FROM {self._FTS} WHERE rowid=?", (row_id,)  # noqa: S608
                )
                if self.vector_enabled:
                    self._db.execute(
                        f"DELETE FROM {self._VECTORS} WHERE rowid=?", (row_id,)  # noqa: S608
                    )
                self._db.execute(
                    f"DELETE FROM {self._DOCUMENTS} WHERE row_id=?", (row_id,)  # noqa: S608
                )
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return True

    def search_fts(self, query: str, *, limit: int = 20) -> list[FTSMatch]:
        """搜索关键词；limit 不在 1~1000 时抛 ValueError，query 最长 4096 字符。"""
        self._ensure_open()
        limit = self._validate_limit(limit)
        expression = _fts_query(_validate_query(query))
        if not expression:
            return []
        with self._lock:
            self._ensure_open()
            sql = (
                f"""SELECT d.asset_id, bm25({self._FTS}) AS rank
                    FROM {self._FTS} AS f
                    JOIN {self._DOCUMENTS} AS d ON d.row_id=f.rowid
                    WHERE {self._FTS} MATCH ?
                    ORDER BY rank ASC, d.asset_id ASC
                    LIMIT ?"""  # noqa: S608
            )
            rows = self._db.execute(sql, (expression, limit)).fetchall()
        return [FTSMatch(asset_id=str(row["asset_id"]), score=-float(row["rank"])) for row in rows]

    def search_vectors(
        self, query_vector: Sequence[float], *, limit: int = 20
    ) -> list[VectorMatch]:
        """搜索向量；limit 不在 1~1000 时抛 ValueError。

        单次 SQLite 查询错误只让本次调用降级，不会关闭已加载的向量轨。
        """
        self._ensure_open()
        limit = self._validate_limit(limit)
        self._vector_search_state.degraded = False
        if not self.vector_enabled:
            self._vector_search_state.degraded = True
            return []
        vector_blob = _serialize_vector(query_vector, self.dimensions)
        with self._lock:
            self._ensure_open()
            try:
                sql = (
                    f"""SELECT d.asset_id, nearest.distance
                        FROM (
                            SELECT rowid, distance
                            FROM {self._VECTORS}
                            WHERE embedding MATCH ?
                            ORDER BY distance ASC
                            LIMIT ?
                        ) AS nearest
                        JOIN {self._DOCUMENTS} AS d ON d.row_id=nearest.rowid
                        WHERE d.vector_hash IS NOT NULL
                        ORDER BY nearest.distance ASC, d.asset_id ASC"""  # noqa: S608
                )
                rows = self._db.execute(sql, (vector_blob, limit)).fetchall()
            except (sqlite3.Error, OverflowError) as exc:
                self._vector_search_state.degraded = True
                logger.warning(
                    "sqlite-vec 查询失败，本次混合检索暂用 FTS5：%s",
                    type(exc).__name__,
                )
                return []
        return [
            VectorMatch(asset_id=str(row["asset_id"]), distance=float(row["distance"]))
            for row in rows
        ]

    @property
    def last_vector_search_degraded(self) -> bool:
        """返回当前线程最近一次向量查询是否临时降级。"""
        return bool(getattr(self._vector_search_state, "degraded", False))

    @property
    def vector_version(self) -> str | None:
        """返回已加载扩展版本；扩展不可用时返回 None。"""
        self._ensure_open()
        if not self.vector_enabled:
            return None
        with self._lock:
            try:
                row = self._db.execute("SELECT vec_version()").fetchone()
            except sqlite3.Error:
                return None
        return str(row[0]) if row else None

    def close(self) -> None:
        if not self._closed:
            with self._lock:
                if not self._closed:
                    self._db.close()
                    self._closed = True

    def __enter__(self) -> SQLiteVecEngine:
        self._ensure_open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
