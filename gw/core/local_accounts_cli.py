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

"""本机本地账户初始化与恢复命令；不启动服务或调用网络接口。"""

import argparse
import getpass
import os
import time

from gw.core import local_accounts
from gw.core.errors import CleanroomException


def _apply_runtime_mode(mode: str | None) -> None:
    """把命令行指定的运行态写进环境，供运行路径解析使用。

    不指定时沿用既有行为（读 ``GW_RUNTIME_MODE``，缺省为开发态）。
    """
    if mode:
        os.environ["GW_RUNTIME_MODE"] = mode


def _trusted_open(args) -> int:
    """打印一次性本机进入链接（受信本机模式）。

    票据放在 URL **片段**里：不会随请求发给服务端，也不进 Referer；
    浏览器兑换后由前端抹除片段。
    """
    from gw.core import trusted_local

    if not trusted_local.trusted_local_enabled():
        print("受信本机模式未启用（GW_TRUSTED_LOCAL=0 或当前不是本地账户模式）。")
        return 1
    try:
        ticket = trusted_local.read_pending_ticket()
        if not ticket:
            ticket = trusted_local.issue_bootstrap_ticket()
    except CleanroomException as exc:
        print(exc.message)
        return 1
    host = args.host or "127.0.0.1"
    port = args.port or int(os.environ.get("GW_PORT") or "2077")
    print("请在本机浏览器打开以下一次性链接（票据短时有效，用过即失效）：")
    print(f"http://{host}:{port}/static/pages/index.html#gw_trusted={ticket}")
    return 0


def _authenticate_claimant(username: str) -> dict:
    """认领前的强制认证：校验既有 owner / 管理员口令。

    只读校验，**不**签发会话、**不**写入任何数据；失败时调用方必须原样退出。
    """
    password = getpass.getpass("该账户密码（不回显）：")
    return local_accounts.verify_admin_password(username, password)


def _trusted_claim(username: str) -> int:
    """把受信本机身份显式认领到既有账户（默认拒绝自动接管）。

    必须先通过原有 owner / 管理员认证；认证失败**不改动任何账户数据**。
    """
    from gw.core import audit as audit_log
    from gw.core import trusted_local

    try:
        account = _authenticate_claimant(username)
        fingerprint = trusted_local.device_key_fingerprint()
        with local_accounts.database() as db:
            db.execute("BEGIN IMMEDIATE")
            binding = local_accounts.claim_trusted_local_owner(
                db,
                account["user_id"],
                fingerprint,
                actor_user_id=account["user_id"],
                method="password_auth",
                now=time.time(),
            )
        audit_log.record_management_event(
            audit_log.EVENT_USER_UPDATED,
            outcome=audit_log.OUTCOME_SUCCEEDED,
            reason="trusted_local_claim",
            subject=account["user_id"],
            role=account["role"],
            auth_mode="local_account",
            resource_id=binding["owner_id"],
        )
    except CleanroomException as exc:
        print(exc.message)
        return 1
    print("已把本机所有者身份认领到该账户；可随时用 trusted-unclaim 回退。")
    return 0


def _trusted_unclaim(username: str) -> int:
    """解除本机绑定（需同样通过 owner / 管理员认证）。

    只解除绑定，**不删除**账户、不删除任何项目数据。
    """
    from gw.core import audit as audit_log

    try:
        account = _authenticate_claimant(username)
        with local_accounts.database() as db:
            db.execute("BEGIN IMMEDIATE")
            removed = local_accounts.unclaim_trusted_local_owner(db, account["user_id"])
        if not removed:
            print("当前没有已绑定的本机所有者身份，无需解绑。")
            return 0
        audit_log.record_management_event(
            audit_log.EVENT_USER_UPDATED,
            outcome=audit_log.OUTCOME_SUCCEEDED,
            reason="trusted_local_unclaim",
            subject=account["user_id"],
            role=account["role"],
            auth_mode="local_account",
        )
    except CleanroomException as exc:
        print(exc.message)
        return 1
    print("已解除本机所有者绑定；账户与项目数据均未改动。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本机本地账户管理")
    parser.add_argument(
        "--mode",
        choices=["dev", "prod", "test"],
        help="运行态；须与正在运行的服务一致（缺省读 GW_RUNTIME_MODE）",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-admin", help="交互式创建首位本地管理员")
    commands.add_parser("reset-password", help="重置本地账户密码并重发恢复码")
    trusted_open = commands.add_parser(
        "trusted-open", help="打印一次性本机进入链接（受信本机模式）"
    )
    trusted_open.add_argument("--host", help="本机访问主机；缺省 127.0.0.1")
    trusted_open.add_argument(
        "--port", type=int, help="本机服务端口；缺省 GW_PORT 或 2077"
    )
    trusted_claim = commands.add_parser(
        "trusted-claim", help="管理员认证后把本机身份认领到既有账户"
    )
    trusted_claim.add_argument("--username", help="既有 owner / 管理员账号名")
    trusted_unclaim = commands.add_parser(
        "trusted-unclaim", help="管理员认证后解除本机身份绑定"
    )
    trusted_unclaim.add_argument("--username", help="既有 owner / 管理员账号名")
    args = parser.parse_args(argv)
    _apply_runtime_mode(args.mode)

    if args.command == "setup-admin":
        from gw.core.config import AUTH_MODE_LOCAL_ACCOUNT, resolve_auth_mode

        if resolve_auth_mode() != AUTH_MODE_LOCAL_ACCOUNT:
            print("请先将 GW_AUTH_MODE 设置为 local_account，再创建本地管理员。")
            return 1
        if local_accounts.list_accounts():
            print("本地管理员已存在，请直接登录；需要改密时使用 reset-password。")
            return 1

        username = input("管理员账号：").strip()
        password = getpass.getpass("管理员密码（8–128位，需包含字母和数字）：")
        confirmation = getpass.getpass("再次输入管理员密码：")
        if password != confirmation:
            print("两次输入的密码不一致，未创建账户。")
            return 1

        try:
            principal, token = local_accounts.create_first_admin(username, password)
            try:
                recovery_codes = local_accounts.ensure_recovery_codes(
                    principal["user_id"]
                )
            finally:
                # CLI 不会把登录 Cookie 交给浏览器，立即撤销无主会话。
                local_accounts.delete_session(token)
        except CleanroomException as exc:
            print(exc.message)
            return 1

        print("管理员创建成功。请立即保存以下一次性恢复码：")
        for code in recovery_codes:
            print(code)
        return 0

    if args.command == "reset-password":
        accounts = local_accounts.list_accounts()
        if not accounts:
            print("本地账户库中没有账户。")
            return 1
        print("可重置账户：")
        for username in accounts:
            print(f"- {username}")
        username = input("要重置的账户名：").strip()
        if username.lower() not in {name.lower() for name in accounts}:
            print("账户不存在，未进行修改。")
            return 1
        password = getpass.getpass("新密码（8–128位，需包含字母和数字）：")
        confirmation = getpass.getpass("再次输入新密码：")
        if password != confirmation:
            print("两次输入的密码不一致，未进行修改。")
            return 1
        try:
            recovery_codes = local_accounts.reset_password_local(username, password)
        except CleanroomException as exc:
            print(exc.message)
            return 1

        print("密码已重置；所有旧会话与旧恢复码已失效。请立即保存以下新恢复码：")
        for code in recovery_codes:
            print(code)
        return 0

    if args.command == "trusted-open":
        return _trusted_open(args)

    if args.command == "trusted-claim":
        username = args.username or input("要认领的 owner / 管理员账号名：").strip()
        if not username:
            print("未提供账号名，未进行认领。")
            return 1
        return _trusted_claim(username)

    if args.command == "trusted-unclaim":
        username = args.username or input("要解绑的 owner / 管理员账号名：").strip()
        if not username:
            print("未提供账号名，未进行解绑。")
            return 1
        return _trusted_unclaim(username)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
