"""Phase 12 已认证浏览器门禁的洁净室最小实现。

本工具只做本地隔离环境、内存 Cookie 工厂、状态摘要和门禁判定；不保存密码、令牌、
Cookie 值或浏览器 storage state，也不把 HTTP 200 自动解释成业务成功。
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
AUTH_STATUS_PATH = "/api/asset-auth/status"
HEALTH_PATH = "/api/observability/health"
BUSINESS_PATH = "/api/observability/tasks?limit=100"


def load_smoke_module() -> Any:
    """加载同目录冒烟模块，避免把工具目录变成运行期包。"""
    path = Path(__file__).with_name("frontend_e2e_smoke.py")
    spec = importlib.util.spec_from_file_location("gw_frontend_e2e_smoke", path)
    if spec is None or spec.loader is None:
        raise ImportError("无法加载前端冒烟模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def isolated_server_environment(source: dict[str, str], port: int, root: str | os.PathLike[str]) -> dict[str, str]:
    """建立仅用于本地门禁的环境，移除凭据和代理变量。"""
    env = dict(source)
    for key in list(env):
        upper = key.upper()
        if upper in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "OPENAI_API_KEY"}:
            env.pop(key, None)
        elif any(token in upper for token in ("TOKEN", "SECRET", "PASSWORD", "COOKIE", "API_KEY")):
            env.pop(key, None)
    base = Path(root).resolve()
    env.update({
        "GW_AUTH_MODE": "local_account",
        "GW_PORT": str(int(port)),
        "GW_CLI_EXECUTION": "0",
        "GW_LOCAL_AUTH_DB": str(base / "auth.sqlite3"),
        "GW_DATA_DIR": str(base / "data"),
    })
    return env


def summarize_gpu(payload: dict[str, Any]) -> dict[str, Any]:
    """仅接受真实遥测或明确未集成状态。"""
    checks = payload.get("checks") if isinstance(payload, dict) else None
    check = next((item for item in checks or [] if isinstance(item, dict) and item.get("name") == "gpu_telemetry"), None)
    if not isinstance(check, dict):
        return {"state": "invalid", "status": "missing"}
    status = check.get("status")
    if status == "not_integrated":
        return {"state": "unavailable", "status": "not_integrated"}
    metrics = check.get("metrics")
    if status != "ok" or not isinstance(metrics, dict):
        return {"state": "invalid", "status": status}
    count = metrics.get("gpu_count")
    utilization = metrics.get("gpu_utilization_percent")
    memory = metrics.get("gpu_memory_percent")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return {"state": "invalid", "status": "invalid_metrics"}
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (utilization, memory)):
        return {"state": "invalid", "status": "invalid_metrics"}
    return {
        "state": "real",
        "source": "nvidia-smi",
        "gpu_count": count,
        "gpu_utilization_percent": utilization,
        "gpu_memory_percent": memory,
    }


def validate_gate(
    pages: list[str] | tuple[str, ...],
    e2e: dict[str, Any],
    probes: list[dict[str, Any]],
    events: list[dict[str, Any]],
    mutations: list[str] | tuple[str, ...],
) -> list[str]:
    """基于认证、业务、GPU 和页面证据生成失败列表。"""
    failures: list[str] = []
    if not pages:
        failures.append("未提供可验证页面")
    if e2e.get("failures"):
        failures.append("端到端页面检查存在失败")
    for probe in probes:
        if probe.get("auth_http_status") != 200 or probe.get("authenticated") is not True:
            failures.append("未证明真实管理员会话 200")
        if probe.get("business_api_http_status") != 200 or probe.get("business_api_payload_valid") is not True:
            failures.append("受保护业务 API 未返回有效 200")
        gpu_status = probe.get("gpu_health_http_status")
        if gpu_status == 401:
            failures.append("仍有 API 返回 401")
        elif gpu_status != 200:
            failures.append(f"GPU API 状态异常：{gpu_status}")
        gpu = probe.get("gpu") or {}
        if gpu.get("state") not in {"real", "unavailable"}:
            failures.append("GPU 状态不是真实值或明确未集成")
        if (probe.get("gpu_ui") or {}).get("matches_backend") is not True:
            failures.append("GPU UI 与后端状态不一致")
        if probe.get("page_errors"):
            failures.append("页面存在运行时错误")
    by_path: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        path = event.get("path")
        if isinstance(path, str):
            by_path.setdefault(path.split("?", 1)[0], []).append(event)
    auth_events = by_path.get(AUTH_STATUS_PATH, [])
    if not any(item.get("status") == 200 and item.get("authenticated") is True for item in auth_events):
        failures.append("未证明真实管理员会话 200")
    business_events = by_path.get(BUSINESS_PATH.split("?", 1)[0], [])
    if not any(item.get("status") == 200 for item in business_events):
        failures.append("受保护业务 API 未返回有效 200")
    if any(item.get("status") == 401 for item in events if item.get("path") in {AUTH_STATUS_PATH, BUSINESS_PATH.split("?", 1)[0]}):
        failures.append("仍有 API 返回 401")
    return failures


def _public_auth_summary(auth: dict[str, Any], logout_status: int | None, session_rows_after_logout: int) -> dict[str, Any]:
    """生成不带敏感值的认证摘要。"""
    allowed = {"setup_http_status", "login_http_status", "principal_role", "cookie_http_only", "cookie_hidden_from_document"}
    summary = {key: auth[key] for key in allowed if key in auth}
    summary.update({
        "logout_http_status": logout_status,
        "browser_storage_state_written": False,
        "server_session_rows_after_logout": int(session_rows_after_logout),
    })
    return summary


def choose_ephemeral_port() -> int:
    """申请一个系统临时端口，不使用产品默认端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    return port if port != 2077 else choose_ephemeral_port()


def choose_artifacts_dir(path: str | os.PathLike[str]) -> Path:
    """只允许将门禁产物放在仓库之外。"""
    target = Path(path).expanduser().resolve()
    try:
        target.relative_to(ROOT)
    except ValueError:
        target.mkdir(parents=True, exist_ok=True)
        return target
    raise ValueError("输出目录必须位于仓库之外")


def create_authenticated_context_factory(
    base_url: str,
    session_value: str,
    events: list[dict[str, Any]],
    phase: list[str],
) -> Callable[..., Any]:
    """创建只在内存中注入 HttpOnly 会话 Cookie 的 Playwright 工厂。"""
    if not isinstance(session_value, str) or not session_value:
        raise ValueError("会话值不能为空")

    def factory(browser: Any, **kwargs: Any) -> Any:
        context = browser.new_context(**kwargs)
        context.add_cookies([{
            "name": "gw_session",
            "value": session_value,
            "url": base_url,
            "httpOnly": True,
            "secure": base_url.startswith("https://"),
            "sameSite": "Strict",
        }])
        return context

    return factory


def summarize_api_events(pages: tuple[str, ...] | list[str], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按页面汇总状态码和失败路径，不保留响应正文。"""
    rows: list[dict[str, Any]] = []
    for page in pages:
        relevant = [event for event in events if event.get("page") == page]
        success: list[str] = []
        failures: list[dict[str, Any]] = []
        for event in relevant:
            path = event.get("path")
            status = event.get("status")
            if not isinstance(path, str) or not isinstance(status, int):
                continue
            if 200 <= status < 300:
                if path not in success:
                    success.append(path)
            else:
                failures.append({"phase": event.get("phase"), "method": event.get("method"), "path": path, "status": status})
        rows.append({"page": page, "successful_paths": success, "failures": failures})
    return rows


def team_message_workflow_verified(result: dict[str, Any]) -> bool:
    """团队消息工作流只有在每一项运行证据齐全时才算通过。"""
    required = {
        "passed", "seeded_messages", "page_errors", "history_after_incremental_verified",
        "ui_send_verified", "reload_readback_verified", "text_only_rendering_verified",
    }
    if not required.issubset(result):
        return False
    return (
        result.get("passed") is True
        and isinstance(result.get("seeded_messages"), int)
        and result["seeded_messages"] > 0
        and result.get("page_errors") == []
        and all(result.get(key) is True for key in required - {"passed", "seeded_messages", "page_errors"})
    )


def main() -> int:
    """命令行入口仅输出安全摘要。"""
    print(json.dumps({"status": "tool_ready", "default_port": 2077, "real_cli_enabled": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
