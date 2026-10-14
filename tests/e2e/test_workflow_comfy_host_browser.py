"""C4-AC14 浏览器回归：open-in-comfy 未配置跳设置、已配置连不上提示，网络全隔离。"""
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

from workflow_support import goto_ready, settings_browser


def _shot(page, name):
    """可选截图：仅当设置 GW_QA_SHOT_DIR 时落盘（本地 QA 证据，不入库）。"""
    target = os.environ.get('GW_QA_SHOT_DIR')
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / f'{name}.png'))


def _open_workflow(page, base):
    created = page.context.request.post(base + '/api/god_workflow/documents?name=' + quote('comfy-host'),
        data={'1': {'class_type': 'LoadImage', 'inputs': {'image': 'input.png'}}}, headers={'Origin': base})
    assert created.status == 201, created.text()
    target = created.json()['workflow_id']
    goto_ready(page, base + '/static/pages/workflow.html?id=' + target)
    page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=target)
    return target


def test_open_in_comfy_unconfigured_shows_notice_and_jumps_to_settings(monkeypatch, tmp_path):
    for name in ('GW_COMFYUI_URL', 'GW_COMFYUI_ROOT_DIR'):
        monkeypatch.delenv(name, raising=False)
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        responses = []
        page.on('response', lambda r: responses.append((urlsplit(r.url).path, r.status)) if '/open-in-comfy' in r.url else None)
        _open_workflow(page, base)
        page.get_by_role('button', name='导入 ComfyUI', exact=True).click()
        page.get_by_text('未配置 ComfyUI 地址：请先在设置中填写本地 ComfyUI 服务地址', exact=True).wait_for()
        assert responses and responses[-1][1] == 409
        _shot(page, 'ac14-not-configured-toast')
        page.get_by_role('button', name='前往设置', exact=True).click()
        page.wait_for_function('() => document.getElementById("settingsModal").classList.contains("open")')
        assert page.locator('#inputComfyUrl').is_visible()
        _shot(page, 'ac14-settings-opened')
        # 未配置时不得打开新标签页（预留空白窗口已关闭），也不得探测默认 127.0.0.1:8188。
        assert len(page.context.pages) == 1
        assert errors == []


def test_open_in_comfy_unreachable_shows_cannot_connect(monkeypatch, tmp_path):
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:9')
    monkeypatch.setattr('gw.god_workflow.routes._comfy_operation',
                        lambda *a, **k: {'ok': True, 'status': 'offline', 'highlight': False, 'managed': False})
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        responses = []
        page.on('response', lambda r: responses.append(r.status) if '/open-in-comfy' in r.url else None)
        _open_workflow(page, base)
        page.get_by_role('button', name='导入 ComfyUI', exact=True).click()
        page.get_by_text('连不上 ComfyUI：ComfyUI 未在线，请先使用全站 CF 控件启动或连接服务', exact=True).wait_for()
        assert responses == [503]
        _shot(page, 'ac14-unreachable-toast')
        assert len(page.context.pages) == 1 and errors == []
