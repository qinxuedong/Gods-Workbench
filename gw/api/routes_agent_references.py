"""MVP2-T1 参考策展 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from gw.agent_episode.references import (
    ReferenceItemUpdateRequest,
    ReferenceSet,
    SQLiteReferenceRepository,
)
from gw.core.errors import CleanroomException

router = APIRouter(prefix="/api/agent/sessions", tags=["agent_references"])


def _get_repository(request: Request) -> SQLiteReferenceRepository:
    repo = getattr(request.app.state, "reference_repository", None)
    if repo is not None:
        return repo
    integration = getattr(request.app.state, "agent_integration", None)
    if integration is not None and hasattr(integration, "database"):
        repo = SQLiteReferenceRepository(integration.database)
        request.app.state.reference_repository = repo
        return repo
    repo = SQLiteReferenceRepository()
    request.app.state.reference_repository = repo
    return repo


@router.get("/{session_id}/references", response_model=ReferenceSet)
async def get_session_references(session_id: str, request: Request) -> ReferenceSet:
    if not session_id or not session_id.strip():
        raise CleanroomException(400, "INVALID_REQUEST", "session_id 不能为空")
    repo = _get_repository(request)
    ref_set = repo.get_reference_set(session_id)
    if ref_set is None:
        raise CleanroomException(
            404, "SESSION_NOT_FOUND", f"会话不存在: {session_id}"
        )
    return ref_set


@router.put("/{session_id}/references/{asset_id}", response_model=ReferenceSet)
async def update_session_reference_item(
    session_id: str,
    asset_id: str,
    payload: ReferenceItemUpdateRequest,
    request: Request,
) -> ReferenceSet:
    if not session_id or not session_id.strip():
        raise CleanroomException(400, "INVALID_REQUEST", "session_id 不能为空")
    if not asset_id or not asset_id.strip():
        raise CleanroomException(400, "INVALID_REQUEST", "asset_id 不能为空")
    if not payload.project_id or not payload.project_id.strip():
        raise CleanroomException(400, "INVALID_REQUEST", "project_id 不能为空")

    repo = _get_repository(request)
    return repo.put_reference_item(
        session_id=session_id,
        asset_id=asset_id,
        project_id=payload.project_id,
        decision=payload.decision,
        note=payload.note,
        order=payload.order,
        expected_version=payload.expected_version,
    )
