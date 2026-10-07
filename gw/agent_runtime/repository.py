"""SQLite development implementation of the C05 run/event/lease ports."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    AgentRun,
    ApprovalRecord,
    ConfigSnapshot,
    ArtifactLifecycle,
    ArtifactRef,
    CreateRunRequest,
    DispatchState,
    ErrorCode,
    EventPage,
    Identifier,
    InvocationRecord,
    LeaseGrant,
    ModelInvocationResult,
    ReviewRecord,
    RunEvent,
    RunStatus,
    StageAttempt,
    UtcDateTime,
    UsageUnavailableReason,
)
from gw.agent_runtime.ports import ArtifactRepository, RunRepository
from gw.agent_runtime.storage.sqlite import SQLiteDatabase


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json()
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if hasattr(item, "model_dump"):
            return normalize(item.model_dump(mode="json"))
        if isinstance(item, Mapping):
            return {str(key): normalize(entry) for key, entry in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(entry) for entry in item]
        return item

    raw = json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _run_from_json(text: str) -> AgentRun:
    return AgentRun.model_validate_json(text)


def _event_from_json(text: str) -> RunEvent:
    return RunEvent.model_validate_json(text)


def _lease_valid(connection: sqlite3.Connection, lease: LeaseGrant) -> bool:
    row = connection.execute(
        "SELECT owner_id, generation, expires_at FROM leases WHERE run_id = ?",
        (lease.run_id.root,),
    ).fetchone()
    if row is None:
        return False
    expiration = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    return (
        row["owner_id"] == lease.owner_id.root
        and row["generation"] == lease.generation
        and expiration == lease.expires_at.root.astimezone(timezone.utc)
        and expiration > utc_now()
    )


class RuntimeRunRepository(RunRepository, Protocol):
    """Runtime persistence extension; production implementations remain port-injected."""

    async def create_run(self, request: CreateRunRequest, *, config_snapshot: ConfigSnapshot | None = None) -> AgentRun: ...
    async def read_run_by_id(self, run_id: Identifier) -> AgentRun | None: ...
    async def read_runtime_state(self, run_id: Identifier) -> dict[str, Any] | None: ...
    async def commit_transition(
        self, run_id: Identifier, expected_revision: int, next_run: AgentRun,
        events: Sequence[RunEvent], counter_changes: Mapping[str, int], *, lease: LeaseGrant,
        idempotency_key: Identifier | None = None, request_fingerprint: str | None = None,
        runtime_state: Mapping[str, Any] | None = None, attempts: Sequence[StageAttempt] = (),
        reviews: Sequence[ReviewRecord] = (), approvals: Sequence[ApprovalRecord] = (),
        stale_version_ids: Sequence[Identifier] = (),
        idempotency_response: AgentRun | ApprovalRecord | None = None,
    ) -> AgentRun | ApprovalRecord: ...
    async def read_idempotent_result(self, run_id: Identifier, idempotency_key: Identifier, request_fingerprint: str) -> AgentRun | ApprovalRecord | None: ...
    async def get_attempts(self, run_id: Identifier) -> list[StageAttempt]: ...
    async def get_reviews(self, run_id: Identifier) -> list[ReviewRecord]: ...
    async def get_approval_for_artifact(self, run_id: Identifier, review_id: Identifier, artifact_version_id: Identifier) -> ApprovalRecord | None: ...
    async def latest_review_for_artifact(self, run_id: Identifier, artifact_version_id: Identifier) -> ReviewRecord | None: ...
    async def is_approved(self, run_id: Identifier, review_id: Identifier, artifact_version_id: Identifier) -> bool: ...
    async def incomplete_invocations(self, run_id: Identifier) -> list[InvocationRecord]: ...
    async def begin_invocation(self, record: InvocationRecord) -> tuple[InvocationRecord, bool, ModelInvocationResult | None]: ...
    async def record_invocation_result(self, invocation_id: Identifier, result: ModelInvocationResult) -> None: ...
    async def read_invocation(self, run_id: Identifier, idempotency_key: Identifier) -> InvocationRecord | None: ...
    async def read_invocation_operation_result(self, run_id: Identifier, operation_id: Identifier) -> ModelInvocationResult | None: ...
    async def record_invocation_operation_result(self, run_id: Identifier, operation_id: Identifier, invocation_id: Identifier, result: ModelInvocationResult) -> None: ...
    async def mark_invocation_unknown(self, invocation_id: Identifier, reason: str) -> InvocationRecord: ...
    async def mark_invocation_not_sent(self, invocation_id: Identifier, reason: str) -> InvocationRecord: ...
    async def reserve_retry(self, run_id: Identifier, lease: LeaseGrant, operation_id: Identifier, reservation_id: Identifier, *, kind: str = "technical", limit: int = 2) -> AgentRun: ...


class RuntimeArtifactRepository(ArtifactRepository, Protocol):
    """Artifact query extension needed to invalidate downstream versions atomically."""

    async def get_downstream_artifacts(self, run_id: Identifier, stage_order: dict[str, int], from_stage: str) -> list[ArtifactRef]: ...
    async def list_run_artifacts(self, run_id: Identifier) -> list[ArtifactRef]: ...


class SQLiteRunRepository(RunRepository):
    """CAS transitions and generation+expiry fenced leases in a local SQLite file.

    Additional records are accepted on ``commit_transition`` to keep review,
    attempts, event references, rollback invalidation, and run state in one
    business transaction. This remains a development/test adapter only.
    """

    def __init__(self, database: SQLiteDatabase | str | None = None) -> None:
        self.database = database if isinstance(database, SQLiteDatabase) else SQLiteDatabase(database)

    async def create_run(self, request: CreateRunRequest, *, config_snapshot: ConfigSnapshot | None = None) -> AgentRun:
        fingerprint = _fingerprint({"request": request, "config_snapshot": config_snapshot.model_dump(mode="json") if config_snapshot else None})
        if config_snapshot is not None:
            if config_snapshot.config_snapshot_id.root != request.config_snapshot_id.root or config_snapshot.mode is not request.mode:
                raise DomainError(ErrorCode.VALIDATION_FAILED, "The resolved configuration snapshot does not match the run request.", request_id=request.request_id.root)
        connection = self.database.transaction()
        try:
            key = connection.execute(
                "SELECT request_fingerprint, run_id FROM create_keys WHERE project_id=? AND idempotency_key=?",
                (request.project_id.root, request.idempotency_key.root),
            ).fetchone()
            if key:
                if key["request_fingerprint"] != fingerprint:
                    raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "The create idempotency key was reused for a different request.", request_id=request.request_id.root)
                existing = connection.execute("SELECT run_json FROM runs WHERE run_id=?", (key["run_id"],)).fetchone()
                if existing is None:
                    raise RuntimeError("Create idempotency record points to a missing run.")
                self.database.close_commit(connection)
                return _run_from_json(existing["run_json"])

            now = utc_now()
            run = AgentRun(
                schema_version=1,
                run_id=uuid.uuid4().hex,
                project_id=request.project_id,
                status=RunStatus.queued,
                current_stage=None,
                version=0,
                config_snapshot_id=request.config_snapshot_id,
                paused_reason=None,
                created_at=now,
                updated_at=now,
            )
            state = {
                "revision_count_by_stage": {},
                "rollback_count": 0,
                "technical_retries_by_invocation": {},
                "format_repairs_by_invocation": {},
                "config_snapshot_id": request.config_snapshot_id.root,
                "config_snapshot": config_snapshot.model_dump(mode="json") if config_snapshot else None,
                "mode": request.mode.value,
                "user_goal": request.user_goal.root,
                "initial_artifact_refs": [ref.model_dump(mode="json") for ref in request.input_artifact_refs],
                "approved_versions": [],
                "current_artifacts_by_stage": {},
                "dispatch_unknown": [],
            }
            connection.execute(
                "INSERT INTO runs(run_id, project_id, version, run_json, state_json) VALUES(?,?,?,?,?)",
                (run.run_id.root, run.project_id.root, 0, run.model_dump_json(), _json(state)),
            )
            connection.execute(
                "INSERT INTO create_keys(project_id,idempotency_key,request_fingerprint,run_id) VALUES(?,?,?,?)",
                (request.project_id.root, request.idempotency_key.root, fingerprint, run.run_id.root),
            )
            created = RunEvent(
                schema_version=1,
                event_id=uuid.uuid4().hex,
                run_id=run.run_id,
                event_seq=1,
                version=run.version,
                event_type="run.created",
                stage=None,
                stage_attempt_id=None,
                artifact_version_ids=[],
                review_id=None,
                approval_id=None,
                occurred_at=now,
                summary="Run accepted with an immutable configuration snapshot reference.",
            )
            connection.execute(
                "INSERT INTO events(run_id,event_seq,event_id,event_json) VALUES(?,?,?,?)",
                (run.run_id.root, 1, created.event_id.root, created.model_dump_json()),
            )
            self.database.close_commit(connection)
            return run
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def read_run(self, project_id: Identifier, run_id: Identifier) -> AgentRun | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT run_json FROM runs WHERE project_id=? AND run_id=?",
                (project_id.root, run_id.root),
            ).fetchone()
        return _run_from_json(row["run_json"]) if row else None

    async def read_run_by_id(self, run_id: Identifier) -> AgentRun | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT run_json FROM runs WHERE run_id=?", (run_id.root,)).fetchone()
        return _run_from_json(row["run_json"]) if row else None

    async def read_runtime_state(self, run_id: Identifier) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT state_json FROM runs WHERE run_id=?", (run_id.root,)).fetchone()
        return json.loads(row["state_json"]) if row else None

    async def read_idempotent_result(self, run_id: Identifier, idempotency_key: Identifier, request_fingerprint: str) -> AgentRun | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT request_fingerprint,response_json,response_kind FROM idempotency WHERE run_id=? AND idempotency_key=?",
                (run_id.root, idempotency_key.root),
            ).fetchone()
        if row is None:
            return None
        if row["request_fingerprint"] != request_fingerprint:
            raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "The idempotency key was reused for a different mutation.", request_id=idempotency_key.root)
        if row["response_kind"] == "ApprovalRecord":
            return ApprovalRecord.model_validate_json(row["response_json"])
        return _run_from_json(row["response_json"])

    async def commit_transition(
        self,
        run_id: Identifier,
        expected_revision: int,
        next_run: AgentRun,
        events: Sequence[RunEvent],
        counter_changes: Mapping[str, int],
        *,
        lease: LeaseGrant,
        idempotency_key: Identifier | None = None,
        request_fingerprint: str | None = None,
        runtime_state: Mapping[str, Any] | None = None,
        attempts: Sequence[StageAttempt] = (),
        reviews: Sequence[ReviewRecord] = (),
        approvals: Sequence[ApprovalRecord] = (),
        stale_version_ids: Sequence[Identifier] = (),
        idempotency_response: AgentRun | ApprovalRecord | None = None,
    ) -> AgentRun | ApprovalRecord:
        if next_run.run_id.root != run_id.root or next_run.version.root != expected_revision + 1:
            raise ValueError("A committed transition must target this run and increment exactly one revision.")
        if idempotency_key is not None and not request_fingerprint:
            raise ValueError("Idempotent transitions require a request fingerprint.")
        connection = self.database.transaction()
        try:
            if idempotency_key is not None:
                replay = connection.execute(
                    "SELECT request_fingerprint,response_json,response_kind FROM idempotency WHERE run_id=? AND idempotency_key=?",
                    (run_id.root, idempotency_key.root),
                ).fetchone()
                if replay:
                    if replay["request_fingerprint"] != request_fingerprint:
                        raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "The idempotency key was reused for a different mutation.", request_id=idempotency_key.root)
                    result = ApprovalRecord.model_validate_json(replay["response_json"]) if replay["response_kind"] == "ApprovalRecord" else _run_from_json(replay["response_json"])
                    self.database.close_commit(connection)
                    return result

            row = connection.execute(
                "SELECT version,run_json,state_json FROM runs WHERE run_id=?", (run_id.root,)
            ).fetchone()
            if row is None:
                raise DomainError(ErrorCode.PROJECT_UNAVAILABLE, "The run is unavailable.", request_id=run_id.root)
            if row["version"] != expected_revision:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "The run revision changed; reload before retrying.", request_id=run_id.root)
            if not _lease_valid(connection, lease):
                raise DomainError(ErrorCode.REVISION_CONFLICT, "The execution lease expired or was fenced by a newer owner.", request_id=run_id.root)
            if next_run.project_id.root != _run_from_json(row["run_json"]).project_id.root:
                raise ValueError("Run project identity cannot change during a transition.")

            state = json.loads(row["state_json"])
            for counter_name, delta in counter_changes.items():
                if not isinstance(delta, int):
                    raise TypeError("Counter changes must be integer deltas.")
                counter = int(state.get(counter_name, 0)) + delta
                if counter < 0:
                    raise ValueError(f"Counter {counter_name!r} cannot become negative.")
                state[counter_name] = counter

            # Validate and invalidate exact previously-owned artifact versions in this transaction.
            for version_ref in stale_version_ids:
                artifact = connection.execute(
                    "SELECT artifact_json,run_id FROM artifacts WHERE version_id=?", (version_ref.root,)
                ).fetchone()
                if artifact is None or artifact["run_id"] != run_id.root:
                    raise DomainError(ErrorCode.ARTIFACT_STALE, "A rollback references an artifact outside this run.", request_id=run_id.root)
                old = ArtifactRef.model_validate_json(artifact["artifact_json"])
                if old.lifecycle is not ArtifactLifecycle.stale:
                    stale = old.model_copy(update={
                        "lifecycle": ArtifactLifecycle.stale,
                        "revision": old.revision.__class__(old.revision.root + 1),
                    })
                    connection.execute("UPDATE artifacts SET artifact_json=? WHERE version_id=?", (stale.model_dump_json(), version_ref.root))
                state.setdefault("invalidated_versions", [])
                if version_ref.root not in state["invalidated_versions"]:
                    state["invalidated_versions"].append(version_ref.root)
                approved = state.setdefault("approved_versions", [])
                if version_ref.root in approved:
                    approved.remove(version_ref.root)

            if runtime_state is not None:
                requested_state = dict(runtime_state)
                invalidated = list(dict.fromkeys([
                    *requested_state.get("invalidated_versions", []),
                    *state.get("invalidated_versions", []),
                ]))
                requested_state["invalidated_versions"] = invalidated
                requested_state["approved_versions"] = [
                    version for version in requested_state.get("approved_versions", [])
                    if version not in set(invalidated)
                ]
                state = requested_state

            for attempt in attempts:
                connection.execute(
                    "INSERT INTO attempts(stage_attempt_id,run_id,stage,attempt_json) VALUES(?,?,?,?) "
                    "ON CONFLICT(stage_attempt_id) DO UPDATE SET stage=excluded.stage,attempt_json=excluded.attempt_json "
                    "WHERE attempts.run_id=excluded.run_id",
                    (attempt.stage_attempt_id.root, run_id.root, attempt.stage.value, attempt.model_dump_json()),
                )
            for review in reviews:
                connection.execute(
                    "INSERT INTO reviews(review_id,run_id,artifact_version_id,review_json) VALUES(?,?,?,?)",
                    (review.review_id.root, run_id.root, review.artifact.version_id.root, review.model_dump_json()),
                )
            for approval in approvals:
                connection.execute(
                    "INSERT INTO approvals(approval_id,run_id,artifact_version_id,approval_json) VALUES(?,?,?,?)",
                    (approval.approval_id.root, run_id.root, approval.artifact_version_id.root, approval.model_dump_json()),
                )
                if approval.decision.value in {"approve", "override"}:
                    state.setdefault("approved_versions", [])
                    if approval.artifact_version_id.root not in state["approved_versions"]:
                        state["approved_versions"].append(approval.artifact_version_id.root)

            head = connection.execute("SELECT COALESCE(MAX(event_seq),0) AS seq FROM events WHERE run_id=?", (run_id.root,)).fetchone()["seq"]
            for offset, event in enumerate(events, start=1):
                stored_event = event.model_copy(update={
                    "run_id": run_id,
                    "event_seq": head + offset,
                    "version": next_run.version,
                })
                connection.execute(
                    "INSERT INTO events(run_id,event_seq,event_id,event_json) VALUES(?,?,?,?)",
                    (run_id.root, head + offset, stored_event.event_id.root, stored_event.model_dump_json()),
                )
            connection.execute(
                "UPDATE runs SET version=?,run_json=?,state_json=? WHERE run_id=? AND version=?",
                (next_run.version.root, next_run.model_dump_json(), _json(state), run_id.root, expected_revision),
            )
            if idempotency_key is not None:
                response = idempotency_response or next_run
                response_kind = "ApprovalRecord" if isinstance(response, ApprovalRecord) else "AgentRun"
                connection.execute(
                    "INSERT INTO idempotency(run_id,idempotency_key,request_fingerprint,response_json,response_kind) VALUES(?,?,?,?,?)",
                    (run_id.root, idempotency_key.root, request_fingerprint, response.model_dump_json(), response_kind),
                )
            self.database.close_commit(connection)
            return next_run
        except sqlite3.IntegrityError as exc:
            self.database.close_rollback(connection)
            raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "A duplicate runtime record prevented this transition.", request_id=run_id.root) from exc
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def claim_lease(self, run_id: Identifier, owner_id: Identifier, lease_seconds: int) -> LeaseGrant | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        connection = self.database.transaction()
        try:
            if connection.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id.root,)).fetchone() is None:
                self.database.close_commit(connection)
                return None
            row = connection.execute("SELECT owner_id,generation,expires_at FROM leases WHERE run_id=?", (run_id.root,)).fetchone()
            now = utc_now()
            now_text = utc_text(now)
            if row:
                expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
                if expires > now and row["owner_id"] != owner_id.root:
                    self.database.close_commit(connection)
                    return None
                if expires > now and row["owner_id"] == owner_id.root:
                    generation = row["generation"]
                else:
                    generation = row["generation"] + 1
            else:
                generation = 1
            expires_at = now + timedelta(seconds=lease_seconds)
            expires_text = utc_text(expires_at)
            connection.execute(
                "INSERT INTO leases(run_id,owner_id,generation,expires_at) VALUES(?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET owner_id=excluded.owner_id,generation=excluded.generation,expires_at=excluded.expires_at",
                (run_id.root, owner_id.root, generation, expires_text),
            )
            lease = LeaseGrant(run_id=run_id, owner_id=owner_id, generation=generation, expires_at=expires_at)
            self.database.close_commit(connection)
            return lease
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def renew_lease(self, lease: LeaseGrant, lease_seconds: int) -> LeaseGrant | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        connection = self.database.transaction()
        try:
            if not _lease_valid(connection, lease):
                self.database.close_commit(connection)
                return None
            expiration = utc_now() + timedelta(seconds=lease_seconds)
            text = utc_text(expiration)
            connection.execute("UPDATE leases SET expires_at=? WHERE run_id=? AND owner_id=? AND generation=?", (text, lease.run_id.root, lease.owner_id.root, lease.generation))
            renewed = LeaseGrant(run_id=lease.run_id, owner_id=lease.owner_id, generation=lease.generation, expires_at=expiration)
            self.database.close_commit(connection)
            return renewed
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def release_lease(self, lease: LeaseGrant) -> bool:
        connection = self.database.transaction()
        try:
            if not _lease_valid(connection, lease):
                self.database.close_commit(connection)
                return False
            connection.execute("UPDATE leases SET expires_at=? WHERE run_id=? AND owner_id=? AND generation=?", (utc_text(), lease.run_id.root, lease.owner_id.root, lease.generation))
            self.database.close_commit(connection)
            return True
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def scan_recoverable(self, cursor: Identifier | None, limit: int) -> tuple[Sequence[AgentRun], Identifier | None]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.database.connect() as connection:
            if cursor is not None:
                exists = connection.execute("SELECT 1 FROM runs WHERE run_id=?", (cursor.root,)).fetchone()
                if exists is None:
                    raise ValueError("Unknown recovery cursor")
            rows = connection.execute(
                "SELECT run_json FROM runs WHERE json_extract(run_json,'$.status') IN ('queued','running','waiting_review','paused') "
                "AND (? IS NULL OR run_id > ?) ORDER BY run_id LIMIT ?",
                (cursor.root if cursor else None, cursor.root if cursor else None, limit + 1),
            ).fetchall()
        more = len(rows) > limit
        selected = rows[:limit]
        values = [_run_from_json(row["run_json"]) for row in selected]
        next_cursor = Identifier(values[-1].run_id.root) if more and values else None
        return values, next_cursor

    async def begin_invocation(self, record: InvocationRecord) -> tuple[InvocationRecord, bool, ModelInvocationResult | None]:
        """Atomically claim one dispatch; only the first/not-sent owner may call upstream."""
        if record.dispatch_state is not DispatchState.sent:
            raise ValueError("begin_invocation must persist sent state before dispatch.")
        connection = self.database.transaction()
        try:
            existing = connection.execute(
                "SELECT invocation_id,request_fingerprint,record_json,result_json FROM invocations WHERE run_id=? AND idempotency_key=?",
                (record.run_id.root, record.idempotency_key.root),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != record.request_fingerprint.root:
                    raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "The invocation idempotency key was reused for different input.", request_id=record.invocation_id.root)
                old = InvocationRecord.model_validate_json(existing["record_json"])
                result = ModelInvocationResult.model_validate_json(existing["result_json"]) if existing["result_json"] else None
                if result is None and old.dispatch_state is DispatchState.not_sent:
                    connection.execute("UPDATE invocations SET record_json=? WHERE invocation_id=?", (record.model_dump_json(), old.invocation_id.root))
                    self.database.close_commit(connection)
                    return record, True, None
                self.database.close_commit(connection)
                return old, False, result
            connection.execute(
                "INSERT INTO invocations(invocation_id,run_id,idempotency_key,request_fingerprint,record_json) VALUES(?,?,?,?,?)",
                (record.invocation_id.root, record.run_id.root, record.idempotency_key.root, record.request_fingerprint.root, record.model_dump_json()),
            )
            self.database.close_commit(connection)
            return record, True, None
        except sqlite3.IntegrityError as exc:
            self.database.close_rollback(connection)
            raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "Invocation claim conflicts with a prior dispatch.", request_id=record.invocation_id.root) from exc
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def mark_invocation_unknown(self, invocation_id: Identifier, reason: str) -> InvocationRecord:
        connection = self.database.transaction()
        try:
            row = connection.execute("SELECT record_json FROM invocations WHERE invocation_id=?", (invocation_id.root,)).fetchone()
            if row is None:
                raise KeyError(invocation_id.root)
            old = InvocationRecord.model_validate_json(row["record_json"])
            updated = old.model_copy(update={
                "dispatch_state": DispatchState.unknown,
                "usage": None,
                "usage_unavailable_reason": UsageUnavailableReason(reason),
                "updated_at": UtcDateTime(utc_now()),
            })
            connection.execute("UPDATE invocations SET record_json=? WHERE invocation_id=?", (updated.model_dump_json(), invocation_id.root))
            self.database.close_commit(connection)
            return updated
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def mark_invocation_not_sent(self, invocation_id: Identifier, reason: str) -> InvocationRecord:
        """Record an adapter's affirmative proof that no upstream dispatch occurred."""
        connection = self.database.transaction()
        try:
            row = connection.execute("SELECT record_json,result_json FROM invocations WHERE invocation_id=?", (invocation_id.root,)).fetchone()
            if row is None:
                raise KeyError(invocation_id.root)
            if row["result_json"] is not None:
                raise ValueError("A completed invocation cannot become not_sent.")
            old = InvocationRecord.model_validate_json(row["record_json"])
            updated = old.model_copy(update={
                "dispatch_state": DispatchState.not_sent,
                "usage": None,
                "usage_unavailable_reason": UsageUnavailableReason(reason),
                "updated_at": UtcDateTime(utc_now()),
            })
            connection.execute("UPDATE invocations SET record_json=? WHERE invocation_id=?", (updated.model_dump_json(), invocation_id.root))
            self.database.close_commit(connection)
            return updated
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def reserve_retry(self, run_id: Identifier, lease: LeaseGrant, operation_id: Identifier, reservation_id: Identifier, *, kind: str = "technical", limit: int = 2) -> AgentRun:
        """CAS-increment a persisted technical retry/format-repair counter and event."""
        if kind not in {"technical", "format"}:
            raise ValueError("Unsupported retry kind")
        expected_limit = 2 if kind == "technical" else 1
        if limit != expected_limit:
            raise ValueError(f"{kind} retry limit is frozen at {expected_limit}.")
        key_prefix = "technical_retries_by_operation" if kind == "technical" else "format_repairs_by_operation"
        fingerprint = _fingerprint({"operation_id": operation_id.root, "kind": kind, "limit": limit})
        connection = self.database.transaction()
        try:
            prior = connection.execute("SELECT request_fingerprint,response_json FROM retry_reservations WHERE run_id=? AND reservation_id=?", (run_id.root, reservation_id.root)).fetchone()
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "Retry reservation id was reused for another operation.", request_id=reservation_id.root)
                result = _run_from_json(prior["response_json"])
                self.database.close_commit(connection)
                return result
            row = connection.execute("SELECT version,run_json,state_json FROM runs WHERE run_id=?", (run_id.root,)).fetchone()
            if row is None:
                raise DomainError(ErrorCode.PROJECT_UNAVAILABLE, "The run is unavailable.", request_id=run_id.root)
            if not _lease_valid(connection, lease):
                raise DomainError(ErrorCode.REVISION_CONFLICT, "Retry reservation requires the active fenced lease.", request_id=run_id.root)
            state = json.loads(row["state_json"])
            counters = dict(state.get(key_prefix, {}))
            current = int(counters.get(operation_id.root, 0))
            if current >= limit:
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The technical retry or format-repair limit is exhausted.", request_id=operation_id.root)
            counters[operation_id.root] = current + 1
            state[key_prefix] = counters
            old_run = _run_from_json(row["run_json"])
            updated_run = old_run.model_copy(update={"version": old_run.version.__class__(old_run.version.root + 1), "updated_at": UtcDateTime(utc_now())})
            seq = connection.execute("SELECT COALESCE(MAX(event_seq),0)+1 AS seq FROM events WHERE run_id=?", (run_id.root,)).fetchone()["seq"]
            event = RunEvent(
                schema_version=1, event_id=uuid.uuid4().hex, run_id=run_id,
                event_seq=seq, version=updated_run.version,
                event_type="runtime.retry.reserved" if kind == "technical" else "runtime.format_repair.reserved",
                stage=old_run.current_stage, stage_attempt_id=None, artifact_version_ids=[],
                review_id=None, approval_id=None, occurred_at=utc_now(),
                summary="A bounded retry was reserved before dispatch; no call result is assumed.",
            )
            connection.execute("INSERT INTO events(run_id,event_seq,event_id,event_json) VALUES(?,?,?,?)", (run_id.root, seq, event.event_id.root, event.model_dump_json()))
            connection.execute("UPDATE runs SET version=?,run_json=?,state_json=? WHERE run_id=? AND version=?", (updated_run.version.root, updated_run.model_dump_json(), _json(state), run_id.root, row["version"]))
            connection.execute("INSERT INTO retry_reservations(run_id,reservation_id,operation_id,request_fingerprint,response_json) VALUES(?,?,?,?,?)", (run_id.root, reservation_id.root, operation_id.root, fingerprint, updated_run.model_dump_json()))
            self.database.close_commit(connection)
            return updated_run
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def record_invocation(self, record: InvocationRecord) -> InvocationRecord:
        connection = self.database.transaction()
        try:
            existing = connection.execute(
                "SELECT invocation_id,request_fingerprint,record_json FROM invocations WHERE run_id=? AND idempotency_key=?",
                (record.run_id.root, record.idempotency_key.root),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != record.request_fingerprint.root:
                    raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "The invocation idempotency key was reused for different input.", request_id=record.invocation_id.root)
                old = InvocationRecord.model_validate_json(existing["record_json"])
                if existing["invocation_id"] != record.invocation_id.root:
                    # Stable key maps to the already-persisted invocation, never a new call.
                    self.database.close_commit(connection)
                    return old
                order = {DispatchState.not_sent: 0, DispatchState.sent: 1, DispatchState.unknown: 2}
                if order[record.dispatch_state] < order[old.dispatch_state]:
                    raise ValueError("Invocation dispatch state cannot move backwards.")
                if old.dispatch_state is DispatchState.unknown and record.dispatch_state is DispatchState.sent:
                    raise ValueError("An unknown invocation outcome cannot be resent.")
                if old.result_ref is not None and record.result_ref != old.result_ref:
                    raise ValueError("A completed invocation result is immutable.")
                connection.execute("UPDATE invocations SET record_json=? WHERE invocation_id=?", (record.model_dump_json(), record.invocation_id.root))
                self.database.close_commit(connection)
                return record
            connection.execute(
                "INSERT INTO invocations(invocation_id,run_id,idempotency_key,request_fingerprint,record_json) VALUES(?,?,?,?,?)",
                (record.invocation_id.root, record.run_id.root, record.idempotency_key.root, record.request_fingerprint.root, record.model_dump_json()),
            )
            self.database.close_commit(connection)
            return record
        except sqlite3.IntegrityError as exc:
            self.database.close_rollback(connection)
            raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "Invocation record conflicts with a prior call.", request_id=record.invocation_id.root) from exc
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def record_invocation_result(self, invocation_id: Identifier, result: ModelInvocationResult) -> None:
        connection = self.database.transaction()
        try:
            row = connection.execute("SELECT record_json,result_json FROM invocations WHERE invocation_id=?", (invocation_id.root,)).fetchone()
            if row is None:
                raise KeyError(invocation_id.root)
            if row["result_json"] is not None and row["result_json"] != result.model_dump_json():
                raise ValueError("An invocation result is immutable and cannot be replaced.")
            record = InvocationRecord.model_validate_json(row["record_json"])
            if record.dispatch_state is DispatchState.not_sent:
                raise ValueError("Cannot record a result for a call that was never marked sent.")
            now = utc_now()
            updated = record.model_copy(update={
                "result_ref": Identifier(f"result:{invocation_id.root}"),
                "upstream_request_id": result.upstream_request_id,
                "usage": result.usage,
                "usage_unavailable_reason": result.usage_unavailable_reason,
                "updated_at": UtcDateTime(now),
            })
            connection.execute("UPDATE invocations SET record_json=?,result_json=? WHERE invocation_id=?", (updated.model_dump_json(), result.model_dump_json(), invocation_id.root))
            self.database.close_commit(connection)
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def read_invocation_result(self, invocation_id: Identifier) -> ModelInvocationResult | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT result_json FROM invocations WHERE invocation_id=?", (invocation_id.root,)).fetchone()
        return ModelInvocationResult.model_validate_json(row["result_json"]) if row and row["result_json"] else None

    async def read_invocation(self, run_id: Identifier, idempotency_key: Identifier) -> InvocationRecord | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT record_json FROM invocations WHERE run_id=? AND idempotency_key=?", (run_id.root, idempotency_key.root)).fetchone()
        return InvocationRecord.model_validate_json(row["record_json"]) if row else None

    async def read_invocation_operation_result(self, run_id: Identifier, operation_id: Identifier) -> ModelInvocationResult | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT result_json FROM invocation_operations WHERE run_id=? AND operation_id=?", (run_id.root, operation_id.root)).fetchone()
        return ModelInvocationResult.model_validate_json(row["result_json"]) if row else None

    async def record_invocation_operation_result(self, run_id: Identifier, operation_id: Identifier, invocation_id: Identifier, result: ModelInvocationResult) -> None:
        connection = self.database.transaction()
        try:
            existing = connection.execute("SELECT invocation_id,result_json FROM invocation_operations WHERE run_id=? AND operation_id=?", (run_id.root, operation_id.root)).fetchone()
            if existing:
                if existing["result_json"] != result.model_dump_json():
                    raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "A logical invocation operation already has a different result.", request_id=operation_id.root)
                self.database.close_commit(connection)
                return
            connection.execute(
                "INSERT INTO invocation_operations(run_id,operation_id,invocation_id,result_json) VALUES(?,?,?,?)",
                (run_id.root, operation_id.root, invocation_id.root, result.model_dump_json()),
            )
            self.database.close_commit(connection)
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def read_events(self, run_id: Identifier, after_cursor: Identifier | None, limit: int) -> EventPage:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.database.connect() as connection:
            run = connection.execute("SELECT version FROM runs WHERE run_id=?", (run_id.root,)).fetchone()
            if run is None:
                raise KeyError(run_id.root)
            after_seq = 0
            if after_cursor is not None:
                cursor_row = connection.execute("SELECT event_seq FROM events WHERE run_id=? AND event_id=?", (run_id.root, after_cursor.root)).fetchone()
                if cursor_row is None:
                    raise ValueError("Event cursor does not belong to this run")
                after_seq = cursor_row["event_seq"]
            rows = connection.execute("SELECT event_json FROM events WHERE run_id=? AND event_seq>? ORDER BY event_seq LIMIT ?", (run_id.root, after_seq, limit + 1)).fetchall()
        more = len(rows) > limit
        selected = rows[:limit]
        events = [_event_from_json(row["event_json"]) for row in selected]
        cursor = Identifier(events[-1].event_id.root) if more and events else None
        return EventPage(events=events, next_cursor=cursor, snapshot_version=run["version"])

    async def append(self, event: RunEvent) -> None:
        connection = self.database.transaction()
        try:
            row = connection.execute("SELECT COALESCE(MAX(event_seq),0) AS seq FROM events WHERE run_id=?", (event.run_id.root,)).fetchone()
            expected = row["seq"] + 1
            if event.event_seq != expected:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "Event sequence changed; refresh before appending.", request_id=event.event_id.root)
            connection.execute("INSERT INTO events(run_id,event_seq,event_id,event_json) VALUES(?,?,?,?)", (event.run_id.root, event.event_seq, event.event_id.root, event.model_dump_json()))
            self.database.close_commit(connection)
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def append_runtime_records(
        self,
        run_id: Identifier,
        *,
        attempts: Sequence[StageAttempt] = (),
        reviews: Sequence[ReviewRecord] = (),
        approvals: Sequence[ApprovalRecord] = (),
    ) -> None:
        """Test/helper insertion; normal runtime writes these with commit_transition."""
        connection = self.database.transaction()
        try:
            for attempt in attempts:
                connection.execute("INSERT INTO attempts VALUES(?,?,?,?)", (attempt.stage_attempt_id.root, run_id.root, attempt.stage.value, attempt.model_dump_json()))
            for review in reviews:
                connection.execute("INSERT INTO reviews VALUES(?,?,?,?)", (review.review_id.root, run_id.root, review.artifact.version_id.root, review.model_dump_json()))
            for approval in approvals:
                connection.execute("INSERT INTO approvals VALUES(?,?,?,?)", (approval.approval_id.root, run_id.root, approval.artifact_version_id.root, approval.model_dump_json()))
            self.database.close_commit(connection)
        except Exception:
            self.database.close_rollback(connection)
            raise

    async def get_attempts(self, run_id: Identifier) -> list[StageAttempt]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT attempt_json FROM attempts WHERE run_id=? ORDER BY rowid", (run_id.root,)).fetchall()
        return [StageAttempt.model_validate_json(row["attempt_json"]) for row in rows]

    async def get_reviews(self, run_id: Identifier) -> list[ReviewRecord]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT review_json FROM reviews WHERE run_id=? ORDER BY rowid", (run_id.root,)).fetchall()
        return [ReviewRecord.model_validate_json(row["review_json"]) for row in rows]

    async def is_approved(self, run_id: Identifier, review_id: Identifier, artifact_version_id: Identifier) -> bool:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT approval_json FROM approvals WHERE run_id=? AND artifact_version_id=?", (run_id.root, artifact_version_id.root)).fetchall()
            artifact = connection.execute("SELECT artifact_json FROM artifacts WHERE run_id=? AND version_id=?", (run_id.root, artifact_version_id.root)).fetchone()
        if artifact is None:
            return False
        ref = ArtifactRef.model_validate_json(artifact["artifact_json"])
        if ref.lifecycle is not ArtifactLifecycle.valid:
            return False
        for item in rows:
            approval = ApprovalRecord.model_validate_json(item["approval_json"])
            if approval.review_id.root == review_id.root and approval.decision.value in {"approve", "override"}:
                return True
        return False

    async def get_approval_for_artifact(self, run_id: Identifier, review_id: Identifier, artifact_version_id: Identifier) -> ApprovalRecord | None:
        """Return only an approval bound to the exact review and artifact version."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT approval_json FROM approvals WHERE run_id=? AND artifact_version_id=? ORDER BY rowid DESC",
                (run_id.root, artifact_version_id.root),
            ).fetchall()
        for row in rows:
            approval = ApprovalRecord.model_validate_json(row["approval_json"])
            if approval.review_id.root == review_id.root and approval.decision.value in {"approve", "override"}:
                return approval
        return None

    async def latest_review_for_artifact(self, run_id: Identifier, artifact_version_id: Identifier) -> ReviewRecord | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT review_json FROM reviews WHERE run_id=? AND artifact_version_id=? ORDER BY rowid DESC LIMIT 1", (run_id.root, artifact_version_id.root)).fetchone()
        return ReviewRecord.model_validate_json(row["review_json"]) if row else None

    async def attempts_by_stage(self, run_id: Identifier, stage: str) -> list[StageAttempt]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT attempt_json FROM attempts WHERE run_id=? AND stage=? ORDER BY rowid", (run_id.root, stage)).fetchall()
        return [StageAttempt.model_validate_json(row["attempt_json"]) for row in rows]

    async def incomplete_invocations(self, run_id: Identifier) -> list[InvocationRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT record_json FROM invocations WHERE run_id=? AND result_json IS NULL",
                (run_id.root,),
            ).fetchall()
        pending = [InvocationRecord.model_validate_json(row["record_json"]) for row in rows]
        return [record for record in pending if record.dispatch_state in {DispatchState.sent, DispatchState.unknown}]
