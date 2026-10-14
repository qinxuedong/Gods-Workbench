"""C3-AC8 所有者脚本拒绝门：ac8_runninghub_real.py 缺 env / 缺 --i-accept-cost / 缺 --workflow-id 一律 BLOCKED。

真实供应商验收只由秦先生本人执行（cut 2026-10-13 10:38）；本测试只证明脚本在三道门槛前
不联网、不启动应用、不花钱：退出码 2、写出 result=BLOCKED 的 JSON，且没有真实网络尝试。
另覆盖：导入被拒时记录并打印上游 provider_code，以及启动时 stdout/stderr 强制 UTF-8。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
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


class _RejectedResponse:
    status_code = 502
    text = '{"detail": {"code": "RH_SOURCE_REJECTED", "provider_code": "380"}}'

    @staticmethod
    def json():
        return {"detail": {"code": "RH_SOURCE_REJECTED",
                           "message": "RunningHub 已配置的工作流源请求被拒绝（RunningHub 错误码 380）；"
                                      "请检查凭据、源权限与链接，或上传原始工作流 JSON",
                           "provider_code": "380"}}


class _RejectedClient:
    """替身 TestClient：导入步骤返回带 provider_code 的 502，其余请求不得发生。"""

    def __init__(self, app=None, **kwargs):
        assert app is not None, "必须传入应用工厂结果"
        self.app = app

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        assert url.endswith("/parse-link"), f"导入被拒后不得继续: {url}"
        return _RejectedResponse()


class _StreamSpy:
    """包装真实流：记录 reconfigure 调用，其余行为全部委托。"""

    def __init__(self, inner):
        self._inner = inner
        self.reconfigure_calls = []

    def reconfigure(self, **kwargs):
        self.reconfigure_calls.append(kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_owner_script_records_and_prints_provider_code_on_import_rejection(
        monkeypatch, tmp_path, no_network, capsys):
    """C3-AC8-OWNER-GATE-03：导入被 RH 拒绝（380）时，结果记录并打印上游错误码，不打印 key。"""
    monkeypatch.setenv("GW_RUNNINGHUB_API_KEY", TEST_API_KEY)
    monkeypatch.setitem(sys.modules, "fastapi.testclient",
                        types.SimpleNamespace(TestClient=_RejectedClient))
    import gw.api.app as app_module
    monkeypatch.setattr(app_module, "create_app", lambda: object(), raising=True)
    out = tmp_path / "ac8-result.json"
    monkeypatch.setattr(sys, "argv", ["ac8_runninghub_real.py", "--out", str(out), "--i-accept-cost",
                                      "--workflow-id", "2108837566177959938"])
    snapshot = dict(os.environ)
    try:
        code = _load_script().main()
    finally:
        os.environ.clear()
        os.environ.update(snapshot)
    assert code == 1
    assert no_network == []
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["result"] == "FAILED" and body["reason"] == "import_rejected"
    step = body["steps"][0]
    assert step["step"] == "import_workflow" and step["http_status"] == 502
    assert step["code"] == "RH_SOURCE_REJECTED"
    assert step["provider_code"] == "380"
    printed = capsys.readouterr().out
    assert "FAILED: RunningHub 拒绝（RH_SOURCE_REJECTED，上游错误码 380）" in printed
    assert TEST_API_KEY not in printed and "secret-msg" not in printed


def test_owner_script_reconfigures_stdout_stderr_to_utf8(monkeypatch, tmp_path, capsys):
    """C3-AC8-OWNER-GATE-04：main() 一进入就把 stdout/stderr 重配置为 UTF-8（Windows 中文输出）。"""
    monkeypatch.delenv("GW_RUNNINGHUB_API_KEY", raising=False)
    stdout_spy = _StreamSpy(sys.stdout)
    stderr_spy = _StreamSpy(sys.stderr)
    monkeypatch.setattr(sys, "stdout", stdout_spy)
    monkeypatch.setattr(sys, "stderr", stderr_spy)
    code, _body = _run(monkeypatch, tmp_path)
    assert code == 2
    assert stdout_spy.reconfigure_calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stderr_spy.reconfigure_calls == [{"encoding": "utf-8", "errors": "replace"}]
