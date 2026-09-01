// The build token is substituted by the backend route that serves this file
// (see next_service_worker() in next_push.py) so that every release gets its own
// cache namespace and the `activate` handler below drops the previous one. When
// the placeholder survives — a raw file read, or a dev server without the route —
// the literal below is used, which keeps the worker functional but pins the
// caches to a single namespace.
const SW_BUILD = "__DISCVAULT_SW_BUILD__".indexOf("DISCVAULT_SW_BUILD") >= 0
  ? "dev"
  : "__DISCVAULT_SW_BUILD__";
const SW_VERSION = `discvault-sw-${SW_BUILD}`;
const APP_CACHE = `${SW_VERSION}-app`;
const API_CACHE = `${SW_VERSION}-api`;
const RUNTIME_CACHE = `${SW_VERSION}-runtime`;

// Cache Storage grows without bound otherwise: opaque cross-origin poster
// responses are charged to the origin quota with a large padding, and Firefox's
// per-origin quota is small enough that a few hundred of them exhaust it.
const RUNTIME_CACHE_MAX_ENTRIES = 300;
const SNAPSHOT_PREFETCH_MAX_URLS = 60;

// Frontend modules served by register_next_static_routes() in next_static.py.
// They live under /api/ but are scripts, not data.
const NEXT_SCRIPT_PREFIX = "/api/next/app/js";

// Paged and offset-keyed responses must never enter Cache Storage: each offset
// is a distinct key, so the cache grows with the library, and replaying a stale
// page mid-hydration corrupts the loaded list.
const NON_CACHEABLE_API_PATHS = [
  "/api/next/collection/movies",
  "/api/next/collection/export"
];

// Only paths the backend actually serves belong here: `cache.add` failures are
// swallowed during install, so a fictional entry silently leaves the app shell
// half-precached. The SPA document is one route under many URLs — precache the
// canonical two and let appShellNetworkFirst() cover the rest at runtime.
const APP_SHELL = [
  "/",
  "/api/next/app",
  "/api/next/app/js/library-paging.js",
  "/api/next/app/js/library-export.js",
  "/api/next/manifest.json",
  "/manifest.json",
  "/apple-touch-icon-152.png",
  "/apple-touch-icon-167.png",
  "/apple-touch-icon.png",
  "/favicon-32.png",
  "/favicon-192.png",
  "/favicon-512.png",
  "/pwa-icon-192.png",
  "/pwa-icon-512.png",
  "/pwa-maskable-192.png",
  "/pwa-maskable-512.png",
  "/api/next/assets/apple-touch-icon-152.png",
  "/api/next/assets/apple-touch-icon-167.png",
  "/api/next/assets/apple-touch-icon.png",
  "/api/next/assets/favicon-32.png",
  "/api/next/assets/favicon-192.png",
  "/api/next/assets/favicon-512.png",
  "/api/next/assets/pwa-icon-192.png",
  "/api/next/assets/pwa-icon-512.png",
  "/api/next/assets/pwa-maskable-192.png",
  "/api/next/assets/pwa-maskable-512.png",
  "/api/next/assets/icon.svg",
  "/api/next/assets/logo.svg",
  // Only the catalogue and the two default locales: the app serves 29 of them,
  // and the active one is cached at runtime anyway.
  "/api/next/i18n",
  "/api/next/i18n/nl-NL",
  "/api/next/i18n/en-US"
];

const APP_SHELL_FALLBACKS = ["/api/next/app", "/"];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(APP_CACHE)
      .then(cache => Promise.all(APP_SHELL.map(url => cache.add(url).catch(err => console.warn("[SW] Failed to cache", url, err)))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => {
      const oldKeys = keys.filter(key => !key.startsWith(SW_VERSION));
      const didUpdate = oldKeys.length > 0;
      return Promise.all(oldKeys.map(key => caches.delete(key)))
        .then(() => self.clients.claim())
        .then(() => {
          if (!didUpdate) return undefined;
          return self.clients.matchAll({ type: "window" }).then(clients =>
            clients.forEach(client => client.postMessage({ type: "sw-updated" }))
          );
        });
    })
  );
});

function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "Content-Type": "application/json",
      "X-DiscVault-Offline": "1"
    }
  });
}

/**
 * Hand back a cached API read *labelled as one*.
 *
 * A cached response is byte-for-byte the response the backend gave when it was
 * still answering: same status, same body, same headers. Returned verbatim it
 * is indistinguishable from a live read, which is what let a backend outage
 * render as a perfectly healthy library -- the collection is on screen, the
 * counts are right, nothing says anything is wrong. The first thing that
 * reveals the outage is a write, and a write fails with a message about the
 * write, so a total outage reads as "deleting a film is broken".
 *
 * The body must not change -- it is the payload the app parses -- so the
 * signal goes in the same `X-DiscVault-Offline` header every other substitute
 * response already carries, with `cache` to say where this one came from.
 */
function staleCacheResponse(cached) {
  const headers = new Headers(cached.headers);
  headers.set("X-DiscVault-Offline", "cache");
  return new Response(cached.body, {
    status: cached.status,
    statusText: cached.statusText,
    headers
  });
}

function imageFallbackResponse() {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="480" viewBox="0 0 320 480"><rect width="320" height="480" rx="32" fill="#111827"/><path d="M88 208h144v64H88z" fill="#374151"/><circle cx="132" cy="188" r="24" fill="#6b7280"/><path d="M88 304l52-56 36 38 22-24 34 42z" fill="#6b7280"/><text x="160" y="370" text-anchor="middle" font-family="system-ui,-apple-system,BlinkMacSystemFont,sans-serif" font-size="18" fill="#d1d5db">Offline</text></svg>`;
  return new Response(svg, {
    status: 200,
    headers: {
      "Content-Type": "image/svg+xml",
      "Cache-Control": "no-store",
      "X-DiscVault-Offline": "1"
    }
  });
}

function isAppScriptPath(pathname) {
  return pathname.startsWith(`${NEXT_SCRIPT_PREFIX}/`);
}

/**
 * A `<script src>` or a navigation must never be answered with a stand-in body.
 * A JSON or text/plain reply to a script request is rejected by strict MIME
 * checking, so the module silently never runs — which is how the library used to
 * freeze at its first-paint page and the export button stopped responding. A
 * plain typed 503 lets the browser report an ordinary load failure instead.
 */
function isSubstitutableRequest(request) {
  const url = new URL(request.url);
  if (request.destination === "script" || request.destination === "document") return false;
  if (request.mode === "navigate") return false;
  return !isAppScriptPath(url.pathname);
}

function scriptFailureResponse() {
  return new Response("", {
    status: 503,
    headers: { "Content-Type": "application/javascript", "X-DiscVault-Offline": "1" }
  });
}

function documentFailureResponse() {
  return new Response("", {
    status: 503,
    headers: { "Content-Type": "text/html; charset=utf-8", "X-DiscVault-Offline": "1" }
  });
}

function unsubstitutableFailureResponse(request) {
  if (request.destination === "document" || request.mode === "navigate") {
    return documentFailureResponse();
  }
  return scriptFailureResponse();
}

function offlineResponseForRequest(request) {
  const url = new URL(request.url);
  if (!isSubstitutableRequest(request)) return unsubstitutableFailureResponse(request);
  if (
    request.destination === "image" ||
    /\.(avif|gif|jpe?g|png|svg|webp)$/i.test(url.pathname) ||
    url.pathname.startsWith("/api/next/media/") ||
    url.pathname.startsWith("/api/next/assets/") ||
    url.pathname.startsWith("/api/next/flags/")
  ) {
    return imageFallbackResponse();
  }
  return new Response("Offline", { status: 503, headers: { "X-DiscVault-Offline": "1" } });
}

function normalizedCacheRequest(request) {
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return request;
  if (
    url.search &&
    (
      url.pathname === "/version.json" ||
      url.pathname === "/manifest.json" ||
      url.pathname === "/api/next/manifest.json" ||
      url.pathname.startsWith("/api/next/i18n") ||
      url.pathname.startsWith("/api/next/app") ||
      url.pathname.startsWith("/api/next/media/") ||
      url.pathname.startsWith("/api/next/assets/") ||
      url.pathname.startsWith("/api/next/flags/")
    )
  ) {
    return new Request(url.origin + url.pathname, { method: "GET" });
  }
  return request;
}

function isProtectedMediaPath(pathname) {
  return pathname.startsWith("/api/next/media/") ||
    pathname.startsWith("/api/next/movievault-v2/posters/");
}

function isProtectedMediaRequest(request) {
  const url = new URL(request.url);
  return url.origin === self.location.origin && isProtectedMediaPath(url.pathname);
}

async function cacheMatch(request, cacheName) {
  const key = normalizedCacheRequest(request);
  const cache = cacheName ? await caches.open(cacheName) : null;
  return cache ? cache.match(key) : caches.match(key);
}

function isNonCacheableApiRequest(request) {
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return false;
  return NON_CACHEABLE_API_PATHS.some(path => url.pathname === path || url.pathname.startsWith(`${path}/`));
}

function isQuotaError(error) {
  if (!error) return false;
  return error.name === "QuotaExceededError" || error.name === "NS_ERROR_DOM_QUOTA_REACHED";
}

/** Drop the oldest entries so a runtime cache cannot grow until the origin quota trips. */
async function trimCache(cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length <= maxEntries) return;
  // cache.keys() is insertion-ordered, so the head of the list is the oldest.
  await Promise.all(keys.slice(0, keys.length - maxEntries).map(key => cache.delete(key)));
}

/**
 * Never let a cache write decide the fate of a live response. Cache Storage is
 * best-effort: a full origin quota must degrade offline support, not break the
 * app while it is online. Callers therefore treat this as fire-and-forget.
 */
async function cachePut(request, response, cacheName) {
  if (!response || !(response.ok || response.type === "opaque")) return;
  if (isProtectedMediaRequest(request)) return;
  if (isNonCacheableApiRequest(request)) return;
  const key = normalizedCacheRequest(request);
  const body = response.clone();
  try {
    const cache = await caches.open(cacheName);
    await cache.put(key, body);
  } catch (error) {
    if (!isQuotaError(error)) throw error;
    // Reclaim the biggest consumer (opaque poster responses) and try once more.
    await caches.delete(RUNTIME_CACHE).catch(() => {});
    try {
      const cache = await caches.open(cacheName);
      await cache.put(key, response.clone());
    } catch (retryError) {
      console.warn("[SW] Cache write skipped, storage quota reached", retryError);
      return;
    }
  }
  if (cacheName === RUNTIME_CACHE) {
    await trimCache(RUNTIME_CACHE, RUNTIME_CACHE_MAX_ENTRIES).catch(() => {});
  }
}

/** Fire-and-forget cache write; ties into event.waitUntil() when one is available. */
function cachePutBackground(request, response, cacheName, event) {
  const write = cachePut(request, response, cacheName).catch(error => {
    console.warn("[SW] Cache write failed", error);
  });
  if (event && typeof event.waitUntil === "function") event.waitUntil(write);
  return write;
}

async function readCachedJson(path) {
  const request = new Request(new URL(path, self.location.origin).toString(), { method: "GET" });
  const cached = await cacheMatch(request, API_CACHE);
  if (!cached) return null;
  return cached.clone().json().catch(() => null);
}

function emptySnapshot() {
  return {
    movies: [],
    containers: [],
    containerMembership: [],
    mediaGroups: [],
    people: [],
    preferences: {},
    counts: {
      movies: 0,
      people: 0,
      containers: 0,
      users: 0,
      personalLists: { watchlist: 0, watched: 0 },
      notifications: { unread: 0, total: 0 }
    }
  };
}

function snapshotPayload(snapshot, extra = {}) {
  return Object.assign({
    status: "ok",
    offline: true,
    snapshot: Object.assign(emptySnapshot(), snapshot || {})
  }, extra);
}

async function cachedSnapshot() {
  const payload = await readCachedJson("/api/next/app/snapshot");
  return (payload && payload.snapshot) || null;
}

function isCacheableImageUrl(value) {
  const text = String(value || "").trim();
  if (!text) return false;
  let parsed;
  try {
    parsed = new URL(text, self.location.origin);
  } catch {
    return false;
  }
  if (parsed.origin === self.location.origin && isProtectedMediaPath(parsed.pathname)) return false;
  if (text.startsWith("/api/next/assets/") || text.startsWith("/api/next/flags/")) return true;
  if (!/^https?:\/\//i.test(text)) return false;
  if (/\/(image\.tmdb\.org|images\.static-bluray\.com)\//i.test(text)) return true;
  return /\.(avif|gif|jpe?g|png|svg|webp)(\?|#|$)/i.test(text);
}

function collectSnapshotImageUrls(snapshot) {
  const urls = new Set();
  const visit = value => {
    if (!value || urls.size >= SNAPSHOT_PREFETCH_MAX_URLS) return;
    if (typeof value === "string") {
      if (isCacheableImageUrl(value)) urls.add(value);
      return;
    }
    if (Array.isArray(value)) {
      value.slice(0, 80).forEach(visit);
      return;
    }
    if (typeof value === "object") {
      [
        "poster_url",
        "posterUrl",
        "backdrop_url",
        "backdropUrl",
        "avatarUrl",
        "avatar_url",
        "profile_url",
        "photo_url",
        "source_url"
      ].forEach(key => visit(value[key]));
      if (value.metadata) visit(value.metadata);
    }
  };
  visit((snapshot || {}).movies || []);
  visit((snapshot || {}).containers || []);
  visit((snapshot || {}).people || []);
  return Array.from(urls);
}

async function prefetchSnapshotAssets(response) {
  const payload = await response.clone().json().catch(() => null);
  const snapshot = payload && payload.snapshot;
  if (!snapshot) return;
  const urls = collectSnapshotImageUrls(snapshot);
  await Promise.all(urls.map(async assetUrl => {
    const absolute = new URL(assetUrl, self.location.origin);
    const request = absolute.origin === self.location.origin
      ? new Request(absolute.toString(), { method: "GET" })
      : new Request(absolute.toString(), { method: "GET", mode: "no-cors" });
    const cached = await cacheMatch(request, RUNTIME_CACHE);
    if (cached) return;
    const resp = await fetch(request).catch(() => null);
    if (resp && (resp.ok || resp.type === "opaque")) await cachePut(request, resp, RUNTIME_CACHE);
  }));
}

async function detailFromSnapshot(url) {
  const snapshot = await cachedSnapshot();
  if (!snapshot) return null;

  const movieMatch = url.pathname.match(/^\/api\/next\/movies\/([^/]+)$/);
  if (movieMatch) {
    const movieId = decodeURIComponent(movieMatch[1]);
    const movie = (snapshot.movies || []).find(item => String(item.id) === movieId);
    if (!movie) return null;
    const memberships = (snapshot.containerMembership || []).filter(item => String(item.movie_id || item.movieId) === movieId);
    const containers = memberships
      .map(item => (snapshot.containers || []).find(container => String(container.id) === String(item.container_id || item.containerId)))
      .filter(Boolean);
    return jsonResponse({
      status: "ok",
      offline: true,
      detail: {
        movie,
        containers,
        credits: [],
        identifiers: [],
        mediaAssets: [],
        technicalSpecs: null
      }
    });
  }

  const containerMatch = url.pathname.match(/^\/api\/next\/containers\/([^/]+)$/);
  if (containerMatch) {
    const containerId = decodeURIComponent(containerMatch[1]);
    const container = (snapshot.containers || []).find(item => String(item.id) === containerId);
    if (!container) return null;
    const memberships = (snapshot.containerMembership || []).filter(item => String(item.container_id || item.containerId) === containerId);
    const movies = memberships
      .map(item => (snapshot.movies || []).find(movie => String(movie.id) === String(item.movie_id || item.movieId)))
      .filter(Boolean);
    return jsonResponse({
      status: "ok",
      offline: true,
      detail: {
        container,
        movies,
        members: movies,
        mediaAssets: [],
        videos: []
      }
    });
  }

  const personMatch = url.pathname.match(/^\/api\/next\/people\/([^/]+)$/);
  if (personMatch) {
    const personId = decodeURIComponent(personMatch[1]);
    const person = (snapshot.people || []).find(item => String(item.id) === personId);
    if (!person) return null;
    return jsonResponse({
      status: "ok",
      offline: true,
      detail: {
        person,
        credits: [],
        movies: [],
        filmography: []
      }
    });
  }

  return null;
}

function authStatusFallback() {
  return {
    status: "ok",
    offline: true,
    auth_enabled: false,
    auth_ready: true,
    authenticated: true,
    configured_auth_enabled: true,
    has_credentials: true,
    has_users: true,
    setup_required: false,
    role: "offline",
    username: "offline"
  };
}

function startupStatusFallback() {
  return {
    status: "ok",
    offline: true,
    startup: {
      phase: "ready",
      ready: true,
      message: "DiscVault is offline. Cached library data is available.",
      auth: { authenticated: true, effective: true },
      migration: { state: "offline", canStart: false, activeJob: null },
      canStartMigration: false,
      canUseCollection: true,
      steps: []
    }
  };
}

function apiFallback(url) {
  const path = url.pathname;
  if (path.startsWith("/api/next/media/") || path.startsWith("/api/next/assets/") || path.startsWith("/api/next/flags/")) {
    return imageFallbackResponse();
  }
  if (path === "/api/next/app/snapshot") return cachedSnapshot().then(snapshot => jsonResponse(snapshotPayload(snapshot)));
  if (path === "/api/next/auth/status") return jsonResponse(authStatusFallback());
  if (path === "/api/next/startup/status") return jsonResponse(startupStatusFallback());
  if (path === "/api/next/health" || path === "/api/health") return jsonResponse({ status: "offline", ok: false, offline: true }, 503);
  if (path === "/api/next/i18n") {
    return jsonResponse({
      status: "ok",
      offline: true,
      sourceLocale: "en-US",
      defaultLocale: "nl-NL",
      locales: []
    });
  }
  if (path.startsWith("/api/next/i18n/")) {
    const locale = decodeURIComponent(path.split("/").pop() || "nl-NL");
    return jsonResponse({ status: "ok", offline: true, locale, sourceLocale: "en-US", messages: {}, locales: [] });
  }
  if (path === "/api/next/lists") return jsonResponse({ status: "ok", offline: true, items: [], lists: [] });
  if (path === "/api/next/notifications") return jsonResponse({ status: "ok", offline: true, notifications: [], unread: 0 });
  if (path === "/api/next/jobs" || path === "/api/next/metadata/jobs") return jsonResponse({ status: "ok", offline: true, jobs: [] });
  if (path === "/api/next/metadata/plugins" || path === "/api/next/plugins/registry") return jsonResponse({ status: "ok", offline: true, plugins: [] });
  if (path === "/api/next/containers") return cachedSnapshot().then(snapshot => jsonResponse({ status: "ok", offline: true, items: (snapshot && snapshot.containers) || [] }));
  if (path === "/api/next/people") return cachedSnapshot().then(snapshot => jsonResponse({ status: "ok", offline: true, items: (snapshot && snapshot.people) || [] }));
  if (path === "/api/next/movies") return cachedSnapshot().then(snapshot => jsonResponse({ status: "ok", offline: true, items: (snapshot && snapshot.movies) || [], limit: 0, offset: 0 }));
  if (path === "/api/movies") return jsonResponse([]);
  if (path === "/api/stats") return jsonResponse({ total: 0, by_format: [], offline: true });
  if (path.startsWith("/api/logs")) return jsonResponse([]);
  return jsonResponse({ status: "error", offline: true, error: "Offline and no cached data is available.", path }, 503);
}

function apiFallbackFor(request, url) {
  // The generic JSON stub is only ever valid for data requests. Anything that
  // the browser will parse as a script or render as a page gets a typed 503.
  if (!isSubstitutableRequest(request)) return unsubstitutableFailureResponse(request);
  return apiFallback(url);
}

/**
 * The answer to a write that never reached the network.
 *
 * `fetch` rejecting means no HTTP response came back at all -- not a 4xx, not
 * a 5xx, nothing. Two very different situations produce it, and the old single
 * sentence about the backend connection being unavailable covered both while
 * naming neither: a device with no network at all, and a device whose network
 * is fine but that cannot reach DiscVault. It also closed with the one
 * instruction that cannot help either of them -- a refresh is served from the
 * cache the app shell already sits in, so it comes back looking healthy and
 * the next write fails exactly the same way.
 *
 * The browser's own reason for the rejection was captured as `detail` and then
 * never shown anywhere, so the only account of the failure was thrown away.
 */
function writeFailureResponse(request, url, error) {
  // A worker's navigator exposes onLine; treat anything else as "online",
  // because claiming the device is offline when it is not sends the reader
  // looking at the wrong thing entirely.
  const browserOffline = Boolean(self.navigator) && self.navigator.onLine === false;
  return jsonResponse({
    status: "error",
    offline: browserOffline,
    browser_offline: browserOffline,
    backend_unreachable: !browserOffline,
    queueable: false,
    errorCode: browserOffline ? "browser_offline" : "backend_unreachable",
    error: browserOffline
      ? "This device is offline, so the change was not sent. Nothing was changed."
      : "DiscVault's backend did not answer, so nothing was changed. Anything already on screen may be cached data.",
    detail: error && error.message ? error.message : "",
    method: request.method,
    path: url.pathname
  }, 503);
}

async function handleApi(request, event) {
  const url = new URL(request.url);
  const isGet = request.method === "GET";
  const isLegacyAuthRoute = url.pathname.startsWith("/api/auth/");

  if (!isGet) {
    try {
      return await fetch(request.clone());
    } catch (error) {
      return writeFailureResponse(request, url, error);
    }
  }

  if (isProtectedMediaPath(url.pathname)) {
    // Network-only, and deliberately `no-store`: protected media must never be
    // replayable from any cache once the viewer loses access to it (see
    // ServiceWorkerProtectedMediaTests). The cost of re-fetching posters is
    // addressed by rendering fewer times and lazy-loading the images, not by
    // caching the bytes.
    try {
      return await fetch(new Request(request, { cache: "no-store" }));
    } catch {
      return imageFallbackResponse();
    }
  }

  if (isLegacyAuthRoute && url.pathname !== "/api/auth/status") {
    try {
      return await fetch(request);
    } catch {
      return jsonResponse({ status: "error", offline: true, error: "Offline: authentication endpoint unavailable." }, 503);
    }
  }

  let networkResp;
  try {
    networkResp = await fetch(request);
  } catch {
    const cached = (await cacheMatch(request, API_CACHE)) || (await cacheMatch(request));
    if (cached) return staleCacheResponse(cached);
    const detail = await detailFromSnapshot(url);
    if (detail) return detail;
    return apiFallbackFor(request, url);
  }
  // Deliberately outside the try: a Cache Storage write must never be able to
  // turn a successful network response into an offline fallback.
  if (networkResp.ok) {
    cachePutBackground(request, networkResp, API_CACHE, event);
    if (url.pathname === "/api/next/app/snapshot") {
      const prefetch = prefetchSnapshotAssets(networkResp).catch(error => console.warn("[SW] Snapshot asset prefetch failed", error));
      if (event && event.waitUntil) event.waitUntil(prefetch);
    }
  }
  return networkResp;
}

async function cacheFirst(request, cacheName, event) {
  const cached = await cacheMatch(request, cacheName);
  if (cached) return cached;
  const resp = await fetch(request);
  if (resp && (resp.ok || resp.type === "opaque")) cachePutBackground(request, resp, cacheName, event);
  return resp;
}

async function networkFirst(request, cacheName, event) {
  let resp;
  try {
    resp = await fetch(new Request(request, { cache: "reload" }));
  } catch {
    const cached = await cacheMatch(request, cacheName);
    return cached || offlineResponseForRequest(request);
  }
  if (resp && resp.ok) cachePutBackground(request, resp, cacheName, event);
  return resp;
}

/**
 * Frontend modules under /api/next/app/js/. They must be served fresh or from
 * cache, and on failure they must fail as scripts — never as a JSON stub, which
 * strict MIME checking blocks and which leaves the SPA running without its
 * paging and export modules.
 */
async function scriptNetworkFirst(request, event) {
  let resp;
  try {
    resp = await fetch(new Request(request, { cache: "reload" }));
  } catch {
    const cached = (await cacheMatch(request, APP_CACHE)) || (await cacheMatch(request, API_CACHE));
    return cached || scriptFailureResponse();
  }
  if (resp && resp.ok) cachePutBackground(request, resp, APP_CACHE, event);
  return resp;
}

async function appShellNetworkFirst(request, event) {
  let resp;
  try {
    resp = await fetch(new Request(request, { cache: "reload" }));
  } catch {
    const cached = await cacheMatch(request, APP_CACHE);
    return cached || appShellFallback();
  }
  if (resp && resp.ok) cachePutBackground(request, resp, APP_CACHE, event);
  return resp;
}

async function appShellFallback() {
  for (const path of APP_SHELL_FALLBACKS) {
    const cached = await cacheMatch(new Request(new URL(path, self.location.origin).toString(), { method: "GET" }), APP_CACHE);
    if (cached) return cached;
  }
  return new Response("DiscVault is offline and the app shell is not cached yet.", {
    status: 503,
    headers: { "Content-Type": "text/html; charset=utf-8", "X-DiscVault-Offline": "1" }
  });
}

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);
  const sameOrigin = url.origin === self.location.origin;

  // Order matters. The SPA document and its modules are served from /api/ routes,
  // so both must be classified before the generic /api/ data branch — otherwise
  // handleApi() answers them with a JSON offline stub and the app loses either
  // its page or its paging/export modules.
  if (request.method === "GET" && request.mode === "navigate") {
    event.respondWith(appShellNetworkFirst(request, event));
    return;
  }

  if (request.method === "GET" && sameOrigin && isAppScriptPath(url.pathname)) {
    event.respondWith(scriptNetworkFirst(request, event));
    return;
  }

  if (sameOrigin && url.pathname.startsWith("/api/")) {
    event.respondWith(handleApi(request, event));
    return;
  }

  if (request.method !== "GET") return;

  if (
    sameOrigin &&
    (
      url.pathname.startsWith("/api/next/media/") ||
      url.pathname.startsWith("/api/next/assets/") ||
      url.pathname.startsWith("/api/next/flags/") ||
      url.pathname.startsWith("/flags/")
    )
  ) {
    event.respondWith(cacheFirst(request, RUNTIME_CACHE, event).catch(async () => (await cacheMatch(request, RUNTIME_CACHE)) || offlineResponseForRequest(request)));
    return;
  }

  if (sameOrigin && /\.(js|css|json)$/.test(url.pathname)) {
    event.respondWith(networkFirst(request, APP_CACHE, event));
    return;
  }

  if (!sameOrigin) {
    event.respondWith(cacheFirst(request, RUNTIME_CACHE, event).catch(async () => (await cacheMatch(request, RUNTIME_CACHE)) || offlineResponseForRequest(request)));
    return;
  }

  event.respondWith(cacheFirst(request, APP_CACHE, event).catch(() => appShellFallback()));
});

self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) {}
  const title = data.title || "DiscVault";
  const options = {
    body: data.body || "",
    icon: "/pwa-icon-192.png",
    badge: "/favicon-32.png",
    data: { url: data.url || "/" },
    vibrate: [100, 50, 100]
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  const isInvite = url.includes("#invites");
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
      if (list.length > 0) {
        const client = list[0];
        if (isInvite) {
          client.postMessage({ type: "open-invites" });
        } else {
          client.navigate(url);
        }
        return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
      return undefined;
    })
  );
});
