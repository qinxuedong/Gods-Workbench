"""Same-process, Cookie-authenticated host composition for durable AgentRuns."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
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
from gw.projects_hub import service as projects_service_module
from gw.projects_hub.service import ProjectsService, owner_key_for_context
from gw.settings import chat as chat_runtime


def _token_fingerprint(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _model_ref(provider_id: str, model: str) -> Identifier:
    raw = json.dumps([provider_id, model], ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return Identifier("gw-chat:" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="))


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
    """Fail on malformed bounds and record every model operation as unknown-cost."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    async def reserve(self, request: BudgetReservationRequest) -> BudgetDecision:
        if request.upper_bound_amount is not None or not request.unknown_reason:
            from gw.agent_runtime.models import ErrorCode
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Host pricing is unavailable; only an explicit unknown-cost reservation is allowed.", request_id=request.operation_id.root)
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO host_agent_budget_ledger(operation_id, state, reason, updated_at) VALUES(?, 'reserved_unknown', ?, ?) "
                "ON CONFLICT(operation_id) DO NOTHING",
                (request.operation_id.root, request.unknown_reason.root, utc_now().isoformat()),
            )
        return BudgetDecision(
            state=BudgetState.allowed,
            amount=None,
            currency=None,
            reason=Reason("Provider pricing is not configured; the monetary amount is unknown."),
        )

    async def settle(self, settlement: BudgetSettlement) -> BudgetDecision:
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO host_agent_budget_ledger(operation_id, state, reason, updated_at) VALUES(?, 'unknown', ?, ?) "
                "ON CONFLICT(operation_id) DO UPDATE SET state='unknown', reason=excluded.reason, updated_at=excluded.updated_at",
                (settlement.operation_id.root, settlement.unknown_reason.root if settlement.unknown_reason else "Monetary settlement is unavailable.", utc_now().isoformat()),
            )
        return BudgetDecision(
            state=BudgetState.allowed,
            amount=None,
            currency=None,
            reason=Reason("Provider pricing is not configured; the monetary amount is unknown."),
        )

    async def release_unspent(self, operation_id: Identifier) -> BudgetDecision:
        with self.database.connect() as db:
            db.execute(
                "UPDATE host_agent_budget_ledger SET state='released_unknown', updated_at=? WHERE operation_id=?",
                (utc_now().isoformat(), operation_id.root),
            )
        return BudgetDecision(
            state=BudgetState.allowed,
            amount=None,
            currency=None,
            reason=Reason("Provider pricing is not configured; the monetary amount is unknown."),
        )


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
                    PRIMARY KEY(project_id, actor_id, identity_domain, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS host_agent_budget_ledger (
                    operation_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

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
        ready = bool(providers)
        default_provider_id = raw.get("default_provider_id")
        if default_provider_id not in {provider["provider_id"] for provider in providers}:
            default_provider_id = providers[0]["provider_id"] if len(providers) == 1 else None
        return {
            "ready": ready,
            "configured": ready,
            "reason": None if ready else "provider_configuration_missing_or_invalid",
            "default_provider_id": default_provider_id,
            "providers": providers,
            "capabilities": {
                "modes": [Mode.automatic.value, Mode.approval.value],
                "typed_messages": True,
                "durable_status": True,
                "manual_review": True,
                "revision_limit": 8,
                "rollback_limit": 3,
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

    def persist_profile(self, profile: ConfigSnapshot) -> None:
        encoded = profile.model_dump_json()
        with self.database.connect() as db:
            db.execute("INSERT INTO host_agent_profiles(config_id,config_json) VALUES(?,?) ON CONFLICT(config_id) DO NOTHING", (profile.config_snapshot_id.root, encoded))
            row = db.execute("SELECT config_json FROM host_agent_profiles WHERE config_id=?", (profile.config_snapshot_id.root,)).fetchone()
        stored = ConfigSnapshot.model_validate_json(row["config_json"])
        self.use_case.profiles[stored.config_snapshot_id.root] = stored
        self.service.config_profiles[stored.config_snapshot_id.root] = stored

    async def create_run(self, auth: AuthContext, fingerprint: str, *, project_id: str, user_goal: str, provider_id: str | None, model: str | None, mode: str, idempotency_key: str, request_id: str) -> Any:
        async with self.run_lock:
            return await self._create_run_locked(
                auth,
                fingerprint,
                project_id=project_id,
                user_goal=user_goal,
                provider_id=provider_id,
                model=model,
                mode=mode,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )

    async def _create_run_locked(self, auth: AuthContext, fingerprint: str, *, project_id: str, user_goal: str, provider_id: str | None, model: str | None, mode: str, idempotency_key: str, request_id: str) -> Any:
        self._verify_auth_for_project(auth, fingerprint, project_id)
        try:
            selected_provider, selected_model = self.resolve_model(provider_id, model)
            selected_mode = Mode(mode)
        except CleanroomException:
            raise
        except ValueError:
            raise CleanroomException(400, "INVALID_REQUEST", "任务模式不合法") from None
        except Exception:
            raise CleanroomException(503, "AGENT_NOT_INTEGRATED", "服务端模型配置不可用，任务未排队") from None

        canonical = {
            "project_id": project_id,
            "user_goal": user_goal,
            "provider_id": selected_provider,
            "model": selected_model,
            "mode": selected_mode.value,
            "actor_id": auth.subject,
            "identity_domain": auth.identity_domain,
        }
        request_fingerprint = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        key = (project_id, auth.subject, auth.identity_domain, idempotency_key)
        with self.database.connect() as db:
            prior = db.execute(
                "SELECT request_fingerprint,request_json,run_id FROM host_agent_create_keys WHERE project_id=? AND actor_id=? AND identity_domain=? AND idempotency_key=?",
                key,
            ).fetchone()
        if prior and prior["request_fingerprint"] != request_fingerprint:
            from gw.agent_runtime.models import ErrorCode
            raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "创建幂等键已用于不同请求", request_id=request_id)
        if prior:
            request_model = __import__("gw.agent_runtime.models", fromlist=["CreateRunRequest"]).CreateRunRequest.model_validate_json(prior["request_json"])
            profile_id = request_model.config_snapshot_id.root
        else:
            profile_id = "cfg:" + hashlib.sha256(("\0".join(key) + "\0" + request_fingerprint).encode("utf-8")).hexdigest()[:48]
            profile = self._new_profile(mode=selected_mode, provider_id=selected_provider, model=selected_model, profile_id=profile_id)
            self.persist_profile(profile)
            from gw.agent_runtime.models import CreateRunRequest, NonEmptyText
            request_model = CreateRunRequest(
                contract_version="1",
                request_id=Identifier(request_id),
                project_id=Identifier(project_id),
                user_goal=NonEmptyText(user_goal),
                input_artifact_refs=[],
                mode=selected_mode,
                idempotency_key=Identifier(idempotency_key),
                config_snapshot_id=Identifier(profile_id),
            )
            with self.database.connect() as db:
                db.execute(
                    "INSERT INTO host_agent_create_keys(project_id,actor_id,identity_domain,idempotency_key,request_fingerprint,request_json,run_id) VALUES(?,?,?,?,?,?,NULL) ON CONFLICT DO NOTHING",
                    (*key, request_fingerprint, request_model.model_dump_json()),
                )
                current = db.execute(
                    "SELECT request_fingerprint,request_json FROM host_agent_create_keys WHERE project_id=? AND actor_id=? AND identity_domain=? AND idempotency_key=?",
                    key,
                ).fetchone()
            if current["request_fingerprint"] != request_fingerprint:
                from gw.agent_runtime.models import ErrorCode
                raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "创建幂等键已用于不同请求", request_id=request_id)
            request_model = __import__("gw.agent_runtime.models", fromlist=["CreateRunRequest"]).CreateRunRequest.model_validate_json(current["request_json"])
            profile_id = request_model.config_snapshot_id.root

        self.persist_profile(self.use_case.profiles.get(profile_id) or self.service.config_profiles[profile_id])
        run = await self.service.create_run(request_model)
        self._write_binding(
            run_id=run.run_id.root,
            project_id=project_id,
            auth=auth,
            fingerprint=fingerprint,
            profile_id=profile_id,
        )
        with self.database.connect() as db:
            db.execute(
                "UPDATE host_agent_create_keys SET run_id=? WHERE project_id=? AND actor_id=? AND identity_domain=? AND idempotency_key=?",
                (run.run_id.root, *key),
            )
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
    dispatcher = SafeInvocationDispatcher(repo, bridge, UnknownCostBudget(runtime_db))
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
