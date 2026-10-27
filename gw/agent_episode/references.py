"""MVP2-T1 参考策展会话模型与 SQLite 持久化仓储。"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from gw.agent_runtime.repository import utc_now
from gw.agent_runtime.storage.sqlite import SQLiteDatabase
from gw.core.errors import CleanroomException


class ReferenceDecision(StrEnum):
    pending = "pending"
    selected = "selected"
    rejected = "rejected"


class ReferenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(..., min_length=1, description="素材标识，非空")
    decision: ReferenceDecision = ReferenceDecision.pending
    note: str | None = Field(default=None, max_length=500, description="备注，≤500 字")
    order: int = Field(..., ge=0, description="排序，≥0")
    updated_at: datetime


class ReferenceSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    items: list[ReferenceItem]
    version: int = Field(..., ge=0)


class ReferenceItemUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1)
    decision: ReferenceDecision
    note: str | None = Field(default=None, max_length=500)
    order: int = Field(..., ge=0)
    expected_version: int = Field(..., ge=0)


class SQLiteReferenceRepository:
    """参考策展会话 SQLite 持久化仓储。"""

    def __init__(self, database: SQLiteDatabase | str | Path | None = None) -> None:
        self.database = (
            database
            if isinstance(database, SQLiteDatabase)
            else SQLiteDatabase(database)
        )
        self._initialize()

    def _initialize(self) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reference_sets (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 0),
                    items_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_reference_sets_project ON reference_sets(project_id);"
            )

    def get_reference_set(self, session_id: str) -> ReferenceSet | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT session_id, project_id, version, items_json FROM reference_sets WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        items_raw = json.loads(row["items_json"])
        items = [ReferenceItem.model_validate(item) for item in items_raw]
        return ReferenceSet(
            session_id=row["session_id"],
            project_id=row["project_id"],
            items=items,
            version=row["version"],
        )

    def put_reference_item(
        self,
        session_id: str,
        asset_id: str,
        project_id: str,
        decision: ReferenceDecision,
        note: str | None,
        order: int,
        expected_version: int,
    ) -> ReferenceSet:
        connection = self.database.transaction()
        try:
            row = connection.execute(
                "SELECT project_id, version, items_json FROM reference_sets WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            now = utc_now()
            now_iso = now.isoformat()

            if row is None:
                if expected_version != 0:
                    raise CleanroomException(
                        409,
                        "VERSION_CONFLICT",
                        f"Version conflict: expected {expected_version}, but session does not exist (version 0)",
                        extra={
                            "expected_version": expected_version,
                            "current_version": 0,
                        },
                    )
                item = ReferenceItem(
                    asset_id=asset_id,
                    decision=decision,
                    note=note,
                    order=order,
                    updated_at=now,
                )
                items = [item]
                version = 1
                items_json = json.dumps([i.model_dump(mode="json") for i in items])
                connection.execute(
                    "INSERT INTO reference_sets(session_id, project_id, version, items_json, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    (session_id, project_id, version, items_json, now_iso, now_iso),
                )
                self.database.close_commit(connection)
                return ReferenceSet(
                    session_id=session_id,
                    project_id=project_id,
                    items=items,
                    version=version,
                )

            current_project_id = row["project_id"]
            if current_project_id != project_id:
                raise CleanroomException(
                    409,
                    "PROJECT_MISMATCH",
                    f"Session project mismatch: bound to '{current_project_id}', request got '{project_id}'",
                )

            current_version = row["version"]
            if current_version != expected_version:
                raise CleanroomException(
                    409,
                    "VERSION_CONFLICT",
                    f"Version conflict: expected {expected_version}, current is {current_version}",
                    extra={
                        "expected_version": expected_version,
                        "current_version": current_version,
                    },
                )

            existing_items = [
                ReferenceItem.model_validate(i) for i in json.loads(row["items_json"])
            ]
            match_idx = None
            for idx, item in enumerate(existing_items):
                if item.asset_id == asset_id:
                    match_idx = idx
                    break

            if match_idx is not None:
                existing = existing_items[match_idx]
                if (
                    existing.decision == decision
                    and existing.note == note
                    and existing.order == order
                ):
                    self.database.close_commit(connection)
                    return ReferenceSet(
                        session_id=session_id,
                        project_id=project_id,
                        items=existing_items,
                        version=current_version,
                    )
                existing_items[match_idx] = ReferenceItem(
                    asset_id=asset_id,
                    decision=decision,
                    note=note,
                    order=order,
                    updated_at=now,
                )
            else:
                existing_items.append(
                    ReferenceItem(
                        asset_id=asset_id,
                        decision=decision,
                        note=note,
                        order=order,
                        updated_at=now,
                    )
                )

            new_version = current_version + 1
            items_json = json.dumps([i.model_dump(mode="json") for i in existing_items])
            connection.execute(
                "UPDATE reference_sets SET version = ?, items_json = ?, updated_at = ? WHERE session_id = ? AND version = ?",
                (new_version, items_json, now_iso, session_id, current_version),
            )
            self.database.close_commit(connection)
            return ReferenceSet(
                session_id=session_id,
                project_id=project_id,
                items=existing_items,
                version=new_version,
            )
        except Exception:
            self.database.close_rollback(connection)
            raise
