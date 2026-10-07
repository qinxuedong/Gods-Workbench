"""Phase 12 合法成功分支 HTTP 覆盖；所有写入都由本测试自己的隔离数据产生。"""

import base64
import json
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.api import routes_asset_registry, routes_god_canvas, routes_projects, routes_settings
from gw.core import audit as audit_log
from gw.core.auth import require_edit_access
from gw.core.auth_management import _Approval, store as auth_store
from gw.god_canvas.service import GodCanvasService
from gw.projects_hub.models import ProjectCreateRequest
from gw.projects_hub import service as projects_service_module
from gw.projects_hub.service import ProjectsService, owner_key_for_context
from gw.settings.service import AssetStructureService
from gw.video_tasks import service as video_service_module
from gw.video_tasks.service import VideoTaskService, get_video_service
from gw.video_tasks.store import VideoTaskStore


EDITOR = {"Authorization": "Bearer phase12-success-fixture", "X-User-Role": "editor"}
GOVERNOR = {**EDITOR, "X-User-Role": "governor"}
ADMIN = {**EDITOR, "X-User-Role": "admin"}


def _client():
    return TestClient(create_app())


def _library_snapshot(client: TestClient) -> dict:
    response = client.get("/api/asset-library", headers=EDITOR)
    assert response.status_code == 200, response.text
    return response.json()["library"]


def _library_and_category(client: TestClient, *, suffix: str = "fixture") -> tuple[str, str]:
    library = client.post(
        "/api/asset-library/libraries",
        headers=EDITOR,
        json={"name": f"Phase12 {suffix} library", "expected_version": 1},
    )
    assert library.status_code == 201, library.text
    library_id = library.json()["library"]["library_id"]
    current = next(item for item in _library_snapshot(client)["libraries"] if item["library_id"] == library_id)
    category = client.post(
        "/api/asset-library/categories",
        headers=EDITOR,
        json={
            "library_id": library_id,
            "name": f"Phase12 {suffix} category",
            "type": "image",
            "expected_version": current["version"],
        },
    )
    assert category.status_code == 201, category.text
    return library_id, category.json()["category"]["category_id"]


def _create_project(client: TestClient, name: str) -> tuple[str, int]:
    response = client.post(
        "/api/asset-registry/projects",
        headers=EDITOR,
        json={"name": name, "project_type": "other"},
    )
    assert response.status_code == 201, response.text
    project = response.json()["project"]
    return project["project_id"], project["version"]


def test_auth_users_get_and_delete_success_roundtrip():
    """用户列表只读回本测试创建的数据，删除使用创建响应中的 CAS 版本。"""
    auth_store.reset_for_tests()
    audit_log.reset_audit_log()
    try:
        with _client() as client:
            created = client.post(
                "/api/asset-auth/users",
                headers=ADMIN,
                json={"display_name": "Phase12 owned user", "role": "editor"},
            )
            assert created.status_code == 201, created.text
            user = created.json()["user"]

            listed = client.get("/api/asset-auth/users", headers=ADMIN)
            assert listed.status_code == 200, listed.text
            matches = [item for item in listed.json()["users"] if item["user_id"] == user["user_id"]]
            assert matches == [user]
            assert "token" not in matches[0]
            assert matches[0].get("external_subject") in (None, "")

            deleted = client.request(
                "DELETE",
                f"/api/asset-auth/users/{user['user_id']}",
                headers=ADMIN,
                json={"expected_version": user["version"]},
            )
            assert deleted.status_code == 204, deleted.text
            assert deleted.content == b""
            after = client.get("/api/asset-auth/users", headers=ADMIN)
            assert after.status_code == 200, after.text
            assert user["user_id"] not in {item["user_id"] for item in after.json()["users"]}
    finally:
        auth_store.reset_for_tests()
        audit_log.reset_audit_log()


def test_operation_approval_put_success_uses_owned_fixture():
    """契约没有公开审批创建端点，因此只为本用例注入一条隔离的待审记录。"""
    auth_store.reset_for_tests()
    audit_log.reset_audit_log()
    approval_id = "ap-phase12-success-owned"
    try:
        auth_store.approvals[approval_id] = _Approval(approval_id=approval_id, owner_subject=None)
        with _client() as client:
            response = client.put(
                f"/api/asset-auth/operation-approvals/{approval_id}",
                headers=ADMIN,
                json={"decision": "approved", "expected_version": 1, "reason": "Phase12 HTTP success fixture"},
            )
            assert response.status_code == 200, response.text
            approval = response.json()["approval"]
            assert approval["approval_id"] == approval_id
            assert approval["status"] == "approved"
            assert approval["version"] == 2

            readback = client.get("/api/asset-auth/operation-approvals?status=approved", headers=ADMIN)
            assert readback.status_code == 200, readback.text
            assert approval_id in {item["approval_id"] for item in readback.json()["approvals"]}
    finally:
        auth_store.reset_for_tests()
        audit_log.reset_audit_log()


def test_library_category_and_item_success_lifecycle():
    """覆盖素材库/分类 CAS 更新删除，以及条目移动、批量删除的 HTTP 写后读回。"""
    with _client() as client:
        library_response = client.post(
            "/api/asset-library/libraries",
            headers=EDITOR,
            json={"name": "Phase12 lifecycle library", "expected_version": 1},
        )
        assert library_response.status_code == 201, library_response.text
        library_id = library_response.json()["library"]["library_id"]

        library = next(item for item in _library_snapshot(client)["libraries"] if item["library_id"] == library_id)
        source_response = client.post(
            "/api/asset-library/categories",
            headers=EDITOR,
            json={"library_id": library_id, "name": "Phase12 source", "type": "image", "expected_version": library["version"]},
        )
        assert source_response.status_code == 201, source_response.text
        source_id = source_response.json()["category"]["category_id"]
        library = next(item for item in _library_snapshot(client)["libraries"] if item["library_id"] == library_id)
        target_response = client.post(
            "/api/asset-library/categories",
            headers=EDITOR,
            json={"library_id": library_id, "name": "Phase12 target", "type": "image", "expected_version": library["version"]},
        )
        assert target_response.status_code == 201, target_response.text
        target_id = target_response.json()["category"]["category_id"]

        library = next(item for item in _library_snapshot(client)["libraries"] if item["library_id"] == library_id)
        batch = client.post(
            "/api/asset-library/items/batch",
            headers=EDITOR,
            json={"library_id": library_id, "category_id": source_id,
                  "names": ["to-keep.png", "to-delete.png", "to-move.png"],
                  "expected_version": library["version"]},
        )
        assert batch.status_code == 201, batch.text
        keep_id, delete_id, move_id = batch.json()["asset_ids"]

        moved = client.post("/api/asset-library/items/move", headers=EDITOR,
                            json={"asset_ids": [move_id], "category_id": target_id})
        assert moved.status_code == 200, moved.text
        assert moved.json()["moved"] == [move_id]
        batch_deleted = client.post("/api/asset-library/items/delete", headers=EDITOR,
                                   json={"asset_ids": [delete_id]})
        assert batch_deleted.status_code == 200, batch_deleted.text
        assert batch_deleted.json()["deleted"] == [delete_id]

        snapshot = _library_snapshot(client)
        source = next(category for lib in snapshot["libraries"] if lib["library_id"] == library_id
                      for category in lib["categories"] if category["category_id"] == source_id)
        target = next(category for lib in snapshot["libraries"] if lib["library_id"] == library_id
                      for category in lib["categories"] if category["category_id"] == target_id)
        assert {item["asset_id"] for item in source["items"]} == {keep_id}
        assert {item["asset_id"] for item in target["items"]} == {move_id}

        # 先清空两类，再按真实版本删除；被测删除语义是 HTTP 接口，而非直接调用 service。
        for asset_id in (keep_id, move_id):
            removed = client.post("/api/asset-library/items/delete", headers=EDITOR,
                                  json={"asset_ids": [asset_id]})
            assert removed.status_code == 200, removed.text
            assert removed.json()["deleted"] == [asset_id]

        source = next(category for lib in _library_snapshot(client)["libraries"] if lib["library_id"] == library_id
                      for category in lib["categories"] if category["category_id"] == source_id)
        renamed_category = client.patch(
            f"/api/asset-library/categories/{source_id}",
            headers=EDITOR,
            json={"name": "Phase12 renamed source", "expected_version": source["version"]},
        )
        assert renamed_category.status_code == 200, renamed_category.text
        assert renamed_category.json()["name"] == "Phase12 renamed source"
        removed_category = client.delete(
            f"/api/asset-library/categories/{source_id}?expected_version={renamed_category.json()['version']}",
            headers=EDITOR,
        )
        assert removed_category.status_code == 200, removed_category.text
        assert removed_category.json() == {"category_id": source_id, "deleted": True}

        target = next(category for lib in _library_snapshot(client)["libraries"] if lib["library_id"] == library_id
                      for category in lib["categories"] if category["category_id"] == target_id)
        removed_target = client.delete(
            f"/api/asset-library/categories/{target_id}?expected_version={target['version']}", headers=EDITOR
        )
        assert removed_target.status_code == 200, removed_target.text

        library = next(item for item in _library_snapshot(client)["libraries"] if item["library_id"] == library_id)
        renamed_library = client.patch(
            f"/api/asset-library/libraries/{library_id}",
            headers=EDITOR,
            json={"name": "Phase12 renamed library", "expected_version": library["version"]},
        )
        assert renamed_library.status_code == 200, renamed_library.text
        assert renamed_library.json()["name"] == "Phase12 renamed library"
        deleted_library = client.delete(
            f"/api/asset-library/libraries/{library_id}?expected_version={renamed_library.json()['version']}",
            headers=EDITOR,
        )
        assert deleted_library.status_code == 200, deleted_library.text
        assert deleted_library.json() == {"library_id": library_id, "deleted": True}
        assert library_id not in {item["library_id"] for item in _library_snapshot(client)["libraries"]}


def test_asset_classification_background_delete_cancels_fixture_job():
    """以隔离任务快照构造可取消运行态，再通过真实 HTTP 删除端点完成取消。"""
    from gw.asset_library import repository as asset_library_repo

    job_id = "acjob_phase12_running"
    asset_library_repo._job_state().write({
        "jobs": {job_id: {"job_id": job_id, "state": "running", "requested": 1, "classified": 0}},
        "sequence": 1,
        "active": job_id,
    })
    with _client() as client:
        response = client.delete("/api/asset-classification/background", headers=EDITOR)
        assert response.status_code == 200, response.text
        assert response.json() == {"cancelled": True, "active": None}
        readback = client.get(f"/api/asset-classification/jobs/{job_id}", headers=EDITOR)
        assert readback.status_code == 200, readback.text
        assert readback.json()["state"] == "cancelled"


def test_asset_avatar_status_and_registration_read_back(tmp_path, monkeypatch):
    """头像注册只读写允许根目录内的测试文件，不访问外部平台。"""
    from gw.core import storage

    allowed_root = tmp_path / "allowed-avatars"
    allowed_root.mkdir()
    avatar_path = allowed_root / "avatar-fixture.png"
    avatar_path.write_bytes(b"controlled-avatar-fixture")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed_root))

    with _client() as client:
        library_id, category_id = _library_and_category(client, suffix="avatar")
        library = next(item for item in _library_snapshot(client)["libraries"] if item["library_id"] == library_id)
        created = client.post(
            "/api/asset-library/items/batch",
            headers=EDITOR,
            json={"library_id": library_id, "category_id": category_id, "names": ["avatar-item.png"],
                  "expected_version": library["version"]},
        )
        assert created.status_code == 201, created.text
        asset_id = created.json()["asset_ids"][0]

        before = client.post(f"/api/asset-library/items/{asset_id}/avatar-status", headers=EDITOR)
        assert before.status_code == 200, before.text
        assert before.json()["asset_id"] == asset_id and before.json()["registered"] is False

        registered = client.post(
            f"/api/asset-library/items/{asset_id}/register-avatar",
            headers=EDITOR,
            json={"avatar_path": str(avatar_path)},
        )
        assert registered.status_code == 200, registered.text
        assert registered.json()["registered"] is True
        assert registered.json()["avatar_path"] == storage.relative_display(avatar_path)

        after = client.post(f"/api/asset-library/items/{asset_id}/avatar-status", headers=EDITOR)
        assert after.status_code == 200, after.text
        assert after.json()["registered"] is True
        assert after.json()["avatar_path"] == registered.json()["avatar_path"]


def test_workflows_upload_writes_only_inside_allowed_root(tmp_path, monkeypatch):
    """工作流上传从受控 base64 载荷真实落盘，并以接口回包做写后核验。"""
    allowed_root = tmp_path / "allowed-workflows"
    allowed_root.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed_root))
    destination = allowed_root / "fixture.godmap"
    payload = json.dumps({"workflow": "phase12-owned", "nodes": []}, separators=(",", ":")).encode("utf-8")

    with _client() as client:
        response = client.post(
            "/api/asset-library/workflows/upload",
            headers=EDITOR,
            json={"path": str(destination), "content_base64": base64.b64encode(payload).decode("ascii")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["written"] is True
        assert response.json()["size_bytes"] == len(payload)
        assert destination.read_bytes() == payload
        assert str(allowed_root) not in response.json()["display_path"]


def test_asset_structure_get_and_patch_success_roundtrip(monkeypatch):
    """素材结构使用本用例专属内存服务，更新必须 CAS 且 GET 读回新版本。"""
    monkeypatch.setattr(routes_settings, "default_asset_structure_service", AssetStructureService())
    with _client() as client:
        created = client.post(
            "/api/asset-registry/asset-structures",
            headers=EDITOR,
            json={"kind": "version", "asset_ids": ["phase12-asset-a", "phase12-asset-b"],
                  "current_asset_id": "phase12-asset-a"},
        )
        assert created.status_code == 201, created.text
        structure = created.json()["structure"]
        structure_id = structure["structure_id"]

        first_read = client.get(f"/api/asset-registry/asset-structures/{structure_id}", headers=EDITOR)
        assert first_read.status_code == 200, first_read.text
        assert first_read.json()["structure"]["members"] == structure["members"]

        updated = client.patch(
            f"/api/asset-registry/asset-structures/{structure_id}",
            headers=EDITOR,
            json={"current_asset_id": "phase12-asset-b", "expected_version": structure["version"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["structure"]["current_asset_id"] == "phase12-asset-b"
        assert updated.json()["structure"]["version"] == structure["version"] + 1

        readback = client.get(f"/api/asset-registry/asset-structures/{structure_id}", headers=EDITOR)
        assert readback.status_code == 200, readback.text
        assert readback.json()["structure"] == updated.json()["structure"]


def test_canonical_project_patch_and_governance_restore_success():
    """规范项目路由用 API 自建项目，覆盖 CAS 编辑、治理归档与治理恢复。"""
    with _client() as client:
        project_id, version = _create_project(client, "Phase12 project before patch")
        updated = client.patch(
            f"/api/asset-registry/projects/{project_id}",
            headers=EDITOR,
            json={"name": "Phase12 project after patch", "progress": 25, "expected_version": version},
        )
        assert updated.status_code == 200, updated.text
        project = updated.json()["project"]
        assert project["project_id"] == project_id
        assert project["version"] == version + 1
        after_patch = client.get("/api/asset-registry/projects?archived=false", headers=EDITOR)
        assert after_patch.status_code == 200, after_patch.text
        patched_record = next(item for item in after_patch.json()["projects"] if item["project_id"] == project_id)
        assert patched_record["name"] == "Phase12 project after patch"
        assert patched_record["progress"] == 25

        archived = client.request(
            "DELETE",
            f"/api/asset-registry/projects/{project_id}",
            headers=GOVERNOR,
            json={"expected_version": project["version"]},
        )
        assert archived.status_code == 200, archived.text
        archived_project = archived.json()["project"]
        assert archived_project["archived_at"]

        restored = client.post(
            f"/api/asset-registry/governance/projects/{project_id}/restore",
            headers=GOVERNOR,
            json={"expected_version": archived_project["version"]},
        )
        assert restored.status_code == 200, restored.text
        restored_project = restored.json()["project"]
        assert restored_project["project_id"] == project_id
        assert restored_project["archived_at"] is None
        assert restored_project["version"] == archived_project["version"] + 1
        active = client.get("/api/asset-registry/projects?archived=false", headers=EDITOR)
        assert active.status_code == 200, active.text
        restored_record = next(item for item in active.json()["projects"] if item["project_id"] == project_id)
        assert restored_record["name"] == "Phase12 project after patch"
        assert restored_record["version"] == restored_project["version"]


def test_local_asset_items_patch_updates_owned_uploaded_file(tmp_path, monkeypatch):
    """先经 HTTP 上传创建本地索引，再通过目标 PATCH 写入并读回名称。"""
    allowed_root = tmp_path / "allowed-local-assets"
    allowed_root.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed_root))
    destination = allowed_root / "owned-fixture.png"
    content = b"controlled-local-asset-fixture"

    with _client() as client:
        uploaded = client.post(
            "/api/local-assets/upload",
            headers=EDITOR,
            json={"path": str(destination), "content_base64": base64.b64encode(content).decode("ascii"),
                  "kind": "image"},
        )
        assert uploaded.status_code == 201, uploaded.text
        assert uploaded.json()["written"] is True
        assert destination.read_bytes() == content

        listed = client.get("/api/local-assets", headers=EDITOR)
        assert listed.status_code == 200, listed.text
        item = next(item for item in listed.json()["items"] if item["display_path"] == uploaded.json()["display_path"])
        patched = client.patch(
            "/api/local-assets/items",
            headers=EDITOR,
            json={"asset_id": item["asset_id"], "name": "owned-renamed-fixture.png", "kind": "image"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["name"] == "owned-renamed-fixture.png"
        reread = client.get("/api/local-assets", headers=EDITOR)
        assert reread.status_code == 200, reread.text
        matched = next(entry for entry in reread.json()["items"] if entry["asset_id"] == item["asset_id"])
        assert matched["name"] == "owned-renamed-fixture.png"
        assert matched["display_path"] == uploaded.json()["display_path"]


def _make_small_video(path):
    """只用本机 FFmpeg 生成一个测试媒体，不访问 Provider 或外网。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("本机未安装 ffmpeg，不能生成隔离的视频授权素材")
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=blue:s=64x64:r=10:d=0.3", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(path)],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert path.is_file() and path.stat().st_size > 0


def _claim_video_provider(video_bytes: bytes):
    """隔离本地回环Provider；只响应本测试的创建、轮询和有效MP4下载。"""
    state = {"posts": 0, "polls": 0, "downloads": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _json(self, value):
            body = json.dumps(value).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            state["posts"] += 1
            self._json({"task_id": "phase12-claim-upstream"})

        def do_GET(self):
            if self.path == "/artifact":
                state["downloads"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(video_bytes)))
                self.end_headers()
                self.wfile.write(video_bytes)
                return
            state["polls"] += 1
            self._json({"status": "completed", "url": base_url + "/artifact"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base_url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, base_url, state


def test_video_project_claim_and_asset_authorize_http_success(tmp_path, monkeypatch):
    """A的历史真源由B真实HTTP claim；仅B能授权素材并完成回环视频任务。"""
    from gw.projects_hub.models import ProjectType

    owner_a = {"Authorization": "Bearer phase12-video-owner-a", "X-User-Role": "editor"}
    grantee_b_governor = {"Authorization": "Bearer phase12-video-grantee-b", "X-User-Role": "governor"}
    grantee_b = {**grantee_b_governor, "X-User-Role": "editor"}
    actor_c = {"Authorization": "Bearer phase12-video-actor-c", "X-User-Role": "editor"}

    allowed_root = tmp_path / "allowed-video-assets"
    allowed_root.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed_root))
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "persistent-data"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "persistent-video"))
    monkeypatch.setenv("GW_PHASE12_VIDEO_KEY", "isolated-video-fixture")

    video_path = allowed_root / "phase12-authorize.mp4"
    _make_small_video(video_path)
    server, provider_thread, provider_url, provider_state = _claim_video_provider(video_path.read_bytes())
    monkeypatch.setenv(
        "GW_PROVIDER_RUNTIME_JSON",
        json.dumps({
            "phase12-loopback": {
                "base_url": provider_url,
                "api_key_env": "GW_PHASE12_VIDEO_KEY",
                "models": ["model"],
                "default_model": "model",
                "video_protocol": "newapi_video",
            }
        }),
    )

    projects = ProjectsService(seed_golden_fixture=False, persistent=True)
    monkeypatch.setattr(projects_service_module, "default_projects_service", projects)
    monkeypatch.setattr(routes_projects, "default_projects_service", projects)
    monkeypatch.setattr(routes_asset_registry, "default_projects_service", projects)
    monkeypatch.setattr(video_service_module, "default_projects_service", projects)
    canvases = GodCanvasService(seed_golden_fixture=False)
    monkeypatch.setattr(routes_god_canvas, "default_god_canvas_service", canvases)
    monkeypatch.setattr(video_service_module, "default_god_canvas_service", canvases)

    owner_context = require_edit_access(owner_a["Authorization"], owner_a["X-User-Role"])
    source_owner_key = owner_key_for_context(owner_context)
    project = projects.create_project(
        ProjectCreateRequest(name="Phase12 controlled legacy video project", project_type=ProjectType.FILM),
        owner_key=source_owner_key,
    )
    project_id = project.project_id

    video = VideoTaskService(
        VideoTaskStore(tmp_path / "persistent-video"), allow_private_artifacts_for_test=True
    )
    monkeypatch.setattr(video_service_module, "_default_service", video)
    canvas_response = None
    try:
        with video.store.reader() as db:
            assert db.execute("SELECT 1 FROM video_projects WHERE project_id=?", (project_id,)).fetchone() is None

        with _client() as client:
            claimed = client.post(
                f"/api/video-project-access/{project_id}/claim", headers=grantee_b_governor
            )
            assert claimed.status_code == 200, claimed.text
            assert claimed.json() == {"project_id": project_id, "owner_bound": True}
            with video.store.reader() as db:
                acl = dict(db.execute(
                    "SELECT * FROM video_projects WHERE project_id=?", (project_id,)
                ).fetchone())
            assert acl["owner_key"] == video.actor(require_edit_access(
                grantee_b["Authorization"], grantee_b["X-User-Role"]
            ))
            assert acl["source_owner_key"] == source_owner_key

            canvas_response = client.post(
                "/api/canvases",
                headers=owner_a,
                json={"project_id": project_id, "title": "历史项目视频画布", "mode": "classic"},
            )
            assert canvas_response.status_code == 201, canvas_response.text
            canvas = canvas_response.json()["canvas"]
            entity_id = "phase12-video-output"
            topology = client.patch(
                f"/api/canvases/{canvas['canvas_id']}",
                headers=owner_a,
                json={
                    "expected_version": canvas["version"],
                    "nodes": [{"entity_id": entity_id, "kind": "output"}],
                    "connections": [],
                },
            )
            assert topology.status_code == 200, topology.text

            imported = client.post(
                "/api/asset-registry/assets/import",
                headers=grantee_b,
                json={"items": [{"name": video_path.name, "kind": "video"}]},
            )
            assert imported.status_code == 200, imported.text
            asset_id = imported.json()["asset_ids"][0]
            attached = client.patch(
                f"/api/asset-registry/assets/{asset_id}",
                headers=grantee_b,
                json={"display_path": str(video_path), "expected_version": imported.json()["revision"]},
            )
            assert attached.status_code == 200, attached.text

            authorized = client.post(
                f"/api/video-projects/{project_id}/assets/authorize",
                headers=grantee_b,
                json={"asset_id": asset_id},
            )
            assert authorized.status_code == 200, authorized.text
            assert authorized.json()["authorized"] is True
            for unauthorized_actor in (owner_a, actor_c):
                denied = client.post(
                    f"/api/video-projects/{project_id}/assets/authorize",
                    headers=unauthorized_actor,
                    json={"asset_id": asset_id},
                )
                assert denied.status_code == 403, denied.text

            payload = {
                "prompt": "受控本地回环生成",
                "provider_id": "phase12-loopback",
                "model": "model",
                "duration": 1,
                "aspect_ratio": "16:9",
                "production_context": {
                    "project_id": project_id,
                    "canvas_id": canvas["canvas_id"],
                    "entity_id": entity_id,
                },
            }
            for unauthorized_actor in (owner_a, actor_c):
                denied = client.post(
                    "/api/video-tasks",
                    headers={**unauthorized_actor, "Idempotency-Key": "phase12-denied-" + unauthorized_actor["Authorization"][-1]},
                    json=payload,
                )
                assert denied.status_code == 403, denied.text

            accepted = client.post(
                "/api/video-tasks",
                headers={**grantee_b, "Idempotency-Key": "phase12-claim-success-001"},
                json=payload,
            )
            assert accepted.status_code == 202, accepted.text
            job_id = accepted.json()["job_id"]
            deadline = time.monotonic() + 15
            completed = None
            while time.monotonic() < deadline:
                completed = client.get(f"/api/video-tasks/{job_id}", headers=grantee_b)
                assert completed.status_code == 200, completed.text
                if completed.json()["status"] in {"succeeded", "failed", "interrupted"}:
                    break
                time.sleep(0.05)
            assert completed is not None and completed.json()["status"] == "succeeded", (
                completed.text if completed else "视频任务未在时限内完成"
            )
            assert provider_state["posts"] == 1
            assert provider_state["polls"] >= 1
            assert provider_state["downloads"] == 1
            for unauthorized_actor in (owner_a, actor_c):
                hidden = client.get(f"/api/video-tasks/{job_id}", headers=unauthorized_actor)
                assert hidden.status_code == 404, hidden.text

            # 普通HTTP新建已自动绑定A；治理主体B不得通过claim夺取该ACL。
            created = client.post(
                "/api/asset-registry/projects",
                headers=owner_a,
                json={"name": "Phase12 new project no takeover", "project_type": "film"},
            )
            assert created.status_code == 201, created.text
            new_project_id = created.json()["project"]["project_id"]
            with video.store.reader() as db:
                before = dict(db.execute(
                    "SELECT * FROM video_projects WHERE project_id=?", (new_project_id,)
                ).fetchone())
            takeover = client.post(
                f"/api/video-project-access/{new_project_id}/claim", headers=grantee_b_governor
            )
            assert takeover.status_code == 409, takeover.text
            with video.store.reader() as db:
                after = dict(db.execute(
                    "SELECT * FROM video_projects WHERE project_id=?", (new_project_id,)
                ).fetchone())
            assert after == before
            assert before["owner_key"] == before["source_owner_key"] == source_owner_key
    finally:
        video.executor.shutdown(wait=True, cancel_futures=True)
        server.shutdown()
        server.server_close()
        provider_thread.join(timeout=5)


def test_video_task_cancel_http_success_without_upstream_dispatch():
    """受控 queued job 直接落库后由 HTTP 取消；测试不触发真实或收费 Provider。"""
    from gw.core.auth import require_edit_access

    with _client() as client:
        project_id, _ = _create_project(client, "Phase12 video cancel project")
        context = require_edit_access(EDITOR["Authorization"], EDITOR["X-User-Role"])
        service = get_video_service()
        actor = service.actor(context)
        row, created = service.store.create_job(
            actor=actor,
            operation="video-generation",
            key="phase12-owned-queued-cancel",
            request={"fixture": "queued-cancel"},
            project_id=project_id,
            canvas_id="phase12-canvas-fixture",
            entity_id="phase12-entity-fixture",
            provider_id="not-dispatched",
            model="not-dispatched",
        )
        assert created is True
        job_id = row["job_id"]

        cancelled = client.post(f"/api/video-tasks/{job_id}/cancel", headers=EDITOR)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json() == {
            "job_id": job_id,
            "status": "canceled",
            "remote_cancelled": False,
            "remote_may_continue_or_bill": False,
        }
        persisted = client.get(f"/api/video-tasks/{job_id}", headers=EDITOR)
        assert persisted.status_code == 200, persisted.text
        assert persisted.json()["status"] == "canceled"