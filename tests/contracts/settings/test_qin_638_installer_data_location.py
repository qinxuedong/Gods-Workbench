"""QIN-638 安装向导双目录选择：路径约束、单一权威指针与提权/升级回归测试。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from gw.core.errors import CleanroomException
from gw.core.runtime_paths import (
    DEFAULT_INSTALLER_DATA_DIR_NAME,
    LEGACY_PROD_DATA_DIR_NAME,
    MODE_PROD,
    data_location_pointer_path,
    paths_non_overlapping,
    resolve_runtime_paths,
)
from gw.settings import data_location as service

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_fresh_install_writes_single_pointer_and_prod_cold_reads_same_location(tmp_path: Path) -> None:
    localappdata = tmp_path / "UserLocalAppData"
    localappdata.mkdir()
    install_dir = tmp_path / "ProgramFiles" / "Gods Workbench"
    install_dir.mkdir(parents=True)
    chosen_data = localappdata / DEFAULT_INSTALLER_DATA_DIR_NAME

    inspect_before = service.inspect_existing_data_location(localappdata=localappdata)
    assert inspect_before["has_existing"] is False
    assert Path(inspect_before["suggested_fresh_location"]) == chosen_data.resolve(strict=False)

    init_res = service.installer_init_data_location(
        str(chosen_data),
        install_dir=str(install_dir),
        localappdata=localappdata,
    )
    assert init_res["ok"] is True
    assert init_res["created"] is True
    pointer_path = data_location_pointer_path(localappdata / LEGACY_PROD_DATA_DIR_NAME)
    assert pointer_path.is_file()
    payload = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert Path(payload["data_location"]) == chosen_data.resolve(strict=False)

    cold_paths = resolve_runtime_paths(MODE_PROD, localappdata=localappdata, env={})
    assert cold_paths.runtime_root == (localappdata / LEGACY_PROD_DATA_DIR_NAME).resolve(strict=False)
    assert cold_paths.data_location == chosen_data.resolve(strict=False)
    assert cold_paths.data_root == (chosen_data / "data").resolve(strict=False)
    assert cold_paths.auth_db == (chosen_data / "auth" / "auth.sqlite3").resolve(strict=False)
    assert cold_paths.video_root == (chosen_data / "video").resolve(strict=False)


def test_paths_non_overlapping_normalizes_case_and_trailing_separators(tmp_path: Path) -> None:
    base = tmp_path / "Gods Workbench"
    assert paths_non_overlapping(str(base), str(base) + os.sep) is False
    if os.name == "nt":
        assert paths_non_overlapping(str(base).upper(), str(base).lower()) is False
    assert paths_non_overlapping(base, base / "data") is False
    assert paths_non_overlapping(base / "app", base) is False
    assert paths_non_overlapping(base, tmp_path / "Gods Workbench data") is True


def test_installer_init_rejects_overlapping_install_and_data_dir(tmp_path: Path) -> None:
    localappdata = tmp_path / "UserLocalAppData"
    localappdata.mkdir()
    install_dir = tmp_path / "ProgramFiles" / "Gods Workbench"
    install_dir.mkdir(parents=True)

    for bad_target in (install_dir, install_dir / "nested-data", install_dir.parent):
        with pytest.raises(CleanroomException) as exc:
            service.installer_init_data_location(
                str(bad_target),
                install_dir=str(install_dir),
                localappdata=localappdata,
            )
        assert exc.value.code == "TARGET_OVERLAP"
    assert not data_location_pointer_path(localappdata / LEGACY_PROD_DATA_DIR_NAME).exists()


def test_upgrade_preserves_existing_pointer_without_overwriting(tmp_path: Path) -> None:
    localappdata = tmp_path / "UserLocalAppData"
    bootstrap = localappdata / LEGACY_PROD_DATA_DIR_NAME
    bootstrap.mkdir(parents=True)
    custom_old = tmp_path / "CustomOldData"
    custom_old.mkdir()
    pointer_path = data_location_pointer_path(bootstrap)
    pointer_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": 3,
                "data_location": str(custom_old),
                "previous_location": None,
                "last_migration": None,
                "keep_user_data": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    inspect_res = service.inspect_existing_data_location(localappdata=localappdata)
    assert inspect_res["has_existing"] is True
    assert inspect_res["pointer_present"] is True
    assert Path(inspect_res["effective_data_location"]) == custom_old.resolve(strict=False)

    same_res = service.installer_init_data_location(str(custom_old), localappdata=localappdata)
    assert same_res["created"] is False
    assert same_res["preserved_existing"] is True

    with pytest.raises(CleanroomException) as exc:
        service.installer_init_data_location(
            str(localappdata / DEFAULT_INSTALLER_DATA_DIR_NAME),
            localappdata=localappdata,
        )
    assert exc.value.code == "EXISTING_DATA_PRESERVED"
    assert Path(json.loads(pointer_path.read_text(encoding="utf-8"))["data_location"]) == custom_old


def test_status_snapshot_reports_effective_install_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    (runtime_root / "data").mkdir(parents=True)
    (runtime_root / "auth").mkdir(parents=True)
    (runtime_root / "video").mkdir(parents=True)
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(runtime_root / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(runtime_root / "auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(runtime_root / "video"))
    fake_install = tmp_path / "ProgramFiles" / "Gods Workbench"
    monkeypatch.setenv("GW_INSTALL_DIR", str(fake_install))

    snap = service.status_snapshot()
    assert snap["install_dir"] == str(fake_install)
    assert "data_location" in snap and Path(snap["data_location"]).is_absolute()


def test_packaging_and_nsis_hooks_honor_dual_dir_and_elevation_contract() -> None:
    sys.path.insert(0, str(REPO_ROOT / "clients" / "desktop" / "packaging"))
    try:
        import build_release  # noqa: PLC0415
    finally:
        sys.path.pop(0)

    hooks_text = (REPO_ROOT / "clients" / "desktop" / "packaging" / "nsis" / "hooks.nsh").read_text(encoding="utf-8")
    upgrade_text = (REPO_ROOT / "clients" / "desktop" / "packaging" / "nsis" / "upgrade.ps1").read_text(encoding="utf-8")
    assert "Gods Workbench data" in hooks_text
    assert "Shell Folders" in hooks_text
    assert "Page custom GwDataDirPageCreate GwDataDirPageLeave" in hooks_text
    assert "-UserLocalAppData" in hooks_text
    assert "Cleanup-FreshInstallPointer" in upgrade_text
    assert '$psi.EnvironmentVariables["LOCALAPPDATA"] = $effectiveLocalAppData' in upgrade_text
    assert build_release.HOOKS_NSH.is_file()
