"""C3-AC8 所有者脚本拒绝门：ac8_runninghub_real.py 缺 env / 缺 --i-accept-cost / 缺 --workflow-id 一律 BLOCKED。

真实供应商验收只由秦先生本人执行（cut 2026-10-13 10:38）；本测试只证明脚本在三道门槛前
不联网、不启动应用、不花钱：退出码 2、写出 result=BLOCKED 的 JSON，且没有真实网络尝试。
越过门槛的用例用进程内替身客户端，不启动应用、不联网。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from media_doubles import TEST_API_KEY

SCRIPT = Path(__file__).resolve().parents[3] / "tools/c3_acceptance/ac8_runninghub_real.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("ac8_runninghub_real", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(monkeypatch, tmp_path, *argv):
    module = _load_script()
    out = tmp_path / "ac8-result.json"
    monkeypatch.setattr(sys, "argv", ["ac8_runninghub_real.py", "--out", str(out), *argv])
    code = module.main()
    assert out.is_file(), "拒绝时也必须写出结果 JSON"
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["result"] == "BLOCKED", body
    assert body["steps"] == []  # 门槛阶段不启动应用、不发起任何请求
    return code, body


@pytest.mark.parametrize("argv,env_key", [
    ([], None),                                   # 无 key、无其余门槛
    ([], TEST_API_KEY),                           # 有 key 但无 --i-accept-cost / --workflow-id
    (["--i-accept-cost"], TEST_API_KEY),          # 有 key、接受计费，但无 --workflow-id
    (["--i-accept-cost", "--workflow-id", "1234"], TEST_API_KEY),  # workflow-id 格式不合法
])
def test_owner_script_refuses_without_gates(monkeypatch, tmp_path, no_network, argv, env_key):
    """C3-AC8-OWNER-GATE-01：三道门槛缺一即退出码 2，零网络尝试。"""
    monkeypatch.delenv("GW_RUNNINGHUB_API_KEY", raising=False)
    if env_key is not None:
        monkeypatch.setenv("GW_RUNNINGHUB_API_KEY", env_key)
    code, body = _run(monkeypatch, tmp_path, *argv)
    assert code == 2
    assert no_network == []
    if env_key is None:
        assert body["reason"] == "env_key_missing"
    elif not argv:
        assert body["reason"] == "cost_not_accepted"
    elif len(argv) == 1:
        assert body["reason"] == "workflow_id_required"
    else:
        assert body["reason"] == "workflow_id_required"


def test_owner_script_result_json_never_contains_key(monkeypatch, tmp_path, no_network):
    """C3-AC8-OWNER-GATE-02：结果 JSON 与输出路径由 --out 决定，key 值绝不落盘。"""
    monkeypatch.delenv("GW_RUNNINGHUB_API_KEY", raising=False)
    code, body = _run(monkeypatch, tmp_path)
    dumped = json.dumps(body, ensure_ascii=False)
    assert TEST_API_KEY not in dumped
    assert "GW_RUNNINGHUB_API_KEY" in dumped or body["reason"] == "env_key_missing"


_WORKFLOW_ID = "1234567890"
_ENV_KEYS = ("GW_RUNTIME_MODE", "GW_AUTH_MODE", "GW_DATA_DIR", "GW_LOCAL_AUTH_DB",
             "GW_VIDEO_DATA_DIR", "GW_DISABLE_AGENT_INTEGRATION")


class _FakeResponse:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text if body is None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise json.JSONDecodeError("Expecting value", self.text or "", 0)
        return self._body


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url))
        return self.responses.pop(0)

    def get(self, url, headers=None):
        self.calls.append(("GET", url))
        return self.responses.pop(0)


class _ClientContext:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *args):
        return False


def _install_doubles(monkeypatch, client):
    monkeypatch.setattr("gw.api.app.create_app", lambda: object())
    monkeypatch.setattr("fastapi.testclient.TestClient", lambda app: _ClientContext(client))


def _run_past_gates(monkeypatch, tmp_path, client, *argv):
    """越过三道门槛后用替身客户端跑脚本，并还原脚本写入的环境变量。"""
    module = _load_script()
    out = tmp_path / "ac8-result.json"
    monkeypatch.delenv("GW_RUNNINGHUB_API_KEY", raising=False)
    monkeypatch.setenv("GW_RUNNINGHUB_API_KEY", TEST_API_KEY)
    monkeypatch.setattr(sys, "argv", ["ac8_runninghub_real.py", "--out", str(out),
                                      "--i-accept-cost", "--workflow-id", _WORKFLOW_ID, *argv])
    _install_doubles(monkeypatch, client)
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    try:
        code = module.main()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    body = json.loads(out.read_text(encoding="utf-8"))
    assert TEST_API_KEY not in json.dumps(body, ensure_ascii=False)
    return code, body


@pytest.mark.parametrize("status_code", [200, 201])
def test_import_success_proceeds_past_import(monkeypatch, tmp_path, no_network, status_code):
    """C3-AC8-OWNER-IMPORT-01：parse-link 返回 200 或 201 都视为导入成功并继续执行。"""
    client = _FakeClient([
        _FakeResponse(status_code, {"workflow_id": "wf-ac8", "version": 3}),
        _FakeResponse(400, {"detail": {"code": "EXECUTE_REJECTED"}}),
    ])
    code, body = _run_past_gates(monkeypatch, tmp_path, client)
    assert code == 1
    assert body["result"] == "FAILED"
    assert body["reason"] == "execute_rejected"
    assert body["workflow_id"] == "wf-ac8"
    assert [step["step"] for step in body["steps"]] == ["import_workflow", "execute_task"]
    assert body["steps"][0]["http_status"] == status_code
    assert body["steps"][1]["code"] == "EXECUTE_REJECTED"
    assert no_network == []


@pytest.mark.parametrize("status_code,body,text,expect_code", [
    (400, {"detail": {"code": "PARSE_FAILED"}}, "", "PARSE_FAILED"),
    (400, {"detail": "plain string"}, "", None),
    (400, None, "<html>not json</html>", None),
])
def test_import_failure_is_rejected_without_crashing(monkeypatch, tmp_path, no_network,
                                                     status_code, body, text, expect_code):
    """C3-AC8-OWNER-IMPORT-02：导入 400 记 import_rejected；非 JSON 或 detail 为字符串时不崩溃。"""
    client = _FakeClient([_FakeResponse(status_code, body, text)])
    code, result = _run_past_gates(monkeypatch, tmp_path, client)
    assert code == 1
    assert result["result"] == "FAILED"
    assert result["reason"] == "import_rejected"
    assert result["steps"] == [{"step": "import_workflow", "http_status": status_code, "code": expect_code}]
    assert no_network == []


@pytest.mark.parametrize("status_code", [200, 201, 202])
def test_execute_outcome_unknown_records_messages(monkeypatch, tmp_path, no_network, status_code):
    """C3-AC8-OWNER-EXEC-01：execute 的 2xx 成功码放行；outcome_unknown 把任务消息写入结果。"""
    messages = ["RH_SUBMIT_INTERNAL_ERROR", "exception: UnicodeEncodeError"]
    task = {"job_id": "job-ac8", "status": "outcome_unknown", "collection_status": "pending",
            "messages": messages, "remote_task_id": None}
    client = _FakeClient([
        _FakeResponse(201, {"workflow_id": "wf-ac8", "version": 1}),
        _FakeResponse(status_code, task),
        _FakeResponse(200, task),
    ])
    code, body = _run_past_gates(monkeypatch, tmp_path, client)
    assert code == 1
    assert body["result"] == "FAILED"
    assert body["reason"] == "task_not_completed"
    assert body["messages"] == messages
    assert body["steps"][1]["http_status"] == status_code
    assert body["steps"][1]["status"] == "outcome_unknown"
    assert no_network == []
