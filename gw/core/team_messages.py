# -*- coding: utf-8 -*-
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

"""团队治理状态与纯文本消息的 SQLite 持久化边界。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import hashlib
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from typing import Any, Iterable, Iterator, Mapping

from gw.core.errors import CleanroomException
from gw.core.storage import data_root

SCHEMA_VERSION = 1
DATABASE_FILENAME = "team_messages.sqlite3"
CLIENT_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z", re.ASCII)
WRITE_GLOBAL_ROLES = frozenset({"admin", "governor", "editor"})
GOVERNANCE_ROLES = frozenset({"admin", "governor"})
WRITE_TEAM_ROLES = frozenset({"admin", "governor", "editor"})
READ_TEAM_ROLES = frozenset({"admin", "governor", "editor", "reviewer", "readonly"})

_INIT_LOCK = threading.RLock()
_INITIALIZED_PATHS: set[str] = set()


class TeamMessageStorageUnavailable(CleanroomException):
    """团队库不可读、损坏、版本不兼容或写事务失败。"""

    def __init__(self) -> None:
        super().__init__(503, "TEAM_MESSAGE_STORE_UNAVAILABLE", "团队消息存储暂不可用，已拒绝读写")


class TeamMessageNotFound(CleanroomException):
    """团队不存在或当前主体无权获知该团队。"""

    def __init__(self) -> None:
        super().__init__(404, "TEAM_NOT_FOUND", "团队不存在或当前主体不可见")


class TeamMessageForbidden(CleanroomException):
    """认证主体无唯一身份绑定或缺少团队读写权限。"""

    def __init__(self) -> None:
        super().__init__(403, "TEAM_MESSAGE_FORBIDDEN", "当前主体没有团队消息读写权限")


class TeamMessageIdempotencyConflict(CleanroomException):
    """相同幂等键被用于不同消息文本。"""

    def __init__(self) -> None:
        super().__init__(409, "MESSAGE_IDEMPOTENCY_CONFLICT", "幂等键已用于不同消息文本")


class TeamMessageInvalidCursor(CleanroomException):
    """团队消息游标格式、范围或组合不正确。"""

    def __init__(self, message: str = "团队消息游标无效") -> None:
        super().__init__(400, "INVALID_MESSAGE_CURSOR", message)


class TeamMessageCursorOutOfRange(CleanroomException):
    """游标指向尚未分配的消息序号。"""

    def __init__(self) -> None:
        super().__init__(422, "MESSAGE_CURSOR_OUT_OF_RANGE", "消息游标超过当前团队已分配序号")


def team_message_database_path() -> Path:
    """返回固定文件名的团队消息库路径，不接受请求提供路径。"""
    return data_root() / DATABASE_FILENAME


def _schema_statements() -> tuple[str, ...]:
    return (
        """CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
        """CREATE TABLE governance_users (
            user_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL,
            version INTEGER NOT NULL CHECK(version >= 1),
            external_subject TEXT,
            identity_domain TEXT,
            owner_subject TEXT,
            owner_domain TEXT
        )""",
        """CREATE UNIQUE INDEX governance_identity_unique
            ON governance_users(identity_domain, external_subject)
            WHERE identity_domain IS NOT NULL AND external_subject IS NOT NULL""",
        """CREATE TABLE teams (
            team_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            version INTEGER NOT NULL CHECK(version >= 1),
            owner_user_id TEXT
        )""",
        """CREATE TABLE team_members (
            team_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            PRIMARY KEY(team_id, user_id)
        )""",
        """CREATE TABLE messages (
            message_id TEXT PRIMARY KEY,
            team_id TEXT NOT NULL,
            author_user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK(sequence >= 1),
            UNIQUE(team_id, sequence)
        )""",
        "CREATE INDEX messages_team_sequence ON messages(team_id, sequence)",
        """CREATE TABLE idempotency_records (
            team_id TEXT NOT NULL,
            author_user_id TEXT NOT NULL,
            client_request_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            message_id TEXT NOT NULL,
            PRIMARY KEY(team_id, author_user_id, client_request_id)
        )""",
    )


_EXPECTED_COLUMNS = {
    "metadata": {"key", "value"},
    "governance_users": {"user_id", "display_name", "role", "version", "external_subject", "identity_domain", "owner_subject", "owner_domain"},
    "teams": {"team_id", "name", "description", "version", "owner_user_id"},
    "team_members": {"team_id", "user_id", "role"},
    "messages": {"message_id", "team_id", "author_user_id", "text", "created_at", "sequence"},
    "idempotency_records": {"team_id", "author_user_id", "client_request_id", "request_hash", "message_id"},
}


class TeamMessageRepository:
    """团队/成员/身份引用与消息共享事务的单实例 SQLite 仓库。"""

    def database_path(self) -> Path:
        return team_message_database_path()

    @staticmethod
    def _unavailable() -> TeamMessageStorageUnavailable:
        return TeamMessageStorageUnavailable()

    def _initialize_path(self, path: Path) -> None:
        key = str(path.resolve())
        if key in _INITIALIZED_PATHS:
            return
        with _INIT_LOCK:
            if key in _INITIALIZED_PATHS:
                return
            existed = path.exists()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(path, timeout=5, isolation_level=None)
                try:
                    connection.row_factory = sqlite3.Row
                    connection.execute("PRAGMA busy_timeout=5000")
                    connection.execute("PRAGMA foreign_keys=ON")
                    connection.execute("PRAGMA synchronous=FULL")
                    if existed:
                        check = connection.execute("PRAGMA quick_check").fetchone()
                        if not check or check[0] != "ok":
                            raise self._unavailable()
                        version = connection.execute("PRAGMA user_version").fetchone()[0]
                        if version != SCHEMA_VERSION:
                            raise self._unavailable()
                        meta_exists = connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
                        ).fetchone()
                        if not meta_exists:
                            raise self._unavailable()
                        stored_version = connection.execute(
                            "SELECT value FROM metadata WHERE key='schema_version'"
                        ).fetchone()
                        required = connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                        table_names = {row[0] for row in required}
                        if not stored_version or stored_version[0] != str(SCHEMA_VERSION) or not set(_EXPECTED_COLUMNS) <= table_names:
                            raise self._unavailable()
                        for table, expected_columns in _EXPECTED_COLUMNS.items():
                            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                            if not expected_columns <= columns:
                                raise self._unavailable()
                        bootstrap = connection.execute(
                            "SELECT value FROM metadata WHERE key='bootstrap_complete'"
                        ).fetchone()
                        if not bootstrap or bootstrap[0] not in {"0", "1"}:
                            raise self._unavailable()
                        users = connection.execute("SELECT 1 FROM governance_users LIMIT 1").fetchone()
                        if users and bootstrap[0] != "1":
                            # 有治理身份即视为初始化已经完成，缺失标记不能重开 bootstrap。
                            connection.execute("BEGIN IMMEDIATE")
                            connection.execute(
                                "UPDATE metadata SET value='1' WHERE key='bootstrap_complete'"
                            )
                            connection.commit()
                    else:
                        connection.execute("BEGIN IMMEDIATE")
                        for statement in _schema_statements():
                            connection.execute(statement)
                        connection.execute("INSERT INTO metadata(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
                        connection.execute("INSERT INTO metadata(key, value) VALUES('bootstrap_complete', '0')")
                        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                        connection.commit()
                finally:
                    connection.close()
            except TeamMessageStorageUnavailable:
                raise
            except (OSError, sqlite3.Error, RuntimeError, ValueError):
                raise self._unavailable()
            _INITIALIZED_PATHS.add(key)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        path = self.database_path()
        self._initialize_path(path)
        try:
            connection = sqlite3.connect(path, timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
        except (OSError, sqlite3.Error, RuntimeError, ValueError):
            raise self._unavailable()
        try:
            yield connection
        except CleanroomException:
            if connection.in_transaction:
                connection.rollback()
            raise
        except (OSError, sqlite3.Error, RuntimeError, ValueError):
            if connection.in_transaction:
                connection.rollback()
            raise self._unavailable()
        finally:
            connection.close()

    @staticmethod
    def _read_state(connection: sqlite3.Connection) -> dict[str, Any]:
        users = [dict(row) for row in connection.execute("SELECT * FROM governance_users ORDER BY user_id")]
        teams = [dict(row) for row in connection.execute("SELECT * FROM teams ORDER BY team_id")]
        members: dict[str, dict[str, str]] = {}
        for row in connection.execute("SELECT team_id, user_id, role FROM team_members ORDER BY team_id, user_id"):
            members.setdefault(row["team_id"], {})[row["user_id"]] = row["role"]
        marker = connection.execute("SELECT value FROM metadata WHERE key='bootstrap_complete'").fetchone()
        if not marker or marker[0] not in {"0", "1"}:
            raise TeamMessageStorageUnavailable()
        if users and marker[0] != "1":
            raise TeamMessageStorageUnavailable()
        return {"users": users, "teams": teams, "members": members, "bootstrapped": marker[0] == "1"}

    def load_governance_state(self) -> dict[str, Any]:
        with self._connection() as connection:
            try:
                state = self._read_state(connection)
            except TeamMessageStorageUnavailable:
                raise
            except sqlite3.Error:
                raise self._unavailable()
            return state

    def save_governance_state(self, users: Iterable[Mapping[str, Any]], teams: Iterable[Mapping[str, Any]], bootstrapped: bool) -> None:
        """在单个 SQLite 事务中替换治理身份、团队和成员快照。"""
        user_rows = list(users)
        team_rows = list(teams)
        marker = bool(bootstrapped or user_rows)
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                old_marker = connection.execute("SELECT value FROM metadata WHERE key='bootstrap_complete'").fetchone()
                if not old_marker or old_marker[0] not in {"0", "1"}:
                    raise self._unavailable()
                connection.execute("DELETE FROM team_members")
                connection.execute("DELETE FROM teams")
                connection.execute("DELETE FROM governance_users")
                connection.executemany(
                    """INSERT INTO governance_users
                       (user_id, display_name, role, version, external_subject, identity_domain, owner_subject, owner_domain)
                       VALUES (:user_id, :display_name, :role, :version, :external_subject, :identity_domain, :owner_subject, :owner_domain)""",
                    user_rows,
                )
                connection.executemany(
                    """INSERT INTO teams(team_id, name, description, version, owner_user_id)
                       VALUES (:team_id, :name, :description, :version, :owner_user_id)""",
                    team_rows,
                )
                connection.executemany(
                    "INSERT INTO team_members(team_id, user_id, role) VALUES (?, ?, ?)",
                    [(team["team_id"], user_id, role) for team in team_rows for user_id, role in team["members"].items()],
                )
                if marker:
                    connection.execute("UPDATE metadata SET value='1' WHERE key='bootstrap_complete'")
                connection.commit()
            except CleanroomException:
                if connection.in_transaction:
                    connection.rollback()
                raise
            except (sqlite3.Error, OSError, RuntimeError, ValueError, KeyError):
                if connection.in_transaction:
                    connection.rollback()
                raise self._unavailable()

    def reset_for_tests(self) -> None:
        """仅 pytest 显式重置当前测试数据目录，绝不用于生产启动。"""
        if not os.getenv("PYTEST_CURRENT_TEST"):
            raise RuntimeError("测试专用重置只能由 pytest 调用")
        path = self.database_path()
        resolved = path.resolve()
        data_override = os.environ.get("GW_DATA_DIR", "").strip()
        if not data_override or not Path(data_override).expanduser().is_absolute():
            raise RuntimeError("测试重置必须显式使用隔离的 GW_DATA_DIR")
        for candidate in (resolved, Path(str(resolved) + "-wal"), Path(str(resolved) + "-shm")):
            try:
                candidate.unlink(missing_ok=True)
            except OSError as exc:
                raise self._unavailable() from exc
        _INITIALIZED_PATHS.discard(str(resolved))

    @staticmethod
    def _resolve_actor(connection: sqlite3.Connection, identity_domain: str | None, identity_subject: str | None) -> sqlite3.Row:
        if not identity_domain or not identity_subject:
            raise TeamMessageForbidden()
        rows = connection.execute(
            "SELECT user_id, role FROM governance_users WHERE identity_domain=? AND external_subject=?",
            (identity_domain, identity_subject),
        ).fetchall()
        if len(rows) != 1:
            raise TeamMessageForbidden()
        return rows[0]

    @classmethod
    def _authorize_team(
        cls,
        connection: sqlite3.Connection,
        team_id: str,
        identity_domain: str | None,
        identity_subject: str | None,
        global_role: str,
        *,
        write: bool,
    ) -> tuple[str, str | None]:
        actor = cls._resolve_actor(connection, identity_domain, identity_subject)
        team = connection.execute(
            "SELECT owner_user_id FROM teams WHERE team_id=?", (team_id,)
        ).fetchone()
        if not team:
            raise TeamMessageNotFound()
        member = connection.execute(
            "SELECT role FROM team_members WHERE team_id=? AND user_id=?",
            (team_id, actor["user_id"]),
        ).fetchone()
        member_role = member["role"] if member else None
        global_role = str(global_role or "").strip().lower()
        governance_override = global_role in GOVERNANCE_ROLES
        readable = governance_override or team["owner_user_id"] == actor["user_id"] or member_role in READ_TEAM_ROLES
        if not readable:
            raise TeamMessageNotFound()
        if write:
            if global_role not in WRITE_GLOBAL_ROLES:
                raise TeamMessageForbidden()
            if not governance_override and member_role not in WRITE_TEAM_ROLES:
                raise TeamMessageForbidden()
        return actor["user_id"], member_role

    @staticmethod
    def encode_cursor(team_id: str, sequence: int) -> str:
        raw = f"{team_id}:{sequence}".encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def decode_cursor(cursor: str, team_id: str) -> int:
        try:
            raw = base64.urlsafe_b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4)).decode("utf-8")
            scoped_team, raw_sequence = raw.rsplit(":", 1)
            sequence = int(raw_sequence)
        except (ValueError, UnicodeError, TypeError, base64.binascii.Error):
            raise TeamMessageInvalidCursor()
        if scoped_team != team_id or sequence < 0:
            raise TeamMessageInvalidCursor()
        return sequence

    def list_messages(
        self,
        team_id: str,
        *,
        identity_domain: str | None,
        identity_subject: str | None,
        global_role: str,
        limit: int,
        after_sequence: int | None,
        cursor: str | None,
    ) -> dict[str, Any]:
        if cursor is not None and after_sequence is not None:
            raise TeamMessageInvalidCursor("cursor 与 after_sequence 不得同时提供")
        offset = self.decode_cursor(cursor, team_id) if cursor is not None else int(after_sequence or 0)
        if offset < 0:
            raise TeamMessageInvalidCursor()
        with self._connection() as connection:
            try:
                connection.execute("BEGIN")
                _, member_role = self._authorize_team(
                    connection, team_id, identity_domain, identity_subject, global_role, write=False
                )
                role = str(global_role or "").strip().lower()
                can_send = role in WRITE_GLOBAL_ROLES and (role in GOVERNANCE_ROLES or member_role in WRITE_TEAM_ROLES)
                maximum = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM messages WHERE team_id=?", (team_id,)
                ).fetchone()[0]
                if offset > maximum:
                    raise TeamMessageCursorOutOfRange()
                rows = connection.execute(
                    """SELECT message_id, team_id, author_user_id, text, created_at, sequence
                       FROM messages WHERE team_id=? AND sequence>? ORDER BY sequence ASC LIMIT ?""",
                    (team_id, offset, limit),
                ).fetchall()
                messages = [dict(row) for row in rows]
                last_sequence = messages[-1]["sequence"] if messages else offset
                next_cursor = self.encode_cursor(team_id, last_sequence) if len(messages) == limit and messages else None
                connection.commit()
                return {
                    "team_id": team_id,
                    "messages": messages,
                    "can_send": can_send,
                    "next_cursor": next_cursor,
                    "next_after_sequence": last_sequence,
                }
            except CleanroomException:
                if connection.in_transaction:
                    connection.rollback()
                raise
            except sqlite3.Error:
                if connection.in_transaction:
                    connection.rollback()
                raise self._unavailable()

    def create_message(
        self,
        team_id: str,
        *,
        identity_domain: str | None,
        identity_subject: str | None,
        global_role: str,
        text: str,
        client_request_id: str,
    ) -> tuple[dict[str, Any], bool]:
        if not isinstance(text, str) or not (1 <= len(text) <= 2000) or not text.strip():
            raise CleanroomException(422, "INVALID_MESSAGE_TEXT", "消息必须为1至2000个字符的非空白纯文本")
        if not isinstance(client_request_id, str) or not CLIENT_REQUEST_ID.fullmatch(client_request_id):
            raise CleanroomException(422, "INVALID_CLIENT_REQUEST_ID", "client_request_id 格式不合法")
        request_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                # 权限校验必须先于幂等记录查找，撤权后不得靠旧键重放读取消息。
                author_user_id, _ = self._authorize_team(
                    connection, team_id, identity_domain, identity_subject, global_role, write=True
                )
                prior = connection.execute(
                    """SELECT request_hash, message_id FROM idempotency_records
                       WHERE team_id=? AND author_user_id=? AND client_request_id=?""",
                    (team_id, author_user_id, client_request_id),
                ).fetchone()
                if prior:
                    if prior["request_hash"] != request_hash:
                        raise TeamMessageIdempotencyConflict()
                    message = connection.execute(
                        """SELECT message_id, team_id, author_user_id, text, created_at, sequence
                           FROM messages WHERE message_id=?""",
                        (prior["message_id"],),
                    ).fetchone()
                    if not message:
                        raise self._unavailable()
                    connection.commit()
                    return {**dict(message), "replayed": True}, True
                sequence = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM messages WHERE team_id=?", (team_id,)
                ).fetchone()[0]
                message_id = f"msg-{team_id}-{sequence}"
                created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                connection.execute(
                    """INSERT INTO messages(message_id, team_id, author_user_id, text, created_at, sequence)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (message_id, team_id, author_user_id, text, created_at, sequence),
                )
                connection.execute(
                    """INSERT INTO idempotency_records(team_id, author_user_id, client_request_id, request_hash, message_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    (team_id, author_user_id, client_request_id, request_hash, message_id),
                )
                connection.commit()
                return {
                    "message_id": message_id,
                    "team_id": team_id,
                    "author_user_id": author_user_id,
                    "text": text,
                    "created_at": created_at,
                    "sequence": sequence,
                    "replayed": False,
                }, False
            except CleanroomException:
                if connection.in_transaction:
                    connection.rollback()
                raise
            except (sqlite3.Error, OSError, RuntimeError, ValueError):
                if connection.in_transaction:
                    connection.rollback()
                raise self._unavailable()
