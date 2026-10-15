"""C4-AC13：页面适配版本标记、冲突明细与受控升级契约。

覆盖口径：
- workbench.js 首行携带 gods-workbench-adapter-version 标记；ADAPTER_VERSION 只从脚本解析。
- 无标记但命中既往官方指纹 → 409 且 installed_version=legacy-<short>、upgradable=true；
  adapter_upgrade=true 时仅原子替换该适配文件，其余冲突一律保留原文件。
- 未知文件永不覆盖；更高版本标记不降级；同版本不同内容视为本地修改不升级。
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core.errors import CleanroomException
from gw.god_workflow import browser_bridge, routes

HEADERS = {'Authorization': 'Bearer adapter-version-test', 'Origin': 'http://testserver'}
WF = '/api/god_workflow'

SOURCE_SCHEMA = {"input": {"required": {"text": ["STRING", {}], "count": ["INT", {}]}},
                 "input_order": {"required": ["text", "count"]}}
SINK_SCHEMA = {"input": {"required": {"images": ["IMAGE", {"forceInput": True}],
                                      "filename_prefix": ["STRING", {}]}},
               "input_order": {"required": ["images", "filename_prefix"]}}
CANVAS_DOCUMENT = {"nodes": [
    {"id": "1", "kind": "AcmeSource", "title": "Source", "inputs": {"text": "seed-value", "count": 7}},
    {"id": "2", "kind": "AcmeSink", "title": "Sink", "inputs": {"images": ["1", 0]}},
], "connections": []}


def frontend(root):
    """按 prepare 认可的便携版布局伪造 ComfyUI 前端目录，返回 static 根。"""
    static = root / 'python_embeded/Lib/site-packages/comfyui_frontend_package/static'
    app = static / 'scripts/app.js'
    app.parent.mkdir(parents=True)
    app.write_text('existing-host-app', encoding='utf-8')
    return static


def target_of(static):
    return static / 'extensions/gods_workbench/workbench.js'


def seed(static, content: bytes):
    """预先放置一份已存在的适配文件，验证冲突与升级判定。"""
    target = target_of(static)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def host_online(monkeypatch, root):
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8199')
    monkeypatch.setenv('GW_COMFYUI_ROOT_DIR', str(root))
    monkeypatch.setattr(routes, '_comfy_operation',
                        lambda *args, **kwargs: {'ok': True, 'status': 'online', 'highlight': True})
    async def probe():
        return {'online': True, 'object_info': {'AcmeSource': SOURCE_SCHEMA, 'AcmeSink': SINK_SCHEMA}}
    monkeypatch.setattr('gw.god_workflow.comfy_executor.probe', probe)


def save_document(client):
    response = client.post(f'{WF}/documents?source=canvas', json=CANVAS_DOCUMENT, headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()['workflow']['workflow_id']


def test_adapter_script_carries_version_marker_and_module_constant():
    assert browser_bridge.ADAPTER_VERSION == 2
    first_line = browser_bridge.SCRIPT.read_text(encoding='utf-8').splitlines()[0]
    assert first_line == '// gods-workbench-adapter-version: 2'


def test_known_legacy_digests_pin_committed_history():
    """指纹必须与既往官方提交一一对应：git show <提交>:tools/.../workbench.js | sha256sum。"""
    assert set(browser_bridge.KNOWN_LEGACY_SHA256.values()) == {'9b0ff8a', '1c915d7', 'efd55f4'}
    assert 'c380c5ad22ef2ced8678efcf4cc3a5ae4acdaaf44719058900bd31690163c515' in browser_bridge.KNOWN_LEGACY_SHA256
    assert '80827a84a49945c2639e1c21c0894fb43cc5100db02731e2525a95a4bd521254' in browser_bridge.KNOWN_LEGACY_SHA256
    assert '77d405e00f4061a5654758dd830935af5a4b5f4d5fa88cf4b67106de754a59ac' in browser_bridge.KNOWN_LEGACY_SHA256


def test_fresh_install_writes_marked_script(tmp_path):
    static = frontend(tmp_path)
    result = browser_bridge.prepare(str(tmp_path))
    target = target_of(static)
    assert result == {'mode': 'managed', 'extension_url': browser_bridge.EXTENSION_URL,
                      'adapter_version': browser_bridge.ADAPTER_VERSION, 'upgraded': False}
    assert target.read_bytes() == browser_bridge.SCRIPT.read_bytes()
    assert not list(static.rglob('*.tmp'))


def test_identical_marked_file_is_noop(tmp_path):
    static = frontend(tmp_path)
    browser_bridge.prepare(str(tmp_path))
    target = target_of(static)
    stamp = target.stat().st_mtime_ns
    result = browser_bridge.prepare(str(tmp_path))
    assert result['upgraded'] is False
    assert target.stat().st_mtime_ns == stamp


def test_marker_only_recognized_on_first_line():
    assert browser_bridge._marker_version(b'// gods-workbench-adapter-version: 2\n') == 2
    assert browser_bridge._marker_version(b'x\n// gods-workbench-adapter-version: 2\n') is None
    assert browser_bridge._classify(b'// gods-workbench-adapter-version: 3\nx;\n') == ('3', False)


def test_legacy_adapter_conflict_then_atomic_upgrade(tmp_path, monkeypatch):
    static = frontend(tmp_path)
    legacy = b'// previous committed adapter without version marker\nexport const bridge = true;\n'
    monkeypatch.setitem(browser_bridge.KNOWN_LEGACY_SHA256,
                        hashlib.sha256(legacy).hexdigest(), '9b0ff8a')
    target = seed(static, legacy)

    with pytest.raises(CleanroomException) as caught:
        browser_bridge.prepare(str(tmp_path))
    error = caught.value
    assert error.status_code == 409 and error.code == 'COMFY_ADAPTER_CONFLICT'
    assert error.extra['installed_version'] == 'legacy-9b0ff8a'
    assert error.extra['required_version'] == str(browser_bridge.ADAPTER_VERSION) == '2'
    assert error.extra['upgradable'] is True
    assert error.extra['fix_hint'] == '已安装的 ComfyUI 页面适配是旧版本，点「升级适配」即可更新'
    assert target.read_bytes() == legacy

    upgraded = browser_bridge.prepare(str(tmp_path), upgrade=True)
    assert upgraded['upgraded'] is True and upgraded['adapter_version'] == 2
    assert target.read_bytes() == browser_bridge.SCRIPT.read_bytes()
    assert not list(static.rglob('*.tmp'))

    # 升级完成后同一打开操作无需再升级即成功。
    again = browser_bridge.prepare(str(tmp_path))
    assert again['upgraded'] is False
    assert target.read_bytes() == browser_bridge.SCRIPT.read_bytes()


def test_unknown_file_is_never_upgraded(tmp_path):
    static = frontend(tmp_path)
    target = seed(static, 'user-custom bridge'.encode('utf-8'))
    for upgrade in (False, True):
        with pytest.raises(CleanroomException) as caught:
            browser_bridge.prepare(str(tmp_path), upgrade=upgrade)
        error = caught.value
        assert error.status_code == 409 and error.code == 'COMFY_ADAPTER_CONFLICT'
        assert error.extra['installed_version'] == 'unknown'
        assert error.extra['upgradable'] is False
        assert error.extra['fix_hint'] == 'ComfyUI 里有一个不是神工坊安装的同名文件，为了安全不会覆盖，请手动移走后重试'
    assert target.read_text(encoding='utf-8') == 'user-custom bridge'


def test_newer_marker_is_not_downgraded(tmp_path):
    static = frontend(tmp_path)
    future = b'// gods-workbench-adapter-version: 3\nexport const future = true;\n'
    target = seed(static, future)
    for upgrade in (False, True):
        with pytest.raises(CleanroomException) as caught:
            browser_bridge.prepare(str(tmp_path), upgrade=upgrade)
        error = caught.value
        assert error.code == 'COMFY_ADAPTER_CONFLICT'
        assert error.extra['installed_version'] == '3'
        assert error.extra['upgradable'] is False
    assert target.read_bytes() == future


def test_same_version_marker_with_other_content_is_not_upgraded(tmp_path):
    static = frontend(tmp_path)
    modified = b'// gods-workbench-adapter-version: 2\nexport const tampered = true;\n'
    target = seed(static, modified)
    for upgrade in (False, True):
        with pytest.raises(CleanroomException) as caught:
            browser_bridge.prepare(str(tmp_path), upgrade=upgrade)
        error = caught.value
        assert error.code == 'COMFY_ADAPTER_CONFLICT'
        assert error.extra['installed_version'] == '2'
        assert error.extra['upgradable'] is False
    assert target.read_bytes() == modified


def test_open_in_comfy_conflict_detail_then_query_flag_upgrade(monkeypatch, tmp_path):
    host_root = tmp_path / 'comfy-host'
    static = frontend(host_root)
    legacy = b'// legacy official adapter\n'
    monkeypatch.setitem(browser_bridge.KNOWN_LEGACY_SHA256,
                        hashlib.sha256(legacy).hexdigest(), '1c915d7')
    target = seed(static, legacy)
    host_online(monkeypatch, host_root)
    with TestClient(create_app()) as client:
        workflow_id = save_document(client)
        conflict = client.post(f'{WF}/open-in-comfy/{workflow_id}', headers=HEADERS)
        assert conflict.status_code == 409, conflict.text
        detail = conflict.json()['detail']
        assert detail['code'] == 'COMFY_ADAPTER_CONFLICT'
        assert detail['installed_version'] == 'legacy-1c915d7'
        assert detail['required_version'] == '2'
        assert detail['upgradable'] is True
        assert detail['fix_hint'] == '已安装的 ComfyUI 页面适配是旧版本，点「升级适配」即可更新'
        assert target.read_bytes() == legacy

        upgraded = client.post(f'{WF}/open-in-comfy/{workflow_id}?adapter_upgrade=true', headers=HEADERS)
        assert upgraded.status_code == 200, upgraded.text
        assert upgraded.json()['browser_adapter']['upgraded'] is True
        assert target.read_bytes() == browser_bridge.SCRIPT.read_bytes()

        reopened = client.post(f'{WF}/open-in-comfy/{workflow_id}', headers=HEADERS)
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()['browser_adapter']['upgraded'] is False


def test_open_in_comfy_upgrade_flag_accepts_json_true_only(monkeypatch, tmp_path):
    host_root = tmp_path / 'comfy-host'
    static = frontend(host_root)
    legacy = b'// legacy official adapter\n'
    monkeypatch.setitem(browser_bridge.KNOWN_LEGACY_SHA256,
                        hashlib.sha256(legacy).hexdigest(), 'efd55f4')
    target = seed(static, legacy)
    host_online(monkeypatch, host_root)
    with TestClient(create_app()) as client:
        workflow_id = save_document(client)
        # 布尔开关只认 JSON true；字符串 "true" 不触发升级。
        truthy = client.post(f'{WF}/open-in-comfy/{workflow_id}', headers=HEADERS,
                             json={'adapter_upgrade': 'true'})
        assert truthy.status_code == 409, truthy.text
        assert target.read_bytes() == legacy

        upgraded = client.post(f'{WF}/open-in-comfy/{workflow_id}', headers=HEADERS,
                               json={'adapter_upgrade': True})
        assert upgraded.status_code == 200, upgraded.text
        assert upgraded.json()['browser_adapter']['upgraded'] is True
        assert target.read_bytes() == browser_bridge.SCRIPT.read_bytes()


FORBIDDEN_HINT_TERMS = ('adapter_upgrade', 'upgrade=', '=true', 'true', 'open-in-comfy', '/api',
                        'extensions/', 'workbench.js', 'installed_version', 'required_version',
                        'upgradable', 'COMFY_', 'legacy-')


@pytest.mark.parametrize('upgradable', [True, False])
def test_fix_hints_contain_no_param_or_endpoint_names(upgradable):
    hint = browser_bridge._conflict('legacy-9b0ff8a' if upgradable else 'unknown', upgradable).extra['fix_hint']
    for term in FORBIDDEN_HINT_TERMS:
        assert term.lower() not in hint.lower(), (term, hint)
    assert '_' not in hint and '=' not in hint and '/' not in hint
