"""QIN-629 工作流输入确认与已登记产物回传契约。"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import asyncio
from pathlib import Path

from PIL import Image
import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer qin629-task-owner", "X-User-Role": "editor"}
OTHER = {"Authorization": "Bearer qin629-task-other", "X-User-Role": "editor"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(tmp_path / "allowed"))
    from gw.api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _saved_workflow(client: TestClient) -> tuple[dict, dict]:
    uploaded = client.post(
        "/api/god_workflow/assets/upload",
        headers=AUTH,
        files={"file": ("source.png", _png(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()
    document = {
        "nodes": [
            {
                "id": "1",
                "kind": "LoadImage",
                "inputs": {"image": "placeholder-a.png"},
                "metadata": {"input_assets": {"image": asset["asset_id"]}},
            },
            {
                "id": "2",
                "kind": "LoadImage",
                "inputs": {"image": "placeholder-b.png"},
                "metadata": {"input_assets": {"image": asset["asset_id"]}},
            },
        ],
        "connections": [],
    }
    saved = client.post(
        "/api/god_workflow/documents?source=canvas",
        headers=AUTH,
        json=document,
    )
    assert saved.status_code == 201, saved.text
    return asset, saved.json()["workflow"]


def _task_preflight(client: TestClient, workflow: dict) -> dict:
    transfer_context = client.get("/api/transfer/context", headers=AUTH)
    assert transfer_context.status_code == 200, transfer_context.text
    bindings = [
        {
            "node_id": str(node["id"]),
            "field": field,
            "asset_id": asset_id,
        }
        for node in workflow["nodes"]
        for field, asset_id in node.get("metadata", {}).get("input_assets", {}).items()
    ]
    response = client.post(
        "/api/transfer/task-preflights",
        headers=AUTH,
        json={
            "workflow_id": workflow["workflow_id"],
            "expected_version": workflow["version"],
            "target": "local_comfy",
            "target_workspace_id": transfer_context.json()["workspace_id"],
            "directory_id": transfer_context.json()["directory_id"],
            "bindings": bindings,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _confirm_task(client: TestClient, preflight: dict, headers=AUTH):
    return client.post(
        f"/api/transfer/task-preflights/{preflight['preflight_id']}/confirm",
        headers=headers,
        json={"expected_version": preflight["version"]},
    )


def test_task_preflight_deduplicates_assets_and_binds_all_node_fields(client: TestClient, tmp_path: Path):
    asset, workflow = _saved_workflow(client)
    preflight = _task_preflight(client, workflow)

    assert preflight["status"] == "awaiting_confirmation"
    assert preflight["workflow_version"] == workflow["version"]
    assert len(preflight["input_assets"]) == 1
    assert preflight["input_assets"][0]["asset_id"] == asset["asset_id"]
    assert preflight["input_assets"][0]["transfer_mode"] == "upload"
    assert preflight["input_assets"][0]["bindings"] == [
        {"node_id": "1", "field": "image"},
        {"node_id": "2", "field": "image"},
    ]
    assert "result_root" not in preflight
    assert str(tmp_path) not in json.dumps(preflight, ensure_ascii=False)

    denied = _confirm_task(client, preflight, OTHER)
    assert denied.status_code == 404
    assert denied.json()["detail"]["code"] == "TRANSFER_NOT_FOUND"

    confirmed = _confirm_task(client, preflight)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["transfer_receipt_id"] == preflight["preflight_id"]
    repeated = _confirm_task(client, preflight)
    assert repeated.status_code == 409


def test_task_execution_requires_receipt_and_uploads_one_copy_for_repeated_asset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    asset, workflow = _saved_workflow(client)
    expected_bytes = client.get(
        f"/api/god_workflow/assets/{asset['asset_id']}/content", headers=AUTH
    ).content
    from gw.god_workflow import comfy_executor, routes as workflow_routes

    upload_calls: list[tuple[bytes, str]] = []

    async def upload(payload, filename, _content_type, **_kwargs):
        upload_calls.append((payload, filename))
        return "engine/task-input.png"

    async def submit(prompt, **_kwargs):
        assert prompt["1"]["inputs"]["image"] == "engine/task-input.png"
        assert prompt["2"]["inputs"]["image"] == "engine/task-input.png"
        return {"prompt_id": "qin629-task-transfer"}

    monkeypatch.setattr(
        workflow_routes,
        "_comfy_endpoint",
        lambda *_args, **_kwargs: comfy_executor.ComfyEndpoint("http://127.0.0.1:8188"),
    )
    monkeypatch.setattr(comfy_executor, "upload_input", upload)
    monkeypatch.setattr(comfy_executor, "submit", submit)

    body = {
        "workflow_id": workflow["workflow_id"],
        "expected_version": workflow["version"],
        "target": "local_comfy",
        "transfer_receipt_id": "",
    }
    missing_receipt = client.post(
        "/api/god_workflow/execute",
        headers={**AUTH, "Idempotency-Key": "qin629-missing-receipt"},
        json=body,
    )
    assert missing_receipt.status_code == 409
    assert missing_receipt.json()["detail"]["code"] == "TASK_TRANSFER_CONFIRMATION_REQUIRED"
    assert upload_calls == []

    preflight = _task_preflight(client, workflow)
    confirmed = _confirm_task(client, preflight)
    assert confirmed.status_code == 200, confirmed.text
    body["transfer_receipt_id"] = confirmed.json()["transfer_receipt_id"]
    submitted = client.post(
        "/api/god_workflow/execute",
        headers={**AUTH, "Idempotency-Key": "qin629-with-receipt"},
        json=body,
    )
    assert submitted.status_code == 202, submitted.text
    assert submitted.json()["status"] == "accepted"
    assert upload_calls == [(expected_bytes, asset["asset_id"] + ".png")]


def test_registered_output_return_is_atomic_non_overwriting_and_retryable(
    client: TestClient, tmp_path: Path
):
    _asset, workflow = _saved_workflow(client)
    preflight = _task_preflight(client, workflow)
    confirmed = _confirm_task(client, preflight)
    assert confirmed.status_code == 200, confirmed.text

    from gw.core.asset_visibility import asset_owner_key
    from gw.core.auth import require_authenticated
    from gw.transfer.service import default_transfer_service

    owner_id = asset_owner_key(require_authenticated(AUTH["Authorization"], "editor"))
    job_id = str(uuid.uuid4())
    receipt_id = confirmed.json()["transfer_receipt_id"]
    default_transfer_service.bind_task_job(
        receipt_id, owner_id=owner_id, job_id=job_id
    )

    source = tmp_path / "registered-output.png"
    source.write_bytes(b"registered image bytes")
    asset_id = str(uuid.uuid4())
    output_key = hashlib.sha256(b"comfy output descriptor").hexdigest()
    returned = default_transfer_service.return_registered_output(
        receipt_id,
        owner_id=owner_id,
        job_id=job_id,
        output_key=output_key,
        asset_id=asset_id,
        filename="output.png",
        source_path=source,
        size_bytes=source.stat().st_size,
        executor="local_comfy",
    )
    assert returned["status"] == "returned"
    target = (
        tmp_path
        / "data"
        / "transfers"
        / "assets"
        / preflight["workspace_id"]
        / preflight["directory_id"]
        / f"{asset_id}.png"
    )
    assert target.read_bytes() == source.read_bytes()
    assert default_transfer_service.get_task_preflight(
        receipt_id, owner_id=owner_id
    )["outputs"] == [returned]

    repeated = default_transfer_service.return_registered_output(
        receipt_id,
        owner_id=owner_id,
        job_id=job_id,
        output_key=output_key,
        asset_id=asset_id,
        filename="output.png",
        source_path=source,
        size_bytes=source.stat().st_size,
        executor="local_comfy",
    )
    assert repeated["status"] == "returned"
    assert len(default_transfer_service.get_task_preflight(
        receipt_id, owner_id=owner_id
    )["outputs"]) == 1

    conflicting_asset_id = str(uuid.uuid4())
    conflicting_target = target.with_name(f"{conflicting_asset_id}.png")
    conflicting_target.write_bytes(b"keep existing")
    retry_key = hashlib.sha256(b"retryable output descriptor").hexdigest()
    failed = default_transfer_service.return_registered_output(
        receipt_id,
        owner_id=owner_id,
        job_id=job_id,
        output_key=retry_key,
        asset_id=conflicting_asset_id,
        filename="conflict.png",
        source_path=source,
        size_bytes=source.stat().st_size,
        executor="local_comfy",
    )
    assert failed["status"] == "failed"
    assert failed["error"] == "TRANSFER_TARGET_EXISTS"
    assert conflicting_target.read_bytes() == b"keep existing"

    conflicting_target.unlink()
    retried = default_transfer_service.return_registered_output(
        receipt_id,
        owner_id=owner_id,
        job_id=job_id,
        output_key=retry_key,
        asset_id=conflicting_asset_id,
        filename="conflict.png",
        source_path=source,
        size_bytes=source.stat().st_size,
        executor="local_comfy",
    )
    assert retried["status"] == "returned"
    assert conflicting_target.read_bytes() == source.read_bytes()


def test_comfy_collection_retry_returns_registered_asset_without_registering_twice(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _input_asset, workflow = _saved_workflow(client)
    preflight = _task_preflight(client, workflow)
    confirmed = _confirm_task(client, preflight)
    assert confirmed.status_code == 200, confirmed.text

    from gw.core.asset_visibility import asset_owner_key
    from gw.core.auth import require_authenticated
    from gw.god_workflow import asset_hub_bridge, registry, routes as workflow_routes
    from gw.transfer.service import default_transfer_service

    auth_context = require_authenticated(AUTH["Authorization"], "editor")
    owner_id = asset_owner_key(auth_context)
    job_id = str(uuid.uuid4())
    receipt_id = confirmed.json()["transfer_receipt_id"]
    default_transfer_service.bind_task_job(
        receipt_id, owner_id=owner_id, job_id=job_id
    )
    payload = _png()
    calls = {"fetch": 0, "store": 0, "register": 0}
    store_bytes = registry.store_bytes

    async def fetch(_context, _method, *_args, **_kwargs):
        calls["fetch"] += 1
        return payload, "image/png"

    def store(*args, **kwargs):
        calls["store"] += 1
        return store_bytes(*args, **kwargs)

    def register(asset, *, context, generation=None):
        calls["register"] += 1
        return {"asset_id": asset["asset_id"]}

    monkeypatch.setattr(workflow_routes, "_comfy_call", fetch)
    monkeypatch.setattr(registry, "store_bytes", store)
    monkeypatch.setattr(asset_hub_bridge, "register_output_to_asset_hub", register)
    task = {
        "job_id": job_id,
        "target": "local_comfy",
        "transfer_receipt_id": receipt_id,
        "outputs": [],
    }
    descriptors = [{"filename": "generated.png", "subfolder": "", "type": "output"}]
    first = asyncio.run(
        workflow_routes._collect_task_outputs(
            auth_context, job_id, descriptors, task=task
        )
    )
    assert first[0]["registered"] is True
    assert first[0]["return_status"] == "returned"
    task["outputs"] = first

    repeated = asyncio.run(
        workflow_routes._collect_task_outputs(
            auth_context, job_id, descriptors, task=task
        )
    )
    assert repeated[0]["asset_id"] == first[0]["asset_id"]
    assert repeated[0]["return_status"] == "returned"
    assert calls == {"fetch": 1, "store": 1, "register": 1}


def test_comfy_mapping_skips_bytes_only_when_input_path_is_proven(client: TestClient, tmp_path: Path):
    from gw.core.auth import require_authenticated
    from gw.transfer.routes import _mapped_comfy_input_name

    comfy_root = tmp_path / "comfy"
    input_root = comfy_root / "input"
    input_root.mkdir(parents=True)
    mapped_file = input_root / "mapped.png"
    mapped_file.write_bytes(b"mapped input")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("GW_COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.setenv("GW_COMFYUI_ROOT_DIR", str(comfy_root))
    try:
        auth_context = require_authenticated(AUTH["Authorization"], "editor")
        assert _mapped_comfy_input_name(
            auth_context,
            mapped_file,
            "local_comfy",
            {"access_path": str(input_root)},
        ) == "mapped.png"
        assert _mapped_comfy_input_name(
            auth_context,
            mapped_file,
            "rh_cloud",
            {"access_path": str(input_root)},
        ) is None
        assert _mapped_comfy_input_name(
            auth_context,
            mapped_file,
            "local_comfy",
            {"access_path": str(tmp_path / "unrelated")},
        ) is None
    finally:
        monkeypatch.undo()
