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

"""MVP1-T5 治理建议清单契约：只建议 + 确认写入 + 永不自动删除（AC5/AC6）。"""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import governance, repository as repo
from gw.core import storage

ADMIN = {"Authorization": "Bearer cleanroom-admin", "X-User-Role": "admin"}
EDITOR = {"Authorization": "Bearer cleanroom-editor", "X-User-Role": "editor"}
SUGGESTIONS_URL = "/api/asset-registry/governance/suggestions"
CONFIRM_URL = "/api/asset-registry/governance/suggestions/confirm"


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(data_dir))
    return TestClient(create_app())


def _seed_asset(
    name: str,
    *,
    kind: str = "video",
    display_path: str | None = None,
    sha256: str | None = None,
    size_bytes: int | None = None,
    tags: list[str] | None = None,
    video_segments: list[dict] | None = None,
) -> str:
    """通过注册表真实写入口播种资产；sha256/路径字段与扫描入库回写一致。"""
    created = repo.import_assets(
        [{"name": name, "kind": kind, "tags": list(tags or [])}], "video", None
    )
    asset_id = created["assets"][0]["asset_id"]

    def mutate(raw):
        item = raw["assets"][asset_id]
        if display_path is not None:
            item["display_path"] = display_path
        if sha256 is not None:
            item["sha256"] = sha256
        if size_bytes is not None:
            item["size_bytes"] = size_bytes
        if video_segments is not None:
            item.setdefault("metadata", {})["video_segments"] = video_segments
        return raw

    repo.state().mutate(mutate)
    return asset_id


def _suggestion(result: dict, kind: str, **filters) -> dict:
    entries = result["suggestions"][kind]
    for entry in entries:
        if all(entry.get(key) == value for key, value in filters.items()):
            return entry
    pytest.fail(f"未找到 {kind} 建议（过滤条件 {filters}）；实际：{entries}")


def test_governance_suggestions_require_authentication(client):
    """未认证读取与确认一律 401，认证先于参数校验。"""
    assert client.get(SUGGESTIONS_URL).status_code == 401
    assert client.post(CONFIRM_URL, json={}).status_code == 401


def test_governance_suggestions_forbid_non_governance_roles(client):
    """editor 角色无治理权限：读取与确认一律 403。"""
    read_response = client.get(SUGGESTIONS_URL, headers=EDITOR)
    assert read_response.status_code == 403
    confirm_response = client.post(
        CONFIRM_URL,
        headers=EDITOR,
        json={"suggestion_id": "gs_any", "expected_version": 1},
    )
    assert confirm_response.status_code == 403


def test_suggestions_are_read_only_and_cover_all_categories(client):
    """AC5：建议覆盖命名/打标/查重且只读——读取前后注册表版本不变。"""
    _seed_asset("我的 素材", display_path="roots/local/我的 素材.mp4")
    _seed_asset(
        "clip_a",
        display_path="roots/local/clip_a.mp4",
        sha256="a" * 64,
        size_bytes=10,
    )
    _seed_asset(
        "clip_b",
        display_path="roots/local/clip_b.mp4",
        sha256="a" * 64,
        size_bytes=10,
    )
    tagged = _seed_asset("镜头素材", kind="video", tags=["特写"])
    before = client.get(SUGGESTIONS_URL, headers=ADMIN)
    assert before.status_code == 200
    first = before.json()
    assert first["revision"] >= 1

    after = client.get(SUGGESTIONS_URL, headers=ADMIN)
    assert after.status_code == 200
    second = after.json()
    assert second["revision"] == first["revision"], "建议清单必须只读，不推进注册表版本"

    naming = _suggestion(first, "naming", name="我的 素材.mp4")
    assert naming["suggested_name"] == "我的_素材_v001.mp4"
    assert any(v["code"] == "SEPARATOR" for v in naming["violations"])
    assert any(v["code"] == "VERSION_SUFFIX" for v in naming["violations"])

    tagging = _suggestion(first, "tagging", asset_id=tagged)
    assert tagging["suggested_tags"] == ["CU"]

    duplicate = _suggestion(first, "duplicate_files", content_hash="a" * 64)
    assert {member["path"] for member in duplicate["members"]} == {
        "roots/local/clip_a.mp4",
        "roots/local/clip_b.mp4",
    }
    assert len(duplicate["asset_ids"]) == 2


def test_confirm_tagging_writes_controlled_tag_with_audit(client):
    """AC5：用户确认后才写入受控标签；确认记录只追加留痕且建议不再重复。"""
    asset_id = _seed_asset("镜头素材", tags=["特写"])

    listing = client.get(SUGGESTIONS_URL, headers=ADMIN).json()
    tagging = _suggestion(listing, "tagging", asset_id=asset_id)
    revision = listing["revision"]

    response = client.post(
        CONFIRM_URL,
        headers=ADMIN,
        json={
            "suggestion_id": tagging["suggestion_id"],
            "expected_version": revision,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["confirmation"]["kind"] == "tagging"
    assert payload["confirmation"]["revision_after"] == revision + 1

    raw = repo.state().read()
    tags = raw["tags"]
    tag_names = {
        str(tags[ref]["name"]) if ref in tags else ref
        for ref in raw["assets"][asset_id]["tags"]
    }
    assert "CU" in tag_names, "确认后必须写入受控词表标签"

    audit = client.get(f"{SUGGESTIONS_URL}/confirmations", headers=ADMIN)
    assert audit.status_code == 200
    confirmations = audit.json()["confirmations"]
    assert [item["suggestion_id"] for item in confirmations] == [
        tagging["suggestion_id"]
    ]

    regenerated = client.get(SUGGESTIONS_URL, headers=ADMIN).json()
    remaining = [
        entry
        for entry in regenerated["suggestions"]["tagging"]
        if entry["asset_id"] == asset_id
    ]
    assert remaining == [], "确认后的打标建议不得重复生成"


def test_confirm_duplicate_creates_relation_and_suppresses_suggestion(client):
    """AC6：确认查重生成候选关系；系统不删除数据，已确认组不再重复建议。"""
    left = _seed_asset(
        "clip_a", display_path="roots/local/clip_a.mp4", sha256="b" * 64, size_bytes=8
    )
    right = _seed_asset(
        "clip_b", display_path="roots/local/clip_b.mp4", sha256="b" * 64, size_bytes=8
    )

    listing = client.get(SUGGESTIONS_URL, headers=ADMIN).json()
    duplicate = _suggestion(listing, "duplicate_files", content_hash="b" * 64)

    response = client.post(
        CONFIRM_URL,
        headers=ADMIN,
        json={
            "suggestion_id": duplicate["suggestion_id"],
            "expected_version": listing["revision"],
        },
    )
    assert response.status_code == 200, response.text

    raw = repo.state().read()
    relations = [
        item
        for item in raw["relations"]
        if item["kind"] == "duplicate"
        and {item["from_asset_id"], item["to_asset_id"]} == {left, right}
    ]
    assert len(relations) == 1, "确认查重必须登记 duplicate 关系"
    assert raw["assets"][left]["archived"] is False, "系统永不自动删除或归档资产"

    regenerated = client.get(SUGGESTIONS_URL, headers=ADMIN).json()
    assert all(
        entry["content_hash"] != "b" * 64
        for entry in regenerated["suggestions"]["duplicate_files"]
    ), "已确认的查重组不得重复建议"


def test_confirm_rejects_stale_version_with_conflict(client):
    """CAS：过期 expected_version 返回 409 VERSION_CONFLICT，不静默覆盖。"""
    asset_id = _seed_asset("镜头素材", tags=["特写"])
    listing = client.get(SUGGESTIONS_URL, headers=ADMIN).json()
    tagging = _suggestion(listing, "tagging", asset_id=asset_id)

    stale = client.post(
        CONFIRM_URL,
        headers=ADMIN,
        json={"suggestion_id": tagging["suggestion_id"], "expected_version": 1},
    )
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "VERSION_CONFLICT"

    missing = client.post(
        CONFIRM_URL, headers=ADMIN, json={"suggestion_id": tagging["suggestion_id"]}
    )
    assert missing.status_code == 400


def test_confirm_unknown_suggestion_returns_not_found(client):
    """不存在或已失效的 suggestion_id 返回 404 SUGGESTION_NOT_FOUND。"""
    response = client.post(
        CONFIRM_URL,
        headers=ADMIN,
        json={"suggestion_id": "gs_does_not_exist", "expected_version": 1},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SUGGESTION_NOT_FOUND"


def _build_index_with_shots_and_vectors(asset_ids: list[str]) -> None:
    """构造真实检索索引：重复镜头内容哈希 + 相同嵌入向量（AC6 双通道）。"""
    from gw.core.sqlite_vec_engine import SQLiteVecEngine

    db_path = storage.data_root() / repo.ASSET_SEARCH_INDEX_FILENAME
    vector = (1.0 / math.sqrt(384),) * 384
    with SQLiteVecEngine(db_path) as engine:
        engine.batch_upsert_shots(
            [
                {
                    "asset_id": asset_ids[0],
                    "root_id": "root_local",
                    "shot_index": 0,
                    "start_sec": 0.0,
                    "end_sec": 2.0,
                    "content_hash": "c" * 64,
                },
                {
                    "asset_id": asset_ids[1],
                    "root_id": "root_local",
                    "shot_index": 0,
                    "start_sec": 0.0,
                    "end_sec": 2.0,
                    "content_hash": "c" * 64,
                },
            ]
        )
        engine.batch_upsert_documents(
            [
                {
                    "asset_id": asset_ids[0],
                    "title": "left",
                    "content": "left",
                    "embedding": list(vector),
                },
                {
                    "asset_id": asset_ids[1],
                    "title": "right",
                    "content": "right",
                    "embedding": list(vector),
                },
            ]
        )


def test_duplicate_shots_and_vector_near_duplicates_from_index(client):
    """AC6：镜头内容哈希与向量近重各自生成候选关系建议；向量轨缺失时如实降级。"""
    left = _seed_asset(
        "shot_a",
        display_path="roots/local/shot_a.mp4",
        video_segments=[{"start_sec": 0.0, "end_sec": 2.0, "tags": [], "summary": ""}],
    )
    right = _seed_asset(
        "shot_b",
        display_path="roots/local/shot_b.mp4",
        video_segments=[{"start_sec": 0.0, "end_sec": 2.0, "tags": [], "summary": ""}],
    )
    _build_index_with_shots_and_vectors([left, right])

    listing = client.get(SUGGESTIONS_URL, headers=ADMIN).json()
    vector_enabled = "vector_track_degraded" not in listing["data_gaps"]

    shots = _suggestion(listing, "duplicate_shots", content_hash="c" * 64)
    assert {member["asset_id"] for member in shots["members"]} == {left, right}

    confirm = client.post(
        CONFIRM_URL,
        headers=ADMIN,
        json={
            "suggestion_id": shots["suggestion_id"],
            "expected_version": listing["revision"],
        },
    )
    assert confirm.status_code == 200, confirm.text
    raw = repo.state().read()
    assert any(
        item["kind"] == "duplicate"
        and {item["from_asset_id"], item["to_asset_id"]} == {left, right}
        for item in raw["relations"]
    ), "确认镜头查重必须登记 duplicate 关系"

    if vector_enabled:
        pair = _suggestion(
            listing,
            "vector_duplicates",
            left_asset_id=left,
            right_asset_id=right,
        )
        assert pair["similarity"] >= governance.NEAR_DUPLICATE_SIMILARITY
    else:
        assert listing["suggestions"]["vector_duplicates"] == []


def test_suggestion_identifiers_are_stable_across_rebuilds(client):
    """确定性：同一登记数据两次生成的 suggestion_id 必须一致（幂等确认键）。"""
    _seed_asset("镜头素材", tags=["特写"])
    first = client.get(SUGGESTIONS_URL, headers=ADMIN).json()
    second = client.get(SUGGESTIONS_URL, headers=ADMIN).json()
    assert first["suggestions"] == second["suggestions"]
