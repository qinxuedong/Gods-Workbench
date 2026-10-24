"""C15 本地 Master MP4 渲染与 Chrome 回播检查。

本测试只证明本地 FFmpeg 渲染与浏览器解码；合成素材不代表真实样片，
也不构成 C15-AC4 全链路验收。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright_support import open_playwright

from gw.api.app import create_app

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ENV = "GW_C15_LOCAL_RENDER_EVIDENCE_DIR"
OPT_IN_ENV = "GW_C15_LOCAL_RENDER_E2E"
PROVIDER_ENV_VARS = (
    "GW_PROVIDER_RUNTIME_JSON",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "RUNNINGHUB_API_KEY",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _call(request, base: str, method: str, route: str, *, expected: int = 200, **kwargs):
    headers = {"Origin": base}
    if "json" in kwargs:
        kwargs["data"] = kwargs.pop("json")
        headers["Content-Type"] = "application/json"
    response = request.fetch(base + route, method=method, headers=headers, **kwargs)
    assert response.status == expected, f"{route}: HTTP {response.status}: {response.text()}"
    return response


def _evidence_dir(tmp_path: Path) -> Path:
    raw = os.environ.get(EVIDENCE_ENV, "").strip()
    if not raw:
        return tmp_path / "evidence"
    evidence_dir = Path(raw).expanduser().resolve()
    expected = (ROOT / ".local" / "qa" / "C15-evidence").resolve()
    assert evidence_dir == expected, f"证据只允许写入 {expected}"
    return evidence_dir


def _write_report(evidence_dir: Path, evidence: dict) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    json_path = evidence_dir / "C15-local-render-evidence.json"
    report_path = evidence_dir / "C15-local-render-report.md"
    json_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(
        "# C15 本地渲染验证记录\n\n"
        "本记录仅覆盖隔离测试数据上的本地 FFmpeg Master MP4 导出与 Chrome 回播。"
        "输入视频由 FFmpeg lavfi 在测试中合成；未调用视频生成服务或商业 Provider。\n\n"
        "**C15-AC4：暂不验收。** 本记录不证明剧本、分镜、真实样片、控时长视频生成及完整交付白皮书链路。\n\n"
        "## 实测结果\n\n"
        f"- 输入来源：{evidence['input_origin']}\n"
        f"- 输入 SHA-256：`{evidence['source_sha256']}`\n"
        f"- 输出 SHA-256：`{evidence['master_sha256']}`\n"
        f"- 输出规格：{evidence['width']}x{evidence['height']}，{evidence['video_codec']}，"
        f"{evidence['duration_seconds']:.3f}s，编码器 `{evidence['encoder']}`\n"
        f"- Chrome 回播：videoWidth={evidence['browser_video_width']}，"
        f"readyState={evidence['browser_ready_state']}，播放进度={evidence['browser_current_time']:.3f}s\n"
        f"- 截图：`{evidence['screenshot']}`\n"
        f"- Provider 调用：{evidence['provider_calls']}\n",
        encoding="utf-8",
    )


def test_c15_local_master_render_plays_in_chrome(monkeypatch, tmp_path):
    """用合成输入走本地注册、时间线、FFmpeg 导出、下载和 Chrome 解码。"""
    if os.environ.get(OPT_IN_ENV) != "1":
        pytest.skip(f"设置 {OPT_IN_ENV}=1 才运行本地 FFmpeg + Chrome 检查")

    browser_api = pytest.importorskip("playwright.sync_api")
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "显式启用本地渲染验证时必须安装 ffmpeg 与 ffprobe"

    for key in PROVIDER_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_DATA_DIR", str((tmp_path / "data").resolve()))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str((tmp_path / "auth" / "auth.sqlite3").resolve()))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str((tmp_path / "video").resolve()))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(tmp_path.resolve()))
    monkeypatch.setenv("GW_CLI_EXECUTION", "0")

    source = tmp_path / "synthetic-source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24",
            "-t",
            "0.8",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    from gw.api import routes_asset_registry, routes_projects
    from gw.projects_hub import service as project_module

    projects = project_module.ProjectsService(seed_golden_fixture=False, persistent=True)
    monkeypatch.setattr(project_module, "default_projects_service", projects)
    monkeypatch.setattr(routes_projects, "default_projects_service", projects)
    monkeypatch.setattr(routes_asset_registry, "default_projects_service", projects)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    browser = None
    evidence_dir = _evidence_dir(tmp_path)
    screenshot = evidence_dir / "C15-local-render-playback.png"
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "本地隔离测试服务未启动"

        with open_playwright(browser_api) as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            request = context.request
            _call(request, base, "POST", "/api/asset-auth/local/setup", expected=201,
                  data={"username": "c15_local_render", "password": "Test2026"})
            _call(request, base, "POST", "/api/asset-auth/identity-binding", expected=201, data={})

            project_response = _call(
                request,
                base,
                "POST",
                "/api/asset-registry/projects",
                expected=201,
                data={"name": "C15 本地渲染检查", "project_type": "film"},
            ).json()
            project_id = project_response["project"]["project_id"]
            asset_response = _call(
                request,
                base,
                "POST",
                "/api/asset-registry/assets/import",
                expected=200,
                multipart={"file": {"name": source.name, "mimeType": "video/mp4", "buffer": source.read_bytes()},
                           "name": source.name},
            ).json()
            asset_id = asset_response["asset"]["asset_id"]
            _call(
                request,
                base,
                "PATCH",
                f"/api/asset-registry/assets/{asset_id}",
                json={"expected_version": asset_response["revision"], "display_path": str(source.resolve())},
            )
            pipeline = _call(
                request,
                base,
                "POST",
                "/api/episode-pipelines",
                expected=201,
                data={"project_id": project_id, "title": "C15 本地渲染检查"},
            ).json()
            pipeline_id = pipeline["pipeline_id"]
            _call(
                request,
                base,
                "POST",
                f"/api/episode-pipelines/{pipeline_id}/timeline-manifest",
                expected=201,
                data={"tracks": [{"track_id": "video-01", "kind": "video", "clips": [
                    {"shot_id": "SC01_SH001", "asset_id": asset_id, "at": 0, "duration": 0.8}
                ]}]},
            )
            exported = _call(
                request,
                base,
                "POST",
                f"/api/episode-pipelines/{pipeline_id}/export",
                data={"use_hardware_acceleration": False},
            ).json()
            downloaded = request.get(base + "/api/download-output", params={"path": exported["output"]})
            assert downloaded.status == 200, downloaded.text()

            master = tmp_path / "master.mp4"
            master.write_bytes(downloaded.body())
            assert _sha256(master) == exported["sha256"]
            probe = subprocess.run(
                [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(master)],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            probed = json.loads(probe.stdout)
            video_stream = next(stream for stream in probed["streams"] if stream["codec_type"] == "video")
            assert video_stream["codec_name"] == "h264"
            assert (video_stream["width"], video_stream["height"]) == (1920, 1080)
            assert exported["encoder"] == "libx264"

            player = tmp_path / "player.html"
            player.write_text(
                '<!doctype html><meta charset="utf-8"><style>'
                'body{margin:0;background:#111;display:grid;place-items:center;height:100vh}'
                'video{width:min(96vw,1152px);background:#000}</style>'
                '<video id="master" controls muted playsinline src="master.mp4"></video>',
                encoding="utf-8",
            )
            page = context.new_page()
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(player.as_uri(), wait_until="load")
            page.wait_for_function(
                "() => { const v = document.querySelector('#master'); return v && v.readyState >= 2 && v.videoWidth > 0; }",
                timeout=10000,
            )
            page.locator("#master").evaluate("video => video.play()")
            page.wait_for_function("() => document.querySelector('#master').currentTime >= 0.2", timeout=5000)
            browser_metrics = page.locator("#master").evaluate(
                "video => ({width: video.videoWidth, height: video.videoHeight, readyState: video.readyState, "
                "currentTime: video.currentTime, paused: video.paused})"
            )
            assert browser_metrics["width"] == 1920
            assert browser_metrics["height"] == 1080
            assert browser_metrics["readyState"] >= 2
            assert browser_metrics["currentTime"] >= 0.2
            assert not browser_metrics["paused"]
            assert not page_errors, page_errors

            evidence = {
                "evidence_id": "c15-local-render-" + _sha256(master)[:12],
                "input_origin": "测试通过 FFmpeg lavfi 合成；不是真实样片或 Provider 产物",
                "provider_calls": 0,
                "ac4_accepted": False,
                "pipeline_id": pipeline_id,
                "asset_id": asset_id,
                "source_sha256": _sha256(source),
                "master_sha256": _sha256(master),
                "bytes": exported["bytes"],
                "duration_seconds": exported["duration_seconds"],
                "width": exported["width"],
                "height": exported["height"],
                "video_codec": exported["video_codec"],
                "audio_codec": exported["audio_codec"],
                "encoder": exported["encoder"],
                "hardware_accelerated": exported["hardware_accelerated"],
                "browser_video_width": browser_metrics["width"],
                "browser_video_height": browser_metrics["height"],
                "browser_ready_state": browser_metrics["readyState"],
                "browser_current_time": browser_metrics["currentTime"],
                "browser_paused": browser_metrics["paused"],
                "screenshot": screenshot.name,
                "page_errors": page_errors,
            }
            evidence_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            _write_report(evidence_dir, evidence)
            context.close()
            browser.close()
            browser = None
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "本地隔离测试服务未正常退出"
