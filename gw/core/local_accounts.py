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

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from gw.core.errors import CleanroomException
from gw.core.runtime_paths import RuntimePathError, resolve_runtime_paths

USERNAME = re.compile(r"[A-Za-z0-9_.-]{3,64}\Z")
RECOVERY_CODE = re.compile(r"RC-([0-9a-f]{8})-([A-Za-z0-9_-]{20,64})\Z")
RECOVERY_CODE_COUNT = 8
RECOVERY_LIMIT = 10
RECOVERY_WINDOW_SECONDS = 60

# 受信本机所有者的固定账号名：口令为随机高熵值、**永不展示给用户**，
# 只用于在既有本地账户体系里保留一个可被权限判定识别的所有者主体。
TRUSTED_LOCAL_OWNER_USERNAME = "local-owner"
# 可执行「认领 / 解绑」的管理角色（既有角色集，不新增角色）。
TRUSTED_LOCAL_CLAIM_ROLES = frozenset({"admin", "governor"})


def database_path() -> Path:
    """返回当前运行态已通过三根路径校验的本地账户库路径。"""
    try:
        return resolve_runtime_paths().auth_db
    except RuntimePathError as exc:
        raise RuntimeError(str(exc)) from None


@contextmanager
def database():
    try:
        runtime = resolve_runtime_paths()
    except RuntimePathError as exc:
        raise RuntimeError(str(exc)) from None
    path = runtime.auth_db
    # QIN-627：本机 CLI 等独立进程不得绕过服务持有的跨进程数据锁直接写库。
    # 互斥必须覆盖“检查锁 -> 打开库 -> 提交/回滚 -> 关闭连接”的完整事务：
    # 未持锁时先取得数据锁并持有到连接关闭，避免检查后另一进程抢锁。
    from gw.core.storage import (
        DataDirLockedError,
        acquire_data_dir_lock,
        is_data_dir_lock_held,
        release_data_dir_lock,
    )

    acquired_lock = False
    if not is_data_dir_lock_held(runtime.data_root):
        try:
            acquire_data_dir_lock(runtime.data_root)
        except DataDirLockedError as exc:
            raise CleanroomException(
                503,
                "DATA_LOCATION_QUIESCING",
                f"数据位置迁移或其他进程占用中（{exc.code}），本机账户写入已暂停；"
                "请等待迁移完成或停止服务后重试",
            ) from None
        acquired_lock = True

    connection = None
    try:
        from gw.core import trusted_local

        trusted_local.ensure_auth_dir_restricted(path.parent)
        # QIN-627 L3：只读窗口内以 mode=ro 打开，跳过建表与迁移，保证 GET 仍可认证。
        from gw.core.write_window import open_sqlite, writes_blocked

        read_only = writes_blocked()
        connection = open_sqlite(path, write=not read_only, timeout=10)
        connection.row_factory = sqlite3.Row
    except BaseException:
        try:
            if connection is not None:
                connection.close()
        finally:
            if acquired_lock:
                release_data_dir_lock(runtime.data_root)
        raise
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        if not read_only:
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
                expires_at REAL
            );
            CREATE TABLE IF NOT EXISTS local_login_limits (
                client_key TEXT PRIMARY KEY,
                window_start REAL NOT NULL,
                attempts INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS local_recovery_codes (
                user_id TEXT NOT NULL REFERENCES local_users(user_id) ON DELETE CASCADE,
                code_id TEXT NOT NULL,
                salt BLOB NOT NULL,
                code_hash BLOB NOT NULL,
                created_at REAL NOT NULL,
                used_at REAL,
                PRIMARY KEY(user_id, code_id)
            );
            CREATE TABLE IF NOT EXISTS local_recovery_limits (
                client_key TEXT PRIMARY KEY,
                window_start REAL NOT NULL,
                attempts INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oidc_sessions (
                token_hash TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                principal_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_oidc_sessions_subject
                ON oidc_sessions(subject);
            CREATE TABLE IF NOT EXISTS trusted_local_owners (
                owner_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES local_users(user_id) ON DELETE CASCADE,
                device_fingerprint TEXT NOT NULL,
                identity_domain TEXT NOT NULL DEFAULT 'local_account',
                claimed_by_user_id TEXT REFERENCES local_users(user_id) ON DELETE SET NULL,
                claimed_at REAL,
                claim_method TEXT,
                established_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_trusted_local_owners_device
                ON trusted_local_owners(device_fingerprint);
            CREATE TABLE IF NOT EXISTS trusted_local_bootstrap_tickets (
                jti TEXT PRIMARY KEY,
                device_fingerprint TEXT NOT NULL,
                issued_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                consumed_at REAL
            );
        """)
            expires_column = next(
                row
                for row in connection.execute("PRAGMA table_info(local_sessions)")
                if row["name"] == "expires_at"
            )
            if expires_column["notnull"]:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute("DROP TABLE IF EXISTS local_sessions_migration")
                    connection.execute(
                        """CREATE TABLE local_sessions_migration (
                            token_hash TEXT PRIMARY KEY,
                            user_id TEXT NOT NULL REFERENCES local_users(user_id) ON DELETE CASCADE,
                            expires_at REAL
                        )"""
                    )
                    connection.execute(
                        """INSERT INTO local_sessions_migration
                           (token_hash, user_id, expires_at)
                           SELECT token_hash, user_id,
                                  CASE WHEN expires_at > ? THEN NULL ELSE expires_at END
                           FROM local_sessions""",
                        (time.time(),),
                    )
                    connection.execute("DROP TABLE local_sessions")
                    connection.execute(
                        "ALTER TABLE local_sessions_migration RENAME TO local_sessions"
                    )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        with connection:
            yield connection
    finally:
        try:
            connection.close()
        finally:
            if acquired_lock:
                release_data_dir_lock(runtime.data_root)


def password_hash(password: str, salt: bytes) -> bytes:
    # 随机盐 + scrypt；哈希参数固定，不接受请求提供的低强度参数。
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=32768,
        r=8,
        p=3,
        maxmem=64 * 1024 * 1024,
        dklen=32,
    )


def _valid_password(password: str) -> bool:
    return (
        isinstance(password, str)
        and 8 <= len(password) <= 128
        and re.search(r"[A-Za-z]", password) is not None
        and re.search(r"[0-9]", password) is not None
    )


def _issue_recovery_codes(db, user_id: str) -> list[str]:
    now = time.time()
    db.execute(
        "UPDATE local_recovery_codes SET used_at=? WHERE user_id=? AND used_at IS NULL",
        (now, user_id),
    )
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        code_id = secrets.token_hex(4)
        secret = secrets.token_urlsafe(18)
        code = f"RC-{code_id}-{secret}"
        salt = secrets.token_bytes(16)
        db.execute(
            """INSERT INTO local_recovery_codes
               (user_id, code_id, salt, code_hash, created_at, used_at)
               VALUES (?, ?, ?, ?, ?, NULL)""",
            (user_id, code_id, salt, password_hash(secret, salt), now),
        )
        codes.append(code)
    return codes


def ensure_recovery_codes(user_id: str) -> list[str]:
    """为从未配置恢复码的旧账户首次登录补发一组；后续登录不再显示。"""
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        exists = db.execute(
            "SELECT 1 FROM local_users WHERE user_id=?", (user_id,)
        ).fetchone()
        if not exists:
            raise CleanroomException(404, "LOCAL_ACCOUNT_NOT_FOUND", "本地账户不存在")
        existing = db.execute(
            "SELECT 1 FROM local_recovery_codes WHERE user_id=? LIMIT 1", (user_id,)
        ).fetchone()
        return [] if existing else _issue_recovery_codes(db, user_id)


def list_accounts() -> list[str]:
    """返回可供本机恢复命令选择的账户名，不包含凭据。"""
    with database() as db:
        return [
            row["username"]
            for row in db.execute("SELECT username FROM local_users ORDER BY username")
        ]


def reset_password_local(username: str, new_password: str) -> list[str]:
    """本机命令专用重置入口：改密、撤销会话并重新签发恢复码。"""
    if not USERNAME.fullmatch(username) or not _valid_password(new_password):
        raise CleanroomException(
            400, "LOCAL_ACCOUNT_INVALID", "账号或新密码格式不符合要求"
        )
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT user_id FROM local_users WHERE username=?", (username.lower(),)
        ).fetchone()
        if not row:
            raise CleanroomException(404, "LOCAL_ACCOUNT_NOT_FOUND", "本地账户不存在")
        salt = secrets.token_bytes(16)
        db.execute(
            "UPDATE local_users SET salt=?, password_hash=? WHERE user_id=?",
            (salt, password_hash(new_password, salt), row["user_id"]),
        )
        db.execute("DELETE FROM local_sessions WHERE user_id=?", (row["user_id"],))
        return _issue_recovery_codes(db, row["user_id"])


def reset_password_with_recovery(
    username: str, recovery_code: str, new_password: str, client_key: str
) -> None:
    """验证并一次性消费恢复码，改密后在同一事务中撤销全部本地会话。"""
    if not _valid_password(new_password):
        raise CleanroomException(
            400,
            "LOCAL_ACCOUNT_INVALID",
            "新密码需8–128个字符且同时包含字母和数字",
        )

    now = time.time()
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "DELETE FROM local_recovery_limits WHERE window_start < ?",
            (now - RECOVERY_WINDOW_SECONDS,),
        )
        limit = db.execute(
            "SELECT window_start, attempts FROM local_recovery_limits WHERE client_key=?",
            (client_key,),
        ).fetchone()
        if limit and limit["attempts"] >= RECOVERY_LIMIT:
            rate_limited = True
        else:
            rate_limited = False
            db.execute(
                """INSERT INTO local_recovery_limits VALUES (?, ?, 1)
                   ON CONFLICT(client_key) DO UPDATE SET
                     window_start=CASE WHEN window_start < ? THEN excluded.window_start ELSE window_start END,
                     attempts=CASE WHEN window_start < ? THEN 1 ELSE attempts+1 END""",
                (
                    client_key,
                    now,
                    now - RECOVERY_WINDOW_SECONDS,
                    now - RECOVERY_WINDOW_SECONDS,
                ),
            )
        if not rate_limited:
            match = RECOVERY_CODE.fullmatch(recovery_code or "")
            normalized_username = username.lower() if USERNAME.fullmatch(username or "") else ""
            user = db.execute(
                "SELECT * FROM local_users WHERE username=?", (normalized_username,)
            ).fetchone()
            recovery = (
                db.execute(
                    """SELECT * FROM local_recovery_codes
                       WHERE user_id=? AND code_id=? AND used_at IS NULL""",
                    (user["user_id"], match.group(1)),
                ).fetchone()
                if user and match
                else None
            )
            salt = recovery["salt"] if recovery else b"\x00" * 16
            expected = recovery["code_hash"] if recovery else b"\x00" * 32
            secret = match.group(2) if match else "invalid-recovery-code"
            valid = hmac.compare_digest(password_hash(secret, salt), expected)
            if user and recovery and match and valid:
                password_salt = secrets.token_bytes(16)
                db.execute(
                    "UPDATE local_users SET salt=?, password_hash=? WHERE user_id=?",
                    (password_salt, password_hash(new_password, password_salt), user["user_id"]),
                )
                db.execute(
                    "UPDATE local_recovery_codes SET used_at=? WHERE user_id=? AND code_id=?",
                    (now, user["user_id"], recovery["code_id"]),
                )
                db.execute("DELETE FROM local_sessions WHERE user_id=?", (user["user_id"],))
                db.execute(
                    "DELETE FROM local_recovery_limits WHERE client_key=?", (client_key,)
                )
                return
    if rate_limited:
        raise CleanroomException(
            429, "RECOVERY_RATE_LIMITED", "恢复码尝试过于频繁，请一分钟后重试"
        )
    raise CleanroomException(401, "LOCAL_RECOVERY_FAILED", "账户或恢复码错误")


def regenerate_recovery_codes(user_id: str, current_password: str) -> list[str]:
    """核验当前密码后作废旧码并返回新的一组恢复码。"""
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        user = db.execute(
            "SELECT * FROM local_users WHERE user_id=?", (user_id,)
        ).fetchone()
        if not user or len(current_password) > 128 or not hmac.compare_digest(
            password_hash(current_password, user["salt"] if user else b"\x00" * 16),
            user["password_hash"] if user else b"\x00" * 32,
        ):
            raise CleanroomException(
                401, "LOCAL_ACCOUNT_CREDENTIALS_INVALID", "当前密码错误"
            )
        return _issue_recovery_codes(db, user_id)


def setup_required() -> bool:
    with database() as db:
        return db.execute("SELECT 1 FROM local_users LIMIT 1").fetchone() is None


def _principal(row) -> dict:
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "display_name": row["username"],
        "role": row["role"],
        "groups": [],
    }


def _new_session(db, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    db.execute(
        "DELETE FROM local_sessions WHERE expires_at IS NOT NULL AND expires_at <= ?",
        (time.time(),),
    )
    db.execute(
        "INSERT INTO local_sessions VALUES (?, ?, ?)",
        (
            hashlib.sha256(token.encode()).hexdigest(),
            user_id,
            None,
        ),
    )
    return token


def _insert_admin_row(db, username: str, password: str) -> str:
    """在**既有事务内**插入一个本地管理员账户并返回 user_id。

    供既有 ``create_first_admin`` 与受信本机所有者建立共用，保证两条路径
    写入的账户结构完全同构（同样的盐、哈希算法与角色约束）。
    """
    salt = secrets.token_bytes(16)
    digest = password_hash(password, salt)
    user_id = "usr-" + secrets.token_hex(16)
    db.execute(
        "INSERT INTO local_users VALUES (?, ?, ?, ?, 'admin')",
        (user_id, username.lower(), salt, digest),
    )
    return user_id


def create_first_admin(username: str, password: str):
    valid_password = (
        8 <= len(password) <= 128
        and re.search(r"[A-Za-z]", password) is not None
        and re.search(r"[0-9]", password) is not None
    )
    if not USERNAME.fullmatch(username) or not valid_password:
        raise CleanroomException(
            400,
            "LOCAL_ACCOUNT_INVALID",
            "账号需为3–64位字母、数字、点、短横线或下划线；密码需8–128个字符且同时包含字母和数字，不强制特殊符号",
        )
    username = username.lower()
    # 提前拒绝已初始化的请求，真正的并发防线仍在事务内二次检查。
    if not setup_required():
        raise CleanroomException(
            409, "LOCAL_ACCOUNT_EXISTS", "管理员已经创建，请直接登录"
        )
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM local_users LIMIT 1").fetchone():
            raise CleanroomException(
                409, "LOCAL_ACCOUNT_EXISTS", "管理员已经创建，请直接登录"
            )
        user_id = _insert_admin_row(db, username, password)
        token = _new_session(db, user_id)
        row = db.execute(
            "SELECT * FROM local_users WHERE user_id=?", (user_id,)
        ).fetchone()
        return _principal(row), token


# ---------------------------------------------------------------------------
# 受信本机所有者与一次性启动票据（持久化层）
# ---------------------------------------------------------------------------


def trusted_local_owner(db) -> dict | None:
    """读取当前受信本机绑定行；不存在时返回 None。"""
    row = db.execute(
        "SELECT * FROM trusted_local_owners ORDER BY established_at LIMIT 1"
    ).fetchone()
    return dict(row) if row is not None else None


def ensure_trusted_local_owner(
    db, device_fingerprint: str, *, now: float
) -> tuple[dict, bool, list[str]]:
    """在**既有事务内**建立或复用本机所有者（QIN-633 个人本机全权限直入）。

    - 已有绑定 → 幂等复用；设备指纹不一致时显式报错（密钥被换过）；
    - 本地账户表非空且无绑定：
      - 优先自动接管已有管理员（多账户优先 admin 且最早创建），写入 auto_takeover 绑定；
      - 若存在账户但无任何 admin 账户：按 PM 裁决选项 A 显式抛出 409
        TRUSTED_LOCAL_ADMIN_REQUIRED，绝不静默替用户猜测归属或绑定为 editor；
    - 本地账户表为空：
      - 原子建立本机管理员账户与绑定；
      - 生成 8 个恢复码（F1）并交付，供可信证明失效时密码重置恢复。

    返回 ``(principal, created, recovery_codes)``；``recovery_codes`` 仅在空库首启时包含恢复码。
    """
    binding = trusted_local_owner(db)
    if binding is not None:
        if str(binding["device_fingerprint"]) != str(device_fingerprint):
            raise CleanroomException(
                409,
                "TRUSTED_LOCAL_KEY_ROTATED",
                "设备密钥与既有本机绑定不一致，请先执行显式撤销与重新绑定",
            )
        row = db.execute(
            "SELECT * FROM local_users WHERE user_id=?", (binding["user_id"],)
        ).fetchone()
        if row is None:
            raise CleanroomException(
                409,
                "TRUSTED_LOCAL_OWNER_MISSING",
                "本机所有者账户已缺失，请执行显式恢复命令",
            )
        return _principal(row), False, []

    # 若本地已有账户但未绑定设备所有者：执行自动所有者接管（QIN-633 个人直入）
    has_any_user = db.execute("SELECT 1 FROM local_users LIMIT 1").fetchone()
    if has_any_user is not None:
        existing_admin = db.execute(
            "SELECT * FROM local_users WHERE role='admin' ORDER BY rowid ASC LIMIT 1"
        ).fetchone()
        if existing_admin is None:
            # 存在用户但无 admin，按 PM 裁决选项 A 显式拒绝，绝不静默猜归属
            raise CleanroomException(
                409,
                "TRUSTED_LOCAL_ADMIN_REQUIRED",
                "本地未找到管理员账户，无法自动接管所有者权限，请使用账户密码登录",
            )
        target_user_id = str(existing_admin["user_id"])
        owner_id = "tlo-" + secrets.token_hex(8)
        db.execute(
            "INSERT INTO trusted_local_owners "
            "(owner_id, user_id, device_fingerprint, identity_domain, "
            " claimed_by_user_id, claimed_at, claim_method, established_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                owner_id,
                target_user_id,
                str(device_fingerprint),
                "local_account",
                target_user_id,
                float(now),
                "auto_takeover",
                float(now),
            ),
        )
        return _principal(existing_admin), True, []

    user_id = _insert_admin_row(db, TRUSTED_LOCAL_OWNER_USERNAME, secrets.token_hex(32))
    recovery_codes = _issue_recovery_codes(db, user_id)
    owner_id = "tlo-" + secrets.token_hex(8)
    db.execute(
        "INSERT INTO trusted_local_owners "
        "(owner_id, user_id, device_fingerprint, identity_domain, "
        " claimed_by_user_id, claimed_at, claim_method, established_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            owner_id,
            user_id,
            str(device_fingerprint),
            "local_account",
            user_id,
            float(now),
            "bootstrap",
            float(now),
        ),
    )
    row = db.execute(
        "SELECT * FROM local_users WHERE user_id=?", (user_id,)
    ).fetchone()
    return _principal(row), True, recovery_codes


def issue_session_for_user(db, user_id: str) -> tuple[dict, str]:
    """在**既有事务内**为既有账户签发服务端会话。"""
    row = db.execute(
        "SELECT * FROM local_users WHERE user_id=?", (user_id,)
    ).fetchone()
    if row is None:
        raise CleanroomException(404, "LOCAL_ACCOUNT_NOT_FOUND", "本地账户不存在")
    token = _new_session(db, user_id)
    return _principal(row), token


def record_bootstrap_ticket(
    db, jti: str, device_fingerprint: str, issued_at: float, expires_at: float
) -> None:
    """登记一张已签发的启动票据（未消费）。"""
    db.execute(
        "INSERT INTO trusted_local_bootstrap_tickets "
        "(jti, device_fingerprint, issued_at, expires_at, consumed_at) "
        "VALUES (?, ?, ?, ?, NULL)",
        (str(jti), str(device_fingerprint), float(issued_at), float(expires_at)),
    )


def consume_bootstrap_ticket(db, jti: str, now: float) -> bool:
    """在**既有事务内**原子消费一张启动票据；成功返回 True。

    已消费、已过期或不存在的票据一律返回 False，由调用方失败关闭。
    """
    row = db.execute(
        "SELECT * FROM trusted_local_bootstrap_tickets WHERE jti=?", (str(jti),)
    ).fetchone()
    if row is None or row["consumed_at"] is not None:
        return False
    if float(row["expires_at"]) <= float(now):
        return False
    updated = db.execute(
        "UPDATE trusted_local_bootstrap_tickets SET consumed_at=? "
        "WHERE jti=? AND consumed_at IS NULL",
        (float(now), str(jti)),
    )
    return updated.rowcount > 0


def claim_trusted_local_owner(
    db,
    user_id: str,
    device_fingerprint: str,
    *,
    actor_user_id: str,
    method: str = "password_auth",
    now: float | None = None,
) -> dict:
    """在**既有事务内**把受信本机绑定到指定账户。

    调用方必须先完成原有 owner / 管理员认证；本函数只负责写入绑定与审计字段，
    不做任何身份判定，也不删除任何账户。
    """
    moment = time.time() if now is None else float(now)
    existing = trusted_local_owner(db)
    if existing is not None:
        if str(existing["user_id"]) == str(user_id):
            return existing
        raise CleanroomException(
            409,
            "TRUSTED_LOCAL_ALREADY_CLAIMED",
            "本机所有者已绑定到其它账户，请先执行解绑",
        )
    row = db.execute(
        "SELECT * FROM local_users WHERE user_id=?", (str(user_id),)
    ).fetchone()
    if row is None:
        raise CleanroomException(404, "LOCAL_ACCOUNT_NOT_FOUND", "本地账户不存在")
    owner_id = "tlo-" + secrets.token_hex(8)
    db.execute(
        "INSERT INTO trusted_local_owners "
        "(owner_id, user_id, device_fingerprint, identity_domain, "
        " claimed_by_user_id, claimed_at, claim_method, established_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            owner_id,
            str(user_id),
            str(device_fingerprint),
            "local_account",
            str(actor_user_id),
            moment,
            str(method),
            moment,
        ),
    )
    claimed = db.execute(
        "SELECT * FROM trusted_local_owners WHERE owner_id=?", (owner_id,)
    ).fetchone()
    return dict(claimed)


def unclaim_trusted_local_owner(db, user_id: str) -> bool:
    """在**既有事务内**解除本机绑定；不删除账户、不删除任何项目数据。"""
    existing = trusted_local_owner(db)
    if existing is None:
        return False
    if str(existing["user_id"]) != str(user_id):
        raise CleanroomException(
            409, "TRUSTED_LOCAL_CLAIM_MISMATCH", "本机绑定指向其它账户，未执行解绑"
        )
    db.execute(
        "DELETE FROM trusted_local_owners WHERE owner_id=?",
        (existing["owner_id"],),
    )
    return True


def verify_admin_password(username: str, password: str) -> dict:
    """校验既有本地账户口令并返回账户行。

    只读校验：**不**签发会话、**不**写入任何数据。口令错误与账户不存在
    返回同一错误，避免枚举账户。
    """
    if not USERNAME.fullmatch(username or "") or len(password or "") > 128:
        raise CleanroomException(
            401, "LOCAL_ACCOUNT_CREDENTIALS_INVALID", "账号或密码错误"
        )
    with database() as db:
        row = db.execute(
            "SELECT * FROM local_users WHERE username=?", (username.lower(),)
        ).fetchone()
    salt = row["salt"] if row else b"\x00" * 16
    expected = row["password_hash"] if row else b"\x00" * 32
    valid = hmac.compare_digest(password_hash(password or "", salt), expected)
    if not row or not valid:
        raise CleanroomException(
            401, "LOCAL_ACCOUNT_CREDENTIALS_INVALID", "账号或密码错误"
        )
    account = dict(row)
    if str(account.get("role") or "") not in TRUSTED_LOCAL_CLAIM_ROLES:
        raise CleanroomException(
            403,
            "TRUSTED_LOCAL_CLAIM_FORBIDDEN",
            "只有本机所有者或管理员可以执行该操作",
        )
    return account


def login(username: str, password: str, client_key: str):
    now = time.time()
    # 限速状态入库，重启服务不能清除窗口；每客户端每分钟最多10次登录。
    limited = False
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM local_login_limits WHERE window_start < ?", (now - 60,))
        row = db.execute(
            "SELECT attempts FROM local_login_limits WHERE client_key=?", (client_key,)
        ).fetchone()
        if row and row["attempts"] >= 10:
            limited = True
        else:
            db.execute(
                "INSERT INTO local_login_limits VALUES (?, ?, 1) ON CONFLICT(client_key) DO UPDATE SET attempts=attempts+1",
                (client_key, now),
            )
    if limited:
        raise CleanroomException(
            429, "LOGIN_RATE_LIMITED", "登录尝试过于频繁，请一分钟后重试"
        )
    if not USERNAME.fullmatch(username) or len(password) > 128:
        raise CleanroomException(401, "LOCAL_LOGIN_FAILED", "账号或密码错误")
    with database() as db:
        row = db.execute(
            "SELECT * FROM local_users WHERE username=?", (username.lower(),)
        ).fetchone()
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
        row = db.execute(
            """SELECT u.* FROM local_sessions s JOIN local_users u USING(user_id)
                            WHERE s.token_hash=?
                              AND (s.expires_at IS NULL OR s.expires_at > ?)""",
            (hashlib.sha256(token.encode()).hexdigest(), time.time()),
        ).fetchone()
        return _principal(row) if row else None


def get_session_by_fingerprint(user_id: str, token_fingerprint: str):
    """Recheck a local-account session without retaining its Cookie token."""
    fingerprint = str(token_fingerprint or "").strip().lower()
    if (
        not user_id
        or len(fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in fingerprint)
    ):
        return None
    with database() as db:
        row = db.execute(
            """SELECT u.* FROM local_sessions s JOIN local_users u USING(user_id)
               WHERE s.token_hash=? AND s.user_id=?
                 AND (s.expires_at IS NULL OR s.expires_at>?)""",
            (fingerprint, user_id, time.time()),
        ).fetchone()
        return _principal(row) if row else None


def delete_session(token: str | None) -> bool:
    if not token:
        return False
    with database() as db:
        return (
            db.execute(
                "DELETE FROM local_sessions WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).rowcount
            > 0
        )
