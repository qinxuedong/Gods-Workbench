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

"""QIN-604 资产注册表只读快照：解析收敛、只读约束、写后读一致与并发语义。

行为证据全部来自真实执行（TestClient / 真实 os.replace / 真实 JSON 落盘），
不使用源码文本匹配作为通过依据。
"""

from __future__ import annotations

import copy
import io
import json
import os
import pickle
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gw.api.app import create_app
from gw.asset_registry import repository as repo
from gw.asset_registry.hybrid_search import normalize_shot_records
from gw.core import storage
from gw.media import repository as media_repo

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
PREVIEW_ID = "qin604_preview_probe"


def _png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _asset(asset_id: str, display_path: str, *, shots: bool = False) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if shots:
        metadata["video_segments"] = [
            {"start_sec": 0.0, "end_sec": 3.5, "tags": ["wide"], "summary": "开场"}
        ]
    return {
        "asset_id": asset_id,
        "id": asset_id,
        "name": f"{asset_id}.png",
        "kind": "image",
        "display_path": display_path,
        "hidden": False,
        "archived": False,
        "version": 1,
        "metadata": metadata,
        "tags": ["qin604"],
        "relations": [],
        "url": "",
        "created_at": "2026-11-12T00:00:00Z",
        "updated_at": "2026-11-12T00:00:00Z",
    }


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """隔离数据根 / 允许根 / 认证模式；每个用例拥有全新注册表文件。"""
    data_root = tmp_path / "gw-data"
    roots = tmp_path / "roots"
    data_root.mkdir()
    roots.mkdir()
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(data_root))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "gw-auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "gw-video"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))
    monkeypatch.setenv("GW_DISABLE_AGENT_INTEGRATION", "1")
    source = roots / "source.png"
    source.write_bytes(_png(320, 180, (12, 34, 56)))
    media_repo.patch_thumbnail_settings(
        {
            "mode": "source",
            "directory": "",
            "cache_folder": "god-cache",
            "width": 256,
            "format": "webp",
        }
    )
    media_repo.reset_preview_cache_stats()
    repo._REGISTRY_SNAPSHOT_CACHE = None
    repo._SNAPSHOT_PATHS_CACHE = None
    return {"data": data_root, "roots": roots, "source": source}


def _write_registry(runtime: dict[str, Path], *, count: int = 3) -> dict[str, Any]:
    raw = repo._blank()
    raw["assets"] = {
        f"asset_{index:04d}": _asset(
            f"asset_{index:04d}", str(runtime["source"]), shots=index == 0
        )
        for index in range(count)
    }
    raw["assets"][PREVIEW_ID] = _asset(PREVIEW_ID, str(runtime["source"]))
    raw["revision"] = 7
    storage.save(repo.NS_REGISTRY, raw)
    repo._REGISTRY_SNAPSHOT_CACHE = None
    repo._SNAPSHOT_PATHS_CACHE = None
    return raw


def _count_registry_loads(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """AC1 计数口径：只统计 asset_registry 命名空间的 storage.load 调用。"""
    counter = {"calls": 0}
    original = storage.load

    def counting_load(namespace: str, default: Any = None) -> Any:
        if namespace == repo.NS_REGISTRY:
            counter["calls"] += 1
        return original(namespace, default)

    monkeypatch.setattr(storage, "load", counting_load)
    return counter


# ---------------------------------------------------------------------------
# AC1：同一文件版本内连续只读，storage.load 只调用 1 次
# ---------------------------------------------------------------------------


def test_ac1_unchanged_registry_parsed_once_across_all_read_paths(
    runtime: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_registry(runtime)
    counter = _count_registry_loads(monkeypatch)

    with TestClient(create_app()) as client:
        for _ in range(4):
            assert client.get("/api/asset-registry/status", headers=AUTH).status_code == 200
            assert client.get("/api/asset-registry/folders", headers=AUTH).status_code == 200
            assert client.get("/api/asset-registry/assets", headers=AUTH, params={"limit": "60"}).status_code == 200
            assert client.get(
                "/api/media-preview", headers=AUTH, params={"asset_id": PREVIEW_ID, "w": "256"}
            ).status_code == 200

    # 结构化列表、批量解析、预热范围、features 都走同一入口。
    structured = repo._list_structured_assets(
        limit=10, offset=0, cursor=None, scope=None, text=None,
        filters=None, sort=None, include=None,
    )
    assert isinstance(structured["items"], list)
    assert PREVIEW_ID in repo.media_files([PREVIEW_ID, "missing"])
    assert PREVIEW_ID in repo.preview_asset_paths()
    assert repo.features_snapshot()["revision"] == 7
    assert repo.folders(None) is not None

    assert counter["calls"] == 1, (
        "注册表文件未变时整段读路径应只解析一次；实际 %s 次" % counter["calls"]
    )


# ---------------------------------------------------------------------------
# AC2：写后读一致（真实 os.replace 改签名）
# ---------------------------------------------------------------------------


def test_ac2_write_invalidates_snapshot_and_next_read_sees_new_asset(
    runtime: dict[str, Path],
) -> None:
    _write_registry(runtime)
    before = repo.list_assets(200, 0, None, None, None)
    assert "asset_9999" not in {item["asset_id"] for item in before["items"]}
    assert before["revision"] == 7

    repo.state().mutate(
        lambda raw: (
            raw["assets"].update(
                {"asset_9999": _asset("asset_9999", str(runtime["source"]))}
            ),
            raw.__setitem__("revision", raw["revision"] + 1),
        )
    )
    after = repo.list_assets(200, 0, None, None, None)
    assert "asset_9999" in {item["asset_id"] for item in after["items"]}
    assert after["revision"] == 8
    with TestClient(create_app()) as client:
        status = client.get("/api/asset-registry/status", headers=AUTH)
        assert status.status_code == 200
        assert status.json()["revision"] == 8


# ---------------------------------------------------------------------------
# AC3：并发读写 + 签名读前/读后不一致不写缓存
# ---------------------------------------------------------------------------


def test_ac3_slow_concurrent_write_does_not_block_reads_or_expose_half_write(
    runtime: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_registry(runtime)
    assert "asset_9999" not in {
        item["asset_id"] for item in repo.list_assets(200, 0, None, None, None)["items"]
    }

    original_replace = os.replace
    entered = threading.Event()

    def slow_replace(src, dst):
        entered.set()
        time.sleep(0.25)
        return original_replace(src, dst)

    monkeypatch.setattr(storage.os, "replace", slow_replace)
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            repo.state().mutate(
                lambda raw: raw["assets"].update(
                    {"asset_9999": _asset("asset_9999", str(runtime["source"]))}
                )
            )
        except BaseException as exc:  # pragma: no cover - 断言在主线程统一给出
            errors.append(exc)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    assert entered.wait(2.0), "写入未进入 replace 阶段"
    started = time.perf_counter()
    mid = repo.list_assets(200, 0, None, None, None)
    elapsed = time.perf_counter() - started
    ids = {item["asset_id"] for item in mid["items"]}
    thread.join(timeout=5.0)
    assert not errors, errors
    assert not thread.is_alive()
    assert elapsed < 0.25, "并发写期间读被阻塞到写完：%.3fs" % elapsed
    assert "asset_9999" not in ids, "读到半写/未提交的新数据"
    assert repo.list_assets(200, 0, None, None, None)["total"] == len(ids) + 1


def test_ac3_signature_mismatch_never_pins_a_stale_result(
    runtime: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_registry(runtime)
    repo._REGISTRY_SNAPSHOT_CACHE = None
    sequence = iter(range(100))
    original_signature = repo._registry_snapshot_signature
    # 只恢复这一项：monkeypatch.undo() 会连带撤掉 runtime 夹具设置的环境变量。
    monkeypatch.setattr(
        repo,
        "_registry_snapshot_signature",
        lambda _path: ("signature", next(sequence), 1, 1),
    )
    snapshot = repo.readonly_registry_snapshot()
    assert snapshot["revision"] == 7
    assert repo._REGISTRY_SNAPSHOT_CACHE is None, "签名读前/读后不一致时不得写缓存"

    monkeypatch.setattr(repo, "_registry_snapshot_signature", original_signature)
    repo._REGISTRY_SNAPSHOT_CACHE = None
    assert repo.readonly_registry_snapshot()["revision"] == 7
    assert repo._REGISTRY_SNAPSHOT_CACHE is not None


# ---------------------------------------------------------------------------
# AC4：缓存值只读 + PM 要求的 copy / deepcopy / pickle 下游兼容
# ---------------------------------------------------------------------------


def test_ac4_snapshot_is_type_compatible_but_rejects_in_place_mutation(
    runtime: dict[str, Path],
) -> None:
    _write_registry(runtime)
    snapshot = repo.readonly_registry_snapshot()
    record = snapshot["assets"]["asset_0000"]

    # 守住 QIN-565 的两处 isinstance(x, dict) 判定：不能用 MappingProxyType。
    assert isinstance(snapshot, dict)
    assert isinstance(snapshot["assets"], dict)
    assert isinstance(record["metadata"], dict)
    assert isinstance(record["metadata"]["video_segments"], list)
    assert normalize_shot_records(record["metadata"]["video_segments"]), (
        "冻结视图必须仍能被镜头检索读到 video_segments"
    )
    assert isinstance(snapshot.get("root_aliases", {}), dict)

    with pytest.raises(TypeError):
        record["name"] = "污染"
    with pytest.raises(TypeError):
        record["tags"].append("污染")
    with pytest.raises(TypeError):
        snapshot["assets"].update({"污染": {}})
    with pytest.raises(TypeError):
        snapshot["relations"].append("污染")
    with pytest.raises(TypeError):
        snapshot.clear()

    assert repo.readonly_registry_snapshot()["assets"]["asset_0000"]["name"] == "asset_0000.png"
    assert repo.list_assets(200, 0, None, None, None)["revision"] == 7


def test_ac4_downstream_copy_deepcopy_pickle_yield_plain_mutable_containers(
    runtime: dict[str, Path],
) -> None:
    """PM 补充要求：copy.copy / copy.deepcopy / pickle 重建不得走 __setitem__。"""
    _write_registry(runtime)
    snapshot = repo.readonly_registry_snapshot()
    for name, cloned in (
        ("copy", copy.copy(snapshot)),
        ("deepcopy", copy.deepcopy(snapshot)),
        ("pickle", pickle.loads(pickle.dumps(snapshot))),  # noqa: S301 - 本地夹具产生的对象，不加载外部数据
    ):
        assert type(cloned) is dict, "%s 应返回普通 dict" % name
        assert type(cloned["assets"]) is dict, "%s 嵌套层也应是普通 dict" % name
        assert type(cloned["assets"]["asset_0000"]["tags"]) is list
        cloned["assets"]["asset_0000"]["name"] = "可写副本"
        cloned["assets"]["asset_0000"]["tags"].append("ok")
    assert type(copy.deepcopy(snapshot["assets"]["asset_0000"]["metadata"])) is dict

    # 下游调用方拿到的是 list_assets 的分页副本，其嵌套字段同样是冻结视图；
    # deepcopy / pickle 这些副本也必须成功且得到普通容器。
    items = repo.list_assets(200, 0, None, None, None)["items"]
    cloned_items = copy.deepcopy(items)
    assert type(cloned_items) is list and type(cloned_items[0]) is dict
    cloned_items[0]["metadata"]["downstream"] = True
    assert type(pickle.loads(pickle.dumps(items))) is list  # noqa: S301 - 本地夹具产生的对象
    assert repo.readonly_registry_snapshot()["assets"]["asset_0000"]["name"] == "asset_0000.png"


# ---------------------------------------------------------------------------
# 失败语义：文件不存在 / 损坏都不缓存失败结果
# ---------------------------------------------------------------------------


def test_load_with_status_distinguishes_empty_state_from_read_failure(
    runtime: dict[str, Path],
) -> None:
    target = runtime["data"] / "asset_registry.json"
    assert not target.exists()
    value, ok = storage.load_with_status(repo.NS_REGISTRY, None)
    assert value is None and ok is True, "文件不存在是合法空态"

    _write_registry(runtime)
    value, ok = storage.load_with_status(repo.NS_REGISTRY, None)
    assert isinstance(value, dict) and ok is True

    target.write_bytes(b"{ broken json")
    value, ok = storage.load_with_status(repo.NS_REGISTRY, None)
    assert value is None and ok is False, "文件存在但解析失败必须报 ok=False"


def test_corrupted_or_missing_registry_is_not_cached(
    runtime: dict[str, Path],
) -> None:
    _write_registry(runtime)
    assert repo.readonly_registry_snapshot()["revision"] == 7
    target = runtime["data"] / "asset_registry.json"
    target.write_bytes(b"{ broken json")

    blank = repo.readonly_registry_snapshot()
    assert blank["assets"] == {}, "损坏时应按现有语义返回空表"
    assert repo.readonly_registry_snapshot()["assets"] == {}

    storage.save(repo.NS_REGISTRY, _write_registry(runtime))
    assert repo.readonly_registry_snapshot()["revision"] == 7, "修复后下一次必须读到真实数据"

    target.unlink()
    assert repo.readonly_registry_snapshot()["assets"] == {}
    storage.save(repo.NS_REGISTRY, _write_registry(runtime))
    assert repo.readonly_registry_snapshot()["revision"] == 7


# ---------------------------------------------------------------------------
# AC6：Cache-Control / ETag / 304
# ---------------------------------------------------------------------------


def test_ac6_media_preview_cache_headers_etag_and_304(
    runtime: dict[str, Path],
) -> None:
    _write_registry(runtime)
    params = {"asset_id": PREVIEW_ID, "w": "256"}
    with TestClient(create_app()) as client:
        first = client.get("/api/media-preview", headers=AUTH, params=params)
        assert first.status_code == 200, first.text
        cache_control = first.headers["cache-control"]
        assert "private" in cache_control and "no-cache" in cache_control
        assert "must-revalidate" in cache_control
        etag = first.headers["etag"]
        assert etag.startswith('"') and etag.endswith('"')
        assert first.headers["content-type"].startswith("image/webp")

        before = dict(media_repo.preview_cache_stats())
        not_modified = client.get(
            "/api/media-preview",
            headers={**AUTH, "If-None-Match": etag},
            params=params,
        )
        assert not_modified.status_code == 304
        assert not_modified.content == b""
        assert not_modified.headers["etag"] == etag
        assert "private" in not_modified.headers["cache-control"]
        assert dict(media_repo.preview_cache_stats()) == before, "304 不得触发出图/缓存统计"

        weak = client.get(
            "/api/media-preview",
            headers={**AUTH, "If-None-Match": "W/" + etag},
            params=params,
        )
        assert weak.status_code == 304
        stale = client.get(
            "/api/media-preview",
            headers={**AUTH, "If-None-Match": '"stale-etag"'},
            params=params,
        )
        assert stale.status_code == 200 and stale.content == first.content

        # 源文件被替换（size/mtime_ns 变）→ ETag 变，返回新字节而不是 304 旧图。
        runtime["source"].write_bytes(_png(400, 240, (200, 10, 10)))
        replaced = client.get(
            "/api/media-preview",
            headers={**AUTH, "If-None-Match": etag},
            params=params,
        )
        assert replaced.status_code == 200
        assert replaced.headers["etag"] != etag
        assert replaced.content != first.content

        # 宽度变化也换键。
        wider = client.get(
            "/api/media-preview",
            headers=AUTH,
            params={"asset_id": PREVIEW_ID, "w": "512"},
        )
        assert wider.status_code == 200
        assert wider.headers["etag"] != replaced.headers["etag"]


def test_ac6_etag_changes_when_thumbnail_format_changes(
    runtime: dict[str, Path],
) -> None:
    _write_registry(runtime)
    payload = {"asset_id": PREVIEW_ID, "w": "256"}
    first = media_repo.preview_etag(payload)
    assert first is not None
    media_repo.patch_thumbnail_settings({"format": "jxl"})
    second = media_repo.preview_etag(payload)
    assert second is not None
    assert second["etag"] != first["etag"], "jxl/webp 切换必须换 ETag"
    media_repo.patch_thumbnail_settings({"format": "webp"})
    assert media_repo.preview_etag(payload)["etag"] == first["etag"]
    assert media_repo.preview_etag({"asset_id": "missing_asset"}) is None, (
        "解析失败时不伪造 ETag，交给 preview_bytes 抛既有错误"
    )


# ---------------------------------------------------------------------------
# 预览读路径复用：等价性 / 单次解析 / 失败仍走完整准入校验
# ---------------------------------------------------------------------------


def test_preview_descriptor_reuse_is_equivalent_and_resolves_once(
    runtime: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """路由把 ETag 阶段已解析的派生值交给 preview_bytes，不得改语义。

    同一次请求内复用「已通过允许根准入校验」的路径，省掉第二遍 Windows
    realpath / 注册表回查链；解析失败的分支必须原样重跑完整解析，不能因为
    快路径把越界或缺失素材放行。
    """
    _write_registry(runtime)
    params = {"asset_id": PREVIEW_ID, "w": "256"}

    descriptor = media_repo.preview_etag(params)
    assert descriptor is not None
    assert descriptor["source"] == storage.relative_display(descriptor["path"])
    assert descriptor["identity"] == media_repo._preview_source_identity(
        descriptor["path"]
    ), "ETag 键与缩略图缓存键必须来自同一份源状态"

    plain = media_repo.preview_bytes(dict(params))
    reused = media_repo.preview_bytes(
        {
            **params,
            "_resolved_path": descriptor["path"],
            "_settings": descriptor["settings"],
            "_cache_dir": descriptor["cache_dir"],
            "_source": descriptor["source"],
            "_identity": descriptor["identity"],
        }
    )
    assert reused == plain, "复用派生值不得改变出图字节 / MIME / source"

    original = media_repo._registry_path
    calls: list[int] = []

    def counting(asset_id: str) -> Path:
        calls.append(1)
        return original(asset_id)

    monkeypatch.setattr(media_repo, "_registry_path", counting)
    with TestClient(create_app()) as client:
        warmup = client.get("/api/media-preview", headers=AUTH, params=params)
        assert warmup.status_code == 200
        calls.clear()
        response = client.get("/api/media-preview", headers=AUTH, params=params)
        assert response.status_code == 200 and response.content == warmup.content
        assert len(calls) == 1, "同一次成功请求只应做一次注册表回查 / 允许根解析链"

        # 允许根之外的显式路径仍被拒绝，且不会拿到 ETag / 304 快路径。
        # 注：失败分支会按既有语义重跑一次完整解析，故不计入上面的成功请求计数。
        outside = client.get(
            "/api/media-preview",
            headers=AUTH,
            params={"path": str(runtime["data"] / "asset_registry.json")},
        )
        missing = client.get(
            "/api/media-preview", headers=AUTH, params={"asset_id": "missing_asset"}
        )
    assert outside.status_code != 200 and "etag" not in outside.headers
    assert missing.status_code != 200 and "etag" not in missing.headers
