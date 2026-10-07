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

"""安全注入开源协议头部版权声明脚本。

针对 gw/ (*.py), run.py, scripts/ (*.py), web/js/ (*.js) 安全注入 Apache License 2.0 声明。
绝不触碰 web/vendor/ 下的第三方制品。
安全保护已有的 shebang、编码声明（# -*- coding: utf-8 -*-）及第三方许可证。
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]

PYTHON_LICENSE_HEADER = """# Copyright 2026 Gods-Workbench Authors
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
# limitations under the License."""

JS_LICENSE_HEADER = """/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */"""

CODING_PATTERN = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")


def detect_newline(raw_bytes: bytes) -> str:
    return "\r\n" if b"\r\n" in raw_bytes else "\n"


def has_existing_license_or_copyright(text: str) -> bool:
    return bool(
        re.search(
            r"Licensed under the Apache License|Copyright 2026 Gods-Workbench Authors|Copyright\s+\(c\)",
            text,
            re.IGNORECASE,
        )
    )


def inject_python_header(content: str, newline: str) -> str:
    lines = content.splitlines()
    insert_idx = 0
    if lines and lines[0].startswith("#!"):
        insert_idx = 1
    if len(lines) > insert_idx and CODING_PATTERN.match(lines[insert_idx]):
        insert_idx += 1

    prefix = lines[:insert_idx]
    remainder = lines[insert_idx:]

    while remainder and remainder[0].strip() == "":
        remainder.pop(0)

    header_lines = PYTHON_LICENSE_HEADER.split("\n")
    new_lines = prefix + header_lines + [""] + remainder
    result = newline.join(new_lines)
    if content.endswith("\n") or content.endswith("\r\n"):
        result += newline
    return result


def inject_js_header(content: str, newline: str) -> str:
    lines = content.splitlines()
    insert_idx = 0
    if lines and lines[0].startswith("#!"):
        insert_idx = 1

    prefix = lines[:insert_idx]
    remainder = lines[insert_idx:]

    while remainder and remainder[0].strip() == "":
        remainder.pop(0)

    header_lines = JS_LICENSE_HEADER.split("\n")
    new_lines = prefix + header_lines + [""] + remainder
    result = newline.join(new_lines)
    if content.endswith("\n") or content.endswith("\r\n"):
        result += newline
    return result


def collect_target_files() -> list[Path]:
    targets: list[Path] = []

    # 1. run.py
    run_py = REPO_ROOT / "run.py"
    if run_py.is_file():
        targets.append(run_py)

    # 2. gw/ (*.py)
    gw_dir = REPO_ROOT / "gw"
    if gw_dir.is_dir():
        for p in sorted(gw_dir.rglob("*.py")):
            if p.is_file():
                targets.append(p)

    # 3. scripts/ (*.py)
    scripts_dir = REPO_ROOT / "scripts"
    if scripts_dir.is_dir():
        for p in sorted(scripts_dir.rglob("*.py")):
            if p.is_file():
                targets.append(p)

    # 4. web/js/ (*.js) - strictly excluding web/vendor/
    web_js_dir = REPO_ROOT / "web" / "js"
    if web_js_dir.is_dir():
        for p in sorted(web_js_dir.rglob("*.js")):
            if p.is_file():
                # Double check to prevent touching vendor
                rel_parts = p.relative_to(REPO_ROOT).parts
                if "vendor" in rel_parts:
                    continue
                targets.append(p)

    return targets


def process_files(dry_run: bool = False, check_only: bool = False) -> tuple[int, int, int]:
    targets = collect_target_files()
    injected_count = 0
    skipped_count = 0
    missing_count = 0

    print(f"Total target files discovered: {len(targets)}")

    for path in targets:
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        raw_bytes = path.read_bytes()
        newline = detect_newline(raw_bytes)
        content = raw_bytes.decode("utf-8")

        if has_existing_license_or_copyright(content):
            skipped_count += 1
            if check_only or dry_run:
                print(f"[OK] {rel_path} already has license/copyright header")
            continue

        missing_count += 1
        if check_only:
            print(f"[MISSING] {rel_path} lacks license header")
            continue

        # Perform injection
        if path.suffix == ".py":
            new_content = inject_python_header(content, newline)
            # Syntax validation via AST
            try:
                ast.parse(new_content, filename=rel_path)
            except SyntaxError as e:
                print(f"[ERROR] Syntax error when injecting {rel_path}: {e}", file=sys.stderr)
                sys.exit(1)
        elif path.suffix == ".js":
            new_content = inject_js_header(content, newline)
        else:
            continue

        if dry_run:
            print(f"[WOULD INJECT] {rel_path}")
        else:
            path.write_text(new_content, encoding="utf-8", newline="")
            print(f"[INJECTED] {rel_path}")
        injected_count += 1

    return injected_count, skipped_count, missing_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject Apache License 2.0 headers into source files.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate injection without modifying files.")
    parser.add_argument("--check", action="store_true", help="Check compliance without modifying files.")
    args = parser.parse_args()

    injected, skipped, missing = process_files(dry_run=args.dry_run, check_only=args.check)

    print("\n--- Summary ---")
    print(f"Processed: {injected + skipped}")
    print(f"Injected:  {injected}")
    print(f"Skipped:   {skipped}")
    print(f"Missing:   {missing}")

    if args.check and missing > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
