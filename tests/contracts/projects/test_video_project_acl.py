"""视频项目持久 ACL 的所有权不变量回归。"""

import concurrent.futures
import sqlite3

import pytest

from gw.core.errors import CleanroomException
from gw.video_tasks.store import VideoTaskStore


def test_project_owner_registration_is_idempotent_but_never_rebinds(tmp_path):
    """同一主体可重试绑定；不同主体不得覆盖已有 ACL。"""
    store = VideoTaskStore(tmp_path / "video")
    store.register_project_owner("prj-stable", "owner-a", "owner-a")
    store.register_project_owner("prj-stable", "owner-a", "owner-a")

    with pytest.raises(CleanroomException) as excinfo:
        store.register_project_owner("prj-stable", "owner-b", "owner-b")

    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "PROJECT_ACCESS_EXISTS"
    with store.reader() as db:
        row = db.execute(
            "SELECT owner_key,granted_by FROM video_projects WHERE project_id=?",
            ("prj-stable",),
        ).fetchone()
    assert row["owner_key"] == "owner-a"
    assert row["granted_by"] == "owner-a"


def test_claim_binds_source_without_rebinding_actor_or_refreshing_grant(tmp_path):
    """claim只补来源或首次自授权；不得覆盖其他主体或刷新历史授权元数据。"""
    store = VideoTaskStore(tmp_path / "video")
    store.claim_unowned_project(
        "prj-claim", "grantee-b", "grantee-b", source_owner_key="source-a"
    )
    with store.reader() as db:
        original = dict(db.execute(
            "SELECT * FROM video_projects WHERE project_id=?", ("prj-claim",)
        ).fetchone())

    store.claim_unowned_project(
        "prj-claim", "grantee-b", "grantee-b", source_owner_key="source-a"
    )
    with pytest.raises(CleanroomException) as mismatch:
        store.claim_unowned_project(
            "prj-claim", "grantee-b", "grantee-b", source_owner_key="source-c"
        )
    assert mismatch.value.status_code == 409
    assert mismatch.value.code == "PROJECT_SOURCE_OWNER_CONFLICT"
    with pytest.raises(CleanroomException) as takeover:
        store.claim_unowned_project(
            "prj-claim", "actor-c", "actor-c", source_owner_key="source-a"
        )
    assert takeover.value.status_code == 409
    assert takeover.value.code == "PROJECT_ACCESS_EXISTS"

    with store.reader() as db:
        after = dict(db.execute(
            "SELECT * FROM video_projects WHERE project_id=?", ("prj-claim",)
        ).fetchone())
    assert after == original


def test_legacy_null_source_fails_closed_until_same_actor_governance_claim(tmp_path):
    """旧NULL仅允许真源owner本人；grantee需经显式claim补来源。"""
    store = VideoTaskStore(tmp_path / "video")
    store.register_project_owner("prj-owner", "source-a", "source-a")
    with store.transaction() as db:
        db.execute("UPDATE video_projects SET source_owner_key=NULL WHERE project_id=?", ("prj-owner",))
    store.require_project_owner("prj-owner", "source-a", "source-a")
    with store.reader() as db:
        assert db.execute(
            "SELECT source_owner_key FROM video_projects WHERE project_id='prj-owner'"
        ).fetchone()[0] is None

    store.register_project_owner("prj-legacy", "grantee-b", "legacy-grant")
    with store.transaction() as db:
        db.execute("UPDATE video_projects SET source_owner_key=NULL WHERE project_id=?", ("prj-legacy",))
    with store.reader() as db:
        before = dict(db.execute(
            "SELECT * FROM video_projects WHERE project_id='prj-legacy'"
        ).fetchone())

    with pytest.raises(CleanroomException) as before_claim:
        store.require_project_owner("prj-legacy", "grantee-b", "source-a")
    assert before_claim.value.status_code == 403
    store.claim_unowned_project(
        "prj-legacy", "grantee-b", "governor-b", source_owner_key="source-a"
    )
    store.require_project_owner("prj-legacy", "grantee-b", "source-a")
    with store.reader() as db:
        after = dict(db.execute(
            "SELECT * FROM video_projects WHERE project_id='prj-legacy'"
        ).fetchone())
    assert after["source_owner_key"] == "source-a"
    assert after["granted_by"] == before["granted_by"]
    assert after["created_at"] == before["created_at"]
    with pytest.raises(CleanroomException) as source_owner:
        store.require_project_owner("prj-legacy", "source-a", "source-a")
    assert source_owner.value.status_code == 403
    with pytest.raises(CleanroomException) as other:
        store.require_project_owner("prj-legacy", "actor-c", "source-a")
    assert other.value.status_code == 403


def test_legacy_null_claim_update_failure_rolls_back_entire_acl_row(tmp_path):
    """失败的NULL来源补齐回滚，既有受权主体与grant元数据不变。"""
    store = VideoTaskStore(tmp_path / "video")
    store.register_project_owner("prj-legacy-fail", "grantee-b", "legacy-grant")
    with store.transaction() as db:
        db.execute(
            "UPDATE video_projects SET source_owner_key=NULL WHERE project_id=?",
            ("prj-legacy-fail",),
        )
        db.execute("""CREATE TRIGGER reject_source_owner_backfill
            BEFORE UPDATE OF source_owner_key ON video_projects
            WHEN OLD.source_owner_key IS NULL
            BEGIN SELECT RAISE(ABORT,'blocked test source backfill'); END""")
        before = dict(db.execute(
            "SELECT * FROM video_projects WHERE project_id='prj-legacy-fail'"
        ).fetchone())

    with pytest.raises(CleanroomException) as excinfo:
        store.claim_unowned_project(
            "prj-legacy-fail", "grantee-b", "governor-b", source_owner_key="source-a"
        )
    assert excinfo.value.status_code == 503
    with store.reader() as db:
        after = dict(db.execute(
            "SELECT * FROM video_projects WHERE project_id='prj-legacy-fail'"
        ).fetchone())
    assert after == before


def test_two_different_claims_serialize_to_exactly_one_acl_winner(tmp_path):
    """SQLite写事务串行化竞争claim，最多一个主体可建立项目授权。"""
    store = VideoTaskStore(tmp_path / "video")

    def claim(actor):
        try:
            store.claim_unowned_project(
                "prj-race", actor, actor, source_owner_key="source-a"
            )
            return "won"
        except CleanroomException as exc:
            return exc.code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, ("actor-b", "actor-c")))
    assert outcomes.count("won") == 1
    assert outcomes.count("PROJECT_ACCESS_EXISTS") == 1
    with store.reader() as db:
        row = db.execute(
            "SELECT owner_key,source_owner_key FROM video_projects WHERE project_id=?",
            ("prj-race",),
        ).fetchone()
    assert row["owner_key"] in {"actor-b", "actor-c"}
    assert row["source_owner_key"] == "source-a"


def _create_legacy_video_database(root, *, version="1", reject_version_upgrade=False):
    """创建旧版SQLite结构，保持迁移测试与运行期真数据完全隔离。"""
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "video_tasks.sqlite3")
    try:
        db.executescript("""
            CREATE TABLE video_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE video_projects(
                project_id TEXT PRIMARY KEY, owner_key TEXT NOT NULL,
                granted_by TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE video_jobs(
                job_id TEXT PRIMARY KEY, actor_key TEXT NOT NULL, operation TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, request_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','canceled','interrupted')),
                project_id TEXT NOT NULL, canvas_id TEXT NOT NULL, entity_id TEXT NOT NULL,
                provider_id TEXT, model TEXT, upstream_task_id TEXT, result_unknown INTEGER NOT NULL DEFAULT 0,
                error_code TEXT, error_message TEXT, remote_may_continue INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(actor_key,operation,idempotency_key));
            CREATE TABLE video_artifacts(
                asset_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, media_json TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES video_jobs(job_id));
        """)
        db.execute("INSERT INTO video_meta(key,value) VALUES('schema_version',?)", (version,))
        db.execute(
            "INSERT INTO video_projects(project_id,owner_key,granted_by,created_at) VALUES(?,?,?,?)",
            ("prj-legacy", "source-a", "source-a", "2026-01-01T00:00:00+00:00"),
        )
        rows = [
            ("job-canceled", "actor-a", "video-generation", "idem-canceled", "hash-c", "{}", "canceled",
             "prj-legacy", "canvas-a", "entity-a", "provider-a", "model-a", None, 0, None, None, 0,
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:01+00:00"),
            ("job-succeeded", "actor-a", "video-generation", "idem-succeeded", "hash-s", "{}", "succeeded",
             "prj-legacy", "canvas-a", "entity-a", "provider-a", "model-a", "upstream-a", 0, None, None, 0,
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:02+00:00"),
        ]
        db.executemany(
            "INSERT INTO video_jobs(job_id,actor_key,operation,idempotency_key,request_hash,request_json,status,"
            "project_id,canvas_id,entity_id,provider_id,model,upstream_task_id,result_unknown,error_code,error_message,"
            "remote_may_continue,created_at,updated_at) VALUES(" + ",".join("?" for _ in rows[0]) + ")",
            rows,
        )
        db.execute(
            "INSERT INTO video_artifacts(asset_id,job_id,path,size_bytes,media_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            ("asset-succeeded", "job-succeeded", "artifact.mp4", 123, "{}", "2026-01-01T00:00:02+00:00"),
        )
        if reject_version_upgrade:
            db.executescript("""
                CREATE TRIGGER reject_video_schema_upgrade BEFORE UPDATE ON video_meta
                WHEN NEW.key='schema_version' AND NEW.value='3'
                BEGIN SELECT RAISE(ABORT,'blocked test migration'); END;
            """)
        db.commit()
    finally:
        db.close()


def test_old_video_schema_migration_preserves_acl_jobs_artifacts_and_risk(tmp_path):
    """舊schema只追加来源列；任务、幂等键、产物和旧风险状态完整保留。"""
    root = tmp_path / "legacy-video"
    _create_legacy_video_database(root, version="1")

    store = VideoTaskStore(root)
    with store.reader() as db:
        assert db.execute("SELECT value FROM video_meta WHERE key='schema_version'").fetchone()[0] == "3"
        project = db.execute("SELECT * FROM video_projects WHERE project_id='prj-legacy'").fetchone()
        assert project["owner_key"] == "source-a"
        assert project["granted_by"] == "source-a"
        assert project["source_owner_key"] is None
        jobs = {row["job_id"]: dict(row) for row in db.execute("SELECT * FROM video_jobs")}
        artifact = db.execute("SELECT * FROM video_artifacts WHERE asset_id='asset-succeeded'").fetchone()
    assert set(jobs) == {"job-canceled", "job-succeeded"}
    assert jobs["job-canceled"]["idempotency_key"] == "idem-canceled"
    assert jobs["job-canceled"]["upstream_create_dispatched"] == 1
    assert jobs["job-canceled"]["remote_may_continue"] == 1
    assert jobs["job-succeeded"]["upstream_task_id"] == "upstream-a"
    assert artifact["job_id"] == "job-succeeded"
    assert artifact["size_bytes"] == 123

    VideoTaskStore(root)
    with store.reader() as db:
        assert db.execute("SELECT source_owner_key FROM video_projects WHERE project_id='prj-legacy'").fetchone()[0] is None
        assert db.execute("SELECT COUNT(*) FROM video_jobs").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM video_artifacts").fetchone()[0] == 1


def test_video_schema_migration_failure_rolls_back_added_column(tmp_path):
    """schema版本写入失败时，ALTER和version变更同事务回滚，不留下半迁移库。"""
    root = tmp_path / "legacy-video-fail"
    _create_legacy_video_database(root, version="2", reject_version_upgrade=True)
    with pytest.raises(CleanroomException) as excinfo:
        VideoTaskStore(root)
    assert excinfo.value.status_code == 503
    with sqlite3.connect(root / "video_tasks.sqlite3") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(video_projects)")}
        version = db.execute("SELECT value FROM video_meta WHERE key='schema_version'").fetchone()[0]
    assert "source_owner_key" not in columns
    assert version == "2"
