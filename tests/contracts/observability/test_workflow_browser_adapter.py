"""自动页面适配与真实定义回传；只使用每例隔离目录和宿主替身。"""
import json
import io
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core.errors import CleanroomException
from gw.god_workflow import browser_bridge, routes

HEADERS = {'Authorization': 'Bearer automatic-adapter', 'Origin': 'http://testserver'}
GRAPH = {'version': 0.4, 'nodes': [{'id': 1, 'type': 'ActualNode', 'pos': [0, 0],
    'widgets_values': ['actual-value', 7], 'inputs': [], 'outputs': []}], 'links': []}
SCHEMA = {'input': {'required': {'text': ['STRING', {}], 'count': ['INT', {}]}},
          'input_order': {'required': ['text', 'count']}}


def test_verified_cache_works_offline_without_host_and_is_principal_isolated(monkeypatch):
    online = True
    probes = []
    controls = []
    def control(context, action):
        controls.append(action)
        return {'highlight': online}
    async def probe():
        probes.append(True)
        return {'online': True, 'object_info': {'ActualNode': SCHEMA}}
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    monkeypatch.setattr(routes, '_comfy_operation', control)
    monkeypatch.setattr('gw.god_workflow.comfy_executor.probe', probe)
    with TestClient(create_app()) as client:
        first = client.post('/api/god_workflow/parse-upload?source=canvas', json=GRAPH, headers=HEADERS)
        assert first.status_code == 200 and first.json()['needs_definition_resolution'] is False
        online = False
        second = client.post('/api/god_workflow/parse-upload?source=canvas', json=GRAPH, headers=HEADERS)
        assert second.status_code == 200 and second.json()['needs_definition_resolution'] is False
        assert second.json()['metadata']['schema_source'] == 'verified_cache'
        other = client.post('/api/god_workflow/parse-upload?source=canvas', json=GRAPH,
                            headers={**HEADERS, 'Authorization': 'Bearer isolated-other'})
        assert other.status_code == 200 and other.json()['needs_definition_resolution'] is True
        assert len(probes) == 1 and controls == ['status', 'status']


@pytest.mark.parametrize('text_prompt', [False, True])
def test_picture_without_graph_metadata_is_rejected_without_changing_saved_workflow(monkeypatch, text_prompt):
    from PIL import Image, PngImagePlugin
    monkeypatch.setattr(routes, '_comfy_operation', lambda *args: {'highlight': False})
    info = PngImagePlugin.PngInfo()
    if text_prompt:
        info.add_text('prompt', 'a picture description is not an API workflow')
    binary = io.BytesIO()
    Image.new('RGB', (2, 2)).save(binary, format='PNG', pnginfo=info)
    with TestClient(create_app()) as client:
        uploaded = client.post('/api/god_workflow/parse-upload?source=canvas', json=GRAPH, headers=HEADERS).json()
        path = routes._path(routes._auth(HEADERS['Authorization'], 'editor', True), uploaded['workflow_id'])
        before = path.read_bytes()
        rejected = client.post('/api/god_workflow/parse-upload', content=binary.getvalue(),
                               headers={**HEADERS, 'Content-Type': 'image/png'})
        assert rejected.status_code == 400
        assert path.read_bytes() == before


def test_host_compilation_cache_is_invalidated_by_execution_edits_and_not_importable(monkeypatch):
    from gw.god_workflow.compiler import compile_prompt
    monkeypatch.setattr(routes, '_comfy_operation', lambda *args: {'highlight': False})
    async def probe():
        return {'online': True, 'object_info': {'ActualNode': SCHEMA}}
    monkeypatch.setattr('gw.god_workflow.comfy_executor.probe', probe)
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    graph = {**GRAPH, 'nodes': [{**GRAPH['nodes'][0], 'widgets_values': ['UI-only', 'actual-value', 7]}]}
    prompt = {'1': {'class_type': 'ActualNode', 'inputs': {'text': 'actual-value', 'count': 7}}}
    with TestClient(create_app()) as client:
        uploaded = client.post('/api/god_workflow/parse-upload?source=canvas', json=graph, headers=HEADERS).json()
        workflow_id = uploaded['workflow_id']
        result = client.post('/api/god_workflow/comfy-bridge/sync-saved', headers=HEADERS,
            json={'workflow_id': workflow_id, 'workflow': graph, 'expected_version': 1,
                  'resolve_definitions': True, 'api_prompt': prompt,
                  'field_maps': {'1': {'widget_1': 'text', 'widget_2': 'count'}}})
        assert result.status_code == 200, result.text
        saved = client.get('/api/god_workflow/item/' + workflow_id, headers=HEADERS).json()
        fields = {}
        assert compile_prompt(saved, field_maps=fields) == prompt
        assert fields == {'1': {'widget_1': 'text', 'widget_2': 'count'}}
        # 端口名/顺序或自定义属性改变也不能沿用旧宿主编译图。
        from gw.god_workflow.compiler import execution_fingerprint
        for raw_change in ({'inputs': [{'name': 'changed-port', 'type': 'STRING'}]}, {'properties': {'custom_mode': 'changed'}}):
            changed_ports = deepcopy(saved)
            changed_ports['nodes'][0]['raw_payload'].update(raw_change)
            assert execution_fingerprint(changed_ports) != saved['metadata']['resolved_api']['fingerprint']
            with pytest.raises(CleanroomException):
                compile_prompt(changed_ports, changed_ports['metadata']['node_schemas'])
        changed = client.put('/api/god_workflow/item/' + workflow_id + '/params', headers=HEADERS,
            json={'expected_version': 2, 'widget_updates': [{'node_id': '1', 'field_name': 'widget_1', 'value': 'edited'}]})
        assert changed.status_code == 200, changed.text
        current = client.get('/api/god_workflow/item/' + workflow_id, headers=HEADERS).json()
        with pytest.raises(CleanroomException):
            compile_prompt(current, current['metadata'].get('node_schemas'))
        forged = {**graph, 'metadata': {'resolved_api': saved['metadata']['resolved_api']}}
        fresh = client.post('/api/god_workflow/parse-upload?source=canvas', json=forged, headers=HEADERS)
        assert fresh.status_code == 200 and 'resolved_api' not in fresh.json()['metadata']


def frontend(root):
    static = root / 'python_embeded/Lib/site-packages/comfyui_frontend_package/static'
    app = static / 'scripts/app.js'
    app.parent.mkdir(parents=True)
    app.write_text('existing-host-app', encoding='utf-8')
    return static


def test_adapter_is_automatic_idempotent_and_does_not_change_host_core(tmp_path):
    static = frontend(tmp_path)
    before = (static / 'scripts/app.js').read_bytes()
    first = browser_bridge.prepare(str(tmp_path))
    target = static / 'extensions/gods_workbench/workbench.js'
    assert first == {'mode': 'managed', 'extension_url': browser_bridge.EXTENSION_URL}
    assert target.read_bytes() == browser_bridge.SCRIPT.read_bytes()
    stamp = target.stat().st_mtime_ns
    assert browser_bridge.prepare(str(tmp_path)) == first
    assert target.stat().st_mtime_ns == stamp
    assert (static / 'scripts/app.js').read_bytes() == before
    assert not (tmp_path / 'custom_nodes').exists()


@pytest.mark.parametrize('root', ['', 'relative/comfy'])
def test_adapter_rejects_unconfigured_or_relative_root(root):
    with pytest.raises(CleanroomException) as caught:
        browser_bridge.prepare(root)
    assert caught.value.code == 'COMFY_ADAPTER_ROOT_REQUIRED'


def test_adapter_unknown_frontend_and_foreign_file_remain_untouched(tmp_path):
    with pytest.raises(CleanroomException) as caught:
        browser_bridge.prepare(str(tmp_path))
    assert caught.value.code == 'COMFY_ADAPTER_FRONTEND_UNSUPPORTED'
    static = frontend(tmp_path)
    target = static / 'extensions/gods_workbench/workbench.js'
    target.parent.mkdir(parents=True)
    target.write_text('user-edited adapter', encoding='utf-8')
    with pytest.raises(CleanroomException) as caught:
        browser_bridge.prepare(str(tmp_path))
    assert caught.value.code == 'COMFY_ADAPTER_CONFLICT'
    assert target.read_text(encoding='utf-8') == 'user-edited adapter'


def test_failed_adapter_publish_leaves_no_partial_script(monkeypatch, tmp_path):
    static = frontend(tmp_path)
    def fail(*args):
        raise OSError('isolated publishing failure')
    monkeypatch.setattr(browser_bridge.os, 'link', fail)
    with pytest.raises(CleanroomException) as caught:
        browser_bridge.prepare(str(tmp_path))
    assert caught.value.code == 'COMFY_ADAPTER_WRITE_FAILED'
    assert list((static / 'extensions/gods_workbench').iterdir()) == []


@pytest.mark.parametrize('available', [False, True])
def test_host_definition_resolution_uses_real_schema_and_preserves_original_on_failure(monkeypatch, available):
    async def probe():
        return {'online': True, 'object_info': {'ActualNode': SCHEMA} if available else {}}
    monkeypatch.setattr('gw.god_workflow.comfy_executor.probe', probe)
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    with TestClient(create_app()) as client:
        response = client.post('/api/god_workflow/parse-upload?source=canvas', json=GRAPH, headers=HEADERS)
        assert response.status_code == 200 and response.json()['needs_definition_resolution'] is True
        workflow_id = response.json()['workflow_id']
        path = routes._path(routes._auth(HEADERS['Authorization'], 'editor', True), workflow_id)
        before = path.read_bytes()
        body = {'workflow_id': workflow_id, 'workflow': {**GRAPH, 'metadata': {'node_schemas': {'ActualNode': {'guessed': True}}}},
                'expected_version': 1, 'resolve_definitions': True}
        resolved = client.post('/api/god_workflow/comfy-bridge/sync-saved', json=body, headers=HEADERS)
        if not available:
            assert resolved.status_code == 422 and resolved.json()['detail']['code'] == 'NODE_SCHEMA_REQUIRED'
            assert path.read_bytes() == before
        else:
            assert resolved.status_code == 200 and resolved.json()['version'] == 2
            saved = json.loads(path.read_text(encoding='utf-8'))
            assert saved['metadata']['node_schemas']['ActualNode'] == SCHEMA
            assert saved['raw_payload']['version'] == 0.4
            assert saved['nodes'][0]['widgets'] == GRAPH['nodes'][0]['widgets_values']
            from gw.god_workflow.compiler import compile_prompt
            assert compile_prompt(saved, saved['metadata']['node_schemas'])['1']['inputs'] == {'text': 'actual-value', 'count': 7}


def test_concurrent_edit_during_real_definition_probe_is_not_overwritten(monkeypatch):
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    with TestClient(create_app()) as client:
        workflow_id = client.post('/api/god_workflow/parse-upload?source=canvas', json=GRAPH, headers=HEADERS).json()['workflow_id']
        path = routes._path(routes._auth(HEADERS['Authorization'], 'editor', True), workflow_id)
        async def probe():
            with routes._STORAGE_LOCK:
                current = routes._load(path)
                current.update(version=2, name='并发编辑保留')
                routes._atomic_write(path, current)
            return {'online': True, 'object_info': {'ActualNode': SCHEMA}}
        monkeypatch.setattr('gw.god_workflow.comfy_executor.probe', probe)
        resolved = client.post('/api/god_workflow/comfy-bridge/sync-saved', headers=HEADERS,
            json={'workflow_id': workflow_id, 'workflow': GRAPH, 'expected_version': 1, 'resolve_definitions': True})
        assert resolved.status_code == 409 and resolved.json()['detail']['code'] == 'VERSION_CONFLICT'
        saved = json.loads(path.read_text(encoding='utf-8'))
        assert saved['version'] == 2 and saved['name'] == '并发编辑保留'
        assert not saved['metadata'].get('node_schemas')
