#!/usr/bin/env python3
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

"""C4-AC5 未归属资产只读盘点。

统计当前数据目录（GW_DATA_DIR）资产注册表内没有 project_id 的资产数量与
ID 清单，输出 JSON 到 stdout。只读：仅调用 ``repository.state().read()``，
绝不 mutate、不修复、不猜测归属。

归属口径：无 workflow_owner 的无主资产只进入本地实例管理员（role == 'admin'）
的未归属收件箱，展示为 source_unknown=true / origin_label='来源未知'；非 admin
账户不可见也不能移动（404）。归属修复只能显式完成：有主资产由所有者、无主资产
由管理员通过 POST /api/assets/{asset_id}/move-to-project 执行，管理员的移动会在
同一次 CAS 写入内把 metadata.workflow_owner 回填为自己的归属键。

用法：
    /workspace/.gw-venv/bin/python tools/c4_acceptance/unassigned_inbox_inventory.py

    默认 dev 态读取仓库 .local/runtime-dev/data；如需指向其他数据目录，
    测试态必须同时显式提供三根环境变量：
    GW_RUNTIME_MODE=test GW_DATA_DIR=... GW_LOCAL_AUTH_DB=... GW_VIDEO_DATA_DIR=... \
        python tools/c4_acceptance/unassigned_inbox_inventory.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def inventory_unassigned_assets() -> dict[str, Any]:
    """返回无 project_id 资产的数量与 ID 清单；全程只读，不修改任何数据。"""
    from gw.asset_registry import repository as repo

    raw = repo.state().read()
    assets = list(raw.get("assets", {}).values())
    unassigned = sorted(
        (record for record in assets if repo.asset_is_unassigned(record)),
        key=lambda record: str(record.get("asset_id") or ""),
    )
    unassigned_ids = [str(record.get("asset_id") or "") for record in unassigned]
    with_owner = sum(
        1 for record in unassigned if repo._record_owner(record) is not None
    )
    return {
        "tool": "unassigned-inbox-inventory",
        "read_only": True,
        "backend": "json-state",
        "revision": raw.get("revision"),
        "total_assets": len(assets),
        "unassigned_count": len(unassigned),
        "unassigned_asset_ids": unassigned_ids,
        "assigned_count": len(assets) - len(unassigned),
        "unassigned_with_owner_count": with_owner,
        "unassigned_ownerless_count": len(unassigned) - with_owner,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_status": "ok",
        "data_gaps": [],
    }


def main() -> int:
    print(json.dumps(inventory_unassigned_assets(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
