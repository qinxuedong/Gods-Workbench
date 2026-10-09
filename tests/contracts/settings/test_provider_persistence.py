"""API配置闭环：持久性、秘密生命周期、严格请求与CAS。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_settings
from gw.api.app import create_app
from gw.core import storage
from gw.core.errors import CleanroomException
from gw.settings.service import ProviderService
from gw.settings import execution_config

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


@pytest.fixture(autouse=True)
def clear_external_provider_configuration(monkeypatch):
    for name in ("GW_PROVIDER_RUNTIME_JSON", "GW_CHAT_BASE_URL", "GW_CHAT_API_KEY", "GW_CHAT_MODEL"):
        monkeypatch.delenv(name, raising=False)


def entry(**overrides):
    return {"id": "synthetic", "name": "合成平台", "base_url": "https://example.invalid/v1",
            "protocol": "openai", "chat_models": ["synthetic-chat"], **overrides}


def test_restart_preserves_metadata_and_key_without_plaintext(tmp_path):
    first = ProviderService().replace([entry(api_key="synthetic-secret-only")], 1)
    restored = ProviderService().get_snapshot()
    assert restored == first
    assert restored.revision == 2
    assert restored.providers[0]["has_key"] is True
    assert ProviderService().credential("synthetic") == "synthetic-secret-only"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"synthetic-secret-only" not in path.read_bytes()
    assert "synthetic-secret-only" not in restored.model_dump_json()


def test_fresh_python_process_restores_protected_configuration():
    ProviderService().replace([entry(api_key="synthetic-process-secret")], 1)
    script = """
from gw.settings.service import ProviderService
service = ProviderService()
assert service.get_snapshot().revision == 2
assert service.get_snapshot().providers[0]['has_key'] is True
assert service.credential('synthetic') == 'synthetic-process-secret'
print('protected_configuration_restored')
"""
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3])}
    result = subprocess.run([sys.executable, "-P", "-c", script], env=environment,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "protected_configuration_restored"


def test_secret_keep_replace_clear_and_provider_removal():
    service = ProviderService()
    service.replace([entry(api_key="synthetic-first")], 1)
    service.replace([entry(name="改名", api_key="")], 2)
    assert service.credential("synthetic") == "synthetic-first"
    service.replace([entry(api_key="synthetic-second")], 3)
    assert service.credential("synthetic") == "synthetic-second"
    service.replace([entry(clear_key=True)], 4)
    assert service.credential("synthetic") == ""
    assert not service.get_snapshot().providers[0]["has_key"]
    service.replace([entry(api_key="synthetic-third")], 5)
    service.replace([], 6)
    assert ProviderService().credential("synthetic") == ""


def test_two_service_instances_share_cas_and_rejected_write_keeps_secret():
    one, two = ProviderService(), ProviderService()
    one.replace([entry(api_key="synthetic-kept")], 1)
    with pytest.raises(CleanroomException) as exc:
        two.replace([entry(api_key="synthetic-lost")], 1)
    assert exc.value.status_code == 409
    assert two.credential("synthetic") == "synthetic-kept"
    assert two.get_snapshot().revision == 2


@pytest.mark.parametrize("payload", [
    {"providersX": []}, {"providers": [], "other": "synthetic-alias"},
    [entry(openai_key="synthetic-alias")], [entry(model_names={"model": {"secret": "synthetic-alias"}})],
    [entry(model_names={"openai_key": "synthetic-alias"})], [entry(image_edit_endpoint="https://user:synthetic-alias@example.invalid")],
    [entry(id="../escape")], [entry(provider_id="other")],
    [entry(api_key="synthetic-alias", clear_key=True)],
])
def test_invalid_requests_cannot_clear_or_leak_configuration(monkeypatch, payload):
    monkeypatch.setattr(routes_settings, "default_provider_service", ProviderService())
    with TestClient(create_app()) as client:
        original = client.put("/api/providers?expected_version=1", json=[entry()], headers=AUTH)
        assert original.status_code == 200
        rejected = client.put("/api/providers?expected_version=2", json=payload, headers=AUTH)
        assert rejected.status_code == 400
        assert rejected.json()["detail"]["code"] == "INVALID_REQUEST"
        assert "synthetic-alias" not in rejected.text
        assert client.get("/api/providers", headers=AUTH).json() == original.json()


def test_version_is_required_even_for_empty_replacement(monkeypatch):
    monkeypatch.setattr(routes_settings, "default_provider_service", ProviderService())
    with TestClient(create_app()) as client:
        response = client.put("/api/providers", json=[], headers=AUTH)
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "EXPECTED_VERSION_REQUIRED"
        assert client.get("/api/providers", headers=AUTH).json()["revision"] == 1


def test_corrupt_state_fails_closed_without_overwriting():
    root = storage.data_root()
    root.mkdir(parents=True)
    path = root / "providers.json"
    path.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(CleanroomException) as exc:
        ProviderService().replace([], 1)
    assert exc.value.status_code == 503
    assert path.read_text(encoding="utf-8") == "{corrupt"


def test_data_roots_do_not_share_configuration(monkeypatch, tmp_path):
    service = ProviderService()
    service.replace([entry(api_key="synthetic-isolated")], 1)
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "other-data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "other-auth/auth.sqlite3"))
    assert service.get_snapshot().providers == []
    assert service.credential("synthetic") == ""


def test_failure_to_write_keeps_old_key_and_revision(monkeypatch):
    service = ProviderService()
    before = service.replace([entry(api_key="synthetic-before")], 1)
    def unavailable(*args):
        raise OSError("synthetic-storage-error")
    monkeypatch.setattr(storage, "save", unavailable)
    with pytest.raises(CleanroomException) as exc:
        service.replace([entry(api_key="synthetic-after")], 2)
    assert exc.value.status_code == 503
    assert service.get_snapshot() == before
    assert service.credential("synthetic") == "synthetic-before"


def test_persisted_provider_is_used_by_actual_chat_http_after_service_restart(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers.get("Authorization"), body["model"]))
            payload = json.dumps({"choices": [{"message": {"content": "本机受控响应"}}],
                                  "usage": {"completion_tokens": 3}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(routes_settings, "default_provider_service", ProviderService())
        with TestClient(create_app()) as client:
            response = client.put("/api/providers?expected_version=1", headers=AUTH,
                json=[entry(base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="synthetic-chat-secret")])
            assert response.status_code == 200
            monkeypatch.setattr(routes_settings, "default_provider_service", ProviderService())
            result = client.post("/api/chat", headers=AUTH, json={"message": "测试", "provider_id": "synthetic"})
            assert result.status_code == 200, result.text
            assert result.json()["provider_id"] == "synthetic"
            assert result.json()["model"] == "synthetic-chat"
            assert "synthetic-chat-secret" not in result.text
            assert received == [("/v1/chat/completions", "Bearer synthetic-chat-secret", "synthetic-chat")]
            client.put("/api/providers?expected_version=2", headers=AUTH, json=[entry(clear_key=True)])
            missing = client.post("/api/chat", headers=AUTH, json={"message": "测试", "provider_id": "synthetic"})
            assert missing.status_code == 503
            assert len(received) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_saved_config_overrides_environment_and_models_are_capability_scoped(monkeypatch):
    monkeypatch.setenv("GW_PROVIDER_RUNTIME_JSON", json.dumps({"synthetic": {
        "base_url": "https://old.invalid", "api_key_env": "GW_SYNTHETIC_ENV", "model": "old-model"}}))
    ProviderService().replace([entry(video_models=["synthetic-video"], video_protocol="newapi_video",
                                     api_key="synthetic-vault")], 1)
    chat = execution_config.resolve_provider("synthetic", capability="chat")
    video = execution_config.resolve_provider("synthetic", capability="video")
    assert chat.source == video.source == "settings"
    assert chat.base_url == "https://example.invalid/v1"
    assert chat.models == ("synthetic-chat",)
    assert video.models == ("synthetic-video",)
    assert execution_config.provider_api_key(video) == "synthetic-vault"
    ProviderService().replace([entry(enabled=False)], 2)
    assert execution_config.resolve_provider("synthetic") is None


def test_runtime_cannot_send_new_secret_to_old_url_after_concurrent_config_change():
    service = ProviderService()
    service.replace([entry(api_key="synthetic-old")], 1)
    old_request = execution_config.resolve_provider("synthetic")
    service.replace([entry(base_url="https://different.invalid", api_key="synthetic-new")], 2)
    assert execution_config.provider_api_key(old_request) == ""
    current = execution_config.resolve_provider("synthetic")
    assert current.base_url == "https://different.invalid/v1"
    assert execution_config.provider_api_key(current) == "synthetic-new"


@pytest.mark.parametrize("protocol", ["gemini", "volcengine", "codex", "gemini-cli", "jimeng"])
def test_unsupported_saved_chat_protocol_is_not_sent_to_openai(protocol):
    ProviderService().replace([entry(protocol=protocol)], 1)
    assert execution_config.resolve_provider("synthetic") is None


def test_tampered_ciphertext_does_not_appear_saved_or_execute():
    service = ProviderService()
    service.replace([entry(api_key="synthetic-original")], 1)
    state = storage.load("providers")
    state["credentials"]["synthetic"]["api_key"] = "dpapi:corrupt"
    storage.save("providers", state)
    with pytest.raises(CleanroomException) as exc:
        service.get_snapshot()
    assert exc.value.code == "PROVIDER_CREDENTIAL_UNAVAILABLE"
    assert execution_config.public_configuration()["configuration_status"] == "misconfigured"


def test_all_secret_fields_are_protected_and_independently_cleared():
    service = ProviderService()
    values = {"api_key": "synthetic-api", "wallet_api_key": "synthetic-wallet",
              "volcengine_access_key_id": "synthetic-ak", "volcengine_secret_access_key": "synthetic-sk"}
    public = service.replace([entry(**values)], 1)
    for field, secret in values.items():
        assert service.credential("synthetic", field) == secret
        assert secret not in json.dumps(storage.load("providers"))
        assert secret not in public.model_dump_json()
    result = service.replace([entry(clear_wallet_key=True)], 2)
    assert not result.providers[0]["has_wallet_key"]
    assert result.providers[0]["has_key"]
    assert result.providers[0]["has_volcengine_access_key"]
    assert result.providers[0]["has_volcengine_secret_key"]


@pytest.mark.parametrize("name", ["providers.json", "providers.json.tmp"])
def test_linked_state_or_temp_file_is_rejected_before_access(tmp_path, name):
    root = storage.data_root()
    root.mkdir(parents=True)
    target = tmp_path / "unrelated.json"
    target.write_text("unrelated", encoding="utf-8")
    try:
        (root / name).symlink_to(target)
    except OSError as exc:
        pytest.skip(f"平台不允许符号链接：{type(exc).__name__}")
    with pytest.raises(CleanroomException) as exc:
        ProviderService().replace([], 1)
    assert exc.value.code == "PROVIDER_STORAGE_UNAVAILABLE"
    assert target.read_text(encoding="utf-8") == "unrelated"


def test_probe_reuses_saved_secret_only_at_saved_url_and_drops_arbitrary_upstream_data():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            received.append(self.headers.get("Authorization"))
            payload = json.dumps({"data": [{"id": "synthetic-chat", "nested": {"openai_key": "synthetic-alias"}},
                                           {"id": "synthetic-saved-probe"}],
                                  "openai_key": "synthetic-alias", "arbitrary": "synthetic-alias"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        ProviderService().replace([entry(base_url=url, api_key="synthetic-saved-probe")], 1)
        with TestClient(create_app()) as client:
            body = {"provider_id": "synthetic", "base_url": url, "api_key": "", "protocol": "openai"}
            response = client.post("/api/providers/probe-async", headers=AUTH, json=body)
            assert response.status_code == 200
            result = client.get(response.json()["poll_hint"], headers=AUTH)
            assert result.status_code == 200
            assert result.json()["task"]["raw"] == {"data": [{"id": "synthetic-chat"}]}
            assert "synthetic-alias" not in result.text
            assert "synthetic-saved-probe" not in result.text
            bad = client.post("/api/providers/fetch-models", headers=AUTH,
                              json={**body, "base_url": url + "/different"})
            assert bad.status_code == 400
            assert bad.json()["detail"]["code"] == "PROVIDER_CONFIG_MISMATCH"
            assert received == ["Bearer synthetic-saved-probe"]
            assert "synthetic-alias" not in json.dumps(storage.load("provider_probes"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
