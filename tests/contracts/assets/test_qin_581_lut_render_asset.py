"""QIN-581：把套用 LUT 的结果保存为新资产（图片直出 / 视频转码 / 来源链 / 失败态）。

验收对应：
- AC1 图片套用后可保存为新资产，像素与 QIN-580 预览一致；
- AC2 视频套用后可保存为新视频资产，成片可见调色差异；
- AC3 新资产记录来源链（原素材 + LUT 资产 + 强度），可反查；
- AC4 LUT 缺失或损坏返回稳定错误码，不静默跳过；
- AC5 未套用 LUT 的既有路径回归不受影响。
"""

from __future__ import annotations

import io
import math
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gw.asset_registry import repository as asset_repo
from gw.core import storage
from gw.core.asset_visibility import asset_owner_key
from gw.core.auth import require_authenticated

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
#: 另一个主体的凭据：本地模式用 Bearer 摘要作为主体，换 token 即换主体。
OTHER_AUTH = {"Authorization": "Bearer cleanroom-other-subject", "X-User-Role": "editor"}
WARM = "builtin_lut_warm"
NEUTRAL = "builtin_lut_neutral"

_PIXELS = [
    (128, 128, 128), (255, 0, 0), (0, 255, 0), (0, 0, 255),
    (0, 0, 0), (255, 255, 255), (64, 128, 192), (10, 20, 30),
    (200, 120, 40), (32, 64, 96), (250, 250, 10), (5, 200, 90),
    (90, 10, 200), (17, 34, 51), (240, 230, 220), (128, 0, 64),
]


def _image_bytes(pixels: list[tuple[int, int, int]] = _PIXELS, size=(4, 4)) -> bytes:
    image = Image.new("RGB", size)
    image.putdata(pixels)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(client: TestClient, name: str, data: bytes, content_type: str) -> dict:
    response = client.post(
        f"{BASE}/assets/import",
        headers=AUTH,
        files={"file": (name, data, content_type)},
        data={"name": name},
    )
    assert response.status_code == 200, response.text
    return response.json()["asset"]


def _ensure_luts(client: TestClient) -> None:
    assert client.post(f"{BASE}/builtin-luts/ensure", headers=AUTH).status_code == 200


def _asset_count(client: TestClient) -> int:
    listing = client.get(f"{BASE}/assets", headers=AUTH, params={"limit": 500})
    assert listing.status_code == 200, listing.text
    return len(listing.json()["assets"])


def _render(client: TestClient, asset_id: str, lut_asset_id: str, strength: float = 1.0):
    return client.post(
        f"{BASE}/assets/{asset_id}/lut-renders",
        headers=AUTH,
        json={"lut_asset_id": lut_asset_id, "strength": strength},
    )


def _decoded(client: TestClient, asset_id: str) -> Image.Image:
    media = client.get(f"{BASE}/assets/{asset_id}/media", headers=AUTH)
    assert media.status_code == 200, media.text
    return Image.open(io.BytesIO(media.content)).convert("RGBA")


#: 极值像素落在 warm cube 的精确格点上（末行采样 1.0 1.0 0.93、首行 0 0 0），
#: 期望值由 cube 内容与「超界通道 clamp 到 255」语义直接推出，不依赖渲染实现。
_EXACT_WARM = {
    (0, 0, 0): (0, 0, 0),
    (255, 255, 255): (255, 255, 237),
    (255, 0, 0): (255, 0, 0),
    (0, 255, 0): (0, 255, 0),
    (0, 0, 255): (0, 0, 237),
}


def _blend(source: int, graded: int, strength: float) -> int:
    """强度混合的独立期望：round(source + (graded - source) * strength)，按 0..255 口径。"""
    return min(255, max(0, math.floor(source + (graded - source) * strength + 0.5)))


def test_image_lut_render_saves_new_asset_with_expected_pixels(client: TestClient):
    """AC1：图片套用 LUT 后保存为新资产，像素符合 LUT 公式（与预览同口径）。"""
    _ensure_luts(client)
    source = _upload(client, "qin581_source.png", _image_bytes(), "image/png")
    assert source["kind"] == "image"
    before = _asset_count(client)

    response = _render(client, source["id"], WARM, 1.0)
    assert response.status_code == 200, response.text
    body = response.json()
    created = body["asset"]
    assert created["id"] != source["id"], "不得覆盖原素材"
    assert created["kind"] == "image"
    assert created["name"].endswith("_v001.png"), created["name"]
    assert created["display_path"].startswith("media_output/lut_render/")
    assert body["source_asset_id"] == source["id"]
    assert body["lut_asset_id"] == WARM
    assert body["strength"] == 1.0
    assert _asset_count(client) == before + 1

    rendered = _decoded(client, created["id"])
    assert rendered.size == (4, 4)
    for index, pixel in enumerate(_PIXELS):
        x, y = index % 4, index // 4
        got = rendered.getpixel((x, y))[:3]
        if pixel in _EXACT_WARM:
            assert got == _EXACT_WARM[pixel], (pixel, got)
        elif pixel == (128, 128, 128):
            # 暖调的方向性：抬红压蓝，绿通道近似不动。
            assert got[0] > 128 and got[2] < 128, got
            assert abs(got[1] - 129) <= 4, got
        else:
            assert got != pixel, f"非中性像素套用后应有变化：{pixel}"


def test_zero_strength_and_identity_lut_keep_pixels(client: TestClient):
    """AC1：强度 0% 与原图逐像素相等；identity LUT 同样不得改动画面。"""
    _ensure_luts(client)
    source = _upload(client, "qin581_identity.png", _image_bytes(), "image/png")
    original = _decoded(client, source["id"])

    zero = _render(client, source["id"], WARM, 0.0)
    assert zero.status_code == 200, zero.text
    assert _decoded(client, zero.json()["asset"]["id"]).tobytes() == original.tobytes()

    identity = _render(client, source["id"], NEUTRAL, 1.0)
    assert identity.status_code == 200, identity.text
    assert _decoded(client, identity.json()["asset"]["id"]).tobytes() == original.tobytes()


def test_lut_render_records_provenance(client: TestClient):
    """AC3：新资产记录原素材 + LUT 资产 + 强度，可从新资产反查。"""
    _ensure_luts(client)
    source = _upload(client, "qin581_provenance.png", _image_bytes(), "image/png")
    created = _render(client, source["id"], WARM, 0.5).json()["asset"]

    detail = client.get(f"{BASE}/assets/{created['id']}", headers=AUTH)
    assert detail.status_code == 200, detail.text
    record = detail.json()["asset"]
    assert record["derived_from"] == {
        "kind": "lut_render",
        "source_asset_id": source["id"],
        "lut_asset_id": WARM,
        "strength": 0.5,
        "renderer": "lut_render/v001",
    }
    assert record["metadata"]["lut_render"]["lut_size"] == 8
    assert record["metadata"]["lut_render"]["strength"] == 0.5
    # 半强度落在原图与满强度之间（用两次真实渲染互证，不依赖 LUT 内部算法）。
    # 满强度结果已取整，反推半强度会有 ±1 的量化误差，这里按 1 个色阶容差断言。
    original = _decoded(client, source["id"])
    full = _decoded(client, _render(client, source["id"], WARM, 1.0).json()["asset"]["id"])
    half = _decoded(client, created["id"])
    for x in range(4):
        for y in range(4):
            source_pixel = original.getpixel((x, y))[:3]
            full_pixel = full.getpixel((x, y))[:3]
            half_pixel = half.getpixel((x, y))[:3]
            expected = tuple(_blend(s, g, 0.5) for s, g in zip(source_pixel, full_pixel, strict=True))
            for source_value, full_value, half_value, wanted in zip(
                source_pixel, full_pixel, half_pixel, expected, strict=True
            ):
                assert abs(half_value - wanted) <= 1, (source_pixel, full_pixel, half_pixel)
                low, high = sorted((source_value, full_value))
                assert low <= half_value <= high, (source_pixel, full_pixel, half_pixel)


def test_corrupt_cube_returns_stable_error(client: TestClient):
    """AC4：Cube 损坏返回 422 + LUT_CUBE_INVALID，不生成资产、不静默回退原图。"""
    _ensure_luts(client)
    source = _upload(client, "qin581_bad_source.png", _image_bytes(), "image/png")
    broken = _upload(
        client,
        "qin581_broken.cube",
        b"LUT_3D_SIZE 2\n0 0 0\n1 1 1\n",
        "application/octet-stream",
    )
    before = _asset_count(client)

    response = _render(client, source["id"], broken["id"], 1.0)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "LUT_CUBE_INVALID"
    assert _asset_count(client) == before, "失败不得留下半条资产记录"


def test_missing_lut_asset_returns_stable_error(client: TestClient):
    """AC4：LUT 资产不存在时返回稳定错误码，不落到 500。"""
    _ensure_luts(client)
    source = _upload(client, "qin581_missing.png", _image_bytes(), "image/png")
    response = _render(client, source["id"], "ast_9999", 1.0)
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "ASSET_NOT_FOUND"


def test_non_media_source_and_strength_are_rejected(client: TestClient):
    """失败态：非图片/视频素材、越界强度都明确 400，不尝试渲染。"""
    _ensure_luts(client)
    lut_target = _upload(
        client,
        "qin581_other.cube",
        b"LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n0 0 1\n1 1 0\n1 0 1\n0 1 1\n1 1 1\n",
        "application/octet-stream",
    )
    source = _upload(client, "qin581_strength.png", _image_bytes(), "image/png")

    unsupported = _render(client, lut_target["id"], WARM, 1.0)
    assert unsupported.status_code == 400, unsupported.text
    assert unsupported.json()["detail"]["code"] == "LUT_SOURCE_UNSUPPORTED"

    for strength in (-0.1, 1.5):
        invalid = _render(client, source["id"], WARM, strength)
        assert invalid.status_code == 400, invalid.text
        assert invalid.json()["detail"]["code"] == "INVALID_LUT_STRENGTH"


def test_existing_media_paths_unaffected(client: TestClient):
    """AC5：保存派生资产后，原素材媒体与登记仍按原样可读。"""
    _ensure_luts(client)
    source_bytes = _image_bytes()
    source = _upload(client, "qin581_original.png", source_bytes, "image/png")
    original_media = client.get(f"{BASE}/assets/{source['id']}/media", headers=AUTH)
    assert original_media.status_code == 200
    assert original_media.content == source_bytes

    created = _render(client, source["id"], WARM, 1.0).json()["asset"]
    assert created["id"] != source["id"]
    after = client.get(f"{BASE}/assets/{source['id']}/media", headers=AUTH)
    assert after.status_code == 200
    assert after.content == source_bytes, "原素材字节不得被派生渲染改写"


def _ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if not binary:
        pytest.skip("环境没有 ffmpeg，视频转码用例跳过")
    return binary


def _make_gray_video(binary: str, path: Path) -> None:
    subprocess.run(
        [
            binary, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=0x808080:s=64x64:d=1:r=10",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        timeout=60,
    )


def _first_pixel(binary: str, video: Path, workdir: Path) -> tuple[int, int, int]:
    frame = workdir / (video.stem + "_frame.png")
    subprocess.run(
        [binary, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-frames:v", "1", str(frame)],
        check=True,
        timeout=60,
    )
    with Image.open(frame) as image:
        return image.convert("RGB").getpixel((32, 32))


def test_video_lut_render_transcodes_with_visible_grading(client: TestClient, tmp_path: Path):
    """AC2：视频套用后保存为新视频资产，成片确有调色差异（暖调抬红压蓝）。"""
    binary = _ffmpeg()
    _ensure_luts(client)
    sample_path = tmp_path / "qin581_sample.mp4"
    _make_gray_video(binary, sample_path)
    source = _upload(
        client, "qin581_sample.mp4", sample_path.read_bytes(), "video/mp4"
    )
    assert source["kind"] == "video"
    original_pixel = _first_pixel(binary, sample_path, tmp_path)

    response = _render(client, source["id"], WARM, 1.0)
    assert response.status_code == 200, response.text
    created = response.json()["asset"]
    assert created["kind"] == "video"
    assert created["name"].endswith("_v001.mp4"), created["name"]

    media = client.get(f"{BASE}/assets/{created['id']}/media", headers=AUTH)
    assert media.status_code == 200, media.text
    graded_path = tmp_path / "qin581_graded.mp4"
    graded_path.write_bytes(media.content)
    graded_pixel = _first_pixel(binary, graded_path, tmp_path)

    # 编码与色彩矩阵会带来几个色阶的偏移，这里断言方向与量级，不做逐位相等。
    assert graded_pixel[0] > original_pixel[0] + 2, (original_pixel, graded_pixel)
    assert graded_pixel[2] < original_pixel[2] - 2, (original_pixel, graded_pixel)
    assert abs(graded_pixel[0] - 136) <= 12, graded_pixel
    assert abs(graded_pixel[2] - 119) <= 12, graded_pixel


def _owner_key() -> str:
    """当前测试凭据对应的归属键（与 gw.core.asset_visibility 同口径）。"""
    return asset_owner_key(require_authenticated(AUTH["Authorization"], "editor"))


def _claim(asset_id: str, owner: str) -> None:
    """把素材标成工作流私有归属。

    真实桥接就是这么写的（``workflow_owner`` 标记该素材只对归属主体可见），
    这里直接落库复现「源素材属于 A」的前置条件。
    """

    def mutate(raw):
        raw["assets"][asset_id]["workflow_owner"] = owner

    asset_repo.state().mutate(mutate)


def test_derived_asset_keeps_source_ownership(client: TestClient):
    """阻断回归：派生资产必须继承源素材归属，否则会变成跨主体可读的公共资源。

    评审实测过的坏样本：B 读 A 的源图与 LUT 都是 404，但 A 派生出来的新资产
    详情和媒体对 B 都是 200，且 ``derived_from`` 把源 asset_id 一起带出去。
    """
    _ensure_luts(client)
    source = _upload(client, "qin581_owned.png", _image_bytes(), "image/png")
    owner = _owner_key()
    _claim(source["id"], owner)
    # 前置条件：源素材对外主体不可见（否则本用例证不到「派生件丢了隔离」）。
    assert client.get(f"{BASE}/assets/{source['id']}", headers=OTHER_AUTH).status_code == 404

    response = _render(client, source["id"], WARM, 1.0)
    assert response.status_code == 200, response.text
    created = response.json()["asset"]
    assert created.get("workflow_owner") == owner, "派生资产必须继承源素材归属"

    # 归属者本人照旧可读。
    assert client.get(f"{BASE}/assets/{created['id']}", headers=AUTH).status_code == 200
    assert client.get(f"{BASE}/assets/{created['id']}/media", headers=AUTH).status_code == 200

    # 其他主体：详情与媒体都必须与「不存在」同口径，来源链不得外泄。
    other_detail = client.get(f"{BASE}/assets/{created['id']}", headers=OTHER_AUTH)
    assert other_detail.status_code == 404, other_detail.text
    assert other_detail.json()["detail"]["code"] == "ASSET_NOT_FOUND"
    assert "derived_from" not in other_detail.text
    other_media = client.get(f"{BASE}/assets/{created['id']}/media", headers=OTHER_AUTH)
    assert other_media.status_code == 404, other_media.text

    # 列表里也不得出现（否则等于用「能列出」泄露存在性）。
    listing = client.get(f"{BASE}/assets", headers=OTHER_AUTH, params={"limit": 500})
    assert listing.status_code == 200, listing.text
    assert created["id"] not in {item["id"] for item in listing.json()["assets"]}


def test_whole_transaction_failure_removes_rendered_file(client: TestClient, monkeypatch):
    """重要回归：事务后半段失败（索引写入）不得留下孤儿产物。

    旧实现只把渲染异常包在清理里；索引或状态提交失败时记录没写进去，
    但盘上已经多了一个 PNG。
    """
    _ensure_luts(client)
    source = _upload(client, "qin581_orphan.png", _image_bytes(), "image/png")
    before = _asset_count(client)
    output_root = storage.data_root() / "media_output" / "lut_render"
    before_files = set(output_root.rglob("*")) if output_root.exists() else set()

    def boom(_records):
        raise RuntimeError("injected search index failure")

    # 只撤这一个补丁：直接 undo() 会把 conftest 注入的测试环境变量一起还原，
    # 认证模式退回本地账户后后续请求全 401。
    with monkeypatch.context() as patched:
        patched.setattr(asset_repo, "_upsert_asset_search_documents", boom)
        with pytest.raises(RuntimeError):
            _render(client, source["id"], WARM, 1.0)

    assert _asset_count(client) == before, "失败不得写进资产记录"
    after_files = set(output_root.rglob("*")) if output_root.exists() else set()
    assert after_files == before_files, f"事务失败后仍残留产物：{after_files - before_files}"
    assert not [path for path in after_files if path.is_file()], "渲染产物必须随事务失败一并清理"


def test_video_strength_curve_is_monotonic(client: TestClient, tmp_path: Path):
    """阻断回归：视频混合强度的方向必须是「0% = 原片、100% = 满调色」。

    旧实现把 blend 的 top/bottom 写反（``all_opacity`` 控制的是 top 层权重），
    于是 0% 出满调色、75% 只有 25% 调色，1.0 又跳回满调色。
    """
    binary = _ffmpeg()
    _ensure_luts(client)
    sample_path = tmp_path / "qin581_curve.mp4"
    _make_gray_video(binary, sample_path)
    source = _upload(client, "qin581_curve.mp4", sample_path.read_bytes(), "video/mp4")
    original_pixel = _first_pixel(binary, sample_path, tmp_path)

    def render_pixel(strength: float) -> tuple[int, int, int]:
        response = _render(client, source["id"], WARM, strength)
        assert response.status_code == 200, response.text
        media = client.get(f"{BASE}/assets/{response.json()['asset']['id']}/media", headers=AUTH)
        assert media.status_code == 200, media.text
        path = tmp_path / f"qin581_curve_{int(strength * 100):03d}.mp4"
        path.write_bytes(media.content)
        return _first_pixel(binary, path, tmp_path)

    zero = render_pixel(0.0)
    half = render_pixel(0.5)
    full = render_pixel(1.0)

    # 0% 必须回到原片（允许编码噪声几个色阶）。
    for channel in (0, 1, 2):
        assert abs(zero[channel] - original_pixel[channel]) <= 3, (original_pixel, zero)
    # 100% 是满调色：暖调抬红压蓝。
    assert full[0] > original_pixel[0] + 2, (original_pixel, full)
    assert full[2] < original_pixel[2] - 2, (original_pixel, full)
    # 中间强度落在两者之间（方向错了会在这里翻车）。
    low, high = sorted((original_pixel[2], full[2]))
    assert low < half[2] < high, (original_pixel, half, full)
    assert low <= zero[2] - 0 or abs(zero[2] - original_pixel[2]) <= 3
    # 强度越大越接近满调色：0% → 50% → 100% 的蓝通道必须严格递减。
    assert zero[2] > half[2] > full[2], (zero, half, full)
