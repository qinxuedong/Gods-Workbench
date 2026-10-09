from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core.errors import CleanroomException
from gw.god_workflow import platforms
from gw.god_workflow.parser import WorkflowParseError, parse_workflow


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    return TestClient(create_app())


def test_parser_reads_canvas_fixture():
    fixture = Path(__file__).parents[2] / "docs" / "fixtures" / "canvas-workflow-minimal.json"
    document = parse_workflow(json.loads(fixture.read_text(encoding="utf-8")), source="canvas")
    assert document.source.value == "canvas"
    assert [node.id for node in document.nodes] == ["nd-0001", "nd-0002"]
    assert document.connections[0].source == "nd-0001"


def test_workflow_routes_require_authentication(client):
    response = client.get("/api/god_workflow/sources")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_workflow_parse_and_storage_use_runtime_data_root(client, tmp_path):
    headers = {"Authorization": "Bearer test-token", "X-User-Role": "editor", "Origin": "http://testserver"}
    payload = {"nodes": [{"id": "n1", "kind": "input"}], "connections": []}
    parsed = client.post("/api/god_workflow/parse?source=canvas", json=payload, headers=headers)
    assert parsed.status_code == 200
    saved = client.post("/api/god_workflow/documents?source=canvas&name=demo", json=payload, headers=headers)
    assert saved.status_code == 201
    workflow_id = saved.json()["workflow_id"]
    assert list((tmp_path / "data" / "workflow").glob(f"*/{workflow_id}.json"))


def _headers(token="test-token"):
    return {"Authorization": f"Bearer {token}", "X-User-Role": "editor", "Origin": "http://testserver"}


def _tiny_png() -> bytes:
    """A real 3x2 PNG produced by Pillow; no synthetic byte literals."""
    Image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _instrumented_client(monkeypatch, transport, tmp_path=None):
    """TestClient whose outbound httpx transport is pinned to ``transport``.

    The default (offline) sandbox has no route to any local ComfyUI host, so the
    probe test injects a transport instead of reaching the network.  The patched
    constructor is also *recorded*, proving the production code path really does
    perform the ``/object_info`` request rather than returning canned data.
    """
    import httpx as httpx_module

    captured: dict[str, object] = {"calls": 0}
    real_client = httpx_module.Client

    def _factory(*args, **kwargs):
        captured["calls"] = int(captured["calls"]) + 1
        captured["trust_env"] = kwargs.get("trust_env")
        captured["follow_redirects"] = kwargs.get("follow_redirects")
        return real_client(*args, transport=transport, **{k: v for k, v in kwargs.items() if k != "transport"})

    monkeypatch.setattr(httpx_module, "Client", _factory)
    monkeypatch.setattr(httpx_module, "HTTPTransport", real_client)
    client = TestClient(create_app())
    client.captured_probe = captured  # type: ignore[attr-defined]
    return client


def _comfy_payload():
    return {"1": {"class_type": "Load", "inputs": {}}, "2": {"class_type": "Save", "inputs": {"image": ["1", 0]}}}


def test_parser_rejects_error_without_graph():
    with pytest.raises(WorkflowParseError):
        parse_workflow({"error": {"anything": "goes"}})


def test_parser_requires_comfy_class_type_and_inputs():
    with pytest.raises(WorkflowParseError):
        parse_workflow({"nodes": [{"id": "1", "inputs": {}}]})
    with pytest.raises(WorkflowParseError):
        parse_workflow({"nodes": [{"id": "1", "class_type": "Load"}]})


def test_parser_rejects_bad_position_duplicate_and_orphan_links():
    with pytest.raises(WorkflowParseError):
        parse_workflow({"nodes": [{"id": "1", "kind": "x", "position": {"x": "nan", "y": 1}}]}, source="canvas")
    with pytest.raises(WorkflowParseError):
        parse_workflow({"nodes": [{"id": "1", "kind": "x"}, {"id": "1", "kind": "y"}]}, source="canvas")
    with pytest.raises(WorkflowParseError):
        parse_workflow({"nodes": [{"id": "1", "kind": "x"}], "links": [["l", "1", 0, "missing", 0]]}, source="canvas")


def test_parser_deep_copies_raw_payload():
    payload = {"nodes": [{"id": "1", "kind": "x"}]}
    document = parse_workflow(payload, source="canvas")
    payload["nodes"][0]["id"] = "changed"
    assert document.raw_payload["nodes"][0]["id"] == "1"


def test_parser_reads_canvas_array_links():
    document = parse_workflow({"nodes": [{"id": "1", "kind": "x"}, {"id": "2", "kind": "y"}], "links": [["l", "1", 0, "2", 1]]}, source="canvas")
    assert document.connections[0].source == "1"
    assert document.connections[0].target_slot == "1"


def test_parser_reads_api_input_links():
    document = parse_workflow(_comfy_payload())
    assert document.connections[0].source == "1"
    assert document.connections[0].target == "2"


def test_api_rejects_invalid_json(client):
    response = client.post("/api/god_workflow/parse", content=b"{", headers={**_headers(), "Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


def test_api_versioned_lifecycle_and_delete_requires_version(client):
    saved = client.post("/api/god_workflow/documents?source=canvas", json={"nodes": [{"id": "1", "kind": "x"}]}, headers=_headers())
    workflow_id = saved.json()["workflow_id"]
    missing = client.delete(f"/api/god_workflow/documents/{workflow_id}", headers=_headers())
    assert missing.status_code == 400
    renamed = client.post(f"/api/god_workflow/documents/{workflow_id}/rename", json={"name": "new", "expected_version": 1}, headers=_headers())
    assert renamed.status_code == 200 and renamed.json()["version"] == 2
    conflict = client.put(f"/api/god_workflow/documents/{workflow_id}", json={"expected_version": 1, "payload": {"nodes": [{"id": "1", "kind": "x"}]}, "source": "canvas"}, headers=_headers())
    assert conflict.status_code == 409
    deleted = client.request("DELETE", f"/api/god_workflow/documents/{workflow_id}", json={"expected_version": 2}, headers=_headers())
    assert deleted.status_code == 200
    assert client.get(f"/api/god_workflow/documents/{workflow_id}", headers=_headers()).status_code == 404


def test_storage_identity_isolation_and_no_public_copy(client, tmp_path):
    first = client.post("/api/god_workflow/documents?source=canvas", json={"nodes": [{"id": "1", "kind": "x"}]}, headers=_headers("one"))
    workflow_id = first.json()["workflow_id"]
    other = client.get(f"/api/god_workflow/documents/{workflow_id}", headers=_headers("two"))
    assert other.status_code == 404
    assert not list((tmp_path / "data" / "workflow").glob(f"*/{workflow_id}.json")) or len(list((tmp_path / "data" / "workflow").glob(f"*/{workflow_id}.json"))) == 1
    assert not (tmp_path / "data" / "workflow" / workflow_id).exists()


def test_reread_preserves_raw_payload(client):
    payload = {"nodes": [{"id": "1", "kind": "x", "metadata": {"nested": True}}]}
    saved = client.post("/api/god_workflow/documents?source=canvas", json=payload, headers=_headers())
    workflow_id = saved.json()["workflow_id"]
    reread = client.get(f"/api/god_workflow/documents/{workflow_id}", headers=_headers())
    assert reread.status_code == 200
    assert reread.json()["workflow"]["raw_payload"] == payload




def test_capabilities_advertise_partial_boundaries(client):
    response = client.get("/api/god_workflow/capabilities", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert "provider-fetch-gateway" in body["implemented"]
    assert "workflow execution and polling" in body["implemented"]
    assert "native ComfyUI host acceptance" in body["todo"]
    assert body["data_status"] == "partial"


def test_json_upload_is_bounded_and_parsed(client):
    response = client.post("/api/god_workflow/upload?source=canvas", content=b'{"nodes":[{"id":"1","kind":"input"}]}',
                           headers={**_headers(), "Content-Type": "application/json"})
    assert response.status_code == 200
    assert response.json()["workflow"]["source"] == "canvas"


def test_upload_rejects_oversized_body(client):
    response = client.post("/api/god_workflow/upload", content=b"x" * (4 * 1024 * 1024 + 1), headers=_headers())
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "WORKFLOW_UPLOAD_TOO_LARGE"


def test_png_upload_extracts_workflow_text(client):
    PIL = pytest.importorskip("PIL.Image")
    from io import BytesIO
    image = PIL.new("RGB", (1, 1), "white")
    image.info["workflow"] = json.dumps({"nodes": [{"id": "1", "kind": "input"}]})
    output = BytesIO()
    image.save(output, format="PNG", pnginfo=__import__("PIL.PngImagePlugin", fromlist=["PngInfo"]).PngInfo())
    # Rebuild with a supported PNG text chunk.
    pnginfo = __import__("PIL.PngImagePlugin", fromlist=["PngInfo"]).PngInfo()
    pnginfo.add_text("workflow", json.dumps({"nodes": [{"id": "1", "kind": "input"}]}))
    output = BytesIO()
    image.save(output, format="PNG", pnginfo=pnginfo)
    response = client.post("/api/god_workflow/upload", content=output.getvalue(), headers={**_headers(), "Content-Type": "image/png"})
    assert response.status_code == 200
    assert response.json()["workflow"]["nodes"][0]["id"] == "1"


def test_provider_fetch_requires_supported_adapter(client):
    # comfyui 没有真实抓取适配器：必须 400 明确拒绝，而不是伪装可用。
    response = client.post("/api/god_workflow/providers/comfyui/fetch", json={"workflow_id": "abc"}, headers=_headers())
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_PROVIDER"


def test_provider_fetch_rejects_empty_reference(client):
    response = client.post("/api/god_workflow/providers/runninghub/fetch", json={}, headers=_headers())
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_PROVIDER_REFERENCE"


def test_provider_reference_extraction_rejects_unknown_host():
    with pytest.raises(CleanroomException) as excinfo:
        platforms.extract_runninghub_reference("https://evil.test/x")
    assert excinfo.value.code == "INVALID_RH_LINK"


def test_liblib_reference_extraction_from_comfy_link():
    info = platforms.extract_liblib_reference(
        "https://www.liblib.art/comfy?opencomfy=workflowData-23368461&comfyname=Qwen&comfyOrid=877af2cd"
    )
    assert info["version_id"] == "23368461"
    assert info["version_uuid"] == "877af2cd"


def test_runninghub_reference_extraction_variants():
    info = platforms.extract_runninghub_reference(
        "看看这个工作流 https://www.runninghub.cn/workflow/2104915887789797378 很好用"
    )
    assert info["workflow_id"] == "2104915887789797378"
    assert info["domain"] == "www.runninghub.cn"
    assert platforms.extract_runninghub_reference("2104915887789797378")["workflow_id"] == "2104915887789797378"
    assert platforms.is_liblib_reference("https://www.liblib.art/modelinfo/68675d8a91b74ecd9943838d4e971deb") is True
    assert platforms.is_runninghub_reference("https://www.runninghub.cn/post/2014522302368587778") is True


def test_task_execute_requires_configured_comfy_endpoint(client, monkeypatch):
    # 未配置 GW_COMFYUI_URL 时不得下发任何请求，也不得伪造 task_id。
    monkeypatch.delenv("GW_COMFYUI_URL", raising=False)
    response = client.post("/api/god_workflow/tasks/execute",
                           json={"workflow": {"1": {"class_type": "Load", "inputs": {}}}},
                           headers=_headers())
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "WORKFLOW_EXECUTOR_UNAVAILABLE"


def test_task_execute_rejects_empty_prompt(client, monkeypatch):
    # 配置了端点但 prompt 为空：这是请求错误，不是执行器不可用。
    monkeypatch.setenv("GW_COMFYUI_URL", "http://127.0.0.1:8188")
    response = client.post("/api/god_workflow/tasks/execute", json={"workflow": {}}, headers=_headers())
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_WORKFLOW"


def test_task_status_unknown_id_returns_not_found(client):
    # 任务存储已启用：未知任务必须 404，而不是笼统的 503。
    response = client.get("/api/god_workflow/tasks/3f1c5b7a-1d2e-4a6f-9c8b-0d5e6f7a8b9c", headers=_headers())
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "TASK_NOT_FOUND"


def test_task_cancel_unknown_id_returns_not_found(client):
    response = client.post("/api/god_workflow/tasks/3f1c5b7a-1d2e-4a6f-9c8b-0d5e6f7a8b9c/cancel", headers=_headers())
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "TASK_NOT_FOUND"


def test_canvas_contract_and_export_have_no_internal_paths(client):
    contract = client.get("/api/god_workflow/canvas/contract", headers=_headers())
    assert contract.status_code == 200
    exported = client.post("/api/god_workflow/canvas/export", json={"nodes": [{"id": "1", "kind": "input"}]}, headers=_headers())
    assert exported.status_code == 200
    assert exported.json()["format"] == "json"
    assert "raw_payload" in exported.json()["workflow"]
    assert "D:\\" not in exported.text and "/sessions/" not in exported.text




def test_parser_preserves_complete_document_fields():
    payload = {"workflow_id": "wf", "version": 4, "folder_id": "folder", "viewport": {"zoom": 2},
               "note": {"text": "memo"}, "media": [{"asset_id": "a1"}], "dependencies": ["dep"],
               "extensions": {"vendor": {"x": 1}}, "unknown_root": True,
               "nodes": [{"id": "n", "kind": "custom", "position": {"x": 3, "y": 4, "width": 100, "height": 50},
                          "inputs": {"a": 1}, "outputs": {"b": 2}, "widgets": {"value": 3},
                          "asset_ids": ["a1"], "unknown_node": "kept"}]}
    document = parse_workflow(payload, source="canvas")
    value = document.as_dict()
    assert value["workflow_id"] == "wf" and value["version"] == 4
    assert value["viewport"] == {"zoom": 2} and value["note"] == {"text": "memo"}
    assert value["media"] == [{"asset_id": "a1"}] and value["dependencies"] == ["dep"]
    assert value["nodes"][0]["position"]["width"] == 100
    assert value["nodes"][0]["widgets"] == {"value": 3}
    assert value["nodes"][0]["extensions"]["unknown_node"] == "kept"


def test_parser_preserves_raw_payload_without_aliasing_nested_values():
    payload = {"nodes": [{"id": "n", "kind": "x", "custom": {"a": [1]}}], "viewport": {"zoom": 1}}
    document = parse_workflow(payload, source="canvas")
    payload["nodes"][0]["custom"]["a"].append(2)
    assert document.raw_payload["nodes"][0]["custom"]["a"] == [1]


def test_folder_crud_and_tree(client):
    root = client.post("/api/god_workflow/folders", json={"name": "Root"}, headers=_headers())
    assert root.status_code == 201
    folder = root.json()["folder"]
    child = client.post("/api/god_workflow/folders", json={"name": "Child", "parent_id": folder["folder_id"]}, headers=_headers())
    assert child.status_code == 201
    tree = client.get("/api/god_workflow/folders/tree", headers=_headers())
    assert tree.status_code == 200 and tree.json()["tree"][0]["children"][0]["name"] == "Child"


def test_folder_patch_uses_revision_cas(client):
    created = client.post("/api/god_workflow/folders", json={"name": "old"}, headers=_headers()).json()["folder"]
    changed = client.patch(f"/api/god_workflow/folders/{created['folder_id']}",
                           json={"name": "new", "expected_revision": 1}, headers=_headers())
    assert changed.status_code == 200 and changed.json()["folder"]["revision"] == 2
    conflict = client.patch(f"/api/god_workflow/folders/{created['folder_id']}",
                            json={"name": "bad", "expected_revision": 1}, headers=_headers())
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "VERSION_CONFLICT"


def test_folder_delete_requires_revision_and_rejects_non_empty(client):
    folder = client.post("/api/god_workflow/folders", json={"name": "used"}, headers=_headers()).json()["folder"]
    client.post("/api/god_workflow/documents?source=canvas", json={"folder_id": folder["folder_id"], "nodes": []}, headers=_headers())
    response = client.request("DELETE", f"/api/god_workflow/folders/{folder['folder_id']}",
                              json={"expected_revision": 1}, headers=_headers())
    assert response.status_code == 409 and response.json()["detail"]["code"] == "FOLDER_NOT_EMPTY"


def test_folder_move_rejects_cycle(client):
    parent = client.post("/api/god_workflow/folders", json={"name": "p"}, headers=_headers()).json()["folder"]
    child = client.post("/api/god_workflow/folders", json={"name": "c", "parent_id": parent["folder_id"]}, headers=_headers()).json()["folder"]
    response = client.post(f"/api/god_workflow/folders/{parent['folder_id']}/move",
                           json={"parent_id": child["folder_id"], "expected_revision": 1}, headers=_headers())
    assert response.status_code == 409 and response.json()["detail"]["code"] == "FOLDER_CYCLE"


def test_document_folder_id_is_persisted(client):
    folder = client.post("/api/god_workflow/folders", json={"name": "docs"}, headers=_headers()).json()["folder"]
    saved = client.post("/api/god_workflow/documents?source=canvas",
                        json={"folder_id": folder["folder_id"], "nodes": []}, headers=_headers())
    workflow_id = saved.json()["workflow_id"]
    fetched = client.get(f"/api/god_workflow/documents/{workflow_id}", headers=_headers())
    assert fetched.json()["workflow"]["folder_id"] == folder["folder_id"]


def test_document_full_update_preserves_unknown_fields(client):
    payload = {"nodes": [{"id": "n", "kind": "x", "widgets": [1], "custom": {"a": 2}}],
               "viewport": {"zoom": 1}, "note": "n", "media": ["asset"], "dependencies": ["d"], "custom_root": 3}
    saved = client.post("/api/god_workflow/documents?source=canvas", json=payload, headers=_headers()).json()
    workflow_id = saved["workflow_id"]
    fetched = client.get(f"/api/god_workflow/documents/{workflow_id}", headers=_headers()).json()["workflow"]
    assert fetched["raw_payload"] == payload
    assert fetched["nodes"][0]["extensions"]["custom"] == {"a": 2}


def test_canvas_export_is_lossless_by_default(client):
    payload = {"nodes": [{"id": "n", "kind": "x", "widgets": {"v": 1}, "custom": True}], "viewport": {"x": 2}, "note": "keep"}
    response = client.post("/api/god_workflow/canvas/export", json=payload, headers=_headers())
    assert response.status_code == 200 and response.json()["workflow"]["raw_payload"] == payload


def test_diagnostics_exposes_partial_executor_state(client):
    response = client.get("/api/god_workflow/diagnostics", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["data_status"] == "partial"
    assert body["diagnostics"]["executor"]["code"] == "WORKFLOW_EXECUTOR_UNAVAILABLE"


def test_folder_reads_are_principal_isolated(client):
    created = client.post("/api/god_workflow/folders", json={"name": "private"}, headers=_headers("one")).json()["folder"]
    response = client.get(f"/api/god_workflow/folders/{created['folder_id']}", headers=_headers("two"))
    assert response.status_code == 404


def test_document_move_requires_expected_version(client):
    first = client.post("/api/god_workflow/documents?source=canvas", json={"nodes": []}, headers=_headers()).json()
    folder = client.post("/api/god_workflow/folders", json={"name": "target"}, headers=_headers()).json()["folder"]
    response = client.patch(f"/api/god_workflow/documents/{first['workflow_id']}",
                            json={"folder_id": folder["folder_id"], "payload": {"nodes": []}}, headers=_headers())
    assert response.status_code == 400 and response.json()["detail"]["code"] == "INVALID_VERSION"


def test_diagnostics_requires_authentication(client):
    response = client.get("/api/god_workflow/diagnostics")
    assert response.status_code == 401 and response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_workflow_write_is_csrf_protected(tmp_path, monkeypatch):
    # 不携带 Cookie，不应被中间件误判为 CSRF 请求。
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    from gw.core import session as session_store
    session_id = session_store.create_session({"user_id": "workflow-user", "username": "workflow-user", "role": "editor"})
    with TestClient(create_app()) as api:
        response = api.post(
            "/api/god_workflow/parse",
            json={"nodes": []},
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "CSRF_ORIGIN_REJECTED"


# ---------------------------------------------------------------------------
# 新增契约：设置持久化 / 文件夹 PUT / 自动布局 / 参数提升 / 导出分支 /
# 本地对比 / 素材上传 / 剪贴板
# ---------------------------------------------------------------------------

def _save_workflow(client, payload, headers=None):
    response = client.post("/api/god_workflow/documents?source=canvas", json=payload, headers=headers or _headers())
    assert response.status_code == 201, response.text
    return response.json()["workflow_id"]


def _widget_workflow():
    return {"nodes": [{"id": "n1", "kind": "Load", "title": "Load",
                       "widgets": [{"name": "seed", "value": 1, "value_type": "int"},
                                   {"name": "steps", "value": 20, "value_type": "int"}]}],
            "connections": []}


def test_settings_round_trip_keeps_local_paths_and_reads_credentials_from_env(client, tmp_path, monkeypatch):
    # 未保存本地凭据时兼容环境变量，不回显原文。
    monkeypatch.setenv("GW_RUNNINGHUB_API_KEY", "rh-super-secret-value")
    monkeypatch.setenv("GW_RUNNINGHUB_ACCESS_TOKEN", "web-token-super-secret")
    payload = {"comfy_url": "http://127.0.0.1:8188",
               "comfy_root_dir": r"E:\ComfyUI-Easy\ComfyUI-Easy-Install",
               "local_models_dir": r"E:\ComfyUI-Easy\ComfyUI-Easy-Install\models"}
    written = client.post("/api/god_workflow/settings", json=payload, headers={**_headers(), "X-User-Role": "admin"})
    assert written.status_code == 200, written.text
    body = written.json()
    for key in ("comfy_url", "comfy_root_dir", "local_models_dir", "has_rh_api_key",
                "has_rh_access_token", "rh_api_key_masked", "rh_access_token_masked",
                "rh_api_key_env", "rh_access_token_env", "credential_source", "provider_status"):
        assert key in body, key
    assert body["comfy_url"] == "http://127.0.0.1:8188"
    assert body["has_rh_api_key"] is True and body["has_rh_access_token"] is True
    assert body["credential_source"] == "environment"
    assert body["rh_api_key_env"] == "GW_RUNNINGHUB_API_KEY"
    assert "rh-super-secret-value" not in written.text
    assert "web-token-super-secret" not in written.text
    # 本地路径由操作者填写，原样保存并原样回显，不做折叠或截断。
    assert body["comfy_root_dir"] == payload["comfy_root_dir"]
    assert body["local_models_dir"] == payload["local_models_dir"]

    fetched = client.get("/api/god_workflow/settings", headers=_headers())
    assert fetched.status_code == 200
    assert fetched.json() == body
    stored = list((tmp_path / "data" / "workflow").glob("*/settings.json"))
    assert stored, "设置必须落在 runtime data_root/workflow 下"
    assert "rh-super-secret-value" not in stored[0].read_text(encoding="utf-8")


def test_settings_credentials_can_be_configured_without_environment(client, monkeypatch):
    # 用户填写的凭据必须保存并生效，接口只返回掩码。
    monkeypatch.delenv("GW_RUNNINGHUB_API_KEY", raising=False)
    monkeypatch.delenv("GW_RUNNINGHUB_ACCESS_TOKEN", raising=False)
    body = client.post("/api/god_workflow/settings",
                       json={"rh_api_key": "only-in-body", "rh_access_token": "only-in-body-token"},
                       headers=_headers()).json()
    assert body["has_rh_api_key"] is True
    assert body["has_rh_access_token"] is True
    assert body["rh_api_key_source"] == body["rh_access_token_source"] == "local"
    assert "only-in-body" not in json.dumps(body)


def test_settings_are_principal_isolated(client, monkeypatch):
    # 路径设置按主体隔离；此例没有本地凭据。
    client.post("/api/god_workflow/settings", json={"comfy_url": "http://127.0.0.1:8188"}, headers={**_headers("one"), "X-User-Role": "admin"})
    mine = client.get("/api/god_workflow/settings", headers=_headers("one")).json()
    other = client.get("/api/god_workflow/settings", headers=_headers("two")).json()
    assert mine["comfy_url"] == "http://127.0.0.1:8188"
    assert other["comfy_url"] == ""
    assert mine["has_rh_api_key"] == other["has_rh_api_key"]


def test_settings_rejects_anonymous_write(client):
    response = client.post("/api/god_workflow/settings", json={"comfy_url": "x"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_folder_put_shares_patch_semantics_with_revision_cas(client):
    folder = client.post("/api/god_workflow/folders", json={"name": "old"}, headers=_headers()).json()["folder"]
    updated = client.put(f"/api/god_workflow/folders/{folder['folder_id']}",
                         json={"name": "new", "expected_revision": 1}, headers=_headers())
    assert updated.status_code == 200 and updated.json()["folder"]["revision"] == 2
    assert updated.json()["folder"]["name"] == "new"
    conflict = client.put(f"/api/god_workflow/folders/{folder['folder_id']}",
                          json={"name": "bad", "expected_revision": 1}, headers=_headers())
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "VERSION_CONFLICT"


def test_auto_layout_layers_a_dag_and_writes_back_positions(client):
    payload = {"nodes": [{"id": "a", "kind": "Load", "position": {"x": 0, "y": 0}},
                         {"id": "b", "kind": "Step", "position": {"x": 0, "y": 0}},
                         {"id": "c", "kind": "Save", "position": {"x": 0, "y": 0}},
                         {"id": "note", "kind": "Note", "position": {"x": 9, "y": 9}}],
               "connections": [{"id": "l1", "source": "a", "target": "b"},
                               {"id": "l2", "source": "b", "target": "c"}]}
    workflow_id = _save_workflow(client, payload)
    laid_out = client.post(f"/api/god_workflow/auto-layout/{workflow_id}", json={"expected_version": 1}, headers=_headers())
    assert laid_out.status_code == 200, laid_out.text
    body = laid_out.json()
    assert body["version"] == 2
    positions = {node["id"]: node["position"] for node in body["workflow"]["nodes"]}
    assert positions["a"]["x"] < positions["b"]["x"] < positions["c"]["x"]
    assert positions["a"]["y"] == positions["b"]["y"] == positions["c"]["y"]
    # 无连线 Note 独立成区，且位于分层图右侧。
    assert positions["note"]["x"] > positions["c"]["x"]
    # 位置确实被持久化，而不是仅回显入参。
    reread = client.get(f"/api/god_workflow/item/{workflow_id}", headers=_headers()).json()
    assert {node["id"]: node["position"] for node in reread["nodes"]} == positions


def test_auto_layout_rejects_stale_expected_version(client):
    workflow_id = _save_workflow(client, {"nodes": [{"id": "a", "kind": "Load"}], "connections": []})
    conflict = client.post(f"/api/god_workflow/auto-layout/{workflow_id}",
                           json={"expected_version": 7}, headers=_headers())
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "VERSION_CONFLICT"


def test_auto_layout_requires_write_role(client):
    workflow_id = _save_workflow(client, {"nodes": [{"id": "a", "kind": "Load"}], "connections": []})
    readonly = {"Authorization": "Bearer test-token", "X-User-Role": "readonly", "Origin": "http://testserver"}
    response = client.post(f"/api/god_workflow/auto-layout/{workflow_id}", json={}, headers=readonly)
    assert response.status_code == 403


def test_params_persist_promoted_flag_and_item_reads_it_back(client):
    workflow_id = _save_workflow(client, _widget_workflow())
    updated = client.put(f"/api/god_workflow/item/{workflow_id}/params",
                         json={"expected_version": 1, "widget_updates": [{"node_id": "n1", "field_name": "seed", "value": 42, "promoted": True}]},
                         headers=_headers())
    assert updated.status_code == 200, updated.text
    widgets = {widget["name"]: widget for widget in updated.json()["nodes"][0]["widgets"]}
    assert widgets["seed"]["promoted"] is True and widgets["seed"]["value"] == 42
    assert widgets["steps"].get("promoted") in (None, False)

    reread = client.get(f"/api/god_workflow/item/{workflow_id}", headers=_headers()).json()
    widgets = {widget["name"]: widget for widget in reread["nodes"][0]["widgets"]}
    assert widgets["seed"]["promoted"] is True
    assert reread["revision"] == reread["version"]


def test_params_round_trip_survives_a_versioned_document_read(client):
    """提升标记与文档版本一并持久化，重新读取后仍可还原提取列表。"""
    workflow_id = _save_workflow(client, _widget_workflow())
    promoted = client.put(f"/api/god_workflow/item/{workflow_id}/params",
                          json={"expected_version": 1, "widget_updates": [{"node_id": "n1", "field_name": "seed", "value": 3, "promoted": True}]},
                          headers=_headers())
    assert promoted.status_code == 200 and promoted.json()["version"] == 2
    document = client.get(f"/api/god_workflow/documents/{workflow_id}", headers=_headers()).json()
    assert document["workflow"]["version"] == 2
    widgets = {widget["name"]: widget for widget in document["workflow"]["nodes"][0]["widgets"]}
    assert widgets["seed"]["promoted"] is True and widgets["seed"]["value"] == 3
    # 非提升字段不应被连带标记。
    assert widgets["steps"].get("promoted") in (None, False)


def test_export_formats_branch_and_canvas_contract_hides_topology(client):
    payload = {"name": "exportable",
               "nodes": [{"id": "n1", "kind": "Load", "title": "Load",
                          "inputs": {"seed": 1},
                          "widgets": [{"name": "seed", "value": 1, "promoted": True},
                                      {"name": "hidden", "value": "x"}]},
                         {"id": "n2", "kind": "Save", "inputs": {"image": ["n1", 0]}}],
               "connections": [{"id": "l1", "source": "n1", "target": "n2"}]}
    workflow_id = _save_workflow(client, payload)

    api_map = client.get(f"/api/god_workflow/export/{workflow_id}?format=api", headers=_headers())
    assert api_map.status_code == 200
    body = api_map.json()
    assert body["n1"]["class_type"] == "Load" and body["n2"]["inputs"]["image"] == ["n1", 0]
    assert body["n1"]["inputs"]["seed"] == 1

    node_list = client.get(f"/api/god_workflow/export/{workflow_id}?format=node_info_list", headers=_headers())
    assert node_list.status_code == 200
    assert node_list.json() == [{"nodeId": "n1", "fieldName": "seed", "fieldValue": 1}]

    contract = client.get(f"/api/god_workflow/export/{workflow_id}?format=canvas_contract", headers=_headers())
    assert contract.status_code == 200
    contract_body = contract.json()
    assert "nodes" not in contract_body and "connections" not in contract_body
    assert contract_body["node_info_list"] == [{"nodeId": "n1", "fieldName": "seed", "fieldValue": 1}]
    assert [item["field_name"] for item in contract_body["actuator_params"]] == ["seed"]
    assert "hidden" not in contract.text

    full = client.get(f"/api/god_workflow/export/{workflow_id}", headers=_headers())
    assert full.status_code == 200 and "connections" in full.json()

    bad = client.get(f"/api/god_workflow/export/{workflow_id}?format=nope", headers=_headers())
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "INVALID_EXPORT_FORMAT"


def test_compare_local_without_configuration_is_truthful_and_structured(client, monkeypatch):
    monkeypatch.delenv("GW_COMFYUI_URL", raising=False)
    payload = {"nodes": [{"id": "n1", "kind": "SomeCustomNode",
                          "inputs": {"ckpt": "missing_model.safetensors"}}]}
    workflow_id = _save_workflow(client, payload)
    response = client.post(f"/api/god_workflow/compare-local/{workflow_id}", headers=_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    for key in ("missing_nodes", "missing_models", "local_online", "recommended_target", "status"):
        assert key in body, key
    assert body["local_online"] is False
    assert body["recommended_target"] == "rh_cloud"
    assert body["status"] == "unconfigured"
    assert "SomeCustomNode" in body["missing_nodes"]
    assert {"model_type": "ckpt", "filename": "missing_model.safetensors"} in body["missing_models"]


def test_compare_local_uses_real_object_info_probe(monkeypatch):
    """配置 GW_COMFYUI_URL 时用真实 /object_info 响应做对比（经注入传输层）。"""
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_COMFYUI_URL", "http://comfy.local:8188")
    import gw.god_workflow.routes as routes_module

    captured: dict[str, str] = {}

    class _Transport(httpx.BaseTransport):
        def handle_request(self, request):
            captured["url"] = str(request.url)
            payload = {"Downloader": {"input": {"required": {"ckpt_name": [["model.safetensors"]]}}}}
            return httpx.Response(200, json=payload, headers={"content-type": "application/json"})

    client = _instrumented_client(monkeypatch, _Transport())
    payload = {"nodes": [{"id": "n1", "kind": "Downloader"},
                         {"id": "n2", "kind": "NotInstalled"}],
               "connections": []}
    workflow_id = _save_workflow(client, payload)
    response = client.post(f"/api/god_workflow/compare-local/{workflow_id}", headers=_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert captured["url"] == "http://comfy.local:8188/object_info"
    assert body["local_online"] is True
    assert body["missing_nodes"] == ["NotInstalled"]
    assert body["recommended_target"] == "rh_cloud"
    assert body["status"] == "compared"
    assert body["installed_nodes_count"] == 1 and body["required_nodes_count"] == 2


def test_compare_local_marks_present_models(monkeypatch):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_COMFYUI_URL", "http://comfy.local:8188")

    class _Transport(httpx.BaseTransport):
        def handle_request(self, request):
            payload = {"Loader": {"input": {"required": {"ckpt_name": [["model.safetensors"]]}}},
                       "Downloader": {"input": {"required": {"ckpt_name": [["other.safetensors"]]}}}}
            return httpx.Response(200, json=payload, headers={"content-type": "application/json"})

    client = _instrumented_client(monkeypatch, _Transport())
    payload = {"nodes": [{"id": "n1", "kind": "Loader",
                          "inputs": {"ckpt_name": "nested/dir/model.safetensors"}}],
               "connections": []}
    workflow_id = _save_workflow(client, payload)
    body = client.post(f"/api/god_workflow/compare-local/{workflow_id}", headers=_headers()).json()
    assert body["missing_models"] == []
    assert body["installed_models_count"] == 1 and body["required_models_count"] == 1
    assert body["recommended_target"] == "local_comfy"
    assert body["status"] == "compared"


def test_compare_local_reports_unreachable_probe_without_fabricating(client, monkeypatch):
    monkeypatch.setenv("GW_COMFYUI_URL", "http://comfy.local:8188")

    def _boom(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "Client", _boom)
    workflow_id = _save_workflow(client, {"nodes": [{"id": "n1", "kind": "Downloader"}], "connections": []})
    response = client.post(f"/api/god_workflow/compare-local/{workflow_id}", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["local_online"] is False
    assert body["status"] == "unreachable"
    assert body["recommended_target"] == "rh_cloud"


def test_asset_upload_and_list_are_isolated_and_probe_dimensions(client):
    png = _tiny_png()
    uploaded = client.post("/api/god_workflow/assets/upload",
                           files={"file": ("poster.png", png, "image/png")}, headers=_headers())
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()
    assert set(("asset_id", "filename", "size_bytes", "media_type", "width", "height")).issubset(asset)
    assert asset["filename"] == "poster.png"
    assert asset["size_bytes"] == len(png)
    assert asset["media_type"] == "image"
    assert (asset["width"], asset["height"]) == (3, 2)

    listing = client.get("/api/god_workflow/assets/list", headers=_headers())
    assert listing.status_code == 200
    assert [item["asset_id"] for item in listing.json()["items"]] == [asset["asset_id"]]
    assert listing.json()["items"][0]["size_bytes"] == len(png)

    other = client.get("/api/god_workflow/assets/list", headers=_headers("two"))
    assert other.json()["items"] == []
    forbidden = client.get(f"/api/god_workflow/assets/{asset['asset_id']}/content", headers=_headers("two"))
    assert forbidden.status_code == 404


def test_asset_upload_rejects_non_media_and_oversized_payloads(client, monkeypatch):
    text = client.post("/api/god_workflow/assets/upload",
                       files={"file": ("notes.txt", b"hello", "text/plain")}, headers=_headers())
    assert text.status_code == 400 and text.json()["detail"]["code"] == "UNSUPPORTED_ASSET_MEDIA_TYPE"

    import gw.god_workflow.registry as registry_module
    monkeypatch.setattr(registry_module, "MAX_ASSET_BYTES", 8)
    oversized = client.post("/api/god_workflow/assets/upload",
                            files={"file": ("big.png", _tiny_png(), "image/png")}, headers=_headers())
    assert oversized.status_code == 413 and oversized.json()["detail"]["code"] == "ASSET_TOO_LARGE"


def test_clipboard_returns_real_recorded_history(client):
    empty = client.get("/api/god_workflow/clipboard", headers=_headers())
    assert empty.status_code == 200
    empty_body = empty.json()
    assert set(("latest", "items", "data_status", "data_gaps")).issubset(empty_body)
    assert empty_body["latest"] is None and empty_body["items"] == []

    recorded = client.get("/api/god_workflow/clipboard?text=https://www.runninghub.cn/workflow/2104915887789797378",
                          headers=_headers())
    assert recorded.status_code == 200
    body = recorded.json()
    assert body["latest"]["text"].endswith("2104915887789797378")
    assert body["latest"]["is_rh_link"] is True

    reread = client.get("/api/god_workflow/clipboard", headers=_headers()).json()
    assert [item["text"] for item in reread["items"]] == [body["latest"]["text"]]
    assert client.get("/api/god_workflow/clipboard", headers=_headers("two")).json()["items"] == []


def test_asset_upload_requires_authentication(client):
    response = client.post("/api/god_workflow/assets/upload", files={"file": ("a.png", _tiny_png(), "image/png")})
    assert response.status_code == 401


def _sample_workflow():
    return {"nodes": [{"id": "1", "kind": "KSampler", "title": "Sampler", "inputs": {"seed": 123}}], "connections": []}


def test_execute_rh_cloud_without_api_key_returns_503(client, monkeypatch):
    monkeypatch.delenv("GW_RUNNINGHUB_API_KEY", raising=False)
    created = client.post("/api/god_workflow/documents?source=canvas", json=_sample_workflow(), headers=_headers()).json()
    wf_id = created["workflow_id"]
    res = client.post("/api/god_workflow/tasks/execute", json={"workflow_id": wf_id, "target": "rh_cloud"}, headers=_headers())
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "WORKFLOW_EXECUTOR_UNAVAILABLE"


def test_execute_rh_cloud_success_and_poll_status(client, monkeypatch):
    monkeypatch.setenv("GW_RUNNINGHUB_API_KEY", "test-key-123")
    created = client.post("/api/god_workflow/documents?source=canvas", json=_sample_workflow(), headers=_headers()).json()
    wf_id = created["workflow_id"]

    import gw.god_workflow.platforms as platforms_mod

    async def _mock_create_task(workflow_id, node_info_list, api_key, domain="www.runninghub.cn"):
        assert workflow_id == "1234567890123456789"
        return {"taskId": "rh-mock-task-888"}

    poll_state = {"called": 0}

    async def _mock_query_task(task_id, api_key, domain="www.runninghub.cn"):
        poll_state["called"] += 1
        if poll_state["called"] == 1:
            return {"status": "RUNNING", "outputs": []}
        return {"status": "SUCCESS", "outputs": [{"fileUrl": "https://mock.runninghub/out.png", "fileName": "out.png"}]}

    monkeypatch.setattr(platforms_mod, "create_rh_cloud_task", _mock_create_task)
    monkeypatch.setattr(platforms_mod, "query_rh_task_outputs", _mock_query_task)

    # 提交任务
    executed = client.post("/api/god_workflow/tasks/execute", json={"workflow_id": wf_id, "target": "rh_cloud",
        "rh_workflow_id": "1234567890123456789"}, headers=_headers())
    assert executed.status_code == 202
    task_body = executed.json()
    assert task_body["target"] == "rh_cloud"
    assert task_body["prompt_id"] == "rh-mock-task-888"
    task_id = task_body["task_id"]

    # 轮询 1：RUNNING
    status1 = client.get(f"/api/god_workflow/tasks/{task_id}", headers=_headers())
    assert status1.status_code == 200
    assert status1.json()["status"] == "running"

    # mock 下载：仅合成获准主机，不访问网络。
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", "mock.runninghub")
    class _MockHttpxResponse:
        status_code = 200
        content = _tiny_png()
        headers = {"content-type": "image/png"}
        async def aiter_bytes(self):
            yield self.content

    class _MockStream:
        async def __aenter__(self): return _MockHttpxResponse()
        async def __aexit__(self, *args): pass

    class _MockHttpxClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, *args, **kwargs): return _MockHttpxResponse()
        def stream(self, *args, **kwargs): return _MockStream()

    monkeypatch.setattr(httpx, "AsyncClient", _MockHttpxClient)

    # 轮询 2：SUCCESS 并登记素材
    status2 = client.get(f"/api/god_workflow/tasks/{task_id}", headers=_headers())
    assert status2.status_code == 200
    res_body = status2.json()
    assert res_body["status"] == "completed"
    assert len(res_body["outputs"]) == 1
    assert res_body["outputs"][0]["registered"] is True
    assert "asset_id" in res_body["outputs"][0]


def test_open_in_comfy_and_bridge_poll_and_sync(client, monkeypatch):
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    monkeypatch.setattr("gw.god_workflow.routes._comfy_operation", lambda *a: {"highlight": True})
    created = client.post("/api/god_workflow/documents?source=canvas", json=_sample_workflow(), headers=_headers()).json()
    wf_id = created["workflow_id"]

    # 1. POST open-in-comfy
    opened = client.post(f"/api/god_workflow/open-in-comfy/{wf_id}", headers=_headers())
    assert opened.status_code == 200
    body = opened.json()
    assert body["ok"] is True
    assert body["workflow_id"] == wf_id
    assert "open_url" in body

    # 2. GET comfy-bridge/poll
    poll1 = client.get("/api/god_workflow/comfy-bridge/poll", headers=_headers())
    assert poll1.status_code == 200
    pbody1 = poll1.json()
    assert pbody1["command"] == "open_workflow"
    assert pbody1["workflow_id"] == wf_id

    # 再次 poll 变为 idle
    poll2 = client.get("/api/god_workflow/comfy-bridge/poll", headers=_headers())
    assert poll2.status_code == 200
    assert poll2.json()["command"] == "idle"

    # 3. POST comfy-bridge/sync-saved 回传更新画布
    new_wf = _sample_workflow()
    new_wf["nodes"][0]["title"] = "Updated by ComfyUI"
    synced = client.post("/api/god_workflow/comfy-bridge/sync-saved",
                         json={"workflow_id": wf_id, "workflow": new_wf, "expected_version": 1},
                         headers=_headers())
    assert synced.status_code == 200
    assert synced.json()["synced"] is True
    assert synced.json()["version"] == 2

    # 回读确认已更新
    doc = client.get(f"/api/god_workflow/documents/{wf_id}", headers=_headers()).json()
    assert doc["workflow"]["version"] == 2
    assert doc["workflow"]["nodes"][0]["title"] == "Updated by ComfyUI"

    # 4. GET sync-status
    st = client.get("/api/god_workflow/comfy-bridge/sync-status", headers=_headers())
    assert st.status_code == 200
    assert st.json()["data_status"] == "ok"
    assert st.json()["revisions"][wf_id] == 2


def test_workflow_and_output_registered_to_asset_hub(client):
    """验证工作流落盘与产物生成时自动登记至统一资产中枢，绑定稳定 asset_id。"""
    # 1. 保存工作流
    doc_resp = client.post(
        "/api/god_workflow/documents?name=统一资产测试流&source=canvas",
        json={"nodes": [{"id": 1, "kind": "KSampler"}]},
        headers=_headers(),
    )
    assert doc_resp.status_code == 201
    wf_id = doc_resp.json()["workflow_id"]

    # 2. 从资产库读取快照，确认 workflow 分类下已登记该工作流
    lib_resp = client.get("/api/asset-library", headers=_headers())
    assert lib_resp.status_code == 200
    libraries = lib_resp.json()["library"]["libraries"]
    assert len(libraries) >= 1

    workflow_items = []
    for lib in libraries:
        for cat in lib["categories"]:
            if cat["type"] == "workflow":
                workflow_items.extend(cat["items"])

    matched_wf = [item for item in workflow_items if item["asset_id"] == doc_resp.json()["workflow"]["asset_id"]]
    assert len(matched_wf) == 1
    assert matched_wf[0]["name"] == "统一资产测试流"
    assert f"id={wf_id}" in matched_wf[0]["url"]

    # 3. 模拟输出产物上传/登记
    asset_resp = client.post(
        "/api/god_workflow/assets/upload",
        files={"file": ("result_render.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82", "image/png")},
        headers=_headers(),
    )
    assert asset_resp.status_code == 201
    output_asset_id = asset_resp.json()["asset_id"]

    from gw.god_workflow import asset_hub_bridge
    asset_hub_bridge.register_output_to_asset_hub(asset_resp.json())

    # 4. 回读资产库快照，确认 image 分类下已登记该产物
    lib_resp2 = client.get("/api/asset-library", headers=_headers())
    image_items = []
    for lib in lib_resp2.json()["library"]["libraries"]:
        for cat in lib["categories"]:
            if cat["type"] == "image":
                image_items.extend(cat["items"])

    matched_img = [item for item in image_items if item["asset_id"] == output_asset_id]
    assert len(matched_img) == 1
    assert matched_img[0]["name"] == "result_render.png"



# R0：离线 Provider 契约；未配置真实服务、禁止收费调用。
def test_r0_missing_remote_id_is_unknown_not_fabricated(client, monkeypatch):
    monkeypatch.setenv('GW_RUNNINGHUB_API_KEY', 'fake-key')
    async def submit(*args, **kwargs):
        return {}
    monkeypatch.setattr(platforms, 'create_rh_cloud_task', submit)
    result = client.post('/api/god_workflow/tasks/execute', headers=_headers(),
                         json={'prompt': _comfy_payload(), 'target': 'rh_cloud', 'rh_workflow_id': '123'})
    assert result.status_code == 202
    payload = result.json()
    assert payload['status'] == 'outcome_unknown'
    assert payload['remote_task_id'] is None
    assert payload['job_id'] == payload['task_id']


def test_r0_idempotent_replay_and_conflict(client, monkeypatch):
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    calls = []
    async def submit(prompt, **kwargs):
        calls.append(prompt)
        return {'prompt_id': 'real-provider-id'}
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    headers = {**_headers(), 'Idempotency-Key': 'r0-key'}
    body = {'prompt': _comfy_payload(), 'target': 'local_comfy'}
    first = client.post('/api/god_workflow/tasks/execute', headers=headers, json=body)
    second = client.post('/api/god_workflow/tasks/execute', headers=headers, json=body)
    assert first.json()['job_id'] == second.json()['job_id']
    assert len(calls) == 1
    body['client_id'] = 'different'
    assert client.post('/api/god_workflow/tasks/execute', headers=headers, json=body).status_code == 409


def test_r0_cloud_cancel_never_calls_local_interrupt(client, monkeypatch):
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv('GW_RUNNINGHUB_API_KEY', 'fake-key')
    async def submit(*args, **kwargs):
        return {'taskId': 'provider-id'}
    async def interrupt():
        pytest.fail('Cloud cancellation must never interrupt local ComfyUI')
    monkeypatch.setattr(platforms, 'create_rh_cloud_task', submit)
    monkeypatch.setattr(comfy_executor, 'interrupt', interrupt)
    task = client.post('/api/god_workflow/tasks/execute', headers=_headers(),
                       json={'prompt': _comfy_payload(), 'target': 'rh_cloud', 'rh_workflow_id': '123'}).json()
    result = client.post(f"/api/god_workflow/tasks/{task['task_id']}/cancel", headers=_headers()).json()
    assert result['cancel_supported'] is False
    assert result['remote_cancelled'] is False
    assert result['remote_may_continue_or_bill'] is True
    assert result['status'] != 'cancelled'


def test_r0_cross_subject_task_is_hidden(client, monkeypatch):
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    async def submit(*args, **kwargs):
        return {'prompt_id': 'provider-id'}
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    task = client.post('/api/god_workflow/tasks/execute', headers=_headers('one'),
                       json={'prompt': _comfy_payload()}).json()
    assert client.get(f"/api/god_workflow/tasks/{task['job_id']}", headers=_headers('two')).status_code == 404
    assert client.post(f"/api/god_workflow/tasks/{task['job_id']}/cancel", headers=_headers('two')).status_code == 404


def test_r0_ledger_exists_before_send_and_timeout_never_resubmits(client, monkeypatch, tmp_path):
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    calls = []
    async def submit(*args, **kwargs):
        records = list((tmp_path / 'data' / 'workflow').glob('*/tasks/*.json'))
        assert len(records) == 1
        assert json.loads(records[0].read_text())['status'] == 'outcome_unknown'
        calls.append(1)
        raise CleanroomException(504, 'WORKFLOW_PROVIDER_TIMEOUT', '超时')
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    headers = {**_headers(), 'Idempotency-Key': 'timeout-window'}
    body = {'prompt': _comfy_payload()}
    first = client.post('/api/god_workflow/tasks/execute', headers=headers, json=body)
    assert first.status_code == 202
    assert first.json()['status'] == 'outcome_unknown'
    second = client.post('/api/god_workflow/tasks/execute', headers=headers, json=body)
    assert first.json()['job_id'] == second.json()['job_id']
    assert len(calls) == 1


def test_r0_local_parameter_overrides_are_sent(client, monkeypatch):
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    async def submit(prompt, **kwargs):
        assert prompt['1']['inputs']['seed'] == 456
        return {'prompt_id': 'remote'}
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    body = {'prompt': {'1': {'class_type': 'Sampler', 'inputs': {'seed': 123}}},
            'node_info_list': [{'nodeId': '1', 'fieldName': 'seed', 'fieldValue': 456}]}
    assert client.post('/api/god_workflow/tasks/execute', headers=_headers(), json=body).status_code == 202


def test_r0_store_output_repeatedly_preserves_asset_id(client):
    from gw.god_workflow import registry
    from gw.god_workflow.routes import _auth
    context = _auth(_headers()['Authorization'], 'editor', True)
    one = registry.store_bytes(context, _tiny_png(), 'out.png', 'image/png', task_id='test-task')
    two = registry.store_bytes(context, _tiny_png(), 'out.png', 'image/png', task_id='test-task')
    assert one['asset_id'] == two['asset_id']
    assert len(registry.load_assets(context)) == 1


def test_r0_concurrent_submission_has_one_remote_effect(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import asyncio
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    calls = []
    async def submit(*args, **kwargs):
        calls.append(1)
        await asyncio.sleep(.1)
        return {'prompt_id': 'remote'}
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    def request(_):
        return client.post('/api/god_workflow/tasks/execute', headers={**_headers(), 'Idempotency-Key': 'concurrent'},
                           json={'prompt': _comfy_payload()}).json()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(request, range(4)))
    assert len({item['job_id'] for item in results}) == 1
    assert len(calls) == 1


def test_r0_common_job_is_persistent_and_principal_scoped(client, monkeypatch):
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    async def submit(*args, **kwargs): return {'prompt_id': 'remote'}
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    task = client.post('/api/god_workflow/tasks/execute', headers=_headers(), json={'prompt': _comfy_payload()}).json()
    result = client.get(f"/api/jobs/{task['job_id']}", headers=_headers())
    assert result.status_code == 200
    assert result.json()['state'] == 'accepted'
    assert result.json()['task_id'] == task['task_id']
    assert client.get(f"/api/jobs/{task['job_id']}", headers=_headers('other')).status_code == 404
    assert client.get(f"/api/jobs/{task['job_id']}").status_code == 401


def test_r0_collection_retry_does_not_regenerate(client, monkeypatch):
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    submitted, fetched = [], []
    async def submit(*args, **kwargs):
        submitted.append(1)
        return {'prompt_id': 'remote'}
    async def history(*args):
        return {'found': True, 'entry': {'outputs': {'1': {'images': [{'filename': 'out.png'}]}}, 'status': {'status_str': 'success'}}}
    async def fetch(*args, **kwargs):
        fetched.append(1)
        if len(fetched) == 1:
            raise CleanroomException(502, 'DOWNLOAD_FAILED', '下载失败')
        return _tiny_png(), 'image/png'
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    monkeypatch.setattr(comfy_executor, 'history', history)
    monkeypatch.setattr(comfy_executor, 'fetch_output', fetch)
    task = client.post('/api/god_workflow/tasks/execute', headers=_headers(), json={'prompt': _comfy_payload()}).json()
    path = f"/api/god_workflow/tasks/{task['job_id']}"
    first = client.get(path, headers=_headers()).json()
    assert first['execution_status'] == 'completed'
    assert first['collection_status'] == 'partial_failed'
    retry = client.post(path + '/collect', headers=_headers())
    assert retry.status_code == 200
    assert retry.json()['status'] == 'completed'
    assert len(submitted) == 1
    asset_id = retry.json()['outputs'][0]['asset_id']
    assert client.post(path + '/collect', headers=_headers()).json()['outputs'][0]['asset_id'] == asset_id


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fc00::1', '::ffff:127.0.0.1'])
def test_r0_output_dns_private_addresses_are_rejected(client, monkeypatch, address):
    import asyncio
    import socket
    from gw.god_workflow.routes import _PublicOutputBackend
    calls = []
    class Backend:
        async def connect_tcp(self, **kwargs): calls.append(kwargs)
    async def resolve(*args, **kwargs):
        return [(socket.AF_INET6 if ':' in address else socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]
    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolve)
        with pytest.raises(CleanroomException) as error:
            await _PublicOutputBackend(Backend()).connect_tcp('approved.example', 443)
        assert error.value.code == 'OUTPUT_ADDRESS_NOT_ALLOWED'
    asyncio.run(run())
    assert not calls


def test_r0_output_dns_connects_to_validated_ip_not_second_resolution(client, monkeypatch):
    import asyncio
    import socket
    from gw.god_workflow.routes import _PublicOutputBackend
    calls = []
    class Backend:
        async def connect_tcp(self, **kwargs): calls.append(kwargs); return object()
    async def resolve(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolve)
        await _PublicOutputBackend(Backend()).connect_tcp('approved.example', 443)
    asyncio.run(run())
    assert calls[0]['host'] == '8.8.8.8'


def test_param_promotion_and_actuator_params_roundtrip(client):
    """验证工作流节点参数提升与取消提升时，actuator_params 能在保存、读取中正确往返。"""
    # 1. 创建工作流
    doc_resp = client.post(
        "/api/god_workflow/documents?name=提升测试流&source=canvas",
        json={
            "nodes": [
                {
                    "id": "4",
                    "kind": "LoadImage",
                    "title": "LoadImage",
                    "inputs": {"image": "input_reference.png"},
                    "widgets": [{"name": "image", "value": "input_reference.png", "promoted": False}],
                }
            ]
        },
        headers=_headers(),
    )
    assert doc_resp.status_code == 201
    wf_id = doc_resp.json()["workflow_id"]

    # 2. 提升节点 #4 的 image 参数
    promote_resp = client.put(
        f"/api/god_workflow/item/{wf_id}/params",
        json={
            "expected_version": 1,
            "widget_updates": [
                {
                    "node_id": "4",
                    "field_name": "image",
                    "value": "custom_image.png",
                    "promoted": True,
                }
            ]
        },
        headers=_headers(),
    )
    assert promote_resp.status_code == 200
    updated = promote_resp.json()
    assert "actuator_params" in updated
    assert len(updated["actuator_params"]) == 1
    param = updated["actuator_params"][0]
    assert param["node_id"] == "4"
    assert param["field_name"] == "image"
    assert param["field_value"] == "custom_image.png"

    # 3. 回读工作流详情验证持久化
    get_resp = client.get(f"/api/god_workflow/documents/{wf_id}", headers=_headers())
    assert get_resp.status_code == 200
    wf_data = get_resp.json()["workflow"]
    assert "actuator_params" in wf_data
    assert len(wf_data["actuator_params"]) == 1
    assert wf_data["actuator_params"][0]["node_id"] == "4"
    assert wf_data["actuator_params"][0]["field_name"] == "image"

    # 4. 取消提升
    unpromote_resp = client.put(
        f"/api/god_workflow/item/{wf_id}/params",
        json={
            "expected_version": 2,
            "widget_updates": [
                {
                    "node_id": "4",
                    "field_name": "image",
                    "value": "custom_image.png",
                    "promoted": False,
                }
            ]
        },
        headers=_headers(),
    )
    assert unpromote_resp.status_code == 200
    unpromoted_wf = unpromote_resp.json()
    assert len(unpromoted_wf.get("actuator_params", [])) == 0



def test_r0_whitespace_key_replays(client, monkeypatch):
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    calls = []
    async def submit(*args, **kwargs): calls.append(1); return {'prompt_id': 'remote'}
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    for key in [' key ', 'key']:
        client.post('/api/god_workflow/tasks/execute', headers={**_headers(), 'Idempotency-Key': key}, json={'prompt': _comfy_payload()})
    assert len(calls) == 1


def test_r0_local_cancel_does_not_interrupt_other_tasks(client, monkeypatch):
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    async def submit(*args, **kwargs): return {'prompt_id': 'remote'}
    async def interrupt(): pytest.fail('全局interrupt不可作为任务级取消')
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    monkeypatch.setattr(comfy_executor, 'interrupt', interrupt)
    task = client.post('/api/god_workflow/tasks/execute', headers=_headers(), json={'prompt': _comfy_payload()}).json()
    result = client.post(f"/api/god_workflow/tasks/{task['job_id']}/cancel", headers=_headers()).json()
    assert result['cancel_supported'] is False
    assert result['status'] == 'accepted'


def test_r0_remote_binding_write_failure_returns_reserved_job(client, monkeypatch):
    from gw.god_workflow import comfy_executor, registry
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    calls = []
    async def submit(*args, **kwargs): calls.append(1); return {'prompt_id': 'accepted-remotely'}
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    write = registry._atomic_write
    def fail_binding(path, payload):
        if isinstance(payload, dict) and payload.get('remote_task_id') == 'accepted-remotely':
            raise OSError('模拟绑定故障')
        return write(path, payload)
    monkeypatch.setattr(registry, '_atomic_write', fail_binding)
    headers = {**_headers(), 'Idempotency-Key': 'bind-window'}
    first = client.post('/api/god_workflow/tasks/execute', headers=headers, json={'prompt': _comfy_payload()})
    assert first.status_code == 202
    assert first.json()['status'] == 'outcome_unknown'
    second = client.post('/api/god_workflow/tasks/execute', headers=headers, json={'prompt': _comfy_payload()})
    assert first.json()['job_id'] == second.json()['job_id']
    assert len(calls) == 1


def test_r0_task_list_is_subject_scoped_and_readonly_cannot_collect(client, monkeypatch):
    from gw.god_workflow import comfy_executor
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    async def submit(*args, **kwargs): return {'prompt_id': 'remote'}
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    task = client.post('/api/god_workflow/tasks/execute', headers=_headers('owner'), json={'prompt': _comfy_payload()}).json()
    assert client.get('/api/god_workflow/tasks', headers=_headers('owner')).json()['tasks'][0]['job_id'] == task['job_id']
    assert client.get('/api/god_workflow/tasks', headers=_headers('other')).json()['tasks'] == []
    assert client.get('/api/god_workflow/tasks').status_code == 401
    assert client.post(f"/api/god_workflow/tasks/{task['job_id']}/collect", headers={**_headers('owner'), 'X-User-Role': 'readonly'}).status_code == 403


def test_r0_output_manifest_failure_recovers_orphan_without_redownload(client, monkeypatch, tmp_path):
    from gw.god_workflow import registry
    from gw.god_workflow.routes import _auth
    context = _auth(_headers()['Authorization'], 'editor', True)
    write = registry._atomic_write
    def fail_manifest(path, payload):
        if path.name == 'assets.json':
            raise OSError('敏感磁盘路径')
        return write(path, payload)
    monkeypatch.setattr(registry, '_atomic_write', fail_manifest)
    with pytest.raises((OSError, CleanroomException)):
        registry.store_bytes(context, _tiny_png(), 'out.png', 'image/png', task_id='orphan-task')
    files = list((tmp_path / 'data' / 'workflow').glob('*/assets/*.png'))
    assert len(files) == 1
    monkeypatch.setattr(registry, '_atomic_write', write)
    assets = registry.load_assets(context)
    assert len(assets) == 1
    assert assets[0]['asset_id'] == files[0].stem
    again = registry.store_bytes(context, _tiny_png(), 'out.png', 'image/png', task_id='orphan-task')
    assert again['asset_id'] == files[0].stem
    assert len(list(files[0].parent.glob('*.png'))) == 1


@pytest.mark.parametrize('window', ['reserve', 'complete'])
def test_r0_task_persistence_faults_fail_closed_and_recover(client, monkeypatch, tmp_path, window):
    from gw.god_workflow import comfy_executor, registry
    monkeypatch.setenv('GW_COMFYUI_URL', 'http://127.0.0.1:8188')
    calls = []
    async def submit(*args, **kwargs): calls.append(1); return {'prompt_id': 'remote'}
    async def history(*args):
        return {'found': True, 'entry': {'outputs': {'1': {'images': [{'filename': 'out.png'}]}}, 'status': {'status_str': 'success'}}}
    async def fetch(*args, **kwargs): return _tiny_png(), 'image/png'
    monkeypatch.setattr(comfy_executor, 'submit', submit)
    monkeypatch.setattr(comfy_executor, 'history', history)
    monkeypatch.setattr(comfy_executor, 'fetch_output', fetch)
    write = registry._atomic_write
    def fail(path, payload):
        if path.parent.name == 'tasks' and isinstance(payload, dict) and payload.get('status') == ('outcome_unknown' if window == 'reserve' else 'completed'):
            raise OSError('敏感磁盘路径')
        return write(path, payload)
    monkeypatch.setattr(registry, '_atomic_write', fail)
    headers = {**_headers(), 'Idempotency-Key': 'fault-' + window}
    body = {'prompt': _comfy_payload()}
    first = client.post('/api/god_workflow/tasks/execute', headers=headers, json=body)
    if window == 'reserve':
        assert first.status_code == 503
        assert first.json()['detail']['code'] == 'TASK_LEDGER_WRITE_FAILED'
        assert not calls
    else:
        assert first.status_code == 202
        job_id = first.json()['job_id']
        poll = client.get('/api/god_workflow/tasks/' + job_id, headers=_headers())
        assert poll.status_code == 503
        assert poll.json()['detail']['code'] == 'TASK_LEDGER_WRITE_FAILED'
        assert '敏感' not in poll.text
        assert client.get('/api/god_workflow/tasks', headers=_headers()).json()['tasks'][0]['status'] == 'accepted'
    monkeypatch.setattr(registry, '_atomic_write', write)
    recovered = client.post('/api/god_workflow/tasks/execute', headers=headers, json=body).json()
    assert len(calls) == 1
    done = client.get('/api/god_workflow/tasks/' + recovered['job_id'], headers=_headers()).json()
    assert done['status'] == 'completed'
    assert len(list((tmp_path / 'data' / 'workflow').glob('*/assets/*.png'))) == 1


def test_r0_local_registration_disk_failure_is_item_error(client, monkeypatch):
    import asyncio
    from gw.god_workflow import comfy_executor, registry, routes
    async def fetch(*args, **kwargs): return _tiny_png(), 'image/png'
    def fail(*args, **kwargs): raise OSError('敏感路径')
    monkeypatch.setattr(comfy_executor, 'fetch_output', fetch)
    monkeypatch.setattr(registry, 'store_bytes', fail)
    result = asyncio.run(routes._collect_task_outputs(routes._auth(_headers()['Authorization'], 'editor', True), 'task', [{'filename': 'out.png'}]))
    assert result == [{'filename': 'out.png', 'registered': False, 'code': 'OUTPUT_REGISTRATION_FAILED'}]


def test_r0_store_bytes_enforces_size_limit(client, monkeypatch):
    from gw.god_workflow import registry, routes
    monkeypatch.setattr(registry, 'MAX_ASSET_BYTES', 3)
    with pytest.raises(CleanroomException) as error:
        registry.store_bytes(routes._auth(_headers()['Authorization'], 'editor', True), b'abcd', 'out.png', 'image/png')
    assert error.value.code == 'ASSET_TOO_LARGE'


@pytest.mark.parametrize('addresses', [['8.8.8.8', '127.0.0.1'], ['2606:4700:4700::1111', 'fc00::1'], []])
def test_r0_output_dns_mixed_or_empty_answers_fail_closed(client, monkeypatch, addresses):
    import asyncio
    import socket
    from gw.god_workflow.routes import _PublicOutputBackend
    class Backend:
        async def connect_tcp(self, **kwargs): pytest.fail('不得连接混合或空DNS结果')
    async def resolve(*args, **kwargs):
        return [(socket.AF_INET6 if ':' in address else socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443)) for address in addresses]
    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolve)
        with pytest.raises(CleanroomException) as error:
            await _PublicOutputBackend(Backend()).connect_tcp('approved.example', 443)
        assert error.value.code == 'OUTPUT_ADDRESS_NOT_ALLOWED'
    asyncio.run(run())


@pytest.mark.parametrize('tls_failure', [False, True])
def test_r0_pinned_transport_preserves_tls_hostname_and_validation(client, monkeypatch, tls_failure):
    import asyncio
    import socket
    import ssl
    import httpcore._backends.auto
    from gw.god_workflow import routes
    calls = []
    payload = _tiny_png()
    class Stream:
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED
            assert ssl_context.check_hostname is True
            assert server_hostname == 'approved.example'
            calls.append(('tls', server_hostname))
            if tls_failure:
                raise ssl.SSLCertVerificationError('不可信证书')
            return self
        async def write(self, buffer, timeout=None): calls.append(('write', buffer))
        async def read(self, max_bytes, timeout=None):
            return b'HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: ' + str(len(payload)).encode() + b'\r\nConnection: close\r\n\r\n' + payload
        async def aclose(self): pass
        def get_extra_info(self, info): return None
    class Backend:
        async def connect_tcp(self, host, port, **kwargs):
            assert host == '8.8.8.8'
            calls.append(('connect', host))
            return Stream()
    async def resolve(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
    monkeypatch.setattr(httpcore._backends.auto, 'AutoBackend', Backend)
    monkeypatch.setenv('GW_WORKFLOW_OUTPUT_HOSTS', 'approved.example')
    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolve)
        return await routes._collect_rh_task_outputs(routes._auth(_headers()['Authorization'], 'editor', True), 'tls-task', [{'fileUrl': 'https://approved.example/out.png'}])
    result = asyncio.run(run())
    assert ('connect', '8.8.8.8') in calls
    assert ('tls', 'approved.example') in calls
    assert result[0]['registered'] is not tls_failure
    if tls_failure:
        assert result[0]['code'] == 'DOWNLOAD_ERROR'
        assert not any(kind == 'write' for kind, _ in calls)


@pytest.mark.parametrize('case,expected', [('redirect', 'DOWNLOAD_FAILED'), ('overflow', 'OUTPUT_TOO_LARGE'), ('count', 'OUTPUT_COUNT_EXCEEDED'), ('unapproved', 'OUTPUT_URL_NOT_ALLOWED'), ('unknown', 'OUTPUT_REGISTRATION_FAILED')])
def test_r0_cloud_output_limits_and_redirects(client, monkeypatch, case, expected):
    import asyncio
    from gw.god_workflow import routes, registry
    requests = []
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'ab'
            yield b'cd'
    def handle(request):
        requests.append(request)
        if case == 'redirect':
            return httpx.Response(302, headers={'location': 'https://approved.example/other.png'})
        if case == 'overflow':
            return httpx.Response(200, stream=Chunks(), headers={'content-type': 'image/png'})
        return httpx.Response(200, content=b'unknown', headers={'content-type': 'application/octet-stream'})
    monkeypatch.setenv('GW_WORKFLOW_OUTPUT_HOSTS', 'approved.example')
    monkeypatch.setattr(routes, '_output_transport', lambda: httpx.MockTransport(handle))
    monkeypatch.setattr(registry, 'MAX_ASSET_BYTES', 3 if case == 'overflow' else 32 * 1024 * 1024)
    outputs = [{'fileUrl': 'https://' + ('unapproved.example' if case == 'unapproved' else 'approved.example') + '/out.' + ('bin' if case == 'unknown' else 'png')}]
    if case == 'count': outputs *= 33
    result = asyncio.run(routes._collect_rh_task_outputs(routes._auth(_headers()['Authorization'], 'editor', True), 'limit-task', outputs))
    assert result[0]['registered'] is False
    assert result[0]['code'] == expected
    assert len(requests) == (0 if case in {'count', 'unapproved'} else 1)


def test_r0_local_output_count_limit_prevents_download(client, monkeypatch):
    import asyncio
    from gw.god_workflow import routes, comfy_executor
    async def fetch(*args, **kwargs): pytest.fail('超限不得开始回收')
    monkeypatch.setattr(comfy_executor, 'fetch_output', fetch)
    outputs = [{'filename': 'out.png'}] * 33
    result = asyncio.run(routes._collect_task_outputs(routes._auth(_headers()['Authorization'], 'editor', True), 'count-task', outputs))
    assert result == [{'registered': False, 'code': 'OUTPUT_COUNT_EXCEEDED'}]


@pytest.mark.parametrize('window', ['receipt', 'file'])
def test_r0_output_pre_manifest_fault_keeps_no_false_success(client, monkeypatch, tmp_path, window):
    from gw.god_workflow import registry, routes
    context = routes._auth(_headers()['Authorization'], 'editor', True)
    write, replace = registry._atomic_write, registry.os.replace
    def fail_receipt(path, payload):
        if path.parent.name == 'output_recovery': raise OSError('凭单失败')
        return write(path, payload)
    def fail_file(src, dst):
        if Path(dst).suffix == '.png': raise OSError('文件失败')
        return replace(src, dst)
    if window == 'receipt': monkeypatch.setattr(registry, '_atomic_write', fail_receipt)
    else: monkeypatch.setattr(registry.os, 'replace', fail_file)
    with pytest.raises(OSError):
        registry.store_bytes(context, _tiny_png(), 'out.png', 'image/png', task_id='pre-manifest')
    assert registry.load_assets(context) == []
    assert list((tmp_path / 'data' / 'workflow').glob('*/assets/*.png')) == []
    monkeypatch.setattr(registry, '_atomic_write', write)
    monkeypatch.setattr(registry.os, 'replace', replace)
    recovered = registry.store_bytes(context, _tiny_png(), 'out.png', 'image/png', task_id='pre-manifest')
    assert registry.load_assets(context)[0]['asset_id'] == recovered['asset_id']


def test_r0_recovery_rejects_corrupt_backing_file_without_deleting_it(client, monkeypatch, tmp_path):
    from gw.god_workflow import registry, routes
    context = routes._auth(_headers()['Authorization'], 'editor', True)
    write = registry._atomic_write
    def fail_manifest(path, payload):
        if path.name == 'assets.json': raise OSError('清单失败')
        return write(path, payload)
    monkeypatch.setattr(registry, '_atomic_write', fail_manifest)
    with pytest.raises(OSError): registry.store_bytes(context, _tiny_png(), 'out.png', 'image/png', task_id='corrupt')
    backing = next((tmp_path / 'data' / 'workflow').glob('*/assets/*.png'))
    original = backing.read_bytes()
    backing.write_bytes(b'x' * len(original))
    monkeypatch.setattr(registry, '_atomic_write', write)
    assert registry.load_assets(context) == []
    assert backing.is_file()


def test_export_to_comfy_litegraph_structure():
    """验证 export_to_comfy_litegraph 转换后的工作流符合 LiteGraph 格式要求。"""
    from gw.god_workflow.exporter import export_to_comfy_litegraph

    internal_doc = {
        "workflow_id": "test-wf-100",
        "name": "测试流",
        "nodes": [
            {
                "id": "1",
                "kind": "CLIPLoader",
                "title": "CLIPLoader",
                "inputs": {"clip_name": "qwen.safetensors", "type": "qwen_image"},
            },
            {
                "id": "2",
                "kind": "TextEncode",
                "title": "TextEncode",
                "inputs": {"prompt": "cat", "clip": ["1", 0]},
            },
        ],
        "connections": [
            {
                "id": "1->2:clip",
                "source": "1",
                "target": "2",
                "source_slot": "0",
                "target_slot": "clip",
            }
        ],
    }

    lg = export_to_comfy_litegraph(internal_doc)
    assert lg["id"] == "test-wf-100"
    assert "nodes" in lg
    assert len(lg["nodes"]) == 2
    for node in lg["nodes"]:
        # 必须是 list，绝不能是 dict，否则 ComfyUI 会报 TypeError: t.inputs?.map is not a function
        assert isinstance(node["inputs"], list)
        assert isinstance(node["outputs"], list)
        assert isinstance(node["pos"], list)
        assert len(node["pos"]) == 2
        assert node["type"] in ("CLIPLoader", "TextEncode")
        assert node["type"] != "undefined"

    # 验证连线
    assert len(lg["links"]) == 1
    link = lg["links"][0]
    assert link[1] == 1  # from_node
    assert link[3] == 2  # to_node
    assert link[5] == "CLIP"


def test_export_endpoint_returns_litegraph(client):
    """验证 GET /api/god_workflow/export/{workflow_id}?format=litegraph 接口返回标准 LiteGraph 格式。"""
    created = client.post(
        "/api/god_workflow/documents?name=导出LiteGraph测试&source=canvas",
        json={
            "nodes": [
                {
                    "id": "1",
                    "kind": "LoadImage",
                    "inputs": {"image": "test.png"},
                }
            ]
        },
        headers=_headers(),
    ).json()
    wf_id = created["workflow_id"]

    res = client.get(f"/api/god_workflow/export/{wf_id}?format=litegraph", headers=_headers())
    assert res.status_code == 200
    body = res.json()
    assert "nodes" in body
    assert isinstance(body["nodes"][0]["inputs"], list)
    assert isinstance(body["nodes"][0]["outputs"], list)


@pytest.mark.parametrize('filename', ['result.bin', 'result'])
def test_r0_output_receipt_recovers_mime_supported_filename(client, monkeypatch, filename):
    from gw.god_workflow import registry, routes
    context = routes._auth(_headers()['Authorization'], 'editor', True)
    original = registry._atomic_write
    manifest = registry._assets_manifest_path(context)
    def fail(path, body):
        if path == manifest:
            raise OSError('synthetic manifest failure')
        return original(path, body)
    monkeypatch.setattr(registry, '_atomic_write', fail)
    with pytest.raises(OSError):
        registry.store_bytes(context, _tiny_png(), filename, 'image/png', task_id='mime-recovery')
    monkeypatch.setattr(registry, '_atomic_write', original)
    recovered = registry.load_assets(context)
    assert len(recovered) == 1
    assert recovered[0]['extension'] == '.png'
    assert registry.resolve_asset_file(context, recovered[0]['asset_id']) is not None


def test_r0_output_receipt_recovers_non_list_manifest(client):
    from gw.god_workflow import registry, routes
    context = routes._auth(_headers()['Authorization'], 'editor', True)
    asset = registry.store_bytes(context, _tiny_png(), 'out.png', 'image/png', task_id='bad-manifest')
    registry._atomic_write(registry._assets_manifest_path(context), {'invalid': 'shape'})
    recovered = registry.load_assets(context)
    assert [item['asset_id'] for item in recovered] == [asset['asset_id']]

def test_r1_private_output_registration_receives_principal(client, monkeypatch):
    from gw.god_workflow import routes
    calls = []
    monkeypatch.setattr(routes.asset_hub_bridge, 'register_output_to_asset_hub', lambda asset, **kwargs: calls.append((asset, kwargs)) or {})
    async def fetch(*args, **kwargs):
        return _tiny_png(), 'image/png'
    monkeypatch.setattr(routes.comfy_executor, 'fetch_output', fetch)
    context = routes._auth(_headers()['Authorization'], 'editor', True)
    import asyncio
    outputs = asyncio.run(routes._collect_task_outputs(context, 'private-task', [{'filename': 'private.png'}]))
    assert outputs[0]['registered'] is True
    assert len(calls) == 1 and calls[0][1]['context'] == context


def test_public_detail_requires_source_topology():
    """公开节点清单没有源 links 时必须失败关闭，禁止猜测正确性。"""
    from gw.god_workflow.routes import _document_from_bundle
    from gw.core.errors import CleanroomException
    detail = {"id": "2040216332402171906", "primitiveNodes": ["LoadImage", "SaveImage"], "customNodes": ["SeeThrough_GenerateLayers"]}
    with pytest.raises(CleanroomException, match="缺少原始拓扑"):
        _document_from_bundle({"workflow_id": detail["id"], "detail": detail})


@pytest.mark.parametrize("workflow_id", ["2084124735289520130", "2108563268972404737"])
def test_rh_public_preview_is_not_a_document_or_executable(client, monkeypatch, workflow_id):
    """同实际接口的缺源结构可预览，但不能落为虚构图或提交生成。"""
    calls = []
    async def source(url, body, headers):
        calls.append(url)
        assert url.endswith('/api/workflow/getDetail')
        assert body['workflowId'] == workflow_id
        return {'code': 0, 'data': {'id': workflow_id, 'name': '公开工作流',
            'workflowContent': None, 'nodeCount': 15, 'primitiveNodes': ['LoadImage'],
            'customNodes': ['CreateVideo', 'LoadImage'], 'usedModels': ['公开模型']}}
    monkeypatch.setattr(platforms, '_post_json', source)
    monkeypatch.setattr('gw.god_workflow.registry.credential_value', lambda *args: '')
    preview = client.post('/api/god_workflow/parse-link',
        json={'url_or_text': f'https://www.runninghub.cn/workflow/{workflow_id}'}, headers=_headers())
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body['data_completeness'] == 'public_metadata_only'
    assert body['public_preview']['node_types'] == ['LoadImage', 'CreateVideo']
    assert body['public_preview']['published_node_count'] == 15
    assert body['public_preview']['models'] == ['公开模型']
    assert not {'nodes', 'connections', 'prompt', 'asset_id'} & body.keys()
    assert client.get('/api/god_workflow/list', headers=_headers()).json()['items'] == []
    assert client.get(f'/api/god_workflow/item/{workflow_id}', headers=_headers()).status_code == 404
    assert client.get(f'/api/god_workflow/export/{workflow_id}', headers=_headers()).status_code == 404
    rejected = client.post('/api/god_workflow/execute',
        json={'workflow_id': workflow_id, 'target': 'rh_cloud'}, headers=_headers())
    assert rejected.status_code == 404
    assert calls == ['https://www.runninghub.cn/api/workflow/getDetail']


def test_rh_public_preview_preserves_existing_source_document(client, monkeypatch):
    """后来原图不可获取时，预览不能覆盖同 ID 的已保存参数。"""
    workflow_id = '2108563268972404737'
    bundle = {'workflow_id': workflow_id, 'api_json': {
        '1': {'class_type': 'LoadImage', 'inputs': {'image': 'user-edited.png'}}}}
    async def source(*args, **kwargs):
        return bundle
    monkeypatch.setattr(platforms, 'fetch_runninghub_bundle', source)
    body = {'url_or_text': f'https://www.runninghub.cn/workflow/{workflow_id}'}
    original = client.post('/api/god_workflow/parse-link', json=body, headers=_headers())
    assert original.status_code == 200
    saved = client.get(f'/api/god_workflow/item/{workflow_id}', headers=_headers()).json()
    bundle.pop('api_json')
    bundle['detail'] = {'primitiveNodes': ['LoadImage'], 'workflowContent': None}
    response = client.post('/api/god_workflow/parse-link', json=body, headers=_headers())
    assert response.status_code == 200 and response.json()['data_status'] == 'preview'
    assert client.get(f'/api/god_workflow/item/{workflow_id}', headers=_headers()).json() == saved


def test_rh_real_public_graph_never_degrades_to_preview():
    from gw.god_workflow.routes import _public_preview_from_bundle
    bundle = {'provider': 'runninghub', 'detail': {
        'workflowContent': {'nodes': [{'id': 1, 'type': 'LoadImage'}], 'links': []},
        'primitiveNodes': ['LoadImage']}}
    assert _public_preview_from_bundle(bundle) is None


@pytest.mark.parametrize("failure,status,code", [(httpx.ConnectError("secret-token"),502,"WORKFLOW_PROVIDER_UNREACHABLE"),(httpx.ReadTimeout("secret-token"),504,"WORKFLOW_PROVIDER_TIMEOUT")])
def test_parse_link_network_error_actionable(client, monkeypatch, failure, status, code):
    class OfflineClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): raise failure
    monkeypatch.setattr(platforms.httpx, "AsyncClient", OfflineClient)
    monkeypatch.setattr("gw.god_workflow.registry.credential_value", lambda *args: "")
    response=client.post("/api/god_workflow/parse-link", json={"url_or_text":"https://www.runninghub.cn/workflow/2104915887789797378"}, headers=_headers())
    assert response.status_code==status
    detail=response.json()["detail"]
    assert detail["code"]==code
    assert "JSON" in detail["message"] and "runninghub.cn" in detail["message"]
    assert "secret-token" not in response.text


def test_parse_link_local_json_never_calls_provider(client, monkeypatch):
    async def forbidden(*args, **kwargs): pytest.fail("本地JSON不得出网")
    monkeypatch.setattr(platforms,"fetch_runninghub_bundle",forbidden)
    graph={"nodes":[{"id":1,"type":"SaveImage","pos":[20,30]}],"links":[]}
    response=client.post("/api/god_workflow/parse-link",json={"url_or_text":json.dumps(graph)},headers=_headers())
    assert response.status_code==200
    assert response.json()["nodes"][0]["position"]=={"x":20.,"y":30.}


def test_parse_link_rh_source_preserves_graph(client, monkeypatch):
    async def source(*args, **kwargs):
        return {"workflow_id":"2104915887789797378","canvas_json":{"nodes":[{"id":1,"type":"LoadImage","pos":[20,30]},{"id":2,"type":"SaveImage","pos":[200,30]}],"links":[[4,1,0,2,0,"IMAGE"]]}}
    monkeypatch.setattr(platforms,"fetch_runninghub_bundle",source)
    response=client.post("/api/god_workflow/parse-link",json={"url_or_text":"https://www.runninghub.cn/workflow/2104915887789797378"},headers=_headers())
    assert response.status_code==200
    assert response.json()["connections"][0]["source"]=="1"


def test_rh_configured_token_failure_not_silently_lost(monkeypatch):
    import asyncio
    async def reject(*args, **kwargs):
        return {"code":401,"msg":"secret-token","data":None}
    monkeypatch.setattr(platforms,"_post_json",reject)
    with pytest.raises(CleanroomException) as caught:
        asyncio.run(platforms.fetch_runninghub_canvas("2104915887789797378","secret-token"))
    assert caught.value.code=="RH_SOURCE_AUTH_FAILED"
    assert "secret-token" not in caught.value.message


def test_rh_post_rejects_redirect_without_forwarding_key(monkeypatch):
    import asyncio
    captured={}
    class RedirectClient:
        def __init__(self, **kwargs): captured.update(kwargs)
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs):
            return httpx.Response(307, headers={"Location":"https://other.test/secret-token"})
    monkeypatch.setattr(platforms.httpx,"AsyncClient",RedirectClient)
    with pytest.raises(CleanroomException) as caught:
        asyncio.run(platforms._post_json("https://www.runninghub.cn/api/workflow/getContent",{"apiKey":"secret-token"},{}))
    assert captured["follow_redirects"] is False
    assert caught.value.code=="WORKFLOW_PROVIDER_REDIRECT_REJECTED"
    assert "secret-token" not in caught.value.message


def test_rh_failed_canvas_can_recover_from_real_public_source(monkeypatch):
    import asyncio
    async def source(url,*args):
        if url.endswith("getContent"): raise CleanroomException(502,"WORKFLOW_PROVIDER_UNREACHABLE","网络不可达")
        return {"code":0,"data":{"workflowContent":{"nodes":[{"id":1,"type":"SaveImage","pos":[9,8]}],"links":[]}}}
    monkeypatch.setattr(platforms,"_post_json",source)
    bundle=asyncio.run(platforms.fetch_runninghub_bundle("https://www.runninghub.cn/workflow/2104915887789797378",access_token="test-only"))
    from gw.god_workflow.routes import _document_from_bundle
    assert _document_from_bundle(bundle).as_dict()["nodes"][0]["position"]=={"x":9.,"y":8.}


def test_local_json_with_rh_metadata_is_not_remote_reference(client, monkeypatch):
    async def forbidden(*args, **kwargs): pytest.fail("JSON元数据内RH链接不应触发抓取")
    monkeypatch.setattr(platforms,"fetch_runninghub_bundle",forbidden)
    graph={"nodes":[{"id":1,"type":"SaveImage","pos":[20,30]}],"links":[],"extra":{"source":"https://www.runninghub.cn/workflow/2104915887789797378"}}
    response=client.post("/api/god_workflow/parse-link",json={"url_or_text":json.dumps(graph)},headers=_headers())
    assert response.status_code==200
