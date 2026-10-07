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

"""Phase 11 B6 资产审查与交付接口。

接口只完成认证、角色和统一失败关闭边界。没有冻结状态机与准入数据后端时，
禁止伪造会话、评论、审批、交付、导出或分享结果。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, Request, status

from gw.asset_review.service import unavailable
from gw.core.auth import require_authenticated, require_edit_access

router = APIRouter(tags=["asset-review-b6"])


def _read(authorization: Optional[str], user_role: str):
    """执行统一读取认证。"""
    return require_authenticated(authorization, user_role)


def _write(authorization: Optional[str], user_role: str):
    """执行统一写入认证；只读角色在此处先返回 403。"""
    return require_edit_access(authorization, user_role)


def _fail(endpoint: str, capability: str = "资产审查与交付能力") -> None:
    """抛出统一 B6 失败关闭响应。"""
    unavailable(endpoint, capability)


def _read_route(endpoint: str, authorization: Optional[str], user_role: str, capability: str = "资产审查与交付能力") -> None:
    _read(authorization, user_role)
    _fail(endpoint, capability)


def _write_route(endpoint: str, authorization: Optional[str], user_role: str, capability: str = "资产审查与交付能力") -> None:
    _write(authorization, user_role)
    _fail(endpoint, capability)


@router.patch("/api/asset-reviews/comments/{comment_id}", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
def update_review_comment(
    comment_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """更新评论；当前仅保留认证与失败关闭边界。"""
    _write_route("/api/asset-reviews/comments/{p}", authorization, x_user_role, "资产审查评论能力")


@router.post("/api/asset-reviews/deliveries/{delivery_id}/export", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
def export_review_delivery(
    delivery_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """导出交付物；未接入时不创建文件、URL、任务或轮询提示。"""
    _write_route("/api/asset-reviews/deliveries/{p}/export", authorization, x_user_role, "资产交付导出能力")


@router.get("/api/asset-reviews/sessions", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
def list_review_sessions(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """列出审查会话；不伪造空会话或状态机数据。"""
    _read_route("/api/asset-reviews/sessions", authorization, x_user_role, "资产审查会话能力")


@router.post("/api/asset-reviews/sessions", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
def create_review_session(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """创建审查会话；未接入时不生成 session_id。"""
    _write_route("/api/asset-reviews/sessions", authorization, x_user_role, "资产审查会话能力")


@router.get("/api/asset-reviews/sessions/{session_id}", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
def get_review_session(
    session_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """读取审查会话；未接入时不声称会话存在。"""
    _read_route("/api/asset-reviews/sessions/{p}", authorization, x_user_role, "资产审查会话能力")


@router.put("/api/asset-reviews/sessions/{session_id}/approval", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
def update_review_approval(
    session_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """更新审批状态；未接入时不推进或伪造状态机。"""
    _write_route("/api/asset-reviews/sessions/{p}/approval", authorization, x_user_role, "资产审查审批能力")


@router.post("/api/asset-reviews/sessions/{session_id}/comments", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
def create_review_comment(
    session_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """新增审查评论；未接入时不生成 comment_id。"""
    _write_route("/api/asset-reviews/sessions/{p}/comments", authorization, x_user_role, "资产审查评论能力")


@router.post("/api/asset-reviews/sessions/{session_id}/delivery", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
def create_review_delivery(
    session_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """创建交付物；未接入时不生成 delivery_id。"""
    _write_route("/api/asset-reviews/sessions/{p}/delivery", authorization, x_user_role, "资产交付能力")


@router.post("/api/asset-reviews/shares", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
def create_review_share(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """创建审查分享；未接入时不生成分享令牌或公开链接。"""
    _write_route("/api/asset-reviews/shares", authorization, x_user_role, "资产审查分享能力")


__all__ = ["router"]
