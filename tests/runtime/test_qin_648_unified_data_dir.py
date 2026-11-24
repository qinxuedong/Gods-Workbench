"""QIN-648：验证桌面端引导目录与默认数据目录收口及存量升级兼容。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from clients.desktop.packaging import build_release
from gw.core.errors import CleanroomException
from gw.core.runtime_paths import (
    MODE_PROD,
    PROD_DATA_LOCATION_INVALID,
    RuntimePathError,
    resolve_runtime_paths,
)
from gw.settings.data_location import (
    inspect_existing_data_location,
    installer_init_data_location,
    status_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_NSH = REPO_ROOT / "clients" / "desktop" / "packaging" / "nsis" / "hooks.nsh"
UPGRADE_PS1 = REPO_ROOT / "clients" / "desktop" / "packaging" / "nsis" / "upgrade.ps1"


def test_qin_648_fresh_prod_paths_and_installer_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1/AC2：新装默认引导目录为 Gods Workbench，数据目录为 Gods Workbench/user data。"""
    local_app = tmp_path / "LocalAppData"
    local_app.mkdir()
    install_dir = tmp_path / "ProgramFiles" / "Gods Workbench"
    install_dir.mkdir(parents=True)

    monkeypatch.setenv("LOCALAPPDATA", str(local_app))
    for key in ("GW_DATA_DIR", "GW_AUTH_DB", "GW_VIDEO_DIR", "GW_RUNTIME_MODE"):
        monkeypatch.delenv(key, raising=False)

    # 1. 未写指针前，resolve_runtime_paths 与 inspect_existing_data_location 一致指向新口径
    paths = resolve_runtime_paths(MODE_PROD, repo_root=tmp_path / "repo", localappdata=local_app, env={})
    expected_bootstrap = (local_app / "Gods Workbench").resolve()
    expected_user_data = (expected_bootstrap / "user data").resolve()
    assert paths.runtime_root == expected_bootstrap
    assert paths.data_root == expected_user_data / "data"
    assert paths.auth_db == expected_user_data / "auth" / "auth.sqlite3"
    assert paths.video_root == expected_user_data / "video"

    info = inspect_existing_data_location(localappdata=local_app)
    assert info["has_existing"] is False
    assert info["bootstrap_root"] == str(expected_bootstrap)
    assert info["suggested_fresh_location"] == str(expected_user_data)
    assert info["effective_data_location"] == str(expected_user_data)

    # 2. 安装向导写入 default user data 时不应误判为与 bootstrap_root 重叠
    init_res = installer_init_data_location(
        str(expected_user_data),
        install_dir=str(install_dir),
        localappdata=local_app,
    )
    assert init_res["ok"] is True
    assert init_res["created"] is True
    assert init_res["pointer_path"] == str(expected_bootstrap / "runtime-root.json")
    assert init_res["data_location"] == str(expected_user_data)

    # 3. 写入指针后，resolve_runtime_paths 读取该指针且 data_location 生效
    after = resolve_runtime_paths(MODE_PROD, repo_root=tmp_path / "repo", localappdata=local_app, env={})
    assert after.runtime_root == expected_bootstrap
    assert after.data_location == expected_user_data
    assert after.data_root == expected_user_data / "data"


def test_qin_648_upgrade_compat_legacy_pointer_and_data(tmp_path: Path) -> None:
    """AC3：旧版 GodsWorkbench 指针、旧版 GodsWorkbench 数据、旧版 Gods Workbench data 只读回退兼容。"""
    # 情形 1：旧版 GodsWorkbench/runtime-root.json 指向自定义目录
    local_1 = tmp_path / "case1"
    legacy_boot_1 = local_1 / "GodsWorkbench"
    custom_data_1 = (tmp_path / "custom_d1").resolve()
    custom_data_1.mkdir(parents=True)
    legacy_boot_1.mkdir(parents=True)
    (legacy_boot_1 / "runtime-root.json").write_text(
        json.dumps({"schema_version": 1, "revision": 1, "data_location": str(custom_data_1)}),
        encoding="utf-8",
    )
    p1 = resolve_runtime_paths(MODE_PROD, repo_root=tmp_path / "repo", localappdata=local_1, env={})
    assert p1.runtime_root == legacy_boot_1.resolve()
    assert p1.data_location == custom_data_1
    assert p1.data_root == custom_data_1 / "data"

    info1 = inspect_existing_data_location(localappdata=local_1)
    assert info1["has_existing"] is True
    assert info1["pointer_present"] is True
    assert info1["effective_data_location"] == str(custom_data_1)

    # 情形 2：旧版 GodsWorkbench 无指针但已有 auth.sqlite3 生产数据
    local_2 = tmp_path / "case2"
    legacy_boot_2 = local_2 / "GodsWorkbench"
    (legacy_boot_2 / "auth").mkdir(parents=True)
    (legacy_boot_2 / "auth" / "auth.sqlite3").write_bytes(b"sqlite")
    p2 = resolve_runtime_paths(MODE_PROD, repo_root=tmp_path / "repo", localappdata=local_2, env={})
    assert p2.runtime_root == legacy_boot_2.resolve()
    assert p2.data_root == legacy_boot_2.resolve() / "data"

    info2 = inspect_existing_data_location(localappdata=local_2)
    assert info2["has_existing"] is True
    assert info2["legacy_data_present"] is True
    assert info2["effective_data_location"] == str(legacy_boot_2.resolve())

    # 情形 3：旧版 Gods Workbench data 无指针但已有生产数据
    local_3 = tmp_path / "case3"
    legacy_data_3 = local_3 / "Gods Workbench data"
    (legacy_data_3 / "data").mkdir(parents=True)
    (legacy_data_3 / "data" / "projects.json").write_text("{}", encoding="utf-8")
    p3 = resolve_runtime_paths(MODE_PROD, repo_root=tmp_path / "repo", localappdata=local_3, env={})
    assert p3.data_root == legacy_data_3.resolve() / "data"

    info3 = inspect_existing_data_location(localappdata=local_3)
    assert info3["has_existing"] is True
    assert info3["effective_data_location"] == str(legacy_data_3.resolve())


def test_qin_648_corrupt_pointer_fails_closed(tmp_path: Path) -> None:
    """AC3：新版或旧版 runtime-root.json 损坏时必须 fail-closed，不得静默回落。"""
    local_app = tmp_path / "LocalAppData"
    new_boot = local_app / "Gods Workbench"
    new_boot.mkdir(parents=True)
    (new_boot / "runtime-root.json").write_text("{corrupt", encoding="utf-8")

    with pytest.raises(RuntimePathError) as exc_info:
        resolve_runtime_paths(MODE_PROD, repo_root=tmp_path / "repo", localappdata=local_app, env={})
    assert exc_info.value.code == PROD_DATA_LOCATION_INVALID


def test_qin_648_build_release_and_nsis_scripts_aligned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2/AC4：build_release.py、hooks.nsh 与 upgrade.ps1 统一指向 Gods Workbench / user data。"""
    local_app = tmp_path / "LocalAppData"
    local_app.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local_app))

    assert build_release.production_bootstrap_root() == local_app / "Gods Workbench"
    assert build_release.production_data_root() == local_app / "Gods Workbench" / "user data"

    hooks_text = HOOKS_NSH.read_text(encoding="utf-8")
    assert '!define GW_BOOTSTRAP_DIR_NAME "Gods Workbench"' in hooks_text
    assert '!define GW_DATA_DIR_DEFAULT_NAME "Gods Workbench\\user data"' in hooks_text
    assert '!define GW_LEGACY_BOOTSTRAP_DIR_NAME "GodsWorkbench"' in hooks_text

    upgrade_text = UPGRADE_PS1.read_text(encoding="utf-8")
    assert '"Gods Workbench"' in upgrade_text
    assert '"GodsWorkbench"' in upgrade_text


def test_qin_648_corrupt_legacy_pointer_with_stale_new_data_fails_closed(
    tmp_path: Path,
) -> None:
    """无新指针、新引导目录存在陈旧 data、旧指针损坏时，必须 fail-closed 不得跳过校验。"""
    local_app = tmp_path / "LocalAppData"
    stale_new_data = local_app / "Gods Workbench" / "data"
    stale_new_data.mkdir(parents=True)
    (stale_new_data / "stale.json").write_text("{}", encoding="utf-8")

    legacy_boot = local_app / "GodsWorkbench"
    legacy_boot.mkdir(parents=True)
    (legacy_boot / "runtime-root.json").write_text("{corrupt", encoding="utf-8")

    with pytest.raises(RuntimePathError) as exc_info:
        resolve_runtime_paths(MODE_PROD, repo_root=tmp_path / "repo", localappdata=local_app, env={})
    assert exc_info.value.code == PROD_DATA_LOCATION_INVALID

    with pytest.raises(CleanroomException) as ce_info:
        inspect_existing_data_location(localappdata=local_app)
    assert ce_info.value.code == PROD_DATA_LOCATION_INVALID


def test_qin_648_valid_legacy_pointer_wins_over_stale_new_data_and_status_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """合法旧指针优先于新引导目录陈旧 data，且 status_snapshot 真实回读一致。"""
    local_app = tmp_path / "LocalAppData"
    stale_new_data = local_app / "Gods Workbench" / "data"
    stale_new_data.mkdir(parents=True)
    (stale_new_data / "stale.json").write_text("{}", encoding="utf-8")

    legacy_boot = local_app / "GodsWorkbench"
    legacy_boot.mkdir(parents=True)
    custom_target = (tmp_path / "CustomLegacyTarget").resolve()
    custom_target.mkdir(parents=True)
    (legacy_boot / "runtime-root.json").write_text(
        json.dumps({"schema_version": 1, "revision": 2, "data_location": str(custom_target)}),
        encoding="utf-8",
    )

    resolved = resolve_runtime_paths(MODE_PROD, repo_root=tmp_path / "repo", localappdata=local_app, env={})
    assert resolved.runtime_root == legacy_boot.resolve()
    assert resolved.data_location == custom_target

    info = inspect_existing_data_location(localappdata=local_app)
    assert info["has_existing"] is True
    assert info["effective_data_location"] == str(custom_target)

    monkeypatch.setenv("GW_RUNTIME_MODE", "prod")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app))
    for key in ("GW_DATA_DIR", "GW_LOCAL_AUTH_DB", "GW_VIDEO_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)
    snap = status_snapshot()
    assert snap["bootstrap_root"] == str(legacy_boot.resolve())
    assert snap["data_location"] == str(custom_target)


def test_qin_648_hooks_nsh_probe_powershell_behavior(tmp_path: Path) -> None:
    """在 Windows 下直接执行 hooks.nsh 探测命令，验证损坏指针退出码 5 与旧指针优先行为。"""
    if sys.platform != "win32":
        pytest.skip("仅 Windows 执行 hooks.nsh PowerShell 探测行为验证")
    ps_exe = shutil.which("powershell.exe") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    if not Path(ps_exe).is_file():
        pytest.skip("未找到 powershell.exe")

    nsh_text = HOOKS_NSH.read_text(encoding="utf-8")
    pattern = re.compile(
        r'nsExec::ExecToStack\s+`"\$SYSDIR\\WindowsPowerShell\\v1\.0\\powershell\.exe"[^`"]*-Command\s+"([^`]+)"`'
    )
    commands = pattern.findall(nsh_text)
    assert len(commands) == 5
    probe_raw = commands[3]  # 第 4 条为 GW_RESOLVE_PATHS 中的指针与数据探测命令

    def run_probe(local_app: Path) -> subprocess.CompletedProcess[str]:
        cmd = probe_raw.replace("$$", "$")
        cmd = cmd.replace("${GW_BOOTSTRAP_DIR_NAME}", "Gods Workbench")
        cmd = cmd.replace("${GW_DATA_DIR_DEFAULT_NAME}", r"Gods Workbench\user data")
        cmd = cmd.replace("${GW_LEGACY_BOOTSTRAP_DIR_NAME}", "GodsWorkbench")
        cmd = cmd.replace("${GW_LEGACY_DATA_DIR_NAME}", "Gods Workbench data")
        cmd = cmd.replace("$R6", str(local_app))
        return subprocess.run(
            [ps_exe, "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    # 1. 全新空目录 -> FRESH|.../Gods Workbench/user data
    fresh_app = tmp_path / "fresh"
    fresh_app.mkdir()
    res_fresh = run_probe(fresh_app)
    assert res_fresh.returncode == 0
    assert res_fresh.stdout.strip() == f"FRESH|{fresh_app / 'Gods Workbench' / 'user data'}"

    # 2. 新目录有陈旧 data + 旧指针损坏 -> 必须非零退出（5）不降级
    corrupt_app = tmp_path / "corrupt"
    (corrupt_app / "Gods Workbench" / "data").mkdir(parents=True)
    (corrupt_app / "Gods Workbench" / "data" / "s.json").write_text("{}", encoding="utf-8")
    (corrupt_app / "GodsWorkbench").mkdir(parents=True)
    (corrupt_app / "GodsWorkbench" / "runtime-root.json").write_text("{bad", encoding="utf-8")
    res_corrupt = run_probe(corrupt_app)
    assert res_corrupt.returncode == 5

    # 3. 新目录有陈旧 data + 旧指针合法 -> 必须返回 EXISTING|<旧指针目标>
    target = (tmp_path / "LegacyCustom").resolve()
    target.mkdir()
    (corrupt_app / "GodsWorkbench" / "runtime-root.json").write_text(
        json.dumps({"schema_version": 1, "data_location": str(target)}),
        encoding="utf-8",
    )
    res_valid = run_probe(corrupt_app)
    assert res_valid.returncode == 0
    assert res_valid.stdout.strip() == f"EXISTING|{target}"

