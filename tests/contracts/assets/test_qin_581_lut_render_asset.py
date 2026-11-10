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
import logging
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
from gw.core.errors import CleanroomException

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


def _search_document_count() -> int:
    """冷读检索索引：每次新开连接重新打开数据库，不复用进程内连接。"""
    from gw.core.sqlite_vec_engine import SQLiteVecEngine

    db_path = storage.data_root() / asset_repo.ASSET_SEARCH_INDEX_FILENAME
    if not db_path.exists():
        return 0
    with SQLiteVecEngine(db_path) as engine:
        return engine.document_count()


def test_derived_asset_does_not_widen_private_lut_scope(client: TestClient):
    """阻断回归：公共源素材 + 私有 LUT，派生件不得退化成公共资源。

    只继承源素材归属时，B 读源素材 200（公共）、读 LUT 404（A 私有），
    但读 A 派生出来的新资产详情/媒体 200、列表里也看得到，
    ``derived_from`` 还把私有 LUT 的 asset_id 一起带出去。
    """
    _ensure_luts(client)
    owner = _owner_key()
    _claim(WARM, owner)
    # 前置条件：源素材是公共资源，LUT 已私有化——否则本用例证不到「派生件扩权」。
    source = _upload(client, "qin581_public.png", _image_bytes(), "image/png")
    assert client.get(f"{BASE}/assets/{source['id']}", headers=OTHER_AUTH).status_code == 200
    assert client.get(f"{BASE}/assets/{WARM}", headers=OTHER_AUTH).status_code == 404

    response = _render(client, source["id"], WARM, 1.0)
    assert response.status_code == 200, response.text
    created = response.json()["asset"]
    assert created.get("workflow_owner") == owner, "派生件必须继承 LUT 的私有归属"

    assert client.get(f"{BASE}/assets/{created['id']}", headers=AUTH).status_code == 200
    assert client.get(f"{BASE}/assets/{created['id']}/media", headers=AUTH).status_code == 200
    other_detail = client.get(f"{BASE}/assets/{created['id']}", headers=OTHER_AUTH)
    assert other_detail.status_code == 404, other_detail.text
    assert other_detail.json()["detail"]["code"] == "ASSET_NOT_FOUND"
    assert "derived_from" not in other_detail.text
    assert client.get(f"{BASE}/assets/{created['id']}/media", headers=OTHER_AUTH).status_code == 404
    listing = client.get(f"{BASE}/assets", headers=OTHER_AUTH, params={"limit": 500})
    assert listing.status_code == 200, listing.text
    assert created["id"] not in {item["id"] for item in listing.json()["assets"]}


@pytest.mark.parametrize(
    "source_extra,lut_extra,expected",
    [
        ({}, {}, {}),
        ({"workflow_owner": "o1"}, {}, {"workflow_owner": "o1"}),
        ({}, {"workflow_owner": "o1"}, {"workflow_owner": "o1"}),
        ({"workflow_owner": "o1"}, {"workflow_owner": "o1"}, {"workflow_owner": "o1"}),
        ({"project_id": "p1"}, {}, {"project_ids": ["p1"], "project_id": "p1"}),
        ({}, {"project_ids": ["p2"]}, {"project_ids": ["p2"]}),
        (
            {"project_ids": ["p1", "p2"]},
            {"project_ids": ["p2", "p3"]},
            {"project_ids": ["p2"]},
        ),
    ],
)
def test_inherited_scope_is_the_intersection_of_both_inputs(
    source_extra: dict, lut_extra: dict, expected: dict
):
    """可见范围只收窄不扩大：归属取唯一值，项目范围取交集。"""
    scope = asset_repo._inherited_scope(
        {"id": "ast_src", **source_extra}, {"id": "builtin_lut_warm", **lut_extra}
    )
    assert scope == expected


@pytest.mark.parametrize(
    "source_extra,lut_extra",
    [
        ({"workflow_owner": "o1"}, {"workflow_owner": "o2"}),
        ({"project_ids": ["p1"]}, {"project_ids": ["p2"]}),
    ],
)
def test_inherited_scope_fails_closed_when_scopes_diverge(
    source_extra: dict, lut_extra: dict
):
    """两个输入的可见范围收敛不到一个不扩权的值时，拒绝生成而不是放任扩权。"""
    with pytest.raises(CleanroomException) as excinfo:
        asset_repo._inherited_scope(
            {"id": "ast_src", **source_extra}, {"id": "builtin_lut_warm", **lut_extra}
        )
    assert excinfo.value.code == "LUT_SCOPE_MISMATCH"
    assert excinfo.value.status_code == 403


def test_rollback_keeps_output_of_committed_record(client: TestClient):
    """阻断回归：补偿必须先判定「这条记录是否真的没提交」。

    两次显式同 revision 的请求都能通过 ``_cas``。先失败的一笔释放锁后，
    另一笔可能已经用同一个派生 ID 提交成功；此时盘上文件属于已提交的那一笔，
    锁外无脑删会把别人刚提交的产物删掉。
    """
    _ensure_luts(client)
    source = _upload(client, "qin581_commit.png", _image_bytes(), "image/png")
    response = _render(client, source["id"], WARM, 1.0)
    assert response.status_code == 200, response.text
    created = response.json()["asset"]
    path = storage.resolve_output_path(created["display_path"])
    assert path.is_file()

    # 模拟「A 的补偿晚到，而 B 已经用同一个 ID 提交成功」。
    asset_repo.compensate_lut_render_outputs(
        [(created["id"], created["display_path"], path)], [created["id"]]
    )

    assert path.is_file(), "已提交的产物不能被后到的补偿删掉"
    assert client.get(f"{BASE}/assets/{created['id']}", headers=AUTH).status_code == 200


def test_interrupt_after_commit_keeps_committed_output(client: TestClient, monkeypatch):
    """阻断回归：JSON 提交完成后才中断，不得按「没提交」清掉已提交产物。"""
    _ensure_luts(client)
    source = _upload(client, "qin581_interrupt.png", _image_bytes(), "image/png")
    before = _asset_count(client)
    inner_factory = asset_repo.state

    class _InterruptingState:
        """真实提交后抛中断，模拟提交点之后被打断。"""

        def __init__(self, inner):
            self._inner = inner

        def mutate(self, mutator):
            self._inner.mutate(mutator)
            raise KeyboardInterrupt

        def __getattr__(self, name):
            return getattr(self._inner, name)

    from gw.api.routes_asset_registry import _render_lut_asset

    monkeypatch.setattr(
        asset_repo, "state", lambda: _InterruptingState(inner_factory())
    )
    with pytest.raises(KeyboardInterrupt):
        asset_repo.create_lut_render(source["id"], WARM, 1.0, None, render=_render_lut_asset)

    assert _asset_count(client) == before + 1, "记录已提交才算「已提交」"
    created = client.get(f"{BASE}/assets", headers=AUTH, params={"limit": 500}).json()["assets"]
    derived = [item for item in created if item.get("derived_from")]
    assert derived, "提交的记录必须还在"
    path = storage.resolve_output_path(derived[-1]["display_path"])
    assert path.is_file(), "已提交记录的产物必须保留"
    assert client.get(f"{BASE}/assets/{derived[-1]['id']}/media", headers=AUTH).status_code == 200


def test_json_commit_failure_leaves_no_orphan_file_or_search_document(
    client: TestClient, monkeypatch
):
    """重要回归：最终 JSON 写失败时，磁盘产物与 SQLite 检索文档都要撤销。

    检索索引是另一个存储：索引先写成功、JSON 提交随后失败时，文件清了但索引里
    仍留一条没有对应资产的孤儿文档，冷读（新连接打开数据库）就能查到。
    """
    _ensure_luts(client)
    source = _upload(client, "qin581_jsonfail.png", _image_bytes(), "image/png")
    before_assets = _asset_count(client)
    before_docs = _search_document_count()
    output_root = storage.data_root() / "media_output" / "lut_render" / source["id"]

    real_save = storage.save
    fired: list[bool] = []

    def flaky_save(namespace: str, payload) -> None:
        if namespace == asset_repo.NS_REGISTRY and not fired:
            fired.append(True)
            raise OSError("injected json commit failure")
        return real_save(namespace, payload)

    # 局部补丁必须用 context：``undo()`` 会把 conftest 注入的环境补丁一起撤掉，
    # 认证模式退回本地账户后后续请求全部 401（第 1 轮已踩过）。
    with monkeypatch.context() as patch:
        patch.setattr(storage, "save", flaky_save)
        with pytest.raises(OSError):
            _render(client, source["id"], WARM, 1.0)

    assert fired, "必须真的命中注入的提交失败，否则本用例没有测到目标路径"
    assert _asset_count(client) == before_assets, "失败不得写进资产记录"
    leftovers = list(output_root.rglob("*")) if output_root.exists() else []
    assert not [path for path in leftovers if path.is_file()], f"失败后仍残留产物：{leftovers}"
    assert _search_document_count() == before_docs, "检索索引里不得留下孤儿文档"


def _pending_render_files(source_id: str) -> list[Path]:
    """列出某个源素材目录下已落盘的派生产物。"""
    root = storage.data_root() / "media_output" / "lut_render" / source_id
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file())


def _commit_other_writer(asset_id: str, display_path: str, size_bytes: int) -> dict:
    """模拟并发的另一笔用同一派生 ID 真实提交（注册表 + 检索文档）。"""
    record = asset_repo._plain_asset(
        asset_id,
        "concurrent.png",
        "image",
        display_path=display_path,
        size_bytes=size_bytes,
    )

    def mutate(raw):
        raw["assets"][asset_id] = record
        raw.setdefault("sequences", {})[asset_id] = True
        asset_repo._bump(raw)

    asset_repo.state().mutate(mutate)
    asset_repo._upsert_asset_search_documents([record])
    return record


def test_scope_rejection_keeps_concurrently_committed_output(client: TestClient, monkeypatch):
    """阻断回归：范围推导失败时 ``created_ids`` 还是空的，补偿不能据此删文件。

    旧实现用 ``created_ids`` 当「已提交集合」：可见范围推导在记录登记**之前**
    抛 ``LUT_SCOPE_MISMATCH``，集合为空，于是把并发另一笔用同一 ID、同一路径
    提交成功的产物删掉——JSON 里有记录、媒体文件却没了，检索文档还留着。
    """
    _ensure_luts(client)
    source = _upload(client, "qin581_scope_race.png", _image_bytes(), "image/png")
    before_docs = _search_document_count()
    captured: dict = {}

    def reject_after_commit(source_record, lut_record):
        pending = _pending_render_files(source["id"])
        assert pending, "前置条件：范围拒绝之前产物必须已经落盘"
        path = pending[-1]
        asset_id = path.name.split("_v001")[0]
        _commit_other_writer(
            asset_id,
            f"media_output/lut_render/{source['id']}/{path.name}",
            path.stat().st_size,
        )
        captured.update({"path": path, "id": asset_id})
        raise CleanroomException(
            403, "LUT_SCOPE_MISMATCH", "注入：两个输入的可见范围不一致"
        )

    with monkeypatch.context() as patched:
        patched.setattr(asset_repo, "_inherited_scope", reject_after_commit)
        response = _render(client, source["id"], WARM, 1.0)

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "LUT_SCOPE_MISMATCH"
    assert captured["path"].is_file(), "并发已提交那一笔的产物不能被补偿删掉"
    detail = client.get(f"{BASE}/assets/{captured['id']}", headers=AUTH)
    assert detail.status_code == 200, detail.text
    assert _search_document_count() == before_docs + 1, "已提交记录的检索文档不得被补偿删除"


def test_interrupt_before_registration_keeps_committed_output(client: TestClient, monkeypatch):
    """阻断回归：登记前被打断，同样不能删掉并发已提交的产物。"""
    _ensure_luts(client)
    source = _upload(client, "qin581_precommit_race.png", _image_bytes(), "image/png")
    captured: dict = {}

    def interrupt_after_commit(source_record, lut_record):
        pending = _pending_render_files(source["id"])
        assert pending, "前置条件：打断之前产物必须已经落盘"
        path = pending[-1]
        asset_id = path.name.split("_v001")[0]
        _commit_other_writer(
            asset_id,
            f"media_output/lut_render/{source['id']}/{path.name}",
            path.stat().st_size,
        )
        captured.update({"path": path, "id": asset_id})
        raise KeyboardInterrupt

    from gw.api.routes_asset_registry import _render_lut_asset

    with monkeypatch.context() as patched:
        patched.setattr(asset_repo, "_inherited_scope", interrupt_after_commit)
        with pytest.raises(KeyboardInterrupt):
            asset_repo.create_lut_render(
                source["id"], WARM, 1.0, None, render=_render_lut_asset
            )

    assert captured["path"].is_file(), "登记前中断也不得删掉已提交产物"
    assert client.get(f"{BASE}/assets/{captured['id']}", headers=AUTH).status_code == 200


def test_json_failure_removes_own_file_but_keeps_committed_document(
    client: TestClient, monkeypatch
):
    """重要回归：同 ID 但另一条源路径时，文件按路径判、索引按 ID 判。

    旧实现只看 ID：同 ID 已有记录就跳过删文件，本笔的 PNG 变成孤儿；
    若反过来按文件归属去删索引，又会删掉并发那一笔的检索文档。
    """
    _ensure_luts(client)
    source = _upload(client, "qin581_path_race.png", _image_bytes(), "image/png")
    other_source = _upload(
        client, "qin581_path_race_other.png", _image_bytes(), "image/png"
    )
    before_docs = _search_document_count()
    real_save = storage.save
    fired: list[bool] = []
    captured: dict = {}

    def flaky_save(namespace: str, payload) -> None:
        if namespace == asset_repo.NS_REGISTRY and not fired:
            fired.append(True)
            pending = _pending_render_files(source["id"])
            path = pending[-1]
            asset_id = path.name.split("_v001")[0]
            other_dir = (
                storage.data_root() / "media_output" / "lut_render" / other_source["id"]
            )
            other_dir.mkdir(parents=True, exist_ok=True)
            other_path = other_dir / f"{asset_id}_v001.png"
            other_path.write_bytes(path.read_bytes())
            _commit_other_writer(
                asset_id,
                f"media_output/lut_render/{other_source['id']}/{other_path.name}",
                other_path.stat().st_size,
            )
            captured.update({"own": path, "other": other_path, "id": asset_id})
            raise OSError("injected json commit failure")
        return real_save(namespace, payload)

    with monkeypatch.context() as patched:
        patched.setattr(storage, "save", flaky_save)
        with pytest.raises(OSError):
            _render(client, source["id"], WARM, 1.0)

    assert fired, "必须真的命中注入的提交失败"
    assert not captured["own"].exists(), "本笔未提交的文件必须清掉，不能因为同 ID 有记录就留下"
    assert captured["other"].is_file(), "并发那一笔的产物必须保留"
    assert _search_document_count() == before_docs + 1, "并发那一笔的检索文档必须保留"
    assert client.get(f"{BASE}/assets/{captured['id']}", headers=AUTH).status_code == 200


def test_search_cleanup_failure_is_observable_and_retryable(
    client: TestClient, monkeypatch, caplog
):
    """重要回归：补偿删除索引失败不得静默；须留痕，且按**统一入口**重放可补完。"""
    _ensure_luts(client)
    source = _upload(client, "qin581_cleanup_fault.png", _image_bytes(), "image/png")
    before_docs = _search_document_count()
    real_save = storage.save
    fired: list[bool] = []
    attempts: list[list[str]] = []

    def flaky_save(namespace: str, payload) -> None:
        if namespace == asset_repo.NS_REGISTRY and not fired:
            fired.append(True)
            raise OSError("injected json commit failure")
        return real_save(namespace, payload)

    def broken_delete(asset_ids, *, strict=False):
        attempts.append(list(asset_ids))
        raise RuntimeError("injected search delete failure")

    with monkeypatch.context() as patched:
        patched.setattr(storage, "save", flaky_save)
        patched.setattr(asset_repo, "delete_asset_search_documents", broken_delete)
        with caplog.at_level(logging.ERROR, logger=asset_repo.__name__):
            with pytest.raises(OSError):
                _render(client, source["id"], WARM, 1.0)

    assert fired
    assert len(attempts) == 2, "补偿应有限重试，不能一次失败就放弃"
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "LUT_RENDER_COMPENSATION_INCOMPLETE" in messages, messages
    assert attempts[0] and attempts[0][0] in messages, messages
    # 日志要指向可安全重放的入口，不能让人直接按 ID 删检索文档（ID 会被复用）。
    assert "compensate_lut_render_outputs" in messages, messages
    # 故障确实留下了孤儿文档（证明打到失败路径），且同一入口重放即可补完。
    assert _search_document_count() == before_docs + 1
    assert asset_repo.compensate_lut_render_outputs([], attempts[0]) == []
    assert _search_document_count() == before_docs


def test_unlink_failure_keeps_original_error_and_code(
    client: TestClient, monkeypatch, caplog
):
    """重要回归：补偿里的 unlink 失败不得覆盖原始的 503 错误码。"""
    _ensure_luts(client)
    source = _upload(client, "qin581_unlink_fault.png", _image_bytes(), "image/png")

    def broken_upsert(_records):
        raise CleanroomException(503, "ASSET_SEARCH_INDEX_FAILED", "检索索引写入失败")

    real_unlink = Path.unlink

    def deny_lut_render_unlink(self, *args, **kwargs):
        if "lut_render" in str(self).replace("\\", "/"):
            raise PermissionError("injected unlink denial")
        return real_unlink(self, *args, **kwargs)

    from gw.api.routes_asset_registry import _render_lut_asset

    with monkeypatch.context() as patched:
        patched.setattr(asset_repo, "_upsert_asset_search_documents", broken_upsert)
        patched.setattr(Path, "unlink", deny_lut_render_unlink)
        with caplog.at_level(logging.ERROR, logger=asset_repo.__name__):
            with pytest.raises(CleanroomException) as excinfo:
                asset_repo.create_lut_render(
                    source["id"], WARM, 1.0, None, render=_render_lut_asset
                )

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "ASSET_SEARCH_INDEX_FAILED", "补偿失败不得覆盖原错误码"
    notes = "\n".join(getattr(excinfo.value, "__notes__", []))
    assert "补偿未完成" in notes, notes
    assert "PermissionError" in notes, notes
    assert any(
        "LUT 派生产物清理失败" in record.getMessage() for record in caplog.records
    ), [record.getMessage() for record in caplog.records]
    assert _pending_render_files(source["id"]), "注入的 unlink 失败必须让文件留在盘上"


def test_interrupt_inside_renderer_leaves_no_orphan_file(client: TestClient):
    """阻断回归：渲染器写完文件、还没返回就中断，产物也必须被清掉。

    旧实现等 ``render`` 返回之后才登记产物：真实 PNG 已经落盘、返回前抛
    ``KeyboardInterrupt``（或进程被杀），登记这一步永远走不到，补偿清单是空的，
    于是留下一个没有任何记录引用的孤儿 PNG。产物必须在调用渲染器**之前**登记。
    """
    _ensure_luts(client)
    source = _upload(client, "qin581_render_interrupt.png", _image_bytes(), "image/png")
    before_assets = _asset_count(client)
    before_docs = _search_document_count()

    def interrupting_render(_kind, _source, _lut, _strength, target: Path):
        target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        assert target.is_file(), "前置条件：中断前产物必须真的写到盘上"
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        asset_repo.create_lut_render(
            source["id"], WARM, 1.0, None, render=interrupting_render
        )

    assert not _pending_render_files(source["id"]), "渲染器写出文件后被中断，必须由统一补偿清掉"
    assert _asset_count(client) == before_assets, "中断不得写进资产记录"
    assert _search_document_count() == before_docs, "检索索引里不得留下孤儿文档"


def test_render_failure_cleanup_failure_is_aggregated(
    client: TestClient, monkeypatch, caplog
):
    """阻断回归：普通渲染错误的早期清理失败，也要进同一份汇总。

    旧实现在渲染异常分支里单独 ``unlink`` 且不看返回值：清理失败既没有日志也没有
    note，运维看不到「盘上还留着半成品」。改走统一补偿后，失败项会带 stage/path
    汇总进 ``LUT_RENDER_COMPENSATION_INCOMPLETE``，并挂到原异常上。
    """
    _ensure_luts(client)
    source = _upload(client, "qin581_render_cleanup_fault.png", _image_bytes(), "image/png")

    def failing_render(_kind, _source, _lut, _strength, target: Path):
        target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        raise RuntimeError("injected renderer crash")

    real_unlink = Path.unlink

    def deny_lut_render_unlink(self, *args, **kwargs):
        if "lut_render" in str(self).replace("\\", "/"):
            raise PermissionError("injected unlink denial")
        return real_unlink(self, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "unlink", deny_lut_render_unlink)
        with caplog.at_level(logging.ERROR, logger=asset_repo.__name__):
            with pytest.raises(CleanroomException) as excinfo:
                asset_repo.create_lut_render(
                    source["id"], WARM, 1.0, None, render=failing_render
                )

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "LUT_RENDER_FAILED", "渲染错误码不得被补偿失败覆盖"
    notes = "\n".join(getattr(excinfo.value, "__notes__", []))
    assert "补偿未完成" in notes, notes
    assert "PermissionError" in notes, notes
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "LUT_RENDER_COMPENSATION_INCOMPLETE" in messages, messages
    assert "media" in messages and "lut_render" in messages, messages
    assert _pending_render_files(source["id"]), "注入的清理失败必须让半成品留在盘上"


def test_compensation_crash_keeps_original_error(client: TestClient, monkeypatch, caplog):
    """重要回归：补偿自身崩溃也不得把原错误码换成补偿的异常。"""
    _ensure_luts(client)
    source = _upload(client, "qin581_comp_crash.png", _image_bytes(), "image/png")

    def broken_upsert(_records):
        raise CleanroomException(503, "ASSET_SEARCH_INDEX_FAILED", "检索索引写入失败")

    def crashing_discard(_path):
        raise RuntimeError("injected compensation crash")

    from gw.api.routes_asset_registry import _render_lut_asset

    with monkeypatch.context() as patched:
        patched.setattr(asset_repo, "_upsert_asset_search_documents", broken_upsert)
        patched.setattr(asset_repo, "_discard_rendered_file", crashing_discard)
        with caplog.at_level(logging.ERROR, logger=asset_repo.__name__):
            with pytest.raises(CleanroomException) as excinfo:
                asset_repo.create_lut_render(
                    source["id"], WARM, 1.0, None, render=_render_lut_asset
                )

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "ASSET_SEARCH_INDEX_FAILED", "补偿崩溃不得覆盖原错误码"
    notes = "\n".join(getattr(excinfo.value, "__notes__", []))
    assert "compensation" in notes and "RuntimeError" in notes, notes
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "LUT_RENDER_COMPENSATION_CRASHED" in messages, messages


def test_replay_after_id_reuse_keeps_concurrent_artifacts(
    client: TestClient, monkeypatch
):
    """重要回归：失败 ID 被别的写入复用后，恢复必须走 guarded 入口。

    补偿没清干净时日志里会留 asset_id，事后按它恢复。裸按 ID 删检索文档不安全：
    同一个 ID 可能已经被另一笔 B 提交（并发同 revision / 序列复用），B 的记录与
    媒体文件都还在，文档却被删掉。统一入口在锁内先查注册表，遇到已提交记录直接
    跳过，因此重放对 B 无害。
    """
    _ensure_luts(client)
    source = _upload(client, "qin581_replay.png", _image_bytes(), "image/png")
    other_source = _upload(client, "qin581_replay_other.png", _image_bytes(), "image/png")
    before_docs = _search_document_count()
    real_save = storage.save
    fired: list[bool] = []
    attempts: list[list[str]] = []

    def flaky_save(namespace: str, payload) -> None:
        if namespace == asset_repo.NS_REGISTRY and not fired:
            fired.append(True)
            raise OSError("injected json commit failure")
        return real_save(namespace, payload)

    def broken_delete(asset_ids, *, strict=False):
        attempts.append(list(asset_ids))
        raise RuntimeError("injected search delete failure")

    with monkeypatch.context() as patched:
        patched.setattr(storage, "save", flaky_save)
        patched.setattr(asset_repo, "delete_asset_search_documents", broken_delete)
        with pytest.raises(OSError):
            _render(client, source["id"], WARM, 1.0)

    assert fired and attempts, "必须真的走到「JSON 失败 + 索引清理失败」的分支"
    orphan_id = attempts[0][0]
    assert _search_document_count() == before_docs + 1, "前置条件：此刻确有 1 条孤儿文档"

    # B 用同一个派生 ID 在另一条源素材下真实提交（记录 + 文件 + 文档）。
    other_dir = storage.data_root() / "media_output" / "lut_render" / other_source["id"]
    other_dir.mkdir(parents=True, exist_ok=True)
    other_path = other_dir / f"{orphan_id}_v001.png"
    other_path.write_bytes(_image_bytes())
    _commit_other_writer(
        orphan_id,
        f"media_output/lut_render/{other_source['id']}/{other_path.name}",
        other_path.stat().st_size,
    )

    assert asset_repo.compensate_lut_render_outputs([], [orphan_id]) == []
    assert _search_document_count() == before_docs + 1, "B 的检索文档不得被重放删掉"
    assert other_path.is_file(), "B 的产物不得被重放删掉"
    assert client.get(f"{BASE}/assets/{orphan_id}", headers=AUTH).status_code == 200
    # 幂等：再放一次也不会动 B。
    assert asset_repo.compensate_lut_render_outputs([], [orphan_id]) == []
    assert _search_document_count() == before_docs + 1
