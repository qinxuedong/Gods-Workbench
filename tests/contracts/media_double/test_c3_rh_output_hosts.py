"""RunningHub 产物主机白名单：空名单、非授权主机与不重新提交的回收。

夹具是文档形态的成功回包，下载与 query_rh_task_outputs 都用替身，不访问网络。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from gw.god_workflow import platforms, routes
from media_doubles import FIXED_PNG, HEADERS, TASKS, execute, prepare_workflow

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runninghub" / "outputs_success_example.json"
HOST = "rh-images.xiaoyaoyou.com"
QUERY = "expires=1"
# 真实 RunningHub 产物主机：真实回包的 fileUrl 落在 tos 主机，而非文档主机。
REAL_HOST = "rh-images-tos.xiaoyaoyou.com"
REAL_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runninghub" / "outputs_success_real_host.json"
# 与 tools/c3_acceptance/ac8_runninghub_real.py 未设置环境变量时的默认值保持一致。
AC8_DEFAULT_HOSTS = f"{REAL_HOST},{HOST}"


def _fixture(path: Path = FIXTURE, host: str = HOST) -> dict:
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["code"] == 0
    item = body["data"][0]
    assert item["fileType"] == "png" and item["nodeId"] == "9" and item["consumeCoins"] == "1"
    assert item["fileUrl"].startswith(f"https://{host}/") and QUERY in item["fileUrl"]
    return body


def _install_query(monkeypatch, fake_rh, body: dict) -> None:
    async def query(task_id, api_key, domain=platforms.RUNNINGHUB_DEFAULT_DOMAIN):
        fake_rh._record("query_rh_task_outputs", task_id=task_id, domain=domain)
        return {"status": "SUCCESS", "outputs": body["data"], "raw": body}

    monkeypatch.setattr(platforms, "query_rh_task_outputs", query)


def _install_download(monkeypatch, *, allow: bool, host: str = HOST) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert allow, "拒绝的产物 URL 不得发起下载"
        assert request.url.host == host
        return httpx.Response(200, content=FIXED_PNG, headers={"content-type": "image/png"})

    monkeypatch.setattr(routes, "_output_transport", lambda: httpx.MockTransport(handler))


def _terminal(media_client, fake_rh, monkeypatch, *, allow_download: bool,
              path: Path = FIXTURE, host: str = HOST) -> tuple[dict, dict]:
    body = _fixture(path, host)
    _install_query(monkeypatch, fake_rh, body)
    _install_download(monkeypatch, allow=allow_download, host=host)
    _, workflow = prepare_workflow(media_client)
    started = execute(media_client, workflow, "rh_cloud", key="rh-output-hosts")
    assert started.status_code == 202, started.text
    started_body = started.json()
    task = media_client.get(f"{TASKS}/{started_body['job_id']}", headers=HEADERS)
    assert task.status_code == 200, task.text
    return started_body, task.json()


def _messages_hide_url(task: dict, host: str = HOST) -> None:
    dumped = json.dumps(task.get("messages"), ensure_ascii=False)
    assert QUERY not in dumped
    assert "fileUrl" not in dumped
    assert f"https://{host}" not in dumped


def test_unset_output_hosts_reports_not_configured(media_client, fake_rh, no_network, monkeypatch):
    """未设置 GW_WORKFLOW_OUTPUT_HOSTS 时给出可操作的主机诊断，且不下载。"""
    monkeypatch.delenv("GW_WORKFLOW_OUTPUT_HOSTS", raising=False)
    started, task = _terminal(media_client, fake_rh, monkeypatch, allow_download=False)
    assert task["status"] == "failed"
    assert task["execution_status"] == "completed"
    assert task["collection_status"] == "partial_failed"
    assert task["outputs"][0]["code"] == "OUTPUT_HOSTS_NOT_CONFIGURED"
    assert task["outputs"][0]["host"] == HOST
    assert "fileUrl" not in task["outputs"][0]
    assert f"OUTPUT_HOSTS_NOT_CONFIGURED host={HOST}" in task["messages"]
    assert any(line.startswith("OUTPUT_COLLECTION_INCOMPLETE:") for line in task["messages"])
    _messages_hide_url(task)
    assert started["job_id"]
    assert no_network == []


def test_official_output_host_collects_when_allowlisted(media_client, fake_rh, no_network, monkeypatch):
    """白名单包含官方产物主机时，文档形态的 fileUrl 可以登记。"""
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", HOST)
    _started, task = _terminal(media_client, fake_rh, monkeypatch, allow_download=True)
    assert task["status"] == "completed"
    assert task["collection_status"] == "completed"
    assert task["messages"] == []
    assert task["outputs"][0]["registered"] is True
    assert task["outputs"][0].get("asset_id")
    assert no_network == []


def test_non_allowed_host_keeps_partial_failure_message(media_client, fake_rh, no_network, monkeypatch):
    """已配置白名单但不含该主机时，保持 partial_failed，并写明未授权主机。"""
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", "approved.example")
    _started, task = _terminal(media_client, fake_rh, monkeypatch, allow_download=False)
    assert task["status"] == "failed"
    assert task["collection_status"] == "partial_failed"
    assert task["messages"]
    assert any(line.startswith("OUTPUT_COLLECTION_INCOMPLETE:") for line in task["messages"])
    assert f"OUTPUT_URL_NOT_ALLOWED host={HOST}" in task["messages"]
    assert task["outputs"][0]["host"] == HOST
    _messages_hide_url(task)
    assert no_network == []


def test_ac8_default_hosts_collect_real_host_output(media_client, fake_rh, no_network, monkeypatch):
    """GW_WORKFLOW_OUTPUT_HOSTS 为 AC8 默认两主机时，真实 tos 主机产物可登记并完成。"""
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", AC8_DEFAULT_HOSTS)
    _started, task = _terminal(media_client, fake_rh, monkeypatch, allow_download=True,
                               path=REAL_FIXTURE, host=REAL_HOST)
    assert task["status"] == "completed"
    assert task["collection_status"] == "completed"
    assert task["messages"] == []
    assert task["outputs"][0]["registered"] is True
    assert task["outputs"][0].get("asset_id")
    assert no_network == []


def test_real_host_output_rejected_with_only_docs_host(media_client, fake_rh, no_network, monkeypatch):
    """白名单只有文档主机（rh-images.xiaoyaoyou.com）时，真实 tos 主机产物被拒并写明主机。"""
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", HOST)
    _started, task = _terminal(media_client, fake_rh, monkeypatch, allow_download=False,
                               path=REAL_FIXTURE, host=REAL_HOST)
    assert task["status"] == "failed"
    assert task["collection_status"] == "partial_failed"
    assert task["messages"]
    assert any(line.startswith("OUTPUT_COLLECTION_INCOMPLETE:") for line in task["messages"])
    assert f"OUTPUT_URL_NOT_ALLOWED host={REAL_HOST}" in task["messages"]
    assert task["outputs"][0]["host"] == REAL_HOST
    _messages_hide_url(task, host=REAL_HOST)
    assert no_network == []


def test_recollect_after_host_config_does_not_resubmit(media_client, fake_rh, no_network, monkeypatch):
    """任务已是 failed/completed/partial_failed 时，POST /collect 只回收，不再次建任务。"""
    monkeypatch.delenv("GW_WORKFLOW_OUTPUT_HOSTS", raising=False)
    started, task = _terminal(media_client, fake_rh, monkeypatch, allow_download=False)
    assert task["status"] == "failed"
    assert task["execution_status"] == "completed"
    assert task["collection_status"] == "partial_failed"
    submits = fake_rh.count("create_rh_cloud_task")
    assert submits == 1
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", HOST)
    _install_download(monkeypatch, allow=True)
    collected = media_client.post(f"{TASKS}/{started['job_id']}/collect", headers=HEADERS)
    assert collected.status_code == 200, collected.text
    body = collected.json()
    assert body["status"] == "completed"
    assert body["collection_status"] == "completed"
    assert body["messages"] == []
    assert body["outputs"][0]["registered"] is True
    assert fake_rh.count("create_rh_cloud_task") == submits
    assert no_network == []
