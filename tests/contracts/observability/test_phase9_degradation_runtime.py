# -*- coding: utf-8 -*-
"""Phase 9 前端显式降级的**运行时行为**守卫（Node 真实执行 JS，非静态字符串断言）。

背景：独立审核（`P9-B-INDEPENDENT-REVIEW.md`）指出 D2/D3——
① 503 服务暂时不可用被误判为「未纳入当前切片」；
② 原守卫只有静态字符串断言，501/503 分支无行为覆盖。

本文件用 Node 真实加载共享 transport 与 `degradation.js`，构造真实 `Response` 对象验证：
- 404 无标准错误包 → `NOT_INTEGRATED`（`unavailable=true`）；
- 404 **含**标准错误包（业务错误，如 `CANVAS_NOT_FOUND`）→ 原样透传，不得降级；
- 501 → `NOT_INTEGRATED`；
- **503 → `SERVICE_UNAVAILABLE`（可恢复，`retryable=true`），不得报成未接入**；
- 401 → `unauthorized`（提示重新登录）；403 → `forbidden`（只读降级）；
  409 → `conflict`（刷新并解决并发差异后重试）；
- 200 → 不降级。

证据边界：Node 环境行为验证 **不等于**真实浏览器 E2E；无 Node 时本文件跳过。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TRANSPORT_JS = REPO_ROOT / "web" / "js" / "core" / "http-transport.js"
DEGRADATION_JS = REPO_ROOT / "web" / "js" / "core" / "degradation.js"

_NODE = shutil.which("node")

PROBE = """import { createFetchTransport, isNotIntegratedResponse, isNotIntegratedError,
         isServiceUnavailableError } from "./http-transport.mjs";

const mk = (status, body) => new Response(JSON.stringify(body), {status, headers: {"Content-Type": "application/json"}});
const mkFetch = (status, body) => async () => mk(status, body);
const results = {};

globalThis.window = globalThis;
await import("./degradation.mjs");
const degradation = globalThis.GWDegradation;

results["404_plain"] = await isNotIntegratedResponse(mk(404, {detail: "Not Found"}));
results["404_business"] = await isNotIntegratedResponse(mk(404, {detail: {code: "CANVAS_NOT_FOUND", message: "x"}}));
results["501_plain"] = await isNotIntegratedResponse(mk(501, {detail: "Not Implemented"}));
results["503_generic"] = await isNotIntegratedResponse(mk(503, {detail: "Service Unavailable"}));
results["200_ok"] = await isNotIntegratedResponse(mk(200, {}));

for (const [status, expectedKind, detail] of [
    [401, "unauthorized", {code: "UNAUTHORIZED", message: "x"}],
    [403, "forbidden", {code: "FORBIDDEN", message: "x"}],
    [409, "conflict", {code: "CANVAS_VERSION_CONFLICT", message: "x"}],
]) {
    const classified = degradation.classifyResponse({status}, {detail});
    const error = degradation.createError(classified.kind, status, detail);
    results[`d${status}_kind`] = classified.kind;
    results[`d${status}_message`] = classified.message;
    results[`d${status}_code`] = error.code;
    results[`d${status}_name`] = error.name;
    results[`d${status}_retryable`] = error.retryable === true;
    results[`d${status}_readOnly`] = error.readOnly === true;
    results[`d${status}_refreshRequired`] = error.refreshRequired === true;
    results[`d${status}_predicate`] = expectedKind === "unauthorized"
        ? degradation.isUnauthorized(error)
        : (expectedKind === "forbidden" ? degradation.isForbidden(error) : degradation.isConflict(error));
}

try {
    await createFetchTransport(mkFetch(404, {detail: "Not Found"})).request("x");
    results["t404_code"] = "NO_THROW";
} catch (error) {
    results["t404_code"] = error.code;
    results["t404_unavailable"] = error.unavailable;
    results["t404_isNotIntegrated"] = isNotIntegratedError(error);
}

try {
    await createFetchTransport(mkFetch(501, {detail: "Not Implemented"})).request("x");
    results["t501_code"] = "NO_THROW";
} catch (error) {
    results["t501_code"] = error.code;
}

try {
    await createFetchTransport(mkFetch(503, {detail: "Service Unavailable"})).request("x");
    results["t503_code"] = "NO_THROW";
} catch (error) {
    results["t503_code"] = error.code;
    results["t503_isNotIntegrated"] = isNotIntegratedError(error);
    results["t503_isServiceUnavailable"] = isServiceUnavailableError(error);
    results["t503_retryable"] = error.retryable === true;
    results["t503_unavailable"] = error.unavailable;
}

const passthrough = await createFetchTransport(mkFetch(404, {detail: {code: "CANVAS_NOT_FOUND", message: "x"}})).request("x");
results["t404_business_status"] = passthrough.status;

console.log(JSON.stringify(results));
"""


@pytest.fixture(scope="module")
def probe_results() -> dict:
    if _NODE is None:
        pytest.skip("未找到 node，跳过运行时行为守卫（CI 环境应具备 Node）")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "http-transport.mjs").write_bytes(TRANSPORT_JS.read_bytes())
        (work / "degradation.mjs").write_bytes(DEGRADATION_JS.read_bytes())
        (work / "probe.mjs").write_text(PROBE, encoding="utf-8")
        completed = subprocess.run(
            [_NODE, str(work / "probe.mjs")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert completed.returncode == 0, f"Node 探针执行失败：{completed.stderr[:2000]}"
        return json.loads(completed.stdout.strip().splitlines()[-1])


def test_404_without_standard_envelope_is_not_integrated(probe_results):
    """404 且无标准错误包 → 判定未接入，错误码为 NOT_INTEGRATED。"""
    assert probe_results["404_plain"] is True
    assert probe_results["t404_code"] == "NOT_INTEGRATED"
    assert probe_results["t404_unavailable"] is True
    assert probe_results["t404_isNotIntegrated"] is True


def test_404_with_standard_envelope_is_passthrough(probe_results):
    """已实现接口的业务 404（对象 detail）必须原样透传，不得降级。"""
    assert probe_results["404_business"] is False
    assert probe_results["t404_business_status"] == 404


def test_501_is_not_integrated(probe_results):
    """501 Not Implemented → 未接入。"""
    assert probe_results["501_plain"] is True
    assert probe_results["t501_code"] == "NOT_INTEGRATED"


def test_503_is_service_unavailable_not_not_integrated(probe_results):
    """503 必须是可恢复的 SERVICE_UNAVAILABLE，不得报成「未纳入当前切片」。"""
    assert probe_results["503_generic"] is False, "503 不应被 isNotIntegratedResponse 判定为未接入"
    assert probe_results["t503_code"] == "SERVICE_UNAVAILABLE"
    assert probe_results["t503_isServiceUnavailable"] is True
    assert probe_results["t503_isNotIntegrated"] is False
    assert probe_results["t503_retryable"] is True
    assert probe_results["t503_unavailable"] is False


def test_frozen_auth_permission_and_conflict_semantics(probe_results):
    """401/403/409 按冻结规范分别映射为认证失效、只读权限与 CAS 冲突。"""
    assert probe_results["d401_kind"] == "unauthorized"
    assert probe_results["d401_code"] == "UNAUTHORIZED"
    assert probe_results["d401_name"] == "UnauthorizedError"
    assert probe_results["d401_predicate"] is True
    assert "重新登录" in probe_results["d401_message"]
    assert probe_results["d401_retryable"] is False

    assert probe_results["d403_kind"] == "forbidden"
    assert probe_results["d403_code"] == "FORBIDDEN"
    assert probe_results["d403_name"] == "ForbiddenError"
    assert probe_results["d403_predicate"] is True
    assert probe_results["d403_readOnly"] is True
    assert "只读" in probe_results["d403_message"]

    assert probe_results["d409_kind"] == "conflict"
    assert probe_results["d409_code"] == "CANVAS_VERSION_CONFLICT"
    assert probe_results["d409_name"] == "ConflictError"
    assert probe_results["d409_predicate"] is True
    assert probe_results["d409_retryable"] is True
    assert probe_results["d409_refreshRequired"] is True
    assert "刷新" in probe_results["d409_message"]


def test_ok_response_is_not_degraded(probe_results):
    """2xx 不得触发任何降级判定。"""
    assert probe_results["200_ok"] is False


def test_transport_source_declares_both_semantics():
    """源码必须同时声明「未接入」与「服务暂时不可用」两种语义。"""
    source = TRANSPORT_JS.read_text(encoding="utf-8")
    assert "NOT_INTEGRATED_STATUSES = new Set([404, 501])" in source
    assert "SERVICE_UNAVAILABLE" in source
    assert "createServiceUnavailableError" in source
    assert "isServiceUnavailableError" in source
