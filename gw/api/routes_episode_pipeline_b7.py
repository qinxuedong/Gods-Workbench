# -*- coding: utf-8 -*-
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

"""Phase 12 A3 剧集流水线 API（真实落盘，不触发真实渲染）。

数据来源：``gw.episode_pipeline.repository``
（core.storage.JsonState 命名空间 ``episode_pipeline``），重启可恢复。

证据边界：阶段 start/complete/cancel **只推进状态机**，本阶段不触发任何真实渲染、
不调用外部模型、不生成媒体产物；单实例一致性，多 worker 并发写属部署方职责。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, Request, status

from gw.core.auth import require_authenticated, require_edit_access
from gw.episode_pipeline import repository as repo

router = APIRouter(tags=["episode-pipeline-b7"])


def _read(authorization: Optional[str], role: str):
    return require_authenticated(authorization, role)


def _write(authorization: Optional[str], role: str):
    return require_edit_access(authorization, role)


async def _json(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


@router.get("/api/episode-pipelines", status_code=status.HTTP_200_OK)
def list_pipelines(
    project_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """按项目读取流水线集合；空集合返回 pipelines: []。"""
    _read(authorization, x_user_role)
    return repo.list_pipelines(project_id)


@router.post("/api/episode-pipelines", status_code=status.HTTP_201_CREATED)
async def create_pipeline(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """创建流水线并建立 4 个阶段（script/assets/video/audio_compose）。"""
    _write(authorization, x_user_role)
    return repo.create_pipeline(await _json(request))


@router.get("/api/episode-pipelines/{pipeline_id}", status_code=status.HTTP_200_OK)
def get_pipeline(
    pipeline_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """读取单个流水线；不存在 404。"""
    _read(authorization, x_user_role)
    return repo.get_pipeline(pipeline_id)


@router.post("/api/episode-pipelines/{pipeline_id}/stages/{stage_id}/start", status_code=status.HTTP_200_OK)
async def start_stage(
    pipeline_id: str,
    stage_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """把阶段推进到 running；非法流转 409，CAS 冲突 409。"""
    _write(authorization, x_user_role)
    return repo.start_stage(pipeline_id, stage_id, await _json(request))


@router.post("/api/episode-pipelines/{pipeline_id}/stages/{stage_id}/complete", status_code=status.HTTP_200_OK)
async def complete_stage(
    pipeline_id: str,
    stage_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """把阶段推进到 completed/failed；未启动直接完成 409。"""
    _write(authorization, x_user_role)
    return repo.complete_stage(pipeline_id, stage_id, await _json(request))


@router.post("/api/episode-pipelines/{pipeline_id}/stages/{stage_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_stage(
    pipeline_id: str,
    stage_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """取消阶段；已终态 409。"""
    _write(authorization, x_user_role)
    return repo.cancel_stage(pipeline_id, stage_id, await _json(request))
