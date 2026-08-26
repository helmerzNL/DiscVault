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
 * Three failure modes have bitten users before and are guarded here explicitly:
 *
 *   - A single failed page request used to disable hydration for the rest of the page
 *     load, leaving the library stuck on its first page with nothing but a console
 *     warning. Failures are now retried with backoff, re-armed when the browser comes
 *     back online, and surfaced in the UI once the retries are spent.
 *   - Stopping at MAX_CHUNKS used to be reported as "hydration complete", which made
 *     the bridge rewrite the total to the loaded count so the counter claimed a
 *     truncated library was whole. Only the server saying `hasMore !== true` counts as
 *     complete now.
 *   - Finishing once used to disable hydration for the rest of the page's life. The
 *     inline SPA reloads its snapshot on its own (returning from a movie, an import, a
 *     bulk action), which resets the bridge's movies/total/hasMore straight back to the
 *     small first-paint set - completely outside this module. hydrate() must not treat
 *     "finished before" as "nothing to do now"; the bridge's own hasMoreMovies() is the
 *     only thing allowed to decide that, checked fresh on every call.
 *   - That same reset used to strand a page already in flight. Its response was appended
 *     blind to an array that had shrunk back to 200 rows, so it landed hundreds of rows
 *     past the end and left a hole no offset could step back over; the next page came
 *     back entirely duplicated, and the anti-loop guard - which by design never retries -
 *     stopped hydration for good, at 200 + 500 (#715). Two rules follow, and both are
 *     load-bearing: a page may only be appended to the array it was fetched against
 *     (appendMovies' expectedOffset), and the cursor may only advance by what the server
 *     says it served (payload.offset + items.length), never by the de-duplicated loaded
 *     count. A snapshot reset is normal, so it restarts the cycle rather than ending it.
 */
(function () {
  "use strict";

  var MOVIES_ENDPOINT = "/api/next/collection/movies";
  var CHUNK_SIZE = 500;
  var MAX_CHUNKS = 200;
  // A snapshot reload landing mid-flight is normal and self-correcting - the cycle
  // simply restarts from wherever the array now ends. A burst of them (a bulk action,
  // a rapid save/back/save) is not worth chasing inside one cycle forever.
  var MAX_STALE_RESTARTS = 5;
  var RENDER_DEBOUNCE_MS = 350;
  var BRIDGE_POLL_MS = 100;
  var BRIDGE_POLL_ATTEMPTS = 100;
  var RETRY_DELAYS_MS = [1000, 3000, 8000];
  // Once the fast retries above are spent, a page fetch that keeps failing (a server
  // hiccup, not a real connectivity loss) used to give up forever - `online` only fires
  // on an actual OS-level connectivity transition. These slower, capped retries give a
  // transient failure a way back without hammering a backend that is genuinely down.
  var SLOW_RETRY_DELAYS_MS = [30000, 60000, 120000, 300000];
  var SLOW_RETRY_JITTER = 0.2;

  var state = {
    hydrating: false,
    hydrated: false,
    truncated: false,
    attempt: 0,
    slowAttempt: 0,
    retryTimer: null,
    slowRetryTimer: null,
    observer: null,
    renderTimer: null,
    growthScheduled: false,
    aborted: false,
    // `null` rather than 0 so the first hydrate() after a late bridge attach adopts
    // whatever the SPA is on instead of reading a spurious reset.
    epoch: null,
  };

  function bridge() {
    return window.DiscVaultLibrary || null;
  }

  /**
   * The SPA's snapshot generation, or null when the bridge predates it - an older
   * inline SPA served from a cache must keep working, just without the reset check.
   */
  function snapshotEpoch(api) {
    if (!api || typeof api.getSnapshotEpoch !== "function") return null;
    try {
      var value = api.getSnapshotEpoch();
      return typeof value === "number" ? value : null;
    } catch (error) {
      return null;
    }
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
    // Only a real success resets the slow-retry backoff - every wake attempt against a
    // still-down backend (a focus regain, a timer tick) must keep escalating it instead
    // of getting pinned back at the shortest delay.
    state.slowAttempt = 0;
    if (state.slowRetryTimer) {
      window.clearTimeout(state.slowRetryTimer);
      state.slowRetryTimer = null;
    }
    if (api && typeof api.setHydrationComplete === "function") api.setHydrationComplete();
    requestRender(true);
  }

  /**
   * Stop without claiming completeness: the loaded set is a prefix of the library,
   * and the total must stay as the server reported it so the counter stays honest.
   *
   * Shared by three call sites, only one of which is transient: a page fetch that
   * failed can succeed on a later try, but MAX_CHUNKS and the zero-rows anti-loop
   * guard below cannot self-resolve and must stay permanent. Callers that want a way
   * back call scheduleSlowRetry() themselves after this returns - it is not automatic
   * here, since it would be wrong for the other two.
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

  /**
   * The single entry point back into hydration after a truncation. Consolidated here
   * rather than duplicated in every "try again" trigger (online, visibility regain, the
   * slow-retry timer itself) so a stale slow-retry timer can never survive an earlier
   * wake and double-fire hydrate() later, racing whatever retry that earlier wake is
   * already in the middle of.
   */
  function wakeFromTruncation() {
    if (state.slowRetryTimer) {
      window.clearTimeout(state.slowRetryTimer);
      state.slowRetryTimer = null;
    }
    state.attempt = 0;
    state.truncated = false;
    hydrate();
  }

  function scheduleSlowRetry() {
    if (state.slowRetryTimer || state.hydrated || state.aborted) return;
    var base = SLOW_RETRY_DELAYS_MS[Math.min(state.slowAttempt, SLOW_RETRY_DELAYS_MS.length - 1)];
    var jitter = base * SLOW_RETRY_JITTER * (Math.random() * 2 - 1);
    state.slowAttempt += 1;
    state.slowRetryTimer = window.setTimeout(function () {
      state.slowRetryTimer = null;
      wakeFromTruncation();
    }, Math.round(base + jitter));
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
    // Unlike MAX_CHUNKS and the anti-loop guard below, a page request failing outright
    // is exactly the kind of transient blip a later retry can clear on its own.
    scheduleSlowRetry();
  }

  function hydrate() {
    var api = bridge();
    if (!api || state.aborted) return;

    // The SPA replaced `movies` wholesale. Everything this module concluded about the
    // previous array describes data that no longer exists - above all a truncation,
    // which it can otherwise never clear by itself. loadAppSnapshot() wipes the
    // on-screen warning at that same moment, so leaving `truncated` set is the worst of
    // both: a library that stays short with nothing on screen saying why (#715).
    var epoch = snapshotEpoch(api);
    if (epoch !== null && epoch !== state.epoch) {
      state.epoch = epoch;
      state.truncated = false;
      state.hydrated = false;
      state.attempt = 0;
      if (state.retryTimer) {
        window.clearTimeout(state.retryTimer);
        state.retryTimer = null;
      }
      if (state.slowRetryTimer) {
        window.clearTimeout(state.slowRetryTimer);
        state.slowRetryTimer = null;
      }
      // state.slowAttempt is deliberately left alone: a snapshot reload is no evidence
      // the backend recovered, and only a page that actually lands may unwind that
      // backoff. state.hydrating is left alone too - a cycle already in flight must
      // discover the reset through its own appendMovies guard rather than have a second
      // cycle stacked on top of it.
    }

    // `state.hydrated` is not checked here on purpose: it only records that a past
    // cycle finished, and the inline SPA can reset the bridge's movies/hasMore back to
    // the small first-paint set at any time, entirely outside this module (reloading
    // its snapshot on returning from a movie, an import, a bulk action). Whether there
    // is work to do is decided fresh, every call, by hasMoreMovies() alone.
    if (state.hydrating || state.truncated) return;
    if (typeof api.hasMoreMovies !== "function" || !api.hasMoreMovies()) return;
    state.hydrating = true;
    state.hydrated = false;

    var chunks = 0;
    var staleRestarts = 0;
    // A cycle *starts* from the loaded count - after a snapshot reset that is the only
    // honest answer - but it does not *advance* by it. See nextOffset below.
    var cursor = api.getLoadedCount();

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
      var offset = cursor;
      // Not `offset`. appendMovies() de-duplicates, so after any page that overlapped
      // rows already held, movies.length trails the server's cursor. The guard asks
      // "is this still the array I fetched against", which is a question about the
      // array's length - measured here, immediately before the request goes out.
      var expectedLoaded = current.getLoadedCount();
      fetchPage(current, offset)
        .then(function (payload) {
          var live = bridge();
          if (!live || state.aborted) {
            state.hydrating = false;
            return;
          }
          state.attempt = 0;
          if (payload && typeof payload.total === "number") live.setMovieTotal(payload.total);
          var items = payload && Array.isArray(payload.items) ? payload.items : [];
          var added = live.appendMovies(items, expectedLoaded);
          if (added === null) {
            // The SPA reset its snapshot while this page was in flight. Appending it is
            // exactly what used to open the hole; drop it and restart the cycle from
            // where the array actually is now. Nothing is wrong, so this is not a
            // truncation - but a reset arriving on every retry is a loop like any other.
            state.epoch = snapshotEpoch(live);
            staleRestarts += 1;
            if (staleRestarts > MAX_STALE_RESTARTS) {
              stopTruncated(
                live,
                "collection.hydrationTruncated",
                "Only part of the library could be loaded."
              );
              // Unlike MAX_CHUNKS and the cursor guard below, a burst of snapshot
              // reloads is transient by nature and clears on its own.
              scheduleSlowRetry();
              return;
            }
            cursor = live.getLoadedCount();
            step();
            return;
          }
          // Advance on what the server says it served, never on the de-duplicated
          // loaded count: a page overlapping rows already held would otherwise leave
          // the cursor short and be requested again for as long as the overlap lasts.
          var served = payload && typeof payload.offset === "number" ? payload.offset : offset;
          var nextOffset = served + items.length;
          if (!payload || payload.hasMore !== true) {
            finishHydration(live);
            return;
          }
          if (nextOffset <= offset) {
            // The server reports more rows but did not move the cursor. Continuing
            // would request the same page forever, and unlike a stale page this cannot
            // resolve itself. Note this is the real invariant - a page that added
            // nothing but *did* advance is survivable, and treating it as fatal is what
            // made an unstable sort order enough to end hydration for good.
            stopTruncated(
              live,
              "collection.hydrationTruncated",
              "Only part of the library could be loaded."
            );
            return;
          }
          cursor = nextOffset;
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
    wakeFromTruncation();
  });

  // `online` only fires on a real connectivity transition, not a server hiccup while
  // the network stays up - regaining focus is the other common moment a stalled
  // hydration is worth trying again, on top of the slow-retry timer already ticking.
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") return;
    if (state.hydrated || state.aborted || !state.truncated) return;
    wakeFromTruncation();
  });

  window.addEventListener("pagehide", function () {
    state.aborted = true;
    if (state.retryTimer) {
      window.clearTimeout(state.retryTimer);
      state.retryTimer = null;
    }
    if (state.slowRetryTimer) {
      window.clearTimeout(state.slowRetryTimer);
      state.slowRetryTimer = null;
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
