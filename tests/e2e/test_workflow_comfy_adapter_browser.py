"""C4-AC13：ComfyUI 页面适配冲突与升级前端真实浏览器回归。

验证：
- 已安装 legacy 旧版适配时，导入 ComfyUI 出现含 fix_hint 与版本的提示和「升级适配」按钮；
- 点击升级后，后端真实升级（文件首行变为新版本标记），出现刷新提示；
- 未知内容文件时，提示无「升级适配」按钮，文件内容未变。
"""
from workflow_support import goto_ready, settings_browser
from pathlib import Path
from urllib.parse import quote
import hashlib
import pytest


def create_workflow(page, base, image='test.png', name='workflow'):
    response = page.context.request.post(base + '/api/god_workflow/documents?name=' + quote(name),
        data={'1': {'class_type': 'LoadImage', 'inputs': {'image': image}}},
        headers={'Origin': base})
    assert response.status == 201, response.text()
    return response.json()['workflow_id']


def _shot(page, name):
    """若环境变量 GW_QA_SHOT_DIR 设置则截图存到该目录，文件名 ac13-{name}.png。"""
    import os
    shot_dir = os.getenv('GW_QA_SHOT_DIR')
    if shot_dir:
        Path(shot_dir).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shot_dir) / f'ac13-{name}.png'))


def seed_legacy_adapter(comfy_root, monkeypatch):
    """构造一个命中既往官方指纹的 legacy 旧版适配文件。"""
    from gw.god_workflow import browser_bridge
    import hashlib
    # 构造一个简单的 legacy 内容
    legacy = b'// previous committed adapter without version marker\nexport const bridge = true;\n'
    digest = hashlib.sha256(legacy).hexdigest()
    # monkeypatch 让这个摘要被识别为 legacy
    monkeypatch.setitem(browser_bridge.KNOWN_LEGACY_SHA256, digest, '9b0ff8a')

    target = comfy_root / 'python_embeded/Lib/site-packages/comfyui_frontend_package/static/extensions/gods_workbench/workbench.js'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(legacy)
    return target, legacy, digest


def seed_unknown_adapter(comfy_root):
    """构造一个未知内容的适配文件。"""
    target = comfy_root / 'python_embeded/Lib/site-packages/comfyui_frontend_package/static/extensions/gods_workbench/workbench.js'
    target.parent.mkdir(parents=True, exist_ok=True)
    unknown = b'user-custom bridge'
    target.write_bytes(unknown)
    return target, unknown


def test_legacy_adapter_conflict_then_upgrade_and_refresh_hint(monkeypatch, tmp_path):
    """legacy 旧版适配 → 点「导入 ComfyUI」→ 出现含 fix_hint 与版本的提示和「升级适配」按钮 → 点击 → 后端真实升级 → 出现刷新提示。"""
    from gw.god_workflow import browser_bridge, routes
    comfy_root = tmp_path / 'comfy-host'
    # 构造前端目录
    static = comfy_root / 'python_embeded/Lib/site-packages/comfyui_frontend_package/static'
    app = static / 'scripts/app.js'
    app.parent.mkdir(parents=True)
    app.write_text('existing-host-app', encoding='utf-8')
    target, legacy, digest = seed_legacy_adapter(comfy_root, monkeypatch)

    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    monkeypatch.setenv('GW_COMFYUI_ROOT_DIR', str(comfy_root))
    monkeypatch.setattr(routes, '_comfy_operation',
                        lambda *args, **kwargs: {'ok': True, 'status': 'online', 'highlight': True})
    async def probe():
        return {'online': True, 'object_info': {
            'LoadImage': {'input': {'required': {'image': ['STRING', {}]}}}}}
    monkeypatch.setattr('gw.god_workflow.comfy_executor.probe', probe)

    # 伪造 ComfyUI 宿主路由（仅用于真实打开窗口，page.context.route 不拦截 about:blank）
    def fake_host(route):
        route.fulfill(status=200, body='<html><body>Fake ComfyUI</body></html>')

    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        page.context.route('http://127.0.0.1:8199/**', fake_host)
        workflow_id = create_workflow(page, base, 'test.png', 'adapter-test')
        goto_ready(page, base + '/static/pages/workflow.html?id=' + workflow_id)
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=workflow_id)

        # 点击「导入 ComfyUI」按钮
        page.locator('#btnImportComfyUi').click()
        # 等待冲突 toast 出现（包含 legacy 版本信息）
        page.wait_for_function(
            '() => document.querySelector("#toastBanner")?.innerText?.includes("legacy") || ' +
            'document.querySelector("#toastBanner")?.innerText?.includes("旧版")',
            timeout=10000
        )
        toast_text = page.locator('#toastBanner').inner_text()
        assert '已安装的 ComfyUI 页面适配是旧版本，点「升级适配」即可更新' in toast_text, toast_text
        assert '（已安装版本：legacy-9b0ff8a，需要版本：2）' in toast_text, toast_text
        upgrade_btn = page.locator('#toastActionBtn')
        assert upgrade_btn.inner_text() == '升级适配'
        _shot(page, 'legacy-conflict-prompt')
        assert target.read_bytes() == legacy, '冲突时文件应保持原样'

        bodies = []
        page.on('request', lambda r: bodies.append(r.post_data) if '/open-in-comfy/' in r.url else None)
        upgrade_btn.click()
        page.locator('#toastBanner', has_text='页面适配已升级到 v2，请刷新 ComfyUI 页面以加载新脚本').wait_for()
        assert bodies == ['{"adapter_upgrade":true}'], bodies
        _shot(page, 'after-upgrade-toast')
        assert target.read_bytes() == browser_bridge.SCRIPT.read_bytes(), '应原子替换为随包新版脚本'
        assert target.read_bytes().split(b'\n')[0].strip() == b'// gods-workbench-adapter-version: 2'
        assert errors == []


def test_unknown_adapter_no_upgrade_button_and_file_unchanged(monkeypatch, tmp_path):
    """未知内容文件 → 提示无「升级适配」按钮，文件内容未变。"""
    from gw.god_workflow import routes
    comfy_root = tmp_path / 'comfy-host'
    static = comfy_root / 'python_embeded/Lib/site-packages/comfyui_frontend_package/static'
    app = static / 'scripts/app.js'
    app.parent.mkdir(parents=True)
    app.write_text('existing-host-app', encoding='utf-8')
    target, unknown = seed_unknown_adapter(comfy_root)

    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    monkeypatch.setenv('GW_COMFYUI_ROOT_DIR', str(comfy_root))
    monkeypatch.setattr(routes, '_comfy_operation',
                        lambda *args, **kwargs: {'ok': True, 'status': 'online', 'highlight': True})
    async def probe():
        return {'online': True, 'object_info': {
            'LoadImage': {'input': {'required': {'image': ['STRING', {}]}}}}}
    monkeypatch.setattr('gw.god_workflow.comfy_executor.probe', probe)

    def fake_host(route):
        route.fulfill(status=200, body='<html><body>Fake ComfyUI</body></html>')

    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        page.context.route('http://127.0.0.1:8199/**', fake_host)
        workflow_id = create_workflow(page, base, 'test.png', 'unknown-adapter-test')
        goto_ready(page, base + '/static/pages/workflow.html?id=' + workflow_id)
        page.wait_for_function('(id) => state.currentWorkflow?.workflow_id === id', arg=workflow_id)

        page.locator('#btnImportComfyUi').click()
        page.locator('#toastBanner', has_text='（已安装版本：unknown，需要版本：2）').wait_for()
        assert 'ComfyUI 里有一个不是神工坊安装的同名文件，为了安全不会覆盖，请手动移走后重试' in page.locator('#toastBanner').inner_text()
        toast_text = page.locator('#toastBanner').inner_text()
        assert '导入 ComfyUI 失败' not in toast_text, toast_text

        # 不应有「升级适配」按钮
        assert not page.locator('#toastActionBtn').count(), '未知文件不应有升级按钮'
        _shot(page, 'unknown-no-upgrade-button')

        # 验证文件内容未变
        assert target.read_bytes() == unknown, '未知文件不应被覆盖'
        assert errors == []
