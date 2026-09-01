// Replays #715 against the real library-paging.js in a stubbed browser.
//
// Driven by tests/test_next_library_hydration_replay.py. Everything else covering
// this module reads it as source text, which is how #715 shipped: every one of those
// assertions passed while the library stopped at 700 of 2,509 movies. This harness
// runs the module instead, so the property under test is what it does rather than
// what it says.
//
// The bridge below mirrors window.DiscVaultLibrary in next_views_ui.py exactly --
// appendMovies' de-duplication and offset guard, hasMoreMovies' two conditions,
// setHydrationComplete's total rewrite. If that contract changes there, it must
// change here, or this file is testing a bridge that no longer exists.
//
// Usage: node library-hydration-replay.mjs <path-to-library-paging.js> <scenario>
//   clean                          no snapshot reload; the baseline
//   race-fixed                     a reload lands mid-flight, bridge enforces the offset guard
//   race-unguarded                 the same reload against a bridge that ignores the guard
//   delete-mid-first-chunk         a movie is deleted (on another device -- no local purge)
//                                  while the FIRST chunk is in flight; the reset restores the
//                                  array to exactly the first-paint size, so the length guard
//                                  alone cannot see the reset (#719)
//   delete-mid-first-chunk-length-only  the same, against the pre-#719 bridge that checks only
//                                  the length -- the deleted movie must come back, proving the
//                                  epoch comparison is what does the work
import fs from "node:fs";

const PAGING_JS = process.argv[2];
const SNAPSHOT = 200, CHUNK = 500, TOTAL = 2509;
// Inside the first chunk's range (200..700), so the stale page carries it.
const DELETED_INDEX = 450;

function makeWorld({ resetAfterPages, guardSupported, epochGuardSupported = guardSupported, deleteOnReset = false }) {
  let server = Array.from({ length: TOTAL }, (_, i) => ({ id: "m" + i }));
  let movies = server.slice(0, SNAPSHOT);
  let libraryMovieTotal = TOTAL;
  let libraryMoviesHasMore = true;
  let epoch = 0;
  let pagesServed = 0;
  let warning = "";
  let deletedId = "";

  // Mirrors the session-local purge set in next_views_ui.py. Deliberately left
  // empty here: the delete scenarios model a deletion made on another device,
  // which is exactly the path where only the epoch guard can stop the ghost.
  const libraryDeletedMovieIds = new Set();

  const bridge = {
    t: (k, f) => f,
    hasMoreMovies: () => libraryMoviesHasMore === true && movies.length < libraryMovieTotal,
    getLoadedCount: () => movies.length,
    getSnapshotEpoch: guardSupported ? () => epoch : undefined,
    setMovieTotal: (t) => { const n = Number(t); if (Number.isFinite(n) && n >= 0) libraryMovieTotal = n; },
    appendMovies: (rows, expectedOffset, expectedEpoch) => {
      if (epochGuardSupported && expectedEpoch !== undefined && expectedEpoch !== null) {
        const expected = Number(expectedEpoch);
        if (!Number.isFinite(expected) || expected !== epoch) return null;
      }
      if (guardSupported && expectedOffset !== undefined && expectedOffset !== null) {
        const expected = Number(expectedOffset);
        if (!Number.isFinite(expected) || expected !== movies.length) return null;
      }
      if (!Array.isArray(rows) || !rows.length) return 0;
      const seen = new Set(movies.map((m) => String(m?.id || "")));
      const added = [];
      for (const row of rows) {
        const id = String(row?.id || "");
        if (!id || seen.has(id)) continue;
        if (libraryDeletedMovieIds.has(id)) continue;
        seen.add(id); added.push(row);
      }
      if (added.length) movies = movies.concat(added);
      libraryMoviesHasMore = movies.length < libraryMovieTotal;
      return added.length;
    },
    setHydrationWarning: (m) => { warning = String(m || ""); bridge.render(); },
    setHydrationComplete: () => {
      warning = ""; libraryMoviesHasMore = false;
      if (movies.length > libraryMovieTotal) libraryMovieTotal = movies.length;
    },
    getRenderStep: () => 120,
    growRenderLimit: () => 120,
    render: () => { if (typeof bridge.onRender === "function") bridge.onRender(); },
    authHeaders: (e) => e,
    onRender: null,
  };

  // The SPA reloading its own snapshot: movies snap back to the first-paint page.
  // With deleteOnReset the reload also carries a deletion made elsewhere: the
  // server loses one movie first, and the fresh snapshot reflects that -- while
  // the page already in flight was built from the pre-delete server.
  const reloadSnapshot = () => {
    if (deleteOnReset && !deletedId) {
      deletedId = server[DELETED_INDEX].id;
      server = server.filter((m) => m.id !== deletedId);
    }
    movies = server.slice(0, SNAPSHOT);
    libraryMovieTotal = server.length;
    libraryMoviesHasMore = true;
    warning = "";
    epoch += 1;
  };

  const timers = [];
  const g = {
    DiscVaultLibrary: bridge,
    setTimeout: (fn, ms) => { const id = timers.length; timers.push({ fn, at: ms, id }); return id; },
    clearTimeout: (id) => { const t = timers.find((t) => t && t.id === id); if (t) t.fn = null; },
    addEventListener: () => {},
    requestAnimationFrame: (fn) => g.setTimeout(fn, 16),
    fetch: (url) => {
      pagesServed += 1;
      const offset = Number(new URL(url, "http://x").searchParams.get("offset"));
      const limit = Number(new URL(url, "http://x").searchParams.get("limit"));
      // Body first, reset after: the response is generated from the server as it
      // stood when the request arrived, and the deletion lands while it is in flight.
      const items = server.slice(offset, offset + limit);
      const body = { items, total: server.length, limit, offset, hasMore: offset + items.length < server.length };
      // Land the snapshot reset while this page is in flight.
      if (resetAfterPages && pagesServed === resetAfterPages) reloadSnapshot();
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    },
  };
  const doc = {
    readyState: "complete",
    addEventListener: () => {},
    querySelector: () => null,
    visibilityState: "visible",
  };
  return { g, doc, bridge, state: () => ({
    loaded: movies.length,
    total: libraryMovieTotal,
    warning,
    pagesServed,
    deletedId,
    ghost: !!deletedId && movies.some((m) => String(m?.id || "") === deletedId),
  }) };
}

async function run(opts) {
  const world = makeWorld(opts);
  const src = fs.readFileSync(PAGING_JS, "utf8");
  const fn = new Function("window", "document", "IntersectionObserver", "console", src);
  fn(world.g, world.doc, undefined, console);
  // Drain the microtask queue; the loop is promise-chained, no real timers needed.
  for (let i = 0; i < 5000; i++) await Promise.resolve();
  return world.state();
}

const scenario = process.argv[3];
const opts =
  scenario === "clean" ? { resetAfterPages: 0, guardSupported: true }
  : scenario === "race-fixed" ? { resetAfterPages: 2, guardSupported: true }
  : scenario === "delete-mid-first-chunk"
    ? { resetAfterPages: 1, guardSupported: true, deleteOnReset: true }
  : scenario === "delete-mid-first-chunk-length-only"
    ? { resetAfterPages: 1, guardSupported: true, epochGuardSupported: false, deleteOnReset: true }
  : { resetAfterPages: 2, guardSupported: false };
run(opts).then((s) => console.log(JSON.stringify({ scenario, ...s })));
