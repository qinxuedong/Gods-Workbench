"""QIN-664 挂载根素材显隐持久化、检索与计数合同。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_settings
from gw.api.app import create_app
from gw.asset_registry import index_jobs, repository
from gw.asset_registry.hybrid_search import HybridAssetSearcher
from gw.core import storage
from gw.core.sqlite_vec_engine import FTSMatch, SQLiteVecEngine
from gw.settings import service as settings_service
from gw.settings.models import StorageSettingsPatchRequest
from gw.settings.service import StorageSettingsService

EDITOR = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
VIEWER = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "viewer"}


@pytest.fixture()
def mounted_runtime(monkeypatch, tmp_path: Path):
    root = tmp_path / "mounted"
    nested = root / "nested"
    sibling = tmp_path / "mounted-sibling"
    for path in (root, nested, sibling):
        path.mkdir(parents=True)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", f"{root}{__import__('os').pathsep}{sibling}")
    service = StorageSettingsService()
    monkeypatch.setattr(settings_service, "default_storage_settings_service", service)
    monkeypatch.setattr(routes_settings, "default_storage_settings_service", service)
    storage._ALLOWED_ROOTS_STATE_CACHE = None
    storage._ALLOWED_ROOTS_STATE_PATH_CACHE = None
    index_jobs.invalidate_root_facets_cache()
    with TestClient(create_app()) as client:
        yield client, service, root, nested, sibling
    index_jobs.invalidate_root_facets_cache()


def _seed_assets(root: Path, nested: Path, sibling: Path) -> tuple[str, str, str]:
    root_id = storage.root_identity_id(root)
    nested_id = "local_extra_qin664_nested"
    sibling_id = storage.root_identity_id(sibling)

    def seed(raw):
        records = (
            ("ast_qin664_hidden", root_id, root_id, "folder/hidden.mp4", "project_hidden"),
            ("ast_qin664_nested", nested_id, nested_id, "nested.mp4", "project_hidden"),
            ("ast_qin664_visible", sibling_id, sibling_id, "folder/visible.mp4", "project_visible"),
        )
        for asset_id, physical_root_id, source_id, relpath, project_id in records:
            raw["assets"][asset_id] = {
                "asset_id": asset_id,
                "name": f"sharedneedle-{asset_id}.mp4",
                "kind": "video",
                "root_id": physical_root_id,
                "storage_root_id": source_id,
                "canonical_relpath": relpath,
                "relative_path": relpath,
                "display_path": relpath,
                "archived": False,
                "is_remote": False,
                "tags": ["sharedneedle"],
                "metadata": {"video_segments": []},
            }
        return raw

    repository.state().mutate(seed)
    return root_id, nested_id, sibling_id


def test_hidden_mount_filters_before_list_counts_and_shot_fallback(mounted_runtime):
    client, service, root, nested, sibling = mounted_runtime
    root_id, nested_id, sibling_id = _seed_assets(root, nested, sibling)

    # 同一物理根同时具有 root_* 与 local_extra_* 身份时，命令仍展开到统一身份组。
    saved = service.patch(
        StorageSettingsPatchRequest(
            extra_local_entries=[
                {"id": "local_extra_qin664", "name": "重复身份", "path": str(root)},
                {"id": nested_id, "name": "子挂载", "path": str(nested)},
            ],
            expected_version=1,
        )
    )
    assert saved.revision == 2
    registry_revision = repository.readonly_registry_snapshot()["revision"]

    denied = client.patch(
        "/api/storage-settings",
        headers=VIEWER,
        json={
            "hidden_asset_root_id": root_id,
            "hidden_asset_hidden": True,
            "expected_version": 2,
        },
    )
    assert denied.status_code == 403

    hidden = client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={
            "hidden_asset_root_id": root_id,
            "hidden_asset_hidden": True,
            "expected_version": 2,
        },
    )
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["hidden_asset_root_keys"] == [storage.directory_path_key(str(root))]
    assert repository.readonly_registry_snapshot()["revision"] == registry_revision

    settings_restored = StorageSettingsService().get_snapshot()
    assert settings_restored.hidden_asset_root_keys == [storage.directory_path_key(str(root))]

    assets = client.get("/api/asset-registry/assets", headers=EDITOR)
    assert assets.status_code == 200, assets.text
    assert assets.json()["total"] == 1
    assert [item["asset_id"] for item in assets.json()["assets"]] == ["ast_qin664_visible"]

    status = client.get("/api/asset-registry/status", headers=EDITOR).json()
    assert status["assets_count"] == status["overview"]["assets"] == 1
    facets = client.get("/api/asset-registry/facets", headers=EDITOR).json()
    assert facets["kinds"] == [{"name": "video", "count": 1}]
    roots = {item["id"]: item for item in facets["roots"]}
    root_facet = next(item for item in roots.values() if root_id in item["aliases"])
    nested_facet = next(item for item in roots.values() if nested_id in item["aliases"])
    sibling_facet = next(item for item in roots.values() if sibling_id in item["aliases"])
    assert root_facet["assets_hidden"] is True
    assert nested_facet["assets_hidden"] is True
    assert sibling_facet["assets_hidden"] is False
    assert root_facet["count"] == 1
    assert repository.keyword_recalled_asset_ids(
        repository.readonly_registry_snapshot(), "sharedneedle", 10
    ) == ["ast_qin664_visible"]
    assert repository.visible_asset_from_snapshot(
        repository.readonly_registry_snapshot(), "ast_qin664_nested"
    ) is None

    tree = repository.folders(root_id)
    assert any(item["path"] == "folder" and item["total_count"] == 1 for item in tree["indexed_folders"])

    # The shot path filters before provider calls and asset-granularity fallback/limit.
    db_path = storage.data_root() / repository.ASSET_SEARCH_INDEX_FILENAME
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteVecEngine(db_path) as engine:
        engine.batch_upsert_documents(
            [
                {"asset_id": "ast_qin664_hidden", "title": "hidden", "content": "sharedneedle"},
                {"asset_id": "ast_qin664_nested", "title": "nested", "content": "sharedneedle"},
                {"asset_id": "ast_qin664_visible", "title": "visible", "content": "sharedneedle"},
            ]
        )
    shot_search = client.get(
        "/api/asset-registry/search/shots", headers=EDITOR,
        params={"q": "sharedneedle", "limit": 1},
    )
    assert shot_search.status_code == 200, shot_search.text
    assert [item["asset_id"] for item in shot_search.json()["items"]] == ["ast_qin664_visible"]

    def add_project_ownership(raw):
        raw["assets"]["ast_qin664_hidden"].update(
            project_id="project_hidden", project_ids=["project_hidden"]
        )
        raw["assets"]["ast_qin664_nested"].update(
            project_id="project_hidden", project_ids=["project_hidden"]
        )
        raw["assets"]["ast_qin664_visible"].update(
            project_id="project_visible", project_ids=["project_visible"]
        )
        return raw

    repository.state().mutate(add_project_ownership)
    assert repository.project_asset_counts() == {"project_visible": 1}
    assert repository.keyword_recalled_asset_ids(
        repository.readonly_registry_snapshot(), "sharedneedle", 10
    ) == ["ast_qin664_visible"]

    def clear_project_ownership(raw):
        for asset in raw["assets"].values():
            asset.pop("project_id", None)
            asset.pop("project_ids", None)
        return raw

    repository.state().mutate(clear_project_ownership)

    stale = client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={
            "hidden_asset_root_id": root_id,
            "hidden_asset_hidden": False,
            "expected_version": 2,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"

    shown = client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={
            "hidden_asset_root_id": root_id,
            "hidden_asset_hidden": False,
            "expected_version": hidden.json()["revision"],
        },
    )
    assert shown.status_code == 200, shown.text
    assert shown.json()["hidden_asset_root_keys"] == []
    restored = client.get("/api/asset-registry/assets", headers=EDITOR)
    assert restored.json()["total"] == 3
    assert repository.readonly_registry_snapshot()["revision"] == registry_revision


def test_purge_clears_hidden_root_and_descendant_keys(mounted_runtime):
    _, service, root, nested, _ = mounted_runtime
    root_key = storage.directory_path_key(str(root))
    nested_key = storage.directory_path_key(str(nested))
    service.patch(
        StorageSettingsPatchRequest(
            hidden_asset_root_keys=[root_key, nested_key], expected_version=1
        )
    )
    service.purge_entries({"root_qin664"}, [str(root)])
    assert service.get_snapshot().hidden_asset_root_keys == []


class _ShotEngine:
    def __init__(self, matches):
        self.matches = matches
        self.last_vector_search_degraded = False

    def search_fts(self, _query, *, limit):
        return self.matches[:limit]

    def search_vectors(self, _query_vector, *, limit):
        return []


def test_hybrid_shot_search_filters_before_fallback_and_limit():
    engine = _ShotEngine([
        FTSMatch(asset_id="hidden", score=2.0),
        FTSMatch(asset_id="visible", score=1.0),
    ])
    provider_calls = []
    results = HybridAssetSearcher(engine).search_shots(
        "query",
        limit=1,
        asset_filter=lambda asset_id: asset_id == "visible",
        shot_provider=lambda asset_id: provider_calls.append(asset_id) or [],
    )
    assert provider_calls == ["visible"]
    assert [hit.asset_id for hit in results.hits] == ["visible"]
    assert results.hits[0].granularity == "asset"
