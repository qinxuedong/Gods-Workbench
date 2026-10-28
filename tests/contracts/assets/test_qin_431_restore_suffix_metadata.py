# -*- coding: utf-8 -*-
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

"""QIN-431 回归：恢复冲突 resolution=suffix 后资产路径元数据未更新。

缺陷（issue #354，基线 origin/master 753b7a5，在 #347 临时目录回归验证中发现）：
回收文件恢复选择 suffix 时，物理文件恢复为带 `` (1)`` 后缀的目标、响应也报告
后缀路径，但资产记录仍保留原 ``display_path`` / ``canonical_relpath``，媒体读取
可能命中原位置的新文件而不是刚恢复的内容。

按票面约定本票**只补测试和 QA 清单，不改业务实现**：
- 通过用例锁定既有正确行为（响应报告后缀目标、原文件不被覆盖）；
- 缺陷用 ``pytest.mark.xfail(strict=False)`` 标注期望行为，修复落地后自动转绿；
- 系统：``gw/asset_registry/lifecycle.py::restore_asset_files`` 调用
  ``repo.restore_from_recycle`` 时未把后缀目标回写登记。

QA 手工核对清单（本地证据目录 ``.local/qa/QIN-431/``）：
1. 允许根登记素材 → 删除入回收站 → 原路径写入同名新文件；
2. POST /recycle-bin/{entry_id}/restore 传 resolution=suffix；
3. 核对响应 display_path 为后缀路径、原文件内容未被覆盖；
4. GET /api/asset-registry/assets/{asset_id} 核对 display_path /
   canonical_relpath 与磁盘后缀文件一致（当前缺陷：仍为原路径）；
5. GET /assets/{asset_id}/media 核对返回恢复内容（当前缺陷：命中替换文件）；
6. 修复后在本清单逐条勾选并记录证据。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core import storage

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
GOVERNOR = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "governor"}
SUFFIX_NAME = "原文件 (1).txt"


@pytest.fixture()
def local_runtime(tmp_path, monkeypatch):
    root = tmp_path / "sources"
    root.mkdir()
    (root / "原文件.txt").write_text("single-file", encoding="utf-8")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "gw-auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "gw-video"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    return root


@pytest.fixture()
def operation_client(local_runtime, monkeypatch):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    with TestClient(create_app()) as client:
        yield client


def _revision(client: TestClient) -> int:
    return client.get(f"{BASE}/status", headers=AUTH).json()["revision"]


def _register_and_recycle(client: TestClient, root, *, content: str = "single-file"):
    """登记素材、删除入回收站并占位同名新文件，返回 (asset_id, entry_id)。"""
    (root / "原文件.txt").write_text(content, encoding="utf-8")
    imported = client.post(
        f"{BASE}/assets/import",
        headers=AUTH,
        json={"items": [{"name": "原文件.txt", "kind": "document", "display_path": "原文件.txt"}]},
    )
    assert imported.status_code == 200, imported.text
    asset_id = imported.json()["asset_ids"][0]
    patched = client.patch(
        f"{BASE}/assets/{asset_id}",
        headers=AUTH,
        json={
            "display_path": "原文件.txt",
            "root_id": storage.root_identity_id(root.resolve()),
            "expected_version": _revision(client),
        },
    )
    assert patched.status_code == 200, patched.text
    deleted = client.request(
        "DELETE",
        f"{BASE}/assets/{asset_id}",
        headers=AUTH,
        json={"expected_version": _revision(client)},
    )
    assert deleted.status_code == 200, deleted.text
    entry_id = deleted.json()["recycle_entry_id"]
    # 原位置出现同名新文件，构成恢复冲突。
    (root / "原文件.txt").write_text("replacement", encoding="utf-8")
    return asset_id, entry_id


def test_suffix_restore_reports_suffix_target_and_preserves_replacement(
    local_runtime, operation_client
):
    """响应报告后缀目标路径，且原位置新文件内容不被覆盖（当前正确行为）。"""
    root = local_runtime
    client = operation_client
    asset_id, entry_id = _register_and_recycle(client, root)

    restored = client.post(
        f"{BASE}/recycle-bin/{entry_id}/restore",
        headers=GOVERNOR,
        json={"expected_version": _revision(client), "resolution": "suffix"},
    )
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["suffix_applied"] is True
    assert body["display_path"] == SUFFIX_NAME, "响应必须报告后缀目标路径"
    assert root.joinpath("原文件.txt").read_text(encoding="utf-8") == "replacement"
    assert root.joinpath(SUFFIX_NAME).read_text(encoding="utf-8") == "single-file"


@pytest.mark.xfail(
    strict=False,
    reason="QIN-431 缺陷：suffix 恢复后资产记录/规范相对路径/媒体读取仍指向原路径，待修复",
)
def test_suffix_restore_updates_asset_record_and_media_to_restored_content(
    local_runtime, operation_client
):
    """期望行为（AC）：恢复成功后登记记录、规范相对路径与物理目标一致，
    媒体读取返回恢复文件内容。当前实现不回写后缀路径，本用例先以 xfail 留痕。"""
    root = local_runtime
    client = operation_client
    asset_id, entry_id = _register_and_recycle(client, root)

    restored = client.post(
        f"{BASE}/recycle-bin/{entry_id}/restore",
        headers=GOVERNOR,
        json={"expected_version": _revision(client), "resolution": "suffix"},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["display_path"] == SUFFIX_NAME

    record = client.get(f"{BASE}/assets/{asset_id}", headers=AUTH)
    assert record.status_code == 200, record.text
    asset = record.json()["asset"]
    assert asset["display_path"] == SUFFIX_NAME, (
        "资产记录 display_path 必须与物理后缀目标一致"
    )
    assert asset.get("canonical_relpath") == SUFFIX_NAME, (
        "规范相对路径必须与物理后缀目标一致"
    )
    media = client.get(f"{BASE}/assets/{asset_id}/media", headers=AUTH)
    assert media.status_code == 200, media.text
    assert media.content == b"single-file", (
        "媒体读取必须返回恢复文件内容，而不是原位置新文件"
    )
