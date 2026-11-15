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

修复口径：状态读写按**两级锁**分层——事务锁（按状态文件绝对路径键控）只串行写事务，
覆盖读-改-写全程**含退避**；`storage.namespace_lock` 只包单次 I/O，退避期间不持有。
只对瞬时共享冲突做**有界**重试（`jobs.STATE_IO_ATTEMPTS` 次、退避合计 ≤200ms）；
非法 JSON 与结构非法仍然一次失败关闭，不重试、不改写文件。
"""

import json
import sys
import threading
import time
import traceback
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
CLEANUP_JOIN_TIMEOUT = 10.0


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


def _persisted_job(job_id):
    """直接读状态文件，绕过服务内存投影：这是轮询竞争真正落盘的结果。"""
    raw = json.loads(storage.file_for(STATE_NAMESPACE).read_text(encoding="utf-8"))
    return raw["jobs"][job_id]


def _committed_receipt(job_id, attempt):
    """读注册表里的提交收据（独立状态文件、与任务状态分属两个命名空间）。"""
    from gw.asset_registry import repository as repo

    return (
        repo.state()
        .read()
        .get("index_job_receipts", {})
        .get(f"{job_id}:{attempt}")
    )


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
    """AC4：同一注入场景下，收据、持久终态与冷读三处必须互相一致。

    前面的轮询断言只覆盖「接口报出的终态」。收据与冷读在别处单独测过，但那里
    没有注入竞争，不能替代同场景证据。这里在**同一次注入**里补三步：
    1. 读注册表提交收据（与任务状态分属两个命名空间，不受本次冲突影响）；
    2. 直接读状态文件，确认落盘的就是 succeeded + 零错误 + 与收据相同的 result；
    3. 关掉应用后新建 service 冷读，对照终态字段与接口观测值。
    """
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

    # 1) 注册表提交收据没有在冲突窗口里丢失，且就是任务终态的结果。
    persisted = _persisted_job(job_id)
    receipt = _committed_receipt(job_id, persisted["attempt"])
    assert receipt is not None, "成功任务必须在注册表留下提交收据"
    assert isinstance(receipt, dict) and "revision" in receipt, receipt
    assert job["result"] == receipt, "接口投影的结果必须就是注册表收据"

    # 2) 落盘终态：succeeded、零错误、结果与收据逐字段相同。
    assert persisted["status"] == "succeeded", persisted
    assert persisted["phase"] == "completed", persisted
    assert persisted["error_code"] is None, persisted
    assert persisted["error_count"] == 0, persisted
    assert persisted["result"] == receipt, "任务终态结果必须与收据一致"

    # 3) 冷读：应用已关闭，新建 service/store 从盘上重建，终态不得漂移。
    cold = jobs.service()
    cold_raw = cold.store.read()["jobs"][job_id]
    cold_view = cold.public(cold._committed_truth(cold_raw))
    for field in ("status", "phase", "attempt", "error_count", "result", "error_code"):
        assert cold_raw[field] == persisted[field], f"冷读 {field} 与落盘终态不一致"
    assert cold_view["result"] == receipt
    assert (
        cold_view["status"],
        cold_view["attempt"],
        cold_view["progress"]["error_count"],
    ) == (job["status"], job["attempt"], job["progress"]["error_count"])


# ---------------------------------------------------------------------------
# 确定性并发装置：到达竞争点用「锁获取尝试」证明，不用等待窗口。
# ---------------------------------------------------------------------------


class _Gate:
    """包装事务锁：阻塞前记录到达，取得后记录持有。

    `second_at_gate` 与 `proceed` 分开，是为让「清理放行」不污染「已到竞争点」
    这条证据——清理只置位 `proceed`，绝不置位 `second_at_gate`。
    """

    def __init__(self, inner):
        self._inner = inner
        self._g = threading.Lock()
        self.attempts = 0
        self.held = 0
        self.first_acquired = threading.Event()
        self.second_at_gate = threading.Event()
        self.proceed = threading.Event()

    def __enter__(self):
        with self._g:
            self.attempts += 1
            arrival = self.attempts
            if arrival >= 2:
                self.second_at_gate.set()
                self.proceed.set()
        ctx = self._inner.__enter__()
        with self._g:
            self.held += 1
            if arrival == 1:
                self.first_acquired.set()
        return ctx

    def __exit__(self, *exc):
        with self._g:
            self.held -= 1
        return self._inner.__exit__(*exc)


class _Worker:
    """把 worker 异常与栈带回主线程，避免「只打印线程异常、测试仍绿」。"""

    def __init__(self, name, fn):
        self.name = name
        self.fn = fn
        self.exc = None
        self.tb = None
        self.done = threading.Event()
        self._t = threading.Thread(target=self._run, name=f"qin605-{name}", daemon=True)

    def _run(self):
        try:
            self.fn()
        except BaseException as exc:  # noqa: BLE001 - 需原样带回主线程
            self.exc = exc
            self.tb = exc.__traceback__
            traceback.print_exc(file=sys.stderr)
        finally:
            self.done.set()

    def start(self):
        self._t.start()
        return self

    def join(self, timeout=CLEANUP_JOIN_TIMEOUT):
        self._t.join(timeout)
        return not self._t.is_alive()


def _instrument(monkeypatch):
    """把事务锁换成可观测包装，返回按状态文件路径取闸门的访问器。"""
    original = jobs._transaction_lock
    gates = {}

    def factory(path):
        key = str(path)
        if key not in gates:
            gates[key] = _Gate(original(path))
        return gates[key]

    monkeypatch.setattr(jobs, "_transaction_lock", factory)
    # 访问器也走 factory：事务尚未开始时调用也能拿到同一实例。
    return lambda: factory(storage.file_for(STATE_NAMESPACE))


def _observe_save(monkeypatch, gate_of, *, block_first):
    """统计 storage.save 的并发进入情况；block_first=True 时首个落盘者等闸门。

    F-1 必须用 block_first=False：那里屏障在退避钩子上，包装器再等闸门会自锁。
    """
    real = storage.save
    seen = {"calls": 0, "active": 0, "peak": 0, "in_save": threading.Event()}
    guard = threading.Lock()

    def wrapper(namespace, payload):
        with guard:
            seen["calls"] += 1
            first = seen["calls"] == 1
            seen["active"] += 1
            seen["peak"] = max(seen["peak"], seen["active"])
            seen["in_save"].set()
        if first and block_first:
            assert gate_of().proceed.wait(
                CLEANUP_JOIN_TIMEOUT
            ), "落盘窗口既无后到者也无清理放行"
        try:
            real(namespace, payload)
        finally:
            with guard:
                seen["active"] -= 1

    monkeypatch.setattr(storage, "save", wrapper)
    return seen


def _close(workers, *signals):
    """唯一清理点：先放行、再逆序有界 join，返回未退出者名单。"""
    for event in signals:
        event.set()
    return [w.name for w in reversed(workers) if not w.join()]


def _surface_worker_errors(workers):
    for worker in workers:
        if worker.exc is not None:
            raise worker.exc.with_traceback(worker.tb)


def _add_key(name="k"):
    def mutator(raw):
        raw["keys"][name] = 1

    return mutator


def test_worker_assertion_propagates_to_main_thread():
    """装置自检：子线程断言失败必须让主线程失败，不能只打印栈。"""
    worker = _Worker("boom", lambda: (_ for _ in ()).throw(AssertionError("worker-marker")))
    worker.start()
    assert worker.join(), "worker 未退出"

    with pytest.raises(AssertionError, match="worker-marker"):
        _surface_worker_errors([worker])


def test_reader_completes_while_writer_in_backoff(monkeypatch):
    """退避期间读者不被阻塞：写者仍持事务锁，读者已读完。"""
    path = _seed({"sequence": 1, "jobs": {}, "keys": {}})
    state = _new_state()
    in_backoff = threading.Event()
    release = threading.Event()
    seen_snapshot = {}

    def hook(seconds):
        in_backoff.set()
        assert release.wait(5), "测试未释放退避"

    monkeypatch.setattr(jobs, "_STATE_IO_SLEEP", hook)
    _flaky_replace(monkeypatch, target=path, failures=1)
    gate = _Gate(jobs._transaction_lock(storage.file_for(STATE_NAMESPACE)))
    monkeypatch.setattr(jobs, "_transaction_lock", lambda _path: gate)

    writer = _Worker("mutate", lambda: state.mutate(_add_key()))

    def reader():
        seen_snapshot["raw"] = state.read()

    worker_reader = _Worker("read", reader)
    workers = []
    primary = None
    try:
        workers.append(writer.start())
        assert in_backoff.wait(5), "未进入退避"
        assert gate.held == 1, "退避期间事务锁未持有"
        workers.append(worker_reader.start())
        assert worker_reader.done.wait(2), "读者在写者退避期间被阻塞"
        assert not writer.done.is_set(), "写者应仍在退避中"
    except BaseException as exc:
        primary = exc
        raise
    finally:
        stuck = _close(workers, release, gate.proceed)
        if stuck:
            message = f"清理失效：{stuck} 未退出（疑似死锁）"
            if primary is not None:
                raise AssertionError(message) from primary
            raise AssertionError(message)
    _surface_worker_errors(workers)

    assert seen_snapshot["raw"]["sequence"] == 1, "读者应读到完整提交前快照"
    assert "k" not in seen_snapshot["raw"]["keys"], "读者看到的是本次事务提交前的状态"
    final = json.loads(path.read_text(encoding="utf-8"))
    assert final["keys"] == {"k": 1}, "退避结束后写者仍要完成提交"


def _run_pair(gate, seen, first, second):
    """先启动先行者并等它持锁，再启动后到者；清理与异常回传统一在此。"""
    workers = []
    first_worker = _Worker("first", first)
    second_worker = _Worker("second", second)
    primary = None
    try:
        workers.append(first_worker.start())
        assert gate.first_acquired.wait(5), "先行者未取得事务锁"
        assert gate.held == 1, "先行者未持锁就开始后到者"
        assert seen["in_save"].wait(5), "先行者未进入落盘窗口"
        workers.append(second_worker.start())
        assert gate.second_at_gate.wait(5), "后到者未到竞争点"
        assert seen["calls"] == 1, "先行者未进入落盘窗口"
    except BaseException as exc:
        primary = exc
        raise
    finally:
        stuck = _close(workers, gate.proceed)
        if stuck:
            message = f"清理失效：{stuck} 未退出（疑似死锁）"
            if primary is not None:
                raise AssertionError(message) from primary
            raise AssertionError(message)
    _surface_worker_errors(workers)


def test_direct_write_excludes_mutate(monkeypatch):
    """E-1：write 先持事务锁，mutate 到闸门；先到先得，最终为 payload + 新增键。"""
    _seed({"sequence": 1, "jobs": {}, "keys": {}})
    gate_of = _instrument(monkeypatch)
    seen = _observe_save(monkeypatch, gate_of, block_first=True)
    state = _new_state()
    payload = {
        "sequence": 11,
        "jobs": {"job_index_00000011": {"job_id": "job_index_00000011"}},
        "keys": {},
    }

    _run_pair(
        gate_of(),
        seen,
        lambda: state.write(payload),
        lambda: state.mutate(_add_key()),
    )

    final = json.loads(_state_path().read_text(encoding="utf-8"))
    assert seen["calls"] == 2
    assert seen["peak"] == 1, "write 与 mutate 不得同时进入落盘"
    assert gate_of().attempts == 2
    assert final["sequence"] == 11
    assert final["jobs"] == payload["jobs"]
    assert final["keys"] == {"k": 1}, "mutate 基于 write 提交后的状态计算"


def test_write_after_mutate_replaces_snapshot(monkeypatch):
    """E-2：mutate 先，write 后 → 整份替换，mutate 的键不保留（非 lost update）。"""
    _seed({"sequence": 1, "jobs": {}, "keys": {}})
    gate_of = _instrument(monkeypatch)
    seen = _observe_save(monkeypatch, gate_of, block_first=True)
    state = _new_state()
    payload = {"sequence": 21, "jobs": {}, "keys": {}}

    _run_pair(
        gate_of(),
        seen,
        lambda: state.mutate(_add_key("from_mutate")),
        lambda: state.write(payload),
    )

    final = json.loads(_state_path().read_text(encoding="utf-8"))
    assert seen["calls"] == 2
    assert seen["peak"] == 1
    assert gate_of().attempts == 2
    assert final == payload, "整份快照替换：后提交的 write 覆盖前一个事务"
    assert "from_mutate" not in final["keys"]


def test_write_blocked_during_mutate_backoff(monkeypatch):
    """F-1：mutate 退避时 write 被挡在事务锁外，退避结束按序提交。"""
    path = _seed({"sequence": 1, "jobs": {}, "keys": {}})
    state = _new_state()
    gate_of = _instrument(monkeypatch)
    seen = _observe_save(monkeypatch, gate_of, block_first=False)
    in_backoff = threading.Event()
    release = threading.Event()
    calls = {"sleep": 0}

    def hook(seconds):
        calls["sleep"] += 1
        in_backoff.set()
        assert release.wait(5), "测试未释放退避"

    monkeypatch.setattr(jobs, "_STATE_IO_SLEEP", hook)
    _flaky_replace(monkeypatch, target=path, failures=1)
    payload = {"sequence": 31, "jobs": {}, "keys": {}}
    writer = _Worker("write", lambda: state.write(payload))
    mutator = _Worker("mutate", lambda: state.mutate(_add_key("dropped")))

    workers = []
    primary = None
    try:
        workers.append(mutator.start())
        assert in_backoff.wait(5), "未进入退避"
        assert gate_of().held == 1, "退避期间事务锁未持有"
        workers.append(writer.start())
        assert gate_of().second_at_gate.wait(5), "write 未到闸门"
        assert seen["calls"] == 1, "退避期间 write 已进入落盘"
    except BaseException as exc:
        primary = exc
        raise
    finally:
        stuck = _close(workers, release, gate_of().proceed)
        if stuck:
            message = f"清理失效：{stuck} 未退出（疑似死锁）"
            if primary is not None:
                raise AssertionError(message) from primary
            raise AssertionError(message)
    _surface_worker_errors(workers)

    final = json.loads(path.read_text(encoding="utf-8"))
    assert calls["sleep"] == 1
    assert seen["calls"] == 3, "mutate 冲突 1 + 重试 1 + write 1"
    assert seen["peak"] == 1
    assert final == payload, "整份替换语义：write 后提交，mutate 的键不保留"
    assert "dropped" not in final["keys"]


def test_mutate_waits_for_write_snapshot(monkeypatch):
    """F-2：write 在落盘窗口内，mutate 到闸门；write 提交后 mutate 基于新快照改。"""
    _seed({"sequence": 1, "jobs": {}, "keys": {}})
    gate_of = _instrument(monkeypatch)
    seen = _observe_save(monkeypatch, gate_of, block_first=True)
    state = _new_state()
    payload = {"sequence": 41, "jobs": {"j": {"job_id": "j"}}, "keys": {}}
    writer = _Worker("write", lambda: state.write(payload))
    mutator = _Worker("mutate", lambda: state.mutate(_add_key()))

    workers = []
    primary = None
    try:
        workers.append(writer.start())
        assert gate_of().first_acquired.wait(5), "write 未取得事务锁"
        assert seen["in_save"].wait(5), "write 未进入落盘窗口"
        workers.append(mutator.start())
        assert gate_of().second_at_gate.wait(5), "mutate 未到闸门"
        assert seen["calls"] == 1, "mutate 已进入落盘"
    except BaseException as exc:
        primary = exc
        raise
    finally:
        stuck = _close(workers, gate_of().proceed)
        if stuck:
            message = f"清理失效：{stuck} 未退出（疑似死锁）"
            if primary is not None:
                raise AssertionError(message) from primary
            raise AssertionError(message)
    _surface_worker_errors(workers)

    final = json.loads(_state_path().read_text(encoding="utf-8"))
    assert seen["calls"] == 2
    assert seen["peak"] == 1
    assert final["sequence"] == payload["sequence"]
    assert final["jobs"] == payload["jobs"], "旧快照不得覆盖 write 提交"
    assert final["keys"] == {"k": 1}


def test_concurrent_mutate_keeps_every_update(monkeypatch):
    """8 线程 × 5 次 mutate：40 个键一个都不能丢（写事务互斥）。"""
    _seed({"sequence": 1, "jobs": {}, "keys": {}})
    state = _new_state()
    monkeypatch.setattr(jobs, "_STATE_IO_SLEEP", lambda _seconds: None)
    rounds, width = 5, 8
    workers = []

    def worker(index):
        def run():
            for round_index in range(rounds):
                state.mutate(_add_key(f"k{index}_{round_index}"))

        return run

    for index in range(width):
        workers.append(_Worker(f"w{index}", worker(index)).start())
    for item in workers:
        assert item.join(), f"{item.name} 未退出"
    _surface_worker_errors(workers)

    final = json.loads(_state_path().read_text(encoding="utf-8"))
    assert len(final["keys"]) == rounds * width
    assert final["sequence"] == 1, "mutate 不改 sequence"


def test_mutate_runs_mutator_once_under_conflicts(monkeypatch):
    """C：mutate 入口持续冲突时恰尝试 STATE_IO_ATTEMPTS 次，mutator 只跑一次。"""
    path = _seed({"sequence": 1, "jobs": {}, "keys": {}})
    monkeypatch.setattr(jobs, "_STATE_IO_SLEEP", lambda _seconds: None)
    seen = _flaky_replace(
        monkeypatch, target=path, failures=jobs.STATE_IO_ATTEMPTS + 3
    )
    calls = {"mutator": 0}

    def mutator(raw):
        calls["mutator"] += 1
        raw["sequence"] = 99

    with pytest.raises(PermissionError):
        _new_state().mutate(mutator)

    assert seen["raised"] == jobs.STATE_IO_ATTEMPTS, "重试次数有界且可数"
    assert calls["mutator"] == 1, "重试只包单次 I/O，不重跑 mutator"
    assert json.loads(path.read_text(encoding="utf-8"))["sequence"] == 1
