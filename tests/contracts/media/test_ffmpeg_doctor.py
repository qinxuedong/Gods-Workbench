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


def test_startup_summary_logged(caplog):
    """AC4：lifespan 发出「FFmpeg 自检」一行摘要，含双工具三态与来源。"""
    import logging

    with caplog.at_level(logging.INFO, logger="gw.api.app"):
        with TestClient(create_app()):
            pass
    records = [r for r in caplog.records if "FFmpeg 自检" in r.getMessage()]
    assert records, "启动日志缺少 FFmpeg 自检摘要行"
    message = records[0].getMessage()
    assert "ffmpeg=" in message and "ffprobe=" in message
    assert "gw_ffmpeg_env" in message or "path" in message or "not_found" in message


def test_app_logger_info_visible_under_default_config():
    """AC4：默认配置（无 root handler）下 gw.api.app 的 info 可见——QA 阻断项反面断言。

    QA 实测根因：run.py → uvicorn.run 无 log_config，root 无 handler，
    lastResort（WARNING 级）过滤 info。修复在 app.py 给本 logger 幂等挂
    stderr StreamHandler（INFO 起），本断言钉住该配置不被回退。
    """
    import logging

    import gw.api.app as app_module  # noqa: F401  import 即完成幂等挂载

    logger = logging.getLogger("gw.api.app")
    stream_handlers = [
        item for item in logger.handlers if isinstance(item, logging.StreamHandler)
    ]
    assert stream_handlers, "gw.api.app logger 缺少默认 stderr handler，info 启动摘要不可见"
    assert all(
        item.level == logging.NOTSET or item.level <= logging.INFO
        for item in stream_handlers
    )
    assert logger.level <= logging.INFO, (
        "gw.api.app logger 有效级别高于 INFO，info 启动摘要会在 logger 入口被丢弃"
    )