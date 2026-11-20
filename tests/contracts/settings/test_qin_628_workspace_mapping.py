"""QIN-628 工作区模式、共享映射与执行端核验契约测试。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_projects, routes_settings
from gw.api.app import create_app
from gw.asset_registry import index_jobs as index_jobs_module
from gw.asset_registry import repository as repository_module
from gw.core import config as runtime_config
from gw.core.errors import CleanroomException
from gw.god_workflow import video_executor as video_executor_module
from gw.projects_hub import service as projects_service_module
from gw.projects_hub.models import ProjectCreateRequest, ProjectType
from gw.projects_hub.service import ProjectsService
from gw.workspaces import context as workspace_context
from gw.workspaces import model, verification
from gw.workspaces.service import WorkspaceService

BASE = "/api/workspaces"
ADMIN = {"X-User-Role": "admin", "Authorization": "Bearer cleanroom-test"}


def _compatible() -> None:
    verification.set_compatibility_checker(
        lambda: {"status": model.COMPATIBILITY_COMPATIBLE, "detail": "兼容检查通过"}
    )


@pytest.fixture()
def workspace_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 允许根不能落在系统目录拒绝清单内；用仓库同级测试目录，避免硬编码本机盘符。
    repo_root = Path(__file__).resolve().parents[3]
    allowed = repo_root.parent / f".pytest-qin628-{tmp_path.name}"
    allowed.mkdir(exist_ok=True)
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    service = WorkspaceService()
    for module in (
        routes_settings,
        projects_service_module,
        repository_module,
        index_jobs_module,
        video_executor_module,
    ):
        monkeypatch.setattr(module, "default_workspace_service", service)
    monkeypatch.setattr(
        routes_projects,
        "default_projects_service",
        ProjectsService(seed_golden_fixture=False, persistent=True),
    )
    verification.set_compatibility_checker(None)
    yield tmp_path, allowed, service
    verification.set_compatibility_checker(None)
    shutil.rmtree(allowed, ignore_errors=True)


def _snapshot(client: TestClient) -> dict:
    response = client.get(BASE, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_default_workspace_is_local_and_nas_not_auto_joined(workspace_runtime):
    with TestClient(create_app()) as client:
        snapshot = _snapshot(client)
    assert snapshot["active_workspace_id"] == model.WORKSPACE_LOCAL_ID
    assert snapshot["result_root_label"] == "独立本机工作区"
    assert [item["mode"] for item in snapshot["workspaces"]] == ["local", "nas"]
    assert snapshot["mappings"] == []
    assert snapshot["compatibility"]["status"] == model.COMPATIBILITY_PENDING


def test_nas_switch_requires_compatibility_and_verified_mapping(workspace_runtime):
    _, allowed, _ = workspace_runtime
    _compatible()
    with TestClient(create_app()) as client:
        snapshot = _snapshot(client)
        response = client.post(
            BASE + "/switch",
            headers=ADMIN,
            json={"workspace_id": model.WORKSPACE_NAS_ID, "expected_version": snapshot["revision"]},
        )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "WORKSPACE_MAPPING_REQUIRED"
    assert allowed.is_dir()


def test_mapping_is_deduped_by_logical_source_and_executor(workspace_runtime):
    _, allowed, service = workspace_runtime
    _compatible()
    with TestClient(create_app()) as client:
        first = client.post(
            BASE + "/mappings",
            headers=ADMIN,
            json={"access_path": str(allowed), "expected_version": 1},
        )
        assert first.status_code == 200, first.text
        second_revision = first.json()["revision"]
        second = client.post(
            BASE + "/mappings",
            headers=ADMIN,
            json={"access_path": str(allowed), "expected_version": second_revision},
        )
        assert second.status_code == 200, second.text
        snapshot = _snapshot(client)
    assert len(snapshot["mappings"]) == 1
    mapping = snapshot["mappings"][0]
    assert mapping["verification"]["status"] == model.VERIFICATION_VERIFIED
    assert mapping["logical_source_id"].startswith("src_")
    assert service.get_snapshot()["mappings"][0]["mapping_id"] == mapping["mapping_id"]


def test_two_execution_machines_register_independently_and_server_context_verifies(workspace_runtime):
    _, allowed, _ = workspace_runtime
    _compatible()
    machines = ("qa_machine_remote_628", "qa_machine_remote_629")
    with TestClient(create_app()) as client:
        revision = 1
        for machine in machines:
            verified = client.post(
                BASE + "/verify",
                headers=ADMIN,
                json={
                    "access_path": str(allowed),
                    "execution_machine_id": machine,
                    "capability": "read_write",
                },
            )
            assert verified.status_code == 200, verified.text
            assert verified.json()["verified_by"] == model.LOCAL_EXECUTION_MACHINE_ID
            assert verified.json()["verification_source"] == "server_context"

            created = client.post(
                BASE + "/mappings",
                headers=ADMIN,
                json={
                    "access_path": str(allowed),
                    "execution_machine_id": machine,
                    "capability": "read_write",
                    "expected_version": revision,
                },
            )
            assert created.status_code == 200, created.text
            revision = created.json()["revision"]
        snapshot = _snapshot(client)
    assert len(snapshot["mappings"]) == 2
    assert {item["execution_machine_id"] for item in snapshot["mappings"]} == set(machines)
    for item in snapshot["mappings"]:
        assert item["verification"]["verified_by"] == model.LOCAL_EXECUTION_MACHINE_ID
        assert item["verification"]["verification_source"] == "server_context"


def test_unreachable_path_does_not_register_partial_mapping(workspace_runtime):
    tmp_path, _, _ = workspace_runtime
    with TestClient(create_app()) as client:
        response = client.post(
            BASE + "/mappings",
            headers=ADMIN,
            json={"access_path": str(tmp_path / "missing"), "expected_version": 1},
        )
        snapshot = _snapshot(client)
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "WORKSPACE_PATH_NOT_PERMITTED"
    assert snapshot["mappings"] == []
    assert snapshot["revision"] == 1


def test_switch_after_verified_mapping_and_cold_read(workspace_runtime):
    _, allowed, service = workspace_runtime
    _compatible()
    with TestClient(create_app()) as client:
        mapping = client.post(
            BASE + "/mappings",
            headers=ADMIN,
            json={
                "access_path": str(allowed),
                "workspace_id": model.WORKSPACE_NAS_ID,
                "expected_version": 1,
            },
        )
        assert mapping.status_code == 200, mapping.text
        revision = mapping.json()["revision"]
        switched = client.post(
            BASE + "/switch",
            headers=ADMIN,
            json={
                "workspace_id": model.WORKSPACE_NAS_ID,
                "expected_version": revision,
                "client_instance_id": "client_a",
            },
        )
        assert switched.status_code == 200, switched.text
        assert switched.json()["active_workspace_id"] == model.WORKSPACE_NAS_ID
        assert switched.json()["result_root_label"] == "连接 NAS 工作区"
    # 新服务实例模拟进程重启后的冷读，工作区与映射必须与最后成功提交一致。
    cold = WorkspaceService().get_snapshot()
    assert cold["active_workspace_id"] == model.WORKSPACE_NAS_ID
    assert len(cold["mappings"]) == 1
    assert service.get_snapshot()["writer_guard"]["client_instance_id"] == "client_a"


def test_stale_expected_version_is_rejected(workspace_runtime):
    _, allowed, _ = workspace_runtime
    _compatible()
    with TestClient(create_app()) as client:
        first = client.post(
            BASE + "/mappings",
            headers=ADMIN,
            json={"access_path": str(allowed), "expected_version": 1},
        )
        assert first.status_code == 200, first.text
        stale = client.post(
            BASE + "/mappings",
            headers=ADMIN,
            json={"access_path": str(allowed), "expected_version": 1},
        )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"


def test_active_writer_guard_blocks_another_client(workspace_runtime):
    with TestClient(create_app()) as client:
        first = client.post(
            BASE + "/switch",
            headers=ADMIN,
            json={
                "workspace_id": model.WORKSPACE_LOCAL_ID,
                "expected_version": 1,
                "client_instance_id": "client_a",
            },
        )
        assert first.status_code == 200, first.text
        second = client.post(
            BASE + "/switch",
            headers=ADMIN,
            json={
                "workspace_id": model.WORKSPACE_LOCAL_ID,
                "expected_version": first.json()["revision"],
                "client_instance_id": "client_b",
            },
        )
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "WORKSPACE_WRITE_CONFLICT"


def _setup_nas(client: TestClient, allowed: Path, client_id: str = "client_a") -> dict:
    _compatible()
    mapping = client.post(
        BASE + "/mappings",
        headers=ADMIN,
        json={
            "access_path": str(allowed),
            "workspace_id": model.WORKSPACE_NAS_ID,
            "expected_version": 1,
        },
    )
    assert mapping.status_code == 200, mapping.text
    switched = client.post(
        BASE + "/switch",
        headers=ADMIN,
        json={
            "workspace_id": model.WORKSPACE_NAS_ID,
            "expected_version": mapping.json()["revision"],
            "client_instance_id": client_id,
        },
    )
    assert switched.status_code == 200, switched.text
    assert client.cookies.get(workspace_context.CLIENT_COOKIE_NAME) == client_id
    return switched.json()


def test_project_writes_follow_nas_mapping_and_stop_when_mapping_is_lost(workspace_runtime):
    tmp_path, allowed, service = workspace_runtime
    with TestClient(create_app()) as client:
        _setup_nas(client, allowed, "client_a")
        token = workspace_context.set_client_instance_id("client_a")
        try:
            execution = service.build_execution_context()
            assert execution["workspace_mode"] == model.MODE_NAS
            assert Path(execution["result_root"]).parent == allowed.resolve()

            projects = ProjectsService(seed_golden_fixture=False, persistent=True)
            created = projects.create_project(
                ProjectCreateRequest(name="NAS 写入证明", project_type=ProjectType.SERIES),
                owner_key="a" * 64,
            )
            nas_state = Path(execution["result_root"]) / "projects.json"
            local_state = tmp_path / "data" / "projects.json"
            assert nas_state.exists()
            assert created.project_id in nas_state.read_text(encoding="utf-8")
            assert (not local_state.exists()) or (
                created.project_id not in local_state.read_text(encoding="utf-8")
            )

            shutil.rmtree(allowed)
            with pytest.raises(CleanroomException) as excinfo:
                projects.create_project(
                    ProjectCreateRequest(name="失联后写入", project_type=ProjectType.SERIES),
                    owner_key="a" * 64,
                )
            assert excinfo.value.code in {
                "WORKSPACE_PATH_UNREACHABLE",
                "WORKSPACE_PATH_NOT_PERMITTED",
            }
            if nas_state.exists():
                assert "失联后写入" not in nas_state.read_text(encoding="utf-8")
        finally:
            workspace_context.reset_client_instance_id(token)


def test_competing_client_cannot_write_project_after_switch(workspace_runtime):
    _, allowed, _ = workspace_runtime
    with TestClient(create_app()) as client:
        _setup_nas(client, allowed, "client_a")
        response = client.post(
            "/api/asset-registry/projects",
            headers={
                **ADMIN,
                workspace_context.CLIENT_HEADER_NAME: "client_b",
            },
            json={"name": "竞争客户端写入", "project_type": "series"},
        )
        snapshot = _snapshot(client)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "WORKSPACE_WRITE_CONFLICT"
    assert snapshot["active_workspace_id"] == model.WORKSPACE_NAS_ID


def test_index_job_binds_context_and_rechecks_mapping_before_queue(workspace_runtime, monkeypatch):
    _, allowed, _ = workspace_runtime
    with TestClient(create_app()) as client:
        _setup_nas(client, allowed, "client_a")

        aliases = {
            "src_qin628": {
                "root_id": "src_qin628",
                "prefix": "",
                "storage_root_id": "src_qin628",
                "availability": "ok",
                "scan_path": str(allowed),
            }
        }
        monkeypatch.setattr(index_jobs_module, "_roots", lambda: ("root", aliases))
        monkeypatch.setattr(index_jobs_module.IndexJobs, "_dispatch", lambda self, job: None)
        if index_jobs_module._default:
            index_jobs_module._default.shutdown()
        index_jobs_module._default = None
        index_jobs = index_jobs_module.service()

        class _Auth:
            subject = "qin-628-tester"
            mode = runtime_config.load_runtime_auth_config().mode
            identity_domain = "test:qin-628"
            role = "editor"

        token = workspace_context.set_client_instance_id("client_a")
        try:
            accepted = index_jobs.submit(
                "reindex", {"roots": ["src_qin628"]}, _Auth(), "qin-628-index"
            )
            job = index_jobs.store.read()["jobs"][accepted["job_id"]]
            assert job["workspace_id"] == model.WORKSPACE_NAS_ID
            assert job["execution_machine_id"] == model.LOCAL_EXECUTION_MACHINE_ID
            assert job["result_root_label"].startswith("连接 NAS 工作区")
            assert Path(job["result_root"]).parent == allowed.resolve()

            before = len(index_jobs.store.read()["jobs"])
            shutil.rmtree(allowed)
            with pytest.raises(CleanroomException) as excinfo:
                index_jobs.submit(
                    "reindex", {"roots": ["src_qin628"]}, _Auth(), "qin-628-index-lost"
                )
            assert excinfo.value.code in {
                "WORKSPACE_PATH_UNREACHABLE",
                "WORKSPACE_PATH_NOT_PERMITTED",
            }
            assert len(index_jobs.store.read()["jobs"]) == before
        finally:
            workspace_context.reset_client_instance_id(token)
            index_jobs.shutdown()
            index_jobs_module._default = None


def test_workspace_json_state_fails_closed_on_corrupt_or_unreadable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """工作区状态只能把「文件不存在」视为空态，读失败不得覆盖原文件。"""
    state = workspace_context.json_state(tmp_path, "qin628_recovery", lambda: {"items": []})
    path = tmp_path / "qin628_recovery.json"

    # 不存在 → 合法空态；首次写入正常创建文件。
    assert state.read() == {"items": []}
    state.mutate(lambda data: data["items"].append("created"))
    assert json.loads(path.read_text(encoding="utf-8")) == {"items": ["created"]}

    # 损坏 JSON → mutate / 直接 write 都必须拒绝，且原文件字节不变。
    corrupt = '{"items": ['
    path.write_text(corrupt, encoding="utf-8")
    corrupt_bytes = path.read_bytes()
    with pytest.raises(CleanroomException) as excinfo:
        state.mutate(lambda data: data["items"].append("should-not-write"))
    assert excinfo.value.code == "WORKSPACE_STATE_READ_FAILED"
    with pytest.raises(CleanroomException) as excinfo:
        state.write({"items": ["overwrite"]})
    assert excinfo.value.code == "WORKSPACE_STATE_READ_FAILED"
    assert path.read_bytes() == corrupt_bytes

    # 读取权限错误 → 同样 fail closed；恢复权限后冷读与写入继续可用。
    kept = {"items": ["kept"]}
    path.write_text(json.dumps(kept), encoding="utf-8")
    kept_bytes = path.read_bytes()
    real_open = Path.open

    def denied_open(self: Path, *args, **kwargs):
        if self == path:
            raise PermissionError("simulated read permission denied")
        return real_open(self, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(Path, "open", denied_open)
        with pytest.raises(CleanroomException) as excinfo:
            state.mutate(lambda data: data["items"].append("should-not-write"))
        assert excinfo.value.code == "WORKSPACE_STATE_READ_FAILED"
        with open(path, "rb") as handle:
            assert handle.read() == kept_bytes

    assert state.read() == kept
    state.mutate(lambda data: data["items"].append("after-recovery"))
    assert state.read() == {"items": ["kept", "after-recovery"]}
