"""GW-DoD-v1 §4.3 反证执行器（QIN-645）。

对每个「关键接线」：
  1) git apply --check 补丁必须成功；
  2) 应用补丁后跑两条判据，都必须红：
     - 运行态矩阵对拍 `diff.py --fail-on-diff`（真实浏览器 computedStyle）
     - 契约测试 `node --test tests/frontend/qin-645-signal-flow-retired.test.mjs`
  3) `git checkout --` 还原，`git diff --quiet` 必须退出 0；
  4) 还原后同一对判据必须绿。

每步都打印命令、退出码与输出尾部，退出码即证据。脚本本身以「全流程符合预期」
为 0 退出，任一期望不符即 1 退出。
"""
from __future__ import annotations

import gzip
import os
import subprocess
import sys
from pathlib import Path

WT = Path(r"D:/Working/Code Pro/GW-worktrees/gw-qin-645")
QA = Path(r"D:/Working/Code Pro/Gods-Workbench/.local/qa/QIN-645")
PY = r"C:/Users/qinxuedong/AppData/Local/Programs/Python/Python311/python.exe"
BASE = "http://127.0.0.1:2090"
TEST = "tests/frontend/qin-645-signal-flow-retired.test.mjs"

CASES = [
    ("m1-marker-gate", "asset-manager", ["web/embeds/asset-manager.html"]),
    ("m2-variable-level", "canvas-interactive", ["web/css/base/hardware-design-system.css"]),
    ("m3-radius-prefix", "task-center", ["web/css/base/hardware-design-system.css"]),
]

failures: list[str] = []


def run(cmd: list[str], *, label: str, env: dict | None = None, tail: int = 6) -> int:
    merged = os.environ.copy()
    merged.update(env or {})
    proc = subprocess.run(cmd, cwd=WT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=merged)
    out = (proc.stdout or "") + (proc.stderr or "")
    lines = [line for line in out.strip().splitlines() if line.strip()]
    print(f"\n$ {' '.join(cmd)}")
    if env:
        print("  env: " + " ".join(f"{k}={v}" for k, v in env.items()))
    for line in lines[-tail:]:
        print("  " + line)
    print(f"  [{label}] exit={proc.returncode}")
    return proc.returncode


def capture(target: str, tag: str) -> None:
    run([PY, str(QA / "capture.py"), "after"], label=f"capture {tag}",
        env={"CAPTURE_BASE": BASE, "CAPTURE_ONLY": target}, tail=4)


def matrix(tag: str) -> int:
    return run([PY, str(QA / "diff.py"), "--fail-on-diff"], label=f"matrix {tag}", tail=20)


def contract(tag: str) -> int:
    return run(["node", "--test", TEST], label=f"contract {tag}", tail=8)


def main() -> int:
    for name, target, touched in CASES:
        patch = QA / "r3" / "patches" / f"{name}.patch"
        print("\n" + "=" * 78)
        print(f"反证 {name}：改坏 {target} 的关键接线")
        print("=" * 78)
        print(patch.read_text(encoding="utf-8"))

        rc = run(["git", "apply", "--check", str(patch)], label="apply --check", tail=2)
        if rc != 0:
            failures.append(f"{name}: 补丁无法应用")
            continue
        rc = run(["git", "apply", str(patch)], label="apply", tail=2)
        if rc != 0:
            failures.append(f"{name}: 应用补丁失败")
            continue

        # 改坏后：运行态与契约都必须红
        if contract("改坏后") == 0:
            failures.append(f"{name}: 改坏后契约测试仍绿")
        capture(target, "改坏后")
        if matrix("改坏后") == 0:
            failures.append(f"{name}: 改坏后矩阵仍绿")
        else:
            # 留存红态差异明细，供审核复核改坏点的具体元素与属性
            red_dir = QA / "r3" / "red"
            red_dir.mkdir(parents=True, exist_ok=True)
            raw = (QA / "diff.json").read_bytes()
            with gzip.GzipFile(red_dir / f"{name}-diff.json.gz", "wb", mtime=0) as handle:
                handle.write(raw)
            print(f"  [red 明细] {name}-diff.json.gz（{len(raw)} 字节）")

        # 还原
        rc = run(["git", "checkout", "--", *touched], label="revert", tail=2)
        rc_clean = run(["git", "diff", "--quiet"], label="工作树干净校验", tail=2)
        if rc != 0 or rc_clean != 0:
            failures.append(f"{name}: 还原失败")
            continue

        # 还原后：两者都必须绿
        if contract("恢复后") != 0:
            failures.append(f"{name}: 恢复后契约测试仍红")
        capture(target, "恢复后")
        if matrix("恢复后") != 0:
            failures.append(f"{name}: 恢复后矩阵仍红")

    print("\n" + "=" * 78)
    if failures:
        print("结论：反证未达预期")
        for item in failures:
            print("  - " + item)
        return 1
    print("结论：3 个反证均「改坏必红、恢复必绿」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
