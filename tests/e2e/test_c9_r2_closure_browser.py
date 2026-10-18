# -*- coding: utf-8 -*-
"""C9-T6：真实单镜头审阅与交付的浏览器收口证据。

该用例只在明确设置 ``GW_C9_REAL_E2E=1`` 且提供真实 RunningHub 配置时运行。
没有配置时保持跳过，避免把替身、演示响应或仓库内 fixture 误报为 C9-AC5/AC6
的真实证据。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from playwright_support import open_playwright


REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPO_ROOT / ".local" / "qa" / "C9-evidence"
PASSWORD_OWNER = "Owner2026"
PASSWORD_REVIEWER = "Reviewer2026"


def _real_run_enabled(environ: dict[str, str] | None = None) -> bool:
    """真实宿主门禁必须由操作者显式开启。"""
    env = os.environ if environ is None else environ
    return str(env.get("GW_C9_REAL_E2E", "")).strip().lower() in {"1", "true", "yes"}


def _safe_config(environ: dict[str, str] | None = None) -> dict[str, str]:
    """读取非敏感配置；绝不把 API key 写入返回值或证据。"""
    env = os.environ if environ is None else environ
    workflow_id = str(env.get("GW_C9_RUNNINGHUB_WORKFLOW_ID", "")).strip()
    workflow_path = str(env.get("GW_C9_WORKFLOW_JSON", "")).strip()
    if not workflow_path:
        workflow_path = str(env.get("GW_C9_WORKFLOW_PATH", "")).strip()
    return {
        "provider": "runninghub",
        "api_key_configured": bool(str(env.get("GW_RUNNINGHUB_API_KEY", "")).strip()),
        "workflow_id": workflow_id,
        "workflow_path": workflow_path,
        "domain": str(env.get("GW_RUNNINGHUB_DOMAIN", "www.runninghub.cn")).strip()
        or "www.runninghub.cn",
    }


def _require_real_config() -> dict[str, object]:
    """显式运行时校验真实宿主配置，缺配置只跳过，不伪造成功。"""
    if not _real_run_enabled():
        pytest.skip("C9 真实证据默认关闭；设置 GW_C9_REAL_E2E=1 后才会触发 RunningHub")
    config = _safe_config()
    if not config["api_key_configured"]:
        pytest.skip("缺少 GW_RUNNINGHUB_API_KEY，未触发真实 RunningHub 请求")
    workflow_id = str(config["workflow_id"])
    if not workflow_id:
        pytest.skip("缺少 GW_C9_RUNNINGHUB_WORKFLOW_ID，无法确认真实 RunningHub 模板")
    if not re.fullmatch(r"\d{10,25}", workflow_id):
        raise AssertionError("GW_C9_RUNNINGHUB_WORKFLOW_ID 必须是 10-25 位数字")
    workflow_path = Path(str(config["workflow_path"])).expanduser()
    if not workflow_path.is_absolute():
        workflow_path = (REPO_ROOT / workflow_path).resolve()
    if not workflow_path.is_file():
        pytest.skip("缺少 GW_C9_WORKFLOW_JSON，未提供真实 Comfy API workflow 导出文件")
    try:
        raw = json.loads(workflow_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AssertionError(f"真实 workflow JSON 不可读取：{workflow_path}") from exc
    if isinstance(raw, dict) and isinstance(raw.get("prompt"), dict):
        raw = raw["prompt"]
    elif isinstance(raw, dict) and isinstance(raw.get("workflow"), dict):
        raw = raw["workflow"]
    if not isinstance(raw, dict) or not raw:
        raise AssertionError("GW_C9_WORKFLOW_JSON 必须是非空 Comfy API prompt 对象")
    if any(
        not isinstance(node, dict)
        or not isinstance(node.get("class_type"), str)
        or not isinstance(node.get("inputs"), dict)
        for node in raw.values()
    ):
        raise AssertionError("GW_C9_WORKFLOW_JSON 含不完整节点，必须提供真实 API prompt")
    config["workflow_path"] = str(workflow_path)
    config["prompt"] = raw
    return config


def _evidence_dir() -> Path:
    configured = str(os.environ.get("GW_C9_EVIDENCE_DIR", "")).strip()
    path = Path(configured).expanduser() if configured else EVIDENCE_ROOT
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _screenshot(page, name: str) -> None:
    _evidence_dir().mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(_evidence_dir() / name))


def test_c9_real_gate_is_closed_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("GW_C9_REAL_E2E", raising=False)
    assert _real_run_enabled() is False


def test_c9_safe_config_does_not_echo_runninghub_secret(monkeypatch):
    monkeypatch.setenv("GW_RUNNINGHUB_API_KEY", "secret-must-not-be-recorded")
    monkeypatch.setenv("GW_C9_RUNNINGHUB_WORKFLOW_ID", "2108837566177959938")
    safe = _safe_config()
    assert safe["api_key_configured"] is True
    assert "secret-must-not-be-recorded" not in json.dumps(safe)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health_ready(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
            body = json.loads(response.read(4096).decode("utf-8"))
        return response.status == 200 and body.get("status") == "ok" and body.get("mode") == "cleanroom"
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False


class _OwnedServer:
    """启动/重启本次测试自己的服务进程，绝不触碰默认开发或生产端口。"""

    def __init__(self, tmp_path: Path):
        self.root = REPO_ROOT
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log_path = tmp_path / "c9-t6-server.log"
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
                "PYTHONPATH": str(self.root),
            }
        )
        for name in ("GW_PROVIDER_RUNTIME_JSON", "GW_CHAT_API_KEY", "GW_CHAT_BASE_URL", "GW_CLI_EXECUTION"):
            self.env.pop(name, None)
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab")
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.process = subprocess.Popen(
                [sys.executable, str(self.root / "run.py"), "--test"],
                cwd=self.root,
                env=self.env,
                stdout=log,
                stderr=log,
                creationflags=creationflags,
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise AssertionError(f"C9 测试服务提前退出，日志见 {self.log_path}")
                if _health_ready(self.port):
                    return
                time.sleep(0.1)
            raise AssertionError(f"C9 测试服务未就绪，日志见 {self.log_path}")
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


def _route_same_origin(route, base: str) -> None:
    if route.request.url.startswith(base + "/"):
        route.continue_()
    else:
        route.abort()


def _assert_status(response, expected: int) -> dict:
    assert response.status == expected, response.text()
    return response.json() if response.text() else {}


def _create_second_account() -> None:
    from gw.core import local_accounts

    salt = secrets.token_bytes(16)
    with local_accounts.database() as db:
        db.execute(
            "INSERT INTO local_users(user_id, username, salt, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            (
                "usr-c9-t6-reviewer",
                "reviewer_c9_t6",
                salt,
                local_accounts.password_hash(PASSWORD_REVIEWER, salt),
                "editor",
            ),
        )


def _production_url(base: str, project_id: str) -> str:
    return f"{base}/static/pages/production.html?project_id={project_id}"


def _storyboard_url(base: str, project_id: str) -> str:
    return f"{base}/static/pages/storyboard.html?project_id={project_id}&view=storyboard"


def _wait_for_review_page(page, state: str = "pending_review") -> None:
    page.wait_for_function("!window.GWAuthGate || window.GWAuthGate.loaded === true")
    page.wait_for_selector(f"#productionReviewPanel [data-production-review-state='{state}']", timeout=30_000)


def test_c9_real_single_shot_review_delivery_browser(monkeypatch, tmp_path):
    """C9-AC5/AC6：真实 RunningHub 结果进入审阅、交付，并跨账户受控回读。"""
    config = _require_real_config()
    browser_api = pytest.importorskip("playwright.sync_api")

    # 进程外服务与本测试进程使用同一组隔离路径，便于创建第二个账号而不触碰
    # 其他测试或开发态账户库。
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))

    server = _OwnedServer(tmp_path)
    server.start()
    browser = None
    context_owner = None
    context_reviewer = None
    storyboard_page = None
    evidence = _evidence_dir()
    try:
        with open_playwright(browser_api) as playwright:
            try:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
            except browser_api.Error as exc:
                pytest.skip(f"Chrome 不可启动：{exc}")

            context_owner = browser.new_context(viewport={"width": 1800, "height": 1000})
            context_owner.route("**/*", lambda route: _route_same_origin(route, server.base))
            setup = context_owner.request.post(
                f"{server.base}/api/asset-auth/local/setup",
                data={"username": "owner_c9_t6", "password": PASSWORD_OWNER},
                headers={"Origin": server.base},
            )
            _assert_status(setup, 201)
            _create_second_account()

            owner_page = context_owner.new_page()
            page_errors: list[str] = []
            owner_page.on("pageerror", lambda error: page_errors.append(str(error)))
            owner_page.on("dialog", lambda dialog: dialog.dismiss())

            project_body = _assert_status(
                context_owner.request.post(
                    f"{server.base}/api/asset-registry/projects",
                    data={"name": "C9 T6 real browser project", "project_type": "film"},
                    headers={"Origin": server.base},
                ),
                201,
            )
            project = project_body["project"]
            project_id = project["project_id"]
            project_version = project["version"]
            canvas_body = _assert_status(
                context_owner.request.post(
                    f"{server.base}/api/canvases",
                    data={"project_id": project_id, "title": "SC01 real shot canvas", "mode": "classic"},
                    headers={"Origin": server.base},
                ),
                201,
            )
            canvas_id = canvas_body["canvas"]["canvas_id"]
            pipeline_body = _assert_status(
                context_owner.request.post(
                    f"{server.base}/api/episode-pipelines",
                    data={
                        "project_id": project_id,
                        "title": "C9 T6 real single shot",
                        "seed": "C9-T6-real",
                        "production_context": {
                            "episodes": [
                                {
                                    "episode_id": "EP01",
                                    "code": "EP01",
                                    "name": "Real episode",
                                    "scenes": [
                                        {
                                            "scene_id": "SC01",
                                            "code": "SC01",
                                            "name": "Real scene",
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
            pipeline_id = pipeline_body["pipeline_id"]
            shot_id = "SC01_SH001"
            generation_payload = {
                "target": "rh_cloud",
                "rh_workflow_id": config["workflow_id"],
                "prompt": config["prompt"],
                "node_info_list": [],
                "source_context": {
                    "project_id": project_id,
                    "project_version": project_version,
                    "canvas_id": canvas_id,
                    "entity_id": "entity-c9-t6-001",
                    "episode_id": "EP01",
                    "scene_id": "SC01",
                    "shot_id": shot_id,
                },
            }
            idempotency_key = "c9-t6-real-shot-001"
            generated = _assert_status(
                context_owner.request.post(
                    f"{server.base}/api/episode-pipelines/{pipeline_id}/shots/{shot_id}/generate",
                    data=generation_payload,
                    headers={"Origin": server.base, "Idempotency-Key": idempotency_key},
                ),
                202,
            )
            generation_shot = generated["shot"]
            assert generation_shot["status"] in {"queued", "running", "waiting"}
            _write_json(
                evidence / "generation_submitted.json",
                {
                    "provider": "runninghub",
                    "workflow_id": config["workflow_id"],
                    "project_id": project_id,
                    "pipeline_id": pipeline_id,
                    "shot_id": shot_id,
                    "job_id": generation_shot.get("job_id"),
                    "remote_task_id": generation_shot.get("remote_task_id"),
                    "status": generation_shot.get("status"),
                    "idempotency_key": idempotency_key,
                },
            )

            timeout = float(os.environ.get("GW_C9_REAL_E2E_TIMEOUT_SECONDS", "600"))
            deadline = time.monotonic() + max(timeout, 30.0)
            completed_shot = None
            shot_responses: list[dict] = []
            while time.monotonic() < deadline:
                response = _assert_status(
                    context_owner.request.get(
                        f"{server.base}/api/episode-pipelines/{pipeline_id}/shots/{shot_id}",
                        headers={"Origin": server.base},
                    ),
                    200,
                )
                current = response["shot"]
                shot_responses.append(current)
                if current.get("status") == "completed" and current.get("asset_id") and current.get("version_id"):
                    completed_shot = current
                    break
                if current.get("status") in {"failed", "unknown"}:
                    raise AssertionError(f"真实 RunningHub 镜头未成功：{json.dumps(current, ensure_ascii=False)}")
                time.sleep(5)
            assert completed_shot is not None, "真实 RunningHub 任务未在超时前形成已登记资产"
            _write_json(evidence / "shot_completed.json", {"responses": shot_responses, "final": completed_shot})

            owner_page.goto(_production_url(server.base, project_id), wait_until="networkidle")
            _wait_for_review_page(owner_page)
            assert owner_page.locator("#currentProjectDisplayTitle").inner_text().startswith("《C9 T6 real browser project》")
            assert owner_page.locator("#currentShotTitle").inner_text() == shot_id
            assert owner_page.locator("#productionReviewPanel").get_attribute("data-gw-degradation") is None
            _screenshot(owner_page, "owner_pending_review.png")

            comment_text = "通过精确真实版本，记录交付前检查。"
            owner_page.locator("#productionReviewComment").fill(comment_text)
            owner_page.get_by_role("button", name="评论", exact=True).click()
            owner_page.locator("[data-production-review-comments]").get_by_text(comment_text, exact=True).wait_for()
            owner_page.get_by_role("button", name="通过", exact=True).click()
            _wait_for_review_page(owner_page, "approved")
            owner_state = owner_page.evaluate("() => window.WorkbenchProduction.getState()")
            approval = owner_state["review"]["approvals"][-1]
            _screenshot(owner_page, "owner_approved_review.png")

            delivery_payload = {
                "title": "C9 T6 real delivery",
                "decision_id": approval["decision_id"],
                "version_id": completed_shot["version_id"],
                "version": completed_shot["version"],
                "text_artifact_id": f"artifact-text-{pipeline_id}",
                "settings_artifact_id": f"artifact-settings-{pipeline_id}",
                "media_artifact_id": completed_shot["asset_id"],
                "expected_version": owner_state["review"]["expectedVersion"],
            }
            delivery_body = owner_page.evaluate(
                """async ({sessionId, payload}) => {
                    const response = await fetch(`/api/asset-reviews/sessions/${encodeURIComponent(sessionId)}/delivery`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(payload)
                    });
                    return {status: response.status, body: await response.json()};
                }""",
                {"sessionId": owner_state["review"]["sessionId"], "payload": delivery_payload},
            )
            assert delivery_body["status"] == 201, delivery_body
            delivery = delivery_body["body"]["delivery"]
            for field, value in {
                "project_id": project_id,
                "shot_id": shot_id,
                "job_id": completed_shot["job_id"],
                "asset_id": completed_shot["asset_id"],
                "version_id": completed_shot["version_id"],
                "decision_id": approval["decision_id"],
            }.items():
                assert delivery[field] == value
            _write_json(evidence / "delivery_record.json", delivery_body["body"])

            # 分镜页必须读取同一条真实流水线与精确版本，并明确保留本期阻塞边界。
            storyboard_page = context_owner.new_page()
            storyboard_page.on("dialog", lambda dialog: dialog.dismiss())
            storyboard_page.goto(_storyboard_url(server.base, project_id), wait_until="networkidle")
            storyboard_page.wait_for_function("!window.GWAuthGate || window.GWAuthGate.loaded === true")
            storyboard_card = storyboard_page.locator("#storyboardGrid [data-storyboard-shot]").first
            storyboard_card.wait_for(timeout=30_000)
            assert storyboard_card.get_attribute("data-asset-id") == completed_shot["asset_id"]
            assert storyboard_card.locator("[data-storyboard-version]").inner_text() == completed_shot["version_id"]
            assert storyboard_card.locator("[data-storyboard-review-status]").inner_text() == "已通过"
            _screenshot(storyboard_page, "storyboard_approved_review.png")

            storyboard_page.evaluate("() => window.WorkbenchStoryboard.batchGenerate()")
            storyboard_page.locator("[data-storyboard-blocker='BATCH_RENDER_OUT_OF_SCOPE']").wait_for()
            _screenshot(storyboard_page, "storyboard_batch_blocked.png")
            storyboard_page.evaluate("() => window.WorkbenchStoryboard.exportPdf()")
            storyboard_page.locator("[data-storyboard-blocker='PDF_EXPORT_NOT_INTEGRATED']").wait_for()
            _screenshot(storyboard_page, "storyboard_pdf_blocked.png")

            # 通过真实 API 删除→回收→恢复同一资产，验证稳定 ID、引用与恢复回读。
            asset_body = _assert_status(
                context_owner.request.get(
                    f"{server.base}/api/asset-registry/assets/{completed_shot['asset_id']}",
                    headers={"Origin": server.base},
                ),
                200,
            )
            asset = asset_body["asset"]
            deleted_body = _assert_status(
                context_owner.request.delete(
                    f"{server.base}/api/asset-registry/assets/{completed_shot['asset_id']}",
                    data={"expected_version": asset["version"]},
                    headers={"Origin": server.base},
                ),
                200,
            )
            recycle_entry_id = deleted_body["recycle_entry_id"]
            assert deleted_body["asset_id"] == completed_shot["asset_id"]
            assert deleted_body["closure_status"] == "recovery_required"
            recycle_body = _assert_status(
                context_owner.request.get(
                    f"{server.base}/api/asset-registry/recycle-bin",
                    headers={"Origin": server.base},
                ),
                200,
            )
            assert any(item.get("recycle_entry_id") == recycle_entry_id for item in recycle_body["assets"])
            restored_body = _assert_status(
                context_owner.request.post(
                    f"{server.base}/api/asset-registry/governance/asset-trash/{recycle_entry_id}/restore",
                    headers={"Origin": server.base},
                ),
                200,
            )
            assert restored_body["restored"] is True
            assert restored_body["asset_id"] == completed_shot["asset_id"]
            restored_asset = _assert_status(
                context_owner.request.get(
                    f"{server.base}/api/asset-registry/assets/{completed_shot['asset_id']}",
                    headers={"Origin": server.base},
                ),
                200,
            )["asset"]
            assert restored_asset["asset_id"] == completed_shot["asset_id"]
            _write_json(
                evidence / "recycle_restore.json",
                {
                    "asset_id": completed_shot["asset_id"],
                    "version_id": completed_shot["version_id"],
                    "recycle_entry_id": recycle_entry_id,
                    "delete": {"closure_status": deleted_body["closure_status"], "next_action": deleted_body["next_action"]},
                    "restore": {"closure_status": restored_body["closure_status"], "next_action": restored_body["next_action"]},
                },
            )
            storyboard_page.reload(wait_until="networkidle")
            storyboard_page.locator("#storyboardGrid [data-storyboard-shot]").first.wait_for(timeout=30_000)
            _screenshot(storyboard_page, "storyboard_after_restore.png")

            owner_page.reload(wait_until="networkidle")
            _wait_for_review_page(owner_page, "approved")
            refreshed_state = owner_page.evaluate("() => window.WorkbenchProduction.getState()")
            assert refreshed_state["review"]["delivery"]["delivery_id"] == delivery["delivery_id"]
            _screenshot(owner_page, "owner_delivery_after_refresh.png")

            # 真正停止并重启本次服务进程，使用同一隔离数据目录回读关系。
            server.stop()
            server.start()
            owner_page.reload(wait_until="networkidle")
            _wait_for_review_page(owner_page, "approved")
            restarted_state = owner_page.evaluate("() => window.WorkbenchProduction.getState()")
            assert restarted_state["review"]["delivery"]["delivery_id"] == delivery["delivery_id"]
            _screenshot(owner_page, "owner_delivery_after_restart.png")

            context_reviewer = browser.new_context(viewport={"width": 1800, "height": 1000})
            context_reviewer.route("**/*", lambda route: _route_same_origin(route, server.base))
            login = context_reviewer.request.post(
                f"{server.base}/api/asset-auth/local/login",
                data={"username": "reviewer_c9_t6", "password": PASSWORD_REVIEWER},
                headers={"Origin": server.base},
            )
            _assert_status(login, 200)
            reviewer_read = context_reviewer.request.get(
                f"{server.base}/api/projects/{project_id}", headers={"Origin": server.base}
            )
            assert reviewer_read.status in {403, 404}
            reviewer_page = context_reviewer.new_page()
            reviewer_page.goto(_production_url(server.base, project_id), wait_until="networkidle")
            reviewer_page.wait_for_function("!window.GWAuthGate || window.GWAuthGate.loaded === true")
            reviewer_page.locator("#productionReviewPanel[data-gw-degradation='not_integrated']").wait_for()
            assert "未接入" in reviewer_page.locator("#productionReviewPanel").inner_text()
            _screenshot(reviewer_page, "reviewer_cross_account_blocked.png")
            _write_json(
                evidence / "cross_account.json",
                {"project_id": project_id, "reviewer_project_status": reviewer_read.status, "owner_delivery_id": delivery["delivery_id"]},
            )
            assert page_errors == []
            _write_json(
                evidence / "manifest.json",
                {
                    "task": "QIN-190",
                    "provider": "runninghub",
                    "workflow_id": config["workflow_id"],
                    "project_id": project_id,
                    "pipeline_id": pipeline_id,
                    "shot_id": shot_id,
                    "job_id": completed_shot["job_id"],
                    "asset_id": completed_shot["asset_id"],
                    "version_id": completed_shot["version_id"],
                    "delivery_id": delivery["delivery_id"],
                    "recycle_entry_id": recycle_entry_id,
                    "browser": "Chrome",
                    "cross_account_status": reviewer_read.status,
                    "real_api_key_value_recorded": False,
                    "out_of_scope": ["batch_render", "storyboard_pdf_export", "external_share"],
                },
            )
    finally:
        if storyboard_page is not None:
            storyboard_page.close()
        if context_reviewer is not None:
            context_reviewer.close()
        if context_owner is not None:
            context_owner.close()
        if browser is not None:
            browser.close()
        server.stop()

\n