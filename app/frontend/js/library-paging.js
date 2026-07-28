/*
 * DiscVault - library paging
 *
 * The library snapshot that ships with the first paint is deliberately small so the
 * inline `initialState` payload stays cheap. This module hydrates the remaining movies
 * in the background and renders the loaded rows incrementally, which removes the old
 * hard caps (200 movies server-side, 80 rendered rows client-side).
 *
 * It talks to the inline SPA exclusively through the `window.DiscVaultLibrary` bridge.
 */
(function () {
  "use strict";

  var MOVIES_ENDPOINT = "/api/next/collection/movies";
  var CHUNK_SIZE = 500;
  var MAX_CHUNKS = 200;
  var RENDER_DEBOUNCE_MS = 350;
  var BRIDGE_POLL_MS = 100;
  var BRIDGE_POLL_ATTEMPTS = 100;

  var state = {
    hydrating: false,
    hydrated: false,
    failed: false,
    observer: null,
    renderTimer: null,
    aborted: false,
  };

  function bridge() {
    return window.DiscVaultLibrary || null;
  }

  function translate(key, fallback) {
    var api = bridge();
    if (!api || typeof api.t !== "function") return fallback;
    try {
      return api.t(key, fallback) || fallback;
    } catch (error) {
      return fallback;
    }
  }

  function requestRender(immediate) {
    var api = bridge();
    if (!api || typeof api.render !== "function") return;
    if (immediate) {
      if (state.renderTimer) {
        window.clearTimeout(state.renderTimer);
        state.renderTimer = null;
      }
      api.render();
      return;
    }
    if (state.renderTimer) return;
    state.renderTimer = window.setTimeout(function () {
      state.renderTimer = null;
      var current = bridge();
      if (current && typeof current.render === "function") current.render();
    }, RENDER_DEBOUNCE_MS);
  }

  function fetchPage(api, offset) {
    var url = MOVIES_ENDPOINT + "?limit=" + CHUNK_SIZE + "&offset=" + offset;
    var headers = { Accept: "application/json" };
    if (typeof api.authHeaders === "function") {
      try {
        headers = api.authHeaders(headers) || headers;
      } catch (error) {
        /* fall back to the default headers */
      }
    }
    return window
      .fetch(url, { credentials: "same-origin", headers: headers })
      .then(function (response) {
        if (!response.ok) throw new Error("library page request failed: " + response.status);
        return response.json();
      });
  }

  function finishHydration(api) {
    state.hydrating = false;
    state.hydrated = true;
    if (api && typeof api.setHydrationComplete === "function") api.setHydrationComplete();
    requestRender(true);
  }

  function hydrate() {
    var api = bridge();
    if (!api || state.hydrating || state.hydrated || state.failed) return;
    if (typeof api.hasMoreMovies !== "function" || !api.hasMoreMovies()) return;
    state.hydrating = true;

    var chunks = 0;

    function step() {
      var current = bridge();
      if (!current || state.aborted) {
        state.hydrating = false;
        return;
      }
      if (chunks >= MAX_CHUNKS || !current.hasMoreMovies()) {
        finishHydration(current);
        return;
      }
      chunks += 1;
      fetchPage(current, current.getLoadedCount())
        .then(function (payload) {
          var live = bridge();
          if (!live || state.aborted) {
            state.hydrating = false;
            return;
          }
          if (payload && typeof payload.total === "number") live.setMovieTotal(payload.total);
          var items = payload && Array.isArray(payload.items) ? payload.items : [];
          var added = live.appendMovies(items);
          if (!added || !payload || payload.hasMore !== true) {
            finishHydration(live);
            return;
          }
          requestRender(false);
          step();
        })
        .catch(function (error) {
          state.hydrating = false;
          state.failed = true;
          console.warn(
            translate("collection.hydrationFailed", "Could not load the rest of the library."),
            error
          );
        });
    }

    step();
  }

  function ensureObserver() {
    var api = bridge();
    if (!api) return;
    var sentinel = document.querySelector("[data-library-render-sentinel]");
    if (!sentinel) return;
    if (!("IntersectionObserver" in window)) {
      api.growRenderLimit(api.getRenderStep());
      requestRender(true);
      return;
    }
    if (!state.observer) {
      state.observer = new window.IntersectionObserver(
        function (entries) {
          var visible = entries.some(function (entry) {
            return entry.isIntersecting;
          });
          if (!visible) return;
          var live = bridge();
          if (!live) return;
          state.observer.disconnect();
          live.growRenderLimit(live.getRenderStep());
          requestRender(true);
        },
        { rootMargin: "600px 0px" }
      );
    } else {
      state.observer.disconnect();
    }
    state.observer.observe(sentinel);
  }

  function onRender() {
    ensureObserver();
    hydrate();
  }

  function attach() {
    var api = bridge();
    if (!api) return false;
    api.onRender = onRender;
    onRender();
    return true;
  }

  function waitForBridge(attempt) {
    if (attach()) return;
    if (attempt >= BRIDGE_POLL_ATTEMPTS) return;
    window.setTimeout(function () {
      waitForBridge(attempt + 1);
    }, BRIDGE_POLL_MS);
  }

  window.addEventListener("pagehide", function () {
    state.aborted = true;
    if (state.observer) state.observer.disconnect();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      waitForBridge(0);
    });
  } else {
    waitForBridge(0);
  }
})();
