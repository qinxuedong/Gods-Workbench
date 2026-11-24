"""QIN-645 证据清单：逐格记录读数、产物哈希，供审核独立复核。

审核要求「四宽度 × 双主题的原始证据完整」，故这里不再只给汇总表，
而是把每一格的：
  - 页面 / 宽度 / 主题 / 交互态强制结果
  - 元素节点数、文本溢出数、按钮塌陷数、页面错误数、文档宽
  - 截图文件名 + 截图 sha256
  - 该格 computedStyle 快照的 sha256
逐条写入 manifest.json，任何一格缺失或被替换都能被发现。

用法：python manifest.py [before|after ...]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

QA = Path(__file__).resolve().parent
WIDTHS = [724, 900, 1440, 1920]
THEMES = ["dark", "light"]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rows_of(label: str) -> list[dict]:
    rows = json.loads((QA / label / "snapshot.json").read_text(encoding="utf-8"))
    extra = QA / label / "snapshot-share-content.json"
    if extra.exists():
        rows += json.loads(extra.read_text(encoding="utf-8"))
    return rows


def main() -> int:
    labels = sys.argv[1:] or ["before", "after"]
    report: dict[str, dict] = {}
    for label in labels:
        rows = rows_of(label)
        pages = sorted({row["page"] for row in rows})
        cells = []
        missing = []
        for row in rows:
            shot = QA / label / "shots" / row["shot"]
            entry = {
                "page": row["page"],
                "width": row["width"],
                "theme": row["theme"],
                "url": row["url"],
                "interact": row.get("interact"),
                "nodes": len(row["nodes"]),
                "overflow": len(row["overflow"]),
                "collapsed": len(row["collapsed"]),
                "errors": len(row["errors"]),
                "doc_width": f"{row['docScrollWidth']}/{row['docClientWidth']}",
                "body_class": row.get("bodyClass", ""),
                "shot": row["shot"],
                "shot_sha256": sha256_of(shot) if shot.exists() else None,
                "cell_sha256": hashlib.sha256(
                    json.dumps(
                        {"nodes": row["nodes"], "overflow": row["overflow"],
                         "collapsed": row["collapsed"]},
                        ensure_ascii=False, sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
            }
            if entry["shot_sha256"] is None:
                missing.append(f"{row['page']}/{row['width']}/{row['theme']}")
            cells.append(entry)

        expected = {(page, width, theme) for page in pages for width in WIDTHS for theme in THEMES}
        actual = {(cell["page"], cell["width"], cell["theme"]) for cell in cells}
        report[label] = {
            "pages": pages,
            "expected_cells": len(expected),
            "captured_cells": len(actual),
            "missing_cells": sorted(f"{p}/{w}/{t}" for p, w, t in expected - actual),
            "missing_shots": missing,
            "cells": cells,
        }
        print(
            f"{label}: 页面 {len(pages)} 个 ({', '.join(pages)}) "
            f"格数 {len(actual)}/{len(expected)} 缺格 {len(expected - actual)} 缺图 {len(missing)}"
        )

    out = QA / "manifest.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
