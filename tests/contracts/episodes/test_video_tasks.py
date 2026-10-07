"""视频任务的契约、恢复、安全边界与本地媒体路径回归。"""

import json
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core.auth import AuthContext
from gw.core.errors import CleanroomException
from gw.video_tasks.service import VideoTaskService, download_provider_artifact
from gw.video_tasks.store import VideoTaskStore


PROJECT_ID = "prj-video-test"
CONTEXT = {"project_id": PROJECT_ID, "canvas_id": "cv-video-test", "entity_id": "ent-video-test"}


def _auth(name="alice"):
    return AuthContext(role="editor", subject=f"local:{name}", mode="local", identity_domain="local_dev")


def _payload(prompt="单镜测试"):
    return {
        "prompt": prompt,
        "provider_id": "video-test",
        "model": "video-test-model",
        "duration": 1,
        "aspect_ratio": "16:9",
        "production_context": dict(CONTEXT),
    }


def _service(tmp_path, context=None, *, allow_private=False, monkeypatch):
    context = context or _auth()
    store = VideoTaskStore(tmp_path / "video")
    # 这些单元用例使用合成项目ID；持久项目真源由专项集成回归覆盖。
    from gw.video_tasks import service as video_service_module
    service = VideoTaskService(store, allow_private_artifacts_for_test=allow_private)
    service._validate_context = lambda *_args: None
    owner = service.actor(context)
    monkeypatch.setattr(video_service_module.default_projects_service, "_get_source_owner_key", lambda *_args: owner)
    store.register_project_owner(PROJECT_ID, owner, owner)
    return service, context


def _runtime(monkeypatch, base_url):
    monkeypatch.setenv("GW_VIDEO_TEST_KEY", "test-secret-do-not-log")
    monkeypatch.setenv("GW_PROVIDER_RUNTIME_JSON", json.dumps({
        "video-test": {
            "base_url": base_url,
            "api_key_env": "GW_VIDEO_TEST_KEY",
            "models": ["video-test-model"],
            "default_model": "video-test-model",
            "video_protocol": "newapi_video",
        }
    }))


def _make_clip(path, *, size="64x64", rate=10, duration="0.5"):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("本机未安装 ffmpeg，无法验证真实本地媒体导出")
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
               "-i", f"color=c=blue:s={size}:r={rate}:d={duration}", "-an", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
    result = subprocess.run(command, capture_output=True, timeout=45, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert path.is_file() and path.stat().st_size > 0
    return path


def _provider_server(video_path, mode="success"):
    state = {"mode": mode, "posts": 0, "polls": 0, "authorization": None, "body": None,
             "artifact_downloads": 0, "post_received": threading.Event(), "release_post": threading.Event(),
             "poll_received": threading.Event(), "release_poll": threading.Event()}
    video_bytes = video_path.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _json(self, value, status=200):
            body = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/v1/video/generations":
                self._json({"error": "not found"}, 404)
                return
            state["posts"] += 1
            state["authorization"] = self.headers.get("Authorization")
            length = int(self.headers.get("Content-Length") or 0)
            state["body"] = json.loads(self.rfile.read(length) or b"{}")
            state["post_received"].set()
            if state["mode"] == "delayed":
                if not state["release_post"].wait(10):
                    self._json({"error": "test response timeout"}, 504)
                    return
                self._json({"task_id": "upstream-delayed-1"})
                return
            if state["mode"] == "unknown":
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return
            self._json({"task_id": "upstream-test-1"})

        def do_GET(self):
            if self.path == "/v1/video/generations/upstream-test-1":
                state["polls"] += 1
                if state["mode"] == "hold_poll":
                    state["poll_received"].set()
                    state["release_poll"].wait(10)
                self._json({"status": "completed", "url": state["artifact_url"]})
                return
            if self.path == "/v1/video/generations/upstream-delayed-1":
                state["polls"] += 1
                state["poll_received"].set()
                state["release_poll"].wait(10)
                self._json({"status": "running"})
                return
            if self.path == "/artifact.mp4":
                state["artifact_downloads"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(video_bytes)))
                self.end_headers()
                self.wfile.write(video_bytes)
                return
            self._json({"error": "not found"}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    state["artifact_url"] = base_url + "/artifact.mp4"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, base_url, state


def _wait_terminal(service, context, job_id, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = service.get_job(job_id, context)
        if job["status"] in {"succeeded", "failed", "canceled", "interrupted"}:
            return job
        time.sleep(0.05)
    pytest.fail("视频任务未在测试时限内进入终态")


def test_video_routes_are_mounted_once_and_have_frozen_paths():
    """应用只注册一次视频端点，嵌套 closure 路由不产生重复 operation。"""
    app = create_app()
    def flattened(routes):
        for route in routes:
            yield route
            nested = getattr(route, "original_router", None)
            if nested is not None:
                yield from flattened(nested.routes)

    routes = [route for route in flattened(app.routes) if getattr(route, "path", "") in {
        "/api/video-tasks", "/api/video-tasks/{job_id}", "/api/video-tasks/{job_id}/cancel",
        "/api/video-tasks/{job_id}/content", "/api/video-exports",
    }]
    seen = set()
    for route in routes:
        for method in getattr(route, "methods", set()) or set():
            key = (method, route.path)
            assert key not in seen, f"视频路由重复注册：{key}"
            seen.add(key)
    assert {("POST", "/api/video-tasks"), ("GET", "/api/video-tasks"),
            ("GET", "/api/video-tasks/{job_id}"), ("POST", "/api/video-tasks/{job_id}/cancel"),
            ("GET", "/api/video-tasks/{job_id}/content"), ("POST", "/api/video-exports")} <= seen
    assert "/api/video-tasks" in app.openapi()["paths"]


def test_video_content_is_inline_for_preview_and_attachment_for_download(monkeypatch, tmp_path):
    """受保护媒体端点默认允许 video 播放器预览，下载参数才请求附件响应。"""
    from gw.video_tasks import routes as video_routes

    artifact_path = tmp_path / "verified-artifact.mp4"
    artifact_path.write_bytes(b"verified-test-content")

    class ArtifactService:
        def open_artifact(self, job_id, context):
            assert job_id == "job-content"
            assert context.subject == "local:alice"
            return artifact_path, {"asset_id": "ast-video-123"}

    monkeypatch.setattr(video_routes, "get_video_service", lambda: ArtifactService())
    monkeypatch.setattr(video_routes, "_read_context", lambda *_args: _auth())

    client = TestClient(create_app())
    preview = client.get("/api/video-tasks/job-content/content")
    download = client.get("/api/video-tasks/job-content/content?download=1")

    assert preview.status_code == 200
    assert preview.headers["content-type"] == "video/mp4"
    assert preview.headers["content-disposition"] == 'inline; filename="ast-video-123.mp4"'
    assert preview.headers["cache-control"] == "private, no-store"
    assert preview.content == b"verified-test-content"
    assert download.status_code == 200
    assert download.headers["content-disposition"] == 'attachment; filename="ast-video-123.mp4"'
    assert download.content == b"verified-test-content"


def test_create_generation_idempotency_and_conflict(monkeypatch, tmp_path):
    """同一主体同键同参复用 job_id，同键异参返回标准冲突。"""
    _runtime(monkeypatch, "https://video.example.invalid")
    service, context = _service(tmp_path, monkeypatch=monkeypatch)
    scheduled = []
    service.schedule = scheduled.append
    try:
        first = service.create_generation(_payload(), "stable-key-001", context)
        retry = service.create_generation(_payload(), "stable-key-001", context)
        assert retry["job_id"] == first["job_id"]
        assert first["status"] == "queued"
        assert scheduled == [first["job_id"]]
        with pytest.raises(CleanroomException) as excinfo:
            service.create_generation(_payload("不同请求"), "stable-key-001", context)
        assert excinfo.value.status_code == 409
        assert excinfo.value.code == "IDEMPOTENCY_CONFLICT"
        assert "test-secret-do-not-log" not in json.dumps(first)
    finally:
        service.executor.shutdown(wait=True, cancel_futures=True)


def test_newapi_create_poll_download_and_protected_artifact(tmp_path, monkeypatch):
    """loopback New API 模拟真实创建、轮询、下载与 ffprobe 校验，不调用商业 Provider。"""
    artifact = _make_clip(tmp_path / "source.mp4", size="96x64", rate=12, duration="0.7")
    server, thread, base_url, state = _provider_server(artifact)
    _runtime(monkeypatch, base_url)
    service, context = _service(tmp_path, allow_private=True, monkeypatch=monkeypatch)
    try:
        accepted = service.create_generation(_payload(), "loopback-key", context)
        finished = _wait_terminal(service, context, accepted["job_id"])
        assert finished["status"] == "succeeded", finished
        assert state["posts"] == 1 and state["polls"] >= 1
        assert state["authorization"] == "Bearer test-secret-do-not-log"
        assert state["body"]["metadata"] == CONTEXT
        assert "test-secret-do-not-log" not in json.dumps(finished)
        path, registered = service.open_artifact(accepted["job_id"], context)
        assert path.parent == service.store.artifact_root
        assert path.is_file() and path.stat().st_size == artifact.stat().st_size
        assert registered["asset_id"].startswith("ast-video-")
        assert finished["content_url"] == f"/api/video-tasks/{accepted['job_id']}/content"
        assert finished["media"]["width"] == 96
        assert finished["media"]["height"] == 64
        assert finished["media"]["video_codec"] == "h264"
        with pytest.raises(CleanroomException) as denied:
            service.open_artifact(accepted["job_id"], _auth("mallory"))
        assert denied.value.status_code == 404
    finally:
        service.executor.shutdown(wait=True, cancel_futures=True)
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_unknown_provider_create_is_interrupted_and_never_reposted(tmp_path, monkeypatch):
    """Provider 收到 POST 后断开连接时保留结果未知，重启恢复不得再次创建。"""
    artifact = _make_clip(tmp_path / "source.mp4")
    server, thread, base_url, state = _provider_server(artifact, mode="unknown")
    _runtime(monkeypatch, base_url)
    service, context = _service(tmp_path, monkeypatch=monkeypatch)
    root = service.store.root
    try:
        accepted = service.create_generation(_payload(), "unknown-key", context)
        finished = _wait_terminal(service, context, accepted["job_id"])
        assert finished["status"] == "interrupted"
        assert finished["result_unknown"] is True
        assert state["posts"] == 1
        recovered_store = VideoTaskStore(root)
        assert recovered_store.recoverable() == []
        recovered = VideoTaskService(recovered_store)
        try:
            time.sleep(0.2)
            assert state["posts"] == 1
            snapshot = recovered_store.get_by_id(accepted["job_id"])
            assert snapshot["status"] == "interrupted"
            assert snapshot["upstream_task_id"] is None
        finally:
            recovered.executor.shutdown(wait=True, cancel_futures=True)
    finally:
        service.executor.shutdown(wait=True, cancel_futures=True)
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_recovery_resumes_only_known_safe_work(tmp_path):
    """重启只恢复 queued 和已知上游引用；不确定创建及本地渲染转中断。"""
    store = VideoTaskStore(tmp_path / "video")
    actor = VideoTaskService.actor(_auth())
    store.register_project_owner(PROJECT_ID, actor, actor)
    queued, _ = store.create_job(actor=actor, operation="video-generation", key="q", request=_payload(),
                                  project_id=PROJECT_ID, canvas_id=CONTEXT["canvas_id"], entity_id=CONTEXT["entity_id"],
                                  provider_id="video-test", model="video-test-model")
    known, _ = store.create_job(actor=actor, operation="video-generation", key="known", request=_payload(),
                                 project_id=PROJECT_ID, canvas_id=CONTEXT["canvas_id"], entity_id=CONTEXT["entity_id"],
                                 provider_id="video-test", model="video-test-model")
    unknown, _ = store.create_job(actor=actor, operation="video-generation", key="unknown", request=_payload(),
                                  project_id=PROJECT_ID, canvas_id=CONTEXT["canvas_id"], entity_id=CONTEXT["entity_id"],
                                  provider_id="video-test", model="video-test-model")
    local, _ = store.create_job(actor=actor, operation="video-export", key="local", request={"asset_ids": ["ast-1"]},
                                project_id=PROJECT_ID, canvas_id=CONTEXT["canvas_id"], entity_id=CONTEXT["entity_id"])
    store.mark_running(known["job_id"])
    assert store.begin_remote_create(known["job_id"])
    assert store.set_upstream_task(known["job_id"], "upstream-known")
    store.mark_running(unknown["job_id"])
    assert store.begin_remote_create(unknown["job_id"])
    never_dispatched, _ = store.create_job(actor=actor, operation="video-generation", key="never-dispatched", request=_payload(),
        project_id=PROJECT_ID, canvas_id=CONTEXT["canvas_id"], entity_id=CONTEXT["entity_id"],
        provider_id="video-test", model="video-test-model")
    store.mark_running(never_dispatched["job_id"])
    store.mark_running(local["job_id"])
    restarted = VideoTaskStore(store.root)
    recoverable = restarted.recoverable()
    assert {item["job_id"] for item in recoverable} == {queued["job_id"], known["job_id"], never_dispatched["job_id"]}
    assert restarted.get_by_id(unknown["job_id"])["status"] == "interrupted"
    assert restarted.get_by_id(unknown["job_id"])["result_unknown"] == 1
    assert restarted.get_by_id(unknown["job_id"])["remote_may_continue"] == 1
    assert restarted.get_by_id(unknown["job_id"])["upstream_create_dispatched"] == 1
    assert restarted.get_by_id(never_dispatched["job_id"])["status"] == "queued"
    assert restarted.get_by_id(never_dispatched["job_id"])["upstream_create_dispatched"] == 0
    assert restarted.get_by_id(local["job_id"])["status"] == "interrupted"
    assert restarted.get_by_id(local["job_id"])["result_unknown"] == 0




def test_cancel_during_remote_create_keeps_risk_and_persists_late_task_id(tmp_path, monkeypatch):
    """POST 已到回环 Provider 后取消必须报计费风险；迟到引用跨重启保留且不再轮询。"""
    artifact = _make_clip(tmp_path / "source.mp4")
    server, thread, base_url, state = _provider_server(artifact, mode="delayed")
    _runtime(monkeypatch, base_url)
    service, context = _service(tmp_path, monkeypatch=monkeypatch)
    root = service.store.root
    try:
        accepted = service.create_generation(_payload(), "cancel-inflight", context)
        assert state["post_received"].wait(5), "回环 Provider 未收到创建 POST"
        canceled = service.cancel(accepted["job_id"], context)
        assert canceled["status"] == "canceled"
        assert canceled["remote_may_continue_or_bill"] is True
        assert service.store.get_by_id(accepted["job_id"])["upstream_task_id"] is None

        state["release_post"].set()
        deadline = time.monotonic() + 5
        while accepted["job_id"] in service._scheduled and time.monotonic() < deadline:
            time.sleep(0.02)
        snapshot = service.store.get_by_id(accepted["job_id"])
        assert snapshot["status"] == "canceled"
        assert snapshot["upstream_task_id"] == "upstream-delayed-1"
        assert snapshot["remote_may_continue"] == 1
        assert state["polls"] == 0, "取消后迟到 task_id 只能追踪，不得继续轮询或发布"

        restarted_store = VideoTaskStore(root)
        restarted = restarted_store.get_by_id(accepted["job_id"])
        assert restarted["status"] == "canceled"
        assert restarted["upstream_task_id"] == "upstream-delayed-1"
        assert restarted["upstream_create_dispatched"] == 1
        assert restarted["remote_may_continue"] == 1
        recovered_service = VideoTaskService(restarted_store, allow_private_artifacts_for_test=True)
        try:
            time.sleep(0.1)
            assert state["posts"] == 1
            assert state["polls"] == 0
        finally:
            recovered_service.executor.shutdown(wait=True, cancel_futures=True)
    finally:
        state["release_post"].set()
        service.executor.shutdown(wait=True, cancel_futures=True)
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_queued_cancel_prevents_remote_create_dispatch(tmp_path, monkeypatch):
    """排队时取消先提交则任务不得越过派发屏障调用 Provider。"""
    artifact = _make_clip(tmp_path / "source.mp4")
    server, thread, base_url, state = _provider_server(artifact)
    _runtime(monkeypatch, base_url)
    service, context = _service(tmp_path, monkeypatch=monkeypatch)
    service.schedule = lambda _job_id: None
    try:
        accepted = service.create_generation(_payload(), "cancel-queued", context)
        canceled = service.cancel(accepted["job_id"], context)
        assert canceled["status"] == "canceled"
        assert canceled["remote_may_continue_or_bill"] is False
        service._run_job(accepted["job_id"])
        snapshot = service.store.get_by_id(accepted["job_id"])
        assert snapshot["status"] == "canceled"
        assert snapshot["upstream_create_dispatched"] == 0
        assert state["posts"] == 0
    finally:
        service.executor.shutdown(wait=True, cancel_futures=True)
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_complete_success_preserves_preexisting_target_bytes(tmp_path):
    """冲突拒绝发布时，原 MP4 字节与哈希保持不变，只有本次临时文件留待核验。"""
    import hashlib

    store = VideoTaskStore(tmp_path / "video")
    actor = VideoTaskService.actor(_auth())
    store.register_project_owner(PROJECT_ID, actor, actor)
    row, _ = store.create_job(actor=actor, operation="video-export", key="collision",
        request={"asset_ids": ["ast-1"]}, project_id=PROJECT_ID,
        canvas_id=CONTEXT["canvas_id"], entity_id=CONTEXT["entity_id"])
    store.mark_running(row["job_id"])
    final_path = store.artifact_root / f"{row['job_id']}.mp4"
    existing_bytes = b"pre-existing-mp4-evidence-do-not-delete"
    final_path.write_bytes(existing_bytes)
    before = hashlib.sha256(final_path.read_bytes()).hexdigest()
    temporary = store.work_root / "new-export.part.mp4"
    temporary.write_bytes(b"new-export-not-published")

    with pytest.raises(CleanroomException) as excinfo:
        store.complete_success(row["job_id"], temporary, {"width": 1})

    after = hashlib.sha256(final_path.read_bytes()).hexdigest()
    assert excinfo.value.status_code == 503
    assert after == before
    assert final_path.read_bytes() == existing_bytes
    assert temporary.read_bytes() == b"new-export-not-published"
    assert store.get_by_id(row["job_id"])["status"] == "running"
    evidence = {"job_id": row["job_id"], "existing_sha256_before": before,
        "existing_sha256_after": after, "existing_bytes_preserved": True,
        "new_temp_preserved": temporary.is_file(), "target": str(final_path)}
    (tmp_path / "publish-retention-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


def test_complete_success_removes_only_this_call_file_after_database_failure(tmp_path):
    """本次独占发布后登记失败，只补偿删除本次文件，不污染任务状态。"""
    store = VideoTaskStore(tmp_path / "video")
    actor = VideoTaskService.actor(_auth())
    store.register_project_owner(PROJECT_ID, actor, actor)
    row, _ = store.create_job(actor=actor, operation="video-export", key="db-failure",
        request={"asset_ids": ["ast-1"]}, project_id=PROJECT_ID,
        canvas_id=CONTEXT["canvas_id"], entity_id=CONTEXT["entity_id"])
    store.mark_running(row["job_id"])
    temporary = store.work_root / "database-failure.part.mp4"
    temporary.write_bytes(b"newly-published-mp4")
    final_path = store.artifact_root / f"{row['job_id']}.mp4"
    with store.transaction() as db:
        db.execute("""CREATE TRIGGER fail_video_artifact_insert BEFORE INSERT ON video_artifacts
            BEGIN SELECT RAISE(ABORT, 'forced video artifact registration failure'); END""")

    with pytest.raises(CleanroomException) as excinfo:
        store.complete_success(row["job_id"], temporary, {"width": 1})

    assert excinfo.value.code == "VIDEO_STORAGE_UNAVAILABLE"
    assert not final_path.exists()
    assert not temporary.exists()
    assert store.get_by_id(row["job_id"])["status"] == "running"


def test_cancel_wins_race_with_artifact_publication(tmp_path, monkeypatch):
    """取消事务先落终态时，迟到本地结果不会发布或注册成资产。"""
    service, context = _service(tmp_path, monkeypatch=monkeypatch)
    actor = service.actor(context)
    try:
        row, _ = service.store.create_job(actor=actor, operation="video-export", key="cancel-race",
            request={"asset_ids": ["ast-1"]}, project_id=PROJECT_ID,
            canvas_id=CONTEXT["canvas_id"], entity_id=CONTEXT["entity_id"])
        running = service.store.mark_running(row["job_id"])
        temporary = service.store.work_root / "late-result.mp4"
        temporary.write_bytes(b"not-published")
        canceled = service.cancel(row["job_id"], context)
        assert canceled["status"] == "canceled"
        assert service.store.complete_success(running["job_id"], temporary, {"width": 1}) is None
        with service.store.reader() as db:
            assert db.execute("SELECT COUNT(*) FROM video_artifacts WHERE job_id=?", (row["job_id"],)).fetchone()[0] == 0
        with pytest.raises(CleanroomException) as excinfo:
            service.open_artifact(row["job_id"], context)
        assert excinfo.value.status_code == 409
    finally:
        service.executor.shutdown(wait=True, cancel_futures=True)


def test_provider_artifact_download_blocks_loopback_ssrf(tmp_path):
    """Provider 返回的 HTTPS loopback 地址在每次下载前都被拒绝。"""
    destination = tmp_path / "blocked.mp4"
    with pytest.raises(CleanroomException) as excinfo:
        download_provider_artifact("https://127.0.0.1/private.mp4", destination)
    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "VIDEO_DOWNLOAD_BLOCKED"
    assert not destination.exists()


def test_local_ffmpeg_export_normalizes_mixed_inputs_and_registers_verified_output(tmp_path, monkeypatch):
    """本机 FFmpeg 真实合并不同尺寸帧率输入，并由 ffprobe 验证受保护产物。"""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("本机未安装 ffmpeg/ffprobe，无法验证真实导出")
    first = _make_clip(tmp_path / "first.mp4", size="64x64", rate=10, duration="0.6")
    second = _make_clip(tmp_path / "second.mp4", size="96x64", rate=12, duration="0.6")
    service, context = _service(tmp_path, monkeypatch=monkeypatch)
    actor = service.actor(context)
    asset_paths = {"asset-1": first, "asset-2": second}
    service._authorized_asset_path = lambda asset_id, _project_id, _actor: asset_paths[asset_id]
    request = {"asset_ids": ["asset-1", "asset-2"], "project_id": PROJECT_ID,
               "canvas_id": CONTEXT["canvas_id"], "entity_id": CONTEXT["entity_id"],
               "preset": "h264_720p_30fps"}
    service.schedule = lambda _job_id: None
    try:
        accepted = service.create_export(request, "real-ffmpeg-export", context)
        running = service.store.mark_running(accepted["job_id"])
        service._run_export(running, request)
        job = service.get_job(accepted["job_id"], context)
        assert job["status"] == "succeeded", job
        assert job["media"]["width"] == 1280 and job["media"]["height"] == 720
        assert job["media"]["video_codec"] == "h264"
        path, _artifact = service.open_artifact(accepted["job_id"], context)
        assert path.is_file() and path.stat().st_size > 0
    finally:
        service.executor.shutdown(wait=True, cancel_futures=True)


def test_cancel_after_task_id_stops_late_completed_poll_and_download(tmp_path, monkeypatch):
    """上游已返回ID且轮询在途时取消；迟到完成响应不得触发下载或发布。"""
    artifact = _make_clip(tmp_path / "source.mp4")
    server, thread, base_url, state = _provider_server(artifact, mode="hold_poll")
    _runtime(monkeypatch, base_url)
    service, context = _service(tmp_path, allow_private=True, monkeypatch=monkeypatch)
    try:
        accepted = service.create_generation(_payload(), "cancel-known-upstream", context)
        assert state["poll_received"].wait(5)
        before = service.store.get_by_id(accepted["job_id"])
        assert before["upstream_task_id"] == "upstream-test-1"
        canceled = service.cancel(accepted["job_id"], context)
        assert canceled["status"] == "canceled" and canceled["remote_may_continue_or_bill"] is True
        state["release_poll"].set()
        deadline = time.monotonic() + 5
        while accepted["job_id"] in service._scheduled and time.monotonic() < deadline:
            time.sleep(.02)
        assert state["posts"] == 1 and state["polls"] == 1
        assert state["artifact_downloads"] == 0
        final = service.store.get_by_id(accepted["job_id"])
        assert final["status"] == "canceled" and final["upstream_task_id"] == "upstream-test-1"
    finally:
        state["release_poll"].set()
        service.executor.shutdown(wait=True, cancel_futures=True)
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
