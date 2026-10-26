"""持久化 OIDC 会话回归测试，替代旧的过期时间契约。

工单 Cut 明确移除会话空闲过期与绝对过期；本文件保留原回归测试并验证新契约。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gw.core import local_accounts  # noqa: E402
from gw.core import session as session_store  # noqa: E402


class _FakeClock:
    """可注入时钟，用于验证 OIDC 会话不会因时间流逝而过期。"""

    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    fake = _FakeClock()
    monkeypatch.setattr(session_store, "time", fake)
    session_store.reset_stores()
    yield fake
    session_store.reset_stores()


def test_absolute_cap_no_longer_terminates_persistent_session(clock):
    """频繁访问的会话越过原 24 小时上限后仍然有效。"""
    session_id = session_store.create_session({"username": "u", "role": "editor"})
    for _ in range(4):
        clock.advance(12 * 60 * 60)
        assert session_store.get_session(session_id) is not None


def test_idle_session_survives_beyond_former_sliding_window(clock):
    """会话即使一年没有访问也保持有效，无需读取续期。"""
    session_id = session_store.create_session({"username": "u", "role": "editor"})
    clock.advance(365 * 24 * 60 * 60)
    assert session_store.get_session(session_id) == {
        "username": "u",
        "role": "editor",
    }


def test_persistent_session_still_resolves_trusted_fingerprint(clock):
    """基于指纹的重新授权仍能取回已存储的主体信息。"""
    principal = {"username": "u", "role": "editor"}
    session_id = session_store.create_session(principal)
    clock.advance(2 * 24 * 60 * 60)
    fingerprint = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    assert session_store.get_session_by_fingerprint(fingerprint, "u") == principal


def test_prune_does_not_remove_session_past_former_absolute_cap(clock):
    """创建新会话时，不会因会话年龄清理仍有效的持久化会话。"""
    session_id = session_store.create_session({"username": "u", "role": "editor"})
    clock.advance(2 * 24 * 60 * 60)
    session_store.create_session({"username": "other", "role": "editor"})
    assert session_store.get_session(session_id) is not None


def test_session_token_is_stored_as_fingerprint_only(clock):
    """SQLite 只保存主体信息和令牌哈希，不保存 Cookie 原令牌。"""
    session_id = session_store.create_session({"username": "u", "role": "editor"})
    auth_db = Path(os.environ["GW_LOCAL_AUTH_DB"])
    with auth_db.open("rb") as database_file:
        raw = database_file.read()
    assert session_id.encode("ascii") not in raw
    assert hashlib.sha256(session_id.encode("utf-8")).hexdigest().encode("ascii") in raw
    with local_accounts.database() as db:
        stored = db.execute(
            "SELECT principal_json FROM oidc_sessions WHERE token_hash=?",
            (hashlib.sha256(session_id.encode("utf-8")).hexdigest(),),
        ).fetchone()
    assert json.loads(stored["principal_json"]) == {"username": "u", "role": "editor"}


def test_normal_persistent_session_resolves_without_expiry_metadata(clock):
    """新建的普通会话立即可用，数据库不再依赖过期元数据。"""
    principal = {"username": "u", "role": "editor"}
    session_id = session_store.create_session(principal)
    assert session_store.get_session(session_id) == principal


def test_delete_revokes_persistent_session(clock):
    """服务端主动撤销会立即使持久化会话失效。"""
    session_id = session_store.create_session({"username": "u", "role": "editor"})
    clock.advance(2 * 24 * 60 * 60)
    assert session_store.delete_session(session_id) is True
    assert session_store.get_session(session_id) is None
