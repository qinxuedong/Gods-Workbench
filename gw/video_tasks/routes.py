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

"""视频任务 HTTP 路由。"""
from typing import Optional
from fastapi import APIRouter, Header, Query, status
from fastapi.responses import FileResponse
from gw.core.auth import AuthContext, require_authenticated, require_edit_access, require_governance_access
from .models import ProjectAssetAuthorizationRequest, VideoExportRequest, VideoGenerationRequest
from .service import get_video_service

router = APIRouter(tags=["video-tasks"])


def _read_context(authorization: Optional[str], role: str) -> AuthContext:
    return require_authenticated(authorization, role)


@router.post("/api/video-tasks", status_code=status.HTTP_202_ACCEPTED)
def create_video_task(payload: VideoGenerationRequest,
                      idempotency_key: str = Header(..., alias="Idempotency-Key"),
                      authorization: Optional[str] = Header(None),
                      x_user_role: str = Header("editor", alias="X-User-Role")):
    context = require_edit_access(authorization, x_user_role)
    return get_video_service().create_generation(payload.model_dump(), idempotency_key, context)


@router.get("/api/video-tasks", status_code=status.HTTP_200_OK)
def list_video_tasks(authorization: Optional[str] = Header(None),
                     x_user_role: str = Header("editor", alias="X-User-Role"),
                     limit: int = Query(50, ge=1, le=100)):
    return get_video_service().list_jobs(_read_context(authorization, x_user_role), limit)


@router.get("/api/video-tasks/{job_id}", status_code=status.HTTP_200_OK)
def get_video_task(job_id: str, authorization: Optional[str] = Header(None),
                   x_user_role: str = Header("editor", alias="X-User-Role")):
    return get_video_service().get_job(job_id, _read_context(authorization, x_user_role))


@router.post("/api/video-tasks/{job_id}/cancel", status_code=status.HTTP_200_OK)
def cancel_video_task(job_id: str, authorization: Optional[str] = Header(None),
                      x_user_role: str = Header("editor", alias="X-User-Role")):
    result = get_video_service().cancel(job_id, require_edit_access(authorization, x_user_role))
    return {"job_id": result["job_id"], "status": result["status"], "remote_cancelled": False,
            "remote_may_continue_or_bill": result["remote_may_continue_or_bill"]}


@router.post("/api/video-exports", status_code=status.HTTP_202_ACCEPTED)
def create_video_export(payload: VideoExportRequest,
                        idempotency_key: str = Header(..., alias="Idempotency-Key"),
                        authorization: Optional[str] = Header(None),
                        x_user_role: str = Header("editor", alias="X-User-Role")):
    context = require_edit_access(authorization, x_user_role)
    return get_video_service().create_export(payload.model_dump(), idempotency_key, context)


@router.post("/api/video-project-access/{project_id}/claim", status_code=status.HTTP_200_OK)
def claim_video_project(project_id: str, authorization: Optional[str] = Header(None),
                        x_user_role: str = Header("editor", alias="X-User-Role")):
    context = require_governance_access(authorization, x_user_role)
    get_video_service().claim_project(project_id, context)
    return {"project_id": project_id, "owner_bound": True}


@router.post("/api/video-projects/{project_id}/assets/authorize", status_code=status.HTTP_200_OK)
def authorize_project_asset(project_id: str, payload: ProjectAssetAuthorizationRequest,
                            authorization: Optional[str] = Header(None),
                            x_user_role: str = Header("editor", alias="X-User-Role")):
    context = require_edit_access(authorization, x_user_role)
    return get_video_service().authorize_asset(project_id, payload.asset_id, context)


@router.get("/api/video-tasks/{job_id}/content", status_code=status.HTTP_200_OK)
def get_video_content(job_id: str, download: bool = Query(False), authorization: Optional[str] = Header(None),
                      x_user_role: str = Header("editor", alias="X-User-Role")):
    path, artifact = get_video_service().open_artifact(job_id, _read_context(authorization, x_user_role))
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=artifact["asset_id"] + ".mp4",
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
