"""QIN-584（GitHub #678）资产库首屏按视口分页契约。

票面 AC1：无论资产总数是否小于等于 1500，首屏列表实际返回条数都不得超过
视口动态上限；禁止「总数不足 1500 就一次下发全库」。

修复前 ``repository`` 的收窄条件是 ``dynamic_limit is not None and total > 1500``，
千级库配 ``limit=1500`` 等于一次下发整库。本文件锁死收窄对任意规模生效，
并核对分页游标在收窄后仍不重不漏。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
LIBRARY_SIZE = 12


@pytest.fixture()
def small_library(client: TestClient) -> list[str]:
    """预置一个小于 1500 条的库，用于验证「小库也不许整库下发」。"""
    response = client.post(
        "/api/asset-registry/assets/import",
        headers=AUTH,
        json={"items": [{"name": f"分镜参考-{index:02d}.png", "kind": "image"} for index in range(LIBRARY_SIZE)]},
    )
    assert response.status_code == 200, response.text
    return [asset["asset_id"] for asset in response.json()["assets"]]


def _page(client: TestClient, **params) -> dict:
    response = client.get("/api/asset-registry/assets", headers=AUTH, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_qin584_small_library_still_capped_by_dynamic_limit(client: TestClient, small_library):
    """AC1：total < 1500 时首屏仍按动态上限分页，不再整库下发。"""
    data = _page(client, limit=1500, dynamic_limit=5)
    assert data["total"] == LIBRARY_SIZE
    assert len(data["items"]) == 5, "小库也必须按动态上限收窄，禁止整库下发"
    assert data["limit"] == 5
    assert data["offset"] == 0
    assert data["has_more"] is True
    assert data["next_offset"] == 5
    assert data["next_cursor"]


def test_qin584_paged_cursor_covers_library_without_gap_or_duplicate(client: TestClient, small_library):
    """AC4 后端面：收窄后的游标翻页覆盖全库，不重复、不漏项。"""
    first = _page(client, limit=1500, dynamic_limit=5)
    second = _page(client, limit=1500, dynamic_limit=5, cursor=first["next_cursor"])
    third = _page(client, limit=1500, dynamic_limit=5, cursor=second["next_cursor"])
    assert len(second["items"]) == 5
    assert third["has_more"] is False
    assert third["next_offset"] is None

    collected = [item["asset_id"] for item in first["items"] + second["items"] + third["items"]]
    assert len(collected) == LIBRARY_SIZE
    assert len(set(collected)) == LIBRARY_SIZE, "分页不得重复下发同一条素材"
    assert set(collected) == set(small_library), "分页不得漏项"


def test_qin584_offset_paging_matches_cursor_paging(client: TestClient, small_library):
    """AC4 后端面：offset 分页与 cursor 分页指向同一序列，首尾衔接不回退。"""
    first = _page(client, limit=1500, dynamic_limit=5)
    by_offset = _page(client, limit=1500, dynamic_limit=5, offset=first["next_offset"])
    by_cursor = _page(client, limit=1500, dynamic_limit=5, cursor=first["next_cursor"])
    assert [item["asset_id"] for item in by_offset["items"]] == [
        item["asset_id"] for item in by_cursor["items"]
    ]
    assert by_offset["next_offset"] == by_cursor["next_offset"]


def test_qin584_dynamic_limit_never_raises_caller_limit(client: TestClient, small_library):
    """dynamic_limit 只收窄不放宽：大于 limit 时以 limit 为准。"""
    data = _page(client, limit=3, dynamic_limit=1200)
    assert data["limit"] == 3
    assert len(data["items"]) == 3


def test_qin584_absent_dynamic_limit_keeps_explicit_limit(client: TestClient, small_library):
    """不带 dynamic_limit 的显式 limit 保持原有语义（服务端与脚本直连仍在用）。"""
    data = _page(client, limit=1500)
    assert data["total"] == LIBRARY_SIZE
    assert len(data["items"]) == LIBRARY_SIZE
    assert data["limit"] == 1500
    assert data["has_more"] is False
