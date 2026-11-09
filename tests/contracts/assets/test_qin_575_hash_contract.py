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

"""QIN-575：内容哈希契约（full / sampled）防漂移。

票面 AC：
- AC1：文档写清 hash_mode 两取值、512MiB 严格大于阈值、采样摘要精确字节
  结构与不足 8MiB 时的首尾重叠行为；本测试把文档锚点与实现常量逐项对齐。
- AC2：注册表 sampled 值与 QIN-561 / T7 基线 manifest 不可直接对拍；按文档
  描述的基线公式独立重算，验证两者必然不同。
- AC3：sha256 缺席不参与 governance 文件查重建议；hash_mode="sampled" 仍按
  sha256 参与 shot_source_key 分组。
- AC4：按文档锚点字面量独立重算采样摘要，与 _sampled_hash 对同一临时夹具
  文件逐字节一致；文档或实现任一侧漂移即红。重算不得复用实现常量。

稀疏文件说明：>512MiB 夹具用 truncate 延伸（NTFS / ext4 不分配真实磁盘块），
头尾写入可辨识标记，确保重算覆盖真实首/尾区间而非全零填充。
"""

import hashlib
import re
import struct
import threading
from pathlib import Path

import pytest

from gw.asset_registry import governance, repository as repo
from gw.asset_registry import index_jobs as jobs

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = REPO_ROOT / "docs/contracts/ASSET-CONTENT-HASH-CONTRACT.md"

HEAD_MARK = b"QIN575-HEAD" * 1024
TAIL_MARK = b"QIN575-TAIL" * 1024
MIXED_DIGEST = "AB" * 32

_ANCHOR_RE = re.compile(r"^QIN575_([A-Z0-9_]+)=(.+)$", re.M)


@pytest.fixture(autouse=True)
def _index_runtime(tmp_path, monkeypatch):
    """与 QIN-564 契约测试同构的最小隔离环境。"""
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    root = tmp_path / "sources"
    root.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None
    yield root
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None


def _anchors() -> tuple[dict[str, str], str]:
    """只从契约文档取值，不接触实现常量，避免文档漂移被实现掩盖。"""
    text = CONTRACT.read_text(encoding="utf-8")
    anchors = dict(_ANCHOR_RE.findall(text))
    assert anchors, "契约文档缺少 QIN575_ 机器可读锚点"
    return anchors, text


def _scanner(monkeypatch):
    scanner = object.__new__(jobs.IndexJobs)
    scanner.condition = threading.Condition(threading.RLock())
    monkeypatch.setattr(scanner, "_checkpoint", lambda *_args: {})
    monkeypatch.setattr(scanner, "_save", lambda _job: None)
    return scanner


def _make_file(path: Path, size: int) -> Path:
    """构造指定 size 的文件；大文件稀疏，头尾带可辨识标记。"""
    with path.open("wb") as handle:
        handle.truncate(size)
        handle.seek(0)
        handle.write(HEAD_MARK)
        handle.seek(max(0, size - len(TAIL_MARK)))
        handle.write(TAIL_MARK)
    return path


def _recompute(path: Path, size: int, anchors: dict[str, str]) -> str:
    """按文档锚点字面量独立重算 sampled 摘要（不引用任何实现常量）。"""
    assert anchors["DIGEST"] == "sha256", "摘要算法漂移"
    assert anchors["FORMULA"] == (
        "DOMAIN||uint64_le(size)||head(0,SAMPLE_BYTES)||"
        "tail(max(0,size-SAMPLE_BYTES),SAMPLE_BYTES)"
    ), "采样公式漂移"
    sample = int(anchors["SAMPLE_BYTES"])
    digest = hashlib.sha256()
    digest.update(anchors["HASH_DOMAIN"].encode("ascii"))
    digest.update(struct.pack(anchors["SIZE_PACK"], size))
    with path.open("rb") as handle:
        for offset in (0, max(0, size - sample)):
            handle.seek(offset)
            digest.update(handle.read(sample))
    return digest.hexdigest()


def test_ac1_contract_anchors_match_implementation() -> None:
    """AC1：文档锚点、关键措辞与实现常量逐项对齐，任一侧漂移即红。"""
    anchors, text = _anchors()
    assert anchors["HASH_DOMAIN"] == jobs._SAMPLED_HASH_DOMAIN.decode("ascii")
    assert anchors["SIZE_PACK"] == "<Q"
    assert int(anchors["SAMPLE_BYTES"]) == jobs._HASH_SAMPLE_BYTES == 4 * 1024 * 1024
    assert int(anchors["LIMIT_BYTES"]) == jobs._HASH_LIMIT_BYTES == 512 * 1024 * 1024
    assert anchors["LIMIT_OPERATOR"] == "strictly_greater"
    assert tuple(anchors["HASH_MODES"].split(",")) == (
        jobs.HASH_MODE_FULL,
        jobs.HASH_MODE_SAMPLED,
    )
    assert anchors["TAIL_OVERLAP"] == "head_and_tail_may_overlap_below_2*SAMPLE_BYTES"
    assert int(anchors["BASELINE_THRESHOLD_BYTES"]) == 4 * 1024 * 1024
    assert anchors["BASELINE_FORMULA"] == "head(4MiB)||tail(4MiB)||str(size).encode()"
    for phrase in ("严格大于", "不足 8MiB 时的重叠行为", "禁止", "重算，而非对拍", "下游语义"):
        assert phrase in text, f"契约文档缺少口径说明: {phrase}"


def test_ac4_sampled_hash_matches_documented_formula_above_limit(
    tmp_path, monkeypatch
) -> None:
    """AC4：>512MiB 文件按文档公式重算，与 _sampled_hash 逐字节一致。"""
    anchors, _ = _anchors()
    size = int(anchors["LIMIT_BYTES"]) + 1
    path = _make_file(tmp_path / "oversize.bin", size)
    scanner = _scanner(monkeypatch)

    expected = scanner._sampled_hash("qin-575-job", 1, path, path.lstat())
    assert expected == _recompute(path, size, anchors)
    assert len(expected) == 64 and all(c in "0123456789abcdef" for c in expected)


def test_ac4_overlap_between_4mib_and_8mib_is_fed_twice(
    tmp_path, monkeypatch
) -> None:
    """AC1/AC4：4MiB < size < 8MiB 时首尾区间重叠，重叠字节被喂两次。"""
    anchors, _ = _anchors()
    sample = int(anchors["SAMPLE_BYTES"])
    size = sample + sample // 2
    path = _make_file(tmp_path / "overlap.bin", size)
    scanner = _scanner(monkeypatch)

    actual = scanner._sampled_hash("qin-575-job", 1, path, path.lstat())
    assert actual == _recompute(path, size, anchors)
    # 「只喂一次全文件」与文档公式不等，证明重叠确实进入摘要输入。
    once = hashlib.sha256()
    once.update(anchors["HASH_DOMAIN"].encode("ascii"))
    once.update(struct.pack(anchors["SIZE_PACK"], size))
    once.update(path.read_bytes())
    assert actual != once.hexdigest()


def test_ac4_below_sample_size_feeds_whole_file_twice(
    tmp_path, monkeypatch
) -> None:
    """AC1/AC4：size <= 4MiB 时 tail offset 退化为 0，两段完全相同。"""
    anchors, _ = _anchors()
    size = 2 * 1024 * 1024
    path = _make_file(tmp_path / "small.bin", size)
    scanner = _scanner(monkeypatch)

    data = path.read_bytes()
    twice = hashlib.sha256()
    twice.update(anchors["HASH_DOMAIN"].encode("ascii"))
    twice.update(struct.pack(anchors["SIZE_PACK"], size))
    twice.update(data)
    twice.update(data)
    assert scanner._sampled_hash("qin-575-job", 1, path, path.lstat()) == twice.hexdigest()
    assert twice.hexdigest() == _recompute(path, size, anchors)


def test_ac1_full_hash_is_standard_sha256(tmp_path, monkeypatch) -> None:
    """AC1：full 口径就是标准 sha256(整个文件字节流)。"""
    path = _make_file(tmp_path / "full.bin", 64 * 1024)
    scanner = _scanner(monkeypatch)

    assert scanner._hash("qin-575-job", 1, path, path.lstat()) == hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def test_ac1_threshold_is_strictly_greater(tmp_path, monkeypatch) -> None:
    """AC1：仅 size > 阈值才采样，等于阈值仍走 full（用缩小阈值做边界复现）。"""
    root = tmp_path / "sources"
    payload = b"qin-575 boundary payload"
    monkeypatch.setattr(jobs, "_HASH_LIMIT_BYTES", len(payload))
    (root / "at-limit.bin").write_bytes(payload)
    (root / "over-limit.bin").write_bytes(payload + b"!")
    scanner = _scanner(monkeypatch)
    request = {
        "roots": ["sources"],
        "scopes": [
            {"root_id": "sources", "prefix": "", "storage_root_id": "sources"}
        ],
        "hash_files": True,
        "max_files": 100,
    }

    files = scanner._scan("qin-575-job", 1, {"sources": root}, request)
    modes = {item["name"]: item["hash_mode"] for item in files}
    assert modes["at-limit.bin"] == jobs.HASH_MODE_FULL
    assert modes["over-limit.bin"] == jobs.HASH_MODE_SAMPLED


def test_ac2_baseline_manifest_value_is_not_interchangeable(
    tmp_path, monkeypatch
) -> None:
    """AC2：基线公式与注册表公式算出的摘要必然不同，禁止逐条对拍。"""
    anchors, _ = _anchors()
    sample = int(anchors["BASELINE_THRESHOLD_BYTES"])
    size = sample + sample // 2
    path = _make_file(tmp_path / "baseline.bin", size)
    scanner = _scanner(monkeypatch)
    registry_value = scanner._sampled_hash("qin-575-job", 1, path, path.lstat())

    baseline = hashlib.sha256()
    with path.open("rb") as handle:
        handle.seek(0)
        baseline.update(handle.read(sample))
        handle.seek(max(0, size - sample))
        baseline.update(handle.read(sample))
    baseline.update(str(size).encode())
    assert baseline.hexdigest() != registry_value


def test_ac3_downstream_hash_semantics() -> None:
    """AC3：缺 sha256 不进查重建议；sampled 仍按 sha256 参与 shot_source_key。"""
    sampled = {
        "display_path": "clip-a.mov",
        "size_bytes": 1024,
        "sha256": MIXED_DIGEST,
        "hash_mode": jobs.HASH_MODE_SAMPLED,
    }
    missing = {
        "display_path": "clip-b.mov",
        "size_bytes": 1024,
        "sha256": "",
        "hash_mode": "",
        "asset_id": "asset-b",
    }

    assert governance._asset_file_records([sampled, missing]) == [
        ("clip-a.mov", 1024, MIXED_DIGEST)
    ]
    assert repo.shot_source_key(sampled) == "sha256:" + MIXED_DIGEST.lower()
    assert repo.shot_source_key(missing) == "asset:asset-b"
    assert repo.shot_source_key({**sampled, "asset_id": "asset-1"}) == repo.shot_source_key(
        {**sampled, "asset_id": "asset-2"}
    )
