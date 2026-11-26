# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0
#
# Licensed under the Functional Source License, Version 1.1, Apache 2.0
# Future License (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
#     https://fsl.software/
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""QIN-635 安装包 JXL 缩略图兼容、请求级回退与缓存隔离契约。"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gw.api.app import create_app
from gw.asset_registry import repository as asset_repo
from gw.core import jxl_tool
from gw.core.errors import CleanroomException
from gw.core.ffmpeg_resolver import FfmpegResolution
from gw.media import repository as media_repo

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


@pytest.fixture(autouse=True)
def media_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    data_root = tmp_path / "gw-data"
    allowed_root = tmp_path / "roots"
    data_root.mkdir()
    allowed_root.mkdir()
    monkeypatch.setenv("GW_DATA_DIR", str(data_root))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed_root))
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "gw-auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "gw-video"))
    media_repo.patch_thumbnail_settings(
        {
            "mode": "source",
            "directory": "",
            "cache_folder": "god-cache",
            "width": 256,
            "format": "jxl",
        }
    )
    return {"data": data_root, "roots": allowed_root}


def _png_bytes(color: tuple[int, int, int] = (40, 120, 200)) -> bytes:
    image = Image.new("RGB", (320, 180), color)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _register_file(runtime: dict[str, Path], name: str, content: bytes) -> tuple[str, Path]:
    source = runtime["roots"] / name
    source.write_bytes(content)
    created = asset_repo.import_assets([{"name": name, "kind": "image"}], "image", None)
    asset_id = created["asset_ids"][0]
    asset_repo.update_asset(
        asset_id, {"display_path": str(source), "expected_version": created["revision"]}
    )
    return asset_id, source


def _stub_jxl_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    def encode(png_bytes: bytes) -> bytes:
        with Image.open(io.BytesIO(png_bytes)) as image:
            return b"\xff\x0a" + f"jxl:{image.width}".encode("ascii")

    monkeypatch.setattr(media_repo, "_encode_jxl_thumbnail", encode)


def test_fallback_query_and_accept_negotiation_preserve_user_settings_and_original(
    media_runtime: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_jxl_encoder(monkeypatch)
    raw = _png_bytes()
    raw_sha = hashlib.sha256(raw).hexdigest()
    asset_id, source = _register_file(media_runtime, "sample.png", raw)

    with TestClient(create_app()) as client:
        # 1. 默认 Accept */* 且无 fallback 时保持返回 image/jxl。
        jxl_resp = client.get(f"/api/media-preview?asset_id={asset_id}&w=256", headers=AUTH)
        assert jxl_resp.status_code == 200
        assert jxl_resp.headers["content-type"].startswith("image/jxl")
        assert "x-gw-preview-fallback" not in {k.lower() for k in jxl_resp.headers}

        # 2. 显式 fallback=webp 时返回 image/webp 与标记头，不触发 JXL ETag 304 串格式。
        fb_resp = client.get(
            f"/api/media-preview?asset_id={asset_id}&w=256&fallback=webp",
            headers={**AUTH, "If-None-Match": jxl_resp.headers["etag"]},
        )
        assert fb_resp.status_code == 200
        assert fb_resp.headers["content-type"].startswith("image/webp")
        assert fb_resp.headers.get("X-GW-Preview-Fallback") == "webp"
        assert fb_resp.headers["etag"] != jxl_resp.headers["etag"]

        # 3. 模拟 WebView2 Accept（含 image/webp 但不含 image/jxl）自动协商返回 webp。
        webview_resp = client.get(
            f"/api/media-preview?asset_id={asset_id}&w=256",
            headers={
                **AUTH,
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )
        assert webview_resp.status_code == 200
        assert webview_resp.headers["content-type"].startswith("image/webp")
        assert "Accept" in webview_resp.headers.get("Vary", "")
        assert webview_resp.headers.get("X-GW-Preview-Fallback") == "webp"

        # 4. 用户缩略图格式配置不得被悄悄改写，原图文件字节零变更。
        settings = client.get("/api/asset-thumbnails/settings", headers=AUTH).json()
        assert settings["format"] == "jxl"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == raw_sha


def test_canonical_jxl_hit_not_returned_for_webp_fallback_and_caches_coexist(
    media_runtime: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_jxl_encoder(monkeypatch)
    asset_id, source = _register_file(media_runtime, "coexist.png", _png_bytes())

    # 预生成主设置格式（.jxl）的 canonical 缩略图产物。
    generated = media_repo.generate_thumbnail({"asset_id": asset_id, "width": 256})
    jxl_canonical = media_runtime["data"] / generated["output"]
    assert jxl_canonical.suffix == ".jxl" and jxl_canonical.is_file()

    # 请求 fallback=webp 不得旁路命中 .jxl 产物，必须返回 image/webp 且保留原 .jxl 缓存。
    webp_result = media_repo.preview_bytes(
        {"asset_id": asset_id, "w": 256, "fallback": "webp"}
    )
    assert webp_result["media_type"] == "image/webp"
    assert webp_result["bytes"].startswith(b"RIFF")
    assert jxl_canonical.is_file()

    cache_dir = jxl_canonical.parent
    assert any(p.suffix == ".jxl" for p in cache_dir.iterdir())
    assert any(p.suffix == ".webp" for p in cache_dir.iterdir())


def test_missing_ffmpeg_falls_back_for_png_and_uses_djxl_or_503_for_jxl_source(
    media_runtime: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        media_repo,
        "resolve_ffmpeg_tool",
        lambda name: FfmpegResolution(path=None, source="not_found"),
    )
    monkeypatch.setattr(jxl_tool, "_find_tool", lambda name: (None, None))

    # 非 .jxl 源图在缺 ffmpeg 时安全回退为 webp，不抛 503。
    png_id, _ = _register_file(media_runtime, "no-ffmpeg.png", _png_bytes())
    with TestClient(create_app()) as client:
        resp = client.get(f"/api/media-preview?asset_id={png_id}&w=256", headers=AUTH)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/webp")
        assert resp.headers.get("X-GW-Preview-Fallback") == "webp"

        # .jxl 源图在 ffmpeg 与 djxl 均不可用时必须明确返回 503，不得伪造成功。
        jxl_bytes = b"\xff\x0a" + b"\x00" * 32
        jxl_id, jxl_source = _register_file(media_runtime, "broken.jxl", jxl_bytes)
        bad_resp = client.get(f"/api/media-preview?asset_id={jxl_id}&w=256", headers=AUTH)
        assert bad_resp.status_code == 503
        assert bad_resp.json()["detail"]["code"] == "JXL_DECODE_FAILED"
        assert jxl_source.read_bytes() == jxl_bytes


def test_atomic_write_failure_degrades_gracefully_and_cold_read_recovers(
    media_runtime: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_jxl_encoder(monkeypatch)
    asset_id, source = _register_file(media_runtime, "cold-read.png", _png_bytes())

    def fail_write(*args, **kwargs):
        raise CleanroomException(500, "DISK_FULL", "磁盘已满")

    with monkeypatch.context() as m:
        m.setattr(media_repo, "_atomic_write_bytes", fail_write)
        degraded = media_repo.preview_bytes(
            {"asset_id": asset_id, "w": 256, "fallback": "webp"}
        )
        assert degraded["media_type"] == "image/webp"
        assert degraded["bytes"].startswith(b"RIFF")

    # 恢复写盘并生成有效磁盘缓存，随后清内存缓存模拟冷读。
    written = media_repo.preview_bytes(
        {"asset_id": asset_id, "w": 256, "fallback": "webp"}
    )
    media_repo._PREVIEW_CACHE_DIR_MEMO.clear()
    before_hits = media_repo.preview_cache_stats()["hit"]
    cold = media_repo.preview_bytes(
        {"asset_id": asset_id, "w": 256, "fallback": "webp"}
    )
    assert cold["bytes"] == written["bytes"]
    assert media_repo.preview_cache_stats()["hit"] == before_hits + 1
