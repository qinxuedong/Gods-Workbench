# -*- coding: utf-8 -*-
"""Phase 10B 观测契约测试。

覆盖 docs/contracts/OBSERVABILITY-INTERFACE-CATALOG.yaml（p12-a4-1）的 8 个 GET 端点：

- GET /api/observability             资源索引 200 形状
- GET /api/observability/overview    真实计数 / 未接入指标为 null
- GET /api/observability/series      空序列 + not_integrated
- GET /api/observability/events      真实审计事件 / 空数组
- GET /api/observability/tasks       真实内存任务
- GET /api/observability/health      如实状态（含 not_integrated）
- GET /api/observability/sources     空数组 + not_integrated
- GET /api/observability/asset-volumes 空数组 + not_integrated

重点守卫：
1. 8 个端点的 200 形状与 **401** 认证边界（错误码复用 UnauthorizedException）；
2. 空 / not_integrated 的**诚实语义**（不得返回演示或伪造数据）；
3. 分页参数（limit / cursor / next_cursor / has_more）真实生效；
4. **反向断言**：响应中不得出现伪造字段（cpu/ram/random 波形、假任务、假事件）。

隔离策略：按用例注入全新服务实例，避免跨用例状态泄漏。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_PATH = REPO_ROOT
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from gw.api import routes_observability as obs_routes  # noqa: E402
from gw.api.app import create_app  # noqa: E402
from gw.asset_library.service import AssetLibraryService  # noqa: E402
from gw.core import audit as audit_log  # noqa: E402
from gw.god_canvas.service import GodCanvasService  # noqa: E402
from gw.observability.service import ObservabilityService  # noqa: E402
from gw.projects_hub.service import ProjectsService  # noqa: E402

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
READONLY = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "readonly"}

ENDPOINTS = (
    "/api/observability",
    "/api/observability/overview",
    "/api/observability/series",
    "/api/observability/events",
    "/api/observability/tasks",
    "/api/observability/health",
    "/api/observability/sources",
    "/api/observability/asset-volumes",
)

CATALOG = REPO_ROOT / "docs" / "contracts" / "OBSERVABILITY-INTERFACE-CATALOG.yaml"

#: 反向断言用：任何响应都不得出现的伪造遥测字段名。
# 2026-09-26 Phase 12 A4：cpu_percent / memory_percent / disk_percent 已由真实 psutil 读数承载，
# 不再属于伪造字段；本清单只保留**无真实来源**的字段名。
FORBIDDEN_FAKE_FIELDS = (
    "cpu_usage",
    "ram_usage",
    "gpu_usage",
    "fake",
    "demo",
    "mock_data",
    "random",
    "synthetic",
    "sample_task",
)

#: 反向断言用：任何响应都不得出现的敏感字段名。
SENSITIVE_FIELDS = (
    "access_token",
    "refresh_token",
    "id_token",
    "authorization_code",
    "code_verifier",
    "client_secret",
    "state",
    "nonce",
    "cookie",
    "set-cookie",
)


def _make_service(*, seed_projects: bool = False, seed_canvas: bool = False) -> ObservabilityService:
    """构造注入式观测服务；默认全部为空内存状态。"""
    return ObservabilityService(
        projects_service=ProjectsService(seed_golden_fixture=seed_projects),
        canvas_service=GodCanvasService(seed_golden_fixture=seed_canvas),
        asset_library_service=AssetLibraryService(seed_golden_fixture=False),
    )


@pytest.fixture()
def empty_client(monkeypatch) -> TestClient:
    """空内存状态：项目 0、任务 0、审计空。"""
    audit_log.reset_audit_log()
    monkeypatch.setattr(obs_routes, "default_observability_service", _make_service())
    with TestClient(create_app()) as client:
        yield client
    audit_log.reset_audit_log()


@pytest.fixture()
def seeded_client(monkeypatch) -> TestClient:
    """注入黄金夹具种子：1 个项目、1 个 accepted 任务。"""
    audit_log.reset_audit_log()
    from gw.core.auth import require_authenticated
    from gw.projects_hub import service as projects
    from gw.projects_hub.models import ProjectCreateRequest
    from gw.god_canvas.tasks import SmartCanvasTaskRequest
    service = _make_service(seed_projects=False, seed_canvas=True)
    owner = projects.owner_key_for_context(require_authenticated("Bearer cleanroom-test", "editor"))
    service._projects.create_project(ProjectCreateRequest(name="观测归属项目", project_type="other"), owner_key=owner)
    monkeypatch.setattr(projects, "default_projects_service", service._projects)
    service._canvas.submit_smart_task("cv-0001", SmartCanvasTaskRequest(entry_nodes=["nd-0001"]), authorization="Bearer cleanroom-test")
    monkeypatch.setattr(obs_routes, "default_observability_service", service)
    with TestClient(create_app()) as client:
        yield client
    audit_log.reset_audit_log()


def _walk(value):
    """深度遍历响应结构，产出 (路径, 键, 值) 三元组，便于反向断言。"""
    if isinstance(value, dict):
        for key, item in value.items():
            yield (), key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _all_keys(payload) -> list:
    return [key for _, key, _ in _walk(payload)]


def _assert_no_fabrication(payload) -> None:
    """反向断言：不得出现伪造遥测字段，也不得出现敏感字段。"""
    keys = {str(key).lower() for key in _all_keys(payload)}
    for forbidden in FORBIDDEN_FAKE_FIELDS:
        assert forbidden not in keys, f"响应出现疑似伪造字段: {forbidden}"
    for sensitive in SENSITIVE_FIELDS:
        assert sensitive not in keys, f"响应出现敏感字段: {sensitive}"


# ---------------------------------------------------------------------------
# 契约文件本身
# ---------------------------------------------------------------------------

def test_catalog_declares_eight_get_endpoints_and_zero_fabrication_rule():
    """契约必须声明 8 个 GET 端点，并显式写入「空 + not_integrated，禁止伪造」。"""
    text = CATALOG.read_text(encoding="utf-8")
    pairs = re.findall(r"method:\s*(\w+)\s*\n\s*path:\s*(\S+)", text)
    assert len(pairs) == 8, f"契约端点数量应为 8，实际 {len(pairs)}"
    assert {method for method, _ in pairs} == {"GET"}, "观测契约不得声明非 GET 方法"
    assert {route for _, route in pairs} == set(ENDPOINTS), "契约端点集合与实现范围不一致"
    assert "not_integrated" in text
    assert "禁止伪造" in text, "契约必须显式写明禁止伪造"


def test_catalog_declares_no_unauthorized_endpoints():
    """契约不得授权观测阶段之外的端点（含 asset-library 明细）。"""
    text = CATALOG.read_text(encoding="utf-8")
    routes = {route for _, route in re.findall(r"method:\s*(\w+)\s*\n\s*path:\s*(\S+)", text)}
    for route in routes:
        assert route.startswith("/api/observability"), f"越权声明端点: {route}"


# ---------------------------------------------------------------------------
# 200 形状 + 401
# ---------------------------------------------------------------------------

def test_all_eight_endpoints_return_200_with_expected_shape(seeded_client: TestClient):
    """8 个端点均须 200，且返回契约要求的关键字段。"""
    expected_keys = {
        "/api/observability": {"service", "contract_version", "generated_at", "data_status", "resources"},
        "/api/observability/overview": {"summary", "projects", "jobs", "data_status", "data_gaps"},
        "/api/observability/series": {"series", "metrics", "data_status", "data_gap"},
        "/api/observability/events": {"items", "total", "limit", "has_more", "data_status"},
        "/api/observability/tasks": {"items", "total", "limit", "has_more", "data_status"},
        "/api/observability/health": {"status", "checks", "data_status", "checked_at"},
        "/api/observability/sources": {"items", "total", "limit", "has_more", "data_status"},
        "/api/observability/asset-volumes": {"items", "total", "limit", "has_more", "data_status"},
    }
    for path in ENDPOINTS:
        res = seeded_client.get(path, headers=AUTH)
        assert res.status_code == 200, f"{path} 期望 200，实际 {res.status_code}: {res.text}"
        body = res.json()
        assert expected_keys[path] <= set(body), f"{path} 缺少字段: {expected_keys[path] - set(body)}"
        assert body["data_status"] in {"ok", "degraded", "not_integrated"}
        _assert_no_fabrication(body)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_endpoint_requires_authentication(empty_client: TestClient, path: str):
    """未认证访问必须 401，错误码为纯 ASCII 小写 unauthorized。"""
    res = empty_client.get(path)
    assert res.status_code == 401, f"{path} 未认证应为 401，实际 {res.status_code}"
    detail = res.json()["detail"]
    assert detail["code"].lower() == "unauthorized"
    assert detail["code"].isascii(), "401 错误码必须为纯 ASCII"


@pytest.mark.parametrize("path", ENDPOINTS)
def test_readonly_role_can_read(empty_client: TestClient, path: str):
    """只读角色必须可读观测端点（本阶段不要求写权限）。"""
    res = empty_client.get(path, headers=READONLY)
    assert res.status_code == 200, f"{path} 只读角色应可读，实际 {res.status_code}"


def test_unknown_role_is_rejected(empty_client: TestClient):
    """未知角色必须失败关闭（403），不得静默降级为只读。"""
    res = empty_client.get(
        "/api/observability",
        headers={"Authorization": "Bearer cleanroom-test", "X-User-Role": "root"},
    )
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# 诚实空语义 / not_integrated
# ---------------------------------------------------------------------------

def test_series_reads_real_samples_or_degrades(empty_client: TestClient):
    """Phase 12 A4：series 读取真实采样；不足 2 点必须 degraded 且不插值。"""
    body = empty_client.get("/api/observability/series?metrics=cpu_percent", headers=AUTH).json()
    assert body["data_status"] in {"ok", "degraded"}
    assert isinstance(body["metrics"], list) and body["metrics"]
    metric = body["metrics"][0]
    assert metric["metric"] == "cpu_percent"
    if metric["data_status"] != "ok":
        assert len(metric["points"]) < 2
        assert body["data_gaps"]
    else:
        assert len(metric["points"]) >= 2
    unknown = empty_client.get("/api/observability/series?metrics=not_a_metric", headers=AUTH).json()
    assert unknown["metrics"][0]["data_status"] == "not_integrated"
    assert unknown["metrics"][0]["points"] == []


def test_series_returns_two_points_after_real_sampling(empty_client: TestClient):
    """真实采样两次后可返回 >= 2 个真实点（验证采样落盘真实生效）。"""
    from gw.observability import telemetry
    telemetry.record_sample()
    telemetry.record_sample()
    body = empty_client.get("/api/observability/series?metrics=cpu_percent", headers=AUTH).json()
    metric = [m for m in body["metrics"] if m["metric"] == "cpu_percent"][0]
    assert len(metric["points"]) >= 2
    assert all("ts_ms" in p and "value" in p for p in metric["points"])


def test_sources_now_returns_real_registry(empty_client: TestClient):
    """Phase 12 A4：sources 返回真实登记的数据源清单（不再恒为空）。"""
    body = empty_client.get("/api/observability/sources", headers=AUTH).json()
    assert body["data_status"] == "ok"
    assert body["total"] >= 1
    assert body["items"] and all("source_id" in item and "path" in item for item in body["items"])


def test_asset_volumes_without_allowed_roots_is_not_integrated(empty_client: TestClient, monkeypatch):
    """未配置 GW_ALLOWED_ROOTS 时 asset-volumes 必须空 + not_integrated（不伪造条目）。"""
    monkeypatch.delenv("GW_ALLOWED_ROOTS", raising=False)
    body = empty_client.get("/api/observability/asset-volumes", headers=AUTH).json()
    assert body["data_status"] == "not_integrated"
    assert body["items"] == [] and body["total"] == 0
    assert body["data_gaps"]


def test_asset_volumes_scans_real_root(empty_client: TestClient, monkeypatch, tmp_path):
    """配置允许根目录后，asset-volumes 真实统计文件体积且不回显绝对路径。"""
    sample = tmp_path / "shot.png"
    sample.write_bytes(b"real-bytes" * 8)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(tmp_path))
    body = empty_client.get("/api/observability/asset-volumes", headers=AUTH).json()
    assert body["data_status"] == "ok"
    assert any(item["path"] == "shot.png" and item["size_bytes"] == 80 for item in body["items"])
    assert str(tmp_path) not in json.dumps(body)


def test_events_are_empty_when_no_real_audit_records(empty_client: TestClient):
    """无真实审计记录时 events 必须返回空数组（不得编造事件）。"""
    body = empty_client.get("/api/observability/events", headers=AUTH).json()
    assert body["data_status"] == "ok"
    assert body["items"] == []
    assert body["total"] == 0


def test_tasks_are_empty_when_no_real_jobs(empty_client: TestClient):
    """无真实任务时 tasks 必须返回空数组（不得编造任务）。"""
    body = empty_client.get("/api/observability/tasks", headers=AUTH).json()
    assert body["items"] == []
    assert body["total"] == 0


def test_overview_counts_are_real_zero_when_services_empty(empty_client: TestClient):
    """空服务状态下计数必须为真实 0，且未接入指标为 null 并登记 data_gaps。"""
    body = empty_client.get("/api/observability/overview", headers=AUTH).json()
    assert body["projects"] == {"total": 0, "active": 0, "archived": 0, "trashed": 0}
    assert body["jobs"]["total"] == 0
    assert body["summary"]["p95_latency_ms"] is None
    assert body["data_gaps"], "未接入指标必须登记在 data_gaps"


def test_overview_counts_come_from_real_services(seeded_client: TestClient):
    """计数必须来自真实服务实例：黄金夹具种子 1 项目 / 1 accepted 任务。"""
    body = seeded_client.get("/api/observability/overview", headers=AUTH).json()
    assert body["projects"]["total"] == 1
    assert body["projects"]["active"] == 1
    assert body["jobs"]["total"] == 1
    assert body["jobs"]["by_status"] == {"accepted": 1}
    assert body["jobs"]["queue_depth"] == 1


def test_tasks_projection_uses_stable_ids(seeded_client: TestClient):
    """任务投影必须使用稳定 job_id，禁止 id/pid/cid 别名。"""
    body = seeded_client.get("/api/observability/tasks", headers=AUTH).json()
    assert len(body["items"]) == 1
    task = body["items"][0]
    assert task["job_id"] == "job-0002"
    assert task["status"] == "accepted"
    for alias in ("id", "pid", "cid", "task_id"):
        assert alias not in task, f"任务投影出现非稳定别名: {alias}"


def test_health_reports_real_hardware_telemetry(empty_client: TestClient):
    """Phase 12 A4：hardware_telemetry 为真实 psutil 读数（psutil 可用时 status=ok）。"""
    body = empty_client.get("/api/observability/health", headers=AUTH).json()
    statuses = {check["name"]: check["status"] for check in body["checks"]}
    assert statuses["projects"] == "ok"
    assert statuses["canvas"] == "ok"
    assert statuses["asset_library"] == "ok"
    telemetry_check = [c for c in body["checks"] if c["name"] == "hardware_telemetry"][0]
    assert telemetry_check["status"] == "ok"
    metrics = telemetry_check["metrics"]
    for key in ("cpu_percent", "memory_percent", "disk_percent", "cpu_count"):
        assert key in metrics and isinstance(metrics[key], (int, float))
    assert 0 <= metrics["cpu_percent"] <= 100
    assert 0 <= metrics["memory_percent"] <= 100
    assert 0 <= metrics["disk_percent"] <= 100


def test_health_degrades_without_psutil(empty_client: TestClient, monkeypatch):
    """psutil 不可用时 hardware_telemetry 必须 not_integrated，不得编造读数。"""
    from gw.observability import telemetry as telemetry_mod
    monkeypatch.setattr(telemetry_mod, "_psutil", lambda: None)
    body = empty_client.get("/api/observability/health", headers=AUTH).json()
    check = [c for c in body["checks"] if c["name"] == "hardware_telemetry"][0]
    assert check["status"] == "not_integrated"
    assert check.get("metrics") in (None, {})
    assert body["status"] != "ok"


def test_health_never_claims_ok_when_component_fails(monkeypatch):
    """组件不可用时 health 必须如实降级，绝不无条件 ok。"""
    audit_log.reset_audit_log()
    broken = ProjectsService(seed_golden_fixture=False)

    def boom(*args, **kwargs):
        raise RuntimeError("服务不可用（测试注入）")

    monkeypatch.setattr(broken, "list_projects", boom)
    service = ObservabilityService(
        projects_service=broken,
        canvas_service=GodCanvasService(seed_golden_fixture=False),
        asset_library_service=AssetLibraryService(seed_golden_fixture=False),
    )
    monkeypatch.setattr(obs_routes, "default_observability_service", service)
    with TestClient(create_app()) as client:
        body = client.get("/api/observability/health", headers=AUTH).json()
    statuses = {check["name"]: check["status"] for check in body["checks"]}
    assert statuses["projects"] == "failed"
    assert body["status"] == "degraded"
    assert any("ProjectsService" in gap for gap in body["data_gaps"])
    audit_log.reset_audit_log()


# ---------------------------------------------------------------------------
# 真实审计事件
# ---------------------------------------------------------------------------

def _record_denied_events(count: int) -> None:
    """写入真实审计记录（走 core.audit 白名单 API，不经任何伪造路径）。"""
    for index in range(count):
        audit_log.record_auth_event(
            audit_log.EVENT_LOGIN_DENIED,
            outcome=audit_log.OUTCOME_DENIED,
            reason=f"oidc_not_ready_{index}",
            subject="alice",
            role="editor",
            auth_mode="oidc",
        )


def test_events_project_real_audit_records(empty_client: TestClient):
    """有真实审计记录时 events 必须如实投影，且不含任何凭据原文。"""
    _record_denied_events(2)
    body = empty_client.get("/api/observability/events", headers=AUTH).json()
    assert body["total"] == 2
    assert body["data_status"] == "ok"
    names = {item["event_name"] for item in body["items"]}
    assert names == {audit_log.EVENT_LOGIN_DENIED}
    assert all(item["source"] == "core.audit" for item in body["items"])
    assert all(item["level"] == "warn" for item in body["items"])
    assert all(item["event_id"] for item in body["items"])
    for item in body["items"]:
        for alias in ("id", "pid", "cid"):
            assert alias not in item
    _assert_no_fabrication(body)


def test_events_free_text_never_leaks_credentials(empty_client: TestClient):
    """响应与事件内容不得包含令牌、授权码、code_verifier、state、nonce、Cookie 值。"""
    secret = "SECRET-TOKEN-VALUE-abcdef123456"
    audit_log.record_auth_event(
        audit_log.EVENT_TOKEN_REJECTED,
        outcome=audit_log.OUTCOME_REJECTED,
        reason="token_verification_failed",
        auth_mode="oidc",
    )
    res = empty_client.get("/api/observability/events", headers=AUTH)
    assert secret not in res.text
    for marker in ("access_token", "code_verifier", "client_secret", "Cookie"):
        assert marker not in res.text, f"事件响应不得包含 {marker}"


def test_events_filter_by_source_and_filter_out_unknown_source(empty_client: TestClient):
    """source 过滤必须真实生效：未命中的来源返回空，而不是忽略过滤条件。"""
    _record_denied_events(1)
    hit = empty_client.get("/api/observability/events?source=core.audit", headers=AUTH).json()
    assert hit["total"] == 1
    miss = empty_client.get("/api/observability/events?source=other_source", headers=AUTH).json()
    assert miss["total"] == 0
    assert miss["items"] == []


def test_events_scoped_filter_returns_empty_and_degraded(empty_client: TestClient):
    """审计事件无 project_id 关联：按 project_id 过滤必须返回空 + degraded（不静默忽略）。"""
    _record_denied_events(3)
    body = empty_client.get("/api/observability/events?project_id=prj-0001", headers=AUTH).json()
    assert body["data_status"] == "degraded"
    assert body["items"] == []
    assert any("project_id" in gap for gap in body["data_gaps"])


def test_tasks_time_window_filter_discloses_snapshot_semantics(seeded_client: TestClient):
    """任务无时间戳：时间窗无法求值，必须返回真实快照 + degraded 并显式披露（不得静默忽略）。

    取舍说明：任务中心默认携带 range；若返回空会把真实任务误报成「窗口内无任务」，
    因此这里返回当前状态快照，同时在 data_gaps 明确写出时间条件未生效。
    """
    body = seeded_client.get("/api/observability/tasks?range=15m", headers=AUTH).json()
    assert body["data_status"] == "degraded"
    assert body["data_gaps"], "未生效的时间条件必须显式披露"
    assert len(body["items"]) == 1, "应返回真实任务快照，而不是误导性的空结果"
    assert body["items"][0]["job_id"] == "job-0002"
    body2 = seeded_client.get("/api/observability/tasks?start_ms=1&end_ms=2", headers=AUTH).json()
    assert body2["data_status"] == "degraded"
    assert body2["data_gaps"]
    assert len(body2["items"]) == 1


def test_tasks_scope_and_unsupported_dimension_filters_fail_closed(seeded_client: TestClient):
    """无法求值且会误导命中的过滤条件（作用域 ID / source / level）必须返回空 + degraded。"""
    for query in (
        "project_id=prj-0001",
        "canvas_id=cv-0001",
        "stable_id=asset-0001",
        "source=core.audit",
        "level=warn",
    ):
        body = seeded_client.get(f"/api/observability/tasks?{query}", headers=AUTH).json()
        assert body["data_status"] == "degraded", query
        assert body["items"] == [], query
        assert body["data_gaps"], query


def test_tasks_status_filter_is_applied(seeded_client: TestClient):
    """status 过滤必须真实生效。"""
    hit = seeded_client.get("/api/observability/tasks?status=accepted", headers=AUTH).json()
    assert hit["total"] == 1
    miss = seeded_client.get("/api/observability/tasks?status=running", headers=AUTH).json()
    assert miss["total"] == 0


# ---------------------------------------------------------------------------
# 全端点查询参数矩阵（真实解析，不得 500）
# ---------------------------------------------------------------------------

def test_every_endpoint_accepts_full_recognized_query_set(empty_client: TestClient):
    """8 个端点都必须解析完整参数集并 200，未识别参数不得 500。"""
    query = (
        "range=1h&limit=5&status=running&source=core.audit&project_id=prj-0001&job_id=job-0001"
        "&entity_id=nd-0001&asset_id=asset-0001&canvas_id=cv-0001&stable_id=asset-0001"
        "&cursor=&start_ms=1000&end_ms=2000&metrics=asset_response_bytes"
        "&job_type=generation&stall_classification=queued&event_id=evt-unknown&bogus=1"
    )
    for path in ENDPOINTS:
        res = empty_client.get(f"{path}?{query}", headers=AUTH)
        assert res.status_code == 200, f"{path} 未识别/边缘参数不得导致 {res.status_code}: {res.text}"
        body = res.json()
        filters = body["applied_filters"]
        assert filters["limit"] == 5, path
        assert filters["range"] == "1h", path
        assert filters["metrics"] == ["asset_response_bytes"], path
        # 稳定 ID 参数必须被真实解析并如实回显（不得静默丢弃）。
        for field, expected in (
            ("status", "running"),
            ("source", "core.audit"),
            ("project_id", "prj-0001"),
            ("job_id", "job-0001"),
            ("entity_id", "nd-0001"),
            ("asset_id", "asset-0001"),
            ("canvas_id", "cv-0001"),
            ("stable_id", "asset-0001"),
            ("start_ms", 1000),
            ("end_ms", 2000),
        ):
            assert filters[field] == expected, f"{path} 未如实解析 {field}: {filters[field]!r}"
        _assert_no_fabrication(body)


def test_every_list_endpoint_reports_applied_limit(empty_client: TestClient):
    """列表端点必须如实回显 limit，不得静默使用默认值。"""
    for path in (
        "/api/observability/events",
        "/api/observability/tasks",
        "/api/observability/sources",
        "/api/observability/asset-volumes",
    ):
        body = empty_client.get(f"{path}?limit=7", headers=AUTH).json()
        assert body["limit"] == 7, path


def test_limit_is_honored_on_events(empty_client: TestClient):
    """events 必须真实应用 limit 并给出 has_more。"""
    _record_denied_events(3)
    body = empty_client.get("/api/observability/events?limit=1", headers=AUTH).json()
    assert len(body["items"]) == 1
    assert body["total"] == 3
    assert body["has_more"] is True


# ---------------------------------------------------------------------------
# 分页与参数健壮性
# ---------------------------------------------------------------------------

def test_events_pagination_uses_next_cursor_and_has_more(empty_client: TestClient):
    """分页必须返回 next_cursor + has_more，且游标可真实翻页。"""
    _record_denied_events(5)
    page1 = empty_client.get("/api/observability/events?limit=2", headers=AUTH).json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    assert page1["has_more"] is True
    assert page1["next_cursor"]
    page2 = empty_client.get(
        f"/api/observability/events?limit=2&cursor={page1['next_cursor']}", headers=AUTH
    ).json()
    assert len(page2["items"]) == 2
    assert {item["event_id"] for item in page1["items"]}.isdisjoint(
        {item["event_id"] for item in page2["items"]}
    ), "翻页不得重复返回同一事件"
    page3 = empty_client.get(
        f"/api/observability/events?limit=2&cursor={page2['next_cursor']}", headers=AUTH
    ).json()
    assert len(page3["items"]) == 1
    assert page3["has_more"] is False
    assert page3["next_cursor"] is None


def test_empty_list_endpoints_report_no_more_pages(empty_client: TestClient):
    """空列表端点必须 has_more=False、next_cursor=None。"""
    for path in (
        "/api/observability/events",
        "/api/observability/tasks",
        "/api/observability/sources",
        "/api/observability/asset-volumes",
    ):
        body = empty_client.get(path, headers=AUTH).json()
        assert body["has_more"] is False, path
        assert body["next_cursor"] is None, path


def test_cursor_and_window_are_reflected_in_applied_filters(empty_client: TestClient):
    """cursor / start_ms / end_ms 必须被真实解析并回显，不得被静默丢弃。"""
    body = empty_client.get(
        "/api/observability/events?start_ms=1000&end_ms=2000", headers=AUTH
    ).json()
    filters = body["applied_filters"]
    assert filters["start_ms"] == 1000
    assert filters["end_ms"] == 2000
    assert body["start_ms"] == 1000
    assert body["end_ms"] == 2000


def test_window_filters_time_range(empty_client: TestClient):
    """start_ms / end_ms 必须真实过滤审计事件。"""
    _record_denied_events(1)
    now_ms = int(__import__("time").time() * 1000)
    inside = empty_client.get(
        f"/api/observability/events?start_ms={now_ms - 60_000}&end_ms={now_ms + 60_000}", headers=AUTH
    ).json()
    assert inside["total"] == 1
    outside = empty_client.get("/api/observability/events?start_ms=1&end_ms=2", headers=AUTH).json()
    assert outside["total"] == 0


def test_invalid_parameters_fail_closed_with_400(empty_client: TestClient):
    """非法 range / limit / cursor / 时间窗必须 400，不得 500 也不得静默忽略。"""
    for query in (
        "range=zzz",
        "range=0m",
        "limit=0",
        "limit=999",
        "cursor=not-a-cursor",
        "start_ms=20&end_ms=10",
        "start_ms=abc",
    ):
        res = empty_client.get(f"/api/observability/events?{query}", headers=AUTH)
        assert res.status_code == 400, f"{query} 应失败关闭为 400，实际 {res.status_code}"
        assert res.json()["detail"]["code"] == "INVALID_REQUEST"


def test_unrecognized_parameters_never_cause_500(empty_client: TestClient):
    """未识别参数不得导致 500（应被忽略或如实降级）。"""
    for query in (
        "bogus=1",
        "job_type=generation",
        "stall_classification=queued",
        "level=info&bogus=2",
        "metrics=unknown_metric",
    ):
        res = empty_client.get(f"/api/observability/events?{query}", headers=AUTH)
        assert res.status_code == 200, f"{query} 不应 500，实际 {res.status_code}"
        res_tasks = empty_client.get(f"/api/observability/tasks?{query}", headers=AUTH)
        assert res_tasks.status_code == 200, f"tasks?{query} 不应 500，实际 {res_tasks.status_code}"


def test_configurable_fields_match_contract(empty_client: TestClient):
    """query 参数白名单：契约声明的核心参数必须都能被接受。"""
    query = (
        "range=24h&limit=10&status=succeeded&source=core.audit&project_id=p1&job_id=j1"
        "&entity_id=e1&asset_id=a1&canvas_id=c1&stable_id=s1&start_ms=1&end_ms=2&metrics=m1"
    )
    res = empty_client.get(f"/api/observability/events?{query}", headers=AUTH)
    assert res.status_code == 200
    filters = res.json()["applied_filters"]
    for field in ("range", "limit", "status", "source", "project_id", "job_id",
                  "entity_id", "asset_id", "canvas_id", "stable_id", "start_ms", "end_ms"):
        assert field in filters, f"applied_filters 缺少 {field}"


def test_observability_index_lists_only_implemented_endpoints(empty_client: TestClient):
    """资源索引只能列出本阶段 8 个端点中的 7 个子资源 + 自身，不得越权声明。"""
    body = empty_client.get("/api/observability", headers=AUTH).json()
    assert body["service"] == "observability"
    assert body["contract_version"] == "p12-a4-1"
    paths = {item["path"] for item in body["resources"]}
    assert paths <= set(ENDPOINTS)
    assert "/api/observability/overview" in paths
    assert "/api/observability/series" in paths
    for item in body["resources"]:
        assert item["data_status"] in {"ok", "degraded", "not_integrated"}


# ---------------------------------------------------------------------------
# 范围守卫
# ---------------------------------------------------------------------------

def test_other_api_endpoints_remain_unimplemented(empty_client: TestClient):
    """未获授权的端点必须仍不可用，观测阶段不得顺带实现其它端点。"""
    for method, path in (
        ("get", "/api/observability/unknown"),
        ("post", "/api/observability"),
        ("get", "/api/asset-library/items/batch"),
        ("get", "/api/asset-library/libraries/library_default"),
        # 2026-09-22 Phase 10C：提示词库读取端点已由本阶段授权实现（p10c-frozen-1），
        # 故从观测阶段的「仍不可用」探针中移除；其范围守卫由
        # tests/contracts/prompts/test_phase10c_prompt_library.py 接管。
        # 2026-09-26 Phase 12 A3：/api/prompt-libraries/items 已按新契约真实接入
        # （PROMPT-LIBRARY-B8-ITEM-INTERFACE-CATALOG.yaml -> p12-a3-1），
        # 故从观察阶段的「不可用」探针中移除，改由 test_phase11_b8_prompt_items.py 接管。
    ):
        res = getattr(empty_client, method)(path, headers=AUTH)
        assert res.status_code in {404, 405}, f"{method.upper()} {path} 不应可用，实际 {res.status_code}"


def test_no_randomness_or_fake_telemetry_in_service_source():
    """源码级反向断言：观测服务与模型不得使用 random 或硬编码假遥测。"""
    for relative in (
        "gw/observability/service.py",
        "gw/observability/models.py",
        "gw/api/routes_observability.py",
    ):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        # 只否定「真的引入随机数」，文档中说明「禁止随机数」的措辞不算违规。
        for marker in ("import random", "random.", "Math.random", "uuid4"):
            assert marker not in text, f"{relative} 不得引入随机数（伪造数据风险）: {marker}"
        for marker in ("random.uniform", "random.randint", "fake_telemetry", "demo_series"):
            assert marker not in text, f"{relative} 不得包含伪造遥测标记 {marker}"


def test_repository_has_no_observability_fake_telemetry_payloads():
    """反向断言：实现与夹具中不存在伪造波形/假任务的载荷文件。"""
    for path in (REPO_ROOT / "docs" / "fixtures").glob("observability-*.json"):
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        payload = json.dumps(data, ensure_ascii=False)
        assert "cpu_percent" not in payload, f"{path.name} 不得包含伪造 CPU 数据"
        assert "ram_usage" not in payload, f"{path.name} 不得包含伪造内存数据"


# ---------------------------------------------------------------------------
# R3 独立复核发现的「静默失败」诚实性缺口（Phase 10B 修复回归）
# ---------------------------------------------------------------------------

def _broken_audit_service(monkeypatch):
    """构造审计源不可读的观测服务（其余依赖为真实空服务）。"""
    audit_log.reset_audit_log()
    service = ObservabilityService(
        projects_service=ProjectsService(seed_golden_fixture=False),
        canvas_service=GodCanvasService(seed_golden_fixture=False),
        asset_library_service=AssetLibraryService(seed_golden_fixture=False),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("audit source unavailable (injected)")

    monkeypatch.setattr(audit_log, "list_auth_events", boom)
    return service


def test_events_degrades_when_audit_source_is_unreadable(monkeypatch):
    """审计源不可读时，events 必须如实降级，绝不能伪装成空事件 + ok。"""
    service = _broken_audit_service(monkeypatch)
    monkeypatch.setattr(obs_routes, "default_observability_service", service)
    with TestClient(create_app()) as client:
        body = client.get("/api/observability/events", headers=AUTH).json()
    assert body["data_status"] == "degraded", "源不可用不得报告为 ok"
    assert body["items"] == []
    assert body["data_gaps"], "降级必须携带可见的 data_gaps"
    audit_log.reset_audit_log()


def test_health_reports_audit_buffer_failure_not_ok(monkeypatch):
    """审计缓冲不可读时，health 的 audit_buffer 检查必须为 failed，且整体降级。"""
    service = _broken_audit_service(monkeypatch)
    monkeypatch.setattr(obs_routes, "default_observability_service", service)
    with TestClient(create_app()) as client:
        body = client.get("/api/observability/health", headers=AUTH).json()
    statuses = {check["name"]: check["status"] for check in body["checks"]}
    assert statuses["audit_buffer"] == "failed", statuses
    assert body["status"] == "degraded"
    assert body["data_status"] == "degraded"
    audit_log.reset_audit_log()


def test_read_audit_records_distinguishes_failure_from_empty(monkeypatch):
    """核心区分：读取失败 -> None；成功但为空 -> []。"""
    service = ObservabilityService(
        projects_service=ProjectsService(seed_golden_fixture=False),
        canvas_service=GodCanvasService(seed_golden_fixture=False),
        asset_library_service=AssetLibraryService(seed_golden_fixture=False),
    )
    audit_log.reset_audit_log()
    assert service._read_audit_records() == [], "成功但为空必须是空列表"

    def boom(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(audit_log, "list_auth_events", boom)
    assert service._read_audit_records() is None, "读取失败必须是 None，不能退化为 []"
    audit_log.reset_audit_log()


def test_health_reports_real_gpu_telemetry_or_degrades(empty_client: TestClient):
    """Phase 12 收口：GPU 遥测为真实 nvidia-smi 读数；无设备/无命令必须如实 not_integrated。

    断言与实现同源：有 nvidia-smi 且检出设备 -> status=ok 且 metrics 含真实字段；
    否则 status=not_integrated 且不得填任何伪造数字。
    """
    from gw.observability import telemetry as telemetry_mod

    body = empty_client.get("/api/observability/health", headers=AUTH).json()
    check = [c for c in body["checks"] if c["name"] == "gpu_telemetry"][0]
    expected = telemetry_mod.gpu_telemetry()
    if expected is None:
        assert check["status"] == "not_integrated"
        assert check.get("metrics") in (None, {})
    else:
        assert check["status"] == "ok"
        metrics = check["metrics"]
        assert metrics["gpu_count"] == expected["gpu_count"] >= 1
        assert isinstance(metrics["gpu_utilization_percent"], (int, float))
        assert 0 <= metrics["gpu_utilization_percent"] <= 100
        devices = metrics["devices"]
        assert len(devices) == metrics["gpu_count"]
        for device in devices:
            assert device["memory_total_mib"] > 0
            assert isinstance(device["name"], str) and device["name"]


def test_gpu_telemetry_returns_none_without_nvidia_smi(monkeypatch):
    """nvidia-smi 缺失时必须返回 None，绝不编造 GPU 读数。"""
    from gw.observability import telemetry as telemetry_mod

    monkeypatch.setattr(telemetry_mod, "gpu_telemetry", lambda: None)
    assert telemetry_mod.gpu_telemetry() is None


def test_gpu_telemetry_never_fabricates_when_command_fails(monkeypatch):
    """nvidia-smi 返回非零码时必须返回 None（fail-closed）。"""
    import shutil
    import subprocess
    from gw.observability import telemetry as telemetry_mod

    class _Proc:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(shutil, "which", lambda name: "C:/fake/nvidia-smi.exe")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    assert telemetry_mod.gpu_telemetry() is None


def test_gpu_metric_whitelist_and_no_fabricated_names():
    """GPU 指标必须进白名单；且响应不得出现 FORBIDDEN_FAKE_FIELDS 中的伪造名。"""
    from gw.observability import telemetry as telemetry_mod

    assert "gpu_utilization_percent" in telemetry_mod.KNOWN_METRICS
    assert "gpu_memory_percent" in telemetry_mod.KNOWN_METRICS
    assert "gpu_usage" not in telemetry_mod.KNOWN_METRICS
