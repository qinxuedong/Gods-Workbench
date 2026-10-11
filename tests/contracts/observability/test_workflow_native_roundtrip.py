"""原生回环必须保留实际参数、项目来源及仍匹配的输入素材。"""
from copy import deepcopy
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gw.api.app import create_app
from gw.core.errors import CleanroomException
from gw.god_workflow import comfy_executor
from gw.god_workflow.compiler import compile_prompt
from gw.god_workflow.exporter import export_to_comfy_litegraph
from gw.god_workflow.parser import parse_workflow


HEADERS = {"Authorization": "Bearer native-roundtrip", "Origin": "http://testserver"}
SCHEMAS = {
    "Pair": {"input": {"required": {"first": ["INT", {}], "second": ["INT", {}]}}},
    "LoadImage": {"input": {"required": {"image": ["STRING", {}]}}},
}


@pytest.fixture
def client():
    with TestClient(create_app()) as value:
        yield value


@pytest.mark.parametrize("edited", [False, True])
def test_api_native_roundtrip_uses_schema_order(edited):
    document = parse_workflow({"1": {"class_type": "Pair", "inputs": {"second": 22, "first": 11}}}).as_dict()
    document["metadata"]["node_schemas"] = SCHEMAS
    if edited:
        document["nodes"][0]["widgets"] = [{"name": "second", "value": 33}]
    native = export_to_comfy_litegraph(document)
    restored = compile_prompt(parse_workflow(native).as_dict(), SCHEMAS)
    assert restored["1"]["inputs"] == {"first": 11, "second": 33 if edited else 22}


def test_api_native_roundtrip_keeps_seed_control_slot():
    schemas = {"Pair": {"input": {"required": {
        "first": ["INT", {"control_after_generate": True}], "second": ["INT", {}]}}}}
    document = parse_workflow({"1": {"class_type": "Pair", "inputs": {"second": 22, "first": 11}}}).as_dict()
    document["metadata"]["node_schemas"] = schemas
    native = export_to_comfy_litegraph(document)
    assert native["nodes"][0]["widgets_values"] == [11, "fixed", 22]
    assert compile_prompt(parse_workflow(native).as_dict(), schemas)["1"]["inputs"] == {"first": 11, "second": 22}


def test_api_native_export_without_schema_does_not_guess_order():
    document = parse_workflow({"1": {"class_type": "Pair", "inputs": {"second": 22, "first": 11}}}).as_dict()
    with pytest.raises(CleanroomException) as error:
        export_to_comfy_litegraph(document)
    assert error.value.code == "NODE_SCHEMA_REQUIRED"


def bound_workflow(client):
    project = client.post("/api/asset-registry/projects", headers=HEADERS,
                          json={"name": "native source", "project_type": "other"})
    assert project.status_code == 201, project.text
    project_id = project.json()["project"]["project_id"]
    document = parse_workflow({"1": {"class_type": "LoadImage", "inputs": {"image": "original.png"}}}).as_dict()
    document["metadata"].update(node_schemas=SCHEMAS, source_context={"project_id": project_id})
    created = client.post("/api/god_workflow/documents", headers=HEADERS, json=document)
    assert created.status_code == 201, created.text
    workflow_id = created.json()["workflow_id"]
    pixels = io.BytesIO()
    Image.new("RGB", (2, 3), "red").save(pixels, format="PNG")
    uploaded = client.post("/api/god_workflow/assets/upload", headers=HEADERS,
                          files={"file": ("reference.png", pixels.getvalue(), "image/png")})
    assert uploaded.status_code == 201, uploaded.text
    asset_id = uploaded.json()["asset_id"]
    changed = client.put(f"/api/god_workflow/item/{workflow_id}/params", headers=HEADERS,
                         json={"expected_version": 1, "widget_updates": [
                             {"node_id": "1", "field_name": "image", "value": "reference.png", "asset_id": asset_id}]})
    assert changed.status_code == 200, changed.text
    native = client.get(f"/api/god_workflow/export/{workflow_id}?format=litegraph", headers=HEADERS)
    assert native.status_code == 200, native.text
    return workflow_id, project_id, asset_id, pixels.getvalue(), native.json()


def sync(client, workflow_id, native, expected_version=2):
    return client.post("/api/god_workflow/comfy-bridge/sync-saved", headers=HEADERS,
                       json={"workflow_id": workflow_id, "workflow": native, "expected_version": expected_version})


def test_native_sync_keeps_source_and_uploads_bound_bytes(client, monkeypatch):
    workflow_id, project_id, asset_id, content, native = bound_workflow(client)
    synced = sync(client, workflow_id, native)
    assert synced.status_code == 200, synced.text
    document = client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json()
    assert document["metadata"]["source_context"]["project_id"] == project_id
    assert asset_id in document["nodes"][0]["metadata"]["input_assets"].values()
    monkeypatch.setenv("GW_COMFYUI_URL", "http://127.0.0.1:8188")
    calls = []

    async def upload(payload, filename, content_type, **kwargs):
        assert payload == content and filename == asset_id + ".png"
        calls.append("upload")
        return "isolated/input.png"

    async def submit(prompt, **kwargs):
        assert prompt["1"]["inputs"]["image"] == "isolated/input.png"
        calls.append("submit")
        return {"prompt_id": "native-roundtrip-remote"}

    async def history(*args, **kwargs):
        return {"found": True, "entry": {"outputs": {"1": {"images": [{"filename": "result.png"}]}},
                                         "status": {"status_str": "success"}}}

    async def fetch_output(*args, **kwargs):
        return content, "image/png"

    for name, method in (("upload_input", upload), ("submit", submit), ("history", history), ("fetch_output", fetch_output)):
        monkeypatch.setattr(comfy_executor, name, method)
    response = client.post("/api/god_workflow/execute", headers={**HEADERS, "Idempotency-Key": "native-once"},
                           json={"workflow_id": workflow_id, "expected_version": 3})
    assert response.status_code == 202, response.text
    assert response.json()["project_id"] == project_id and calls == ["upload", "submit"]
    status = client.get("/api/god_workflow/tasks/" + response.json()["job_id"], headers=HEADERS)
    assert status.status_code == 200 and status.json()["status"] == "completed", status.text
    output_id = status.json()["outputs"][0]["asset_id"]
    assets = client.get("/api/asset-registry/assets", headers=HEADERS, params={"project_id": project_id}).json()["items"]
    assert any(asset["asset_id"] == output_id for asset in assets)


@pytest.mark.parametrize("change", ["value", "delete", "kind"])
def test_native_changes_do_not_reuse_stale_asset_binding(client, change):
    workflow_id, project_id, _, _, native = bound_workflow(client)
    if change == "value":
        native["nodes"][0]["widgets_values"][0] = "different.png"
    elif change == "delete":
        native["nodes"] = []
    else:
        native["nodes"][0]["type"] = "DifferentLoader"
    response = sync(client, workflow_id, native)
    assert response.status_code == 200, response.text
    document = client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json()
    assert document["metadata"]["source_context"]["project_id"] == project_id
    assert all(not node["metadata"].get("input_assets") for node in document["nodes"])


def test_native_sync_conflict_keeps_original_source_and_binding(client):
    workflow_id, project_id, asset_id, _, native = bound_workflow(client)
    before = client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json()
    response = sync(client, workflow_id, deepcopy(native), expected_version=1)
    assert response.status_code == 409
    after = client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json()
    assert after == before
    assert after["metadata"]["source_context"]["project_id"] == project_id
    assert asset_id in after["nodes"][0]["metadata"]["input_assets"].values()


def test_edit_after_native_sync_can_clear_previous_asset(client):
    workflow_id, _, _, _, native = bound_workflow(client)
    assert sync(client, workflow_id, native).status_code == 200
    response = client.put(f"/api/god_workflow/item/{workflow_id}/params", headers=HEADERS,
                          json={"expected_version": 3, "widget_updates": [
                              {"node_id": "1", "field_name": "widget_0", "value": "manual.png"}]})
    assert response.status_code == 200, response.text
    document = client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json()
    assert not document["nodes"][0]["metadata"].get("input_assets")
    assert compile_prompt(document, SCHEMAS)["1"]["inputs"]["image"] == "manual.png"


def test_native_export_endpoint_fetches_schema_without_generation(client, monkeypatch):
    created = client.post("/api/god_workflow/documents", headers=HEADERS,
                          json={"1": {"class_type": "Pair", "inputs": {"second": 22, "first": 11}}})
    workflow_id = created.json()["workflow_id"]
    monkeypatch.setenv("GW_COMFYUI_URL", "http://127.0.0.1:8188")
    calls = []

    async def probe():
        calls.append("schema")
        return {"online": True, "object_info": SCHEMAS}

    async def submit(*args, **kwargs):
        pytest.fail("导出不能触发生成")

    monkeypatch.setattr(comfy_executor, "probe", probe)
    monkeypatch.setattr(comfy_executor, "submit", submit)
    response = client.get(f"/api/god_workflow/export/{workflow_id}?format=litegraph", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["nodes"][0]["widgets_values"] == [11, 22] and calls == ["schema"]


@pytest.mark.parametrize("probe_result", [
    {"online": False, "status": "unreachable"},
    {"online": True, "object_info": {}},
    {"online": True, "object_info": {"OtherNode": SCHEMAS["Pair"]}},
])
def test_native_sync_missing_schema_keeps_original_document(client, monkeypatch, probe_result):
    workflow_id, _, _, _, native = bound_workflow(client)
    document = client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json()
    document["metadata"].pop("node_schemas")
    changed = client.put(f"/api/god_workflow/documents/{workflow_id}", headers=HEADERS,
                         json={"expected_version": 2, "workflow": document})
    assert changed.status_code == 200, changed.text
    before = client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json()
    monkeypatch.setenv("GW_COMFYUI_URL", "http://127.0.0.1:8188")

    async def probe():
        return probe_result

    monkeypatch.setattr(comfy_executor, "probe", probe)
    response = sync(client, workflow_id, native, expected_version=3)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "NODE_SCHEMA_REQUIRED"
    assert client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json() == before


def test_native_connected_input_drops_binding_and_executes_link(client, monkeypatch):
    workflow_id, _, _, _, native = bound_workflow(client)
    native["nodes"][0]["inputs"] = [{"name": "image", "type": "STRING", "link": 77}]
    native["nodes"].append({"id": 2, "type": "ValueSource", "pos": [0, 0], "inputs": [],
                            "outputs": [{"name": "text", "type": "STRING", "links": [77]}], "widgets_values": []})
    native["links"] = [[77, 2, 0, 1, 0, "STRING"]]
    response = sync(client, workflow_id, native)
    assert response.status_code == 200, response.text
    document = client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json()
    assert not document["nodes"][0]["metadata"].get("input_assets")
    monkeypatch.setenv("GW_COMFYUI_URL", "http://127.0.0.1:8188")

    async def forbidden_upload(*args, **kwargs):
        pytest.fail("宿主已连接的输入不能再由旧素材上传覆盖")

    async def submit(prompt, **kwargs):
        assert prompt["1"]["inputs"]["image"] == ["2", 0]
        return {"prompt_id": "native-linked-input"}

    monkeypatch.setattr(comfy_executor, "upload_input", forbidden_upload)
    monkeypatch.setattr(comfy_executor, "submit", submit)
    executed = client.post("/api/god_workflow/execute", headers=HEADERS,
                           json={"workflow_id": workflow_id, "expected_version": 3})
    assert executed.status_code == 202, executed.text


@pytest.mark.parametrize("missing", [False, True])
def test_native_missing_widget_rejects_save_without_losing_binding(client, missing):
    workflow_id, _, _, _, native = bound_workflow(client)
    before = client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json()
    if missing:
        native["nodes"][0].pop("widgets_values")
    else:
        native["nodes"][0]["widgets_values"] = []
    response = sync(client, workflow_id, native)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "UNSUPPORTED_WIDGET_MAPPING"
    assert client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json() == before


@pytest.mark.parametrize("change", ["delete", "kind"])
def test_native_removed_binding_does_not_require_old_node_schema(client, monkeypatch, change):
    workflow_id, _, _, _, native = bound_workflow(client)
    document = client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json()
    document["metadata"].pop("node_schemas")
    assert client.put(f"/api/god_workflow/documents/{workflow_id}", headers=HEADERS,
                      json={"expected_version": 2, "workflow": document}).status_code == 200
    if change == "delete":
        native["nodes"] = []
    else:
        native["nodes"][0]["type"] = "DifferentLoader"

    async def forbidden_probe():
        pytest.fail("已删除或换类的节点无需为旧素材绑定读取 Schema")

    monkeypatch.setattr(comfy_executor, "probe", forbidden_probe)
    response = sync(client, workflow_id, native, expected_version=3)
    assert response.status_code == 200, response.text
    restored = client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS).json()
    assert all(not node["metadata"].get("input_assets") for node in restored["nodes"])
