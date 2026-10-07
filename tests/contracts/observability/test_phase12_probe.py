"""Phase 12 探针回归：覆盖安全边界、分类器变异及全量 OpenAPI 盘点。"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROBE_PATH = ROOT / "tools" / "_probe_p12.py"
_SPEC = importlib.util.spec_from_file_location("phase12_probe", PROBE_PATH)
assert _SPEC and _SPEC.loader
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)


def test_path_parameters_are_mandatory_and_exactly_substituted():
    with pytest.raises(ValueError, match="路径参数未替换"):
        probe.render_path("/api/projects/{project_id}/entities")
    assert probe.render_path(
        "/api/projects/{project_id}/entities", {"project_id": "prj-0001"}
    ) == "/api/projects/prj-0001/entities"
    with pytest.raises(ValueError, match="无效"):
        probe.render_path("/api/projects/{project_id}", {"project_id": "prj/0001"})


def test_business_pass_rejects_empty_validation_stub_and_response_only_mutations():
    base = {
        "http_status": 201,
        "execution_context": "isolated_local_application",
        "readback_verified": True,
        "persistence_verified": True,
        "validation_only": False,
    }
    assert probe.business_pass(base) is True
    mutations = [
        {**base, "readback_verified": False},
        {**base, "persistence_verified": False},
        {**base, "http_status": 422},
        {**base, "request_body": {}, "readback_verified": False, "persistence_verified": False},
        {**base, "validation_only": True},
        {**base, "execution_context": "controlled_local_cli_stub"},
        {**base, "http_status": 200, "readback_verified": False, "persistence_verified": False},
    ]
    assert all(probe.business_pass(mutated) is False for mutated in mutations)


def test_attempt_attachment_rejects_unsubstituted_path_even_for_200():
    inventory = {
        ("GET", "/api/projects/{project_id}"): {
            "attempts": [], "disposition": "NOT_ASSESSED", "scope_decision": "IN_SCOPE",
        }
    }
    response = type("Response", (), {"status_code": 200, "json": lambda self: {"ok": True}})()
    observation = probe._new_observation(
        method="GET", path="/api/projects/{project_id}", request_body=None,
        query={}, response=response, execution_context="isolated_local_application",
        classification="OBSERVED_RESPONSE_NOT_BUSINESS_VERIFIED",
    )
    with pytest.raises(ValueError, match="未替换路径参数"):
        probe._attach_attempt(inventory, ("GET", "/api/projects/{project_id}"), observation)
    assert inventory[("GET", "/api/projects/{project_id}")]["attempts"] == []


def test_unavailable_classification_separates_code_config_dependency_and_stub():
    classify = probe.classify_unavailable
    assert classify("CLI_HELP_NOT_INTEGRATED", config_enabled=False,
                    dependency_available=None) == "CONFIGURATION_MISSING"
    assert classify("CLI_HELP_NOT_INTEGRATED", config_enabled=True,
                    dependency_available=False) == "DEPENDENCY_MISSING"
    assert classify("OTHER_NOT_INTEGRATED", config_enabled=False,
                    dependency_available=None) == "CODE_MISSING"
    assert classify("CLI_HELP_NOT_INTEGRATED", config_enabled=True,
                    dependency_available=True, local_stub=True) == "LOCAL_STUB_ONLY_NOT_UPSTREAM"
    # 变异守卫：全局 CLI 配置关闭不能把 renderer 缺失伪装成配置缺失。
    assert classify("VIDEO_RENDERER_NOT_INTEGRATED", config_enabled=False,
                    dependency_available=None) == "CODE_MISSING"


def test_video_renderer_is_never_a_canvas_exclusion():
    canvas_exclusions, _ = probe._contract_operations(ROOT)
    assert probe.VIDEO_RENDER_CREATE not in canvas_exclusions
    sample_spec = {
        "paths": {
            "/api/video-tasks": {"post": {"summary": "创建视频任务"}},
            "/api/video-exports": {"post": {"summary": "创建本地视频导出"}},
        },
        "components": {"schemas": {}},
    }
    inventory = {
        probe.VIDEO_RENDER_CREATE: {
            "method": "POST", "path": "/api/video-tasks", "attempts": [],
            "disposition": "NOT_ASSESSED", "scope_decision": "IN_SCOPE_OR_UNRESOLVED",
        }
    }
    probe._mark_static_dispositions(inventory, ROOT)
    video = inventory[probe.VIDEO_RENDER_CREATE]
    assert video["disposition"] == "NOT_EXECUTED_SAFETY"
    assert video["scope_decision"] == "IN_SCOPE_USER_AUTHORIZED_IMPLEMENTED_PENDING_AUDIT"
    assert not video["attempts"]  # 不触发可能计费或改变状态的请求。
    gaps = {item["capability"]: item for item in probe._audit_required_capabilities(sample_spec)}
    assert gaps["video_render_export"]["disposition"] == "IMPLEMENTATION_REQUIRES_SEPARATE_CONTRACT_AUDIT"
    assert gaps["video_render_export"]["scope_decision"] == "IN_SCOPE_USER_AUTHORIZED_IMPLEMENTED_PENDING_AUDIT"


def test_newly_authorized_gaps_are_reported_separately_from_openapi_operations():
    spec = {"paths": {}, "components": {"schemas": {}}}
    gaps = {item["capability"]: item for item in probe._audit_required_capabilities(spec)}
    assert set(gaps) == {
        "video_render_export",
        "team_messaging_send_and_history",
        "provider_tokens_per_second_metric",
    }
    assert gaps["video_render_export"]["disposition"] == "CODE_MISSING"
    assert gaps["team_messaging_send_and_history"]["disposition"] == "CODE_MISSING"
    # 任意字典响应可能没有 OpenAPI 属性；缺 schema 不能证明功能不存在。
    assert gaps["provider_tokens_per_second_metric"]["scope_decision"] == "IN_SCOPE_USER_AUTHORIZED_UNVERIFIED"
    assert gaps["provider_tokens_per_second_metric"]["disposition"] == "NOT_ASSESSED"


def test_required_capability_scans_detect_mutated_team_and_throughput_contracts():
    spec = {
        "paths": {
            "/api/teams/{team_id}/messages": {
                "post": {"summary": "发送团队消息"},
                "get": {"summary": "读取团队消息历史"},
            }
        },
        "components": {"schemas": {"ProviderMetrics": {
            "type": "object", "properties": {"tokens_per_second": {"type": "number"}},
        }}},
    }
    gaps = {item["capability"]: item for item in probe._audit_required_capabilities(spec)}
    assert gaps["team_messaging_send_and_history"]["matching_openapi_operations"] == [
        {"method": "POST", "path": "/api/teams/{team_id}/messages"},
        {"method": "GET", "path": "/api/teams/{team_id}/messages"},
    ]
    assert gaps["team_messaging_send_and_history"]["scope_decision"] == "IN_SCOPE_USER_AUTHORIZED_IMPLEMENTED_PENDING_AUDIT"
    assert gaps["team_messaging_send_and_history"]["disposition"] == "IMPLEMENTATION_REQUIRES_SEPARATE_CONTRACT_AUDIT"
    assert gaps["provider_tokens_per_second_metric"]["matching_schema_fields"] == ["tokens_per_second"]
    assert gaps["provider_tokens_per_second_metric"]["scope_decision"] == "IN_SCOPE_USER_AUTHORIZED_UNVERIFIED"
    assert gaps["provider_tokens_per_second_metric"]["disposition"] == "NOT_ASSESSED"
    assert gaps["video_render_export"]["related_openapi_operations"] == []


def test_schema_only_throughput_evidence_does_not_claim_runtime_success():
    """单一 schema 字段只能证明字段命中，不能伪造运行测量。"""
    spec = {
        "paths": {},
        "components": {"schemas": {"OnlyMetricField": {
            "type": "object", "properties": {"tokens_per_second": {"type": "number"}},
        }}},
    }
    metric = next(
        item for item in probe._audit_required_capabilities(spec)
        if item["capability"] == "provider_tokens_per_second_metric"
    )

    assert metric["matching_schema_fields"] == ["tokens_per_second"]
    assert metric["scope_decision"] == "IN_SCOPE_USER_AUTHORIZED_UNVERIFIED"
    assert metric["runtime_evidence"] == {
        "status": "NOT_EXECUTED",
        "verified": False,
        "formula_verified": False,
        "missing_usage_verified": False,
        "commercial_provider_verified": False,
        "request_count": 0,
        "metrics": None,
        "missing_usage_metrics": None,
        "acceptance_level": "not_executed",
    }


def test_sensitive_response_data_is_redacted_recursively():
    source = {
        "detail": {"access_token": "secret-token", "message": "Authorization: Bearer abc.def"},
        "nested": [{"password": "dont-store"}],
    }
    redacted = probe.redact_payload(source)
    assert redacted["detail"]["access_token"] == "[已脱敏]"
    assert "abc.def" not in redacted["detail"]["message"]
    assert redacted["nested"][0]["password"] == "[已脱敏]"
    assert source["nested"][0]["password"] == "dont-store"  # 不原地改写调用方对象。


def test_isolated_environment_restores_after_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    names = ("GW_DATA_DIR", "GW_LOCAL_AUTH_DB", "GW_AUTH_MODE", "GW_ALLOWED_ROOTS",
             "GW_CLI_EXECUTION", "LOCALAPPDATA")
    monkeypatch.setenv("GW_CLI_EXECUTION", "1")
    before = {name: os.environ.get(name) for name in names}
    monitor: dict[str, Any] = {}
    with pytest.raises(RuntimeError, match="injected"):
        with probe._isolated_environment(tmp_path / "isolated", monitor):
            assert os.environ["GW_CLI_EXECUTION"] == "0"
            assert Path(os.environ["GW_DATA_DIR"]).is_relative_to(tmp_path)
            raise RuntimeError("injected")
    assert {name: os.environ.get(name) for name in names} == before
    assert monitor["environment_restored"] is True


def test_network_and_process_guards_block_then_restore(monkeypatch: pytest.MonkeyPatch):
    original_run = subprocess.run
    original_connect = socket.create_connection
    monitor = {"network_attempts_blocked": 0, "real_process_attempts_blocked": 0}
    with pytest.raises(RuntimeError, match="Phase 12"):
        with probe._side_effect_guard(monitor):
            socket.create_connection(("example.invalid", 80))
    assert monitor["network_guard_installed"] is True
    assert monitor["network_attempts_blocked"] == 1
    assert monitor["side_effect_guards_restored"] is True
    assert subprocess.run is original_run
    assert socket.create_connection is original_connect

    with pytest.raises(RuntimeError, match="Phase 12"):
        with probe._side_effect_guard(monitor):
            subprocess.run(["definitely-not-a-real-command"])
    assert monitor["real_process_attempts_blocked"] == 1
    assert monitor["side_effect_guards_restored"] is True
    assert subprocess.run is original_run


def test_local_cli_stub_restores_environment_and_monkeypatch(monkeypatch: pytest.MonkeyPatch):
    from gw.core import cli_runtime

    class FailingClient:
        def request(self, *args: Any, **kwargs: Any):
            raise RuntimeError("injected client failure")

    key = ("POST", "/api/codex/help")
    inventory = {key: {"attempts": [], "disposition": "NOT_ASSESSED", "scope_decision": "NOT_ASSESSED"}}
    monitor = {"local_cli_stub_calls": 0}
    monkeypatch.setenv("GW_CLI_EXECUTION", "0")
    before_find = cli_runtime.find_cli
    before_run = cli_runtime.subprocess.run
    with pytest.raises(RuntimeError, match="injected"):
        probe._run_local_cli_stubs(FailingClient(), inventory, {}, monitor)
    assert os.environ["GW_CLI_EXECUTION"] == "0"
    assert cli_runtime.find_cli is before_find
    assert cli_runtime.subprocess.run is before_run
    assert monitor["local_stub_environment_restored"] is True
    assert monitor["local_stub_patches_restored"] is True
    assert monitor["local_cli_stub_calls"] == 0


def _run_probe_once(output_dir: Path) -> tuple[dict[str, Any], str]:
    environment = os.environ.copy()
    # 故意继承“配置已启用”的值，验证探针强制安全隔离并在退出时还原。
    environment["GW_CLI_EXECUTION"] = "1"
    environment["PYTHONUTF8"] = "1"
    prior_reports = set(output_dir.glob("phase12-probe-*.json"))
    result = subprocess.run(
        [sys.executable, str(PROBE_PATH), "--output-dir", str(output_dir)],
        cwd=ROOT, env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    reports = set(output_dir.glob("phase12-probe-*.json")) - prior_reports
    assert len(reports) == 1
    report_path = reports.pop()
    return json.loads(report_path.read_text(encoding="utf-8")), result.stdout


def test_executable_probe_records_full_inventory_unique_output_and_real_gaps(tmp_path: Path):
    report_a, stdout_a = _run_probe_once(tmp_path)
    report_b, stdout_b = _run_probe_once(tmp_path)
    files = sorted(tmp_path.glob("phase12-probe-*.json"))
    assert len(files) == 2
    assert files[0].name != files[1].name
    assert "完整唯一 JSON" in stdout_a and "完整唯一 JSON" in stdout_b

    for report in (report_a, report_b):
        summary = report["summary"]
        operations = report["operations"]
        keys = {(item["method"], item["path"]) for item in operations}
        assert len(keys) == summary["openapi_operation_count"]
        assert summary["operation_count_with_attempts"] == 0
        assert summary["unexecuted_operation_count"] == summary["openapi_operation_count"]
        assert summary["business_verified_operation_count"] == 0
        assert summary["business_verified_flow_count"] == 0
        assert summary["route_readback_only_operation_count"] == 0
        assert summary["validation_only_attempt_count"] == 0
        assert summary["disposition_counts"].get("CODE_MISSING", 0) == 0
        assert summary["disposition_counts"].get("CONFIGURATION_MISSING", 0) == 4

        safety = report["safety"]
        assert safety["status"] == "NOT_EXECUTED"
        assert safety["data_directory_isolated"] is False
        assert safety["network_guard_active"] is False
        assert safety["process_guard_active"] is False
        assert safety["only_loopback_network_allowed"] is False
        assert safety["network_attempts_blocked"] == 0
        assert safety["external_network_calls_made"] is False
        assert safety["paid_provider_calls_made"] is False
        assert safety["real_cli_processes_started"] == 0
        assert safety["real_process_attempts_blocked"] == 0
        assert safety["local_cli_stub_calls"] == 0
        assert safety["local_cli_stub_is_upstream_evidence"] is False
        assert safety["login_logout_operations_executed"] is False
        assert safety["response_bodies_redacted"] is True
        assert safety["environment_restored_after_probe"] is False
        assert safety["monkeypatches_restored_after_probe"] is False
        assert safety["temporary_json_output_unique"] is True

        video = next(item for item in operations if (item["method"], item["path"]) == probe.VIDEO_RENDER_CREATE)
        assert video["disposition"] == "NOT_EXECUTED_SAFETY"
        assert video["scope_decision"] == "IN_SCOPE_USER_AUTHORIZED_IMPLEMENTED_PENDING_AUDIT"
        assert video["attempts"] == []
        assert video["evidence_source"] == "docs/contracts/VIDEO-TASKS-INTERFACE-CATALOG.yaml"

        gaps = {item["capability"]: item for item in report["additional_required_capabilities"]}
        assert len(gaps) == 3
        assert gaps["video_render_export"]["scope_decision"] == "IN_SCOPE_USER_AUTHORIZED_IMPLEMENTED_PENDING_AUDIT"
        assert gaps["provider_tokens_per_second_metric"]["scope_decision"] == "IN_SCOPE_USER_AUTHORIZED_IMPLEMENTED_PENDING_AUDIT"
        assert gaps["team_messaging_send_and_history"]["scope_decision"] == "IN_SCOPE_USER_AUTHORIZED_IMPLEMENTED_PENDING_AUDIT"
        assert gaps["video_render_export"]["disposition"] == "IMPLEMENTATION_REQUIRES_SEPARATE_CONTRACT_AUDIT"
        assert gaps["team_messaging_send_and_history"]["disposition"] == "IMPLEMENTATION_REQUIRES_SEPARATE_CONTRACT_AUDIT"
        assert gaps["team_messaging_send_and_history"]["matching_openapi_operations"] == [
            {"method": "GET", "path": "/api/asset-auth/teams/{team_id}/messages"},
            {"method": "POST", "path": "/api/asset-auth/teams/{team_id}/messages"},
        ]
        assert gaps["provider_tokens_per_second_metric"]["disposition"] == "NOT_EXECUTED_SAFETY"
        throughput = gaps["provider_tokens_per_second_metric"]["runtime_evidence"]
        assert throughput["status"] == "NOT_EXECUTED"
        assert throughput["verified"] is False
        assert throughput["formula_verified"] is False
        assert throughput["missing_usage_verified"] is False
        assert throughput["commercial_provider_verified"] is False
        assert throughput["request_count"] == 0
        assert throughput["metrics"] is None
        assert throughput["missing_usage_metrics"] is None
        protocol_operations = [row for row in operations if row["path"] in {"/api/chat", "/api/chat/agent"}]
        assert len(protocol_operations) == 2
        assert all(row["attempts"] == [] for row in protocol_operations)
        assert gaps["provider_tokens_per_second_metric"]["matching_schema_fields"] == []

        attempts = [attempt for operation in operations for attempt in operation["attempts"]]
        assert attempts == []

        asset_flow = report["persistence_flows"]["asset_import_list_readback"]
        assert asset_flow["persistence_verified"] is False
        assert asset_flow["asset_id"] is None
        project_flow = report["persistence_flows"]["project_create_update_route_readback"]
        assert project_flow["route_readback_verified"] is False
        assert project_flow["persistence_verified"] is False
        assert project_flow["acceptance_level"] == "not_executed"



def test_redaction_preserves_numeric_metrics_but_not_credentials():
    value = probe.redact_payload({"output_tokens": 12, "tokens_per_second": 6.0,
                                 "api_token": "private-value", "refresh_token": "private-value"})
    assert value["output_tokens"] == 12 and value["tokens_per_second"] == 6.0
    assert "private-value" not in str(value)
