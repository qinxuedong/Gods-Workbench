"""洁净室登记合并驱动的并集、哈希重算与真实 Git 合并回归。

覆盖 GitHub Issue #426 的合并驱动配套能力：

- JSON 驱动：对象列表按键并集、纯字符串列表排序去重、标量冲突取 ours；
- 哈希重算：完全忽略两侧陈旧 ``sha256``，按工作树内容（CRLF→LF）重算；
- 基础账本钉：驱动写出账本后重写 ``ADMISSION_BASE_METADATA_SHA256``；
- 行驱动：并集、排序、去重并保留顶部注释；
- 真实仓库：配置驱动后 ``git merge`` 零冲突且结果自动并集；
- CI 自检：对当前仓库运行 ``fix-hygiene-registry.py --check``。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DRIVER_DIR = _REPO_ROOT / "scripts" / "git-merge-drivers"
if str(_DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(_DRIVER_DIR))

import merge_json_registry  # noqa: E402
import merge_line_list  # noqa: E402
from merge_common import BASE_LEDGER_PATH, dump_json  # noqa: E402

FIX_COMMAND = "python3 scripts/fix-hygiene-registry.py --fix"


def _entry(path: str, sha: str = "0" * 64, classification: str = "frozen_interface_contract"):
    return {
        "path": path,
        "classification": classification,
        "authorization_basis": "GitHub Issue #900, 2026-10-28",
        "sha256": sha,
        "admission": "accepted",
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_json(payload), encoding="utf-8")


def _write_file(root: Path, relative: str, content: bytes) -> Path:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _merge(ours: dict, theirs: dict, root: Path) -> tuple[dict, list[str]]:
    merged, fatal, _warnings = merge_json_registry.merge_payloads(ours, theirs, root)
    assert not fatal, fatal
    return merged, fatal


def test_json_driver_unions_lists_and_dedupes_strings(tmp_path: Path):
    for relative in ("gw/a.py", "gw/b.py", "gw/c.py"):
        _write_file(tmp_path, relative, b"placeholder\n")
    _write_file(tmp_path, "docs/contracts/A.yaml", b"a: 1\n")
    _write_file(tmp_path, "docs/contracts/B.yaml", b"b: 2\n")

    ours = {
        "schema": "gw.cleanroom-admission/v1",
        "as_of": "2026-10-28",
        "purpose": "ours",
        "files": ["gw/b.py", "gw/a.py"],
        "authorized_issue_file_sets": [
            {"issue": 900, "authorization_basis": "GitHub Issue #900, 2026-10-28",
             "files": ["gw/b.py", "gw/a.py"]},
            {"issue": 901, "authorization_basis": "GitHub Issue #901, 2026-10-28",
             "files": ["gw/c.py"]},
        ],
        "admitted_files": [
            _entry("docs/contracts/A.yaml", classification="frozen_interface_contract"),
            _entry("docs/contracts/B.yaml"),
        ],
    }
    theirs = {
        "schema": "gw.cleanroom-admission/v1",
        "as_of": "2026-10-29",
        "purpose": "theirs",
        "files": ["gw/c.py", "gw/a.py"],
        "authorized_issue_file_sets": [
            {"issue": 900, "authorization_basis": "GitHub Issue #900, 2026-10-28",
             "files": ["gw/c.py"]},
            {"issue": 902, "authorization_basis": "GitHub Issue #902, 2026-10-28",
             "files": ["gw/a.py"]},
        ],
        "admitted_files": [
            _entry("docs/contracts/A.yaml", classification="golden_fixture"),
            _entry("docs/contracts/C.yaml"),
        ],
    }
    _write_file(tmp_path, "docs/contracts/C.yaml", b"c: 3\n")

    merged, _ = _merge(ours, theirs, tmp_path)

    assert merged["files"] == ["gw/a.py", "gw/b.py", "gw/c.py"]
    issue_sets = {item["issue"]: item for item in merged["authorized_issue_file_sets"]}
    assert list(issue_sets) == [900, 901, 902]
    assert issue_sets[900]["files"] == ["gw/a.py", "gw/b.py", "gw/c.py"]
    # 标量冲突保留 ours；as_of 虽为字符串也按 ours 处理。
    assert merged["as_of"] == "2026-10-28"
    assert merged["purpose"] == "ours"
    admitted = {item["path"]: item for item in merged["admitted_files"]}
    assert [item["path"] for item in merged["admitted_files"]] == [
        "docs/contracts/A.yaml",
        "docs/contracts/B.yaml",
        "docs/contracts/C.yaml",
    ]
    # 同一 path 的冲突字段保留 ours。
    assert admitted["docs/contracts/A.yaml"]["classification"] == "frozen_interface_contract"


def test_json_driver_recomputes_hashes_from_working_tree(tmp_path: Path):
    target = _write_file(tmp_path, "docs/contracts/A.yaml", b"line1\r\nline2\r\n")
    expected = hashlib.sha256(b"line1\nline2\n").hexdigest()
    ours = {
        "schema": "gw.cleanroom-admission/v1",
        "admitted_files": [_entry("docs/contracts/A.yaml", sha="a" * 64)],
        "authorized_test_files": [
            {"path": "docs/contracts/A.yaml", "authorization_basis": "x", "sha256": "b" * 64}
        ],
    }
    theirs = {
        "schema": "gw.cleanroom-admission/v1",
        "admitted_files": [_entry("docs/contracts/A.yaml", sha="c" * 64)],
    }

    merged, _ = _merge(ours, theirs, tmp_path)

    assert merged["admitted_files"][0]["sha256"] == expected
    assert merged["authorized_test_files"][0]["sha256"] == expected
    assert target.read_bytes() == b"line1\r\nline2\r\n"


def test_json_driver_rejects_schema_mismatch(tmp_path: Path):
    ours = {"schema": "gw.cleanroom-admission/v1"}
    theirs = {"schema": "gw.cleanroom-admission-shard/v1"}
    merged, fatal = merge_json_registry.merge_payloads(ours, theirs, tmp_path)[:2]
    assert fatal, "schema 不一致必须视为致命冲突"
    assert "schema" in fatal[0]


def test_base_ledger_driver_rewrites_pin(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger_path = tmp_path.joinpath(*BASE_LEDGER_PATH.split("/"))
    _write_file(tmp_path, "docs/contracts/A.yaml", b"a: 1\n")
    _write_file(tmp_path, "docs/contracts/B.yaml", b"b: 2\n")
    ledger = {
        "schema": "gw.cleanroom-admission/v1",
        "admitted_files": [_entry("docs/contracts/A.yaml")],
    }
    theirs = {
        "schema": "gw.cleanroom-admission/v1",
        "admitted_files": [_entry("docs/contracts/B.yaml")],
    }
    _write_json(ledger_path, ledger)
    base_path = tmp_path / "base.json"
    theirs_path = tmp_path / "theirs.json"
    _write_json(base_path, ledger)
    _write_json(theirs_path, theirs)

    pin_path = tmp_path / "tests" / "hygiene" / "test_cleanroom_hygiene.py"
    pin_path.parent.mkdir(parents=True, exist_ok=True)
    pin_path.write_text(
        "ADMISSION_BASE_METADATA_SHA256 = (\n"
        '    "' + "0" * 64 + '"\n'
        ")\n",
        encoding="utf-8",
    )

    exit_code = merge_json_registry.main(
        ["driver", str(base_path), str(ledger_path), str(theirs_path), BASE_LEDGER_PATH]
    )
    assert exit_code == 0

    merged = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert [item["path"] for item in merged["admitted_files"]] == [
        "docs/contracts/A.yaml",
        "docs/contracts/B.yaml",
    ]
    match = re.search(
        r"ADMISSION_BASE_METADATA_SHA256\s*=\s*\(?\s*\"([0-9a-f]{64})\"",
        pin_path.read_text(encoding="utf-8"),
    )
    assert match, "钉常量未被找到"
    from merge_common import canonical_sha256

    assert match.group(1) == canonical_sha256(ledger_path)


def test_line_driver_unions_sorts_and_keeps_header(tmp_path: Path):
    ours = "# managed list\n\nb.txt\na.txt\n"
    theirs = "# other header\nc.txt\na.txt\n"
    merged = merge_line_list.merge_line_text(ours, theirs)
    assert merged == "# managed list\n\na.txt\nb.txt\nc.txt\n"

    ours_path = tmp_path / "ours.txt"
    theirs_path = tmp_path / "theirs.txt"
    ours_path.write_text(ours, encoding="utf-8")
    theirs_path.write_text(theirs, encoding="utf-8")
    assert (
        merge_line_list.main(
            ["driver", str(tmp_path / "base.txt"), str(ours_path), str(theirs_path), "ours.txt"]
        )
        == 0
    )
    assert ours_path.read_text(encoding="utf-8") == merged


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "merge-driver-test",
        "GIT_AUTHOR_EMAIL": "merge-driver-test@example.invalid",
        "GIT_COMMITTER_NAME": "merge-driver-test",
        "GIT_COMMITTER_EMAIL": "merge-driver-test@example.invalid",
    }
    return subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True, env=env
    )


def _quote(path: Path) -> str:
    return '"' + str(path).replace('"', '\\"') + '"'


def _configure_drivers(root: Path) -> None:
    json_cmd = f"{_quote(Path(sys.executable))} {_quote(_DRIVER_DIR / 'merge_json_registry.py')} %O %A %B %P"
    line_cmd = f"{_quote(Path(sys.executable))} {_quote(_DRIVER_DIR / 'merge_line_list.py')} %O %A %B %P"
    assert _git(root, "config", "merge.hygiene-json.driver", json_cmd).returncode == 0
    assert _git(root, "config", "merge.hygiene-lines.driver", line_cmd).returncode == 0


def test_real_git_merge_uses_configured_drivers(tmp_path: Path):
    for relative, content in (
        ("docs/contracts/A.yaml", b"a: 1\n"),
        ("docs/contracts/B.yaml", b"b: 2\n"),
    ):
        _write_file(tmp_path, relative, content)
    _write_file(
        tmp_path,
        ".gitattributes",
        (
            f"{BASE_LEDGER_PATH} merge=hygiene-json\n"
            "tests/frontend/node-files.txt merge=hygiene-lines\n"
        ).encode(),
    )
    ledger_path = tmp_path.joinpath(*BASE_LEDGER_PATH.split("/"))
    _write_json(
        ledger_path,
        {
            "schema": "gw.cleanroom-admission/v1",
            "as_of": "2026-10-28",
            "release_authorized": True,
            "authorized_issue_file_sets": [],
            "admitted_files": [],
            "accepted_test_files": [],
        },
    )
    pin_path = tmp_path / "tests" / "hygiene" / "test_cleanroom_hygiene.py"
    pin_path.parent.mkdir(parents=True, exist_ok=True)
    pin_path.write_text(
        'ADMISSION_BASE_METADATA_SHA256 = (\n    "' + "0" * 64 + '"\n)\n',
        encoding="utf-8",
    )
    _write_file(tmp_path, "tests/frontend/node-files.txt", b"tests/frontend/z.mjs\n")

    assert _git(tmp_path, "init", "--quiet").returncode == 0
    assert _git(tmp_path, "add", "-A").returncode == 0
    assert _git(tmp_path, "commit", "--quiet", "-m", "base").returncode == 0
    _configure_drivers(tmp_path)
    base_sha = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    # 分支 ours：账本追加 A，node 清单追加 a。
    assert _git(tmp_path, "checkout", "--quiet", "-b", "ours").returncode == 0
    ours_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ours_ledger["admitted_files"] = [_entry("docs/contracts/A.yaml")]
    _write_json(ledger_path, ours_ledger)
    _write_file(
        tmp_path,
        "tests/frontend/node-files.txt",
        b"tests/frontend/a.mjs\ntests/frontend/z.mjs\n",
    )
    assert _git(tmp_path, "add", "-A").returncode == 0
    assert _git(tmp_path, "commit", "--quiet", "-m", "ours").returncode == 0

    # 分支 theirs：从同一基线出发，账本追加 B，node 清单追加 b。
    assert _git(tmp_path, "checkout", "--quiet", "-b", "theirs", base_sha).returncode == 0
    theirs_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    theirs_ledger["admitted_files"] = [_entry("docs/contracts/B.yaml")]
    _write_json(ledger_path, theirs_ledger)
    _write_file(
        tmp_path,
        "tests/frontend/node-files.txt",
        b"tests/frontend/b.mjs\ntests/frontend/z.mjs\n",
    )
    assert _git(tmp_path, "add", "-A").returncode == 0
    assert _git(tmp_path, "commit", "--quiet", "-m", "theirs").returncode == 0

    assert _git(tmp_path, "checkout", "--quiet", "ours").returncode == 0
    merge = _git(tmp_path, "merge", "--no-edit", "theirs")
    assert merge.returncode == 0, merge.stdout + merge.stderr
    assert "CONFLICT" not in merge.stdout + merge.stderr

    merged = json.loads(ledger_path.read_text(encoding="utf-8"))
    paths = [item["path"] for item in merged["admitted_files"]]
    assert paths == ["docs/contracts/A.yaml", "docs/contracts/B.yaml"]
    from merge_common import canonical_sha256

    for entry in merged["admitted_files"]:
        target = tmp_path.joinpath(*entry["path"].split("/"))
        assert entry["sha256"] == canonical_sha256(target)

    node_lines = (tmp_path / "tests" / "frontend" / "node-files.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    assert node_lines == [
        "tests/frontend/a.mjs",
        "tests/frontend/b.mjs",
        "tests/frontend/z.mjs",
    ]

    pin_match = re.search(
        r"ADMISSION_BASE_METADATA_SHA256\s*=\s*\(?\s*\"([0-9a-f]{64})\"",
        pin_path.read_text(encoding="utf-8"),
    )
    assert pin_match and pin_match.group(1) == canonical_sha256(ledger_path), (
        "基础账本合并后必须重写 ADMISSION_BASE_METADATA_SHA256"
    )


def test_repository_registry_passes_check(repo_root: Path):
    """CI 兜底：网页端合并绕过驱动后，本用例仍会以修复命令失败。"""
    script = repo_root / "scripts" / "fix-hygiene-registry.py"
    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        "洁净室登记与工作树不一致。\n"
        f"{output}\n"
        f"修复命令：{FIX_COMMAND}\n"
        "本地合并前请先安装驱动：sh scripts/git-merge-drivers/install.sh"
    )
