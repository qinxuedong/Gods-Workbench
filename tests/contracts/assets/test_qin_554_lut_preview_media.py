"""QIN-554：LUT 预览依赖的媒体读取契约（.cube 按 UTF-8 文本下发且可解析）。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from gw.api.routes_asset_registry import _media_type
from gw.asset_registry import builtin_luts
from gw.asset_registry.lut_cube import parse_lut_cube_bytes

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
BUILTIN_LUT_IDS = [
    "builtin_lut_neutral",
    "builtin_lut_warm",
    "builtin_lut_cool",
    "builtin_lut_high_contrast",
    "builtin_lut_cinematic",
]


def test_cube_media_is_served_as_utf8_text(client: TestClient):
    """预览直接 fetch 媒体端点解析，故 .cube 必须以 UTF-8 文本声明并原样下发。"""
    ensured = client.post(f"{BASE}/builtin-luts/ensure", headers=AUTH)
    assert ensured.status_code == 200, ensured.text

    media = client.get(f"{BASE}/assets/builtin_lut_warm/media", headers=AUTH)
    assert media.status_code == 200, media.text
    content_type = media.headers["content-type"].lower()
    assert content_type.startswith("text/plain")
    assert "charset=utf-8" in content_type
    meta = parse_lut_cube_bytes(media.content)
    assert meta["title"] == "GW Warm"
    assert meta["lut_3d_size"] == 8
    assert meta["sample_count"] == 512


def test_every_builtin_lut_media_is_previewable(client: TestClient):
    """五条预置 LUT 都能经媒体端点读到可解析内容，卡片与详情不会拿到空帧。"""
    assert client.post(f"{BASE}/builtin-luts/ensure", headers=AUTH).status_code == 200
    for asset_id in BUILTIN_LUT_IDS:
        media = client.get(f"{BASE}/assets/{asset_id}/media", headers=AUTH)
        assert media.status_code == 200, (asset_id, media.text)
        meta = parse_lut_cube_bytes(media.content)
        assert meta["sample_count"] == 512, asset_id
        assert meta["title"], asset_id


def test_uploaded_cube_is_registered_as_lut_and_previewable(client: TestClient):
    """用户上传的 .cube 与索引扫描同口径登记为 lut，媒体可读——预览侧栏才切得到用户 LUT。"""
    cube = builtin_luts.source_builtin_cube_path("builtin_lut_warm.cube").read_bytes()
    uploaded = client.post(
        f"{BASE}/assets/import",
        headers=AUTH,
        files={"file": ("my_look.cube", cube, "application/octet-stream")},
        data={"name": "my_look.cube"},
    )
    assert uploaded.status_code == 200, uploaded.text
    asset = uploaded.json()["asset"]
    assert asset["kind"] == "lut", asset
    media = client.get(f"{BASE}/assets/{asset['id']}/media", headers=AUTH)
    assert media.status_code == 200, media.text
    assert media.headers["content-type"].lower().startswith("text/plain")
    assert parse_lut_cube_bytes(media.content)["sample_count"] == 512


def test_media_type_mapping_keeps_other_suffixes():
    """新增 .cube 映射不得改变既有类型的下发口径。"""
    assert _media_type(Path("frame.png")) == "image/png"
    assert _media_type(Path("clip.mp4")) == "video/mp4"
    assert _media_type(Path("notes.txt")) == "text/plain; charset=utf-8"
    assert _media_type(Path("look.cube")) == "text/plain; charset=utf-8"
    assert _media_type(Path("blob.bin")) == "application/octet-stream"
    assert _media_type(Path("look.CUBE")) == "text/plain; charset=utf-8"
