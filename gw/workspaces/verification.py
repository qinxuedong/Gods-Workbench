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

"""执行端路径核验与 QIN-626 兼容检查接缝（QIN-628）。

本模块只做探测和状态判定，不修改操作系统权限、SMB 共享或素材文件；
探测失败一律失败关闭，不返回虚假的「已连接 / 已登记成功」。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gw.core import storage
from gw.core.errors import CleanroomException
from gw.workspaces import model

CompatibilityChecker = Callable[[], dict[str, Any]]

_compatibility_checker: CompatibilityChecker | None = None


def set_compatibility_checker(checker: CompatibilityChecker | None) -> None:
    """注入兼容检查实现，供 QIN-626 落地或测试替换；None 恢复默认失败关闭。"""
    global _compatibility_checker
    _compatibility_checker = checker


def compatibility_status() -> dict[str, str]:
    """返回兼容检查状态；未接入 QIN-626 契约时明确 pending 并阻止连接。"""
    if _compatibility_checker is None:
        return {
            "status": model.COMPATIBILITY_PENDING,
            "detail": "QIN-626 兼容契约尚未接入，连接 NAS 工作区暂不可用",
        }
    result = _compatibility_checker()
    status = str(result.get("status", model.COMPATIBILITY_INCOMPATIBLE))
    detail = str(result.get("detail", "兼容检查未返回有效结论"))
    if status not in {
        model.COMPATIBILITY_COMPATIBLE,
        model.COMPATIBILITY_PENDING,
        model.COMPATIBILITY_INCOMPATIBLE,
    }:
        status = model.COMPATIBILITY_INCOMPATIBLE
    return {"status": status, "detail": detail}


def verify_executor_path(access_path: str, capability: str = model.CAPABILITY_READ_WRITE) -> dict[str, str]:
    """核验服务端执行端对该共享路径的真实读/写能力。

    写能力探测只创建并立即回收一个随机空文件，不改动原有素材、权限或共享配置。
    任何失败都返回不可用结论，由调用方拒绝登记，避免留下半注册。
    """
    try:
        root, prefix = storage.resolve_asset_registration_root(access_path)
    except CleanroomException:
        raise CleanroomException(
            status_code=403,
            code="WORKSPACE_PATH_NOT_PERMITTED",
            message="该目录未获服务端授权，需共享/授权给实际执行端后再登记映射",
        ) from None
    normalized = storage.normalize_directory_path(access_path)
    probe = storage.probe_mount(normalized)
    if not probe.get("available"):
        raise CleanroomException(
            status_code=503,
            code="WORKSPACE_PATH_UNREACHABLE",
            message="实际执行端无法访问该目录，需共享/授权或检查连接",
        )

    if capability == model.CAPABILITY_READ_WRITE:
        probe_path: Path = root / prefix / f".gw-workspace-probe-{uuid.uuid4().hex}"
        try:
            with probe_path.open("xb") as handle:
                handle.write(b"")
        except OSError:
            raise CleanroomException(
                status_code=403,
                code="WORKSPACE_WRITE_DENIED",
                message="实际执行端对该目录无写权限，需共享/授权给执行端",
            ) from None
        finally:
            try:
                if probe_path.exists():
                    probe_path.unlink()
            except OSError:
                pass

    return {
        "status": model.VERIFICATION_VERIFIED,
        "verified_at": storage.now_iso(),
        "checked_capability": capability,
        # 实际探测由服务端进程执行，不采信客户端传来的执行机器 ID。
        "verified_by": model.LOCAL_EXECUTION_MACHINE_ID,
        "verification_source": "server_context",
        "reason": "",
    }
