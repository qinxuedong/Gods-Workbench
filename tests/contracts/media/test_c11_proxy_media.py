# -*- coding: utf-8 -*-
"""C11-T4 轻量代理媒体与降级预览契约。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gw.api.app import create_app
from gw.asset_registry import repository as asset_repo
from gw.media import proxy

AUTH = {"Authorization": "Bearer c11-t4-fixture", "X-User-Role": "editor"}
READONLY = {"Authorization": "Bearer c11-t4-fixture", "X-User-Role": "readonly"}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_root = tmp_path / "data"
    allowed_root = tmp_path / "media"
    data_root.mkdir()
    allowed_root.mkdir()
    monkeypatch.setenv("GW_DATA_DIR", str(data_root))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed_root))
    return TestClient(create_app())


def _registered_image(tmp_path: Path) -> tuple[str, Path, int]:
    image = tmp_path / "media" / "source.png"
    Image.new("RGB", (1600, 900), (191, 146, 54)).save(image)
    asset = asset_repo.import_assets([{"name": image.name, "kind": "image"}], "image", None)
    asset_id = asset["asset_ids"][0]
    updated = asset_repo.update_asset(asset_id, {"display_path": str(image), "expected_version": asset["revision"]})
    return asset_id, image, updated["revision"]


def test_proxy_descriptor_generates_small_real_image_and_content(client: TestClient, tmp_path: Path) -> None:
    asset_id, source, _ = _registered_image(tmp_path)

    descriptor = client.get(
        "/api/media-proxy",
        headers=READONLY,
        params={"asset_id": asset_id, "profile": "preview", "width": 320},
    )
    assert descriptor.status_code == 200, descriptor.text
    body = descriptor.json()
    assert body["availability"] == "ready"
    assert body["asset_id"] == asset_id
    assert body["version_id"] is None
    assert body["media_type"] in {"image/jpeg", "image/png"}
    assert 0 < body["width"] <= 320
    assert body["height"] > 0
    assert body["artifact_id"]
    assert str(source) not in descriptor.text
    assert str(source.parent) not in descriptor.text
    assert body["proxy_url"].startswith("/api/media-proxy/content?")

    content = client.get(body["proxy_url"], headers=READONLY)
    assert content.status_code == 200, content.text
    assert content.headers["content-type"].split(";", 1)[0] == body["media_type"]
    assert content.content and content.content != source.read_bytes()


def test_proxy_binds_exact_version_and_rejects_unknown_version(client: TestClient, tmp_path: Path) -> None:
    asset_id, source, revision = _registered_image(tmp_path)
    version = asset_repo.create_image_version(asset_id, str(source), expected_version=revision)["version"]["version_id"]

    ready = client.get(
        "/api/media-proxy",
        headers=AUTH,
        params={"asset_id": asset_id, "version_id": version, "width": 240},
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["version_id"] == version

    missing = client.get(
        "/api/media-proxy",
        headers=AUTH,
        params={"asset_id": asset_id, "version_id": "imgv_9999", "width": 240},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "VERSION_NOT_FOUND"


def test_proxy_missing_source_returns_explicit_degradation_without_fake_url(client: TestClient) -> None:
    created = asset_repo.import_assets([{"name": "offline.png", "kind": "image"}], "image", None)
    asset_id = created["asset_ids"][0]

    descriptor = client.get("/api/media-proxy", headers=AUTH, params={"asset_id": asset_id})
    assert descriptor.status_code == 200, descriptor.text
    body = descriptor.json()
    assert body["availability"] == "unavailable"
    assert body["reason_code"] == "MEDIA_NOT_AVAILABLE"
    assert body["next_action"] == "restore_source_media"
    assert body["data_status"] == "degraded"
    assert body["proxy_url"] is None
    assert body["data_gaps"]

    content = client.get("/api/media-proxy/content", headers=AUTH, params={"asset_id": asset_id})
    assert content.status_code == 503
    detail = content.json()["detail"]
    assert detail["code"] == "MEDIA_NOT_AVAILABLE"
    assert detail["reason_code"] == "MEDIA_NOT_AVAILABLE"
    assert detail["next_action"] == "restore_source_media"
    assert str(asset_id) not in detail.get("message", "") or str(asset_id) not in content.text


def test_proxy_dependency_failure_is_not_integrated(monkeypatch: pytest.MonkeyPatch, client: TestClient, tmp_path: Path) -> None:
    asset_id, _, _ = _registered_image(tmp_path)
    monkeypatch.setattr(proxy, "_pil", lambda: None)

    descriptor = client.get("/api/media-proxy", headers=AUTH, params={"asset_id": asset_id})
    assert descriptor.status_code == 200, descriptor.text
    body = descriptor.json()
    assert body["availability"] == "not_integrated"
    assert body["reason_code"] == "PIL_NOT_AVAILABLE"
    assert body["next_action"] == "install_media_dependency"
    assert body["proxy_url"] is None


def test_proxy_auth_and_path_boundary(client: TestClient, tmp_path: Path) -> None:
    assert client.get("/api/media-proxy").status_code == 401
    assert client.get("/api/media-proxy", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get("/api/media-proxy", headers={**AUTH, "X-User-Role": "guest"}).status_code == 403

    outside = tmp_path / "outside.png"
    Image.new("RGB", (10, 10), "red").save(outside)
    rejected = client.get("/api/media-proxy", headers=AUTH, params={"path": "../outside.png"})
    assert rejected.status_code == 403
    assert "outside.png" not in rejected.text


def test_proxy_video_uses_real_ffmpeg_when_available(client: TestClient, tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg 未安装")
    source = tmp_path / "media" / "source.mp4"
    generated = subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=0.3", "-pix_fmt", "yuv420p", str(source)],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if generated.returncode != 0 or not source.is_file():
        pytest.skip("当前 ffmpeg 不支持 lavfi 测试夹具")
    asset = asset_repo.import_assets([{"name": source.name, "kind": "video"}], "video", None)
    asset_id = asset["asset_ids"][0]
    asset_repo.update_asset(asset_id, {"display_path": str(source), "expected_version": asset["revision"]})

    descriptor = client.get("/api/media-proxy", headers=AUTH, params={"asset_id": asset_id, "width": 320})
    assert descriptor.status_code == 200, descriptor.text
    body = descriptor.json()
    assert body["availability"] == "ready"
    assert body["media_type"] == "video/mp4"
    assert body["artifact_id"].endswith(".mp4")
    content = client.get(body["proxy_url"], headers=AUTH)
    assert content.status_code == 200, content.text
    assert content.content.startswith(b"\x00\x00\x00")


def test_preview_player_is_self_contained_and_has_degradation_states() -> None:
    source = Path("web/js/modules/media-preview-player.js").read_text(encoding="utf-8")
    assert "MediaPreviewPlayer" in source
    assert "/api/media-preview" in source
    assert "metadata" in source
    assert "data-gw-degradation" in source
    assert "not_integrated" in source and "unavailable" in source
    assert "next_action" in source
    assert "http://" not in source and "https://" not in source
