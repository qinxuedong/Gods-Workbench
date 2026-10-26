import json
from contextlib import asynccontextmanager

import httpx
import pytest

from gw.core.errors import CleanroomException
from gw.god_workflow.clients import ProviderClient, get_provider

def mock_async_client_factory(status_code=200, chunks=None, stream_exc=None, aiter_exc=None):
    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        @asynccontextmanager
        async def stream(self, method, url, headers=None, **kwargs):
            if stream_exc:
                raise stream_exc
            class Response:
                def __init__(self):
                    self.status_code = status_code
                async def aiter_bytes(self):
                    if aiter_exc:
                        raise aiter_exc
                    for chunk in (chunks or [b'{"success": true}']):
                        yield chunk
            yield Response()
    return MockClient

def test_configured_property(monkeypatch):
    client = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, True)
    monkeypatch.setenv("API_KEY", "secret")
    assert client.configured is True

    # Missing API Key
    monkeypatch.delenv("API_KEY", raising=False)
    assert client.configured is False

    # Empty API key
    monkeypatch.setenv("API_KEY", "   ")
    assert client.configured is False

    # Invalid URL scheme
    monkeypatch.setenv("API_KEY", "secret")
    client2 = ProviderClient("runninghub", "ftp://valid.com", "API_KEY", 10.0, True)
    assert client2.configured is False

    # Invalid URL with query
    client3 = ProviderClient("runninghub", "https://valid.com?q=1", "API_KEY", 10.0, True)
    assert client3.configured is False

    # Invalid URL with credentials
    client3_1 = ProviderClient("runninghub", "https://user:pass@valid.com", "API_KEY", 10.0, True)
    assert client3_1.configured is False

    # Invalid timeout values
    client4 = ProviderClient("runninghub", "https://valid.com", "API_KEY", -1, True)
    assert client4.configured is False

    client5 = ProviderClient("runninghub", "https://valid.com", "API_KEY", 150, True)
    assert client5.configured is False

    # Disabled
    client6 = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, False)
    assert client6.configured is False


@pytest.mark.anyio
async def test_fetch_happy_path(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    client = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, True)

    def mock_client(*args, **kwargs):
        return mock_async_client_factory(chunks=[b'{"a": 1}'])()

    monkeypatch.setattr("httpx.AsyncClient", mock_client)

    res = await client.fetch("workflow-123")
    assert res == {"a": 1}


@pytest.mark.anyio
async def test_fetch_invalid_workflow_id():
    client = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, True)

    with pytest.raises(CleanroomException) as exc:
        await client.fetch("invalid/id")
    assert exc.value.status_code == 400
    assert exc.value.code == "INVALID_PROVIDER_REFERENCE"


@pytest.mark.anyio
async def test_fetch_not_configured():
    client = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, False)

    with pytest.raises(CleanroomException) as exc:
        await client.fetch("valid-id")
    assert exc.value.status_code == 503
    assert exc.value.code == "WORKFLOW_PROVIDER_UNAVAILABLE"


@pytest.mark.anyio
async def test_fetch_404(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    client = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, True)

    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda *args, **kwargs: mock_async_client_factory(status_code=404)()
    )

    with pytest.raises(CleanroomException) as exc:
        await client.fetch("valid-id")
    assert exc.value.status_code == 404
    assert exc.value.code == "PROVIDER_WORKFLOW_NOT_FOUND"


@pytest.mark.anyio
async def test_fetch_500(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    client = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, True)

    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda *args, **kwargs: mock_async_client_factory(status_code=500)()
    )

    with pytest.raises(CleanroomException) as exc:
        await client.fetch("valid-id")
    assert exc.value.status_code == 502
    assert exc.value.code == "WORKFLOW_PROVIDER_ERROR"


@pytest.mark.anyio
async def test_fetch_timeout(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    client = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, True)

    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda *args, **kwargs: mock_async_client_factory(stream_exc=httpx.TimeoutException("Timeout"))()
    )

    with pytest.raises(CleanroomException) as exc:
        await client.fetch("valid-id")
    assert exc.value.status_code == 504
    assert exc.value.code == "WORKFLOW_PROVIDER_TIMEOUT"


@pytest.mark.anyio
async def test_fetch_http_error(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    client = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, True)

    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda *args, **kwargs: mock_async_client_factory(stream_exc=httpx.HTTPError("Error"))()
    )

    with pytest.raises(CleanroomException) as exc:
        await client.fetch("valid-id")
    assert exc.value.status_code == 502
    assert exc.value.code == "WORKFLOW_PROVIDER_ERROR"


@pytest.mark.anyio
async def test_fetch_invalid_json_type(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    client = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, True)

    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda *args, **kwargs: mock_async_client_factory(chunks=[b'[]'])()
    )

    with pytest.raises(CleanroomException) as exc:
        await client.fetch("valid-id")
    assert exc.value.status_code == 502
    assert exc.value.code == "WORKFLOW_PROVIDER_INVALID_RESPONSE"


@pytest.mark.anyio
async def test_fetch_invalid_json_syntax(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    client = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, True)

    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda *args, **kwargs: mock_async_client_factory(chunks=[b'not a json'])()
    )

    with pytest.raises(CleanroomException) as exc:
        await client.fetch("valid-id")
    assert exc.value.status_code == 502
    assert exc.value.code == "WORKFLOW_PROVIDER_INVALID_RESPONSE"


@pytest.mark.anyio
async def test_fetch_response_too_large(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    client = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, True)

    # chunks total size > 4MB (MAX_PROVIDER_BYTES)
    chunk = b'a' * (1024 * 1024)
    chunks = [chunk, chunk, chunk, chunk, chunk] # 5MB
    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda *args, **kwargs: mock_async_client_factory(chunks=chunks)()
    )

    with pytest.raises(CleanroomException) as exc:
        await client.fetch("valid-id")
    assert exc.value.status_code == 502
    assert exc.value.code == "WORKFLOW_PROVIDER_RESPONSE_TOO_LARGE"


@pytest.mark.anyio
async def test_execute():
    client = ProviderClient("runninghub", "https://valid.com", "API_KEY", 10.0, True)
    with pytest.raises(CleanroomException) as exc:
        await client.execute({})
    assert exc.value.status_code == 503
    assert exc.value.code == "WORKFLOW_EXECUTOR_UNAVAILABLE"


def test_get_provider_invalid():
    with pytest.raises(CleanroomException) as exc:
        get_provider("invalid-provider")
    assert exc.value.status_code == 400
    assert exc.value.code == "INVALID_PROVIDER"


def test_get_provider_valid_runninghub(monkeypatch):
    monkeypatch.setenv("GW_RUNNINGHUB_URL", "https://api.runninghub.ai")
    monkeypatch.setenv("GW_RUNNINGHUB_FETCH_ENABLED", "1")
    monkeypatch.setenv("GW_RUNNINGHUB_TIMEOUT_SECONDS", "20")

    client = get_provider("runninghub")
    assert client.provider == "runninghub"
    assert client.base_url == "https://api.runninghub.ai"
    assert client.api_key_env == "GW_RUNNINGHUB_API_KEY"
    assert client.timeout == 20.0
    assert client.enabled is True


def test_get_provider_default_timeout(monkeypatch):
    monkeypatch.setenv("GW_COMFYUI_URL", "https://comfyui.com")
    monkeypatch.delenv("GW_COMFYUI_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("GW_COMFYUI_FETCH_ENABLED", raising=False)

    client = get_provider("comfyui")
    assert client.timeout == 15.0
    assert client.enabled is False


def test_get_provider_invalid_timeout(monkeypatch):
    monkeypatch.setenv("GW_COMFYUI_TIMEOUT_SECONDS", "invalid")
    client = get_provider("comfyui")
    assert client.timeout == 0.0
