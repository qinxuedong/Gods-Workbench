# -*- coding: utf-8 -*-
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

"""AI 上传与 CLI 真实执行的配置驱动运行时（Phase 12 A4）。

真实数据源与边界：
- ``POST /api/ai/upload`` 真实接收 multipart 文件并落盘到 ``GW_DATA_DIR/ai_uploads``，
  返回真实文件 ID 与可下载 URL；
- CLI（codex / gemini-cli / jimeng）**仅当** ``GW_CLI_EXECUTION=1`` 且
  ``shutil.which()`` 命中白名单候选命令时才真实执行：固定参数、超时、**不使用 shell=True**；
- 未启用或命令缺失 → 503 + ``data_status=not_integrated``，绝不伪造版本、余额、二维码或登录状态。

证据边界：单进程落盘；上传文件不做内容解析，不进入素材注册表（由调用方按需登记）。
"""

from __future__ import annotations

import atexit
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gw.core import storage
from gw.core.errors import CleanroomException, ForbiddenException
from gw.core.auth import AuthContext
from gw.core.cli_login import LoginController, SHORT_COMMAND_TIMEOUT_SECONDS

NS_UPLOADS = "ai_uploads"

ENV_CLI_EXECUTION = "GW_CLI_EXECUTION"

CLI_CANDIDATES: Dict[str, Sequence[str]] = {
    "codex": ("codex",),
    "gemini-cli": ("gemini", "gemini-cli", "antigravity"),
    "jimeng": ("dreamina",),
}

CLI_TIMEOUT_SECONDS = 30

# Dreamina共享本机账户只由该进程内的单实例控制器管理。
login_controller = LoginController()
atexit.register(login_controller.shutdown)

#: 帮助类端点固定参数；调用方提交的 command 文本**只作展示**，绝不进入 argv。
HELP_ARGV: Dict[str, Tuple[str, ...]] = {
    "codex": ("--help",),
    "gemini-cli": ("--help",),
    "jimeng": ("--help",),
}

JIMENG_ARGV: Dict[str, Tuple[str, ...]] = {
    "help": ("--help",),
    "credit": ("user_credit",),
    "login": ("login",),
    "logout": ("logout",),
}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def cli_execution_enabled() -> bool:
    """只有显式配置 GW_CLI_EXECUTION=1 才允许真实执行外部 CLI。"""
    return os.environ.get(ENV_CLI_EXECUTION, "").strip() == "1"


def find_cli(protocol: str) -> Optional[str]:
    """在 PATH 中查找白名单候选命令；找不到返回 None。"""
    for name in CLI_CANDIDATES.get(protocol, ()):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def _unavailable(code: str, endpoint: str, message: str) -> None:
    raise CleanroomException(
        status_code=503,
        code=code,
        message=message,
        extra={"endpoint": endpoint, "unavailable": True, "data_status": "not_integrated"},
        expose_extra_fields={"endpoint", "unavailable", "data_status"},
    )


def run_cli(protocol: str, argv: Sequence[str], endpoint: str, code: str) -> Dict[str, Any]:
    """在配置门禁之后真实执行 CLI；未启用/命令缺失一律 503 失败关闭。"""
    if not cli_execution_enabled():
        _unavailable(code, endpoint,
                     "未启用 GW_CLI_EXECUTION=1，未执行外部 CLI，也未返回任何探测结果。")
    executable = find_cli(protocol)
    if not executable:
        _unavailable(code, endpoint, "未发现已准入的本机 CLI 命令，未执行任何外部进程。")
    command: List[str] = [executable, *argv]
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=CLI_TIMEOUT_SECONDS, check=False,
            shell=False, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        raise CleanroomException(503, "CLI_COMMAND_TIMEOUT", "CLI执行超时，未自动重试；有副作用的命令结果需要重新核实",
            extra={"endpoint": endpoint, "data_status": "degraded", "result_unknown": endpoint.endswith(("/login/start", "/logout"))},
            expose_extra_fields={"endpoint", "data_status", "result_unknown"}) from None
    except OSError:
        _unavailable(code, endpoint, "外部CLI进程无法启动，未返回伪造结果。")
    if completed.returncode != 0:
        raise CleanroomException(503, "CLI_COMMAND_FAILED", "CLI以非零状态退出，不能判定操作成功",
            extra={"endpoint": endpoint, "data_status": "failed", "returncode": completed.returncode,
                   "result_unknown": endpoint.endswith(("/login/start", "/logout"))},
            expose_extra_fields={"endpoint", "data_status", "returncode", "result_unknown"})
    return {
        "command": " ".join(argv),
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }


def cli_help(protocol: str, endpoint: str) -> Dict[str, Any]:
    """真实执行 ``<cli> --help``；不执行调用方提交的任何命令文本。"""
    result = run_cli(protocol, HELP_ARGV[protocol], endpoint, "CLI_HELP_NOT_INTEGRATED")
    text = result["stdout"] or result["stderr"]
    if not text:
        _unavailable("CLI_HELP_NOT_INTEGRATED", endpoint, "CLI 未返回可读的帮助文本。")
    return {
        "protocol": protocol,
        "command": result["command"],
        "returncode": result["returncode"],
        "text": text,
        "raw": {"stdout": result["stdout"], "stderr": result["stderr"]},
        "data_status": "ok",
        "data_gaps": [],
    }


def stable_cli_actor_key(context: AuthContext) -> str:
    """基于已认证稳定主体生成进程内隔离键，不保存令牌或用户名原文。"""
    subject = str(getattr(context, "subject", "") or "").strip()
    if not subject:
        raise ForbiddenException(message="本机 CLI 操作要求可验证的稳定用户主体")
    domain = str(getattr(context, "identity_domain", "") or getattr(context, "mode", "") or "")
    return hashlib.sha256((domain + "\0" + subject).encode("utf-8")).hexdigest()


def _dreamina_executable(endpoint: str, code: str) -> str:
    if not cli_execution_enabled():
        _unavailable(code, endpoint,
                     "未启用 GW_CLI_EXECUTION=1，未执行外部 CLI，也未返回任何探测结果。")
    executable = find_cli("jimeng")
    if not executable:
        _unavailable(code, endpoint, "未发现准入的 Dreamina CLI，未执行任何外部进程。")
    return executable


def jimeng_credit(context: AuthContext, endpoint: str = "/api/jimeng/credit") -> Dict[str, Any]:
    """使用固定 user_credit 查询一次余额；超时/失败不重试且不泄露原始错误流。"""
    executable = _dreamina_executable(endpoint, "JIMENG_CREDIT_NOT_INTEGRATED")
    state, output = login_controller.execute_short(
        stable_cli_actor_key(context), executable, "user_credit", JIMENG_ARGV["credit"], endpoint,
        timeout_seconds=SHORT_COMMAND_TIMEOUT_SECONDS,
    )
    text = output.decode("utf-8", errors="replace").strip()
    if not text:
        _unavailable("JIMENG_CREDIT_NOT_INTEGRATED", endpoint, "Dreamina CLI 未返回余额数据。")
    raw: Any = text
    try:
        import json
        raw = json.loads(text)
    except (TypeError, ValueError):
        pass
    return {
        "protocol": "jimeng",
        "cli_name": "dreamina",
        "command": "user_credit",
        "returncode": state["returncode"],
        "raw": raw,
        "data_status": "ok",
        "data_gaps": [],
    }


def jimeng_login_start(context: AuthContext, endpoint: str = "/api/jimeng/login/start") -> Dict[str, Any]:
    """启动并接管普通 Dreamina Device Flow 登录，不回显原始输出。"""
    executable = _dreamina_executable(endpoint, "JIMENG_LOGIN_NOT_INTEGRATED")
    return login_controller.start_login(stable_cli_actor_key(context), executable, endpoint)


def jimeng_login_status(context: AuthContext, endpoint: str = "/api/jimeng/login/status") -> Dict[str, Any]:
    """只返回当前进程最后一次操作的观察结果；重启后自然为 unknown。"""
    return login_controller.status(stable_cli_actor_key(context), endpoint)


def jimeng_logout(context: AuthContext, endpoint: str = "/api/jimeng/logout") -> Dict[str, Any]:
    """串行运行固定 logout；只有退出码0才记录 logged_in=false。"""
    executable = _dreamina_executable(endpoint, "JIMENG_LOGIN_NOT_INTEGRATED")
    state, _ = login_controller.execute_short(
        stable_cli_actor_key(context), executable, "logout", JIMENG_ARGV["logout"], endpoint,
        timeout_seconds=SHORT_COMMAND_TIMEOUT_SECONDS,
    )
    return {
        "protocol": "jimeng",
        "cli_name": "dreamina",
        "command": "logout",
        "returncode": state["returncode"],
        "logged_in": False,
        "running": False,
        "state": state["state"],
        "last_operation": state["last_operation"],
        "observed_at": state["observed_at"],
        "source": state["source"],
        "result_unknown": False,
        "data_status": "ok",
        "data_gaps": [],
    }


# ---------------------------------------------------------------------------
# AI 附件上传（真实落盘）
# ---------------------------------------------------------------------------

def _upload_state() -> storage.JsonState:
    return storage.JsonState(NS_UPLOADS, lambda: {"revision": 1, "sequence": 0, "files": {}})


def _upload_dir() -> Path:
    target = storage.data_root() / "ai_uploads"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _safe_name(name: str) -> str:
    clean = _SAFE_NAME.sub("_", Path(str(name or "file")).name).strip("._") or "file"
    return clean[:120]


def save_uploads(files: List[Tuple[str, bytes]]) -> Dict[str, Any]:
    """真实保存上传文件并登记稳定 file_id；写入后可经 download-output 回读。"""
    if not files:
        raise CleanroomException(400, "INVALID_REQUEST", "未收到任何上传文件")
    directory = _upload_dir()
    saved: List[Dict[str, Any]] = []

    def mutate(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for name, payload in files:
            raw["sequence"] = int(raw.get("sequence", 0)) + 1
            file_id = "upl_%04d" % raw["sequence"]
            safe = _safe_name(name)
            target = directory / ("%s_%s" % (file_id, safe))
            target.write_bytes(payload)
            display = target.relative_to(storage.data_root()).as_posix()
            record = {
                "file_id": file_id,
                "name": Path(str(name or safe)).name,
                "size_bytes": len(payload),
                "display_path": display,
                "url": "/api/download-output?path=%s" % display,
                "created_at": storage.now_iso(),
            }
            raw.setdefault("files", {})[file_id] = record
            records.append(dict(record))
        raw["revision"] = int(raw.get("revision") or 1) + 1
        return records

    saved = _upload_state().mutate(mutate)
    return {"files": saved, "count": len(saved), "data_status": "ok", "data_gaps": []}


def list_uploads() -> Dict[str, Any]:
    """列出已真实落盘的上传文件；空时返回空集合，不编造。"""
    raw = _upload_state().read()
    files = [dict(v) for v in raw.get("files", {}).values()]
    return {"files": files, "count": len(files), "revision": raw.get("revision", 1),
            "data_status": "ok", "data_gaps": []}
