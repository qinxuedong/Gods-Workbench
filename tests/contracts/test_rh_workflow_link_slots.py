"""RH 工作流连线槽位契约：API prompt 槽位命名在解析与往返中不得退化为行号。

背景（真实浏览器复现）：fetch & parse 取回 API 格式 prompt，后端 parser 正确产出
target_slot=输入名、source_slot=输出序号；但前端 normalizeMatureWorkflow 曾把数字
to_slot 置于命名 target_slot 之前，二次归一化后所有连线落在输入第 0 行。
本文件固定三件事：公开详情确无源图、API prompt 解析出的槽位命名、以及前端保存
负载（同时携带命名槽位与数字槽位）往返后命名槽位优先。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core.errors import CleanroomException
from gw.god_workflow import platforms
from gw.god_workflow.parser import extract_source_graph, parse_workflow


FIXTURES = Path(__file__).parent / "fixtures" / "runninghub"
PUBLIC_DETAIL = FIXTURES / "workflow_2108837566177959938_public_detail.json"
API_PROMPT = FIXTURES / "workflow_2108837566177959938_api_prompt_synthetic.json"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    return TestClient(create_app())


@pytest.fixture
def api_prompt() -> dict:
    return json.loads(API_PROMPT.read_text(encoding="utf-8"))


def _headers(token="test-token"):
    return {"Authorization": f"Bearer {token}", "X-User-Role": "editor", "Origin": "http://testserver"}


def test_public_detail_has_no_source_graph():
    """公开（非所有者）详情只有节点/模型清单：必须如实判定没有源图。"""
    detail = json.loads(PUBLIC_DETAIL.read_text(encoding="utf-8"))["data"]
    assert extract_source_graph(detail) is None
    assert detail["name"].startswith("角色资产")
    assert "TextEncodeQwenImage21" in detail["customNodes"]


def _slots(document_dict) -> dict[tuple[str, str, str], tuple[str, str]]:
    """按 (source, target, target_slot) 索引 as_dict 形态的 connections 槽位。"""
    return {(connection["source"], connection["target"], connection["target_slot"]):
            (connection["source_slot"], connection["target_slot"])
            for connection in document_dict["connections"]}


def test_api_prompt_connections_keep_named_target_slots(api_prompt):
    """parser 对 API prompt 的每条连线：target_slot=输入名，source_slot=输出序号字符串。"""
    document = parse_workflow(api_prompt, source="comfyui")
    slots = _slots(document.as_dict())
    expected = 0
    for node_id, node in api_prompt.items():
        for input_name, value in node["inputs"].items():
            if isinstance(value, list) and len(value) >= 2 and not isinstance(value[0], (dict, list)):
                expected += 1
                found = slots.get((str(value[0]), node_id, input_name))
                assert found is not None, f"缺少连线 {value[0]}->{node_id}:{input_name}"
                assert found == (str(value[1]), input_name), \
                    f"{value[0]}->{node_id}:{input_name} 槽位应是 ({value[1]!r}, {input_name!r})，实际 {found}"
    assert expected == len(document.connections) == 17

    assert slots[("484", "3", "positive")] == ("0", "positive")
    assert slots[("482", "3", "latent_image")] == ("0", "latent_image")
    assert slots[("483", "3", "model")] == ("0", "model")
    assert slots[("476", "484", "vae")] == ("0", "vae")
    assert slots[("485", "484", "prompt")] == ("0", "prompt")
    assert slots[("477", "484", "clip")] == ("0", "clip")
    assert slots[("470", "484", "images.image_1")] == ("0", "images.image_1")
    assert slots[("13", "479", "width")] == ("0", "width")
    assert slots[("13", "479", "height")] == ("1", "height")

    outgoing_484 = sorted(
        ((connection["source_slot"], connection["target"], connection["target_slot"])
         for connection in document.as_dict()["connections"] if connection["source"] == "484"),
        key=lambda item: int(item[0]))
    assert outgoing_484 == [("0", "3", "positive"), ("1", "3", "negative"), ("2", "482", "on_true")]


def test_document_from_bundle_uses_api_prompt_slots(api_prompt):
    """routes._document_from_bundle 走 API prompt 时同样保持命名槽位。"""
    from gw.god_workflow.routes import _document_from_bundle
    view = _document_from_bundle({"provider": "runninghub", "workflow_id": "2108837566177959938",
                                  "api_json": api_prompt})
    slots = _slots(view.as_dict())
    assert slots[("484", "3", "positive")] == ("0", "positive")
    assert slots[("482", "3", "latent_image")] == ("0", "latent_image")
    assert slots[("470", "484", "images.image_1")] == ("0", "images.image_1")
    assert slots[("13", "479", "height")] == ("1", "height")
    assert slots[("484", "482", "on_true")] == ("2", "on_true")


def test_parse_round_trip_prefers_named_slot_over_numeric_zero():
    """前端保存负载同时携带命名槽位与数字槽位（to_slot=0）：后端必须保留输入名。"""
    payload = {
        "nodes": [{"id": "484", "class_type": "TextEncodeQwenImage21", "inputs": {}},
                  {"id": "3", "class_type": "KSampler", "inputs": {}}],
        "links": [{"id": "l1", "source": "484", "target": "3",
                   "source_slot": "0", "target_slot": "positive", "from_slot": 0, "to_slot": 0}],
    }
    connection = parse_workflow(payload, source="canvas").as_dict()["connections"][0]
    assert connection["source_slot"] == "0"
    assert connection["target_slot"] == "positive"


@pytest.mark.parametrize("code_body,expected_code,provider_code", [
    ({"code": 380, "msg": "secret-msg"}, "RH_SOURCE_REJECTED", "380"),
    ({"code": "380", "msg": "secret-msg"}, "RH_SOURCE_REJECTED", "380"),
    ({"code": 301, "msg": "param apiKey is required"}, "RH_SOURCE_AUTH_FAILED", "301"),
    ({"code": 401, "msg": "secret-msg"}, "RH_SOURCE_AUTH_FAILED", "401"),
    ({"code": 403, "msg": "secret-msg"}, "RH_SOURCE_AUTH_FAILED", "403"),
    ({"code": 1002, "msg": "secret-msg"}, "RH_SOURCE_REJECTED", "1002"),
])
def test_upstream_rejection_maps_codes_and_carries_provider_code(monkeypatch, code_body, expected_code, provider_code):
    """OpenAPI 业务码：301 与 401/403 同为鉴权失败，其余拒绝为 RH_SOURCE_REJECTED；只回显数字码。"""
    async def post(url, payload, headers):
        return code_body

    monkeypatch.setattr(platforms, "_post_json", post)
    with pytest.raises(CleanroomException) as caught:
        asyncio.run(platforms.fetch_runninghub_api_json("2108837566177959938", "synthetic-key"))
    assert caught.value.code == expected_code
    assert caught.value.extra.get("provider_code") == provider_code
    assert f"RunningHub 错误码 {provider_code}" in caught.value.message
    assert "secret-msg" not in caught.value.message
    assert "param apiKey is required" not in caught.value.message


def test_canvas_upstream_rejection_maps_codes_and_carries_provider_code(monkeypatch):
    """getContent 同样走 301→鉴权失败映射与 provider_code 透出。"""
    async def post(url, payload, headers):
        return {"code": 301, "msg": "user not exist"}

    monkeypatch.setattr(platforms, "_post_json", post)
    with pytest.raises(CleanroomException) as caught:
        asyncio.run(platforms.fetch_runninghub_canvas("2108837566177959938", "synthetic-token"))
    assert caught.value.code == "RH_SOURCE_AUTH_FAILED"
    assert caught.value.extra.get("provider_code") == "301"


def test_string_zero_code_is_success(api_prompt, monkeypatch):
    """上游把 code 以字符串 "0" 返回时必须视为成功（getJsonApiFormat）。"""
    async def post(url, payload, headers):
        assert url.endswith("/api/openapi/getJsonApiFormat")
        return {"code": "0", "msg": "success", "data": {"prompt": api_prompt}}

    monkeypatch.setattr(platforms, "_post_json", post)
    prompt = asyncio.run(platforms.fetch_runninghub_api_json("2108837566177959938", "synthetic-key"))
    assert prompt == api_prompt


def test_parse_link_rejection_detail_exposes_provider_code(client, monkeypatch):
    """getJsonApiFormat 返回 380 时 parse-link 502 detail 携带 code 与 provider_code。"""
    configured = client.post("/api/god_workflow/settings", json={"rh_api_key": "synthetic-parse-link-key"},
                             headers={**_headers(), "X-User-Role": "admin"})
    assert configured.status_code == 200

    async def source(url, payload, headers):
        if url.endswith(("getDetail", "/detail")):
            return {"code": 0, "data": {"name": "x"}}
        assert url.endswith("getJsonApiFormat")
        return {"code": 380, "msg": "secret-msg"}

    monkeypatch.setattr(platforms, "_post_json", source)
    response = client.post("/api/god_workflow/parse-link", headers=_headers(),
                           json={"url_or_text": "https://www.runninghub.cn/workflow/2108837566177959938"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "RH_SOURCE_REJECTED"
    assert detail["provider_code"] == "380"
    assert "RunningHub 错误码 380" in detail["message"]
    assert "secret-msg" not in response.text
