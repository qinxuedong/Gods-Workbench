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

"""Phase 12 A3 资产审查与交付 API（真实落盘）。

数据来源：``gw.asset_review.repository``（core.storage.JsonState
命名空间 ``asset_review``），重启可恢复；单实例一致性，多 worker 属部署方职责。

安全口径：
- 读端点需认证；写端点需 ``require_edit_access``（只读角色 403）；
- 状态机 ``open → delivered → approved/rejected``，非法流转 409；C9 额外返回
  ``closure_status``、``blocker``、``next_action``，失败/等待/未知不返回成功形态；
- 导出 PDF 由纯标准库生成真实字节（``%PDF-`` 开头、``%%EOF`` 结尾），不伪造；
- 分享为**本地**令牌：服务端只存哈希，明文仅返回一次，查不到 404 ``SHARE_NOT_FOUND``。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request, Response, status

from gw.asset_review import repository as repo
from gw.core.auth import require_authenticated, require_edit_access
from gw.core.errors import CleanroomException

router = APIRouter(tags=["asset-review-b6"])


def _read(authorization: str | None, role: str):
    return require_authenticated(authorization, role)


def _write(authorization: str | None, role: str):
    return require_edit_access(authorization, role)


async def _json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


@router.get("/api/asset-reviews/sessions", status_code=status.HTTP_200_OK)
def list_sessions(
    asset_id: str | None = None,
    project_id: str | None = None,
    shot_id: str | None = None,
    version_id: str | None = None,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """读取审查会话集合；C8 精确版本与 C9 收口投影一并回读。"""
    _read(authorization, x_user_role)
    return repo.list_sessions(
        asset_id, project_id=project_id, shot_id=shot_id, version_id=version_id
    )


@router.post("/api/asset-reviews/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """创建审查会话，返回稳定 ``rs-NNNN``；写后可被 GET 读回。"""
    _write(authorization, x_user_role)
    payload = await _json(request)
    expected = payload.get("expected_version")
    if expected is not None:
        try:
            expected = int(expected)
        except (TypeError, ValueError):
            raise CleanroomException(
                400, "INVALID_REQUEST", "expected_version 必须是整数"
            ) from None
    return repo.create_session(payload, expected)


@router.get("/api/asset-reviews/sessions/{session_id}", status_code=status.HTTP_200_OK)
def get_session(
    session_id: str,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """读取单个审查会话详情；包含批准失效、阻塞和下一步；不存在 404。"""
    _read(authorization, x_user_role)
    return repo.get_session(session_id)


@router.post(
    "/api/asset-reviews/sessions/{session_id}/delivery",
    status_code=status.HTTP_201_CREATED,
)
async def create_delivery(
    session_id: str,
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """创建交付记录并把当前版本收口为 ``closed``；稳定 ``dlv-NNNN``。"""
    _write(authorization, x_user_role)
    payload = await _json(request)
    return repo.create_delivery(session_id, payload)


@router.post(
    "/api/asset-reviews/deliveries/{delivery_id}/export", status_code=status.HTTP_200_OK
)
def export_delivery(
    delivery_id: str,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """导出交付清单 PDF；交付不存在 404，PDF 字节真实可校验。"""
    _write(authorization, x_user_role)
    result = repo.export_delivery(delivery_id)
    filename = result["filename"]
    ascii_name = (
        "".join(ch for ch in filename if ch.isascii() and ch not in '\\/:*?"<>|\r\n')
        or "delivery.pdf"
    )
    return Response(
        content=result["pdf"],
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{ascii_name}"',
            "X-Delivery-Id": delivery_id,
        },
    )


@router.post(
    "/api/asset-reviews/sessions/{session_id}/comments",
    status_code=status.HTTP_201_CREATED,
)
async def create_comment(
    session_id: str,
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """新增审查评论；稳定 ``rc-NNNN``，created_at 为数字 epoch（前端依赖）。"""
    _write(authorization, x_user_role)
    payload = await _json(request)
    return repo.create_comment(session_id, payload)


@router.patch(
    "/api/asset-reviews/comments/{comment_id}", status_code=status.HTTP_200_OK
)
async def update_comment(
    comment_id: str,
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """更新评论（解决/重开/改文本）；不存在 404。"""
    _write(authorization, x_user_role)
    payload = await _json(request)
    return repo.update_comment(comment_id, payload)


@router.put(
    "/api/asset-reviews/sessions/{session_id}/approval", status_code=status.HTTP_200_OK
)
async def put_approval(
    session_id: str,
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """登记审批结论；Provider 未完成或终态不可再翻转时返回可定位 409。"""
    _write(authorization, x_user_role)
    payload = await _json(request)
    return repo.put_approval(session_id, payload)


@router.post("/api/asset-reviews/shares", status_code=status.HTTP_201_CREATED)
async def create_share(
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """创建本地分享令牌；明文仅返回一次，服务端只保存 SHA-256 哈希。"""
    context = _write(authorization, x_user_role)
    payload = await _json(request)
    return repo.create_share(payload, context=context)
