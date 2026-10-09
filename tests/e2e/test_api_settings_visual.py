"""设置页视觉与交互回归：本地合成数据，不使用个人浏览器。"""

import re
import json

import pytest

from test_api_settings_browser import ENTRIES, settings_browser


def settings_frame(page, base, entry):
    page.goto(base + entry, wait_until="networkidle")
    frame = page.frame(url=re.compile(r"/static/api-settings.html")) or page.main_frame
    frame.locator("#providerList .provider-card").first.wait_for()
    frame.evaluate("() => document.fonts.ready")
    return frame


@pytest.mark.parametrize("entry", ENTRIES)
def test_editor_uses_readable_type_scale_and_responsive_controls(entry, monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        frame = settings_frame(page, base, entry)
        metrics = frame.evaluate("""() => {
            const style = selector => getComputedStyle(document.querySelector(selector));
            return {title:style('#editorTitle').fontSize, section:style('.block-title').fontSize,
                label:style('.label').fontSize, input:style('#nameInput').fontSize,
                hint:style('#keyHint').fontSize, hintWeight:style('#keyHint').fontWeight,
                hintTracking:style('#keyHint').letterSpacing, family:style('#nameInput').fontFamily,
                inputHeight:document.querySelector('#nameInput').getBoundingClientRect().height,
                buttonHeight:document.querySelector('.api-page-save-btn').getBoundingClientRect().height};
        }""")
        # 页面主标题角色统一 16/24/700（全站排版规范，不得回退24px）。
        assert metrics["title"] == "16px"
        # 遵循当前已生效的全站排版规范：栏目标题14/22/500，正文14px，说明12px。
        assert metrics["section"] == "14px"
        assert metrics["label"] == metrics["input"] == "14px"
        assert metrics["hint"] == "12px"
        assert int(metrics["hintWeight"]) >= 500
        # 当前中文正文/说明规范禁止人为加字距；normal 与 0px 均表示零附加字距。
        assert metrics["hintTracking"] in ("normal", "0px")
        assert "Source Han Sans CN" in metrics["family"]
        assert metrics["inputHeight"] >= 40 and metrics["buttonHeight"] >= 40
        for width in (1800, 1280, 960, 760, 480):
            page.set_viewport_size({"width": width, "height": 1000})
            assert frame.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
            assert frame.locator("#nameInput").is_visible()
            if entry == ENTRIES[1] and width <= 960:
                assert page.locator("#navPillsGroup").bounding_box()["width"] >= 200
            assert frame.locator("#settingsContent").is_visible()
            sidebar = frame.locator(".layout > .sidebar").bounding_box()
            content = frame.locator(".layout > .content").bounding_box()
            if frame.evaluate("innerWidth") > 760:
                assert abs(sidebar["y"] - content["y"]) <= 1
                assert abs(sidebar["height"] - content["height"]) <= 1
                assert content["x"] > sidebar["x"] + sidebar["width"]
            else:
                assert abs(sidebar["x"] - content["x"]) <= 1
                assert abs(sidebar["width"] - content["width"]) <= 1
            frame.locator("#providerList .provider-card").first.click()
        assert errors == [] and writes == []


@pytest.mark.parametrize("entry", ENTRIES)
def test_controlled_two_hundred_percent_css_zoom_reflows(entry, monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        frame = settings_frame(page, base, entry)
        page.evaluate("document.documentElement.style.zoom='2'")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert frame.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        frame.locator("#nameInput").scroll_into_view_if_needed()
        assert frame.locator("#nameInput").is_visible()
        assert errors == [] and writes == []


def test_autofill_stays_dark_and_secret_fields_have_distinct_semantics(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        frame = settings_frame(page, base, ENTRIES[0])
        assert frame.locator("#nameInput").get_attribute("autocomplete") == "off"
        assert frame.locator("#keyInput").get_attribute("autocomplete") == "new-password"
        assert frame.locator("#keyInput").get_attribute("name") == "provider-api-key"
        assert frame.get_by_label("API Key", exact=True).count() == 1
        cdp = page.context.new_cdp_session(page)
        cdp.send("DOM.enable")
        cdp.send("CSS.enable")
        root = cdp.send("DOM.getDocument")["root"]["nodeId"]
        for selector in ("#nameInput", "#keyInput"):
            node = cdp.send("DOM.querySelector", {"nodeId": root, "selector": selector})["nodeId"]
            cdp.send("CSS.forcePseudoState", {"nodeId": node, "forcedPseudoClasses": ["autofill"]})
            style = frame.locator(selector).evaluate("el => { const s=getComputedStyle(el); return {shadow:s.boxShadow, fill:s.webkitTextFillColor}; }")
            assert "rgb(15, 17, 21)" in style["shadow"]
            assert style["fill"] == "rgb(229, 231, 235)"
        page.screenshot(path=str(tmp_path / "settings-autofill.png"), full_page=True)
        assert errors == [] and writes == []


def test_deleting_last_platform_returns_to_add_provider_empty_state(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        frame = settings_frame(page, base, ENTRIES[0])
        page.once("dialog", lambda dialog: dialog.accept())
        with page.expect_response(lambda response: response.request.method == "PUT" and "/api/providers" in response.url) as response:
            frame.locator("#deleteBtn").click()
        assert response.value.status == 200
        frame.locator("#providerList .empty").wait_for()
        assert frame.locator("#providerEmptyState").is_visible()
        assert not frame.locator("#providerEditor").is_visible()
        assert frame.locator("#settingsContent").is_visible()
        assert frame.locator("#recommendContent, #recommendPanel, #openRecommendApiBtn").count() == 0
        assert errors == [] and writes == ["PUT"]


def test_unsaved_switch_cancellation_preserves_name_and_secret(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        result = page.context.request.put(base + "/api/providers?expected_version=2",
            headers={"Origin":base}, data=[{"id":"browser-fixture","name":"浏览器测试平台"},
                                         {"id":"other","name":"其他平台"}])
        assert result.status == 200
        frame = settings_frame(page, base, ENTRIES[0])
        frame.locator("#nameInput").fill("未保存的编辑")
        frame.locator("#keyInput").fill("synthetic-unsaved-key")
        page.once("dialog", lambda dialog: dialog.dismiss())
        frame.locator("#providerList .provider-card").last.click()
        assert frame.locator("#nameInput").input_value() == "未保存的编辑"
        assert frame.locator("#keyInput").input_value() == "synthetic-unsaved-key"
        page.once("dialog", lambda dialog: dialog.accept())
        frame.locator("#providerList .provider-card").last.click()
        assert frame.locator("#nameInput").input_value() == "其他平台"
        assert frame.locator("#keyInput").input_value() == ""
        assert errors == [] and writes == []


def test_delete_failure_restores_unsaved_name_and_key(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        frame = settings_frame(page, base, ENTRIES[0])
        frame.locator("#nameInput").fill("删除失败后的草稿")
        frame.locator("#keyInput").fill("synthetic-delete-draft")
        page.route("**/api/providers*", lambda route: route.fulfill(status=503,
            content_type="application/json", body=json.dumps({"detail":{"code":"SERVICE_UNAVAILABLE","message":"受控失败"}}))
            if route.request.method == "PUT" else route.continue_())
        page.once("dialog", lambda dialog: dialog.accept())
        frame.locator("#deleteBtn").click()
        frame.locator("#nameInput").wait_for()
        assert frame.locator("#nameInput").input_value() == "删除失败后的草稿"
        assert frame.locator("#keyInput").input_value() == "synthetic-delete-draft"
        assert frame.locator("#status").get_attribute("data-kind") == "error"
        assert errors == [] and writes == ["PUT"]


def test_pending_save_disables_edits_and_sends_once(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        frame = settings_frame(page, base, ENTRIES[0])
        pending = []
        page.route("**/api/providers*", lambda route: pending.append(route)
            if route.request.method == "PUT" else route.continue_())
        frame.locator("#nameInput").fill("保存中平台")
        frame.locator(".api-page-save-btn").click()
        assert frame.locator("#nameInput").is_disabled()
        assert frame.locator("#keyInput").is_disabled()
        assert frame.locator(".api-page-save-btn").is_disabled()
        assert frame.evaluate("saveProviders()") is False
        assert len(pending) == 1
        pending[0].continue_()
        frame.locator("#status").filter(has_text="尚未验证").wait_for()
        assert frame.locator("#nameInput").is_enabled()
        assert frame.locator(".api-page-save-btn").is_enabled()
        assert frame.locator("#nameInput").input_value() == "保存中平台"
        assert errors == [] and writes == ["PUT"]


def test_autofilled_values_require_explicit_review_before_save(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        frame = settings_frame(page, base, ENTRIES[0])
        frame.locator("#nameInput").fill("合成自动填充平台")
        frame.locator("#keyInput").fill("synthetic-autofill-only")
        cdp = page.context.new_cdp_session(page)
        cdp.send("DOM.enable")
        cdp.send("CSS.enable")
        root = cdp.send("DOM.getDocument")["root"]["nodeId"]
        node = cdp.send("DOM.querySelector", {"nodeId":root,"selector":"#keyInput"})["nodeId"]
        cdp.send("CSS.forcePseudoState", {"nodeId":node,"forcedPseudoClasses":["autofill"]})
        frame.locator(".api-page-save-btn").click()
        assert frame.locator("#autofillReview").is_visible()
        assert writes == []
        frame.locator("#autofillAck").check()
        with page.expect_response(lambda response: response.request.method == "PUT" and "/api/providers" in response.url) as saved:
            frame.locator(".api-page-save-btn").click()
        assert saved.value.status == 200
        assert "synthetic-autofill-only" not in saved.value.text()
        assert errors == [] and writes == ["PUT"]


@pytest.mark.parametrize("selector", ['a[href$="projects.html"]', 'button[data-section="general"]'])
def test_parent_navigation_cancellation_keeps_draft(selector, monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        frame = settings_frame(page, base, ENTRIES[1])
        frame.locator("#nameInput").fill("父壳退出前草稿")
        frame.locator("#keyInput").fill("synthetic-navigation-draft")
        page.once("dialog", lambda dialog: dialog.dismiss())
        page.locator(selector).first.click()
        assert "settings.html" in page.url
        assert frame.locator("#nameInput").input_value() == "父壳退出前草稿"
        assert frame.locator("#keyInput").input_value() == "synthetic-navigation-draft"
        assert errors == [] and writes == []


def test_model_picker_closes_without_apply_and_restores_focus(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        frame = settings_frame(page, base, ENTRIES[1])
        page.route("**/api/providers/fetch-models", lambda route: route.fulfill(status=200,
            content_type="application/json", body=json.dumps({"ok":True,"protocol":"openai",
                "all":["synthetic-chat-model"],"chat_models":["synthetic-chat-model"]})))
        frame.locator("#fetchModelsBtn").click()
        trigger = frame.locator("#openPickerBtn")
        overlay = frame.locator("#modelPickerOverlay")
        overlay.wait_for()
        page.keyboard.press("Escape")
        trigger.click()
        for _ in range(20):
            page.keyboard.press("Tab")
            assert frame.evaluate("document.querySelector('#modelPickerOverlay').contains(document.activeElement)")
        overlay.locator(".picker-row").first.click()
        page.once("dialog", lambda dialog: dialog.dismiss())
        page.keyboard.press("Escape")
        assert overlay.is_visible()
        page.once("dialog", lambda dialog: dialog.accept())
        page.keyboard.press("Escape")
        assert not overlay.is_visible()
        # 共享浮窗通过下一帧恢复焦点，等待真实状态而非假定同步完成。
        frame.wait_for_function("document.activeElement === document.querySelector('#openPickerBtn')")
        assert trigger.evaluate("el => el === document.activeElement")
        trigger.click()
        overlay.locator("[data-picker-close]").first.click()
        assert not overlay.is_visible()
        frame.wait_for_function("document.activeElement === document.querySelector('#openPickerBtn')")
        assert trigger.evaluate("el => el === document.activeElement")
        trigger.click()
        overlay.click(position={"x":24,"y":24})
        assert not overlay.is_visible()
        assert frame.locator("#chatModelList .empty").is_visible()
        assert errors == [] and writes == []


def test_invalid_url_is_reported_at_field_without_sending_save(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        frame = settings_frame(page, base, ENTRIES[0])
        frame.locator("#baseInput").fill("https://user:synthetic-password@example.invalid")
        frame.locator(".api-page-save-btn").click()
        frame.locator("#baseError").filter(has_text="地址").wait_for()
        assert frame.locator("#baseInput").get_attribute("aria-invalid") == "true"
        assert errors == [] and writes == []


def test_erased_unsaved_key_is_not_sent_after_cancelled_switch(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        frame = settings_frame(page, base, ENTRIES[0])
        frame.locator("#keyInput").fill("synthetic-erased-key")
        page.once("dialog", lambda dialog: dialog.dismiss())
        frame.get_by_role("button", name="新增平台", exact=True).click()
        frame.locator("#keyInput").fill("")
        with page.expect_response(lambda response: response.request.method == "PUT" and "/api/providers" in response.url) as saved:
            frame.locator(".api-page-save-btn").click()
        assert saved.value.status == 200
        assert not saved.value.request.post_data_json[0].get("api_key")
        assert saved.value.json()["providers"][0]["has_key"] is False
        assert errors == [] and writes == ["PUT"]


def test_new_key_replaces_failed_clear_intent(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        response = page.context.request.put(base + "/api/providers?expected_version=2", headers={"Origin":base},
            data=[{"id":"browser-fixture","name":"已存密钥平台","api_key":"synthetic-old-key"}])
        assert response.status == 200
        frame = settings_frame(page, base, ENTRIES[0])
        attempts = []
        def fail_first(route):
            if route.request.method != "PUT":
                route.continue_()
                return
            attempts.append(route.request.post_data_json)
            if len(attempts) == 1:
                route.fulfill(status=503, content_type="application/json",
                    body=json.dumps({"detail":{"code":"SERVICE_UNAVAILABLE","message":"受控清除失败"}}))
            else:
                route.continue_()
        page.route("**/api/providers*", fail_first)
        page.once("dialog", lambda dialog: dialog.accept())
        frame.locator(".key-clear").first.click()
        frame.locator("#status[data-kind='error']").wait_for()
        frame.locator("#keyInput").fill("synthetic-replacement-key")
        with page.expect_response(lambda response: response.request.method == "PUT" and "/api/providers" in response.url) as saved:
            frame.locator(".api-page-save-btn").click()
        assert saved.value.status == 200
        assert attempts[1][0]["clear_key"] is False
        assert saved.value.json()["providers"][0]["has_key"] is True
        assert errors == [] and writes == ["PUT","PUT"]


def test_key_and_model_text_have_adequate_contrast(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        frame = settings_frame(page, base, ENTRIES[0])
        colors = frame.evaluate("""() => {
            const result = [];
            const luminance = rgb => rgb.map(v => {v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4})
                .reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
            const parse = color => color.match(/[\\d.]+/g).map(Number);
            const bg = el => { const color = parse(getComputedStyle(el).backgroundColor);
                if(color.length===4 && color[3]===0) return el.parentElement?bg(el.parentElement):[9,10,13];
                return color.slice(0,3); };
            for(const selector of ['#nameInput','#keyInput','.label','#keyHint','.empty']){
                const el=document.querySelector(selector); const color=parse(getComputedStyle(el).color).slice(0,3);
                const a=luminance(color),b=luminance(bg(el));result.push({selector,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)});
            }
            const el=document.querySelector('#baseInput');const a=luminance(parse(getComputedStyle(el,'::placeholder').color).slice(0,3));
            const b=luminance(bg(el));result.push({selector:'placeholder',ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)});
            return result;
        }""")
        assert all(item["ratio"] >= 4.5 for item in colors), colors
        assert errors == [] and writes == []
