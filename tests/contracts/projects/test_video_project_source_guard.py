"""视频任务恢复与素材授权必须绑定项目持久真源。"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.api import routes_asset_registry, routes_projects
from gw.core.auth import AuthContext, require_edit_access
from gw.projects_hub import service as projects_service_module
from gw.projects_hub.models import ProjectCreateRequest, ProjectType
from gw.projects_hub.service import ProjectsService, owner_key_for_context
from gw.video_tasks import service as video_service_module
from gw.video_tasks.service import VideoTaskService
from gw.video_tasks.store import VideoTaskStore

AUTH = {"Authorization": "Bearer recovery-owner", "X-User-Role": "editor"}


class _ReviewProvider(BaseHTTPRequestHandler):
    state: dict[str, int] = {}
    base_url = ""

    def log_message(self, *_args):
        return

    def _json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.state["posts"] += 1
        self._json({"task_id": "review-upstream-1"})

    def do_GET(self):
        if self.path == "/artifact":
            self.state["downloads"] += 1
            body = b"not-a-video"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.state["polls"] += 1
        self._json({"status": "completed", "url": self.base_url + "/artifact"})


def _provider_server():
    state = {"posts": 0, "polls": 0, "downloads": 0}
    handler = type("ReviewProvider", (_ReviewProvider,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    handler.base_url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, handler.base_url, state


def _persistent_http_service(tmp_path, monkeypatch, provider_url):
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_PROVIDER_REVIEW_KEY", "local-review-fixture")
    monkeypatch.setenv(
        "GW_PROVIDER_RUNTIME_JSON",
        json.dumps({
            "review": {
                "base_url": provider_url,
                "api_key_env": "GW_PROVIDER_REVIEW_KEY",
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
    monkeypatch.setattr(video_service_module, "_default_service", None)
    return projects


def _create_http_video_job(tmp_path, monkeypatch, provider_url, *, with_upstream=False):
    _persistent_http_service(tmp_path, monkeypatch, provider_url)
    with TestClient(create_app()) as client:
        project_response = client.post(
            "/api/asset-registry/projects",
            headers=AUTH,
            json={"name": "视频恢复真源审核", "project_type": "film"},
        )
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["project"]["project_id"]
        canvas_response = client.post(
            "/api/canvases",
            headers=AUTH,
            json={"project_id": project_id, "title": "审核画布", "mode": "classic"},
        )
        assert canvas_response.status_code == 201, canvas_response.text
        canvas = canvas_response.json()["canvas"]
        entity_id = "review-output"
        topology = client.patch(
            "/api/canvases/" + canvas["canvas_id"],
            headers=AUTH,
            json={
                "expected_version": canvas["version"],
                "nodes": [{"entity_id": entity_id, "kind": "output"}],
                "connections": [],
            },
        )
        assert topology.status_code == 200, topology.text

        video = video_service_module.get_video_service()
        video.schedule = lambda _job_id: None
        accepted = client.post(
            "/api/video-tasks",
            headers={**AUTH, "Idempotency-Key": "review-recovery-01"},
            json={
                "prompt": "本地回环审核夹具",
                "provider_id": "review",
                "model": "model",
                "duration": 1,
                "aspect_ratio": "16:9",
                "production_context": {
                    "project_id": project_id,
                    "canvas_id": canvas["canvas_id"],
                    "entity_id": entity_id,
                },
            },
        )
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        if with_upstream:
            running = video.store.mark_running(job_id)
            assert running is not None
            assert video.store.begin_remote_create(job_id)
            assert video.store.set_upstream_task(job_id, "review-upstream-1")
    return project_id, job_id


@pytest.mark.parametrize(
    ("job_kind", "source_damage"),
    [
        ("queued", "missing"),
        ("known-upstream", "corrupt"),
        ("queued", "owner-mismatch"),
    ],
)
def test_new_process_quarantines_recovery_when_project_source_cannot_be_trusted(
    tmp_path, monkeypatch, job_kind, source_damage
):
    """全新进程恢复前校验项目真源与owner；不得POST、轮询、下载或发布产物。"""
    server, thread, provider_url, provider_state = _provider_server()
    try:
        project_id, job_id = _create_http_video_job(
            tmp_path, monkeypatch, provider_url, with_upstream=job_kind == "known-upstream"
        )
        data_root = tmp_path / "data"
        snapshot = data_root / "projects.json"
        if source_damage == "missing":
            snapshot.rename(data_root / "projects.saved.json")
        elif source_damage == "corrupt":
            snapshot.write_text("{损坏", encoding="utf-8")
        else:
            value = json.loads(snapshot.read_text(encoding="utf-8"))
            owner = value["owners"][project_id]
            value["owners"][project_id] = "e" * 64 if owner != "e" * 64 else "f" * 64
            snapshot.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

        repo_root = Path(__file__).resolve().parents[3]
        child_env = os.environ.copy()
        child_env["PYTHONPATH"] = str(repo_root)
        child_env["PYTHONIOENCODING"] = "utf-8"
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json; from gw.video_tasks.service import get_video_service; "
                f"s=get_video_service(); s.executor.shutdown(wait=True); j=s.store.get_by_id({job_id!r}); "
                "print(json.dumps({'status':j['status'],'error_code':j['error_code'],"
                "'upstream_create_dispatched':j['upstream_create_dispatched']}))",
            ],
            cwd=repo_root,
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        assert child.returncode == 0, child.stderr
        result = json.loads(child.stdout)
        assert result["status"] == "interrupted", result
        assert result["error_code"] in {
            "VIDEO_PROJECT_NOT_FOUND", "VIDEO_PROJECT_SOURCE_UNAVAILABLE"
        }
        assert provider_state == {"posts": 0, "polls": 0, "downloads": 0}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_authorize_asset_http_rejects_missing_project_snapshot_before_asset_resolution(tmp_path, monkeypatch):
    """HTTP授权入口先核对项目真源；孤儿视频ACL不能创建素材授权。"""
    _persistent_http_service(tmp_path, monkeypatch, "http://127.0.0.1:9")
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/asset-registry/projects",
            headers=AUTH,
            json={"name": "授权真源审核", "project_type": "film"},
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["project"]["project_id"]
        service = video_service_module.get_video_service()
        (tmp_path / "data" / "projects.json").unlink()

        response = client.post(
            f"/api/video-projects/{project_id}/assets/authorize",
            headers=AUTH,
            json={"asset_id": "ast-review"},
        )
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "VIDEO_PROJECT_SOURCE_UNAVAILABLE"
        with service.store.reader() as db:
            assert db.execute(
                "SELECT 1 FROM video_project_assets WHERE project_id=? AND asset_id=?",
                (project_id, "ast-review"),
            ).fetchone() is None


def test_local_export_recovery_is_quarantined_when_project_snapshot_is_missing(tmp_path, monkeypatch):
    """本地导出恢复同样需要项目真源；缺快照不得解析素材或发布产物。"""
    data_root = tmp_path / "data"
    monkeypatch.setenv("GW_DATA_DIR", str(data_root))
    projects = ProjectsService(seed_golden_fixture=False, persistent=True)
    monkeypatch.setattr(video_service_module, "default_projects_service", projects)
    context = AuthContext(role="editor", subject="export-owner", mode="local", identity_domain="local_dev")
    owner = VideoTaskService.actor(context)
    created = projects.create_project(
        ProjectCreateRequest(name="本地导出真源审核", project_type=ProjectType.FILM),
        owner_key=owner,
    )
    store = VideoTaskStore(tmp_path / "video")
    store.register_project_owner(created.project_id, owner, owner)
    store.authorize_asset(created.project_id, "ast-source", owner, owner)
    row, _ = store.create_job(
        actor=owner,
        operation="video-export",
        key="export-source-guard",
        request={"asset_ids": ["ast-source"], "preset": "h264_720p_30fps"},
        project_id=created.project_id,
        canvas_id="cv-review",
        entity_id="ent-review",
    )
    (data_root / "projects.json").unlink()

    service = VideoTaskService(store)
    try:
        blocked = store.get_by_id(row["job_id"])
        assert blocked["status"] == "interrupted"
        assert blocked["error_code"] == "VIDEO_PROJECT_SOURCE_UNAVAILABLE"
        assert list(store.artifact_root.glob("*.mp4")) == []
        with store.reader() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM video_artifacts WHERE job_id=?", (row["job_id"],)
            ).fetchone()[0] == 0
    finally:
        service.executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_http_claim_fails_closed_without_trusted_project_snapshot(tmp_path, monkeypatch, damage):
    """项目真源缺失或损坏时，真实claim路由不得新建/修改视频ACL。"""
    _persistent_http_service(tmp_path, monkeypatch, "http://127.0.0.1:9")
    projects = video_service_module.default_projects_service
    owner_context = require_edit_access(AUTH["Authorization"], AUTH["X-User-Role"])
    project = projects.create_project(
        ProjectCreateRequest(name="claim真源损坏回归", project_type=ProjectType.FILM),
        owner_key=owner_key_for_context(owner_context),
    )
    governor = {"Authorization": "Bearer claim-governor", "X-User-Role": "governor"}
    with TestClient(create_app()) as client:
        missing = client.post(
            "/api/video-project-access/prj-absent-from-truth/claim", headers=governor
        )
        assert missing.status_code == 404, missing.text

    data_root = tmp_path / "data"
    snapshot = data_root / "projects.json"
    if damage == "missing":
        snapshot.rename(data_root / "projects.saved.json")
    else:
        snapshot.write_text("{损坏", encoding="utf-8")

    with TestClient(create_app()) as client:
        blocked = client.post(
            f"/api/video-project-access/{project.project_id}/claim", headers=governor
        )
        assert blocked.status_code == 503, blocked.text
        assert blocked.json()["detail"]["code"] == "VIDEO_PROJECT_SOURCE_UNAVAILABLE"

    service = video_service_module.get_video_service()
    with service.store.reader() as db:
        assert db.execute("SELECT COUNT(*) FROM video_projects").fetchone()[0] == 0

@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_original_actor_can_observe_and_cancel_during_project_source_outage(tmp_path, monkeypatch, damage):
    """真实回环轮询在途时，源故障不切断任务止损；其他主体和产物仍隔离。"""
    entered, release = threading.Event(), threading.Event()

    def held_poll(self):
        self.state["polls"] += 1
        entered.set()
        assert release.wait(15)
        self._json({"status": "running"})

    monkeypatch.setattr(_ReviewProvider, "do_GET", held_poll)
    server, provider_thread, provider_url, provider_state = _provider_server()
    worker = None
    snapshot = tmp_path / "data" / "projects.json"
    saved = None
    try:
        _, job_id = _create_http_video_job(tmp_path, monkeypatch, provider_url)
        service = video_service_module.get_video_service()
        worker = threading.Thread(target=service._run_job, args=(job_id,), daemon=True)
        worker.start()
        assert entered.wait(10), service.store.get_by_id(job_id)
        assert provider_state["posts"] == 1
        saved = snapshot.read_bytes()
        if damage == "missing":
            snapshot.rename(snapshot.with_suffix(".saved"))
        else:
            snapshot.write_text("{损坏", encoding="utf-8")
        outsider = {"Authorization": "Bearer independent-outside-actor", "X-User-Role": "editor"}
        with TestClient(create_app()) as client:
            detail = client.get(f"/api/video-tasks/{job_id}", headers=AUTH)
            assert detail.status_code == 200, detail.text
            assert detail.json()["status"] == "running"
            assert detail.json()["remote_may_continue_or_bill"] is True
            listed = client.get("/api/video-tasks", headers=AUTH)
            assert listed.status_code == 200, listed.text
            assert job_id in {row["job_id"] for row in listed.json()["items"]}
            assert client.get(f"/api/video-tasks/{job_id}", headers=outsider).status_code == 404
            assert client.post(f"/api/video-tasks/{job_id}/cancel", headers=outsider).status_code == 404
            assert client.get("/api/video-tasks", headers=outsider).json()["items"] == []
            assert client.post(f"/api/video-tasks/{job_id}/cancel", headers={**AUTH, "X-User-Role": "readonly"}).status_code == 403
            media = client.get(f"/api/video-tasks/{job_id}/content", headers=AUTH)
            assert media.status_code == 503
            assert media.json()["detail"]["code"] == "VIDEO_PROJECT_SOURCE_UNAVAILABLE"
            cancelled = client.post(f"/api/video-tasks/{job_id}/cancel", headers=AUTH)
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["status"] == "canceled"
            assert cancelled.json()["remote_may_continue_or_bill"] is True
            release.set()
            worker.join(10)
            assert not worker.is_alive(), "取消后不应继续轮询"
            assert provider_state == {"posts": 1, "polls": 1, "downloads": 0}
            final = client.get(f"/api/video-tasks/{job_id}", headers=AUTH)
            assert final.status_code == 200 and final.json()["status"] == "canceled"
            assert service.store.get_by_id(job_id).get("artifact") is None
    finally:
        release.set()
        if worker:
            worker.join(10)
        if saved is not None:
            if damage == "missing":
                snapshot.with_suffix(".saved").rename(snapshot)
            else:
                snapshot.write_bytes(saved)
        server.shutdown()
        server.server_close()
        provider_thread.join(5)
