"""C4 任务列表过滤与分页契约。

GET /api/god_workflow/tasks 支持按 workflow_id 过滤并按 cursor/limit 分页；
不传任何参数时保持既有响应形状（tasks/data_status/data_gaps）不变。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.god_workflow import registry, routes

TASKS = "/api/god_workflow/tasks"
HEADERS = {"Authorization": "Bearer c4-tasks-pagination", "X-User-Role": "editor", "Origin": "http://testserver"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    with TestClient(create_app()) as test_client:
        yield test_client


def _context():
    """与 HEADERS 客户端完全相同主体的账本上下文。"""
    return routes._auth(HEADERS["Authorization"], "editor", False)


def _seed_tasks(workflow_id, count, *, start=0):
    """写入 count 条任务，created_at 按秒递增且互不相同。"""
    context = _context()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job_ids = []
    for i in range(start, start + count):
        task = registry.create_task(context, workflow_id=workflow_id, prompt_id=None, target="local_comfy")
        registry.update_task(context, task["job_id"], created_at=(base + timedelta(seconds=i)).isoformat())
        job_ids.append(task["job_id"])
    return job_ids


def _get(client, **params):
    response = client.get(TASKS, headers=HEADERS, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_no_params_keeps_legacy_shape(client):
    _seed_tasks("wf-a", 2)
    _seed_tasks("wf-b", 1)
    response = client.get(TASKS, headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"tasks", "data_status", "data_gaps"}
    assert body["data_status"] == "ok" and body["data_gaps"] == []
    assert body["tasks"] == registry.list_tasks(_context())


def test_workflow_pagination_walk_returns_all_tasks(client):
    """回归：单一工作流超过 100 条时翻页必须取回全部，且 Newest 在前。"""
    wf_a_ids = _seed_tasks("wf-a", 130)
    _seed_tasks("wf-b", 5, start=1000)  # 另一工作流的最新任务，绝不能混入。
    pages = [_get(client, workflow_id="wf-a", limit="100")]
    assert len(pages[0]["tasks"]) == 100
    assert pages[0]["has_more"] is True
    assert pages[0]["next_cursor"] == "100"
    while pages[-1]["next_cursor"] is not None:
        pages.append(_get(client, workflow_id="wf-a", limit="100", cursor=pages[-1]["next_cursor"]))
    assert len(pages) == 2
    assert len(pages[1]["tasks"]) == 30
    assert pages[1]["has_more"] is False and pages[1]["next_cursor"] is None
    walked = [task for page in pages for task in page["tasks"]]
    assert [task["job_id"] for task in walked] == list(reversed(wf_a_ids))  # 后创建的在前
    assert set(task["job_id"] for task in walked) == set(wf_a_ids)
    assert all(task["workflow_id"] == "wf-a" for task in walked)
    created = [task["created_at"] for task in walked]
    assert created == sorted(created, reverse=True)  # 全局严格 newest first


def test_workflow_id_echoed_verbatim_including_unknown(client):
    known = _seed_tasks("wf-a", 1)
    body = _get(client, workflow_id="wf-a")
    assert body["workflow_id"] == "wf-a"
    assert [task["job_id"] for task in body["tasks"]] == known

    for unknown in ("no-such-workflow", "wf-a/../settings"):
        body = _get(client, workflow_id=unknown)
        assert body["workflow_id"] == unknown
        assert body["tasks"] == []
        assert body["has_more"] is False
        assert body["next_cursor"] is None


def test_default_limit_is_20(client):
    _seed_tasks("wf-a", 21)
    first = _get(client, workflow_id="wf-a")
    assert len(first["tasks"]) == 20
    assert first["next_cursor"] == "20" and first["has_more"] is True
    second = _get(client, workflow_id="wf-a", cursor="20")
    assert len(second["tasks"]) == 1
    assert second["next_cursor"] is None and second["has_more"] is False


def test_limit_clamped_to_100(client):
    _seed_tasks("wf-a", 130)
    body = _get(client, workflow_id="wf-a", limit="150")
    assert len(body["tasks"]) == 100
    assert body["has_more"] is True and body["next_cursor"] == "100"


def test_exact_multiple_has_no_extra_page(client):
    _seed_tasks("wf-a", 4)
    first = _get(client, workflow_id="wf-a", limit="2")
    assert len(first["tasks"]) == 2
    assert first["next_cursor"] == "2" and first["has_more"] is True
    second = _get(client, workflow_id="wf-a", limit="2", cursor="2")
    assert len(second["tasks"]) == 2
    assert second["next_cursor"] is None and second["has_more"] is False


def test_limit_equal_to_total(client):
    _seed_tasks("wf-a", 5)
    body = _get(client, workflow_id="wf-a", limit="5")
    assert len(body["tasks"]) == 5
    assert body["next_cursor"] is None and body["has_more"] is False


def test_cursor_past_end_returns_empty(client):
    _seed_tasks("wf-a", 3)
    body = _get(client, workflow_id="wf-a", cursor="99")
    assert body["tasks"] == []
    assert body["has_more"] is False and body["next_cursor"] is None


@pytest.mark.parametrize("cursor", ["abc", "-1", "1.5"])
def test_invalid_cursor_rejected(client, cursor):
    _seed_tasks("wf-a", 1)
    response = client.get(TASKS, headers=HEADERS, params={"workflow_id": "wf-a", "cursor": cursor})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_CURSOR"


@pytest.mark.parametrize("limit", ["0", "-3", "abc"])
def test_invalid_limit_rejected(client, limit):
    _seed_tasks("wf-a", 1)
    response = client.get(TASKS, headers=HEADERS, params={"workflow_id": "wf-a", "limit": limit})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_LIMIT"
