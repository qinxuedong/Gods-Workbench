"""QIN-377: Windows junction 不得让常规素材路径越过允许根。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from gw.asset_registry import repository as asset_repository
from gw.core import storage
from gw.core.errors import CleanroomException


def _create_junction(link: Path, target: Path) -> None:
    """创建真实 Windows Junction，避免以普通符号链接替代实机证据。"""
    powershell = shutil.which("powershell.exe")
    assert powershell is not None, "未找到 powershell.exe，无法创建真实 Junction"
    environment = {
        **os.environ,
        "GW_TEST_JUNCTION": str(link),
        "GW_TEST_TARGET": str(target),
    }
    # 仅调用 PATH 中解析出的 PowerShell，参数固定；临时路径来自 pytest tmp_path。
    result = subprocess.run(  # noqa: S603
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:GW_TEST_JUNCTION -Target $env:GW_TEST_TARGET | Out-Null",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    assert result.returncode == 0, (
        "真实 Junction 创建失败，不能用符号链接结果替代验收: "
        f"{result.stderr or result.stdout}"
    )


@pytest.mark.skipif(os.name != "nt", reason="该动态验收需要 Windows Junction")
def test_external_junction_is_rejected_for_asset_access_and_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed_root = tmp_path / "allowed_root"
    outside_root = tmp_path / "outside_root"
    allowed_root.mkdir()
    outside_root.mkdir()
    outside_file = outside_root / "asset_377.bin"
    outside_file.write_bytes(b"outside-allowed-root")
    junction = allowed_root / "linked_assets"
    _create_junction(junction, outside_root)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed_root))

    with pytest.raises(CleanroomException) as read_error:
        storage.resolve_request_path(str(junction / outside_file.name)).read_bytes()
    assert read_error.value.status_code == 403
    assert read_error.value.code == "PATH_OUTSIDE_ALLOWED_ROOTS"

    with pytest.raises(CleanroomException) as write_error:
        storage.resolve_request_path(str(junction / "asset_377_created.bin")).write_bytes(
            b"must-not-escape"
        )
    assert write_error.value.status_code == 403
    assert write_error.value.code == "PATH_OUTSIDE_ALLOWED_ROOTS"
    assert not (outside_root / "asset_377_created.bin").exists()

    with pytest.raises(CleanroomException) as identity_error:
        storage.location_identity(junction / outside_file.name)
    assert identity_error.value.status_code == 403
    assert identity_error.value.code == "PATH_OUTSIDE_ALLOWED_ROOTS"

    scanned, gaps = asset_repository._scan_allowed_roots()
    assert gaps == []
    assert outside_file.name not in {item["name"] for item in scanned}
