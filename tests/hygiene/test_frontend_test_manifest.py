"""前端 Node 测试清单完整性与防漏自动化门禁 (QIN-314)。

确保 tests/frontend/ 下所有跟踪的 .mjs 测试均已登记入 tests/frontend/node-files.txt，
并且在 CI 中全量执行，杜绝任何新增前端测试因命名或遗漏未被 CI 执行。
"""

import subprocess
from pathlib import Path


def _get_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def test_frontend_node_files_manifest_exists_and_valid() -> None:
    """验证 tests/frontend/node-files.txt 存在、非空且格式合规。"""
    repo_root = _get_repo_root()
    manifest_path = repo_root / "tests" / "frontend" / "node-files.txt"

    assert manifest_path.is_file(), f"清单文件不存在: {manifest_path}"
    content = manifest_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip()]

    assert len(lines) >= 39, f"清单文件中的测试数应 >= 39，实际: {len(lines)}"

    # 路径格式检查：必须使用 POSIX 正斜杠，必须以 tests/frontend/ 开头，必须以 .mjs 结尾
    for rel_path in lines:
        assert "\\" not in rel_path, f"路径中包含反斜杠: {rel_path}"
        assert rel_path.startswith("tests/frontend/"), f"路径未以 tests/frontend/ 开头: {rel_path}"
        assert rel_path.endswith(".mjs"), f"非 .mjs 文件: {rel_path}"
        full_path = repo_root / rel_path
        assert full_path.is_file(), f"清单中登记的文件在磁盘上不存在: {rel_path}"

    # 查重检查
    assert len(lines) == len(set(lines)), f"清单文件存在重复项: {[x for x in lines if lines.count(x) > 1]}"

    # 特殊命名文件检查（QIN-314 显式要求）
    assert "tests/frontend/test_c18_omnibar_hotkeys.mjs" in lines, (
        "下划线前缀的 test_c18_omnibar_hotkeys.mjs 必须在清单中"
    )


def test_frontend_node_files_manifest_matches_git_tracked_mjs() -> None:
    """防漏门禁：断言 git ls-files 'tests/frontend/*.mjs' 与 node-files.txt 集合严格 100% 一致。"""
    repo_root = _get_repo_root()
    manifest_path = repo_root / "tests" / "frontend" / "node-files.txt"
    manifest_files = set(manifest_path.read_text(encoding="utf-8").splitlines())
    manifest_files = {line.strip() for line in manifest_files if line.strip()}

    # 从 git 查询已跟踪的 tests/frontend/*.mjs
    proc = subprocess.run(
        ["git", "ls-files", "tests/frontend/*.mjs"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_files = {line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()}

    missing_in_manifest = tracked_files - manifest_files
    extra_in_manifest = manifest_files - tracked_files

    assert not missing_in_manifest, (
        f"发现未在 tests/frontend/node-files.txt 登记的前端测试文件（漏入 CI 风险）: {sorted(missing_in_manifest)}"
    )
    assert not extra_in_manifest, (
        f"tests/frontend/node-files.txt 中存在未被 Git 跟踪的多余条目: {sorted(extra_in_manifest)}"
    )
    assert tracked_files == manifest_files, "Git 跟踪文件与清单集合必须完全相等"
