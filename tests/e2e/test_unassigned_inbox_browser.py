"""C4-AC5b 浏览器回归：「未归属」收件箱与「移入项目」（真实后端 CAS，网络隔离）。"""
import os
from pathlib import Path
from urllib.parse import quote

from gw.asset_registry import repository as asset_repo
from gw.core import local_accounts
from workflow_support import goto_ready, settings_browser


def _shot(page, name):
    target = os.environ.get('GW_QA_SHOT_DIR')
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / f'{name}.png'))


def _create_workflow(page, base, name):
    created = page.context.request.post(base + '/api/god_workflow/documents?name=' + quote(name),
        data={'1': {'class_type': 'LoadImage', 'inputs': {'image': 'input.png'}}}, headers={'Origin': base})
    assert created.status == 201, created.text()
    return created.json()['workflow_id']


def _seed(page, base):
    """真实工作流登记一条有主未归属资产；再登记一条无主（无 workflow_owner）资产，只进管理员收件箱。"""
    _create_workflow(page, base, 'inbox-workflow')
    listed = page.context.request.get(base + '/api/assets/unassigned').json()
    assert listed['total'] == 1, listed
    asset_repo.register_asset_record('ast-c4-unknown-src', '来源不明.png', 'image', metadata={})
    project = page.context.request.post(base + '/api/asset-registry/projects',
        data={'name': 'C4收件项目', 'project_type': 'film'}, headers={'Origin': base})
    assert project.status == 201, project.text()
    return project.json()['project']['project_id']


def _open_inbox(page, base):
    goto_ready(page, base + '/static/pages/assets.html')
    entry = page.locator('#btnUnassignedInbox')
    entry.wait_for(state='visible')
    entry.click()
    page.locator('#unassignedInboxDialog [data-asset-id]').first.wait_for()


def test_unassigned_inbox_lists_marks_and_moves(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        project_id = _seed(page, base)
        _open_inbox(page, base)
        rows = page.locator('#unassignedInboxDialog [data-asset-id]')
        assert rows.count() == 2
        unknown = page.locator('[data-asset-id="ast-c4-unknown-src"]')
        # 管理员收件箱：无主资产按后端 source_unknown/origin_label 标记，有主资产不标记。
        assert unknown.locator('[data-source-unknown]').inner_text() == '来源未知'
        assert page.locator('[data-source-unknown]').count() == 1
        api = page.context.request.get(base + '/api/assets/unassigned').json()['assets']
        flags = {a['asset_id']: (a['source_unknown'], a['origin_label']) for a in api}
        assert flags['ast-c4-unknown-src'] == (True, '来源未知')
        _shot(page, 'ac5b-inbox-open')
        unknown.locator('[data-move-project]').select_option(project_id)
        unknown.get_by_role('button', name='移入项目', exact=True).click()
        page.locator('#unassignedInboxStatus', has_text='已移入项目「C4收件项目」').wait_for()
        unknown.wait_for(state='detached')
        assert rows.count() == 1
        record = page.context.request.get(base + '/api/asset-registry/assets/ast-c4-unknown-src').json()['asset']
        assert record['project_id'] == project_id
        _shot(page, 'ac5b-moved')
        assert errors == []


def _capture_moves(page):
    calls = []
    page.on('response', lambda r: calls.append((r.request.post_data_json, r.status)) if r.url.endswith('/move-to-project') else None)
    return calls


def test_unrelated_new_asset_no_longer_conflicts(monkeypatch, tmp_path):
    """按资产 CAS：打开收件箱后其他资产登记推高库 revision，本资产移动不再 409。"""
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        project_id = _seed(page, base)
        _open_inbox(page, base)
        stale_revision = page.evaluate('GWUnassignedInbox.snapshot.revision')
        asset_version = page.evaluate('GWUnassignedInbox.snapshot.assets.find(a => a.asset_id === "ast-c4-unknown-src").asset_version')
        _create_workflow(page, base, 'bump-revision')  # 无关资产登记，库级 revision 前进
        assert page.context.request.get(base + '/api/assets/unassigned').json()['revision'] > stale_revision
        calls = _capture_moves(page)
        row = page.locator('[data-asset-id="ast-c4-unknown-src"]')
        row.locator('[data-move-project]').select_option(project_id)
        row.get_by_role('button', name='移入项目', exact=True).click()
        page.locator('#unassignedInboxStatus', has_text='已移入项目').wait_for()
        assert calls == [({'project_id': project_id, 'expected_asset_version': asset_version}, 200)]
        assert errors == []


def test_per_asset_conflict_refreshes_item_and_retries_once(monkeypatch, tmp_path):
    """真实资产级冲突：打开收件箱后该资产本身被改（version+1）→409（带 asset_id）→刷新→按新版本重试成功。"""
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        project_id = _seed(page, base)
        _open_inbox(page, base)
        stale = page.evaluate('GWUnassignedInbox.snapshot.assets.find(a => a.asset_id === "ast-c4-unknown-src").asset_version')
        asset_repo.update_asset('ast-c4-unknown-src', {'name': '来源不明-已改名.png'})
        conflicts = []
        page.on('response', lambda r: conflicts.append(r.json()['detail']) if r.url.endswith('/move-to-project') and r.status == 409 else None)
        calls = _capture_moves(page)
        row = page.locator('[data-asset-id="ast-c4-unknown-src"]')
        row.locator('[data-move-project]').select_option(project_id)
        row.get_by_role('button', name='移入项目', exact=True).click()
        page.locator('#unassignedInboxStatus', has_text='已移入项目').wait_for()
        assert [c[1] for c in calls] == [409, 200]
        assert [c[0]['expected_asset_version'] for c in calls] == [stale, stale + 1]
        assert conflicts[0]['code'] == 'VERSION_CONFLICT' and conflicts[0]['asset_id'] == 'ast-c4-unknown-src'
        record = page.context.request.get(base + '/api/asset-registry/assets/ast-c4-unknown-src').json()['asset']
        assert record['project_id'] == project_id
        assert errors == []


def test_per_asset_conflict_twice_tells_user(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        project_id = _seed(page, base)
        _open_inbox(page, base)
        # 每次移动请求到达前都先改动该资产，制造连续两次真实资产级冲突。
        def bump(route):
            asset_repo.update_asset('ast-c4-unknown-src', {'name': 'bumped.png'})
            route.continue_()
        page.route('**/move-to-project', bump)
        calls = _capture_moves(page)
        row = page.locator('[data-asset-id="ast-c4-unknown-src"]')
        row.locator('[data-move-project]').select_option(project_id)
        row.get_by_role('button', name='移入项目', exact=True).click()
        page.locator('#unassignedInboxStatus', has_text='移入失败：该资产刚被其他操作修改，请稍后重试').wait_for()
        assert [c[1] for c in calls] == [409, 409]
        _shot(page, 'ac5b-conflict-twice')
        assert errors == []


def test_anonymous_does_not_see_inbox_entry(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        page.context.clear_cookies()
        goto_ready(page, base + '/static/pages/assets.html')
        page.wait_for_timeout(800)
        assert not page.locator('#btnUnassignedInbox').is_visible()


def test_non_admin_inbox_hides_ownerless_assets(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        _seed(page, base)
        # 首个本地账户为 admin；降为 editor 模拟非管理员（会话按请求实时读取角色）。
        with local_accounts.database() as db:
            db.execute("UPDATE local_users SET role='editor'")
            db.commit()
        _open_inbox(page, base)
        rows = page.locator('#unassignedInboxDialog [data-asset-id]')
        assert rows.count() == 1
        assert page.locator('[data-asset-id="ast-c4-unknown-src"]').count() == 0
        assert page.locator('[data-source-unknown]').count() == 0
        _shot(page, 'ac5b-non-admin-inbox')
        assert errors == []
