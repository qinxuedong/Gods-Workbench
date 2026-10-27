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

"""MVP1 素材收集 API 路由 (QIN-436)。

支持 POST /api/asset-collect/url, POST /api/asset-collect/files, GET /api/asset-collect/jobs/{job_id}。
对不支持的素材来源返回统一错误码与中文提示。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from gw.asset_registry import collector
from gw.asset_registry.collector import UnsupportedSourceError

router = APIRouter(prefix="/api/asset-collect", tags=["asset-collect"])

# 内存任务记录字典 (job_id -> job_dict)
_JOBS: dict[str, dict[str, Any]] = {}


@router.post("/url", status_code=status.HTTP_202_ACCEPTED)
async def submit_collect_url(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """提交 URL 收集任务。"""
    url = str(payload.get("url") or "").strip()
    pair_token = payload.get("pair_token")

    try:
        collector.validate_source(url)
    except UnsupportedSourceError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": exc.code,
                "error_code": exc.code,
                "message": exc.message,
                "detail": {
                    "code": exc.code,
                    "message": exc.message,
                },
            },
        )
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": "COLLECT_FAILED",
                "error_code": "COLLECT_FAILED",
                "message": str(exc),
                "detail": {
                    "code": "COLLECT_FAILED",
                    "message": str(exc),
                },
            },
        )

    job_id = f"collect-{uuid.uuid4().hex[:8]}"
    job_info = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "url": url,
        "pair_token": pair_token,
    }
    _JOBS[job_id] = job_info
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"job_id": job_id, "status": "queued"},
    )


@router.post("/files", status_code=status.HTTP_202_ACCEPTED)
async def submit_collect_files(
    files: list[UploadFile] = File(...),
    pair_token: str | None = Form(None),
) -> JSONResponse:
    """提交文件收集任务。"""
    accepted = []
    for f in files:
        accepted.append({"name": f.filename or "file", "size": f.size or 0})

    job_id = f"collect-{uuid.uuid4().hex[:8]}"
    job_info = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "accepted": accepted,
        "pair_token": pair_token,
    }
    _JOBS[job_id] = job_info
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"job_id": job_id, "accepted": accepted},
    )


@router.get("/jobs/{job_id}")
async def get_collect_job(job_id: str) -> JSONResponse:
    """查询收集任务状态。"""
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "收集任务不存在"},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "job_id": job["job_id"],
            "status": job["status"],
            "progress": job.get("progress", 0),
            "error_code": job.get("error_code", ""),
        },
    )
