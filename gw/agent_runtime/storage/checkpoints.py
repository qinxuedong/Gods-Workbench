"""SQLite implementation of the LangGraph checkpoint seam, separate from run truth."""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from gw.agent_runtime.models import Identifier, LeaseGrant
from gw.agent_runtime.ports import CheckpointBackend
from gw.agent_runtime.repository import _lease_valid
from gw.agent_runtime.storage.sqlite import SQLiteDatabase


class SQLiteCheckpointBackend(CheckpointBackend):
    """Persist graph resumptions locally; never treats checkpoint data as business approval."""

    def __init__(self, database: SQLiteDatabase | str | None = None) -> None:
        self.database = database if isinstance(database, SQLiteDatabase) else SQLiteDatabase(database)

    async def save(self, run_id: Identifier, checkpoint: Mapping[str, Any], *, lease: LeaseGrant) -> Identifier:
        if lease.run_id.root != run_id.root:
            raise ValueError("Checkpoint lease must be for the checkpoint run.")
        encoded = json.dumps(dict(checkpoint), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        checkpoint_ref = Identifier(uuid.uuid4().hex)
        connection = self.database.transaction()
        try:
            if not _lease_valid(connection, lease):
                raise ValueError("Cannot checkpoint with an expired or fenced lease.")
            connection.execute(
                "INSERT INTO checkpoints(checkpoint_ref,run_id,lease_generation,checkpoint_json) VALUES(?,?,?,?)",
                (checkpoint_ref.root, run_id.root, lease.generation, encoded),
            )
            self.database.close_commit(connection)
            return checkpoint_ref
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def load(self, checkpoint_ref: Identifier) -> Mapping[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT checkpoint_json FROM checkpoints WHERE checkpoint_ref=?", (checkpoint_ref.root,)).fetchone()
        return json.loads(row["checkpoint_json"]) if row else None

    async def latest_for_run(self, run_id: Identifier) -> tuple[Identifier, Mapping[str, Any]] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT checkpoint_ref,checkpoint_json FROM checkpoints WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (run_id.root,)).fetchone()
        if row is None:
            return None
        return Identifier(row["checkpoint_ref"]), json.loads(row["checkpoint_json"])
