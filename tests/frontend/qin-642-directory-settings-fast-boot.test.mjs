import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..", "..");
const MANAGER_PATH = path.join(ROOT, "web", "js", "modules", "asset-manager.js");
const SETTINGS_PATH = path.join(ROOT, "web", "js", "modules", "asset-manager", "settings.js");

function sliceBetween(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  assert.notEqual(start, -1, `missing start marker: ${startMarker}`);
  const end = source.indexOf(endMarker, start);
  assert.notEqual(end, -1, `missing end marker: ${endMarker}`);
  return source.slice(start, end);
}

function createFastBootHarness({
  probeDelay = false,
  facetsDelay = false,
  workspacesDelay = false,
  statusDelay = false,
} = {}) {
  const manager = fs.readFileSync(MANAGER_PATH, "utf8");
  const loaderSource = sliceBetween(
    manager,
    "async function loadGlobalDirectorySettings()",
    "let pendingPurgeRoot = null;",
  );

  const state = {
    storageSettingsState: { open: false, dirs: {} },
    directorySettingsReady: false,
    directorySettingsError: "",
    registryStatus: null,
    currentStatusText: "",
    registryFacets: { kinds: [], categories: [], tags: [], collections: [], roots: [] },
    gwCalls: [],
    appliedRoots: [],
    requestedUrls: [],
  };

  let resolveSlowProbe;
  let resolveSlowFacets;
  let resolveSlowWorkspaces;
  let resolveSlowStatus;

  const factory = new Function(
    "storageSettingsState",
    "directorySettingsReady",
    "directorySettingsError",
    "gwCall",
    "loadRegistryPrincipal",
    "canManageProjectWorkspaceSettings",
    "apiJsonResponse",
    "assetManagerApi",
    "canManageAllowedRoots",
    "assetManagerHttp",
    "registryStatus",
    "isTauriClientMode",
    "currentStatusText",
    "registryFacets",
    "setTimeout",
    "clearTimeout",
    `${loaderSource}; return {
      loadGlobalDirectorySettings,
      loadPermissionsAllowedRootsSettings,
      cancelGlobalDirectorySettingsLoad,
      getState: () => ({
        directorySettingsReady,
        directorySettingsError,
        currentStatusText,
        registryStatus,
        registryFacets,
      }),
    };`,
  );

  const runtime = factory(
    state.storageSettingsState,
    state.directorySettingsReady,
    state.directorySettingsError,
    (domain, method, ...args) => {
      state.gwCalls.push([domain, method, ...args]);
      if (domain === "settings" && method === "applyAllowedRootSettingsData") {
        state.appliedRoots.push(args[0]);
      }
      return undefined;
    },
    async () => {},
    () => true,
    async (req) => (typeof req === "function" ? await req() : await req),
    {
      getStorageSettings: () => Promise.resolve({ dirs: { local: "/local" }, revision: 1 }),
      getThumbnailSettings: () => Promise.resolve({ mode: "default" }),
      getRegistryStatus: () => {
        if (statusDelay) return new Promise((r) => { resolveSlowStatus = r; });
        return Promise.resolve({ ready: true });
      },
      getRegistryFacets: () => {
        if (facetsDelay) return new Promise((r) => { resolveSlowFacets = r; });
        return Promise.resolve({ roots: [{ id: "r1" }] });
      },
    },
    () => true,
    {
      request: (url) => {
        state.requestedUrls.push(url);
        if (url === "/api/storage-settings/roots?probe=0") {
          return Promise.resolve({
            revision: 1,
            roots: [{ id: "r1", path: "Z:\\Media", availability: "probing" }],
          });
        }
        if (url === "/api/storage-settings/roots") {
          if (probeDelay) return new Promise((r) => { resolveSlowProbe = r; });
          return Promise.resolve({
            revision: 1,
            roots: [{ id: "r1", path: "Z:\\Media", availability: "available" }],
          });
        }
        if (url === "/api/workspaces") {
          if (workspacesDelay) return new Promise((r) => { resolveSlowWorkspaces = r; });
          return Promise.resolve({ active_workspace_id: "ws_1" });
        }
        return Promise.resolve({});
      },
    },
    state.registryStatus,
    () => false,
    state.currentStatusText,
    state.registryFacets,
    globalThis.setTimeout,
    globalThis.clearTimeout,
  );

  return {
    state,
    runtime,
    resolveSlowProbe: (v) => resolveSlowProbe?.(v),
    resolveSlowFacets: (v) => resolveSlowFacets?.(v),
    resolveSlowWorkspaces: (v) => resolveSlowWorkspaces?.(v),
    resolveSlowStatus: (v) => resolveSlowStatus?.(v),
  };
}

test("QIN-642: 目录设置首屏不被慢盘 probe=1、facets、workspaces 与 status 阻塞", async () => {
  const harness = createFastBootHarness({
    probeDelay: true,
    facetsDelay: true,
    workspacesDelay: true,
    statusDelay: true,
  });

  await harness.runtime.loadGlobalDirectorySettings();
  assert.equal(harness.runtime.getState().directorySettingsReady, true, "首屏核心路径完成即应可交互");
  assert.ok(
    harness.state.requestedUrls.includes("/api/storage-settings/roots?probe=0"),
    "首屏应请求快速快照 probe=0",
  );
  assert.equal(harness.state.appliedRoots.length, 1);
  assert.equal(harness.state.appliedRoots[0].roots[0].availability, "probing");

  // 慢盘探活在后台完成后就地刷新允许根状态
  harness.resolveSlowProbe({
    revision: 1,
    roots: [{ id: "r1", path: "Z:\\Media", availability: "available" }],
  });
  await new Promise((r) => setImmediate(r));
  assert.equal(harness.state.appliedRoots.length, 2);
  assert.equal(harness.state.appliedRoots[1].roots[0].availability, "available");

  harness.resolveSlowFacets({ roots: [{ id: "r1" }] });
  harness.resolveSlowWorkspaces({ active_workspace_id: "ws_1" });
  harness.resolveSlowStatus({ ready: true });
});

test("QIN-642: 取消/重读后旧代际慢盘探活返回不得覆盖当前状态", async () => {
  const harness = createFastBootHarness({ probeDelay: true });
  await harness.runtime.loadGlobalDirectorySettings();
  assert.equal(harness.state.appliedRoots.length, 1);

  harness.runtime.cancelGlobalDirectorySettingsLoad();
  harness.resolveSlowProbe({
    revision: 99,
    roots: [{ id: "r1", path: "Z:\\Stale", availability: "offline" }],
  });
  await new Promise((r) => setImmediate(r));
  assert.equal(harness.state.appliedRoots.length, 1, "已取消代际的迟到探活应被丢弃");
});

test("QIN-642: loadPermissionsAllowedRootsSettings 同样首屏走 probe=0 且后台异步探活", async () => {
  const harness = createFastBootHarness({ probeDelay: true });
  await harness.runtime.loadPermissionsAllowedRootsSettings();
  assert.equal(harness.runtime.getState().directorySettingsReady, true);
  assert.ok(harness.state.requestedUrls.includes("/api/storage-settings/roots?probe=0"));
  assert.equal(harness.state.appliedRoots[0].roots[0].availability, "probing");

  harness.resolveSlowProbe({
    revision: 1,
    roots: [{ id: "r1", path: "Z:\\Media", availability: "available" }],
  });
  await new Promise((r) => setImmediate(r));
  assert.equal(harness.state.appliedRoots.length, 2);
  assert.equal(harness.state.appliedRoots[1].roots[0].availability, "available");
});

test("QIN-642: settings.js 正确渲染 probing 为「探活中」而非离线", () => {
  const settingsSource = fs.readFileSync(SETTINGS_PATH, "utf8");
  assert.match(settingsSource, /root\.availability === 'probing' \? '探活中'/);
  assert.match(settingsSource, /data-directory-retry/);
});
