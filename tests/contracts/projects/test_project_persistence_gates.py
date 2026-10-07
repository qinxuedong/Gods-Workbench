"""项目真源、阶段门HTTP契约与持久恢复回归。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi.testclient import TestClient

from gw.api.app import app
from gw.api import routes_projects, routes_asset_registry
from gw.core.auth import require_authenticated
from gw.core.errors import CleanroomException
from gw.projects_hub import service as projects_service_module
from gw.projects_hub.models import ProjectCreateRequest, ProjectType
from gw.projects_hub.service import ProjectsService, owner_key_for_context

AUTH_OWNER = {"Authorization": "Bearer phase12-owner-a", "X-User-Role": "editor"}
AUTH_OTHER = {"Authorization": "Bearer phase12-owner-b", "X-User-Role": "editor"}
AUTH_ADMIN = {"Authorization": "Bearer phase12-admin", "X-User-Role": "admin"}
CREATE_PAYLOAD = {
    "name": "阶段门持久化项目",
    "project_type": "series",
    "description": "由真实HTTP创建并跨进程恢复",
    "start_at": 1780000000000,
    "due_at": 1781000000000,
    "client_request_id": "phase12-gates-0001",
    "gates": [
        {"code": "SCRIPT", "name": "剧本评审"},
        {"code": "layout-01", "name": "分镜评审"},
    ],
}


def _persistent_routes(monkeypatch) -> ProjectsService:
    service = ProjectsService(seed_golden_fixture=False, persistent=True)
    monkeypatch.setattr(projects_service_module, "default_projects_service", service)
    monkeypatch.setattr(routes_projects, "default_projects_service", service)
    monkeypatch.setattr(routes_asset_registry, "default_projects_service", service)
    from gw.video_tasks import service as video_service_module
    monkeypatch.setattr(video_service_module, "default_projects_service", service)
    return service


def test_production_project_service_is_persistent_and_does_not_seed_golden(tmp_path, monkeypatch):
    """生产模式读空库时为空，不把显式内存夹具混入运行期。"""
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "production-empty"))
    service = ProjectsService(seed_golden_fixture=False, persistent=True)
    assert service.list_projects() == []
    assert service.list_projects() == []
    assert not (tmp_path / "production-empty" / "projects.json").read_bytes().find(b"prj-0001") >= 0


def test_http_project_and_gates_are_authorized_idempotent_and_recover_in_new_process(
    client: TestClient, monkeypatch, tmp_path
):
    """真实HTTP创建门后，授权、CAS、生命周期与新Python进程读回同一快照。"""
    root = tmp_path / "http-project-state"
    monkeypatch.setenv("GW_DATA_DIR", str(root))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "http-video-state"))
    service = _persistent_routes(monkeypatch)

    created = client.post("/api/asset-registry/projects", headers=AUTH_OWNER, json=CREATE_PAYLOAD)
    assert created.status_code == 201, created.text
    project_id = created.json()["project"]["project_id"]
    assert project_id.startswith("prj-p-")
    first_gates = client.get(f"/api/asset-registry/projects/{project_id}/gates", headers=AUTH_OWNER)
    assert first_gates.status_code == 200
    gates = first_gates.json()["gates"]
    assert [gate["code"] for gate in gates] == ["script", "layout-01"]
    assert all(gate["state"] == "pending" and gate["version"] == 1 for gate in gates)
    assert len({gate["gate_id"] for gate in gates}) == 2

    # 同请求不重复分配项目/门ID；同键异参在副作用前拒绝。
    replay = client.post("/api/asset-registry/projects", headers=AUTH_OWNER, json=CREATE_PAYLOAD)
    assert replay.status_code == 201
    assert replay.json()["project"]["project_id"] == project_id
    conflict_payload = {**CREATE_PAYLOAD, "name": "另一个请求内容"}
    conflict = client.post("/api/asset-registry/projects", headers=AUTH_OWNER, json=conflict_payload)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

    # 未授权集合过滤，详情与写入统一隐藏为404。
    hidden_collection = client.get(f"/api/asset-registry/projects/{project_id}/gates", headers=AUTH_OTHER)
    assert hidden_collection.status_code == 200 and hidden_collection.json()["gates"] == []
    hidden_detail = client.get(f"/api/asset-registry/project-gates/{gates[0]['gate_id']}", headers=AUTH_OTHER)
    assert hidden_detail.status_code == 404
    assert hidden_detail.json()["detail"]["code"] == "PROJECT_GATE_NOT_FOUND"
    assert client.get(f"/api/asset-registry/projects/{project_id}/gates").status_code == 401

    gate_id = gates[0]["gate_id"]
    approved = client.patch(
        f"/api/asset-registry/project-gates/{gate_id}",
        headers=AUTH_OWNER,
        json={"state": "approved", "expected_version": 1},
    )
    assert approved.status_code == 200
    assert approved.json()["gate"]["version"] == 2
    same_state = client.patch(
        f"/api/asset-registry/project-gates/{gate_id}",
        headers=AUTH_OWNER,
        json={"state": "approved", "note": "不可覆盖同态记录", "expected_version": 2},
    )
    assert same_state.status_code == 200
    assert same_state.json()["gate"]["version"] == 2
    assert same_state.json()["gate"]["note"] is None
    non_governor_retract = client.patch(
        f"/api/asset-registry/project-gates/{gate_id}",
        headers=AUTH_OWNER,
        json={"state": "changes_requested", "note": "撤回", "expected_version": 2},
    )
    assert non_governor_retract.status_code == 403
    retract = client.patch(
        f"/api/asset-registry/project-gates/{gate_id}",
        headers=AUTH_ADMIN,
        json={"state": "changes_requested", "note": "治理复核要求补证", "expected_version": 2},
    )
    assert retract.status_code == 200
    assert retract.json()["gate"]["version"] == 3
    assert retract.json()["gate"]["note"] == "治理复核要求补证"
    stale = client.patch(
        f"/api/asset-registry/project-gates/{gate_id}",
        headers=AUTH_OWNER,
        json={"state": "pending", "expected_version": 2},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"
    invalid = client.patch(
        f"/api/asset-registry/project-gates/{gate_id}",
        headers=AUTH_OWNER,
        json={"state": "unknown", "expected_version": 3},
    )
    assert invalid.status_code == 400

    # 门CAS不触碰项目版本；归档与回收只读，恢复不重置门状态/版本。
    detail = client.get(f"/api/projects/{project_id}", headers=AUTH_OWNER)
    assert detail.status_code == 200 and detail.json()["project"]["version"] == 1
    archived = client.request("DELETE", f"/api/asset-registry/projects/{project_id}", headers=AUTH_ADMIN, json={"expected_version": 1})
    assert archived.status_code == 200 and archived.json()["project"]["version"] == 2
    readonly = client.patch(
        f"/api/asset-registry/project-gates/{gate_id}",
        headers=AUTH_OWNER,
        json={"state": "pending", "expected_version": 3},
    )
    assert readonly.status_code == 403 and readonly.json()["detail"]["code"] == "PROJECT_READ_ONLY"
    trashed = client.post(
        f"/api/asset-registry/projects/{project_id}/trash",
        headers=AUTH_ADMIN,
        json={"expected_version": 2},
    )
    assert trashed.status_code == 200 and trashed.json()["project"]["version"] == 3
    restored = client.post(
        f"/api/asset-registry/projects/{project_id}/trash/restore",
        headers=AUTH_ADMIN,
        json={"expected_version": 3},
    )
    assert restored.status_code == 200 and restored.json()["project"]["version"] == 4
    preserved = client.get(f"/api/asset-registry/project-gates/{gate_id}", headers=AUTH_ADMIN)
    assert preserved.status_code == 200
    assert preserved.json()["gate"]["state"] == "changes_requested"
    assert preserved.json()["gate"]["version"] == 3

    # 另起python解释器读取同一根目录，证明不是同一进程缓存/测试夹具回显。
    root_path = str(Path(__file__).resolve().parents[3])
    owner_key = owner_key_for_context(require_authenticated(AUTH_OWNER["Authorization"], "editor"))
    child_env = os.environ.copy()
    child_env["GW_DATA_DIR"] = str(root)
    child_env["GW_TEST_PROJECT_ID"] = project_id
    child_env["GW_TEST_OWNER_KEY"] = owner_key
    child_env["PYTHONPATH"] = str(Path(root_path))
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_code = (
        "import json, os; from gw.projects_hub.service import ProjectsService; "
        "s=ProjectsService(seed_golden_fixture=False, persistent=True); "
        "p=s.get_project(os.environ['GW_TEST_PROJECT_ID']); "
        "g=s.list_project_gates(p.project_id, owner_key=os.environ['GW_TEST_OWNER_KEY'], role='editor'); "
        "print(json.dumps({'project':p.model_dump(mode='json'),'gates':g}, ensure_ascii=False))"
    )
    child = subprocess.run(
        [sys.executable, "-c", child_code],
        cwd=root_path,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    restored_state = json.loads(child.stdout)
    assert restored_state["project"]["project_id"] == project_id
    assert restored_state["project"]["version"] == 4
    assert restored_state["project"]["archived_at"] is None
    assert restored_state["project"]["deleted_at"] is None
    assert restored_state["gates"][0]["state"] == "changes_requested"
    assert restored_state["gates"][0]["version"] == 3
    assert "owner_key" not in restored_state["project"]
    assert "idempotency" not in restored_state["project"]


def test_persistent_project_snapshot_fails_closed_on_corruption(tmp_path, monkeypatch):
    """损坏快照不得伪装成空库或黄金种子。"""
    root = tmp_path / "corrupt-project-state"
    monkeypatch.setenv("GW_DATA_DIR", str(root))
    service = ProjectsService(seed_golden_fixture=False, persistent=True)
    assert service.list_projects() == []
    (root / "projects.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(CleanroomException) as excinfo:
        ProjectsService(seed_golden_fixture=False, persistent=True).list_projects()
    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "PROJECT_STATE_CORRUPT"


def test_failed_acl_and_snapshot_publication_burn_reserved_project_ids(tmp_path, monkeypatch):
    """ACL绑定或项目快照发布失败均保留已经持久预留的序号。"""
    root = tmp_path / "failed-publication-state"
    monkeypatch.setenv("GW_DATA_DIR", str(root))
    service = ProjectsService(seed_golden_fixture=False, persistent=True)
    assert service.list_projects() == []
    owner_key = "b" * 64
    payload = ProjectCreateRequest(name="失败不复用", project_type=ProjectType.OTHER)
    orphan_ids = []

    def reject_acl(project_id: str) -> None:
        orphan_ids.append(project_id)
        raise RuntimeError("模拟ACL绑定失败")

    with pytest.raises(RuntimeError, match="ACL绑定"):
        service.create_project(payload, owner_key=owner_key, before_publish=reject_acl)

    original_write = projects_service_module._atomic_write
    def reject_project_snapshot(path: Path, value):
        if path.name == "projects.json":
            orphan_ids.append(value["projects"].copy().popitem()[0])
            raise CleanroomException(503, "PROJECT_STATE_WRITE_FAILED", "模拟源快照发布失败")
        return original_write(path, value)

    monkeypatch.setattr(projects_service_module, "_atomic_write", reject_project_snapshot)
    with pytest.raises(CleanroomException) as excinfo:
        service.create_project(payload, owner_key=owner_key)
    assert excinfo.value.code == "PROJECT_STATE_WRITE_FAILED"
    monkeypatch.setattr(projects_service_module, "_atomic_write", original_write)

    restarted = ProjectsService(seed_golden_fixture=False, persistent=True)
    next_result = restarted.create_project(payload, owner_key=owner_key)
    assert len(set(orphan_ids)) == 2
    assert next_result.project_id not in orphan_ids
    assert next_result.project_id.endswith("-3")
    assert len(restarted.list_projects()) == 1


def test_project_and_gate_ids_survive_data_root_move_and_concurrent_reservations(tmp_path, monkeypatch):
    """拷贝完整状态后namespace保持不变，多个服务实例的序号由公共域锁单调分配。"""
    first_root = tmp_path / "source-root"
    moved_root = tmp_path / "moved-root"
    monkeypatch.setenv("GW_DATA_DIR", str(first_root))
    owner_key = "c" * 64
    payload = ProjectCreateRequest(
        name="搬迁项目",
        project_type=ProjectType.FILM,
        client_request_id="move-gates-001",
        gates=[{"code": "P1", "name": "首审"}],
    )
    source = ProjectsService(seed_golden_fixture=False, persistent=True)
    first = source.create_project(payload, owner_key=owner_key)
    shutil.copytree(first_root, moved_root)
    monkeypatch.setenv("GW_DATA_DIR", str(moved_root))
    moved = ProjectsService(seed_golden_fixture=False, persistent=True)
    assert moved.get_project(first.project_id).project_id == first.project_id

    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "concurrent-root"))
    services = [ProjectsService(seed_golden_fixture=False, persistent=True) for _ in range(3)]
    request = ProjectCreateRequest(name="并发项目", project_type=ProjectType.OTHER)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda index: services[index % len(services)].create_project(request, owner_key=owner_key), range(6)))
    project_ids = [result.project_id for result in results]
    assert len(set(project_ids)) == 6
    assert sorted(int(project_id.rsplit("-", 1)[1]) for project_id in project_ids) == list(range(1, 7))


def test_gate_write_and_project_archive_share_one_domain_lock(tmp_path, monkeypatch):
    """门CAS与项目归档同时竞争时，归档完成后门写不能越过只读屏障。"""
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gate-archive-race"))
    service = ProjectsService(seed_golden_fixture=False, persistent=True)
    owner_key = "d" * 64
    created = service.create_project(
        ProjectCreateRequest(
            name="竞争项目",
            project_type=ProjectType.FILM,
            client_request_id="gate-race-001",
            gates=[{"code": "P1", "name": "首审"}],
        ),
        owner_key=owner_key,
    )
    gate = service.list_project_gates(created.project_id, owner_key=owner_key, role="editor")[0]
    barrier = threading.Barrier(2)

    def update_gate():
        barrier.wait()
        try:
            return service.update_project_gate(
                gate["gate_id"],
                {"state": "approved", "expected_version": 1},
                owner_key=owner_key,
                role="editor",
            )
        except CleanroomException as exc:
            return exc.code

    def archive_project():
        barrier.wait()
        try:
            return service.archive_project(created.project_id, expected_version=1)
        except CleanroomException as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        gate_result = pool.submit(update_gate)
        archive_result = pool.submit(archive_project)
        gate_result = gate_result.result(timeout=10)
        archive_result = archive_result.result(timeout=10)

    assert not isinstance(archive_result, str)
    assert service.get_project(created.project_id).archived_at is not None
    final_gate = service.get_project_gate(gate["gate_id"], owner_key=owner_key, role="editor")
    if isinstance(gate_result, str):
        assert gate_result == "PROJECT_READ_ONLY"
        assert final_gate["state"] == "pending"
        assert final_gate["version"] == 1
    else:
        assert gate_result["state"] == "approved"
        assert final_gate["version"] == 2


def test_persistent_project_creation_does_not_reuse_gate_id_after_source_failure(tmp_path, monkeypatch):
    """初始阶段门ID与项目快照同进退，但快照失败后其预留序号也不回收。"""
    root = tmp_path / "gate-failed-source"
    monkeypatch.setenv("GW_DATA_DIR", str(root))
    service = ProjectsService(seed_golden_fixture=False, persistent=True)
    service.list_projects()
    owner_key = "e" * 64
    payload = ProjectCreateRequest(
        name="门源发布失败",
        project_type=ProjectType.SERIES,
        client_request_id="gate-source-01",
        gates=[{"code": "P1", "name": "门一"}],
    )
    original_write = projects_service_module._atomic_write
    orphaned = []

    def fail_project_file(path: Path, value):
        if path.name == "projects.json":
            orphaned.extend(value["gates"].keys())
            raise CleanroomException(503, "PROJECT_STATE_WRITE_FAILED", "模拟门源快照发布失败")
        return original_write(path, value)

    monkeypatch.setattr(projects_service_module, "_atomic_write", fail_project_file)
    with pytest.raises(CleanroomException):
        service.create_project(payload, owner_key=owner_key)
    monkeypatch.setattr(projects_service_module, "_atomic_write", original_write)
    created = ProjectsService(seed_golden_fixture=False, persistent=True).create_project(payload, owner_key=owner_key)
    gate = ProjectsService(seed_golden_fixture=False, persistent=True).list_project_gates(
        created.project_id, owner_key=owner_key, role="editor"
    )[0]
    assert len(orphaned) == 1
    assert gate["gate_id"] != orphaned[0]
    assert gate["gate_id"].endswith("-2")