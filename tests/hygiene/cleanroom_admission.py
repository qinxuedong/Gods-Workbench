"""洁净室准入登记的加载、合并与校验**单一来源**。

背景（QIN-465 / GitHub #426）
-----------------------------
历史做法是把「票面文件集 + 准入文件哈希」全部堆在
``tests/hygiene/fixtures/cleanroom_admission_metadata.json`` 一个文件里，并在
``test_cleanroom_hygiene.py`` 里钉一个**整文件** SHA-256。任何一张票追加登记
都要改同一个文件、重算同一个哈希，导致并行 PR 反复对同一处热点做三路合并。

现行结构（按票拆分 + 加载时并集）
--------------------------------
- 基础账本：``cleanroom_admission_metadata.json``，只保留**全局边界**
  （``accepted_roots`` / ``curated_files`` / ``admitted_files`` / ``excluded_*`` 等）
  与迁移前的历史票条目。它只在全局边界变化时改动，默认**不被单张票触碰**。
- 票面分片：``admissions/issue-<N>.json``，一票一文件。每张票只写自己的分片，
  因此两张并行票改的是两个不同文件，Git 三路合并天然无冲突。
- 加载：本模块把两处以**并集**方式合并成既有测试所依赖的同一视图
  （``authorized_issue_file_sets`` / ``admitted_files`` / ``accepted_test_files``），
  所以历史断言无需改写。

同时**取消整文件钉死**：单点 SHA 钉在这里指向基础账本
（``ADMISSION_BASE_METADATA_SHA256``），分片则逐条用 ``sha256`` 与真实文件内容
绑定。分片自身增删不再需要重算任何全局哈希。

禁止项：不允许用 ``ours`` / ``theirs`` 整文件覆盖登记，也不允许删除或裁剪
其他票的分片（见 ``fixtures/admissions/README.md``）。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

BASE_SCHEMA = "gw.cleanroom-admission/v1"
SHARD_SCHEMA = "gw.cleanroom-admission-shard/v1"

BASE_METADATA_PATH = "tests/hygiene/fixtures/cleanroom_admission_metadata.json"
SHARD_DIR_PATH = "tests/hygiene/fixtures/admissions"

# 分片文件名即票号，两者不一致会被下面的校验直接拒绝。
_SHARD_FILENAME = re.compile(r"issue-([1-9][0-9]*)\.json")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

# 分片允许的顶层键；拼写错误不得静默变成「没有登记」。
_SHARD_KEYS = frozenset(
    {
        "schema",
        "issue",
        "authorization_basis",
        "files",
        "admitted_files",
        "accepted_test_files",
        "notes",
    }
)

# 分片 ``admitted_files`` 条目允许的键。
_SHARD_ADMITTED_KEYS = frozenset(
    {"path", "classification", "authorization_basis", "sha256", "admission"}
)

# 分片 ``accepted_test_files`` 条目允许的键。
_SHARD_ACCEPTED_TEST_KEYS = frozenset(
    {"path", "classification", "authorization_basis", "admission"}
)


class AdmissionMetadataError(RuntimeError):
    """准入登记结构非法；调用方必须让测试失败，不得降级为通过。"""


def canonical_sha256(path: Path) -> str:
    """按内容规范化（CRLF/CR 归一为 LF）后计算 SHA-256。

    登记表绑定的是**文件内容**，而不是某个平台的检出行尾表示；Windows 工作树
    与 Linux CI 检出必须得到同一结论，因此比较前统一归一化行尾。
    """
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AdmissionMetadataError(f"缺少准入登记文件: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdmissionMetadataError(f"准入登记不是合法 JSON（{path.name}）: {exc}") from exc
    if not isinstance(payload, dict):
        raise AdmissionMetadataError(f"准入登记顶层必须是对象（{path.name}）")
    return payload


def _require_relative_path(value: Any, label: str) -> str:
    """登记路径必须是仓库内相对路径，且满足全局命名的 ASCII 无空格约束。"""
    if not isinstance(value, str) or not value:
        raise AdmissionMetadataError(f"{label} 必须是仓库内相对路径")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise AdmissionMetadataError(f"{label} 不得是绝对路径: {value}")
    parts = value.split("/")
    if ".." in parts or "\\" in value:
        raise AdmissionMetadataError(f"{label} 不得包含回溯片段: {value}")
    if not value.isascii() or any(char.isspace() for char in value):
        raise AdmissionMetadataError(
            f"{label} 必须是 ASCII 且无空格的规范路径: {value}"
        )
    return value


def _require_non_empty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdmissionMetadataError(f"{label} 必须是非空字符串")
    return value


def _require_file_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise AdmissionMetadataError(f"{label} 必须是非空路径列表")
    files = [_require_relative_path(item, label) for item in value]
    if len(files) != len(set(files)):
        raise AdmissionMetadataError(f"{label} 不得重复登记同一路径")
    return files


def _validate_admitted_entry(entry: Any, label: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise AdmissionMetadataError(f"{label} 必须是对象")
    unknown = set(entry) - _SHARD_ADMITTED_KEYS
    if unknown:
        raise AdmissionMetadataError(f"{label} 含未知字段: {sorted(unknown)}")
    _require_relative_path(entry.get("path"), f"{label}.path")
    _require_non_empty_str(entry.get("classification"), f"{label}.classification")
    _require_non_empty_str(
        entry.get("authorization_basis"), f"{label}.authorization_basis"
    )
    if not _SHA256_HEX.fullmatch(str(entry.get("sha256", ""))):
        raise AdmissionMetadataError(f"{label}.sha256 必须是 64 位小写十六进制")
    return entry


def _validate_accepted_test_entry(entry: Any, label: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise AdmissionMetadataError(f"{label} 必须是对象")
    unknown = set(entry) - _SHARD_ACCEPTED_TEST_KEYS
    if unknown:
        raise AdmissionMetadataError(f"{label} 含未知字段: {sorted(unknown)}")
    _require_relative_path(entry.get("path"), f"{label}.path")
    _require_non_empty_str(entry.get("classification"), f"{label}.classification")
    _require_non_empty_str(
        entry.get("authorization_basis"), f"{label}.authorization_basis"
    )
    return entry


def load_shard(path: Path) -> dict[str, Any]:
    """读取并校验单张票的分片；返回规范化的分片内容。"""
    match = _SHARD_FILENAME.fullmatch(path.name)
    if match is None:
        raise AdmissionMetadataError(
            f"分片文件名必须是 issue-<票号>.json（当前 {path.name}）"
        )
    declared = int(match.group(1))

    payload = _read_json_object(path)
    unknown = set(payload) - _SHARD_KEYS
    if unknown:
        raise AdmissionMetadataError(f"{path.name} 含未知字段: {sorted(unknown)}")
    if payload.get("schema") != SHARD_SCHEMA:
        raise AdmissionMetadataError(
            f"{path.name} 的 schema 必须是 {SHARD_SCHEMA}"
        )
    issue = payload.get("issue")
    if not isinstance(issue, int) or isinstance(issue, bool) or issue != declared:
        raise AdmissionMetadataError(
            f"{path.name} 的 issue 字段必须等于文件名中的票号 {declared}"
        )

    shard: dict[str, Any] = {
        "issue": issue,
        "authorization_basis": _require_non_empty_str(
            payload.get("authorization_basis"), f"{path.name}.authorization_basis"
        ),
        "files": _require_file_list(payload.get("files"), f"{path.name}.files"),
    }
    admitted = payload.get("admitted_files", [])
    if not isinstance(admitted, list):
        raise AdmissionMetadataError(f"{path.name}.admitted_files 必须是列表")
    shard["admitted_files"] = [
        _validate_admitted_entry(entry, f"{path.name}.admitted_files[{index}]")
        for index, entry in enumerate(admitted)
    ]
    accepted_tests = payload.get("accepted_test_files", [])
    if not isinstance(accepted_tests, list):
        raise AdmissionMetadataError(f"{path.name}.accepted_test_files 必须是列表")
    shard["accepted_test_files"] = [
        _validate_accepted_test_entry(entry, f"{path.name}.accepted_test_files[{index}]")
        for index, entry in enumerate(accepted_tests)
    ]
    if "notes" in payload:
        _require_non_empty_str(payload.get("notes"), f"{path.name}.notes")
        shard["notes"] = payload["notes"]
    shard["shard_path"] = f"{SHARD_DIR_PATH}/{path.name}"
    return shard


def load_shards(repo_root: Path) -> list[dict[str, Any]]:
    """按票号升序加载全部分片；目录缺失视为登记结构损坏。"""
    shard_dir = repo_root.joinpath(*SHARD_DIR_PATH.split("/"))
    if not shard_dir.is_dir():
        raise AdmissionMetadataError(f"缺少票面分片目录: {SHARD_DIR_PATH}")

    shards: list[dict[str, Any]] = []
    for entry in sorted(shard_dir.iterdir(), key=lambda item: item.name):
        if entry.is_dir() or entry.name == "README.md":
            continue
        if entry.suffix != ".json":
            raise AdmissionMetadataError(
                f"{SHARD_DIR_PATH} 只允许 issue-<票号>.json 与 README.md（发现 {entry.name}）"
            )
        shards.append(load_shard(entry))

    issues = [shard["issue"] for shard in shards]
    duplicates = sorted({issue for issue in issues if issues.count(issue) > 1})
    if duplicates:
        raise AdmissionMetadataError(f"同一票号存在多个分片: {duplicates}")
    return shards


def merge_admission_metadata(
    base: dict[str, Any], shards: list[dict[str, Any]]
) -> dict[str, Any]:
    """基础账本 ∪ 全部分片；结果保持历史测试所依赖的同一形状。"""
    merged = dict(base)
    legacy_sets = list(base.get("authorized_issue_file_sets", []))
    legacy_issues = {
        item.get("issue") for item in legacy_sets if isinstance(item, dict)
    }
    overlaps = sorted(
        {shard["issue"] for shard in shards if shard["issue"] in legacy_issues}
    )
    if overlaps:
        raise AdmissionMetadataError(
            f"票号在基础账本与分片中重复登记（迁移时须二选一）: {overlaps}"
        )

    merged["authorized_issue_file_sets"] = legacy_sets + [
        {
            "issue": shard["issue"],
            "authorization_basis": shard["authorization_basis"],
            "files": shard["files"],
            "source": shard["shard_path"],
        }
        for shard in sorted(shards, key=lambda item: item["issue"])
    ]

    admitted = list(base.get("admitted_files", []))
    admitted_paths = {
        entry.get("path") for entry in admitted if isinstance(entry, dict)
    }
    accepted_tests = list(base.get("accepted_test_files", []))
    accepted_paths = {
        entry.get("path") for entry in accepted_tests if isinstance(entry, dict)
    }
    for shard in sorted(shards, key=lambda item: item["issue"]):
        for entry in shard["admitted_files"]:
            if entry["path"] in admitted_paths:
                raise AdmissionMetadataError(
                    f"准入文件被重复登记（{shard['shard_path']}）: {entry['path']}"
                )
            admitted_paths.add(entry["path"])
            admitted.append(entry)
        for entry in shard["accepted_test_files"]:
            # 同一路径同时出现在 admitted_files 与 accepted_test_files 是既有真实形态
            # （前者绑定哈希，后者提供边界分类），此处不视为重复；已登记者以先到为准。
            if entry["path"] in accepted_paths:
                continue
            accepted_paths.add(entry["path"])
            accepted_tests.append(entry)
    merged["admitted_files"] = admitted
    merged["accepted_test_files"] = accepted_tests
    merged["admission_shards"] = [
        {"issue": shard["issue"], "path": shard["shard_path"]}
        for shard in sorted(shards, key=lambda item: item["issue"])
    ]
    return merged


def load_admission_metadata(repo_root: Path) -> dict[str, Any]:
    """读取基础账本 + 全部票面分片，返回并集视图。"""
    base_path = repo_root.joinpath(*BASE_METADATA_PATH.split("/"))
    base = _read_json_object(base_path)
    if base.get("schema") != BASE_SCHEMA:
        raise AdmissionMetadataError(f"基础账本 schema 必须是 {BASE_SCHEMA}")
    if base.get("release_authorized") is not True:
        raise AdmissionMetadataError("基础账本 release_authorized 必须为 true")
    return merge_admission_metadata(base, load_shards(repo_root))


def admission_union_digest(metadata: dict[str, Any]) -> str:
    """并集登记的可复算摘要，仅用于审计留痕，**不作为断言钉死**。

    分片按票号排序后参与计算，因此与文件创建顺序、落盘顺序无关。
    任何票新增或修改自己的分片都会改变该摘要，但不需要手工重算写回仓库，
    这正是本方案消除「整文件 SHA 热点」的关键。
    """
    payload = {
        "schema": metadata.get("schema"),
        "authorized_issue_file_sets": sorted(
            metadata.get("authorized_issue_file_sets", []),
            key=lambda item: item.get("issue", 0),
        ),
        "admitted_files": sorted(
            metadata.get("admitted_files", []), key=lambda item: item.get("path", "")
        ),
        "accepted_test_files": sorted(
            metadata.get("accepted_test_files", []), key=lambda item: item.get("path", "")
        ),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _main(argv: list[str]) -> int:
    """轻量自检入口：打印并集视图规模与摘要，供 PR 正文留痕。"""
    repo_root = Path(argv[1]).resolve() if len(argv) > 1 else Path.cwd()
    try:
        metadata = load_admission_metadata(repo_root)
    except AdmissionMetadataError as exc:
        print(f"准入登记非法: {exc}", file=sys.stderr)
        return 1
    print(f"authorized_issue_file_sets: {len(metadata['authorized_issue_file_sets'])}")
    print(f"admission_shards: {len(metadata['admission_shards'])}")
    print(f"admitted_files: {len(metadata['admitted_files'])}")
    print(f"union_digest: {admission_union_digest(metadata)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - 本地自检入口
    raise SystemExit(_main(sys.argv))
