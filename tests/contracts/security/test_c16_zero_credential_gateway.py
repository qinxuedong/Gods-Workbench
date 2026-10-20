# -*- coding: utf-8 -*-
"""C16-AC2 零凭据客户端与服务端出口网关契约。"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_settings
from gw.api.app import create_app
from gw.core import storage
from gw.core.credentials_vault import CredentialVault
from gw.core.errors import CleanroomException
from gw.settings.service import ProviderService

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


@pytest.fixture(autouse=True)
def isolate_runtime(monkeypatch, tmp_path):
    """每例使用独立三态数据根，避免把密文写入开发目录。"""
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_MEDIA_DIR", str(tmp_path / "video"))
    monkeypatch.delenv("GW_PROVIDER_RUNTIME_JSON", raising=False)
    monkeypatch.delenv("GW_CHAT_BASE_URL", raising=False)
    monkeypatch.delenv("GW_CHAT_API_KEY", raising=False)
    monkeypatch.delenv("GW_CHAT_MODEL", raising=False)


def provider(**overrides):
    return {
        "id": "gateway-provider",
        "name": "网关测试平台",
        "base_url": "https://example.invalid/v1",
        "protocol": "openai",
        "chat_models": ["gateway-chat"],
        **overrides,
    }


def test_vault_public_snapshot_is_status_only_and_storage_has_no_plaintext():
    service = ProviderService()
    service.replace([provider(api_key="vault-secret-value")], 1)
    vault = CredentialVault(service)

    public = vault.public_snapshot()
    dumped = json.dumps(public, ensure_ascii=False)
    assert public["configured"] is True
    assert public["providers"] == [
        {"provider_id": "gateway-provider", "fields": {"api_key": True}}
    ]
    assert "vault-secret-value" not in dumped
    assert vault.resolve("gateway-provider") == "vault-secret-value"

    persisted = json.dumps(storage.load("providers"), ensure_ascii=False)
    assert "vault-secret-value" not in persisted


def test_vault_status_marks_provider_without_key_as_not_configured():
    service = ProviderService()
    service.replace([provider()], 1)

    public = CredentialVault(service).public_snapshot()

    assert public["configured"] is False
    assert public["data_status"] == "not_configured"
    assert public["providers"] == [
        {"provider_id": "gateway-provider", "fields": {}}
    ]


def test_vault_rejects_unsupported_or_missing_credentials_without_secret_echo():
    vault = CredentialVault(ProviderService())
    with pytest.raises(CleanroomException) as missing:
        vault.resolve("missing-provider")
    assert missing.value.code == "PROVIDER_CREDENTIAL_UNAVAILABLE"

    with pytest.raises(ValueError):
        vault.resolve("missing-provider", "unsupported_secret")


def test_provider_settings_route_exposes_vault_status_without_secret(monkeypatch):
    service = ProviderService()
    monkeypatch.setattr(routes_settings, "default_provider_service", service)
    with TestClient(create_app()) as client:
        saved = client.put(
            "/api/providers?expected_version=1",
            headers=AUTH,
            json=[provider(api_key="route-secret-value")],
        )
        assert saved.status_code == 200, saved.text

        status = client.get("/api/providers/credentials", headers=AUTH)
        assert status.status_code == 200, status.text
        assert status.json()["providers"] == [
            {"provider_id": "gateway-provider", "fields": {"api_key": True}}
        ]
        assert "route-secret-value" not in status.text



def test_gateway_injects_server_credential_and_rejects_client_credential(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            received.append({
                "authorization": self.headers.get("Authorization"),
                "body": body,
            })
            response = json.dumps({
                "choices": [{"message": {"content": "网关响应"}}],
                "model": "gateway-chat",
                "usage": {"completion_tokens": 2},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        service = ProviderService()
        monkeypatch.setattr(routes_settings, "default_provider_service", service)
        with TestClient(create_app()) as client:
            saved = client.put(
                "/api/providers?expected_version=1",
                headers=AUTH,
                json=[provider(
                    base_url=f"http://127.0.0.1:{upstream.server_port}/v1",
                    api_key="gateway-server-secret",
                )],
            )
            assert saved.status_code == 200, saved.text

            result = client.post(
                "/api/providers/gateway-provider/gateway/chat",
                headers=AUTH,
                json={"message": "你好"},
            )
            assert result.status_code == 200, result.text
            assert result.json()["reply"] == "网关响应"
            assert "gateway-server-secret" not in result.text
            assert received[0]["authorization"] == "Bearer gateway-server-secret"
            assert "gateway-server-secret" not in json.dumps(received[0]["body"])

            rejected = client.post(
                "/api/providers/gateway-provider/gateway/chat",
                headers=AUTH,
                json={"message": "你好", "api_key": "client-secret-must-not-pass"},
            )
            assert rejected.status_code == 400
            assert "client-secret-must-not-pass" not in rejected.text
            assert len(received) == 1
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=3)


def test_gateway_requires_authentication_and_write_role(monkeypatch):
    monkeypatch.setattr(routes_settings, "default_provider_service", ProviderService())
    with TestClient(create_app()) as client:
        no_auth = client.get("/api/providers/credentials")
        assert no_auth.status_code == 401
        readonly = client.post(
            "/api/providers/gateway-provider/gateway/chat",
            headers={"Authorization": "Bearer cleanroom-test", "X-User-Role": "readonly"},
            json={"message": "hello"},
        )
        assert readonly.status_code == 403
