"""QIN-371 启动认证模式与监听地址安全回归。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

import run
from gw.core import auth, storage

ROOT = Path(__file__).resolve().parents[2]
ALL_INTERFACES_HOST = "0.0.0.0"  # noqa: S104 - 测试需覆盖全接口绑定被拒绝


def _validator():
    validator = getattr(auth, "validate_auth_mode_host", None)
    assert callable(validator), "认证模块应提供启动期 auth mode / bind host 校验"
    return validator


@pytest.mark.parametrize(
    "host", [ALL_INTERFACES_HOST, "::", "192.168.0.10", "workstation.lan"]
)
def test_local_auth_rejects_non_loopback_hosts(host: str):
    with pytest.raises(ValueError, match="GW_AUTH_MODE=local"):
        _validator()("local", host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "LOCALHOST."])
def test_local_auth_allows_loopback_hosts(host: str):
    _validator()("local", host)


def test_non_local_auth_allows_non_loopback_host():
    _validator()("oidc", ALL_INTERFACES_HOST)


def test_run_entry_rejects_local_auth_before_importing_app(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_HOST", ALL_INTERFACES_HOST)
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setattr(storage, "acquire_data_dir_lock", lambda _root: object())
    monkeypatch.setattr(run.uvicorn, "run", lambda *_args, **_kwargs: None)
    fake_app = ModuleType("gw.api.app")
    fake_app.app = object()
    monkeypatch.setitem(sys.modules, "gw.api.app", fake_app)

    with pytest.raises(SystemExit) as raised:
        run.main(["--test"])

    assert raised.value.code == 2
    assert "GW_AUTH_MODE=local" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("auth_mode", "host"),
    [
        ("local", "127.0.0.1"),
        ("local", "::1"),
        ("local", "localhost"),
        ("oidc", ALL_INTERFACES_HOST),
    ],
)
def test_run_entry_accepts_safe_auth_bind_pairs(
    auth_mode: str, host: str, tmp_path, monkeypatch
):
    monkeypatch.setenv("GW_AUTH_MODE", auth_mode)
    monkeypatch.setenv("GW_HOST", host)
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setattr(storage, "acquire_data_dir_lock", lambda _root: object())
    calls = []
    monkeypatch.setattr(
        run.uvicorn,
        "run",
        lambda _app, **kwargs: calls.append(kwargs),
    )
    fake_app = ModuleType("gw.api.app")
    fake_app.app = object()
    monkeypatch.setitem(sys.modules, "gw.api.app", fake_app)

    assert run.main(["--test"]) == 0
    assert calls[0]["host"] == host


def test_direct_app_import_rejects_local_auth_with_non_loopback_host(tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GW_")
    }
    env.update(
        {
            "GW_AUTH_MODE": "local",
            "GW_HOST": ALL_INTERFACES_HOST,
            "GW_RUNTIME_MODE": "test",
            "GW_DATA_DIR": str(tmp_path / "data"),
            "GW_LOCAL_AUTH_DB": str(tmp_path / "auth.sqlite3"),
            "GW_VIDEO_DATA_DIR": str(tmp_path / "video"),
            "GW_DISABLE_AGENT_INTEGRATION": "1",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", "import gw.api.app"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode != 0
    assert "GW_AUTH_MODE=local" in result.stderr


@pytest.mark.parametrize(
    ("auth_mode", "host"),
    [
        ("local", "127.0.0.1"),
        ("local", "::1"),
        ("local", "localhost"),
        ("oidc", ALL_INTERFACES_HOST),
    ],
)
def test_direct_app_import_accepts_safe_auth_bind_pairs(
    auth_mode: str, host: str, tmp_path
):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GW_")
    }
    env.update(
        {
            "GW_AUTH_MODE": auth_mode,
            "GW_HOST": host,
            "GW_RUNTIME_MODE": "test",
            "GW_DATA_DIR": str(tmp_path / "data"),
            "GW_LOCAL_AUTH_DB": str(tmp_path / "auth.sqlite3"),
            "GW_VIDEO_DATA_DIR": str(tmp_path / "video"),
            "GW_DISABLE_AGENT_INTEGRATION": "1",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", "import gw.api.app"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_docker_defaults_explicitly_require_non_local_auth():
    dockerfile = (ROOT / "deploy" / "docker" / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "deploy" / "docker" / "docker-compose.nas.yml").read_text(encoding="utf-8")
    env_example = (ROOT / "deploy" / "docker" / ".env.example").read_text(encoding="utf-8")

    assert "GW_AUTH_MODE=oidc" in dockerfile
    assert 'GW_AUTH_MODE: "${GW_AUTH_MODE:-oidc}"' in compose
    assert re.search(r"(?m)^GW_AUTH_MODE=local_account\s*$", env_example)


def test_readme_says_local_auth_mode_is_development_only():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "GW_AUTH_MODE=local" in readme
    assert "仅限开发" in readme or "仅用于开发" in readme
