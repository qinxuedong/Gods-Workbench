"""RH凭据配置闭环：隔离存储、合成密钥与供应商替身。"""
import json

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core.auth import require_authenticated
from gw.core.errors import CleanroomException
from gw.god_workflow import platforms, registry


def headers(subject="owner"):
    return {"Authorization": f"Bearer {subject}", "Origin": "http://testserver"}


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


def configure(client, **fields):
    response = client.post("/api/god_workflow/settings", headers={**headers(), "X-User-Role": "admin"}, json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def test_encrypted_credentials_survive_restart_and_drive_source_and_tasks(client, monkeypatch, tmp_path):
    key, token = "synthetic-workflow-api-key", "synthetic-workflow-access-token"
    configure(client, rh_api_key=key, rh_access_token=token)
    snapshot = next((tmp_path / "data/workflow").glob("*/settings.json")).read_text(encoding="utf-8")
    assert key not in snapshot and token not in snapshot
    stored = json.loads(snapshot)
    assert stored["rh_api_key_encrypted"].startswith(("dpapi:", "fernet:"))
    assert stored["rh_access_token_encrypted"].startswith(("dpapi:", "fernet:"))
    seen = []

    async def source(reference, *, api_key, access_token):
        seen.append(("source", api_key, access_token))
        return {"workflow_id": "2108563268972404737", "api_json": {
            "1": {"class_type": "LoadImage", "inputs": {"image": "real-input.png"}}}}

    async def submit(workflow_id, node_info, api_key, **kwargs):
        seen.append(("submit", api_key))
        return {"taskId": "synthetic-remote-task"}

    async def query(task_id, api_key, **kwargs):
        seen.append(("query", api_key))
        return {"status": "SUCCESS", "outputs": []}

    monkeypatch.setattr(platforms, "fetch_runninghub_bundle", source)
    monkeypatch.setattr(platforms, "create_rh_cloud_task", submit)
    monkeypatch.setattr(platforms, "query_rh_task_outputs", query)
    with TestClient(create_app()) as restarted:
        config = restarted.get("/api/god_workflow/settings", headers=headers())
        assert config.status_code == 200
        assert config.json()["has_rh_api_key"] and config.json()["has_rh_access_token"]
        assert "encrypted" not in config.text and key not in config.text and token not in config.text
        for path in ("/api/god_workflow/providers/runninghub/fetch", "/api/god_workflow/parse-link"):
            parsed = restarted.post(path, headers=headers(), json={"url_or_text": "https://www.runninghub.cn/workflow/2108563268972404737"})
            assert parsed.status_code == 200, parsed.text
        workflow_id = parsed.json()["workflow_id"]
        executed = restarted.post("/api/god_workflow/tasks/execute", headers=headers(),
            json={"workflow_id": workflow_id, "target": "rh_cloud"})
        assert executed.status_code == 202, executed.text
        job_id = executed.json()["job_id"]
        assert restarted.get(f"/api/god_workflow/tasks/{job_id}", headers=headers()).status_code == 200
        assert restarted.post(f"/api/god_workflow/tasks/{job_id}/collect", headers=headers()).status_code == 200
        assert restarted.get("/api/god_workflow/capabilities", headers=headers()).json()["executors"]["rh_cloud"] == "configured"
        assert seen == [("source", key, token), ("source", key, token), ("submit", key), ("query", key), ("query", key)]
    for path in (tmp_path / "data/workflow").rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert key not in text and token not in text


def test_credentials_are_isolated_blank_preserves_and_clear_falls_back(client, monkeypatch):
    configure(client, rh_api_key="private-synthetic-key", rh_access_token="private-synthetic-token")
    other = client.get("/api/god_workflow/settings", headers=headers("other")).json()
    assert not other["has_rh_api_key"] and not other["has_rh_access_token"]
    mine = configure(client, rh_api_key="", rh_access_token=" ", comfy_url="http://127.0.0.1:8199")
    assert mine["rh_api_key_source"] == mine["rh_access_token_source"] == "local"
    monkeypatch.setenv("GW_RUNNINGHUB_API_KEY", "synthetic-env-key")
    mine = configure(client, clear_rh_api_key=True)
    assert mine["rh_api_key_source"] == "environment" and mine["has_rh_api_key"]
    assert mine["rh_access_token_source"] == "local"
    monkeypatch.delenv("GW_RUNNINGHUB_API_KEY")
    mine = configure(client, clear_rh_access_token=True)
    assert not mine["has_rh_api_key"] and not mine["has_rh_access_token"]


def test_save_failure_is_atomic_and_does_not_leak_secret(client, monkeypatch, tmp_path):
    configure(client, comfy_url="http://127.0.0.1:8199")
    path = next((tmp_path / "data/workflow").glob("*/settings.json"))
    before = path.read_bytes()

    def unavailable(*args):
        raise CleanroomException(503, "PROVIDER_CREDENTIAL_UNAVAILABLE", "凭据保护失败")

    monkeypatch.setattr(registry.credentials, "protect", unavailable)
    response = client.post("/api/god_workflow/settings", headers={**headers(), "X-User-Role": "admin"},
        json={"rh_api_key": "synthetic-rejected-key", "comfy_url": "http://127.0.0.1:9999"})
    assert response.status_code == 503
    assert "synthetic-rejected-key" not in response.text
    assert path.read_bytes() == before


def test_swapped_or_corrupt_ciphertext_fails_closed_and_can_be_cleared(client, monkeypatch, tmp_path):
    configure(client, rh_api_key="private-synthetic-key", rh_access_token="private-synthetic-token")
    owner_path = next((tmp_path / "data/workflow").glob("*/settings.json"))
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    # 同一系统用户下换主体或换字段也不能使用该密文。
    other_path = registry._settings_path(require_authenticated("Bearer other", "editor"))
    registry._atomic_write(other_path, owner)
    assert client.get("/api/god_workflow/settings", headers=headers("other")).status_code == 503
    owner["rh_api_key_encrypted"] = owner["rh_access_token_encrypted"]
    registry._atomic_write(owner_path, owner)
    monkeypatch.setenv("GW_RUNNINGHUB_API_KEY", "fallback-must-not-hide-corruption")
    assert client.get("/api/god_workflow/settings", headers=headers()).status_code == 503
    repaired = configure(client, clear_rh_api_key=True)
    assert repaired["rh_api_key_source"] == "environment"


@pytest.mark.parametrize("payload", [
    {"rh_api_key": 1}, {"rh_access_token": {}}, {"clear_rh_api_key": "yes"},
    {"rh_api_key": "synthetic-key", "clear_rh_api_key": True},
])
def test_invalid_credential_updates_fail_before_commit(client, payload, tmp_path):
    response = client.post("/api/god_workflow/settings", headers=headers(), json=payload)
    assert response.status_code == 400
    assert not list((tmp_path / "data/workflow").glob("*/settings.json"))
