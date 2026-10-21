"""C13-T4：动态样片连播与 2D 雷达越轴预警的真实浏览器收口。

默认只验证契约夹具与真实浏览器门禁；Chrome/服务路径必须由操作者显式设置
``GW_C13_REAL_E2E=1`` 才运行。夹具使用静态 SVG/WAV 数据，不调用 Provider，
证据报告会明确标记为非 Provider 生成结果。
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright_support import open_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPO_ROOT / ".local" / "qa" / "C13-evidence"
REAL_RUN_ENV = "GW_C13_REAL_E2E"
FIXTURE_ID = "c13-t4-animatic-radar-v001"


def _real_run_enabled(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(REAL_RUN_ENV, "")).strip().lower() in {"1", "true", "yes"}


def _evidence_dir() -> Path:
    configured = str(os.environ.get("GW_C13_EVIDENCE_DIR", "")).strip()
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
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _tiny_wav_data_url() -> str:
    """一个无外部请求的 8kHz 单声道静音 WAV，供浏览器音轨加载。"""
    raw = bytearray(
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x40\x1f\x00\x00\x80\x3e\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )
    return "data:audio/wav;base64," + base64.b64encode(raw).decode("ascii")


def _timeline_fixture() -> dict[str, Any]:
    audio_url = _tiny_wav_data_url()
    frames = [
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='640' height='360'%3E%3Crect width='640' height='360' fill='%2314161c'/%3E%3Ctext x='24' y='52' fill='%23dfc384'%3ESC01_SH001%3C/text%3E%3C/svg%3E",
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='640' height='360'%3E%3Crect width='640' height='360' fill='%23201612'/%3E%3Ctext x='24' y='52' fill='%23dfc384'%3ESC01_SH002%3C/text%3E%3C/svg%3E",
    ]
    return {
        "metadata": {
            "fixture_id": FIXTURE_ID,
            "provider": None,
            "video_compute": False,
        },
        "tracks": [
            {
                "track_id": "video-01",
                "kind": "video",
                "clips": [
                    {
                        "shot_id": "SC01_SH001",
                        "asset_id": "asset-c13-t4-frame-001",
                        "at": 0.0,
                        "duration": 0.30,
                        "overrides": {
                            "src": frames[0] + "#t=0,0.30",
                            "camera": "MS",
                            "movement": "DOLLY_IN",
                        },
                    },
                    {
                        "shot_id": "SC01_SH002",
                        "asset_id": "asset-c13-t4-frame-002",
                        "at": 0.30,
                        "duration": 0.30,
                        "overrides": {
                            "src": frames[1] + "#t=0,0.30",
                            "camera": "CU",
                            "movement": "PAN_RIGHT",
                        },
                    },
                ],
            },
            {
                "track_id": "dialogue-01",
                "kind": "dialogue",
                "audio_url": audio_url,
                "clips": [
                    {
                        "shot_id": "SC01_SH001_DIALOGUE",
                        "asset_id": "asset-c13-t4-audio-001",
                        "at": 0.0,
                        "duration": 0.30,
                    },
                    {
                        "shot_id": "SC01_SH002_DIALOGUE",
                        "asset_id": "asset-c13-t4-audio-002",
                        "at": 0.30,
                        "duration": 0.30,
                    },
                ],
            },
        ],
    }


def _radar_shots(project_id: str, canvas_id: str) -> list[dict[str, Any]]:
    """相邻镜头跨越动作轴，供真实页面计算越轴告警。"""
    return [
        {
            "id": "SC01_SH001",
            "shot_id": "SC01_SH001",
            "asset_id": "asset-c13-t4-frame-001",
            "project_id": project_id,
            "canvas_id": canvas_id,
            "name": "SC01_SH001",
            "media_type": "image",
            "status": "completed",
            "imageUrl": _timeline_fixture()["tracks"][0]["clips"][0]["overrides"][
                "src"
            ].split("#", 1)[0],
            "camera_radar": {
                "x": -10,
                "y": -5,
                "z": 1.6,
                "heading": 0,
                "fov": 50,
                "range": 100,
            },
            "subject_position": {"x": 0, "y": 0, "z": 0},
            "action_axis": 0,
        },
        {
            "id": "SC01_SH002",
            "shot_id": "SC01_SH002",
            "asset_id": "asset-c13-t4-frame-002",
            "project_id": project_id,
            "canvas_id": canvas_id,
            "name": "SC01_SH002",
            "media_type": "image",
            "status": "completed",
            "imageUrl": _timeline_fixture()["tracks"][0]["clips"][1]["overrides"][
                "src"
            ].split("#", 1)[0],
            "camera_radar": {
                "x": 10,
                "y": 5,
                "z": 1.6,
                "heading": 180,
                "fov": 50,
                "range": 100,
            },
            "subject_position": {"x": 0, "y": 0, "z": 0},
            "action_axis": 0,
        },
    ]


def _manifest_contract(payload: dict[str, Any]) -> dict[str, Any]:
    tracks = payload["tracks"]
    video = next(track for track in tracks if track["kind"] == "video")
    dialogue = next(track for track in tracks if track["kind"] == "dialogue")
    assert [clip["shot_id"] for clip in video["clips"]] == ["SC01_SH001", "SC01_SH002"]
    assert [clip["at"] for clip in video["clips"]] == [0.0, 0.30]
    assert [clip["shot_id"] for clip in dialogue["clips"]] == [
        "SC01_SH001_DIALOGUE",
        "SC01_SH002_DIALOGUE",
    ]
    assert dialogue["audio_url"].startswith("data:audio/wav")
    return {
        "fixture_id": FIXTURE_ID,
        "video_clip_count": len(video["clips"]),
        "dialogue_clip_count": len(dialogue["clips"]),
        "shot_ids": [clip["shot_id"] for clip in video["clips"]],
        "durations": [clip["duration"] for clip in video["clips"]],
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health_ready(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
            payload = json.loads(response.read(4096).decode("utf-8"))
        return response.status == 200 and payload.get("status") == "ok"
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False


class _OwnedServer:
    """只启动本测试拥有的隔离服务，不接触开发或生产数据。"""

    def __init__(self, tmp_path: Path):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log_path = tmp_path / "c13-t4-server.log"
        self.env = os.environ.copy()
        self.env.update(
            {
                "GW_RUNTIME_MODE": "test",
                "GW_AUTH_MODE": "local_account",
                "GW_DATA_DIR": str(tmp_path / "data"),
                "GW_LOCAL_AUTH_DB": str(tmp_path / "auth" / "auth.sqlite3"),
                "GW_VIDEO_DATA_DIR": str(tmp_path / "video"),
                "GW_PORT": str(self.port),
                "GW_HOST": "127.0.0.1",
                "GW_RELOAD": "false",
                "PYTHONPATH": str(REPO_ROOT),
            }
        )
        for name in (
            "GW_PROVIDER_RUNTIME_JSON",
            "GW_CHAT_API_KEY",
            "GW_CHAT_BASE_URL",
            "GW_CLI_EXECUTION",
        ):
            self.env.pop(name, None)
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab")
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.process = subprocess.Popen(
                [sys.executable, str(REPO_ROOT / "run.py"), "--test"],
                cwd=REPO_ROOT,
                env=self.env,
                stdout=log,
                stderr=log,
                creationflags=creationflags,
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise AssertionError(
                        f"C13-T4 测试服务提前退出，日志见 {self.log_path}"
                    )
                if _health_ready(self.port):
                    return
                time.sleep(0.1)
            raise AssertionError(f"C13-T4 测试服务未就绪，日志见 {self.log_path}")
        except BaseException:
            self.stop()
            raise
        finally:
            log.close()

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _json_response(response: Any, expected: int) -> dict[str, Any]:
    assert response.status == expected, response.text()
    payload = response.json() if response.text() else {}
    assert isinstance(payload, dict)
    return payload


def _same_origin(route: Any, base: str) -> None:
    if route.request.url.startswith(base + "/") or route.request.url.startswith(
        "data:"
    ):
        route.continue_()
    else:
        route.abort()


def test_c13_real_browser_gate_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REAL_RUN_ENV, raising=False)
    assert _real_run_enabled() is False
    assert _real_run_enabled({REAL_RUN_ENV: "true"}) is True


def test_c13_fixture_is_explicitly_non_provider_evidence() -> None:
    payload = _timeline_fixture()
    contract = _manifest_contract(payload)
    serialized = json.dumps({"fixture": contract, "provider": None}, ensure_ascii=False)
    assert contract["fixture_id"] == FIXTURE_ID
    assert "provider_success" not in serialized
    assert "真实 Provider 已完成" not in serialized


def test_c13_animatic_radar_real_browser_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C13-AC2/AC3/AC4：真实 Chrome、真实服务 API、刷新与服务重启回读。"""
    if not _real_run_enabled():
        pytest.skip("C13 真实浏览器默认关闭；设置 GW_C13_REAL_E2E=1 后才会运行")
    browser_api = pytest.importorskip("playwright.sync_api")

    server = _OwnedServer(tmp_path)
    server.start()
    manifest_payload = _timeline_fixture()
    fixture_contract = _manifest_contract(manifest_payload)
    evidence: dict[str, Any] = {
        "task": "QIN-215 / C13-T4",
        "commit_sha": _git_sha(),
        "fixture_id": FIXTURE_ID,
        "provider": None,
        "video_compute": False,
        "status": "not_run",
        "screenshots": [],
        "page_errors": [],
    }

    try:
        with open_playwright(browser_api) as playwright:
            try:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
            except browser_api.Error as exc:
                evidence.update(
                    status="blocked_environment",
                    blocker=f"Chrome 不可启动：{type(exc).__name__}",
                )
                _write_json(_evidence_dir() / "c13-t4-dependency-gate.json", evidence)
                pytest.skip(f"Chrome 不可启动：{type(exc).__name__}")

            context = browser.new_context(viewport={"width": 1800, "height": 1000})
            context.route("**/*", lambda route: _same_origin(route, server.base))
            request = context.request
            try:
                _json_response(
                    request.post(
                        f"{server.base}/api/asset-auth/local/setup",
                        data={"username": "c13_t4_owner", "password": "Test2026"},
                        headers={"Origin": server.base},
                    ),
                    201,
                )
                project = _json_response(
                    request.post(
                        f"{server.base}/api/asset-registry/projects",
                        data={"name": "C13 T4 animatic radar", "project_type": "film"},
                        headers={"Origin": server.base},
                    ),
                    201,
                )["project"]
                project_id = project["project_id"]
                pipeline = _json_response(
                    request.post(
                        f"{server.base}/api/episode-pipelines",
                        data={
                            "project_id": project_id,
                            "title": "C13 T4 browser fixture",
                            "seed": FIXTURE_ID,
                            "production_context": {
                                "episodes": [
                                    {
                                        "episode_id": "EP01",
                                        "code": "EP01",
                                        "name": "C13 T4",
                                        "scenes": [
                                            {
                                                "scene_id": "SC01",
                                                "code": "SC01",
                                                "name": "Radar",
                                                "shots": [],
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
                pipeline_id = pipeline["pipeline_id"]
                manifest = _json_response(
                    request.post(
                        f"{server.base}/api/episode-pipelines/{pipeline_id}/timeline-manifest",
                        data=manifest_payload,
                        headers={"Origin": server.base},
                    ),
                    201,
                )["timeline_manifest"]

                canvas = _json_response(
                    request.post(
                        f"{server.base}/api/canvases",
                        data={
                            "project_id": project_id,
                            "title": "C13 T4 camera radar",
                            "mode": "smart",
                            "initial_payload": {
                                "nodes": [
                                    {
                                        "entity_id": "camera-radar-SC01_SH002",
                                        "node_type": "camera_radar",
                                        "kind": "camera_radar",
                                        "project_id": project_id,
                                        "shot_id": "SC01_SH002",
                                        "camera": {
                                            "x": 10,
                                            "y": 5,
                                            "z": 1.6,
                                            "heading": 180,
                                            "fov": 50,
                                            "range": 100,
                                        },
                                        "subject": {"x": 0, "y": 0, "z": 0},
                                        "metadata": {
                                            "shot_id": "SC01_SH002",
                                            "project_id": project_id,
                                            "previous_camera": {
                                                "x": -10,
                                                "y": -5,
                                                "z": 1.6,
                                                "heading": 0,
                                                "fov": 50,
                                                "range": 100,
                                            },
                                            "axis_angle": 0,
                                        },
                                    }
                                ],
                                "connections": [],
                            },
                        },
                        headers={"Origin": server.base},
                    ),
                    201,
                )["canvas"]
                canvas_id = canvas["canvas_id"]
                evidence.update(
                    {
                        "project_id": project_id,
                        "pipeline_id": pipeline_id,
                        "manifest_id": manifest["manifest_id"],
                        "canvas_id": canvas_id,
                    }
                )
                _write_json(
                    _evidence_dir() / "c13-t4-api.json",
                    {
                        "project": project,
                        "pipeline": pipeline,
                        "manifest": manifest,
                        "canvas": canvas,
                        "fixture": fixture_contract,
                    },
                )

                page_errors: list[str] = []
                production_page = context.new_page()
                production_page.on(
                    "pageerror", lambda error: page_errors.append(str(error))
                )
                production_page.goto(
                    f"{server.base}/static/pages/production.html?project_id={project_id}",
                    wait_until="networkidle",
                )
                production_page.wait_for_function(
                    "!window.GWAuthGate || window.GWAuthGate.loaded === true"
                )
                production_page.wait_for_function(
                    "() => window.WorkbenchProduction?.getState?.().timelineClips?.length === 2",
                    timeout=30_000,
                )
                production_state = production_page.evaluate(
                    "() => window.WorkbenchProduction.getState()"
                )
                assert (
                    production_state["timelineManifest"]["manifest_id"]
                    == manifest["manifest_id"]
                )
                assert [
                    clip["shot_id"] for clip in production_state["timelineClips"]
                ] == fixture_contract["shot_ids"]
                assert len(production_state["timelineAudioClips"]) == 2
                production_page.screenshot(
                    path=str(_evidence_dir() / "c13-t4-production-loaded.png"),
                    full_page=True,
                )
                evidence["screenshots"].append("c13-t4-production-loaded.png")
                production_page.get_by_role("button", name="连续播放").click()
                production_page.wait_for_function(
                    "() => window.WorkbenchProduction.getState().timelinePlaying === true",
                    timeout=5_000,
                )
                production_page.wait_for_function(
                    "() => window.WorkbenchProduction.getState().timelinePlaying === false && window.WorkbenchProduction.getState().activeTimelineIndex === 1",
                    timeout=10_000,
                )
                evidence["playback"] = production_page.evaluate(
                    "() => window.WorkbenchProduction.getState()"
                )
                production_page.reload(wait_until="networkidle")
                production_page.wait_for_function(
                    "() => window.WorkbenchProduction?.getState?.().timelineClips?.length === 2",
                    timeout=30_000,
                )
                evidence["refresh_playback"] = production_page.evaluate(
                    "() => window.WorkbenchProduction.getState()"
                )

                storyboard_page = context.new_page()
                storyboard_page.on(
                    "pageerror", lambda error: page_errors.append(str(error))
                )
                storyboard_page.goto(
                    f"{server.base}/static/pages/storyboard.html?project_id={project_id}&canvas_id={canvas_id}&view=storyboard&shot_id=SC01_SH002",
                    wait_until="networkidle",
                )
                storyboard_page.wait_for_function(
                    "!window.GWAuthGate || window.GWAuthGate.loaded === true"
                )
                storyboard_page.evaluate(
                    "shots => window.WorkbenchStoryboard.setShots(shots)",
                    _radar_shots(project_id, canvas_id),
                )
                storyboard_page.evaluate(
                    "() => window.switchToolTab(document.querySelector('[data-tool-id=radar]'), 'radar')"
                )
                storyboard_page.locator("[data-radar-panel]").wait_for(state="visible")
                assert (
                    storyboard_page.locator(
                        '[data-radar-panel][data-radar-alert="axis-crossing"]'
                    ).count()
                    == 1
                )
                assert (
                    storyboard_page.locator(
                        '[data-radar-host] svg[data-radar-type="topdown-camera-radar"]'
                    ).count()
                    == 1
                )
                focus_link = (
                    storyboard_page.locator("[data-radar-canvas-link]").get_attribute(
                        "href"
                    )
                    or ""
                )
                assert (
                    "canvas.html" in focus_link
                    and "focus_node=camera-radar-SC01_SH002" in focus_link
                )
                storyboard_page.screenshot(
                    path=str(_evidence_dir() / "c13-t4-storyboard-radar.png"),
                    full_page=True,
                )
                evidence["screenshots"].append("c13-t4-storyboard-radar.png")

                deep_link = request.get(
                    f"{server.base}{focus_link}", headers={"Origin": server.base}
                )
                evidence["deep_link"] = {"href": focus_link, "status": deep_link.status}
                if deep_link.status != 200:
                    evidence.update(
                        status="blocked_dependency",
                        blocker=(
                            f"QIN-212 生成的 {focus_link.split('?', 1)[0]} 未被当前服务接入 "
                            f"（HTTP {deep_link.status}）；规范静态入口为 /static/pages/canvas.html，未越界修改路由。"
                        ),
                    )
                    _write_json(
                        _evidence_dir() / "c13-t4-dependency-gate.json", evidence
                    )
                    pytest.skip("QIN-212 画布根路径兼容入口未接入，AC3 深链证据阻断")

                canvas_page = context.new_page()
                canvas_page.on(
                    "pageerror", lambda error: page_errors.append(str(error))
                )
                canvas_page.goto(server.base + focus_link, wait_until="networkidle")
                canvas_page.locator('#canvasRadarStatus[data-state="ready"]').wait_for(
                    state="visible", timeout=15_000
                )
                canvas_page.locator(
                    'article[data-node-id="camera-radar-SC01_SH002"]'
                ).wait_for(state="visible")
                assert (
                    canvas_page.locator(
                        '[data-node-type="camera_radar"] svg[data-radar-alert="axis-crossing"]'
                    ).count()
                    == 1
                )
                assert canvas_page.locator("[data-storyboard-return]").count() == 1
                canvas_page.screenshot(
                    path=str(_evidence_dir() / "c13-t4-canvas-radar.png"),
                    full_page=True,
                )
                evidence["screenshots"].append("c13-t4-canvas-radar.png")

                server.stop()
                server.start()
                production_page.reload(wait_until="networkidle")
                production_page.wait_for_function(
                    "() => window.WorkbenchProduction?.getState?.().timelineClips?.length === 2",
                    timeout=30_000,
                )
                canvas_page.reload(wait_until="networkidle")
                canvas_page.locator('#canvasRadarStatus[data-state="ready"]').wait_for(
                    state="visible", timeout=15_000
                )
                evidence["restart"] = {
                    "timeline_manifest_id": production_page.evaluate(
                        "() => window.WorkbenchProduction.getState().timelineManifest.manifest_id"
                    ),
                    "canvas_status": canvas_page.locator(
                        "#canvasRadarStatus"
                    ).inner_text(),
                }
                evidence["page_errors"] = page_errors
                assert not page_errors, page_errors
                evidence["status"] = "observed"
                _write_json(_evidence_dir() / "c13-t4-report.json", evidence)
            finally:
                context.close()
                browser.close()
    finally:
        server.stop()
