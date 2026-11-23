import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

const REPO_ROOT = path.resolve(import.meta.dirname, "../..");
const SETTINGS_JS = path.join(REPO_ROOT, "web/js/modules/asset-manager/settings.js");

function loadSettingsHarness() {
  const source = fs.readFileSync(SETTINGS_JS, "utf8");
  const start = source.indexOf("const DATA_LOCATION_TIMEOUT_MS =");
  const end = source.indexOf("async function startDataLocationMigration");
  assert.ok(start > -1 && end > start, "应能定位 dataLocationState 模块片段");
  const snippet = source.slice(start, end);

  let fetchImpl = async () => ({ ok: true, json: async () => ({}) });
  let domHtml = "";
  const mockHost = {
    querySelector(selector) {
      if (selector === "[data-board-data-location]") {
        return {
          set outerHTML(val) {
            domHtml = String(val);
          },
          get outerHTML() {
            return domHtml;
          },
        };
      }
      return null;
    },
  };

  const context = {
    document: {
      getElementById: (id) => (id === "storageSettingsOverlay" ? mockHost : null),
      querySelectorAll: () => [],
    },
    fetch: (url, opts) => fetchImpl(url, opts),
    apiJsonResponse: async (p) => {
      const res = await p;
      return typeof res.json === "function" ? res.json() : res;
    },
    escapeAttr: (v) => String(v),
    escapeHtml: (v) => String(v),
    refreshIcons: () => {},
    setTimeout,
    clearTimeout,
    AbortController,
    console,
  };
  vm.createContext(context);
  vm.runInContext(
    `${snippet}\nthis.__test = { dataLocationState, loadDataLocationData, checkDataLocationTarget, renderDataLocationCard };`,
    context
  );
  return {
    ...context.__test,
    setFetch: (fn) => {
      fetchImpl = fn;
    },
    getDomHtml: () => domHtml,
  };
}

test("QIN-638: 数据位置卡片渲染生效软件目录与当前数据位置且支持长路径断行", () => {
  const h = loadSettingsHarness();
  h.dataLocationState.ready = true;
  h.dataLocationState.snapshot = {
    install_dir: "C:\\Program Files\\Gods Workbench",
    bootstrap_root: "C:\\Users\\demo\\AppData\\Local\\GodsWorkbench",
    data_location: "C:\\Users\\demo\\AppData\\Local\\Gods Workbench data",
    pointer: { present: true, revision: 1 },
    disk: { free_bytes: 1024 * 1024 * 1024, total_bytes: 2 * 1024 * 1024 * 1024 },
    categories: [],
    write_window: { state: "open" },
    active_jobs: [],
  };
  const html = h.renderDataLocationCard();
  assert.match(html, /生效软件目录/);
  assert.match(html, /C:\\Program Files\\Gods Workbench/);
  assert.match(html, /C:\\Users\\demo\\AppData\\Local\\Gods Workbench data/);
  assert.match(html, /overflow-wrap:anywhere/);
});

test("QIN-638: 读取超时转入可见失败并丢弃过时乱序返回", async () => {
  const h = loadSettingsHarness();
  h.dataLocationState.timeoutMs = 30;
  h.dataLocationState.snapshot = {
    install_dir: "C:\\Program Files\\Gods Workbench",
    data_location: "C:\\Users\\demo\\AppData\\Local\\Gods Workbench data",
  };
  h.setFetch((_url, opts) => {
    return new Promise((_resolve, reject) => {
      opts.signal.addEventListener("abort", () => reject(new Error("Aborted")));
    });
  });

  await h.loadDataLocationData();
  assert.equal(h.dataLocationState.loading, false);
  assert.match(h.dataLocationState.error, /超时/);
  assert.ok(h.dataLocationState.snapshot, "超时失败后不得丢掉失败前已有正确快照");

  // 验证乱序：先发慢请求 G1，再发快请求 G2，G1 晚到不能覆盖 G2
  h.dataLocationState.timeoutMs = 2000;
  let resolveSlow;
  let callCount = 0;
  h.setFetch(() => {
    callCount += 1;
    if (callCount === 1) {
      return new Promise((resolve) => {
        resolveSlow = resolve;
      });
    }
    return Promise.resolve({
      json: async () => ({
        install_dir: "C:\\Program Files\\Gods Workbench",
        data_location: "D:\\FastNewData",
        pointer: { present: true, revision: 2 },
        write_window: { state: "open" },
      }),
    });
  });

  const p1 = h.loadDataLocationData();
  const p2 = h.loadDataLocationData();
  await p2;
  resolveSlow({
    json: async () => ({
      install_dir: "C:\\Old",
      data_location: "D:\\StaleOldData",
      pointer: { present: true, revision: 1 },
      write_window: { state: "open" },
    }),
  });
  await p1;
  assert.equal(h.dataLocationState.snapshot.data_location, "D:\\FastNewData");
});
