"""C3-AC9：open-in-comfy 自动载入宿主 + comfy-bridge/sync-saved 回存 CAS 契约（ComfyUI 全替身，无网络）。

覆盖口径（cut 2026-10-13 10:38 G3 已批准 http://127.0.0.1:8188/）：
- open-in-comfy：宿主在线时写入宿主工作流目录、安装页面适配、登记待载入指令；
  宿主离线 / 非本机地址一律失败关闭，不写宿主目录。
- sync-saved：expected_version 正确 → 200 且版本 +1；过期 → 409 且原文档逐字节不变；
  未知语义（宿主缺节点定义、虚拟节点标识无效、宿主参数映射不匹配）→ 422 失败关闭，原文档不变。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gw.god_workflow import browser_bridge
from media_doubles import HEADERS, count_assets

WF = "/api/god_workflow"
COMFY_URL = "http://127.0.0.1:8188"
EXTENSION_URL = "/extensions/gods_workbench/workbench.js"

SOURCE_SCHEMA = {"input": {"required": {"text": ["STRING", {}], "count": ["INT", {}]}},
                 "input_order": {"required": ["text", "count"]}}
SINK_SCHEMA = {"input": {"required": {"images": ["IMAGE", {"forceInput": True}],
                                      "filename_prefix": ["STRING", {}]}},
               "input_order": {"required": ["images", "filename_prefix"]}}
OBJECT_INFO = {"AcmeSource": SOURCE_SCHEMA, "AcmeSink": SINK_SCHEMA}

CANVAS_DOCUMENT = {"nodes": [
    {"id": "1", "kind": "AcmeSource", "title": "Source", "inputs": {"text": "seed-value", "count": 7}},
    {"id": "2", "kind": "AcmeSink", "title": "Sink", "inputs": {"images": ["1", 0]}},
], "connections": []}
API_PROMPT = {"1": {"class_type": "AcmeSource", "inputs": {"text": "seed-value", "count": 7}},
              "2": {"class_type": "AcmeSink", "inputs": {"images": ["1", 0]}}}
HOST_GRAPH = {"version": 0.4, "nodes": [
    {"id": 1, "type": "AcmeSource", "pos": [40, 60], "inputs": [], "outputs": [],
     "widgets_values": ["seed-value", 7]},
    {"id": 2, "type": "AcmeSink", "pos": [360, 60],
     "inputs": [{"name": "images", "type": "IMAGE", "link": 1}], "outputs": [],
     "widgets_values": ["c3-ac9"]},
], "links": [[1, 1, 0, 2, 0, "IMAGE"]]}


@pytest.fixture
def comfy_host(tmp_path, monkeypatch):
    """本机 ComfyUI 宿主替身：loopback 地址 + 可安装页面适配的前端目录；在线探针打桩。"""
    root = tmp_path / "comfy-host"
    app_js = root / "python_embeded/Lib/site-packages/comfyui_frontend_package/static/scripts/app.js"
    app_js.parent.mkdir(parents=True)
    app_js.write_text("host-frontend-placeholder", encoding="utf-8")
    monkeypatch.setenv("GW_COMFYUI_URL", COMFY_URL)
    monkeypatch.setenv("GW_COMFYUI_ROOT_DIR", str(root))
    from gw.god_workflow import routes
    monkeypatch.setattr(routes, "_comfy_operation", lambda *a, **k: {"ok": True, "status": "online", "highlight": True})
    return root


@pytest.fixture
def object_info(monkeypatch):
    """宿主 /object_info 替身：在线，返回可注入的节点定义；record 记录探针次数。"""
    state = {"object_info": dict(OBJECT_INFO), "online": True, "count": 0}

    async def probe():
        state["count"] += 1
        return {"online": state["online"], "object_info": dict(state["object_info"])}

    from gw.god_workflow import comfy_executor
    monkeypatch.setattr(comfy_executor, "probe", probe)
    return state


def save_document(client):
    response = client.post(f"{WF}/documents?source=canvas", json=CANVAS_DOCUMENT, headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()["workflow"]


def host_file_bytes(root, saved_path):
    path = Path(saved_path)
    assert path.is_relative_to(root.resolve()), saved_path
    return path.read_bytes()


def sync_body(workflow_id, expected_version, *, resolve_definitions=False, **extra):
    body = {"workflow_id": workflow_id, "workflow": HOST_GRAPH, "expected_version": expected_version}
    if resolve_definitions:
        body.update({"resolve_definitions": True, "api_prompt": API_PROMPT,
                     "field_maps": {}, "virtual_node_ids": []})
    body.update(extra)
    return body


def test_open_in_comfy_writes_host_file_installs_adapter_and_queues_load(media_client, no_network, comfy_host, object_info):
    """C3-AC9-OPEN-01：自动载入的全部服务端要件落位；指令单次消费；离线探针不打桩。"""
    workflow = save_document(media_client)
    opened = media_client.post(f"{WF}/open-in-comfy/{workflow['workflow_id']}", headers=HEADERS)
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["ok"] is True and body["online"] is True
    assert body["workflow_id"] == workflow["workflow_id"]
    assert body["expected_version"] == 1
    assert body["workflow_format"] == "api"
    assert body["workflow_graph"] == API_PROMPT
    assert body["resolve_definitions"] is True
    assert body["browser_adapter"] == {"mode": "managed", "extension_url": EXTENSION_URL}
    assert body["workflow_file"] and body["saved_path"]
    # 1. 工作流文件已写入宿主工作流目录，内容即自动载入用的 API 图。
    on_disk = json.loads(host_file_bytes(comfy_host, body["saved_path"]))
    assert on_disk == API_PROMPT
    assert body["workflow_file"] in body["saved_path"]
    # 2. 页面适配脚本已提供到宿主前端目录，宿主核心 app.js 未被改写。
    extension = comfy_host / ("python_embeded/Lib/site-packages/comfyui_frontend_package"
                              "/static" + EXTENSION_URL)
    assert extension.read_bytes() == browser_bridge.SCRIPT.read_bytes()
    # 3. 待载入指令按主体排队，单次消费后转 idle。
    poll = media_client.get(f"{WF}/comfy-bridge/poll", headers=HEADERS)
    assert poll.status_code == 200, poll.text
    command = poll.json()
    assert command["command"] == "open_workflow"
    assert command["workflow_id"] == workflow["workflow_id"]
    assert command["workflow_file"] == body["workflow_file"]
    assert media_client.get(f"{WF}/comfy-bridge/poll", headers=HEADERS).json() == {"command": "idle"}
    assert object_info["count"] == 0  # 导入路径不触发定义探针
    assert body["data_gaps"] == ["native_load_not_confirmed"]


def test_open_in_comfy_offline_host_fails_closed_without_writing(media_client, no_network, comfy_host, monkeypatch):
    """C3-AC9-OPEN-02：宿主未在线（已配置但连不上）→ 503 COMFY_HOST_UNREACHABLE，不写宿主目录、不排队指令。"""
    from gw.god_workflow import routes
    monkeypatch.setattr(routes, "_comfy_operation",
                        lambda *a, **k: {"ok": True, "status": "offline", "highlight": False})
    workflow = save_document(media_client)
    opened = media_client.post(f"{WF}/open-in-comfy/{workflow['workflow_id']}", headers=HEADERS)
    assert opened.status_code == 503, opened.text
    assert opened.json()["detail"]["code"] == "COMFY_HOST_UNREACHABLE"
    assert list(comfy_host.rglob("GW_*.json")) == []
    assert media_client.get(f"{WF}/comfy-bridge/poll", headers=HEADERS).json() == {"command": "idle"}


def test_open_in_comfy_remote_host_is_rejected_without_writing(media_client, no_network, comfy_host, monkeypatch):
    """C3-AC9-OPEN-03：非本机宿主地址 → 422，不向远端写入。"""
    from gw.god_workflow import routes
    monkeypatch.setenv("GW_COMFYUI_URL", "http://comfy.example.internal:8188")
    workflow = save_document(media_client)
    opened = media_client.post(f"{WF}/open-in-comfy/{workflow['workflow_id']}", headers=HEADERS)
    assert opened.status_code == 422, opened.text
    assert opened.json()["detail"]["code"] == "COMFY_ADAPTER_LOCAL_REQUIRED"
    assert list(comfy_host.rglob("GW_*.json")) == []


def test_sync_saved_with_correct_version_increments_and_keeps_host_resolution(media_client, no_network, comfy_host, object_info):
    """C3-AC9-SYNC-01：正确 expected_version → 200、版本 +1，宿主定义与真实编译缓存入库。"""
    workflow = save_document(media_client)
    opened = media_client.post(f"{WF}/open-in-comfy/{workflow['workflow_id']}", headers=HEADERS)
    assert opened.status_code == 200, opened.text
    synced = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                               json=sync_body(workflow["workflow_id"], 1, resolve_definitions=True))
    assert synced.status_code == 200, synced.text
    assert synced.json() == {"synced": True, "workflow_id": workflow["workflow_id"],
                             "version": 2, "data_status": "ok", "data_gaps": []}
    saved = media_client.get(f"{WF}/item/{workflow['workflow_id']}", headers=HEADERS).json()
    assert saved["version"] == 2
    assert saved["metadata"]["node_schemas"]["AcmeSource"] == SOURCE_SCHEMA
    assert saved["metadata"]["node_schemas"]["AcmeSink"] == SINK_SCHEMA
    resolved = saved["metadata"]["resolved_api"]
    assert resolved["source"] == "comfyui_graph_to_prompt" and resolved["prompt"] == API_PROMPT
    from gw.god_workflow.compiler import compile_prompt
    assert compile_prompt(saved, saved["metadata"]["node_schemas"]) == API_PROMPT
    assert saved["metadata"]["needs_host_resolution"] is False


def test_sync_saved_stale_version_is_409_and_document_unchanged(media_client, no_network, comfy_host, object_info):
    """C3-AC9-SYNC-02：过期 expected_version → 409，原文档逐字节不变；随后用当前版本仍可回存。"""
    workflow = save_document(media_client)
    opened = media_client.post(f"{WF}/open-in-comfy/{workflow['workflow_id']}", headers=HEADERS)
    assert opened.status_code == 200, opened.text
    first = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                              json=sync_body(workflow["workflow_id"], 1, resolve_definitions=True))
    assert first.status_code == 200, first.text
    from gw.god_workflow import routes
    context = routes._auth(HEADERS["Authorization"], "editor", True)
    path = routes._path(context, workflow["workflow_id"])
    stored = path.read_bytes()

    stale = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                              json=sync_body(workflow["workflow_id"], 1))
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"
    assert stale.json()["detail"]["expected_version"] == 1
    assert stale.json()["detail"]["current_version"] == 2
    assert path.read_bytes() == stored

    again = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                              json=sync_body(workflow["workflow_id"], 2))
    assert again.status_code == 200, again.text
    assert again.json()["version"] == 3
    assert media_client.get(f"{WF}/item/{workflow['workflow_id']}", headers=HEADERS).json()["version"] == 3


def test_sync_saved_unknown_semantics_fail_closed(media_client, no_network, comfy_host, object_info):
    """C3-AC9-SYNC-03：未知语义一律 422 失败关闭，原文档不变、探针后无第二次副作用。"""
    workflow = save_document(media_client)
    opened = media_client.post(f"{WF}/open-in-comfy/{workflow['workflow_id']}", headers=HEADERS)
    assert opened.status_code == 200, opened.text
    from gw.god_workflow import routes
    path = routes._path(routes._auth(HEADERS["Authorization"], "editor", True), workflow["workflow_id"])
    stored = path.read_bytes()
    workflow_id = workflow["workflow_id"]

    # 1. 宿主缺少该图的真实节点定义。
    object_info["object_info"] = {}
    missing_defs = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                                     json=sync_body(workflow_id, 1, resolve_definitions=True))
    assert missing_defs.status_code == 422, missing_defs.text
    assert missing_defs.json()["detail"]["code"] == "NODE_SCHEMA_REQUIRED"
    assert path.read_bytes() == stored
    # 2. 宿主探针离线。
    object_info["online"] = False
    offline = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                                json=sync_body(workflow_id, 1, resolve_definitions=True))
    assert offline.status_code == 422, offline.text
    assert offline.json()["detail"]["code"] == "NODE_SCHEMA_REQUIRED"
    assert path.read_bytes() == stored
    object_info["online"] = True
    object_info["object_info"] = dict(OBJECT_INFO)
    # 3. 宿主虚拟节点标识无效。
    bad_virtual = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                                    json=sync_body(workflow_id, 1, resolve_definitions=True, virtual_node_ids="1"))
    assert bad_virtual.status_code == 422, bad_virtual.text
    assert bad_virtual.json()["detail"]["code"] == "INVALID_WORKFLOW"
    # 4. 宿主未返回可执行图。
    empty_prompt = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                                     json=sync_body(workflow_id, 1, resolve_definitions=True, api_prompt={}))
    assert empty_prompt.status_code == 422, empty_prompt.text
    assert empty_prompt.json()["detail"]["code"] == "INVALID_WORKFLOW"
    # 5. 宿主参数映射指向不存在的字段。
    bad_fields = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                                   json=sync_body(workflow_id, 1, resolve_definitions=True,
                                                  field_maps={"1": {"widget_0": "not-a-field"}}))
    assert bad_fields.status_code == 422, bad_fields.text
    assert bad_fields.json()["detail"]["code"] == "INVALID_WORKFLOW"
    assert path.read_bytes() == stored
    # 前两次缺定义/离线 + 后三次非法回传体各触发一次只读探针；失败后均无重试或写入。
    assert object_info["count"] == 5


def test_sync_saved_rejects_missing_version_and_unknown_workflow(media_client, no_network, comfy_host, object_info):
    """C3-AC9-SYNC-04：缺 expected_version → 400；未知工作流 → 404，不产生任何写入。"""
    workflow = save_document(media_client)
    missing = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                                json={"workflow_id": workflow["workflow_id"], "workflow": HOST_GRAPH})
    assert missing.status_code == 400, missing.text
    assert missing.json()["detail"]["code"] == "INVALID_VERSION"
    assert media_client.get(f"{WF}/item/{workflow['workflow_id']}", headers=HEADERS).json()["version"] == 1
    unknown = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                                json=sync_body("00000000-0000-0000-0000-000000000000", 1))
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["detail"]["code"] == "WORKFLOW_NOT_FOUND"


def test_open_in_comfy_and_sync_leave_asset_registry_untouched(media_client, no_network, comfy_host, object_info, asset_counts):
    """C3-AC9-ASSET-01：导入与回存是文档操作；除保存时登记的工作流资产外不新增统一资产。"""
    workflow = save_document(media_client)
    baseline = count_assets(media_client)  # 保存工作流本身登记一条 workflow 资产
    opened = media_client.post(f"{WF}/open-in-comfy/{workflow['workflow_id']}", headers=HEADERS)
    assert opened.status_code == 200, opened.text
    synced = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                               json=sync_body(workflow["workflow_id"], 1, resolve_definitions=True))
    assert synced.status_code == 200, synced.text
    assert count_assets(media_client) == baseline  # 回存更新原资产，不产生重复条目
    assert no_network == []


LITEGRAPH_UPLOAD = {"version": 0.4, "nodes": [{"id": 1, "type": "AcmeSource", "pos": [0, 0],
    "widgets_values": ["actual-value", 7], "inputs": [], "outputs": []}], "links": []}
LITEGRAPH_PROMPT = {"1": {"class_type": "AcmeSource", "inputs": {"text": "actual-value", "count": 7}}}


def test_open_in_comfy_native_roundtrip_and_sync(media_client, no_network, comfy_host, object_info):
    """C3-AC9-OPEN-04：原生（litegraph）文档走 export 分支；宿主回传后参数与端口保持真实解析。"""
    uploaded = media_client.post(f"{WF}/parse-upload?source=canvas", json=LITEGRAPH_UPLOAD, headers=HEADERS)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["needs_definition_resolution"] is False  # 宿主探针已补齐真实定义
    workflow_id = uploaded.json()["workflow_id"]
    opened = media_client.post(f"{WF}/open-in-comfy/{workflow_id}", headers=HEADERS)
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["workflow_format"] == "native"
    assert body["workflow_graph"]["nodes"][0]["widgets_values"] == ["actual-value", 7]
    assert body["resolve_definitions"] is False  # 真实定义已在导入期取自宿主探针，无需再次解析
    on_disk = json.loads(host_file_bytes(comfy_host, body["saved_path"]))
    assert on_disk == body["workflow_graph"]
    synced = media_client.post(f"{WF}/comfy-bridge/sync-saved", headers=HEADERS,
                               json={"workflow_id": workflow_id, "workflow": LITEGRAPH_UPLOAD,
                                     "expected_version": 1, "resolve_definitions": True,
                                     "api_prompt": LITEGRAPH_PROMPT,
                                     "field_maps": {"1": {"widget_0": "text", "widget_1": "count"}},
                                     "virtual_node_ids": []})
    assert synced.status_code == 200, synced.text
    assert synced.json()["version"] == 2
    saved = media_client.get(f"{WF}/item/{workflow_id}", headers=HEADERS).json()
    from gw.god_workflow.compiler import compile_prompt
    fields: dict = {}
    assert compile_prompt(saved, saved["metadata"]["node_schemas"], field_maps=fields) == LITEGRAPH_PROMPT
    assert fields == {"1": {"widget_0": "text", "widget_1": "count"}}


def test_adapter_api_load_passes_safe_filename_derived_from_workflow_id():
    """C3-AC9-FIX-01：API 图载入必须传文件名——ComfyUI 0.39.0（前端 1.55）的 openWorkflow
    读取 filename.path，缺省文件名会抛 TypeError reading 'path'；文件名由 workflow_id 派生。"""
    adapter = browser_bridge.SCRIPT.read_text(encoding='utf-8')
    # 直接在 loadApiJson 调用点断言第二个文件名实参（GW_<workflow_id>.json）。
    call = re.search(r"await app\.loadApiJson\([^)]*\)", adapter)
    assert call, 'adapter 应调用 app.loadApiJson'
    assert re.fullmatch(
        r"await app\.loadApiJson\(message\.graph,\s*'GW_' \+ message\.workflow_id \+ '\.json'\)",
        call.group(0)), call.group(0)
    # 提供到宿主的页面适配即本源文件，无第二份拷贝需要同步。
    assert browser_bridge.SCRIPT.name == 'workbench.js'
    assert browser_bridge.EXTENSION_URL.endswith('/workbench.js')
