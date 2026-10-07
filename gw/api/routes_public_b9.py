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

"""Phase 12 A3 公开分享访问 API（无认证语义 + 持久限流 + 哈希令牌）。

安全边界：票据只经请求体（写入）或 X-Share-Ticket（媒体读取）传递；
媒体仅从本机素材注册表读取，并且所有成功/失败响应均禁止缓存。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import FileResponse, JSONResponse, Response

from gw.asset_review import repository as repo
from gw.core.errors import CleanroomException


_MAX_PUBLIC_BODY_BYTES = 16 * 1024
_NO_STORE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
        headers=_NO_STORE_HEADERS,
    )


class _NoStorePublicShareRoute(APIRoute):
    """在路由边界覆盖成功、契约错误与意外错误的缓存头。"""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def no_store_handler(request: Request):
            try:
                response = await original_handler(request)
            except CleanroomException as exc:
                response = JSONResponse(
                    status_code=exc.status_code,
                    content=exc.to_envelope().model_dump(exclude_none=True),
                    headers=_NO_STORE_HEADERS,
                )
            except RequestValidationError:
                response = _error_response(400, "INVALID_REQUEST", "请求参数不合法")
            except Exception:
                # 公共访问失败必须闭合且不缓存；不把异常文本或令牌写入响应。
                response = _error_response(500, "PUBLIC_SHARE_UNAVAILABLE", "分享服务暂时不可用")
            for name, value in _NO_STORE_HEADERS.items():
                response.headers[name] = value
            return response

        return no_store_handler


router = APIRouter(
    prefix="/api/public/shares",
    tags=["public-share-b9"],
    route_class=_NoStorePublicShareRoute,
)


async def _json(request: Request) -> Dict[str, Any]:
    """以流式长度计数解析公开请求体，避免无 Content-Length 的大请求占用内存。"""
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > _MAX_PUBLIC_BODY_BYTES:
                raise CleanroomException(413, "PAYLOAD_TOO_LARGE", "公开分享请求体超过 16 KiB 上限")
        except ValueError:
            raise CleanroomException(400, "INVALID_REQUEST", "Content-Length 不合法")
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _MAX_PUBLIC_BODY_BYTES:
            raise CleanroomException(413, "PAYLOAD_TOO_LARGE", "公开分享请求体超过 16 KiB 上限")
        chunks.append(chunk)
    if not size:
        return {}
    try:
        payload = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是有效 JSON")
    if not isinstance(payload, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    return payload


@router.get("/{share_token}", status_code=status.HTTP_200_OK)
def get_public_share(share_token: str):
    """读取分享元信息；令牌不透明且只存哈希，查不到 404 SHARE_NOT_FOUND。"""
    return repo.public_meta(share_token)


@router.post("/{share_token}/access", status_code=status.HTTP_200_OK)
async def access_public_share(share_token: str, request: Request):
    """校验分享口令并签发当前唯一有效访问票据。"""
    repo.enforce_public_rate(share_token, "access")
    return repo.public_access(share_token, await _json(request), rate_checked=True)


@router.put("/{share_token}/approvals", status_code=status.HTTP_200_OK)
async def approve_public_share(share_token: str, request: Request):
    """访客提交审批结论；必须携带有效票据（401），未开放审批 403。"""
    repo.enforce_public_rate(share_token, "approval")
    return repo.public_approval(share_token, await _json(request), rate_checked=True)


@router.post("/{share_token}/comments", status_code=status.HTTP_201_CREATED)
async def comment_public_share(share_token: str, request: Request):
    """访客提交评论；必须携带有效票据（401），未开放评论 403。"""
    repo.enforce_public_rate(share_token, "comment")
    return repo.public_comment(share_token, await _json(request), rate_checked=True)


@router.get("/{share_token}/assets/{asset_id}/media", status_code=status.HTTP_200_OK)
def get_public_share_media(
    share_token: str,
    asset_id: str,
    x_share_ticket: Optional[str] = Header(None, alias="X-Share-Ticket"),
    download: Optional[str] = Query(None),
):
    """读取分享媒体；票据只从请求头读取，PDF/文本等只允许附件方式下载。"""
    repo.enforce_public_rate(share_token, "media")
    if download not in (None, "true", "false", "1", "0"):
        raise CleanroomException(400, "INVALID_REQUEST", "download 只接受 true/false")
    download_requested = download in ("true", "1")
    path, media_type, filename, disposition = repo.public_media(
        share_token, asset_id, x_share_ticket, download=download_requested, rate_checked=True
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type=disposition,
        headers=_NO_STORE_HEADERS,
    )
