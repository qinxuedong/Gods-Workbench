# -*- coding: utf-8 -*-
"""QIN-219 / C14-T4: ten-shot I2V and multi-take browser closeout.

The real-browser run is opt-in through ``GW_C14_REAL_E2E=1``. It uses the
actual FastAPI routes, a test-only mounted C14 video-job router, and a
deterministic provider fixture. It never contacts an external model or claims
that fixture job descriptors are generated MP4 media.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import pytest
import uvicorn

from gw.api.routes_workflow_jobs import create_video_jobs_router
from gw.god_workflow.video_executor import VideoJobExecutor
from playwright_support import open_playwright


REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPO_ROOT / ".local" / "qa" / "C14-evidence"
REAL_RUN_ENV = "GW_C14_REAL_E2E"
FIXTURE_ID = "c14-t4-i2v-e2e-v001"
SHOT_DURATIONS = (3.6, 4.2, 5.1, 2.5, 6.0, 4.8, 3.0, 5.4, 4.0, 3.3)
SHOT_COUNT = 10
TAKE_VERSIONS = (1, 2, 3)


def _real_run_enabled(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(REAL_RUN_ENV, "")).strip().lower() in {"1", "true", "yes"}


def _evidence_dir() -> Path:
    configured = str(os.environ.get("GW_C14_EVIDENCE_DIR", "")).strip()
    path = Path(configured).expanduser() if configured else EVIDENCE_ROOT
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _write_artifacts(evidence: dict[str, Any], api_evidence: dict[str, Any] | None = None) -> None:
    root = _evidence_dir()
    _write_json(root / "c14-t4-report.json", evidence)
    if api_evidence is not None:
        _write_json(root / "c14-t4-api.json", api_evidence)
    _write_json(
        root / "c14-t4-test-log.json",
        {
            "task": "QIN-219 / C14-T4",
            "fixture_id": FIXTURE_ID,
            "status": evidence.get("status", "not_run"),
            "assertions": evidence.get("assertions", []),
            "failure": evidence.get("failure"),
            "blocker": evidence.get("blocker"),
        },
    )
    summary = evidence.get("timeline_summary") or {}
    screenshots = evidence.get("screenshots") or []
    lines = [
        "# C14 控时长视频生成实测报告",
        "",
        f"- 任务：QIN-219 / C14-T4",
        f"- 状态：`{evidence.get('status', 'not_run')}`",
        f"- Commit：`{evidence.get('commit_sha', 'unknown')}`",
        f"- 浏览器：{(evidence.get('browser') or {}).get('engine', '未运行')}",
        f"- 生成作业：{evidence.get('job_count', 0)} 个确定性 provider fixture 作业；未调用外部模型，provider MP4 字节数=0；本地合成预览={evidence.get('preview_mp4_count', 0)} 段（仅供浏览器展示）。",
        f"- 镜头与 Take：{evidence.get('shot_count', 0)} 镜头 × {evidence.get('take_count_per_shot', 0)} Take。",
        f"- 时间线：manifest `{summary.get('manifest_id', '未建立')}`，版本 `{summary.get('version', '未建立')}`，片段数 `{summary.get('clip_count', 0)}`，总时长 `{summary.get('total_duration_seconds', '未测量')} 秒`。",
        f"- 版本指向：`{summary.get('version_pointer_status', '未测量')}`。",
        f"- 默认应用是否挂载 C14 视频作业路由：`{evidence.get('default_app_video_job_route_mounted', '未测量')}`；E2E 通过测试 host 挂载真实路由工厂并使用确定性 provider fixture。",
        "- API 证据：`c14-t4-api.json`；机器报告：`c14-t4-report.json`；测试记录：`c14-t4-test-log.json`。",
        "- 截图：" + ("、".join(f"`{name}`" for name in screenshots) if screenshots else "无"),
    ]
    if evidence.get("failure"):
        lines.extend(["", f"- 失败：`{evidence['failure']}`"])
    if evidence.get("blocker"):
        lines.extend(["", f"- 环境门禁：{evidence['blocker']}"])
    (root / "c14-t4-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health_ready(base: str) -> bool:
    try:
        with urlopen(f"{base}/healthz", timeout=1) as response:
            payload = json.loads(response.read(4096).decode("utf-8"))
        return response.status == 200 and payload.get("status") == "ok"
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False


class _DeterministicVideoProvider:
    """C14 API adapter fixture; records requests and never creates media files."""

    def __init__(self) -> None:
        self.submissions: list[dict[str, Any]] = []
        self._tasks: dict[str, dict[str, Any]] = {}
        self._counter = 0

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._counter += 1
        prompt_id = f"{FIXTURE_ID}-{self._counter:03d}"
        self._tasks[prompt_id] = dict(payload)
        self.submissions.append({"prompt_id": prompt_id, "payload": dict(payload)})
        return {"prompt_id": prompt_id}

    async def poll(self, prompt_id: str) -> dict[str, Any]:
        payload = self._tasks[prompt_id]
        return {
            "status": "completed",
            "outputs": [
                {
                    "filename": f"{prompt_id}.mp4",
                    "fixture_id": FIXTURE_ID,
                    "requested_target_duration": payload["target_duration"],
                }
            ],
        }

    async def cancel(self, prompt_id: str) -> dict[str, Any]:
        return {"remote_cancelled": prompt_id in self._tasks}


class _OwnedServer:
    """Owns an isolated app, temporary data root, listener, and fixture executor."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.provider = _DeterministicVideoProvider()
        self.executor = VideoJobExecutor(self.provider, storage_root="c14-e2e-artifacts")
        self.default_video_route_mounted: bool | None = None
        self.preview_paths: dict[str, Path] = {}
        self.preview_mode = "unavailable"
        self.socket: socket.socket | None = None
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None

    def prepare_preview_media(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            return
        preview_dir = self.tmp_path / "fixture-preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        colors = {"v001": "0xD4A72C", "v002": "0x2F80ED", "v003": "0x27AE60"}
        try:
            for version, color in colors.items():
                path = preview_dir / f"{version}.mp4"
                subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c={color}:s=640x360:r=24",
                        "-t",
                        "8",
                        "-an",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-pix_fmt",
                        "yuv420p",
                        "-movflags",
                        "+faststart",
                        "-y",
                        str(path),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if path.stat().st_size == 0:
                    raise OSError(f"FFmpeg produced an empty preview: {path.name}")
                self.preview_paths[version] = path
        except (OSError, subprocess.CalledProcessError):
            self.preview_paths.clear()
            return
        self.preview_mode = "ffmpeg_synthetic_color_clips"

    def start(self) -> None:
        self.prepare_preview_media()
        env = {
            "GW_RUNTIME_MODE": "test",
            "GW_AUTH_MODE": "local_account",
            "GW_DATA_DIR": str(self.tmp_path / "data"),
            "GW_LOCAL_AUTH_DB": str(self.tmp_path / "auth" / "auth.sqlite3"),
            "GW_VIDEO_DATA_DIR": str(self.tmp_path / "video"),
            "GW_PORT": str(self.port),
            "GW_HOST": "127.0.0.1",
            "GW_RELOAD": "false",
            "GW_DISABLE_AGENT_INTEGRATION": "1",
            "PYTHONPATH": str(REPO_ROOT),
        }
        for name, value in env.items():
            self.monkeypatch.setenv(name, value)
        for name in (
            "GW_PROVIDER_RUNTIME_JSON",
            "GW_CHAT_API_KEY",
            "GW_CHAT_BASE_URL",
            "GW_CLI_EXECUTION",
        ):
            self.monkeypatch.delenv(name, raising=False)

        from gw.api.app import create_app

        app = create_app()
        self.default_video_route_mounted = any(
            str(getattr(route, "path", "")).startswith("/api/god_workflow/video-jobs")
            for route in app.routes
        )
        # The test host uses the real route and executor with only the provider replaced.
        app.include_router(create_video_jobs_router(self.executor))
        if self.preview_paths:
            from fastapi import HTTPException
            from fastapi.responses import FileResponse

            async def serve_fixture_preview(filename: str) -> FileResponse:
                match = re.fullmatch(r"EP01_SC01_SH\d{3}_(v00[123])_raw\.mp4", filename)
                if match is None or match.group(1) not in self.preview_paths:
                    raise HTTPException(status_code=404, detail="fixture preview not found")
                return FileResponse(
                    self.preview_paths[match.group(1)],
                    media_type="video/mp4",
                    filename=filename,
                    headers={"Cache-Control": "no-store"},
                )

            app.add_api_route(
                "/c14-e2e-media/{filename}",
                serve_fixture_preview,
                methods=["GET"],
                include_in_schema=False,
            )

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.port))
        listener.listen()
        listener.setblocking(False)
        self.socket = listener
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error", lifespan="off")
        )
        self.thread = threading.Thread(
            target=self.server.run,
            kwargs={"sockets": [listener]},
            name="c14-t4-e2e-server",
            daemon=True,
        )
        self.thread.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.server.should_exit or not self.thread.is_alive():
                raise AssertionError(f"C14-T4 测试服务提前退出：{self.base}")
            if _health_ready(self.base):
                return
            time.sleep(0.1)
        raise AssertionError(f"C14-T4 测试服务未就绪：{self.base}")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise AssertionError("C14-T4 隔离测试服务未退出")
        if self.socket is not None:
            self.socket.close()
            self.socket = None


def _json_response(response: Any, expected: int) -> dict[str, Any]:
    assert response.status == expected, response.text()
    payload = response.json() if response.text() else {}
    assert isinstance(payload, dict)
    return payload


def _same_origin(route: Any, base: str, preview_media_available: bool) -> None:
    if route.request.url.startswith(base + "/c14-e2e-media/"):
        if preview_media_available:
            route.continue_()
        else:
            route.abort()
    elif route.request.url.startswith(base + "/") or route.request.url.startswith("data:"):
        route.continue_()
    else:
        route.abort()


def _shot_id(number: int) -> str:
    return f"SC01_SH{number:03d}"


def _make_shots(generated: dict[tuple[str, int], dict[str, Any]]) -> list[dict[str, Any]]:
    shots: list[dict[str, Any]] = []
    for number, duration in enumerate(SHOT_DURATIONS, start=1):
        shot_id = _shot_id(number)
        takes = []
        for version in TAKE_VERSIONS:
            job = generated[(shot_id, version)]
            asset = job["asset"]
            version_id = f"EP01_SC01_SH{number:03d}_v{version:03d}"
            takes.append(
                {
                    "take_id": f"{shot_id}-take-{version}",
                    "asset_id": asset["asset_id"],
                    "version_id": version_id,
                    "version": version,
                    "media_type": "video",
                    "media_url": f"/c14-e2e-media/{asset['filename']}",
                    "status": "completed",
                    "job_id": job["job_id"],
                    "path": asset["path"],
                }
            )
        shots.append(
            {
                "id": shot_id,
                "shot_id": shot_id,
                "scene_id": "SC01",
                "episode_id": "EP01",
                "project_id": generated[(shot_id, 1)]["project_id"],
                "name": shot_id,
                "duration": duration,
                "asset_id": takes[0]["asset_id"],
                "version_id": takes[0]["version_id"],
                "version": 1,
                "media_type": "video",
                "media_url": takes[0]["media_url"],
                "status": "completed",
                "primary_take_id": takes[0]["take_id"],
                "takes": takes,
                "camera": "MS" if number % 2 else "CU",
                "movement": "DOLLY_IN" if number % 2 else "PAN_RIGHT",
            }
        )
    return shots


def _submit_and_poll_takes(request: Any, base: str, project_id: str) -> tuple[dict[tuple[str, int], dict[str, Any]], list[dict[str, Any]]]:
    generated: dict[tuple[str, int], dict[str, Any]] = {}
    api_jobs: list[dict[str, Any]] = []
    for number, duration in enumerate(SHOT_DURATIONS, start=1):
        shot_code = f"SH{number:03d}"
        shot_id = _shot_id(number)
        for version in TAKE_VERSIONS:
            body = {
                "shot_id": shot_code,
                "episode": "EP01",
                "scene": "SC01",
                "version": version,
                "image_asset_id": f"{project_id}-{shot_id}-storyboard-v001",
                "target_duration": duration,
                "prompt": f"C14-T4 fixture shot {shot_id}, take v{version:03d}",
                "camera_ctrl": {
                    "trajectory": [
                        {"at": 0.0, "x": 0.0, "y": 0.0, "z": 0.0},
                        {"at": 1.0, "x": 0.2 * version, "y": 0.0, "z": 0.0},
                    ]
                },
                "lora": [{"name": "c14-t4-test-character", "weight": 0.75}],
                "fixture_id": FIXTURE_ID,
            }
            submit = _json_response(
                request.post(
                    f"{base}/api/god_workflow/video-jobs",
                    data=body,
                    headers={"Origin": base},
                ),
                202,
            )
            assert submit["status"] == "accepted"
            assert submit["payload"]["target_duration"] == duration
            assert submit["payload"]["fixture_id"] == FIXTURE_ID
            assert submit["payload"]["camera_ctrl"]["trajectory"][-1]["x"] == pytest.approx(
                0.2 * version
            )
            assert submit["payload"]["lora"] == body["lora"]
            polled = _json_response(
                request.get(
                    f"{base}/api/god_workflow/video-jobs/{submit['job_id']}",
                    headers={"Origin": base},
                ),
                200,
            )
            assert polled["status"] == "completed"
            assert polled["asset"]["source_shot_id"] == shot_code
            assert polled["asset"]["source_version"] == f"v{version:03d}"
            assert polled["asset"]["content_type"] == "video/mp4"
            assert polled["outputs"][0]["requested_target_duration"] == duration
            assert polled["filename"] == f"EP01_SC01_{shot_code}_v{version:03d}_raw.mp4"
            record = {
                "project_id": project_id,
                "shot_id": shot_id,
                "target_duration": duration,
                "version": version,
                "job_id": submit["job_id"],
                "filename": polled["filename"],
                "asset": polled["asset"],
                "submit": submit,
                "poll": polled,
            }
            generated[(shot_id, version)] = record
            api_jobs.append(record)
    return generated, api_jobs


def _promote_via_ui(page: Any, pipeline_id: str, shot_id: str, take_id: str, expected_version: int) -> dict[str, Any]:
    selector = (
        f'[data-contact-sheet][data-shot-id="{shot_id}"] '
        f'[data-shot-action="promote-take"][data-take-id="{take_id}"]'
    )
    button = page.locator(selector)
    assert button.count() == 1, f"找不到 {shot_id} 的回选按钮 {take_id}"
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/episode-pipelines/{pipeline_id}/timeline-manifest")
        and response.request.method == "PUT",
        timeout=15_000,
    ) as response_info:
        button.click()
    response = response_info.value
    assert response.status == 200, response.text()
    request_body = response.request.post_data_json
    payload = response.json()
    manifest = payload["timeline_manifest"]
    assert request_body["expected_version"] == expected_version
    assert manifest["version"] == expected_version + 1
    page.wait_for_function(
        "args => window.WorkbenchStoryboard.getState().timelineManifest?.version === args.version",
        arg={"version": manifest["version"]},
        timeout=10_000,
    )
    return {
        "shot_id": shot_id,
        "take_id": take_id,
        "expected_version": request_body["expected_version"],
        "response_version": manifest["version"],
        "manifest_id": manifest["manifest_id"],
    }


def test_c14_real_browser_gate_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REAL_RUN_ENV, raising=False)
    assert _real_run_enabled() is False
    assert _real_run_enabled({REAL_RUN_ENV: "true"}) is True


def test_c14_fixture_labels_simulated_provider_evidence() -> None:
    payload = {
        "fixture_id": FIXTURE_ID,
        "provider": "deterministic-test-fixture",
        "video_compute": False,
        "provider_mp4_bytes_created": False,
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert FIXTURE_ID in serialized
    assert payload["video_compute"] is False
    assert payload["provider_mp4_bytes_created"] is False
    assert "provider_success" not in serialized
    assert "真实 Provider 已完成" not in serialized


def test_c14_i2v_ten_shot_generation_and_multitake_browser_closeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """C14-AC4: real Chrome + actual API persistence + 10 × 3 deterministic takes."""
    if not _real_run_enabled():
        pytest.skip("C14 真实浏览器默认关闭；设置 GW_C14_REAL_E2E=1 后才运行")

    evidence: dict[str, Any] = {
        "task": "QIN-219 / C14-T4",
        "commit_sha": _git_sha(),
        "fixture_id": FIXTURE_ID,
        "provider": "deterministic-test-fixture",
        "video_compute": False,
        "provider_mp4_bytes_created": False,
        "preview_mp4_count": 0,
        "preview_mode": "not_prepared",
        "status": "not_run",
        "shot_count": SHOT_COUNT,
        "take_count_per_shot": len(TAKE_VERSIONS),
        "job_count": SHOT_COUNT * len(TAKE_VERSIONS),
        "screenshots": [],
        "page_errors": [],
        "assertions": [],
    }
    api_evidence: dict[str, Any] = {}
    server = _OwnedServer(tmp_path, monkeypatch)
    server_started = False
    try:
        try:
            browser_api = importlib.import_module("playwright.sync_api")
        except ImportError as exc:
            evidence.update(status="blocked_environment", blocker=f"Playwright 不可用：{type(exc).__name__}")
            _write_artifacts(evidence, api_evidence)
            pytest.skip("Playwright 不可用；真实浏览器证据未取得")

        server_started = True
        server.start()
        evidence.update(
            {
                "preview_mp4_count": len(server.preview_paths),
                "preview_mode": server.preview_mode,
                "default_app_video_job_route_mounted": server.default_video_route_mounted,
            }
        )
        try:
            with open_playwright(browser_api) as playwright:
                try:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                except browser_api.Error as exc:
                    evidence.update(
                        status="blocked_environment",
                        blocker=f"Chrome 不可启动：{type(exc).__name__}",
                    )
                    _write_artifacts(evidence, api_evidence)
                    pytest.skip(f"Chrome 不可启动：{type(exc).__name__}")

                context = browser.new_context(viewport={"width": 1800, "height": 1000})
                context.route(
                    "**/*",
                    lambda route: _same_origin(route, server.base, bool(server.preview_paths)),
                )
                request = context.request
                try:
                    _json_response(
                        request.post(
                            f"{server.base}/api/asset-auth/local/setup",
                            data={"username": "c14_t4_browser_owner", "password": "Test2026"},
                            headers={"Origin": server.base},
                        ),
                        201,
                    )
                    project_payload = _json_response(
                        request.post(
                            f"{server.base}/api/asset-registry/projects",
                            data={"name": "C14 T4 ten-shot browser evidence", "project_type": "film"},
                            headers={"Origin": server.base},
                        ),
                        201,
                    )
                    project = project_payload["project"]
                    project_id = project["project_id"]
                    shot_context = [
                        {"shot_id": _shot_id(number), "id": _shot_id(number), "code": _shot_id(number)}
                        for number in range(1, SHOT_COUNT + 1)
                    ]
                    pipeline_payload = _json_response(
                        request.post(
                            f"{server.base}/api/episode-pipelines",
                            data={
                                "project_id": project_id,
                                "title": "C14 T4 10-shot I2V acceptance fixture",
                                "seed": FIXTURE_ID,
                                "production_context": {
                                    "episodes": [
                                        {
                                            "episode_id": "EP01",
                                            "id": "EP01",
                                            "code": "EP01",
                                            "name": "C14 T4",
                                            "scenes": [
                                                {
                                                    "scene_id": "SC01",
                                                    "id": "SC01",
                                                    "code": "SC01",
                                                    "name": "十镜闭环",
                                                    "shots": shot_context,
                                                }
                                            ],
                                        }
                                    ]
                                },
                            },
                            headers={"Origin": server.base},
                        ),
                        201,
                    )
                    pipeline = pipeline_payload.get("pipeline", pipeline_payload)
                    pipeline_id = pipeline["pipeline_id"]
                    generated, api_jobs = _submit_and_poll_takes(request, server.base, project_id)
                    shots = _make_shots(generated)
                    assert len(shots) == SHOT_COUNT
                    assert all(len(shot["takes"]) == len(TAKE_VERSIONS) for shot in shots)
                    evidence["assertions"].append("10 镜 × 3 Take；30 个真实 API submit/poll 响应")
                    api_evidence.update(
                        {
                            "fixture_id": FIXTURE_ID,
                            "video_compute": False,
                            "provider_mp4_bytes_created": False,
                            "preview_mp4_count": len(server.preview_paths),
                            "preview_mode": server.preview_mode,
                            "default_app_video_job_route_mounted": server.default_video_route_mounted,
                            "project": project,
                            "pipeline": pipeline,
                            "video_jobs": api_jobs,
                        }
                    )

                    page_errors: list[str] = []
                    page = context.new_page()
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    page.goto(
                        f"{server.base}/static/pages/storyboard.html"
                        f"?project_id={project_id}&view=storyboard&shot_id={_shot_id(1)}",
                        wait_until="networkidle",
                    )
                    page.wait_for_function("!window.GWAuthGate || window.GWAuthGate.loaded === true")
                    page.wait_for_function(
                        "args => window.WorkbenchStoryboard?.getState?.().pipelineId === args.pipelineId",
                        arg={"pipelineId": pipeline_id},
                        timeout=20_000,
                    )
                    page.evaluate("shots => window.WorkbenchStoryboard.setShots(shots)", shots)
                    page.locator('#storyboardGrid [data-contact-sheet][data-shot-id]').first.wait_for(state="visible")
                    assert page.locator("#storyboardGrid [data-contact-sheet][data-shot-id]").count() == SHOT_COUNT
                    assert page.locator('[data-take-version="v001"]').count() == SHOT_COUNT
                    assert page.locator('[data-take-version="v002"]').count() == SHOT_COUNT
                    assert page.locator('[data-take-version="v003"]').count() == SHOT_COUNT
                    if server.preview_paths:
                        page.wait_for_function(
                            "() => { const videos = Array.from(document.querySelectorAll('#storyboardGrid video[data-take-preview]')); return videos.length === 30 && videos.every(video => video.readyState >= 2); }",
                            timeout=20_000,
                        )
                        evidence["assertions"].append("30 个 Take 预览均由本地合成 MP4 在 Chrome 中解码")
                    page.screenshot(path=str(_evidence_dir() / "c14-t4-contact-sheets-before.png"), full_page=True)
                    evidence["screenshots"].append("c14-t4-contact-sheets-before.png")

                    with page.expect_response(
                        lambda response: response.url.endswith(
                            f"/api/episode-pipelines/{pipeline_id}/timeline-manifest"
                        ) and response.request.method == "POST",
                        timeout=15_000,
                    ) as create_response_info:
                        page.locator("#btnSyncTimeline").click()
                    create_response = create_response_info.value
                    assert create_response.status == 201, create_response.text()
                    initial_manifest = create_response.json()["timeline_manifest"]
                    assert initial_manifest["version"] == 1
                    assert len(initial_manifest["tracks"][0]["clips"]) == SHOT_COUNT
                    page.wait_for_function(
                        "() => window.WorkbenchStoryboard.getState().timelineManifest?.version === 1",
                        timeout=10_000,
                    )
                    evidence["assertions"].append("真实分镜台创建 10 段持久化时间线，初始 manifest version=1")

                    promotions: list[dict[str, Any]] = []
                    expected_version = 1
                    for number in range(1, SHOT_COUNT + 1):
                        promotion = _promote_via_ui(
                            page,
                            pipeline_id,
                            _shot_id(number),
                            f"{_shot_id(number)}-take-2",
                            expected_version,
                        )
                        promotions.append(promotion)
                        expected_version = promotion["response_version"]
                    # Re-select two shots again to prove pointer changes and version CAS do not drift.
                    for number in (1, SHOT_COUNT):
                        promotion = _promote_via_ui(
                            page,
                            pipeline_id,
                            _shot_id(number),
                            f"{_shot_id(number)}-take-3",
                            expected_version,
                        )
                        promotions.append(promotion)
                        expected_version = promotion["response_version"]

                    grid = page.locator("#storyboardGrid")
                    grid.evaluate("element => { element.parentElement.scrollTop = 0; }")
                    page.wait_for_timeout(200)
                    page.screenshot(
                        path=str(_evidence_dir() / "c14-t4-contact-sheets-after-top.png"),
                        full_page=True,
                    )
                    evidence["screenshots"].append("c14-t4-contact-sheets-after-top.png")
                    grid.evaluate("element => { element.parentElement.scrollTop = element.parentElement.scrollHeight; }")
                    page.wait_for_timeout(200)
                    page.screenshot(
                        path=str(_evidence_dir() / "c14-t4-contact-sheets-after-bottom.png"),
                        full_page=True,
                    )
                    evidence["screenshots"].append("c14-t4-contact-sheets-after-bottom.png")
                    final_payload = _json_response(
                        request.get(
                            f"{server.base}/api/episode-pipelines/{pipeline_id}/timeline-manifest",
                            headers={"Origin": server.base},
                        ),
                        200,
                    )
                    final_manifest = final_payload["timeline_manifest"]
                    clips = final_manifest["tracks"][0]["clips"]
                    by_shot = {clip["shot_id"]: clip for clip in clips}
                    assert len(by_shot) == SHOT_COUNT
                    assert set(by_shot) == {_shot_id(number) for number in range(1, SHOT_COUNT + 1)}
                    assert final_manifest["version"] == 1 + len(promotions)
                    assert [item["expected_version"] for item in promotions] == list(
                        range(1, 1 + len(promotions))
                    )
                    expected_at = 0.0
                    for number, duration in enumerate(SHOT_DURATIONS, start=1):
                        shot_id = _shot_id(number)
                        selected_version = 3 if number in (1, SHOT_COUNT) else 2
                        clip = by_shot[shot_id]
                        expected_version_id = f"EP01_SC01_SH{number:03d}_v{selected_version:03d}"
                        expected_take_id = f"{shot_id}-take-{selected_version}"
                        expected_asset = generated[(shot_id, selected_version)]["asset"]["asset_id"]
                        assert clip["version_id"] == expected_version_id
                        assert clip["take_id"] == expected_take_id
                        assert clip["asset_id"] == expected_asset
                        assert clip["at"] == pytest.approx(expected_at, abs=1e-9)
                        assert clip["duration"] == duration
                        assert clip["overrides"]["src"].endswith(f"#t=0,{duration:g}")
                        expected_at += duration
                    assert expected_at == pytest.approx(41.9, abs=1e-9)
                    assert final_manifest["metadata"]["primary_take_by_shot"][_shot_id(1)]["take_id"] == (
                        f"{_shot_id(1)}-take-3"
                    )
                    assert final_manifest["metadata"]["primary_take_by_shot"][_shot_id(SHOT_COUNT)][
                        "take_id"
                    ] == f"{_shot_id(SHOT_COUNT)}-take-3"
                    assert not page_errors, page_errors

                    total_duration = round(sum(clip["duration"] for clip in clips), 3)
                    timeline_summary = {
                        "manifest_id": final_manifest["manifest_id"],
                        "version": final_manifest["version"],
                        "clip_count": len(clips),
                        "total_duration_seconds": total_duration,
                        "version_pointer_status": "10/10 clip asset_id/version_id/take_id align with selected takes; no drift",
                        "promotions": promotions,
                    }
                    evidence.update(
                        {
                            "status": "observed",
                            "project_id": project_id,
                            "pipeline_id": pipeline_id,
                            "job_count": len(api_jobs),
                            "default_app_video_job_route_mounted": server.default_video_route_mounted,
                            "timeline_summary": timeline_summary,
                            "browser": {"engine": "Chrome via Playwright", "real_opt_in": True},
                            "page_errors": page_errors,
                        }
                    )
                    evidence["assertions"].extend(
                        [
                            "10 镜头 timeline clip 的起点累计、target duration 与 #t=0,T 均无漂移",
                            "12 次主力 Take CAS 回选连续递增，末版指针与 UI 选中版本一致",
                            "真实 Chrome 页面无 JavaScript pageerror",
                        ]
                    )
                    api_evidence.update(
                        {
                            "initial_timeline_manifest": initial_manifest,
                            "promotions": promotions,
                            "final_timeline_manifest": final_manifest,
                        }
                    )
                    _write_artifacts(evidence, api_evidence)
                finally:
                    context.close()
                    browser.close()
        except BaseException as exc:
            if type(exc).__name__ == "Skipped":
                evidence["status"] = "blocked_environment"
                evidence.setdefault("blocker", str(exc))
            else:
                evidence["status"] = "failed"
                evidence["failure"] = f"{type(exc).__name__}: {exc}"
            _write_artifacts(evidence, api_evidence)
            raise
    finally:
        if server_started:
            server.stop()
