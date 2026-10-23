"""C4 任务列表过滤与分页契约。

GET /api/god_workflow/tasks 支持按 workflow_id 过滤并按不透明 cursor/limit 键集分页
（(created_at, task_id) 降序，游标为 base64url 紧凑 JSON）；
不传任何参数时保持既有响应形状（tasks/data_status/data_gaps）不变。
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.god_workflow import registry, routes

TASKS = "/api/god_workflow/tasks"
HEADERS = {
    "Authorization": "Bearer c4-tasks-pagination",
    "X-User-Role": "editor",
    "Origin": "http://testserver",
}


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
    base = datetime(2026, 1, 1, tzinfo=UTC)
    job_ids = []
    for i in range(start, start + count):
        task = registry.create_task(
            context, workflow_id=workflow_id, prompt_id=None, target="local_comfy"
        )
        registry.update_task(
            context,
            task["job_id"],
            created_at=(base + timedelta(seconds=i)).isoformat(),
        )
        job_ids.append(task["job_id"])
    return job_ids


def _encode_cursor(created_at, task_id):
    payload = json.dumps({"c": created_at, "t": task_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _b64url(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _walk_pages(client, first_page, **params):
    """从 first_page 之后继续翻页，返回已取回任务 job_id 的完整列表。"""
    fetched = [task["job_id"] for task in first_page["tasks"]]
    cursor = first_page["next_cursor"]
    while cursor is not None:
        body = _get(client, cursor=cursor, **params)
        fetched.extend(task["job_id"] for task in body["tasks"])
        cursor = body["next_cursor"]
    return fetched


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
    assert pages[0]["next_cursor"]  # 不透明键集游标，非空即可
    while pages[-1]["next_cursor"] is not None:
        pages.append(
            _get(
                client, workflow_id="wf-a", limit="100", cursor=pages[-1]["next_cursor"]
            )
        )
    assert len(pages) == 2
    assert len(pages[1]["tasks"]) == 30
    assert pages[1]["has_more"] is False and pages[1]["next_cursor"] is None
    walked = [task for page in pages for task in page["tasks"]]
    assert [task["job_id"] for task in walked] == list(
        reversed(wf_a_ids)
    )  # 后创建的在前
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
    assert first["next_cursor"] and first["has_more"] is True
    second = _get(client, workflow_id="wf-a", cursor=first["next_cursor"])
    assert len(second["tasks"]) == 1
    assert second["next_cursor"] is None and second["has_more"] is False


def test_limit_clamped_to_100(client):
    _seed_tasks("wf-a", 130)
    body = _get(client, workflow_id="wf-a", limit="150")
    assert len(body["tasks"]) == 100
    assert body["has_more"] is True and body["next_cursor"]


def test_exact_multiple_has_no_extra_page(client):
    _seed_tasks("wf-a", 4)
    first = _get(client, workflow_id="wf-a", limit="2")
    assert len(first["tasks"]) == 2
    assert first["next_cursor"] and first["has_more"] is True
    second = _get(client, workflow_id="wf-a", limit="2", cursor=first["next_cursor"])
    assert len(second["tasks"]) == 2
    assert second["next_cursor"] is None and second["has_more"] is False


def test_cursor_past_end_returns_empty(client):
    """游标编码比所有任务都旧的键：键集分页下应得到空页且无下一页。"""
    _seed_tasks("wf-a", 3)
    stale = _encode_cursor(
        "1999-01-01T00:00:00+00:00", "00000000-0000-0000-0000-000000000000"
    )
    body = _get(client, workflow_id="wf-a", cursor=stale)
    assert body["tasks"] == []
    assert body["has_more"] is False and body["next_cursor"] is None


@pytest.mark.parametrize(
    "cursor",
    [
        "abc!",
        "-1",
        "1.5",
        _b64url("[1,2]"),
        _b64url('{"c":1}'),
        "x" * 600,
    ],
)
def test_invalid_cursor_rejected(client, cursor):
    _seed_tasks("wf-a", 1)
    response = client.get(
        TASKS, headers=HEADERS, params={"workflow_id": "wf-a", "cursor": cursor}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_CURSOR"


@pytest.mark.parametrize("limit", ["0", "-3", "abc"])
def test_invalid_limit_rejected(client, limit):
    _seed_tasks("wf-a", 1)
    response = client.get(
        TASKS, headers=HEADERS, params={"workflow_id": "wf-a", "limit": limit}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_LIMIT"


def test_deletions_between_pages_no_duplicates_no_missing(client):
    """回归：翻页间隙任务被删除，键集分页不得重复也不得遗漏任何现存任务。"""
    ids = _seed_tasks("wf-a", 10)  # 创建序 = created_at 升序；第 1 页持有最新的 4 条
    page1 = _get(client, workflow_id="wf-a", limit="4")
    assert len(page1["tasks"]) == 4
    page1_ids = [task["job_id"] for task in page1["tasks"]]
    context = _context()
    deleted = {page1_ids[0], page1_ids[1], ids[2]}  # 第 1 页 2 条 + 尚未取回的 1 条
    for task_id in deleted:
        registry._task_path(context, task_id).unlink()

    fetched = _walk_pages(client, page1, workflow_id="wf-a", limit="4")
    assert len(fetched) == len(set(fetched))  # 无重复
    # 第 1 页里被删的两条在删除前已取回，允许留在并集内；
    # 但尚未取回就被删除的 ids[2] 绝不能再出现在后续页，后续页也无遗漏。
    assert set(fetched) - set(page1_ids) == {
        task_id for task_id in ids if task_id not in deleted | set(page1_ids)
    }


def test_newer_task_inserted_between_pages_no_duplicates(client):
    """回归：翻页间隙插入更新的任务，游标之后的内容不受影响，无重复无遗漏。"""
    ids = _seed_tasks("wf-a", 6)
    page1 = _get(client, workflow_id="wf-a", limit="3")
    _seed_tasks("wf-a", 1, start=100)  # 两页之间插入一条 created_at 更新的任务
    fetched = _walk_pages(client, page1, workflow_id="wf-a", limit="3")
    assert len(fetched) == len(set(fetched))
    assert set(fetched) == set(ids)  # 新任务按游标语义留到下一轮，既有任务一条不漏
