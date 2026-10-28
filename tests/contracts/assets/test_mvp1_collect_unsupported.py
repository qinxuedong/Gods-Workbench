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

"""QIN-436（#385）不支持来源的失败契约。

AC1：blob:、超限 data:、.m3u8/.mpd 清单、已知 DRM 标记统一返回
`COLLECT_UNSUPPORTED_SOURCE` 与中文提示；
AC2：不产生任何 inbox 文件；
AC3：校验发生在网络之前，测试全程不发起真实外联。
"""

import socket

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from gw.asset_registry import collector
from gw.core import storage
from gw.core.errors import CleanroomException

HEADERS = {"Authorization": "Bearer local-test"}
OVERSIZE_INLINE = "data:video/mp4;base64," + "A" * (12 * 1024 * 1024)


def _has_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _inbox_files() -> list[str]:
    root = storage.data_root() / "registry_uploads" / "collect_inbox"
    if not root.exists():
        return []
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


@pytest.fixture()
def api(monkeypatch):
    """隔离的本机收集接口，带配对令牌；数据的落盘根由全局夹具指向 tmp_path。"""
    from gw.api import routes_asset_collect as routes

    app = FastAPI()
    app.include_router(routes.router)

    @app.exception_handler(CleanroomException)
    async def error_handler(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_envelope().model_dump(exclude_none=True),
        )

    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 5000)) as client:
        token = client.post("/api/asset-collect/pair", headers=HEADERS).json()["pair_token"]
        client.pair_token = token
        yield client


def _submit(api, url: str):
    return api.post(
        "/api/asset-collect/url",
        headers=HEADERS,
        json={"url": url, "pair_token": api.pair_token},
    )


@pytest.mark.parametrize(
    "url, reason_hint",
    [
        pytest.param("blob:https://example.com/clip.mp4", "浏览器", id="blob"),
        pytest.param(OVERSIZE_INLINE, "MiB", id="oversized-data"),
        pytest.param("data:text/plain;base64,aGVsbG8=", "内联数据", id="inline-data"),
        pytest.param("https://cdn.example.com/live/index.m3u8", "HLS", id="hls"),
        pytest.param("https://cdn.example.com/live/index.m3u8?auth=1", "HLS", id="hls-query"),
        pytest.param("https://cdn.example.com/vod/manifest.mpd", "HLS", id="dash"),
        pytest.param("https://drm.example.com/movie.mp4", "DRM", id="drm-marker"),
        pytest.param("https://cdn.example.com/widevine/asset.mp4", "DRM", id="widevine"),
        pytest.param("https://cdn.example.com/playready/asset.mp4", "DRM", id="playready"),
        pytest.param("https://cdn.example.com/fairplay/asset.mp4", "DRM", id="fairplay"),
    ],
)
def test_unsupported_sources_fail_with_unified_code(api, url, reason_hint):
    """AC1：四类不支持来源统一错误码 + 中文提示，且 HTTP 400。"""
    before = len(_inbox_files())

    response = _submit(api, url)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == collector.UNSUPPORTED_SOURCE_CODE
    assert reason_hint in detail["message"]
    assert _has_chinese(detail["message"])
    assert len(_inbox_files()) == before


@pytest.mark.parametrize(
    "url",
    [
        "blob:https://example.com/clip.mp4",
        "https://cdn.example.com/live/index.m3u8",
        "https://drm.example.com/movie.mp4",
    ],
)
def test_unsupported_sources_never_touch_network_or_storage(api, monkeypatch, url):
    """AC2/AC3：拒绝发生在下载、入库与建任务之前，且不发起外联。"""
    from gw.api import routes_asset_collect as routes

    def forbidden(*args, **kwargs):
        raise AssertionError("不支持来源不应触发下载、入库或建任务")

    monkeypatch.setattr(collector, "find_ytdlp", forbidden)
    monkeypatch.setattr(collector, "download_direct", forbidden)
    monkeypatch.setattr(collector, "download_with_ytdlp", forbidden)
    monkeypatch.setattr(collector, "store_content", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    jobs_before = len(routes._jobs)
    before = len(_inbox_files())

    response = _submit(api, url)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == collector.UNSUPPORTED_SOURCE_CODE
    assert len(_inbox_files()) == before
    assert len(routes._jobs) == jobs_before


def test_supported_url_still_queues_job(api, monkeypatch):
    """回归：正常直链不被误拦，仍返回 202 并建立任务。"""
    from gw.api import routes_asset_collect as routes

    monkeypatch.setattr(collector, "find_ytdlp", lambda: "yt-dlp")
    monkeypatch.setattr(collector, "download_direct", lambda url: (b"data", url))
    monkeypatch.setattr(collector, "store_content", lambda *a, **k: {})
    jobs_before = len(routes._jobs)

    response = _submit(api, "https://example.com/clip.mp4")

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert len(routes._jobs) == jobs_before + 1


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/clip.mp4",
        "https://example.com/drum-loop.wav",
        "https://example.com/a/data-guide.html",
    ],
)
def test_supported_sources_have_no_rejection_reason(url):
    """不误伤：普通直链、含 drum 的路径、含 data 的路径都不算不支持来源。"""
    assert collector.unsupported_source_reason(url) is None


def test_reject_unsupported_source_raises_unified_code():
    """收集器内部同类入口（下载前哨）复用同一错误码。"""
    with pytest.raises(collector.CollectError) as error:
        collector.reject_unsupported_source("https://cdn.example.com/live/index.m3u8")

    assert error.value.code == collector.UNSUPPORTED_SOURCE_CODE
    assert _has_chinese(str(error.value))


def test_legacy_codes_for_direct_validators_are_unchanged():
    """既有细分码保持不变，避免破坏已交付的收集面板文案与旧契约。"""
    with pytest.raises(collector.CollectError) as blob_error:
        collector.validate_collect_url("blob:https://example.com/id")
    with pytest.raises(collector.CollectError) as scheme_error:
        collector.validate_collect_url("file:///a.mp4")

    assert blob_error.value.code == "blob_url"
    assert scheme_error.value.code == "unsupported_url"


def test_download_direct_rejects_manifest_before_network(monkeypatch):
    """AC3：即使是直接调用下载器，也在建连前拒绝清单地址。"""
    def forbidden(*args, **kwargs):
        raise AssertionError("清单地址不应进入连接流程")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    with pytest.raises(collector.CollectError) as error:
        collector.download_direct("https://cdn.example.com/live/index.m3u8")

    assert error.value.code == collector.UNSUPPORTED_SOURCE_CODE
