const CACHE_PREFIX = "gw-static-shell-";
const CACHE_NAME = `${CACHE_PREFIX}v3`;
const SHELL_DOCUMENT = "/static/pages/index.html";
const PRE_CACHE_URLS = [SHELL_DOCUMENT, "/static/manifest.json"];
const STATIC_PREFIXES = ["/static/css/", "/static/js/", "/static/vendor/", "/static/pages/"];
const BLOCKED_PREFIXES = ["/" + "api/", "/auth/", "/oauth/", "/login", "/logout", "/token", "/static/prompt-registry/", "/static/system-prompts/", "/static/pages/login"];
const VERSION_QUERY_KEYS = new Set(["v", "build", "ui", "m3"]);

function hasOnlyVersionQuery(url) {
  for (const key of url.searchParams.keys()) {
    if (!VERSION_QUERY_KEYS.has(key)) return false;
  }
  return true;
}

function isBlockedPath(pathname) {
  const normalized = pathname.toLowerCase();
  return BLOCKED_PREFIXES.some(prefix => normalized.startsWith(prefix));
}

function isCacheableRequest(request, url) {
  if (request.method !== "GET" || url.origin !== self.location.origin) return false;
  if (request.headers.has("authorization") || request.headers.has("x-csrf-token")) return false;
  if (isBlockedPath(url.pathname)) return false;
  if (request.mode === "navigate") {
    return !url.search && url.pathname === SHELL_DOCUMENT;
  }
  if (url.pathname === SHELL_DOCUMENT) return !url.search;
  if (url.pathname === "/static/manifest.json") return !url.search;
  if (!STATIC_PREFIXES.some(prefix => url.pathname.startsWith(prefix))) return false;
  if (url.pathname.startsWith("/static/pages/")) return false;
  return hasOnlyVersionQuery(url);
}

function isCacheableResponse(response, requestUrl) {
  if (!response.ok || response.type !== "basic" || response.redirected) return false;
  if (response.headers.has("set-cookie")) return false;
  const cacheControl = response.headers.get("cache-control") || "";
  if (/\b(?:private|no-store)\b/i.test(cacheControl)) return false;
  try {
    const responseUrl = new URL(response.url);
    const isStaticPath = responseUrl.pathname === "/static/manifest.json"
      || STATIC_PREFIXES.some(prefix => responseUrl.pathname.startsWith(prefix));
    return responseUrl.origin === requestUrl.origin && isStaticPath && !isBlockedPath(responseUrl.pathname);
  } catch {
    return false;
  }
}

async function precacheStaticShell(cache, path) {
  const url = new URL(path, self.location.origin);
  const request = new Request(url, { credentials: "omit", cache: "reload" });
  const response = await fetch(request);
  if (isCacheableResponse(response, url)) await cache.put(request, response);
}

// 只读当前代缓存：避免旧代 cache 里的历史 index.html 串味（跨代兜底由 activate 清理保证）。
async function readCurrentShell() {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(SHELL_DOCUMENT);
  return cached || null;
}

async function refreshShellCache(response, requestUrl) {
  if (!isCacheableResponse(response, requestUrl)) return;
  try {
    const cache = await caches.open(CACHE_NAME);
    await cache.put(SHELL_DOCUMENT, response.clone());
  } catch {
    // 配额或存储异常不应让本次导航失败
  }
}

// 导航请求统一走 Network-First：先取服务端最新壳，只有网络/服务不可用时才回退缓存。
async function fetchNavigationShell(request) {
  let response;
  try {
    response = await fetch(request);
  } catch {
    const cached = await readCurrentShell();
    if (cached) return cached;
    return new Response("Offline shell unavailable", { status: 503, statusText: "Offline" });
  }
  await refreshShellCache(response, new URL(request.url));
  if (!response.ok && response.status >= 500) {
    const cached = await readCurrentShell();
    if (cached) return cached;
  }
  return response;
}

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(async cache => {
        await Promise.all(PRE_CACHE_URLS.map(path => precacheStaticShell(cache, path).catch(() => {})));
      })
      .then(() => self.skipWaiting())
  );
});
self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  const request = event.request;
  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (request.mode === "navigate") {
    if (!isCacheableRequest(request, url)) return;
    event.respondWith(fetchNavigationShell(request));
    return;
  }
  if (!isCacheableRequest(request, url)) return;
  const publicRequest = new Request(request, { credentials: "omit" });

  event.respondWith(
    fetch(publicRequest).then(async response => {
      if (isCacheableResponse(response, url)) {
        const cache = await caches.open(CACHE_NAME);
        await cache.put(publicRequest, response.clone());
      }
      return response;
    }).catch(async () => {
      const cached = await caches.match(publicRequest);
      if (cached) return cached;
      return new Response("Offline asset unavailable", { status: 503, statusText: "Offline" });
    })
  );
});
