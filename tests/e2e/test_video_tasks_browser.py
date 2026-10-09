"""视频生产页真实浏览器闭环：本地认证、HTTP Provider、FFmpeg 和下载。

仅允许回环测试服务；不调用商业 Provider，不读取真实账户配置。
"""
import importlib.util
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from gw.api.app import create_app
from playwright_support import open_playwright
from gw.settings import service as settings_service
from gw.video_tasks import service as video_module
from gw.video_tasks.service import VideoTaskService
from gw.video_tasks.store import VideoTaskStore

ROOT = Path(__file__).resolve().parents[2]


def _helpers():
    spec = importlib.util.spec_from_file_location("video_browser_fixtures", ROOT / "tests/contracts/episodes/test_video_tasks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_video_generate_preview_export_download_browser(monkeypatch, tmp_path):
    """通过真实页面按钮提交生成和合并，下载后用 ffprobe 验证实际媒体。"""
    browser_api = pytest.importorskip("playwright.sync_api")
    helpers = _helpers()
    source = helpers._make_clip(tmp_path / "source.mp4", size="96x64", duration="0.7")
    provider, provider_thread, provider_url, upstream = helpers._provider_server(source)
    # 本用例改由设置API持久配置驱动，验证管理页保存与视频执行共享真源。
    monkeypatch.delenv("GW_PROVIDER_RUNTIME_JSON", raising=False)
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_CLI_EXECUTION", "0")
    # 浏览器验证生产持久项目真源，不能沿用契约测试的内存黄金项目。
    from gw.projects_hub import service as project_module
    from gw.api import routes_projects, routes_asset_registry
    project_service = project_module.ProjectsService(seed_golden_fixture=False, persistent=True)
    for module in (project_module, routes_projects, routes_asset_registry, video_module):
        monkeypatch.setattr(module, "default_projects_service", project_service)

    isolated_providers = settings_service.ProviderService()
    from gw.api import routes_settings
    monkeypatch.setattr(routes_settings, "default_provider_service", isolated_providers)
    # 仅本测试实例放行回环产物下载，不修改生产 URL 安全策略或上下文鉴权。
    service = VideoTaskService(VideoTaskStore(tmp_path / "video"), allow_private_artifacts_for_test=True)
    monkeypatch.setattr(video_module, "_default_service", service)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    evidence = {"scope": "本地 Chrome + 回环 Provider；非商业 Provider 验收", "page_errors": []}
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        with open_playwright(browser_api) as pw:
            try:
                browser = pw.chromium.launch(channel="chrome", headless=True)
            except browser_api.Error as exc:
                pytest.skip(f"Chrome 不可启动：{type(exc).__name__}")
            try:
                context = browser.new_context(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
                page = context.new_page()
                page.on("pageerror", lambda error: evidence["page_errors"].append(str(error)))
                request = context.request

                def call(method, path, payload=None, expected=200):
                    response = request.fetch(base + path, method=method, data=payload,
                                             headers={"Origin": base})
                    assert response.status == expected, (path, response.status, response.text())
                    return response.json()

                call("POST", "/api/asset-auth/local/setup", {"username": "video_browser_owner", "password": "Test2026"}, 201)
                identity = call("POST", "/api/asset-auth/identity-binding", {}, 201)
                project = call("POST", "/api/asset-registry/projects", {"name": "视频浏览器验收", "project_type": "film"}, 201)["project"]
                project_id = project["project_id"]
                canvas = call("POST", "/api/canvases", {"project_id": project_id, "title": "视频验收画布", "mode": "classic"}, 201)["canvas"]
                canvas_id = canvas["canvas_id"]
                call("PATCH", f"/api/canvases/{canvas_id}", {"expected_version": canvas["version"],
                     "nodes": [{"entity_id": "video-browser-entity", "kind": "output"}], "connections": []})
                pipeline = call("POST", "/api/episode-pipelines", {"project_id": project_id,
                    "title": "浏览器出片"}, 201)
                revision = call("GET", "/api/providers")["revision"]
                call("PUT", f"/api/providers?expected_version={revision}", {"providers": [{"id": "video-test",
                     "name": "回环验收", "enabled": True, "base_url": provider_url,
                     "protocol": "openai", "video_protocol": "newapi_video", "api_key": "test-secret-do-not-log",
                     "video_models": ["video-test-model"]}]})
                # 页面本来就将逐镜草稿保存在 localStorage；只准备输入，不注入成功或任务状态。
                page.goto(base + "/healthz")
                key = "gwb_episode_workspace_v2:" + pipeline["pipeline_id"]
                page.evaluate("([key,value]) => localStorage.setItem(key, JSON.stringify(value))", [key, {
                    "videoScripts": [{"id": "browser-shot", "shotId": "S1", "title": "验收镜头",
                                      "prompt": "静态蓝色画面", "negative": "", "assets": ""}],
                    "models": {"video_render": {"provider_id": "video-test", "model": "video-test-model"}}}])
                page.goto(base + f"/static/episode-pipeline.html?project_id={project_id}&pipeline_id={pipeline['pipeline_id']}&step=audio_compose",
                          wait_until="networkidle")
                stage = page.locator("#pipelineIframe").element_handle().content_frame()
                assert stage is not None
                generate = stage.locator('[data-video-generate="browser-shot"]')
                (tmp_path / "video-page.html").write_text(page.content(), encoding="utf-8")
                page.screenshot(path=str(tmp_path / "video-initial.png"), full_page=True)
                generate.wait_for(state="visible", timeout=5000)
                assert generate.is_disabled(), "无 production_context 时必须先手动选择真实画布与实体"
                canvas_select = stage.locator('[data-video-context-canvas]')
                entity_select = stage.locator('[data-video-context-entity]')
                assert canvas_select.is_enabled()
                canvas_select.select_option(canvas_id)
                entity_select.wait_for(state="visible")
                stage.wait_for_function("() => document.querySelector('[data-video-context-entity]')?.options.length > 1")
                entity_select.select_option("video-browser-entity")
                stage.wait_for_function("() => document.querySelector('[data-video-generate=\"browser-shot\"]')?.disabled === false")
                assert canvas_select.input_value() == canvas_id
                assert entity_select.input_value() == "video-browser-entity"
                assert generate.is_enabled()
                with page.expect_response(lambda r: r.url == base + "/api/video-tasks" and r.request.method == "POST") as created:
                    generate.click()
                assert created.value.status == 202, created.value.text()
                generation_id = created.value.json()["job_id"]
                stage.wait_for_function("() => !!document.querySelector('[data-video-export-select]')", timeout=45000)
                preview = stage.locator(".video-script-card video")
                preview.evaluate("video => video.load()")
                stage.wait_for_function("() => document.querySelector('.video-script-card video')?.readyState >= 1")
                assert preview.evaluate("video => video.videoWidth") == 96
                with page.expect_download() as downloaded:
                    stage.get_by_role("link", name="下载视频", exact=True).click()
                output = tmp_path / "generation.mp4"
                downloaded.value.save_as(output)
                assert output.read_bytes() == source.read_bytes()
                stage.locator('[data-video-export-select="browser-shot"]').check()
                with page.expect_response(lambda r: r.url == base + "/api/video-exports" and r.request.method == "POST") as exported:
                    stage.locator("[data-video-export]").click()
                assert exported.value.status == 202, exported.value.text()
                export_id = exported.value.json()["job_id"]
                stage.get_by_role("link", name="下载合并视频", exact=True).wait_for(timeout=15000)
                with page.expect_download() as downloaded_export:
                    stage.get_by_role("link", name="下载合并视频", exact=True).click()
                merged = tmp_path / "export.mp4"
                downloaded_export.value.save_as(merged)
                import subprocess
                import shutil
                probe = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(merged)],
                                       capture_output=True, text=True, timeout=15, check=True)
                stream = next(item for item in json.loads(probe.stdout)["streams"] if item["codec_type"] == "video")
                assert (stream["width"], stream["height"], stream["codec_name"]) == (1280, 720, "h264")
                assert stream["r_frame_rate"] == "30/1"
                page.reload(wait_until="networkidle")
                stage = page.locator("#pipelineIframe").element_handle().content_frame()
                stage.get_by_role("link", name="下载合并视频", exact=True).wait_for()
                stage.wait_for_function("() => document.querySelector('[data-video-context-canvas]')?.value && document.querySelector('[data-video-context-entity]')?.value")
                assert stage.locator('[data-video-context-canvas]').input_value() == canvas_id
                assert stage.locator('[data-video-context-entity]').input_value() == "video-browser-entity"
                preference_key = f"gwb_video_production_context_v1:{identity['user_id']}:{project_id}:{pipeline['pipeline_id']}"
                preference = page.evaluate("key => JSON.parse(localStorage.getItem(key) || 'null')", preference_key)
                assert preference == {"project_id": project_id, "pipeline_id": pipeline["pipeline_id"],
                                      "canvas_id": canvas_id, "entity_id": "video-browser-entity"}
                assert call("GET", f"/api/video-tasks/{generation_id}")["status"] == "succeeded"
                assert call("GET", f"/api/video-tasks/{export_id}")["status"] == "succeeded"
                assert upstream["posts"] == 1, "刷新或本地导出不应重新计费生成"
                assert "本地任务已取消" not in stage.locator("body").inner_text(), "成功任务不可显示已取消误报"
                assert "上游任务可能继续运行或计费" not in stage.locator("body").inner_text(), "成功终态不应保留运行中风险提示"
                evidence.update(generation_job_id=generation_id, export_job_id=export_id,
                    upstream_posts=upstream["posts"], preview_width=96, downloaded_source_equal=True,
                    export_width=1280, export_height=720, export_fps="30/1", reload_readback=True,
                    selected_canvas_id=canvas_id, selected_entity_id="video-browser-entity",
                    preference_user_id=identity["user_id"])
                page.screenshot(path=str(tmp_path / "video-browser.png"), full_page=True)
                assert not evidence["page_errors"], evidence["page_errors"]
                context.close()
            finally:
                if "page" in locals() and not page.is_closed():
                    evidence["last_url"] = page.url
                    if "key" in locals():
                        evidence["workspace"] = page.evaluate("key => JSON.parse(localStorage.getItem(key) || '{}')", key)
                    if "stage" in locals() and stage is not None:
                        (tmp_path / "video-last.html").write_text(stage.content(), encoding="utf-8")
                    page.screenshot(path=str(tmp_path / "video-last.png"), full_page=True)
                browser.close()
    finally:
        (tmp_path / "video-browser-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        service.executor.shutdown(wait=True, cancel_futures=True)
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=5)
