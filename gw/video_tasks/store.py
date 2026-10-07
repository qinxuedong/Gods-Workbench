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

"""视频异步任务 SQLite 存储；状态、幂等和产物登记由事务保护。"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterator, Optional
import uuid
from gw.core.errors import CleanroomException
from gw.core.runtime_paths import MODE_DEV, MODE_PROD, RuntimePathError, _canonical, _inside, resolve_runtime_paths

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_VERSION = "3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def video_data_root() -> Path:
    """返回当前运行态已通过三根路径校验的视频数据根。"""
    try:
        return resolve_runtime_paths().video_root
    except RuntimePathError as exc:
        raise CleanroomException(503, "VIDEO_STORAGE_UNAVAILABLE", str(exc)) from None


def actor_key(context: Any) -> str:
    """从已认证上下文生成匿名稳定主体键，不保存令牌或原始 subject。"""
    subject = str(getattr(context, "subject", "") or "").strip()
    domain = str(getattr(context, "identity_domain", "") or "").strip()
    if not subject or not domain:
        raise CleanroomException(401, "IDENTITY_REQUIRED", "当前认证上下文缺少稳定主体")
    return hashlib.sha256((domain + "\0" + subject).encode("utf-8")).hexdigest()


class VideoTaskStore:
    """可跨重启恢复的单实例 SQLite 任务和授权状态。"""
    def __init__(self, root: Optional[Path] = None):
        try:
            runtime = resolve_runtime_paths()
            self.root = (video_data_root() if root is None else _canonical(root, code="VIDEO_STORAGE_UNAVAILABLE"))
            if root is not None:
                if runtime.mode == MODE_PROD and self.root != runtime.video_root:
                    raise RuntimePathError("VIDEO_PATH_NOT_ALLOWED", "生产态视频目录必须使用固定用户数据根")
                if runtime.mode == MODE_DEV and not _inside(self.root, runtime.runtime_root):
                    raise RuntimePathError("VIDEO_PATH_NOT_ALLOWED", "开发态视频目录必须位于 runtime-dev 内")
                if runtime.mode == "test" and _inside(self.root, runtime.repo_root):
                    raise RuntimePathError("VIDEO_PATH_IN_REPOSITORY", "测试态视频目录不得位于源码仓库内")
        except RuntimePathError as exc:
            raise CleanroomException(503, "VIDEO_STORAGE_UNAVAILABLE", str(exc)) from None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.artifact_root = self.root / "artifacts"
            self.work_root = self.root / "work"
            self.artifact_root.mkdir(exist_ok=True)
            self.work_root.mkdir(exist_ok=True)
            self.db_path = self.root / "video_tasks.sqlite3"
            existed = self.db_path.exists() and self.db_path.stat().st_size > 0
            db = self._connect()
            try:
                if existed:
                    check = db.execute("PRAGMA quick_check").fetchone()
                    has_schema = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='video_meta'").fetchone()
                    if not check or check[0] != "ok" or not has_schema:
                        raise sqlite3.DatabaseError("existing video database failed integrity/schema check")
                self._initialize_schema(db)
            finally:
                db.close()
        except CleanroomException:
            raise
        except Exception:
            raise CleanroomException(503, "VIDEO_STORAGE_UNAVAILABLE", "视频任务存储不可用；未重置现有数据") from None
        import threading
        self._worker_lock = threading.RLock()
        self._processes: Dict[str, Any] = {}

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _initialize_schema(db: sqlite3.Connection) -> None:
        """在单一写事务内创建或扩展视频任务schema，不重建旧数据。"""
        statements = (
            "CREATE TABLE IF NOT EXISTS video_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
            """CREATE TABLE IF NOT EXISTS video_projects(
                project_id TEXT PRIMARY KEY, owner_key TEXT NOT NULL, source_owner_key TEXT,
                granted_by TEXT NOT NULL, created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS video_project_assets(
                project_id TEXT NOT NULL, asset_id TEXT NOT NULL, granted_by TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project_id,asset_id),
                FOREIGN KEY(project_id) REFERENCES video_projects(project_id) ON DELETE CASCADE)""",
            """CREATE TABLE IF NOT EXISTS video_jobs(
                job_id TEXT PRIMARY KEY, actor_key TEXT NOT NULL, operation TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, request_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','canceled','interrupted')),
                project_id TEXT NOT NULL, canvas_id TEXT NOT NULL, entity_id TEXT NOT NULL,
                provider_id TEXT, model TEXT, upstream_task_id TEXT, result_unknown INTEGER NOT NULL DEFAULT 0,
                upstream_create_dispatched INTEGER NOT NULL DEFAULT 0, error_code TEXT, error_message TEXT,
                remote_may_continue INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(actor_key,operation,idempotency_key))""",
            "CREATE INDEX IF NOT EXISTS video_jobs_actor_updated ON video_jobs(actor_key,updated_at DESC)",
            """CREATE TABLE IF NOT EXISTS video_artifacts(
                asset_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, media_json TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES video_jobs(job_id))""",
        )
        db.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                db.execute(statement)
            row = db.execute("SELECT value FROM video_meta WHERE key='schema_version'").fetchone()
            old_version = str(row[0]) if row else None
            if old_version not in {None, "1", "2", _SCHEMA_VERSION}:
                raise sqlite3.DatabaseError("unsupported schema version")

            project_columns = {item[1] for item in db.execute("PRAGMA table_info(video_projects)").fetchall()}
            if "source_owner_key" not in project_columns:
                db.execute("ALTER TABLE video_projects ADD COLUMN source_owner_key TEXT")
            job_columns = {item[1] for item in db.execute("PRAGMA table_info(video_jobs)").fetchall()}
            if "upstream_create_dispatched" not in job_columns:
                db.execute("ALTER TABLE video_jobs ADD COLUMN upstream_create_dispatched INTEGER NOT NULL DEFAULT 0")
            if old_version == "1":
                # 旧版未区分发送前与响应中的创建请求；无法判定的任务继续保守保留风险。
                db.execute("""UPDATE video_jobs SET upstream_create_dispatched=1
                    WHERE operation='video-generation' AND
                    (upstream_task_id IS NOT NULL OR status IN ('running','canceled','interrupted'))""")
                db.execute("""UPDATE video_jobs SET remote_may_continue=1
                    WHERE operation='video-generation' AND status='canceled'""")
            db.execute(
                "INSERT INTO video_meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_SCHEMA_VERSION,),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            db = self._connect()
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
        except CleanroomException:
            raise
        except Exception:
            raise CleanroomException(503, "VIDEO_STORAGE_UNAVAILABLE", "视频任务存储暂不可用") from None

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        try:
            db = self._connect()
            try:
                yield db
            finally:
                db.close()
        except CleanroomException:
            raise
        except Exception:
            raise CleanroomException(503, "VIDEO_STORAGE_UNAVAILABLE", "视频任务读取失败") from None

    def register_project_owner(self, project_id: str, owner: str, granted_by: str) -> None:
        """绑定新项目主体；新建项目的真源owner由服务端认证上下文确定。"""
        self.claim_unowned_project(project_id, owner, granted_by, source_owner_key=owner)

    def claim_unowned_project(
        self, project_id: str, owner: str, granted_by: str, *, source_owner_key: str
    ) -> None:
        """治理主体仅能自授权；旧NULL可补来源，非NULL来源绝不静默重绑。"""
        with self.transaction() as db:
            row = db.execute(
                "SELECT owner_key,source_owner_key FROM video_projects WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO video_projects(project_id,owner_key,source_owner_key,granted_by,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (project_id, owner, source_owner_key, granted_by, _now()),
                )
                return
            if row["owner_key"] != owner:
                raise CleanroomException(409, "PROJECT_ACCESS_EXISTS", "该项目已绑定其他主体，拒绝覆盖")
            if row["source_owner_key"] is None:
                updated = db.execute(
                    "UPDATE video_projects SET source_owner_key=? "
                    "WHERE project_id=? AND owner_key=? AND source_owner_key IS NULL",
                    (source_owner_key, project_id, owner),
                )
                if updated.rowcount != 1:
                    raise CleanroomException(409, "PROJECT_SOURCE_OWNER_CONFLICT", "项目授权来源已变化，拒绝覆盖")
                return
            if row["source_owner_key"] != source_owner_key:
                raise CleanroomException(409, "PROJECT_SOURCE_OWNER_CONFLICT", "视频授权来源与项目真源owner不匹配，拒绝重绑定")

    @staticmethod
    def _validate_project_access(row: Optional[sqlite3.Row], owner: str, source_owner_key: str) -> None:
        if not row:
            raise CleanroomException(403, "PROJECT_ACCESS_REQUIRED", "项目尚无视频访问授权；请由项目创建者或治理员建立授权")
        if row["owner_key"] != owner:
            raise CleanroomException(403, "PROJECT_ACCESS_DENIED", "当前主体无该项目的视频访问权限")
        acl_source_owner = row["source_owner_key"]
        if acl_source_owner is None:
            if source_owner_key != owner:
                raise CleanroomException(403, "PROJECT_ACCESS_DENIED", "历史视频授权来源无法验证，当前主体无权访问")
        elif acl_source_owner != source_owner_key:
            raise CleanroomException(403, "PROJECT_SOURCE_MISMATCH", "视频授权来源与项目真源owner不匹配")

    def require_project_owner(self, project_id: str, owner: str, source_owner_key: str) -> None:
        with self.reader() as db:
            row = db.execute(
                "SELECT owner_key,source_owner_key FROM video_projects WHERE project_id=?", (project_id,)
            ).fetchone()
        self._validate_project_access(row, owner, source_owner_key)

    def authorize_asset(self, project_id: str, asset_id: str, owner: str, source_owner_key: str) -> None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT owner_key,source_owner_key FROM video_projects WHERE project_id=?", (project_id,)
            ).fetchone()
            self._validate_project_access(row, owner, source_owner_key)
            db.execute(
                "INSERT OR IGNORE INTO video_project_assets(project_id,asset_id,granted_by,created_at) "
                "VALUES(?,?,?,?)",
                (project_id, asset_id, owner, _now()),
            )

    def asset_is_authorized(self, project_id: str, asset_id: str, owner: str, source_owner_key: str) -> bool:
        with self.reader() as db:
            row = db.execute(
                "SELECT owner_key,source_owner_key FROM video_projects WHERE project_id=?", (project_id,)
            ).fetchone()
            self._validate_project_access(row, owner, source_owner_key)
            return bool(db.execute(
                "SELECT 1 FROM video_project_assets WHERE project_id=? AND asset_id=?", (project_id, asset_id)
            ).fetchone())

    def owned_video_artifact_path(
        self, project_id: str, asset_id: str, owner: str, source_owner_key: str
    ) -> Optional[str]:
        """仅在受权主体与当前项目真源owner匹配时返回已完成本地产物。"""
        with self.reader() as db:
            acl = db.execute(
                "SELECT owner_key,source_owner_key FROM video_projects WHERE project_id=?", (project_id,)
            ).fetchone()
            self._validate_project_access(acl, owner, source_owner_key)
            row = db.execute("""SELECT a.path FROM video_artifacts a
                JOIN video_jobs j ON j.job_id=a.job_id
                WHERE a.asset_id=? AND j.project_id=? AND j.actor_key=? AND j.status='succeeded'""",
                (asset_id, project_id, owner)).fetchone()
        return str(row["path"]) if row else None

    def create_job(self, *, actor: str, operation: str, key: str, request: Dict[str, Any],
                   project_id: str, canvas_id: str, entity_id: str,
                   provider_id: Optional[str] = None, model: Optional[str] = None) -> tuple[Dict[str, Any], bool]:
        canonical = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        now = _now()
        with self.transaction() as db:
            old = db.execute("SELECT * FROM video_jobs WHERE actor_key=? AND operation=? AND idempotency_key=?", (actor, operation, key)).fetchone()
            if old:
                if old["request_hash"] != digest:
                    raise CleanroomException(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求")
                return dict(old), False
            job_id = "job-video-" + uuid.uuid4().hex
            db.execute("""INSERT INTO video_jobs(
                job_id,actor_key,operation,idempotency_key,request_hash,request_json,status,
                project_id,canvas_id,entity_id,provider_id,model,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'queued',?,?,?,?,?,?,?)""",
                (job_id, actor, operation, key, digest, canonical, project_id, canvas_id,
                 entity_id, provider_id, model, now, now))
            return dict(db.execute("SELECT * FROM video_jobs WHERE job_id=?", (job_id,)).fetchone()), True

    def list_jobs(self, actor: str, limit: int = 50) -> list[Dict[str, Any]]:
        with self.reader() as db:
            rows = db.execute("SELECT j.* FROM video_jobs j JOIN video_projects p ON p.project_id=j.project_id AND p.owner_key=j.actor_key WHERE j.actor_key=? ORDER BY j.created_at DESC LIMIT ?",
                              (actor, min(max(int(limit), 1), 100))).fetchall()
        return [dict(row) for row in rows]

    def get_owned_job(self, job_id: str, actor: str) -> Dict[str, Any]:
        with self.reader() as db:
            row = db.execute("SELECT j.* FROM video_jobs j JOIN video_projects p ON p.project_id=j.project_id AND p.owner_key=j.actor_key WHERE j.job_id=? AND j.actor_key=?", (job_id, actor)).fetchone()
            if not row:
                raise CleanroomException(404, "VIDEO_TASK_NOT_FOUND", "视频任务不存在")
            result = dict(row)
            artifact = db.execute("SELECT * FROM video_artifacts WHERE job_id=?", (job_id,)).fetchone()
            result["artifact"] = dict(artifact) if artifact else None
            return result

    def get_by_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.reader() as db:
            row = db.execute("SELECT * FROM video_jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def recoverable(self) -> list[Dict[str, Any]]:
        """恢复未派发/已知上游作业；已派发但无引用的创建结果绝不重提。"""
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM video_jobs WHERE status IN ('queued','running') ORDER BY created_at").fetchall()
            ready = []
            for row in rows:
                if row["status"] == "queued":
                    ready.append(dict(row))
                elif row["operation"] == "video-generation" and row["upstream_task_id"]:
                    ready.append(dict(row))
                elif row["operation"] == "video-generation" and not row["upstream_create_dispatched"]:
                    # 派发屏障未越过，远端必然未收到创建请求；重启后可安全重新入队。
                    db.execute("UPDATE video_jobs SET status='queued',updated_at=? WHERE job_id=? AND status='running'",
                               (_now(), row["job_id"]))
                    ready.append({**dict(row), "status": "queued"})
                else:
                    unknown = int(row["operation"] == "video-generation" and not row["upstream_task_id"])
                    code = "VIDEO_RESULT_UNKNOWN" if unknown else "VIDEO_INTERRUPTED"
                    message = "上游创建结果未知；未自动重新提交" if unknown else "服务重启时本地渲染未完成"
                    db.execute("UPDATE video_jobs SET status='interrupted',result_unknown=?,error_code=?,error_message=?,updated_at=? WHERE job_id=? AND status='running'",
                               (unknown, code, message, _now(), row["job_id"]))
            return ready

    def mark_running(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.transaction() as db:
            db.execute("UPDATE video_jobs SET status='running',updated_at=? WHERE job_id=? AND status='queued'", (_now(), job_id))
            row = db.execute("SELECT * FROM video_jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row and row["status"] == "running" else None

    def begin_remote_create(self, job_id: str) -> bool:
        """以事务建立远端 POST 的派发线性化点；取消先提交时绝不再发送。"""
        with self.transaction() as db:
            cur = db.execute("""UPDATE video_jobs SET upstream_create_dispatched=1,remote_may_continue=1,updated_at=?
                WHERE job_id=? AND operation='video-generation' AND status='running'
                AND upstream_create_dispatched=0 AND upstream_task_id IS NULL""", (_now(), job_id))
            return cur.rowcount == 1

    def set_upstream_task(self, job_id: str, upstream_task_id: str) -> bool:
        """保存迟到 task_id 供追踪；已取消任务仍记录引用但绝不恢复轮询。"""
        with self.transaction() as db:
            row = db.execute("SELECT status,upstream_create_dispatched FROM video_jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row or not row["upstream_create_dispatched"] or row["status"] not in {"running", "canceled"}:
                return False
            db.execute("UPDATE video_jobs SET upstream_task_id=?,updated_at=? WHERE job_id=?",
                       (upstream_task_id, _now(), job_id))
            return row["status"] == "running"

    def mark_failed(self, job_id: str, code: str, message: str) -> None:
        with self.transaction() as db:
            db.execute("UPDATE video_jobs SET status='failed',error_code=?,error_message=?,updated_at=? WHERE job_id=? AND status='running'",
                       (code, message[:500], _now(), job_id))

    def mark_interrupted(self, job_id: str, code: str, message: str, unknown: bool = False) -> None:
        with self.transaction() as db:
            db.execute("UPDATE video_jobs SET status='interrupted',result_unknown=?,error_code=?,error_message=?,updated_at=? WHERE job_id=? AND status='running'",
                       (int(unknown), code, message[:500], _now(), job_id))

    def cancel(self, job_id: str, actor: str) -> Dict[str, Any]:
        with self.transaction() as db:
            row = db.execute("SELECT j.* FROM video_jobs j JOIN video_projects p ON p.project_id=j.project_id AND p.owner_key=j.actor_key WHERE j.job_id=? AND j.actor_key=?", (job_id, actor)).fetchone()
            if not row:
                raise CleanroomException(404, "VIDEO_TASK_NOT_FOUND", "视频任务不存在")
            if row["status"] in {"succeeded", "failed", "interrupted"}:
                raise CleanroomException(409, "VIDEO_TASK_TERMINAL", "视频任务已结束，无法取消")
            if row["status"] != "canceled":
                remote_may_continue = int(row["operation"] == "video-generation" and
                    (bool(row["upstream_create_dispatched"]) or bool(row["upstream_task_id"])))
                db.execute("UPDATE video_jobs SET status='canceled',remote_may_continue=?,updated_at=? WHERE job_id=? AND status IN ('queued','running')",
                           (remote_may_continue, _now(), job_id))
            return dict(db.execute("SELECT * FROM video_jobs WHERE job_id=?", (job_id,)).fetchone())

    def complete_success(self, job_id: str, temp_path: Path, media: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """同一写事务发布唯一文件、登记 asset_id 并结束；取消先到则不发布。"""
        final_path: Optional[Path] = None
        published = False
        try:
            with self.transaction() as db:
                row = db.execute("SELECT * FROM video_jobs WHERE job_id=?", (job_id,)).fetchone()
                if not row or row["status"] != "running":
                    return None
                asset_id = "ast-video-" + uuid.uuid4().hex
                final_path = self.artifact_root / (job_id + ".mp4")
                # hard link 的创建具备目标不存在检查的原子性，避免并发发布覆盖既有唯一产物。
                os.link(temp_path, final_path)
                published = True
                temp_path.unlink(missing_ok=True)
                size = final_path.stat().st_size
                db.execute("INSERT INTO video_artifacts VALUES(?,?,?,?,?,?)",
                           (asset_id, job_id, str(final_path), size, json.dumps(media, sort_keys=True), _now()))
                # 生成物自动归属创建任务的主体与项目，供同项目后续本地合并使用。
                db.execute("INSERT OR IGNORE INTO video_project_assets VALUES(?,?,?,?)",
                           (row["project_id"], asset_id, row["actor_key"], _now()))
                db.execute("UPDATE video_jobs SET status='succeeded',updated_at=?,error_code=NULL,error_message=NULL WHERE job_id=? AND status='running'",
                           (_now(), job_id))
                saved = dict(db.execute("SELECT * FROM video_jobs WHERE job_id=?", (job_id,)).fetchone())
                saved["artifact"] = {"asset_id": asset_id, "job_id": job_id, "path": str(final_path),
                                      "size_bytes": size, "media_json": json.dumps(media, sort_keys=True)}
                return saved
        except CleanroomException:
            if published and final_path:
                final_path.unlink(missing_ok=True)
            raise
        except Exception:
            if published and final_path:
                final_path.unlink(missing_ok=True)
            raise CleanroomException(503, "VIDEO_STORAGE_UNAVAILABLE", "视频产物无法安全登记") from None

    def process_for(self, job_id: str) -> Any:
        with self._worker_lock:
            return self._processes.get(job_id)

    def set_process(self, job_id: str, process: Any) -> None:
        with self._worker_lock:
            self._processes[job_id] = process

    def clear_process(self, job_id: str) -> None:
        with self._worker_lock:
            self._processes.pop(job_id, None)
