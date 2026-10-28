"""QIN-411/412 服务端原生目录选择器契约（GitHub #356）。

覆盖：认证与管理员边界、回环限制、三态结果（selected/cancelled/unavailable）、
超时保护与请求体校验；不弹出真实对话框，全部经替换点驱动。
"""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_settings as routes
from gw.api.app import create_app
from gw.settings import native_picker
from gw.settings.service import (
    AssetStructureService,
    ProviderService,
    StorageSettingsService,
)

ADMIN = {"Authorization": "Bearer qin432-admin", "X-User-Role": "admin"}
EDITOR = {"Authorization": "Bearer qin432-editor", "X-User-Role": "editor"}


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(routes, "default_storage_settings_service", StorageSettingsService())
    monkeypatch.setattr(routes, "default_provider_service", ProviderService())
    monkeypatch.setattr(routes, "default_asset_structure_service", AssetStructureService())
    return TestClient(create_app())


def _as_loopback(monkeypatch) -> None:
    monkeypatch.setattr(routes, "_is_loopback_request", lambda request: True)


def test_pick_native_requires_authentication_and_admin(monkeypatch):
    _as_loopback(monkeypatch)
    with _client(monkeypatch) as client:
        assert (
            client.post("/api/settings/directories/pick-native", json={}).status_code == 401
        )
        response = client.post(
            "/api/settings/directories/pick-native", json={}, headers=EDITOR
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "FORBIDDEN"


def test_pick_native_rejects_non_loopback_sessions(monkeypatch):
    """TestClient 的来源不是回环地址，应被拒之门外且不在服务器弹窗。"""
    called = False

    def forbidden_call(*args, **kwargs):
        nonlocal called
        called = True
        return {"status": "selected", "path": "<不应触达>", "reason": None}

    monkeypatch.setattr(native_picker, "pick_directory", forbidden_call)
    with _client(monkeypatch) as client:
        response = client.post(
            "/api/settings/directories/pick-native", json={}, headers=ADMIN
        )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "PICKER_NON_LOOPBACK"
    assert called is False


def test_pick_native_returns_selected_path(monkeypatch):
    _as_loopback(monkeypatch)

    def fake_pick(title, initial_dir, timeout_seconds):
        assert title == "选择服务器目录"
        assert initial_dir == ""
        assert timeout_seconds == 60
        return {"status": "selected", "path": "<所选路径>", "reason": None}

    monkeypatch.setattr(native_picker, "pick_directory", fake_pick)
    with _client(monkeypatch) as client:
        response = client.post(
            "/api/settings/directories/pick-native", json={}, headers=ADMIN
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "selected"
    assert body["path"] == "<所选路径>"
    assert body["reason"] is None


def test_pick_native_returns_cancelled_without_error(monkeypatch):
    _as_loopback(monkeypatch)
    monkeypatch.setattr(
        native_picker,
        "pick_directory",
        lambda title, initial_dir, timeout_seconds: {
            "status": "cancelled",
            "path": None,
            "reason": None,
        },
    )
    with _client(monkeypatch) as client:
        response = client.post(
            "/api/settings/directories/pick-native",
            json={"title": "选择上传目录", "initial_dir": "<初始目录>"},
            headers=ADMIN,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["path"] is None


def test_pick_native_reports_unavailable_with_reason(monkeypatch):
    _as_loopback(monkeypatch)
    monkeypatch.setattr(
        native_picker,
        "pick_directory",
        lambda title, initial_dir, timeout_seconds: {
            "status": "unavailable",
            "path": None,
            "reason": "no-display",
        },
    )
    with _client(monkeypatch) as client:
        response = client.post(
            "/api/settings/directories/pick-native", json={}, headers=ADMIN
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["reason"] == "no-display"


def test_pick_native_timeout_is_reported_not_hanging(monkeypatch):
    """超时秒数透传给选择器；超时按 unavailable 返回而非挂起或报错。"""
    _as_loopback(monkeypatch)
    seen: dict[str, int] = {}

    def fake_pick(title, initial_dir, timeout_seconds):
        seen["timeout_seconds"] = timeout_seconds
        return {"status": "unavailable", "path": None, "reason": "timeout"}

    monkeypatch.setattr(native_picker, "pick_directory", fake_pick)
    with _client(monkeypatch) as client:
        response = client.post(
            "/api/settings/directories/pick-native",
            json={"timeout_seconds": 5},
            headers=ADMIN,
        )
    assert response.status_code == 200, response.text
    assert seen["timeout_seconds"] == 5
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["reason"] == "timeout"


def test_pick_native_child_timeout_maps_to_unavailable(monkeypatch):
    """子进程超时被转换为 unavailable，不向上抛异常。"""
    def hang_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="picker", timeout=1)

    monkeypatch.setattr(native_picker.subprocess, "run", hang_run)
    result = native_picker.pick_directory("标题", "", 5)
    assert result == {"status": "unavailable", "path": None, "reason": "timeout"}


def test_pick_native_rejects_unknown_fields_and_bad_timeout(monkeypatch):
    _as_loopback(monkeypatch)
    with _client(monkeypatch) as client:
        extra = client.post(
            "/api/settings/directories/pick-native",
            json={"path": "<未授权字段>"},
            headers=ADMIN,
        )
        assert extra.status_code == 400
        assert extra.json()["detail"]["code"] == "INVALID_REQUEST"
        small = client.post(
            "/api/settings/directories/pick-native",
            json={"timeout_seconds": 1},
            headers=ADMIN,
        )
        assert small.status_code == 400
        assert small.json()["detail"]["code"] == "INVALID_REQUEST"
        large = client.post(
            "/api/settings/directories/pick-native",
            json={"timeout_seconds": 301},
            headers=ADMIN,
        )
        assert large.status_code == 400
        assert large.json()["detail"]["code"] == "INVALID_REQUEST"


def test_pick_directory_is_read_only_over_http(monkeypatch):
    _as_loopback(monkeypatch)
    with _client(monkeypatch) as client:
        assert (
            client.get("/api/settings/directories/pick-native", headers=ADMIN).status_code
            == 405
        )


@pytest.mark.parametrize("status_value,reason", [("cancelled", None), ("unavailable", "x")])
def test_pick_native_response_never_fabricates_paths(monkeypatch, status_value, reason):
    _as_loopback(monkeypatch)
    monkeypatch.setattr(
        native_picker,
        "pick_directory",
        lambda title, initial_dir, timeout_seconds: {
            "status": status_value,
            "path": None,
            "reason": reason,
        },
    )
    with _client(monkeypatch) as client:
        response = client.post(
            "/api/settings/directories/pick-native", json={}, headers=ADMIN
        )
    assert response.status_code == 200, response.text
    assert response.json()["path"] is None
