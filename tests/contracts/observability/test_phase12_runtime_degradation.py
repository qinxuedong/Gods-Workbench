# -*- coding: utf-8 -*-
"""Phase 12 Workbench 控制器「真实接入」运行时守卫（Node 真实执行 JS，非字符串存在性检查）。

背景（第二轮独立审核 Required）：
`test_phase9_frontend_degradation.py` 中针对 AURA / GPU / 任务队列的守卫只做源码关键字
存在性断言，一个空实现或一行包含关键字的注释即可通过（审核实测
`MUTATION_RESULT=PASS_EMPTY_COMMENT_ONLY`），无法拦住真实回归。

本文件把被测控制器装载进 Node `vm` 沙箱（自带最小 DOM 实现，零外部依赖），
真实执行其初始化路径，并断言：
- 真的发出了预期的 `fetch`（URL + 方法），而不是只看源码里出现过地址；
- 真实应答 → DOM 写真实读数；
- 401/403/404/501/503/网络异常 → DOM 文案与 `data-gw-degradation` 分类正确；
- 反向变异：把函数体替换为空实现 / 仅注释后，同一守卫必须失败。

证据边界：Node `vm` + 最小 DOM **不等于**真实浏览器 E2E；本文件证明的是
「控制器逻辑真的会按应答改写 DOM」，不证明后端端点存在或生产可用。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONTROLLERS_DIR = REPO_ROOT / "web" / "js" / "controllers"
STATIC_JS = REPO_ROOT / "web" / "js"

_NODE = shutil.which("node")

# 最小 DOM + fetch 沙箱：只实现被测控制器实际用到的浏览器 API。
# 不做任何关键字匹配——控制器要么真的改到 DOM / 真的 fetch，要么断言失败。
HARNESS = r"""
import fs from "node:fs";
import vm from "node:vm";

const controllerPath = process.argv[2];
const plan = JSON.parse(process.argv[3] || "{}");
const src = fs.readFileSync(controllerPath, "utf8");

function makeEl(id) {
  const el = {
    id: id || "", _attrs: {}, _classes: new Set(), textContent: "", innerHTML: "",
    outerHTML: "", text: "", title: "", style: {}, dataset: {}, hidden: false, disabled: false,
    value: "", checked: false, src: "", href: "", type: "", name: "", htmlFor: "",
    children: [], childNodes: [], parentElement: null, parentNode: null, files: [], options: [],
    classList: {
      add(...c) { c.forEach((x) => el._classes.add(x)); },
      remove(...c) { c.forEach((x) => el._classes.delete(x)); },
      contains(c) { return el._classes.has(c); },
      toggle(c) { el._classes.has(c) ? el._classes.delete(c) : el._classes.add(c); },
      replace(a, b) { el._classes.delete(a); el._classes.add(b); },
    },
    setAttribute(k, v) { el._attrs[k] = String(v); },
    removeAttribute(k) { delete el._attrs[k]; },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(el._attrs, k) ? el._attrs[k] : null; },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(el._attrs, k); },
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    appendChild(c) { el.children.push(c); return c; },
    append(...c) { el.children.push(...c); }, prepend(...c) { el.children.unshift(...c); },
    insertAdjacentHTML() {}, insertBefore(c) { el.children.push(c); return c; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    closest() { return null; }, matches() { return false; },
    focus() {}, blur() {}, click() {}, remove() {}, replaceChildren() {},
    setSelectionRange() {}, scrollIntoView() {}, reset() {}, submit() {},
    getBoundingClientRect() { return { width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0 }; },
    getContext() { return null; }, toDataURL() { return ""; },
    animate() { return { finished: Promise.resolve(), cancel() {} }; },
  };
  Object.defineProperty(el, "className", {
    get() { return [...el._classes].join(" "); },
    set(v) { el._classes = new Set(String(v || "").split(/\s+/).filter(Boolean)); },
  });
  return el;
}

const registry = new Map();
const byId = (id) => {
  if (!registry.has(id)) registry.set(id, makeEl(id));
  return registry.get(id);
};
const selectorMap = plan.selectorMap || {};
const selectorNodes = (sel) => (selectorMap[sel] || []).map(byId);

const doc = {
  readyState: "complete",
  _listeners: {},
  getElementById: byId,
  querySelector(sel) { return selectorNodes(sel)[0] || null; },
  querySelectorAll(sel) { return selectorNodes(sel); },
  createElement() { return makeEl(""); },
  createElementNS() { return makeEl(""); },
  addEventListener(t, fn) { (doc._listeners[t] = doc._listeners[t] || []).push(fn); },
  removeEventListener() {},
  body: makeEl("body"), head: makeEl("head"), documentElement: makeEl("html"),
  cookie: "", title: "",
  createRange() { return { selectNodeContents() {}, setStart() {}, setEnd() {} }; },
  execCommand() { return true; },
};

const calls = [];
const defaultSpec = plan.default || { status: 200, body: {} };
function pickPlan(url) {
  for (const r of (plan.responses || [])) {
    const mode = r.match || "prefix";
    const hit = mode === "exact" ? url === r.url
      : (mode === "contains" ? url.includes(r.url) : url.startsWith(r.url));
    if (hit) return r;
  }
  return defaultSpec;
}

const winListeners = {};
const sandbox = {
  console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
  performance, URLSearchParams, URL, AbortController, AbortSignal, Date, Math, JSON,
  // 共享顶栏会发出认证事件；补齐DOM构造器，保留全部遥测行为断言。
  CustomEvent: globalThis.CustomEvent || class extends Event {
    constructor(type, options = {}) { super(type, options); this.detail = options.detail ?? null; }
  },
  document: doc,
  localStorage: { getItem() { return null; }, setItem() {}, removeItem() {}, clear() {} },
  sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {}, clear() {} },
  location: { href: "http://127.0.0.1:2077/static/pages/index.html", search: "", pathname: "/static/pages/index.html", origin: "http://127.0.0.1:2077", hash: "" },
  navigator: { userAgent: "node", clipboard: { writeText: async () => {} } },
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  alert() {}, confirm: () => true, prompt: () => null,
  requestAnimationFrame: (fn) => setTimeout(fn, 0), cancelAnimationFrame() {},
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
  addEventListener(t, fn) { (winListeners[t] = winListeners[t] || []).push(fn); },
  removeEventListener() {}, dispatchEvent() { return true; },
  innerWidth: 1920, innerHeight: 1080, scrollY: 0, devicePixelRatio: 1,
  fetch: async (url, opts) => {
    const u = String(url);
    calls.push({ url: u, method: String((opts && opts.method) || "GET").toUpperCase() });
    const spec = pickPlan(u);
    if (spec.networkError) { const e = new Error("fetch failed"); e.name = "TypeError"; throw e; }
    const status = spec.status === undefined ? 200 : spec.status;
    const text = JSON.stringify(spec.body === undefined ? {} : spec.body);
    return {
      ok: status >= 200 && status < 300,
      status, statusText: String(status),
      headers: { get: () => "application/json" },
      json: async () => JSON.parse(text),
      text: async () => text,
      clone() { return this; },
    };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.window.lucide = { createIcons() {} };

const out = { loadError: null, calls, snapshot: {} };
try {
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: controllerPath });
} catch (e) {
  out.loadError = String((e && e.stack) || e).slice(0, 1200);
}

const flush = () => new Promise((r) => setTimeout(r, 0));
try {
  for (let i = 0; i < 20; i += 1) await flush();
  if (plan.driver) {
    const returned = vm.runInContext("(async () => { " + plan.driver + " })()", sandbox, { filename: "__driver__.js" });
    out.driver = await Promise.resolve(returned);
    for (let i = 0; i < 20; i += 1) await flush();
  }
  for (const id of (plan.snapshotIds || [])) {
    const el = byId(id);
    out.snapshot[id] = {
      text: el.textContent,
      deg: el.getAttribute("data-gw-degradation"),
      title: String(el.title || "").slice(0, 160),
      className: el.className,
    };
  }
} catch (e) {
  out.driverError = String((e && e.stack) || e).slice(0, 1200);
}
console.log(JSON.stringify(out));
// 控制器可能注册常驻定时器/监听；探针拿到快照后必须显式退出，
// 否则 Node 事件循环不结束，pytest 会一直挂在子进程上。
process.exit(0);
"""


def _run(controller: Path, plan: dict, tmp_path: Path) -> dict:
    """在 Node 沙箱里真实装载并执行控制器，返回 fetch 调用与 DOM 快照。"""
    if _NODE is None:
        pytest.skip("未找到 node，跳过 Workbench 运行时行为守卫")
    harness = tmp_path / "workbench_runtime_probe.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    completed = subprocess.run(
        [_NODE, str(harness), str(controller), json.dumps(plan, ensure_ascii=False)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert completed.returncode == 0, f"Node 探针执行失败：{completed.stderr[:2000]}"
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["loadError"] is None, f"控制器装载即报错：{result['loadError']}"
    assert "driverError" not in result, f"驱动器执行报错：{result.get('driverError')}"
    return result


def _chat_body(code: str) -> dict:
    return {"detail": {"code": code, "message": "x"}}


# ---------------------------------------------------------------------------
# 1. 首页 AURA 总线：GET 配置摘要驱动（不触发模型生成）
# ---------------------------------------------------------------------------

def test_home_aura_bus_reads_chat_configuration_without_generation(tmp_path):
    """页面加载只读配置：即使 Provider 已配置也不得自动发起计费 POST。"""
    result = _run(
        CONTROLLERS_DIR / "home-controller.js",
        {"responses": [{"url": "/api/chat/config", "match": "exact", "status": 200,
                        "body": {"configured": True, "configuration_status": "configured", "providers": []}}],
         "snapshotIds": ["auraBusStatus"]},
        tmp_path,
    )
    reads = [c for c in result["calls"] if c["method"] == "GET" and c["url"] == "/api/chat/config"]
    posts = [c for c in result["calls"] if c["method"] == "POST" and c["url"].startswith("/api/chat")]
    assert reads, f"页面加载必须读取本地配置摘要：{result['calls']}"
    assert not posts, f"页面加载不得触发任何可能计费的对话请求：{result['calls']}"
    snap = result["snapshot"]["auraBusStatus"]
    assert "Provider 已配置" in snap["text"] and "未测连接" in snap["text"], snap
    assert snap["deg"] is None, "配置已读不表示上游在线，但无需降级标记"


def test_home_aura_bus_marks_missing_provider_configuration(tmp_path):
    result = _run(
        CONTROLLERS_DIR / "home-controller.js",
        {"responses": [{"url": "/api/chat/config", "match": "exact", "status": 200,
                        "body": {"configured": False, "configuration_status": "not_configured", "providers": []}}],
         "snapshotIds": ["auraBusStatus"]},
        tmp_path,
    )
    snap = result["snapshot"]["auraBusStatus"]
    assert "Provider 未配置" in snap["text"], snap
    assert snap["deg"] == "not_integrated", snap
    assert not [c for c in result["calls"] if c["method"] == "POST"], result["calls"]


def test_home_aura_bus_marks_generic_503_as_service_unavailable(tmp_path):
    result = _run(
        CONTROLLERS_DIR / "home-controller.js",
        {"responses": [{"url": "/api/chat/config", "match": "exact", "status": 503,
                        "body": _chat_body("UPSTREAM_BUSY")}],
         "snapshotIds": ["auraBusStatus"]},
        tmp_path,
    )
    snap = result["snapshot"]["auraBusStatus"]
    assert snap["deg"] == "service_unavailable", snap
    assert "Provider 已配置" not in snap["text"], snap
    assert not [c for c in result["calls"] if c["method"] == "POST"], result["calls"]


@pytest.mark.parametrize("status", [401, 403, 404, 501])
def test_home_aura_bus_config_failure_never_claims_connection(tmp_path, status):
    result = _run(
        CONTROLLERS_DIR / "home-controller.js",
        {"responses": [{"url": "/api/chat/config", "match": "exact", "status": status,
                        "body": _chat_body("SOME_CODE")}],
         "snapshotIds": ["auraBusStatus"]},
        tmp_path,
    )
    snap = result["snapshot"]["auraBusStatus"]
    assert "Provider 已配置" not in snap["text"], snap
    assert snap["deg"] == "service_unavailable", snap
    assert not [c for c in result["calls"] if c["method"] == "POST"], result["calls"]


def test_home_aura_bus_configuration_network_error_is_service_unavailable(tmp_path):
    result = _run(
        CONTROLLERS_DIR / "home-controller.js",
        {"responses": [{"url": "/api/chat/config", "match": "exact", "networkError": True}],
         "snapshotIds": ["auraBusStatus"]},
        tmp_path,
    )
    snap = result["snapshot"]["auraBusStatus"]
    assert "Provider 已配置" not in snap["text"], snap
    assert snap["deg"] == "service_unavailable", snap
    assert not [c for c in result["calls"] if c["method"] == "POST"], result["calls"]


# ---------------------------------------------------------------------------
# 2. 智能体页：配置仅读；吞吐仅由用户明确提交后的真 usage 驱动
# ---------------------------------------------------------------------------

def _configured_chat_body():
    return {
        "configured": True,
        "configuration_status": "configured",
        "default_provider_id": "provider-a",
        "providers": [{"provider_id": "provider-a", "models": ["model-a"],
                       "default_model": "model-a", "configured": True, "source": "runtime_mapping"}],
    }


def test_agents_page_load_reads_configuration_but_never_posts_chat(tmp_path):
    result = _run(
        CONTROLLERS_DIR / "agents-controller.js",
        {"responses": [{"url": "/api/chat/config", "match": "exact", "status": 200,
                        "body": _configured_chat_body()}],
         "snapshotIds": ["agentBusStatus", "agentLatencyReadout", "agentThroughputReadout"]},
        tmp_path,
    )
    reads = [c for c in result["calls"] if c["method"] == "GET" and c["url"] == "/api/chat/config"]
    posts = [c for c in result["calls"] if c["method"] == "POST" and c["url"].startswith("/api/chat")]
    assert reads, result["calls"]
    assert not posts, f"Agents 页面装载不得发送ping/生成：{result['calls']}"
    bus = result["snapshot"]["agentBusStatus"]
    assert "Provider 已配置" in bus["text"] and "尚未试请求" in bus["text"], bus
    assert result["snapshot"]["agentLatencyReadout"]["deg"] == "not_measured"
    assert result["snapshot"]["agentThroughputReadout"]["deg"] == "not_measured"


def test_agents_configuration_unavailable_does_not_fake_metrics(tmp_path):
    result = _run(
        CONTROLLERS_DIR / "agents-controller.js",
        {"responses": [{"url": "/api/chat/config", "match": "exact", "status": 200,
                        "body": {"configured": False, "configuration_status": "not_configured", "providers": []}}],
         "snapshotIds": ["agentBusStatus", "agentLatencyReadout", "agentThroughputReadout"]},
        tmp_path,
    )
    bus = result["snapshot"]["agentBusStatus"]
    assert "Provider 未配置" in bus["text"], bus
    assert bus["deg"] == "not_integrated", bus
    for name in ("agentLatencyReadout", "agentThroughputReadout"):
        snap = result["snapshot"][name]
        assert "尚未测量" in snap["text"] and snap["deg"] == "not_measured", snap
    assert not [c for c in result["calls"] if c["method"] == "POST"], result["calls"]


def test_agents_user_click_posts_once_and_renders_provider_usage_metrics(tmp_path):
    result = _run(
        CONTROLLERS_DIR / "agents-controller.js",
        {"responses": [
            {"url": "/api/chat/config", "match": "exact", "status": 200, "body": _configured_chat_body()},
            {"url": "/api/chat/agent", "match": "exact", "status": 200, "body": {
                "reply": "真实回复", "provider_id": "provider-a", "model": "model-a",
                "metrics": {"kind": "end_to_end_output_tokens_per_second", "provider_id": "provider-a",
                            "model": "model-a", "output_tokens": 24, "elapsed_seconds": 2.0,
                            "tokens_per_second": 12.0,
                            "usage_source": "chat_completions.usage.completion_tokens", "unavailable_reason": None}}},
        ], "driver": "document.getElementById('agentPromptInput').value='请测试'; await window.WorkbenchAgents.sendTestPrompt({preventDefault(){}});",
        "snapshotIds": ["agentThroughputReadout", "agentLatencyReadout"]},
        tmp_path,
    )
    posts = [c for c in result["calls"] if c["method"] == "POST" and c["url"] == "/api/chat/agent"]
    assert len(posts) == 1, f"一次人工提交最多发送一次付费请求：{result['calls']}"
    throughput = result["snapshot"]["agentThroughputReadout"]
    assert "12.00 tokens/s" in throughput["text"], throughput
    assert throughput["deg"] is None and "不是解码速度" in throughput["title"], throughput
    latency = result["snapshot"]["agentLatencyReadout"]
    assert "2000 ms" in latency["text"] and latency["deg"] is None, latency

# ---------------------------------------------------------------------------
# 3. 项目卡「资产规模」：真实 GET 必须在 render() 之后写入
# ---------------------------------------------------------------------------

def test_projects_asset_scale_writes_real_total_after_render(tmp_path):
    """200 + total：卡片重建后必须写真实数字，不得停留静态初值「未接入」。"""
    result = _run(
        CONTROLLERS_DIR / "projects-controller.js",
        {"selectorMap": {"[data-gw-asset-scale]": ["assetScaleCell"]},
         "responses": [{"url": "/api/asset-registry/assets", "status": 200, "body": {"total": 42}}],
         "snapshotIds": ["assetScaleCell"]},
        tmp_path,
    )
    fetched = [c for c in result["calls"] if c["url"].startswith("/api/asset-registry/assets")]
    assert fetched, f"必须真实请求素材登记端点，实际调用：{result['calls']}"
    snap = result["snapshot"]["assetScaleCell"]
    assert snap["text"] == "42 项", f"必须写入后端真实 total：{snap}"
    assert snap["deg"] is None, snap


def test_projects_asset_scale_degrades_honestly_on_failure(tmp_path):
    """503：如实回退「未接入」，不得伪造数字。"""
    result = _run(
        CONTROLLERS_DIR / "projects-controller.js",
        {"selectorMap": {"[data-gw-asset-scale]": ["assetScaleCell"]},
         "responses": [{"url": "/api/asset-registry/assets", "status": 503,
                        "body": _chat_body("SERVICE_UNAVAILABLE")}],
         "snapshotIds": ["assetScaleCell"]},
        tmp_path,
    )
    snap = result["snapshot"]["assetScaleCell"]
    assert "未接入" in snap["text"], snap
    assert snap["deg"] == "service_unavailable", snap


# ---------------------------------------------------------------------------
# 4. GPU 遥测：真实 health 应答驱动，缺项必须降级
# ---------------------------------------------------------------------------

def test_hardware_deck_gpu_cluster_uses_real_devices(tmp_path):
    """gpu_telemetry.status=ok + devices：写设备台数，不得写死型号。"""
    result = _run(
        STATIC_JS / "core" / "hardware-telemetry.js",
        {"selectorMap": {"[data-gw-gpu-cluster]": ["gpuClusterCell"]},
         "snapshotIds": ["gpuClusterCell"],
         "driver": "window.HardwareDeck.applyGpuTelemetry({status:'ok',metrics:{"
                   "gpu_utilization_percent:42,gpu_memory_percent:61,"
                   "devices:[{index:0,name:'NVIDIA GeForce RTX 5090'},{index:1,name:'NVIDIA GeForce RTX 5090'}]}});"},
        tmp_path,
    )
    snap = result["snapshot"]["gpuClusterCell"]
    assert snap["text"] == "2 台 GPU", f"必须按真实 devices 数量渲染：{snap}"
    assert snap["deg"] is None, snap


def test_hardware_deck_gpu_cluster_degrades_when_check_missing(tmp_path):
    """gpu_telemetry 缺失：必须显式降级「未接入」，不得伪造读数。"""
    result = _run(
        STATIC_JS / "core" / "hardware-telemetry.js",
        {"selectorMap": {"[data-gw-gpu-cluster]": ["gpuClusterCell"]},
         "snapshotIds": ["gpuClusterCell"],
         "driver": "window.HardwareDeck.applyGpuTelemetry(null);"},
        tmp_path,
    )
    snap = result["snapshot"]["gpuClusterCell"]
    assert "未接入" in snap["text"], snap
    assert snap["deg"] == "not_integrated", snap


# ---------------------------------------------------------------------------
# 5. 反向变异：守卫必须拦住「空实现 / 仅注释」——这正是上一轮的 Required
# ---------------------------------------------------------------------------

def test_guard_rejects_comment_only_aura_implementation(tmp_path):
    """变异验证：把 syncAuraBusStatus 换成仅含关键字的注释后，运行时守卫必须失败。

    这条用例证明上面的守卫不是「源码里出现过关键字就算过」——它真的执行了 JS。
    """
    source = (CONTROLLERS_DIR / "home-controller.js").read_text(encoding="utf-8")
    anchor = source.find("async function syncAuraBusStatus()")
    assert anchor != -1, "未找到 syncAuraBusStatus"
    # 空实现：函数体只剩包含关键字的注释，不能靠注释满足真实 GET 守卫。
    mutated = (
        source[:anchor]
        + "async function syncAuraBusStatus() {\n"
        + "  // id=auraBusStatus 读取中… syncAuraBusStatus '/api/chat/config' auraBusStatus data-gw-degradation\n"
        + "}\n"
        + source[source.find("function init()", anchor):]
    )
    mutant = tmp_path / "home-controller.mutant.js"
    mutant.write_text(mutated, encoding="utf-8")

    result = _run(
        mutant,
        {"responses": [{"url": "/api/chat/config", "match": "exact", "status": 200,
                        "body": {"configured": True, "configuration_status": "configured", "providers": []}}],
         "snapshotIds": ["auraBusStatus"]},
        tmp_path,
    )
    posted = [c for c in result["calls"] if c["method"] == "POST" and c["url"].startswith("/api/chat")]
    reads = [c for c in result["calls"] if c["method"] == "GET" and c["url"] == "/api/chat/config"]
    assert not posted, "空实现不得发出任何模型POST"
    assert not reads, "变异后的空实现应被上方正式功能用例识别为缺少配置GET"
    snap = result["snapshot"]["auraBusStatus"]
    assert "Provider 已配置" not in snap["text"], "空实现不得写出已配置结果"


def test_guard_rejects_comment_only_asset_scale_implementation(tmp_path):
    """变异验证：把 syncAssetScale 换成仅注释后，资产规模守卫必须失败。"""
    source = (CONTROLLERS_DIR / "projects-controller.js").read_text(encoding="utf-8")
    anchor = source.find("async function syncAssetScale()")
    assert anchor != -1, "未找到 syncAssetScale"
    end = source.find("function init()", anchor)
    mutated = (
        source[:anchor]
        + "async function syncAssetScale() {\n"
        + "  // data-gw-asset-scale data-gw-degradation /api/asset-registry/assets total 未接入\n"
        + "}\n"
        + source[end:]
    )
    mutant = tmp_path / "projects-controller.mutant.js"
    mutant.write_text(mutated, encoding="utf-8")

    result = _run(
        mutant,
        {"selectorMap": {"[data-gw-asset-scale]": ["assetScaleCell"]},
         "responses": [{"url": "/api/asset-registry/assets", "status": 200, "body": {"total": 42}}],
         "snapshotIds": ["assetScaleCell"]},
        tmp_path,
    )
    fetched = [c for c in result["calls"] if c["url"].startswith("/api/asset-registry/assets")]
    assert not fetched, "空实现不应请求素材端点"
    snap = result["snapshot"]["assetScaleCell"]
    assert snap["text"] != "42 项", "空实现不得写出真实 total"


# ---------------------------------------------------------------------------
# 6. workbench-shell 注入的顶栏推子：不得永久「未接入」，必须由真实读数驱动
#    （修复前实测：顶栏同时显示真实 FLUX/VRAM 读数与一个声称「本切片无任何真实
#     算力/GPU 遥测数据源」的假降级块，自相矛盾。）
# ---------------------------------------------------------------------------

def test_shell_fader_hooks_render_real_gpu_percentages(tmp_path):
    """真实 gpu_telemetry：注入推子钩子必须写出真实百分比并清除降级标记。"""
    result = _run(
        STATIC_JS / "core" / "hardware-telemetry.js",
        {"selectorMap": {"[data-gw-gpu-util]": ["shellFlux"], "[data-gw-gpu-vram]": ["shellVram"]},
         "snapshotIds": ["shellFlux", "shellVram"],
         "driver": "window.HardwareDeck.applyGpuTelemetry({status:'ok',metrics:{"
                   "gpu_utilization_percent:42,gpu_memory_percent:61,"
                   "devices:[{index:0,name:'NVIDIA GeForce RTX 5090'}]}});"},
        tmp_path,
    )
    flux = result["snapshot"]["shellFlux"]
    vram = result["snapshot"]["shellVram"]
    assert flux["text"] == "42%", f"FLUX 推子必须写入真实利用率：{flux}"
    assert vram["text"] == "61%", f"VRAM 推子必须写入真实显存占用率：{vram}"
    assert flux["deg"] is None and vram["deg"] is None, "真实读数路径不得残留降级标记"


def test_shell_fader_hooks_degrade_when_gpu_missing(tmp_path):
    """gpu_telemetry 缺失：注入推子必须显式降级「未接入」，不得伪造数字。"""
    result = _run(
        STATIC_JS / "core" / "hardware-telemetry.js",
        {"selectorMap": {"[data-gw-gpu-util]": ["shellFlux"], "[data-gw-gpu-vram]": ["shellVram"]},
         "snapshotIds": ["shellFlux", "shellVram"],
         "driver": "window.HardwareDeck.applyGpuTelemetry(null);"},
        tmp_path,
    )
    for key in ("shellFlux", "shellVram"):
        snap = result["snapshot"][key]
        assert "未接入" in snap["text"], snap
        assert snap["deg"] == "not_integrated", snap
