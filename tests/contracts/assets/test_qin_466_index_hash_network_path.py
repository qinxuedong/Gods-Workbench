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

"""QIN-466：映射盘内容哈希不再把 resolve/absolute 形态差误判为路径已改变。"""

import hashlib
import threading

import pytest

from gw.asset_registry import index_jobs
from gw.core import storage
from gw.core.errors import CleanroomException

PAYLOAD = b"qin-466 mapped drive hash payload"


def _scanner(monkeypatch):
    """构造不落盘的最小扫描器，只验证哈希阶段的路径判定。"""
    scanner = object.__new__(index_jobs.IndexJobs)
    scanner.condition = threading.Condition(threading.RLock())
    monkeypatch.setattr(scanner, "_checkpoint", lambda *_args: {})
    return scanner


def _form_difference_path(tmp_path):
    """构造形态差路径：absolute() 保留 `..`，resolve() 归一为真实路径。

    真实映射盘上的形态差来自 resolve() 返回 UNC、absolute() 保留盘符；
    两者语义相同（同一文件、路径文本不同），本地用 `..` 等价复现该形态差，
    这样无需挂载真实网络盘即可覆盖判定分支。
    """
    folder = tmp_path / "scan-root" / "nested"
    folder.mkdir(parents=True)
    (folder / "qin466-sample.bin").write_bytes(PAYLOAD)
    path = folder / ".." / "nested" / "qin466-sample.bin"
    assert path.resolve() != path.absolute()
    return path


def _treat_as_network(monkeypatch):
    """把路径标定为网络/映射盘，等价于真机上 GetDriveTypeW 返回远程盘。"""
    monkeypatch.setattr(storage, "_is_network_path", lambda _raw_path: True)


def _scan_request():
    return {
        "roots": ["scan-root"],
        "scopes": [
            {"root_id": "scan-root", "prefix": "", "storage_root_id": "scan-root"}
        ],
        "hash_files": True,
        "max_files": 100,
    }


def test_network_path_form_difference_still_hashes(tmp_path, monkeypatch):
    """AC1：网络/映射路径上形态差不再中断哈希，摘要按内容计算。"""
    path = _form_difference_path(tmp_path)
    expected = path.lstat()
    _treat_as_network(monkeypatch)
    scanner = _scanner(monkeypatch)

    digest = scanner._hash("qin-466-job", 1, path, expected)

    assert digest == hashlib.sha256(PAYLOAD).hexdigest()


def test_local_path_form_difference_still_rejected(tmp_path, monkeypatch):
    """AC2：本地盘同一形态差仍按路径已改变失败关闭。"""
    path = _form_difference_path(tmp_path)
    expected = path.lstat()
    scanner = _scanner(monkeypatch)

    assert storage._is_network_path(str(path)) is False
    with pytest.raises(CleanroomException) as captured:
        scanner._hash("qin-466-job", 1, path, expected)

    assert captured.value.status_code == 403
    assert captured.value.code == "INDEX_PATH_CHANGED"


def test_network_path_real_replacement_still_rejected(tmp_path, monkeypatch):
    """AC2：网络路径上文件被真实替换仍失败关闭，不以网络豁免放行。"""
    path = _form_difference_path(tmp_path)
    expected = path.lstat()
    path.write_bytes(PAYLOAD + b" replaced after scan")
    _treat_as_network(monkeypatch)
    scanner = _scanner(monkeypatch)

    with pytest.raises(CleanroomException) as captured:
        scanner._hash("qin-466-job", 1, path, expected)

    assert captured.value.status_code == 409
    assert captured.value.code == "INDEX_FILE_CHANGED"


def test_scan_with_hash_files_on_network_root_keeps_digest(tmp_path, monkeypatch):
    """AC1 回归护栏：网络根上 hash_files 扫描仍产出 sha256 且不中断。"""
    root = tmp_path / "scan-root"
    root.mkdir()
    (root / "qin466shot.txt").write_bytes(PAYLOAD)
    _treat_as_network(monkeypatch)
    scanner = _scanner(monkeypatch)

    files = scanner._scan(
        "qin-466-job", 1, {"scan-root": root}, _scan_request()
    )

    assert [item["display_path"] for item in files] == ["qin466shot.txt"]
    assert files[0]["size_bytes"] == len(PAYLOAD)
    assert files[0]["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
