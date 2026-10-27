# -*- coding: utf-8 -*-
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

"""MVP1 收集器不支持来源契约测试 (QIN-436).

AC1: blob:、data: 超限、.m3u8/.mpd 清单 URL、已知 DRM 标记 -> 统一错误码 (COLLECT_UNSUPPORTED_SOURCE) + 中文 message。
AC2: 不产生任何 inbox 文件。
AC3: 不发起外网请求（测试用 mock）。
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gw.api.routes_asset_collect import router as collect_router
from gw.asset_registry import collector
from gw.asset_registry.collector import (
    COLLECT_UNSUPPORTED_SOURCE,
    MAX_DATA_URL_LENGTH,
    UnsupportedSourceError,
)

app = FastAPI()
app.include_router(collect_router)
client = TestClient(app)


def test_collect_unsupported_blob_url():
    """AC1: blob: 协议地址统一报错 COLLECT_UNSUPPORTED_SOURCE + 中文提示。"""
    blob_url = "blob:https://example.com/1234-5678-90ab"

    # Core 校验
    with pytest.raises(UnsupportedSourceError) as exc_info:
        collector.validate_source(blob_url)

    assert exc_info.value.code == COLLECT_UNSUPPORTED_SOURCE
    assert "blob:" in exc_info.value.message
    assert "无法收集" in exc_info.value.message

    # API 接口
    response = client.post("/api/asset-collect/url", json={"url": blob_url})
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == COLLECT_UNSUPPORTED_SOURCE
    assert data["error_code"] == COLLECT_UNSUPPORTED_SOURCE
    assert "blob:" in data["message"]


def test_collect_unsupported_oversized_data_url():
    """AC1: 超限 data: URL 统一报错 COLLECT_UNSUPPORTED_SOURCE + 中文提示。"""
    oversized_data_url = "data:image/png;base64," + "A" * (MAX_DATA_URL_LENGTH + 100)

    # Core 校验
    with pytest.raises(UnsupportedSourceError) as exc_info:
        collector.validate_source(oversized_data_url)

    assert exc_info.value.code == COLLECT_UNSUPPORTED_SOURCE
    assert "data:" in exc_info.value.message
    assert "超过" in exc_info.value.message

    # API 接口
    response = client.post("/api/asset-collect/url", json={"url": oversized_data_url})
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == COLLECT_UNSUPPORTED_SOURCE
    assert "data:" in data["message"]


def test_collect_unsupported_m3u8_manifest_url():
    """AC1: .m3u8 HLS 流媒体清单统一报错 COLLECT_UNSUPPORTED_SOURCE + 中文提示。"""
    m3u8_urls = [
        "https://example.com/live/playlist.m3u8",
        "http://stream.org/video/index.m3u8?token=secret123",
    ]

    for url in m3u8_urls:
        with pytest.raises(UnsupportedSourceError) as exc_info:
            collector.validate_source(url)

        assert exc_info.value.code == COLLECT_UNSUPPORTED_SOURCE
        assert ".m3u8" in exc_info.value.message or "清单" in exc_info.value.message

        response = client.post("/api/asset-collect/url", json={"url": url})
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == COLLECT_UNSUPPORTED_SOURCE


def test_collect_unsupported_mpd_manifest_url():
    """AC1: .mpd DASH 流媒体清单统一报错 COLLECT_UNSUPPORTED_SOURCE + 中文提示。"""
    mpd_url = "https://example.com/dash/manifest.mpd"

    with pytest.raises(UnsupportedSourceError) as exc_info:
        collector.validate_source(mpd_url)

    assert exc_info.value.code == COLLECT_UNSUPPORTED_SOURCE
    assert ".mpd" in exc_info.value.message or "清单" in exc_info.value.message

    response = client.post("/api/asset-collect/url", json={"url": mpd_url})
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == COLLECT_UNSUPPORTED_SOURCE


def test_collect_unsupported_drm_markers():
    """AC1: 已知 DRM 标记 (URL/Headers/Content) 统一报错 COLLECT_UNSUPPORTED_SOURCE + 中文提示。"""
    drm_urls = [
        "https://example.com/stream?drm_token=xyz123",
        "https://widevine.license.com/get_key",
        "https://ezdrm.com/fairplay/key",
    ]

    for url in drm_urls:
        with pytest.raises(UnsupportedSourceError) as exc_info:
            collector.validate_source(url)

        assert exc_info.value.code == COLLECT_UNSUPPORTED_SOURCE
        assert "DRM" in exc_info.value.message

        response = client.post("/api/asset-collect/url", json={"url": url})
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == COLLECT_UNSUPPORTED_SOURCE
        assert "DRM" in data["message"]

    # 校验 Header / Content 包含 DRM 或 manifest 内容
    with pytest.raises(UnsupportedSourceError) as exc_info:
        collector.validate_source(
            "https://example.com/media.mp4",
            headers={"X-Protection": "widevine"},
        )
    assert exc_info.value.code == COLLECT_UNSUPPORTED_SOURCE

    with pytest.raises(UnsupportedSourceError) as exc_info:
        collector.validate_source(
            "https://example.com/media.mp4",
            content=b"#EXT-X-FAIRPLAY-KEY:URI=skd://...",
        )
    assert exc_info.value.code == COLLECT_UNSUPPORTED_SOURCE


def test_no_inbox_files_created_for_unsupported_sources(tmp_path: Path):
    """AC2: 不产生任何 inbox 文件。"""
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    unsupported_urls = [
        "blob:https://example.com/test",
        "https://example.com/test.m3u8",
        "https://example.com/test.mpd",
        "https://example.com/video?drm_token=abc",
    ]

    for url in unsupported_urls:
        with pytest.raises(UnsupportedSourceError):
            collector.collect_url(url, inbox_dir=inbox_dir)

    # 验证 inbox 目录中不包含任何文件
    created_files = list(inbox_dir.glob("*"))
    assert (
        len(created_files) == 0
    ), f"发现不应被创建的 inbox 文件: {created_files}"


def test_no_external_network_requests_made(monkeypatch):
    """AC3: 不发起外网请求 (测试用 mock/socket 拦截)。"""

    def _disallow_socket_connect(*args, **kwargs):
        raise RuntimeError("External network request forbidden in contract test!")

    monkeypatch.setattr(socket.socket, "connect", _disallow_socket_connect)

    unsupported_urls = [
        "blob:https://example.com/test",
        "data:text/plain;base64," + "X" * (MAX_DATA_URL_LENGTH + 10),
        "https://example.com/test.m3u8",
        "https://example.com/test.mpd",
        "https://example.com/video?drm_token=abc",
    ]

    for url in unsupported_urls:
        # 校验静态分析拦截，绝不触发 socket.connect
        with pytest.raises(UnsupportedSourceError):
            collector.validate_source(url)

        res = client.post("/api/asset-collect/url", json={"url": url})
        assert res.status_code == 400
