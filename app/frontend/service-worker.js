const SW_VERSION = "discvault-sw-v102";
const APP_CACHE = `${SW_VERSION}-app`;
const API_CACHE = `${SW_VERSION}-api`;
const RUNTIME_CACHE = `${SW_VERSION}-runtime`;

const APP_SHELL = [
  "/",
  "/index.html",
  "/styles.css",
  "/manifest.json",
  "/version.json",
  "/apple-touch-icon.png",
  "/favicon-32.png",
  "/favicon-192.png",
  "/icon.svg",
  "/logo.svg",
  "/js/i18n.js",
  "/js/scanner.js",
  "/js/collection.js",
  "/js/import.js",
  "/js/auth.js",
  "/js/social.js",
  "/js/settings.js",
  "/js/app.js",
  "/i18n/translations.json",
  "/flags/nl.svg",
  "/flags/de.svg",
  "/flags/fr.svg",
  "/flags/es.svg",
  "/flags/pt.svg",
  "/flags/it.svg",
  "/flags/us.svg",
  "/flags/gb.svg",
  "/flags/ca.svg"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(APP_CACHE).then(cache =>
      Promise.all(
        APP_SHELL.map(url =>
          cache.add(url).catch(err => console.warn('[SW] Failed to cache', url, err))
        )
      )
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => {
      const oldKeys = keys.filter(k => !k.startsWith(SW_VERSION));
      const didUpdate = oldKeys.length > 0;
      return Promise.all(oldKeys.map(k => caches.delete(k)))
        .then(() => self.clients.claim())
        .then(() => {
          if (didUpdate) {
            // Notify all open tabs to reload so they pick up fresh JS/CSS files
            return self.clients.matchAll({ type: 'window' }).then(clients =>
              clients.forEach(c => c.postMessage({ type: 'sw-updated' }))
            );
          }
        });
    })
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
    // Only cache successful responses — never cache errors
    if (networkResp.ok) {
      const cache = await caches.open(API_CACHE);
      cache.put(request, networkResp.clone());
    }
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

// Network-first: always try network, fall back to cache when offline.
// Used for JS/CSS/JSON so deploys are immediately visible after a reload.
// cache:'reload' bypasses the browser HTTP cache so stale files are never served.
async function networkFirst(request, cacheName) {
  try {
    const networkRequest = new Request(request, { cache: 'reload' });
    const resp = await fetch(networkRequest);
    if (resp && resp.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, resp.clone());
    }
    return resp;
  } catch {
    const cached = await caches.match(request);
    return cached || new Response('Offline', { status: 503 });
  }
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

  // JS, CSS, JSON → network-first so updated files are served immediately after a deploy+reload
  if (url.origin === self.location.origin && /\.(js|css|json)$/.test(url.pathname)) {
    event.respondWith(networkFirst(request, APP_CACHE));
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

// ── Push notifications ────────────────────────────────────────────────────────

self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch(e) {}
  const title   = data.title || "DiscVault";
  const options = {
    body:    data.body  || "",
    icon:    "/favicon-192.png",
    badge:   "/favicon-32.png",
    data:    { url: data.url || "/" },
    vibrate: [100, 50, 100],
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  const isInvite = url.includes("#invites");
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
      // If the app is already open, send a message instead of reloading the page.
      if (list.length > 0) {
        const client = list[0];
        if (isInvite) {
          client.postMessage({ type: "open-invites" });
        } else {
          client.navigate(url);
        }
        return client.focus();
      }
      // App not open — open a new window with the target URL.
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
