import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const repoRoot = process.cwd();
const managerSource = readFileSync(
  path.join(repoRoot, "web/js/modules/asset-manager.js"),
  "utf8",
);
const settingsSource = readFileSync(
  path.join(repoRoot, "web/js/modules/asset-manager/settings.js"),
  "utf8",
);

function sliceBetween(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `missing slice: ${startMarker} .. ${endMarker}`);
  return source.slice(start, end);
}

function createDirectoryLoaderHarness({
  principalDelay = 0,
  principalError = null,
  storageImpl = async () => ({ dirs: { local: "/data/local", upload: "/data/upload", generated: "/data/gen" }, revision: 1 }),
  rootsImpl = async () => ({ roots: [], revision: 1 }),
  tauriMode = false,
  tauriUploadCallImpl = async () => {},
  useFakeTimers = false,
} = {}) {
  const loaderSource = sliceBetween(
    managerSource,
    "async function loadGlobalDirectorySettings()",
    "let pendingPurgeRoot = null;",
  );
  const timers = [];
  const clearedTimers = new Set();
  let nextTimerId = 1;
  const fakeSetTimeout = useFakeTimers
    ? (fn, ms) => {
        const id = nextTimerId++;
        timers.push({ id, fn, ms });
        return id;
      }
    : setTimeout;
  const fakeClearTimeout = useFakeTimers
    ? (id) => {
        clearedTimers.add(id);
      }
    : clearTimeout;

  const state = {
    storageSettingsState: { open: false },
    directorySettingsError: "",
    directorySettingsReady: false,
    currentStatusText: "准备就绪",
    registryFacets: { kinds: [], categories: [], tags: [], collections: [], roots: [] },
    registryStatus: { overview: { roots: [] } },
    gwCalls: [],
  };

  const factory = new Function(
    "storageSettingsState",
    "directorySettingsError",
    "directorySettingsReady",
    "gwCall",
    "loadRegistryPrincipal",
    "canManageProjectWorkspaceSettings",
    "apiJsonResponse",
    "assetManagerApi",
    "canManageAllowedRoots",
    "assetManagerHttp",
    "applyStorageSettingsData",
    "registryStatus",
    "applyThumbnailSettingsSnapshot",
    "isTauriClientMode",
    "loadClientUploadSourceDir",
    "currentStatusText",
    "setStatus",
    "registryFacets",
    "setTimeout",
    "clearTimeout",
    `${loaderSource}; return {
      loadGlobalDirectorySettings,
      cancelGlobalDirectorySettingsLoad,
      getState: () => ({
        directorySettingsReady,
        directorySettingsError,
        currentStatusText,
      }),
    };`,
  );

  const runtime = factory(
    state.storageSettingsState,
    state.directorySettingsError,
    state.directorySettingsReady,
    (domain, method, ...args) => {
      state.gwCalls.push([domain, method, ...args]);
      if (domain === "upload" && method === "loadClientUploadSourceDir") {
        return tauriUploadCallImpl();
      }
      return undefined;
    },
    async () => {
      if (principalDelay > 0) {
        await new Promise((resolve) => setTimeout(resolve, principalDelay));
      }
      if (principalError) throw principalError;
    },
    () => true,
    async (req) => {
      if (typeof req === "function") return await req();
      return await req;
    },
    {
      getStorageSettings: () => storageImpl(),
      getThumbnailSettings: () => Promise.resolve({ mode: "default" }),
      getRegistryStatus: () => Promise.resolve({ ready: true }),
      getRegistryFacets: () => Promise.resolve({ roots: [] }),
    },
    () => true,
    {
      request: (url, init) => {
        if (url === "/api/storage-settings/roots") return rootsImpl(init);
        return Promise.resolve({ active_workspace_id: "ws_local_01" });
      },
    },
    () => {},
    state.registryStatus,
    () => {},
    () => tauriMode,
    async () => {},
    state.currentStatusText,
    () => {},
    state.registryFacets,
    fakeSetTimeout,
    fakeClearTimeout,
  );

  return {
    state,
    runtime,
    flushTimersMinMs(minMs) {
      for (const t of timers.slice()) {
        if (!clearedTimers.has(t.id) && t.ms <= minMs) {
          clearedTimers.add(t.id);
          t.fn();
        }
      }
    },
  };
}

function createDataLocationHarness({ fetchImpl, useFakeTimers = false } = {}) {
  const dataLocationSource = sliceBetween(
    settingsSource,
    "const DATA_LOCATION_LOAD_TIMEOUT_MS = 8000;",
    "function selectedDataLocationCategories()",
  );
  const clickSource = sliceBetween(
    settingsSource,
    "function handleDataLocationClick(event)",
    "function ensureDataLocationBindings(host)",
  );
  const timers = [];
  const clearedTimers = new Set();
  let nextTimerId = 1;
  const fakeSetTimeout = useFakeTimers
    ? (fn, ms) => {
        const id = nextTimerId++;
        timers.push({ id, fn, ms });
        return id;
      }
    : setTimeout;
  const fakeClearTimeout = useFakeTimers
    ? (id) => {
        clearedTimers.add(id);
      }
    : clearTimeout;

  const domCards = [];
  const cardEl = { outerHTML: "" };
  const rootEl = {
    querySelector(selector) {
      if (selector === "[data-board-data-location]") return cardEl;
      return null;
    },
  };
  const fakeDocument = {
    getElementById(id) {
      if (id === "assetManagerRoot") return rootEl;
      return null;
    },
  };

  const factory = new Function(
    "document",
    "fetch",
    "apiJsonResponse",
    "escapeHtml",
    "escapeAttr",
    "refreshIcons",
    "setTimeout",
    "clearTimeout",
    `${dataLocationSource}\n${clickSource}\nreturn {
      dataLocationState,
      renderDataLocationCard,
      refreshDataLocationDom,
      loadDataLocationData,
      handleDataLocationClick,
    };`,
  );

  const runtime = factory(
    fakeDocument,
    fetchImpl,
    async (req) => await req,
    (v) => String(v ?? ""),
    (v) => String(v ?? ""),
    () => {},
    fakeSetTimeout,
    fakeClearTimeout,
  );

  return {
    runtime,
    cardEl,
    domCards,
    flushTimersMinMs(minMs) {
      for (const t of timers.slice()) {
        if (!clearedTimers.has(t.id) && t.ms <= minMs) {
          clearedTimers.add(t.id);
          t.fn();
        }
      }
    },
  };
}

test("QIN-636: 目录设置正常与空态可在 3s 内渲染并在完成后标为 ready", async () => {
  const { runtime, state } = createDirectoryLoaderHarness();
  const pending = runtime.loadGlobalDirectorySettings();
  assert.equal(runtime.getState().directorySettingsReady, false);
  assert.equal(runtime.getState().directorySettingsError, "");
  assert.ok(
    state.gwCalls.some((c) => c[0] === "settings" && c[1] === "renderGlobalDirectorySettings"),
    "进入读取时立即触发 loading 渲染",
  );
  await pending;
  assert.equal(runtime.getState().directorySettingsReady, true);
  assert.equal(runtime.getState().directorySettingsError, "");
  assert.equal(runtime.getState().currentStatusText, "目录已同步");
});

test("QIN-636: 8s 超时中止请求并展示重试，9s/20s 迟到返回不覆盖", async () => {
  let resolveSlowStorage;
  let aborted = false;
  const harness = createDirectoryLoaderHarness({
    useFakeTimers: true,
    storageImpl: () => new Promise((resolve) => {
      resolveSlowStorage = resolve;
    }),
    rootsImpl: (init) =>
      new Promise(() => {
        init?.signal?.addEventListener?.("abort", () => {
          aborted = true;
        });
      }),
  });

  const promise = harness.runtime.loadGlobalDirectorySettings();
  await new Promise((r) => setImmediate(r));
  harness.flushTimersMinMs(8000);
  await promise;

  assert.equal(aborted, true, "超时后应中止在途请求");
  assert.equal(harness.runtime.getState().directorySettingsReady, false);
  assert.match(harness.runtime.getState().directorySettingsError, /超时/);

  // 模拟 9s / 20s 后迟到返回，不得将失败态改写为 ready
  resolveSlowStorage?.({ dirs: { local: "/late" }, revision: 1 });
  await Promise.resolve();
  assert.equal(harness.runtime.getState().directorySettingsReady, false);
  assert.match(harness.runtime.getState().directorySettingsError, /超时/);
});

test("QIN-636: Tauri 客户端模式读取上传源目录挂起时在 3s 降级，不阻塞目录设置就绪", async () => {
  const harness = createDirectoryLoaderHarness({
    useFakeTimers: true,
    tauriMode: true,
    tauriUploadCallImpl: () => new Promise(() => {}),
  });

  const promise = harness.runtime.loadGlobalDirectorySettings();
  await new Promise((r) => setImmediate(r));
  harness.flushTimersMinMs(3000);
  await promise;

  assert.equal(harness.runtime.getState().directorySettingsReady, true);
  assert.equal(harness.runtime.getState().directorySettingsError, "");
});

test("QIN-636: 重试与乱序防覆盖——旧代际失败不得覆盖新代际成功，取消后旧请求不写回", async () => {
  let rejectFirst;
  let attempt = 0;
  const harness = createDirectoryLoaderHarness({
    storageImpl: () => {
      attempt += 1;
      if (attempt === 1) {
        return new Promise((_, reject) => {
          rejectFirst = reject;
        });
      }
      return Promise.resolve({ dirs: { local: "/ok" }, revision: 2 });
    },
  });

  const first = harness.runtime.loadGlobalDirectorySettings();
  await new Promise((r) => setImmediate(r));
  const second = harness.runtime.loadGlobalDirectorySettings();
  await second;
  assert.equal(harness.runtime.getState().directorySettingsReady, true);

  rejectFirst(new Error("第一代请求迟到失败"));
  await first;
  assert.equal(harness.runtime.getState().directorySettingsReady, true);
  assert.equal(harness.runtime.getState().directorySettingsError, "");

  // 取消正在进行的第 3 代请求
  attempt = 0;
  const third = harness.runtime.loadGlobalDirectorySettings();
  await new Promise((r) => setImmediate(r));
  harness.runtime.cancelGlobalDirectorySettingsLoad();
  rejectFirst(new Error("取消后的失败"));
  await third;
  assert.equal(harness.runtime.getState().directorySettingsError, "");
});

test("QIN-636: 数据位置子卡片兼容 #assetManagerRoot、8s 超时离开 loading 且可点击重试恢复", async () => {
  let callCount = 0;
  let aborted = false;
  const harness = createDataLocationHarness({
    useFakeTimers: true,
    fetchImpl: (_url, init) => {
      callCount += 1;
      if (callCount === 1) {
        return new Promise(() => {
          init?.signal?.addEventListener?.("abort", () => {
            aborted = true;
          });
        });
      }
      return Promise.resolve({
        bootstrap_root: "/app",
        data_location: "/data",
        pointer: { revision: 1, present: false },
        disk: { free_bytes: 1024, total_bytes: 2048 },
        categories: [],
        active_jobs: 0,
      });
    },
  });

  const first = harness.runtime.loadDataLocationData();
  assert.equal(harness.runtime.dataLocationState.loading, true);
  assert.match(harness.cardEl.outerHTML, /正在读取数据位置…/);

  harness.flushTimersMinMs(8000);
  await first;

  assert.equal(aborted, true);
  assert.equal(harness.runtime.dataLocationState.loading, false);
  assert.equal(harness.runtime.dataLocationState.ready, false);
  assert.match(harness.cardEl.outerHTML, /超时/);
  assert.match(harness.cardEl.outerHTML, /data-data-location-retry/);

  // 点击重试按钮触发 force: true 恢复
  harness.runtime.handleDataLocationClick({
    target: {
      closest: (sel) => (sel === "[data-data-location-refresh]" ? {} : null),
    },
  });
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(harness.runtime.dataLocationState.ready, true);
  assert.equal(harness.runtime.dataLocationState.error, "");
  assert.match(harness.cardEl.outerHTML, /\/data/);
});

test("QIN-636: 主目录设置渲染在失败/超时态输出显式重试按钮且不搬回 625 AC4 根操作", () => {
  assert.match(settingsSource, /data-directory-reload data-directory-retry/);
  assert.match(settingsSource, /data-data-location-refresh data-data-location-retry/);
  assert.doesNotMatch(
    sliceBetween(settingsSource, "if(permissionsSettingsPage){", "refreshIcons();"),
    /移出资源库|清除索引/,
  );
});
