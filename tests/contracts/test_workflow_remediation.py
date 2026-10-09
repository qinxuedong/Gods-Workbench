"""工作流跨文档、版本与兼容接口回归；只使用隔离测试存储。"""
from __future__ import annotations

import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from gw.api.app import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as value:
        yield value


HEADERS = {"Authorization": "Bearer workflow-remediation", "Origin": "http://testserver"}


def save(client, value="original"):
    response = client.post("/api/god_workflow/documents", headers=HEADERS,
                           json={"1": {"class_type": "LoadImage", "inputs": {"image": value}}})
    assert response.status_code == 201, response.text
    return response.json()["workflow_id"]


def item(client, workflow_id):
    response = client.get(f"/api/god_workflow/item/{workflow_id}", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def test_move_ignores_other_document_payload(client):
    source = save(client, "source.png")
    target = save(client, "target.png")
    folder_response = client.post("/api/god_workflow/folders", headers=HEADERS, json={"name": "destination"})
    folder_id = folder_response.json()["folder"]["folder_id"]
    response = client.put(f"/api/god_workflow/item/{target}/folder", headers=HEADERS,
                          json={"payload": item(client, source), "folder_id": folder_id, "expected_version": 1})
    assert response.status_code == 200, response.text
    stored = item(client, target)
    assert stored["nodes"][0]["inputs"]["image"] == "target.png"
    assert stored["folder_id"] == folder_id
    assert stored["version"] == 2


def test_api_import_parameters_can_save_and_conflict(client):
    workflow_id = save(client)
    url = f"/api/god_workflow/item/{workflow_id}/params"
    updates = [{"node_id": "1", "field_name": "image", "value": "edited.png"}]
    assert client.put(url, headers=HEADERS, json={"widget_updates": updates}).status_code == 400
    response = client.put(url, headers=HEADERS, json={"widget_updates": updates, "expected_version": 1})
    assert response.status_code == 200, response.text
    assert item(client, workflow_id)["nodes"][0]["inputs"]["image"] == "edited.png"
    assert client.put(url, headers=HEADERS, json={"widget_updates": updates, "expected_version": 1}).status_code == 409


@pytest.mark.parametrize('version', [True, False, 1.2, 0, -1, '1.0'])
def test_cas_rejects_non_integer_or_non_positive_versions(client, version):
    workflow_id = save(client)
    response = client.put(f'/api/god_workflow/item/{workflow_id}/params', headers=HEADERS,
                          json={'expected_version': version, 'widget_updates': []})
    assert response.status_code == 400
    assert item(client, workflow_id)['version'] == 1


def test_parameters_reject_malformed_positions_and_unknown_nodes(client):
    workflow_id = save(client)
    url = f'/api/god_workflow/item/{workflow_id}/params'
    for changes in ({'positions': {}}, {'widget_updates': [{'node_id': 'absent', 'field_name': 'image', 'value': 'bad'}]}):
        response = client.put(url, headers=HEADERS, json={'expected_version': 1, **changes})
        assert response.status_code == 400
    assert item(client, workflow_id)['version'] == 1


def test_layout_and_sync_require_client_version(client):
    workflow_id = save(client)
    assert client.post(f"/api/god_workflow/auto-layout/{workflow_id}", headers=HEADERS).status_code == 400
    workflow = item(client, workflow_id)
    url = "/api/god_workflow/comfy-bridge/sync-saved"
    assert client.post(url, headers=HEADERS, json={"workflow_id": workflow_id, "workflow": workflow}).status_code == 400
    response = client.post(url, headers=HEADERS,
                           json={"workflow_id": workflow_id, "workflow": workflow, "expected_version": 1})
    assert response.status_code == 200, response.text
    revisions = client.get("/api/god_workflow/comfy-bridge/sync-status", headers=HEADERS).json()["revisions"]
    assert revisions[workflow_id] == 2


def test_compat_delete_requires_version_and_removes_only_target(client):
    target = save(client)
    other = save(client, "other.png")
    url = f"/api/god_workflow/item/{target}"
    assert client.request("DELETE", url, headers=HEADERS, json={}).status_code == 400
    assert client.request("DELETE", url, headers=HEADERS, json={"expected_version": 99}).status_code == 409
    assert client.request("DELETE", url, headers=HEADERS, json={"expected_version": 1}).status_code == 200
    assert client.get(url, headers=HEADERS).status_code == 404
    assert item(client, other)["nodes"][0]["inputs"]["image"] == "other.png"


def test_catalog_skips_private_metadata_and_survives_new_app(client):
    """保存配置/剪贴板/素材清单后，新进程视角仍只列当前主体的工作流。"""
    from gw.god_workflow import registry, routes
    target = save(client, 'saved.png')
    context = routes._auth(HEADERS['Authorization'], 'editor', True)
    registry.update_settings(context, {'comfy_url': 'http://127.0.0.1:8199'})
    registry.record_clipboard(context, [{'text': 'https://www.liblib.art/modelinfo/source'}])
    registry._atomic_write(registry._assets_manifest_path(context), [])
    registry._atomic_write(routes._principal_root(context) / 'invalid-document.json', [])
    paths = [registry._settings_path(context), registry._clipboard_path(context), registry._assets_manifest_path(context)]
    before = [path.read_bytes() for path in paths]
    for app_client in (client, TestClient(create_app())):
        listed = app_client.get('/api/god_workflow/list', headers=HEADERS)
        assert listed.status_code == 200, listed.text
        assert [entry['workflow_id'] for entry in listed.json()['items']] == [target]
        assert item(app_client, target)['nodes'][0]['inputs']['image'] == 'saved.png'
    empty = client.post('/api/god_workflow/folders', headers=HEADERS, json={'name': '空目录'}).json()['folder']
    deleted = client.request('DELETE', '/api/god_workflow/folders/' + empty['folder_id'],
        headers=HEADERS, json={'expected_revision': empty['revision']})
    assert deleted.status_code == 200, deleted.text
    assert [path.read_bytes() for path in paths] == before
    for reserved in ('settings', 'clipboard', 'assets', 'SETTINGS', 'ClIpBoArD', 'Assets'):
        assert client.get('/api/god_workflow/item/' + reserved, headers=HEADERS).status_code == 404


def test_source_reimport_cas_preserves_user_edit_and_advances_version(client, monkeypatch):
    from gw.god_workflow import platforms
    remote_id = '1234567890123456789'
    async def source(*args, **kwargs):
        return {'workflow_id': remote_id, 'api_json': {
            '1': {'class_type': 'LoadImage', 'inputs': {'image': 'provider-original.png'}}}}
    monkeypatch.setattr(platforms, 'fetch_runninghub_bundle', source)
    reference = 'https://www.runninghub.cn/workflow/' + remote_id
    body = {'url_or_text': reference}
    first = client.post('/api/god_workflow/parse-link', headers=HEADERS, json=body)
    assert first.status_code == 200, first.text
    workflow_id = first.json()['workflow_id']
    asset_id = first.json()['asset_id']
    changed = client.put(f'/api/god_workflow/item/{workflow_id}/params', headers=HEADERS,
        json={'expected_version': 1, 'widget_updates': [
            {'node_id': '1', 'field_name': 'image', 'value': 'user-edited.png'}]})
    assert changed.status_code == 200, changed.text
    edited = item(client, workflow_id)
    assert edited['version'] == 2
    for version in (None, 1, True):
        retry = dict(body)
        if version is not None:
            retry['expected_version'] = version
        refused = client.post('/api/god_workflow/parse-link', headers=HEADERS, json=retry)
        assert refused.status_code == 409, refused.text
        assert refused.json()['detail']['code'] == 'VERSION_CONFLICT'
        assert item(client, workflow_id) == edited
    accepted = client.post('/api/god_workflow/parse-link', headers=HEADERS,
                           json={**body, 'expected_version': 2})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['workflow_id'] == workflow_id
    assert accepted.json()['asset_id'] == asset_id
    assert accepted.json()['version'] == 3
    stored = item(client, workflow_id)
    assert stored['nodes'][0]['inputs']['image'] == 'provider-original.png'
    assert stored['external_workflow_id'] == remote_id


def test_native_export_preserves_widgets_sparse_ports_and_extensions():
    from gw.god_workflow.parser import parse_workflow
    from gw.god_workflow.exporter import export_to_comfy_litegraph
    graph = {"version": 0.4, "groups": [{"title": "Keep"}], "extra": {"custom": True},
             "nodes": [{"id": 1, "type": "Source", "pos": [10, 20], "widgets_values": [42, "value"],
                        "inputs": [], "outputs": [{"name": "first", "type": "IMAGE"},
                                                   {"name": "unused", "type": "MASK"},
                                                   {"name": "third", "type": "IMAGE"}],
                        "mode": 2, "properties": {"custom": "keep"}},
                       {"id": 2, "type": "Target", "pos": [50, 60], "widgets_values": [],
                        "inputs": [{"name": "unused", "type": "MASK", "link": None},
                                   {"name": "image", "type": "IMAGE", "link": 5}], "outputs": []}],
             "links": [[5, 1, 2, 2, 1, "IMAGE"]]}
    document = parse_workflow(graph).as_dict()
    document["nodes"][0]["position"]["x"] = 123
    exported = export_to_comfy_litegraph(document)
    assert exported["groups"] == graph["groups"] and exported["extra"] == graph["extra"]
    source, target = exported["nodes"]
    assert exported['links'][0][0] == 5 and exported['last_link_id'] == 5
    assert source["widgets_values"] == [42, "value"] and source["pos"][0] == 123
    assert source["mode"] == 2 and source["properties"] == {"custom": "keep"}
    assert len(source["outputs"]) == 3
    assert exported["links"][0][2:5] == [2, 2, 1]
    assert target["inputs"][1]["name"] == "image"


def test_private_workflow_metadata_hidden_from_other_principal(client):
    workflow_id = save(client, "private-reference.png")
    asset_id = item(client, workflow_id)["asset_id"]
    other = {"Authorization": "Bearer other-workflow-user", "Origin": "http://testserver"}
    for headers, expected in ((HEADERS, True), (other, False)):
        tree = client.get("/api/asset-library", headers=headers).json()["library"]
        items = [value for library in tree["libraries"] for category in library["categories"] for value in category["items"]]
        assert any(value["asset_id"] == asset_id for value in items) is expected
        assets = client.get("/api/asset-registry/assets", headers=headers).json()["items"]
        assert any(value["asset_id"] == asset_id for value in assets) is expected
    assert client.get(f"/api/asset-registry/assets/{asset_id}", headers=other).status_code == 404
    assert client.get(f"/api/asset-registry/assets/{asset_id}", headers=HEADERS).status_code == 200


def test_endpoint_snapshot_survives_settings_change(client, monkeypatch):
    from gw.god_workflow import comfy_executor
    monkeypatch.delenv("GW_COMFYUI_URL", raising=False)
    calls = []
    async def submit(prompt, **kwargs):
        calls.append(("submit", comfy_executor.require_endpoint().base_url))
        return {"prompt_id": "remote-123"}
    async def history(prompt_id):
        calls.append(("history", comfy_executor.require_endpoint().base_url))
        return {"found": False, "entry": None}
    monkeypatch.setattr(comfy_executor, "submit", submit)
    monkeypatch.setattr(comfy_executor, "history", history)
    admin = {**HEADERS, "X-User-Role": "admin"}
    assert client.post("/api/god_workflow/settings", headers=admin, json={"comfy_url": "http://127.0.0.1:8288"}).status_code == 200
    workflow_id = save(client)
    request = {"workflow_id": workflow_id, "expected_version": 1}
    response = client.post("/api/god_workflow/execute", headers={**HEADERS, "Idempotency-Key": "one-request"}, json=request)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert response.json()["workflow_version"] == 1
    assert client.post("/api/god_workflow/settings", headers=admin, json={"comfy_url": "http://127.0.0.1:8388"}).status_code == 200
    assert client.get(f"/api/god_workflow/tasks/{job_id}", headers=HEADERS).status_code == 200
    replay = client.post("/api/god_workflow/execute", headers={**HEADERS, "Idempotency-Key": "one-request"}, json=request)
    assert replay.json()["job_id"] == job_id
    assert calls == [("submit", "http://127.0.0.1:8288"), ("history", "http://127.0.0.1:8288")]


def test_bridge_failure_is_visible_and_recoverable(client, monkeypatch):
    from gw.asset_registry import repository
    original = repository.register_asset_record
    def fail(*args, **kwargs):
        raise OSError("mock disk failure")
    monkeypatch.setattr(repository, "register_asset_record", fail)
    response = client.post("/api/god_workflow/documents", headers=HEADERS,
                           json={"1": {"class_type": "LoadImage", "inputs": {"image": "kept.png"}}})
    assert response.status_code == 201 and response.json()["data_status"] == "partial"
    workflow_id = response.json()["workflow_id"]
    assert item(client, workflow_id)["nodes"][0]["inputs"]["image"] == "kept.png"
    monkeypatch.setattr(repository, "register_asset_record", original)
    recovered = client.post("/api/god_workflow/asset-hub/reconcile", headers=HEADERS)
    assert recovered.status_code == 200 and recovered.json()["pending"] == 0
    asset_id = item(client, workflow_id)["asset_id"]
    assert client.get(f"/api/asset-registry/assets/{asset_id}", headers=HEADERS).status_code == 200


def test_native_compiler_uses_real_schema_and_links():
    from gw.god_workflow.compiler import compile_prompt
    from gw.god_workflow.parser import parse_workflow
    from gw.core.errors import CleanroomException
    graph = {"nodes": [{"id": 1, "type": "LoadImage", "pos": [0, 0], "inputs": [], "widgets_values": ["input.png"]},
                       {"id": 2, "type": "SaveImage", "pos": [0, 0], "inputs": [{"name": "images", "type": "IMAGE"}],
                        "widgets_values": ["prefix"]}], "links": [[4, 1, 0, 2, 0, "IMAGE"]]}
    document = parse_workflow(graph).as_dict()
    schemas = {"LoadImage": {"input": {"required": {"image": [["input.png"], {}]}}},
               "SaveImage": {"input": {"required": {"images": ["IMAGE"], "filename_prefix": ["STRING", {}]}}}}
    with pytest.raises(CleanroomException) as missing:
        compile_prompt(document)
    assert missing.value.code == "NODE_SCHEMA_REQUIRED"
    compiled = compile_prompt(document, schemas)
    assert compiled["1"]["inputs"] == {"image": "input.png"}
    assert compiled["2"]["inputs"] == {"filename_prefix": "prefix", "images": ["1", 0]}


def test_webp_workflow_metadata_roundtrip():
    import io
    import json
    Image = pytest.importorskip("PIL.Image")
    from gw.god_workflow.parser import parse_upload
    workflow = {"nodes": [{"id": 1, "type": "LoadImage", "pos": [3, 4], "widgets_values": ["test.png"], "inputs": []}], "links": []}
    exif = Image.Exif()
    exif[0x010F] = "workflow:" + json.dumps(workflow)
    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), "white").save(buffer, format="WEBP", exif=exif)
    parsed = parse_upload(buffer.getvalue(), "image/webp")
    assert parsed.nodes[0].widgets == ["test.png"]
    assert parsed.nodes[0].position == (3.0, 4.0)


def test_selected_asset_bytes_reach_engine_before_generation(client, monkeypatch):
    import io
    Image = pytest.importorskip("PIL.Image")
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv("GW_COMFYUI_URL", "http://127.0.0.1:8188")
    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), "white").save(buffer, format="PNG")
    content = buffer.getvalue()
    uploaded = client.post("/api/god_workflow/assets/upload", headers=HEADERS,
                           files={"file": ("reference.png", content, "image/png")})
    assert uploaded.status_code == 201, uploaded.text
    asset_id = uploaded.json()["asset_id"]
    workflow_id = save(client)
    saved = client.put(f"/api/god_workflow/item/{workflow_id}/params", headers=HEADERS,
                       json={"expected_version": 1, "widget_updates": [
                           {"node_id": "1", "field_name": "image", "value": "reference.png", "asset_id": asset_id}]})
    assert saved.status_code == 200, saved.text
    calls = []
    async def upload(payload, filename, content_type, **kwargs):
        assert payload == content and filename == asset_id + ".png" and content_type == "image/png"
        assert kwargs["subfolder"]
        calls.append("upload")
        return "engine/input-reference.png"
    async def submit(prompt, **kwargs):
        assert prompt["1"]["inputs"]["image"] == "engine/input-reference.png"
        calls.append("generate")
        return {"prompt_id": "remote-input-test"}
    monkeypatch.setattr(comfy_executor, "upload_input", upload)
    monkeypatch.setattr(comfy_executor, "submit", submit)
    headers = {**HEADERS, "Idempotency-Key": "input-one"}
    body = {"workflow_id": workflow_id, "expected_version": 2}
    first = client.post("/api/god_workflow/execute", headers=headers, json=body)
    assert first.status_code == 202 and first.json()["status"] == "accepted", first.text
    second = client.post("/api/god_workflow/execute", headers=headers, json=body)
    assert second.json()["job_id"] == first.json()["job_id"]
    assert calls == ["upload", "generate"]


def test_workflow_asset_rename_delete_follow_source(client):
    workflow_id = save(client)
    asset_id = item(client, workflow_id)["asset_id"]
    renamed = client.put(f"/api/god_workflow/item/{workflow_id}/name", headers=HEADERS,
                         json={"name": "new source name", "expected_version": 1})
    assert renamed.status_code == 200
    asset = client.get(f"/api/asset-registry/assets/{asset_id}", headers=HEADERS).json()["asset"]
    assert asset["name"] == "new source name" and asset["metadata"]["workflow_version"] == 2
    deleted = client.request("DELETE", f"/api/god_workflow/item/{workflow_id}", headers=HEADERS,
                             json={"expected_version": 2})
    assert deleted.status_code == 200
    assert client.get(f"/api/asset-registry/assets/{asset_id}", headers=HEADERS).status_code == 404


def test_large_node_layout_avoids_overlaps():
    from gw.god_workflow.layout import compute_layout
    nodes = [{"id": "a", "position": {"width": 800, "height": 900}},
             {"id": "b", "position": {"width": 700, "height": 800}},
             {"id": "c", "position": {"width": 400, "height": 1100}},
             {"id": "note", "position": {"width": 1500, "height": 1000}}]
    connections = [{"source": "a", "target": "c"}, {"source": "b", "target": "c"}]
    positions = compute_layout(nodes, connections)
    for i, first in enumerate(nodes):
        for second in nodes[i + 1:]:
            x1, y1 = positions[first["id"]]
            x2, y2 = positions[second["id"]]
            g1, g2 = first["position"], second["position"]
            assert (x1 + g1["width"] <= x2 or x2 + g2["width"] <= x1
                    or y1 + g1["height"] <= y2 or y2 + g2["height"] <= y1)


def test_psd_original_download_and_media_category(client):
    import struct
    payload = b'8BPS' + struct.pack('>H6sHIIHH', 1, bytes(6), 3, 2, 3, 8, 3) + bytes(12)
    response = client.post('/api/god_workflow/assets/upload', headers=HEADERS,
                           files={'file': ('layers.psd', payload, 'application/octet-stream')})
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset['media_type'] == 'document' and asset['preview_available'] is False
    content = client.get(asset['url'], headers=HEADERS)
    assert content.content == payload and 'attachment' in content.headers['content-disposition']
    record = client.get(f"/api/asset-registry/assets/{asset['asset_id']}", headers=HEADERS).json()['asset']
    assert record['kind'] == 'document'
    assert client.post('/api/god_workflow/assets/upload', headers=HEADERS,
                       files={'file': ('invalid.psd', b'not a PSD', 'application/octet-stream')}).status_code == 400


@pytest.mark.parametrize('remote_id', [None, '', '  ', True, {'id': '123'}, '1' * 129])
def test_cloud_requires_remote_workflow_id_before_side_effects(client, monkeypatch, remote_id):
    from gw.god_workflow import platforms
    monkeypatch.setenv('GW_RUNNINGHUB_API_KEY', 'synthetic-test-key')
    workflow_id = save(client)
    calls = []
    async def unexpected(*args, **kwargs):
        calls.append('unexpected')
        raise AssertionError('标识无效时不能向供应商发请求')
    monkeypatch.setattr(platforms, 'upload_rh_input', unexpected)
    monkeypatch.setattr(platforms, 'create_rh_cloud_task', unexpected)
    body = {'workflow_id': workflow_id, 'target': 'rh_cloud'}
    if remote_id is not None:
        body['rh_workflow_id'] = remote_id
    response = client.post('/api/god_workflow/execute', headers=HEADERS, json=body)
    assert response.status_code == 400, response.text
    assert response.json()['detail']['code'] == 'INVALID_REQUEST'
    assert calls == []
    assert client.get('/api/god_workflow/tasks', headers=HEADERS).json()['tasks'] == []


@pytest.mark.parametrize('provider', ['runninghub', 'liblib'])
def test_cloud_uses_only_matching_imported_supplier_id(client, monkeypatch, provider):
    from gw.god_workflow import platforms
    monkeypatch.setenv('GW_RUNNINGHUB_API_KEY', 'synthetic-test-key')
    remote_id = '1234567890123456789'
    async def source(*args, **kwargs):
        return {'workflow_id': remote_id, 'api_json': {'1': {'class_type': 'LoadImage', 'inputs': {'image': 'remote.png'}}}}
    monkeypatch.setattr(platforms, 'fetch_runninghub_bundle', source)
    monkeypatch.setattr(platforms, 'fetch_liblib_bundle', source)
    reference = ('https://www.runninghub.cn/workflow/' if provider == 'runninghub'
                 else 'https://www.liblib.art/workflows/') + remote_id
    parsed = client.post('/api/god_workflow/parse-link', headers=HEADERS, json={'url_or_text': reference})
    assert parsed.status_code == 200, parsed.text
    workflow_id = parsed.json()['workflow_id']
    assert item(client, workflow_id)['external_provider'] == provider
    calls = []
    async def submit(workflow, *args, **kwargs):
        calls.append(workflow)
        return {'taskId': 'supplier-bound-test'}
    monkeypatch.setattr(platforms, 'create_rh_cloud_task', submit)
    response = client.post('/api/god_workflow/execute', headers=HEADERS,
                           json={'workflow_id': workflow_id, 'target': 'rh_cloud'})
    if provider == 'runninghub':
        assert response.status_code == 202, response.text
        assert calls == [remote_id]
    else:
        assert response.status_code == 400, response.text
        assert calls == []


def test_cloud_upload_precedes_generate_and_saved_unpromoted_parameter_applies(client, monkeypatch):
    from gw.god_workflow import platforms
    monkeypatch.setenv('GW_RUNNINGHUB_API_KEY', 'synthetic-test-key')
    asset = client.post('/api/god_workflow/assets/upload', headers=HEADERS,
        files={'file': ('reference.png', b'isolated-input-bytes', 'image/png')}).json()
    workflow_id = save(client)
    changed = client.put(f'/api/god_workflow/item/{workflow_id}/params', headers=HEADERS,
        json={'expected_version': 1, 'widget_updates': [
            {'node_id': '1', 'field_name': 'image', 'value': 'reference.png', 'asset_id': asset['asset_id']}]})
    assert changed.status_code == 200
    calls = []
    async def upload(payload, filename, content_type, key, **kwargs):
        assert payload == b'isolated-input-bytes' and key == 'synthetic-test-key'
        calls.append('upload')
        return 'api/engine-reference.png'
    async def submit(workflow, values, key, **kwargs):
        assert workflow == '1234567890123456789'
        assert values == [{'nodeId': '1', 'fieldName': 'image', 'fieldValue': 'api/engine-reference.png'}]
        calls.append('generate')
        return {'taskId': 'cloud-real-id'}
    monkeypatch.setattr(platforms, 'upload_rh_input', upload)
    monkeypatch.setattr(platforms, 'create_rh_cloud_task', submit)
    headers = {**HEADERS, 'Idempotency-Key': 'cloud-input-one'}
    body = {'workflow_id': workflow_id, 'expected_version': 2, 'target': 'rh_cloud',
            'rh_workflow_id': '1234567890123456789', 'node_info_list': []}
    first = client.post('/api/god_workflow/execute', headers=headers, json=body)
    assert first.status_code == 202 and first.json()['status'] == 'accepted', first.text
    repeated = client.post('/api/god_workflow/execute', headers=headers, json=body)
    assert repeated.json()['job_id'] == first.json()['job_id'] and calls == ['upload', 'generate']


def test_source_project_acl_and_version_snapshot(client, monkeypatch):
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    async def submit(*args, **kwargs):
        return {'prompt_id': 'source-context-test'}
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    project = client.post('/api/asset-registry/projects', headers=HEADERS,
                          json={'name': 'isolated source', 'project_type': 'other'})
    assert project.status_code == 201, project.text
    project_id = project.json()['project']['project_id']
    workflow_id = save(client)
    response = client.post('/api/god_workflow/execute', headers={**HEADERS, 'Idempotency-Key': 'project-source'},
        json={'workflow_id': workflow_id, 'expected_version': 1,
              'source_context': {'project_id': project_id, 'project_version': 1}})
    assert response.status_code == 202 and response.json()['project_id'] == project_id, response.text
    assert response.json()['source_context']['project_version'] == 1
    import io
    from PIL import Image
    pixels = io.BytesIO()
    Image.new('RGB', (2, 2), 'blue').save(pixels, format='PNG')
    content = pixels.getvalue()
    async def history(*args, **kwargs):
        return {'found': True, 'entry': {'outputs': {'1': {'images': [{'filename': 'project.png'}]}}, 'status': {'status_str': 'success'}}}
    async def fetch_output(*args, **kwargs):
        return content, 'image/png'
    monkeypatch.setattr(comfy_executor, 'history', history)
    monkeypatch.setattr(comfy_executor, 'fetch_output', fetch_output)
    job_id = response.json()['job_id']
    status = client.get('/api/god_workflow/tasks/' + job_id, headers=HEADERS)
    assert status.status_code == 200 and status.json()['status'] == 'completed', status.text
    asset_id = status.json()['outputs'][0]['asset_id']
    project_assets = client.get('/api/asset-registry/assets', headers=HEADERS, params={'project_id': project_id}).json()['items']
    assert any(asset['asset_id'] == asset_id for asset in project_assets)
    media = client.get('/api/asset-registry/assets/' + asset_id + '/media', headers=HEADERS)
    assert media.status_code == 200 and media.content == content
    assert 'x-media-display' not in media.headers
    other = {**HEADERS, 'Authorization': 'Bearer other-subject'}
    assert client.get('/api/asset-registry/assets/' + asset_id + '/media', headers=other).status_code == 404
    assert not client.get('/api/asset-registry/assets', headers=other, params={'project_id': project_id}).json()['items']
    other_workflow = client.post('/api/god_workflow/documents', headers=other,
        json={'1': {'class_type': 'LoadImage', 'inputs': {'image': 'other.png'}}}).json()['workflow_id']
    denied = client.post('/api/god_workflow/execute', headers=other,
        json={'workflow_id': other_workflow, 'source_context': {'project_id': project_id}})
    assert denied.status_code == 404


def test_native_open_never_overwrites_user_named_files(client, monkeypatch, tmp_path):
    from gw.god_workflow import registry
    from gw.god_workflow.routes import _auth
    context = _auth(HEADERS['Authorization'], 'editor', True)
    root = tmp_path / 'isolated-comfy'
    workflows = root / 'user/default/workflows'
    workflows.mkdir(parents=True)
    user_file = workflows / 'RH_workflow.json'
    user_file.write_text('user authored content', encoding='utf-8')
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    registry.update_settings(context, {'comfy_root_dir': str(root)})
    monkeypatch.setattr('gw.god_workflow.routes._comfy_operation', lambda *args: {'highlight': True})
    workflow_id = save(client)
    first = client.post('/api/god_workflow/open-in-comfy/' + workflow_id, headers=HEADERS)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body['workflow_file'].startswith('GW_') and 'native_load_not_confirmed' in body['data_gaps']
    assert user_file.read_text(encoding='utf-8') == 'user authored content'
    target = Path(body['saved_path'])
    target.write_text('manually changed bridge file', encoding='utf-8')
    repeated = client.post('/api/god_workflow/open-in-comfy/' + workflow_id, headers=HEADERS).json()
    assert 'COMFY_WORKFLOW_WRITE_FAILED' in repeated['data_gaps']
    assert target.read_text(encoding='utf-8') == 'manually changed bridge file'


def test_registry_overview_and_bulk_mutations_respect_workflow_owner(client):
    workflow_id = save(client)
    asset_id = item(client, workflow_id)['asset_id']
    other = {**HEADERS, 'Authorization': 'Bearer other-subject'}
    root = client.get('/api/asset-registry', headers=other)
    assert root.status_code == 200 and all(value['asset_id'] != asset_id for value in root.json()['assets'])
    assert client.get('/api/asset-registry/status', headers=other).json()['assets_count'] == 0
    denied = client.post('/api/asset-registry/assets/tags', headers=other,
                         json={'asset_ids': [asset_id], 'names': ['forbidden']})
    assert denied.status_code == 404, denied.text
    assert client.get(f'/api/asset-registry/assets/{asset_id}', headers=HEADERS).json()['asset']['tags'] == []


def test_fresh_process_restores_task_endpoint_and_output_without_resubmission(client, monkeypatch):
    import json
    import os
    import subprocess
    import sys
    from gw.god_workflow import comfy_executor, registry
    from gw.god_workflow.routes import _auth
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    calls = []
    async def submit(*args, **kwargs):
        calls.append('generate')
        return {'prompt_id': 'durable-remote-id'}
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    workflow_id = save(client)
    headers = {**HEADERS, 'Idempotency-Key': 'restart-one'}
    body = {'workflow_id': workflow_id, 'expected_version': 1}
    response = client.post('/api/god_workflow/execute', headers=headers, json=body)
    assert response.status_code == 202
    task = response.json()
    context = _auth(HEADERS['Authorization'], 'editor', True)
    output = registry.store_bytes(context, b'isolated-output', 'output.png', 'image/png', task_id=task['job_id'])
    # 新解释器没有父进程的内存对象；禁止任何网络连接。
    code = '''
import json, os, socket
from gw.god_workflow import registry
from gw.core.auth import require_authenticated
def deny(*args, **kwargs): raise AssertionError('恢复不得访问外网')
socket.socket.connect = deny
context = require_authenticated('Bearer workflow-remediation', 'editor')
task = registry.load_task(context, os.environ['GW_RESTART_JOB'])
assets = registry.load_assets(context)
print(json.dumps({'job_id':task['job_id'], 'remote_task_id':task['remote_task_id'],
 'base_url':task['executor_snapshot']['base_url'], 'workflow_version':task['workflow_version'],
 'output_ids':[a['asset_id'] for a in assets]}))
'''
    environment = {**os.environ, 'GW_RESTART_JOB': task['job_id'], 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    restarted = subprocess.run([sys.executable, '-c', code], env=environment,
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert restarted.returncode == 0, restarted.stderr
    restored = json.loads(restarted.stdout)
    assert restored == {'job_id': task['job_id'], 'remote_task_id': 'durable-remote-id',
                        'base_url': 'http://127.0.0.1:8188', 'workflow_version': 1, 'output_ids': [output['asset_id']]}
    replay = client.post('/api/god_workflow/execute', headers=headers, json=body)
    assert replay.json()['job_id'] == task['job_id'] and calls == ['generate']


def test_api_export_after_edit_keeps_untouched_parameter_values(client):
    workflow = {'1': {'class_type': 'ExampleNode', 'inputs': {'seed': 12, 'steps': 20, 'label': 'untouched'}}}
    created = client.post('/api/god_workflow/documents', headers=HEADERS, json=workflow)
    workflow_id = created.json()['workflow_id']
    changed = client.put(f'/api/god_workflow/item/{workflow_id}/params', headers=HEADERS,
        json={'expected_version': 1, 'widget_updates': [{'node_id': '1', 'field_name': 'seed', 'value': 34}]})
    assert changed.status_code == 200
    exported = client.get(f'/api/god_workflow/export/{workflow_id}?format=litegraph', headers=HEADERS)
    assert exported.status_code == 200
    assert exported.json()['nodes'][0]['widgets_values'] == [34, 20, 'untouched']
