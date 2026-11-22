"""受信本机首启、一次性票据与权限边界回归（QIN-630 / GitHub #765）。"""

import ctypes
import ctypes.wintypes
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import app
from gw.core.errors import CleanroomException

PASSWORD = "Test2026"
BOOTSTRAP_PATH = "/api/asset-auth/local/trusted-bootstrap"


@pytest.fixture
def trusted_env(monkeypatch):
    """受信本机模式的隔离环境：本地账户模式 + 受信本机开关打开。"""
    from gw.api import routes_projects
    from gw.projects_hub.service import ProjectsService

    monkeypatch.setattr(routes_projects, "default_projects_service", ProjectsService())
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_TRUSTED_LOCAL", "1")
    yield


def make_client(base_url: str = "http://localhost", client_addr=("127.0.0.1", 50000)):
    """构造一个指定 base_url 与来源地址的测试客户端。"""
    return TestClient(
        app,
        base_url=base_url,
        client=client_addr,
        headers={"Origin": base_url},
    )


def issue_ticket():
    from gw.core import trusted_local

    return trusted_local.issue_bootstrap_ticket()


def auth_db_path() -> str:
    return os.environ["GW_LOCAL_AUTH_DB"]


def read_table(name: str) -> list[dict]:
    """直接读账户库，断言落盘事实（不依赖 HTTP 表述）。"""
    connection = sqlite3.connect(auth_db_path())
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(f"SELECT * FROM {name}").fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def test_bootstrap_establishes_owner_and_keeps_existing_permission_model(trusted_env):
    """首启：一次性票据兑换成本机所有者会话，权限判定一行未改。"""
    ticket = issue_ticket()
    with make_client() as client:
        response = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert response.status_code == 200, response.text
        assert "httponly" in response.headers["set-cookie"].lower()
        state = client.get("/api/asset-auth/status").json()
        assert state["authenticated"] is True
        assert state["principal"]["role"] == "admin"
        assert state["setup_required"] is False
        assert state["trusted_local"]["established"] is True
        assert state["trusted_local"]["mode"] == "trusted_local"
        # 既有业务写接口仍按既有角色判定放行
        assert (
            client.post(
                "/api/asset-registry/projects",
                json={"name": "本机所有者项目", "project_type": "film"},
            ).status_code
            == 201
        )
    owners = read_table("trusted_local_owners")
    assert len(owners) == 1
    assert owners[0]["device_fingerprint"]
    # 本机所有者是既有本地账户体系里的管理员，不新增角色
    assert [row["role"] for row in read_table("local_users")] == ["admin"]


def test_bootstrap_ticket_is_single_use(trusted_env):
    """同一张票据只能兑换一次；重放返回 401 且不新建所有者。"""
    ticket = issue_ticket()
    with make_client() as client:
        assert client.post(BOOTSTRAP_PATH, json={"ticket": ticket}).status_code == 200
        replay = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert replay.status_code == 401, replay.text
        assert replay.json()["detail"]["code"] == "TRUSTED_LOCAL_BOOTSTRAP_INVALID"
    assert len(read_table("trusted_local_owners")) == 1


def test_tampered_ticket_is_rejected_without_creating_accounts(trusted_env):
    """签名被改坏的票据一律拒绝，且不得留下任何账户。"""
    ticket = issue_ticket()
    body, signature = ticket.split(".", 1)
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    with make_client() as client:
        response = client.post(BOOTSTRAP_PATH, json={"ticket": f"{body}.{flipped}"})
        assert response.status_code == 401, response.text
        assert response.json()["detail"]["code"] == "TRUSTED_LOCAL_BOOTSTRAP_INVALID"
        state = client.get("/api/asset-auth/status").json()
        assert state["authenticated"] is False
        assert state["principal"] is None
    assert read_table("local_users") == []
    assert read_table("trusted_local_owners") == []


def test_existing_account_is_never_taken_over_and_ticket_survives_failure(trusted_env):
    """已有账户在 QIN-633 规则下自动接管为本地所有者并兑换票据直入。"""
    from gw.core import local_accounts, trusted_local

    with make_client() as client:
        created = client.post(
            "/api/asset-auth/local/setup", json={"username": "owner", "password": PASSWORD}
        )
        assert created.status_code == 201, created.text
    with local_accounts.database() as db:
        db.execute("BEGIN IMMEDIATE")
        principal, auto_created, recovery_codes = local_accounts.ensure_trusted_local_owner(
            db, trusted_local.device_key_fingerprint(), now=1.0
        )
    assert auto_created is True
    assert principal["username"] == "owner"
    assert recovery_codes == []
    assert [row["username"] for row in read_table("local_users")] == ["owner"]
    ticket = issue_ticket()
    with make_client() as client:
        redeemed = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert redeemed.status_code == 200, redeemed.text
        body = redeemed.json()
        assert body["authenticated"] is True
        assert body["principal"]["username"] == "owner"
        assert body["recovery_codes"] == []
        state = client.get("/api/asset-auth/status").json()
        assert state["authenticated"] is True
        assert state["trusted_local"]["needs_manual_claim"] is False
        assert state["trusted_local"]["established"] is True
    # 既有账户未被改动，也没有静默建立第二个用户
    assert [row["username"] for row in read_table("local_users")] == ["owner"]
    owners = read_table("trusted_local_owners")
    assert len(owners) == 1
    assert owners[0]["claim_method"] == "auto_takeover"


def test_claim_requires_admin_authentication_and_keeps_ticket_usable(
    trusted_env, monkeypatch
):
    """认领必须先过管理员认证；认证失败不改数据；票据保持可用。"""
    from gw.core import local_accounts_cli

    with make_client() as client:
        created = client.post(
            "/api/asset-auth/local/setup", json={"username": "owner", "password": PASSWORD}
        )
        assert created.status_code == 201, created.text
    # QIN-633: 已有账户时自动接管直入（特批方案 A 替换旧 409 行为断言）
    ticket_auto = issue_ticket()
    with make_client() as client:
        redeemed = client.post(BOOTSTRAP_PATH, json={"ticket": ticket_auto})
        assert redeemed.status_code == 200, redeemed.text
        assert redeemed.json()["authenticated"] is True
        assert redeemed.json()["principal"]["username"] == "owner"
        state = client.get("/api/asset-auth/status").json()
        assert state["authenticated"] is True
        assert state["trusted_local"]["established"] is True

    # 解绑后测试手动 trusted-claim：认证失败不改数据、原票据保持可用
    monkeypatch.setattr("getpass.getpass", lambda *args, **kwargs: PASSWORD)
    assert local_accounts_cli.main(["trusted-unclaim", "--username", "owner"]) == 0
    assert read_table("trusted_local_owners") == []

    # 在执行失败认领前签发票据 ticket
    ticket = issue_ticket()

    # 错误密码认证失败：返回 1，且未建立所有者绑定
    monkeypatch.setattr("getpass.getpass", lambda *args, **kwargs: "错误密码")
    assert local_accounts_cli.main(["trusted-claim", "--username", "owner"]) == 1
    assert read_table("trusted_local_owners") == []

    # 正确密码认证成功：建立 password_auth 绑定
    monkeypatch.setattr("getpass.getpass", lambda *args, **kwargs: PASSWORD)
    assert local_accounts_cli.main(["trusted-claim", "--username", "owner"]) == 0
    owners = read_table("trusted_local_owners")
    assert len(owners) == 1
    assert owners[0]["claim_method"] == "password_auth"

    # 核心安全断言：错误密码认领失败并未使此前签发的票据失效，该 ticket 依然可用并成功兑换
    with make_client() as client:
        redeemed = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert redeemed.status_code == 200, redeemed.text
        assert redeemed.json()["authenticated"] is True
        assert redeemed.json()["principal"]["username"] == "owner"
        state = client.get("/api/asset-auth/status").json()
        assert state["authenticated"] is True
        assert state["trusted_local"]["established"] is True


def test_unclaim_removes_binding_without_deleting_accounts(trusted_env, monkeypatch):
    """解绑只解除绑定，不删除账户与项目数据。"""
    from gw.core import local_accounts_cli

    with make_client() as client:
        created = client.post(
            "/api/asset-auth/local/setup", json={"username": "owner", "password": PASSWORD}
        )
        assert created.status_code == 201, created.text
    monkeypatch.setattr("getpass.getpass", lambda *args, **kwargs: PASSWORD)
    assert local_accounts_cli.main(["trusted-claim", "--username", "owner"]) == 0
    assert local_accounts_cli.main(["trusted-unclaim", "--username", "owner"]) == 0
    assert read_table("trusted_local_owners") == []
    assert [row["username"] for row in read_table("local_users")] == ["owner"]


@pytest.mark.parametrize(
    "host",
    [
        "",
        " ",
        "localhost",
        "example.invalid",
        "127.0.0.1.evil",
        "127.0.0.1:2077",
        "127.0.0.1@evil.invalid",
        "http://127.0.0.1",
        "127.1",
        "2130706433",
        "0x7f000001",
        "[::1]evil",
        "::ffff:127.0.0.1",
    ],
)
def test_trusted_open_rejects_nonliteral_or_spoofed_hosts_before_ticket_access(
    trusted_env, monkeypatch, capsys, host
):
    """远程/伪造 host 在读取或签发票据前被拒绝，且不落密钥或票据。"""
    from gw.core import local_accounts_cli, trusted_local

    accessed = []
    monkeypatch.setattr(trusted_local, "trusted_local_enabled", lambda: True)
    monkeypatch.setattr(
        trusted_local, "read_pending_ticket", lambda: accessed.append("read")
    )
    monkeypatch.setattr(
        trusted_local, "issue_bootstrap_ticket", lambda: accessed.append("issue")
    )

    assert local_accounts_cli.main(["trusted-open", "--host", host]) == 1
    output = capsys.readouterr().out
    assert "已拒绝" in output
    assert "eyJ" not in output
    assert accessed == []
    assert not trusted_local.device_key_path().exists()
    assert not trusted_local.pending_ticket_path().exists()


@pytest.mark.parametrize(
    ("host", "authority"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("127.5.6.7", "127.5.6.7"),
        ("::1", "[::1]"),
        ("[0:0:0:0:0:0:0:1]", "[::1]"),
    ],
)
def test_trusted_open_formats_only_loopback_ip_literals(
    trusted_env, monkeypatch, capsys, host, authority
):
    """受信本机链接只为回环 IP 字面量生成，IPv6 使用 URL 方括号。"""
    from gw.core import local_accounts_cli, trusted_local

    monkeypatch.setattr(trusted_local, "trusted_local_enabled", lambda: True)
    monkeypatch.setattr(trusted_local, "read_pending_ticket", lambda: "test-ticket")
    monkeypatch.setattr(
        trusted_local,
        "issue_bootstrap_ticket",
        lambda: (_ for _ in ()).throw(AssertionError("已有待用票据，不应重签")),
    )

    assert local_accounts_cli.main(["trusted-open", "--host", host]) == 0
    expected_url = f"http://{authority}:2077/static/pages/index.html#gw_trusted=test-ticket"
    assert expected_url in capsys.readouterr().out


@pytest.mark.parametrize("port", ["0", "-1", "65536"])
def test_trusted_open_rejects_invalid_ports_before_ticket_access(
    trusted_env, monkeypatch, capsys, port
):
    """无效端口在读取或签发票据前被拒绝。"""
    from gw.core import local_accounts_cli, trusted_local

    accessed = []
    monkeypatch.setattr(trusted_local, "trusted_local_enabled", lambda: True)
    monkeypatch.setattr(
        trusted_local, "read_pending_ticket", lambda: accessed.append("read")
    )
    monkeypatch.setattr(
        trusted_local, "issue_bootstrap_ticket", lambda: accessed.append("issue")
    )

    assert local_accounts_cli.main(["trusted-open", "--port", port]) == 1
    assert "端口无效" in capsys.readouterr().out
    assert accessed == []


def test_non_loopback_client_and_host_are_rejected(trusted_env, monkeypatch):
    """回环传输与回环 Host 是纵深防御：远程来源拿不到本机身份。"""
    ticket = issue_ticket()
    with make_client(client_addr=("10.0.0.5", 50000)) as remote_client:
        remote = remote_client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert remote.status_code == 403, remote.text
        assert remote.json()["detail"]["code"] == "TRUSTED_LOCAL_TRANSPORT_REJECTED"
    # 非回环 Host：即使通过既有 Host 白名单，本票纵深防御仍拒绝
    monkeypatch.setenv("GW_ALLOWED_HOSTS", "gw-remote.test")
    with make_client(base_url="http://gw-remote.test") as remote_host_client:
        remote_host = remote_host_client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert remote_host.status_code == 403, remote_host.text
        assert remote_host.json()["detail"]["code"] == "TRUSTED_LOCAL_TRANSPORT_REJECTED"
    # 白名单之外的 Host：既有主机门禁先行挡下，同样不会建立所有者
    with make_client(base_url="http://example.com") as unknown_host_client:
        unknown = unknown_host_client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert unknown.status_code == 400, unknown.text
    assert read_table("trusted_local_owners") == []
    # 票据未被远程尝试消费
    assert read_table("trusted_local_bootstrap_tickets")[0]["consumed_at"] is None


def test_remote_disconnect_write_fails_without_local_fallback(trusted_env, monkeypatch):
    """切到远程来源后业务写入返回 401，不回落写入本机项目库。"""
    ticket = issue_ticket()
    with make_client() as local_client:
        assert (
            local_client.post(BOOTSTRAP_PATH, json={"ticket": ticket}).status_code
            == 200
        )
        assert (
            local_client.post(
                "/api/asset-registry/projects",
                json={"name": "本机基线项目", "project_type": "film"},
            ).status_code
            == 201
        )

        monkeypatch.setenv("GW_ALLOWED_HOSTS", "gw-remote.test")
        with make_client(
            base_url="http://gw-remote.test", client_addr=("198.51.100.15", 50000)
        ) as remote_client:
            failed = remote_client.post(
                "/api/asset-registry/projects",
                json={"name": "远程断开写入", "project_type": "film"},
            )
            assert failed.status_code == 401, failed.text

        projects = local_client.get("/api/asset-registry/projects")
        assert projects.status_code == 200, projects.text
        assert "远程断开写入" not in projects.text
        assert "本机基线项目" in projects.text


def test_trusted_local_switch_is_only_a_feature_flag(trusted_env, monkeypatch):
    """开关关掉即拒绝；开关本身永远不是身份凭据。"""
    ticket = issue_ticket()
    monkeypatch.setenv("GW_TRUSTED_LOCAL", "0")
    with make_client() as client:
        response = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "TRUSTED_LOCAL_DISABLED"
    assert read_table("local_users") == []


def test_status_block_is_additive_and_never_leaks_secrets(trusted_env):
    """状态端点只增不改；不泄露票据、密钥、路径与主体名。"""
    ticket = issue_ticket()
    with make_client() as client:
        before = client.get("/api/asset-auth/status").json()
        assert before["trusted_local"]["enabled"] is True
        assert before["trusted_local"]["established"] is False
        assert before["trusted_local"]["mode"] == "uninitialized"
        assert before["setup_required"] is True
        assert client.post(BOOTSTRAP_PATH, json={"ticket": ticket}).status_code == 200
        after = client.get("/api/asset-auth/status").json()
    body = repr(after)
    assert ticket not in body
    assert "trusted_local.key" not in body
    # 受信本机块本身不携带主体名、票据、设备指纹或路径
    trusted_block = repr(after["trusted_local"])
    assert "local-owner" not in trusted_block
    assert "aud" not in trusted_block
    assert "jti" not in trusted_block


def test_unauthenticated_requests_still_fail_with_401(trusted_env):
    """本机自动进入不放松既有鉴权：未登录的业务写操作仍是 401。"""
    ticket = issue_ticket()
    with make_client() as client:
        assert (
            client.post(
                "/api/asset-registry/projects",
                json={"name": "未授权", "project_type": "film"},
                headers={"Authorization": "Bearer fabricated"},
            ).status_code
            == 401
        )
        assert client.post(BOOTSTRAP_PATH, json={"ticket": ticket}).status_code == 200
    # 换一个全新客户端（无 Cookie）仍然 401
    with make_client() as anonymous:
        assert (
            anonymous.post(
                "/api/asset-registry/projects",
                json={"name": "未授权", "project_type": "film"},
            ).status_code
            == 401
        )


def test_device_key_is_created_with_restricted_permissions(trusted_env):
    """设备密钥落在受控目录，权限不合格即失败关闭。"""
    from gw.core import trusted_local

    key_path = trusted_local.device_key_path()
    trusted_local.load_or_create_device_key()
    assert key_path.exists()
    assert key_path.name == "trusted_local.key"
    assert key_path.parent == Path(os.path.dirname(auth_db_path()))
    trusted_local.verify_file_permissions(key_path)
    assert len(key_path.read_bytes()) == trusted_local.DEVICE_KEY_BYTES


def test_secret_files_are_protected_before_payload_write(trusted_env, tmp_path, monkeypatch):
    """ACL/权限失败时秘密尚未写入，设备密钥和待用票据均不留文件。"""
    from gw.core import trusted_local

    original_restrict = trusted_local.restrict_file_permissions
    observed = []

    def observe_before_write(path):
        if path.suffix == ".tmp" or path.name == trusted_local.DEVICE_KEY_FILENAME:
            assert path.stat().st_size == 0
            observed.append(path.name)
        original_restrict(path)
        trusted_local.verify_file_permissions(path)

    monkeypatch.setattr(trusted_local, "restrict_file_permissions", observe_before_write)
    key_path = trusted_local.device_key_path()
    trusted_local.load_or_create_device_key()
    pending_path = tmp_path / "trusted_local.pending"
    trusted_local._write_restricted_file(pending_path, b"one-time-ticket")
    assert key_path.read_bytes()
    assert pending_path.read_bytes() == b"one-time-ticket"
    assert len(observed) == 2


def test_acl_setup_failure_aborts_without_writing_secret(trusted_env, tmp_path, monkeypatch):
    """无法建立受限权限时，设备密钥与票据临时文件都不会包含秘密。"""
    from gw.core import trusted_local

    def fail_restrict(path):
        assert path.stat().st_size == 0
        raise CleanroomException(
            500,
            "TRUSTED_LOCAL_KEY_PERMISSIONS_UNSAFE",
            "受限权限设置失败",
        )

    key_path = trusted_local.device_key_path()
    monkeypatch.setattr(trusted_local, "restrict_file_permissions", fail_restrict)
    with pytest.raises(CleanroomException) as key_error:
        trusted_local.load_or_create_device_key()
    assert key_error.value.code == "TRUSTED_LOCAL_KEY_PERMISSIONS_UNSAFE"
    assert not key_path.exists()

    pending_path = tmp_path / "trusted_local.pending"
    with pytest.raises(CleanroomException) as ticket_error:
        trusted_local._write_restricted_file(pending_path, b"one-time-ticket")
    assert ticket_error.value.code == "TRUSTED_LOCAL_KEY_PERMISSIONS_UNSAFE"
    assert not pending_path.exists()
    assert list(tmp_path.glob("trusted_local.pending.*.tmp")) == []


def _restricted_token_can_read(path: Path, restricting_sid: str) -> bool:
    """用仅受指定 SID 限制的 Windows 令牌实际尝试读取文件。"""
    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", ctypes.wintypes.DWORD)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    process_token = ctypes.wintypes.HANDLE()
    restricted_token = ctypes.wintypes.HANDLE()
    impersonation_token = ctypes.wintypes.HANDLE()
    sid = ctypes.c_void_p()

    def check(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    advapi32.ConvertStringSidToSidW.argtypes = [
        ctypes.wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.ConvertStringSidToSidW.restype = ctypes.wintypes.BOOL
    check(advapi32.ConvertStringSidToSidW(restricting_sid, ctypes.byref(sid)))
    try:
        advapi32.OpenProcessToken.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.DWORD,
            ctypes.POINTER(ctypes.wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = ctypes.wintypes.BOOL
        check(advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), 0x0002 | 0x0008, ctypes.byref(process_token)
        ))
        restricted_sid = SidAndAttributes(sid, 0)
        advapi32.CreateRestrictedToken.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
            ctypes.POINTER(SidAndAttributes),
            ctypes.POINTER(ctypes.wintypes.HANDLE),
        ]
        advapi32.CreateRestrictedToken.restype = ctypes.wintypes.BOOL
        check(advapi32.CreateRestrictedToken(
            process_token,
            0x00000001,
            0,
            None,
            0,
            None,
            1,
            ctypes.byref(restricted_sid),
            ctypes.byref(restricted_token),
        ))
        advapi32.DuplicateTokenEx.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.DWORD,
            ctypes.POINTER(ctypes.wintypes.HANDLE),
        ]
        advapi32.DuplicateTokenEx.restype = ctypes.wintypes.BOOL
        check(advapi32.DuplicateTokenEx(
            restricted_token,
            0x0008 | 0x0004,
            None,
            2,
            2,
            ctypes.byref(impersonation_token),
        ))
        advapi32.ImpersonateLoggedOnUser.argtypes = [ctypes.wintypes.HANDLE]
        advapi32.ImpersonateLoggedOnUser.restype = ctypes.wintypes.BOOL
        check(advapi32.ImpersonateLoggedOnUser(impersonation_token))
        try:
            path.read_bytes()
            return True
        except PermissionError:
            return False
        finally:
            advapi32.RevertToSelf.restype = ctypes.wintypes.BOOL
            advapi32.RevertToSelf()
    finally:
        for handle in (impersonation_token, restricted_token, process_token):
            if handle:
                kernel32.CloseHandle(handle)
        if sid:
            kernel32.LocalFree(sid)


@pytest.mark.skipif(os.name != "nt", reason="Windows NTFS ACL 验收")
def test_windows_secret_file_drops_permissive_inherited_parent_acl(
    trusted_env, tmp_path, monkeypatch
):
    """宽松父目录下先观察到继承权限，再确认秘密文件切断继承并拒绝 Users。"""
    from gw.core import trusted_local

    users_sid = "S-1-5-32-545"
    grant = subprocess.run(
        [
            "icacls",
            str(tmp_path),
            "/grant",
            f"*{users_sid}:(OI)(CI)(M)",
            "/c",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert grant.returncode == 0, grant.stderr or grant.stdout

    original_restrict = trusted_local.restrict_file_permissions
    observed_inherited_users = []

    def restrict_after_recording_parent_acl(path):
        if path.suffix == ".tmp":
            assert path.stat().st_size == 0
            inherited_sids, inheritance_broken = trusted_local._windows_dacl_sids(path)
            observed_inherited_users.append(users_sid in inherited_sids and not inheritance_broken)
        original_restrict(path)

    monkeypatch.setattr(
        trusted_local, "restrict_file_permissions", restrict_after_recording_parent_acl
    )
    secret_path = tmp_path / "trusted_local.pending"
    trusted_local._write_restricted_file(secret_path, b"secret")

    trustees, inheritance_broken = trusted_local._windows_dacl_sids(secret_path)
    assert observed_inherited_users == [True]
    assert users_sid not in trustees
    assert inheritance_broken is True
    trusted_local.verify_file_permissions(secret_path)
    assert not _restricted_token_can_read(secret_path, users_sid)


def test_missing_device_key_fails_closed_without_rotating_trust_root(trusted_env):
    """设备密钥缺失时显式报错，既不兑换、也不静默换一把新密钥（AC8）。"""
    from gw.core import trusted_local

    ticket = issue_ticket()
    key_path = trusted_local.device_key_path()
    original = key_path.read_bytes()
    key_path.unlink()
    with make_client() as client:
        response = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert response.status_code == 500, response.text
        assert response.json()["detail"]["code"] == "TRUSTED_LOCAL_KEY_MISSING"
        state = client.get("/api/asset-auth/status").json()
        assert state["authenticated"] is False
    # 关键：校验路径不得悄悄创建新密钥
    assert not key_path.exists()
    assert read_table("local_users") == []
    assert read_table("trusted_local_owners") == []
    # 恢复密钥后，同一张票据仍可正常兑换（票据未被消费）
    key_path.write_bytes(original)
    trusted_local.restrict_file_permissions(key_path)
    with make_client() as client:
        assert client.post(BOOTSTRAP_PATH, json={"ticket": ticket}).status_code == 200


def test_existing_owner_blocks_new_ticket_when_device_key_is_lost(trusted_env):
    """已建立绑定且密钥丢失时拒绝签发新票据，不静默轮换信任根。"""
    from gw.core import trusted_local

    ticket = issue_ticket()
    with make_client() as client:
        assert client.post(BOOTSTRAP_PATH, json={"ticket": ticket}).status_code == 200
    key_path = trusted_local.device_key_path()
    key_path.unlink()
    try:
        trusted_local.issue_bootstrap_ticket()
    except CleanroomException as exc:
        assert exc.code == "TRUSTED_LOCAL_KEY_MISSING"
    else:
        raise AssertionError("设备密钥丢失后仍允许签发新票据")
    assert not key_path.exists()
    # 既有绑定不受影响
    assert len(read_table("trusted_local_owners")) == 1
