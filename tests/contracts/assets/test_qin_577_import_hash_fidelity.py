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

"""QIN-577：导入素材保真 `sha256` 与 `hash_mode`，往返不再静默丢哈希。

票面 AC：
- AC1：导入接受并持久化 `sha256`（64 位十六进制，大写归一为小写）与
  `hash_mode`（full / sampled）；只给合法 `sha256` 时按 `full` 落库。
- AC2：非法组合明确 400 `INVALID_REQUEST`（`sha256` 非 64 位十六进制、
  `hash_mode` 非枚举值、`hash_mode` 脱离 `sha256` 单独出现）。
- AC3：往返保真——详情读回记录回灌后 `sha256` / `hash_mode` 逐项一致，
  `shot_source_key` 不变，sampled 记录仍在查重组中按采样哈希参与分组。
- AC4：本契约测试覆盖 AC1–AC3，并钉住导入侧哈希模式词表与
  `index_jobs.HASH_MODE_*` 不漂移。
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import governance
from gw.asset_registry import index_jobs as jobs
from gw.asset_registry import repository as repo

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer qin577-owner", "X-User-Role": "editor"}

PAYLOAD = b"qin-577 import fidelity payload"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture
def api_client():
    """每个用例独立应用实例：导入写真实 JSON 状态，不能跨用例复用。"""
    with TestClient(create_app()) as client:
        yield client


def _import(client, items):
    return client.post(BASE + "/assets/import", headers=AUTH, json={"items": items})


def _detail(client, asset_id):
    response = client.get(BASE + "/assets/" + asset_id, headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()["asset"]


def _records():
    return list(repo.state().read()["assets"].values())


def test_ac1_import_persists_sha256_and_full_mode(api_client):
    """AC1：只给合法 sha256 时按 full 落库，大写输入归一为小写。"""
    created = _import(
        api_client,
        [
            {
                "name": "clip-full.mov",
                "kind": "video",
                "size_bytes": len(PAYLOAD),
                "sha256": DIGEST.upper(),
            }
        ],
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["asset_ids"][0]

    detail = _detail(api_client, asset_id)
    assert detail["sha256"] == DIGEST
    assert detail["hash_mode"] == jobs.HASH_MODE_FULL
    # 哈希身份直接进入镜头来源指纹，不再回落到 asset:<id>。
    assert repo.shot_source_key(detail) == "sha256:" + DIGEST


def test_ac1_import_persists_sampled_marker(api_client):
    """AC1：sampled 标记与采样摘要一同落盘，不被降级成 full。"""
    sampled = hashlib.sha256(b"gw-asset-registry-sampled-payload").hexdigest()

    created = _import(
        api_client,
        [
            {
                "name": "clip-oversize.bin",
                "kind": "video",
                "size_bytes": 1024,
                "sha256": sampled,
                "hash_mode": jobs.HASH_MODE_SAMPLED,
            }
        ],
    )
    assert created.status_code == 200, created.text
    detail = _detail(api_client, created.json()["asset_ids"][0])

    assert detail["sha256"] == sampled
    assert detail["hash_mode"] == jobs.HASH_MODE_SAMPLED


@pytest.mark.parametrize(
    "entry, reason",
    [
        ({"sha256": "abc123"}, "sha256 长度不足"),
        ({"sha256": "g" * 64}, "sha256 含非十六进制字符"),
        ({"sha256": DIGEST, "hash_mode": "partial"}, "hash_mode 非枚举值"),
        ({"hash_mode": "sampled"}, "hash_mode 脱离 sha256 单独出现"),
        ({"hash_mode": "full"}, "hash_mode 无哈希时同样拒绝"),
    ],
)
def test_ac2_invalid_hash_combinations_rejected(api_client, entry, reason):
    """AC2：非法组合明确 400，不得静默忽略或降级接受。"""
    response = _import(api_client, [{"name": "bad.mov", "kind": "video", **entry}])

    assert response.status_code == 400, f"{reason}：{response.text}"
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"
    # 拒绝发生在入账前：不得留下半成品记录。
    assert _records() == []


def test_ac2_blank_hash_fields_keep_legacy_behavior(api_client):
    """AC2 边界：空串视为「未提供」，保持导入不落哈希的既有行为。"""
    created = _import(
        api_client,
        [{"name": "no-hash.mov", "kind": "video", "sha256": "", "hash_mode": ""}],
    )
    assert created.status_code == 200, created.text
    detail = _detail(api_client, created.json()["asset_ids"][0])

    assert "sha256" not in detail
    assert "hash_mode" not in detail
    assert repo.shot_source_key(detail) == "asset:" + detail["asset_id"]


def test_ac3_round_trip_keeps_hash_identity(api_client):
    """AC3：详情回灌后字段逐项一致，指纹不变，sampled 仍参与查重分组。"""
    first = _import(
        api_client,
        [
            {
                "name": "round-trip.bin",
                "kind": "video",
                "size_bytes": 2048,
                "sha256": DIGEST,
                "hash_mode": jobs.HASH_MODE_SAMPLED,
            }
        ],
    )
    assert first.status_code == 200, first.text
    original = _detail(api_client, first.json()["asset_ids"][0])

    replay = {
        "name": original["name"],
        "kind": original["kind"],
        "size_bytes": original["size_bytes"],
        "sha256": original["sha256"],
        "hash_mode": original["hash_mode"],
    }
    second = _import(api_client, [replay])
    assert second.status_code == 200, second.text
    restored = _detail(api_client, second.json()["asset_ids"][0])

    assert restored["sha256"] == original["sha256"] == DIGEST
    assert restored["hash_mode"] == original["hash_mode"] == jobs.HASH_MODE_SAMPLED
    assert repo.shot_source_key(restored) == repo.shot_source_key(original)

    # sampled 记录仍带 sha256，继续进入文件查重组（QIN-564 AC3 语义不变）。
    digests = [record[2] for record in governance._asset_file_records(_records())]
    assert digests.count(DIGEST) == 2


def test_ac4_hash_mode_vocabulary_matches_index_jobs():
    """AC4：导入侧词表与索引侧常量同值，防止两处漂移。"""
    assert repo._IMPORT_HASH_MODES == frozenset(
        {jobs.HASH_MODE_FULL, jobs.HASH_MODE_SAMPLED}
    )
