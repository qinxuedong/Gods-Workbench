"""C2：重启任务恢复与数据目录跨进程独占锁契约。"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gw.core.auth import require_authenticated
from gw.core.storage import DataDirLockedError, acquire_data_dir_lock, data_root
from gw.god_canvas.models import CanvasCreateRequest
from gw.god_canvas.service import GodCanvasService
from gw.god_canvas.tasks import SmartCanvasTaskRequest
from gw.projects_hub import service as projects

REPO = Path(__file__).resolve().parents[3]


def _seed_jobs():
    svc = GodCanvasService(seed_golden_fixture=False, persistent=True)
    ctx = require_authenticated("Bearer cleanroom-test", "editor")
    cid = svc.create_canvas(CanvasCreateRequest(project_id="prj-0001", title="恢复", initial_payload={"nodes":[{"entity_id":"node-1","kind":"text_prompt"}]}), owner_key=projects.owner_key_for_context(ctx)).canvas_id
    ids = [svc.submit_smart_task(cid, SmartCanvasTaskRequest(expected_version=1, entry_nodes=["node-1"]), "Bearer cleanroom-test").job_id for _ in range(5)]
    svc.update_job_state(ids[1], "running")
    svc.update_job_state(ids[2], "running")
    svc.update_job_state(ids[2], "completed")
    svc.update_job_state(ids[3], "running")
    svc.update_job_state(ids[3], "paused")
    svc.update_job_state(ids[4], "running")
    svc.update_job_state(ids[4], "failed", error="ORIGINAL_ERROR")
    return ids


def test_restart_marks_running_and_accepted_jobs_interrupted():
    accepted, running, completed, paused, failed = _seed_jobs()
    fresh = GodCanvasService(seed_golden_fixture=False, persistent=True)
    assert set(fresh.recover_interrupted_jobs()) == {accepted, running}
    for jid in (accepted, running):
        job = fresh.get_job(jid)
        assert job.state == "failed"
        assert job.error == "CANVAS_TASK_INTERRUPTED"
        assert job.poll_hint is None
        assert getattr(job, "interrupted_at")
    assert fresh.get_job(completed).state == "completed"
    assert fresh.get_job(paused).state == "paused"
    assert (fresh.get_job(failed).state, fresh.get_job(failed).error) == ("failed", "ORIGINAL_ERROR")
    # 幂等：再次恢复不改动任何任务，也不重发
    assert fresh.recover_interrupted_jobs() == []
    assert len(fresh.list_jobs()) == 5


def test_recovery_survives_new_process():
    accepted, running, *_ = _seed_jobs()
    GodCanvasService(seed_golden_fixture=False, persistent=True).recover_interrupted_jobs()
    code = f"from gw.god_canvas.service import GodCanvasService as S;s=S(seed_golden_fixture=False,persistent=True);print(s.get_job('{running}').state, s.get_job('{running}').error)"
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=os.environ.copy(), capture_output=True, text=True, check=True).stdout.split()
    assert out == ["failed", "CANVAS_TASK_INTERRUPTED"]


def test_second_process_on_same_data_dir_is_rejected():
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "sentinel.json").write_text("{}", encoding="utf-8")
    handle = acquire_data_dir_lock(root)
    try:
        code = "from gw.core.storage import acquire_data_dir_lock,data_root,DataDirLockedError\ntry:\n    acquire_data_dir_lock(data_root())\nexcept DataDirLockedError as e:\n    print('LOCKED', e); raise SystemExit(3)\n"
        res = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=os.environ.copy(), capture_output=True, text=True)
        assert res.returncode == 3, res.stderr
        assert "LOCKED" in res.stdout and str(root) in res.stdout
        assert (root / "sentinel.json").read_text(encoding="utf-8") == "{}"
    finally:
        handle.release()
    # 释放后另一进程可以拿到锁
    ok = subprocess.run([sys.executable, "-c", "from gw.core.storage import acquire_data_dir_lock,data_root;acquire_data_dir_lock(data_root()).release();print('OK')"], cwd=REPO, env=os.environ.copy(), capture_output=True, text=True)
    assert ok.stdout.strip() == "OK", ok.stderr


def test_lock_rejects_in_same_process_too():
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    first = acquire_data_dir_lock(root)
    try:
        with pytest.raises(DataDirLockedError):
            acquire_data_dir_lock(root)
    finally:
        first.release()


def test_run_py_refuses_second_instance(tmp_path):
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    handle = acquire_data_dir_lock(root)
    try:
        env = os.environ.copy()
        res = subprocess.run([sys.executable, "run.py", "--test", "--port", "18765"], cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
        assert res.returncode != 0
        assert "数据目录已被另一个神工坊进程占用" in (res.stderr + res.stdout)
    finally:
        handle.release()
