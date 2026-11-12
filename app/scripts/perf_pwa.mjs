#!/usr/bin/env node
// Measure what a browser does with the library the server holds.
//
// The browser half of the measurement harness (D-0 of the performance roadmap in
// App-Guidance, shared/plans/2026-11-11-discvault-performance-roadmap.md).
// perf_measure.py times the server; this times the page: how long until the
// library can be used, how long until it is completely loaded, what one search
// keystroke costs once it is, and how much memory the page then holds.
//
// It needs a running DiscVault (the real gunicorn, so the two sync workers are
// part of the number) on a database filled by perf_seed.py, and Playwright:
//
//   cd app/backend && DATABASE_URL=... JWT_SECRET=local-only LEGACY_AUTH_ENABLED=true \
//       gunicorn --bind 127.0.0.1:5057 --workers 2 --timeout 180 next_app:app
//   node app/scripts/perf_pwa.mjs --url http://127.0.0.1:5057/ --create-owner --output /tmp/perf/pwa.json
//
// The page shows no library until the install has an owner, and passkey
// onboarding needs an HTTPS domain name. On a throwaway database, --create-owner
// uses the password onboarding instead (LEGACY_AUTH_ENABLED=true, a local
// address, no users yet) and signs the browser in with the session it returns.
// For an install that already has an owner, pass --token <session token>.
//
// Local only, not a CI gate: a browser timing on a shared runner is noise. The
// seeded artwork lives on posters.invalid, which never resolves, so no poster is
// drawn and image decoding is NOT part of these numbers.

import { writeFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

function argument(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index > -1 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const url = argument("url", "http://127.0.0.1:5057/");
const output = argument("output", "");
const hydrationTimeoutMs = Number(argument("hydration-timeout-ms", "900000"));
const searchText = argument("search", "river");
let token = argument("token", "");

// First-owner password onboarding, for a throwaway local install only.
async function createOwner() {
  const post = async (path, body) => {
    const response = await fetch(new URL(path, url), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(`${path}: ${response.status} ${payload.error || ""}`);
    return payload;
  };
  const started = await post("/api/next/auth/legacy/bootstrap/start", {
    username: "perfowner",
    password: `Perf-${Math.random().toString(36).slice(2)}-${Date.now()}-Aa1!`,
    mfa_enabled: false,
  });
  const done = await post("/api/next/auth/legacy/recovery-codes/ack", {
    flow_token: started.flow_token,
    acknowledged: true,
  });
  if (!done.token) throw new Error("the onboarding returned no session token");
  return done.token;
}

if (process.argv.includes("--create-owner") && !token) {
  try {
    token = await createOwner();
  } catch (error) {
    console.error(`ERROR: could not create an owner (${error.message}). Pass --token for an install that has one.`);
    process.exit(2);
  }
}

let chromium;
try {
  ({ chromium } = require("playwright"));
} catch {
  console.error("ERROR: Playwright is not installed (npm install playwright, then npx playwright install chromium)");
  process.exit(2);
}

const round = (value) => Math.round(value);

// Runs in the page before any of its scripts: collects long tasks (>50 ms on the main thread).
function installObservers() {
  window.__perf = { longTasks: [] };
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        window.__perf.longTasks.push({ start: entry.startTime, duration: entry.duration });
      }
    }).observe({ type: "longtask", buffered: true });
  } catch {
    window.__perf.longTasksUnsupported = true;
  }
}

function longTaskSummary(tasks) {
  const durations = tasks.map((task) => task.duration);
  return {
    count: durations.length,
    totalMs: round(durations.reduce((sum, value) => sum + value, 0)),
    longestMs: round(durations.reduce((max, value) => Math.max(max, value), 0)),
  };
}

// One interaction: run `action`, then wait until the main thread has been quiet
// for two animation frames. The cost is the wall time plus the long tasks in it.
async function interaction(page, label, action) {
  const before = await page.evaluate(() => window.__perf.longTasks.length);
  const started = Date.now();
  await action();
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 0)))),
  );
  const elapsed = Date.now() - started;
  await page.waitForTimeout(400); // the long-task observer reports late
  const tasks = await page.evaluate((from) => window.__perf.longTasks.slice(from), before);
  return { label, wallMs: elapsed, longTasks: longTaskSummary(tasks) };
}

async function heapMb(session) {
  await session.send("HeapProfiler.collectGarbage");
  const { usedSize } = await session.send("Runtime.getHeapUsage");
  return round(usedSize / (1024 * 1024));
}

const browser = await chromium.launch({ args: ["--no-sandbox"] });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
await page.addInitScript(installObservers);
if (token) await context.addInitScript((value) => localStorage.setItem("dv_next_token", value), token);
const session = await context.newCDPSession(page);

let transferred = 0;
let requests = 0;
page.on("response", async (response) => {
  requests += 1;
  const length = Number(response.headers()["content-length"] || 0);
  transferred += Number.isFinite(length) ? length : 0;
});

const report = { url, measuredAt: new Date().toISOString(), userAgent: await browser.version() };

const navigationStart = Date.now();
await page.goto(url, { waitUntil: "domcontentloaded", timeout: 120000 });
report.domContentLoadedMs = Date.now() - navigationStart;
await page.waitForFunction(() => window.DiscVaultLibrary && window.DiscVaultLibrary.getLoadedCount() > 0, null, {
  timeout: 120000,
});
await page.waitForSelector("[data-preview-movie]", { timeout: 120000 });
report.firstTilesMs = Date.now() - navigationStart;
report.total = await page.evaluate(() => window.DiscVaultLibrary.getMovieTotal());
report.loadedAtFirstTiles = await page.evaluate(() => window.DiscVaultLibrary.getLoadedCount());
report.heapAtFirstTilesMb = await heapMb(session);

try {
// Hydration: library-paging.js walks the whole library in the background.
let hydrated = true;
try {
  await page.waitForFunction(
    () => window.DiscVaultLibrary.getLoadedCount() >= window.DiscVaultLibrary.getMovieTotal(),
    null,
    { timeout: hydrationTimeoutMs, polling: 500 },
  );
} catch {
  hydrated = false;
}
report.hydrated = hydrated;
report.hydrationMs = Date.now() - navigationStart;
report.loaded = await page.evaluate(() => window.DiscVaultLibrary.getLoadedCount());
await page.waitForTimeout(1500); // let the debounced re-render after the last page finish
report.longTasksDuringLoad = longTaskSummary(await page.evaluate(() => window.__perf.longTasks));
report.heapHydratedMb = await heapMb(session);
report.domNodesHydrated = await page.evaluate(() => document.getElementsByTagName("*").length);
report.requests = requests;
report.transferredMb = Math.round((transferred / (1024 * 1024)) * 10) / 10;

// Interactions on the fully loaded library. A first-run dialog (the support
// prompt) may sit over the page; close it the way a user would.
const dialogClose = page.locator("#supportPromptCloseButton");
if (await dialogClose.isVisible().catch(() => false)) await dialogClose.click();
report.interactions = [];
const search = page.locator("#previewSearch");
if (await search.count()) {
  await search.click({ timeout: 15000 });
  for (const character of searchText) {
    report.interactions.push(
      await interaction(page, `search keystroke "${character}"`, () => page.keyboard.type(character)),
    );
  }
  report.searchResultTiles = await page.locator("[data-preview-movie]").count();
  report.interactions.push(
    await interaction(page, "clear search", async () => {
      await search.fill("");
    }),
  );
}
report.interactions.push(
  await interaction(page, "scroll to the end of the rendered list", () =>
    page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight)),
  ),
);
report.domNodesAfterScroll = await page.evaluate(() => document.getElementsByTagName("*").length);
report.heapAfterInteractionsMb = await heapMb(session);
} catch (error) {
  // Keep what was measured: a 50,000-film load is too slow to lose to a late step.
  report.error = String(error && error.message ? error.message : error).split("\n")[0];
}

await browser.close();

if (output) writeFileSync(output, `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify(report, null, 2));
process.exit(report.error ? 1 : 0);
