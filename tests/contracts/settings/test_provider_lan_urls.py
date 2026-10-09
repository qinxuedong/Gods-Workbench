"""明确配置的局域网API准入、版本地址与凭据绑定；仅合成数据。"""
import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from gw.api import routes_settings
from gw.api.app import create_app
from gw.core import storage
from gw.core.errors import CleanroomException
from gw.settings import probes, execution_config, chat
from gw.settings.execution_config import normalize_provider_base_url
from gw.settings.service import ProviderService

AUTH = {"Authorization":"Bearer lan-fixture", "X-User-Role":"editor"}


@pytest.mark.parametrize('url', ('http://192.168.0.199:3100/v1', 'http://10.0.0.8:3100',
                              'http://172.16.0.8:3100', 'http://[fd00::8]:3100'))
def test_explicit_lan_is_provider_only_opt_in(url):
    assert probes.guard_provider_url(url, allow_lan=True) == url
    with pytest.raises(CleanroomException) as denied:
        probes.guard_provider_url(url)
    assert denied.value.code == 'SSRF_BLOCKED'


@pytest.mark.parametrize('host', ('169.254.169.254', '0.0.0.0', '224.0.0.1', '240.0.0.1', '[fe80::1]'))
def test_dangerous_networks_stay_blocked_with_lan_enabled(host):
    with pytest.raises(CleanroomException) as denied:
        probes.guard_provider_url(f'http://{host}:3100', allow_lan=True)
    assert denied.value.code == 'SSRF_BLOCKED'


def test_domain_resolving_to_lan_stays_blocked(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *_: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.0.199', 3100))])
    with pytest.raises(CleanroomException) as denied:
        probes.guard_provider_url('https://synthetic.test:3100', allow_lan=True)
    assert denied.value.code == 'SSRF_BLOCKED'


def test_public_http_is_not_implicitly_enabled(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *_: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 80))])
    with pytest.raises(CleanroomException) as denied:
        probes.guard_provider_url('http://public.test', allow_lan=True)
    assert denied.value.code == 'URL_NOT_ALLOWED'


@pytest.mark.parametrize('suffix', ('', '/v1', '/v1/'))
@pytest.mark.parametrize('endpoint', ('fetch-models', 'test-connection', 'probe-async'))
def test_lan_directory_uses_one_v1_and_direct_transport(monkeypatch, suffix, endpoint):
    hits, options = [], []
    real_client = httpx.Client
    def respond(request):
        hits.append((str(request.url), request.headers.get('authorization')))
        return httpx.Response(200, json={'data':[{'id':'synthetic-chat'}]})
    def make_client(**kwargs):
        options.append(kwargs)
        return real_client(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(probes, '_httpx', lambda: type('Httpx', (), {'Client':staticmethod(make_client)}))
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:9')
    with TestClient(create_app()) as client:
        response = client.post('/api/providers/' + endpoint, headers=AUTH,
            json={'base_url':'http://192.168.0.199:3100'+suffix, 'protocol':'openai', 'api_key':'synthetic-lan-key'})
    assert response.status_code == 200
    assert hits == [('http://192.168.0.199:3100/v1/models', 'Bearer synthetic-lan-key')]
    assert options[0]['trust_env'] is False and options[0]['follow_redirects'] is False
    assert 'synthetic-lan-key' not in response.text


@pytest.mark.parametrize('protocol,path', (('openai','/proxy/v1'), ('apimart','/proxy/v1'),
                                        ('grok','/proxy/v1'), ('gemini','/proxy'), ('volcengine','/proxy')))
def test_version_completion_matches_protocol(protocol, path):
    assert normalize_provider_base_url('https://example.invalid/proxy/', protocol) == 'https://example.invalid'+path


def test_saved_root_can_reuse_its_key_without_redirecting_credentials(monkeypatch):
    service = ProviderService()
    saved = service.replace([{'id':'lan','base_url':'http://192.168.0.199:3100','api_key':'synthetic-old-key',
                              'chat_models':['synthetic-chat']}], 1)
    assert saved.providers[0]['base_url'] == 'http://192.168.0.199:3100/v1'
    # 模拟旧版已保存的根地址；生产文件绝不参与。
    state = storage.JsonState('providers', ProviderService._empty_state).read()
    state['providers'][0]['base_url'] = 'http://192.168.0.199:3100'
    storage.JsonState('providers', ProviderService._empty_state).write(state)
    base, _, key = probes._probe_payload({'provider_id':'lan','base_url':'http://192.168.0.199:3100/v1'})
    assert base.endswith('/v1') and key == 'synthetic-old-key'
    runtime = execution_config.resolve_provider('lan')
    assert runtime.base_url == 'http://192.168.0.199:3100/v1'
    assert execution_config.provider_api_key(runtime) == 'synthetic-old-key'
    with pytest.raises(CleanroomException) as denied:
        probes._probe_payload({'provider_id':'lan','base_url':'http://192.168.0.200:3100'})
    assert denied.value.code == 'PROVIDER_CONFIG_MISMATCH'


def test_saved_lan_provider_chat_uses_the_same_versioned_endpoint(monkeypatch):
    for name in ('GW_PROVIDER_RUNTIME_JSON', 'GW_CHAT_API_KEY', 'GW_CHAT_BASE_URL'):
        monkeypatch.delenv(name, raising=False)
    service = ProviderService()
    service.replace([{'id':'lan','base_url':'http://192.168.0.199:3100','api_key':'synthetic-chat-lan',
                      'chat_models':['synthetic-chat']}], 1)
    monkeypatch.setattr(routes_settings, 'default_provider_service', service)
    hits, options = [], []
    real_client = httpx.Client
    def respond(request):
        hits.append((str(request.url), request.headers['authorization']))
        return httpx.Response(200, json={'choices':[{'message':{'content':'局域网合成回复'}}]})
    def make_client(**kwargs):
        options.append(kwargs)
        return real_client(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(chat, '_httpx', lambda *_: type('Httpx', (), {'Client':staticmethod(make_client)}))
    with TestClient(create_app()) as client:
        result = client.post('/api/chat', headers=AUTH, json={'message':'测试','provider_id':'lan'})
    assert result.status_code == 200, result.text
    assert hits == [('http://192.168.0.199:3100/v1/chat/completions', 'Bearer synthetic-chat-lan')]
    assert options[0]['trust_env'] is False
    assert 'synthetic-chat-lan' not in result.text
