"""RH 真实源与 ComfyUI 安全控制专项离线回归。"""
import json
import pytest
from gw.god_workflow.routes import _document_from_bundle, _compare_local_document
from gw.god_workflow.parser import WorkflowParseError, build_topology_from_public_detail

@pytest.mark.parametrize('wrapper', ['workflowContent', 'workflow', 'prompt'])
def test_rh_embedded_source_preserved(wrapper):
    graph = {'nodes': [{'id': 7, 'type': 'MiniMax', 'pos': [123, 456], 'inputs': [], 'widgets_values': ['real']}, {'id': 9, 'type': 'SaveImage', 'pos': [600, 456], 'inputs': []}], 'links': [[31, 7, 2, 9, 0, 'IMAGE']]}
    doc = _document_from_bundle({'detail': {wrapper: json.dumps(graph)}}).as_dict()
    assert doc['nodes'][0]['position'] == {'x': 123., 'y': 456.}
    assert [(c['source'], c['target'], c['source_slot']) for c in doc['connections']] == [('7', '9', '2')]

def test_public_inventory_never_fabricates_graph():
    with pytest.raises(WorkflowParseError):
        build_topology_from_public_detail({'primitiveNodes': ['LoadImage', 'KSampler', 'SaveImage']})

def test_empty_diagnostic_not_ready(monkeypatch):
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    monkeypatch.setattr('gw.god_workflow.routes._probe_comfy_url', lambda _: {})
    result = _compare_local_document({'nodes': [], 'connections': []})
    assert result['status'] != 'compared'
    assert result['recommended_target'] != 'local_comfy'
