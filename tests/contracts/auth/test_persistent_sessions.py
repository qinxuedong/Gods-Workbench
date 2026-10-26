"""持久会话契约：OIDC 会话跨进程重启且不因空闲过期。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gw.asset_registry import index_jobs
from gw.core import local_accounts
from gw.core import session as session_store
from gw.core.errors import CleanroomException

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_oidc_session_survives_process_restart_without_idle_expiry(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    auth_db = tmp_path / "auth" / "auth.sqlite3"
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(auth_db))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    session_store.reset_stores()
    try:
        principal = {
            "username": "restart-user",
            "role": "editor",
            "groups": ["gw-editor"],
            "issuer": "https://idp.example.test",
            "config_generation": 7,
        }
        monkeypatch.setattr(
            session_store, "time", SimpleNamespace(time=lambda: 1_000_000.0)
        )
        session_id = session_store.create_session(principal)

        env = os.environ.copy()
        env["GW_TEST_SESSION_ID"] = session_id
        env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        child_code = (
            "import json, os\n"
            "from types import SimpleNamespace\n"
            "from gw.core import session as session_store\n"
            "session_store.time = SimpleNamespace(time=lambda: 1_158_760_000.0)\n"
            "principal = session_store.get_session(os.environ['GW_TEST_SESSION_ID'])\n"
            "print(json.dumps(principal, sort_keys=True))\n"
        )
        child = subprocess.run(  # noqa: S603 - 仅启动本测试的隔离子进程
            [sys.executable, "-c", child_code],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == json.dumps(principal, sort_keys=True)
    finally:
        session_store.reset_stores()


def test_local_account_session_survives_extended_idle(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    clock = SimpleNamespace(time=lambda: 1_000_000.0)
    monkeypatch.setattr(local_accounts, "time", clock)
    with local_accounts.database() as db:
        db.execute(
            "INSERT INTO local_users VALUES (?, ?, ?, ?, 'editor')",
            ("usr-persistent", "persistent-user", b"salt", b"hash"),
        )
        token = local_accounts._new_session(db, "usr-persistent")

    clock.time = lambda: 1_000_000.0 + 365 * 24 * 60 * 60
    principal = local_accounts.get_session(token)
    assert principal is not None
    assert principal["username"] == "persistent-user"


def test_legacy_local_sessions_migrate_without_revoking_active_sessions(
    monkeypatch, tmp_path
):
    import hashlib
    import sqlite3

    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    auth_db = tmp_path / "auth" / "auth.sqlite3"
    auth_db.parent.mkdir(parents=True)
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(auth_db))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    clock = SimpleNamespace(time=lambda: 1_000_000.0)
    monkeypatch.setattr(local_accounts, "time", clock)
    active_session = "active-legacy-session"
    expired_session = "expired-legacy-session"
    with sqlite3.connect(auth_db) as db:
        db.executescript(
            """
            CREATE TABLE local_users (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','governor','editor','reviewer','readonly'))
            );
            CREATE TABLE local_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES local_users(user_id) ON DELETE CASCADE,
                expires_at REAL NOT NULL
            );
            """
        )
        db.execute(
            "INSERT INTO local_users VALUES (?, ?, ?, ?, 'editor')",
            ("usr-legacy", "legacy-user", b"salt", b"hash"),
        )
        db.executemany(
            "INSERT INTO local_sessions VALUES (?, ?, ?)",
            [
                (
                    hashlib.sha256(active_session.encode()).hexdigest(),
                    "usr-legacy",
                    1_000_100.0,
                ),
                (
                    hashlib.sha256(expired_session.encode()).hexdigest(),
                    "usr-legacy",
                    999_999.0,
                ),
            ],
        )

    assert local_accounts.get_session(active_session)["username"] == "legacy-user"
    assert local_accounts.get_session(expired_session) is None
    with local_accounts.database() as db:
        expires_column = next(
            row for row in db.execute("PRAGMA table_info(local_sessions)")
            if row["name"] == "expires_at"
        )
        assert expires_column["notnull"] == 0


def test_local_index_job_admission_accepts_unexpired_forever_session(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setattr(
        index_jobs,
        "load_runtime_auth_config",
        lambda: SimpleNamespace(mode="local_account"),
    )
    with local_accounts.database() as db:
        db.execute(
            "INSERT INTO local_users VALUES (?, ?, ?, ?, 'editor')",
            ("usr-index", "index-user", b"salt", b"hash"),
        )
        token = local_accounts._new_session(db, "usr-index")

    actor = {
        "subject": "usr-index",
        "mode": "local_account",
        "domain": "local_account",
        "role": "editor",
    }
    index_jobs._admission(actor)
    assert local_accounts.get_session(token)["user_id"] == "usr-index"
    assert local_accounts.delete_session(token) is True
    with pytest.raises(CleanroomException) as revoked:
        index_jobs._admission(actor)
    assert revoked.value.code == "INDEX_AUTH_REVOKED"


def test_oidc_index_job_admission_uses_persistent_sessions(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    session_store.reset_stores()
    issuer = "https://idp.example.test"
    monkeypatch.setattr(
        index_jobs,
        "load_runtime_auth_config",
        lambda: SimpleNamespace(
            mode="oidc", oidc=SimpleNamespace(issuer=issuer)
        ),
    )
    principal = {"username": "index-user", "role": "editor", "issuer": issuer}
    session_id = session_store.create_session(principal)
    actor = {
        "subject": "index-user",
        "mode": "oidc",
        "domain": f"oidc:{issuer}",
        "role": "editor",
    }

    index_jobs._admission(actor)
    assert session_store.delete_session(session_id) is True
    with pytest.raises(CleanroomException) as revoked:
        index_jobs._admission(actor)
    assert revoked.value.code == "INDEX_SESSION_REQUIRED"
