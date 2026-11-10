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

"""QIN-547 收集箱契约测试（CI 门禁范围：tests/contracts）。

`tests/api` 不在 CI 执行范围，这里单独锁住审核 635-r1 指出的四条不变量：

- 迁移失败**不入账**，下次调用必须重试（否则记录被永久 skip）；
- 迁移只覆盖**当前主体可见**的资产（端点必须挂 `asset_request_scope`）；
- 遗留 `collect_inbox` 只列当前主体的子目录（不跨主体泄露文件名）；
- 改位置搬不动文件时**不切换 path**，已搬走的文件回滚回旧位置。

另锁两条迁移语义：源文件保留（不产生死链）、重复执行不多出数据。
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from PIL import Image

from gw.api.app import create_app
from gw.asset_library import inbox
from gw.asset_library.service import default_asset_library_service
from gw.core import storage
from gw.core.asset_visibility import asset_owner_key
from gw.core.auth import require_authenticated

BASE = "/api/asset-inbox"
AUTH = {"Authorization": "Bearer contract-owner", "X-User-Role": "editor"}


@pytest.fixture
def inbox_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "gw-data"
    data_dir.mkdir(parents=True)
    allowed = tmp_path / "user-library"
    allowed.mkdir(parents=True)
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    storage._ALLOWED_ROOTS_STATE_CACHE = None
    storage._ALLOWED_ROOTS_STATE_PATH_CACHE = None
    return {"data": data_dir, "allowed": allowed, "tmp": tmp_path}


def _owner_key() -> str:
    return asset_owner_key(require_authenticated(AUTH["Authorization"], "editor"))


def _register(asset_id: str, name: str, url: str | None, owner: str | None = None) -> str:
    item = default_asset_library_service.register_item(
        asset_id=asset_id,
        name=name,
        category_type="image",
        category_name="图片",
        url=url,
        workflow_owner=owner,
    )
    return item.asset_id


def test_migrate_failure_is_not_recorded_and_retries(inbox_env, monkeypatch):
    """失败不入账：第一次搬不动 → 不入账；第二次必须重试成功。"""
    source = inbox_env["allowed"] / "retry.png"
    source.write_bytes(b"retry-payload")
    asset_id = _register("asset_contract_retry", "重试素材.png", source.as_uri())

    with TestClient(create_app()) as client:
        real_transfer = inbox._transfer_into
        monkeypatch.setattr(
            inbox, "_transfer_into", lambda *_args: (_ for _ in ()).throw(OSError("locked"))
        )
        broken = client.post(f"{BASE}/migrate", headers=AUTH).json()
        assert broken["failed"] == [{"asset_id": asset_id, "code": "INBOX_MIGRATE_FAILED"}]
        assert broken["moved_files"] == 0
        assert broken["total_records"] == 0, "失败记录不得写入 migrated 账本"

        again = client.post(f"{BASE}/migrate", headers=AUTH).json()
        assert again["skipped"] == 0, "失败过的资产不能被当成已迁移"
        assert again["moved_files"] == 0, "仍然失败时不入账"

        monkeypatch.setattr(inbox, "_transfer_into", real_transfer)
        recovered = client.post(f"{BASE}/migrate", headers=AUTH).json()
        assert recovered["failed"] == []
        assert recovered["moved_files"] == 1, "恢复后必须重试成功"
        assert recovered["skipped"] == 0


def test_migrate_keeps_source_file_and_is_idempotent(inbox_env):
    """迁移保留源文件（不产生死链），且重复执行不多出文件。"""
    source = inbox_env["allowed"] / "keep-source.png"
    source.write_bytes(b"source-payload")
    _register("asset_contract_keep", "保留源文件.png", source.as_uri())

    with TestClient(create_app()) as client:
        first = client.post(f"{BASE}/migrate", headers=AUTH).json()
        assert first["moved_files"] == 1, first
        assert first["linked"] + first["copied"] == 1, first
        assert source.is_file(), "源文件必须保留，否则托管资产 url 变成死链"

        copies = list((inbox_env["data"] / "inbox").rglob("*.png"))
        assert len(copies) == 1
        assert copies[0].read_bytes() == b"source-payload"
        assert copies[0].name.startswith("inbox_"), "物理落盘名应为纯 ASCII 生成名"

        second = client.post(f"{BASE}/migrate", headers=AUTH).json()
        assert second["moved_files"] == 0
        assert second["skipped"] == 1
        assert len(list((inbox_env["data"] / "inbox").rglob("*.png"))) == 1


def test_migrate_ignores_assets_owned_by_other_subjects(inbox_env):
    """主体隔离：带 workflow_owner 的资产只有归属者能迁入。"""
    source = inbox_env["allowed"] / "owned.png"
    source.write_bytes(b"owned")
    _register("asset_contract_mine", "我的素材.png", source.as_uri(), owner=_owner_key())
    _register("asset_contract_other", "别人的素材.png", source.as_uri(), owner="other-subject")

    with TestClient(create_app()) as client:
        payload = client.post(f"{BASE}/migrate", headers=AUTH).json()
        assert payload["moved_files"] == 1, payload
        assert payload["total_records"] == 1, "别人的资产不得被迁入"
        names = {
            item["name"]
            for item in client.get(BASE, headers=AUTH).json()["items"]
            if item["source"] == "inbox"
        }
        assert names == {"我的素材.png"}


def test_legacy_collect_dir_is_scoped_to_current_subject(inbox_env):
    """遗留收集目录按主体分账：只列当前主体的子目录。"""
    owner_dir = hashlib.sha256(_owner_key().encode("utf-8")).hexdigest()
    mine = inbox_env["data"] / "registry_uploads" / "collect_inbox" / owner_dir
    mine.mkdir(parents=True)
    (mine / "mine.png").write_bytes(b"mine")
    stranger = inbox_env["data"] / "registry_uploads" / "collect_inbox" / ("a" * 64)
    stranger.mkdir(parents=True)
    (stranger / "stranger.png").write_bytes(b"stranger")

    with TestClient(create_app()) as client:
        entries = [
            item
            for item in client.get(BASE, headers=AUTH).json()["items"]
            if item["source"] == "collect_inbox"
        ]
        assert [item["name"] for item in entries] == ["mine.png"]
        assert entries[0]["file"] == f"registry_uploads/collect_inbox/{owner_dir}/mine.png"


def test_relocate_failure_keeps_path_and_rolls_back(inbox_env, monkeypatch):
    """改位置搬不动时不切 path，并把已搬走的文件搬回旧位置。"""
    target = inbox_env["tmp"] / "target-inbox"
    with TestClient(create_app()) as client:
        upload = client.post(
            f"{BASE}/upload",
            headers=AUTH,
            files={"files": ("half.png", b"payload", "image/png")},
        )
        stored = upload.json()["items"][0]["file"]
        real_move = inbox._move_into
        state = {"calls": 0}

        def flaky(new_root: Path, relative: str, source: Path) -> str:
            state["calls"] += 1
            if state["calls"] == 1:
                raise OSError("volume full")
            return real_move(new_root, relative, source)

        monkeypatch.setattr(inbox, "_move_into", flaky)
        payload = client.put(
            f"{BASE}/settings", headers=AUTH, json={"path": str(target), "migrate": True}
        ).json()
        assert payload["applied"] is False, payload
        assert payload["failed"], payload
        assert Path(payload["path"]) == (inbox_env["data"] / "inbox").resolve()
        assert (inbox_env["data"] / "inbox" / stored).is_file(), "文件必须仍在旧位置可访问"
        assert client.get(BASE, headers=AUTH).json()["count"] == 1


def _png_bytes(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_inbox_preview_identity_fails_closed(inbox_env):
    """【QIN-588】收集箱预览身份必须失败关闭：穿越/绝对路径/软链接逃逸/匿名全部拒绝。

    媒体预览是允许根之外的唯一受控读图通道，边界破一处就是任意文件读。
    """
    (inbox_env["data"] / "inbox").mkdir(parents=True, exist_ok=True)
    (inbox_env["data"] / "inbox" / "inbox_ok.png").write_bytes(_png_bytes())

    # 路径穿越：即使指向 data_root 内的 auth 目录也必须拒绝（收集箱边界不是 data_root）。
    assert inbox.resolve_preview_request({"inbox_file": "../auth/auth.sqlite3"}) is None
    assert inbox.resolve_preview_request({"inbox_file": "a/../b.png"}) is None
    # 绝对路径与反斜杠：结构性拒绝。
    assert inbox.resolve_preview_request({"inbox_file": "C:/Windows/win.ini"}) is None
    assert inbox.resolve_preview_request({"inbox_file": "a\\b.png"}) is None
    # 空值与未知参数回落既有链路：不下发受控身份。
    assert inbox.resolve_preview_request({"inbox_file": ""}) is None
    assert inbox.resolve_preview_request({}) is None

    with TestClient(create_app()) as client:
        stored = client.post(
            f"{BASE}/upload",
            headers=AUTH,
            files={"files": ("shot.png", _png_bytes(), "image/png")},
        ).json()["items"][0]["file"]
        traversal = client.get(
            f"/api/media-preview?w=256&inbox_file={stored}/..%2F..%2Fauth%2Fauth.sqlite3",
            headers=AUTH,
        )
        assert traversal.status_code == 404, traversal.text

        absolute = client.get(
            "/api/media-preview?w=256&inbox_file="
            + quote(str(inbox_env["data"] / "inbox" / stored), safe=""),
            headers=AUTH,
        )
        assert absolute.status_code == 404, absolute.text

        anonymous = client.get(f"/api/media-preview?w=256&inbox_file={stored}")
        assert anonymous.status_code == 401, "预览必须经鉴权"

    # 软链接逃逸：平台支持时必须拒绝（Linux CI 覆盖；Windows 无特权时跳过）。
    outside = inbox_env["tmp"] / "outside.png"
    outside.write_bytes(_png_bytes((0, 255, 0)))
    try:
        link = inbox_env["data"] / "inbox" / "inbox_escape.png"
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台无法创建符号链接")
    assert inbox.resolve_preview_request({"inbox_file": "inbox_escape.png"}) is None


def test_inbox_list_preview_query_contract(inbox_env):
    """【QIN-588】列表条目只下发受控预览查询串：本地条目有、URL 条目没有、遗留按主体。

    前端零路径知识：预览 URL 全部由服务端授权的相对参数拼装，响应不得泄露绝对路径。
    """
    source = inbox_env["allowed"] / "migrated.png"
    source.write_bytes(_png_bytes((0, 128, 255)))
    _register("asset_contract_preview", "已迁移素材.png", source.as_uri())
    _register("asset_contract_url", "外链素材.png", "https://example.com/external.png")

    with TestClient(create_app()) as client:
        client.post(f"{BASE}/migrate", headers=AUTH)
        uploaded = client.post(
            f"{BASE}/upload",
            headers=AUTH,
            files={"files": ("shot.png", _png_bytes(), "image/png")},
        ).json()["items"][0]

        listing = client.get(BASE, headers=AUTH).json()
        by_id = {item["id"]: item for item in listing["items"]}
        assert uploaded["id"] in by_id
        assert by_id[uploaded["id"]]["preview_query"].startswith("inbox_file=")
        assert str(inbox_env["data"]) not in json_dumps(listing), "列表响应不得含绝对路径"

        url_entry = next(
            item
            for item in listing["items"]
            if item["source"] == "asset_library" and item.get("asset_id") == "asset_contract_url"
        )
        assert url_entry["preview_query"] is None, "URL 条目不得下发受控预览参数"


def json_dumps(payload) -> str:
    from json import dumps

    return dumps(payload, ensure_ascii=False, default=str)
