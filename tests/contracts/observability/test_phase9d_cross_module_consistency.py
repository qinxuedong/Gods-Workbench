# -*- coding: utf-8 -*-
"""Phase 9D 跨模块降级语义一致性守卫（Node 真实执行，防未来漂移）。

背景：独立复核（`docs/governance/agent-reports-2026-09-21/P9-D-INDEPENDENT-REVIEW.md`）
指出 `web/js/degradation.js`（经典脚本，`window.GWDegradation`）
与 `web/js/http-transport.js`（ESM）各自实现同一判定口径，
但**没有任何用例覆盖两者一致性**：任一侧改判定后 CI 不会报错。

本文件用 Node 同时加载两侧并对同一批 8 个输入做对照，分歧数必须为 0。

证据边界：Node 环境行为验证 **不等于**真实浏览器 E2E；无 Node 时跳过。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STATIC_JS = REPO_ROOT / "web" / "js"
DEGRADATION_JS = STATIC_JS / "core" / "degradation.js"
TRANSPORT_JS = STATIC_JS / "core" / "http-transport.js"

_NODE = shutil.which("node")

#: 对照表：与两侧实现共同覆盖的语义一一对应
PROBE = """import { isNotIntegratedResponse, isServiceUnavailableError,
         createFetchTransport } from "./http-transport.mjs";

const mk = (status, body) => new Response(JSON.stringify(body), {status, headers: {"Content-Type": "application/json"}});
const mkFetch = (status, body) => async () => mk(status, body);

// 经典脚本形态：degradation.js 需要 window 全局
const window = globalThis;
const { readFileSync } = await import("node:fs");
const source = readFileSync("./degradation.js", "utf8");
new Function("window", source)(window);
const GWD = window.GWDegradation;

const CASES = [
    {name: "404_plain_empty",      status: 404, body: {}},
    {name: "404_plain_notfound",   status: 404, body: {detail: "Not Found"}},
    {name: "404_lowercase",        status: 404, body: {detail: "not found"}},
    {name: "404_501_style",        status: 404, body: {detail: "Not Implemented"}},
    {name: "404_business_envelope",status: 404, body: {detail: {code: "CANVAS_NOT_FOUND", message: "x"}}},
    {name: "404_business_text",    status: 404, body: {detail: "项目不存在"}},
    {name: "501_plain",            status: 501, body: {detail: "Not Implemented"}},
    {name: "503_generic",          status: 503, body: {detail: "Service Unavailable"}},
    {name: "500_generic",          status: 500, body: {detail: "boom"}},
    {name: "200_ok",               status: 200, body: {}},
];

const out = [];
for (const c of CASES) {
    const kind = GWD.statusKind(c.status, c.body.detail);
    const notIntegrated = await isNotIntegratedResponse(mk(c.status, c.body));
    // 口径归一：两侧都只分三档 —— not_integrated / service_unavailable / 未降级（none）。
    // transport 侧「不抛错直接透传」等价于 GWD 侧的 'error'（原样透传，非降级）。
    const normalize = value => ((value === "error" || value === "ok") ? "none" : value);
    let transportKind = "ok";
    try {
        await createFetchTransport(mkFetch(c.status, c.body)).request("x");
    } catch (error) {
        transportKind = isServiceUnavailableError(error) ? "service_unavailable"
            : (error.code === "NOT_INTEGRATED" ? "not_integrated" : "none");
    }
    out.push({name: c.name, degradation: normalize(kind), transport: normalize(transportKind), notIntegrated});
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def probe_rows() -> list[dict]:
    if _NODE is None:
        pytest.skip("未找到 node，跳过跨模块一致性守卫（CI 环境应具备 Node）")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "http-transport.mjs").write_bytes(TRANSPORT_JS.read_bytes())
        (work / "degradation.js").write_bytes(DEGRADATION_JS.read_bytes())
        (work / "probe.mjs").write_text(PROBE, encoding="utf-8")
        completed = subprocess.run(
            [_NODE, str(work / "probe.mjs")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(work),
            timeout=120,
        )
        assert completed.returncode == 0, f"Node 探针执行失败：{completed.stderr[:2000]}"
        return json.loads(completed.stdout.strip().splitlines()[-1])


def test_degradation_js_and_transport_js_agree(probe_rows):
    """两侧判定必须逐条一致（含 503 与业务 404 的分流）。"""
    mismatches = [
        row for row in probe_rows
        if row["degradation"] != row["transport"]
    ]
    assert mismatches == [], f"跨模块降级语义不一致：{mismatches}"


def test_business_404_is_never_reported_as_not_integrated(probe_rows):
    """业务 404（对象型 detail / 业务字符串）两侧都不得降级为「未接入」。"""
    by_name = {row["name"]: row for row in probe_rows}
    for name in ("404_business_envelope", "404_business_text"):
        assert by_name[name]["degradation"] == "none", name
        assert by_name[name]["transport"] == "none", name
        assert by_name[name]["notIntegrated"] is False, name


def test_503_is_service_unavailable_on_both_sides(probe_rows):
    """503 两侧都必须是可恢复的服务不可用，不得是「未接入」。"""
    row = {r["name"]: r for r in probe_rows}["503_generic"]
    assert row["degradation"] == "service_unavailable"
    assert row["transport"] == "service_unavailable"
    assert row["notIntegrated"] is False


def test_plain_404_and_501_are_not_integrated_on_both_sides(probe_rows):
    """无标准错误包的 404/501 两侧都判未接入。"""
    by_name = {row["name"]: row for row in probe_rows}
    for name in ("404_plain_empty", "404_plain_notfound", "404_lowercase", "501_plain"):
        assert by_name[name]["degradation"] == "not_integrated", name
        assert by_name[name]["transport"] == "not_integrated", name


def test_other_statuses_are_plain_errors_on_both_sides(probe_rows):
    """500 / 200 两侧都不降级。"""
    by_name = {row["name"]: row for row in probe_rows}
    assert by_name["500_generic"]["degradation"] == "none"
    assert by_name["500_generic"]["transport"] == "none"
    assert by_name["200_ok"]["degradation"] == "none"
    assert by_name["200_ok"]["transport"] == "none"


def test_statuses_sets_are_identical_in_source():
    """静态双保险：两侧的「未接入状态码集合」必须字面一致。"""
    degradation = DEGRADATION_JS.read_text(encoding="utf-8")
    transport = TRANSPORT_JS.read_text(encoding="utf-8")
    assert "NOT_INTEGRATED_STATUSES = [404, 501]" in degradation
    assert "NOT_INTEGRATED_STATUSES = new Set([404, 501])" in transport
