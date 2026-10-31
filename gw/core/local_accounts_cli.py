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

from gw.core import local_accounts
from gw.core.errors import CleanroomException


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本机本地账户管理")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-admin", help="交互式创建首位本地管理员")
    commands.add_parser("reset-password", help="重置本地账户密码并重发恢复码")
    args = parser.parse_args(argv)

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

    if args.command != "reset-password":
        return 2

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


if __name__ == "__main__":
    raise SystemExit(main())
