# -*- coding: utf-8 -*-
"""C11-T2 结构化多维过滤与稳定游标契约。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
PATH = "/api/asset-registry/assets"


def _import(client: TestClient, items: list[dict[str, object]]) -> list[str]:
    response = client.post(PATH + "/import", headers=AUTH, json={"items": items})
    assert response.status_code == 200, response.text
    return response.json()["asset_ids"]


def _structured_params(filters: dict[str, object] | None = None, **extra: object) -> dict[str, object]:
    params: dict[str, object] = {
        "scope": json.dumps(["asset"], separators=(",", ":")),
        "filters": json.dumps(filters or {}, separators=(",", ":")),
        "include": json.dumps(["facets"], separators=(",", ":")),
        "sort": "name",
        "limit": 1,
    }
    params.update(extra)
    return params


def test_c11_structured_filters_and_facets_are_composed(client: TestClient) -> None:
    created = _import(
        client,
        [
            {"name": "zeta.png", "kind": "image"},
            {"name": "alpha.png", "kind": "image"},
            {"name": "hero.mp4", "kind": "video"},
        ],
    )
    tagged = client.post(PATH + "/tags", headers=AUTH,
                         json={"asset_ids": created[:2], "names": ["hero"]})
    assert tagged.status_code == 200, tagged.text
    tag_id = tagged.json()["tags"][0]["tag_id"]
    project = client.post("/api/asset-registry/projects", headers=AUTH,
                          json={"name": "C11 filter fixture", "project_type": "film"})
    assert project.status_code == 201, project.text
    project_id = project.json()["project"]["project_id"]
    bound = client.post(f"/api/asset-registry/projects/{project_id}/assets", headers=AUTH,
                        json={"asset_ids": created[:2]})
    assert bound.status_code == 200, bound.text
    archived = client.patch(f"{PATH}/{created[0]}", headers=AUTH, json={"archived": True})
    assert archived.status_code == 200, archived.text

    filters = {
        "kind": ["image"],
        "project_id": [project_id],
        "tag_id": [tag_id],
        "created_at": {"from": "2000-01-01T00:00:00Z", "to": "2100-01-01T00:00:00Z"},
        "status": {"asset": ["active"]},
    }
    response = client.get(PATH, headers=AUTH,
                           params=_structured_params(filters, limit=1))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["asset_id"] for item in body["items"]] == [created[1]]
    item = body["items"][0]
    assert item["entity_type"] == "asset"
    assert item["status"] == "active"
    assert item["tag_id"] == [tag_id]
    assert body["page"] == {
        "limit": 1,
        "has_more": False,
        "next_cursor": None,
        "collection_revision": body["revision"],
    }
    assert body["applied_filters"]["kind"] == ["image"]
    assert body["applied_filters"]["status"] == {"asset": ["active"]}
    assert body["facets"]["kinds"] == [{"name": "image", "count": 1}]
    assert body["data_status"] == "complete"
    assert body["data_gaps"] == []


def test_c11_cursor_is_query_and_revision_bound_without_duplicates(client: TestClient) -> None:
    created = _import(
        client,
        [
            {"name": "zeta.png", "kind": "image"},
            {"name": "alpha.png", "kind": "image"},
            {"name": "beta.png", "kind": "image"},
        ],
    )
    filters = {"kind": ["image"], "status": {"asset": ["active"]}}
    first = client.get(PATH, headers=AUTH, params=_structured_params(filters, limit=1))
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["page"]["has_more"] is True
    cursor = first_body["page"]["next_cursor"]
    seen = [item["asset_id"] for item in first_body["items"]]

    while cursor:
        page = client.get(PATH, headers=AUTH,
                          params=_structured_params(filters, limit=1, cursor=cursor))
        assert page.status_code == 200, page.text
        body = page.json()
        seen.extend(item["asset_id"] for item in body["items"])
        cursor = body["page"]["next_cursor"]

    assert seen == [created[1], created[2], created[0]]
    assert len(seen) == len(set(seen)) == 3

    mismatch = client.get(PATH, headers=AUTH,
                          params=_structured_params({"kind": ["video"]}, cursor=first_body["page"]["next_cursor"]))
    assert mismatch.status_code == 400, mismatch.text
    assert mismatch.json()["detail"]["code"] == "INVALID_CURSOR"

    stale_cursor = first_body["page"]["next_cursor"]
    changed = client.patch(f"{PATH}/{created[0]}", headers=AUTH,
                           json={"name": "zeta-renamed.png"})
    assert changed.status_code == 200, changed.text
    stale = client.get(PATH, headers=AUTH,
                        params=_structured_params(filters, limit=1, cursor=stale_cursor))
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"filters": json.dumps({"unknown": ["value"]})}, "UNSUPPORTED_FILTER_VALUE"),
        ({"scope": json.dumps(["project"])}, "UNSUPPORTED_FILTER_VALUE"),
        ({"filters": json.dumps({"created_at": {"from": "2026-10-19T00:00:00Z", "to": "2026-10-18T00:00:00Z"}})},
         "INVALID_TIME_RANGE"),
        ({"limit": 201}, "INVALID_LIMIT"),
    ],
)
def test_c11_invalid_structured_filters_fail_closed(
    client: TestClient, params: dict[str, object], code: str
) -> None:
    query = {"scope": json.dumps(["asset"], separators=(",", ":"))}
    query.update(params)
    response = client.get(PATH, headers=AUTH, params=query)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == code


def test_c11_empty_structured_result_keeps_standard_page(client: TestClient) -> None:
    response = client.get(
        PATH,
        headers=AUTH,
        params=_structured_params({"project_id": ["project-does-not-exist"]}, limit=50),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["page"]["limit"] == 50
    assert body["page"]["has_more"] is False
    assert body["page"]["next_cursor"] is None
    assert body["facets"] is not None
