"""QIN-625 首次登记、重用、提交收据与后台恢复契约。"""

import copy
import threading
import time

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs as jobs
from gw.asset_registry import repository as repo
from gw.core import storage
from gw.settings.service import (
    default_storage_allowed_roots_service,
    default_storage_settings_service,
)

BASE = "/api/asset-registry"
ADMIN = {"Authorization": "Bearer qin625-owner", "X-User-Role": "admin"}
EDITOR = {"Authorization": "Bearer qin625-owner", "X-User-Role": "editor"}


@pytest.fixture(autouse=True)
def index_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    admitted = tmp_path / "admitted"
    admitted.mkdir()
    (admitted / "seed.txt").write_text("既有允许根", encoding="utf-8")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(admitted))
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None
    yield admitted
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None


def register(client, path, *, source_id="qin625_source", key="register", auth=ADMIN):
    settings = client.get("/api/storage-settings", headers=auth)
    assert settings.status_code == 200, settings.text
    return client.post(
        BASE + "/reindex",
        headers={**auth, "Idempotency-Key": key},
        json={
            "background": True,
            "hash_files": True,
            "max_files": 250000,
            "roots": [source_id],
            "register_root": {
                "id": source_id,
                "name": "QIN-625 测试来源",
                "path": str(path),
                "expected_version": settings.json()["revision"],
                "authorize_root": True,
            },
        },
    )


def read(client, job_id):
    return client.get(BASE + "/workspace-jobs/" + job_id, headers=ADMIN)


def wait(client, job_id, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = read(client, job_id)
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        if job["status"] in jobs.TERMINAL:
            return job
        time.sleep(0.02)
    pytest.fail("QIN-625 索引任务未按时终结")


def source_for(path):
    key = storage.directory_path_key(str(path))
    return next(
        (
            item
            for item in default_storage_settings_service.get_snapshot().local_libraries
            if item.get("path") and storage.directory_path_key(str(item["path"])) == key
        ),
        None,
    )


def has_persistent_root(path):
    key = storage.directory_path_key(str(path))
    return any(
        item.source == "persistent"
        and item.active
        and storage.directory_path_key(item.path) == key
        for item in default_storage_allowed_roots_service.get_snapshot().roots
    )


def test_registration_returns_pending_then_commits_matching_receipt(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    original = jobs.IndexJobs._scan

    def held(self, *args):
        entered.set()
        assert release.wait(5)
        return original(self, *args)

    monkeypatch.setattr(jobs.IndexJobs, "_scan", held)
    folder = tmp_path / "new-library"
    folder.mkdir()
    source_file = folder / "asset.txt"
    source_file.write_text("本票素材", encoding="utf-8")
    with TestClient(create_app()) as client:
        accepted = register(client, folder)
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        assert entered.wait(3)
        pending = read(client, job_id).json()["job"]
        assert pending["status"] in {"queued", "running"}
        assert pending["data_status"] != "ok"
        assert pending["result"] is None
        # 索引扫描受阻时，资产浏览读口仍可用。
        assert client.get(BASE + "/assets", headers=ADMIN).status_code == 200

        duplicate = register(client, folder, source_id="qin625_duplicate", key="same-path")
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json()["job_id"] == job_id
        assert len([
            item for item in default_storage_settings_service.get_snapshot().local_libraries
            if item.get("path")
            and storage.directory_path_key(str(item["path"])) == storage.directory_path_key(str(folder))
        ]) == 1

        release.set()
        completed = wait(client, job_id)
        assert completed["status"] == "succeeded"
        assert completed["result"]["indexed"] == 1
        receipt_id = f"{job_id}:1"
        receipt = repo.resource_folder_registration_receipt(
            repo.readonly_registry_snapshot(), receipt_id
        )
        assert receipt and receipt["source_id"] == "qin625_source"
        assert receipt["operation_id"]
        assert source_for(folder)["id"] == "qin625_source"
        assert has_persistent_root(folder)
        assert source_file.exists()


def test_unreachable_directory_is_rejected_before_any_persistent_registration(tmp_path):
    missing = tmp_path / "not-mounted"
    with TestClient(create_app()) as client:
        response = register(client, missing, key="unreachable-directory")
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "RESOURCE_FOLDER_BACKEND_UNAVAILABLE"
        assert source_for(missing) is None
        assert not has_persistent_root(missing)
        assert not default_storage_settings_service.get_snapshot().configured
        assert jobs.service().store.read()["jobs"] == {}
        assert not jobs.service().store.read().get("registration_ops")


def test_cancel_before_commit_rolls_back_only_new_registration(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()

    def held(self, *_args):
        entered.set()
        assert release.wait(5)
        return []

    monkeypatch.setattr(jobs.IndexJobs, "_scan", held)
    folder = tmp_path / "cancel-library"
    folder.mkdir()
    source_file = folder / "keep.txt"
    source_file.write_text("源文件必须保留", encoding="utf-8")
    with TestClient(create_app()) as client:
        accepted = register(client, folder, key="cancel-new")
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        assert entered.wait(3)
        task = read(client, job_id).json()["job"]
        cancelled = client.post(
            BASE + f"/workspace-jobs/{job_id}/cancel",
            headers=EDITOR,
            json={"expected_version": task["version"]},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["job"]["status"] == "cancelled"
        release.set()
        assert wait(client, job_id)["status"] == "cancelled"
        assert source_for(folder) is None
        assert not has_persistent_root(folder)
        assert not default_storage_settings_service.get_snapshot().configured
        assert source_file.read_text(encoding="utf-8") == "源文件必须保留"
        assert not repo.state().read().get("resource_folder_registration_ops")
        assert not jobs.service().store.read().get("registration_ops")


def test_rollback_does_not_undo_later_storage_settings_change(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()

    def held(self, *_args):
        entered.set()
        assert release.wait(5)
        return []

    monkeypatch.setattr(jobs.IndexJobs, "_scan", held)
    folder = tmp_path / "settings-change-library"
    folder.mkdir()
    (folder / "keep.txt").write_text("保留后续设置变更", encoding="utf-8")
    with TestClient(create_app()) as client:
        accepted = register(client, folder, key="cancel-after-settings-change")
        job_id = accepted.json()["job_id"]
        assert entered.wait(3)
        settings = client.get("/api/storage-settings", headers=ADMIN)
        changed = client.patch(
            "/api/storage-settings",
            headers=ADMIN,
            json={
                "expected_version": settings.json()["revision"],
                "local_library_names": {"local": "独立设置变更"},
            },
        )
        assert changed.status_code == 200, changed.text
        release.set()
        assert wait(client, job_id)["status"] == "failed"
        snapshot = default_storage_settings_service.get_snapshot()
        assert snapshot.configured
        assert snapshot.local_library_names["local"] == "独立设置变更"
        assert source_for(folder) is None
        assert not has_persistent_root(folder)


def test_cancel_preserves_registration_already_used_by_another_job(monkeypatch, tmp_path):
    first_entered, second_entered, release = threading.Event(), threading.Event(), threading.Event()
    calls_lock = threading.Lock()
    calls = 0
    original = jobs.IndexJobs._scan

    def hold_first_scan(self, *args):
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_entered.set()
        elif call_number == 2:
            second_entered.set()
        assert release.wait(5)
        return original(self, *args)

    monkeypatch.setattr(jobs.IndexJobs, "_scan", hold_first_scan)
    folder = tmp_path / "shared-registration-library"
    folder.mkdir()
    source_file = folder / "used.txt"
    source_file.write_text("另一任务已使用", encoding="utf-8")
    with TestClient(create_app()) as client:
        accepted = register(client, folder, key="cancel-after-use")
        first_job_id = accepted.json()["job_id"]
        assert first_entered.wait(3)

        reused = client.post(
            BASE + "/reindex",
            headers={**ADMIN, "Idempotency-Key": "use-new-registration"},
            json={
                "background": True,
                "hash_files": True,
                "max_files": 250000,
                "roots": ["qin625_source"],
            },
        )
        assert reused.status_code == 202, reused.text
        second_job_id = reused.json()["job_id"]
        assert second_entered.wait(3)

        first = read(client, first_job_id).json()["job"]
        cancelled = client.post(
            BASE + f"/workspace-jobs/{first_job_id}/cancel",
            headers=ADMIN,
            json={"expected_version": first["version"]},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert source_for(folder)["id"] == "qin625_source"
        assert has_persistent_root(folder)
        release.set()
        assert wait(client, first_job_id)["status"] == "cancelled"
        assert wait(client, second_job_id)["status"] == "succeeded"
        assert source_for(folder)["id"] == "qin625_source"
        assert has_persistent_root(folder)
        assert repo.state().read().get("assets")
        assert source_file.read_text(encoding="utf-8") == "另一任务已使用"


def test_failed_first_scan_rolls_back_but_failed_reuse_keeps_existing_index(monkeypatch, tmp_path):
    folder = tmp_path / "failure-library"
    folder.mkdir()
    source_file = folder / "stable.txt"
    source_file.write_text("旧索引内容", encoding="utf-8")
    with TestClient(create_app()) as client:
        first = register(client, folder, key="first-success")
        assert first.status_code == 202, first.text
        first_job = wait(client, first.json()["job_id"])
        assert first_job["status"] == "succeeded"
        before_assets = copy.deepcopy(repo.state().read()["assets"])
        assert source_for(folder)["id"] == "qin625_source"
        assert has_persistent_root(folder)

        def fail_scan(self, *_args):
            raise RuntimeError("注入扫描失败")

        monkeypatch.setattr(jobs.IndexJobs, "_scan", fail_scan)
        second = register(client, folder, source_id="qin625_new_id", key="reuse-failure")
        assert second.status_code == 202, second.text
        failed_reuse = wait(client, second.json()["job_id"])
        assert failed_reuse["status"] == "failed"
        assert source_for(folder)["id"] == "qin625_source"
        assert has_persistent_root(folder)
        assert repo.state().read()["assets"] == before_assets
        assert source_file.read_text(encoding="utf-8") == "旧索引内容"


def test_interrupted_accepted_registration_recovers_without_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs.IndexJobs, "_run", lambda self, *_args: None)
    folder = tmp_path / "interrupted-library"
    folder.mkdir()
    source_file = folder / "keep.txt"
    source_file.write_text("恢复时不删除", encoding="utf-8")
    with TestClient(create_app()) as client:
        accepted = register(client, folder, key="recover-without-receipt")
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        assert source_for(folder)
        assert has_persistent_root(folder)
        jobs._default.shutdown()
        jobs._default = None
        recovered = jobs.service()
        assert recovered.store.read()["jobs"][job_id]["status"] == "interrupted"
        assert not recovered.store.read().get("registration_ops")
        assert source_for(folder) is None
        assert not has_persistent_root(folder)
        assert source_file.read_text(encoding="utf-8") == "恢复时不删除"


def test_enqueue_persistence_failure_compensates_registration(monkeypatch, tmp_path):
    folder = tmp_path / "enqueue-failure-library"
    folder.mkdir()
    source_file = folder / "keep.txt"
    source_file.write_text("持久化失败不删文件", encoding="utf-8")
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        service = jobs.service()
        original_write = service.store.write
        existing_ids = set(service.store.read()["jobs"])

        def fail_job_enqueue(raw):
            if any(
                job_id not in existing_ids
                and isinstance(job.get("_resource_folder_registration"), dict)
                for job_id, job in raw["jobs"].items()
            ):
                raise OSError("注入任务落盘失败")
            return original_write(raw)

        monkeypatch.setattr(service.store, "write", fail_job_enqueue)
        failed = register(client, folder, key="enqueue-failure")
        assert failed.status_code >= 500
        assert source_for(folder) is None
        assert not has_persistent_root(folder)
        assert not service.store.read().get("registration_ops")
        assert not default_storage_settings_service.get_snapshot().configured
        assert source_file.read_text(encoding="utf-8") == "持久化失败不删文件"


@pytest.mark.parametrize("failure_point", ["allowed_root", "source_entry"])
def test_registration_write_failure_compensates_only_owned_records(
    monkeypatch, tmp_path, failure_point
):
    folder = tmp_path / f"{failure_point}-failure-library"
    folder.mkdir()
    source_file = folder / "keep.txt"
    source_file.write_text("授权或来源写入失败不得删除", encoding="utf-8")

    if failure_point == "allowed_root":
        service = default_storage_allowed_roots_service
        original = service.add_root

        def fail_after_root_write(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("注入允许根写入后失败")

        monkeypatch.setattr(service, "add_root", fail_after_root_write)
    else:
        service = default_storage_settings_service
        original = service.register_resource_folder_entry

        def fail_after_source_write(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("注入来源写入后失败")

        monkeypatch.setattr(service, "register_resource_folder_entry", fail_after_source_write)

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = register(client, folder, key=f"{failure_point}-write-failure")
        assert response.status_code >= 500, response.text
        assert source_for(folder) is None
        assert not has_persistent_root(folder)
        assert not default_storage_settings_service.get_snapshot().configured
        assert source_file.read_text(encoding="utf-8") == "授权或来源写入失败不得删除"
        assert not jobs.service().store.read().get("registration_ops")


def test_final_commit_failure_rolls_back_first_registration_without_deleting_files(
    monkeypatch, tmp_path
):
    folder = tmp_path / "commit-failure-library"
    folder.mkdir()
    source_file = folder / "keep.txt"
    source_file.write_text("提交失败保留素材", encoding="utf-8")

    def fail_commit(self, *_args, **_kwargs):
        raise OSError("注入 registry 提交失败")

    monkeypatch.setattr(jobs.IndexJobs, "_commit", fail_commit)
    with TestClient(create_app()) as client:
        accepted = register(client, folder, key="commit-failure")
        assert accepted.status_code == 202, accepted.text
        failed = wait(client, accepted.json()["job_id"])
        assert failed["status"] == "failed"
        assert source_for(folder) is None
        assert not has_persistent_root(folder)
        assert not default_storage_settings_service.get_snapshot().configured
        assert repo.state().read().get("assets", {}) == {}
        assert source_file.read_text(encoding="utf-8") == "提交失败保留素材"


def test_retry_after_rollback_uses_new_registration_attempt(monkeypatch, tmp_path):
    folder = tmp_path / "retry-library"
    folder.mkdir()
    source_file = folder / "retry.txt"
    source_file.write_text("attempt 2 会重建索引", encoding="utf-8")
    original = jobs.IndexJobs._scan

    def fail_first_attempt(self, job_id, attempt, roots, request):
        if attempt == 1:
            raise RuntimeError("首次扫描失败，允许重试一次新 attempt")
        return original(self, job_id, attempt, roots, request)

    monkeypatch.setattr(jobs.IndexJobs, "_scan", fail_first_attempt)
    with TestClient(create_app()) as client:
        accepted = register(client, folder, key="retry-registration")
        job_id = accepted.json()["job_id"]
        failed = wait(client, job_id)
        assert failed["status"] == "failed"
        assert not default_storage_settings_service.get_snapshot().configured
        old_operation = jobs.service().store.read()["jobs"][job_id]["_resource_folder_registration"]["operation_id"]
        assert source_for(folder) is None
        assert not has_persistent_root(folder)

        retry_response = client.post(
            BASE + f"/workspace-jobs/{job_id}/retry",
            headers=ADMIN,
            json={"expected_version": failed["version"]},
        )
        assert retry_response.status_code == 200, retry_response.text
        completed = wait(client, job_id)
        assert completed["status"] == "succeeded"
        assert completed["attempt"] == 2
        new_registration = jobs.service().store.read()["jobs"][job_id]["_resource_folder_registration"]
        assert new_registration["operation_id"] != old_operation
        assert new_registration["settled"] == "committed"
        assert repo.resource_folder_registration_receipt(
            repo.readonly_registry_snapshot(), f"{job_id}:2"
        )
        assert source_for(folder)["id"] == "qin625_source"
        assert has_persistent_root(folder)
        assert default_storage_settings_service.get_snapshot().configured
        assert source_file.exists()


def test_late_cancelled_attempt_cannot_write_after_retry(monkeypatch, tmp_path):
    old_entered, release_old, old_returned = threading.Event(), threading.Event(), threading.Event()
    committed_attempts = []
    original_scan = jobs.IndexJobs._scan
    original_commit = jobs.IndexJobs._commit

    def delay_old_attempt(self, job_id, attempt, roots, request):
        if attempt == 1:
            old_entered.set()
            assert release_old.wait(5)
            try:
                return original_scan(self, job_id, attempt, roots, request)
            finally:
                old_returned.set()
        return original_scan(self, job_id, attempt, roots, request)

    def record_commit(self, job, *args, **kwargs):
        committed_attempts.append(job["attempt"])
        return original_commit(self, job, *args, **kwargs)

    monkeypatch.setattr(jobs.IndexJobs, "_scan", delay_old_attempt)
    monkeypatch.setattr(jobs.IndexJobs, "_commit", record_commit)
    folder = tmp_path / "late-attempt-library"
    folder.mkdir()
    source_file = folder / "attempt.txt"
    source_file.write_text("迟到的 attempt 1 不可提交", encoding="utf-8")
    with TestClient(create_app()) as client:
        accepted = register(client, folder, key="late-attempt")
        job_id = accepted.json()["job_id"]
        assert old_entered.wait(3)
        first = read(client, job_id).json()["job"]
        cancelled = client.post(
            BASE + f"/workspace-jobs/{job_id}/cancel",
            headers=ADMIN,
            json={"expected_version": first["version"]},
        )
        assert cancelled.status_code == 200, cancelled.text
        retry = client.post(
            BASE + f"/workspace-jobs/{job_id}/retry",
            headers=ADMIN,
            json={"expected_version": cancelled.json()["job"]["version"]},
        )
        assert retry.status_code == 200, retry.text
        completed = wait(client, job_id)
        assert completed["status"] == "succeeded"
        assert completed["attempt"] == 2
        release_old.set()
        assert old_returned.wait(3)
        time.sleep(0.05)
        assert committed_attempts == [2]
        snapshot = repo.readonly_registry_snapshot()
        assert repo.resource_folder_registration_receipt(snapshot, f"{job_id}:2")
        assert repo.resource_folder_registration_receipt(snapshot, f"{job_id}:1") is None
        assert source_for(folder)
        assert has_persistent_root(folder)
        assert source_file.exists()


def test_commit_wins_over_concurrent_cancel(monkeypatch, tmp_path):
    scan_entered, scan_release = threading.Event(), threading.Event()
    entered, release = threading.Event(), threading.Event()
    original_scan = jobs.IndexJobs._scan
    original = jobs.IndexJobs._commit

    def held_scan(self, *args):
        scan_entered.set()
        assert scan_release.wait(5)
        return original_scan(self, *args)

    def held_commit(self, job, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, job, *args, **kwargs)

    monkeypatch.setattr(jobs.IndexJobs, "_scan", held_scan)
    monkeypatch.setattr(jobs.IndexJobs, "_commit", held_commit)
    folder = tmp_path / "commit-wins-library"
    folder.mkdir()
    (folder / "asset.txt").write_text("提交先于取消", encoding="utf-8")
    with TestClient(create_app()) as client:
        accepted = register(client, folder, key="commit-wins")
        job_id = accepted.json()["job_id"]
        assert scan_entered.wait(3)
        running = read(client, job_id).json()["job"]
        scan_release.set()
        assert entered.wait(3)
        cancel_started, cancel_done = threading.Event(), threading.Event()
        response_box = {}

        def cancel_after_commit_starts():
            cancel_started.set()
            response_box["response"] = client.post(
                BASE + f"/workspace-jobs/{job_id}/cancel",
                headers=ADMIN,
                json={"expected_version": running["version"]},
            )
            cancel_done.set()

        cancel_thread = threading.Thread(target=cancel_after_commit_starts, daemon=True)
        cancel_thread.start()
        assert cancel_started.wait(2)
        assert not cancel_done.wait(0.05)
        release.set()
        cancel_thread.join(3)
        assert not cancel_thread.is_alive()
        assert response_box["response"].status_code == 409
        completed = wait(client, job_id)
        assert completed["status"] == "succeeded"
        assert source_for(folder)
        assert has_persistent_root(folder)


def test_cold_recovery_uses_atomic_receipt_after_job_status_write_fails(
    monkeypatch, tmp_path
):
    settled = threading.Event()
    original_save = jobs.IndexJobs._save
    original_settle = jobs.IndexJobs._settle_resource_folder_registration

    def fail_success_save(self, job):
        if job.get("status") == "succeeded":
            raise OSError("注入收据写入后任务状态落盘失败")
        original_save(self, job)

    def leave_registration_for_recovery(self, operation_id, *, committed=False):
        if committed:
            settled.set()
            return
        original_settle(self, operation_id, committed=committed)

    monkeypatch.setattr(jobs.IndexJobs, "_save", fail_success_save)
    monkeypatch.setattr(
        jobs.IndexJobs,
        "_settle_resource_folder_registration",
        leave_registration_for_recovery,
    )
    folder = tmp_path / "receipt-recovery-library"
    folder.mkdir()
    source_file = folder / "committed.txt"
    source_file.write_text("收据提交后冷读", encoding="utf-8")
    with TestClient(create_app()) as client:
        accepted = register(client, folder, key="receipt-recovery")
        job_id = accepted.json()["job_id"]
        assert settled.wait(5)
        raw = jobs.service().store.read()
        assert raw["jobs"][job_id]["status"] == "running"
        assert raw["registration_ops"]
        assert repo.resource_folder_registration_receipt(
            repo.readonly_registry_snapshot(), f"{job_id}:1"
        )

        jobs._default.shutdown()
        jobs._default = None
        monkeypatch.setattr(jobs.IndexJobs, "_save", original_save)
        monkeypatch.setattr(
            jobs.IndexJobs,
            "_settle_resource_folder_registration",
            original_settle,
        )
        recovered = jobs.service()
        recovered_state = recovered.store.read()
        assert recovered_state["jobs"][job_id]["status"] == "succeeded"
        assert not recovered_state.get("registration_ops")
        assert (
            recovered_state["jobs"][job_id]["_resource_folder_registration"]["settled"]
            == "committed"
        )
        assert source_for(folder)
        assert has_persistent_root(folder)
        assert source_file.exists()
