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

"""QIN-626 Windows 标准安装版打包、自检、契约与安全边界测试。

覆盖设计表第 12 节的窄测要求：版本来源、安装自检无账户副作用、能力契约
摘要、打包白名单、安装/数据根互不包含、升级 journal、票据交付边界、
ACL 校验正反例与公开材料脱敏。
"""

from __future__ import annotations

import getpass
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import run
from gw.core import config as auth_config

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "clients" / "desktop"
PACKAGING = DESKTOP / "packaging"
BASELINE_DIGEST = "cc47d8d4ac74adc87f073127886e13e71ffbcd3aef3248a11bb957d55c4086e2"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


compat_contract = _load_module("gw_qin626_compat_contract", PACKAGING / "compat_contract.py")
build_release = _load_module("gw_qin626_build_release", PACKAGING / "build_release.py")


def _manifest_payload(product_version: str = "9.8.7", build_id: str = "build-test") -> dict[str, object]:
    return {
        "schema_version": 1,
        "product_version": product_version,
        "build_id": build_id,
        "git_commit": "abcdef0",
        "capability_profile": "gw-compat-v1",
        "capability_digest": BASELINE_DIGEST,
        "runtime_inventory_sha256": "0" * 64,
    }


def _write_manifest(path: Path, **overrides: object) -> Path:
    payload = _manifest_payload()
    payload.update(overrides)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _clean_env(tmp_path: Path, manifest: Path | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GW_")}
    env["LOCALAPPDATA"] = str(tmp_path / "localappdata")
    env["PYTHONIOENCODING"] = "utf-8"
    env["GW_DISABLE_AGENT_INTEGRATION"] = "1"
    if manifest is not None:
        env["GW_BUNDLE_MANIFEST"] = str(manifest)
    return env


def _run_self_check(tmp_path: Path, manifest: Path | None, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "run.py"), "--prod", "--self-check", *extra],
        cwd=ROOT,
        env=_clean_env(tmp_path, manifest),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def test_capability_digest_matches_python_and_rust_baselines() -> None:
    assert compat_contract.capability_digest() == BASELINE_DIGEST
    result = subprocess.run(
        [sys.executable, str(PACKAGING / "compat_contract.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == BASELINE_DIGEST
    rust = (DESKTOP / "src" / "compat.rs").read_text(encoding="utf-8")
    assert BASELINE_DIGEST in rust
    assert "capability_profile" in rust


def test_manifest_version_is_real_and_missing_never_defaults_to_static_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path / "bundle-manifest.json")
    info = run.resolve_version(manifest)
    assert info.source == "manifest"
    assert info.product_version == "9.8.7"
    assert info.app_version == "9.8.7+gw.build-test"
    assert info.trusted_marker is True

    invalid = tmp_path / "invalid-manifest.json"
    invalid.write_text("{not-json", encoding="utf-8")
    broken = run.resolve_version(invalid)
    assert broken.source == "unknown"
    assert broken.app_version == "unknown"
    assert broken.trusted_marker is False

    missing = tmp_path / "missing-manifest.json"
    monkeypatch.setenv("GW_PRODUCT_VERSION", "1.2.3")
    declared = run.resolve_version(missing)
    assert declared.source == "declared"
    assert declared.app_version == "1.2.3+gw.declared"

    monkeypatch.setenv("GW_PRODUCT_VERSION", "not-a-version")
    assert run.resolve_version(missing).source == "unknown"

    monkeypatch.delenv("GW_PRODUCT_VERSION", raising=False)
    monkeypatch.setattr(run, "_git_version", lambda _root: None)
    unknown = run.resolve_version(missing)
    assert unknown.source == "unknown"
    assert unknown.product_version != "0.1.0"
    assert unknown.trusted_marker is False


def test_manifest_validation_rejects_profile_digest_and_build_id(tmp_path: Path) -> None:
    bad_profile = _write_manifest(tmp_path / "bad-profile.json", capability_profile="other-profile")
    bad_digest = _write_manifest(tmp_path / "bad-digest.json", capability_digest="not-a-digest")
    bad_build = _write_manifest(tmp_path / "bad-build.json", build_id="bad id")
    for path in (bad_profile, bad_digest, bad_build):
        info = run.resolve_version(path)
        assert info.source == "unknown"
        assert info.error


def test_self_check_uses_manifest_and_writes_no_user_data(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "bundle-manifest.json")
    result = _run_self_check(tmp_path, manifest, "--expect-version", "9.8.7")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["product_version"] == "9.8.7"
    assert payload["app_version"] == "9.8.7+gw.build-test"
    assert payload["install_data_non_overlapping"] is True
    created = sorted(
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")
    )
    assert created == ["bundle-manifest.json"]


def test_self_check_rejects_missing_or_mismatched_manifest(tmp_path: Path) -> None:
    missing = tmp_path / "missing-manifest.json"
    result = _run_self_check(tmp_path, missing)
    assert result.returncode != 0
    assert "可信随包版本" in result.stderr

    manifest = _write_manifest(tmp_path / "bundle-manifest.json")
    mismatch = _run_self_check(tmp_path, manifest, "--expect-version", "9.9.9")
    assert mismatch.returncode != 0
    assert "版本" in mismatch.stderr

    corrupt = tmp_path / "corrupt-manifest.json"
    corrupt.write_text("{", encoding="utf-8")
    broken = _run_self_check(tmp_path, corrupt)
    assert broken.returncode != 0


def test_run_py_no_store_middleware_is_limited_to_probe_paths(tmp_path: Path) -> None:
    app = FastAPI()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/other")
    def other() -> dict[str, str]:
        return {"ok": "yes"}

    info = run.resolve_version(_write_manifest(tmp_path / "bundle-manifest.json"))
    assert run._apply_version_to_app(app, info) is True
    client = TestClient(app)
    health = client.get("/healthz")
    openapi = client.get("/openapi.json")
    other_response = client.get("/other")
    assert "no-store" in health.headers["cache-control"]
    assert "no-store" in openapi.headers["cache-control"]
    assert "cache-control" not in other_response.headers


def test_version_injection_invalidates_openapi_schema() -> None:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    first = run.VersionInfo("1.0.0", "build-a", "1.0.0+gw.build-a", "manifest")
    second = run.VersionInfo("1.0.1", "build-b", "1.0.1+gw.build-b", "manifest")
    assert run._apply_version_to_app(app, first)
    assert app.openapi()["info"]["version"] == first.app_version
    assert run._apply_version_to_app(app, second)
    assert app.openapi()["info"]["version"] == second.app_version


def test_backend_exposes_required_capability_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gw.api.app import app

    schema = app.openapi()
    assert schema["info"]["title"] == compat_contract.OPENAPI_TITLE
    assert set(run.REQUIRED_OPENAPI_OPERATIONS) <= run._openapi_operations(schema)

    monkeypatch.delenv("GW_AUTH_MODE", raising=False)
    auth_db = tmp_path / "gw-auth" / "auth.sqlite3"
    auth_db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(auth_db))
    auth_config.reset_runtime_auth_config_cache()
    client = TestClient(app)
    health = client.get("/healthz")
    assert health.status_code == 200
    health_body = health.json()
    assert health_body["status"] == "ok"
    assert health_body["mode"] == "cleanroom"
    assert set(compat_contract.HEALTH_REQUIRED_KEYS) <= set(health_body)

    status = client.get("/api/asset-auth/status")
    assert status.status_code == 200
    status_body = status.json()
    assert status_body["authenticated"] is False
    assert set(compat_contract.STATUS_REQUIRED_KEYS) <= set(status_body)
    assert "no-store" in status.headers.get("cache-control", "")

    projects = client.get("/api/asset-registry/projects")
    assert projects.status_code == 401
    assert projects.json()["detail"]["code"] == "UNAUTHORIZED"


def test_capability_payload_matches_document_and_required_keys() -> None:
    payload = compat_contract.canonical_payload()
    assert payload["profile"] == "gw-compat-v1"
    assert payload["openapi"]["title"] == compat_contract.OPENAPI_TITLE
    assert set(payload["openapi"]["operations"]) == set(compat_contract.REQUIRED_OPERATIONS)
    assert set(payload["health"]["required_keys"]) == set(compat_contract.HEALTH_REQUIRED_KEYS)
    assert set(payload["status"]["required_keys"]) == set(compat_contract.STATUS_REQUIRED_KEYS)
    assert payload["projects_unauth"] == {"code": "UNAUTHORIZED", "status": 401}
    assert payload["version_provenance"] is True
    document = (ROOT / "docs" / "guides" / "DESKTOP-COMPATIBILITY-CONTRACT.md").read_text(
        encoding="utf-8"
    )
    assert BASELINE_DIGEST in document
    assert "gw-compat-v1" in document
    for operation in compat_contract.REQUIRED_OPERATIONS:
        assert operation in document


def test_packaging_lock_whitelist_and_exclusions() -> None:
    lock = json.loads((PACKAGING / "runtime-lock.json").read_text(encoding="utf-8"))
    assert lock["cpython"]["version"] == "3.11.9"
    assert len(lock["cpython"]["sha256"]) == 64
    assert lock["cpython"]["url"].startswith("https://www.python.org/")
    assert lock["wheel_platform"]["abi"] == "cp311"
    assert set(lock["source_paths"]) == {"gw", "web", "run.py", "tools/comfy_workbench_bridge/js/workbench.js"}
    assert lock["helper_scripts"] == ["clients/desktop/packaging/verify_trusted_acl.py"]
    assert "ComfyUI" not in json.dumps(lock["source_paths"])
    assert lock["forbidden_segments"] and lock["forbidden_suffixes"]


def test_packaging_scan_rejects_comfyui_ffmpeg_and_models(tmp_path: Path) -> None:
    lock = json.loads((PACKAGING / "runtime-lock.json").read_text(encoding="utf-8"))
    for relative in ("ComfyUI/runtime.py", "tools/ffmpeg.exe", "models/base.safetensors", "video/clip.mp4"):
        staging = tmp_path / relative
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_bytes(b"x")
        with pytest.raises(build_release.BuildError):
            build_release.scan_forbidden(tmp_path, lock)
        staging.unlink()


def test_install_data_non_containment_is_bidirectional(tmp_path: Path) -> None:
    install = tmp_path / "install"
    data = tmp_path / "data"
    install.mkdir()
    data.mkdir()
    assert build_release.paths_non_overlapping(install, data) is True
    assert build_release.paths_non_overlapping(install, install / "inside") is False
    assert build_release.paths_non_overlapping(install, install) is False
    assert run._paths_non_overlapping(install, data) is True
    assert run._paths_non_overlapping(install, install / "inside") is False
    assert run._paths_non_overlapping(install, install) is False


def test_release_config_and_manifest_are_same_version(tmp_path: Path) -> None:
    staging_backend = tmp_path / "staging" / "backend"
    staging_backend.mkdir(parents=True)
    manifest = build_release.write_manifest(
        staging_backend, "2.3.4", "build-42", "abcdef0", "0" * 64
    )
    config_path = build_release.write_release_config(tmp_path, staging_backend, "2.3.4")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert manifest["product_version"] == "2.3.4"
    assert manifest["capability_digest"] == BASELINE_DIGEST
    assert config["version"] == "2.3.4"
    assert config["bundle"]["targets"] == ["nsis"]
    assert config["bundle"]["windows"]["nsis"]["installMode"] == "currentUser"
    assert str(staging_backend) in config["bundle"]["resources"]
    assert "packaging/upgrade.ps1" in config["bundle"]["resources"].values()
    assert config["bundle"]["windows"]["nsis"]["installerHooks"].endswith("hooks.nsh")


def test_runtime_inventory_and_generated_notices_cover_actual_wheels(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    python_dir = backend / "python"
    dist_info = python_dir / "Lib" / "site-packages" / "demo_pkg-1.2.3.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: demo-pkg\nVersion: 1.2.3\n"
        "License: MIT\nAuthor: Demo Authors\nHome-page: https://example.invalid/demo\n",
        encoding="utf-8",
    )
    licenses = backend / "licenses" / "demo_pkg-1.2.3.dist-info"
    licenses.mkdir(parents=True)
    (licenses / "LICENSE").write_text("demo license", encoding="utf-8")
    wheel = tmp_path / "demo_pkg-1.2.3-py3-none-any.whl"
    wheel.write_bytes(b"fake wheel")
    lock = json.loads((PACKAGING / "runtime-lock.json").read_text(encoding="utf-8"))
    license_paths = ["licenses/demo_pkg-1.2.3.dist-info"]
    notices_path = build_release.write_runtime_notices(backend, lock, python_dir, license_paths)
    assert notices_path == "licenses/RUNTIME-COMPONENTS.md"
    notices = (backend / notices_path).read_text(encoding="utf-8")
    assert "demo-pkg" in notices
    assert "1.2.3" in notices
    assert "MIT" in notices
    assert "Python-2.0" in notices
    inventory = build_release.build_runtime_inventory(
        lock,
        lock["cpython"]["sha256"],
        [wheel],
        license_paths,
        notices_path,
    )
    assert inventory["notices"] == notices_path
    assert inventory["wheels"][0]["name"] == "demo_pkg"
    assert "ComfyUI" in inventory["excluded_components"]
    assert "ComfyUI" not in inventory["wheel_lock"]


def test_copy_license_files_handles_directory_and_file_names(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    python_dir = backend / "python"
    python_dir.mkdir(parents=True)
    (python_dir / "LICENSE.txt").write_text("CPython license", encoding="utf-8")
    dist_info = python_dir / "Lib" / "site-packages" / "demo_pkg-1.2.3.dist-info"
    (dist_info / "licenses").mkdir(parents=True)
    (dist_info / "licenses" / "LICENSE").write_text("wheel license", encoding="utf-8")
    (dist_info / "COPYING").write_text("wheel copying", encoding="utf-8")
    lock = {"license_files": ["LICENSE"], "cpython": {"version": "3.11.9"}}
    copied = build_release.copy_license_files(backend, lock, python_dir)
    target = backend / "licenses" / dist_info.name
    assert "licenses/demo_pkg-1.2.3.dist-info" in copied
    assert (target / "licenses" / "LICENSE").read_text(encoding="utf-8") == "wheel license"
    assert (target / "COPYING").read_text(encoding="utf-8") == "wheel copying"
    assert (backend / "licenses" / "CPython-3.11.9" / "LICENSE.txt").is_file()


def test_packaging_self_check_never_writes_bytecode_into_payload() -> None:
    source = (PACKAGING / "build_release.py").read_text(encoding="utf-8")
    upgrade = (PACKAGING / "nsis" / "upgrade.ps1").read_text(encoding="utf-8")
    assert 'environment["PYTHONDONTWRITEBYTECODE"] = "1"' in source
    assert '$env:PYTHONDONTWRITEBYTECODE = "1"' in upgrade
    assert '`"$runPy`"' in upgrade
    assert "$psi.Arguments =" in upgrade
    assert "WaitForExit(60000)" in upgrade
    assert upgrade.startswith("\ufeff"), "Windows PowerShell 5.1 需要 UTF-8 BOM 才能正确解析中文提示"


def test_installer_hooks_and_self_check_contract() -> None:
    hooks = (PACKAGING / "nsis" / "hooks.nsh").read_text(encoding="utf-8")
    upgrade = (PACKAGING / "nsis" / "upgrade.ps1").read_text(encoding="utf-8")
    main_source = (DESKTOP / "src" / "main.rs").read_text(encoding="utf-8")
    assert '.env("PYTHONDONTWRITEBYTECODE", "1")' in main_source
    assert hooks.startswith("\ufeff"), "NSIS 3 需要 UTF-8 BOM 才能正确编译中文提示"
    for state in ("prepare", "backed_up", "installing", "installed", "rolled_back", "aborted"):
        assert f'"{state}"' in hooks
    assert '"verified"' not in hooks, "verified 由安装后自检写入，且不得触发上一代备份还原"
    assert "NSIS_HOOK_PREINSTALL" in hooks
    assert "NSIS_HOOK_POSTINSTALL" in hooks
    assert "NSIS_HOOK_PREUNINSTALL" in hooks
    assert "upgrade.ps1" in hooks
    assert "robocopy.exe" in hooks
    assert "$GW_" not in hooks, "编译期常量必须写成 ${GW_*}，$GW_* 会被 NSIS 展开为空"
    assert '!define GW_BACKUP_PROGRAM "${GW_UPGRADE_ROOT}' in hooks
    assert "${MAINBINARYNAME}.exe" in hooks
    assert "Gods Workbench.exe" not in hooks, "安装产物主程序名以生成脚本的 MAINBINARYNAME 为准"
    assert 'RMDir /r "$GW_DATA_ROOT"' not in hooks
    assert 'GW_WRITE_STATE "committed"' not in hooks
    message_boxes = [line for line in hooks.splitlines() if "MessageBox" in line]
    assert message_boxes
    assert all("/SD IDOK" in line for line in message_boxes), "静默安装失败必须返回退出码而不是阻塞在提示框"
    assert "--self-check" in upgrade
    assert "--expect-version" in upgrade
    assert 'Set-JournalState "verified"' in upgrade
    assert 'Set-JournalState "committed"' not in upgrade
    assert "local_accounts_cli" not in upgrade
    assert "trusted-claim" not in upgrade


def test_upgrade_journal_commit_is_deferred_to_first_handshake() -> None:
    main_source = (DESKTOP / "src" / "main.rs").read_text(encoding="utf-8")
    assert "fn commit_upgrade_journal" in main_source
    assert 'matches!(state.trim(), "installed" | "verified")' in main_source
    assert "if report.local {\n        commit_upgrade_journal();\n    }" in main_source
    assert "commit_upgrade_journal();\n\n    if manifest.is_some() && report.trusted_bootstrap_available" in main_source


def test_upgrade_recovery_never_restores_an_older_successful_generation() -> None:
    hooks = (PACKAGING / "nsis" / "hooks.nsh").read_text(encoding="utf-8")
    upgrade = (PACKAGING / "nsis" / "upgrade.ps1").read_text(encoding="utf-8")
    preinstall = hooks.split("!macro NSIS_HOOK_PREINSTALL", 1)[1].split("!macroend", 1)[0]
    recovery_guard = preinstall.split('StrCpy $R3 "clean"', 1)[1].split(
        '!insertmacro GW_WRITE_STATE "prepare"', 1
    )[0]
    restore_macro = hooks.split("!macro GW_RESTORE_BACKUP", 1)[1].split("!macroend", 1)[0]
    restore_failure = restore_macro.split("${If} $0 > 7", 1)[1].split("${EndIf}", 1)[0]

    assert "rolled_back" not in restore_failure, "还原失败不能写成已回退，否则下次会覆盖仍可用的备份"
    assert '$R3 == "installing"' in recovery_guard
    assert '$R3 == "installed"' in recovery_guard
    assert '$R3 == "verified"' not in recovery_guard, (
        "verified 表示上一代升级已通过自检，若据此还原 backup-last，"
        "下一次升级会先回退到更早版本，失败时也无法回到升级前版本"
    )
    assert preinstall.index('!insertmacro GW_WRITE_STATE "backed_up"') < preinstall.index(
        '!insertmacro GW_WRITE_STATE "installing"'
    )
    assert 'Set-JournalState "rolled_back"' not in upgrade, (
        "自检失败必须先由 NSIS 完成还原再写 rolled_back；提前写入会让中断的安装覆盖唯一备份"
    )


def test_ticket_delivery_is_loopback_fragment_and_memory_only() -> None:
    trusted = (DESKTOP / "src" / "trusted.rs").read_text(encoding="utf-8")
    main_source = (DESKTOP / "src" / "main.rs").read_text(encoding="utf-8")
    ui = (DESKTOP / "ui" / "app.js").read_text(encoding="utf-8")
    assert '!matches!(host, "127.0.0.1" | "::1")' in trusted
    assert "url.set_fragment" in trusted
    assert "url.set_query(None)" in trusted
    assert "is_loopback_url(base)" in trusted
    assert "gw_trusted=" in trusted
    assert "verify_acl" in trusted
    assert "report.local" in main_source
    assert "trusted_attempt_for" in main_source
    assert "remote_failure_message" in main_source
    assert "window.location.replace(destination)" in ui
    assert "gw_trusted" not in ui


def test_release_workflow_builds_from_manifest_and_does_not_publish() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-desktop.yml").read_text(encoding="utf-8")
    assert "build_release.py" in workflow
    assert "--download-wheels" in workflow
    assert "--config" in workflow
    assert "--bundles nsis" in workflow
    assert "product_version" in workflow
    assert "release-evidence.json" in workflow
    assert "gh release" not in workflow
    assert re.search(r"(?m)^\s*contents:\s*write\s*$", workflow) is None


def test_unsigned_installer_notice_does_not_teach_bypassing_protection() -> None:
    readme = (DESKTOP / "README.md").read_text(encoding="utf-8")
    assert "Unsigned test builds" in readme
    assert "Do not disable Defender/SmartScreen" in readme
    assert "SHA-256" in readme
    assert "QIN-630" in readme


def _run_acl_verifier(auth_dir: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(PACKAGING / "verify_trusted_acl.py"), "--json", "--auth-dir", str(auth_dir)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )


def test_acl_verifier_fails_closed_without_leaking_paths(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-auth"
    result = _run_acl_verifier(missing)
    assert result.returncode != 0
    payload = json.loads(result.stdout.decode("utf-8"))
    assert payload["ok"] is False
    assert str(missing) not in result.stdout.decode("utf-8")
    assert "S-1-5-21" not in result.stdout.decode("utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 语义仅在 Windows 上验证")
def test_acl_verifier_accepts_restricted_and_rejects_broad_acl(tmp_path: Path) -> None:
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    user = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    principal = f"{domain}\\{user}" if domain else user
    restricted = subprocess.run(
        [
            "icacls",
            str(auth_dir),
            "/inheritance:r",
            "/grant:r",
            f"{principal}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
        ],
        capture_output=True,
        check=False,
    )
    assert restricted.returncode == 0, restricted.stdout + restricted.stderr
    accepted = _run_acl_verifier(auth_dir)
    assert accepted.returncode == 0, accepted.stdout.decode("utf-8", errors="replace")
    assert json.loads(accepted.stdout.decode("utf-8"))["ok"] is True

    broad = subprocess.run(
        ["icacls", str(auth_dir), "/grant", "*S-1-1-0:(OI)(CI)(RX)"],
        capture_output=True,
        check=False,
    )
    assert broad.returncode == 0, broad.stdout + broad.stderr
    rejected = _run_acl_verifier(auth_dir)
    assert rejected.returncode != 0
    payload = json.loads(rejected.stdout.decode("utf-8"))
    assert payload["ok"] is False
    assert "非受控访问主体" in payload["reason"]
    assert str(auth_dir) not in rejected.stdout.decode("utf-8")
