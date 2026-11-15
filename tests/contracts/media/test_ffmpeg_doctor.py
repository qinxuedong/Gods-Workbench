# -*- coding: utf-8 -*-
"""QIN-607：FFmpeg doctor 自检端点契约测试。

AC4：需认证 200、未认证 401、只读三态 found/source/version、无绝对路径回显；
契约登记反向断言（MEDIA-INTERFACE-CATALOG.yaml 含 get_media_ffmpeg_doctor）。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from gw.api.app import create_app

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
READONLY = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "readonly"}

CATALOG_PATH = Path(__file__).resolve().parents[3] / (
    "docs/contracts/MEDIA-INTERFACE-CATALOG.yaml"
)


def test_doctor_contract_registered():
    """契约登记反向断言：MEDIA catalog 含 get_media_ffmpeg_doctor。"""
    text = CATALOG_PATH.read_text(encoding="utf-8")
    assert "get_media_ffmpeg_doctor" in text
    assert "path: /api/media/ffmpeg-doctor" in text


def test_doctor_requires_auth():
    """未认证 401；只读角色可读（观测口径一致）。"""
    with TestClient(create_app()) as client:
        assert (
            client.get("/api/media/ffmpeg-doctor").status_code == 401
        )
        r = client.get("/api/media/ffmpeg-doctor", headers=READONLY)
        assert r.status_code == 200


def test_doctor_shape_no_path_echo():
    """认证后返回 ffmpeg/ffprobe 三态结构，且响应不回显绝对路径。"""
    with TestClient(create_app()) as client:
        r = client.get("/api/media/ffmpeg-doctor", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        for tool in ("ffmpeg", "ffprobe"):
            entry = body[tool]
            assert set(entry) == {"found", "source", "version"}
            assert entry["source"] in ("gw_ffmpeg_env", "path", "not_found")
            if not entry["found"]:
                assert entry["version"] is None
        # 无绝对路径回显（Windows 盘符样式与 POSIX 根样式都拦）。
        text = json.dumps(body)
        assert ":\\" not in text
        assert not text.startswith("/")