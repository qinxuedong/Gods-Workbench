/** C16-T3 PWA and Tauri desktop contract checks. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const manifest = JSON.parse(readFileSync("web/manifest.json", "utf8"));
const page = readFileSync("web/pages/index.html", "utf8");
const workerSource = readFileSync("web/sw.js", "utf8");
const desktopConfig = JSON.parse(readFileSync("clients/desktop/tauri.conf.json", "utf8"));
const desktopSource = readFileSync("clients/desktop/src/main.rs", "utf8");
const desktopUi = readFileSync("clients/desktop/ui/app.js", "utf8");

test("PWA manifest has an installable standalone entry and embedded SVG icon", () => {
  assert.equal(manifest.start_url, "/static/pages/index.html");
  assert.equal(manifest.scope, "/static/");
  assert.equal(manifest.display, "standalone");
  assert.ok(manifest.name);
  assert.ok(manifest.icons.length > 0);
  const icon = manifest.icons.find(item => item.type === "image/svg+xml");
  assert.ok(icon, "an SVG icon is required");
  assert.equal(icon.sizes, "any");
  assert.match(icon.purpose, /maskable/);
  assert.match(icon.src, /^data:image\/svg\+xml,/);
  assert.match(decodeURIComponent(icon.src.slice("data:image/svg+xml,".length)), /^<svg\b/);
});

test("served entry links the manifest and registers the scoped service worker", () => {
  assert.match(page, /<link rel="manifest" href="\/static\/manifest\.json">/);
  assert.match(page, /navigator\.serviceWorker\.register\("\/static\/sw\.js", \{ scope: "\/static\/" \}\)/);
  assert.match(page, /window\.isSecureContext/);
  assert.doesNotMatch(page, /<script[^>]+src="https?:\/\//i);
});

test("service worker caches only safe static shell requests", () => {
  const listeners = {};
  const context = {
    URL,
    Set,
    self: {
      location: { origin: "https://workbench.local" },
      addEventListener(name, handler) { listeners[name] = handler; }
    }
  };
  vm.runInNewContext(workerSource, context);
  const request = (path, mode = "cors", headers = new Headers()) => ({
    method: "GET",
    mode,
    headers,
    url: `https://workbench.local${path}`
  });
  const cacheable = (path, mode = "cors", headers = new Headers()) => {
    const value = request(path, mode, headers);
    return context.isCacheableRequest(value, new URL(value.url));
  };
  assert.ok(listeners.install);
  assert.ok(listeners.activate);
  assert.ok(listeners.fetch);
  assert.equal(cacheable("/static/js/app.js?v=20261021"), true);
  assert.equal(cacheable("/static/pages/index.html", "navigate"), true);
  assert.equal(cacheable("/api/v1/projects"), false);
  assert.equal(cacheable("/healthz"), false);
  assert.equal(cacheable("/auth/callback"), false);
  assert.equal(cacheable("/static/prompt-registry/index.json"), false);
  assert.equal(cacheable("/static/pages/index.html?project_id=private", "navigate"), false);
  assert.equal(cacheable("/static/pages/project.html", "navigate"), false);
  assert.equal(cacheable("/static/js/app.js?token=secret"), false);
  assert.equal(cacheable("/static/js/app.js", "cors", new Headers({ Authorization: "Bearer secret" })), false);
  assert.match(workerSource, /credentials: "omit"/);
  assert.match(workerSource, /new Request\(request, \{ credentials: "omit" \}\)/);

  const shellUrl = new URL("https://workbench.local/static/pages/index.html");
  const response = (path, headers = {}, redirected = false) => ({
    ok: true,
    type: "basic",
    redirected,
    headers: new Headers(headers),
    url: `https://workbench.local${path}`
  });
  assert.equal(context.isCacheableResponse(response("/static/pages/index.html"), shellUrl), true);
  assert.equal(context.isCacheableResponse(response("/static/pages/index.html", { "cache-control": "private" }), shellUrl), false);
  assert.equal(context.isCacheableResponse(response("/static/pages/index.html", { "cache-control": "no-store" }), shellUrl), false);
  assert.equal(context.isCacheableResponse(response("/static/pages/index.html", { "set-cookie": "session=secret" }), shellUrl), false);
  assert.equal(context.isCacheableResponse(response("/static/pages/index.html", {}, true), shellUrl), false);
  assert.equal(context.isCacheableResponse(response("/api/v1/projects"), shellUrl), false);
});

test("Tauri client probes NAS health, bounds discovery, persists only LAN URLs, and has a tray", () => {
  assert.equal(desktopConfig.app.withGlobalTauri, true);
  assert.equal(desktopConfig.app.windows[0].label, "main");
  assert.match(desktopSource, /set_path\("\/healthz"\)/);
  assert.match(desktopSource, /:2077/);
  assert.match(desktopSource, /prefix\.max\(24\)/);
  assert.match(desktopSource, /ensure_local_ui/);
  assert.match(desktopSource, /WindowEvent::CloseRequested/);
  assert.match(desktopSource, /TrayIconBuilder::new\(\)/);
  assert.match(desktopSource, /set_query\(Some\("rescan=1"\)\)/);
  assert.match(desktopUi, /discover_lan/);
  assert.match(desktopUi, /connect_to_server/);
  assert.match(desktopUi, /get\("rescan"\)/);
});
