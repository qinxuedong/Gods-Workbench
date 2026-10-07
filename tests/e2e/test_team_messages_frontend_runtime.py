# -*- coding: utf-8 -*-
"""协作页团队消息运行时回归：在 Node 沙箱中真实执行控制器 JavaScript。"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "web" / "js" / "controllers" / "collab-controller.js"
NODE = shutil.which("node")

# 最小 DOM 与真实 fetch 控制器沙箱；测试行为，不依赖源码关键字。
HARNESS = r"""
import fs from "node:fs";
import vm from "node:vm";

const [controllerPath, scenario] = process.argv.slice(2);
const source = fs.readFileSync(controllerPath, "utf8");
const calls = [];
const timers = new Map();
let timerSequence = 0;
let abortedReads = 0;
let abortedPosts = 0;
let heldRead = false;
let heldPost = false;
const teamId = "team-alpha";
const idempotency = new Map();
const messages = scenario === "history"
  ? Array.from({length: 75}, (_, index) => makeMessage(index + 1, `历史消息-${index + 1}`))
  : scenario === "resume-read"
    ? [makeMessage(1, "离页后读回")]
    : [];

function makeMessage(sequence, text) {
  return {
    message_id: `msg-${teamId}-${sequence}`, team_id: teamId,
    author_user_id: "user-1", text, created_at: "2026-09-27T00:00:00.000Z", sequence,
  };
}

class EventTargetLite {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener, options = {}) {
    const signal = options && options.signal;
    if (signal?.aborted) return;
    const entries = this.listeners.get(type) || [];
    const entry = {listener, signal, abortHandler: null};
    if (signal) {
      entry.abortHandler = () => this.removeEventListener(type, listener);
      signal.addEventListener("abort", entry.abortHandler, {once: true});
    }
    entries.push(entry);
    this.listeners.set(type, entries);
  }
  removeEventListener(type, listener) {
    const entries = this.listeners.get(type) || [];
    for (const entry of entries.filter(item => item.listener === listener)) {
      if (entry.signal && entry.abortHandler) entry.signal.removeEventListener("abort", entry.abortHandler);
    }
    this.listeners.set(type, entries.filter(item => item.listener !== listener));
  }
  async emit(type, event = {}) {
    const entries = [...(this.listeners.get(type) || [])];
    await Promise.all(entries.map(entry => Promise.resolve(entry.listener(event))));
  }
}

function makeElement(id = "") {
  const element = Object.assign(new EventTargetLite(), {
    id, _attrs: {}, _classes: new Set(), textContent: "", innerHTML: "", title: "",
    style: {}, dataset: {}, hidden: false, disabled: false, value: "", href: "",
    children: [], scrollHeight: 0, scrollTop: 0, clientHeight: 0,
    appendChild(child) { this.children.push(child); return child; },
    append(...children) { this.children.push(...children); },
    prepend(...children) { this.children.unshift(...children); },
    replaceChildren(...children) { this.children = [...children]; },
    setAttribute(name, value) { this._attrs[name] = String(value); },
    removeAttribute(name) { delete this._attrs[name]; },
    getAttribute(name) { return this._attrs[name] ?? null; },
    hasAttribute(name) { return Object.hasOwn(this._attrs, name); },
  });
  element.classList = {
    add: (...names) => names.forEach(name => element._classes.add(name)),
    remove: (...names) => names.forEach(name => element._classes.delete(name)),
    contains: name => element._classes.has(name),
    toggle(name, force) {
      const enabled = force === undefined ? !element._classes.has(name) : Boolean(force);
      if (enabled) element._classes.add(name); else element._classes.delete(name);
      return enabled;
    },
  };
  return element;
}

const elements = new Map();
const byId = id => {
  if (!elements.has(id)) elements.set(id, makeElement(id));
  return elements.get(id);
};
const tabs = ["tasks", "logs"].map(view => {
  const tab = byId(`tab-${view}`);
  tab.dataset.collabView = view;
  return tab;
});
const routeLink = byId("collab-route-link");
const documentLite = Object.assign(new EventTargetLite(), {
  readyState: "complete", hidden: false,
  getElementById: byId,
  querySelectorAll(selector) { return selector === "[data-collab-view]" ? tabs : []; },
  querySelector(selector) { return selector === "[data-collab-route-link]" ? routeLink : null; },
  createElement() { return makeElement(); },
});
const windowLite = new EventTargetLite();
windowLite.setTimeout = (callback, delay) => {
  const id = ++timerSequence;
  timers.set(id, {callback, delay});
  return id;
};
windowLite.clearTimeout = id => timers.delete(id);
windowLite.crypto = {randomUUID: () => "retry-stable-id"};
windowLite.lucide = {createIcons() {}};

const locationLite = {
  search: `?team_id=${teamId}`,
  href: `http://workbench.local/static/pages/collab.html?team_id=${teamId}`,
};
const historyLite = {state: null, replaceState(_state, _title, url) { locationLite.href = String(url); }};
globalThis.window = windowLite;
globalThis.document = documentLite;
globalThis.location = locationLite;
globalThis.history = historyLite;
globalThis.fetch = fetchStub;

function response(body, status = 200) {
  return {ok: status >= 200 && status < 300, status, async json() { return body; }};
}
function abortError() {
  const error = new Error("请求已取消");
  error.name = "AbortError";
  return error;
}
function holdUntilAbort(signal, onAbort) {
  return new Promise((_resolve, reject) => {
    if (!signal || signal.aborted) {
      onAbort();
      reject(abortError());
      return;
    }
    signal.addEventListener("abort", () => {
      onAbort();
      reject(abortError());
    }, {once: true});
  });
}
function cursorFor(sequence) {
  return Buffer.from(`${teamId}:${sequence}`, "utf8").toString("base64url");
}
function pageMessages(url) {
  let offset = Number(url.searchParams.get("after_sequence") || 0);
  const cursor = url.searchParams.get("cursor");
  if (cursor) offset = Number(Buffer.from(cursor, "base64url").toString("utf8").split(":").at(-1));
  const limit = Number(url.searchParams.get("limit") || 100);
  const page = messages.filter(message => message.sequence > offset).slice(0, limit);
  return response({
    team_id: teamId,
    messages: page,
    can_send: scenario !== "readonly",
    next_cursor: page.length === limit ? cursorFor(page.at(-1).sequence) : null,
    next_after_sequence: page.at(-1)?.sequence ?? offset,
  });
}
async function fetchStub(rawUrl, init = {}) {
  const url = new URL(rawUrl, "http://workbench.local");
  const method = String(init.method || "GET").toUpperCase();
  calls.push({url: `${url.pathname}${url.search}`, method, body: init.body || null});
  if (url.pathname === "/api/asset-auth/users") return response({users: []});
  if (url.pathname === "/api/asset-auth/teams") return response({teams: [{team_id: teamId, name: "回归团队", members: []}]});
  if (url.pathname === "/api/asset-auth/operation-approvals") return response({approvals: []});
  if (url.pathname.startsWith("/api/observability/")) return response({items: [], has_more: false});
  if (url.pathname === "/api/asset-auth/identity-binding") return response({bound: true, user_id: "user-1"});
  if (url.pathname === `/api/asset-auth/teams/${teamId}/messages` && method === "GET") {
    if (scenario === "resume-read" && !heldRead) {
      heldRead = true;
      return holdUntilAbort(init.signal, () => { abortedReads += 1; });
    }
    return pageMessages(url);
  }
  if (url.pathname === `/api/asset-auth/teams/${teamId}/messages` && method === "POST") {
    const payload = JSON.parse(init.body || "{}");
    const postCount = calls.filter(call => call.method === "POST" && call.url.startsWith(`/api/asset-auth/teams/${teamId}/messages`)).length;
    let message = idempotency.get(payload.client_request_id);
    const replayed = Boolean(message);
    if (!message) {
      message = {...makeMessage(messages.length + 1, payload.text), replayed: false};
      messages.push(message);
      idempotency.set(payload.client_request_id, message);
    }
    if (["resume-send", "switch-send"].includes(scenario) && postCount === 1 && !heldPost) {
      heldPost = true;
      return holdUntilAbort(init.signal, () => { abortedPosts += 1; });
    }
    return response({...message, replayed}, replayed ? 200 : 201);
  }
  return response({detail: {code: "UNEXPECTED_ROUTE", message: url.pathname}}, 404);
}

async function waitFor(predicate, description) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (predicate()) return;
    await new Promise(resolve => setImmediate(resolve));
  }
  throw new Error(`等待超时：${description}`);
}
async function runTimer(delay) {
  const found = [...timers.entries()].find(([, timer]) => timer.delay === delay);
  if (!found) throw new Error(`未找到 ${delay}ms 定时器`);
  timers.delete(found[0]);
  await found[1].callback();
}
function messageBodies() {
  return byId("collabReviewFeed").children
    .filter(article => article.className === "collab-feed-item collab-task-item")
    .map(article => article.children[1]?.textContent || "");
}
function messageCalls() {
  return calls.filter(call => call.url.startsWith(`/api/asset-auth/teams/${teamId}/messages`));
}

vm.runInThisContext(source, {filename: controllerPath});

if (scenario === "readonly") {
  await waitFor(() => byId("collabMessageStatus").textContent.includes("只读"), "服务端只读权限显示");
  const input = byId("collabMessageInput");
  input.value = "不可发送";
  await input.emit("input", {target: input});
  await byId("collabMessageSend").emit("click", {});
  process.stdout.write(JSON.stringify({inputDisabled: input.disabled, sendDisabled: byId("collabMessageSend").disabled,
    posts: messageCalls().filter(call => call.method === "POST").length}));
} else if (scenario === "history") {
  await waitFor(() => messageCalls().some(call => call.method === "GET") && !byId("collabMessageHistoryMore").hidden, "首屏50条历史消息");
  const initialUrl = messageCalls().find(call => call.method === "GET").url;
  await runTimer(5000);
  await waitFor(() => messageCalls().filter(call => call.method === "GET").length === 2, "增量轮询完成");
  const buttonVisibleAfterPoll = !byId("collabMessageHistoryMore").hidden;
  const stateAfterPoll = byId("collabMessageState").textContent;
  await byId("collabMessageHistoryMore").emit("click", {target: byId("collabMessageHistoryMore")});
  const gets = messageCalls().filter(call => call.method === "GET");
  const historyUrl = gets[2]?.url || "";
  const bodies = messageBodies();
  process.stdout.write(JSON.stringify({
    initialUrl, pollUrl: gets[1]?.url || "", historyUrl,
    buttonVisibleAfterPoll, stateAfterPoll, messageCount: bodies.length,
    uniqueMessageCount: new Set(bodies).size, firstMessage: bodies[0], lastMessage: bodies.at(-1),
  }));
} else if (scenario === "resume-read") {
  await waitFor(() => messageCalls().filter(call => call.method === "GET").length === 1, "在途历史读取开始");
  await windowLite.emit("pagehide");
  await waitFor(() => abortedReads === 1, "离页取消在途读取");
  await windowLite.emit("pageshow");
  await waitFor(() => messageCalls().filter(call => call.method === "GET").length === 2 && messageBodies().length === 1, "BFCache返回后重新读取消息");
  process.stdout.write(JSON.stringify({
    readCalls: messageCalls().filter(call => call.method === "GET").length,
    abortedReads, messages: messageBodies(), state: byId("collabMessageState").textContent,
  }));
} else if (["resume-send", "switch-send"].includes(scenario)) {
  await waitFor(() => messageCalls().filter(call => call.method === "GET").length === 1 && byId("collabMessageState").textContent.includes("序号 0"), "初始团队消息读取完成");
  const input = byId("collabMessageInput");
  const send = byId("collabMessageSend");
  input.value = "离页后安全重试";
  await input.emit("input", {target: input});
  if (send.disabled) throw new Error("初始读取后发送按钮仍被禁用");
  const firstSend = send.emit("click", {target: send});
  await waitFor(() => messageCalls().some(call => call.method === "POST"), "首个发送请求开始");
  if (scenario === "switch-send") await byId("collabTeamSelect").emit("change", {target:{value:""}});
  else await windowLite.emit("pagehide");
  await waitFor(() => abortedPosts === 1, "离页或切队取消在途发送" );
  await firstSend;
  if (scenario === "switch-send") await byId("collabTeamSelect").emit("change", {target:{value:teamId}});
  else await windowLite.emit("pageshow");
  await waitFor(() => messageCalls().filter(call => call.method === "GET").length === 2 && messageBodies().length === 1, "BFCache返回后恢复读取");
  const sendEnabledAfterRestore = !send.disabled;
  if (!sendEnabledAfterRestore) {
    process.stdout.write(JSON.stringify({sendEnabledAfterRestore, abortedPosts, postBodies: messageCalls().filter(call => call.method === "POST").map(call => JSON.parse(call.body))}));
  } else {
    await send.emit("click", {target: send});
    const posts = messageCalls().filter(call => call.method === "POST").map(call => JSON.parse(call.body));
    process.stdout.write(JSON.stringify({
      sendEnabledAfterRestore, abortedPosts, posts,
      messageCount: messageBodies().length, status: byId("collabMessageStatus").textContent,
    }));
  }
} else {
  throw new Error(`未知场景：${scenario}`);
}
"""


def _run_scenario(scenario: str, tmp_path: Path) -> dict:
    """在隔离 Node 进程中运行当前控制器及指定交互场景。"""
    assert NODE, "团队消息运行时回归需要 Node.js，不能静默跳过"
    harness_path = tmp_path / "team_messages_frontend_harness.mjs"
    harness_path.write_text(HARNESS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(harness_path), str(CONTROLLER), scenario],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, f"协作页 JS 场景执行失败：{result.stderr[:3000]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_team_history_cursor_survives_incremental_poll_and_deduplicates_overlap(tmp_path):
    """超过50条历史消息轮询后仍可继续翻页；重叠序号只在列表保留一份。"""
    result = _run_scenario("history", tmp_path)
    assert result["buttonVisibleAfterPoll"] is True, result
    assert "after_sequence=50" in result["pollUrl"]
    assert "cursor=" in result["historyUrl"] and "after_sequence=" not in result["historyUrl"]
    assert result["messageCount"] == 75, result
    assert result["uniqueMessageCount"] == 75, result
    assert result["firstMessage"] == "历史消息-1"
    assert result["lastMessage"] == "历史消息-75"


def test_team_read_in_flight_is_released_and_reloaded_after_bfcache_restore(tmp_path):
    """离页中止的历史读取不得遗留 busy 锁，BFCache 返回应重新发起读取。"""
    result = _run_scenario("resume-read", tmp_path)
    assert result["abortedReads"] == 1, result
    assert result["readCalls"] == 2, result
    assert result["messages"] == ["离页后读回"], result


@pytest.mark.parametrize("scenario", ["resume-send", "switch-send"])
def test_team_send_in_flight_can_retry_same_idempotency_key_after_bfcache_restore(tmp_path, scenario):
    """离页发送不确定时恢复发送能力，并复用已提交请求的幂等键。"""
    result = _run_scenario(scenario, tmp_path)
    assert result["sendEnabledAfterRestore"] is True, result
    assert result["abortedPosts"] == 1, result
    assert len(result["posts"]) == 2, result
    assert result["posts"][0]["client_request_id"] == result["posts"][1]["client_request_id"], result
    assert result["posts"][0]["text"] == result["posts"][1]["text"] == "离页后安全重试"
    assert result["messageCount"] == 1, result
    assert "幂等重放" in result["status"], result


def test_team_readonly_response_disables_composer_and_never_posts(tmp_path):
    result = _run_scenario("readonly", tmp_path)
    assert result == {"inputDisabled": True, "sendDisabled": True, "posts": 0}
