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

"""参考策展会话模型与读取路径（MVP2-T1a）。

参考集以持久台账为真源：会话绑定项目并持有单调版本号，条目按 ``order``
升序回读；用同一 SQLite 文件重建实例即可完整恢复（模拟重启）。本模块只提供
读语义与夹具所需的会话登记，写路径（选中/拒绝/备注/排序）由 MVP2-T1b 承接。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode
from gw.agent_runtime.repository import utc_now
from gw.agent_runtime.storage.sqlite import SQLiteDatabase

#: 会话登记时的初始版本号；写路径每成功变更一次递增，冲突走 409。
INITIAL_VERSION = 1

#: 备注长度上限；与前端输入框限制一致，超长拒绝而不是截断。
NOTE_MAX_LENGTH = 500

_IDENTIFIER_MAX_LENGTH = 256


class ReferenceDecision(StrEnum):
    """用户对单条参考的策展结论。"""

    pending = "pending"
    selected = "selected"
    rejected = "rejected"


class ReferenceItem(BaseModel):
    """会话内的一条参考策展条目（按 ``asset_id`` 唯一）。"""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1, max_length=_IDENTIFIER_MAX_LENGTH)
    decision: ReferenceDecision = ReferenceDecision.pending
    note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)
    order: int = Field(ge=0)
    updated_at: datetime


class ReferenceSet(BaseModel):
    """一个会话的完整参考集；``version`` 用于写路径的乐观并发控制。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=_IDENTIFIER_MAX_LENGTH)
    project_id: str = Field(min_length=1, max_length=_IDENTIFIER_MAX_LENGTH)
    items: list[ReferenceItem] = Field(default_factory=list)
    version: int = Field(ge=INITIAL_VERSION)


def _item_from_row(row: sqlite3.Row) -> ReferenceItem:
    note = row["note"]
    return ReferenceItem(
        asset_id=row["asset_id"],
        decision=ReferenceDecision(row["decision"]),
        note=note if isinstance(note, str) else None,
        order=row["order_index"],
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class ReferenceStore:
    """``ReferenceSet`` 的 SQLite 持久台账。

    复用 ``SQLiteDatabase`` 的连接工厂与路径隔离；表结构在本模块内
    ``CREATE TABLE IF NOT EXISTS``，不改动共享的 sqlite.py 建表脚本。
    同一文件重建实例即可恢复会话绑定、条目与版本号（模拟重启）。
    """

    def __init__(self, database: SQLiteDatabase | str | None = None) -> None:
        self.database = (
            database if isinstance(database, SQLiteDatabase) else SQLiteDatabase(database)
        )
        self._initialize()

    def _initialize(self) -> None:
        with self.database.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_reference_sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_reference_items (
                    session_id TEXT NOT NULL
                        REFERENCES agent_reference_sessions(session_id) ON DELETE CASCADE,
                    asset_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    note TEXT,
                    order_index INTEGER NOT NULL CHECK(order_index >= 0),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, asset_id)
                );
                CREATE INDEX IF NOT EXISTS ix_agent_reference_items_order
                    ON agent_reference_items(session_id, order_index, asset_id);
                """
            )

    def create_session(self, *, session_id: str, project_id: str) -> ReferenceSet:
        """登记会话并把项目上下文固定下来；重复 ``session_id`` 拒绝。

        会话登记是读路径与写路径共用的前置事实：读路径据此区分「空参考集」
        与「会话不存在」，写路径据此校验 ``project_id`` 是否与会话绑定一致。
        """
        if not session_id or not project_id:
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "Reference session and project identifiers must not be empty.",
                request_id=session_id or "reference-session",
            )
        now = utc_now().isoformat()
        connection = self.database.transaction()
        try:
            connection.execute(
                "INSERT INTO agent_reference_sessions"
                "(session_id, project_id, version, created_at, updated_at)"
                " VALUES(?, ?, ?, ?, ?)",
                (session_id, project_id, INITIAL_VERSION, now, now),
            )
            self.database.close_commit(connection)
        except sqlite3.IntegrityError as exc:
            self.database.close_rollback(connection)
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "A reference session with this id already exists.",
                request_id=session_id,
            ) from exc
        except Exception:
            self.database.close_rollback(connection)
            raise
        return ReferenceSet(
            session_id=session_id,
            project_id=project_id,
            items=[],
            version=INITIAL_VERSION,
        )

    def get(self, session_id: str) -> ReferenceSet | None:
        """读取会话参考集；会话不存在返回 ``None``，由调用方映射为 404。"""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT session_id, project_id, version"
                " FROM agent_reference_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            item_rows = connection.execute(
                "SELECT asset_id, decision, note, order_index, updated_at"
                " FROM agent_reference_items WHERE session_id=?"
                " ORDER BY order_index, asset_id",
                (session_id,),
            ).fetchall()
        return ReferenceSet(
            session_id=row["session_id"],
            project_id=row["project_id"],
            items=[_item_from_row(item) for item in item_rows],
            version=row["version"],
        )


__all__ = [
    "INITIAL_VERSION",
    "NOTE_MAX_LENGTH",
    "ReferenceDecision",
    "ReferenceItem",
    "ReferenceSet",
    "ReferenceStore",
]
