"""C4-AC3 浏览器回归：旧工作流提示 + 一键「重新解析」（真实后端 CAS，网络隔离）。"""

import json
import os
from pathlib import Path
from urllib.parse import quote

from workflow_support import goto_ready, settings_browser

from gw.god_workflow import routes

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "fixtures"
    / "runninghub"
    / "workflow_legacy_rh_link_slots_2108837566177959938.json"
)


def _shot(page, name):
    target = os.environ.get("GW_QA_SHOT_DIR")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / f"{name}.png"))


def _seed_legacy(page, base):
    """先经 API 建档，再把落盘文件替换为 #22 前旧前端保存形态（fixture），模拟旧数据。"""
    created = page.context.request.post(
        base + "/api/god_workflow/documents?name=" + quote("legacy"),
        data={"1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}}},
        headers={"Origin": base},
    )
    assert created.status == 201, created.text()
    workflow_id = created.json()["workflow_id"]
    path = next(routes._workflow_root().rglob(f"{workflow_id}.json"))
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document.update(workflow_id=workflow_id, version=1)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return workflow_id


def _open(page, base, workflow_id):
    goto_ready(page, base + "/static/pages/workflow.html?id=" + workflow_id)
    page.wait_for_function(
        "(id) => state.currentWorkflow?.workflow_id === id", arg=workflow_id
    )


def test_legacy_workflow_notice_and_one_click_reparse(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        workflow_id = _seed_legacy(page, base)
        _open(page, base, workflow_id)
        notice = page.locator("#workflowReparseNotice")
        notice.wait_for()
        assert "建议重新解析" in notice.inner_text()
        _shot(page, "ac3-legacy-notice")
        page.get_by_role("button", name="重新解析", exact=True).click()
        page.get_by_text("重新解析完成，已生成新版本 v002", exact=True).wait_for()
        notice.wait_for(state="detached")
        assert page.evaluate("state.currentWorkflow.revision") == 2
        item = page.context.request.get(
            base + f"/api/god_workflow/item/{workflow_id}"
        ).json()
        assert item["needs_reparse"] is False and item["revision"] == 2
        _shot(page, "ac3-reparsed")
        assert errors == []


def test_reparse_conflict_refreshes_and_retries_once(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        workflow_id = _seed_legacy(page, base)
        _open(page, base, workflow_id)
        page.locator("#workflowReparseNotice").wait_for()
        # 页面加载后由另一处改名，服务端版本 1→2，页面仍持有 v1。
        renamed = page.context.request.put(
            base + f"/api/god_workflow/item/{workflow_id}/name",
            data={"name": "legacy-renamed", "expected_version": 1},
            headers={"Origin": base},
        )
        assert renamed.ok, renamed.text()
        posts = []
        page.on(
            "request",
            lambda r: (
                posts.append(json.loads(r.post_data or "{}"))
                if r.url.endswith("/reparse")
                else None
            ),
        )
        page.get_by_role("button", name="重新解析", exact=True).click()
        page.get_by_text("重新解析完成，已生成新版本 v003", exact=True).wait_for()
        assert [p["expected_version"] for p in posts] == [1, 2]
        assert errors == []


def test_reparse_source_unavailable_shows_notice(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        workflow_id = _seed_legacy(page, base)
        _open(page, base, workflow_id)
        page.locator("#workflowReparseNotice").wait_for()
        # 422 在真实 fixture 下不可达（判定本身依赖原始来源可解析），此处只替换该响应验证 UI。
        page.route(
            "**/reparse",
            lambda route: route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps(
                    {
                        "detail": {
                            "code": "REPARSE_SOURCE_UNAVAILABLE",
                            "message": "保存的原始来源缺少可重新解析的源图，未做任何修改",
                        }
                    }
                ),
            ),
        )
        page.get_by_role("button", name="重新解析", exact=True).click()
        page.get_by_text(
            "来源不可用：保存的原始来源无法重新解析，工作流未做任何修改", exact=True
        ).wait_for()
        _shot(page, "ac3-source-unavailable")
        assert page.evaluate("state.currentWorkflow.revision") == 1
        assert errors == []
