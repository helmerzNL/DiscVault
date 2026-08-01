/*
 * DiscVault - library paging
 *
 * The library snapshot that ships with the first paint is deliberately small so the
 * inline `initialState` payload stays cheap. This module hydrates the remaining movies
 * in the background and renders the loaded rows incrementally, which removes the old
 * hard caps (200 movies server-side, 80 rendered rows client-side).
 *
 * It talks to the inline SPA exclusively through the `window.DiscVaultLibrary` bridge.
 *
 * Two failure modes have bitten users before and are guarded here explicitly:
 *
 *   - A single failed page request used to disable hydration for the rest of the page
 *     load, leaving the library stuck on its first page with nothing but a console
 *     warning. Failures are now retried with backoff, re-armed when the browser comes
 *     back online, and surfaced in the UI once the retries are spent.
 *   - Stopping at MAX_CHUNKS used to be reported as "hydration complete", which made
 *     the bridge rewrite the total to the loaded count so the counter claimed a
 *     truncated library was whole. Only the server saying `hasMore !== true` counts as
 *     complete now.
 */
(function () {
  "use strict";

  var MOVIES_ENDPOINT = "/api/next/collection/movies";
  var CHUNK_SIZE = 500;
  var MAX_CHUNKS = 200;
  var RENDER_DEBOUNCE_MS = 350;
  var BRIDGE_POLL_MS = 100;
  var BRIDGE_POLL_ATTEMPTS = 100;
  var RETRY_DELAYS_MS = [1000, 3000, 8000];

  var state = {
    hydrating: false,
    hydrated: false,
    truncated: false,
    attempt: 0,
    retryTimer: null,
    observer: null,
    renderTimer: null,
    growthScheduled: false,
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

  function warn(api, key, fallback) {
    if (!api || typeof api.setHydrationWarning !== "function") return;
    try {
      api.setHydrationWarning(translate(key, fallback));
    } catch (error) {
      /* the bridge may have been torn down mid-navigation */
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
    state.attempt = 0;
    if (api && typeof api.setHydrationComplete === "function") api.setHydrationComplete();
    requestRender(true);
  }

  /**
   * Stop without claiming completeness: the loaded set is a prefix of the library,
   * and the total must stay as the server reported it so the counter stays honest.
   */
  function stopTruncated(api, key, fallback) {
    // `truncated` must be set before the warning: setHydrationWarning() re-renders,
    // and a render calls back into onRender() -> hydrate().
    state.truncated = true;
    state.hydrating = false;
    warn(api, key, fallback);
  }

  function scheduleRetry() {
    if (state.retryTimer) return;
    var delay = RETRY_DELAYS_MS[Math.min(state.attempt, RETRY_DELAYS_MS.length - 1)];
    state.retryTimer = window.setTimeout(function () {
      state.retryTimer = null;
      hydrate();
    }, delay);
  }

  function onPageError(error) {
    state.hydrating = false;
    state.attempt += 1;
    var api = bridge();
    if (state.attempt <= RETRY_DELAYS_MS.length) {
      console.warn("DiscVault library hydration failed, retrying", error);
      scheduleRetry();
      return;
    }
    console.warn(
      translate("collection.hydrationFailed", "Could not load the rest of the library."),
      error
    );
    stopTruncated(
      api,
      "collection.hydrationFailed",
      "Could not load the rest of the library."
    );
  }

  function hydrate() {
    var api = bridge();
    if (!api || state.hydrating || state.hydrated || state.truncated || state.aborted) return;
    if (typeof api.hasMoreMovies !== "function" || !api.hasMoreMovies()) return;
    state.hydrating = true;

    var chunks = 0;

    function step() {
      var current = bridge();
      if (!current || state.aborted) {
        state.hydrating = false;
        return;
      }
      if (!current.hasMoreMovies()) {
        finishHydration(current);
        return;
      }
      if (chunks >= MAX_CHUNKS) {
        stopTruncated(
          current,
          "collection.hydrationTruncated",
          "Only part of the library could be loaded."
        );
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
          state.attempt = 0;
          if (payload && typeof payload.total === "number") live.setMovieTotal(payload.total);
          var items = payload && Array.isArray(payload.items) ? payload.items : [];
          var added = live.appendMovies(items);
          if (!payload || payload.hasMore !== true) {
            finishHydration(live);
            return;
          }
          if (!added) {
            // The server still reports more rows but sent nothing new. Continuing
            // would loop on the same offset forever.
            stopTruncated(
              live,
              "collection.hydrationTruncated",
              "Only part of the library could be loaded."
            );
            return;
          }
          requestRender(false);
          step();
        })
        .catch(onPageError);
    }

    step();
  }

  /**
   * Grow the rendered window one step per animation frame. Growing synchronously
   * inside the observer callback re-renders immediately, emits a fresh sentinel that
   * is usually still inside the 600px root margin, and fires the callback again — a
   * cascade of full re-renders in one burst, each over a larger row set.
   */
  function scheduleGrowth() {
    if (state.growthScheduled) return;
    state.growthScheduled = true;
    var run = function () {
      state.growthScheduled = false;
      var live = bridge();
      if (!live || state.aborted) return;
      live.growRenderLimit(live.getRenderStep());
      requestRender(true);
    };
    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(run);
    } else {
      window.setTimeout(run, 16);
    }
  }

  function ensureObserver() {
    var api = bridge();
    if (!api) return;
    var sentinel = document.querySelector("[data-library-render-sentinel]");
    if (!sentinel) return;
    if (!("IntersectionObserver" in window)) {
      scheduleGrowth();
      return;
    }
    if (!state.observer) {
      state.observer = new window.IntersectionObserver(
        function (entries) {
          var visible = entries.some(function (entry) {
            return entry.isIntersecting;
          });
          if (!visible) return;
          state.observer.disconnect();
          scheduleGrowth();
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

  // A dropped connection is the common cause of a failed page, so retry the moment
  // connectivity returns rather than leaving the library short until a reload.
  window.addEventListener("online", function () {
    if (state.hydrated || state.aborted) return;
    state.attempt = 0;
    state.truncated = false;
    hydrate();
  });

  window.addEventListener("pagehide", function () {
    state.aborted = true;
    if (state.retryTimer) {
      window.clearTimeout(state.retryTimer);
      state.retryTimer = null;
    }
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
