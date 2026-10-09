"""工作流私有资产的请求主体边界；跨主体隐藏资源而非泄露归属。"""
from __future__ import annotations

import hashlib
from contextvars import ContextVar
from typing import Optional

from fastapi import Header
from gw.core.auth import require_authenticated

current_asset_owner: ContextVar[Optional[str]] = ContextVar("current_asset_owner", default=None)


def asset_owner_key(context) -> str:
    value = f"{context.identity_domain or ''}\x00{context.subject or 'anonymous'}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def asset_visible(record: dict) -> bool:
    owner = record.get("workflow_owner") or record.get("metadata", {}).get("workflow_owner")
    if owner:
        return owner == current_asset_owner.get()
    # 旧桥接没有可核实的归属，禁止作为公共资源继续暴露。
    url = str(record.get("url") or "")
    return not ("/api/god_workflow/assets/" in url or "/pages/workflow.html?id=" in url)


async def asset_request_scope(authorization: Optional[str] = Header(None),
                              x_user_role: str = Header("editor", alias="X-User-Role")):
    context = require_authenticated(authorization, x_user_role)
    token = current_asset_owner.set(asset_owner_key(context))
    try:
        yield
    finally:
        current_asset_owner.reset(token)
