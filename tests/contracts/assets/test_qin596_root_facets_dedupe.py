"""QIN-596 root_facets 物理路径合并、计数去重、root_id 身份组与 folders 归一契约。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs, repository as registry_repo
from gw.core import storage
from gw.settings.service import (
    StorageAllowedRootsService,
    StorageSettingsPatchRequest,
    StorageSettingsService,
)

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


@pytest.fixture()
def facet_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    allowed = tmp_path / "Assets"
    allowed.mkdir()
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setattr(
        "gw.settings.service.default_storage_settings_service", StorageSettingsService()
    )
    monkeypatch.setattr(
        "gw.settings.service.default_storage_allowed_roots_service",
        StorageAllowedRootsService(),
    )
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None
    index_jobs._ROOT_FACETS_CACHE.update(key=None, value=None, expires_at=0.0)
    yield allowed
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None
    index_jobs._ROOT_FACETS_CACHE.update(key=None, value=None, expires_at=0.0)


def _root_id(allowed: Path) -> str:
    return storage.root_identity_id(allowed)


def _register_source(allowed: Path, source_id: str, name: str = "素材库") -> None:
    """把登记来源写入 storage settings（等价 register_root 的 settings patch 步骤）。"""
    service = StorageSettingsService()
    snapshot = service.get_snapshot()
    entries = [dict(item) for item in snapshot.extra_local_entries]
    entries.append({"id": source_id, "kind": source_id, "name": name, "path": str(allowed)})
    service.patch(
        StorageSettingsPatchRequest(
            dirs=dict(snapshot.dirs),
            extra_local_entries=entries,
            local_library_names={source_id: name},
            local_library_order=[*(item for item in snapshot.local_library_order if item != source_id), source_id],
            expected_version=snapshot.revision,
        )
    )


def _seed_asset(root_id: str, storage_root_id: str, relpath: str, asset_id: str) -> None:
    asset = registry_repo._plain_asset(
        asset_id,
        relpath.rsplit("/", 1)[-1],
        "image",
        root_id=root_id,
        storage_root_id=storage_root_id,
        display_path=relpath,
        canonical_relpath=relpath,
    )
    registry_repo.state().mutate(
        lambda raw: raw.setdefault("assets", {}).update({asset["asset_id"]: asset})
    )


def _merged_facet(allowed: Path) -> dict | None:
    index_jobs._ROOT_FACETS_CACHE.update(key=None, value=None, expires_at=0.0)
    rid = _root_id(allowed)
    for facet in index_jobs.root_facets():
        ids = {str(facet.get("id") or "")} | {str(a) for a in facet.get("aliases") or []}
        if rid in ids:
            return facet
    return None


def test_same_path_dual_identity_merges_into_one_facet_with_union_count(facet_runtime):
    allowed = facet_runtime
    rid = _root_id(allowed)
    _register_source(allowed, "local_extra_aaa")
    # 完全重叠：root_xxx 身份 3 条历史记录 + local_extra 身份同 3 个物理位置。
    for index in range(3):
        relpath = f"file_{index}.png"
        _seed_asset(rid, rid, relpath, f"ast_stale_{index}")
        _seed_asset(rid, "local_extra_aaa", relpath, f"ast_fresh_{index}")
    facet = _merged_facet(allowed)
    assert facet is not None
    assert facet["id"] == "local_extra_aaa"  # 主 ID = 登记来源身份
    assert facet["count"] == 3  # 物理位置并集去重，不重复相加
    assert set(facet["aliases"]) == {rid, "local_extra_aaa"}


def test_partial_overlap_and_disjoint_counts_union(facet_runtime):
    allowed = facet_runtime
    rid = _root_id(allowed)
    _register_source(allowed, "local_extra_bbb")
    # 部分重叠：root_xxx {A,B,C}、local_extra {B,C,D} → 并集 4。
    for relpath in ("a.png", "b.png", "c.png"):
        _seed_asset(rid, rid, relpath, f"ast_p_{relpath}")
    for relpath in ("b.png", "c.png", "d.png"):
        _seed_asset(rid, "local_extra_bbb", relpath, f"ast_q_{relpath}")
    facet = _merged_facet(allowed)
    assert facet["count"] == 4
    # 各自独有：root_xxx {e}、local_extra {f} → 2（先清空 registry 再种）。
    registry_repo.state().mutate(lambda raw: raw.update({"assets": {}}))
    _seed_asset(rid, rid, "e.png", "ast_e")
    _seed_asset(rid, "local_extra_bbb", "f.png", "ast_f")
    index_jobs._ROOT_FACETS_CACHE.update(key=None, value=None, expires_at=0.0)
    facet = _merged_facet(allowed)
    assert facet["count"] == 2


def test_registry_snapshot_isolation_between_steps(facet_runtime):
    """同一路径双身份与不同路径不合并（父根与子来源保持两条 facet）。"""
    allowed = facet_runtime
    rid = _root_id(allowed)
    sub = allowed / "Sub"
    sub.mkdir()
    (sub / "s.txt").write_text("x", encoding="utf-8")
    _register_source(allowed, "local_extra_top", "顶层库")
    _register_source(sub, "local_extra_sub", "子库")
    index_jobs._ROOT_FACETS_CACHE.update(key=None, value=None, expires_at=0.0)
    facets = index_jobs.root_facets()
    top = next(f for f in facets if "local_extra_top" in {f["id"], *(f.get("aliases") or [])})
    sub_facet = next(f for f in facets if "local_extra_sub" in {f["id"], *(f.get("aliases") or [])})
    assert top["id"] == "local_extra_top"
    assert sub_facet["id"] == "local_extra_sub"
    assert top["id"] != sub_facet["id"]


def test_pure_allowed_root_keeps_root_identity_facet(facet_runtime):
    allowed = facet_runtime
    rid = _root_id(allowed)
    facet = _merged_facet(allowed)
    assert facet is not None
    assert facet["id"] == rid  # 无登记来源时主 ID = 允许根身份（现状保持）


def test_root_id_filter_accepts_primary_and_alias_ids(facet_runtime):
    allowed = facet_runtime
    rid = _root_id(allowed)
    _register_source(allowed, "local_extra_ccc")
    _seed_asset(rid, "local_extra_ccc", "x.png", "ast_x")
    index_jobs._ROOT_FACETS_CACHE.update(key=None, value=None, expires_at=0.0)
    with TestClient(create_app()) as client:
        for request_id in (rid, "local_extra_ccc"):
            response = client.get(
                BASE + f"/assets?root_id={request_id}&limit=50", headers=AUTH
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert [item["id"] for item in payload["items"]] == ["ast_x"]
        # 未知身份仍 404。
        missing = client.get(BASE + "/assets?root_id=root_unknown", headers=AUTH)
        assert missing.status_code == 404


def test_folders_returns_same_normalized_tree_for_any_group_identity(facet_runtime):
    allowed = facet_runtime
    rid = _root_id(allowed)
    _register_source(allowed, "local_extra_ddd")
    _seed_asset(rid, "local_extra_ddd", "docs/a.png", "ast_a")
    _seed_asset(rid, rid, "docs/b.png", "ast_b")
    index_jobs._ROOT_FACETS_CACHE.update(key=None, value=None, expires_at=0.0)
    with TestClient(create_app()) as client:
        trees = []
        for request_id in (rid, "local_extra_ddd"):
            response = client.get(
                BASE + f"/folders?root_id={request_id}", headers=AUTH
            )
            assert response.status_code == 200, response.text
            body = response.json()
            indexed = sorted(
                (item["path"], item["count"], item["root_id"])
                for item in body["indexed_folders"]
            )
            trees.append(indexed)
        assert trees[0] == trees[1]
        # 返回身份归一为主 ID：同一棵树、无重复节点。
        assert {item[2] for item in trees[0]} == {"local_extra_ddd"}
        assert trees[0] == [("docs", 2, "local_extra_ddd")]
