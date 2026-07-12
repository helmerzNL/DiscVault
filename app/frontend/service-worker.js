const SW_VERSION = "discvault-sw-v150";
const APP_CACHE = `${SW_VERSION}-app`;
const API_CACHE = `${SW_VERSION}-api`;
const RUNTIME_CACHE = `${SW_VERSION}-runtime`;

const APP_SHELL = [
  "/",
  "/index.html",
  "/styles.css",
  "/app",
  "/app/",
  "/import",
  "/lists",
  "/notifications",
  "/profile",
  "/admin",
  "/api/next/app",
  "/api/next/app/",
  "/api/next/app/import",
  "/api/next/manifest.json",
  "/manifest.json",
  "/version.json",
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
  "/icon.svg",
  "/logo.svg",
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
  "/js/i18n.js",
  "/js/scanner.js",
  "/js/collection.js",
  "/js/import.js",
  "/js/auth.js",
  "/js/social.js",
  "/js/settings.js",
  "/js/app.js",
  "/i18n/translations.json",
  "/api/next/i18n",
  "/api/next/i18n/nl-NL",
  "/api/next/i18n/en-US",
  "/api/next/i18n/fr-FR",
  "/api/next/i18n/de-DE",
  "/api/next/i18n/es-ES",
  "/api/next/i18n/pt-PT",
  "/api/next/i18n/it-IT",
  "/api/next/i18n/sv-SE",
  "/api/next/i18n/da-DK",
  "/api/next/i18n/nb-NO",
  "/api/next/i18n/fi-FI",
  "/flags/nl.svg",
  "/flags/de.svg",
  "/flags/fr.svg",
  "/flags/es.svg",
  "/flags/pt.svg",
  "/flags/it.svg",
  "/flags/sv.svg",
  "/flags/da.svg",
  "/flags/no.svg",
  "/flags/fi.svg",
  "/flags/us.svg",
  "/flags/gb.svg",
  "/flags/ca.svg",
  "/api/next/flags/nl.svg",
  "/api/next/flags/de.svg",
  "/api/next/flags/fr.svg",
  "/api/next/flags/es.svg",
  "/api/next/flags/pt.svg",
  "/api/next/flags/it.svg",
  "/api/next/flags/sv.svg",
  "/api/next/flags/da.svg",
  "/api/next/flags/no.svg",
  "/api/next/flags/fi.svg",
  "/api/next/flags/us.svg",
  "/api/next/flags/gb.svg",
  "/api/next/flags/ca.svg"
];

const APP_SHELL_FALLBACKS = ["/api/next/app", "/", "/index.html"];

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

function offlineResponseForRequest(request) {
  const url = new URL(request.url);
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

async function cacheMatch(request, cacheName) {
  const key = normalizedCacheRequest(request);
  const cache = cacheName ? await caches.open(cacheName) : null;
  return cache ? cache.match(key) : caches.match(key);
}

async function cachePut(request, response, cacheName) {
  if (!response || !(response.ok || response.type === "opaque")) return;
  const cache = await caches.open(cacheName);
  await cache.put(normalizedCacheRequest(request), response.clone());
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
  if (text.startsWith("/api/next/media/") || text.startsWith("/api/next/assets/") || text.startsWith("/api/next/flags/")) return true;
  if (!/^https?:\/\//i.test(text)) return false;
  if (/\/(image\.tmdb\.org|images\.static-bluray\.com)\//i.test(text)) return true;
  return /\.(avif|gif|jpe?g|png|svg|webp)(\?|#|$)/i.test(text);
}

function collectSnapshotImageUrls(snapshot) {
  const urls = new Set();
  const visit = value => {
    if (!value || urls.size >= 240) return;
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
  const cache = await caches.open(RUNTIME_CACHE);
  await Promise.all(urls.map(async assetUrl => {
    const absolute = new URL(assetUrl, self.location.origin);
    const request = absolute.origin === self.location.origin
      ? new Request(absolute.toString(), { method: "GET" })
      : new Request(absolute.toString(), { method: "GET", mode: "no-cors" });
    const cached = await cache.match(normalizedCacheRequest(request));
    if (cached) return;
    const resp = await fetch(request).catch(() => null);
    if (resp && (resp.ok || resp.type === "opaque")) await cache.put(normalizedCacheRequest(request), resp.clone());
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

async function handleApi(request, event) {
  const url = new URL(request.url);
  const isGet = request.method === "GET";
  const isLegacyAuthRoute = url.pathname.startsWith("/api/auth/");

  if (!isGet) {
    try {
      return await fetch(request.clone());
    } catch (error) {
      return jsonResponse({
        status: "error",
        offline: true,
        backend_unreachable: true,
        queueable: false,
        error: "Backend connection unavailable. Refresh DiscVault and try again.",
        detail: error && error.message ? error.message : "",
        path: url.pathname
      }, 503);
    }
  }

  if (isLegacyAuthRoute && url.pathname !== "/api/auth/status") {
    try {
      return await fetch(request);
    } catch {
      return jsonResponse({ status: "error", offline: true, error: "Offline: authentication endpoint unavailable." }, 503);
    }
  }

  try {
    const networkResp = await fetch(request);
    if (networkResp.ok) {
      await cachePut(request, networkResp, API_CACHE);
      if (url.pathname === "/api/next/app/snapshot") {
        const prefetch = prefetchSnapshotAssets(networkResp).catch(error => console.warn("[SW] Snapshot asset prefetch failed", error));
        if (event && event.waitUntil) event.waitUntil(prefetch);
      }
    }
    return networkResp;
  } catch {
    const cached = (await cacheMatch(request, API_CACHE)) || (await cacheMatch(request));
    if (cached) return cached;
    const detail = await detailFromSnapshot(url);
    if (detail) return detail;
    return apiFallback(url);
  }
}

async function cacheFirst(request, cacheName) {
  const cached = await cacheMatch(request, cacheName);
  if (cached) return cached;
  const resp = await fetch(request);
  if (resp && (resp.ok || resp.type === "opaque")) await cachePut(request, resp, cacheName);
  return resp;
}

async function networkFirst(request, cacheName) {
  try {
    const networkRequest = new Request(request, { cache: "reload" });
    const resp = await fetch(networkRequest);
    if (resp && resp.ok) await cachePut(request, resp, cacheName);
    return resp;
  } catch {
    const cached = await cacheMatch(request, cacheName);
    return cached || offlineResponseForRequest(request);
  }
}

async function appShellNetworkFirst(request) {
  try {
    const networkRequest = new Request(request, { cache: "reload" });
    const resp = await fetch(networkRequest);
    if (resp && resp.ok) await cachePut(request, resp, APP_CACHE);
    return resp;
  } catch {
    const cached = await cacheMatch(request, APP_CACHE);
    return cached || appShellFallback();
  }
}

async function appShellFallback() {
  for (const path of APP_SHELL_FALLBACKS) {
    const cached = await cacheMatch(new Request(new URL(path, self.location.origin).toString(), { method: "GET" }), APP_CACHE);
    if (cached) return cached;
  }
  return new Response("DiscVault is offline and the app shell is not cached yet.", {
    status: 503,
    headers: { "Content-Type": "text/plain", "X-DiscVault-Offline": "1" }
  });
}

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);

  if (url.origin === self.location.origin && url.pathname.startsWith("/api/")) {
    event.respondWith(handleApi(request, event));
    return;
  }

  if (request.method !== "GET") return;

  if (request.mode === "navigate") {
    event.respondWith(appShellNetworkFirst(request));
    return;
  }

  if (
    url.origin === self.location.origin &&
    (
      url.pathname.startsWith("/api/next/media/") ||
      url.pathname.startsWith("/api/next/assets/") ||
      url.pathname.startsWith("/api/next/flags/") ||
      url.pathname.startsWith("/flags/")
    )
  ) {
    event.respondWith(cacheFirst(request, RUNTIME_CACHE).catch(async () => (await cacheMatch(request, RUNTIME_CACHE)) || offlineResponseForRequest(request)));
    return;
  }

  if (url.origin === self.location.origin && /\.(js|css|json)$/.test(url.pathname)) {
    event.respondWith(networkFirst(request, APP_CACHE));
    return;
  }

  if (url.origin !== self.location.origin) {
    event.respondWith(cacheFirst(request, RUNTIME_CACHE).catch(async () => (await cacheMatch(request, RUNTIME_CACHE)) || offlineResponseForRequest(request)));
    return;
  }

  event.respondWith(cacheFirst(request, APP_CACHE).catch(() => appShellFallback()));
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
