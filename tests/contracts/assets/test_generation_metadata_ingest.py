# -*- coding: utf-8 -*-
"""QIN-600 入库期生成元数据：AC1-AC9 与票面风险项。

样本全部由本文件用 `zlib` + 手工构造字节生成，不引用真实素材库（AC9）。
行为断言来自真实执行：真实 PNG/JPEG/WebP 字节、真实索引任务、真实注册表落盘。
"""

from __future__ import annotations

import json
import struct
import time
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import generation_metadata as gm
from gw.asset_registry import index_jobs as jobs
from gw.asset_registry import repository as repo
from gw.core import storage

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer qin600-owner", "X-User-Role": "editor"}


# --------------------------------------------------------------------------- #
# 合成样本（AC9）
# --------------------------------------------------------------------------- #
def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def png_bytes(chunks: list[tuple[bytes, bytes, bytes]]) -> bytes:
    """1×1 RGB PNG；chunks 为 (块类型, 关键字, 文本) 列表。"""
    parts = [
        b"\x89PNG\r\n\x1a\n",
        _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)),
    ]
    for kind, keyword, text in chunks:
        if kind == b"tEXt":
            body = keyword + b"\x00" + text
        elif kind == b"zTXt":
            body = keyword + b"\x00\x00" + zlib.compress(text)
        else:  # iTXt，未压缩
            body = keyword + b"\x00\x00\x00" + b"\x00" + b"\x00" + text
        parts.append(_chunk(kind, body))
    parts.append(_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")))
    parts.append(_chunk(b"IEND", b""))
    return b"".join(parts)


def plain_png() -> bytes:
    return png_bytes([])


def _tiff(entries: list[tuple[int, int, bytes]]) -> bytes:
    """最小小端 TIFF；count 由数据长度推导。"""
    normalized = [(tag, value_type, len(data), data) for tag, value_type, data in entries]
    header = b"II" + struct.pack("<HI", 42, 8)
    data_offset = 8 + 2 + 12 * len(normalized) + 4
    tail = b""
    records = b""
    for tag, value_type, count, data in normalized:
        if len(data) <= 4:
            records += struct.pack("<HHI", tag, value_type, count) + data.ljust(4, b"\x00")
        else:
            records += struct.pack("<HHI", tag, value_type, count) + struct.pack(
                "<I", data_offset + len(tail)
            )
            tail += data
    return header + struct.pack("<H", len(normalized)) + records + struct.pack("<I", 0) + tail


def jpeg_bytes(tiff: bytes | None = None, xmp: bytes | None = None) -> bytes:
    out = b"\xff\xd8"
    if tiff is not None:
        payload = b"Exif\x00\x00" + tiff
        out += b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    if xmp is not None:
        payload = b"http://ns.adobe.com/xap/1.0/\x00" + xmp
        out += b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return out + b"\xff\xda\x00\x02"


def webp_bytes(exif: bytes = b"", xmp: bytes = b"") -> bytes:
    chunks = b""
    for fourcc, payload in ((b"EXIF", exif), (b"XMP ", xmp)):
        if not payload:
            continue
        chunks += fourcc + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            chunks += b"\x00"
    body = b"WEBP" + chunks
    return b"RIFF" + struct.pack("<I", len(body)) + body


A1111_TEXT = (
    b"a misty harbour at dawn, cinematic\n"
    b"Negative prompt: blurry, watermark\n"
    b"Steps: 32, Sampler: DPM++ 2M, CFG scale: 7, Seed: 7, Size: 1024x1024"
)

COMFY_API_GRAPH = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"positive": ["6", 0], "negative": ["7", 0], "seed": 1},
    },
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "comfy api positive"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "comfy api negative"}},
}

COMFY_UI = {
    "nodes": [
        {
            "id": 1,
            "type": "KSampler",
            "inputs": [
                {"name": "positive", "link": 11},
                {"name": "negative", "link": 12},
            ],
            "widgets_values": [],
        },
        {"id": 5, "type": "CLIPTextEncode", "widgets_values": ["ui positive"]},
        {"id": 6, "type": "CLIPTextEncode", "widgets_values": ["ui negative"]},
    ],
    "links": [
        [11, 5, 0, 1, 0, "CONDITIONING"],
        [12, 6, 0, 1, 1, "CONDITIONING"],
    ],
}

NOVELAI_COMMENT = {"prompt": "novelai positive", "uc": "novelai negative", "steps": 28}

XMP_MIDJOURNEY = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
    b'dc:description="midjourney otter --ar 3:2 --v 6"/>'
    b"</rdf:RDF></x:xmpmeta>"
)


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def ingest_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "gw-auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "gw-video"))
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(library))
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None
    yield library
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None


def write_file(library: Path, name: str, payload: bytes) -> Path:
    target = library / name
    target.write_bytes(payload)
    return target


def submit(client, kind="reindex", key="qin600"):
    return client.post(
        BASE + "/" + kind,
        headers={**AUTH, "Idempotency-Key": key},
        json={"background": True},
    )


def read_job(client, job_id):
    return client.get(BASE + "/workspace-jobs/" + job_id, headers=AUTH)


def wait_job(client, job_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = read_job(client, job_id).json()["job"]
        if job["status"] in jobs.TERMINAL:
            return job
        time.sleep(0.02)
    pytest.fail("后台索引未在期限内终结")


def indexed_asset_by_name(name: str) -> dict:
    raw = repo.state().read()
    for asset in raw["assets"].values():
        if asset.get("name") == name:
            return asset
    raise AssertionError(f"未找到素材 {name}")


# --------------------------------------------------------------------------- #
# AC1 / AC2：字段结构与读取位置
# --------------------------------------------------------------------------- #
def test_png_a1111_parameters_fields(tmp_path):
    """AC1 + AC2：A1111 `parameters` → 正/反向提示词与字段结构。"""
    target = tmp_path / "a1111.png"
    target.write_bytes(png_bytes([(b"tEXt", b"parameters", A1111_TEXT)]))
    generation, warnings = gm.extract_generation(target)
    assert warnings == 0
    assert generation["tool"] == "a1111"
    assert generation["source"] == "parameters"
    assert generation["raw_keys"] == ["parameters"]
    assert generation["truncated"] is False
    assert generation["prompt"] == "a misty harbour at dawn, cinematic"
    assert generation["negative_prompt"] == "blurry, watermark"
    assert set(generation) == {
        "prompt",
        "negative_prompt",
        "tool",
        "source",
        "raw_keys",
        "truncated",
    }


@pytest.mark.parametrize("kind", [b"tEXt", b"zTXt", b"iTXt"])
def test_png_text_block_variants_all_read(tmp_path, kind):
    """AC2：PNG 三种文本块都能读到同一份提示词。"""
    target = tmp_path / f"variant-{kind.decode()}.png"
    target.write_bytes(png_bytes([(kind, b"parameters", A1111_TEXT)]))
    generation, _ = gm.extract_generation(target)
    assert generation["prompt"] == "a misty harbour at dawn, cinematic"


def test_png_comfyui_api_follows_sampler_positive(tmp_path):
    """AC2：ComfyUI API 格式从 `inputs.positive` 追到文本编码节点。"""
    target = tmp_path / "comfy.png"
    target.write_bytes(
        png_bytes([(b"tEXt", b"prompt", json.dumps(COMFY_API_GRAPH).encode("utf-8"))])
    )
    generation, warnings = gm.extract_generation(target)
    assert warnings == 0
    assert (generation["tool"], generation["source"]) == ("comfyui", "prompt")
    assert generation["prompt"] == "comfy api positive"
    assert generation["negative_prompt"] == "comfy api negative"


def test_png_comfyui_api_fallback_records_node_hint(tmp_path):
    """AC2：链路断开时按节点 id 升序回退，并在 raw_keys 追加所用节点 id。"""
    broken = {
        "1": {
            "class_type": "KSampler",
            "inputs": {"positive": ["99", 0], "negative": ["98", 0]},
        },
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "fallback positive"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "fallback negative"}},
    }
    target = tmp_path / "fallback.png"
    target.write_bytes(png_bytes([(b"tEXt", b"prompt", json.dumps(broken).encode())]))
    generation, _ = gm.extract_generation(target)
    assert generation["prompt"] == "fallback positive"
    assert "prompt#node:5" in generation["raw_keys"]


def test_png_comfyui_workflow_ui_uses_links(tmp_path):
    """AC2：ComfyUI UI 格式按 `links` 解析采样器 positive 连线。"""
    target = tmp_path / "ui.png"
    target.write_bytes(
        png_bytes([(b"tEXt", b"workflow", json.dumps(COMFY_UI).encode())])
    )
    generation, _ = gm.extract_generation(target)
    assert (generation["tool"], generation["source"]) == ("comfyui", "workflow")
    assert generation["prompt"] == "ui positive"
    assert generation["negative_prompt"] == "ui negative"


def test_png_novelai_comment_and_description(tmp_path):
    """AC2：NovelAI 的 `Comment`（JSON），`Comment` 缺失时回退 `Description`。"""
    comment = tmp_path / "novelai.png"
    comment.write_bytes(
        png_bytes([(b"tEXt", b"Comment", json.dumps(NOVELAI_COMMENT).encode())])
    )
    generation, _ = gm.extract_generation(comment)
    assert (generation["tool"], generation["source"]) == ("novelai", "Comment")
    assert generation["prompt"] == "novelai positive"
    assert generation["negative_prompt"] == "novelai negative"

    description = tmp_path / "novelai-desc.png"
    description.write_bytes(png_bytes([(b"tEXt", b"Description", b"plain description")]))
    generation, _ = gm.extract_generation(description)
    assert (generation["tool"], generation["source"]) == ("novelai", "Description")
    assert generation["prompt"] == "plain description"


def test_png_midjourney_description_strips_arguments(tmp_path):
    """AC2：Midjourney 的 `--ar/--v` 参数后缀不入库。"""
    target = tmp_path / "mj.png"
    target.write_bytes(png_bytes([(b"tEXt", b"Description", b"mj otter --ar 2:1 --v 6")]))
    generation, _ = gm.extract_generation(target)
    assert generation["tool"] == "midjourney"
    assert generation["prompt"] == "mj otter"


def test_jpeg_exif_user_comment_and_xmp(tmp_path):
    """AC2：JPEG 的 EXIF `UserComment`、`ImageDescription` 与 XMP `dc:description`。"""
    comment = tmp_path / "comment.jpg"
    comment.write_bytes(
        jpeg_bytes(
            _tiff([(0x9286, 7, b"UNICODE\x00" + "jpeg 提示词".encode("utf-16-le"))])
        )
    )
    generation, _ = gm.extract_generation(comment)
    assert generation["source"] == "exif:UserComment"
    assert generation["prompt"] == "jpeg 提示词"

    described = tmp_path / "described.jpg"
    described.write_bytes(jpeg_bytes(_tiff([(0x010E, 2, b"jpeg description\x00")])))
    generation, _ = gm.extract_generation(described)
    assert generation["source"] == "exif:ImageDescription"
    assert generation["prompt"] == "jpeg description"

    xmp_only = tmp_path / "xmp.jpg"
    xmp_only.write_bytes(jpeg_bytes(_tiff([]), XMP_MIDJOURNEY))
    generation, _ = gm.extract_generation(xmp_only)
    assert generation["source"] == "xmp:dc:description"
    assert generation["tool"] == "midjourney"
    assert generation["prompt"] == "midjourney otter"


def test_webp_exif_prompt_chunk(tmp_path):
    """AC2：WebP 的 `EXIF` chunk 里 `prompt:<json>`（沿用既有上传读取口径）。"""
    payload = b'prompt:{"1":{"class_type":"CLIPTextEncode","inputs":{"text":"webp prompt"}}}'
    target = tmp_path / "webp.webp"
    target.write_bytes(webp_bytes(exif=payload))
    generation, warnings = gm.extract_generation(target)
    assert warnings == 0
    assert (generation["tool"], generation["source"]) == ("comfyui", "exif:prompt")
    assert generation["prompt"] == "webp prompt"


# --------------------------------------------------------------------------- #
# AC3：截断、失败与「不解码像素」
# --------------------------------------------------------------------------- #
def test_text_block_over_limit_is_truncated(tmp_path):
    """AC3：单块超过 1 MiB 只读前 1 MiB，并置 `truncated=true`。"""
    big = b"z" * (gm.MAX_TEXT_BLOCK_BYTES + 4096)
    target = tmp_path / "big.png"
    target.write_bytes(png_bytes([(b"tEXt", b"parameters", big)]))
    generation, warnings = gm.extract_generation(target)
    assert warnings == 0
    assert generation["truncated"] is True
    assert len(generation["prompt"].encode("utf-8")) <= gm.MAX_STORED_PROMPT_BYTES


def test_store_limit_truncates_prompt_to_eight_kib(tmp_path):
    """PM Q2：登记落盘上限 8 KiB，超出置 `truncated=true`。"""
    oversized = b"q" * (gm.MAX_STORED_PROMPT_BYTES * 3)
    target = tmp_path / "long.png"
    target.write_bytes(png_bytes([(b"tEXt", b"parameters", oversized)]))
    generation, _ = gm.extract_generation(target)
    assert len(generation["prompt"].encode("utf-8")) <= gm.MAX_STORED_PROMPT_BYTES
    assert generation["truncated"] is True


def test_broken_payload_only_bumps_warning(tmp_path):
    """AC3：损坏的 JSON 块不抛异常，只是不写字段并计 1 次 warning。"""
    target = tmp_path / "broken.png"
    target.write_bytes(png_bytes([(b"tEXt", b"prompt", b"{not json")]))
    generation, warnings = gm.extract_generation(target)
    assert generation is None
    assert warnings == 1


def test_broken_zlib_chunk_is_ignored(tmp_path):
    """AC3：zTXt 压缩体损坏按解析失败处理，不影响调用方。"""
    raw = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _chunk(b"zTXt", b"parameters\x00\x00" + b"not-zlib")
        + _chunk(b"IEND", b"")
    )
    target = tmp_path / "bad-zlib.png"
    target.write_bytes(raw)
    generation, warnings = gm.extract_generation(target)
    assert generation is None
    assert warnings == 0


def test_extraction_never_decodes_pixels(tmp_path, monkeypatch):
    """AC3：只读元数据段；像素解码入口被调用即失败。"""
    import PIL.Image

    def explode(*_args, **_kwargs):
        raise AssertionError("入库期不得整幅解码像素")

    monkeypatch.setattr(PIL.Image, "open", explode, raising=False)
    target = tmp_path / "no-decode.png"
    target.write_bytes(png_bytes([(b"tEXt", b"parameters", A1111_TEXT)]))
    generation, _ = gm.extract_generation(target)
    assert generation["prompt"] == "a misty harbour at dawn, cinematic"


# --------------------------------------------------------------------------- #
# AC4：无元数据不写字段
# --------------------------------------------------------------------------- #
def test_plain_image_writes_no_generation(tmp_path):
    """AC4：普通图片不写空对象、不写占位。"""
    target = tmp_path / "plain.png"
    target.write_bytes(plain_png())
    assert gm.extract_generation(target) == (None, 0)

    text_file = tmp_path / "note.txt"
    text_file.write_text("not an image", encoding="utf-8")
    assert gm.extract_generation(text_file) == (None, 0)


# --------------------------------------------------------------------------- #
# AC1 / AC5 / AC6：索引任务真实落盘
# --------------------------------------------------------------------------- #
def test_reindex_writes_generation_and_keeps_other_metadata(ingest_runtime):
    """AC1 + AC5：reindex 写入 generation；其他 metadata 键不被清掉。"""
    write_file(ingest_runtime, "a1111.png", png_bytes([(b"tEXt", b"parameters", A1111_TEXT)]))
    with TestClient(create_app()) as client:
        first = submit(client).json()
        job = wait_job(client, first["job_id"])
        assert job["status"] == "succeeded", job
        assert job["result"]["generation"] == {
            "scanned_images": 1,
            "with_metadata": 1,
            "warnings": 0,
            "truncated": 0,
        }
        asset = indexed_asset_by_name("a1111.png")
        assert asset["metadata"]["generation"]["tool"] == "a1111"

        # 模拟其他来源写入的键，再跑一次 reindex，必须原样保留（合并不覆盖）。
        def add_other_keys(raw):
            record = raw["assets"][asset["asset_id"]]
            record["metadata"]["lut"] = {"size": 17}
            record["metadata"]["workflow_id"] = "wf-keep"

        repo.state().mutate(add_other_keys)
        second = submit(client, key="qin600-second").json()
        wait_job(client, second["job_id"])
        asset = indexed_asset_by_name("a1111.png")
        assert asset["metadata"]["lut"] == {"size": 17}
        assert asset["metadata"]["workflow_id"] == "wf-keep"
        assert asset["metadata"]["generation"]["prompt"] == "a misty harbour at dawn, cinematic"


def test_reindex_is_replayable(ingest_runtime):
    """AC6：同一素材库连跑两次，第二次不产生变更、revision 不变。"""
    write_file(ingest_runtime, "novelai.png", png_bytes([(b"tEXt", b"Comment", json.dumps(NOVELAI_COMMENT).encode())]))
    with TestClient(create_app()) as client:
        wait_job(client, submit(client, key="replay-1").json()["job_id"])
        revision = repo.state().read()["revision"]
        second = wait_job(client, submit(client, key="replay-2").json()["job_id"])
        assert second["result"]["indexed"] == 0
        assert second["result"]["updated"] == 0
        assert second["result"]["revision"] == revision


def test_reindex_backfills_generation_for_existing_record(ingest_runtime):
    """AC6 存量补齐：已入库但缺 generation 的记录，必须经 `_commit` 合并分支补齐。

    这条守卫专指「升级前没有该键的存量记录」：新建资产走 `_plain_asset` 整体建记录，
    即使 `_commit` 的 generation 合并分支被整个删掉，新建路径的用例也照样全绿。
    因此这里显式构造存量记录（保留其他 metadata 键、删掉 generation），
    断言 `updated == 1`（证明确实走了合并分支）、落盘字段与写后回读一致。
    """
    write_file(ingest_runtime, "legacy.png", png_bytes([(b"tEXt", b"parameters", A1111_TEXT)]))
    with TestClient(create_app()) as client:
        wait_job(client, submit(client, key="legacy-1").json()["job_id"])
        asset = indexed_asset_by_name("legacy.png")

        # 模拟升级前的存量记录：字段齐全、其他 metadata 键在，但没有 generation。
        def strip_generation(raw):
            record = raw["assets"][asset["asset_id"]]
            record["metadata"].pop("generation", None)
            record["metadata"]["workflow_id"] = "wf-legacy"

        repo.state().mutate(strip_generation)
        assert "generation" not in indexed_asset_by_name("legacy.png")["metadata"]

        job = wait_job(client, submit(client, key="legacy-2").json()["job_id"])
        assert job["status"] == "succeeded", job
        assert job["result"]["indexed"] == 0
        # 只有本文件的元数据变化 → updated 必须为 1（合并分支未生效时为 0）。
        assert job["result"]["updated"] == 1

        stored = indexed_asset_by_name("legacy.png")["metadata"]
        assert stored["generation"]["tool"] == "a1111"
        assert stored["generation"]["prompt"] == "a misty harbour at dawn, cinematic"
        assert stored["workflow_id"] == "wf-legacy"

        # 写后回读：列表接口原样返回补写后的字段（Q3 裁定不裁剪）。
        listed = client.get(BASE + "/assets", headers=AUTH, params={"limit": "20"}).json()
        entry = next(item for item in listed["items"] if item["name"] == "legacy.png")
        assert entry["metadata"]["generation"]["tool"] == "a1111"


def test_sync_kind_does_not_read_metadata(ingest_runtime, monkeypatch):
    """副作用边界：sync 只做差集统计，不读文件元数据。"""
    write_file(ingest_runtime, "a1111.png", png_bytes([(b"tEXt", b"parameters", A1111_TEXT)]))
    calls = []

    def tracking(path):
        calls.append(path.name)
        return None, 0

    monkeypatch.setattr(gm, "extract_generation", tracking)
    with TestClient(create_app()) as client:
        job = wait_job(client, submit(client, kind="index/sync", key="sync-1").json()["job_id"])
        assert job["status"] == "succeeded", job
        assert calls == []
        assert "generation" not in job["result"]


def test_write_then_refresh_returns_generation(ingest_runtime):
    """风险项「写后刷新」：索引提交后读取接口立即返回新字段。"""
    write_file(ingest_runtime, "webp.webp", webp_bytes(
        exif=b'prompt:{"1":{"class_type":"CLIPTextEncode","inputs":{"text":"webp prompt"}}}'
    ))
    with TestClient(create_app()) as client:
        wait_job(client, submit(client, key="refresh-1").json()["job_id"])
        assets = client.get(BASE + "/assets", headers=AUTH, params={"limit": "20"}).json()
        entry = next(item for item in assets["items"] if item["name"] == "webp.webp")
        assert entry["metadata"]["generation"]["tool"] == "comfyui"
        assert entry["metadata"]["generation"]["prompt"] == "webp prompt"


# --------------------------------------------------------------------------- #
# AC2：GW 产物登记（tool=gw / source=gw-task）
# --------------------------------------------------------------------------- #
def test_gw_output_registers_gw_task_generation(ingest_runtime):
    """AC2：GW 产物登记写入任务提交的提示词，`tool=gw` / `source=gw-task`。"""
    from gw.god_workflow import asset_hub_bridge, registry, routes

    context = routes._auth(AUTH["Authorization"], "editor", True)
    generation = routes._task_generation({"prompt_snapshot": COMFY_API_GRAPH})
    asset = registry.store_bytes(
        context, plain_png(), "gw-output.png", "image/png", generation=generation
    )
    assert asset["generation"]["tool"] == "gw"
    result = asset_hub_bridge.register_output_to_asset_hub(
        asset, context=context, generation=generation
    )
    assert not result.get("error"), result

    record = repo.state().read()["assets"][asset["asset_id"]]
    stored = record["metadata"]["generation"]
    assert stored["tool"] == "gw"
    assert stored["source"] == "gw-task"
    assert stored["prompt"] == "comfy api positive"
    assert stored["negative_prompt"] == "comfy api negative"
    assert stored["truncated"] is False


def test_gw_reconcile_replay_reuses_stored_generation(ingest_runtime):
    """重放登记时从资产凭单复用同一份提示词，不伪造也不丢字段。"""
    from gw.god_workflow import asset_hub_bridge, registry, routes

    context = routes._auth(AUTH["Authorization"], "editor", True)
    generation = routes._task_generation({"prompt_snapshot": COMFY_API_GRAPH})
    asset = registry.store_bytes(
        context, plain_png(), "gw-replay.png", "image/png", generation=generation
    )
    # 不传 generation，模拟 reconcile_pending 的调用形态。
    asset_hub_bridge.register_output_to_asset_hub(asset, context=context)
    record = repo.state().read()["assets"][asset["asset_id"]]
    assert record["metadata"]["generation"]["source"] == "gw-task"


def test_task_generation_is_none_without_prompt_snapshot(ingest_runtime):
    """任务没有可用快照时不写字段（不伪造空对象）。"""
    from gw.god_workflow import routes

    assert routes._task_generation(None) is None
    assert routes._task_generation({}) is None
    assert routes._task_generation({"prompt_snapshot": {}}) is None
    assert routes._task_generation(
        {"prompt_snapshot": {"1": {"class_type": "PreviewImage", "inputs": {}}}}
    ) is None


# --------------------------------------------------------------------------- #
# 风险项：失败隔离、慢、取消、乱序、并发、退出
# --------------------------------------------------------------------------- #
def test_broken_file_does_not_fail_index_job(ingest_runtime):
    """异步-失败：单个坏文件不影响其余文件与任务结果，失败计数可见。"""
    write_file(ingest_runtime, "broken.png", png_bytes([(b"tEXt", b"prompt", b"{oops")]))
    write_file(ingest_runtime, "good.png", png_bytes([(b"tEXt", b"parameters", A1111_TEXT)]))
    with TestClient(create_app()) as client:
        job = wait_job(client, submit(client, key="failure-1").json()["job_id"])
        assert job["status"] == "succeeded", job
        stats = job["result"]["generation"]
        assert stats["scanned_images"] == 2
        assert stats["with_metadata"] == 1
        assert stats["warnings"] == 1
        assert "generation" not in indexed_asset_by_name("broken.png")["metadata"]
        assert indexed_asset_by_name("good.png")["metadata"]["generation"]["tool"] == "a1111"


def test_slow_metadata_read_does_not_complete_early(ingest_runtime, monkeypatch):
    """异步-慢（3 秒档）：扫描期间任务不得提前标完成，完成后字段齐全。"""
    write_file(ingest_runtime, "slow.png", png_bytes([(b"tEXt", b"parameters", A1111_TEXT)]))
    real_extract = gm.extract_generation

    def slow(path):
        time.sleep(3)
        return real_extract(path)

    monkeypatch.setattr(gm, "extract_generation", slow)
    with TestClient(create_app()) as client:
        job_id = submit(client, key="slow-1").json()["job_id"]
        time.sleep(0.3)
        running = read_job(client, job_id).json()["job"]
        assert running["status"] == "running", running
        assert running["result"] is None or running["result"] == {}
        finished = wait_job(client, job_id)
        assert finished["status"] == "succeeded"
        assert indexed_asset_by_name("slow.png")["metadata"]["generation"]["tool"] == "a1111"


def test_cancel_during_slow_scan_writes_no_generation(ingest_runtime, monkeypatch):
    """异步-取消（20 秒档）：取消后不得写入半截 generation 字段。"""
    write_file(ingest_runtime, "cancel.png", png_bytes([(b"tEXt", b"parameters", A1111_TEXT)]))
    real_extract = gm.extract_generation

    def very_slow(path):
        time.sleep(20)
        return real_extract(path)

    monkeypatch.setattr(gm, "extract_generation", very_slow)
    with TestClient(create_app()) as client:
        job_id = submit(client, key="cancel-1").json()["job_id"]
        time.sleep(0.3)
        version = read_job(client, job_id).json()["job"]["version"]
        cancelled = client.post(
            BASE + f"/workspace-jobs/{job_id}/cancel",
            headers=AUTH,
            json={"expected_version": version},
        )
        assert cancelled.status_code == 200, cancelled.text[:300]
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            job = read_job(client, job_id).json()["job"]
            if job["status"] in jobs.TERMINAL:
                break
            time.sleep(0.05)
        assert job["status"] == "cancelled", job
        raw = repo.state().read()
        assert all(
            "generation" not in (asset.get("metadata") or {})
            for asset in raw["assets"].values()
        ), "取消后不得留下半截 generation"


def test_reindex_keeps_gw_generation_when_file_has_none(ingest_runtime):
    """合并语义：文件无内嵌元数据时，GW 登记写入的 generation 不被重扫清掉。"""
    from gw.god_workflow import asset_hub_bridge, registry, routes

    target = write_file(ingest_runtime, "gw-plain.png", plain_png())
    context = routes._auth(AUTH["Authorization"], "editor", True)
    generation = routes._task_generation({"prompt_snapshot": COMFY_API_GRAPH})
    asset = registry.store_bytes(
        context, target.read_bytes(), "gw-plain.png", "image/png", generation=generation
    )
    asset_hub_bridge.register_output_to_asset_hub(asset, context=context, generation=generation)

    root = ingest_runtime
    root_id = storage.root_identity_id(root)
    relpath = storage.canonical_relpath("gw-plain.png")

    def anchor(raw):
        record = raw["assets"][asset["asset_id"]]
        record.update(
            {
                "root_id": root_id,
                "storage_root_id": root_id,
                "display_path": "gw-plain.png",
                "canonical_relpath": relpath,
            }
        )

    repo.state().mutate(anchor)
    with TestClient(create_app()) as client:
        job = wait_job(client, submit(client, key="keep-gw").json()["job_id"])
        assert job["status"] == "succeeded", job
        record = repo.state().read()["assets"][asset["asset_id"]]
        assert record["metadata"]["generation"]["source"] == "gw-task"
        assert record["metadata"]["generation"]["prompt"] == "comfy api positive"


def test_cold_read_after_restart_keeps_generation(ingest_runtime):
    """持久化-冷读：重启后从磁盘读到的字段与上次成功提交一致。"""
    write_file(ingest_runtime, "persist.png", png_bytes([(b"tEXt", b"parameters", A1111_TEXT)]))
    with TestClient(create_app()) as client:
        wait_job(client, submit(client, key="cold-1").json()["job_id"])
    with TestClient(create_app()) as client:
        assets = client.get(BASE + "/assets", headers=AUTH, params={"limit": "20"}).json()
        entry = next(item for item in assets["items"] if item["name"] == "persist.png")
        assert entry["metadata"]["generation"]["prompt"] == "a misty harbour at dawn, cinematic"


def test_stale_attempt_cannot_commit(ingest_runtime):
    """风险项-乱序：attempt 递增或已终态后，旧 attempt 不得再进入提交。

    `_run` 在扫描前后与提交前都过 `_checkpoint`；这里直接对该闸门取证：
    旧 attempt 必须抛 `_Stopped`，且不得产生任何写入（revision 不变）。
    """
    write_file(ingest_runtime, "stale.png", png_bytes([(b"tEXt", b"parameters", A1111_TEXT)]))
    with TestClient(create_app()) as client:
        job = wait_job(client, submit(client, key="stale-1").json()["job_id"])
        assert job["status"] == "succeeded", job
        instance = jobs.service()
        revision = repo.state().read()["revision"]

        # 新尝试已接管（attempt+1）：旧 attempt 在提交前被拦。
        with pytest.raises(jobs._Stopped):
            instance._checkpoint(job["job_id"], job["attempt"] + 1)
        # 任务已终态：同一 attempt 也不能再提交一次。
        with pytest.raises(jobs._Stopped):
            instance._checkpoint(job["job_id"], job["attempt"])
        assert repo.state().read()["revision"] == revision


def test_generation_written_during_scan_is_not_cleared(ingest_runtime, monkeypatch):
    """风险项-并发：扫描期间另一处写同一 asset 的 metadata，提交只替换 generation。

    等价于「reindex 与 GW 产物登记交错写同一 asset」：写入点放在真实扫描线程内触发，
    保证先后顺序固定（并发写 → 提交）。断言两半语义同时成立：
    文件自带元数据时该键被扫描值替换，而并发写入的其他 metadata 键不被清掉。
    """
    write_file(
        ingest_runtime, "race.png", png_bytes([(b"tEXt", b"parameters", A1111_TEXT)])
    )
    with TestClient(create_app()) as client:
        wait_job(client, submit(client, key="race-1").json()["job_id"])
        asset = indexed_asset_by_name("race.png")
        assert asset["metadata"]["generation"]["tool"] == "a1111"

        concurrent = {
            "prompt": "concurrent writer",
            "tool": "gw",
            "source": "gw-task",
            "raw_keys": ["prompt_snapshot"],
            "truncated": False,
        }
        real_extract = gm.extract_generation
        written = []

        def write_while_scanning(path):
            if not written:

                def inject(raw):
                    record = raw["assets"][asset["asset_id"]]
                    record["metadata"]["generation"] = dict(concurrent)
                    record["metadata"]["workflow_id"] = "wf-concurrent"

                repo.state().mutate(inject)
                written.append(path.name)
            return real_extract(path)

        monkeypatch.setattr(gm, "extract_generation", write_while_scanning)
        job = wait_job(client, submit(client, key="race-2").json()["job_id"])
        assert written == ["race.png"], written
        assert job["status"] == "succeeded", job
        assert job["result"]["updated"] == 1

        metadata = indexed_asset_by_name("race.png")["metadata"]
        # 有内嵌元数据 → `generation` 这一个键被扫描值替换。
        assert metadata["generation"]["tool"] == "a1111"
        assert metadata["generation"]["prompt"] == "a misty harbour at dawn, cinematic"
        # 并发写入的其他键不得被清掉（合并不覆盖）。
        assert metadata["workflow_id"] == "wf-concurrent"
