"""工作流私有资产的请求主体边界；跨主体隐藏资源而非泄露归属。"""
from __future__ import annotations

import hashlib
from contextvars import ContextVar
from typing import Optional

from fastapi import Header
from gw.core.auth import require_authenticated

current_asset_owner: ContextVar[Optional[str]] = ContextVar("current_asset_owner", default=None)
current_auth_context: ContextVar[Optional[object]] = ContextVar("current_auth_context", default=None)


def asset_owner_key(context) -> str:
    value = f"{context.identity_domain or ''}\x00{context.subject or 'anonymous'}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def asset_visible(record: dict) -> bool:
    owner = record.get("workflow_owner") or record.get("metadata", {}).get("workflow_owner")
    if owner:
        return owner == current_asset_owner.get()
    project_ids = list(record.get("project_ids") or [])
    if record.get("project_id") and record["project_id"] not in project_ids:
        project_ids.append(record["project_id"])
    if project_ids:
        context = current_auth_context.get()
        if context is None:
            return False
        from gw.projects_hub import service as projects_service_module
        owner_key = None
        role = str(getattr(context, "role", "") or "")
        try:
            owner_key = projects_service_module.owner_key_for_context(context)
        except Exception:
            owner_key = None
        can_read = False
        for pid in project_ids:
            if projects_service_module.default_projects_service.can_read_project(pid, owner_key=owner_key, role=role):
                can_read = True
                break
        if not can_read:
            return False
    # 旧桥接没有可核实的归属，禁止作为公共资源继续暴露。
    url = str(record.get("url") or "")
    return not ("/api/god_workflow/assets/" in url or "/pages/workflow.html?id=" in url)


async def asset_request_scope(authorization: Optional[str] = Header(None),
                              x_user_role: str = Header("editor", alias="X-User-Role")):
    context = require_authenticated(authorization, x_user_role)
    token = current_asset_owner.set(asset_owner_key(context))
    auth_token = current_auth_context.set(context)
    try:
        yield
    finally:
        current_asset_owner.reset(token)
        current_auth_context.reset(auth_token)
