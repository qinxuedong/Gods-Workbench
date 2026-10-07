"""后台索引生产HTTP来源、任务控制、失败与提交竞争。"""
import json
import os
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient
from gw.api.app import create_app
from gw.asset_registry import index_jobs as jobs, repository as repo

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer index-owner", "X-User-Role": "editor"}
OTHER = {"Authorization": "Bearer index-other", "X-User-Role": "admin"}


@pytest.fixture(autouse=True)
def index_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    root = tmp_path / "sources"
    root.mkdir()
    (root / "一.txt").write_text("索引真实内容", encoding="utf-8")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None
    yield root
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None


def test_application_shutdown_stops_index_executor(tmp_path, monkeypatch):
    """应用退出必须停掉索引线程池，不能只依赖已删除的 add_event_handler。"""
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    app = create_app()
    with TestClient(app):
        service = jobs.service()
        assert service.closed is False
    assert service.closed is True
    assert jobs._default is service


def submit(client, payload=None, key="one", kind="reindex", auth=AUTH):
    return client.post(BASE + "/" + kind, headers={**auth, "Idempotency-Key": key},
                       json={"background": True, **(payload or {})})


def read(client, job_id, auth=AUTH):
    return client.get(BASE + "/workspace-jobs/" + job_id, headers=auth)


def wait(client, job_id):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        body = read(client, job_id).json()
        if body["job"]["status"] in jobs.TERMINAL:
            return body["job"]
        time.sleep(.02)
    pytest.fail("后台索引未在期限内终结")


def action(client, job_id, name, version=None):
    if version is None:
        version = read(client, job_id).json()["job"]["version"]
    return client.post(BASE + f"/workspace-jobs/{job_id}/{name}", headers=AUTH,
                       json={"expected_version": version})


def test_http_index_hash_discovery_scope_and_sync(index_runtime):
    with TestClient(create_app()) as client:
        created = submit(client, {"hash_files": True})
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        assert created.json()["poll_hint"]["url"].endswith(job_id)
        job = wait(client, job_id)
        assert job["status"] == "succeeded", job
        assert job["result"]["indexed"] == 1
        asset = client.get(BASE + "/assets", headers=AUTH).json()["assets"][0]
        assert asset["name"] == "一.txt" and len(asset["sha256"]) == 64
        assert client.get(BASE + "/status", headers=AUTH).json()["jobs"][0]["job_id"] == job_id
        assert client.get(BASE + "/status", headers=OTHER).json()["jobs"] == []
        assert read(client, job_id, OTHER).status_code == 404
        assert read(client, job_id).json()["actions"]["cancel"] is False
        assert "_actor" not in read(client, job_id).text
        assert str(index_runtime) not in read(client, job_id).text
        assert action(client, job_id, "cancel").status_code == 409
        assert submit(client, {"hash_files": True}).json()["job_id"] == job_id
        assert submit(client, {"hash_files": False}).status_code == 409
        follow = submit(client, key="again").json()["job_id"]
        assert wait(client, follow)["result"]["indexed"] == 0
        sync = submit(client, kind="index/sync", key="sync").json()["job_id"]
        assert wait(client, sync)["result"]["missing_from_registry"] == 0


def test_pause_resume_cancel_and_retry_real_scan(monkeypatch, index_runtime):
    entered, release = threading.Event(), threading.Event()
    original = jobs.IndexJobs._scan
    def held(self, *args):
        entered.set()
        assert release.wait(5)
        return original(self, *args)
    monkeypatch.setattr(jobs.IndexJobs, "_scan", held)
    with TestClient(create_app()) as client:
        job_id = submit(client).json()["job_id"]
        assert entered.wait(3)
        paused = action(client, job_id, "pause")
        assert paused.status_code == 200 and paused.json()["job"]["status"] == "paused"
        assert action(client, job_id, "resume", version=1).status_code == 409
        release.set()
        time.sleep(.15)
        assert repo.state().read()["assets"] == {}
        assert action(client, job_id, "cancel").json()["job"]["status"] == "cancelled"
        assert repo.state().read()["assets"] == {}
        retry = action(client, job_id, "retry")
        assert retry.status_code == 200 and retry.json()["job"]["attempt"] == 2
        assert wait(client, job_id)["result"]["indexed"] == 1


def test_pause_then_resume_continues(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = jobs.IndexJobs._scan
    def held(self, *args):
        entered.set()
        assert release.wait(5)
        return original(self, *args)
    monkeypatch.setattr(jobs.IndexJobs, "_scan", held)
    with TestClient(create_app()) as client:
        job_id = submit(client).json()["job_id"]
        assert entered.wait(3)
        assert action(client, job_id, "pause").status_code == 200
        release.set()
        assert action(client, job_id, "resume").status_code == 200
        assert wait(client, job_id)["status"] == "succeeded"


def test_root_change_refuses_commit_and_retry(monkeypatch, index_runtime, tmp_path):
    entered, release = threading.Event(), threading.Event()
    original = jobs.IndexJobs._scan
    def held(self, *args):
        result = original(self, *args)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(jobs.IndexJobs, "_scan", held)
    with TestClient(create_app()) as client:
        job_id = submit(client).json()["job_id"]
        assert entered.wait(3)
        other = tmp_path / "new-root"
        other.mkdir()
        monkeypatch.setenv("GW_ALLOWED_ROOTS", str(other))
        release.set()
        job = wait(client, job_id)
        assert job["status"] == "failed" and job["error_code"] == "INDEX_ROOTS_CHANGED"
        assert repo.state().read()["assets"] == {}
        assert action(client, job_id, "retry").status_code == 403


def test_parallel_edit_survives_safe_merge(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = jobs.IndexJobs._scan
    def held(self, *args):
        result = original(self, *args)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(jobs.IndexJobs, "_scan", held)
    with TestClient(create_app()) as client:
        job_id = submit(client).json()["job_id"]
        assert entered.wait(3)
        created = client.post(BASE + "/assets/import", headers=AUTH, json={"items": [{"name": "并发手工素材"}]} )
        assert created.status_code == 200, created.text
        release.set()
        assert wait(client, job_id)["status"] == "succeeded"
        assert {x["name"] for x in repo.state().read()["assets"].values()} == {"一.txt", "并发手工素材"}


def test_limits_failed_retry_and_no_partial_index(index_runtime):
    (index_runtime / "二.txt").write_text("第二个", encoding="utf-8")
    with TestClient(create_app()) as client:
        job_id = submit(client, {"max_files": 1}).json()["job_id"]
        job = wait(client, job_id)
        assert job["status"] == "failed" and job["error_code"] == "INDEX_SCAN_LIMIT"
        assert repo.state().read()["assets"] == {}
        assert action(client, job_id, "retry").status_code == 200
        assert wait(client, job_id)["attempt"] == 2
        assert submit(client, {"roots": [str(index_runtime)]}, key="bad").status_code == 400
        assert submit(client, {"max_files": True}, key="bad").status_code == 400
        assert client.post(BASE + "/reindex", headers=AUTH, json={"background":True}).status_code == 400


def test_new_process_recovers_real_submitted_active_task(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = jobs.IndexJobs._scan
    def held(self, *args):
        entered.set()
        release.wait(5)
        return original(self, *args)
    monkeypatch.setattr(jobs.IndexJobs, "_scan", held)
    with TestClient(create_app()) as client:
        job_id = submit(client).json()["job_id"]
        assert entered.wait(3)
        assert action(client, job_id, "pause").status_code == 200
        # 停止自有执行器后启动新Python进程，不直接构造任务记录。
        release.set()
        jobs._default.shutdown()
        script = 'from gw.asset_registry.index_jobs import service; s=service(); print(s.store.read()["jobs"]["' + job_id + '"]["status"]); s.shutdown()'
        result = subprocess.run([sys.executable, "-c", script], env={**os.environ, "PYTHONPATH":"src"}, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        # 优雅关闭的执行失败同样是终态；真正进程崩溃恢复在专项进程用例验证。
        assert result.stdout.strip() in {"failed", "interrupted"}


def test_receipt_prevents_duplicate_after_job_save_failure(monkeypatch):
    original = jobs.IndexJobs._save
    def fail_success(self, job):
        if job["status"] == "succeeded":
            raise OSError("受控任务状态写失败")
        return original(self, job)
    monkeypatch.setattr(jobs.IndexJobs, "_save", fail_success)
    with TestClient(create_app()) as client:
        job_id = submit(client).json()["job_id"]
        end = time.monotonic() + 5
        while not repo.state().read().get("index_job_receipts") and time.monotonic() < end:
            time.sleep(.02)
        jobs._default.shutdown()
        monkeypatch.setattr(jobs.IndexJobs, "_save", original)
        jobs._default = None
        job = wait(client, job_id)
        assert job["status"] == "succeeded" and job["result"]["indexed"] == 1
        assert len(repo.state().read()["assets"]) == 1
        assert action(client, job_id, "retry").status_code == 409

def test_queue_bound_and_executor_failure(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = jobs.IndexJobs._scan
    def held(self, *args):
        entered.set()
        assert release.wait(5)
        return original(self, *args)
    monkeypatch.setattr(jobs.IndexJobs, "_scan", held)
    monkeypatch.setattr(jobs, "MAX_ACTIVE", 1)
    with TestClient(create_app()) as client:
        job_id = submit(client).json()["job_id"]
        assert entered.wait(3)
        assert submit(client, key="full").status_code == 429
        assert action(client, job_id, "cancel").status_code == 200
        release.set()
        def reject(*args, **kwargs):
            raise RuntimeError("受控执行器拒绝")
        monkeypatch.setattr(jobs.service().executor, "submit", reject)
        response = submit(client, key="dispatch-failure")
        assert response.status_code == 202
        assert read(client, response.json()["job_id"]).json()["job"]["error_code"] == "INDEX_EXECUTOR_UNAVAILABLE"


def test_local_account_revocation_at_commit(monkeypatch, tmp_path):
    from gw.core.local_accounts import database
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    entered, release = threading.Event(), threading.Event()
    original = jobs.IndexJobs._scan
    def held(self, *args):
        result = original(self, *args)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(jobs.IndexJobs, "_scan", held)
    with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 50000), headers={"Origin": "http://localhost"}) as client:
        assert client.post("/api/asset-auth/local/setup", json={"username":"index_owner", "password":"Test2026"}).status_code == 201
        response = submit(client)
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        assert entered.wait(3)
        with database() as db:
            db.execute("UPDATE local_users SET role='readonly'")
        release.set()
        job = wait(client, job_id)
        assert job["status"] == "failed" and job["error_code"] == "INDEX_AUTH_REVOKED"
        assert repo.state().read()["assets"] == {}
        assert not read(client, job_id).json()["actions"]["retry"]
        assert action(client, job_id, "retry").status_code == 403


def test_commit_wins_cancel_never_reports_cancelled(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = jobs.IndexJobs._commit
    def held(self, *args):
        result = original(self, *args)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(jobs.IndexJobs, "_commit", held)
    with TestClient(create_app()) as client:
        created = submit(client).json()
        job_id = created["job_id"]
        assert entered.wait(3)
        assert len(repo.state().read()["assets"]) == 1
        replies = []
        thread = threading.Thread(target=lambda: replies.append(action(client, job_id, "cancel", version=2)))
        thread.start()
        time.sleep(.05)
        assert thread.is_alive()
        release.set()
        thread.join(5)
        assert replies[0].status_code == 409
        assert wait(client, job_id)["status"] == "succeeded"


def test_actual_process_exit_restores_interrupted_and_explicit_retry(tmp_path):
    # 子进程通过合法HTTP创建任务；仅阻塞扫描以确定崩溃窗口，不注入任务记录。
    script = r'''
import os, threading
from fastapi.testclient import TestClient
from gw.api.app import create_app
from gw.asset_registry import index_jobs as jobs
entered=threading.Event()
def hold(self, *args):
    entered.set()
    threading.Event().wait(20)
jobs.IndexJobs._scan=hold
client=TestClient(create_app())
headers={"Authorization":"Bearer index-owner", "X-User-Role":"editor", "Idempotency-Key":"crash-source"}
response=client.post("/api/asset-registry/reindex",headers=headers,json={"background":True})
assert response.status_code==202, response.text
assert entered.wait(5)
print(response.json()["job_id"],flush=True)
os._exit(0)
'''
    result = subprocess.run([sys.executable, "-c", script], env={**os.environ, "PYTHONPATH":"src"}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    job_id = result.stdout.strip()
    with TestClient(create_app()) as client:
        job = read(client, job_id).json()["job"]
        assert job["status"] == "interrupted"
        assert repo.state().read()["assets"] == {}
        assert action(client, job_id, "retry").status_code == 200
        assert wait(client, job_id)["status"] == "succeeded"
        assert len(repo.state().read()["assets"]) == 1


def test_same_name_different_roots_are_not_overwritten(monkeypatch, index_runtime, tmp_path):
    second = tmp_path / "second"
    second.mkdir()
    (second / "一.txt").write_text("另一允许根", encoding="utf-8")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", os.pathsep.join([str(index_runtime), str(second)]))
    with TestClient(create_app()) as client:
        job_id = submit(client).json()["job_id"]
        assert wait(client, job_id)["result"]["indexed"] == 2
        assets = list(repo.state().read()["assets"].values())
        assert len({a["root_id"] for a in assets}) == 2


def test_corrupt_jobs_fail_closed_instead_of_reusing_ids(tmp_path):
    path = tmp_path / "gw-data" / "registry_index_jobs.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text("broken", encoding="utf-8")
    with TestClient(create_app()) as client:
        response = submit(client)
        assert response.status_code == 503 and response.json()["detail"]["code"] == "INDEX_STORE_INVALID"
        assert path.read_text(encoding="utf-8") == "broken"


def test_committed_receipt_blocks_cancel_and_retry_without_restart(monkeypatch):
    """成功状态持续写失败且工作线程已退出，普通读取/动作仍以收据为准。"""
    original = jobs.IndexJobs._save
    worker_finished = threading.Event()
    run = jobs.IndexJobs._run
    def fail_success(self, job):
        if job["status"] == "succeeded":
            raise OSError("受控连续成功状态写失败")
        return original(self, job)
    def mark_finished(self, *args):
        try:
            return run(self, *args)
        finally:
            worker_finished.set()
    monkeypatch.setattr(jobs.IndexJobs, "_save", fail_success)
    monkeypatch.setattr(jobs.IndexJobs, "_run", mark_finished)
    with TestClient(create_app()) as client:
        job_id = submit(client).json()["job_id"]
        assert worker_finished.wait(5)
        durable = jobs.service().store.read()["jobs"][job_id]
        assert durable["status"] == "running"
        assert len(repo.state().read()["assets"]) == 1
        shown = read(client, job_id).json()
        assert shown["job"]["status"] == "succeeded"
        assert not any(shown["actions"].values())
        assert client.get(BASE + "/status", headers=AUTH).json()["jobs"][0]["status"] == "succeeded"
        assert submit(client).json()["job"]["status"] == "succeeded"
        for command in ("cancel", "pause", "retry"):
            assert action(client, job_id, command, version=shown["job"]["version"]).status_code == 409
        assert len(repo.state().read()["assets"]) == 1
        assert jobs.service().store.read()["jobs"][job_id]["status"] == "running"
