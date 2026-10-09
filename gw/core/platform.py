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

"""Phase 11 B2 平台与 AI 接入的洁净室服务。

本模块只提供本仓已冻结的本地事实与失败关闭边界：
- 不执行外部 CLI，不发起网络请求，不保存上传内容或模型凭据；
- CLI 状态最多观察本机 PATH 中的命令位置，不能据此宣称可执行或已登录；
- 执行开关、登录观察与生成接入分别表达；其他操作的准入由对应路由控制。
"""

from __future__ import annotations

import platform as platform_module
import shutil
import sys
from dataclasses import dataclass
from typing import Optional, Sequence

from gw import __version__
from gw.core.config import load_runtime_auth_config
from gw.core.errors import CleanroomException
from gw.core.cli_runtime import CLI_CANDIDATES, cli_execution_enabled


AI_UPLOAD_NOT_INTEGRATED = "AI_UPLOAD_NOT_INTEGRATED"
CHAT_NOT_INTEGRATED = "CHAT_NOT_INTEGRATED"
CLI_HELP_NOT_INTEGRATED = "CLI_HELP_NOT_INTEGRATED"
JIMENG_CREDIT_NOT_INTEGRATED = "JIMENG_CREDIT_NOT_INTEGRATED"
JIMENG_LOGIN_NOT_INTEGRATED = "JIMENG_LOGIN_NOT_INTEGRATED"


@dataclass(frozen=True)
class CliDescriptor:
    """外部 CLI 的只读观察描述。"""

    protocol: str
    display_name: str
    command_candidates: Sequence[str]


CLI_DESCRIPTORS = {
    "codex": CliDescriptor("codex", "GPT CLI", CLI_CANDIDATES["codex"]),
    "gemini-cli": CliDescriptor("gemini-cli", "Gemini / Antigravity CLI", CLI_CANDIDATES["gemini-cli"]),
    "jimeng": CliDescriptor("jimeng", "即梦 CLI", CLI_CANDIDATES["jimeng"]),
}


def _find_cli_path(candidates: Sequence[str]) -> Optional[str]:
    """只查询 PATH，不启动命令，避免隐藏外部副作用。"""
    for name in candidates:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def cli_status(protocol: str) -> dict:
    """返回 CLI 本机路径观察结果，不把存在路径误报为可用服务。"""
    descriptor = CLI_DESCRIPTORS[protocol]
    path = _find_cli_path(descriptor.command_candidates)
    installed = path is not None
    execution_enabled = cli_execution_enabled()
    return {
        "protocol": descriptor.protocol,
        "name": descriptor.display_name,
        "installed": installed,
        "path": path,
        "version": None,
        "logged_in": None,
        "running": None,
        "execution_enabled": execution_enabled,
        "command_candidates": list(descriptor.command_candidates),
        "generation_ready": False,
        "generation_status": "not_integrated",
        "data_status": "observed" if installed else "not_integrated",
        "data_gaps": ["cli_generation_not_integrated", "cli_login_unobserved"] + ([] if execution_enabled else ["cli_execution_disabled"]),
        "message": (
            "仅观察到本机命令路径；未执行 CLI，不能据此确认版本、登录或服务可用性。"
            if installed
            else "未发现已准入的本机 CLI 命令；未执行任何外部探测。"
        ),
    }


def app_info() -> dict:
    """构造可由源码和运行时直接证明的应用信息。"""
    auth = load_runtime_auth_config()
    return {
        "name": "Gods-Workbench Cleanroom",
        "version": __version__,
        "runtime": "cleanroom",
        "python_version": platform_module.python_version(),
        "platform": sys.platform,
        "auth_mode": auth.mode,
        "release_authorized": True,
        "data_status": "ok",
        "data_gaps": [],
    }


def _not_integrated(code: str, endpoint: str, message: str) -> None:
    """统一生成外部能力未准入的失败关闭异常。"""
    raise CleanroomException(
        status_code=503,
        code=code,
        message=message,
        extra={"endpoint": endpoint, "unavailable": True, "data_status": "not_integrated"},
        expose_extra_fields={"endpoint", "unavailable", "data_status"},
    )


def reject_upload() -> None:
    """拒绝上传：不读取、不保存、不回显用户文件。"""
    _not_integrated(
        AI_UPLOAD_NOT_INTEGRATED,
        "/api/ai/upload",
        "附件上传尚未接入真实资产存储，已拒绝请求；文件内容未被读取或保存。",
    )


def reject_chat(endpoint: str) -> None:
    """拒绝对话/智能体调用：不把提示词发送到未准入 Provider。"""
    _not_integrated(
        CHAT_NOT_INTEGRATED,
        endpoint,
        "对话 Provider 尚未完成准入，已按洁净室口径拒绝执行；消息未发送到外部服务。",
    )


def reject_cli_help(endpoint: str) -> None:
    """拒绝 CLI 帮助：不执行用户提交的命令文本。"""
    _not_integrated(
        CLI_HELP_NOT_INTEGRATED,
        endpoint,
        "CLI 帮助执行尚未完成准入，已拒绝执行命令；未启动外部进程。",
    )


def reject_jimeng_credit() -> None:
    """拒绝查询即梦余额：不伪造余额。"""
    _not_integrated(
        JIMENG_CREDIT_NOT_INTEGRATED,
        "/api/jimeng/credit",
        "即梦余额查询尚未接入真实 CLI 会话，未返回伪造余额。",
    )


def reject_jimeng_login(endpoint: str) -> None:
    """拒绝即梦登录/退出：不创建伪造登录状态。"""
    _not_integrated(
        JIMENG_LOGIN_NOT_INTEGRATED,
        endpoint,
        "即梦 CLI 登录尚未完成准入，未创建或修改任何登录会话。",
    )
