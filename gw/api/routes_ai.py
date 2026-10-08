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

"""Phase 11 B2 平台、AI 与 CLI 路由。

路由覆盖前端冻结调用面。Phase 12 A4 起：上传真实落盘、CLI 配置门禁下真实执行、
对话在配置了真实 base_url + 凭据时向真实 Provider 发起调用；
无配置/无依赖时仍 fail-closed，绝不伪造结果。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Literal, Mapping
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Header, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from gw.core import cli_runtime
from gw.core.errors import CleanroomException
from gw.core.auth import require_authenticated, require_edit_access, require_governance_access
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    ApprovalDecision,
    ApprovalRecord,
    ClarificationAnswerRequest,
    ErrorCode,
    Identifier,
    ReviewDecisionRequest,
    StageId,
    TextExportFormat,
    Version,
    VersionedMutation,
)
from gw.core.platform import (
    app_info,
    cli_status,
)
from gw.settings import chat as chat_runtime

@asynccontextmanager
async def _platform_ai_lifespan(app):
    """应用关闭时回收单进程控制器明确持有的 Dreamina 子进程。"""
    try:
        yield
    finally:
        cli_runtime.login_controller.shutdown()


router = APIRouter(tags=["platform-ai"], lifespan=_platform_ai_lifespan)


class ChatRequest(BaseModel):
    """兼容工作台与剧集流水线的最小对话请求。"""

    model_config = ConfigDict(extra="allow")

    message: str = Field(..., min_length=1, max_length=200_000)
    mode: Optional[str] = Field(None, max_length=64)
    model: Optional[str] = Field(None, max_length=256)
    provider_id: Optional[str] = Field(None, max_length=128)
    provider: Optional[str] = Field(None, max_length=128)


class CliHelpRequest(BaseModel):
    """CLI 帮助请求；命令文本只接受为数据，路由绝不执行。"""

    model_config = ConfigDict(extra="ignore")

    command: str = Field("", max_length=2_000)


class AgentCreateRunRequest(BaseModel):
    """浏览器Cookie身份下创建一个持久Agent运行。"""

    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(..., min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$")
    user_goal: str = Field(..., min_length=1, max_length=200_000)
    provider_id: Optional[str] = Field(None, min_length=1, max_length=128)
    model: Optional[str] = Field(None, min_length=1, max_length=256)
    mode: Literal["approval", "automatic"] = "approval"
    idempotency_key: str = Field(..., min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    request_id: str = Field(..., min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
    config_snapshot_id: str = Field(..., min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$")
    input_artifact_refs: list[str] = Field(..., max_length=128)

    @field_validator("user_goal")
    @classmethod
    def validate_user_goal(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("创作目标不能为空")
        return value


class AgentMutationRequest(BaseModel):
    """运行状态CAS变更；客户端必须提交当前已读取的版本与幂等标识。"""

    model_config = ConfigDict(extra="forbid")
    expected_version: StrictInt = Field(..., ge=0)
    idempotency_key: str = Field(..., min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    request_id: str = Field(..., min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")


class AgentConfigurationRequest(AgentMutationRequest):
    """选择一个服务端发布的不可变配置快照。"""

    config_snapshot_id: str = Field(..., min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$")


class AgentReviewRequest(BaseModel):
    """对精确待审版本作人工决策。"""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "revise", "override"]
    reason: str = Field(..., min_length=1, max_length=20_000)
    expected_version: StrictInt = Field(..., ge=0)
    review_id: str = Field(..., min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$")
    artifact_version_id: str = Field(..., min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$")
    idempotency_key: str = Field(..., min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    request_id: str = Field(..., min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("决定理由不能为空")
        return value


class AgentClarificationRequest(BaseModel):
    """为持久问题提交一次明确回答。"""

    model_config = ConfigDict(extra="forbid")
    answer: str = Field(..., min_length=1, max_length=20_000)
    question_id: str = Field(..., min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$")
    expected_version: StrictInt = Field(..., ge=0)
    idempotency_key: str = Field(..., min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    request_id: str = Field(..., min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")

    @field_validator("answer")
    @classmethod
    def validate_answer(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("澄清答复不能为空")
        return value


class AgentRollbackRequest(BaseModel):
    """带CAS的运行时回退申请。"""

    model_config = ConfigDict(extra="forbid")
    target_stage: str = Field(..., min_length=1, max_length=64)
    expected_version: StrictInt = Field(..., ge=0)
    idempotency_key: str = Field(..., min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    request_id: str = Field(..., min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")


def _agent_integration(request: Request):
    integration = getattr(request.app.state, "agent_integration", None)
    if integration is None:
        raise CleanroomException(503, "AGENT_NOT_INTEGRATED", "智能体运行服务尚未就绪")
    return integration


async def _agent_identity(request: Request, authorization: Optional[str], x_user_role: str):
    """Require a live host Cookie session, never a development bearer identity."""
    auth = require_authenticated(authorization, x_user_role)
    integration = _agent_integration(request)
    _, fingerprint = await integration.authenticate_request(auth)
    return integration, auth, fingerprint


def _agent_run_payload(run: Any, integration: Any, *, accepted_response: bool = False) -> dict[str, Any]:
    from gw.api.routes_god_canvas import agent_run_job_payload

    run_id = run.run_id.root
    job_id = integration.job_id_for_run(run_id)
    paused = getattr(run.paused_reason, "root", None) if run.paused_reason is not None else None
    stage = run.current_stage.value if run.current_stage is not None else None
    projected = agent_run_job_payload(job_id, run, accepted_response=accepted_response)
    return {
        **projected,
        "run_id": run_id,
        "project_id": run.project_id.root,
        "status": run.status.value,
        "stage": stage,
        "version": run.version.root,
        "paused_reason": paused,
        "cost_ledger": integration.cost_ledger(run_id),
    }


def _agent_context(integration: Any, auth: Any, fingerprint: str, run_id: str, request_id: str, *, edit_required: bool):
    return integration.context_for_run(
        auth, fingerprint, run_id, request_id, edit_required=edit_required,
    )


@router.get("/api/agent/config", summary="读取Agent服务端模型准入配置")
async def agent_configuration(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    integration, _, _ = await _agent_identity(request, authorization, x_user_role)
    return integration.config_summary()


@router.post("/api/agent/runs", status_code=status.HTTP_202_ACCEPTED, summary="创建持久Agent运行任务")
async def create_agent_run(
    payload: AgentCreateRunRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    integration, auth, fingerprint = await _agent_identity(request, authorization, x_user_role)
    run = await integration.create_run(
        auth,
        fingerprint,
        project_id=payload.project_id,
        user_goal=payload.user_goal,
        provider_id=payload.provider_id,
        model=payload.model,
        mode=payload.mode,
        config_snapshot_id=payload.config_snapshot_id,
        input_artifact_refs=payload.input_artifact_refs,
        idempotency_key=payload.idempotency_key,
        request_id=payload.request_id,
    )
    return JSONResponse(status_code=202, content=_agent_run_payload(run, integration, accepted_response=True))


@router.get("/api/agent/runs/{run_id}", summary="读取Agent运行状态")
async def get_agent_run(
    run_id: str,
    request: Request,
    request_id: str = Query(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    integration, auth, fingerprint = await _agent_identity(request, authorization, x_user_role)
    context = await _agent_context(integration, auth, fingerprint, run_id, request_id, edit_required=False)
    run = await integration.use_case.get_run(context, Identifier(run_id))
    if run is None:
        raise DomainError(ErrorCode.PROJECT_UNAVAILABLE, "任务不存在或当前主体不可访问", request_id=request_id)
    return _agent_run_payload(run, integration)


@router.get("/api/agent/runs/{run_id}/events", summary="按版本读取Agent事件")
async def get_agent_events(
    run_id: str,
    request: Request,
    after_version: int = Query(default=0, ge=0),
    cursor: Optional[str] = Query(None, min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$"),
    limit: int = Query(default=100, ge=1, le=1000),
    request_id: str = Query(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    integration, auth, fingerprint = await _agent_identity(request, authorization, x_user_role)
    context = await _agent_context(integration, auth, fingerprint, run_id, request_id, edit_required=False)
    run_ref = Identifier(run_id)
    if cursor:
        page = await integration.use_case.list_events(context, run_ref, Identifier(cursor), limit)
        events = page.events
    else:
        page = await integration.use_case.list_events(context, run_ref, None, min(limit, 1000))
        events = []
        page_cursor = page.next_cursor
        while True:
            events.extend(item for item in page.events if item.version.root > after_version)
            if len(events) >= limit or page_cursor is None:
                break
            page = await integration.use_case.list_events(context, run_ref, page_cursor, min(limit, 1000))
            page_cursor = page.next_cursor
        events = events[:limit]
    last_cursor = events[-1].event_id.root if events else cursor
    return {
        "events": [item.model_dump(mode="json") for item in events],
        "cursor": last_cursor,
        "next_cursor": page.next_cursor.root if page.next_cursor is not None else None,
        "snapshot_version": page.snapshot_version.root,
    }


@router.get("/api/agent/runs/{run_id}/candidate", summary="读取当前人工审核候选")
async def get_agent_candidate(
    run_id: str,
    request: Request,
    request_id: str = Query(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    integration, auth, fingerprint = await _agent_identity(request, authorization, x_user_role)
    context = await _agent_context(integration, auth, fingerprint, run_id, request_id, edit_required=False)
    run = await integration.use_case.get_run(context, Identifier(run_id))
    if run is None:
        raise DomainError(ErrorCode.PROJECT_UNAVAILABLE, "任务不存在或当前主体不可访问", request_id=request_id)
    state = await integration.service.repository.read_runtime_state(run.run_id) or {}
    pending = state.get("pending_review")
    if not isinstance(pending, Mapping):
        return {"candidate": None}
    review_id = pending.get("review_id")
    artifact_version_id = pending.get("artifact_version_id")
    if not isinstance(review_id, str) or not isinstance(artifact_version_id, str):
        return {"candidate": None}
    candidate = await integration.use_case.read_review_candidate(
        context, run.run_id, Identifier(review_id), Identifier(artifact_version_id),
    )
    return {"candidate": candidate.model_dump(mode="json")}


@router.get("/api/agent/runs/{run_id}/reviews", summary="读取Agent审核与人工决策记录")
async def get_agent_reviews(
    run_id: str,
    request: Request,
    request_id: str = Query(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    integration, auth, fingerprint = await _agent_identity(request, authorization, x_user_role)
    context = await _agent_context(integration, auth, fingerprint, run_id, request_id, edit_required=False)
    run = await integration.use_case.get_run(context, Identifier(run_id))
    if run is None:
        raise DomainError(ErrorCode.PROJECT_UNAVAILABLE, "任务不存在或当前主体不可访问", request_id=request_id)
    reviews = await integration.service.repository.get_reviews(run.run_id)
    items = []
    for review in reviews:
        approval = await integration.service.repository.get_approval_for_artifact(
            run.run_id, review.review_id, review.artifact.version_id,
        )
        export_formats = await integration.service.export_formats_for_review(
            run.run_id, review.review_id, review.artifact.version_id,
        )
        items.append({
            "review": review.model_dump(mode="json"),
            "approval": approval.model_dump(mode="json") if approval is not None else None,
            "export_formats": [
                "exact-json" if export_format is TextExportFormat.json else export_format.value
                for export_format in export_formats
            ],
        })
    return {"reviews": items}


@router.get("/api/agent/runs/{run_id}/clarifications", summary="读取待答澄清问题")
async def get_agent_clarifications(
    run_id: str,
    request: Request,
    request_id: str = Query(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    integration, auth, fingerprint = await _agent_identity(request, authorization, x_user_role)
    context = await _agent_context(integration, auth, fingerprint, run_id, request_id, edit_required=False)
    value = await integration.use_case.read_clarifications(context, Identifier(run_id))
    return value.model_dump(mode="json")


@router.get("/api/agent/runs/{run_id}/artifacts", summary="读取Agent产物版本目录")
async def get_agent_artifacts(
    run_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: Optional[str] = Query(None, min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$"),
    request_id: str = Query(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    integration, auth, fingerprint = await _agent_identity(request, authorization, x_user_role)
    context = await _agent_context(integration, auth, fingerprint, run_id, request_id, edit_required=False)
    artifacts, next_cursor = await integration.use_case.list_artifacts(
        context, Identifier(run_id), Identifier(cursor) if cursor else None, limit,
    )
    return {
        "artifacts": [item.model_dump(mode="json") for item in artifacts],
        "next_cursor": next_cursor.root if next_cursor else None,
    }


@router.get("/api/agent/runs/{run_id}/export", summary="按精确审核与产物版本导出")
async def export_agent_artifact(
    run_id: str,
    request: Request,
    format: Literal["exact-json", "markdown", "csv"],
    review_id: str = Query(..., min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$"),
    artifact_version_id: str = Query(..., min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$"),
    request_id: str = Query(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    from urllib.parse import quote

    integration, auth, fingerprint = await _agent_identity(request, authorization, x_user_role)
    context = await _agent_context(integration, auth, fingerprint, run_id, request_id, edit_required=False)
    selected_format = TextExportFormat.json if format == "exact-json" else TextExportFormat(format)
    payload = await integration.use_case.export_episode(
        context, Identifier(run_id), Identifier(review_id), Identifier(artifact_version_id), selected_format,
    )
    safe_name = quote(payload.filename.replace("\\", "_").replace("/", "_"), safe="-_.")
    return Response(
        content=payload.content,
        media_type=payload.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _agent_mutation_context(request: Request, authorization: Optional[str], x_user_role: str, run_id: str, request_id: str):
    integration, auth, fingerprint = await _agent_identity(request, authorization, x_user_role)
    context = await _agent_context(integration, auth, fingerprint, run_id, request_id, edit_required=True)
    return integration, context


def _agent_versioned_mutation(payload: AgentMutationRequest) -> VersionedMutation:
    return VersionedMutation(
        expected_version=Version(payload.expected_version),
        idempotency_key=Identifier(payload.idempotency_key),
        request_id=Identifier(payload.request_id),
    )


@router.post("/api/agent/runs/{run_id}/configuration", summary="为暂停运行应用服务端不可变配置")
async def apply_agent_configuration(
    run_id: str,
    payload: AgentConfigurationRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    integration, context = await _agent_mutation_context(
        request, authorization, x_user_role, run_id, payload.request_id,
    )
    profile = integration.persist_current_profile(payload.config_snapshot_id)
    if profile is None:
        replay = await integration.read_configuration_replay(
            run_id=run_id,
            expected_version=payload.expected_version,
            idempotency_key=payload.idempotency_key,
            config_snapshot_id=payload.config_snapshot_id,
        )
        if replay is not None:
            return _agent_run_payload(replay, integration)
        raise DomainError(
            ErrorCode.CAPABILITY_UNSUPPORTED,
            "配置快照未知或已过期，无法应用",
            request_id=payload.request_id,
        )
    run = await integration.use_case.apply_configuration(
        context,
        Identifier(run_id),
        _agent_versioned_mutation(payload),
        Identifier(payload.config_snapshot_id),
    )
    return _agent_run_payload(run, integration)


@router.post("/api/agent/runs/{run_id}/pause", summary="暂停Agent后续阶段")
async def pause_agent_run(run_id: str, payload: AgentMutationRequest, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    integration, context = await _agent_mutation_context(request, authorization, x_user_role, run_id, payload.request_id)
    run = await integration.use_case.pause_run(context, Identifier(run_id), _agent_versioned_mutation(payload))
    return _agent_run_payload(run, integration)


@router.post("/api/agent/runs/{run_id}/resume", summary="恢复Agent运行")
async def resume_agent_run(run_id: str, payload: AgentMutationRequest, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    integration, context = await _agent_mutation_context(request, authorization, x_user_role, run_id, payload.request_id)
    run = await integration.use_case.resume_run(context, Identifier(run_id), _agent_versioned_mutation(payload))
    return _agent_run_payload(run, integration)


@router.post("/api/agent/runs/{run_id}/cancel", summary="终止Agent后续阶段")
async def cancel_agent_run(run_id: str, payload: AgentMutationRequest, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    integration, context = await _agent_mutation_context(request, authorization, x_user_role, run_id, payload.request_id)
    run = await integration.use_case.cancel_run(context, Identifier(run_id), _agent_versioned_mutation(payload))
    return _agent_run_payload(run, integration)


@router.post("/api/agent/runs/{run_id}/review", summary="提交绑定精确审核版本的人工作用")
async def decide_agent_review(run_id: str, payload: AgentReviewRequest, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    integration, context = await _agent_mutation_context(request, authorization, x_user_role, run_id, payload.request_id)
    decision = ApprovalDecision.reject if payload.decision == "revise" else ApprovalDecision(payload.decision)
    await integration.use_case.submit_review_decision(
        context,
        ReviewDecisionRequest(
            request_id=Identifier(payload.request_id),
            idempotency_key=Identifier(payload.idempotency_key),
            run_id=Identifier(run_id),
            review_id=Identifier(payload.review_id),
            artifact_version_id=Identifier(payload.artifact_version_id),
            expected_version=Version(payload.expected_version),
            decision=decision,
            reason=payload.reason,
        ),
    )
    run_ref = Identifier(run_id)
    approval = await integration.service.repository.get_approval_for_artifact(
        run_ref, Identifier(payload.review_id), Identifier(payload.artifact_version_id),
    )
    run = await integration.use_case.get_run(context, run_ref)
    return {
        "approval": approval.model_dump(mode="json") if isinstance(approval, ApprovalRecord) else None,
        "run": _agent_run_payload(run, integration) if run is not None else None,
    }


@router.post("/api/agent/runs/{run_id}/clarifications", summary="提交精确澄清问题的回答")
async def answer_agent_clarification(run_id: str, payload: AgentClarificationRequest, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    integration, context = await _agent_mutation_context(request, authorization, x_user_role, run_id, payload.request_id)
    result = await integration.use_case.submit_clarification(
        context,
        Identifier(run_id),
        ClarificationAnswerRequest(
            expected_version=Version(payload.expected_version),
            idempotency_key=Identifier(payload.idempotency_key),
            request_id=Identifier(payload.request_id),
            answers={payload.question_id: payload.answer.strip()},
        ),
    )
    return _agent_run_payload(result, integration)


@router.post("/api/agent/runs/{run_id}/rollback", summary="申请绑定CAS版本的阶段回退")
async def rollback_agent_run(run_id: str, payload: AgentRollbackRequest, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    integration, context = await _agent_mutation_context(request, authorization, x_user_role, run_id, payload.request_id)
    try:
        target_stage = StageId(payload.target_stage)
    except ValueError:
        raise CleanroomException(400, "INVALID_REQUEST", "回退阶段不合法") from None
    result = await integration.use_case.request_rollback(
        context,
        Identifier(run_id),
        target_stage=target_stage,
        mutation=VersionedMutation(
            expected_version=Version(payload.expected_version),
            idempotency_key=Identifier(payload.idempotency_key),
            request_id=Identifier(payload.request_id),
        ),
    )
    return _agent_run_payload(result, integration)


@router.post(
    "/api/ai/upload",
    summary="上传 AI 附件（真实落盘）",
    status_code=status.HTTP_200_OK,
)
async def upload_ai_files(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """真实接收 multipart 文件并落盘到 GW_DATA_DIR，返回真实 file_id 与下载 URL。"""
    require_edit_access(authorization, x_user_role)
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" not in content_type:
        raise CleanroomException(400, "INVALID_REQUEST", "上传必须使用 multipart/form-data")
    form = await request.form()
    payloads: List[tuple] = []
    for key in ("files", "file"):
        for item in form.getlist(key):
            if hasattr(item, "read"):
                payloads.append((getattr(item, "filename", "") or "file", await item.read()))
        if payloads:
            break
    return cli_runtime.save_uploads(payloads)


@router.get(
    "/api/app-info",
    summary="读取洁净室应用信息",
    status_code=status.HTTP_200_OK,
)
def get_app_info(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
) -> Dict[str, Any]:
    """返回源码和运行时可证明的信息，不宣称发布授权或外部服务就绪。"""
    require_authenticated(authorization, x_user_role)
    return app_info()


@router.get(
    "/api/chat/config",
    summary="读取服务端 Chat Provider 配置状态（不触发生成）",
    status_code=status.HTTP_200_OK,
)
def chat_configuration(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
) -> Dict[str, Any]:
    """只读服务端运行配置摘要；不探测网络，不触发可能计费的模型请求。"""
    require_authenticated(authorization, x_user_role)
    return chat_runtime.public_configuration()


def _complete_chat(payload: ChatRequest, *, agent: bool):
    """把 Chat 专属的空吞吐原因放进标准 detail，不透传异常或上游 raw。"""
    try:
        return chat_runtime.complete(payload.model_dump(), agent=agent)
    except CleanroomException as exc:
        return chat_runtime.error_response(exc)


@router.post(
    "/api/chat",
    summary="对话请求（配置驱动真实调用；无配置失败关闭）",
    status_code=status.HTTP_200_OK,
)
def chat(
    payload: ChatRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """只按服务端运行配置路由；一次用户动作最多产生一次真实请求。"""
    require_edit_access(authorization, x_user_role)
    return _complete_chat(payload, agent=False)


@router.post(
    "/api/chat/agent",
    summary="智能体对话请求（配置驱动真实调用；无配置失败关闭）",
    status_code=status.HTTP_200_OK,
)
def chat_agent(
    payload: ChatRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """只按服务端运行配置路由；一次用户动作最多产生一次真实请求。"""
    require_edit_access(authorization, x_user_role)
    return _complete_chat(payload, agent=True)


@router.get(
    "/api/codex/status",
    summary="读取 GPT CLI 本机路径观察状态",
    status_code=status.HTTP_200_OK,
)
def codex_status(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
) -> Dict[str, Any]:
    """只观察 PATH，不执行 codex 命令。"""
    require_authenticated(authorization, x_user_role)
    return cli_status("codex")


@router.post(
    "/api/codex/help",
    summary="读取 GPT CLI 帮助（配置驱动真实执行）",
    status_code=status.HTTP_200_OK,
)
def codex_help(
    payload: CliHelpRequest = Body(default_factory=CliHelpRequest),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """仅当 GW_CLI_EXECUTION=1 且命令存在时执行固定 `--help`；不执行调用方命令文本。"""
    require_edit_access(authorization, x_user_role)
    return cli_runtime.cli_help("codex", "/api/codex/help")


@router.get(
    "/api/gemini-cli/status",
    summary="读取 Antigravity CLI 本机路径观察状态",
    status_code=status.HTTP_200_OK,
)
def gemini_cli_status(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
) -> Dict[str, Any]:
    """只观察 PATH，不执行 gemini/antigravity 命令。"""
    require_authenticated(authorization, x_user_role)
    return cli_status("gemini-cli")


@router.post(
    "/api/gemini-cli/help",
    summary="读取 Antigravity CLI 帮助（配置驱动真实执行）",
    status_code=status.HTTP_200_OK,
)
def gemini_cli_help(
    payload: CliHelpRequest = Body(default_factory=CliHelpRequest),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """仅当 GW_CLI_EXECUTION=1 且命令存在时执行固定 `--help`；不执行调用方命令文本。"""
    require_edit_access(authorization, x_user_role)
    return cli_runtime.cli_help("gemini-cli", "/api/gemini-cli/help")


@router.get(
    "/api/jimeng/credit",
    summary="读取 Dreamina CLI 余额（治理权限、固定 user_credit）",
    status_code=status.HTTP_200_OK,
)
def jimeng_credit(
    response: Response,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """仅治理角色可查询服务器本机共享账户；余额查询只由显式按钮触发。"""
    context = require_governance_access(authorization, x_user_role)
    _set_private_cli_headers(response)
    return cli_runtime.jimeng_credit(context)


@router.post(
    "/api/jimeng/help",
    summary="读取即梦 CLI 帮助（配置驱动真实执行）",
    status_code=status.HTTP_200_OK,
)
def jimeng_help(
    payload: CliHelpRequest = Body(default_factory=CliHelpRequest),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """仅当 GW_CLI_EXECUTION=1 且命令存在时执行固定 `--help`；不执行调用方命令文本。"""
    require_edit_access(authorization, x_user_role)
    return cli_runtime.cli_help("jimeng", "/api/jimeng/help")


@router.post(
    "/api/jimeng/login/start",
    summary="启动 Dreamina CLI Device Flow 登录（治理权限）",
    status_code=status.HTTP_200_OK,
)
def jimeng_login_start(
    response: Response,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """200仅表示接管/观察单实例登录进程，不代表账户已登录。"""
    context = require_governance_access(authorization, x_user_role)
    _set_private_cli_headers(response)
    return cli_runtime.jimeng_login_start(context)


@router.get(
    "/api/jimeng/login/status",
    summary="读取 Dreamina CLI 最近一次登录操作观察",
    status_code=status.HTTP_200_OK,
)
def jimeng_login_status(
    response: Response,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
) -> Dict[str, Any]:
    """只返回本进程最后一次操作观测；重启/失败均不伪称未登录。"""
    context = require_governance_access(authorization, x_user_role)
    _set_private_cli_headers(response)
    return cli_runtime.jimeng_login_status(context)


@router.post(
    "/api/jimeng/logout",
    summary="退出 Dreamina CLI 登录（治理权限）",
    status_code=status.HTTP_200_OK,
)
def jimeng_logout(
    response: Response,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """只运行固定 logout；若登录仍运行则409拒绝，不隐式取消。"""
    context = require_governance_access(authorization, x_user_role)
    _set_private_cli_headers(response)
    return cli_runtime.jimeng_logout(context)


@router.get(
    "/api/jimeng/status",
    summary="读取 Dreamina CLI 最近一次操作观察（兼容路径）",
    status_code=status.HTTP_200_OK,
)
def jimeng_status(
    response: Response,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
) -> Dict[str, Any]:
    """兼容状态路径与登录状态共用单一控制器与主体隔离。"""
    context = require_governance_access(authorization, x_user_role)
    _set_private_cli_headers(response)
    return cli_runtime.jimeng_login_status(context, "/api/jimeng/status")


def _set_private_cli_headers(response: Response) -> None:
    """避免浏览器、中间缓存和共享代理保存账户操作响应。"""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
