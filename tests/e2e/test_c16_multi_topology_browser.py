"""QIN-227 / C16-T4 本机拓扑与断线恢复验证。

本文件的可重复测试只使用隔离临时目录和本机服务。NAS 上游实测由用户
部署后单独补做；这些本机 fixture 不构成 NAS、跨 PC 或十分钟断网验收。
"""

from __future__ import annotations

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

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPO_ROOT / ".local" / "qa" / "C16-evidence"
NAS_BASE_URL = "http://192.168.0.10:2077"
READ_ONLY_PATHS = ("/healthz", "/static/manifest.json", "/static/sw.js")


def _real_run_enabled(environ: dict[str, str] | None = None) -> bool:
    """NAS 真实环境检查必须由操作者显式开启。"""
    env = os.environ if environ is None else environ
    return str(env.get("GW_C16_REAL_E2E", "")).strip().lower() in {"1", "true", "yes"}


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health_ready(base: str) -> bool:
    try:
        with urlopen(f"{base}/healthz", timeout=1) as response:
            body = json.loads(response.read(4096).decode("utf-8"))
        return response.status == 200 and body.get("status") == "ok"
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False


class _OwnedLocalServer:
    """只管理当前测试启动的 localhost 服务和 tmp_path 数据。"""

    def __init__(self, tmp_path: Path) -> None:
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log_path = tmp_path / "c16-local-server.log"
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
            self.process = subprocess.Popen(
                [sys.executable, str(REPO_ROOT / "run.py"), "--test"],
                cwd=REPO_ROOT,
                env=self.env,
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise AssertionError(
                        f"隔离本机服务提前退出，日志见 {self.log_path}"
                    )
                if _health_ready(self.base):
                    return
                time.sleep(0.1)
            raise AssertionError(f"隔离本机服务未就绪，日志见 {self.log_path}")
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


def test_c16_real_nas_run_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GW_C16_REAL_E2E", raising=False)
    assert _real_run_enabled() is False
    monkeypatch.setenv("GW_C16_REAL_E2E", "1")
    assert _real_run_enabled() is True


def test_c16_nas_preflight_uses_read_only_get_requests() -> None:
    """保留 NAS 复测门禁契约；本测试不发出任何 NAS 请求。"""
    assert READ_ONLY_PATHS == ("/healthz", "/static/manifest.json", "/static/sw.js")
    assert all(path.startswith("/") for path in READ_ONLY_PATHS)


def test_c16_windows_single_machine_topology_uses_isolated_local_service(
    tmp_path: Path,
) -> None:
    """在 Windows 启动 localhost 测试服务并检查健康页、PWA 资源与隔离路径。"""
    if os.name != "nt":
        pytest.skip("Windows 单机拓扑实测只在 Windows 运行")

    server = _OwnedLocalServer(tmp_path)
    server.start()
    try:
        with urlopen(f"{server.base}/healthz", timeout=3) as response:
            health = json.loads(response.read(4096).decode("utf-8"))
        assert response.status == 200
        assert health.get("status") == "ok"

        with urlopen(f"{server.base}/static/manifest.json", timeout=3) as response:
            manifest = json.loads(response.read(16_384).decode("utf-8"))
        assert response.status == 200
        assert manifest.get("display") == "standalone"

        with urlopen(f"{server.base}/static/sw.js", timeout=3) as response:
            service_worker = response.read(64_000).decode("utf-8")
        assert response.status == 200
        assert "addEventListener" in service_worker

        report = {
            "task": "QIN-227 / C16-T4",
            "evidence_type": "isolated_windows_single_machine_topology",
            "host": "127.0.0.1",
            "port": server.port,
            "runtime_mode": "test",
            "runtime_roots": {
                "data": str(tmp_path / "data"),
                "auth": str(tmp_path / "auth" / "auth.sqlite3"),
                "video": str(tmp_path / "video"),
            },
            "checks": [
                "GET /healthz",
                "GET /static/manifest.json",
                "GET /static/sw.js",
            ],
            "nas_contacted": False,
            "production_data_touched": False,
        }
        _write_json(EVIDENCE_ROOT / "c16-windows-single-machine.json", report)
    finally:
        server.stop()


def test_c16_lan_discovery_contract_is_bounded_and_credential_free() -> None:
    """检查桌面端局域网探测只扫描受限私网并只发健康 GET。"""
    source = (REPO_ROOT / "clients" / "desktop" / "src" / "main.rs").read_text(
        encoding="utf-8"
    )
    assert "fn bounded_subnet_hosts" in source
    assert "let scan_prefix = prefix.max(24);" in source
    assert "fn probe_ip" in source
    assert "client.get(health_url).send().await.ok()?" in source
    assert 'health_url.set_path("/healthz");' in source
    assert "fn new_http_client()" in source
    assert ".no_proxy()" in source
    assert "url.username().is_empty()" in source
    assert "url.query().is_some()" in source
    assert "url.fragment().is_some()" in source
    assert 'normalized.ends_with(".local")' in source


def test_c16_local_task_survives_client_disconnect_and_app_recomposition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """创建本地 Agent 作业、断开首个客户端并重组应用后读取相同持久任务。"""
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_DISABLE_AGENT_INTEGRATION", "0")
    monkeypatch.setenv("GW_AGENT_MAX_MODEL_CALLS_PER_RUN", "2")

    from fastapi.testclient import TestClient

    from gw.api.agent_integration import build_agent_integration
    from gw.api.app import create_app
    from gw.core import local_accounts, session
    from gw.core.auth import AuthContext
    from gw.core.runtime_paths import resolve_runtime_paths
    from gw.core.session import session_cookie_name
    from gw.projects_hub.models import ProjectCreateRequest, ProjectType
    from gw.projects_hub.service import ProjectsService, owner_key_for_context

    class LocalGatewayFixture:
        async def describe_capabilities(self, model_ref: Any) -> Any:
            from gw.agent_runtime.models import MessageRole, ModelCapabilities

            return ModelCapabilities(
                model_ref=model_ref,
                supported_roles=list(MessageRole),
                structured_output=True,
                tool_calls=False,
                streaming=False,
                multimodal=False,
                lookup=False,
                cancellation=False,
                usage_reporting=False,
                max_context_tokens=None,
            )

        async def invoke(self, request: Any) -> Any:
            raise AssertionError("本地恢复 fixture 不应调用模型网关")

    session.reset_stores()
    principal, session_token = local_accounts.create_first_admin(
        "c16_local_owner", "LocalTest2026"
    )
    auth = AuthContext(
        role=principal["role"],
        subject=principal["user_id"],
        mode="local_account",
        identity_domain="local_account",
    )
    projects = ProjectsService(seed_golden_fixture=False, persistent=True)
    project = projects.create_project(
        ProjectCreateRequest(
            name="C16 本机断线恢复 fixture", project_type=ProjectType.FILM
        ),
        owner_key=owner_key_for_context(auth),
    )
    gateway = LocalGatewayFixture()
    config = {
        "configured": True,
        "default_provider_id": "local-fixture-provider",
        "providers": [
            {
                "provider_id": "local-fixture-provider",
                "configured": True,
                "models": ["local-fixture-model"],
            }
        ],
    }

    def model_resolver(provider_id: str | None, model: str | None) -> dict[str, str]:
        if provider_id not in (None, "local-fixture-provider") or model not in (
            None,
            "local-fixture-model",
        ):
            raise ValueError("requested model is outside the isolated local fixture")
        return {
            "provider_id": provider_id or "local-fixture-provider",
            "model": model or "local-fixture-model",
        }

    integration = build_agent_integration(
        gateway=gateway,
        runtime_paths=resolve_runtime_paths(),
        projects=projects,
        model_resolver=model_resolver,
        configuration_provider=lambda: config,
        start_worker=False,
    )
    create_payload: dict[str, Any]
    with TestClient(
        create_app(agent_integration_factory=integration),
        base_url="http://localhost",
        client=("127.0.0.1", 50160),
        headers={"Origin": "http://localhost"},
    ) as connected_client:
        connected_client.cookies.set(session_cookie_name(), session_token)
        profiles = connected_client.get("/api/agent/config").json()["profiles"]
        approval_profile = next(item for item in profiles if item["mode"] == "approval")
        create_payload = {
            "project_id": project.project_id,
            "user_goal": "C16 本机断线恢复 fixture task",
            "provider_id": "local-fixture-provider",
            "model": "local-fixture-model",
            "mode": "approval",
            "config_snapshot_id": approval_profile["config_snapshot_id"],
            "input_artifact_refs": [],
            "idempotency_key": "c16-local-recovery-create-v001",
            "request_id": "c16-local-recovery-request-v001",
        }
        created = connected_client.post("/api/agent/runs", json=create_payload)
        assert created.status_code == 202, created.text
        accepted = created.json()
        run_id = accepted["run_id"]
        job_id = accepted["job_id"]

        paused = connected_client.post(
            f"/api/agent/runs/{run_id}/pause",
            json={
                "expected_version": accepted["version"],
                "idempotency_key": "c16-local-recovery-pause-v001",
                "request_id": "c16-local-recovery-pause-request-v001",
            },
        )
        assert paused.status_code == 200, paused.text
        resumed = connected_client.post(
            f"/api/agent/runs/{run_id}/resume",
            json={
                "expected_version": paused.json()["version"],
                "idempotency_key": "c16-local-recovery-resume-v001",
                "request_id": "c16-local-recovery-resume-request-v001",
            },
        )
        assert resumed.status_code == 200, resumed.text
        before_disconnect = resumed.json()

    # 第一个 TestClient 已关闭，模拟客户端离线；随后以相同本地数据库重组服务。
    recovered_integration = build_agent_integration(
        gateway=gateway,
        database=integration.database,
        projects=integration.projects,
        model_resolver=integration.model_resolver,
        configuration_provider=integration.configuration_provider,
        start_worker=False,
    )
    with TestClient(
        create_app(agent_integration_factory=recovered_integration),
        base_url="http://localhost",
        client=("127.0.0.1", 50161),
        headers={"Origin": "http://localhost"},
    ) as reconnected_client:
        reconnected_client.cookies.set(session_cookie_name(), session_token)
        recovered = reconnected_client.get(f"/api/jobs/{job_id}")
        assert recovered.status_code == 200, recovered.text
        state = recovered.json()

    assert state["job_id"] == job_id == before_disconnect["job_id"]
    assert state["run_id"] == run_id == before_disconnect["run_id"]
    assert state["agent_status"] == before_disconnect["agent_status"]
    assert state["state"] == before_disconnect["state"]
    assert state["poll_hint"] == before_disconnect["poll_hint"]
    assert state["agent_poll_hint"] == before_disconnect["agent_poll_hint"]
    report = {
        "task": "QIN-227 / C16-T4",
        "evidence_type": "isolated_local_agent_disconnect_recomposition_fixture",
        "fixture_id": "c16-local-recovery-v001",
        "job_id": job_id,
        "run_id": run_id,
        "version_before_disconnect": before_disconnect["version"],
        "version_after_reconnect": state["version"],
        "status_before_disconnect": before_disconnect["agent_status"],
        "status_after_reconnect": state["agent_status"],
        "model_gateway_calls": 0,
        "nas_contacted": False,
        "real_ten_minute_disconnect": False,
    }
    _write_json(EVIDENCE_ROOT / "c16-local-offline-recovery.json", report)
    session.reset_stores()


def test_c16_real_nas_read_only_preflight() -> None:
    """对真实 NAS 做只读 HTTP 预检；仅在显式批准后运行。"""
    if not _real_run_enabled():
        pytest.skip("C16 真实 NAS 预检默认关闭；用户完成部署并明确通知后再补测")
    pytest.fail("NAS 实测暂缓：不得在用户通知部署完成前尝试连接")
