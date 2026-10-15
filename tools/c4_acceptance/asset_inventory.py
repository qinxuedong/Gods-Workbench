"""C4-AC6 只读盘点：资产注册表 vs 素材库 的不一致清单（含回收站）。

严格只读：
- 不导入 gw 运行时（避免任何初始化写盘），直接以只读方式打开两个 JSON 快照；
- 不修改、不创建、不删除数据目录中的任何文件；
- 报告只写到 stdout，或写到 --out 指定的路径（该路径不得位于数据目录内）。

用法（Windows PowerShell，在仓库根目录）：
    python tools\\c4_acceptance\\asset_inventory.py --data-dir "D:\\GodsWorkbench\\data"
    python tools\\c4_acceptance\\asset_inventory.py --data-dir "D:\\GodsWorkbench\\data" --out "$env:USERPROFILE\\Desktop\\c4-inventory.json"
--data-dir 为包含 asset_registry.json 与 asset_library.json 的目录（即服务的 GW_DATA_DIR 数据根）。
省略 --data-dir 时读取环境变量 GW_DATA_DIR。退出码：0=一致，1=发现不一致，2=输入错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REGISTRY_FILE = "asset_registry.json"
LIBRARY_FILE = "asset_library.json"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    with open(path, "rb") as handle:  # 只读二进制打开
        return json.loads(handle.read().decode("utf-8"))


def _library_items(library: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for lib_id, lib in (library.get("libraries") or {}).items():
        for category in lib.get("categories") or []:
            for item in category.get("items") or []:
                row = dict(item)
                row.setdefault("library_id", lib_id)
                row.setdefault("category_id", category.get("category_id"))
                out.append(row)
    return out


def _owner(asset: Dict[str, Any]) -> Optional[str]:
    meta = asset.get("metadata") or {}
    return meta.get("workflow_owner") or asset.get("workflow_owner")


def inventory(registry: Dict[str, Any], library: Dict[str, Any]) -> Dict[str, Any]:
    assets: Dict[str, Any] = registry.get("assets") or {}
    recycle: Dict[str, Any] = registry.get("recycle_bin") or {}
    items = _library_items(library)
    lib_by_id: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        lib_by_id.setdefault(str(item.get("asset_id")), []).append(item)

    recycled_ids: Dict[str, List[str]] = {}
    for entry_id, entry in recycle.items():
        if entry.get("kind") == "asset":
            recycled_ids.setdefault(str(entry.get("asset_id")), []).append(entry_id)

    findings: Dict[str, List[Dict[str, Any]]] = {
        "registry_only_bridged": [],
        "library_only_bridged": [],
        "library_item_of_recycled_asset": [],
        "owner_mismatch": [],
        "duplicate_library_items": [],
        "recycle_entry_for_live_asset": [],
        "recycle_stash_still_in_library": [],
        "recycle_stash_id_mismatch": [],
        "multiple_recycle_entries": [],
    }

    for asset_id, asset in assets.items():
        owner = _owner(asset)
        hits = lib_by_id.get(asset_id, [])
        if owner and not hits:
            findings["registry_only_bridged"].append({"asset_id": asset_id, "kind": asset.get("kind"),
                                                      "name": asset.get("name"), "workflow_owner": owner})
        for item in hits:
            if item.get("workflow_owner") and owner and item.get("workflow_owner") != owner:
                findings["owner_mismatch"].append({"asset_id": asset_id, "registry_owner": owner,
                                                   "library_owner": item.get("workflow_owner")})

    for asset_id, hits in lib_by_id.items():
        if len(hits) > 1:
            findings["duplicate_library_items"].append({"asset_id": asset_id, "count": len(hits),
                                                        "locations": [[h.get("library_id"), h.get("category_id")] for h in hits]})
        if asset_id in assets:
            continue
        for item in hits:
            row = {"asset_id": asset_id, "name": item.get("name"), "library_id": item.get("library_id"),
                   "category_id": item.get("category_id"), "workflow_owner": item.get("workflow_owner")}
            if asset_id in recycled_ids:
                row["recycle_entry_ids"] = recycled_ids[asset_id]
                findings["library_item_of_recycled_asset"].append(row)
            elif item.get("workflow_owner"):
                findings["library_only_bridged"].append(row)

    for asset_id, entry_ids in recycled_ids.items():
        if asset_id in assets:
            findings["recycle_entry_for_live_asset"].append({"asset_id": asset_id, "recycle_entry_ids": entry_ids})
        if len(entry_ids) > 1:
            findings["multiple_recycle_entries"].append({"asset_id": asset_id, "recycle_entry_ids": entry_ids})
    for entry_id, entry in recycle.items():
        stash = (entry.get("payload") or {}).get("library_item")
        if not isinstance(stash, dict):
            continue
        if str(stash.get("asset_id")) != str(entry.get("asset_id")):
            findings["recycle_stash_id_mismatch"].append({"entry_id": entry_id, "asset_id": entry.get("asset_id"),
                                                          "stash_asset_id": stash.get("asset_id")})
        if str(entry.get("asset_id")) in lib_by_id:
            findings["recycle_stash_still_in_library"].append({"entry_id": entry_id, "asset_id": entry.get("asset_id")})

    counts = {key: len(rows) for key, rows in findings.items()}
    return {
        "schema": "gw.c4-asset-inventory/v1",
        "totals": {"registry_assets": len(assets), "registry_recycle_entries": len(recycle),
                   "library_items": len(items),
                   "library_items_bridged": sum(1 for i in items if i.get("workflow_owner"))},
        "counts": counts,
        "consistent": not any(counts.values()),
        "findings": findings,
    }


LABELS = {
    "registry_only_bridged": "注册表有、素材库缺（桥接资产）",
    "library_only_bridged": "素材库有、注册表缺且不在回收站（桥接条目）",
    "library_item_of_recycled_asset": "资产已进回收站但素材库条目残留（AC6 修复前的历史删除）",
    "owner_mismatch": "两侧 workflow_owner 不一致",
    "duplicate_library_items": "同一 asset_id 在素材库出现多次",
    "recycle_entry_for_live_asset": "回收站条目对应的资产仍在注册表中",
    "recycle_stash_still_in_library": "回收站暂存了素材库条目，但素材库仍有同 id 条目",
    "recycle_stash_id_mismatch": "回收站暂存条目 asset_id 与回收站条目不符",
    "multiple_recycle_entries": "同一 asset_id 有多个回收站条目",
}


def summary(report: Dict[str, Any]) -> str:
    t = report["totals"]
    lines = ["C4-AC6 资产盘点（只读）",
             f"注册表资产 {t['registry_assets']}，回收站 {t['registry_recycle_entries']}，"
             f"素材库条目 {t['library_items']}（桥接 {t['library_items_bridged']}）",
             "结论：一致" if report["consistent"] else "结论：发现不一致"]
    for key, count in report["counts"].items():
        if count:
            lines.append(f"- {LABELS[key]}: {count}")
            for row in report["findings"][key][:20]:
                lines.append(f"    {json.dumps(row, ensure_ascii=False)}")
            if count > 20:
                lines.append(f"    …另有 {count - 20} 条，见 JSON")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="C4-AC6 只读资产盘点")
    parser.add_argument("--data-dir", default=os.environ.get("GW_DATA_DIR"))
    parser.add_argument("--out", help="JSON 报告输出路径（不得位于数据目录内）；省略则打印到 stdout")
    args = parser.parse_args(argv)
    if not args.data_dir:
        print("缺少 --data-dir（或环境变量 GW_DATA_DIR）", file=sys.stderr)
        return 2
    data_dir = Path(args.data_dir).resolve()
    registry = _read_json(data_dir / REGISTRY_FILE)
    library = _read_json(data_dir / LIBRARY_FILE)
    if registry is None and library is None:
        print(f"数据目录中没有 {REGISTRY_FILE} / {LIBRARY_FILE}: {data_dir}", file=sys.stderr)
        return 2
    report = inventory(registry or {}, library or {})
    report["data_dir"] = str(data_dir)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        out = Path(args.out).resolve()
        if out == data_dir or data_dir in out.parents:
            print("--out 不得位于数据目录内（盘点必须对数据目录零写入）", file=sys.stderr)
            return 2
        out.write_text(text, encoding="utf-8")
        print(summary(report))
        print(f"JSON 报告: {out}")
    else:
        print(summary(report))
        print("----- JSON -----")
        print(text)
    return 0 if report["consistent"] else 1


if __name__ == "__main__":
    sys.exit(main())
