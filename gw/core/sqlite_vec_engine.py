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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True, slots=True)
class ShotRecord:
    """镜头级索引行；内容哈希是幂等与查重候选的事实键。"""

    asset_id: str
    root_id: str
    shot_index: int
    start_sec: float
    end_sec: float
    content_hash: str


@dataclass(frozen=True, slots=True)
class ShotDuplicateGroup:
    """按内容哈希聚合的重复镜头候选组；只报告，不删除。"""

    content_hash: str
    members: tuple[ShotRecord, ...]


@dataclass(frozen=True, slots=True)
class IndexMetadata:
    """索引元数据；记录产出该索引的模型 id/版本、维度与量化格式。"""

    model_id: str | None
    model_version: str | None
    dimensions: int
    quantization: str | None


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
        self._vec_extension_loaded = False
        self._vector_search_state = threading.local()
        self._lock = threading.RLock()
        self._closed = False
        self._vector_version_warning_emitted = False

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
        # MVP1 镜头级表与索引元数据：全部使用字面量静态 SQL（含 DDL/DML），
        # 数据值一律参数绑定，杜绝动态拼接。
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS mvp1_shots (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id TEXT NOT NULL,
                root_id TEXT NOT NULL DEFAULT '',
                shot_index INTEGER NOT NULL,
                start_sec REAL NOT NULL,
                end_sec REAL NOT NULL,
                content_hash TEXT NOT NULL,
                UNIQUE(asset_id, shot_index)
            )"""
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS mvp1_shots_hash ON mvp1_shots(content_hash)"
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS mvp1_index_metadata (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                model_id TEXT,
                model_version TEXT,
                dimensions INTEGER NOT NULL,
                quantization TEXT
            )"""
        )
        metadata = self._db.execute(
            "SELECT dimensions FROM mvp1_index_metadata WHERE id=1"
        ).fetchone()
        if metadata is not None:
            stored_dimensions = metadata["dimensions"]
            if isinstance(stored_dimensions, int) and stored_dimensions > 0:
                self.dimensions = stored_dimensions
        try:
            _load_sqlite_vec(self._db)
        except Exception as exc:
            self.vector_error = f"sqlite-vec unavailable: {type(exc).__name__}"
            logger.warning("sqlite-vec 未能加载，检索降级为 FTS5：%s", self.vector_error)
            return
        self._vec_extension_loaded = True

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
        prepared = self._prepare_document(asset_id, title, content, embedding)

        with self._lock:
            self._ensure_open()
            self._db.execute("BEGIN IMMEDIATE")
            try:
                vector_indexed = self._upsert_document_in_transaction(*prepared)
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return vector_indexed

    def batch_upsert_documents(
        self,
        documents: Iterable[Mapping[str, Any]],
        *,
        batch_size: int = 500,
    ) -> list[bool]:
        """分批写入资产文档；每批原子提交，返回各条记录的向量写入结果。"""
        self._ensure_open()
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size < 1
        ):
            raise ValueError("batch_size 必须是正整数")

        iterator = iter(documents)
        vector_results: list[bool] = []
        while True:
            batch: list[Mapping[str, Any]] = []
            try:
                for _ in range(batch_size):
                    batch.append(next(iterator))
            except StopIteration:
                pass
            if not batch:
                return vector_results

            with self._lock:
                self._ensure_open()
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    for document in batch:
                        prepared = self._prepare_document(
                            document.get("asset_id"),
                            document.get("title"),
                            document.get("content"),
                            document.get("embedding"),
                        )
                        vector_results.append(
                            self._upsert_document_in_transaction(*prepared)
                        )
                    self._db.commit()
                except Exception:
                    self._db.rollback()
                    raise

    def _prepare_document(
        self,
        asset_id: Any,
        title: Any,
        content: Any,
        embedding: Any,
    ) -> tuple[str, str, str, bytes | None, str | None]:
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
        return asset_id, title, content, vector_blob, vector_hash

    def _upsert_document_in_transaction(
        self,
        asset_id: str,
        title: str,
        content: str,
        vector_blob: bytes | None,
        vector_hash: str | None,
    ) -> bool:
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

    def batch_upsert_shots(
        self,
        shots: Iterable[Mapping[str, Any]],
        *,
        batch_size: int = 500,
    ) -> int:
        """分批写入镜头级索引行，按 (asset_id, shot_index) 幂等替换。

        返回实际写入的行数；同一批内重复的 (asset_id, shot_index) 以最后
        一条为准，不产生重复镜头行。
        """
        self._ensure_open()
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size < 1
        ):
            raise ValueError("batch_size 必须是正整数")

        iterator = iter(shots)
        written = 0
        while True:
            batch: list[Mapping[str, Any]] = []
            try:
                for _ in range(batch_size):
                    batch.append(next(iterator))
            except StopIteration:
                pass
            if not batch:
                return written

            with self._lock:
                self._ensure_open()
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    for shot in batch:
                        prepared = self._prepare_shot(shot)
                        self._upsert_shot_in_transaction(*prepared)
                        written += 1
                    self._db.commit()
                except Exception:
                    self._db.rollback()
                    raise

    def _prepare_shot(
        self, shot: Mapping[str, Any]
    ) -> tuple[str, str, int, float, float, str]:
        if not isinstance(shot, Mapping):
            raise ValueError("镜头行必须是映射")
        asset_id = shot.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ValueError("asset_id 不能为空")
        root_id = shot.get("root_id")
        if root_id is None:
            root_id = ""
        if not isinstance(root_id, str):
            raise ValueError("root_id 必须是字符串")
        shot_index = shot.get("shot_index")
        if (
            isinstance(shot_index, bool)
            or not isinstance(shot_index, int)
            or shot_index < 0
        ):
            raise ValueError("shot_index 必须是非负整数")
        for name, value in (
            ("start_sec", shot.get("start_sec")),
            ("end_sec", shot.get("end_sec")),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} 必须是数值")
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} 必须是有限数值")
        start_sec = float(shot["start_sec"])
        end_sec = float(shot["end_sec"])
        if start_sec < 0 or end_sec <= start_sec:
            raise ValueError("镜头时间戳必须满足 0 <= start_sec < end_sec")
        content_hash = shot.get("content_hash")
        if not isinstance(content_hash, str) or not content_hash.strip():
            raise ValueError("content_hash 不能为空")
        return asset_id, root_id, shot_index, start_sec, end_sec, content_hash

    def _upsert_shot_in_transaction(
        self,
        asset_id: str,
        root_id: str,
        shot_index: int,
        start_sec: float,
        end_sec: float,
        content_hash: str,
    ) -> None:
        row = self._db.execute(
            "SELECT row_id FROM mvp1_shots WHERE asset_id=? AND shot_index=?",
            (asset_id, shot_index),
        ).fetchone()
        if row:
            self._db.execute(
                """UPDATE mvp1_shots
                   SET root_id=?, start_sec=?, end_sec=?, content_hash=?
                   WHERE row_id=?""",
                (root_id, start_sec, end_sec, content_hash, int(row["row_id"])),
            )
        else:
            self._db.execute(
                """INSERT INTO mvp1_shots
                   (asset_id, root_id, shot_index, start_sec, end_sec, content_hash)
                   VALUES(?,?,?,?,?,?)""",
                (asset_id, root_id, shot_index, start_sec, end_sec, content_hash),
            )

    def delete_shots(self, asset_id: str) -> int:
        """删除一个资产的全部镜头行，返回删除数量；不触碰文档与向量。"""
        self._ensure_open()
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ValueError("asset_id 不能为空")
        with self._lock:
            self._ensure_open()
            self._db.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._db.execute(
                    "DELETE FROM mvp1_shots WHERE asset_id=?", (asset_id,)
                )
                removed = int(cursor.rowcount)
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return removed

    def list_shots(
        self,
        *,
        asset_id: str | None = None,
        content_hash: str | None = None,
        limit: int = 1000,
    ) -> list[ShotRecord]:
        """按条件读取镜头行；默认按 asset_id、shot_index 稳定排序。"""
        self._ensure_open()
        limit = self._validate_limit(limit)
        if asset_id is not None:
            if not isinstance(asset_id, str) or not asset_id.strip():
                raise ValueError("asset_id 不能为空")
        if content_hash is not None:
            if not isinstance(content_hash, str) or not content_hash.strip():
                raise ValueError("content_hash 不能为空")
        with self._lock:
            self._ensure_open()
            # WHERE 只从固定分支选取，语句为字面量，取值全部参数绑定。
            if asset_id is not None and content_hash is not None:
                rows = self._db.execute(
                    """SELECT asset_id, root_id, shot_index, start_sec, end_sec, content_hash
                       FROM mvp1_shots WHERE asset_id=? AND content_hash=?
                       ORDER BY asset_id ASC, shot_index ASC LIMIT ?""",
                    (asset_id, content_hash, limit),
                ).fetchall()
            elif asset_id is not None:
                rows = self._db.execute(
                    """SELECT asset_id, root_id, shot_index, start_sec, end_sec, content_hash
                       FROM mvp1_shots WHERE asset_id=?
                       ORDER BY asset_id ASC, shot_index ASC LIMIT ?""",
                    (asset_id, limit),
                ).fetchall()
            elif content_hash is not None:
                rows = self._db.execute(
                    """SELECT asset_id, root_id, shot_index, start_sec, end_sec, content_hash
                       FROM mvp1_shots WHERE content_hash=?
                       ORDER BY asset_id ASC, shot_index ASC LIMIT ?""",
                    (content_hash, limit),
                ).fetchall()
            else:
                rows = self._db.execute(
                    """SELECT asset_id, root_id, shot_index, start_sec, end_sec, content_hash
                       FROM mvp1_shots
                       ORDER BY asset_id ASC, shot_index ASC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [
            ShotRecord(
                asset_id=str(row["asset_id"]),
                root_id=str(row["root_id"]),
                shot_index=int(row["shot_index"]),
                start_sec=float(row["start_sec"]),
                end_sec=float(row["end_sec"]),
                content_hash=str(row["content_hash"]),
            )
            for row in rows
        ]

    def shot_duplicate_candidates(
        self, *, limit: int = 1000
    ) -> list[ShotDuplicateGroup]:
        """按内容哈希聚合重复镜头候选；每组至少两行，只报告不删除。"""
        self._ensure_open()
        limit = self._validate_limit(limit)
        candidate_limit = limit * 2
        with self._lock:
            self._ensure_open()
            rows = self._db.execute(
                """SELECT asset_id, root_id, shot_index, start_sec, end_sec, content_hash
                   FROM mvp1_shots
                   WHERE content_hash IN (
                       SELECT content_hash FROM mvp1_shots
                       GROUP BY content_hash HAVING COUNT(*) > 1
                   )
                   ORDER BY content_hash ASC, asset_id ASC, shot_index ASC
                   LIMIT ?""",
                (candidate_limit,),
            ).fetchall()
        grouped: dict[str, list[ShotRecord]] = {}
        for row in rows:
            record = ShotRecord(
                asset_id=str(row["asset_id"]),
                root_id=str(row["root_id"]),
                shot_index=int(row["shot_index"]),
                start_sec=float(row["start_sec"]),
                end_sec=float(row["end_sec"]),
                content_hash=str(row["content_hash"]),
            )
            grouped.setdefault(str(row["content_hash"]), []).append(record)
        return [
            ShotDuplicateGroup(content_hash=content_hash, members=tuple(members))
            for content_hash, members in sorted(grouped.items())[:limit]
        ]

    def set_index_metadata(
        self,
        *,
        model_id: str | None,
        model_version: str | None,
        dimensions: int,
        quantization: str | None,
    ) -> None:
        """写入单行索引元数据；维度必须与当前索引配置一致。"""
        self._ensure_open()
        for name, value in (("model_id", model_id), ("model_version", model_version)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} 必须是 None 或非空字符串")
        if (
            isinstance(dimensions, bool)
            or not isinstance(dimensions, int)
            or dimensions < 1
        ):
            raise ValueError("dimensions 必须是正整数")
        if dimensions != self.dimensions:
            raise ValueError("dimensions 与当前索引配置的向量维度不一致")
        if quantization is not None and (
            not isinstance(quantization, str) or not quantization.strip()
        ):
            raise ValueError("quantization 必须是 None 或非空字符串")
        with self._lock:
            self._ensure_open()
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute("DELETE FROM mvp1_index_metadata WHERE id=1")
                self._db.execute(
                    """INSERT INTO mvp1_index_metadata
                       (id, model_id, model_version, dimensions, quantization)
                       VALUES(1,?,?,?,?)""",
                    (model_id, model_version, dimensions, quantization),
                )
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    def switch_embedding_model(
        self,
        *,
        model_id: str,
        model_version: str,
        dimensions: int,
        quantization: str = "float32",
    ) -> bool:
        """切换嵌入模型并清理旧向量，保留文档及 FTS5 内容。"""
        self._ensure_open()
        for name, value in (
            ("model_id", model_id),
            ("model_version", model_version),
            ("quantization", quantization),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} 必须是非空字符串")
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
            raise ValueError("dimensions 必须是正整数")

        with self._lock:
            self._ensure_open()
            current = self.index_metadata()
            unchanged = (
                current is not None
                and current.model_id == model_id
                and current.model_version == model_version
                and current.dimensions == dimensions
                and current.quantization
                == (quantization if self._vec_extension_loaded else None)
                and self.dimensions == dimensions
            )
            if unchanged and (self.vector_enabled or not self._vec_extension_loaded):
                return False

            stored_quantization = (
                quantization if self._vec_extension_loaded else None
            )
            self._db.execute("BEGIN IMMEDIATE")
            try:
                if self._vec_extension_loaded:
                    self._db.execute(f"DROP TABLE IF EXISTS {self._VECTORS}")
                    self._db.execute(
                        f"""CREATE VIRTUAL TABLE {self._VECTORS}
                            USING vec0(embedding float[{dimensions}])"""
                    )
                self._db.execute(
                    f"UPDATE {self._DOCUMENTS} SET vector_hash=NULL"  # noqa: S608
                )
                self._db.execute("DELETE FROM mvp1_index_metadata WHERE id=1")
                self._db.execute(
                    """INSERT INTO mvp1_index_metadata
                       (id, model_id, model_version, dimensions, quantization)
                       VALUES(1,?,?,?,?)""",
                    (model_id, model_version, dimensions, stored_quantization),
                )
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

            self.dimensions = dimensions
            self.vector_enabled = self._vec_extension_loaded
            self.vector_error = None if self.vector_enabled else self.vector_error
            return True

    def index_metadata(self) -> IndexMetadata | None:
        """读取索引元数据；尚未写入时返回 None。"""
        self._ensure_open()
        with self._lock:
            self._ensure_open()
            row = self._db.execute(
                """SELECT model_id, model_version, dimensions, quantization
                   FROM mvp1_index_metadata WHERE id=1"""
            ).fetchone()
        if row is None:
            return None
        return IndexMetadata(
            model_id=row["model_id"] if row["model_id"] is None else str(row["model_id"]),
            model_version=(
                row["model_version"]
                if row["model_version"] is None
                else str(row["model_version"])
            ),
            dimensions=int(row["dimensions"]),
            quantization=(
                row["quantization"]
                if row["quantization"] is None
                else str(row["quantization"])
            ),
        )

    def document_count(self) -> int:
        """返回文档表行数。"""
        self._ensure_open()
        with self._lock:
            self._ensure_open()
            row = self._db.execute("SELECT COUNT(*) FROM c18_documents").fetchone()
        return int(row[0])

    def shot_count(self) -> int:
        """返回镜头表行数。"""
        self._ensure_open()
        with self._lock:
            self._ensure_open()
            row = self._db.execute("SELECT COUNT(*) FROM mvp1_shots").fetchone()
        return int(row[0])

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
        vector_blob = _serialize_vector(query_vector, self.dimensions)
        if not self.vector_enabled:
            self._vector_search_state.degraded = True
            return []
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
            except sqlite3.Error as exc:
                if not self._vector_version_warning_emitted:
                    self._vector_version_warning_emitted = True
                    logger.warning("sqlite-vec version probe failed: %s", exc)
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
