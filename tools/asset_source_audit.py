"""MVP1 源目录只读审计工具.

对目录生成快照并比对两份快照，以审计源目录是否在入库或其他操作过程中保持未变（只读）。
不在被审计目录写任何文件。路径输出一律相对根目录。
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def compute_file_sha256(file_path: Path) -> str:
    """计算文件的 SHA-256 哈希值（只读模式）."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def create_snapshot(
    root_dir: str | Path, compute_hash: bool = True
) -> dict[str, Any]:
    """生成指定目录的文件快照.

    快照中的键一律为相对 posix 路径，只读访问源文件。
    """
    root_path = Path(root_dir).resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise ValueError(f"指定目录不存在或不是有效目录: {root_dir}")

    files_data: dict[str, dict[str, Any]] = {}

    for dirpath, _, filenames in os.walk(root_path):
        for filename in sorted(filenames):
            full_path = Path(dirpath) / filename
            if full_path.is_symlink() or not full_path.is_file():
                continue

            rel_path = full_path.relative_to(root_path).as_posix()
            stat = full_path.stat()
            file_info: dict[str, Any] = {
                "path": rel_path,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "sha256": compute_file_sha256(full_path) if compute_hash else None,
            }
            files_data[rel_path] = file_info

    return {
        "root_name": root_path.name,
        "total_files": len(files_data),
        "files": files_data,
    }


def compare_snapshots(
    snapshot1: dict[str, Any], snapshot2: dict[str, Any]
) -> dict[str, Any]:
    """比对两份快照，得出新增、删除、重命名疑似、修改与未变更清单."""
    files1: dict[str, dict[str, Any]] = snapshot1.get("files", {})
    files2: dict[str, dict[str, Any]] = snapshot2.get("files", {})

    paths1 = set(files1.keys())
    paths2 = set(files2.keys())

    common_paths = paths1 & paths2
    only_in_1 = paths1 - paths2
    only_in_2 = paths2 - paths1

    modified: list[dict[str, Any]] = []
    unchanged: list[str] = []

    for path in sorted(common_paths):
        info1 = files1[path]
        info2 = files2[path]

        sha1 = info1.get("sha256")
        sha2 = info2.get("sha256")

        if sha1 is not None and sha2 is not None:
            is_changed = sha1 != sha2
        else:
            is_changed = (info1.get("size") != info2.get("size")) or (
                info1.get("mtime") != info2.get("mtime")
            )

        if is_changed:
            modified.append(
                {
                    "path": path,
                    "old_size": info1.get("size"),
                    "new_size": info2.get("size"),
                    "old_mtime": info1.get("mtime"),
                    "new_mtime": info2.get("mtime"),
                    "old_sha256": sha1,
                    "new_sha256": sha2,
                }
            )
        else:
            unchanged.append(path)

    # 重命名检测
    renamed: list[dict[str, Any]] = []
    unmatched_del: set[str] = set(only_in_1)
    unmatched_add: set[str] = set(only_in_2)

    # 优先基于 SHA-256 匹配重命名
    for p1 in list(sorted(only_in_1)):
        sha1 = files1[p1].get("sha256")
        if not sha1:
            continue
        for p2 in list(sorted(unmatched_add)):
            sha2 = files2[p2].get("sha256")
            if sha1 == sha2:
                renamed.append(
                    {
                        "old_path": p1,
                        "new_path": p2,
                        "sha256": sha1,
                        "size": files1[p1].get("size"),
                    }
                )
                unmatched_del.remove(p1)
                unmatched_add.remove(p2)
                break

    # 若未包含哈希，按大小与 mtime 匹配重命名疑似
    for p1 in list(sorted(unmatched_del)):
        info1 = files1[p1]
        size1 = info1.get("size")
        mtime1 = info1.get("mtime")
        if size1 is None:
            continue

        for p2 in list(sorted(unmatched_add)):
            info2 = files2[p2]
            if info2.get("size") == size1 and (
                mtime1 is None or info2.get("mtime") == mtime1
            ):
                renamed.append(
                    {
                        "old_path": p1,
                        "new_path": p2,
                        "sha256": None,
                        "size": size1,
                        "suspected": True,
                    }
                )
                unmatched_del.remove(p1)
                unmatched_add.remove(p2)
                break

    added = [
        {"path": p, "size": files2[p].get("size"), "sha256": files2[p].get("sha256")}
        for p in sorted(unmatched_add)
    ]
    deleted = [
        {"path": p, "size": files1[p].get("size"), "sha256": files1[p].get("sha256")}
        for p in sorted(unmatched_del)
    ]

    return {
        "summary": {
            "total_before": len(files1),
            "total_after": len(files2),
            "added_count": len(added),
            "deleted_count": len(deleted),
            "renamed_count": len(renamed),
            "modified_count": len(modified),
            "unchanged_count": len(unchanged),
            "is_clean": len(added) == 0
            and len(deleted) == 0
            and len(renamed) == 0
            and len(modified) == 0,
        },
        "added": added,
        "deleted": deleted,
        "renamed": renamed,
        "modified": modified,
        "unchanged": unchanged,
    }


def generate_markdown_summary(
    diff_result: dict[str, Any], snapshot1_name: str = "快照1", snapshot2_name: str = "快照2"
) -> str:
    """生成中文 Markdown 审计摘要."""
    summary = diff_result.get("summary", {})
    added = diff_result.get("added", [])
    deleted = diff_result.get("deleted", [])
    renamed = diff_result.get("renamed", [])
    modified = diff_result.get("modified", [])

    lines: list[str] = [
        "# 源目录只读审计摘要报告",
        "",
        f"- **基线快照**: {snapshot1_name}",
        f"- **目标快照**: {snapshot2_name}",
        f"- **只读状态**: {'干净（无变动）' if summary.get('is_clean') else '发现差异变动'}",
        "",
        "## 变动统计",
        "",
        f"| 统计项 | 数量 |",
        f"| --- | --- |",
        f"| 原始文件总数 | {summary.get('total_before', 0)} |",
        f"| 当前文件总数 | {summary.get('total_after', 0)} |",
        f"| 新增文件 | {summary.get('added_count', 0)} |",
        f"| 删除文件 | {summary.get('deleted_count', 0)} |",
        f"| 重命名 (疑似/确定) | {summary.get('renamed_count', 0)} |",
        f"| 内容/属性修改 | {summary.get('modified_count', 0)} |",
        f"| 未变更文件 | {summary.get('unchanged_count', 0)} |",
        "",
    ]

    if added:
        lines.extend(["## 新增文件", ""])
        for item in added:
            lines.append(f"- `{item['path']}` ({item.get('size', 0)} bytes)")
        lines.append("")

    if deleted:
        lines.extend(["## 删除文件", ""])
        for item in deleted:
            lines.append(f"- `{item['path']}` ({item.get('size', 0)} bytes)")
        lines.append("")

    if renamed:
        lines.extend(["## 重命名文件", ""])
        for item in renamed:
            tag = " [疑似]" if item.get("suspected") else ""
            lines.append(f"- `{item['old_path']}` -> `{item['new_path']}`{tag}")
        lines.append("")

    if modified:
        lines.extend(["## 修改文件", ""])
        for item in modified:
            lines.append(f"- `{item['path']}` (大小/哈希已变更)")
        lines.append("")

    if summary.get("is_clean"):
        lines.extend(["## 结论", "", "源目录在两次快照期间未发生任何增删改改名变动，符合只读入库合规要求。"])

    return "\n".join(lines)


def main() -> None:
    """命令行入口."""
    parser = argparse.ArgumentParser(description="源目录只读审计工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 子命令: create
    create_parser = subparsers.add_parser("create", help="对目录生成快照 JSON")
    create_parser.add_argument("dir", help="目标被审计目录")
    create_parser.add_argument("--out", required=True, help="快照 JSON 保存路径（必须位于审计目录之外）")
    create_parser.add_argument("--no-hash", action="store_true", help="不计算文件 SHA-256 哈希")

    # 子命令: compare
    compare_parser = subparsers.add_parser("compare", help="比对两份快照 JSON")
    compare_parser.add_argument("snap1", help="基线快照 JSON 路径")
    compare_parser.add_argument("snap2", help="目标快照 JSON 路径")
    compare_parser.add_argument("--json-out", help="比对结果 JSON 输出路径")
    compare_parser.add_argument("--md-out", help="中文 Markdown 摘要输出路径")

    args = parser.parse_args()

    if args.command == "create":
        snap = create_snapshot(args.dir, compute_hash=not args.no_hash)
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        print(f"快照已成功写入: {out_path}")

    elif args.command == "compare":
        with Path(args.snap1).open("r", encoding="utf-8") as f:
            snap1 = json.load(f)
        with Path(args.snap2).open("r", encoding="utf-8") as f:
            snap2 = json.load(f)

        diff = compare_snapshots(snap1, snap2)
        md_text = generate_markdown_summary(
            diff,
            snapshot1_name=snap1.get("root_name", "快照1"),
            snapshot2_name=snap2.get("root_name", "快照2"),
        )

        if args.json_out:
            out_json = Path(args.json_out).resolve()
            out_json.parent.mkdir(parents=True, exist_ok=True)
            with out_json.open("w", encoding="utf-8") as f:
                json.dump(diff, f, ensure_ascii=False, indent=2)
            print(f"比对 JSON 已写入: {out_json}")

        if args.md_out:
            out_md = Path(args.md_out).resolve()
            out_md.parent.mkdir(parents=True, exist_ok=True)
            with out_md.open("w", encoding="utf-8") as f:
                f.write(md_text)
            print(f"Markdown 摘要已写入: {out_md}")

        if not args.json_out and not args.md_out:
            print(md_text)


if __name__ == "__main__":
    main()
