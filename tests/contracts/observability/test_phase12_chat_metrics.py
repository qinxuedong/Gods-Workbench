"""Phase 12 R3：服务端 Provider 解析与 Chat Completions 吞吐契约。"""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.settings import chat, execution_config

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
READONLY = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "readonly"}
SECRET = "chat-secret-test-only"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """使用每例隔离的测试根和显式本地 Bearer 测试认证。"""
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    with TestClient(create_app()) as value:
        yield value


def _configure(monkeypatch, *, provider_id="provider-a", base_url="http://127.0.0.1:8123/v1",
              model="server-model", models=None, secret=SECRET):
    definition = {"base_url": base_url, "api_key_env": "GW_TEST_CHAT_SECRET"}
    if models is None:
        definition["model"] = model
    else:
        definition["models"] = models
        definition["default_model"] = model
    monkeypatch.setenv("GW_PROVIDER_RUNTIME_JSON", json.dumps({provider_id: definition}))
    monkeypatch.setenv("GW_TEST_CHAT_SECRET", secret)
    monkeypatch.delenv("GW_CHAT_BASE_URL", raising=False)
    monkeypatch.delenv("GW_CHAT_API_KEY", raising=False)
    monkeypatch.delenv("GW_CHAT_MODEL", raising=False)


def _install_http(monkeypatch, *, payload=None, status=200, error=None):
    calls = []
    response_payload = payload if payload is not None else {
        "choices": [{"message": {"content": "受控真实响应"}}],
        "usage": {"completion_tokens": 12},
        "model": "upstream-echo-is-not-authority",
    }

    class Response:
        status_code = status

        def json(self):
            if error:
                raise error
            return response_payload

    class Client:
        def __init__(self, *, timeout, follow_redirects):
            calls.append({"timeout": timeout, "follow_redirects": follow_redirects})

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def post(self, url, *, json, headers):
            calls.append({"url": url, "json": json, "headers": headers})
            if error == "raise":
                raise TimeoutError("secret must not escape")
            return Response()

    monkeypatch.setattr(chat, "_httpx", lambda _endpoint, _config: SimpleNamespace(Client=Client))
    return calls


def _clock(monkeypatch, start=10.0, end=12.0):
    values = iter((start, end))
    monkeypatch.setattr(chat, "time", SimpleNamespace(monotonic=lambda: next(values)))


def test_runtime_config_exposes_only_provider_and_model_allowlist(monkeypatch):
    _configure(monkeypatch, models=["model-a", "model-b"], model="model-b")
    public = execution_config.public_configuration()
    assert public["configured"] is True
    assert public["default_provider_id"] == "provider-a"
    assert public["providers"] == [{
        "provider_id": "provider-a",
        "models": ["model-a", "model-b"],
        "default_model": "model-b",
        "configured": True,
        "source": "runtime_mapping",
        "video_protocol": None,
    }]
    serialized = json.dumps(public)
    assert SECRET not in serialized
    assert "base_url" not in serialized and "api_key_env" not in serialized


def test_api_endpoint_url_does_not_duplicate_v1():
    assert execution_config.api_endpoint_url("https://example.invalid", "/v1/chat/completions") == \
        "https://example.invalid/v1/chat/completions"
    assert execution_config.api_endpoint_url("https://example.invalid/v1", "/v1/chat/completions") == \
        "https://example.invalid/v1/chat/completions"
    assert execution_config.api_endpoint_url("https://example.invalid/prefix/v1/", "/v1/video/generations") == \
        "https://example.invalid/prefix/v1/video/generations"


def test_chat_success_uses_verified_provider_usage_and_never_returns_raw(monkeypatch, client):
    _configure(monkeypatch, base_url="http://127.0.0.1:8123/v1")
    _clock(monkeypatch, 100.0, 102.5)
    payload = {
        "choices": [{"message": {"content": "服务端核验后的回复"}}],
        "usage": {"completion_tokens": 25},
        "model": "client-independent-upstream-echo",
        "api_key": SECRET,
        "raw_private_dump": "不得透传",
    }
    calls = _install_http(monkeypatch, payload=payload)

    response = client.post("/api/chat", headers=AUTH, json={
        "message": "测试提示",
        "provider_id": "provider-a",
        "provider": "caller-forged-label",
        "model": "caller-forged-model",
        "max_completion_tokens": 999_999,
    })

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["provider_id"] == "provider-a"
    assert data["model"] == "server-model"
    assert "raw" not in data and "raw_private_dump" not in json.dumps(data)
    assert SECRET not in response.text
    assert len([call for call in calls if "url" in call]) == 1
    request = next(call for call in calls if "url" in call)
    assert request["url"] == "http://127.0.0.1:8123/v1/chat/completions"
    assert request["json"]["model"] == "server-model"
    assert request["json"]["max_completion_tokens"] == 512
    assert request["json"]["stream"] is False
    assert request["headers"]["Authorization"] == f"Bearer {SECRET}"
    metrics = data["metrics"]
    assert metrics == {
        "kind": "end_to_end_output_tokens_per_second",
        "provider_id": "provider-a",
        "model": "server-model",
        "output_tokens": 25,
        "elapsed_seconds": 2.5,
        "tokens_per_second": 10.0,
        "usage_source": "chat_completions.usage.completion_tokens",
        "unavailable_reason": None,
    }


@pytest.mark.parametrize(
    ("usage", "expected_reason"),
    [
        (None, "usage_missing"),
        ({}, "usage_missing"),
        ({"completion_tokens": True}, "usage_invalid"),
        ({"completion_tokens": False}, "usage_invalid"),
        ({"completion_tokens": -1}, "usage_invalid"),
        ({"completion_tokens": 12.0}, "usage_invalid"),
        ({"completion_tokens": "12"}, "usage_invalid"),
        ({"completion_tokens": float("nan")}, "usage_invalid"),
        ({"completion_tokens": float("inf")}, "usage_invalid"),
    ],
)
def test_invalid_or_missing_completion_usage_is_unavailable(monkeypatch, client, usage, expected_reason):
    _configure(monkeypatch)
    _clock(monkeypatch, 4.0, 6.0)
    body = {"choices": [{"message": {"content": "ok"}}]}
    if usage is not None:
        body["usage"] = usage
    _install_http(monkeypatch, payload=body)

    response = client.post("/api/chat", headers=AUTH, json={"message": "x", "provider_id": "provider-a"})

    assert response.status_code == 200, response.text
    metrics = response.json()["metrics"]
    assert metrics["output_tokens"] is None
    assert metrics["elapsed_seconds"] == 2.0
    assert metrics["tokens_per_second"] is None
    assert metrics["unavailable_reason"] == expected_reason


def test_zero_output_tokens_yield_valid_zero_rate(monkeypatch, client):
    _configure(monkeypatch)
    _clock(monkeypatch, 7.0, 9.0)
    _install_http(monkeypatch, payload={"choices": [{"message": {"content": ""}}],
                                        "usage": {"completion_tokens": 0}})
    response = client.post("/api/chat", headers=AUTH, json={"message": "x", "provider_id": "provider-a"})
    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert metrics["output_tokens"] == 0
    assert metrics["tokens_per_second"] == 0
    assert metrics["unavailable_reason"] is None


@pytest.mark.parametrize(("start", "end"), [(10.0, 10.0), (10.0, 9.0), (10.0, float("inf")), (10.0, float("nan"))])
def test_invalid_elapsed_never_yields_rate(monkeypatch, client, start, end):
    _configure(monkeypatch)
    _clock(monkeypatch, start, end)
    _install_http(monkeypatch)
    response = client.post("/api/chat", headers=AUTH, json={"message": "x", "provider_id": "provider-a"})
    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert metrics["output_tokens"] == 12
    assert metrics["elapsed_seconds"] is None
    assert metrics["tokens_per_second"] is None
    assert metrics["unavailable_reason"] == "elapsed_invalid"


def test_unknown_client_provider_never_relabels_legacy_environment(monkeypatch, client):
    monkeypatch.delenv("GW_PROVIDER_RUNTIME_JSON", raising=False)
    monkeypatch.setenv("GW_CHAT_BASE_URL", "http://127.0.0.1:8123")
    monkeypatch.setenv("GW_CHAT_API_KEY", SECRET)
    monkeypatch.setenv("GW_CHAT_MODEL", "legacy-model")
    calls = _install_http(monkeypatch)

    response = client.post("/api/chat", headers=AUTH, json={
        "message": "x", "provider_id": "forged-provider", "provider": "forged-provider",
    })

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CHAT_PROVIDER_NOT_CONFIGURED"
    assert response.json()["detail"]["metrics"]["provider_id"] is None
    assert response.json()["detail"]["metrics"]["unavailable_reason"] == "provider_not_configured"
    assert not [call for call in calls if "url" in call]
    assert SECRET not in response.text


def test_legacy_environment_is_server_attributed_not_client_labeled(monkeypatch, client):
    monkeypatch.delenv("GW_PROVIDER_RUNTIME_JSON", raising=False)
    monkeypatch.setenv("GW_CHAT_BASE_URL", "http://127.0.0.1:8123/v1")
    monkeypatch.setenv("GW_CHAT_API_KEY", SECRET)
    monkeypatch.setenv("GW_CHAT_MODEL", "legacy-model")
    _clock(monkeypatch)
    calls = _install_http(monkeypatch)

    response = client.post("/api/chat", headers=AUTH, json={
        "message": "x", "provider": "caller-pretends-vendor", "model": "caller-model",
    })

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["provider_id"] == execution_config.LEGACY_PROVIDER_ID
    assert data["model"] == "legacy-model"
    assert data["metrics"]["provider_id"] == execution_config.LEGACY_PROVIDER_ID
    assert data["metrics"]["model"] == "legacy-model"
    request = next(call for call in calls if "url" in call)
    assert request["url"] == "http://127.0.0.1:8123/v1/chat/completions"


def test_unlisted_model_is_rejected_for_multi_model_provider(monkeypatch, client):
    _configure(monkeypatch, models=["model-a", "model-b"], model="model-a")
    calls = _install_http(monkeypatch)
    response = client.post("/api/chat/agent", headers=AUTH, json={
        "message": "x", "provider_id": "provider-a", "model": "model-c",
    })
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CHAT_MODEL_NOT_CONFIGURED"
    assert response.json()["detail"]["metrics"]["unavailable_reason"] == "model_not_configured"
    assert not [call for call in calls if "url" in call]


def test_multiple_providers_require_explicit_selection(monkeypatch, client):
    monkeypatch.setenv("GW_PROVIDER_RUNTIME_JSON", json.dumps({
        "provider-a": {"base_url": "http://127.0.0.1:8123", "api_key_env": "GW_KEY_A", "model": "a"},
        "provider-b": {"base_url": "http://127.0.0.1:8124", "api_key_env": "GW_KEY_B", "model": "b"},
    }))
    monkeypatch.setenv("GW_KEY_A", SECRET)
    monkeypatch.setenv("GW_KEY_B", "other-test-secret")
    calls = _install_http(monkeypatch)
    response = client.post("/api/chat", headers=AUTH, json={"message": "x", "provider": "provider-a"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CHAT_PROVIDER_SELECTION_REQUIRED"
    assert not [call for call in calls if "url" in call]


def test_client_base_url_or_credential_is_rejected_without_network(monkeypatch, client):
    _configure(monkeypatch)
    calls = _install_http(monkeypatch)
    response = client.post("/api/chat", headers=AUTH, json={
        "message": "x", "base_url": "http://127.0.0.1:8123", "api_key": SECRET,
    })
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"
    assert response.json()["detail"]["metrics"]["unavailable_reason"] == "client_provider_configuration_rejected"
    assert SECRET not in response.text
    assert not [call for call in calls if "url" in call]


def test_upstream_failure_returns_explicit_null_metrics_and_does_not_retry(monkeypatch, client):
    _configure(monkeypatch)
    calls = _install_http(monkeypatch, error="raise")
    response = client.post("/api/chat", headers=AUTH, json={"message": "x", "provider_id": "provider-a"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "CHAT_PROVIDER_FAILED"
    assert detail["metrics"]["provider_id"] == "provider-a"
    assert detail["metrics"]["output_tokens"] is None
    assert detail["metrics"]["elapsed_seconds"] is None
    assert detail["metrics"]["tokens_per_second"] is None
    assert detail["metrics"]["unavailable_reason"] == "upstream_error"
    assert len([call for call in calls if "url" in call]) == 1
    assert SECRET not in response.text and "secret must not escape" not in response.text


def test_capacity_limit_fails_without_second_provider_call(monkeypatch, client):
    _configure(monkeypatch)
    calls = _install_http(monkeypatch)
    acquired = [_ for _ in range(chat.MAX_CONCURRENT_REQUESTS) if chat._CHAT_SLOTS.acquire(blocking=False)]
    try:
        response = client.post("/api/chat", headers=AUTH, json={"message": "x", "provider_id": "provider-a"})
    finally:
        for _ in acquired:
            chat._CHAT_SLOTS.release()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CHAT_CAPACITY_EXCEEDED"
    assert response.json()["detail"]["metrics"]["unavailable_reason"] == "concurrency_limit"
    assert not [call for call in calls if "url" in call]


def test_configuration_get_has_no_provider_side_effect_and_no_secret_fields(monkeypatch, client):
    _configure(monkeypatch)
    calls = _install_http(monkeypatch)
    response = client.get("/api/chat/config", headers=READONLY)
    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is True and data["default_provider_id"] == "provider-a"
    assert SECRET not in response.text
    assert "base_url" not in response.text and "api_key_env" not in response.text
    assert not [call for call in calls if "url" in call]


def test_unauthenticated_and_readonly_chat_writes_are_denied(monkeypatch, client):
    _configure(monkeypatch)
    calls = _install_http(monkeypatch)
    assert client.post("/api/chat", json={"message": "x", "provider_id": "provider-a"}).status_code == 401
    assert client.post("/api/chat", headers=READONLY,
                       json={"message": "x", "provider_id": "provider-a"}).status_code == 403
    assert not [call for call in calls if "url" in call]


def test_extreme_integer_usage_overflow_is_unavailable_not_server_error():
    result = chat._build_metrics({"usage": {"completion_tokens": 10 ** 400}}, 1.0, 2.0, "provider-a", "model-a")
    assert result["tokens_per_second"] is None
    assert result["unavailable_reason"] == "rate_invalid"
