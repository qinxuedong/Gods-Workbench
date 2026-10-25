"""工作流实际浏览器回归，账户、存储与网络均隔离。"""
import base64
import io
import json
import logging
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest
from workflow_support import goto_ready, settings_browser


def create_workflow(page, base, image, name='workflow'):
    response = page.context.request.post(base + '/api/god_workflow/documents?name=' + quote(name),
        data={'1': {'class_type': 'LoadImage', 'inputs': {'image': image}}},
        headers={'Origin': base})
    assert response.status == 201, response.text()
    return response.json()['workflow_id']


def test_deep_link_and_continuous_parameter_save_survive_reload(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        create_workflow(page, base, 'first.png')
        target = create_workflow(page, base, 'target.png')
        goto_ready(page, base + '/static/pages/workflow.html?id=' + target)
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        page.locator('.canvas-node[data-node-id="1"]').click()
        control = page.locator('[data-widget-control="1:image"]').first
        control.fill('edited.png')
        page.wait_for_function('() => state.currentWorkflow.version === 2')
        control.fill('edited-again.png')
        page.wait_for_function('() => state.currentWorkflow.version === 3')
        page.reload(wait_until='networkidle')
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        assert page.evaluate('state.currentWorkflow.nodes[0].inputs.image') == 'edited-again.png'
        assert errors == []


def test_modal_escape_focus_loop_and_return(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        target = create_workflow(page, base, 'target.png')
        goto_ready(page, base + '/static/pages/workflow.html?id=' + target)
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        page.locator('#btnViewTaskDrawer').click()
        page.locator('#taskModal.open').wait_for()
        page.wait_for_function('() => document.activeElement.id === "btnCloseTaskModal"')
        page.keyboard.press('Shift+Tab')
        assert page.evaluate('document.activeElement.id') == 'btnCloseTaskFooter'
        page.keyboard.press('Tab')
        assert page.evaluate('document.activeElement.id') == 'btnCloseTaskModal'
        page.keyboard.press('Escape')
        page.wait_for_function('() => !document.querySelector("#taskModal").classList.contains("open")')
        assert page.evaluate('document.activeElement.id') == 'btnViewTaskDrawer'
        assert errors == []


def test_settings_save_does_not_reimport_or_overwrite_current_workflow(monkeypatch, tmp_path):
    """真实页面保存配置不能触发来源重导入；供应商与本地探测均替身。"""
    from gw.god_workflow import platforms
    calls = []
    async def source(*args, **kwargs):
        calls.append('source')
        return {'workflow_id': '1234567890123456789', 'api_json': {
            '1': {'class_type': 'LoadImage', 'inputs': {'image': 'provider-original.png'}}}}
    monkeypatch.setattr(platforms, 'fetch_runninghub_bundle', source)
    monkeypatch.setattr('gw.god_workflow.routes._probe_comfy_url', lambda *args: None)
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        parsed = page.context.request.post(base + '/api/god_workflow/parse-link', headers={'Origin': base},
            data={'url_or_text': 'https://www.runninghub.cn/workflow/1234567890123456789'})
        assert parsed.status == 200, parsed.text()
        target = parsed.json()['workflow_id']
        changed = page.context.request.put(base + f'/api/god_workflow/item/{target}/params',
            headers={'Origin': base}, data={'expected_version': 1, 'widget_updates': [
                {'node_id': '1', 'field_name': 'image', 'value': 'user-edited.png'}]})
        assert changed.status == 200, changed.text()
        before = page.context.request.get(base + f'/api/god_workflow/item/{target}').json()
        goto_ready(page, base + '/static/pages/workflow.html?id=' + target)
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        page.locator('#btnOpenSettings').click()
        page.locator('#settingsModal.open').wait_for()
        page.locator('#inputComfyUrl').fill('http://127.0.0.1:8199')
        with page.expect_response(lambda response: urlsplit(response.url).path == '/api/god_workflow/settings'
                                  and response.request.method == 'POST') as saved:
            page.locator('#btnSaveSettings').click()
        assert saved.value.status == 200
        page.wait_for_function('() => !document.querySelector("#settingsModal").classList.contains("open")')
        page.wait_for_load_state('networkidle')
        assert calls == ['source']
        assert page.context.request.get(base + f'/api/god_workflow/item/{target}').json() == before
        page.reload(wait_until='networkidle')
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        assert page.evaluate('state.currentWorkflow.version') == 2
        assert page.evaluate('state.currentWorkflow.nodes[0].inputs.image') == 'user-edited.png'
        assert errors == []


def test_rh_credentials_can_be_typed_saved_and_used_after_reload(monkeypatch, tmp_path):
    """真实Chrome输入两字段；保存后立即用于解析，刷新仅展示掩码。"""
    from gw.god_workflow import platforms
    monkeypatch.delenv('GW_RUNNINGHUB_API_KEY', raising=False)
    monkeypatch.delenv('GW_RUNNINGHUB_ACCESS_TOKEN', raising=False)
    calls = []
    async def source(reference, *, api_key, access_token):
        calls.append((api_key, access_token))
        return {'workflow_id': '2108563268972404737', 'api_json': {
            '1': {'class_type': 'LoadImage', 'inputs': {'image': 'source.png'}}}}
    monkeypatch.setattr(platforms, 'fetch_runninghub_bundle', source)
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        goto_ready(page, base + '/static/pages/workflow.html')
        page.locator('#btnOpenSettings').click()
        page.locator('#settingsModal.open').wait_for()
        key_input, token_input = page.locator('#inputRhApiKey'), page.locator('#inputRhAccessToken')
        assert key_input.is_editable() and token_input.is_editable()
        assert key_input.get_attribute('type') == token_input.get_attribute('type') == 'password'
        key_input.fill('synthetic-browser-api-key')
        token_input.fill('synthetic-browser-access-token')
        with page.expect_response(lambda r: urlsplit(r.url).path == '/api/god_workflow/settings'
                                  and r.request.method == 'POST') as saved:
            page.locator('#btnSaveSettings').click()
        assert saved.value.status == 200
        assert 'synthetic-browser-api-key' not in saved.value.text()
        assert 'synthetic-browser-access-token' not in saved.value.text()
        page.locator('#settingsModal.open').wait_for(state='hidden')
        assert key_input.input_value() == token_input.input_value() == ''
        page.evaluate('(url) => handleParseLink(url)', 'https://www.runninghub.cn/workflow/2108563268972404737')
        assert calls == [('synthetic-browser-api-key', 'synthetic-browser-access-token')]
        page.reload(wait_until='networkidle')
        page.locator('#btnOpenSettings').click()
        page.locator('#settingsModal.open').wait_for()
        assert key_input.input_value() == token_input.input_value() == ''
        assert '本地加密' in page.locator('#maskedApiKeyHint').inner_text()
        assert '本地加密' in page.locator('#maskedTokenHint').inner_text()
        page.screenshot(path=str(tmp_path / 'rh-credentials-editable.png'))
        # 留空保存不会清除已有配置；勾选清除只清除对应字段。
        with page.expect_response(lambda r: urlsplit(r.url).path == '/api/god_workflow/settings'
                                  and r.request.method == 'POST') as preserved:
            page.locator('#btnSaveSettings').click()
        assert preserved.value.json()['has_rh_api_key'] and preserved.value.json()['has_rh_access_token']
        page.locator('#settingsModal.open').wait_for(state='hidden')
        page.locator('#btnOpenSettings').click()
        page.locator('#settingsModal.open').wait_for()
        page.locator('#clearRhApiKey').check()
        with page.expect_response(lambda r: urlsplit(r.url).path == '/api/god_workflow/settings'
                                  and r.request.method == 'POST') as cleared:
            page.locator('#btnSaveSettings').click()
        assert not cleared.value.json()['has_rh_api_key'] and cleared.value.json()['has_rh_access_token']
        assert errors == []


def test_rh_api_source_keeps_original_name_in_catalog_and_assets(monkeypatch, tmp_path):
    """原始API JSON无标题时补取公开名称，目录刷新与资产仍同名。"""
    from gw.god_workflow import platforms
    title, workflow_id = '原始 RH 工作流名称', '2108563268972404737'
    monkeypatch.delenv('GW_RUNNINGHUB_API_KEY', raising=False)
    monkeypatch.delenv('GW_RUNNINGHUB_ACCESS_TOKEN', raising=False)
    seen = []
    async def source(url, body, headers):
        seen.append(url)
        if url.endswith('getJsonApiFormat'):
            assert headers['Authorization'] == 'Bearer synthetic-name-browser-key'
            return {'code': 0, 'data': {'prompt': json.dumps({
                '1': {'class_type': 'LoadImage', 'inputs': {'image': 'source.png'}},
                '2': {'class_type': 'SaveImage', 'inputs': {'images': ['1', 0], 'filename_prefix': 'source'}}})}}
        assert url.endswith(('getDetail', '/api/portal/workflow/detail')), url
        assert 'Authorization' not in headers
        return {'code': 0, 'data': {'name': title, 'workflowContent': None}}
    monkeypatch.setattr(platforms, '_post_json', source)
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        goto_ready(page, base + '/static/pages/workflow.html')
        page.locator('#btnOpenSettings').click()
        page.locator('#settingsModal.open').wait_for()
        page.locator('#inputRhApiKey').fill('synthetic-name-browser-key')
        with page.expect_response(lambda r: urlsplit(r.url).path == '/api/god_workflow/settings'
                                  and r.request.method == 'POST') as saved:
            page.locator('#btnSaveSettings').click()
        assert saved.value.status == 200
        page.locator('#settingsModal.open').wait_for(state='hidden')
        page.evaluate('(url) => handleParseLink(url)', 'https://www.runninghub.cn/workflow/' + workflow_id)
        page.wait_for_function('(title) => state.currentWorkflow?.name === title', arg=title)
        assert title in page.locator('#workflowTreeList').inner_text()
        assert page.evaluate('state.currentWorkflow.connections.length') == 1
        asset_id = page.evaluate('state.currentWorkflow.asset_id')
        page.reload(wait_until='networkidle')
        page.wait_for_function('(title) => state.currentWorkflow?.name === title', arg=title)
        assert title in page.locator('#workflowTreeList').inner_text()
        page.screenshot(path=str(tmp_path / 'rh-original-name-reloaded.png'))
        goto_ready(page, base + '/static/embeds/asset-manager.html')
        page.locator('[data-tab="workflows"]').click()
        card = page.locator('[data-workflow-card="' + asset_id + '"]')
        card.wait_for()
        assert title in card.inner_text()
        assert len(seen) == 3
        assert seen[0].endswith('getDetail') and seen[1].endswith('/api/portal/workflow/detail')
        assert seen[2].endswith('getJsonApiFormat')
        assert errors == []


def test_rh_public_preview_preserves_editor_and_has_no_execution_controls(monkeypatch, tmp_path):
    """真实页面展示只读清单、隔离脚本内容，并验证关闭与焦点闭环。"""
    from gw.god_workflow import platforms
    async def source(*args, **kwargs):
        return {'workflow_id': '2108563268972404737', 'detail': {
            'name': 'MiniMax H3 公开预览', 'workflowContent': None, 'nodeCount': 15,
            'primitiveNodes': ['LoadImage'], 'customNodes': ['CreateVideo', '<img src=x onerror="window.previewInjected=1">'],
            'usedModels': ['公开模型']}}
    monkeypatch.setattr(platforms, 'fetch_runninghub_bundle', source)
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        target = create_workflow(page, base, 'keep-edited.png')
        goto_ready(page, base + '/static/pages/workflow.html?id=' + target)
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        before = page.evaluate('JSON.stringify(state.currentWorkflow)')
        page.locator('#btnParseLink').focus()
        await_preview = '(url) => handleParseLink(url)'
        page.evaluate(await_preview, 'https://www.runninghub.cn/workflow/2108563268972404737')
        modal = page.locator('#publicWorkflowPreviewModal.open')
        modal.wait_for()
        assert '不能执行或导出' in modal.inner_text()
        assert 'LoadImage' in modal.inner_text() and '公开模型' in modal.inner_text()
        assert modal.locator('img, input, textarea, select, a[download]').count() == 0
        assert modal.locator('button').count() == 2
        assert page.evaluate('window.previewInjected') is None
        assert page.evaluate('JSON.stringify(state.currentWorkflow)') == before
        page.wait_for_function('() => document.activeElement.id === "btnClosePublicWorkflowPreview"')
        page.keyboard.press('Shift+Tab')
        assert page.evaluate('document.activeElement.id') == 'btnClosePublicWorkflowPreviewFooter'
        page.keyboard.press('Tab')
        assert page.evaluate('document.activeElement.id') == 'btnClosePublicWorkflowPreview'
        page.screenshot(path=str(tmp_path / 'rh-public-preview.png'))
        page.keyboard.press('Escape')
        page.wait_for_function('() => !document.querySelector("#publicWorkflowPreviewModal").classList.contains("open")')
        assert page.evaluate('document.activeElement.id') == 'btnParseLink'
        page.evaluate(await_preview, 'https://www.runninghub.cn/post/2084124735289520130')
        modal.wait_for()
        page.locator('#btnClosePublicWorkflowPreviewFooter').click()
        modal.wait_for(state='hidden')
        assert page.evaluate('JSON.stringify(state.currentWorkflow)') == before
        assert errors == []


def test_imported_workflow_catalog_survives_metadata_and_reload(monkeypatch, tmp_path):
    """导入源图后配置/剪贴板/素材清单不会污染目录，刷新可回读原图与资产。"""
    from gw.god_workflow import platforms, registry
    workflow_id = 'liblib_18678492'
    async def source(*args, **kwargs):
        return {'workflow_id': workflow_id, 'name': '公开Liblib工作流', 'canvas_json': {
            'nodes': [{'id': 1, 'type': 'LoadImage', 'pos': [10, 20], 'widgets_values': ['original.png']},
                      {'id': 2, 'type': 'SaveImage', 'pos': [300, 20], 'widgets_values': ['result']}],
            'links': [[1, 1, 0, 2, 0, 'IMAGE']]}}
    monkeypatch.setattr(platforms, 'fetch_liblib_bundle', source)
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        goto_ready(page, base + '/static/pages/workflow.html')
        settings = page.context.request.post(base + '/api/god_workflow/settings',
            headers={'Origin': base}, data={'comfy_url': 'http://127.0.0.1:8199'})
        assert settings.status == 200
        clipboard = page.context.request.get(base + '/api/god_workflow/clipboard?text=' + quote('https://www.liblib.art/modelinfo/source'))
        assert clipboard.status == 200, clipboard.text()
        # 同一主体的素材清单属于内部状态；通过已建立的文档路径定位临时根。
        created = create_workflow(page, base, 'existing.png')
        doc_path = next((tmp_path / 'gw-data/workflow').glob(f'*/{created}.json'))
        registry._atomic_write(doc_path.parent / 'assets.json', [])
        folder_response = page.context.request.post(base + '/api/god_workflow/folders',
            headers={'Origin': base}, data={'name': '实际导入目录'})
        folder_id = folder_response.json()['folder']['folder_id']
        page.evaluate('(id) => {state.activeFolderId = id}', folder_id)
        page.evaluate('(url) => handleParseLink(url)',
            'https://www.liblib.art/modelinfo/deecad9292404e24bc0f1cb8609c7270?versionUuid=078841a37d4844fda4f2a6f859939e2b')
        row = page.locator('#workflowTreeList [data-wf-id="' + workflow_id + '"]')
        row.wait_for()
        saved = page.context.request.get(base + '/api/god_workflow/item/' + workflow_id).json()
        assert saved['folder_id'] == folder_id and len(saved['connections']) == 1
        page.reload(wait_until='networkidle')
        row.wait_for()
        assert page.locator('#btnReloadDemo').count() == 0
        assert page.locator('#workflowTreeList [data-wf-id="settings"], #workflowTreeList [data-wf-id="assets"], #workflowTreeList [data-wf-id="clipboard"]').count() == 0
        row.click()
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=workflow_id)
        assert page.evaluate('state.currentWorkflow.nodes[0].widgets[0].value') == 'original.png'
        assert page.context.request.get(base + '/api/god_workflow/item/' + workflow_id).json() == saved
        goto_ready(page, base + '/static/embeds/asset-manager.html')
        page.locator('[data-tab="workflows"]').click()
        page.locator('[data-workflow-card="' + saved['asset_id'] + '"]').wait_for()
        assert errors == []


@pytest.mark.parametrize('two_parameters', [False, True])
def test_native_bridge_load_save_and_conflict_in_isolated_browser_host(monkeypatch, tmp_path, two_parameters):
    """使用真实扩展和浏览器消息链；宿主 app 为隔离替身，不代表 ComfyUI 现场验收。"""
    root = tmp_path / 'comfy-native-host'
    app_file = root / 'python_embeded/Lib/site-packages/comfyui_frontend_package/static/scripts/app.js'
    app_file.parent.mkdir(parents=True)
    app_file.write_text('isolated', encoding='utf-8')
    monkeypatch.setenv('GW_COMFYUI_ROOT_DIR', str(root))
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    async def probe():
        return {'online': True, 'object_info': {
            'LoadImage': {'input': {'required': {'image': ['STRING', {}]}}},
            'ImageAndLabel': {'input': {'required': {'image': ['STRING', {}], 'label': ['STRING', {}]}}},
        }}
    monkeypatch.setattr('gw.god_workflow.comfy_executor.probe', probe)
    monkeypatch.setattr('gw.god_workflow.routes._comfy_operation', lambda *args: {'highlight': True})
    script = (Path(__file__).resolve().parents[2] / 'tools/comfy_workbench_bridge/js/workbench.js').read_text(encoding='utf-8')
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        def host(route):
            path = urlsplit(route.request.url).path
            if path == '/scripts/app.js':
                body = 'export const app = {registerExtension: e => e.setup(), loadGraphData: async g => {window.loadedGraph = structuredClone(g)}, loadApiJson: async g => {window.loadedGraph = {version:0.4,links:[],nodes:Object.entries(g).map(([id,n])=>({id:Number(id),type:n.class_type,pos:[0,0],inputs:[],outputs:[],widgets_values:n.class_type==="ImageAndLabel"?[n.inputs.image,n.inputs.label]:[n.inputs.image]}))}}, graphToPrompt: async()=>({output:Object.fromEntries(window.loadedGraph.nodes.map(n=>[String(n.id),{class_type:n.type,inputs:n.type==="ImageAndLabel"?{image:n.widgets_values[0],label:n.widgets_values[1]}:{image:n.widgets_values[0]}}]))}), graph: {serialize: () => structuredClone(window.loadedGraph)}};'
                kind = 'application/javascript'
            elif path.endswith('/workbench.js'):
                body, kind = script, 'application/javascript'
            else:
                body, kind = '<!doctype html><html><body><script type="module" src="/extensions/comfy_workbench_bridge/workbench.js"></script></body></html>', 'text/html'
            route.fulfill(status=200, body=body, content_type=kind)
        page.context.route('http://127.0.0.1:8199/**', host)
        target = create_workflow(page, base, 'source.png')
        if two_parameters:
            response = page.context.request.post(base + '/api/god_workflow/documents', headers={'Origin': base},
                data={'1': {'class_type': 'ImageAndLabel', 'inputs': {'label': 'keep', 'image': 'source.png'}}})
            assert response.status == 201, response.text()
            target = response.json()['workflow_id']
        goto_ready(page, base + '/static/pages/workflow.html?id=' + target)
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        if not two_parameters:
            page.locator('.wf-capsule[data-wf-id="' + target + '"]').click(button='right')
        with page.expect_popup() as opened:
            page.locator('#btnImportComfyUi' if two_parameters else '#ctxEditNativeWorkflow').click()
        native = opened.value
        native.wait_for_function('() => window.loadedGraph?.nodes?.length === 1')
        if two_parameters:
            assert native.evaluate('window.loadedGraph.nodes[0].widgets_values') == ['source.png', 'keep']
        page.wait_for_function('() => state.comfyBridgeSession?.connected === true')
        page.wait_for_function('state.currentWorkflow.version === 2')
        native.evaluate('window.loadedGraph.nodes[0].widgets_values[0] = "native-edited.png"')
        native.get_by_role('button', name='将当前工作流回存神工坊').filter(has_text='已回存').wait_for()
        native.get_by_role('button', name='将当前工作流回存神工坊').click()
        page.wait_for_function('() => state.currentWorkflow.version === 3')
        saved = page.context.request.get(base + '/api/god_workflow/item/' + target).json()
        assert saved['nodes'][0]['widgets'][0] == 'native-edited.png'
        if two_parameters:
            assert saved['nodes'][0]['widgets'][1] == 'keep'
        native.get_by_role('button', name='将当前工作流回存神工坊').filter(has_text='已回存').wait_for()
        changed = page.context.request.put(base + '/api/god_workflow/item/' + target + '/name',
            data={'name': '并发编辑', 'expected_version': 3}, headers={'Origin': base})
        assert changed.status == 200
        native.get_by_role('button', name='将当前工作流回存神工坊').click()
        native.get_by_role('button', name='将当前工作流回存神工坊').filter(has_text='回存失败').wait_for()
        assert page.context.request.get(base + '/api/god_workflow/item/' + target).json()['version'] == 4
        # 原页结束桥会话后，按钮超时解除；迟到的旧回执不能解锁下一次回存。
        page.evaluate('''() => {
            state.comfyBridgeSession = null; window.bridgeRequests = [];
            window.addEventListener('message', event => {
                if (event.data?.kind === 'gw-comfy-save') window.bridgeRequests.push(event.data.request_id);
            });
        }''')
        native.clock.install()
        native.get_by_role('button', name='将当前工作流回存神工坊').click()
        page.wait_for_function('window.bridgeRequests.length === 1')
        first_request = page.evaluate('window.bridgeRequests[0]')
        native.clock.fast_forward(30_001)
        native.get_by_role('button', name='将当前工作流回存神工坊').filter(has_text='未确认').wait_for()
        native.get_by_role('button', name='将当前工作流回存神工坊').click()
        page.wait_for_function('window.bridgeRequests.length === 2')
        assert page.evaluate('window.bridgeRequests[1]') != first_request
        native.evaluate('''data => window.dispatchEvent(new MessageEvent('message', {
            origin:data.origin, source:window.opener, data:{kind:'gw-comfy-save-result', ok:true,
                version:9, request_id:data.request, nonce:new URL(location.href).searchParams.get('gw_nonce')}
        }))''', {'origin': base, 'request': first_request})
        assert native.get_by_role('button', name='将当前工作流回存神工坊').is_disabled()
        native.clock.fast_forward(30_001)
        assert not native.get_by_role('button', name='将当前工作流回存神工坊').is_disabled()
        assert errors == []


def test_cf_lifecycle_and_import_button_have_separate_responsibilities(monkeypatch, tmp_path):
    """真实Chrome点击，CF假进程隔离；不启停用户ComfyUI。"""
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    current = {'status': 'offline', 'highlight': False, 'managed': False, 'ok': True}
    actions = []
    def operate(context, action, token=None):
        actions.append(action)
        if action == 'start':
            current.update(status='starting', highlight=False, managed=True, can_stop=True)
        elif action == 'stop':
            current.update(status='offline', highlight=False, managed=False, can_stop=False)
        return dict(current)
    monkeypatch.setattr('gw.god_workflow.routes._comfy_operation', operate)
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        paths = []
        page.on('request', lambda request: paths.append(urlsplit(request.url).path))
        goto_ready(page, base + '/static/pages/workflow.html')
        page.wait_for_function('() => state.authLoading === false')
        button = page.get_by_role('button', name='导入 ComfyUI', exact=True)
        button.click()
        page.get_by_text('请先选择或解析一个工作流，再导入 ComfyUI', exact=True).wait_for()
        assert not any('/open-in-comfy' in path for path in paths)
        target = create_workflow(page, base, 'input.png')
        goto_ready(page, base + '/static/pages/workflow.html?id=' + target)
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        assert page.evaluate('document.getElementById("btnParseLink").nextElementSibling.id') == 'btnImportComfyUi'
        button.click()
        page.get_by_text('连不上 ComfyUI：ComfyUI 未在线，请先使用全站 CF 控件启动或连接服务', exact=True).wait_for()
        assert 'start' not in actions and len(page.context.pages) == 1
        paths.clear()
        toggle = page.locator('[data-gw-comfy-toggle]')
        page.wait_for_function('() => document.querySelector("[data-gw-comfy-toggle]").getAttribute("aria-busy") === "false"')
        toggle.click()
        page.wait_for_function('() => document.querySelector("[data-gw-comfy-toggle]").dataset.gwComfyStatus === "starting"')
        assert toggle.get_attribute('aria-pressed') == 'false'
        assert '取消启动' in toggle.get_attribute('title')
        assert not any('/open-in-comfy' in path for path in paths)
        button.click()
        page.get_by_text('导入 ComfyUI 失败：ComfyUI 正在启动，请等待右上角 CF 确认运行后再导入', exact=True).wait_for()
        assert actions.count('start') == 1 and len(page.context.pages) == 1
        page.wait_for_function('() => document.querySelector("[data-gw-comfy-toggle]").getAttribute("aria-busy") === "false"')
        toggle.click()
        page.wait_for_function('() => document.querySelector("[data-gw-comfy-toggle]").dataset.gwComfyStatus === "offline"')
        assert actions.count('stop') == 1
        assert errors == []


def test_anonymous_import_click_has_feedback_without_business_request(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        page.context.clear_cookies()
        paths = []
        page.on('request', lambda request: paths.append(urlsplit(request.url).path))
        goto_ready(page, base + '/static/pages/workflow.html')
        page.get_by_role('button', name='导入 ComfyUI', exact=True).click()
        page.get_by_text('请先登录神工坊，再导入 ComfyUI', exact=True).wait_for()
        assert not any('/open-in-comfy' in path for path in paths)
        assert len(page.context.pages) == 1 and errors == []


def test_project_managed_adapter_loads_plain_native_json_and_reuses_window(monkeypatch, tmp_path):
    """真实仓库脚本由导入路由自动提供；宿主app隔离替身，不接触用户ComfyUI。"""
    host_root = tmp_path / 'comfy-host'
    static = host_root / 'python_embeded/Lib/site-packages/comfyui_frontend_package/static'
    app_file = static / 'scripts/app.js'
    app_file.parent.mkdir(parents=True)
    app_file.write_text('isolated-native-app', encoding='utf-8')
    monkeypatch.setenv('GW_COMFYUI_ROOT_DIR', str(host_root))
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    monkeypatch.setattr('gw.god_workflow.routes._comfy_operation', lambda *args: {'highlight': True, 'status': 'online'})
    async def probe():
        return {'online': True, 'object_info': {'ImageAndLabel': {'input': {'required': {'image': ['STRING', {}], 'label': ['STRING', {}]}}}}}
    monkeypatch.setattr('gw.god_workflow.comfy_executor.probe', probe)
    graph = {'version': 0.4, 'nodes': [{'id': 1, 'type': 'ImageAndLabel', 'pos': [10, 20],
        'widgets_values': ['direct-json.png', '保留第二参数'], 'inputs': [], 'outputs': []}], 'links': [],
        'groups': [{'title': '保留分组'}]}
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        def host(route):
            path = urlsplit(route.request.url).path
            if path == '/scripts/app.js':
                body = 'export const app = {registerExtension: e => e.setup(), loadGraphData: async g => {window.loadedGraph = structuredClone(g)}, graph: {serialize: () => structuredClone(window.loadedGraph)}};'
                kind = 'application/javascript'
            elif path == '/extensions/gods_workbench/workbench.js':
                body = (static / 'extensions/gods_workbench/workbench.js').read_text(encoding='utf-8')
                kind = 'application/javascript'
            else:
                body = '<!doctype html><script type="module" src="/extensions/gods_workbench/workbench.js"></script>'
                kind = 'text/html'
            route.fulfill(status=200, body=body, content_type=kind)
        page.context.route('http://127.0.0.1:8199/**', host)
        goto_ready(page, base + '/static/pages/workflow.html')
        native_file = tmp_path / 'ordinary-workflow.json'
        native_file.write_text(json.dumps(graph), encoding='utf-8')
        page.wait_for_function('window.WorkbenchComfy.getStatus() === "online"')
        page.locator('#localFileInput').set_input_files(native_file)
        page.wait_for_function('state.currentWorkflow?.nodes?.length === 1')
        target = page.evaluate('state.currentWorkflow.workflow_id')
        # 内部真实定义足够时既不打开宿主页，也不安装页面适配。
        assert len(page.context.pages) == 1
        assert not (static / 'extensions/gods_workbench/workbench.js').exists()
        saved = page.context.request.get(base + '/api/god_workflow/item/' + target).json()
        assert saved['metadata']['node_schemas']['ImageAndLabel']['input']['required']['label'] == ['STRING', {}]
        assert saved['version'] == 1 and saved['metadata']['needs_host_resolution'] is False
        with page.expect_popup() as opened:
            page.get_by_role('button', name='导入 ComfyUI', exact=True).click()
        native = opened.value
        native.wait_for_function('window.loadedGraph?.nodes?.length === 1')
        assert native.evaluate('window.loadedGraph.nodes[0].widgets_values') == ['direct-json.png', '保留第二参数']
        assert native.evaluate('window.loadedGraph.groups') == graph['groups']
        assert native.evaluate('window.loadedGraph.version') == 0.4
        assert 'rh_workflow' not in native.url and 'gw_workflow=' + target in native.url
        page.wait_for_function('state.comfyBridgeSession?.connected === true')
        adapter = static / 'extensions/gods_workbench/workbench.js'
        assert adapter.read_bytes() == (Path(__file__).resolve().parents[2] / 'tools/comfy_workbench_bridge/js/workbench.js').read_bytes()
        before = adapter.stat().st_mtime_ns
        with native.expect_navigation(wait_until='load'):
            page.get_by_role('button', name='导入 ComfyUI', exact=True).click()
        native.wait_for_function('window.loadedGraph?.nodes?.length === 1')
        page.wait_for_function('state.comfyBridgeSession?.connected === true')
        assert len(page.context.pages) == 2 and adapter.stat().st_mtime_ns == before
        assert errors == []


@pytest.mark.parametrize('entry', ['upload', 'drop', 'clipboard'])
def test_embedded_png_workflow_uses_real_metadata_without_opening_host(monkeypatch, tmp_path, entry):
    from PIL import Image, PngImagePlugin
    graph = {'version': 0.4, 'nodes': [{'id': 1, 'type': 'ActualNode', 'pos': [12, 34],
        'inputs': [], 'outputs': [], 'widgets_values': ['metadata-value', 9]}], 'links': []}
    info = PngImagePlugin.PngInfo()
    info.add_text('workflow', json.dumps(graph))
    binary = io.BytesIO()
    Image.new('RGB', (2, 2)).save(binary, format='PNG', pnginfo=info)
    encoded = base64.b64encode(binary.getvalue()).decode('ascii')
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    monkeypatch.setattr('gw.god_workflow.routes._comfy_operation', lambda *args: {'highlight': True, 'status': 'online'})
    async def probe():
        return {'online': True, 'object_info': {'ActualNode': {'input': {'required': {'text': ['STRING', {}], 'count': ['INT', {}]}}}}}
    monkeypatch.setattr('gw.god_workflow.comfy_executor.probe', probe)
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        if entry == 'clipboard':
            # 隔离系统剪贴板，实际控制器读到含原始元数据的ClipboardItem。
            page.add_init_script('''Object.defineProperty(navigator, 'clipboard', {{value: {{
              readText: async()=>'', read: async()=>[{{types:['image/png'], getType: async()=>
              new Blob([Uint8Array.from(atob('{0}'), c=>c.charCodeAt(0))], {{type:'image/png'}})}}]
            }}}});'''.format(encoded))
        goto_ready(page, base + '/static/pages/workflow.html')
        if entry == 'upload':
            image = tmp_path / 'embedded.png'
            image.write_bytes(binary.getvalue())
            page.locator('#localFileInput').set_input_files(image)
        elif entry == 'drop':
            page.evaluate('''encoded=>{const data=new DataTransfer();
              data.items.add(new File([Uint8Array.from(atob(encoded), c=>c.charCodeAt(0))], 'embedded.png', {type:'image/png'}));
              document.getElementById('canvasViewport').dispatchEvent(new DragEvent('drop', {bubbles:true,dataTransfer:data}));}''', encoded)
        else:
            history = page.context.request.get(base + '/api/god_workflow/clipboard?text=' + quote('https://www.runninghub.cn/workflow/9999999999999999999'))
            assert history.status == 200
            page.locator('#btnParseLink').click()
        page.wait_for_function('state.currentWorkflow?.nodes?.length === 1')
        current = page.evaluate('state.currentWorkflow')
        saved = page.context.request.get(base + '/api/god_workflow/item/' + current['workflow_id']).json()
        assert saved['nodes'][0]['widgets'] == ['metadata-value', 9]
        assert saved['raw_payload'] == graph
        assert saved['metadata']['needs_host_resolution'] is False
        assert len(page.context.pages) == 1 and errors == []


def test_native_page_resolution_is_last_resort_for_unsupported_widget_mapping(monkeypatch, tmp_path):
    host_root = tmp_path / 'last-resort-host'
    static = host_root / 'python_embeded/Lib/site-packages/comfyui_frontend_package/static'
    (static / 'scripts').mkdir(parents=True)
    (static / 'scripts/app.js').write_text('isolated', encoding='utf-8')
    monkeypatch.setenv('GW_COMFYUI_ROOT_DIR', str(host_root))
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    controls = []
    def control(context, action):
        controls.append(action)
        return {'status': 'online', 'highlight': True}
    monkeypatch.setattr('gw.god_workflow.routes._comfy_operation', control)
    async def probe():
        return {'online': True, 'object_info': {'ActualNode': {'input': {'required': {'text': ['STRING', {}], 'count': ['INT', {}]}}}}}
    monkeypatch.setattr('gw.god_workflow.comfy_executor.probe', probe)
    graph = {'version': 0.4, 'nodes': [{'id': 1, 'type': 'ActualNode', 'pos': [12, 34],
        'inputs': [], 'outputs': [], 'widgets_values': ['UI-only', 'native-text', 9]}], 'links': []}
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        def host(route):
            path = urlsplit(route.request.url).path
            if path == '/scripts/app.js':
                body = '''export const app={registerExtension:e=>e.setup(),
                  loadGraphData:async g=>{window.loadedGraph=structuredClone(g)},
                  graphToPrompt:async()=>({output:{'1':{class_type:'ActualNode',inputs:{text:window.loadedGraph.nodes[0].widgets_values[1],count:window.loadedGraph.nodes[0].widgets_values[2]}}}}),
                  graph:{serialize:()=>structuredClone(window.loadedGraph),
                    get _nodes(){return [{id:1,widgets:[{name:'ui'},{name:'text'},{name:'count'}]}]}}};'''
                kind = 'application/javascript'
            elif path.endswith('/workbench.js'):
                body = (static / 'extensions/gods_workbench/workbench.js').read_text(encoding='utf-8')
                kind = 'application/javascript'
            else:
                body = '<!doctype html><script type="module" src="/extensions/gods_workbench/workbench.js"></script>'
                kind = 'text/html'
            route.fulfill(status=200, content_type=kind, body=body)
        page.context.route('http://127.0.0.1:8199/**', host)
        goto_ready(page, base + '/static/pages/workflow.html')
        native_file = tmp_path / 'custom-native.json'
        native_file.write_text(json.dumps(graph), encoding='utf-8')
        with page.expect_popup() as opened:
            page.locator('#localFileInput').set_input_files(native_file)
        opened.value.wait_for_function('window.loadedGraph?.nodes?.length === 1')
        page.wait_for_function('state.currentWorkflow?.version === 2')
        workflow_id = page.evaluate('state.currentWorkflow.workflow_id')
        saved = page.context.request.get(base + '/api/god_workflow/item/' + workflow_id).json()
        assert saved['metadata']['needs_host_resolution'] is False
        assert saved['metadata']['resolved_api']['prompt']['1']['inputs'] == {'text': 'native-text', 'count': 9}
        assert saved['metadata']['resolved_api']['field_maps'] == {'1': {'widget_1': 'text', 'widget_2': 'count'}}
        assert saved['raw_payload']['nodes'][0]['widgets_values'] == graph['nodes'][0]['widgets_values']
        assert set(controls) == {'status'} and errors == []


@pytest.mark.parametrize('legacy_zero', [False, True])
def test_native_format_version_can_save_then_submit_local_in_browser(monkeypatch, tmp_path, legacy_zero):
    """原生0.4新导入与旧零版本均通过真实页面CAS保存，执行器隔离替身。"""
    from gw.god_workflow import comfy_executor
    submissions = []
    async def probe():
        return {'online': True, 'object_info': {'LoadImage': {'input': {'required': {'image': ['STRING', {}]}}}}}
    async def submit(prompt, **kwargs):
        submissions.append(prompt)
        return {'prompt_id': 'isolated-native-version'}
    async def history(*args, **kwargs):
        return {'found': False}
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    monkeypatch.setattr(comfy_executor, 'probe', probe)
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    monkeypatch.setattr(comfy_executor, 'history', history)
    monkeypatch.setattr('gw.god_workflow.routes._comfy_operation', lambda *args: {'highlight': False})
    graph = {'version': 0.4, 'nodes': [{'id': 1, 'type': 'LoadImage', 'pos': [10, 20],
        'widgets_values': ['original.png'], 'inputs': [], 'outputs': []}], 'links': []}
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        response = page.context.request.post(base + '/api/god_workflow/parse-upload?source=canvas',
            data=graph, headers={'Origin': base})
        assert response.status == 200, response.text()
        target = response.json()['workflow_id']
        if legacy_zero:
            import os
            storage = Path(os.environ['GW_DATA_DIR'])
            assert storage.is_relative_to(tmp_path)
            files = list(storage.rglob(target + '.json'))
            assert len(files) == 1
            stored = json.loads(files[0].read_text(encoding='utf-8'))
            stored['version'] = 0
            files[0].write_text(json.dumps(stored), encoding='utf-8')
        goto_ready(page, base + '/static/pages/workflow.html?id=' + target)
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        assert page.evaluate('state.currentWorkflow.version') == 1
        page.locator('.canvas-node[data-node-id="1"]').click()
        page.locator('[data-widget-control="1:widget_0"]').first.fill('native-edited.png')
        page.wait_for_function('state.currentWorkflow.version === 2')
        page.locator('#btnTargetLocal').click()
        page.locator('#btnExecuteWorkflow').click()
        page.wait_for_function('state.activeTaskId !== null')
        assert len(submissions) == 1 and submissions[0]['1']['inputs']['image'] == 'native-edited.png'
        saved = page.context.request.get(base + '/api/god_workflow/item/' + target).json()
        task = page.context.request.get(base + '/api/god_workflow/tasks').json()['tasks'][0]
        assert task['workflow_version'] == saved['version']
        assert saved['nodes'][0]['widgets'] == ['native-edited.png']
        assert saved['raw_payload']['version'] == 0.4
        assert errors == []


def test_delete_last_workflow_stays_empty_after_reload(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        target = create_workflow(page, base, 'delete.png')
        goto_ready(page, base + '/static/pages/workflow.html?id=' + target)
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        page.locator('.wf-capsule[data-wf-id="' + target + '"]').click(button='right')
        page.locator('#ctxDeleteWf').click()
        page.wait_for_function('() => state.currentWorkflow === null && state.workflows.length === 0')
        assert page.locator('.canvas-node').count() == 0
        page.reload(wait_until='networkidle')
        page.wait_for_function('() => state.authLoading === false')
        assert page.evaluate('state.workflows.length') == 0
        assert page.context.request.get(base + '/api/god_workflow/list').json()['items'] == []
        assert errors == []


def test_rebind_after_root_replacement_restores_real_controls(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        target = create_workflow(page, base, 'root.png')
        goto_ready(page, base + '/static/pages/workflow.html?id=' + target)
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        old_timer = page.evaluate('state.comfySyncTimer')
        page.evaluate('''() => {
            const root = document.getElementById('workflowWorkbench');
            root.replaceWith(root.cloneNode(true));
            window.WorkbenchWorkflow.rebind();
        }''')
        page.wait_for_function('() => state.authLoading === false && lifecycleAbortController !== null')
        assert page.evaluate('state.comfySyncTimer') != old_timer
        page.locator('#btnViewTaskDrawer').click()
        page.locator('#taskModal.open').wait_for()
        page.keyboard.press('Escape')
        page.wait_for_function('() => !document.querySelector("#taskModal").classList.contains("open")')
        assert page.evaluate('document.activeElement.id') == 'btnViewTaskDrawer'
        assert errors == []


def test_native_wire_centres_type_and_source_at_zoom_and_pan(monkeypatch, tmp_path):
    """以实际 DOM 与 SVG 变换核验端口，避免只比较计算公式。"""
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        graph = {'nodes': [
            {'id': 1, 'type': 'LoadImage', 'pos': [50, 80], 'size': [240, 240],
             'widgets_values': ['isolated.png'], 'inputs': [],
             'outputs': [{'name': 'IMAGE', 'type': 'IMAGE'}]},
            {'id': 2, 'type': 'SaveImage', 'pos': [480, 80], 'size': [230, 190],
             'widgets_values': ['output'], 'inputs': [{'name': 'images', 'type': 'IMAGE', 'link': 5}],
             'outputs': []}], 'links': [[5, 1, 0, 2, 0, 'IMAGE']], 'version': 0.4}
        saved = page.context.request.post(base + '/api/god_workflow/documents?source=canvas',
            data=graph, headers={'Origin': base})
        assert saved.status == 201, saved.text()
        target = saved.json()['workflow_id']
        goto_ready(page, base + '/static/pages/workflow.html?id=' + target)
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        assert page.locator('.wf-tree-item[data-wf-id="' + target + '"] .wf-source-badge').inner_text() == 'LOCAL'
        for zoom in (1, 0.5, 1.7):
            result = page.evaluate('''zoom => {
                state.zoom = zoom; state.panX = -90; state.panY = 65;
                applyCanvasTransform(); renderSvgWires(state.currentWorkflow);
                const path = document.querySelector('#svgLinksGroup path');
                const matrix = path.getScreenCTM();
                const start = path.getPointAtLength(0).matrixTransform(matrix);
                const end = path.getPointAtLength(path.getTotalLength()).matrixTransform(matrix);
                const centre = selector => {const r = document.querySelector(selector).getBoundingClientRect();
                    return {x:r.left+r.width/2, y:r.top+r.height/2};};
                const source = centre('.canvas-node[data-node-id="1"] .ports-col-out .port-dot');
                const target = centre('.canvas-node[data-node-id="2"] .ports-col-in .port-dot');
                return {start:Math.hypot(start.x-source.x,start.y-source.y),
                    end:Math.hypot(end.x-target.x,end.y-target.y), stroke:path.getAttribute('stroke')};
            }''', zoom)
            assert result['start'] < 1, result
            assert result['end'] < 1, result
            assert result['stroke'] == 'url(#gradIMAGE)', result
        header = page.locator('.canvas-node[data-node-id="1"] .node-header').bounding_box()
        assert header is not None
        page.mouse.move(header['x'] + header['width'] / 2, header['y'] + header['height'] / 2)
        page.mouse.down()
        page.mouse.move(header['x'] + header['width'] / 2 + 51, header['y'] + header['height'] / 2 + 34, steps=3)
        page.mouse.up()
        page.wait_for_function('state.currentWorkflow.version === 2')
        persisted = page.context.request.get(base + '/api/god_workflow/item/' + target).json()
        source_node = next(node for node in persisted['nodes'] if str(node['id']) == '1')
        assert abs(source_node['position']['x'] - 80) < 1
        assert abs(source_node['position']['y'] - 100) < 1
        distance = page.evaluate('''() => {
            const path = document.querySelector('#svgLinksGroup path');
            const point = path.getPointAtLength(0).matrixTransform(path.getScreenCTM());
            const dot = document.querySelector('.canvas-node[data-node-id="1"] .ports-col-out .port-dot').getBoundingClientRect();
            return Math.hypot(point.x-dot.left-dot.width/2, point.y-dot.top-dot.height/2);
        }''')
        assert distance < 1
        page.evaluate('''() => {
            state.currentWorkflow.nodes[0].outputs[0].type = 'CUSTOM_UNKNOWN';
            renderSvgWires(state.currentWorkflow);
        }''')
        assert page.locator('#svgLinksGroup path').first.get_attribute('stroke') == '#94a3b8'
        assert errors == []


def _capture_workflow_delete_cas_failure(page, base, workflow_id, asset_id, tmp_path, caplog, responses):
    screenshot = tmp_path / 'workflow-delete-cas-failure.png'
    diagnostics = tmp_path / 'workflow-delete-cas-failure.json'
    state = {}
    try:
        page.screenshot(path=str(screenshot), full_page=True)
    except Exception as error:
        state['screenshot_error'] = str(error)
    try:
        state['browser'] = page.evaluate('''(assetId) => ({
            statuses: [...document.querySelectorAll('#assetStatus')].map(el => ({text:el.textContent, visible:!!el.getClientRects().length})),
            cards: [...document.querySelectorAll('[data-workflow-card]')].map(el => ({id:el.dataset.workflowCard, text:el.innerText})),
            deleteButtons: [...document.querySelectorAll('[data-workflow-delete]')]
                .filter(el => el.dataset.workflowDelete === assetId)
                .map(el => ({title:el.title, disabled:el.disabled, busy:el.getAttribute('aria-busy')})),
        })''', asset_id)
    except Exception as error:
        state['browser_state_error'] = str(error)
    try:
        response = page.context.request.get(base + '/api/god_workflow/item/' + workflow_id)
        state['workflow_response'] = {
            'status': response.status,
            'body': response.json() if response.ok else response.text(),
        }
    except Exception as error:
        state['workflow_response_error'] = str(error)
    state['delete_responses'] = responses
    state['server_logs'] = caplog.text
    diagnostics.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    return screenshot, diagnostics


def test_asset_host_workflow_ids_native_export_cas_and_edit_jump(monkeypatch, tmp_path, caplog):
    """实际素材页点击和下载字节验证桥接入口，全部使用临时主体。"""
    caplog.set_level(logging.ERROR, logger='uvicorn.error')
    async def probe():
        return {'online': True, 'object_info': {'LoadImage': {'input': {'required': {'image': ['STRING', {}]}}}}}
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    monkeypatch.setattr('gw.god_workflow.comfy_executor.probe', probe)
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        delete_responses = []
        page.on('response', lambda response: delete_responses.append({
            'method': response.request.method,
            'url': response.url,
            'status': response.status,
        }) if response.request.method == 'DELETE' else None)
        first = create_workflow(page, base, 'first.png', '中秋工作流')
        second = create_workflow(page, base, 'second.png', '中秋工作流')
        first_doc = page.context.request.get(base + '/api/god_workflow/item/' + first).json()
        second_doc = page.context.request.get(base + '/api/god_workflow/item/' + second).json()
        first_asset, second_asset = first_doc['asset_id'], second_doc['asset_id']
        goto_ready(page, base + '/static/embeds/asset-manager.html')
        page.locator('[data-tab="workflows"]').click()
        assert page.locator('[data-workflow-card="' + first_asset + '"]').count() == 1
        assert page.locator('[data-workflow-card="' + second_asset + '"]').count() == 1
        assert page.locator('[data-workflow-cat]').first.get_attribute('data-workflow-cat')
        page.locator('[data-workflow-card="' + first_asset + '"]').click()
        page.get_by_role('link', name='编辑工作流').wait_for()
        with page.expect_download() as pending:
            page.locator('[data-workflow-download="' + first_asset + '"]').click()
        download = pending.value
        assert download.suggested_filename.endswith('.json')
        graph = json.loads(Path(download.path()).read_bytes())
        assert graph['nodes'][0]['widgets_values'][0] == 'first.png'
        assert graph['nodes'][0]['type'] == 'LoadImage'
        page.locator('[data-workflow-manage]').click()
        page.locator('[data-workflow-select-all]').click()
        with page.expect_download() as batch:
            page.locator('[data-workflow-export-selected]').click()
        assert batch.value.suggested_filename == 'workflows.zip'
        with zipfile.ZipFile(io.BytesIO(Path(batch.value.path()).read_bytes())) as bundle:
            assert bundle.testzip() is None
            assert len(bundle.namelist()) == 2
            assert {json.loads(bundle.read(name))['nodes'][0]['widgets_values'][0] for name in bundle.namelist()} == {'first.png', 'second.png'}
            assert any(first_asset in name for name in bundle.namelist())
            assert all(name.startswith('中秋工作流-') for name in bundle.namelist())
        page.locator('[data-workflow-manage]').click()
        page.locator('[data-workflow-card="' + first_asset + '"]').click()
        page.once('dialog', lambda dialog: dialog.accept('更名后的工作流'))
        page.locator('[data-workflow-rename="' + first_asset + '"]').click()
        page.locator('[data-workflow-card="' + first_asset + '"]').filter(has_text='更名后的工作流').wait_for()
        assert page.context.request.get(base + '/api/god_workflow/item/' + first).json()['version'] == 2
        page.locator('[data-workflow-delete="' + first_asset + '"]').click()
        # 首次点击异步读取删除基线版本；须待其进入确认态后再制造并发改名，否则基线可能已是新版本。
        page.locator('#assetStatus').filter(has_text='再次点击确认删除').wait_for()
        concurrent = page.context.request.put(base + '/api/god_workflow/item/' + first + '/name',
            data={'name': '并发新版本', 'expected_version': 2}, headers={'Origin': base})
        assert concurrent.status == 200
        try:
            with page.expect_response(lambda response: response.request.method == 'DELETE'
                    and response.url.split('?', 1)[0] == base + '/api/god_workflow/item/' + first) as deletion:
                page.locator('[data-workflow-delete="' + first_asset + '"]').click()
            conflict = deletion.value
            assert conflict.status == 409, conflict.text()
            detail = conflict.json().get('detail', {})
            assert isinstance(detail, dict) and detail.get('code') == 'VERSION_CONFLICT'
            # 等删除请求回收确认态；共享状态提示文字可能被其他异步刷新更新。
            page.locator(f'[data-workflow-delete="{first_asset}"][title="删除"]').wait_for()
            latest = page.context.request.get(base + '/api/god_workflow/item/' + first)
            assert latest.status == 200
            assert latest.json()['version'] == 3
        except Exception as error:
            screenshot, diagnostics = _capture_workflow_delete_cas_failure(
                page, base, first, first_asset, tmp_path, caplog, delete_responses
            )
            raise AssertionError(
                f'工作流删除 CAS 未到达预期终态；截图 {screenshot}，状态与服务日志 {diagnostics}'
            ) from error
        page.locator('[data-workflow-delete="' + first_asset + '"]').click()
        page.locator('[data-workflow-delete="' + first_asset + '"]').click()
        page.wait_for_function('(id) => !document.querySelector(`[data-workflow-card="${id}"]`)', arg=first_asset)
        assert page.context.request.get(base + '/api/god_workflow/item/' + first).status == 404
        page.locator('[data-workflow-card="' + second_asset + '"]').click()
        page.get_by_role('link', name='编辑工作流').click()
        page.wait_for_load_state('networkidle')
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=second)
        assert errors == []


def test_project_workflow_output_asset_source_roundtrip_in_browser(monkeypatch, tmp_path):
    """真实项目/工作流/素材页；引擎为隔离替身，不宣称供应商验收。"""
    from PIL import Image

    from gw.god_workflow import comfy_executor
    pixels = io.BytesIO()
    Image.new('RGB', (2, 2), 'blue').save(pixels, format='PNG')
    output_bytes = pixels.getvalue()
    submissions = []
    async def submit(prompt, **kwargs):
        submissions.append(prompt)
        return {'prompt_id': 'isolated-project-output'}
    async def history(*args, **kwargs):
        return {'found': True, 'entry': {'outputs': {'1': {'images': [{'filename': 'isolated-source.png'}]}}, 'status': {'status_str': 'success'}}}
    async def fetch_output(*args, **kwargs):
        return output_bytes, 'image/png'
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    monkeypatch.setattr(comfy_executor, 'history', history)
    monkeypatch.setattr(comfy_executor, 'fetch_output', fetch_output)
    monkeypatch.setattr('gw.god_workflow.routes._comfy_operation', lambda *args: {'highlight': False})
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        target = create_workflow(page, base, 'project-reference.png')
        created = page.context.request.post(base + '/api/asset-registry/projects',
            data={'name': '隔离来源项目', 'project_type': 'other'}, headers={'Origin': base})
        assert created.status == 201, created.text()
        project_id = created.json()['project']['project_id']
        goto_ready(page, base + '/static/pages/projects.html?project_id=' + project_id)
        link = page.locator('a[title="工作流"]')
        page.wait_for_function('(id) => document.querySelector(\'a[title="工作流"]\').href.includes(id)', arg=project_id)
        link.click()
        page.wait_for_load_state('networkidle')
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
        page.locator('#btnExecuteWorkflow').click()
        page.locator('#taskModalBody').filter(has_text='打开受控输出').wait_for()
        tasks = page.context.request.get(base + '/api/god_workflow/tasks').json()['tasks']
        assert len(tasks) == 1 and len(submissions) == 1
        job_id = tasks[0]['job_id']
        assert tasks[0]['source_context']['project_id'] == project_id
        assert tasks[0]['source_context']['project_version'] == 1
        assets = page.context.request.get(base + '/api/asset-registry/assets?project_id=' + project_id).json()['items']
        generated = next(asset for asset in assets if asset['metadata'].get('job_id') == job_id)
        asset_id = generated['asset_id']
        media = page.context.request.get(base + '/api/asset-registry/assets/' + asset_id + '/media')
        assert media.status == 200 and media.body() == output_bytes
        assert 'x-media-display' not in media.headers
        # 保存后重复查询没有重复资产或再次生成。
        page.context.request.get(base + '/api/god_workflow/tasks/' + job_id)
        current_assets = page.context.request.get(base + '/api/asset-registry/assets?project_id=' + project_id).json()['items']
        assert sum(asset['asset_id'] == asset_id for asset in current_assets) == 1
        assert len(submissions) == 1
        goto_ready(page, base + '/static/embeds/asset-manager.html')
        page.locator('[data-registry-card="' + asset_id + '"]').dblclick()
        page.locator('[data-detail-media-library-toggle]').wait_for()
        toggle = page.locator('[data-detail-media-library-toggle][aria-expanded="false"]')
        if toggle.count():
            toggle.first.click()
        page.locator('[data-detail-media-sidebar-view="detail"]').click()
        page.get_by_role('link', name='返回来源项目').wait_for()
        page.get_by_role('link', name='查看来源工作流与任务').click()
        page.wait_for_load_state('networkidle')
        page.wait_for_function('(id) => state.activeTaskId === id', arg=job_id)
        page.locator('#taskModalBody').filter(has_text='打开受控输出').wait_for()
        page.reload(wait_until='networkidle')
        page.wait_for_function('(id) => state.activeTaskId === id', arg=job_id)
        assert errors == []
