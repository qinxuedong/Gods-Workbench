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

"""本地账户与会话持久化；数据库放在当前系统用户的数据目录，不随源码提交。"""

from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

from gw.core.errors import CleanroomException
from gw.core.runtime_paths import RuntimePathError, resolve_runtime_paths

SESSION_SECONDS = 8 * 60 * 60
USERNAME = re.compile(r"[A-Za-z0-9_.-]{3,64}\Z")


def database_path() -> Path:
    """返回当前运行态已通过三根路径校验的本地账户库路径。"""
    try:
        return resolve_runtime_paths().auth_db
    except RuntimePathError as exc:
        raise RuntimeError(str(exc)) from None


@contextmanager
def database():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS local_users (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','governor','editor','reviewer','readonly'))
            );
            CREATE TABLE IF NOT EXISTS local_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES local_users(user_id) ON DELETE CASCADE,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS local_login_limits (
                client_key TEXT PRIMARY KEY,
                window_start REAL NOT NULL,
                attempts INTEGER NOT NULL
            );
        """)
        with connection:
            yield connection
    finally:
        connection.close()


def password_hash(password: str, salt: bytes) -> bytes:
    # 随机盐 + scrypt；哈希参数固定，不接受请求提供的低强度参数。
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=32768, r=8, p=3,
                          maxmem=64 * 1024 * 1024, dklen=32)


def setup_required() -> bool:
    with database() as db:
        return db.execute("SELECT 1 FROM local_users LIMIT 1").fetchone() is None


def _principal(row) -> dict:
    return {"user_id": row["user_id"], "username": row["username"],
            "display_name": row["username"], "role": row["role"], "groups": []}


def _new_session(db, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM local_sessions WHERE expires_at <= ?", (time.time(),))
    db.execute("INSERT INTO local_sessions VALUES (?, ?, ?)",
               (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + SESSION_SECONDS))
    return token


def create_first_admin(username: str, password: str):
    valid_password = (8 <= len(password) <= 128
                      and re.search(r"[A-Za-z]", password) is not None
                      and re.search(r"[0-9]", password) is not None)
    if not USERNAME.fullmatch(username) or not valid_password:
        raise CleanroomException(400, "LOCAL_ACCOUNT_INVALID", "账号需为3–64位字母、数字、点、短横线或下划线；密码需8–128个字符且同时包含字母和数字，不强制特殊符号")
    username = username.lower()
    # 提前拒绝已初始化的请求，真正的并发防线仍在事务内二次检查。
    if not setup_required():
        raise CleanroomException(409, "LOCAL_ACCOUNT_EXISTS", "管理员已经创建，请直接登录")
    salt = secrets.token_bytes(16)
    digest = password_hash(password, salt)
    user_id = "usr-" + secrets.token_hex(16)
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM local_users LIMIT 1").fetchone():
            raise CleanroomException(409, "LOCAL_ACCOUNT_EXISTS", "管理员已经创建，请直接登录")
        db.execute("INSERT INTO local_users VALUES (?, ?, ?, ?, 'admin')", (user_id, username, salt, digest))
        token = _new_session(db, user_id)
        row = db.execute("SELECT * FROM local_users WHERE user_id=?", (user_id,)).fetchone()
        return _principal(row), token


def login(username: str, password: str, client_key: str):
    now = time.time()
    # 限速状态入库，重启服务不能清除窗口；每客户端每分钟最多10次登录。
    limited = False
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM local_login_limits WHERE window_start < ?", (now - 60,))
        row = db.execute("SELECT attempts FROM local_login_limits WHERE client_key=?", (client_key,)).fetchone()
        if row and row["attempts"] >= 10:
            limited = True
        else:
            db.execute("INSERT INTO local_login_limits VALUES (?, ?, 1) ON CONFLICT(client_key) DO UPDATE SET attempts=attempts+1", (client_key, now))
    if limited:
        raise CleanroomException(429, "LOGIN_RATE_LIMITED", "登录尝试过于频繁，请一分钟后重试")
    if not USERNAME.fullmatch(username) or len(password) > 128:
        raise CleanroomException(401, "LOCAL_LOGIN_FAILED", "账号或密码错误")
    with database() as db:
        row = db.execute("SELECT * FROM local_users WHERE username=?", (username.lower(),)).fetchone()
    # 账户不存在仍执行同强度派生，避免通过快速返回枚举账户。
    salt = row["salt"] if row else b"\x00" * 16
    expected = row["password_hash"] if row else b"\x00" * 32
    valid = hmac.compare_digest(password_hash(password, salt), expected)
    if not row or not valid:
        raise CleanroomException(401, "LOCAL_LOGIN_FAILED", "账号或密码错误")
    with database() as db:
        token = _new_session(db, row["user_id"])
    return _principal(row), token


def get_session(token: str | None):
    if not token or len(token) > 128:
        return None
    with database() as db:
        row = db.execute("""SELECT u.* FROM local_sessions s JOIN local_users u USING(user_id)
                            WHERE s.token_hash=? AND s.expires_at > ?""",
                         (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return _principal(row) if row else None


def get_session_by_fingerprint(user_id: str, token_fingerprint: str):
    """Recheck a local-account session without retaining its Cookie token."""
    fingerprint = str(token_fingerprint or "").strip().lower()
    if (not user_id or len(fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in fingerprint)):
        return None
    with database() as db:
        row = db.execute(
            """SELECT u.* FROM local_sessions s JOIN local_users u USING(user_id)
               WHERE s.token_hash=? AND s.user_id=? AND s.expires_at>?""",
            (fingerprint, user_id, time.time()),
        ).fetchone()
        return _principal(row) if row else None


def delete_session(token: str | None) -> bool:
    if not token:
        return False
    with database() as db:
        return db.execute("DELETE FROM local_sessions WHERE token_hash=?",
                          (hashlib.sha256(token.encode()).hexdigest(),)).rowcount > 0


