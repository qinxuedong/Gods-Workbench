"""MVP1 源目录只读审计契约测试 (QIN-452).

验证 AC1~AC5:
AC1: 相同目录两次快照比对结果为空。
AC2: 临时目录中新增/删除/修改/重命名文件被分别列出。
AC3: 工具本身不在被审计目录写任何文件。
AC4: 输出 JSON + 中文 Markdown 摘要。
AC5: 路径一律相对根目录，不泄露绝对路径。
"""

import json
from pathlib import Path

from tools.asset_source_audit import (
    compare_snapshots,
    create_snapshot,
    generate_markdown_summary,
)


def test_ac1_identical_directory_snapshots_diff_empty(tmp_path: Path) -> None:
    """AC1: 相同目录两次快照比对结果为空."""
    source_dir = tmp_path / "source_assets"
    source_dir.mkdir()
    (source_dir / "asset1.txt").write_text("content 1", encoding="utf-8")
    (source_dir / "sub" / "asset2.txt").parent.mkdir(parents=True, exist_ok=True)
    (source_dir / "sub" / "asset2.txt").write_text("content 2", encoding="utf-8")

    snap1 = create_snapshot(source_dir, compute_hash=True)
    snap2 = create_snapshot(source_dir, compute_hash=True)

    diff = compare_snapshots(snap1, snap2)

    assert diff["summary"]["is_clean"] is True
    assert diff["summary"]["added_count"] == 0
    assert diff["summary"]["deleted_count"] == 0
    assert diff["summary"]["renamed_count"] == 0
    assert diff["summary"]["modified_count"] == 0
    assert len(diff["added"]) == 0
    assert len(diff["deleted"]) == 0
    assert len(diff["renamed"]) == 0
    assert len(diff["modified"]) == 0
    assert len(diff["unchanged"]) == 2


def test_ac2_added_deleted_renamed_modified_detected(tmp_path: Path) -> None:
    """AC2: 临时目录中新增/删除/重命名/修改文件被分别列出."""
    source_dir = tmp_path / "source_dir"
    source_dir.mkdir()

    file_keep = source_dir / "keep.txt"
    file_keep.write_text("keep content", encoding="utf-8")

    file_delete = source_dir / "delete.txt"
    file_delete.write_text("delete content", encoding="utf-8")

    file_modify = source_dir / "modify.txt"
    file_modify.write_text("old content", encoding="utf-8")

    file_rename = source_dir / "old_name.txt"
    file_rename.write_text("rename content", encoding="utf-8")

    snap1 = create_snapshot(source_dir, compute_hash=True)

    # 模拟文件变更
    file_delete.unlink()
    file_modify.write_text("new content updated", encoding="utf-8")
    file_rename.rename(source_dir / "new_name.txt")
    (source_dir / "add.txt").write_text("add content", encoding="utf-8")

    snap2 = create_snapshot(source_dir, compute_hash=True)

    diff = compare_snapshots(snap1, snap2)

    assert diff["summary"]["is_clean"] is False
    assert diff["summary"]["added_count"] == 1
    assert diff["added"][0]["path"] == "add.txt"

    assert diff["summary"]["deleted_count"] == 1
    assert diff["deleted"][0]["path"] == "delete.txt"

    assert diff["summary"]["modified_count"] == 1
    assert diff["modified"][0]["path"] == "modify.txt"

    assert diff["summary"]["renamed_count"] == 1
    assert diff["renamed"][0]["old_path"] == "old_name.txt"
    assert diff["renamed"][0]["new_path"] == "new_name.txt"


def test_ac3_audit_tool_does_not_modify_audited_dir(tmp_path: Path) -> None:
    """AC3: 工具本身不在被审计目录写任何文件（测试校验）."""
    source_dir = tmp_path / "audited_source"
    source_dir.mkdir()
    (source_dir / "file1.dat").write_bytes(b"data1")
    (source_dir / "nested" / "file2.dat").parent.mkdir(parents=True, exist_ok=True)
    (source_dir / "nested" / "file2.dat").write_bytes(b"data2")

    before_files = sorted(
        str(p.relative_to(source_dir)) for p in source_dir.rglob("*")
    )
    before_mtimes = {
        p.relative_to(source_dir).as_posix(): p.stat().st_mtime
        for p in source_dir.rglob("*")
        if p.is_file()
    }

    # 执行生成快照
    out_json = tmp_path / "external_snapshot.json"
    snapshot = create_snapshot(source_dir, compute_hash=True)
    out_json.write_text(json.dumps(snapshot), encoding="utf-8")

    after_files = sorted(
        str(p.relative_to(source_dir)) for p in source_dir.rglob("*")
    )
    after_mtimes = {
        p.relative_to(source_dir).as_posix(): p.stat().st_mtime
        for p in source_dir.rglob("*")
        if p.is_file()
    }

    # 校验源目录未被写入任何新文件或修改 mtime
    assert before_files == after_files
    assert before_mtimes == after_mtimes
    assert not (source_dir / "external_snapshot.json").exists()


def test_ac4_output_json_and_chinese_markdown_summary(tmp_path: Path) -> None:
    """AC4: 输出 JSON + 中文 Markdown 摘要."""
    source_dir = tmp_path / "project_src"
    source_dir.mkdir()
    (source_dir / "doc.txt").write_text("hello", encoding="utf-8")

    snap1 = create_snapshot(source_dir, compute_hash=True)
    (source_dir / "doc.txt").write_text("hello world", encoding="utf-8")
    snap2 = create_snapshot(source_dir, compute_hash=True)

    diff = compare_snapshots(snap1, snap2)
    md_summary = generate_markdown_summary(
        diff, snapshot1_name="基线", snapshot2_name="当前"
    )

    # JSON 结构断言
    assert "summary" in diff
    assert "added" in diff
    assert "deleted" in diff
    assert "renamed" in diff
    assert "modified" in diff
    assert "unchanged" in diff

    # 中文 Markdown 摘要断言
    assert "# 源目录只读审计摘要报告" in md_summary
    assert "只读状态" in md_summary
    assert "变动统计" in md_summary
    assert "修改文件" in md_summary
    assert "`doc.txt`" in md_summary


def test_ac5_relative_paths_no_absolute_path_leak(tmp_path: Path) -> None:
    """AC5: 路径一律相对根目录，不泄露绝对路径."""
    source_dir = tmp_path / "deep" / "nested" / "root"
    source_dir.mkdir(parents=True)
    (source_dir / "a" / "b" / "file.txt").parent.mkdir(parents=True, exist_ok=True)
    (source_dir / "a" / "b" / "file.txt").write_text("deep path", encoding="utf-8")

    snap = create_snapshot(source_dir, compute_hash=True)

    # 校验快照中文件 Path key 均相对且为 POSIX 格式
    rel_key = "a/b/file.txt"
    assert rel_key in snap["files"]

    abs_str = str(source_dir.resolve())
    for key, file_info in snap["files"].items():
        assert not key.startswith("/")
        assert not key.startswith("C:")
        assert abs_str not in key
        assert abs_str not in file_info["path"]

    diff = compare_snapshots(snap, snap)
    md_summary = generate_markdown_summary(diff)
    assert abs_str not in md_summary
