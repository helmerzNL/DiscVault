const SW_VERSION = "discvault-sw-v1";
const APP_CACHE = `${SW_VERSION}-app`;
const API_CACHE = `${SW_VERSION}-api`;
const RUNTIME_CACHE = `${SW_VERSION}-runtime`;

const APP_SHELL = [
  "/",
  "/index.html",
  "/manifest.json",
  "/version.json",
  "/apple-touch-icon.png",
  "/favicon-32.png",
  "/favicon-192.png",
  "/icon.svg",
  "/logo.svg"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(APP_CACHE)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys
        .filter(k => !k.startsWith(SW_VERSION))
        .map(k => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

async function handleApi(request) {
  const url = new URL(request.url);
  const isGet = request.method === "GET";
  const isAuthRoute = url.pathname.startsWith("/api/auth/");

  if (!isGet) {
    return jsonResponse({
      error: "Offline: deze actie vereist backend-verbinding."
    }, 503);
  }

  if (isAuthRoute && url.pathname !== "/api/auth/status") {
    try {
      return await fetch(request);
    } catch {
      return jsonResponse({ error: "Offline: authenticatie endpoint niet bereikbaar." }, 503);
    }
  }

  try {
    const networkResp = await fetch(request);
    const cache = await caches.open(API_CACHE);
    cache.put(request, networkResp.clone());
    return networkResp;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;

    if (url.pathname === "/api/movies") return jsonResponse([]);
    if (url.pathname === "/api/stats") {
      return jsonResponse({ total: 0, by_format: [], offline: true });
    }
    if (url.pathname.startsWith("/api/logs")) return jsonResponse([]);
    if (url.pathname === "/api/auth/status") {
      return jsonResponse({ auth_enabled: false, has_credentials: false, offline: true });
    }
    if (url.pathname === "/api/health") {
      return jsonResponse({ status: "offline", ok: false }, 503);
    }

    return jsonResponse({ error: "Offline en geen cached data beschikbaar." }, 503);
  }
}

async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;

  const resp = await fetch(request);
  if (resp && (resp.ok || resp.type === "opaque")) {
    const cache = await caches.open(cacheName);
    cache.put(request, resp.clone());
  }
  return resp;
}

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET") return;

  if (url.origin === self.location.origin && url.pathname.startsWith("/api/")) {
    event.respondWith(handleApi(request));
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match("/index.html"))
    );
    return;
  }

  if (url.origin !== self.location.origin) {
    event.respondWith(
      cacheFirst(request, RUNTIME_CACHE).catch(() => caches.match(request))
    );
    return;
  }

  event.respondWith(
    cacheFirst(request, APP_CACHE).catch(() => caches.match("/index.html"))
  );
});
