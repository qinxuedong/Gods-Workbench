"""设置页目录探测和CLI能力陈述；隔离夹具，不调用供应商或本机命令。"""
import json

from test_api_settings_browser import settings_browser, ENTRIES
from test_api_settings_visual import settings_frame


def test_failed_probe_keeps_selected_protocol_and_image_api(monkeypatch,tmp_path):
    with settings_browser(monkeypatch,tmp_path,populated=True) as (page,base,errors,writes):
        frame=settings_frame(page,base,ENTRIES[1])
        frame.locator('#protocolInput').select_option('apimart')
        frame.locator('#imageRequestModeInput').select_option('openai-json')
        page.route('**/api/providers/probe-async',lambda route:route.fulfill(status=503,content_type='application/json',
            body=json.dumps({'detail':{'code':'PROVIDER_PROBE_FAILED','message':'上游鉴权失败，未确认目录可用'}})))
        frame.locator('#probeAsyncBtn').click()
        frame.locator('#verifyResult').filter(has_text='已保留当前协议').wait_for()
        assert frame.locator('#protocolInput').input_value()=='apimart'
        assert frame.locator('#imageRequestModeInput').input_value()=='openai-json'
        assert errors==[] and writes==[]


def test_sync_probe_shows_listing_only_and_does_not_change_image_api(monkeypatch,tmp_path):
    with settings_browser(monkeypatch,tmp_path,populated=True) as (page,base,errors,writes):
        frame=settings_frame(page,base,ENTRIES[0])
        frame.locator('#imageRequestModeInput').select_option('openai-json')
        page.route('**/api/providers/probe-async',lambda route:route.fulfill(status=200,content_type='application/json',
            body=json.dumps({'ok':True,'protocol':'apimart','image_request_mode':'tudou-async',
                'execution_mode':'synchronous','completed':True,'generation_verified':False,
                'message':'模型目录可见','raw':{'data':[{'id':'synthetic-model'}]}})))
        frame.locator('#probeAsyncBtn').click()
        frame.locator('#verifyResult').filter(has_text='同步探测已结束').wait_for()
        assert '生成能力尚未验证' in frame.locator('#verifyResult').inner_text()
        assert frame.locator('#protocolInput').input_value()=='openai'
        assert frame.locator('#imageRequestModeInput').input_value()=='openai-json'
        assert errors==[] and writes==[]


def test_cli_path_observation_is_not_generation_ready(monkeypatch,tmp_path):
    with settings_browser(monkeypatch,tmp_path,populated=True) as (page,base,errors,writes):
        frame=settings_frame(page,base,ENTRIES[0])
        page.route('**/api/gemini-cli/status',lambda route:route.fulfill(status=200,content_type='application/json',
            body=json.dumps({'installed':True,'execution_enabled':False,'logged_in':None,'generation_ready':False,
                'command_candidates':['gemini','gemini-cli','antigravity'],'path':'C:/synthetic-cli.exe'})))
        frame.locator('#protocolInput').select_option('gemini-cli')
        frame.locator('#geminiCliInfo').filter(has_text='登录未知').wait_for()
        info=frame.locator('#geminiCliInfo').inner_text()
        assert '执行未启用' in info and '生成未接入' in info
        assert 'agy' not in frame.locator('#geminiCliPanel').inner_text()
        for control in ('testUrlBtn','probeAsyncBtn','fetchModelsBtn'):
            assert frame.locator('#'+control).is_disabled()
        assert errors==[] and writes==[]


def test_model_picker_does_not_reuse_another_platform_catalog(monkeypatch,tmp_path):
    with settings_browser(monkeypatch,tmp_path,populated=True) as (page,base,errors,writes):
        seed=page.context.request.put(base+'/api/providers?expected_version=2',headers={'Origin':base},
            data=[{'id':'browser-fixture','name':'目录平台','base_url':'https://example.invalid/v1'},
                  {'id':'other-platform','name':'其他平台','base_url':'https://other.invalid/v1'}])
        assert seed.status==200
        frame=settings_frame(page,base,ENTRIES[0])
        page.route('**/api/providers/fetch-models',lambda route:route.fulfill(status=200,content_type='application/json',
            body=json.dumps({'ok':True,'all':['synthetic-owned-model'],'chat_models':['synthetic-owned-model']})))
        frame.locator('#fetchModelsBtn').click()
        frame.locator('#modelPickerOverlay').wait_for()
        page.keyboard.press('Escape')
        frame.locator('#providerList .provider-card').last.click()
        assert frame.locator('#openPickerBtn').is_disabled()
        assert frame.locator('#chatModelList .empty').is_visible()
        assert errors==[] and writes==[]


def test_failed_old_probe_does_not_replace_new_platform_feedback(monkeypatch,tmp_path):
    with settings_browser(monkeypatch,tmp_path,populated=True) as (page,base,errors,writes):
        seed=page.context.request.put(base+'/api/providers?expected_version=2',headers={'Origin':base},
            data=[{'id':'browser-fixture','name':'探测平台','base_url':'https://example.invalid/v1'},
                  {'id':'other-platform','name':'其他平台','base_url':'https://other.invalid/v1'}])
        assert seed.status==200
        frame=settings_frame(page,base,ENTRIES[0])
        pending=[]
        page.route('**/api/providers/probe-async',lambda route:pending.append(route))
        frame.locator('#probeAsyncBtn').click()
        assert len(pending)==1
        frame.locator('#providerList .provider-card').last.click()
        frame.evaluate("setStatus('当前平台提示')")
        pending[0].fulfill(status=503,content_type='application/json',
            body=json.dumps({'detail':{'code':'PROVIDER_PROBE_FAILED','message':'旧平台失败'}}))
        frame.wait_for_function('providerProbePending === false')
        assert frame.locator('#status').inner_text()=='当前平台提示'
        assert frame.locator('#nameInput').input_value()=='其他平台'
        assert errors==[] and writes==[]
