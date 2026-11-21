"""QIN-629 显式上传契约。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer qin629-owner", "X-User-Role": "editor"}
OTHER = {"Authorization": "Bearer qin629-other", "X-User-Role": "editor"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("GW_DATA_DIR", str(data_root))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(tmp_path / "allowed"))
    from gw.api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def _preflight(client: TestClient, payload: bytes, name: str = "素材.png", headers=AUTH):
    response = client.post(
        "/api/transfer/preflight",
        headers=headers,
        json={
            "target_workspace_id": "ws_local_01",
            "directory_id": "transfer_default_01",
            "files": [
                {
                    "name": name,
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _confirm(client: TestClient, preflight: dict, *, action: str = "upload", new_name=None, key="qin629-idem", headers=AUTH):
    item = preflight["files"][0]
    return client.post(
        "/api/transfer/jobs",
        headers={**headers, "Idempotency-Key": key},
        json={
            "preflight_id": preflight["transfer_id"],
            "expected_version": preflight["version"],
            "choices": [{"file_id": item["file_id"], "action": action, "new_name": new_name}],
        },
    )


def _upload(client: TestClient, transfer: dict, payload: bytes, *, headers=AUTH):
    item = transfer["files"][0]
    return client.put(
        f"/api/transfer/jobs/{transfer['transfer_id']}/files/{item['file_id']}",
        headers={
            **headers,
            "X-Content-SHA256": item["sha256"],
            "Content-Length": str(len(payload)),
        },
        params={
            "generation": transfer["generation"],
            "expected_version": transfer["version"],
        },
        content=payload,
    )


def test_upload_preflight_confirmation_commit_and_registry_readback(client: TestClient, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    payload = b"qin629 binary test payload"
    preflight = _preflight(client, payload)
    assert preflight["status"] == "awaiting_confirmation"
    assert preflight["estimated_bytes"] == len(payload)
    assert preflight["source_label"] == "当前设备（本地选择）"
    assert preflight["target_label"] == "独立本机工作区"

    confirmed_response = _confirm(client, preflight)
    assert confirmed_response.status_code == 201, confirmed_response.text
    confirmed = confirmed_response.json()
    assert confirmed["status"] == "uploading"

    uploaded_response = _upload(client, confirmed, payload)
    assert uploaded_response.status_code == 200, uploaded_response.text
    uploaded = uploaded_response.json()
    assert uploaded["status"] == "ready"
    assert uploaded["files"][0]["status"] == "verified"

    commit = client.post(
        f"/api/transfer/jobs/{uploaded['transfer_id']}/commit",
        headers=AUTH,
        json={"generation": uploaded["generation"], "expected_version": uploaded["version"]},
    )
    assert commit.status_code == 200, commit.text
    completed = commit.json()
    assert completed["status"] == "completed"
    asset = completed["files"][0]
    assert asset["status"] == "committed"
    assert Path(asset["published_path"]).read_bytes() == payload

    media = client.get(f"/api/asset-registry/assets/{asset['asset_id']}/media", headers=AUTH)
    assert media.status_code == 200, media.text
    assert media.content == payload


def test_confirmation_is_idempotent_and_conflict_requires_explicit_choice(client: TestClient):
    payload = b"same name"
    first = _preflight(client, payload, "duplicate.png")
    confirmed = _confirm(client, first)
    assert confirmed.status_code == 201
    repeated = _confirm(client, first)
    assert repeated.status_code == 201
    assert repeated.json()["transfer_id"] == confirmed.json()["transfer_id"]

    uploaded = _upload(client, confirmed.json(), payload)
    assert uploaded.status_code == 200
    commit = client.post(
        f"/api/transfer/jobs/{uploaded.json()['transfer_id']}/commit",
        headers=AUTH,
        json={"generation": uploaded.json()["generation"], "expected_version": uploaded.json()["version"]},
    )
    assert commit.status_code == 200

    conflict = _preflight(client, payload, "duplicate.png")
    assert conflict["files"][0]["has_name_conflict"] is True
    rejected = _confirm(client, conflict, action="upload", key="new-key")
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "TRANSFER_NAME_CONFLICT"

    renamed = _confirm(client, conflict, action="rename", new_name="duplicate-copy.png", key="rename-key")
    assert renamed.status_code == 201, renamed.text


def test_checksum_mismatch_fails_closed_and_retry_advances_generation(client: TestClient):
    expected = b"aaaa"
    received = b"bbbb"
    preflight = _preflight(client, expected, "checksum.txt")
    confirmed = _confirm(client, preflight).json()
    response = _upload(client, confirmed, received)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TRANSFER_CHECKSUM_MISMATCH"

    state = client.get(f"/api/transfer/jobs/{confirmed['transfer_id']}", headers=AUTH).json()
    assert state["status"] == "failed"
    retry = client.post(
        f"/api/transfer/jobs/{confirmed['transfer_id']}/retry",
        headers=AUTH,
        json={"expected_version": state["version"]},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["generation"] == confirmed["generation"] + 1
    assert retry.json()["status"] == "uploading"


def test_cancel_invalidates_old_generation_and_owner_scope_hides_record(client: TestClient):
    preflight = _preflight(client, b"cancel me", "cancel.txt")
    confirmed = _confirm(client, preflight).json()
    transfer_id = confirmed["transfer_id"]
    cancelled = client.post(
        f"/api/transfer/jobs/{transfer_id}/cancel",
        headers=AUTH,
        json={"generation": confirmed["generation"], "expected_version": confirmed["version"]},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    hidden = client.get(f"/api/transfer/jobs/{transfer_id}", headers=OTHER)
    assert hidden.status_code == 404

    stale = _upload(client, confirmed, b"cancel me")
    assert stale.status_code == 409
