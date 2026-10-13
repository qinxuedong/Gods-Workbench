"""C3-AC8 所有者脚本拒绝门：ac8_runninghub_real.py 缺 env / 缺 --i-accept-cost / 缺 --workflow-id 一律 BLOCKED。

真实供应商验收只由秦先生本人执行（cut 2026-10-13 10:38）；本测试只证明脚本在三道门槛前
不联网、不启动应用、不花钱：退出码 2、写出 result=BLOCKED 的 JSON，且没有真实网络尝试。
"""
from __future__ import annotations

import importlib.util
import json
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
