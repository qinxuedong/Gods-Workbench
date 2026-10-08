"""Same-process, Cookie-authenticated host composition for durable AgentRuns."""
from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import inspect
import json
import os
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gw.agent_episode.runtime_bridge import EpisodeRuntimeBridge
from gw.agent_episode.scoring import SCORING_RULE_VERSION, STAGE_WEIGHTS
from gw.agent_runtime.adapters.artifacts import SQLiteArtifactRepository
from gw.agent_runtime.adapters.invocation import SafeInvocationDispatcher
from gw.agent_runtime.application import HostIdentityProjectAdapter, RuntimeAgentUseCase
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    BudgetDecision,
    BudgetOutcomeState,
    BudgetReservationRequest,
    BudgetSettlement,
    BudgetState,
    CallLimits,
    ConfigSnapshot,
    DispatchState,
    ErrorCode,
    FinishReason,
    Identifier,
    InvocationLimits,
    MessageRole,
    ModelCapabilities,
    ModelInvocationRequest,
    ModelInvocationResult,
    Mode,
    Reason,
    Usage,
    UsageUnavailableReason,
    Version,
)
from gw.agent_runtime.policy import CapabilityRegistry, RoleRegistration, RuntimePolicy
from gw.agent_runtime.repository import SQLiteRunRepository, utc_now
from gw.agent_runtime.storage.checkpoints import SQLiteCheckpointBackend
from gw.agent_runtime.storage.sqlite import SQLiteDatabase
from gw.agent_runtime.worker import RuntimeBackgroundWorker
from gw.core import local_accounts, session as session_store
from gw.core.auth import AuthContext, EDIT_ROLES, KNOWN_ROLES, oidc_session_trust_failure_reason
from gw.core.config import AUTH_MODE_LOCAL_ACCOUNT, AUTH_MODE_OIDC, load_runtime_auth_config
from gw.core.errors import CleanroomException, ForbiddenException, UnauthorizedException
from gw.core.runtime_paths import RuntimePathError, RuntimePaths, resolve_runtime_paths
from gw.core.storage import next_sequence
from gw.projects_hub import service as projects_service_module
from gw.projects_hub.service import ProjectsService, owner_key_for_context
from gw.settings import chat as chat_runtime


def _token_fingerprint(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _model_ref(provider_id: str, model: str) -> Identifier:
    raw = json.dumps([provider_id, model], ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return Identifier("gw-chat:" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="))


_MODEL_CALL_LIMIT_ENV = "GW_AGENT_MAX_MODEL_CALLS_PER_RUN"
AGENT_JOB_ID_PREFIX = "job_agent_"


def _configured_model_call_limit() -> int | None:
    raw = os.environ.get(_MODEL_CALL_LIMIT_ENV)
    if not isinstance(raw, str) or not raw or not raw.isascii() or not raw.isdecimal():
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _decode_model_ref(value: Identifier) -> tuple[str, str]:
    prefix = "gw-chat:"
    if not value.root.startswith(prefix):
        raise ValueError("The captured model reference is not a host Chat model.")
    encoded = value.root[len(prefix):]
    decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    payload = json.loads(decoded)
    if (not isinstance(payload, list) or len(payload) != 2
            or not all(isinstance(item, str) and item for item in payload)):
        raise ValueError("The captured model reference is invalid.")
    return payload[0], payload[1]


class HostChatModelGateway:
    """Typed host gateway that uses only the server's allowlisted Chat settings."""

    async def describe_capabilities(self, model_ref: Identifier) -> ModelCapabilities:
        return ModelCapabilities(
            model_ref=model_ref,
            supported_roles=list(MessageRole),
            structured_output=True,
            tool_calls=False,
            streaming=False,
            multimodal=False,
            lookup=False,
            cancellation=False,
            usage_reporting=True,
            max_context_tokens=None,
        )

    async def invoke(self, request: ModelInvocationRequest) -> ModelInvocationResult:
        try:
            provider_id, model = _decode_model_ref(request.model_ref)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise DomainError(
                code=ErrorCode.VALIDATION_FAILED,
                message="The captured host model reference is invalid.",
                request_id=request.invocation_id.root,
            ) from exc

        messages = [
            {"role": message.role.value, "content": message.content,
             **({"name": message.name.root} if message.name is not None else {})}
            for message in request.messages
        ]
        try:
            response = await asyncio.to_thread(
                chat_runtime.complete_messages,
                {"provider_id": provider_id, "model": model},
                messages,
                timeout_seconds=request.limits.timeout_seconds,
                max_output_tokens=request.limits.max_output_tokens,
                output_schema=request.output_schema,
                endpoint="/api/agent/model-invocation",
                include_agent_usage=True,
            )
        except CleanroomException as exc:
            # A disabled/missing local client or a changed server-side profile is
            # rejected before the network boundary. Provider/transport failures
            # may have been billed, so their outcome stays unknown and is never retried.
            preflight = exc.code in {
                "CHAT_NOT_INTEGRATED", "CHAT_PROVIDER_NOT_CONFIGURED",
                "CHAT_MODEL_NOT_CONFIGURED", "CHAT_PROVIDER_SELECTION_REQUIRED",
                "INVALID_REQUEST",
            }
            if preflight:
                raise DomainError(
                    ErrorCode.UPSTREAM_UNAVAILABLE,
                    "The captured host model is no longer dispatchable; no upstream call was sent.",
                    request_id=request.invocation_id.root,
                    retryable=True,
                    dispatch_state=DispatchState.not_sent,
                ) from None
            raise DomainError(
                ErrorCode.OUTCOME_UNKNOWN,
                "The host provider outcome could not be confirmed; automatic resend is forbidden.",
                request_id=request.invocation_id.root,
                dispatch_state=DispatchState.unknown,
            ) from None

        raw_usage = response.get("agent_usage")
        usage = Usage.model_validate(raw_usage) if isinstance(raw_usage, Mapping) else None
        unavailable = response.get("agent_usage_unavailable_reason")
        usage_reason = UsageUnavailableReason(unavailable) if isinstance(unavailable, str) and unavailable else None
        raw_upstream_id = response.get("agent_upstream_request_id")
        upstream_id = None
        if isinstance(raw_upstream_id, str) and raw_upstream_id.strip() and len(raw_upstream_id) <= 256 and not any(c.isspace() for c in raw_upstream_id):
            upstream_id = Identifier(raw_upstream_id)
        finish = str(response.get("agent_finish_reason") or "unknown")
        try:
            finish_reason = FinishReason(finish)
        except ValueError:
            finish_reason = FinishReason.unknown
        return ModelInvocationResult(
            content=response.get("reply") if isinstance(response.get("reply"), str) else None,
            tool_calls=[],
            finish_reason=finish_reason,
            upstream_request_id=upstream_id,
            usage=usage,
            usage_unavailable_reason=usage_reason,
        )


class UnknownCostBudget:
    """Atomically enforce a durable per-run call ceiling; monetary cost stays unknown."""

    _ACTIVE_STATES = ("reserved_unknown", "unknown")

    def __init__(self, database: SQLiteDatabase, max_model_calls_per_run: int | None = None) -> None:
        self.database = database
        self.max_model_calls_per_run = max_model_calls_per_run
        self._active_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"agent_budget_run_{id(self)}", default=None,
        )

    def bind_run(self, run_id: Identifier):
        return self._active_run_id.set(run_id.root)

    def reset_run(self, token: contextvars.Token[str | None]) -> None:
        self._active_run_id.reset(token)

    @staticmethod
    def _run_id_for_operation(operation_id: str) -> str | None:
        parts = operation_id.split(":", 3)
        if len(parts) != 4 or parts[0] != "episode" or not parts[1]:
            return None
        try:
            return Identifier(parts[1]).root
        except Exception:
            return None

    @staticmethod
    def _legacy_run_id_from_invocations(db: Any, operation_id: str) -> str | None:
        tables = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invocations'"
        ).fetchone()
        if tables is None:
            return None
        columns = {row["name"] for row in db.execute("PRAGMA table_info(invocations)").fetchall()}
        operation_columns = [name for name in ("idempotency_key", "operation_id") if name in columns]
        if not operation_columns or "run_id" not in columns:
            return None
        run_ids: set[str] = set()
        for operation_column in operation_columns:
            rows = db.execute(
                f"SELECT DISTINCT run_id FROM invocations WHERE {operation_column}=? LIMIT 2",
                (operation_id,),
            ).fetchall()
            run_ids.update(str(row["run_id"]) for row in rows if row["run_id"] is not None)
            if len(run_ids) > 1:
                return None
        if len(run_ids) != 1:
            return None
        try:
            return Identifier(next(iter(run_ids))).root
        except Exception:
            return None

    @staticmethod
    def _decision(state: BudgetState, message: str) -> BudgetDecision:
        return BudgetDecision(
            state=state,
            amount=None,
            currency=None,
            reason=Reason(message),
        )

    @staticmethod
    def _ledger_operation_id(db: Any, operation_id: str, run_id: str, *, embedded_run_id: str | None) -> str:
        if embedded_run_id is not None:
            return operation_id
        legacy = db.execute(
            "SELECT run_id FROM host_agent_budget_ledger WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if legacy is not None and legacy["run_id"] == run_id:
            return operation_id
        return f"{operation_id}:{run_id}"

    async def reserve(self, request: BudgetReservationRequest) -> BudgetDecision:
        if request.upper_bound_amount is not None or not request.unknown_reason:
            from gw.agent_runtime.models import ErrorCode
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Host pricing is unavailable; only an explicit unknown-cost reservation is allowed.", request_id=request.operation_id.root)
        limit = self.max_model_calls_per_run
        embedded_run_id = self._run_id_for_operation(request.operation_id.root)
        active_run_id = self._active_run_id.get()
        if embedded_run_id is not None and active_run_id is not None and embedded_run_id != active_run_id:
            return self._decision(BudgetState.denied, "The model operation run identity does not match its dispatch context.")
        run_id = embedded_run_id or active_run_id
        if limit is None or run_id is None:
            return self._decision(BudgetState.denied, "A valid server-side per-run model call limit and run identity are required.")
        db = self.database.transaction()
        try:
            ledger_operation_id = self._ledger_operation_id(
                db, request.operation_id.root, run_id, embedded_run_id=embedded_run_id,
            )
            existing = db.execute(
                "SELECT run_id,state FROM host_agent_budget_ledger WHERE operation_id=?",
                (ledger_operation_id,),
            ).fetchone()
            if existing is not None and existing["run_id"] not in (None, run_id):
                self.database.close_rollback(db)
                return self._decision(BudgetState.denied, "The model operation is already bound to another run.")

            active_count = int(db.execute(
                "SELECT COUNT(*) FROM host_agent_budget_ledger "
                "WHERE state IN ('reserved_unknown','unknown') AND (run_id=? OR run_id IS NULL)",
                (run_id,),
            ).fetchone()[0])
            is_active = existing is not None and existing["state"] in self._ACTIVE_STATES
            if active_count > limit or (active_count >= limit and not is_active):
                self.database.close_rollback(db)
                return self._decision(BudgetState.denied, "The server-side per-run model call limit has been reached; monetary cost remains unknown.")

            now = utc_now().isoformat()
            db.execute(
                "INSERT INTO host_agent_budget_ledger(operation_id, run_id, state, reason, updated_at) "
                "VALUES(?, ?, 'reserved_unknown', ?, ?) "
                "ON CONFLICT(operation_id) DO UPDATE SET run_id=excluded.run_id, state='reserved_unknown', reason=excluded.reason, updated_at=excluded.updated_at",
                (ledger_operation_id, run_id, request.unknown_reason.root, now),
            )
            self.database.close_commit(db)
        except Exception:
            self.database.close_rollback(db)
            raise
        return self._decision(BudgetState.allowed, "Provider pricing is not configured; the monetary amount is unknown.")

    async def settle(self, settlement: BudgetSettlement) -> BudgetDecision:
        embedded_run_id = self._run_id_for_operation(settlement.operation_id.root)
        run_id = embedded_run_id or self._active_run_id.get()
        if run_id is None:
            ledger_operation_id = settlement.operation_id.root
            run_id = None
        else:
            ledger_operation_id = None
        db = self.database.transaction()
        try:
            if run_id is not None:
                ledger_operation_id = self._ledger_operation_id(
                    db, settlement.operation_id.root, run_id, embedded_run_id=embedded_run_id,
                )
            db.execute(
                "INSERT INTO host_agent_budget_ledger(operation_id, run_id, state, reason, updated_at) VALUES(?, ?, 'unknown', ?, ?) "
                "ON CONFLICT(operation_id) DO UPDATE SET run_id=COALESCE(host_agent_budget_ledger.run_id, excluded.run_id), state='unknown', reason=excluded.reason, updated_at=excluded.updated_at",
                (ledger_operation_id, run_id, settlement.unknown_reason.root if settlement.unknown_reason else "Monetary settlement is unavailable.", utc_now().isoformat()),
            )
            self.database.close_commit(db)
        except Exception:
            self.database.close_rollback(db)
            raise
        return self._decision(BudgetState.allowed, "Provider pricing is not configured; the monetary amount is unknown.")

    async def release_unspent(self, operation_id: Identifier) -> BudgetDecision:
        embedded_run_id = self._run_id_for_operation(operation_id.root)
        run_id = embedded_run_id or self._active_run_id.get()
        db = self.database.transaction()
        try:
            ledger_operation_id = (
                operation_id.root if run_id is None
                else self._ledger_operation_id(db, operation_id.root, run_id, embedded_run_id=embedded_run_id)
            )
            db.execute(
                "UPDATE host_agent_budget_ledger SET state='released_unknown', updated_at=? WHERE operation_id=?",
                (utc_now().isoformat(), ledger_operation_id),
            )
            self.database.close_commit(db)
        except Exception:
            self.database.close_rollback(db)
            raise
        return self._decision(BudgetState.allowed, "Provider pricing is not configured; the monetary amount is unknown.")


class _HostSafeInvocationDispatcher(SafeInvocationDispatcher):
    """Bind the active run for provider operations that carry placeholder IDs."""

    def __init__(self, repository: Any, gateway: Any, budget: UnknownCostBudget) -> None:
        super().__init__(repository, gateway, budget)
        self.host_budget = budget

    async def invoke(self, run_id: Identifier, request: ModelInvocationRequest, **kwargs: Any) -> ModelInvocationResult:
        token = self.host_budget.bind_run(run_id)
        try:
            return await super().invoke(run_id, request, **kwargs)
        finally:
            self.host_budget.reset_run(token)


class AgentIntegration:
    """Host-authenticated composition root for one process and trusted data root."""

    def __init__(
        self,
        *,
        database: SQLiteDatabase,
        service: Any,
        use_case: RuntimeAgentUseCase,
        projects: ProjectsService,
        gateway: Any,
        model_resolver: Callable[[str | None, str | None], Mapping[str, str]],
        configuration_provider: Callable[[], Mapping[str, Any]],
        max_model_calls_per_run: int | None = None,
        start_worker: bool = True,
    ) -> None:
        self.database = database
        self.service = service
        self.repository = service.repository
        self.use_case = use_case
        self.projects = projects
        self.gateway = gateway
        self.model_resolver = model_resolver
        self.configuration_provider = configuration_provider
        self.max_model_calls_per_run = max_model_calls_per_run
        self.start_worker_enabled = start_worker
        # Covers the durable run row and its host identity binding as one
        # single-process unit. The worker takes the same lock only while it
        # discovers/starts runs; it releases it before model I/O.
        self.run_lock = asyncio.Lock()
        self.worker = RuntimeBackgroundWorker(self)
        self._initialize_host_tables()
        self._load_profiles()

    def _initialize_host_tables(self) -> None:
        with self.database.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS host_agent_bindings (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    identity_domain TEXT NOT NULL,
                    auth_mode TEXT NOT NULL,
                    owner_key TEXT NOT NULL,
                    session_fingerprint TEXT NOT NULL CHECK(length(session_fingerprint)=64),
                    profile_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_host_agent_bindings_project ON host_agent_bindings(project_id, run_id);
                CREATE TABLE IF NOT EXISTS host_agent_profiles (
                    config_id TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS host_agent_create_keys (
                    project_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    identity_domain TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    run_id TEXT,
                    job_id TEXT,
                    PRIMARY KEY(project_id, actor_id, identity_domain, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS host_agent_budget_ledger (
                    operation_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            # Serialize migration inspection and ALTER/ID backfill so concurrent
            # processes cannot both observe the legacy schema and allocate the
            # same first sequence value or duplicate a column migration.
            db.execute("BEGIN IMMEDIATE")
            try:
                create_key_columns = {
                    row["name"] for row in db.execute("PRAGMA table_info(host_agent_create_keys)").fetchall()
                }
                if "job_id" not in create_key_columns:
                    db.execute("ALTER TABLE host_agent_create_keys ADD COLUMN job_id TEXT")
                existing_job_ids = [
                    str(row["job_id"])
                    for row in db.execute(
                        "SELECT job_id FROM host_agent_create_keys WHERE job_id IS NOT NULL"
                    ).fetchall()
                ]
                legacy_keys = db.execute(
                    "SELECT project_id,actor_id,identity_domain,idempotency_key "
                    "FROM host_agent_create_keys WHERE job_id IS NULL "
                    "ORDER BY project_id,actor_id,identity_domain,idempotency_key"
                ).fetchall()
                for row in legacy_keys:
                    job_id = next_sequence(AGENT_JOB_ID_PREFIX, iter(existing_job_ids))
                    db.execute(
                        "UPDATE host_agent_create_keys SET job_id=? "
                        "WHERE project_id=? AND actor_id=? AND identity_domain=? AND idempotency_key=? "
                        "AND job_id IS NULL",
                        (job_id, row["project_id"], row["actor_id"], row["identity_domain"], row["idempotency_key"]),
                    )
                    existing_job_ids.append(job_id)
                db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_host_agent_create_keys_job_id "
                    "ON host_agent_create_keys(job_id) WHERE job_id IS NOT NULL"
                )
                db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_host_agent_create_keys_run_id "
                    "ON host_agent_create_keys(run_id) WHERE run_id IS NOT NULL"
                )
                budget_columns = {
                    row["name"] for row in db.execute("PRAGMA table_info(host_agent_budget_ledger)").fetchall()
                }
                if "run_id" not in budget_columns:
                    db.execute("ALTER TABLE host_agent_budget_ledger ADD COLUMN run_id TEXT")
                # Backfill ledgers written before run_id became explicit. Any row
                # that cannot be attributed remains NULL and conservatively counts
                # against every run in reserve().
                legacy = db.execute(
                    "SELECT operation_id FROM host_agent_budget_ledger WHERE run_id IS NULL"
                ).fetchall()
                for row in legacy:
                    run_id = UnknownCostBudget._run_id_for_operation(row["operation_id"])
                    if run_id is None:
                        run_id = UnknownCostBudget._legacy_run_id_from_invocations(db, row["operation_id"])
                    if run_id is not None:
                        db.execute(
                            "UPDATE host_agent_budget_ledger SET run_id=? WHERE operation_id=? AND run_id IS NULL",
                            (run_id, row["operation_id"]),
                        )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS ix_host_agent_budget_run_state "
                    "ON host_agent_budget_ledger(run_id, state)"
                )
                db.commit()
            except Exception:
                db.rollback()
                raise

    def _load_profiles(self) -> None:
        with self.database.connect() as db:
            rows = db.execute("SELECT config_json FROM host_agent_profiles").fetchall()
        for row in rows:
            profile = ConfigSnapshot.model_validate_json(row["config_json"])
            self.use_case.profiles[profile.config_snapshot_id.root] = profile
            self.service.config_profiles[profile.config_snapshot_id.root] = profile

    async def start(self) -> None:
        if self.start_worker_enabled:
            await self.worker.start()

    async def stop(self) -> None:
        await self.worker.stop()

    def config_summary(self) -> dict[str, Any]:
        try:
            raw = dict(self.configuration_provider())
        except Exception:
            raw = {"configured": False, "configuration_status": "misconfigured", "providers": [], "default_provider_id": None}
        providers: list[dict[str, Any]] = []
        for provider in raw.get("providers", []) if isinstance(raw.get("providers"), list) else []:
            if not isinstance(provider, Mapping) or not provider.get("configured"):
                continue
            provider_id = provider.get("provider_id")
            models = provider.get("models", [])
            if not isinstance(provider_id, str) or not isinstance(models, list):
                continue
            allowed = []
            for model in models:
                if not isinstance(model, str):
                    continue
                try:
                    resolved = self.model_resolver(provider_id, model)
                    if resolved.get("provider_id") == provider_id and resolved.get("model") == model:
                        allowed.append(model)
                except Exception:
                    continue
            if allowed:
                providers.append({"provider_id": provider_id, "models": allowed})
        provider_ready = bool(providers)
        call_limit_ready = self.max_model_calls_per_run is not None
        ready = provider_ready and call_limit_ready
        default_provider_id = raw.get("default_provider_id")
        if default_provider_id not in {provider["provider_id"] for provider in providers}:
            default_provider_id = providers[0]["provider_id"] if len(providers) == 1 else None

        profiles: list[dict[str, str]] = []
        for provider in providers:
            for model in provider["models"]:
                for mode in (Mode.approval, Mode.automatic):
                    profile = self._new_content_addressed_profile(
                        mode=mode, provider_id=provider["provider_id"], model=model,
                    )
                    version = profile.config_snapshot_id.root
                    profiles.append({
                        "config_snapshot_id": version,
                        "version": version,
                        "provider_id": provider["provider_id"],
                        "model": model,
                        "mode": mode.value,
                    })
        return {
            "ready": ready,
            "configured": ready,
            "reason": None if ready else (
                "model_call_limit_missing_or_invalid" if not call_limit_ready
                else "provider_configuration_missing_or_invalid"
            ),
            "default_provider_id": default_provider_id,
            "providers": providers,
            "profiles": profiles,
            "capabilities": {
                "modes": [Mode.automatic.value, Mode.approval.value],
                "typed_messages": True,
                "durable_status": True,
                "manual_review": True,
                "revision_limit": 8,
                "rollback_limit": 3,
                "max_model_calls_per_run": self.max_model_calls_per_run,
                "cost_state": "unknown",
            },
        }

    def resolve_model(self, provider_id: str | None, model: str | None) -> tuple[str, str]:
        summary = self.config_summary()
        if not summary["ready"]:
            raise CleanroomException(503, "AGENT_NOT_INTEGRATED", "没有可用的服务端模型配置，任务未排队")
        resolved = self.model_resolver(provider_id, model)
        selected_provider = resolved.get("provider_id")
        selected_model = resolved.get("model")
        if not isinstance(selected_provider, str) or not isinstance(selected_model, str):
            raise CleanroomException(503, "AGENT_NOT_INTEGRATED", "服务端模型配置不可用，任务未排队")
        allowed = next((item for item in summary["providers"] if item["provider_id"] == selected_provider), None)
        if allowed is None or selected_model not in allowed["models"]:
            raise CleanroomException(400, "AGENT_MODEL_NOT_ALLOWED", "所选模型不属于服务端允许列表")
        return selected_provider, selected_model

    def get_binding(self, run_id: str) -> dict[str, str] | None:
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM host_agent_bindings WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _next_agent_job_id(db: Any) -> str:
        existing = (
            str(row["job_id"])
            for row in db.execute(
                "SELECT job_id FROM host_agent_create_keys WHERE job_id IS NOT NULL"
            ).fetchall()
        )
        return next_sequence(AGENT_JOB_ID_PREFIX, existing)

    def job_id_for_run(self, run_id: str) -> str:
        """Return the durable Workbench job ID associated with an Agent run."""
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT job_id FROM host_agent_create_keys WHERE run_id=? LIMIT 2",
                (run_id,),
            ).fetchall()
        if len(rows) != 1:
            raise CleanroomException(503, "AGENT_JOB_MAPPING_UNAVAILABLE", "任务稳定标识映射不可用")
        job_id = rows[0]["job_id"]
        if isinstance(job_id, str) and job_id.startswith(AGENT_JOB_ID_PREFIX):
            return job_id

        db = self.database.transaction()
        try:
            row = db.execute(
                "SELECT project_id,actor_id,identity_domain,idempotency_key,job_id "
                "FROM host_agent_create_keys WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise CleanroomException(503, "AGENT_JOB_MAPPING_UNAVAILABLE", "任务稳定标识映射不可用")
            job_id = row["job_id"]
            if not isinstance(job_id, str) or not job_id.startswith(AGENT_JOB_ID_PREFIX):
                job_id = self._next_agent_job_id(db)
                db.execute(
                    "UPDATE host_agent_create_keys SET job_id=? WHERE project_id=? AND actor_id=? "
                    "AND identity_domain=? AND idempotency_key=? AND job_id IS NULL",
                    (job_id, row["project_id"], row["actor_id"], row["identity_domain"], row["idempotency_key"]),
                )
                current = db.execute(
                    "SELECT job_id FROM host_agent_create_keys WHERE project_id=? AND actor_id=? "
                    "AND identity_domain=? AND idempotency_key=?",
                    (row["project_id"], row["actor_id"], row["identity_domain"], row["idempotency_key"]),
                ).fetchone()
                job_id = current["job_id"] if current is not None else None
            if not isinstance(job_id, str) or not job_id.startswith(AGENT_JOB_ID_PREFIX):
                raise CleanroomException(503, "AGENT_JOB_MAPPING_UNAVAILABLE", "任务稳定标识映射不可用")
            self.database.close_commit(db)
            return job_id
        except Exception:
            self.database.close_rollback(db)
            raise

    def get_agent_job_binding(self, job_id: str) -> dict[str, str | None] | None:
        """Read only the persisted external-ID mapping; callers must authorize before exposing it."""
        with self.database.connect() as db:
            row = db.execute(
                "SELECT project_id,actor_id,identity_domain,run_id FROM host_agent_create_keys WHERE job_id=?",
                (job_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def _write_binding(self, *, run_id: str, project_id: str, auth: AuthContext, fingerprint: str, profile_id: str) -> None:
        if not auth.subject or not auth.identity_domain:
            raise UnauthorizedException()
        owner_key = owner_key_for_context(auth)
        with self.database.connect() as db:
            old = db.execute("SELECT actor_id,identity_domain,project_id FROM host_agent_bindings WHERE run_id=?", (run_id,)).fetchone()
            if old and (old["actor_id"] != auth.subject or old["identity_domain"] != auth.identity_domain or old["project_id"] != project_id):
                raise ForbiddenException("当前身份不能接管该任务")
            db.execute(
                "INSERT INTO host_agent_bindings(run_id,project_id,actor_id,identity_domain,auth_mode,owner_key,session_fingerprint,profile_id) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET session_fingerprint=excluded.session_fingerprint",
                (run_id, project_id, auth.subject, auth.identity_domain, auth.mode, owner_key, fingerprint, profile_id),
            )

    async def authenticate_request(self, auth: AuthContext) -> tuple[dict[str, Any], str]:
        principal = session_store.get_current_principal()
        if auth.mode not in {AUTH_MODE_LOCAL_ACCOUNT, AUTH_MODE_OIDC} or not isinstance(principal, Mapping):
            raise UnauthorizedException("智能体任务仅接受当前有效的浏览器会话")
        session_id = principal.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise UnauthorizedException("当前认证主体没有有效Cookie会话")
        fingerprint = _token_fingerprint(session_id)
        live = self._live_principal(auth.mode, auth.subject or "", auth.identity_domain or "", fingerprint)
        if live is None:
            raise UnauthorizedException("Cookie会话已撤销或不再有效")
        return dict(live), fingerprint

    def _live_principal(self, auth_mode: str, actor_id: str, identity_domain: str, fingerprint: str) -> dict[str, Any] | None:
        if auth_mode == AUTH_MODE_LOCAL_ACCOUNT:
            if identity_domain != "local_account":
                return None
            current = local_accounts.get_session_by_fingerprint(actor_id, fingerprint)
            return current if current and current.get("user_id") == actor_id else None
        if auth_mode == AUTH_MODE_OIDC:
            current = session_store.get_session_by_fingerprint(fingerprint, actor_id)
            if not current:
                return None
            runtime = load_runtime_auth_config()
            if oidc_session_trust_failure_reason(current, runtime) is not None:
                return None
            if identity_domain != f"oidc:{runtime.oidc.issuer}":
                return None
            return current
        return None

    def _verify_auth_for_project(self, auth: AuthContext, fingerprint: str, project_id: str, *, edit_required: bool = True) -> dict[str, Any]:
        if not auth.subject or not auth.identity_domain:
            raise UnauthorizedException()
        principal = self._live_principal(auth.mode, auth.subject, auth.identity_domain, fingerprint)
        allowed_roles = EDIT_ROLES if edit_required else KNOWN_ROLES
        if principal is None or principal.get("role") not in allowed_roles:
            raise ForbiddenException("会话已失去智能体任务访问权限")
        try:
            project = self.projects.get_owned_project(project_id, owner_key_for_context(auth))
        except CleanroomException:
            raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在") from None
        if project.deleted_at is not None:
            raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在")
        if edit_required and project.archived_at is not None:
            raise ForbiddenException("项目已归档，智能体任务只读")
        return principal

    async def request_context(self, auth: AuthContext, principal: Mapping[str, Any], fingerprint: str, project_id: str, request_id: str, *, edit_required: bool = True) -> Any:
        live = self._verify_auth_for_project(auth, fingerprint, project_id, edit_required=edit_required)
        current_auth = AuthContext(
            role=str(live["role"]), subject=auth.subject, mode=auth.mode,
            identity_domain=auth.identity_domain, display_name=auth.display_name,
        )
        from gw.agent_runtime.models import ActorContext, RequestContext
        return RequestContext(
            contract_version="1",
            request_id=Identifier(request_id),
            trace_id=Identifier(request_id),
            project_id=Identifier(project_id),
            actor=ActorContext(actor_id=Identifier(auth.subject or ""), authorization_version=self._encoded_authorization(current_auth)),
        )

    @staticmethod
    def _encoded_authorization(auth: AuthContext) -> Identifier:
        from gw.agent_runtime.application import encode_auth_context
        return encode_auth_context(auth)

    async def context_for_run(self, auth: AuthContext, fingerprint: str, run_id: str, request_id: str, *, edit_required: bool = True) -> Any:
        binding = self.get_binding(run_id)
        if binding is None or binding["actor_id"] != auth.subject or binding["identity_domain"] != auth.identity_domain:
            from gw.agent_runtime.models import ErrorCode
            raise DomainError(ErrorCode.PROJECT_UNAVAILABLE, "任务不存在或当前主体不可访问", request_id=request_id)
        if binding["auth_mode"] != auth.mode:
            from gw.agent_runtime.models import ErrorCode
            raise DomainError(ErrorCode.PROJECT_UNAVAILABLE, "任务不存在或当前主体不可访问", request_id=request_id)
        self._verify_auth_for_project(auth, fingerprint, binding["project_id"], edit_required=edit_required)
        if edit_required:
            self._write_binding(
                run_id=run_id,
                project_id=binding["project_id"],
                auth=auth,
                fingerprint=fingerprint,
                profile_id=binding["profile_id"],
            )
        return await self.request_context(auth, {}, fingerprint, binding["project_id"], request_id, edit_required=edit_required)

    async def authorize_background(self, project_id: Identifier, actor_id: Identifier, run_id: Identifier | None = None) -> bool:
        if run_id is None:
            return False
        binding = self.get_binding(run_id.root)
        if (binding is None or binding["project_id"] != project_id.root
                or binding["actor_id"] != actor_id.root):
            return False
        live = self._live_principal(
            binding["auth_mode"], binding["actor_id"], binding["identity_domain"],
            binding["session_fingerprint"],
        )
        if live is None or live.get("role") not in EDIT_ROLES:
            return False
        auth = AuthContext(
            role=str(live.get("role")), subject=binding["actor_id"], mode=binding["auth_mode"],
            identity_domain=binding["identity_domain"],
        )
        try:
            project = self.projects.get_owned_project(project_id.root, owner_key_for_context(auth))
        except CleanroomException:
            return False
        return project.deleted_at is None and project.archived_at is None

    def _new_profile(self, *, mode: Mode, provider_id: str, model: str, profile_id: str) -> ConfigSnapshot:
        from gw.agent_episode.prompts import ORCHESTRATOR_TEMPLATE, PROMPT_CATALOG
        prompt_refs = {item.ref.template_id.root + "@" + item.ref.version.root: item.ref for item in PROMPT_CATALOG.values()}
        prompt_refs[ORCHESTRATOR_TEMPLATE.ref.template_id.root + "@" + ORCHESTRATOR_TEMPLATE.ref.version.root] = ORCHESTRATOR_TEMPLATE.ref
        return ConfigSnapshot(
            schema_version=1,
            config_snapshot_id=Identifier(profile_id),
            mode=mode,
            scoring_rule_version=Identifier(SCORING_RULE_VERSION),
            revision_limit=8,
            rollback_limit=3,
            pass_score=9.0,
            stage_score_weights={
                stage.value: {name: float(value) for name, value in weights.items()}
                for stage, weights in STAGE_WEIGHTS.items()
            },
            model_ref=_model_ref(provider_id, model),
            prompt_refs=list(prompt_refs.values()),
            call_limits=CallLimits(
                max_model_retries=0,
                max_format_repairs=0,
                timeout_seconds=30,
                max_output_tokens=4096,
            ),
            effective_from_version=Version(0),
            created_at=utc_now(),
        )

    def _new_content_addressed_profile(self, *, mode: Mode, provider_id: str, model: str) -> ConfigSnapshot:
        draft = self._new_profile(mode=mode, provider_id=provider_id, model=model, profile_id="cfg:pending")
        content = draft.model_dump(mode="json", exclude={"config_snapshot_id", "created_at"})
        canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        profile_id = "cfg:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return draft.model_copy(update={"config_snapshot_id": Identifier(profile_id)})

    def persist_current_profile(
        self,
        profile_id: str,
        *,
        summary: Mapping[str, Any] | None = None,
    ) -> ConfigSnapshot | None:
        current = summary if summary is not None else self.config_summary()
        option = next((
            item for item in current.get("profiles", [])
            if isinstance(item, Mapping) and item.get("config_snapshot_id") == profile_id
        ), None)
        if option is None:
            return None
        try:
            profile = self._new_content_addressed_profile(
                mode=Mode(option["mode"]),
                provider_id=option["provider_id"],
                model=option["model"],
            )
        except (KeyError, TypeError, ValueError):
            return None
        if profile.config_snapshot_id.root != profile_id:
            return None
        return self.persist_profile(profile)

    def persist_profile(self, profile: ConfigSnapshot) -> ConfigSnapshot:
        encoded = profile.model_dump_json()
        with self.database.connect() as db:
            db.execute("INSERT INTO host_agent_profiles(config_id,config_json) VALUES(?,?) ON CONFLICT(config_id) DO NOTHING", (profile.config_snapshot_id.root, encoded))
            row = db.execute("SELECT config_json FROM host_agent_profiles WHERE config_id=?", (profile.config_snapshot_id.root,)).fetchone()
        stored = ConfigSnapshot.model_validate_json(row["config_json"])
        self.use_case.profiles[stored.config_snapshot_id.root] = stored
        self.service.config_profiles[stored.config_snapshot_id.root] = stored
        return stored

    async def read_configuration_replay(
        self,
        *,
        run_id: str,
        expected_version: int,
        idempotency_key: str,
        config_snapshot_id: str,
    ) -> Any | None:
        """Read only an exact prior apply-config result for an expired profile."""
        profile = self._load_profile(config_snapshot_id)
        if profile is None:
            return None
        from gw.agent_runtime.repository import _fingerprint
        request_fingerprint = _fingerprint({
            "op": "apply-config",
            "run_id": run_id,
            "expected_version": expected_version,
            "profile": profile.model_dump(mode="json"),
        })
        return await self.repository.read_idempotent_result(
            Identifier(run_id), Identifier(idempotency_key), request_fingerprint,
        )

    def _load_profile(self, profile_id: str) -> ConfigSnapshot | None:
        profile = self.use_case.profiles.get(profile_id) or self.service.config_profiles.get(profile_id)
        if profile is None:
            with self.database.connect() as db:
                row = db.execute(
                    "SELECT config_json FROM host_agent_profiles WHERE config_id=?",
                    (profile_id,),
                ).fetchone()
            if row is None:
                return None
            profile = ConfigSnapshot.model_validate_json(row["config_json"])
        if profile.config_snapshot_id.root != profile_id:
            return None
        self.use_case.profiles[profile_id] = profile
        self.service.config_profiles[profile_id] = profile
        return profile

    @staticmethod
    def _create_request_fingerprint(
        *, project_id: str, user_goal: str, provider_id: str, model: str,
        mode: Mode, config_snapshot_id: str, input_artifact_refs: list[str],
        auth: AuthContext,
    ) -> str:
        canonical = {
            "project_id": project_id,
            "user_goal": user_goal,
            "provider_id": provider_id,
            "model": model,
            "mode": mode.value,
            "config_snapshot_id": config_snapshot_id,
            "input_artifact_refs": input_artifact_refs,
            "actor_id": auth.subject,
            "identity_domain": auth.identity_domain,
        }
        return hashlib.sha256(json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()

    @staticmethod
    def _legacy_create_request_fingerprint(
        *, project_id: str, user_goal: str, provider_id: str, model: str,
        mode: Mode, auth: AuthContext,
    ) -> str:
        """Fingerprint format written before profile and artifact refs were included."""
        canonical = {
            "project_id": project_id,
            "user_goal": user_goal,
            "provider_id": provider_id,
            "model": model,
            "mode": mode.value,
            "actor_id": auth.subject,
            "identity_domain": auth.identity_domain,
        }
        return hashlib.sha256(json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()

    async def create_run(self, auth: AuthContext, fingerprint: str, *, project_id: str, user_goal: str, provider_id: str | None, model: str | None, mode: str, config_snapshot_id: str, input_artifact_refs: list[str], idempotency_key: str, request_id: str) -> Any:
        async with self.run_lock:
            return await self._create_run_locked(
                auth,
                fingerprint,
                project_id=project_id,
                user_goal=user_goal,
                provider_id=provider_id,
                model=model,
                mode=mode,
                config_snapshot_id=config_snapshot_id,
                input_artifact_refs=input_artifact_refs,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )

    async def _create_run_locked(self, auth: AuthContext, fingerprint: str, *, project_id: str, user_goal: str, provider_id: str | None, model: str | None, mode: str, config_snapshot_id: str, input_artifact_refs: list[str], idempotency_key: str, request_id: str) -> Any:
        self._verify_auth_for_project(auth, fingerprint, project_id)
        if input_artifact_refs:
            raise CleanroomException(
                400,
                "AGENT_INPUT_ARTIFACT_REFS_UNSUPPORTED",
                "输入产物引用尚未接入同项目权限校验与消费链路；请传空列表",
            )
        try:
            selected_mode = Mode(mode)
        except ValueError:
            raise CleanroomException(400, "INVALID_REQUEST", "任务模式不合法") from None

        key = (project_id, auth.subject, auth.identity_domain, idempotency_key)
        with self.database.connect() as db:
            prior = db.execute(
                "SELECT request_fingerprint,request_json,run_id FROM host_agent_create_keys WHERE project_id=? AND actor_id=? AND identity_domain=? AND idempotency_key=?",
                key,
            ).fetchone()

        from gw.agent_runtime.models import CreateRunRequest, NonEmptyText
        if prior is not None:
            request_model = CreateRunRequest.model_validate_json(prior["request_json"])
            profile_id = request_model.config_snapshot_id.root
            profile = self._load_profile(profile_id)
            if profile is None:
                raise CleanroomException(503, "AGENT_NOT_INTEGRATED", "已受理任务的配置快照不可恢复，未重复排队")
            try:
                selected_provider, selected_model = _decode_model_ref(profile.model_ref)
            except (ValueError, TypeError, json.JSONDecodeError):
                raise CleanroomException(503, "AGENT_NOT_INTEGRATED", "已受理任务的配置快照不可恢复，未重复排队") from None
            if (
                request_model.project_id.root != project_id
                or request_model.user_goal.root != user_goal
                or request_model.mode is not selected_mode
                or request_model.config_snapshot_id.root != config_snapshot_id
                or request_model.idempotency_key.root != idempotency_key
                or request_model.input_artifact_refs
                or profile.mode is not selected_mode
                or (provider_id is not None and provider_id != selected_provider)
                or (model is not None and model != selected_model)
            ):
                from gw.agent_runtime.models import ErrorCode
                raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "创建幂等键已用于不同请求", request_id=request_id)
            request_fingerprint = self._create_request_fingerprint(
                project_id=request_model.project_id.root,
                user_goal=request_model.user_goal.root,
                provider_id=selected_provider,
                model=selected_model,
                mode=request_model.mode,
                config_snapshot_id=profile_id,
                input_artifact_refs=[],
                auth=auth,
            )
            legacy_fingerprint = self._legacy_create_request_fingerprint(
                project_id=request_model.project_id.root,
                user_goal=request_model.user_goal.root,
                provider_id=selected_provider,
                model=selected_model,
                mode=request_model.mode,
                auth=auth,
            )
            if prior["request_fingerprint"] not in {request_fingerprint, legacy_fingerprint}:
                from gw.agent_runtime.models import ErrorCode
                raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "创建幂等键已用于不同请求", request_id=request_id)
        else:
            summary = self.config_summary()
            if not summary["ready"]:
                raise CleanroomException(503, "AGENT_NOT_INTEGRATED", "没有可用的服务端模型配置，任务未排队")
            selected_profile = next((
                item for item in summary["profiles"]
                if item["config_snapshot_id"] == config_snapshot_id
            ), None)
            if selected_profile is None:
                raise CleanroomException(400, "AGENT_CONFIG_PROFILE_UNAVAILABLE", "配置快照未知或已过期，任务未排队")
            profile = self.persist_current_profile(config_snapshot_id, summary=summary)
            if profile is None:
                raise CleanroomException(400, "AGENT_CONFIG_PROFILE_UNAVAILABLE", "配置快照未知或已过期，任务未排队")
            selected_provider = selected_profile["provider_id"]
            selected_model = selected_profile["model"]
            if (selected_profile["mode"] != selected_mode.value
                    or (provider_id is not None and provider_id != selected_provider)
                    or (model is not None and model != selected_model)):
                raise CleanroomException(400, "AGENT_CONFIG_PROFILE_MISMATCH", "所选配置快照与 provider、model 或 mode 不一致")

            request_fingerprint = self._create_request_fingerprint(
                project_id=project_id,
                user_goal=user_goal,
                provider_id=selected_provider,
                model=selected_model,
                mode=selected_mode,
                config_snapshot_id=config_snapshot_id,
                input_artifact_refs=input_artifact_refs,
                auth=auth,
            )
            request_model = CreateRunRequest(
                contract_version="1",
                request_id=Identifier(request_id),
                project_id=Identifier(project_id),
                user_goal=NonEmptyText(user_goal),
                input_artifact_refs=input_artifact_refs,
                mode=selected_mode,
                idempotency_key=Identifier(idempotency_key),
                config_snapshot_id=Identifier(config_snapshot_id),
            )
            db = self.database.transaction()
            try:
                reserved_job_id = self._next_agent_job_id(db)
                db.execute(
                    "INSERT INTO host_agent_create_keys(project_id,actor_id,identity_domain,idempotency_key,request_fingerprint,request_json,run_id,job_id) "
                    "VALUES(?,?,?,?,?,?,NULL,?) ON CONFLICT DO NOTHING",
                    (*key, request_fingerprint, request_model.model_dump_json(), reserved_job_id),
                )
                current = db.execute(
                    "SELECT request_fingerprint,request_json,job_id FROM host_agent_create_keys "
                    "WHERE project_id=? AND actor_id=? AND identity_domain=? AND idempotency_key=?",
                    key,
                ).fetchone()
                self.database.close_commit(db)
            except Exception:
                self.database.close_rollback(db)
                raise
            if current is None or not isinstance(current["job_id"], str):
                raise CleanroomException(503, "AGENT_JOB_MAPPING_UNAVAILABLE", "任务稳定标识映射不可用")
            if current["request_fingerprint"] != request_fingerprint:
                from gw.agent_runtime.models import ErrorCode
                raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "创建幂等键已用于不同请求", request_id=request_id)
            request_model = CreateRunRequest.model_validate_json(current["request_json"])
            profile_id = request_model.config_snapshot_id.root
            if self._load_profile(profile_id) is None:
                raise CleanroomException(503, "AGENT_NOT_INTEGRATED", "服务端配置快照不可恢复，任务未排队")

        run = await self.service.create_run(request_model)
        self._write_binding(
            run_id=run.run_id.root,
            project_id=project_id,
            auth=auth,
            fingerprint=fingerprint,
            profile_id=profile_id,
        )
        db = self.database.transaction()
        try:
            db.execute(
                "UPDATE host_agent_create_keys SET run_id=? WHERE project_id=? AND actor_id=? "
                "AND identity_domain=? AND idempotency_key=? AND (run_id IS NULL OR run_id=?)",
                (run.run_id.root, *key, run.run_id.root),
            )
            current = db.execute(
                "SELECT run_id,job_id FROM host_agent_create_keys WHERE project_id=? AND actor_id=? "
                "AND identity_domain=? AND idempotency_key=?",
                key,
            ).fetchone()
            if current is None or current["run_id"] != run.run_id.root or not current["job_id"]:
                raise CleanroomException(503, "AGENT_JOB_MAPPING_UNAVAILABLE", "任务稳定标识映射不可用")
            self.database.close_commit(db)
        except Exception:
            self.database.close_rollback(db)
            raise
        return run

    def cost_ledger(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            calls = db.execute("SELECT COUNT(*) AS count FROM invocations WHERE run_id=?", (run_id,)).fetchone()["count"]
            # The runtime uses distinct operation and invocation idempotency
            # keys. Every recorded invocation is therefore conservatively
            # surfaced as unknown-cost rather than undercounted by a false join.
        return {"state": "unknown", "recorded_model_calls": calls, "unknown_cost_operations": calls}


def build_agent_integration(
    *,
    gateway: Any | None = None,
    database: SQLiteDatabase | None = None,
    runtime_paths: RuntimePaths | None = None,
    projects: ProjectsService | None = None,
    model_resolver: Callable[[str | None, str | None], Mapping[str, str]] | None = None,
    configuration_provider: Callable[[], Mapping[str, Any]] | None = None,
    start_worker: bool = True,
) -> AgentIntegration:
    """Build the same-process service; tests inject a fake gateway and temp roots."""
    paths = runtime_paths or resolve_runtime_paths()
    runtime_db = database or SQLiteDatabase(Path("agent/runtime.sqlite3"), trusted_root=paths.data_root)
    repo = SQLiteRunRepository(runtime_db)
    artifacts = SQLiteArtifactRepository(runtime_db)
    checkpoints = SQLiteCheckpointBackend(runtime_db)
    model_gateway = gateway or HostChatModelGateway()
    bridge = EpisodeRuntimeBridge(artifacts, repo, model_gateway)

    async def capability_authorizer(_actor_id: Identifier, _permissions: Any) -> bool:
        return True

    registry = CapabilityRegistry(authorizer=capability_authorizer)
    registry.register_role(RoleRegistration(name="writer", handler=bridge.write, stage_writer=True))
    registry.register_role(RoleRegistration(name="reviewer", handler=bridge.review))
    registry.register_role(RoleRegistration(name="orchestrator", handler=bridge.orchestrate, proposal_safe=True))
    project_service = projects or projects_service_module.default_projects_service
    integration_ref: dict[str, AgentIntegration] = {}

    async def execution_authorizer(project_id: Identifier, actor_id: Identifier, run_id: Identifier | None = None) -> bool:
        integration = integration_ref.get("value")
        return bool(integration and await integration.authorize_background(project_id, actor_id, run_id))

    policy = RuntimePolicy(
        default_stage_score_weights={
            stage.value: weights for stage, weights in STAGE_WEIGHTS.items()
        },
        registered_rule_version=Identifier(SCORING_RULE_VERSION),
    )
    model_call_limit = _configured_model_call_limit()
    budget = UnknownCostBudget(runtime_db, model_call_limit)
    dispatcher = _HostSafeInvocationDispatcher(repo, bridge, budget)
    from gw.agent_episode.exports import EpisodeArtifactExporter
    from gw.agent_runtime.service import AgentRuntimeService
    service = AgentRuntimeService(
        repo,
        artifacts,
        registry,
        policy=policy,
        checkpoints=checkpoints,
        invocation_dispatcher=dispatcher,
        artifact_exporter=EpisodeArtifactExporter(),
        orchestrator_role="orchestrator",
        execution_authorizer=execution_authorizer,
    )
    identity = HostIdentityProjectAdapter(project_service)
    use_case = RuntimeAgentUseCase(service, identity, service.config_profiles)
    integration = AgentIntegration(
        database=runtime_db,
        service=service,
        use_case=use_case,
        projects=project_service,
        gateway=model_gateway,
        model_resolver=model_resolver or chat_runtime.resolve_agent_model,
        configuration_provider=configuration_provider or chat_runtime.public_configuration,
        max_model_calls_per_run=model_call_limit,
        start_worker=start_worker,
    )
    integration_ref["value"] = integration
    return integration


async def start_agent_integration(app: Any) -> AgentIntegration:
    factory = getattr(app.state, "agent_integration_factory", None)
    integration = build_agent_integration() if factory is None else (factory() if callable(factory) else factory)
    if inspect.isawaitable(integration):
        integration = await integration
    if not isinstance(integration, AgentIntegration):
        raise TypeError("agent_integration_factory must return AgentIntegration")
    app.state.agent_integration = integration
    await integration.start()
    return integration


async def stop_agent_integration(app: Any) -> None:
    integration = getattr(app.state, "agent_integration", None)
    if integration is not None:
        await integration.stop()


__all__ = [
    "AgentIntegration",
    "HostChatModelGateway",
    "UnknownCostBudget",
    "build_agent_integration",
    "start_agent_integration",
    "stop_agent_integration",
]
