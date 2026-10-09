"""API设置三类入口回归；仅使用隔离账户与合成平台。"""

import pytest

from test_api_settings_browser import settings_browser
from test_api_settings_visual import settings_frame


@pytest.mark.parametrize("entry,redirected", (
    ("/static/api-settings.html?section=api-settings&ignored=synthetic", True),
    ("/static/embeds/api-settings.html", False),
))
def test_legacy_redirect_and_standalone_embed_remain_operable(entry, redirected, monkeypatch, tmp_path):
    with settings_browser(monkeypatch,tmp_path,populated=True) as (page,base,errors,writes):
        frame=settings_frame(page,base,entry)
        if redirected:
            # 主设置页会写入当前分区hash；旧地址跳转应保留获准查询并丢弃未知参数。
            assert page.url.split('#',1)[0]==base+"/static/pages/settings.html?section=api-settings"
            assert not frame.locator('.page-head').is_visible()
        else:
            assert page.url==base+entry
            assert frame.locator('.page-head').is_visible()
        assert frame.locator('#settingsContent').is_visible()
        assert frame.locator('#providerEditor').is_visible()
        assert frame.locator('#recommendContent, #recommendApiOverlay, #openRecommendApiBtn').count() == 0
        frame.locator('#providerList .provider-card').first.click()
        frame.locator('#nameInput').fill('入口草稿')
        frame.evaluate("setStatus('可见状态反馈')")
        assert frame.locator('#status').is_visible()
        assert frame.locator('#status').get_attribute('role')=='status'
        assert frame.locator('#nameInput').input_value()=='入口草稿'
        assert errors==[] and writes==[]
