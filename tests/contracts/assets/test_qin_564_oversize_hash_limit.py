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

"""QIN-564：>512MiB 文件不再 413 fail-fast，改采样哈希并诚实标记。

票面 AC：
- AC1：hash_files 任务遇 >512MiB 文件不失败整个任务，超限文件诚实标记，
  结果区分「已哈希 N / 超限转采样 M」。
- AC2：采样哈希（首尾 4MiB + 长度，与 QIN-561 基线工具口径一致）；
  采样值与完整 sha256 域分离，不共摘要空间。
- AC3：素材级查重对超限文件按采样哈希分组（真重复仍命中同组），
  不静默当作「无重复」。
- AC4：契约测试覆盖任务不失败、诚实标记、计数区分。

稀疏文件说明：测试用 truncate 延伸产生 >512MiB 稀疏文件（NTFS / ext4
均不分配真实磁盘块），st_size 与真实大文件一致，扫描与哈希分支按真实
大小走采样路径，不需要写入 512MiB 数据。
"""

import hashlib
import threading
import time

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs as jobs
from gw.asset_registry import repository as repo
from gw.core.errors import CleanroomException

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer qin564-owner", "X-User-Role": "editor"}

PAYLOAD = b"qin-564 small file payload"


@pytest.fixture(autouse=True)
def index_runtime(tmp_path, monkeypatch):
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


def _make_sparse(path, size=None):
    """延伸出稀疏大文件：st_size 超限但磁盘近乎零占用。"""
    if size is None:
        size = jobs._HASH_LIMIT_BYTES + 1
    with open(path, "wb") as handle:
        handle.truncate(size)
    return path


def _scanner(monkeypatch):
    """不落盘的最小扫描器（与 QIN-466 契约测试同构）。"""
    scanner = object.__new__(jobs.IndexJobs)
    scanner.condition = threading.Condition(threading.RLock())
    monkeypatch.setattr(scanner, "_checkpoint", lambda *_args: {})
    # 进度落盘节流（PROGRESS_MIN_INTERVAL_SECONDS）会在慢机器上触发 _save，
    # stub 掉以保持纯内存单测。
    monkeypatch.setattr(scanner, "_save", lambda _job: None)
    return scanner


def _scan_request():
    return {
        "roots": ["sources"],
        "scopes": [
            {"root_id": "sources", "prefix": "", "storage_root_id": "sources"}
        ],
        "hash_files": True,
        "max_files": 100,
    }


def _submit(client, payload):
    return client.post(
        BASE + "/reindex",
        headers={**AUTH, "Idempotency-Key": "qin-564"},
        json={"background": True, **payload},
    )


def _wait(client, job_id):
    end = time.monotonic() + 15
    while time.monotonic() < end:
        body = client.get(
            BASE + "/workspace-jobs/" + job_id, headers=AUTH
        ).json()
        if body["job"]["status"] in jobs.TERMINAL:
            return body["job"]
        time.sleep(0.02)
    pytest.fail("后台索引未在期限内终结")


def _assets():
    return repo.state().read()["assets"]


def test_ac1_oversize_file_completes_and_marks_sampled(index_runtime):
    """AC1/AC4：超限文件不失败任务；诚实标记 sampled；计数区分两种模式。"""
    root = index_runtime
    (root / "small.txt").write_bytes(PAYLOAD)
    _make_sparse(root / "oversize.bin")

    with TestClient(create_app()) as client:
        created = _submit(client, {"hash_files": True})
        assert created.status_code == 202, created.text
        job = _wait(client, created.json()["job_id"])
        assert job["status"] == "succeeded", job

        # AC1：任务结果如实区分「完整哈希 N / 超限转采样 M」。
        assert job["result"]["scanned"] == 2
        assert job["result"]["hashed"] == 1
        assert job["result"]["hash_sampled_oversize"] == 1

    records = {item["name"]: item for item in _assets().values()}
    assert set(records) == {"small.txt", "oversize.bin"}

    # AC1：超限文件登记采样哈希（64 位小写十六进制）+ sampled 诚实标记。
    oversize = records["oversize.bin"]
    assert len(oversize["sha256"]) == 64
    assert all(char in "0123456789abcdef" for char in oversize["sha256"])
    assert oversize["hash_mode"] == jobs.HASH_MODE_SAMPLED

    # 完整哈希素材登记 full 标记，两种口径显式可区分。
    small = records["small.txt"]
    assert small["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
    assert small["hash_mode"] == jobs.HASH_MODE_FULL


def test_ac1_non_hash_task_has_no_hash_counts(index_runtime):
    """AC1/AC4：非哈希任务不附加哈希计数，不伪造 0。"""
    root = index_runtime
    (root / "small.txt").write_bytes(PAYLOAD)

    with TestClient(create_app()) as client:
        job = _wait(client, _submit(client, {"hash_files": False}).json()["job_id"])
        assert job["status"] == "succeeded", job
        assert "hashed" not in job["result"]
        assert "hash_sampled_oversize" not in job["result"]


def test_ac3_oversize_true_duplicates_group_by_sampled_hash(index_runtime):
    """AC3：超限真重复按采样哈希命中同一查重组，不静默当作无重复。"""
    root = index_runtime
    _make_sparse(root / "dup-a.bin")
    _make_sparse(root / "dup-b.bin")
    (root / "small.txt").write_bytes(PAYLOAD)


    with TestClient(create_app()) as client:
        job = _wait(client, _submit(client, {"hash_files": True}).json()["job_id"])
        assert job["status"] == "succeeded", job
        assert job["result"]["hash_sampled_oversize"] == 2

    records = {item["name"]: item for item in _assets().values()}
    key_a = repo.shot_source_key(records["dup-a.bin"])
    key_b = repo.shot_source_key(records["dup-b.bin"])
    key_small = repo.shot_source_key(records["small.txt"])

    # 同内容的两个超限副本命中同一采样指纹组（跨资产查重能力保留）。
    assert key_a.startswith("sha256:")
    assert key_a == key_b
    # 与完整哈希素材不共组；小素材仍走完整 sha256 指纹。
    assert key_small.startswith("sha256:")
    assert key_small != key_a


def test_ac2_sampled_hash_deterministic_and_domain_separated(tmp_path, monkeypatch):
    """AC2：采样哈希幂等确定，且与完整 sha256 域分离（不共摘要空间）。"""
    path = _make_sparse(tmp_path / "oversize.bin")
    expected = path.lstat()
    scanner = _scanner(monkeypatch)

    first = scanner._sampled_hash("qin-564-job", 1, path, expected)
    second = scanner._sampled_hash("qin-564-job", 2, path, expected)

    assert first == second
    assert len(first) == 64
    assert all(char in "0123456789abcdef" for char in first)

    # 完整 sha256（增量计算，避免整块内存）；域分离保证两种模式不同值。
    full = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            full.update(chunk)
    assert first != full.hexdigest()


def test_ac2_sampled_hash_rejects_replacement_after_scan(tmp_path, monkeypatch):
    """AC2：扫描后文件被替换，采样哈希仍失败关闭（409），不放行脏指纹。"""
    path = _make_sparse(tmp_path / "oversize.bin")
    expected = path.lstat()
    path.write_bytes(PAYLOAD)
    scanner = _scanner(monkeypatch)

    with pytest.raises(CleanroomException) as captured:
        scanner._sampled_hash("qin-564-job", 1, path, expected)

    assert captured.value.status_code == 409
    assert captured.value.code == "INDEX_FILE_CHANGED"


def test_ac4_scan_keeps_full_hash_below_limit(index_runtime, monkeypatch):
    """AC4 回归护栏：上限内文件仍走完整哈希，不误入采样分支。"""
    root = index_runtime
    (root / "small.txt").write_bytes(PAYLOAD)
    scanner = _scanner(monkeypatch)

    files = scanner._scan("qin-564-job", 1, {"sources": root}, _scan_request())

    assert len(files) == 1
    assert files[0]["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
    assert files[0]["hash_mode"] == jobs.HASH_MODE_FULL
