import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const taskCenterJs = readFileSync(
  path.join(repoRoot, "web/js/modules/task-center.js"),
  "utf8",
);

function createMockElement(tagName, id = "", attrs = {}) {
  const classes = new Set();
  const listeners = new Map();
  const el = {
    tagName: tagName.toUpperCase(),
    id,
    dataset: { ...attrs.dataset },
    attributes: new Map(Object.entries(attrs.raw || {})),
    children: [],
    parentNode: null,
    style: {},
    hidden: Boolean(attrs.hidden),
    disabled: false,
    value: attrs.value || "",
    _innerHTML: "",
    _textContent: attrs.textContent || "",
    classList: {
      add(...tokens) {
        tokens.forEach((t) => classes.add(t));
      },
      remove(...tokens) {
        tokens.forEach((t) => classes.delete(t));
      },
      toggle(t, force) {
        if (force === undefined) {
          if (classes.has(t)) classes.delete(t);
          else classes.add(t);
        } else if (force) classes.add(t);
        else classes.delete(t);
      },
      contains(t) {
        return classes.has(t);
      },
    },
    set className(val) {
      classes.clear();
      String(val || "")
        .split(/\s+/)
        .filter(Boolean)
        .forEach((t) => classes.add(t));
    },
    get className() {
      return [...classes].join(" ");
    },
    set innerHTML(val) {
      this._innerHTML = String(val ?? "");
    },
    get innerHTML() {
      return this._innerHTML;
    },
    set textContent(val) {
      this._textContent = String(val ?? "");
    },
    get textContent() {
      return this._textContent;
    },
    setAttribute(k, v) {
      this.attributes.set(k, String(v));
      if (k === "hidden") this.hidden = true;
    },
    getAttribute(k) {
      return this.attributes.get(k) ?? null;
    },
    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
    insertBefore(newNode, refNode) {
      if (newNode.parentNode) {
        const oldIdx = newNode.parentNode.children.indexOf(newNode);
        if (oldIdx !== -1) newNode.parentNode.children.splice(oldIdx, 1);
      }
      newNode.parentNode = this;
      const idx = this.children.indexOf(refNode);
      if (idx === -1) this.children.push(newNode);
      else this.children.splice(idx, 0, newNode);
      return newNode;
    },
    remove() {
      if (!this.parentNode) return;
      const idx = this.parentNode.children.indexOf(this);
      if (idx !== -1) this.parentNode.children.splice(idx, 1);
      this.parentNode = null;
    },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    dispatchEvent(evt) {
      const event = {
        preventDefault() {},
        target: el,
        ...evt,
      };
      for (const fn of listeners.get(event.type) || []) fn(event);
      return true;
    },
    closest() {
      return null;
    },
    querySelectorAll(selector) {
      if (selector === "[data-view]") {
        return el.children.filter((n) => n.dataset?.view);
      }
      return [];
    },
    matches() {
      return false;
    },
    focus() {
      el._ownerDoc.activeElement = el;
    },
    scrollIntoView() {},
    get options() {
      return [];
    },
  };
  return el;
}

function buildEmbeddedDom() {
  const main = createMockElement("main", "main");
  main.className = "workspace-page center-page";
  const toolbar = createMockElement("section", "toolbar");
  toolbar.className = "observability-toolbar";
  const notice = createMockElement("div", "observabilityNotice");
  const viewNav = createMockElement("nav", "observationViews");

  const tabOverview = createMockElement("button", "observation-tab-overview", {
    dataset: { view: "overview" },
  });
  const tabTasks = createMockElement("button", "observation-tab-tasks", {
    dataset: { view: "tasks" },
  });
  const tasksBadge = createMockElement("span", "", {
    dataset: { tabCount: "tasks" },
    hidden: true,
  });
  tabTasks.appendChild(tasksBadge);

  const tabLogs = createMockElement("button", "observation-tab-logs", {
    dataset: { view: "logs" },
  });
  const logsBadge = createMockElement("span", "", {
    dataset: { tabCount: "logs" },
    hidden: true,
  });
  tabLogs.appendChild(logsBadge);

  const tabMetrics = createMockElement("button", "observation-tab-metrics", {
    dataset: { view: "metrics" },
  });

  viewNav.appendChild(tabOverview);
  viewNav.appendChild(tabTasks);
  viewNav.appendChild(tabLogs);
  viewNav.appendChild(tabMetrics);

  const panelOverview = createMockElement("section", "observation-panel-overview", {
    dataset: { viewPanel: "overview" },
  });
  const panelTasks = createMockElement("section", "observation-panel-tasks", {
    dataset: { viewPanel: "tasks" },
  });
  const panelLogs = createMockElement("section", "observation-panel-logs", {
    dataset: { viewPanel: "logs" },
  });
  const panelMetrics = createMockElement("section", "observation-panel-metrics", {
    dataset: { viewPanel: "metrics" },
  });

  const nodes = {
    "#observationViews": viewNav,
    ".observability-toolbar": toolbar,
    "#observabilityNotice": notice,
    "#observabilityContent": createMockElement("div", "observabilityContent"),
    "#observationRange": createMockElement("select", "observationRange", { value: "15m" }),
    "#observationSource": createMockElement("select", "observationSource", { value: "all" }),
    "#observationLevel": createMockElement("select", "observationLevel", { value: "all" }),
    "#observationStatus": createMockElement("select", "observationStatus", { value: "all" }),
    "#observationStableId": createMockElement("input", "observationStableId", { value: "" }),
    "#observationScope": createMockElement("div", "observationScope"),
    "#refreshState": createMockElement("span", "refreshState"),
    "#taskCount": createMockElement("span", "taskCount"),
    "#logCount": createMockElement("span", "logCount"),
    "#taskRows": createMockElement("tbody", "taskRows"),
    "#logRows": createMockElement("div", "logRows"),
    "#loadMoreTasks": createMockElement("button", "loadMoreTasks"),
    "#loadMoreEvents": createMockElement("button", "loadMoreEvents"),
    "#loadMoreAssetVolumes": createMockElement("button", "loadMoreAssetVolumes"),
    "#taskCursorState": createMockElement("span", "taskCursorState"),
    "#cursorState": createMockElement("span", "cursorState"),
    "#taskDetail": createMockElement("dialog", "taskDetail"),
    "#taskToast": createMockElement("div", "taskToast"),
    '[data-start-index]': createMockElement("button", "startIndexBtn"),
    '[data-tab-count="tasks"]': tasksBadge,
    '[data-tab-count="logs"]': logsBadge,
  };

  main.appendChild(toolbar);
  main.appendChild(notice);
  main.appendChild(viewNav);
  main.appendChild(panelOverview);
  main.appendChild(panelTasks);
  main.appendChild(panelLogs);
  main.appendChild(panelMetrics);

  const docListeners = new Map();
  const docEl = createMockElement("html", "");
  const document = {
    hidden: false,
    activeElement: null,
    documentElement: docEl,
    querySelector(selector) {
      if (nodes[selector] && nodes[selector].parentNode !== null || selector in nodes && !selector.startsWith("#overview")) {
        // Check if removed from tree for panels
        if (selector === "#observabilityContent" || selector.startsWith("#observation") || selector.startsWith("#task") || selector.startsWith("#log") || selector.startsWith("#refresh") || selector.startsWith("#cursor") || selector.startsWith("#loadMore") || selector.startsWith("[data-tab-count") || selector === ".observability-toolbar") {
          return nodes[selector];
        }
      }
      return nodes[selector] ?? null;
    },
    querySelectorAll(selector) {
      if (selector === '[data-view="overview"], [data-view="metrics"]') {
        return [tabOverview, tabMetrics].filter((n) => n.parentNode);
      }
      if (selector === '[data-view-panel="overview"], [data-view-panel="metrics"]') {
        return [panelOverview, panelMetrics].filter((n) => n.parentNode);
      }
      if (selector === "[data-view-panel]") {
        return main.children.filter((n) => n.dataset?.viewPanel);
      }
      if (selector === "[data-view]") {
        return viewNav.children.filter((n) => n.dataset?.view);
      }
      return [];
    },
    addEventListener(type, fn) {
      if (!docListeners.has(type)) docListeners.set(type, []);
      docListeners.get(type).push(fn);
    },
  };
  Object.values(nodes).forEach((n) => {
    n._ownerDoc = document;
  });
  [tabOverview, tabTasks, tabLogs, tabMetrics].forEach((n) => {
    n._ownerDoc = document;
  });

  return { document, main, viewNav, toolbar, tabTasks, tabLogs, tasksBadge, logsBadge, nodes };
}

function bootTaskCenter(apiImpl, workspaceOverrides = {}) {
  const dom = buildEmbeddedDom();
  const winListeners = new Map();
  const intervalCallbacks = [];
  const windowObj = {
    self: {},
    top: {},
    parent: { postMessage() {} },
    location: {
      origin: "http://127.0.0.1:2077",
      href: "http://127.0.0.1:2077/static/embeds/task-center.html?embedded=1&view=tasks",
      search: "?embedded=1&view=tasks",
      hash: "",
    },
    addEventListener(type, fn) {
      if (!winListeners.has(type)) winListeners.set(type, []);
      winListeners.get(type).push(fn);
    },
    dispatchWindowEvent(type, evt = {}) {
      for (const fn of winListeners.get(type) || []) fn({ type, ...evt });
    },
    setTimeout,
    clearTimeout,
    setInterval(fn, ms) {
      intervalCallbacks.push({ fn, ms });
      return intervalCallbacks.length;
    },
    clearInterval() {},
    triggerAutoRefresh() {
      intervalCallbacks.forEach((entry) => entry.fn());
    },
  };

  const sandbox = {
    window: windowObj,
    self: windowObj.self,
    top: windowObj.top,
    document: dom.document,
    location: windowObj.location,
    history: { replaceState() {} },
    localStorage: { getItem: () => null },
    URL,
    URLSearchParams,
    AbortController,
    CSS: { escape: (v) => String(v) },
    setTimeout,
    clearTimeout,
    Workspace: {
      api: apiImpl,
      escapeHtml: (v) =>
        String(v ?? "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;"),
      dateTime: (v) => String(v || "—"),
      timeValue: (v) => String(v || "—"),
      navigate() {},
      reportTouchbarCapabilities() {},
      ...workspaceOverrides,
    },
  };
  vm.runInNewContext(taskCenterJs, sandbox);
  return { ...dom, windowObj, sandbox };
}

test("AC1/AC2/AC4: 嵌入态正常渲染落盘任务与运行日志，不抛 innerHTML 空节点错误，主按钮在筛选上方且键盘可达", async () => {
  const { main, viewNav, toolbar, tabTasks, tabLogs, tasksBadge, logsBadge, nodes } =
    bootTaskCenter(async (url) => {
      if (url.startsWith("/api/observability/tasks")) {
        return {
          data_status: "ok",
          items: [
            {
              job_id: "job-ws-001",
              status: "running",
              source: "asset_registry",
              job_type: "index",
              updated_at: "2026-11-18T12:00:00Z",
            },
          ],
        };
      }
      if (url.startsWith("/api/observability/events")) {
        return {
          data_status: "ok",
          items: [
            {
              event_id: "wsjob:job-ws-001:running",
              event_name: "job.running",
              level: "info",
              status: "running",
              source: "asset_registry",
              message_safe: "索引工作区素材中",
              timestamp: "2026-11-18T12:00:00Z",
              job_id: "job-ws-001",
            },
          ],
        };
      }
      return { items: [] };
    });

  await new Promise((r) => setTimeout(r, 20));

  // AC1: 落盘任务与运行事件均可见
  assert.match(nodes["#taskRows"].innerHTML, /job-ws-001/);
  assert.match(nodes["#logRows"].innerHTML, /索引工作区素材中/);
  assert.equal(tasksBadge.textContent, "1");
  assert.equal(logsBadge.textContent, "1");
  assert.equal(nodes["#observabilityNotice"].innerHTML, "");

  // AC4: 嵌入态移除总览/指标，且主按钮位于筛选工具条之前
  assert.equal(viewNav.children.length, 2);
  assert.ok(main.children.indexOf(viewNav) < main.children.indexOf(toolbar));

  // 键盘 ArrowRight 切换日志视图且保留计数徽标
  viewNav.dispatchEvent({ type: "keydown", key: "ArrowRight" });
  assert.equal(tabLogs.getAttribute("aria-selected"), "true");
  assert.equal(tabTasks.getAttribute("aria-selected"), "false");
  assert.equal(logsBadge.textContent, "1");
  assert.equal(tasksBadge.textContent, "1");
});

test("AC2: 接口失败时显示可见失败且保留旧数据，禁止画成成功空态", async () => {
  let failNext = false;
  const { nodes } = bootTaskCenter(async (url) => {
    if (failNext) throw new Error("后端观测接口 503 不可用");
    if (url.startsWith("/api/observability/tasks")) {
      return {
        data_status: "ok",
        items: [{ job_id: "job-keep-01", status: "running", source: "asset_registry" }],
      };
    }
    return { data_status: "ok", items: [] };
  });

  await new Promise((r) => setTimeout(r, 20));
  assert.match(nodes["#taskRows"].innerHTML, /job-keep-01/);

  // 触发失败的刷新：必须保留旧数据，且 notice 和 toast 一致报失败
  failNext = true;
  nodes["#observationRange"].value = "1h";
  nodes["#observationRange"].dispatchEvent({ type: "change" });
  await new Promise((r) => setTimeout(r, 20));

  assert.match(nodes["#taskRows"].innerHTML, /job-keep-01/);
  assert.match(nodes["#observabilityNotice"].innerHTML, /data-gw-degradation="error"/);
  assert.match(nodes["#taskToast"].textContent, /暂不可用|503|tasksUnavailable/);
});

test("AC2: 首次加载即失败时显示失败态而非暂无数据", async () => {
  const { nodes } = bootTaskCenter(async () => {
    throw new Error("网络连接失败");
  });

  await new Promise((r) => setTimeout(r, 20));
  assert.match(nodes["#taskRows"].innerHTML, /is-error/);
  assert.doesNotMatch(nodes["#taskRows"].innerHTML, /暂无任务/);
  assert.match(nodes["#logRows"].innerHTML, /is-error/);
  assert.match(nodes["#observabilityNotice"].innerHTML, /data-gw-degradation="error"/);
});

test("AC6: 慢请求（1500ms / 5000ms）不提前标就绪，新请求取消旧请求且乱序不覆盖", async () => {
  const calls = [];
  const { nodes, windowObj } = bootTaskCenter((url, options = {}) => {
    return new Promise((resolve, reject) => {
      const idx = calls.length;
      calls.push({ url, signal: options.signal, resolve, reject });
    });
  });

  // 刚发起第 1 次慢请求：此时处于加载中，绝不能标成「暂无任务」或已就绪
  await new Promise((r) => setTimeout(r, 5));
  assert.ok(calls.length >= 2);
  assert.doesNotMatch(nodes["#taskRows"].innerHTML, /暂无任务|emptyTasks/);
  assert.match(nodes["#refreshState"].textContent, /载入|刷新|refreshing|loading/);

  const firstTaskSignal = calls[1].signal;
  assert.equal(firstTaskSignal.aborted, false);

  // 第 2 次切换筛选条件：必须立即取消第 1 次请求
  nodes["#observationRange"].value = "24h";
  nodes["#observationRange"].dispatchEvent({ type: "change" });
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(firstTaskSignal.aborted, true);

  // 先让第 2 次请求返回新数据
  const latestTasksCall = calls[calls.length - 1];
  const latestEventsCall = calls[calls.length - 2];
  latestEventsCall.resolve({ data_status: "ok", items: [] });
  latestTasksCall.resolve({
    data_status: "ok",
    items: [{ job_id: "job-latest-gen2", status: "succeeded", source: "video_tasks" }],
  });
  await new Promise((r) => setTimeout(r, 15));
  assert.match(nodes["#taskRows"].innerHTML, /job-latest-gen2/);

  // 即使第 1 次旧请求此时乱序 resolve 旧数据，也绝不能覆盖新代际
  calls[1].resolve({
    data_status: "ok",
    items: [{ job_id: "job-stale-gen1", status: "failed", source: "god_canvas" }],
  });
  await new Promise((r) => setTimeout(r, 15));
  assert.match(nodes["#taskRows"].innerHTML, /job-latest-gen2/);
  assert.doesNotMatch(nodes["#taskRows"].innerHTML, /job-stale-gen1/);

  // pagehide 触发取消在途请求
  nodes["#observationRange"].value = "15m";
  nodes["#observationRange"].dispatchEvent({ type: "change" });
  await new Promise((r) => setTimeout(r, 5));
  const thirdSignal = calls[calls.length - 1].signal;
  assert.equal(thirdSignal.aborted, false);
  windowObj.dispatchWindowEvent("pagehide");
  assert.equal(thirdSignal.aborted, true);
});

test("AC6 必改1: 5000ms 自动刷新定时器在慢请求在途时跳过而不中止慢请求", async () => {
  const calls = [];
  const { nodes, windowObj } = bootTaskCenter((url, options = {}) => {
    return new Promise((resolve, reject) => {
      calls.push({ url, signal: options.signal, resolve, reject });
    });
  });

  await new Promise((r) => setTimeout(r, 5));
  assert.equal(calls.length, 2);
  const initialEventsSignal = calls[0].signal;
  const initialTasksSignal = calls[1].signal;

  // 模拟 5000ms 到期触发自动刷新定时器：在途慢请求绝不能被 abort()
  windowObj.triggerAutoRefresh();
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(calls.length, 2, "慢请求在途时后台自动刷新不得重发");
  assert.equal(initialEventsSignal.aborted, false);
  assert.equal(initialTasksSignal.aborted, false);

  // 5000ms 慢请求最终返回，正常落地渲染
  calls[0].resolve({ data_status: "ok", items: [] });
  calls[1].resolve({
    data_status: "ok",
    items: [{ job_id: "job-slow-5000ms", status: "running", source: "asset_registry" }],
  });
  await new Promise((r) => setTimeout(r, 15));
  assert.match(nodes["#taskRows"].innerHTML, /job-slow-5000ms/);
});

test("AC2 必改3: HTTP 200 + SOURCE_UNAVAILABLE 降级空结果不得画成暂无日志，render 异常须置列表错态", async () => {
  const { nodes, viewNav, tabLogs } = bootTaskCenter(async (url) => {
    if (url.startsWith("/api/observability/events")) {
      return {
        data_status: "degraded",
        items: [],
        data_gaps: ["SOURCE_UNAVAILABLE:agent_runtime"],
        degraded_sources: ["agent_runtime"],
      };
    }
    return { data_status: "ok", items: [] };
  });

  await new Promise((r) => setTimeout(r, 20));
  assert.match(nodes["#logRows"].innerHTML, /is-error/);
  assert.doesNotMatch(nodes["#logRows"].innerHTML, /暂无日志|emptyLogs/);
  assert.match(nodes["#observabilityNotice"].innerHTML, /data-gw-degradation="error"/);

  // 模拟 render 期间异常：在 dateTime 抛出异常，验证 #taskRows 与 #logRows 均切为错误态且视图切换不中断
  const broken = bootTaskCenter(
    async () => ({ data_status: "ok", items: [] }),
    {
      dateTime() {
        throw new Error("模拟渲染异常");
      },
    },
  );
  await new Promise((r) => setTimeout(r, 20));
  assert.match(broken.nodes["#taskRows"].innerHTML, /is-error/);
  assert.doesNotMatch(broken.nodes["#taskRows"].innerHTML, /等待真实数据|暂无任务/);
  assert.match(broken.nodes["#logRows"].innerHTML, /is-error/);
  broken.viewNav.dispatchEvent({ type: "keydown", key: "ArrowRight" });
  assert.equal(broken.tabLogs.getAttribute("aria-selected"), "true");
});
