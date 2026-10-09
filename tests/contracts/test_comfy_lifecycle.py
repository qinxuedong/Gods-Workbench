"""仅模拟进程和探测，不真实启停 Windows/本机服务。"""
import threading

import pytest
from fastapi.testclient import TestClient

from gw.god_workflow.comfy_control import ComfyControl, ProcessBackend, local_endpoint

URL = 'http://127.0.0.1:8188'
IDENTITY = {'pid': 42, 'created': 123.0, 'exe': '/python', 'cmd': ['/python', '/ComfyUI/main.py']}


class FakeBackend:
    def __init__(self, online=False):
        self.online = online
        self.current = dict(IDENTITY)
        self.launches = 0
        self.stops = []
    def listener(self, port):
        return self.current if self.online else None
    def spawn(self, root, port):
        self.launches += 1
        self.online = True
        return self, dict(self.current)
    def poll(self):
        return None
    def stop(self, expected):
        assert expected == self.current
        self.stops.append(expected)
        self.online = False
        return True


def control(backend=None, **kwargs):
    backend = backend or FakeBackend()
    return ComfyControl(backend, lambda _: backend.online, **kwargs), backend


def test_owned_start_stop_and_status():
    c, b = control()
    assert c.operate('start', URL, '/configured', 'admin')['managed']
    assert c.owned == IDENTITY
    assert c.operate('status', URL, '', 'admin')['highlight']
    assert c.operate('stop', URL, '', 'admin')['status'] == 'offline'
    assert len(b.stops) == 1


def test_external_requires_explicit_confirmation_then_exact_stop():
    c, b = control(FakeBackend(True))
    status = c.operate('status', URL, '', 'admin')
    assert not status['managed']
    assert c.operate('stop', URL, '', 'admin')['code'] == 'COMFY_EXTERNAL_CONFIRM_REQUIRED'
    assert not b.stops
    assert c.operate('stop', URL, '', 'admin', status['confirmation_token'])['ok']
    assert b.stops == [IDENTITY]


@pytest.mark.parametrize('change', ['created', 'cmd', 'exe', 'pid'])
def test_confirmation_identity_change_refused(change):
    c, b = control(FakeBackend(True))
    token = c.operate('status', URL, '', 'admin')['confirmation_token']
    b.current = dict(IDENTITY, **{change: 'changed'})
    assert c.operate('stop', URL, '', 'admin', token)['code'] == 'COMFY_PROCESS_IDENTITY_CHANGED'
    assert not b.stops


def test_confirmation_bound_to_principal_and_endpoint():
    c, b = control(FakeBackend(True))
    token = c.operate('status', URL, '', 'admin')['confirmation_token']
    assert not c.operate('stop', URL, '', 'other', token)['ok']
    assert not b.stops


def test_unknown_identity_refuses_stop():
    c, b = control(FakeBackend(True))
    b.current = None
    assert c.operate('stop', URL, '', 'admin')['code'] == 'COMFY_PROCESS_IDENTITY_UNKNOWN'
    assert not b.stops


def test_stop_revalidates_listener_race():
    c, b = control(FakeBackend(True))
    c.owned = dict(IDENTITY)
    c.owner = 'admin'
    calls = []
    def listener(port):
        calls.append(port)
        return IDENTITY if len(calls) == 1 else dict(IDENTITY, created=456)
    b.listener = listener
    assert c.operate('stop', URL, '', 'admin')['code'] == 'COMFY_PROCESS_IDENTITY_CHANGED'
    assert not b.stops


def test_start_timeout_no_duplicate_process():
    c, b = control(wait_seconds=0)
    c.probe = lambda _: False
    b.stop = lambda _: False
    assert c.operate('start', URL, '/configured', 'admin')['code'] == 'COMFY_START_TIMEOUT'
    assert c.operate('start', URL, '/configured', 'admin')['code'] == 'COMFY_START_PENDING'
    assert b.launches == 1


def test_start_waits_for_readiness():
    c, b = control(sleep=lambda _: None)
    calls = []
    def probe(_):
        calls.append(1)
        return b.online and len(calls) >= 4
    c.probe = probe
    assert c.operate('start', URL, '/configured', 'admin')['ok']
    assert len(calls) == 4


def test_concurrent_start_refused():
    entered, release = threading.Event(), threading.Event()
    c, b = control()
    original = b.spawn
    def spawn(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)
    b.spawn = spawn
    thread = threading.Thread(target=c.operate, args=('start', URL, '/configured', 'admin'))
    thread.start()
    assert entered.wait(2)
    try:
        assert c.operate('start', URL, '/configured', 'admin')['code'] == 'COMFY_CONTROL_BUSY'
    finally:
        release.set()
        thread.join(2)
    assert b.launches == 1


@pytest.mark.parametrize('url', ['http://evil.test:8188', 'http://127.0.0.1/path', 'http://user@localhost', 'https://localhost', 'http://localhost?x=1'])
def test_only_local_control_endpoint(url):
    with pytest.raises(ValueError):
        local_endpoint(url)


def test_no_arbitrary_scripts(tmp_path):
    (tmp_path / 'run.bat').write_text('arbitrary')
    with pytest.raises(ValueError):
        ProcessBackend().spawn(str(tmp_path), 8188)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('GW_RUNTIME_MODE', 'test')
    monkeypatch.setenv('GW_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('GW_LOCAL_AUTH_DB', str(tmp_path / 'auth.sqlite3'))
    monkeypatch.setenv('GW_VIDEO_DATA_DIR', str(tmp_path / 'video'))
    monkeypatch.setenv('GW_AUTH_MODE', 'local')
    c, _ = control()
    monkeypatch.setattr('gw.god_workflow.comfy_control.service', c)
    from gw.api.app import create_app
    return TestClient(create_app())


@pytest.mark.parametrize('role', ['editor', 'governor', 'readonly'])
@pytest.mark.parametrize('action', ['start', 'stop'])
def test_control_admin_only(client, role, action):
    response = client.post('/api/god_workflow/comfy-control', json={'action': action},
                           headers={'Authorization': 'Bearer test', 'X-User-Role': role, 'Origin': 'http://testserver'})
    assert response.status_code == 403


def test_configuration_admin_only(client):
    headers = {'Authorization': 'Bearer test', 'Origin': 'http://testserver'}
    assert client.post('/api/god_workflow/settings', json={'comfy_root_dir': '/custom'}, headers=headers).status_code == 403
    headers['X-User-Role'] = 'admin'
    assert client.post('/api/god_workflow/settings', json={'comfy_root_dir': '/custom'}, headers=headers).status_code == 200


def test_route_actual_status_and_owned_stop(client):
    headers = {'Authorization': 'Bearer test', 'X-User-Role': 'admin', 'Origin': 'http://testserver'}
    assert client.get('/api/god_workflow/comfy-control', headers=headers).json()['status'] == 'offline'
    assert client.post('/api/god_workflow/comfy-control', json={'action': 'start'}, headers=headers).json()['highlight']
    assert client.post('/api/god_workflow/comfy-control', json={'action': 'stop'}, headers=headers).json()['status'] == 'offline'


def test_anonymous_denied(client):
    assert client.get('/api/god_workflow/comfy-control').status_code == 401
    assert client.post('/api/god_workflow/comfy-control', json={'action': 'start'}, headers={'Origin': 'http://testserver'}).status_code == 401


def test_timeout_cleans_exact_child_and_allows_retry():
    c, b = control(wait_seconds=0)
    c.probe = lambda _: False
    result = c.operate('start', URL, '/configured', 'admin')
    assert result['cleanup_confirmed']
    assert b.stops == [IDENTITY]
    assert c.child is None
    c.operate('start', URL, '/configured', 'admin')
    assert b.launches == 2


def test_different_admin_needs_confirmation_for_owned_instance():
    c, b = control()
    c.operate('start', URL, '/configured', 'admin-a')
    status = c.operate('status', URL, '', 'admin-b')
    assert not status['managed']
    assert not c.operate('stop', URL, '', 'admin-b')['ok']
    assert c.operate('stop', URL, '', 'admin-b', status['confirmation_token'])['ok']


def test_listener_rejects_wildcard_and_multiple_pids(monkeypatch):
    from types import SimpleNamespace as S
    import psutil
    monkeypatch.setattr(psutil, 'net_connections', lambda **_: [S(status=psutil.CONN_LISTEN, laddr=S(port=8188, ip='0.0.0.0'), pid=42)])
    assert ProcessBackend().listener(8188) is None
    monkeypatch.setattr(psutil, 'net_connections', lambda **_: [S(status=psutil.CONN_LISTEN, laddr=S(port=8188, ip='127.0.0.1'), pid=pid) for pid in (42, 43)])
    assert ProcessBackend().listener(8188) is None


def test_os_pidfd_identity_change_never_signals(monkeypatch):
    import os
    import psutil
    import signal
    if os.name == 'nt':
        pytest.skip('Linux pidfd 模拟测试')
    backend = ProcessBackend()
    monkeypatch.setattr(os, 'pidfd_open', lambda _: 99)
    closed, signals = [], []
    monkeypatch.setattr(os, 'close', closed.append)
    monkeypatch.setattr(psutil, 'Process', lambda _: object())
    monkeypatch.setattr(backend, 'identity', lambda _: dict(IDENTITY, created=999))
    monkeypatch.setattr(signal, 'pidfd_send_signal', lambda *a: signals.append(a))
    assert not backend.stop(IDENTITY)
    assert not signals
    assert closed == [99]


def test_spawn_records_identity_without_shell(tmp_path, monkeypatch):
    import subprocess
    import psutil
    (tmp_path / 'ComfyUI' / 'comfy').mkdir(parents=True)
    (tmp_path / 'ComfyUI' / 'main.py').write_text('')
    (tmp_path / 'python_embeded').mkdir()
    (tmp_path / 'python_embeded' / 'python.exe').write_text('')
    backend = ProcessBackend()
    calls = []
    def popen(argv, **options):
        calls.append((argv, options))
        return type('Child', (), {'pid': 42})()
    monkeypatch.setattr(subprocess, 'Popen', popen)
    monkeypatch.setattr(psutil, 'Process', lambda _: object())
    monkeypatch.setattr(backend, 'identity', lambda _: IDENTITY)
    child, identity = backend.spawn(str(tmp_path), 8188)
    assert identity == IDENTITY
    assert calls[0][1]['shell'] is False
    assert calls[0][0][-4:] == ['--listen', '127.0.0.1', '--port', '8188']


def test_bridge_command_bound_to_subject(client, monkeypatch):
    from gw.god_workflow import routes, registry
    from gw.core.auth import AuthContext
    from gw.god_workflow import comfy_control
    comfy_control.service.backend.online = True
    context = AuthContext(role='editor', subject='a', mode='local', identity_domain='local')
    # 使用路由实际认证摘要，不读取任何真实数据。
    context = routes._auth('Bearer a', 'editor', False)
    key = registry.principal_digest(context.identity_domain, context.subject)
    import time
    monkeypatch.setattr(routes, '_PENDING_COMFY_COMMAND', {key: {'workflow_id': 'sample', 'timestamp': time.time(), 'command': 'open_workflow'}})
    monkeypatch.setattr(routes, 'get_document', lambda *a: {})
    assert client.get('/api/god_workflow/comfy-bridge/poll', headers={'Authorization':'Bearer b'}).json()['command'] == 'idle'
    assert client.get('/api/god_workflow/comfy-bridge/poll', headers={'Authorization':'Bearer a'}).json()['command'] == 'open_workflow'
