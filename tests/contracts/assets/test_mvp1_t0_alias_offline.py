"""MVP1-T0 索引别名、离线 facet 与移除根诊断契约。"""

from __future__ import annotations

import threading
from pathlib import Path

from gw.asset_registry import index_jobs, repository
from gw.core import storage
from gw.settings import service as settings_service_module
from gw.settings.models import (
    StorageAllowedRootDeleteRequest,
    StorageAllowedRootRequest,
    StorageSettingsPatchRequest,
)
from gw.settings.service import StorageAllowedRootsService, StorageSettingsService


def test_background_scan_accepts_network_root_resolved_to_unc(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "mapped-root"
    root.mkdir()
    (root / "sample.mp4").write_bytes(b"metadata-only-scan")
    scanner = object.__new__(index_jobs.IndexJobs)
    scanner.condition = threading.Condition(threading.RLock())
    monkeypatch.setattr(scanner, "_checkpoint", lambda *_args: None)
    monkeypatch.setattr(storage, "_is_network_path", lambda _path: True)
    original_resolve = Path.resolve

    def resolve_as_unc(path, *args, **kwargs):
        if path == root:
            return Path(r"\\nas\share\mapped-root")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_as_unc)
    files = scanner._scan(
        "mvp1-t0-network-root",
        1,
        {"root-network": root},
        {
            "roots": ["root-network"],
            "scopes": [
                {
                    "root_id": "root-network",
                    "prefix": "",
                    "storage_root_id": "root-network",
                }
            ],
            "hash_files": False,
            "max_files": 10,
        },
    )

    assert [item["display_path"] for item in files] == ["sample.mp4"]
    assert files[0]["size_bytes"] == len(b"metadata-only-scan")


def test_mapped_drive_and_unc_suggestion_is_explicit_and_keeps_root_id(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    drive_root = r"Z:\素材库"
    unc_root = r"\\nas\media\素材库"
    mapped_paths = {
        drive_root: unc_root,
        drive_root + r"\子目录": unc_root + r"\子目录",
    }
    monkeypatch.setattr(storage, "_mapped_drive_unc", lambda path: mapped_paths.get(str(path)))
    roots = [Path(drive_root)]

    drive_suggestions = storage.mount_alias_candidates(drive_root + r"\子目录", roots)
    unc_suggestions = storage.mount_alias_candidates(unc_root + r"\子目录", roots)
    assert drive_suggestions == [{"root_id": storage.root_identity_id(Path(drive_root)), "path": unc_root + r"\子目录"}]
    assert unc_suggestions == [{"root_id": storage.root_identity_id(Path(drive_root)), "path": unc_root + r"\子目录"}]


def test_index_facets_retain_offline_and_removed_roots_with_reasons(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "allowed"
    library = root / "library"
    library.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    roots_service = StorageAllowedRootsService()
    added = roots_service.add_root(
        StorageAllowedRootRequest(path=str(root), expected_version=1)
    )
    root_id = next(item.id for item in added.roots if item.source == "persistent")
    settings = StorageSettingsService()
    monkeypatch.setattr(settings_service_module, "default_storage_settings_service", settings)
    settings.patch(
        StorageSettingsPatchRequest(
            dirs={"local": str(library)}, expected_version=1
        )
    )

    local_facet = next(item for item in index_jobs.root_facets() if item["id"] == "local")
    assert local_facet["availability"] == "available"
    assert local_facet["availability_reason"] == ""
    location_identity = storage.location_identity(library / "sample.mp4")

    offline = root / "library-offline"
    library.rename(offline)
    path_type = type(library)
    original_resolve = path_type.resolve
    library_key = storage.directory_path_key(str(library))

    def reject_offline_resolve(path, *args, **kwargs):
        if storage.directory_path_key(str(path)) == library_key:
            raise AssertionError("offline network path resolution must stay bounded")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "resolve", reject_offline_resolve)
    offline_facet = next(item for item in index_jobs.root_facets() if item["id"] == "local")
    monkeypatch.setattr(path_type, "resolve", original_resolve)
    assert offline_facet["availability"] == "offline"
    assert offline_facet["availability_reason"] == "MOUNT_NOT_FOUND"
    offline.rename(library)
    recovered_facet = next(item for item in index_jobs.root_facets() if item["id"] == "local")
    assert recovered_facet["availability"] == "available"
    assert storage.location_identity(library / "sample.mp4") == location_identity

    root_offline = tmp_path / "allowed-offline"
    root_id = storage.root_identity_id(root)
    root.rename(root_offline)
    offline_root_facet = next(item for item in index_jobs.root_facets() if item["id"] == root_id)
    assert offline_root_facet["availability"] == "offline"
    assert offline_root_facet["availability_reason"] == "MOUNT_NOT_FOUND"
    root_offline.rename(root)
    restored_root_facet = next(item for item in index_jobs.root_facets() if item["id"] == root_id)
    assert restored_root_facet["availability"] == "available"
    assert storage.root_identity_id(root) == root_id
    assert storage.location_identity(library / "sample.mp4") == location_identity

    settings._state.mutate(lambda state: state["dirs"].update({"local": str(outside)}))
    invalid_facet = next(item for item in index_jobs.root_facets() if item["id"] == "local")
    assert invalid_facet["availability"] == "invalid"
    assert invalid_facet["availability_reason"] == "MOUNT_OUTSIDE_ALLOWED_ROOTS"
    settings._state.mutate(lambda state: state["dirs"].update({"local": str(library)}))

    asset = repository._plain_asset(
        "ast_mvp1_t0_removed",
        "保留的素材",
        "video",
        root_id=root_id,
        storage_root_id=root_id,
        canonical_relpath="clip.mp4",
        display_path="clip.mp4",
        is_remote=False,
    )
    repository.state().mutate(lambda state: state["assets"].update({asset["asset_id"]: asset}))
    roots_service.remove_root(
        root_id, StorageAllowedRootDeleteRequest(expected_version=2)
    )

    removed_facet = next(item for item in index_jobs.root_facets() if item["id"] == root_id)
    assert removed_facet["availability"] == "removed"
    assert removed_facet["availability_reason"] == "MOUNT_ROOT_REMOVED"
    assert removed_facet["count"] == 1


def test_index_roots_surfaces_unconfirmed_mount_alias_as_invalid_facet(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "unc-path"
    outside.mkdir()
    roots_service = StorageAllowedRootsService()
    roots_service.add_root(StorageAllowedRootRequest(path=str(root), expected_version=1))
    settings = StorageSettingsService()
    monkeypatch.setattr(settings_service_module, "default_storage_settings_service", settings)
    settings.patch(StorageSettingsPatchRequest(dirs={"local": str(root)}, expected_version=1))
    settings._state.mutate(lambda state: state["dirs"].update({"local": str(outside)}))
    monkeypatch.setattr(
        storage,
        "mount_alias_candidates",
        lambda *_args, **_kwargs: [{"root_id": "root-suggestion", "path": str(outside)}],
    )

    facet = next(item for item in index_jobs.root_facets() if item["id"] == "local")
    assert facet["availability"] == "invalid"
    assert facet["availability_reason"] == "MOUNT_ALIAS_UNCONFIRMED"
