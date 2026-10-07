"""Workspace-confined SQLite connection factory for development/test adapters.

This is deliberately not a production database selection. Each operation opens an
independent connection so separate processes exercise the same durable file.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from gw.agent_runtime.config import PathIsolationError, resolve_trusted_path, resolve_workspace_path, workspace_root


_DEFAULT_RELATIVE = Path(".local/runtime-dev/data/runtime.sqlite3")


class _ClosingConnection(sqlite3.Connection):
    """Connection context manager that also closes its file handle on exit."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class SQLiteDatabase:
    """SQLite file constrained to a non-symlink path beneath this checkout's .local."""

    def __init__(self, path: str | Path | None = None, *, trusted_root: str | Path | None = None) -> None:
        if trusted_root is None:
            root = workspace_root()
            candidate = Path(path) if path is not None else root / _DEFAULT_RELATIVE
            safe = resolve_workspace_path(candidate)
            local_root = resolve_workspace_path(".local")
            try:
                safe.relative_to(local_root)
            except ValueError as exc:
                raise PathIsolationError("Runtime SQLite data must remain below workspace .local.") from exc
        else:
            candidate = Path(path) if path is not None else Path("agent/runtime.sqlite3")
            safe = resolve_trusted_path(candidate, trusted_root=trusted_root)
        if safe.suffix not in {".sqlite3", ".sqlite", ".db"}:
            raise ValueError("SQLite database path must use .sqlite3, .sqlite, or .db.")
        if safe.exists() and not safe.is_file():
            raise PathIsolationError("Runtime SQLite database path must be a file.")
        self.path = safe
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 0),
                    run_json TEXT NOT NULL,
                    state_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_runs_project ON runs(project_id, run_id);
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    event_seq INTEGER NOT NULL CHECK(event_seq >= 1),
                    event_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, event_seq),
                    UNIQUE(run_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS leases (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
                    owner_id TEXT NOT NULL,
                    generation INTEGER NOT NULL CHECK(generation >= 1),
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS invocations (
                    invocation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    result_json TEXT,
                    UNIQUE(run_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    version_id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL,
                    run_id TEXT,
                    stage TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    artifact_json TEXT NOT NULL,
                    content TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_artifacts_run ON artifacts(run_id, stage);
                CREATE TABLE IF NOT EXISTS artifact_idempotency_fingerprints (
                    idempotency_key TEXT PRIMARY KEY REFERENCES artifacts(idempotency_key) ON DELETE CASCADE,
                    request_fingerprint TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    stage_attempt_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    attempt_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    artifact_version_id TEXT NOT NULL,
                    review_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    artifact_version_id TEXT NOT NULL,
                    approval_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    response_kind TEXT NOT NULL DEFAULT 'AgentRun',
                    PRIMARY KEY(run_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS create_keys (
                    project_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    PRIMARY KEY(project_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS invocation_operations (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    operation_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, operation_id)
                );
                CREATE TABLE IF NOT EXISTS retry_reservations (
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    reservation_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, reservation_id)
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_ref TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    lease_generation INTEGER NOT NULL,
                    checkpoint_json TEXT NOT NULL
                );
                """
            )

    def transaction(self) -> sqlite3.Connection:
        """Return a connection with an immediate write transaction begun."""
        connection = self.connect()
        connection.execute("BEGIN IMMEDIATE")
        return connection

    @staticmethod
    def close_rollback(connection: sqlite3.Connection) -> None:
        try:
            if connection.in_transaction:
                connection.rollback()
        finally:
            connection.close()

    @staticmethod
    def close_commit(connection: sqlite3.Connection) -> None:
        try:
            connection.commit()
        finally:
            connection.close()
