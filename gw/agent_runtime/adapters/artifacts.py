"""SQLite development artifact adapter; immutable bodies and exact version references."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from typing import Any

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    ArtifactLifecycle,
    ArtifactRef,
    ArtifactSourceRef,
    ArtifactVersionInput,
    ErrorCode,
    Identifier,
)
from gw.agent_runtime.ports import ArtifactRepository
from gw.agent_runtime.repository import utc_now
from gw.agent_runtime.storage.sqlite import SQLiteDatabase


class SQLiteArtifactRepository(ArtifactRepository):
    """Local artifact store that never writes content outside the configured SQLite file."""

    def __init__(self, database: SQLiteDatabase | str | None = None) -> None:
        self.database = database if isinstance(database, SQLiteDatabase) else SQLiteDatabase(database)

    async def create_version(self, version: ArtifactVersionInput) -> ArtifactRef:
        fingerprint = hashlib.sha256(version.content.encode("utf-8")).hexdigest()
        request_fingerprint = self._request_fingerprint(version)
        connection = self.database.transaction()
        try:
            existing = connection.execute(
                "SELECT artifact_json,content,run_id,stage FROM artifacts WHERE idempotency_key=?",
                (version.idempotency_key.root,),
            ).fetchone()
            if existing:
                artifact = ArtifactRef.model_validate_json(existing["artifact_json"])
                stored_fingerprint = connection.execute(
                    "SELECT request_fingerprint FROM artifact_idempotency_fingerprints WHERE idempotency_key=?",
                    (version.idempotency_key.root,),
                ).fetchone()
                if stored_fingerprint:
                    matches = stored_fingerprint["request_fingerprint"] == request_fingerprint
                else:
                    # Pre-R4 rows did not retain a request fingerprint. Compare all
                    # identity/provenance fields that the old table actually stored.
                    # Non-ownership metadata was never persisted by that schema.
                    metadata = version.metadata
                    expected_run_id = metadata.get("run_id")
                    if not isinstance(expected_run_id, str):
                        expected_run_id = None
                    expected_stage = metadata.get("runtime_stage")
                    if not isinstance(expected_stage, str):
                        expected_stage = None
                    matches = (
                        existing["content"] == version.content
                        and artifact.content_hash.root == fingerprint
                        and (version.artifact_id is None or artifact.artifact_id == version.artifact_id)
                        and artifact.parent_version_ids == ([version.parent_version_id] if version.parent_version_id else [])
                        and artifact.source_refs == version.source_refs
                        and existing["run_id"] == expected_run_id
                        and existing["stage"] == expected_stage
                    )
                    if matches:
                        connection.execute(
                            "INSERT INTO artifact_idempotency_fingerprints(idempotency_key,request_fingerprint) VALUES(?,?)",
                            (version.idempotency_key.root, request_fingerprint),
                        )
                if not matches:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "Artifact idempotency key conflicts with different identity or provenance.",
                        request_id=version.idempotency_key.root,
                    )
                self.database.close_commit(connection)
                return artifact
            artifact_id = version.artifact_id.root if version.artifact_id else uuid.uuid4().hex
            version_id = uuid.uuid4().hex
            revision = connection.execute("SELECT COALESCE(MAX(json_extract(artifact_json,'$.revision')),0) AS rev FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()["rev"] + 1
            now = utc_now()
            artifact = ArtifactRef(
                schema_version=1,
                artifact_id=artifact_id,
                version_id=version_id,
                revision=revision,
                content_hash=fingerprint,
                content_ref=f"sqlite-content:{version_id}",
                parent_version_ids=[version.parent_version_id] if version.parent_version_id else [],
                source_refs=version.source_refs,
                lifecycle=ArtifactLifecycle.valid,
                created_at=now,
            )
            metadata = dict(version.metadata)
            run_id = metadata.get("run_id")
            if not isinstance(run_id, str):
                run_id = None
            stage = metadata.get("runtime_stage")
            if not isinstance(stage, str):
                stage = None
            connection.execute(
                "INSERT INTO artifacts(version_id,artifact_id,run_id,stage,idempotency_key,artifact_json,content) VALUES(?,?,?,?,?,?,?)",
                (version_id, artifact_id, run_id, stage, version.idempotency_key.root, artifact.model_dump_json(), version.content),
            )
            connection.execute(
                "INSERT INTO artifact_idempotency_fingerprints(idempotency_key,request_fingerprint) VALUES(?,?)",
                (version.idempotency_key.root, request_fingerprint),
            )
            self.database.close_commit(connection)
            return artifact
        except Exception:
            self.database.close_rollback(connection)
            raise

    @staticmethod
    def _request_fingerprint(version: ArtifactVersionInput) -> str:
        identity = version.model_dump(mode="json", exclude={"idempotency_key"})
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def get_version(self, artifact_id: Identifier, version_id: Identifier) -> ArtifactRef | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT artifact_json FROM artifacts WHERE artifact_id=? AND version_id=?", (artifact_id.root, version_id.root)).fetchone()
        return ArtifactRef.model_validate_json(row["artifact_json"]) if row else None

    async def read_content(self, artifact_id: Identifier, version_id: Identifier) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT content FROM artifacts WHERE artifact_id=? AND version_id=?", (artifact_id.root, version_id.root)).fetchone()
        return row["content"] if row else None

    async def list_versions(self, artifact_id: Identifier, cursor: Identifier | None, limit: int) -> tuple[Sequence[ArtifactRef], Identifier | None]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.database.connect() as connection:
            after = 0
            if cursor:
                found = connection.execute(
                    "SELECT artifact_json FROM artifacts WHERE artifact_id=? AND version_id=?",
                    (artifact_id.root, cursor.root),
                ).fetchone()
                if not found:
                    raise DomainError(
                        ErrorCode.ARTIFACT_STALE,
                        "The artifact cursor is unknown for this artifact.",
                        request_id=cursor.root,
                    )
                after = ArtifactRef.model_validate_json(found["artifact_json"]).revision.root
            rows = connection.execute("SELECT artifact_json FROM artifacts WHERE artifact_id=? AND json_extract(artifact_json,'$.revision')>? ORDER BY json_extract(artifact_json,'$.revision') LIMIT ?", (artifact_id.root, after, limit + 1)).fetchall()
        more = len(rows) > limit
        selected = rows[:limit]
        items = [ArtifactRef.model_validate_json(row["artifact_json"]) for row in selected]
        return items, Identifier(items[-1].version_id.root) if more and items else None

    async def move_to_trash(self, artifact_id: Identifier, expected_revision: int) -> int:
        return await self._set_lifecycle(artifact_id, expected_revision, ArtifactLifecycle.trashed)

    async def restore(self, artifact_id: Identifier, expected_revision: int) -> int:
        return await self._set_lifecycle(artifact_id, expected_revision, ArtifactLifecycle.valid)

    async def _set_lifecycle(self, artifact_id: Identifier, expected_revision: int, lifecycle: ArtifactLifecycle) -> int:
        connection = self.database.transaction()
        try:
            rows = connection.execute("SELECT version_id,artifact_json FROM artifacts WHERE artifact_id=? ORDER BY rowid", (artifact_id.root,)).fetchall()
            if not rows:
                raise KeyError(artifact_id.root)
            revisions = [ArtifactRef.model_validate_json(row["artifact_json"]).revision.root for row in rows]
            actual = max(revisions)
            if actual != expected_revision:
                raise ValueError(f"Artifact revision conflict: expected {expected_revision}, found {actual}.")
            for row in rows:
                old = ArtifactRef.model_validate_json(row["artifact_json"])
                updated = old.model_copy(update={"lifecycle": lifecycle, "revision": old.revision.__class__(old.revision.root + 1)})
                connection.execute("UPDATE artifacts SET artifact_json=? WHERE version_id=?", (updated.model_dump_json(), row["version_id"]))
            result = actual + 1
            self.database.close_commit(connection)
            return result
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def get_content_by_ref(self, content_ref: Identifier) -> str | None:
        version_id = content_ref.root.removeprefix("sqlite-content:")
        with self.database.connect() as connection:
            row = connection.execute("SELECT content FROM artifacts WHERE version_id=?", (version_id,)).fetchone()
        return row["content"] if row else None

    async def list_run_artifacts(self, run_id: Identifier) -> list[ArtifactRef]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT artifact_json FROM artifacts WHERE run_id=? ORDER BY rowid", (run_id.root,)).fetchall()
        return [ArtifactRef.model_validate_json(row["artifact_json"]) for row in rows]

    async def get_downstream_artifacts(self, run_id: Identifier, stage_order: dict[str, int], from_stage: str) -> list[ArtifactRef]:
        threshold = stage_order[from_stage]
        with self.database.connect() as connection:
            rows = connection.execute("SELECT stage,artifact_json FROM artifacts WHERE run_id=? AND stage IS NOT NULL ORDER BY rowid", (run_id.root,)).fetchall()
        result: list[ArtifactRef] = []
        for row in rows:
            if row["stage"] in stage_order and stage_order[row["stage"]] >= threshold:
                ref = ArtifactRef.model_validate_json(row["artifact_json"])
                if ref.lifecycle is ArtifactLifecycle.valid:
                    result.append(ref)
        return result
