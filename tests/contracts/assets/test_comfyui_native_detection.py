# -*- coding: utf-8 -*-
"""QIN-601 ComfyUI 通用识别：AC1-AC8 与票面风险项。

样本全部由本文件用 `zlib` + 手工构造字节生成，不引用真实素材库，也不构造
JPEG 样本（本票只覆盖 PNG 内嵌文本块与 WebP EXIF，见设计表 §2.3）。
行为断言来自真实执行：真实 PNG/WebP 字节、真实索引任务、真实注册表落盘。
"""

from __future__ import annotations

import json
import struct
import threading
import time
import uuid
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import generation_metadata as gm
from gw.asset_registry import index_jobs as jobs
from gw.asset_registry import repository as repo
from gw.core import storage
from gw.god_workflow.parser import detect_comfyui_graph, extract_source_graph, parse_workflow

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer qin601-owner", "X-User-Role": "editor"}
MARKER_KEYS = {"has_workflow", "has_prompt_graph", "source"}


# --------------------------------------------------------------------------- #
# 合成样本（AC8）
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
        else:  # iTXt，未压缩
            body = keyword + b"\x00\x00\x00" + b"\x00" + b"\x00" + text
        parts.append(_chunk(kind, body))
    parts.append(_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")))
    parts.append(_chunk(b"IEND", b""))
    return b"".join(parts)


def plain_png() -> bytes:
    return png_bytes([])


def webp_bytes(exif: bytes = b"") -> bytes:
    chunk = b""
    if exif:
        chunk = b"EXIF" + struct.pack("<I", len(exif)) + exif
        if len(exif) % 2:
            chunk += b"\x00"
    body = b"WEBP" + chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


COMFY_API = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"positive": ["6", 0], "negative": ["7", 0], "seed": 1},
    },
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "comfy api positive"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "comfy api negative"}},
}

#: 典型 UI 导出图：采样器 + 文本编码节点，能与 QIN-600 的提示词提取共存。
COMFY_UI_WITH_TEXT = {
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

#: 纯 img2img 形态：有合法 UI 图，但没有任何文本编码节点 → 取不到正向提示词。
COMFY_UI_NO_TEXT = {
    "nodes": [
        {"id": 1, "type": "LoadImage", "widgets_values": ["input.png"]},
        {"id": 2, "type": "VAEEncode", "widgets_values": []},
    ],
    "links": [],
}

A1111_TEXT = (
    b"a misty harbour at dawn, cinematic\n"
    b"Negative prompt: blurry, watermark\n"
    b"Steps: 32, Sampler: DPM++ 2M, CFG scale: 7, Seed: 7, Size: 1024x1024"
)


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def comfyui_runtime(tmp_path, monkeypatch):
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


def submit(client, key="qin601", kind="reindex"):
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
    for asset in repo.state().read()["assets"].values():
        if asset.get("name") == name:
            return asset
    raise AssertionError(f"未找到素材 {name}")


def marker_of(asset: dict) -> dict:
    return (asset.get("metadata") or {})["generation"]["comfyui"]


# --------------------------------------------------------------------------- #
# AC1：标记结构
# --------------------------------------------------------------------------- #
def test_png_workflow_only_writes_embedded_marker(tmp_path):
    """AC1：仅 `workflow` 块的 ComfyUI PNG → 工作流标记，`tool` 仍是 comfyui。"""
    target = write_file(
        tmp_path,
        "ac1.png",
        png_bytes([(b"tEXt", b"workflow", json.dumps(COMFY_UI_WITH_TEXT).encode())]),
    )
    generation, warnings = gm.extract_generation(target)
    assert warnings == 0
    assert generation["comfyui"] == {
        "has_workflow": True,
        "has_prompt_graph": False,
        "source": "embedded",
    }
    # QIN-600 字段语义不变（Q1）。
    assert generation["tool"] == "comfyui"
    assert generation["source"] == "workflow"
    assert generation["raw_keys"] == ["workflow"]


def test_marker_carries_no_text(tmp_path):
    """AC1：标记只含三个判定字段，不复制提示词文本。"""
    target = write_file(
        tmp_path,
        "ac1-shape.png",
        png_bytes([(b"tEXt", b"prompt", json.dumps(COMFY_API).encode())]),
    )
    generation, _ = gm.extract_generation(target)
    assert set(generation["comfyui"]) == MARKER_KEYS
    assert all(
        isinstance(value, bool) or value in {"embedded", "gw-workflow"}
        for value in generation["comfyui"].values()
    )


# --------------------------------------------------------------------------- #
# AC2：原生导出 PNG 三态 + 伪造同名键
# --------------------------------------------------------------------------- #
def test_png_prompt_only_marks_api_graph(tmp_path):
    """AC2：仅 `prompt` 块（API 图）→ `has_prompt_graph`。"""
    target = write_file(
        tmp_path,
        "ac2-prompt.png",
        png_bytes([(b"tEXt", b"prompt", json.dumps(COMFY_API).encode())]),
    )
    generation, _ = gm.extract_generation(target)
    assert generation["comfyui"] == {
        "has_workflow": False,
        "has_prompt_graph": True,
        "source": "embedded",
    }


def test_png_workflow_and_prompt_marks_both_flags(tmp_path):
    """AC2：两个块都有 → 两个 flag 都为 true，且只写一条 generation。"""
    target = write_file(
        tmp_path,
        "ac2-both.png",
        png_bytes(
            [
                (b"tEXt", b"prompt", json.dumps(COMFY_API).encode()),
                (b"tEXt", b"workflow", json.dumps(COMFY_UI_NO_TEXT).encode()),
            ]
        ),
    )
    generation, warnings = gm.extract_generation(target)
    assert warnings == 0
    assert generation["comfyui"] == {
        "has_workflow": True,
        "has_prompt_graph": True,
        "source": "embedded",
    }
    assert generation["raw_keys"] == ["prompt", "workflow"]


@pytest.mark.parametrize(
    "payload",
    [
        b"{oops",  # 非法 JSON
        b'{"hello": 1}',  # 合法 JSON 但不是图
        b'{"nodes": "abc"}',  # 同名键但结构不对
        b'{"error": {"message": "boom"}}',  # 错误信封
    ],
)
def test_forged_block_is_not_marked(tmp_path, payload):
    """AC2：伪造同名键不标记，并按 §2.5 计一次 warning。"""
    target = write_file(
        tmp_path, "ac2-forged.png", png_bytes([(b"tEXt", b"workflow", payload)])
    )
    generation, warnings = gm.extract_generation(target)
    assert generation is None
    assert warnings == 1


def test_comfyui_graph_without_prompt_writes_marker_only(tmp_path):
    """AC2 + Q2：有合法图但取不到正向提示词 → 只写标记、不写 prompt、不计 warning。"""
    target = write_file(
        tmp_path,
        "ac2-marker-only.png",
        png_bytes([(b"tEXt", b"workflow", json.dumps(COMFY_UI_NO_TEXT).encode())]),
    )
    generation, warnings = gm.extract_generation(target)
    assert warnings == 0
    assert generation == {
        "comfyui": {
            "has_workflow": True,
            "has_prompt_graph": False,
            "source": "embedded",
        }
    }


def test_plain_image_has_no_marker(tmp_path):
    """AC4 反面：普通图片不写任何 generation 字段。"""
    target = write_file(tmp_path, "plain.png", plain_png())
    assert gm.extract_generation(target) == (None, 0)


# --------------------------------------------------------------------------- #
# AC3：WebP EXIF
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", ["prompt", "workflow"])
def test_webp_exif_blocks_are_marked(tmp_path, key):
    """AC3：WebP `EXIF` 里的 `prompt:{json}` / `workflow:{json}` 同口径识别。"""
    graph = COMFY_API if key == "prompt" else COMFY_UI_NO_TEXT
    target = write_file(
        tmp_path,
        f"ac3-{key}.webp",
        webp_bytes(f"{key}:{json.dumps(graph)}".encode()),
    )
    generation, _ = gm.extract_generation(target)
    assert generation["comfyui"]["source"] == "embedded"
    assert generation["comfyui"]["has_prompt_graph"] is (key == "prompt")
    assert generation["comfyui"]["has_workflow"] is (key == "workflow")


# --------------------------------------------------------------------------- #
# AC4：GW 两类来源
# --------------------------------------------------------------------------- #
def _gw_context():
    from gw.god_workflow import routes

    return routes._auth(AUTH["Authorization"], "editor", True)


def _store_output_with_task(context, payload: bytes, name: str, *, snapshot):
    """建一个真实 GW 任务，把产物按 W3 路径登记，返回资产字典。"""
    from gw.god_workflow import registry, routes

    task = registry.create_task(
        context, workflow_id="wf-qin601", prompt_id="prompt-qin601", target="local"
    )
    generation = routes._task_generation({"prompt_snapshot": snapshot})
    asset = registry.store_bytes(
        context, payload, name, "image/png", task_id=task["task_id"], generation=generation
    )
    return asset, generation


def test_gw_output_marked_gw_workflow(comfyui_runtime):
    """AC4：GW 产物（有快照）→ `has_prompt_graph=true`、`source=gw-workflow`。"""
    from gw.god_workflow import asset_hub_bridge

    context = _gw_context()
    asset, generation = _store_output_with_task(
        context, plain_png(), "gw-out.png", snapshot=COMFY_API
    )
    result = asset_hub_bridge.register_output_to_asset_hub(
        asset, context=context, generation=generation
    )
    assert not result.get("error"), result

    record = repo.state().read()["assets"][asset["asset_id"]]
    assert marker_of(record) == {
        "has_workflow": False,
        "has_prompt_graph": True,
        "source": "gw-workflow",
    }
    # `workflow_id` 原样保留（前端「编辑来源工作流」链接依赖它）。
    assert record["metadata"]["workflow_id"] == "wf-qin601"


def test_gw_output_without_snapshot_still_marked(comfyui_runtime):
    """AC4 + Q2：无快照的 GW 产物写「仅标记」的 generation。"""
    from gw.god_workflow import asset_hub_bridge

    context = _gw_context()
    asset, _ = _store_output_with_task(
        context, plain_png(), "gw-out-no-snapshot.png", snapshot={}
    )
    assert "generation" not in asset
    result = asset_hub_bridge.register_output_to_asset_hub(asset, context=context)
    assert not result.get("error"), result

    record = repo.state().read()["assets"][asset["asset_id"]]
    assert record["metadata"]["generation"] == {
        "comfyui": {
            "has_workflow": False,
            "has_prompt_graph": False,
            "source": "gw-workflow",
        }
    }


def test_gw_upload_without_workflow_id_gets_no_marker(comfyui_runtime):
    """AC4 边界：没有 `workflow_id` 的普通上传不写标记。"""
    from gw.god_workflow import asset_hub_bridge, registry

    context = _gw_context()
    asset = registry.store_bytes(context, plain_png(), "upload.png", "image/png")
    assert "workflow_id" not in asset
    result = asset_hub_bridge.register_output_to_asset_hub(asset, context=context)
    assert not result.get("error"), result
    record = repo.state().read()["assets"][asset["asset_id"]]
    assert "generation" not in (record.get("metadata") or {})


def test_gw_workflow_asset_is_marked(comfyui_runtime):
    """AC4：GW 工作流资产登记即写标记，`workflow_id` 不变。"""
    from gw.god_workflow import asset_hub_bridge, registry

    context = _gw_context()
    workflow_id = str(uuid.uuid4())
    document = {
        "name": "wf-qin601",
        "nodes": COMFY_UI_NO_TEXT["nodes"],
        "links": [],
        "version": 1,
        "metadata": {"source_context": {}},
    }
    registry._atomic_write(
        registry._principal_root(context) / f"{workflow_id}.json", document
    )

    result = asset_hub_bridge.register_workflow_to_asset_hub(
        workflow_id, "wf-qin601", document=document, context=context
    )
    assert not result.get("error"), result

    asset_id = asset_hub_bridge._workflow_asset_id(context, workflow_id)
    record = repo.state().read()["assets"][asset_id]
    assert marker_of(record) == {
        "has_workflow": True,
        "has_prompt_graph": False,
        "source": "gw-workflow",
    }
    assert record["metadata"]["workflow_id"] == workflow_id


def test_gw_api_document_also_marks_prompt_graph(comfyui_runtime):
    """AC4：API 格式的工作流文档同时具备可执行图。"""
    from gw.god_workflow import asset_hub_bridge, registry

    context = _gw_context()
    workflow_id = str(uuid.uuid4())
    document = {"name": "api-wf", "workflowContent": COMFY_API, "metadata": {"source_context": {}}}
    registry._atomic_write(registry._principal_root(context) / f"{workflow_id}.json", document)

    result = asset_hub_bridge.register_workflow_to_asset_hub(
        workflow_id, "api-wf", document=document, context=context
    )
    assert not result.get("error"), result
    asset_id = asset_hub_bridge._workflow_asset_id(context, workflow_id)
    assert marker_of(repo.state().read()["assets"][asset_id]) == {
        "has_workflow": True,
        "has_prompt_graph": True,
        "source": "gw-workflow",
    }


def test_assets_api_returns_marker_after_commit(comfyui_runtime):
    """异步-写后刷新：提交后 `GET /assets` 立即返回标记。"""
    write_file(
        comfyui_runtime,
        "refresh.png",
        png_bytes([(b"tEXt", b"workflow", json.dumps(COMFY_UI_NO_TEXT).encode())]),
    )
    with TestClient(create_app()) as client:
        job = wait_job(client, submit(client, key="refresh").json()["job_id"])
        assert job["status"] == "succeeded", job
        payload = client.get(BASE + "/assets", headers=AUTH, params={"limit": "20"}).json()
        entry = next(item for item in payload["items"] if item["name"] == "refresh.png")
        assert entry["metadata"]["generation"]["comfyui"]["has_workflow"] is True


def test_job_result_reports_comfyui_tagged(comfyui_runtime):
    """读取方：`job["result"].generation` 增补可复核的 `comfyui_tagged` 计数。"""
    write_file(
        comfyui_runtime,
        "counted.png",
        png_bytes([(b"tEXt", b"prompt", json.dumps(COMFY_API).encode())]),
    )
    write_file(comfyui_runtime, "not-comfy.png", plain_png())
    with TestClient(create_app()) as client:
        job = wait_job(client, submit(client, key="counted").json()["job_id"])
        assert job["result"]["generation"]["scanned_images"] == 2
        assert job["result"]["generation"]["comfyui_tagged"] == 1


# --------------------------------------------------------------------------- #
# AC5：parser 复用（交叉验证，不在热路径调 parse_workflow）
# --------------------------------------------------------------------------- #
def test_detect_matches_parse_workflow_on_real_graphs():
    """AC5：判为 ComfyUI 的样本，`parse_workflow` 必须也能解析。"""
    for graph in (COMFY_API, COMFY_UI_NO_TEXT):
        detected = detect_comfyui_graph(graph)
        assert detected is not None
        document = parse_workflow(graph)
        assert document.nodes


@pytest.mark.parametrize(
    "payload",
    [
        {"hello": 1},
        {"nodes": "abc"},
        {"error": {"message": "boom"}},
        {"nodes": []},
        "not json",
        None,
        {},
    ],
)
def test_detect_rejects_non_graphs(payload):
    """AC5：伪造样本判为 None，且 `extract_source_graph` 也不认它。"""
    assert detect_comfyui_graph(payload) is None
    assert extract_source_graph(payload) is None


# --------------------------------------------------------------------------- #
# AC7：契约文档
# --------------------------------------------------------------------------- #
def test_contract_yaml_documents_marker():
    """AC7：契约 YAML 写明标记结构与 source 取值。"""
    root = Path(__file__).resolve().parents[3]
    text = (root / "docs/contracts/ASSET-REGISTRY-INTERFACE-CATALOG.yaml").read_text(
        encoding="utf-8"
    )
    assert "comfyui" in text
    assert "has_workflow" in text and "has_prompt_graph" in text
    assert "gw-workflow" in text and "embedded" in text


# --------------------------------------------------------------------------- #
# 风险项：失败隔离、慢、取消、乱序、并发、退出恢复、合并语义
# --------------------------------------------------------------------------- #
def test_broken_comfyui_block_does_not_fail_job(comfyui_runtime):
    """异步-失败：坏块只计 warning，其余文件照常标记、整单成功。"""
    write_file(
        comfyui_runtime,
        "bad-block.png",
        png_bytes([(b"tEXt", b"workflow", b"{oops")]),
    )
    write_file(
        comfyui_runtime,
        "good-block.png",
        png_bytes([(b"tEXt", b"workflow", json.dumps(COMFY_UI_NO_TEXT).encode())]),
    )
    with TestClient(create_app()) as client:
        job = wait_job(client, submit(client, key="bad-block").json()["job_id"])
        assert job["status"] == "succeeded", job
        stats = job["result"]["generation"]
        assert stats["warnings"] == 1
        assert stats["comfyui_tagged"] == 1
    assert marker_of(indexed_asset_by_name("good-block.png"))["has_workflow"] is True


def test_slow_marker_detection_does_not_finish_early(comfyui_runtime, monkeypatch):
    """异步-慢（3 秒档）：判定挂住时任务不得提前标 succeeded。"""
    import gw.god_workflow.parser as parser

    write_file(
        comfyui_runtime,
        "slow.png",
        png_bytes([(b"tEXt", b"workflow", json.dumps(COMFY_UI_NO_TEXT).encode())]),
    )
    real_detect = parser.detect_comfyui_graph

    def slow_detect(payload):
        time.sleep(3)
        return real_detect(payload)

    monkeypatch.setattr(parser, "detect_comfyui_graph", slow_detect)
    with TestClient(create_app()) as client:
        job_id = submit(client, key="slow").json()["job_id"]
        time.sleep(0.5)
        assert read_job(client, job_id).json()["job"]["status"] not in jobs.TERMINAL
        job = wait_job(client, job_id)
        assert job["status"] == "succeeded", job
    assert marker_of(indexed_asset_by_name("slow.png"))["has_workflow"] is True


def test_cancel_during_slow_detection_writes_no_marker(comfyui_runtime, monkeypatch):
    """异步-取消：取消后不得写入半截标记。"""
    import gw.god_workflow.parser as parser

    write_file(
        comfyui_runtime,
        "cancel.png",
        png_bytes([(b"tEXt", b"workflow", json.dumps(COMFY_UI_NO_TEXT).encode())]),
    )
    real_detect = parser.detect_comfyui_graph

    def slow_detect(payload):
        time.sleep(20)
        return real_detect(payload)

    monkeypatch.setattr(parser, "detect_comfyui_graph", slow_detect)
    with TestClient(create_app()) as client:
        job_id = submit(client, key="cancel").json()["job_id"]
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
        ), "取消后不得留下半截标记"


def test_stale_attempt_cannot_commit(comfyui_runtime, monkeypatch):
    """异步-乱序：任务仍在 running 时新尝试接管，旧 attempt 不得提交。"""
    write_file(
        comfyui_runtime,
        "stale.png",
        png_bytes([(b"tEXt", b"workflow", json.dumps(COMFY_UI_NO_TEXT).encode())]),
    )
    entered = threading.Event()
    release = threading.Event()
    real_extract = gm.extract_generation

    def gated(path):
        entered.set()
        release.wait(30)
        return real_extract(path)

    committed = []
    monkeypatch.setattr(gm, "extract_generation", gated)
    try:
        with TestClient(create_app()) as client:
            job_id = submit(client, key="stale").json()["job_id"]
            assert entered.wait(20), "扫描线程未进入闸门"
            instance = jobs.service()
            real_commit = instance._commit

            def spy_commit(job, files, generation_stats=None):
                committed.append((job["job_id"], job["attempt"]))
                return real_commit(job, files, generation_stats)

            monkeypatch.setattr(instance, "_commit", spy_commit)
            revision = repo.state().read()["revision"]

            def takeover(raw):
                raw["jobs"][job_id]["attempt"] += 1
                raw["jobs"][job_id]["version"] += 1

            instance.store.mutate(takeover)
            live = instance.store.read()["jobs"][job_id]
            assert live["status"] == "running", live

            stale_blocked = False
            try:
                instance._checkpoint(job_id, 1)
            except jobs._Stopped:
                stale_blocked = True
            finally:
                release.set()
            assert stale_blocked, "旧 attempt 在任务仍 running 时未被拦下（可提交）"
            assert instance._checkpoint(job_id, 2)["attempt"] == 2
    finally:
        release.set()

    assert committed == [], committed
    raw = repo.state().read()
    assert raw["revision"] == revision
    assert not raw.get("assets"), "旧 attempt 不得提交任何资产"


def test_reindex_keeps_gw_workflow_source(comfyui_runtime):
    """进程-并发/合并语义（Q4）：GW 产物自带 ComfyUI 元数据，重扫不翻回 embedded。"""
    from gw.god_workflow import asset_hub_bridge, registry, routes

    payload = png_bytes([(b"tEXt", b"workflow", json.dumps(COMFY_UI_NO_TEXT).encode())])
    target = write_file(comfyui_runtime, "gw-embedded.png", payload)
    context = _gw_context()
    task = registry.create_task(
        context, workflow_id="wf-merge", prompt_id="prompt-merge", target="local"
    )
    generation = routes._task_generation({"prompt_snapshot": COMFY_API})
    asset = registry.store_bytes(
        context, payload, "gw-embedded.png", "image/png", task_id=task["task_id"], generation=generation
    )
    asset_hub_bridge.register_output_to_asset_hub(asset, context=context, generation=generation)

    root_id = storage.root_identity_id(target.parent)
    relpath = storage.canonical_relpath("gw-embedded.png")

    def anchor(raw):
        record = raw["assets"][asset["asset_id"]]
        record.update(
            {
                "root_id": root_id,
                "storage_root_id": root_id,
                "display_path": "gw-embedded.png",
                "canonical_relpath": relpath,
            }
        )

    repo.state().mutate(anchor)
    with TestClient(create_app()) as client:
        job = wait_job(client, submit(client, key="merge").json()["job_id"])
        assert job["status"] == "succeeded", job
    record = repo.state().read()["assets"][asset["asset_id"]]
    marker = marker_of(record)
    assert marker["source"] == "gw-workflow"
    assert marker["has_workflow"] is True  # 扫描结果取或
    assert marker["has_prompt_graph"] is True  # GW 侧保留
    # 扫描没给出 prompt 时保留 GW 写入的提示词。
    assert record["metadata"]["generation"]["prompt"] == "comfy api positive"


def test_reindex_replays_without_changes(comfyui_runtime):
    """持久化-可重放：连跑两次，第二次不产生变更、revision 不变。"""
    write_file(
        comfyui_runtime,
        "replay.png",
        png_bytes([(b"tEXt", b"workflow", json.dumps(COMFY_UI_NO_TEXT).encode())]),
    )
    with TestClient(create_app()) as client:
        wait_job(client, submit(client, key="replay-1").json()["job_id"])
        revision = repo.state().read()["revision"]
        second = wait_job(client, submit(client, key="replay-2").json()["job_id"])
        assert second["result"]["indexed"] == 0
        assert second["result"]["updated"] == 0
        assert second["result"]["revision"] == revision


def test_cold_read_after_restart_keeps_marker(comfyui_runtime):
    """持久化-冷读：重启后标记与上次提交一致。"""
    write_file(
        comfyui_runtime,
        "cold.png",
        png_bytes([(b"tEXt", b"prompt", json.dumps(COMFY_API).encode())]),
    )
    with TestClient(create_app()) as client:
        wait_job(client, submit(client, key="cold-1").json()["job_id"])
    with TestClient(create_app()) as client:
        payload = client.get(BASE + "/assets", headers=AUTH, params={"limit": "20"}).json()
        entry = next(item for item in payload["items"] if item["name"] == "cold.png")
        assert entry["metadata"]["generation"]["comfyui"]["has_prompt_graph"] is True
