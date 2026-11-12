#!/usr/bin/env python3
"""Measure the hot read paths of DiscVault Next on whatever library the database holds.

The measuring half of the harness (D-0 of the performance roadmap in
App-Guidance, `shared/plans/2026-11-11-discvault-performance-roadmap.md`);
`perf_seed.py` provides the data. It follows the order of work in
`shared/guidelines/performance-playbook.md`: measure the slow path first, cold
and warm, on data of production size, and write the numbers down with a date and
a data size.

For every endpoint in ENDPOINTS it records:

  first      the first call in this process (imports and caches cold)
  p50, p95   over `--runs` further calls
  bytes      the response body, uncompressed (the app sends no compression)
  queries    SQL statements executed for one call
  conns      database connections opened for one call
  slowest    the slowest single statement of one call

plus the full library hydration the PWA performs (every page of
`/api/next/collection/movies`, 500 rows at a time, by OFFSET), and with
`--explain` the plan of each statement slower than `--explain-above-ms`, listing
the sequential scans in it.

It drives the Flask app in-process through its test client, as the built-in
system owner (a database without users). So the numbers are Python plus
PostgreSQL: no network, no gunicorn, no TLS, no browser. They are a floor, not
what a user waits for, and they say nothing about the group-visibility
predicate a non-owner account gets. They are meant to be compared with
themselves: before and after a change, on the same data and the same machine.

It only issues GET requests. Some of those write (the personal statistics store
a value snapshot; that is a finding, not a harness side effect), so point it at
a throwaway database, never at an install.

    DATABASE_URL=postgresql://... JWT_SECRET=local-only \\
        python app/scripts/perf_measure.py --output-dir /tmp/perf --explain
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

HYDRATION_CHUNK = 500  # CHUNK_SIZE in app/frontend/js/library-paging.js

# (label, path). `{deep}`, `{last}`, `{movie}`, `{revision}` and `{asset}` are filled from the database.
ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("health", "/api/next/health"),
    ("startup status", "/api/next/startup/status"),
    ("app shell (HTML)", "/"),
    ("app snapshot", "/api/next/app/snapshot"),
    ("library page, offset 0", "/api/next/collection/movies?limit=500&offset=0"),
    ("library page, middle", "/api/next/collection/movies?limit=500&offset={deep}"),
    ("library page, last", "/api/next/collection/movies?limit=500&offset={last}"),
    ("movie search (title)", "/api/next/movies?q=river&limit=50"),
    ("API v1 search (title + metadata)", "/api/next/api/v1/movies?q=river&limit=100"),
    ("API v1 list, limit 1000 (MCP)", "/api/next/api/v1/movies?limit=1000"),
    ("movie detail", "/api/next/movies/{movie}"),
    ("containers", "/api/next/containers"),
    ("people, as the page opens", "/api/next/people?limit=180"),
    ("people, search", "/api/next/people?q=berg&limit=180"),
    ("personal statistics", "/api/next/stats/personal"),
    ("statistics", "/api/next/stats"),
    # Seeded artwork is remote, so this answers 404 after the same auth and lookup a real poster pays.
    ("media asset (auth and lookup)", "/api/next/media/assets/{asset}"),
    ("sync state", "/api/next/sync/state"),
    ("sync delta, caught up", "/api/next/sync/delta?since={revision}"),
    ("sync delta, from 0 (one page)", "/api/next/sync/delta?since=0"),
    ("sync bootstrap, limit 5000", "/api/next/sync/bootstrap?limit=5000"),
    ("backup status", "/api/next/backup/status"),
)


class StatementLog:
    """Counts what one request sends to PostgreSQL, by wrapping psycopg itself."""

    def __init__(self) -> None:
        self.statements: list[tuple[float, str, object]] = []
        self.connections = 0
        self.enabled = False

    def reset(self) -> None:
        self.statements = []
        self.connections = 0

    def install(self) -> None:
        import psycopg

        log = self
        original_execute = psycopg.Cursor.execute
        original_connect = psycopg.Connection.connect.__func__

        def execute(cursor, query, params=None, **kwargs):
            if not log.enabled:
                return original_execute(cursor, query, params, **kwargs)
            started = time.perf_counter()
            try:
                return original_execute(cursor, query, params, **kwargs)
            finally:
                log.statements.append((time.perf_counter() - started, query, params))

        def connect(cls, *args, **kwargs):
            if log.enabled:
                log.connections += 1
            return original_connect(cls, *args, **kwargs)

        psycopg.Cursor.execute = execute
        psycopg.Connection.connect = classmethod(connect)
        psycopg.connect = psycopg.Connection.connect


def _statement_text(query) -> str:
    text = query if isinstance(query, str) else getattr(query, "as_string", lambda _=None: str(query))(None)
    return " ".join(str(text).split())


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def _call(client, log: StatementLog, path: str) -> dict:
    log.reset()
    log.enabled = True
    started = time.perf_counter()
    response = client.get(path)
    body = response.get_data()
    elapsed = time.perf_counter() - started
    log.enabled = False
    return {
        "status": response.status_code,
        "seconds": elapsed,
        "bytes": len(body),
        "queries": len(log.statements),
        "connections": log.connections,
        "statements": list(log.statements),
        "body": body,
    }


def _seq_scans(node: dict, found: list[dict]) -> None:
    if node.get("Node Type") == "Seq Scan":
        found.append(
            {
                "table": node.get("Relation Name"),
                "rows": node.get("Actual Rows"),
                "loops": node.get("Actual Loops"),
                "removedByFilter": node.get("Rows Removed by Filter", 0),
            }
        )
    for child in node.get("Plans", []) or []:
        _seq_scans(child, found)


def explain_slow_statements(database_url: str, statements, above_seconds: float) -> list[dict]:
    """EXPLAIN (ANALYZE, BUFFERS) every read slower than the threshold; report its sequential scans."""
    import psycopg

    results = []
    seen: set[str] = set()
    with psycopg.connect(database_url) as conn:
        for seconds, query, params in sorted(statements, key=lambda item: -item[0]):
            text = _statement_text(query)
            if seconds < above_seconds or text in seen:
                continue
            seen.add(text)
            if not text.lstrip("( ").upper().startswith(("SELECT", "WITH")):
                continue
            entry = {"ms": _ms(seconds), "statement": text[:400]}
            try:
                with conn.cursor() as cur:
                    cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + (query if isinstance(query, str) else text), params)
                    plan = cur.fetchone()[0][0]
                scans: list[dict] = []
                _seq_scans(plan["Plan"], scans)
                entry["executionMs"] = round(plan.get("Execution Time", 0.0), 1)
                entry["planningMs"] = round(plan.get("Planning Time", 0.0), 1)
                entry["sharedBuffersRead"] = plan["Plan"].get("Shared Read Blocks", 0)
                entry["sharedBuffersHit"] = plan["Plan"].get("Shared Hit Blocks", 0)
                entry["seqScans"] = scans
            except Exception as exc:  # a plan that cannot be taken is reported, not fatal
                entry["explainError"] = str(exc).splitlines()[0][:200]
            finally:
                conn.rollback()
            results.append(entry)
    return results


def _placeholders(database_url: str) -> tuple[dict, dict]:
    import psycopg

    counts: dict[str, int] = {}
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        for table in ("movies", "people", "movie_credits", "movie_genres", "movie_identifiers",
                      "containers", "container_movies", "media_assets", "entity_media", "sync_changes", "users"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM movies WHERE deleted_at IS NULL")
        live = cur.fetchone()[0]
        cur.execute("SELECT id FROM movies WHERE deleted_at IS NULL ORDER BY id LIMIT 1")
        row = cur.fetchone()
        movie = str(row[0]) if row else "missing"
        cur.execute("SELECT em.media_id FROM entity_media em WHERE em.entity_type='movie' AND em.deleted_at IS NULL ORDER BY em.media_id LIMIT 1")
        row = cur.fetchone()
        asset = str(row[0]) if row else "missing"
        cur.execute("SELECT COALESCE(MAX(revision), 0) FROM sync_state")
        revision = cur.fetchone()[0]
        cur.execute("SHOW server_version")
        counts["_postgres"] = cur.fetchone()[0]
    last = max(0, ((live - 1) // HYDRATION_CHUNK) * HYDRATION_CHUNK)
    deep = (live // 2 // HYDRATION_CHUNK) * HYDRATION_CHUNK
    return counts, {"deep": deep, "last": last, "movie": movie, "asset": asset, "revision": revision, "live": live}


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return "unknown"


def measure(*, runs: int, explain: bool, explain_above_ms: float, hydrate: bool, only: str | None = None) -> dict:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("ERROR: DATABASE_URL is not set")
    log = StatementLog()
    log.install()
    import_started = time.perf_counter()
    from app.backend import next_app

    app = next_app.create_app()
    import_seconds = time.perf_counter() - import_started
    client = app.test_client()
    counts, values = _placeholders(database_url)
    postgres_version = counts.pop("_postgres")

    report = {
        "measuredAt": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": _git_commit(),
        "version": (REPO_ROOT / "app" / "VERSION").read_text().strip(),
        "postgres": postgres_version,
        "python": platform.python_version(),
        "machine": f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs",
        "transport": "in-process Flask test client, system owner, no network",
        "rows": counts,
        "liveMovies": values["live"],
        "runs": runs,
        "importAndCreateAppMs": _ms(import_seconds),
        "endpoints": [],
    }

    for label, template in ENDPOINTS:
        if only and only.lower() not in label.lower():
            continue
        path = template.format(**values)
        first = _call(client, log, path)
        samples = [_call(client, log, path) for _ in range(runs)]
        timings = [sample["seconds"] for sample in samples]
        last = samples[-1]
        slowest = max(last["statements"], key=lambda item: item[0], default=None)
        entry = {
            "label": label,
            "path": path,
            "status": last["status"],
            "firstMs": _ms(first["seconds"]),
            "p50Ms": _ms(statistics.median(timings)),
            "p95Ms": _ms(_percentile(timings, 0.95)),
            "bytes": last["bytes"],
            "queries": last["queries"],
            "connections": last["connections"],
            "slowestStatementMs": _ms(slowest[0]) if slowest else 0.0,
            "slowestStatement": _statement_text(slowest[1])[:200] if slowest else "",
        }
        if explain:
            entry["plans"] = explain_slow_statements(database_url, last["statements"], explain_above_ms / 1000)
        report["endpoints"].append(entry)
        print(f"{label:36} {entry['status']}  p50 {entry['p50Ms']:>9.1f} ms  {entry['queries']:>4} queries", file=sys.stderr)

    if hydrate and not only:
        # What library-paging.js does after first paint: walk the whole library by OFFSET.
        offset, pages, total_seconds, total_bytes, total_queries, slowest_page = 0, 0, 0.0, 0, 0, 0.0
        while True:
            page = _call(client, log, f"/api/next/collection/movies?limit={HYDRATION_CHUNK}&offset={offset}")
            payload = json.loads(page["body"]) if page["status"] == 200 else {}
            pages += 1
            total_seconds += page["seconds"]
            total_bytes += page["bytes"]
            total_queries += page["queries"]
            slowest_page = max(slowest_page, page["seconds"])
            items = payload.get("items") or []
            offset += len(items)
            if not items or not payload.get("hasMore"):
                break
        report["hydration"] = {
            "pages": pages,
            "rows": offset,
            "totalMs": _ms(total_seconds),
            "slowestPageMs": _ms(slowest_page),
            "bytes": total_bytes,
            "queries": total_queries,
        }
    return report


def to_markdown(report: dict) -> str:
    rows = report["rows"]
    lines = [
        f"Measured {report['measuredAt']} on DiscVault {report['version']} (`{report['commit']}`), "
        f"PostgreSQL {report['postgres']}, Python {report['python']}, {report['machine']}.",
        "",
        f"Data: {report['liveMovies']} live movies, {rows.get('movie_credits', 0)} credits, {rows.get('people', 0)} people, "
        f"{rows.get('containers', 0)} containers, {rows.get('entity_media', 0)} artwork links, "
        f"{rows.get('sync_changes', 0)} sync changes, {rows.get('users', 0)} users.",
        "",
        f"Transport: {report['transport']}. Timings are server time only; {report['runs']} warm runs per endpoint.",
        "",
        "| Endpoint | Status | First (ms) | p50 (ms) | p95 (ms) | Body | Queries | Connections | Slowest statement (ms) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in report["endpoints"]:
        lines.append(
            f"| {entry['label']} | {entry['status']} | {entry['firstMs']} | {entry['p50Ms']} | {entry['p95Ms']} | "
            f"{_size(entry['bytes'])} | {entry['queries']} | {entry['connections']} | {entry['slowestStatementMs']} |"
        )
    hydration = report.get("hydration")
    if hydration:
        lines += [
            "",
            f"Full library hydration (what the PWA does after first paint): {hydration['pages']} pages of {HYDRATION_CHUNK}, "
            f"{hydration['rows']} rows, {hydration['totalMs'] / 1000:.1f} s of server time, slowest page {hydration['slowestPageMs']} ms, "
            f"{_size(hydration['bytes'])} of JSON, {hydration['queries']} queries.",
        ]
    scans: dict[str, set[str]] = {}
    for entry in report["endpoints"]:
        for plan in entry.get("plans", []):
            for scan in plan.get("seqScans", []):
                if (scan.get("rows") or 0) * (scan.get("loops") or 1) + (scan.get("removedByFilter") or 0) >= 1000:
                    scans.setdefault(entry["label"], set()).add(str(scan["table"]))
    if scans:
        lines += ["", "Sequential scans over 1000 rows or more, in statements above the explain threshold:", ""]
        lines += [f"- {label}: {', '.join(sorted(tables))}" for label, tables in scans.items()]
    return "\n".join(lines) + "\n"


def _size(count: int) -> str:
    if count >= 1024 * 1024:
        return f"{count / (1024 * 1024):.1f} MB"
    if count >= 1024:
        return f"{count / 1024:.0f} kB"
    return f"{count} B"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=5, help="warm calls per endpoint (default 5)")
    parser.add_argument("--explain", action="store_true", help="EXPLAIN (ANALYZE, BUFFERS) the slow statements")
    parser.add_argument("--explain-above-ms", type=float, default=20.0, help="threshold for --explain (default 20)")
    parser.add_argument("--no-hydration", action="store_true", help="skip the full library walk")
    parser.add_argument("--only", help="measure only endpoints whose label contains this text")
    parser.add_argument("--output-dir", help="write perf-report.json and perf-report.md here")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    report = measure(
        runs=args.runs, explain=args.explain, explain_above_ms=args.explain_above_ms,
        hydrate=not args.no_hydration, only=args.only,
    )
    markdown = to_markdown(report)
    if args.output_dir:
        target = Path(args.output_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "perf-report.json").write_text(json.dumps(report, indent=2) + "\n")
        (target / "perf-report.md").write_text(markdown)
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
