"""QIN-645 迁出前后 computedStyle 逐元素逐属性对拍。

配对键：页面 × 宽度 × 主题。元素键：CSS 结构路径。
输出：差异汇总、按属性聚合、按页面聚合、明细 JSON。
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

QA = Path(__file__).resolve().parent


def load(label: str) -> dict:
    rows = json.loads((QA / label / "snapshot.json").read_text(encoding="utf-8"))
    extra = QA / label / "snapshot-share-content.json"
    if extra.exists():
        rows += json.loads(extra.read_text(encoding="utf-8"))
    return {(r["page"], r["width"], r["theme"]): r for r in rows}


def main() -> int:
    before = load("before")
    after = load("after")
    keys = sorted(set(before) & set(after))
    print(f"配对组数 {len(keys)} / before {len(before)} / after {len(after)}\n")

    by_page = defaultdict(lambda: {"cells": 0, "diffs": 0, "props": Counter()})
    hard = []
    details = []

    for key in keys:
        b, a = before[key], after[key]
        page, width, theme = key
        # 硬判据对比
        hard.append({
            "key": f"{page}/{width}/{theme}",
            "overflow_before": len(b["overflow"]), "overflow_after": len(a["overflow"]),
            "collapsed_before": len(b["collapsed"]), "collapsed_after": len(a["collapsed"]),
            "errors_before": len(b["errors"]), "errors_after": len(a["errors"]),
            "docw_before": (b["docScrollWidth"], b["docClientWidth"]),
            "docw_after": (a["docScrollWidth"], a["docClientWidth"]),
            "nodecount_before": len(b["nodes"]), "nodecount_after": len(a["nodes"]),
        })
        bm = {n["path"]: n for n in b["nodes"]}
        am = {n["path"]: n for n in a["nodes"]}
        st = by_page[page]
        st["cells"] += 1
        for path in sorted(set(bm) & set(am)):
            nb, na = bm[path], am[path]
            diffprops = []
            for p in nb["style"]:
                if nb["style"][p] != na["style"][p]:
                    diffprops.append(p)
                    st["props"][p] += 1
            if nb["rect"] != na["rect"]:
                diffprops.append("__rect__")
                st["props"]["__rect__"] += 1
            if diffprops:
                st["diffs"] += 1
                details.append({
                    "key": f"{page}/{width}/{theme}", "path": path,
                    "cls": nb["cls"][:80], "props": diffprops,
                    "before": {p: nb["style"].get(p) for p in diffprops if p != "__rect__"},
                    "after": {p: na["style"].get(p) for p in diffprops if p != "__rect__"},
                    "rect_before": nb["rect"], "rect_after": na["rect"],
                })
        # 结构差异
        onlyb = set(bm) - set(am)
        onlya = set(am) - set(bm)
        if onlyb or onlya:
            details.append({"key": f"{page}/{width}/{theme}", "path": "<structure>",
                            "only_before": sorted(onlyb), "only_after": sorted(onlya)})

    print("== 各页差异汇总 ==")
    for page in sorted(by_page):
        s = by_page[page]
        top = ", ".join(f"{p}×{c}" for p, c in s["props"].most_common(6))
        print(f"{page:22s} 组={s['cells']:2d} 差异元素={s['diffs']:4d} | 高频属性: {top}")

    print("\n== 硬判据变化（overflow / collapsed / errors / 文档宽 / 元素数）==")
    changed = [
        h for h in hard
        if h["overflow_before"] != h["overflow_after"]
        or h["collapsed_before"] != h["collapsed_after"]
        or h["errors_before"] != h["errors_after"]
        or h["docw_before"] != h["docw_after"]
        or h["nodecount_before"] != h["nodecount_after"]
    ]
    if not changed:
        print("  无变化")
    for h in changed:
        print(f"  {h['key']:34s} overflow {h['overflow_before']}→{h['overflow_after']} "
              f"collapsed {h['collapsed_before']}→{h['collapsed_after']} "
              f"errors {h['errors_before']}→{h['errors_after']} "
              f"nodes {h['nodecount_before']}→{h['nodecount_after']}")

    (QA / "diff.json").write_text(json.dumps(details, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n明细 {len(details)} 条 → diff.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
