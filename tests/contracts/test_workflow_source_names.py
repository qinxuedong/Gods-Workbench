"""原始标题与真实图分别取源，验证目录/资产及失败降级。"""
import json

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core.errors import CleanroomException
from gw.god_workflow import platforms

HEADERS = {"Authorization": "Bearer source-names", "Origin": "http://testserver"}
REFERENCE = "https://www.runninghub.cn/workflow/2108563268972404737"
TITLE = "MiniMax H3 原始工作流标题"
PROMPT = {"1": {"class_type": "LoadImage", "inputs": {"image": "original.png"}},
          "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "source"}}}
CANVAS = {"nodes": [{"id": 1, "type": "LoadImage", "pos": [10, 20], "widgets_values": ["original.png"]},
                    {"id": 2, "type": "SaveImage", "pos": [300, 20], "widgets_values": ["source"]}],
          "links": [[1, 1, 0, 2, 0, "IMAGE"]]}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.delenv("GW_RUNNINGHUB_API_KEY", raising=False)
    monkeypatch.delenv("GW_RUNNINGHUB_ACCESS_TOKEN", raising=False)
    with TestClient(create_app()) as value:
        yield value


def provider_source(monkeypatch, *, fail_detail=False):
    calls = []
    async def source(url, body, headers):
        calls.append(url)
        if url.endswith("getJsonApiFormat"):
            assert headers["Authorization"] == "Bearer synthetic-name-key"
            return {"code": 0, "data": {"prompt": json.dumps(PROMPT)}}
        if url.endswith("getContent"):
            assert headers["Authorization"] == "Bearer synthetic-name-token"
            return {"code": 0, "data": {"workflowContent": json.dumps(CANVAS)}}
        assert url.endswith(("getDetail", "/workflow/detail")), url
        assert "Authorization" not in headers
        if fail_detail:
            raise CleanroomException(504, "WORKFLOW_PROVIDER_TIMEOUT", "公开名称查询超时")
        # 公开详情只有名称，真实拓扑仍必须来自原始接口。
        return {"code": 0, "data": {"name": TITLE, "workflowContent": None}}
    monkeypatch.setattr(platforms, "_post_json", source)
    return calls


@pytest.mark.parametrize("kind", ["api", "canvas"])
@pytest.mark.parametrize("custom_name", [None, "用户自定义名称"])
def test_original_name_survives_parse_storage_catalog_assets_and_restart(client, monkeypatch, kind, custom_name):
    calls = provider_source(monkeypatch)
    configured = client.post("/api/god_workflow/settings", headers=HEADERS,
        json={"rh_api_key" if kind == "api" else "rh_access_token":
              "synthetic-name-key" if kind == "api" else "synthetic-name-token"})
    assert configured.status_code == 200
    body = {"url_or_text": REFERENCE}
    if custom_name:
        body["name"] = custom_name
    parsed = client.post("/api/god_workflow/parse-link", headers=HEADERS, json=body)
    assert parsed.status_code == 200, parsed.text
    expected = custom_name or TITLE
    document = parsed.json()
    assert document["name"] == expected
    assert len(document["nodes"]) == 2 and len(document["connections"]) == 1
    assert any(url.endswith("getDetail") for url in calls)
    with TestClient(create_app()) as restarted:
        stored = restarted.get("/api/god_workflow/item/" + document["workflow_id"], headers=HEADERS).json()
        assert stored["name"] == expected
        items = restarted.get("/api/god_workflow/list", headers=HEADERS).json()["items"]
        assert [entry["name"] for entry in items] == [expected]
        asset = restarted.get("/api/asset-registry/assets/" + document["asset_id"], headers=HEADERS)
        assert asset.status_code == 200, asset.text
        assert asset.json()["asset"]["name"] == expected


@pytest.mark.parametrize("kind", ["api", "canvas"])
def test_missing_name_metadata_never_discards_available_source_graph(client, monkeypatch, kind):
    provider_source(monkeypatch, fail_detail=True)
    monkeypatch.setenv("GW_RUNNINGHUB_API_KEY" if kind == "api" else "GW_RUNNINGHUB_ACCESS_TOKEN",
                       "synthetic-name-key" if kind == "api" else "synthetic-name-token")
    parsed = client.post("/api/god_workflow/parse-link", headers=HEADERS, json={"url_or_text": REFERENCE})
    assert parsed.status_code == 200, parsed.text
    assert len(parsed.json()["nodes"]) == 2 and len(parsed.json()["connections"]) == 1
    assert not parsed.json().get("public_preview")
