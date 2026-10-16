"""C6 回归测试共享夹具。

夹具只服务本目录，复用已审查的媒体双替身；不引入运行时依赖。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

_MEDIA_DOUBLE = Path(__file__).resolve().parents[1] / "media_double"
if str(_MEDIA_DOUBLE) not in sys.path:
    sys.path.insert(0, str(_MEDIA_DOUBLE))

from media_doubles import (  # noqa: E402
    APPROVED_HOST,
    TEST_API_KEY,
    FakeComfy,
    FakeRunningHub,
    NetworkBlocked,
    count_assets,
    install_fake_resolver,
    install_output_transport,
)


@pytest.fixture
def no_network(monkeypatch):
    """未被替身覆盖的外部 HTTP 必须失败。"""
    attempts: list[str] = []

    async def refuse_async(self, request, *args, **kwargs):
        if isinstance(getattr(self, "_transport", None), httpx.MockTransport):
            return await original_async(self, request, *args, **kwargs)
        attempts.append(str(request.url))
        raise NetworkBlocked(f"测试替身禁止真实网络: {request.url.host}")

    def refuse_sync(self, request, *args, **kwargs):
        if isinstance(getattr(self, "_transport", None), httpx.MockTransport) or request.url.host == "testserver":
            return original_sync(self, request, *args, **kwargs)
        attempts.append(str(request.url))
        raise NetworkBlocked(f"测试替身禁止真实网络: {request.url.host}")

    original_async = httpx.AsyncClient.send
    original_sync = httpx.Client.send
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_async)
    monkeypatch.setattr(httpx.Client, "send", refuse_sync)
    return attempts


def _mode(request) -> str:
    return getattr(request, "param", "success")


@pytest.fixture
def fake_rh(monkeypatch, request) -> FakeRunningHub:
    fake = FakeRunningHub(_mode(request))
    fake.install(monkeypatch)
    return fake


@pytest.fixture
def fake_comfy(monkeypatch, request) -> FakeComfy:
    fake = FakeComfy(_mode(request))
    fake.install(monkeypatch)
    return fake


@pytest.fixture
def media_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(data))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setenv("GW_COMFYUI_URL", "http://fake-comfy.invalid")
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", APPROVED_HOST)
    monkeypatch.setenv("GW_RUNNINGHUB_API_KEY", TEST_API_KEY)
    install_output_transport(monkeypatch)
    install_fake_resolver(monkeypatch)
    return data


@pytest.fixture
def media_client(media_env):
    from gw.api.app import create_app

    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def fake_media(fake_rh, fake_comfy, no_network):
    return SimpleNamespace(rh=fake_rh, comfy=fake_comfy, network_attempts=no_network)


@pytest.fixture
def asset_counts(media_client):
    return lambda: count_assets(media_client)
