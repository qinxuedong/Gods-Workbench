"""MVP1-T5a：无副作用的资产命名、相似名称与内容重复建议。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Iterable, TypeAlias


FileRecordInput: TypeAlias = tuple[str, int, str]

_SEPARATOR_PATTERN = re.compile(r"[\s._-]+")
_COPY_SUFFIX_PATTERN = re.compile(
    r"(?:[\s._-]*(?:\(\d+\)|副本|copy(?:[\s_-]?\d+)?))+$", re.IGNORECASE
)
_VERSION_SUFFIX_PATTERN = re.compile(r"_v\d+$", re.IGNORECASE)
_VERSION_LIKE_SUFFIX_PATTERN = re.compile(r"_v\d+$", re.IGNORECASE)
_COMMON_SEPARATORS = frozenset(" \t\r\n._-")


@dataclass(frozen=True)
class NamingRules:
    """可配置的命名校验规则；版本正则需匹配文件名主干的结尾。"""

    prefix: str | None = None
    separator: str = "_"
    forbidden_characters: str = ""
    max_length: int | None = None
    version_suffix_pattern: str | None = None
    suggested_version_suffix: str = "v001"

    def __post_init__(self) -> None:
        if self.prefix is not None and not isinstance(self.prefix, str):
            raise ValueError("prefix 必须是字符串或 None")
        if not isinstance(self.separator, str) or len(self.separator) != 1:
            raise ValueError("separator 必须是单个字符")
        if self.separator.isspace():
            raise ValueError("separator 不能是空白字符")
        if not isinstance(self.forbidden_characters, str):
            raise ValueError("forbidden_characters 必须是字符串")
        if self.max_length is not None and (
            isinstance(self.max_length, bool)
            or not isinstance(self.max_length, int)
            or self.max_length < 1
        ):
            raise ValueError("max_length 必须是正整数或 None")
        if self.version_suffix_pattern is not None:
            try:
                re.compile(self.version_suffix_pattern)
            except re.error as exc:
                raise ValueError("version_suffix_pattern 不是有效正则") from exc
            if not self.suggested_version_suffix:
                raise ValueError("配置版本后缀时 suggested_version_suffix 不能为空")


@dataclass(frozen=True)
class NamingViolation:
    """单条命名违规及简短说明。"""

    code: str
    message: str


@dataclass(frozen=True)
class NamingAssessment:
    """一个文件名的校验结果；有效名称的 suggested_name 为 None。"""

    name: str
    violations: tuple[NamingViolation, ...]
    suggested_name: str | None


@dataclass(frozen=True)
class SimilarNameGroup:
    """相似文件名建议组；仅报告候选，不修改文件。"""

    normalized_name: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class AssetFileRecord:
    """供纯函数分析的文件元数据，不代表实际文件系统对象。"""

    path: str
    size: int
    content_hash: str


@dataclass(frozen=True)
class DuplicateContentGroup:
    """按内容哈希聚合的重复候选组。"""

    content_hash: str
    members: tuple[AssetFileRecord, ...]


@dataclass(frozen=True)
class GovernanceSuggestions:
    """结构化治理建议，包含命名问题、相似名称和内容重复候选。"""

    naming_assessments: tuple[NamingAssessment, ...]
    similar_name_groups: tuple[SimilarNameGroup, ...]
    duplicate_content_groups: tuple[DuplicateContentGroup, ...]


def _split_extension(name: str) -> tuple[str, str]:
    dot = name.rfind(".")
    if dot > 0 and dot < len(name) - 1:
        return name[:dot], name[dot:]
    return name, ""


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def validate_name(name: str, rules: NamingRules) -> NamingAssessment:
    """按显式规则检查名称并生成建议；不访问磁盘或修改输入。"""
    if not isinstance(name, str) or not name:
        raise ValueError("name 必须是非空字符串")
    if not isinstance(rules, NamingRules):
        raise ValueError("rules 必须是 NamingRules")

    stem, extension = _split_extension(name)
    violations: list[NamingViolation] = []

    if rules.prefix and not stem.startswith(rules.prefix):
        violations.append(NamingViolation("PREFIX", "名称缺少要求的前缀"))

    alternate_separators = _COMMON_SEPARATORS - {rules.separator}
    has_alternate_separator = any(char in stem for char in alternate_separators)
    prefix_separator_missing = bool(
        rules.prefix
        and stem.startswith(rules.prefix)
        and len(stem) > len(rules.prefix)
        and stem[len(rules.prefix)] != rules.separator
    )
    repeated_separator = bool(
        stem.startswith(rules.separator)
        or stem.endswith(rules.separator)
        or rules.separator * 2 in stem
    )
    if has_alternate_separator or prefix_separator_missing or repeated_separator:
        violations.append(NamingViolation("SEPARATOR", "名称未使用规范分隔符"))

    if rules.forbidden_characters and any(
        char in name for char in rules.forbidden_characters
    ):
        violations.append(NamingViolation("FORBIDDEN_CHARACTER", "名称包含禁用字符"))

    if rules.max_length is not None and len(name) > rules.max_length:
        violations.append(NamingViolation("MAX_LENGTH", "名称超过最大长度"))

    if rules.version_suffix_pattern and not re.search(
        rules.version_suffix_pattern, stem
    ):
        violations.append(NamingViolation("VERSION_SUFFIX", "名称缺少有效版本后缀"))

    if not violations:
        return NamingAssessment(name=name, violations=(), suggested_name=None)

    suggested_stem = _SEPARATOR_PATTERN.sub(rules.separator, stem).strip(
        rules.separator
    )
    for char in rules.forbidden_characters:
        suggested_stem = suggested_stem.replace(char, rules.separator)
        extension = extension.replace(char, "")

    if rules.prefix:
        if not suggested_stem.startswith(rules.prefix):
            suggested_stem = f"{rules.prefix}{rules.separator}{suggested_stem}"
        elif len(suggested_stem) > len(rules.prefix) and (
            suggested_stem[len(rules.prefix)] != rules.separator
        ):
            tail = suggested_stem[len(rules.prefix) :].lstrip(
                "".join(_COMMON_SEPARATORS)
            )
            suggested_stem = f"{rules.prefix}{rules.separator}{tail}"

    if rules.version_suffix_pattern and not re.search(
        rules.version_suffix_pattern, stem
    ):
        suggested_stem = _VERSION_LIKE_SUFFIX_PATTERN.sub("", suggested_stem)
        suffix = rules.suggested_version_suffix
        if not suffix.startswith(rules.separator):
            suffix = f"{rules.separator}{suffix}"
        suggested_stem = f"{suggested_stem.rstrip(rules.separator)}{suffix}"

    if rules.max_length is not None and len(suggested_stem + extension) > rules.max_length:
        prefix_part = (
            f"{rules.prefix}{rules.separator}" if rules.prefix else ""
        )
        suffix_part = ""
        if rules.version_suffix_pattern:
            suffix_part = rules.suggested_version_suffix
            if not suffix_part.startswith(rules.separator):
                suffix_part = f"{rules.separator}{suffix_part}"
        core = suggested_stem
        if prefix_part and core.startswith(prefix_part):
            core = core[len(prefix_part) :]
        if suffix_part and core.endswith(suffix_part):
            core = core[: -len(suffix_part)]
        available = (
            rules.max_length
            - len(prefix_part)
            - len(suffix_part)
            - len(extension)
        )
        if available >= 0:
            core = core[:available].rstrip(rules.separator)
            suggested_stem = f"{prefix_part}{core}{suffix_part}"

    return NamingAssessment(
        name=name,
        violations=tuple(violations),
        suggested_name=suggested_stem + extension,
    )


def normalize_filename(name_or_path: str) -> str:
    """统一全半角、大小写、空白与常见副本/版本后缀，供相似度比较。"""
    if not isinstance(name_or_path, str) or not name_or_path:
        raise ValueError("name_or_path 必须是非空字符串")
    basename = unicodedata.normalize("NFKC", _basename(name_or_path)).casefold()
    stem, extension = _split_extension(basename)
    previous = None
    while stem != previous:
        previous = stem
        stem = _COPY_SUFFIX_PATTERN.sub("", stem)
        stem = _VERSION_SUFFIX_PATTERN.sub("", stem)
    stem = _SEPARATOR_PATTERN.sub("_", stem).strip("_")
    return f"{stem}{extension.casefold()}"


def group_similar_names(
    names_or_paths: Iterable[str], *, similarity_threshold: float = 0.9
) -> tuple[SimilarNameGroup, ...]:
    """以规范化名称和相似度分组；保留重复输入以反映同名冲突建议。"""
    if (
        isinstance(similarity_threshold, bool)
        or not isinstance(similarity_threshold, (int, float))
        or not 0 <= similarity_threshold <= 1
    ):
        raise ValueError("similarity_threshold 必须在 0 到 1 之间")

    candidates = []
    for value in names_or_paths:
        if not isinstance(value, str) or not value:
            raise ValueError("文件名候选必须是非空字符串")
        normalized = normalize_filename(value)
        if normalized:
            candidates.append((value, normalized))
    candidates.sort(key=lambda item: (item[1], _stable_text_key(item[0])))
    if len(candidates) < 2:
        return ()

    parents = list(range(len(candidates)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    for left in range(len(candidates)):
        for right in range(left + 1, len(candidates)):
            left_name = candidates[left][1]
            right_name = candidates[right][1]
            if left_name == right_name or SequenceMatcher(
                None, left_name, right_name, autojunk=False
            ).ratio() >= similarity_threshold:
                union(left, right)

    grouped: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for index, candidate in enumerate(candidates):
        grouped[find(index)].append(candidate)

    result = []
    for members in grouped.values():
        if len(members) < 2:
            continue
        normalized_name = min(item[1] for item in members)
        paths = tuple(sorted((item[0] for item in members), key=_stable_text_key))
        result.append(SimilarNameGroup(normalized_name, paths))
    return tuple(sorted(result, key=lambda group: (group.normalized_name, group.members)))


def _stable_text_key(value: str) -> tuple[str, str]:
    return (unicodedata.normalize("NFKC", value).casefold(), value)


def _parse_file_records(
    records: Iterable[FileRecordInput],
) -> tuple[AssetFileRecord, ...]:
    parsed = set()
    for record in records:
        if not isinstance(record, (tuple, list)) or len(record) != 3:
            raise ValueError("每条文件记录必须是 (path, size, hash)")
        path, size, content_hash = record
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path 必须是非空字符串")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("size 必须是非负整数")
        if not isinstance(content_hash, str) or not content_hash.strip():
            raise ValueError("hash 必须是非空字符串")
        parsed.add(AssetFileRecord(path, size, content_hash.strip().casefold()))
    return tuple(
        sorted(parsed, key=lambda item: (_stable_text_key(item.path), item.size, item.content_hash))
    )


def find_duplicate_groups(
    records: Iterable[FileRecordInput],
) -> tuple[DuplicateContentGroup, ...]:
    """按内容哈希报告不同路径的重复候选，不按名称删改或合并记录。"""
    by_hash: dict[str, list[AssetFileRecord]] = defaultdict(list)
    for record in _parse_file_records(records):
        by_hash[record.content_hash].append(record)

    groups = []
    for content_hash, members in by_hash.items():
        path_keys = {
            unicodedata.normalize("NFKC", member.path.replace("\\", "/")).casefold()
            for member in members
        }
        if len(path_keys) < 2:
            continue
        ordered_members = tuple(
            sorted(
                members,
                key=lambda item: (
                    unicodedata.normalize("NFKC", item.path).casefold(),
                    item.path,
                    item.size,
                ),
            )
        )
        groups.append(DuplicateContentGroup(content_hash, ordered_members))
    return tuple(sorted(groups, key=lambda group: group.content_hash))


def build_governance_suggestions(
    records: Iterable[FileRecordInput],
    rules: NamingRules,
    *,
    similarity_threshold: float = 0.9,
) -> GovernanceSuggestions:
    """为给定文件元数据生成可重复、只读的完整建议结果。"""
    parsed = _parse_file_records(records)
    naming = tuple(validate_name(_basename(item.path), rules) for item in parsed)
    name_candidates = tuple(
        item.path for item in parsed
    )
    return GovernanceSuggestions(
        naming_assessments=naming,
        similar_name_groups=group_similar_names(
            name_candidates, similarity_threshold=similarity_threshold
        ),
        duplicate_content_groups=find_duplicate_groups(
            (item.path, item.size, item.content_hash) for item in parsed
        ),
    )


__all__ = [
    "AssetFileRecord",
    "DuplicateContentGroup",
    "GovernanceSuggestions",
    "NamingAssessment",
    "NamingRules",
    "NamingViolation",
    "SimilarNameGroup",
    "build_governance_suggestions",
    "find_duplicate_groups",
    "group_similar_names",
    "normalize_filename",
    "validate_name",
]
