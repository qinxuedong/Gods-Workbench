from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
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
    assert "workflow execution and polling" in body["todo"]
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


def test_unconfigured_provider_fetch_is_standard_503(client):
    response = client.post("/api/god_workflow/providers/comfyui/fetch", json={"workflow_id": "abc"}, headers=_headers())
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "WORKFLOW_PROVIDER_UNAVAILABLE"


def test_provider_fetch_rejects_url_like_reference(client):
    response = client.post("/api/god_workflow/providers/comfyui/fetch", json={"workflow_id": "https://evil.test/x"}, headers=_headers())
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_PROVIDER_REFERENCE"


def test_task_execute_does_not_fabricate_result(client):
    response = client.post("/api/god_workflow/tasks/execute", json={"workflow": {}}, headers=_headers())
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "WORKFLOW_EXECUTOR_UNAVAILABLE"


def test_task_status_is_explicitly_unavailable(client):
    response = client.get("/api/god_workflow/tasks/task-1", headers=_headers())
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "WORKFLOW_EXECUTOR_UNAVAILABLE"


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


def test_settings_round_trip_masks_secrets_and_hides_local_paths(client, tmp_path):
    payload = {"comfy_url": "http://127.0.0.1:8188",
               "comfy_root_dir": r"E:\ComfyUI-Easy\ComfyUI-Easy-Install",
               "local_models_dir": r"E:\ComfyUI-Easy\ComfyUI-Easy-Install\models",
               "rh_api_key": "rh-super-secret-value",
               "rh_access_token": "web-token-super-secret"}
    written = client.post("/api/god_workflow/settings", json=payload, headers=_headers())
    assert written.status_code == 200, written.text
    body = written.json()
    for key in ("comfy_url", "comfy_root_dir", "local_models_dir", "has_rh_api_key",
                "has_rh_access_token", "rh_api_key_masked", "rh_access_token_masked", "provider_status"):
        assert key in body, key
    assert body["comfy_url"] == "http://127.0.0.1:8188"
    assert body["has_rh_api_key"] is True and body["has_rh_access_token"] is True
    assert "rh-super-secret-value" not in written.text
    assert "web-token-super-secret" not in written.text
    # 不在运行沙盒内的绝对路径只保留末段名字，不回显本机绝对布局。
    assert body["comfy_root_dir"] == "ComfyUI-Easy-Install"
    assert body["local_models_dir"] == "models"
    assert "E:" not in written.text and "/sessions/" not in written.text

    fetched = client.get("/api/god_workflow/settings", headers=_headers())
    assert fetched.status_code == 200
    assert fetched.json() == body
    stored = list((tmp_path / "data" / "workflow").glob("*/settings.json"))
    assert stored, "设置必须落在 runtime data_root/workflow 下"
    assert "rh-super-secret-value" not in stored[0].read_text(encoding="utf-8")


def test_settings_are_principal_isolated(client):
    client.post("/api/god_workflow/settings", json={"rh_api_key": "key-one-tenant"}, headers=_headers("one"))
    mine = client.get("/api/god_workflow/settings", headers=_headers("one")).json()
    other = client.get("/api/god_workflow/settings", headers=_headers("two")).json()
    assert mine["has_rh_api_key"] is True
    assert other["has_rh_api_key"] is False
    assert other["rh_api_key_masked"] == ""


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
    laid_out = client.post(f"/api/god_workflow/auto-layout/{workflow_id}", json={}, headers=_headers())
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
                         json={"widget_updates": [{"node_id": "n1", "field_name": "seed", "value": 42, "promoted": True}]},
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
                          json={"widget_updates": [{"node_id": "n1", "field_name": "seed", "value": 3, "promoted": True}]},
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

