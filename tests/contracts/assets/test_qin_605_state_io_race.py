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

"""QIN-605：索引任务状态文件的瞬时读写竞争（Windows 共享冲突）契约。

回归点：状态写入走 `tmp + os.replace` 原子替换，读取直读同一路径。Windows 上
Python 的读句柄默认不带 FILE_SHARE_DELETE，两者交错时**双向**抛
`PermissionError`（读 errno=13 / 写 winerror=5），同一个冲突窗口历史上既产生
轮询 `503 INDEX_STORE_INVALID`，也产生任务误标 `INDEX_EXECUTION_FAILED` 且
`error_count=0`。

修复口径：状态读写串行化到同一把命名空间锁，且只对瞬时共享冲突做**有界**重试
（`jobs.STATE_IO_ATTEMPTS` 次、退避合计 ≤200ms）；非法 JSON 与结构非法仍然
一次失败关闭，不重试、不改写文件。
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs as jobs
from gw.core import storage
from gw.core.errors import CleanroomException

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer qin605-owner", "X-User-Role": "editor"}
STATE_NAMESPACE = "registry_index_jobs"
FILE_COUNT = 3


def _new_state():
    return jobs._JobState(
        STATE_NAMESPACE, lambda: {"sequence": 0, "jobs": {}, "keys": {}}
    )


def _state_path():
    path = storage.file_for(STATE_NAMESPACE)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _seed(payload):
    path = _state_path()
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _flaky_replace(monkeypatch, *, target, failures):
    """把 storage 模块看到的 os.replace 换成「前 failures 次对 target 抛共享冲突」。

    只替换 `gw.core.storage` 的模块属性，不动进程级 `os`；未命中 target 的替换
    原样委托，避免影响注册表等其它命名空间。
    """
    real_os = storage.os
    seen = {"raised": 0}

    def flaky_replace(src, dst):
        if str(dst) == str(target) and seen["raised"] < failures:
            seen["raised"] += 1
            raise PermissionError(5, "sharing violation")
        return real_os.replace(src, dst)

    class _FlakyOs:
        def __getattr__(self, name):
            return getattr(real_os, name)

        replace = staticmethod(flaky_replace)

    monkeypatch.setattr(storage, "os", _FlakyOs())
    return seen


def test_read_retries_transient_sharing_violation(monkeypatch):
    """读侧首次共享冲突被有界重试吸收，读到的是最新完整状态。"""
    _seed({"sequence": 2, "jobs": {"job_index_00000002": {"job_id": "j"}}, "keys": {}})
    real_read_text = Path.read_text
    calls = {"n": 0}

    def flaky_read_text(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "sharing violation")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    raw = _new_state().read()

    assert raw["sequence"] == 2
    assert "job_index_00000002" in raw["jobs"]
    assert calls["n"] == 2, "瞬时冲突应恰好多读一次，不做无界重试"


def test_read_fails_closed_after_bounded_attempts(monkeypatch):
    """持续占用超出重试预算时明确报错，不伪装成空状态。"""
    _seed({"sequence": 0, "jobs": {}, "keys": {}})
    calls = {"n": 0}

    def always_busy(self, *args, **kwargs):
        calls["n"] += 1
        raise PermissionError(13, "sharing violation")

    monkeypatch.setattr(Path, "read_text", always_busy)

    with pytest.raises(CleanroomException) as excinfo:
        _new_state().read()

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "INDEX_STORE_INVALID"
    assert calls["n"] == jobs.STATE_IO_ATTEMPTS, "重试次数必须有界且可数"


def test_corrupt_state_is_not_retried_and_untouched(monkeypatch):
    """真实损坏一次失败关闭：不重试、不回落空状态、不改写文件。"""
    path = _state_path()
    path.write_text("broken", encoding="utf-8")
    real_read_text = Path.read_text
    calls = {"n": 0}

    def counting_read_text(self, *args, **kwargs):
        calls["n"] += 1
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    with pytest.raises(CleanroomException) as excinfo:
        _new_state().read()

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "INDEX_STORE_INVALID"
    assert calls["n"] == 1, "非法 JSON 属真实损坏，不得进入重试"
    assert real_read_text(path, encoding="utf-8") == "broken"


def test_state_structure_invalid_fails_closed():
    """字段类型不合法同样立即失败关闭，不返回半成品状态。"""
    _seed({"sequence": "1", "jobs": {}, "keys": {}})

    with pytest.raises(CleanroomException) as excinfo:
        _new_state().read()

    assert excinfo.value.code == "INDEX_STORE_INVALID"


def test_write_retries_transient_replace_conflict(monkeypatch):
    """写侧替换冲突被吸收，落盘结果是最后一次提交的完整快照。"""
    path = _seed({"sequence": 0, "jobs": {}, "keys": {}})
    seen = _flaky_replace(monkeypatch, target=path, failures=2)

    _new_state().write({"sequence": 9, "jobs": {}, "keys": {}})

    assert seen["raised"] == 2
    assert json.loads(path.read_text(encoding="utf-8"))["sequence"] == 9


def test_write_fails_closed_after_bounded_attempts(monkeypatch):
    """持续替换冲突超出预算时仍抛出，不静默吞掉落盘失败。"""
    path = _seed({"sequence": 0, "jobs": {}, "keys": {}})
    _flaky_replace(monkeypatch, target=path, failures=jobs.STATE_IO_ATTEMPTS + 3)

    with pytest.raises(PermissionError):
        _new_state().write({"sequence": 9, "jobs": {}, "keys": {}})

    assert json.loads(path.read_text(encoding="utf-8"))["sequence"] == 0


def test_state_survives_cold_read_after_commit():
    """冷读：新实例读到的是最后一次完整原子提交，不是半截文件。"""
    payload = {
        "sequence": 7,
        "jobs": {"job_index_00000007": {"job_id": "job_index_00000007"}},
        "keys": {},
    }

    _new_state().write(payload)

    raw = _new_state().read()
    assert raw["sequence"] == 7
    assert raw["jobs"]["job_index_00000007"]["job_id"] == "job_index_00000007"


@pytest.fixture
def race_runtime(tmp_path, monkeypatch):
    root = tmp_path / "sources"
    root.mkdir()
    for index in range(FILE_COUNT):
        (root / f"clip-{index}.txt").write_bytes(f"source {index}".encode())
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None
    yield root
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None


def test_index_job_survives_transient_state_replace_conflict(race_runtime, monkeypatch):
    """AC4：轮询期间的瞬时状态盘冲突不得让任务误报 failed 或误增 error_count。"""
    seen_conflicts = _flaky_replace(
        monkeypatch, target=storage.file_for(STATE_NAMESPACE), failures=2
    )
    observed = []
    with TestClient(create_app()) as client:
        created = client.post(
            BASE + "/reindex",
            headers={**AUTH, "Idempotency-Key": "qin-605-state-race"},
            json={"background": True},
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            response = client.get(BASE + "/workspace-jobs/" + job_id, headers=AUTH)
            assert response.status_code == 200, response.text
            job = response.json()["job"]
            observed.append((job["status"], job["progress"]["error_count"]))
            if job["status"] in jobs.TERMINAL:
                break
            time.sleep(0.01)

    assert seen_conflicts["raised"] == 2, "注入的两次瞬时冲突应确实发生"
    assert job["status"] == "succeeded", job
    assert job["progress"]["error_count"] == 0, observed
    assert all(status != "failed" for status, _ in observed), observed
