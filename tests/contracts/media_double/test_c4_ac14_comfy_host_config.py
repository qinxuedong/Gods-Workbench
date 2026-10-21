"""C4-AC14：open-in-comfy 的 ComfyUI 地址配置契约（全替身，无真实网络）。

覆盖口径：
- 未配置（主体设置 comfy_url 与环境变量 GW_COMFYUI_URL 均为空）→ 409
  COMFY_HOST_NOT_CONFIGURED，detail.settings_path 指向前端真实设置页；
  open-in-comfy 涉及的整条路径（含 _comfy_operation）绝不探测 127.0.0.1:8188。
- 已配置但连不上 → 503 COMFY_HOST_UNREACHABLE；启动中保留 COMFY_START_PENDING。
- GET /capabilities 顶层新增 comfy_host 如实报告配置状态，不做阻塞网络探测；
  executors.local_comfy 与 data_gaps 等既有字段保持向后兼容。
- GET /comfy-control 未配置时如实返回 unconfigured 状态而非猜测本地端口。
"""

from __future__ import annotations

import json

from media_doubles import HEADERS

WF = "/api/god_workflow"
#: 与 routes.COMFY_SETTINGS_PATH 一致：ComfyUI 地址在 workflow 页的
#: 「RunningHub 凭证与本地 ComfyUI 环境设置」弹窗（inputComfyUrl）中配置。
SETTINGS_PATH = "/static/pages/workflow.html"

CANVAS_DOCUMENT = {
    "nodes": [
        {
            "id": "1",
            "kind": "AcmeSource",
            "title": "Source",
            "inputs": {"text": "seed-value", "count": 7},
        },
    ],
    "connections": [],
}


def _unconfigure(monkeypatch):
    """清空全部地址来源：环境变量与（默认即空的）主体设置。"""
    monkeypatch.delenv("GW_COMFYUI_URL", raising=False)
    monkeypatch.delenv("GW_COMFYUI_ROOT_DIR", raising=False)
    monkeypatch.delenv("GW_COMFYUI_TIMEOUT_SECONDS", raising=False)


def _assert_no_default_host_probe(attempts) -> list[str]:
    urls = [str(url) for url in attempts]
    assert not any("127.0.0.1:8188" in url for url in urls), urls
    return urls


def test_open_in_comfy_unconfigured_returns_409_with_settings_path(
    media_client, no_network, monkeypatch
):
    """C4-AC14-OPEN-01：未配置 → 409 COMFY_HOST_NOT_CONFIGURED + settings_path；全程零网络请求。"""
    _unconfigure(monkeypatch)
    opened = media_client.post(f"{WF}/open-in-comfy", headers=HEADERS)
    assert opened.status_code == 409, opened.text
    detail = opened.json()["detail"]
    assert detail["code"] == "COMFY_HOST_NOT_CONFIGURED"
    assert detail["message"]
    assert detail["settings_path"] == SETTINGS_PATH
    assert no_network == []
    _assert_no_default_host_probe(no_network)


def test_open_in_comfy_unconfigured_with_document_queues_nothing(
    media_client, no_network, monkeypatch
):
    """C4-AC14-OPEN-02：带 workflow_id 且未配置 → 409，不排队导入指令、不产生任何网络请求。"""
    from gw.god_workflow import routes

    # 指令队列是模块级全局；先清掉其他用例遗留条目，断言才只反映本用例行为。
    monkeypatch.setattr(routes, "_PENDING_COMFY_COMMAND", {})
    _unconfigure(monkeypatch)
    saved = media_client.post(
        f"{WF}/documents?source=canvas", json=CANVAS_DOCUMENT, headers=HEADERS
    )
    assert saved.status_code == 201, saved.text
    workflow_id = saved.json()["workflow_id"]
    opened = media_client.post(f"{WF}/open-in-comfy/{workflow_id}", headers=HEADERS)
    assert opened.status_code == 409, opened.text
    assert opened.json()["detail"]["code"] == "COMFY_HOST_NOT_CONFIGURED"
    assert routes._PENDING_COMFY_COMMAND == {}
    poll = media_client.get(f"{WF}/comfy-bridge/poll", headers=HEADERS)
    assert poll.status_code == 200, poll.text
    assert poll.json() == {"command": "idle"}
    assert no_network == []
    _assert_no_default_host_probe(no_network)


def test_open_in_comfy_configured_but_offline_returns_503(
    media_client, no_network, monkeypatch
):
    """C4-AC14-OPEN-03：已配置 loopback 地址但连不上 → 503 COMFY_HOST_UNREACHABLE；
    探测只允许打向已配置地址，绝无 127.0.0.1:8188 默认回退。"""
    monkeypatch.setenv("GW_COMFYUI_URL", "http://127.0.0.1:9")
    opened = media_client.post(f"{WF}/open-in-comfy", headers=HEADERS)
    assert opened.status_code == 503, opened.text
    assert opened.json()["detail"]["code"] == "COMFY_HOST_UNREACHABLE"
    urls = _assert_no_default_host_probe(no_network)
    assert urls, "已配置地址应触发一次真实探测（由替身拦截）"
    assert all(url.startswith("http://127.0.0.1:9/") for url in urls), urls


def test_open_in_comfy_starting_keeps_comfy_start_pending(
    media_client, no_network, monkeypatch
):
    """C4-AC14-OPEN-04：宿主启动中 → 仍为 503 COMFY_START_PENDING，既有语义不变。"""
    from gw.god_workflow import routes

    monkeypatch.setenv("GW_COMFYUI_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(
        routes,
        "_comfy_operation",
        lambda *a, **k: {"ok": True, "status": "starting", "highlight": False},
    )
    opened = media_client.post(f"{WF}/open-in-comfy", headers=HEADERS)
    assert opened.status_code == 503, opened.text
    assert opened.json()["detail"]["code"] == "COMFY_START_PENDING"
    assert no_network == []


def test_comfy_operation_without_config_reports_unconfigured(
    media_client, no_network, monkeypatch
):
    """C4-AC14-CTRL-01：真实 _comfy_operation 未配置时如实返回 unconfigured，不猜测默认端口、不探测。"""
    _unconfigure(monkeypatch)
    from gw.god_workflow import routes

    context = routes._auth(HEADERS["Authorization"], "editor", False)
    result = routes._comfy_operation(context, "status")
    assert result["status"] == "unconfigured"
    assert result["highlight"] is False
    assert result["code"] == "COMFY_HOST_NOT_CONFIGURED"
    assert "127.0.0.1:8188" not in json.dumps(result, ensure_ascii=False)
    assert no_network == []


def test_comfy_control_endpoint_reports_unconfigured_state(
    media_client, no_network, monkeypatch
):
    """C4-AC14-CTRL-02：GET /comfy-control 未配置 → 200 且 status=unconfigured，而非对默认端口探测。"""
    _unconfigure(monkeypatch)
    status = media_client.get(f"{WF}/comfy-control", headers=HEADERS)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["status"] == "unconfigured"
    assert body["code"] == "COMFY_HOST_NOT_CONFIGURED"
    assert body["highlight"] is False
    assert no_network == []


def test_capabilities_reports_comfy_host_states_without_probing(
    media_client, no_network, monkeypatch
):
    """C4-AC14-CAP-01：capabilities 如实报告 comfy_host 两态且零探测；executors/data_gaps 向后兼容。"""
    _unconfigure(monkeypatch)
    body = media_client.get(f"{WF}/capabilities", headers=HEADERS).json()
    assert body["comfy_host"] == "not_configured"
    assert body["executors"]["local_comfy"] == "unavailable"
    assert "local_comfy_not_configured" in body["data_gaps"]
    assert no_network == []

    # 仅主体设置配置地址（清空环境变量）同样如实报告 configured。
    admin = {**HEADERS, "X-User-Role": "admin"}
    saved = media_client.post(
        f"{WF}/settings", json={"comfy_url": "http://127.0.0.1:8188"}, headers=admin
    )
    assert saved.status_code == 200, saved.text
    body = media_client.get(f"{WF}/capabilities", headers=HEADERS).json()
    assert body["comfy_host"] == "configured"
    assert body["executors"]["local_comfy"] == "configured"
    assert "local_comfy_not_configured" not in body["data_gaps"]
    assert no_network == []
    _assert_no_default_host_probe(no_network)
