import hashlib
from types import SimpleNamespace
import pytest
from fastapi import Header
from gw.core.asset_visibility import (
    asset_owner_key,
    asset_visible,
    asset_request_scope,
    current_asset_owner,
    current_auth_context,
)

def test_asset_owner_key():
    ctx = SimpleNamespace(identity_domain="test_domain", subject="user123")
    key = asset_owner_key(ctx)
    expected = hashlib.sha256(b"test_domain\x00user123").hexdigest()[:32]
    assert key == expected

    ctx_empty = SimpleNamespace(identity_domain=None, subject=None)
    key_empty = asset_owner_key(ctx_empty)
    expected_empty = hashlib.sha256(b"\x00anonymous").hexdigest()[:32]
    assert key_empty == expected_empty

def test_asset_visible_with_workflow_owner():
    token = current_asset_owner.set("owner123")
    try:
        # Top level workflow_owner
        assert asset_visible({"workflow_owner": "owner123"}) is True
        assert asset_visible({"workflow_owner": "other"}) is False

        # Metadata workflow_owner
        assert asset_visible({"metadata": {"workflow_owner": "owner123"}}) is True
        assert asset_visible({"metadata": {"workflow_owner": "other"}}) is False
    finally:
        current_asset_owner.reset(token)


class MockDefaultProjectsService:
    def can_read_project(self, pid, owner_key, role):
        return pid == "proj_allowed"

class MockProjectsServiceModule:
    default_projects_service = MockDefaultProjectsService()

    @staticmethod
    def owner_key_for_context(context):
        return "mock_owner_key"

def test_asset_visible_with_project_permissions(monkeypatch):
    auth_token = current_auth_context.set(SimpleNamespace(role="editor", identity_domain="test", subject="sub"))

    try:
        # Since it uses `from gw.projects_hub import service as projects_service_module` inside the function block
        import gw.projects_hub.service
        monkeypatch.setattr(gw.projects_hub.service, "owner_key_for_context", MockProjectsServiceModule.owner_key_for_context)
        monkeypatch.setattr(gw.projects_hub.service, "default_projects_service", MockDefaultProjectsService())

        assert asset_visible({"project_id": "proj_allowed"}) is True
        assert asset_visible({"project_ids": ["proj_denied", "proj_allowed"]}) is True
        assert asset_visible({"project_id": "proj_denied"}) is False

    finally:
        current_auth_context.reset(auth_token)

class MockDefaultProjectsServiceErr:
    def can_read_project(self, pid, owner_key, role):
        return owner_key is None and pid == "proj_allowed"

def mock_owner_key_err(context):
    raise Exception("mock error")

def test_asset_visible_project_exception(monkeypatch):
    auth_token = current_auth_context.set(SimpleNamespace(role="editor"))

    try:
        import gw.projects_hub.service
        monkeypatch.setattr(gw.projects_hub.service, "owner_key_for_context", mock_owner_key_err)
        monkeypatch.setattr(gw.projects_hub.service, "default_projects_service", MockDefaultProjectsServiceErr())

        assert asset_visible({"project_id": "proj_allowed"}) is True
        assert asset_visible({"project_id": "proj_denied"}) is False
    finally:
        current_auth_context.reset(auth_token)

def test_asset_visible_old_bridge_url():
    assert asset_visible({"url": "/api/god_workflow/assets/123"}) is False
    assert asset_visible({"url": "/pages/workflow.html?id=abc"}) is False
    assert asset_visible({"url": "/some/other/url.mp4"}) is True
    assert asset_visible({}) is True

@pytest.mark.anyio
async def test_asset_request_scope(monkeypatch):
    def mock_require_authenticated(auth, role):
        return SimpleNamespace(identity_domain="d", subject="s", role="admin")

    monkeypatch.setattr("gw.core.asset_visibility.require_authenticated", mock_require_authenticated)

    assert current_asset_owner.get() is None
    assert current_auth_context.get() is None

    async def mock_endpoint():
        owner = current_asset_owner.get()
        ctx = current_auth_context.get()
        assert owner is not None
        assert ctx is not None
        assert ctx.role == "admin"

    gen = asset_request_scope(authorization="Bearer xxx", x_user_role="admin")
    await gen.__anext__()
    await mock_endpoint()
    try:
        await gen.__anext__()
    except StopAsyncIteration:
        pass

    assert current_asset_owner.get() is None
    assert current_auth_context.get() is None
