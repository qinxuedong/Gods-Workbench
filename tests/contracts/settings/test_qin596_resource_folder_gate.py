"""QIN-596 系统目录拒绝清单三入口契约与 VERSION_CONFLICT 确定性编排。"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_asset_registry
from gw.api.app import create_app
from gw.core.errors import CleanroomException
from gw.settings.service import (
    StorageAllowedRootsService,
    StorageSettingsService,
)

BASE = "/api/asset-registry"
ADMIN = {"X-User-Role": "admin", "Authorization": "Bearer cleanroom-test"}
EDITOR = {"X-User-Role": "editor", "Authorization": "Bearer cleanroom-test"}


@pytest.fixture()
def gate_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    allowed = tmp_path / "Assets"
    allowed.mkdir()
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    settings_service = StorageSettingsService()
    roots_service = StorageAllowedRootsService()
    monkeypatch.setattr(routes_asset_registry, "default_storage_settings_service", settings_service)
    monkeypatch.setattr("gw.settings.service.default_storage_settings_service", settings_service)
    monkeypatch.setattr("gw.settings.service.default_storage_allowed_roots_service", roots_service)
    from gw.api import routes_settings

    monkeypatch.setattr(routes_settings, "default_storage_settings_service", settings_service)
    monkeypatch.setattr(routes_settings, "default_storage_allowed_roots_service", roots_service)
    yield tmp_path, allowed


BLOCKED_SAMPLES = [
    "C:", "C:\\", "D:\\", "C:/",
    "\\\\NAS", "\\\\NAS\\",
    "/", "//",
    "C:\\Windows", "C:\\Windows\\Fonts", "E:/Program Files/工具",
    "C:\\ProgramData", "C:\\Users", "C:\\Users\\某用户",
    "/Users", "/Users/某用户",
    "/System", "/Library", "/usr/bin",
]


def test_rejection_list_blocks_register_entry(gate_runtime):
    tmp_path, allowed = gate_runtime
    with TestClient(create_app()) as client:
        for sample in BLOCKED_SAMPLES:
            response = client.post(
                BASE + "/reindex",
                headers=EDITOR,
                json={
                    "background": True,
                    "hash_files": False,
                    "max_files": 10,
                    "roots": ["local_extra_probe"],
                    "register_root": {
                        "id": "local_extra_probe",
                        "name": "探针",
                        "path": sample,
                        "expected_version": 1,
                    },
                },
            )
            assert response.status_code == 403, (sample, response.text)
            assert response.json()["detail"]["code"] == "RESOURCE_FOLDER_NOT_PERMITTED", sample


def test_rejection_list_blocks_restore_entry(gate_runtime):
    tmp_path, allowed = gate_runtime
    with TestClient(create_app()) as client:
        roots_service = StorageAllowedRootsService()
        roots_service._state.mutate(
            lambda state: state["roots"].append(
                {"id": "root_removed_probe", "path": "C:\\Windows", "aliases": [], "active": False}
            )
        )
        response = client.request(
            "POST",
            "/api/storage-settings/roots/root_removed_probe/restore",
            headers=ADMIN,
            json={"expected_version": 1},
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "RESOURCE_FOLDER_NOT_PERMITTED"


def test_rejection_list_allows_normal_paths_and_keeps_invalid_path_semantics(gate_runtime):
    tmp_path, allowed = gate_runtime
    normal = tmp_path / "普通素材目录"
    normal.mkdir()
    with TestClient(create_app()) as client:
        ok = client.post(
            "/api/storage-settings/roots",
            headers=ADMIN,
            json={"path": str(normal), "expected_version": 1},
        )
        assert ok.status_code == 200, ok.text
        bad = client.post(
            "/api/storage-settings/roots",
            headers=ADMIN,
            json={"path": "not-absolute", "expected_version": 2},
        )
        assert bad.status_code == 400, bad.text
        assert bad.json()["detail"]["code"] == "INVALID_PATH"


def test_register_root_reuse_same_id_is_idempotent(gate_runtime):
    tmp_path, allowed = gate_runtime
    with TestClient(create_app()) as client:
        payload = {
            "background": True,
            "hash_files": False,
            "max_files": 10,
            "roots": ["local_extra_same"],
            "register_root": {
                "id": "local_extra_same",
                "name": "同名库",
                "path": str(allowed),
                "expected_version": 1,
            },
        }
        first = client.post(
            BASE + "/reindex",
            headers={**EDITOR, "Idempotency-Key": "qin596-idem-first"},
            json=payload,
        )
        assert first.status_code == 202, first.text
        snapshot = client.get("/api/storage-settings", headers=EDITOR).json()
        second = client.post(
            BASE + "/reindex",
            headers={**EDITOR, "Idempotency-Key": "qin596-idem-second"},
            json={
                "background": True,
                "hash_files": False,
                "max_files": 10,
                "roots": ["local_extra_same"],
                "register_root": {
                    "id": "local_extra_same",
                    "name": "同名库",
                    "path": str(allowed),
                    "expected_version": snapshot["revision"],
                },
            },
        )
        assert second.status_code == 202, second.text
        final = client.get("/api/storage-settings", headers=EDITOR).json()
        entries = [item for item in final["extra_local_entries"] if item["id"] == "local_extra_same"]
        assert len(entries) == 1


def test_rejection_list_blocks_dot_segment_traversals_on_all_entries(gate_runtime):
    """审核 FIX_REQUIRED：点段路径不得绕过系统目录清单（三入口 + 越界 + 合法点段）。"""
    tmp_path, allowed = gate_runtime
    normal = tmp_path / "普通素材目录"
    normal.mkdir()
    with TestClient(create_app()) as client:
        traversals = ["C:/foo/../Windows", "C:\\foo\\..\\Windows", "C:/../Windows", "/foo/../System"]
        for sample in traversals:
            add = client.post(
                "/api/storage-settings/roots",
                headers=ADMIN,
                json={"path": sample, "expected_version": 1},
            )
            assert add.status_code == 403, (sample, add.text)
            assert add.json()["detail"]["code"] == "RESOURCE_FOLDER_NOT_PERMITTED", sample
            register = client.post(
                BASE + "/reindex",
                headers=EDITOR,
                json={
                    "background": True,
                    "hash_files": False,
                    "max_files": 10,
                    "roots": ["local_extra_dotseg"],
                    "register_root": {
                        "id": "local_extra_dotseg",
                        "name": "点段探针",
                        "path": sample,
                        "expected_version": 1,
                    },
                },
            )
            assert register.status_code == 403, (sample, register.text)
            assert register.json()["detail"]["code"] == "RESOURCE_FOLDER_NOT_PERMITTED", sample
        # restore 入口：inactive 记录 path 为点段形态，恢复被拒。
        roots_service = StorageAllowedRootsService()
        roots_service._state.mutate(
            lambda state: state["roots"].append(
                {"id": "root_removed_dotseg", "path": "C:/foo/../Windows", "aliases": [], "active": False}
            )
        )
        restore = client.request(
            "POST",
            "/api/storage-settings/roots/root_removed_dotseg/restore",
            headers=ADMIN,
            json={"expected_version": 1},
        )
        assert restore.status_code == 403, restore.text
        assert restore.json()["detail"]["code"] == "RESOURCE_FOLDER_NOT_PERMITTED"
        # 合法点段（消解后非系统目录）不扩大拒绝面：`<normal>/sub/..` 消解回
        # normal 本身（正斜杠形态在 Windows/POSIX 的 normalize 语义一致）。
        benign = f"{normal}/sub/.."
        ok = client.post(
            "/api/storage-settings/roots",
            headers=ADMIN,
            json={"path": benign, "expected_version": 1},
        )
        assert ok.status_code == 200, ok.text


def test_version_conflict_orchestration(gate_runtime, monkeypatch: pytest.MonkeyPatch):
    """v9 §6.5 确定性编排：B 过 duplicate 检查后暂停在 patch 前；A 的 patch
    透传成功推进 revision；释放 B 得 VERSION_CONFLICT；失败可见；known 重试收敛。
    """
    tmp_path, allowed = gate_runtime
    arrived = threading.Event()
    release = threading.Event()
    calls = {"n": 0}
    original_patch = StorageSettingsService.patch

    def selective_patch(self, request):
        calls["n"] += 1
        if calls["n"] == 1:
            # 只阻塞首个 patch 调用（编排中首个到达者即 B）；后续调用透传。
            arrived.set()
            release.wait(timeout=10)
        return original_patch(self, request)

    monkeypatch.setattr(StorageSettingsService, "patch", selective_patch)

    b_payload = {
        "background": True,
        "hash_files": False,
        "max_files": 10,
        "roots": ["local_extra_b"],
        "register_root": {
            "id": "local_extra_b",
            "name": "入口B",
            "path": str(allowed),
            "expected_version": 1,
        },
    }

    def call_b():
        try:
            routes_asset_registry.reindex_registry(
                idempotency_key="qin596-version-b",
                payload=b_payload,
                authorization="Bearer cleanroom-test",
                x_user_role="editor",
            )
        except CleanroomException as error:
            b_outcome.append(error.code)

    b_outcome: list = []
    worker = threading.Thread(target=call_b)
    worker.start()
    assert arrived.wait(timeout=10), "B 未在期限内到达 patch 屏障"
    with TestClient(create_app()) as client:
        a_response = client.post(
            BASE + "/reindex",
            headers={**EDITOR, "Idempotency-Key": "qin596-version-a"},
            json={
                "background": True,
                "hash_files": False,
                "max_files": 10,
                "roots": ["local_extra_a"],
                "register_root": {
                    "id": "local_extra_a",
                    "name": "入口A",
                    "path": str(allowed),
                    "expected_version": 1,
                },
            },
        )
        assert a_response.status_code == 202, a_response.text
    release.set()
    worker.join(timeout=10)
    assert b_outcome == ["VERSION_CONFLICT"], b_outcome
    # S4/S5：A 的登记保留；settings 恰一条登记（local_extra_a），无 local_extra_b。
    snapshot = StorageSettingsService().get_snapshot()
    entry_ids = [str(item.get("id") or "") for item in snapshot.extra_local_entries]
    assert entry_ids.count("local_extra_a") == 1
    assert "local_extra_b" not in entry_ids
    tmp_path, allowed = gate_runtime
    with TestClient(create_app()) as client:
        for sample in BLOCKED_SAMPLES:
            response = client.post(
                "/api/storage-settings/roots",
                headers=ADMIN,
                json={"path": sample, "expected_version": 1},
            )
            assert response.status_code == 403, (sample, response.text)
            assert response.json()["detail"]["code"] == "RESOURCE_FOLDER_NOT_PERMITTED", sample
