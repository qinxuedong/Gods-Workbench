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

"""真实素材索引后台任务；单实例、有限队列、持续授权及提交收据。"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gw.core import storage
from gw.core.auth import EDIT_ROLES
from gw.core.config import load_runtime_auth_config
from gw.core.errors import CleanroomException

MAX_ACTIVE = 16
MAX_JOBS = 1000
MAX_FILES = 250000
MAX_ENTRIES = 500000
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
ACTIONS = {"pause": {"queued", "running"}, "resume": {"paused"},
           "cancel": {"queued", "running", "paused"},
           "retry": {"failed", "cancelled", "interrupted"}}


def _error(status, code, message):
    raise CleanroomException(status, code, message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _actor(auth):
    return {"subject": auth.subject, "mode": auth.mode, "domain": auth.identity_domain, "role": auth.role}


def _owner(actor):
    return _digest([actor["mode"], actor["domain"], actor["subject"]])


def _roots():
    """来源别名只能指向允许根；允许根另有不含路径的稳定标识。"""
    from gw.settings.service import default_storage_settings_service
    roots = storage.allowed_roots()
    if not roots:
        _error(503, "INDEX_SOURCE_NOT_AVAILABLE", "未配置允许根目录")
    canonical = {"root_" + hashlib.sha256(os.path.normcase(str(p)).encode()).hexdigest()[:24]: p for p in roots}
    aliases = dict(canonical)
    for item in default_storage_settings_service.get_snapshot().local_libraries:
        path = Path(str(item.get("path") or ""))
        if path.is_absolute() and path.resolve() in roots:
            aliases[str(item["id"])] = path.resolve()
    return canonical, aliases


def _admission(actor):
    """只检查服务器本地可撤销授权；不把会话有效冒称远端IdP实时授权。"""
    runtime = load_runtime_auth_config()
    if runtime.mode != actor["mode"]:
        _error(403, "INDEX_AUTH_CHANGED", "认证配置已改变，任务停止")
    if actor["mode"] == "local_account":
        from gw.core.local_accounts import database
        with database() as db:
            row = db.execute("SELECT role FROM local_users WHERE user_id=?", (actor["subject"],)).fetchone()
            live = db.execute("SELECT 1 FROM local_sessions WHERE user_id=? AND expires_at>? LIMIT 1",
                              (actor["subject"], time.time())).fetchone()
        if not row or row["role"] not in EDIT_ROLES or not live:
            _error(403, "INDEX_AUTH_REVOKED", "发起账户写权限或会话已失效")
    elif actor["mode"] == "oidc":
        from gw.core import session
        if actor["domain"] != "oidc:" + runtime.oidc.issuer:
            _error(403, "INDEX_AUTH_CHANGED", "身份域已改变")
        now = time.time()
        if not any(s.principal.get("username") == actor["subject"] and s.principal.get("role") in EDIT_ROLES
                   and min(s.expires_at, s.absolute_expires_at) > now for s in list(session._SESSIONS.values())):
            _error(403, "INDEX_SESSION_REQUIRED", "后台索引需要持续有效的服务端登录会话")
    elif actor["role"] not in EDIT_ROLES:
        _error(403, "INDEX_AUTH_REVOKED", "发起主体无写权限")


class _JobState(storage.JsonState):
    """任务真源损坏时失败关闭，不能回退空表复用稳定ID。"""
    def read(self):
        path = storage.data_root() / "registry_index_jobs.json"
        if not path.exists():
            return {"sequence": 0, "jobs": {}, "keys": {}}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if type(raw["sequence"]) is not int or not isinstance(raw["jobs"], dict) or not isinstance(raw["keys"], dict):
                raise ValueError()
            return raw
        except (OSError, ValueError, KeyError, TypeError):
            _error(503, "INDEX_STORE_INVALID", "索引任务持久状态不可读取")


class IndexJobs:
    """进度独立持久化；取消/暂停和注册表提交在同一线程锁内线性化。"""
    def __init__(self):
        self.root = storage.data_root().resolve()
        self.store = _JobState("registry_index_jobs", lambda: {"sequence": 0, "jobs": {}, "keys": {}})
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gw-index")
        self.closed = False
        self._recover()

    def _recover(self):
        from gw.asset_registry import repository as repo
        receipts = repo.state().read().get("index_job_receipts", {})
        def restore(raw):
            for job in raw["jobs"].values():
                receipt = receipts.get(f'{job["job_id"]}:{job["attempt"]}')
                if receipt:
                    job.update(status="succeeded", result=receipt, error_code=None)
                elif job["status"] not in TERMINAL:
                    job.update(status="interrupted", error_code="INDEX_PROCESS_INTERRUPTED")
                else:
                    continue
                job["version"] += 1
                job["updated_at"] = storage.now_iso()
        self.store.mutate(restore)

    def shutdown(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def _check(self, job):
        if self.closed or storage.data_root().resolve() != self.root:
            _error(409, "INDEX_PROCESS_INTERRUPTED", "任务执行环境已停止")
        _admission(job["_actor"])
        canonical, aliases = _roots()
        fingerprint = _digest({k: str(v) for k, v in sorted(aliases.items())})
        if fingerprint != job["_fingerprint"]:
            _error(403, "INDEX_ROOTS_CHANGED", "允许根或来源配置已改变")
        return canonical

    def _job(self, job_id, auth):
        job = self.store.read()["jobs"].get(job_id)
        if not job or job["_owner"] != _owner(_actor(auth)):
            _error(404, "WORKSPACE_JOB_NOT_FOUND", "工作区任务不存在")
        return self._committed_truth(job)

    def _committed_truth(self, job):
        """业务提交收据高于任务快照；写状态失败也绝不开放取消或重试。"""
        from gw.asset_registry import repository as repo
        receipt = repo.state().read().get("index_job_receipts", {}).get(f'{job["job_id"]}:{job["attempt"]}')
        if receipt is not None and job["status"] != "succeeded":
            job.update(status="succeeded", result=receipt, error_code=None)
            job["version"] += 1
            try:
                self._save(job)
            except OSError:
                # 状态盘仍故障时按已持久化收据投影成功；动作判断使用同一投影。
                pass
        return job

    def _save(self, job):
        job["updated_at"] = storage.now_iso()
        self.store.mutate(lambda raw: raw["jobs"].__setitem__(job["job_id"], job))

    @staticmethod
    def public(job):
        public = {k: v for k, v in job.items() if not k.startswith("_")}
        public["has_error"] = bool(job.get("error_code"))
        public["progress"] = {"scanned": job["scanned"], "error_count": job["error_count"]}
        return public

    def detail(self, job_id, auth):
        with self.lock:
            job = self._job(job_id, auth)
            writable = auth.role in EDIT_ROLES
            actions = {a: writable and job["status"] in states and (a != "retry" or job["attempt"] < 5) for a, states in ACTIONS.items()}
            return {"job": self.public(job), "actions": actions,
                    "action_reasons": {a: "当前状态或权限不允许此操作" for a, ok in actions.items() if not ok},
                    "data_status": "ok"}

    def listing(self, auth):
        with self.lock:
            owner = _owner(_actor(auth))
            jobs = [self.public(self._committed_truth(j)) for j in self.store.read()["jobs"].values() if j["_owner"] == owner]
            return sorted(jobs, key=lambda j: j["created_at"], reverse=True)[:100]

    def submit(self, kind, payload, auth, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 160:
            _error(400, "INDEX_IDEMPOTENCY_KEY_REQUIRED", "后台索引需要不超过160字符的幂等键")
        actor = _actor(auth)
        _admission(actor)
        canonical, aliases = _roots()
        selected = payload.get("roots")
        if selected is not None and (not isinstance(selected, list) or not selected or len(selected) > 64
                                      or any(not isinstance(x, str) or x not in aliases for x in selected)):
            _error(400, "INDEX_ROOT_INVALID", "来源必须为已准入的根标识")
        root_ids = sorted(k for k, p in canonical.items() if selected is None or p in [aliases[x] for x in selected])
        max_files = payload.get("max_files", MAX_FILES)
        if type(max_files) is not int or not 1 <= max_files <= MAX_FILES:
            _error(400, "INDEX_LIMIT_INVALID", "扫描文件数上限无效")
        hash_files = payload.get("hash_files", False)
        if not isinstance(hash_files, bool):
            _error(400, "INDEX_HASH_INVALID", "hash_files必须为布尔值")
        request = {"kind": kind, "roots": root_ids, "max_files": max_files, "hash_files": hash_files}
        fingerprint = _digest({k: str(v) for k, v in sorted(aliases.items())})
        request_digest = _digest([request, fingerprint])
        key_digest = _digest([_owner(actor), key])
        with self.condition:
            raw = self.store.read()
            previous = raw["keys"].get(key_digest)
            if previous:
                job = self._committed_truth(raw["jobs"][previous])
                if job["_request_digest"] != request_digest:
                    _error(409, "INDEX_IDEMPOTENCY_CONFLICT", "幂等键已用于不同索引请求")
                return self._accepted(job)
            if len(raw["jobs"]) >= MAX_JOBS or sum(j["status"] not in TERMINAL for j in raw["jobs"].values()) >= MAX_ACTIVE:
                _error(429, "INDEX_QUEUE_FULL", "索引任务容量已满")
            raw["sequence"] += 1
            job_id = f'job_index_{raw["sequence"]:08d}'
            job = {"job_id": job_id, "job_type": "asset.index" if kind == "reindex" else "asset.index.sync",
                   "name": "重建素材索引" if kind == "reindex" else "同步索引差集", "status": "queued", "version": 1,
                   "attempt": 1, "max_attempts": 5, "created_at": storage.now_iso(), "updated_at": storage.now_iso(),
                   "scanned": 0, "error_count": 0, "result": None, "error_code": None,
                   "_actor": actor, "_owner": _owner(actor), "_fingerprint": fingerprint,
                   "_request": request, "_request_digest": request_digest}
            raw["jobs"][job_id] = job
            raw["keys"][key_digest] = job_id
            self.store.write(raw)
            self._dispatch(job)
            return self._accepted(job)

    def _dispatch(self, job):
        try:
            self.executor.submit(self._run, job["job_id"], job["attempt"])
        except RuntimeError:
            job.update(status="failed", error_code="INDEX_EXECUTOR_UNAVAILABLE")
            job["version"] += 1
            self._save(job)

    def _accepted(self, job):
        return {"job_id": job["job_id"], "job": self.public(job),
                "poll_hint": {"url": "/api/asset-registry/workspace-jobs/" + job["job_id"], "interval_ms": 500},
                "data_status": "ok"}

    def operate(self, job_id, action, payload, auth):
        if action not in ACTIONS:
            _error(400, "INVALID_ACTION", "不支持的任务操作")
        with self.condition:
            job = self._job(job_id, auth)
            expected = payload.get("expected_version")
            if type(expected) is not int or expected < 1:
                _error(400, "EXPECTED_VERSION_REQUIRED", "任务操作必须携带expected_version")
            if expected != job["version"]:
                _error(409, "VERSION_CONFLICT", "任务版本已改变，请重新读取")
            self._check(job)
            if job["status"] not in ACTIONS[action] or (action == "retry" and job["attempt"] >= 5):
                _error(409, "INDEX_ACTION_CONFLICT", "当前任务状态不允许此操作")
            if action == "retry":
                if sum(j["status"] not in TERMINAL for j in self.store.read()["jobs"].values()) >= MAX_ACTIVE:
                    _error(429, "INDEX_QUEUE_FULL", "索引任务容量已满")
                job.update(status="queued", attempt=job["attempt"] + 1, scanned=0, error_count=0, result=None, error_code=None)
            else:
                job["status"] = {"pause": "paused", "resume": "running", "cancel": "cancelled"}[action]
            job["version"] += 1
            self._save(job)
            self.condition.notify_all()
            if action == "retry":
                self._dispatch(job)
            return self.detail(job_id, auth)

    def _checkpoint(self, job_id, attempt):
        with self.condition:
            while True:
                job = self.store.read()["jobs"][job_id]
                if job["attempt"] != attempt or job["status"] in TERMINAL:
                    raise _Stopped()
                self._check(job)
                if job["status"] != "paused":
                    return job
                self.condition.wait(0.2)

    def _run(self, job_id, attempt):
        try:
            with self.condition:
                job = self.store.read()["jobs"][job_id]
                if job["attempt"] != attempt or job["status"] in TERMINAL:
                    return
                if job["status"] == "queued":
                    job["status"] = "running"
                    job["version"] += 1
                    self._save(job)
            job = self._checkpoint(job_id, attempt)
            roots = self._check(job)
            files = self._scan(job_id, attempt, roots, job["_request"])
            with self.condition:
                job = self._checkpoint(job_id, attempt)
                if job["error_count"]:
                    _error(503, "INDEX_FILE_ERRORS", "扫描存在不可读取文件，未提交不完整索引")
                result = self._commit(job, files)
                job.update(status="succeeded", result=result, scanned=len(files), error_code=None)
                job["version"] += 1
                self._save(job)
        except _Stopped:
            return
        except Exception as exc:
            with self.condition:
                if storage.data_root().resolve() != self.root:
                    return
                job = self.store.read()["jobs"].get(job_id)
                if not job or job["attempt"] != attempt or job["status"] in TERMINAL:
                    return
                # 注册表已提交而任务状态落盘失败时，以同事务收据为准。
                from gw.asset_registry import repository as repo
                receipt = repo.state().read().get("index_job_receipts", {}).get(f"{job_id}:{attempt}")
                job.update(status="succeeded" if receipt else ("interrupted" if self.closed else "failed"), result=receipt,
                           error_code=None if receipt else (exc.code if isinstance(exc, CleanroomException) else "INDEX_EXECUTION_FAILED"))
                job["version"] += 1
                try:
                    self._save(job)
                except Exception:
                    pass  # 恢复时只相信注册表收据，禁止重新提交。

    def _children(self, job_id, attempt, folder, root):
        # 目录项枚举及每次磁盘读取均在检查点锁内，paused后不继续扫描。
        with self.condition:
            self._checkpoint(job_id, attempt)
            if folder.resolve() != folder.absolute() or not folder.resolve().is_relative_to(root):
                _error(403, "INDEX_PATH_CHANGED", "扫描目录已改变")
            iterator = os.scandir(folder)
        try:
            while True:
                with self.condition:
                    self._checkpoint(job_id, attempt)
                    try:
                        child = next(iterator)
                    except StopIteration:
                        break
                yield child
        finally:
            iterator.close()

    def _hash(self, job_id, attempt, path, expected):
        with self.condition:
            self._checkpoint(job_id, attempt)
            if path.resolve() != path.absolute():
                _error(403, "INDEX_PATH_CHANGED", "摘要源路径已改变")
            handle = path.open("rb")
            actual = os.fstat(handle.fileno())
            if (actual.st_ino, actual.st_dev, actual.st_size) != (expected.st_ino, expected.st_dev, expected.st_size):
                handle.close()
                _error(409, "INDEX_FILE_CHANGED", "摘要源文件已改变")
        digest = hashlib.sha256()
        read_bytes = 0
        try:
            while True:
                with self.condition:
                    self._checkpoint(job_id, attempt)
                    chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                read_bytes += len(chunk)
                if read_bytes > 512 * 1024 * 1024:
                    _error(413, "INDEX_HASH_LIMIT", "单个文件超过摘要计算上限")
                digest.update(chunk)
            final = os.fstat(handle.fileno())
            if (final.st_size, final.st_mtime_ns) != (expected.st_size, expected.st_mtime_ns):
                _error(409, "INDEX_FILE_CHANGED", "摘要计算期间文件已改变")
        finally:
            handle.close()
        return digest.hexdigest()

    def _scan(self, job_id, attempt, roots, request):
        files = []
        entries = 0
        for root_id in request["roots"]:
            root = roots[root_id]
            with self.condition:
                self._checkpoint(job_id, attempt)
                if not root.is_dir():
                    _error(503, "INDEX_ROOT_UNAVAILABLE", "索引来源目录不可用")
            pending = [root]
            while pending:
                folder = pending.pop()
                for child in self._children(job_id, attempt, folder, root):
                    entries += 1
                    if entries > MAX_ENTRIES:
                        _error(413, "INDEX_SCAN_LIMIT", "扫描目录项数量超过上限")
                    try:
                        with self.condition:
                            self._checkpoint(job_id, attempt)
                            # Windows DirEntry缓存的inode/dev可能为0；使用真实lstat作身份比较。
                            info = Path(child.path).lstat()
                            if child.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                                continue
                            path = Path(child.path)
                            if path.resolve() != path.absolute() or not path.resolve().is_relative_to(root):
                                continue
                            if stat.S_ISDIR(info.st_mode):
                                pending.append(path)
                                continue
                            if not stat.S_ISREG(info.st_mode):
                                continue
                            if len(files) >= request["max_files"]:
                                _error(413, "INDEX_SCAN_LIMIT", "扫描文件数量超过上限")
                            item = {"root_id": root_id, "display_path": path.relative_to(root).as_posix(),
                                    "name": path.name, "size_bytes": info.st_size}
                        if request["hash_files"]:
                            if info.st_size > 512 * 1024 * 1024:
                                _error(413, "INDEX_HASH_LIMIT", "单个文件超过摘要计算上限")
                            item["sha256"] = self._hash(job_id, attempt, path, info)
                        files.append(item)
                        if len(files) % 25 == 0:
                            with self.condition:
                                job = self._checkpoint(job_id, attempt)
                                job["scanned"] = len(files)
                                self._save(job)
                    except OSError:
                        with self.condition:
                            job = self._checkpoint(job_id, attempt)
                            job["error_count"] += 1
                            self._save(job)
                            if job["error_count"] >= 100:
                                _error(503, "INDEX_FILE_ERRORS", "不可读取文件数量超过上限")
        return files

    def _commit(self, job, files):
        from gw.asset_registry import repository as repo
        self._check(job)
        receipt_id = f'{job["job_id"]}:{job["attempt"]}'
        def commit(raw):
            self._check(job)
            receipts = raw.setdefault("index_job_receipts", {})
            if receipt_id in receipts:
                return receipts[receipt_id]
            known = {(a.get("root_id"), a.get("display_path")) for a in raw["assets"].values()}
            disk = {(x["root_id"], x["display_path"]) for x in files}
            added = 0
            if job["_request"]["kind"] == "reindex":
                for item in files:
                    identity = (item["root_id"], item["display_path"])
                    if identity in known:
                        continue
                    asset_id = repo._seq(raw, "ast_", raw["assets"].keys())
                    raw["assets"][asset_id] = repo._plain_asset(asset_id, item["name"], "indexed", **{k:v for k,v in item.items() if k != "name"})
                    known.add(identity)
                    added += 1
                if added:
                    repo._bump(raw)
                result = {"indexed": added, "scanned": len(files), "revision": raw["revision"]}
            else:
                scoped = {x for x in known if x[0] in job["_request"]["roots"]}
                result = {"on_disk": len(disk), "registered": len(scoped),
                          "missing_from_registry": len(disk - scoped), "missing_from_disk": len(scoped - disk),
                          "revision": raw["revision"]}
            receipts[receipt_id] = result
            return result
        return repo.state().mutate(commit)


class _Stopped(Exception):
    """取消或新尝试已接管，不落失败状态覆盖新任务。"""


_default = None
_default_lock = threading.Lock()


def service():
    global _default
    with _default_lock:
        root = storage.data_root().resolve()
        if _default is None or _default.root != root or _default.closed:
            if _default is not None:
                _default.shutdown()
            _default = IndexJobs()
        return _default


def shutdown():
    """应用关闭只停止自身索引线程，不清理任务、资产和用户文件。"""
    if _default is not None:
        _default.shutdown()
