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
//   clean            no snapshot reload; the baseline
//   race-fixed       a reload lands mid-flight, bridge enforces the offset guard
//   race-unguarded   the same reload against a bridge that ignores the guard
import fs from "node:fs";

const PAGING_JS = process.argv[2];
const SNAPSHOT = 200, CHUNK = 500, TOTAL = 2509;

function makeWorld({ resetAfterPages, guardSupported }) {
  const server = Array.from({ length: TOTAL }, (_, i) => ({ id: "m" + i }));
  let movies = server.slice(0, SNAPSHOT);
  let libraryMovieTotal = TOTAL;
  let libraryMoviesHasMore = true;
  let epoch = 0;
  let pagesServed = 0;
  let warning = "";

  const bridge = {
    t: (k, f) => f,
    hasMoreMovies: () => libraryMoviesHasMore === true && movies.length < libraryMovieTotal,
    getLoadedCount: () => movies.length,
    getSnapshotEpoch: guardSupported ? () => epoch : undefined,
    setMovieTotal: (t) => { const n = Number(t); if (Number.isFinite(n) && n >= 0) libraryMovieTotal = n; },
    appendMovies: (rows, expectedOffset) => {
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
  const reloadSnapshot = () => {
    movies = server.slice(0, SNAPSHOT);
    libraryMovieTotal = TOTAL;
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
      const items = server.slice(offset, offset + limit);
      const body = { items, total: TOTAL, limit, offset, hasMore: offset + items.length < TOTAL };
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
  return { g, doc, bridge, state: () => ({ loaded: movies.length, total: libraryMovieTotal, warning, pagesServed }) };
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
  : { resetAfterPages: 2, guardSupported: false };
run(opts).then((s) => console.log(JSON.stringify({ scenario, ...s })));
