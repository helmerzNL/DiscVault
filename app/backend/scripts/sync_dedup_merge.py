#!/usr/bin/env python3
"""One-off duplicate-movie merge for the sync dedup rollout (Deel 3).

Detects duplicate movie groups in the shared catalogue using the same identity
ladder the sync create-path uses, and merges each group onto a single winner:

    barcode  ->  tmdb + format + edition  ->  title + year (within one format)

Design rules (from docs/discvault-sync-server-opdracht.md, Deel 3):

* **Dry-run first.** The default mode performs NO mutations. It prints a JSON
  report of the duplicate groups, counts, and a few worked examples. This report
  must be shared and approved before anyone runs ``--execute``.
* **Winner = most user-data.** The surviving record is the one with the richest
  user-entered data (own artwork/notes/locked fields, watch history, tags, …).
  Losers' relations are re-hung onto the winner.
* **Losers get a tombstone, never a hard delete** — otherwise a client holding a
  stale copy would just push the duplicate straight back. Each loser is
  soft-deleted (``deleted_at = now()``) and a ``delete`` sync_change is emitted so
  connected clients prune it from the delta feed.
* **No client_id backfill.** Winners without a ``client_id`` are left as-is; the
  column fills itself the first time a client adopts the record. Generating a
  server-side UUID here would collide with the real per-record client UUID.

The script is standalone (it never imports the Flask app), so it can be pointed
at a copied/staging database first. It talks to PostgreSQL via ``DATABASE_URL``.

Usage::

    DATABASE_URL=postgres://… python app/scripts/sync_dedup_merge.py            # dry-run
    DATABASE_URL=postgres://… python app/scripts/sync_dedup_merge.py --report out.json
    DATABASE_URL=postgres://… python app/scripts/sync_dedup_merge.py --execute  # after approval
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Title normalization — kept byte-for-byte in step with next_app.normalize_title
# so the merge groups the exact same records the live ladder would match.
# ---------------------------------------------------------------------------

_TITLE_ARTICLES = {"the"}


def normalize_title(title):
    if not title or not str(title).strip():
        return None
    text = str(title).strip()
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.lower()
    folded = re.sub(r"[^a-z0-9]+", " ", folded).strip()
    if not folded:
        return None
    parts = folded.split()
    if len(parts) > 1 and parts[0] in _TITLE_ARTICLES:
        parts = parts[1:]
    return " ".join(parts) or None


def normalize_barcode(barcode):
    if not barcode:
        return None
    digits = re.sub(r"\D", "", str(barcode))
    return digits or None


def normalize_format(value):
    # Unify separators: underscores, hyphens, slashes → spaces
    text = str(value or "").strip().lower().replace("-", " ").replace("_", " ").replace("/", " ")
    # Normalize repeated spaces
    text = " ".join(text.split())
    if not text:
        return ""
    # Check for known formats (order matters: check longer patterns first)
    if "ultra hd" in text or "uhd" in text or "4k" in text:
        return "ultra_hd_blu_ray"
    if "blu ray" in text or "bluray" in text or text == "bd":
        return "blu_ray"
    if text in {"dvd", "dvd video"}:
        return "dvd"
    if "hd dvd" in text or "hddvd" in text:
        return "hd_dvd"
    if "laserdisc" in text or "laser disc" in text:
        return "laserdisc"
    if "svcd" in text or "vcd" in text:
        return "vcd_svcd"
    # Return the normalized text (space-separated, lowercase)
    return text


def _year_key(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _barcode_conflicts(left, right):
    """Detect barcode conflicts: mismatches or null/non-null mismatch.
    
    Returns True if:
    - One is null and the other is not (mixed null/non-null always conflicts)
    - Both are non-null and different
    """
    left_code = normalize_barcode(left)
    right_code = normalize_barcode(right)
    # Mixed null/non-null is always a conflict
    if bool(left_code) != bool(right_code):
        return True
    # Both non-null: conflict if different
    if left_code and right_code and left_code != right_code:
        return True
    return False


def _tmdb_sanity_conflicts(left_movie, right_movie):
    left_title = normalize_title(left_movie.get("title"))
    right_title = normalize_title(right_movie.get("title"))
    if left_title and right_title and left_title != right_title:
        return True
    left_year = _year_key(left_movie.get("year"))
    right_year = _year_key(right_movie.get("year"))
    if left_year and right_year and left_year != right_year:
        return True
    return False


def _members_are_compatible(left_movie, right_movie, *, enforce_tmdb_sanity):
    if _barcode_conflicts(left_movie.get("barcode"), right_movie.get("barcode")):
        return False
    if enforce_tmdb_sanity and _tmdb_sanity_conflicts(left_movie, right_movie):
        return False
    return True


def _split_group_members(members, by_id, *, enforce_tmdb_sanity):
    clusters = []
    for movie_id in members:
        movie = by_id.get(movie_id) or {}
        placed = False
        for cluster in clusters:
            if all(
                _members_are_compatible(movie, by_id.get(existing_id) or {}, enforce_tmdb_sanity=enforce_tmdb_sanity)
                for existing_id in cluster
            ):
                cluster.append(movie_id)
                placed = True
                break
        if not placed:
            clusters.append([movie_id])
    return [cluster for cluster in clusters if len(cluster) > 1]


def _split_group_map(groups, by_id, *, enforce_tmdb_sanity):
    split = {}
    for key, members in groups.items():
        compatible_clusters = _split_group_members(
            members,
            by_id,
            enforce_tmdb_sanity=enforce_tmdb_sanity,
        )
        if len(compatible_clusters) == 1 and len(compatible_clusters[0]) == len(members):
            split[key] = compatible_clusters[0]
            continue
        for idx, cluster in enumerate(compatible_clusters, start=1):
            split[(key, f"split{idx}")] = cluster
    return split


# ---------------------------------------------------------------------------
# Relations to re-hang from a loser onto the winner. Each entry is a table plus
# the column that references movies(id) and the columns that (with movie_id)
# form a uniqueness constraint, so we can drop rows that would collide before
# repointing the rest. SET-NULL references (matched_movie_id, …) are repointed
# to the winner rather than nulled, to preserve the user's link.
# ---------------------------------------------------------------------------

MOVIE_RELATIONS = [
    # table, fk_column, conflict_columns (besides fk_column)
    ("movie_identifiers", "movie_id", ["provider_id", "identifier_type", "identifier"]),
    ("movie_localizations", "movie_id", ["lang"]),
    ("movie_technical_specs", "movie_id", []),
    ("movie_credits", "movie_id", ["person_id", "credit_type", "job", "character"]),
    ("movie_genres", "movie_id", ["genre_key"]),
    ("container_movies", "movie_id", ["container_id"]),
    ("media_group_movies", "movie_id", ["group_id"]),
    ("watchlist_items", "movie_id", ["user_id"]),
    ("watch_history", "movie_id", []),
    ("movie_tags", "movie_id", ["user_id", "tag_id"]),
    ("digital_media_items", "matched_movie_id", []),
    ("loan_requests", "movie_id", []),
]

# Columns on `movies` that indicate user-entered data, used to score the winner.
USER_DATA_COLUMNS = [
    "notes",
    "rating",
    "purchase_date",
    "purchase_price",
    "location",
    "edition",
    "edition_type",
]


def _table_exists(conn, table_name):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"public.{table_name}",))
        row = cur.fetchone() or {}
    return bool(row.get("ok"))


def _column_exists(conn, table_name, column_name):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                  AND column_name = %s
            ) AS ok
            """,
            (table_name, column_name),
        )
        row = cur.fetchone() or {}
    return bool(row.get("ok"))


def _locked_fields(metadata):
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("field_locks")
    if raw is None:
        raw = metadata.get("fieldLocks")
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        return []
    out = sorted({str(value or "").strip() for value in values if str(value or "").strip()})
    return out


def _connect():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is required.", file=sys.stderr)
        raise SystemExit(2)
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:  # pragma: no cover - deployment always ships psycopg
        print("psycopg is required to run the merge script.", file=sys.stderr)
        raise SystemExit(2)
    return psycopg.connect(url, autocommit=False, row_factory=dict_row)


def _target_database_name():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    path = (parsed.path or "").lstrip("/")
    return path or None


def _report_metadata():
    """Extract report metadata from environment variables.
    
    Fallbacks cover local dev (BUILD_VERSION), Docker compose (DISCVAULT_*),
    and CI/CD (BUILD_SHA) environments.
    """
    script_commit = (
        os.environ.get("BUILD_SHA")
        or os.environ.get("DISCVAULT_BUILD_SHA")
        or os.environ.get("GITHUB_SHA")
    )
    backend_version = (
        os.environ.get("DISCVAULT_BACKEND_VERSION")
        or os.environ.get("BUILD_VERSION")
    )
    return {
        "script_commit": script_commit,
        "target_database": _target_database_name(),
        "backend_version": backend_version,
    }


def _fetch_live_movies(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                client_id,
                barcode,
                title,
                year,
                format,
                edition,
                edition_type,
                notes,
                rating,
                purchase_date,
                purchase_price,
                location,
                metadata,
                created_at
            FROM movies
            WHERE deleted_at IS NULL
            """
        )
        return cur.fetchall()


def _fetch_tmdb_ids(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT movie_id, identifier
            FROM movie_identifiers
            WHERE provider_id = 'tmdb'
            """
        )
        rows = cur.fetchall()
    out = {}
    for row in rows:
        out.setdefault(row["movie_id"], row["identifier"])
    return out


def detect_groups(conn):
    """Return duplicate groups keyed by tier, each a list of member movie ids."""
    movies = _fetch_live_movies(conn)
    tmdb = _fetch_tmdb_ids(conn)
    by_id = {m["id"]: m for m in movies}

    barcode_groups = {}
    tmdb_groups = {}
    titleyear_groups = {}

    for m in movies:
        mid = m["id"]
        code = normalize_barcode(m.get("barcode"))
        if code:
            barcode_groups.setdefault(code, []).append(mid)

        fmt = normalize_format(m.get("format"))
        tid = tmdb.get(mid)
        if tid and fmt:
            edition = (m.get("edition") or "").strip().lower()
            tmdb_groups.setdefault((tid, fmt, edition), []).append(mid)

        norm = normalize_title(m.get("title"))
        year = _year_key(m.get("year")) or ""
        if norm and fmt:
            titleyear_groups.setdefault((norm, year, fmt), []).append(mid)

    def _dups(groups):
        return {k: v for k, v in groups.items() if len(v) > 1}

    return {
        "barcode": _dups(barcode_groups),
        "tmdbEdition": _split_group_map(_dups(tmdb_groups), by_id, enforce_tmdb_sanity=True),
        "titleYear": _split_group_map(_dups(titleyear_groups), by_id, enforce_tmdb_sanity=False),
    }, by_id, tmdb


def _fetch_watch_history_counts(conn):
    if not _table_exists(conn, "watch_history"):
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT movie_id, count(*)::int AS n
            FROM watch_history
            WHERE movie_id IS NOT NULL
            GROUP BY movie_id
            """
        )
        rows = cur.fetchall()
    return {str(row["movie_id"]): int(row["n"]) for row in rows}


def _fetch_artwork_rows(conn):
    if not _table_exists(conn, "entity_media") or not _table_exists(conn, "media_assets"):
        return []
    hidden_filter = ""
    if _column_exists(conn, "entity_media", "hidden_at"):
        hidden_filter = "AND em.hidden_at IS NULL"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                em.entity_id AS movie_id,
                ma.provider_id
            FROM entity_media em
            JOIN media_assets ma ON ma.id = em.media_id
            WHERE em.entity_type = 'movie'
              {hidden_filter}
            """
        )
        return cur.fetchall()


def _movie_signals(conn, by_id):
    watch_counts = _fetch_watch_history_counts(conn)
    artwork_rows = _fetch_artwork_rows(conn)
    artwork_by_movie = {}
    for row in artwork_rows:
        movie_id = str(row["movie_id"])
        artwork_by_movie.setdefault(movie_id, []).append(row.get("provider_id"))

    signals = {}
    for mid, movie in by_id.items():
        movie_id = str(mid)
        providers = artwork_by_movie.get(movie_id, [])
        provider_set = sorted({p for p in providers if p})
        has_own_artwork = any((p or "").lower() not in {"tmdb", "themoviedb"} for p in providers)
        if not providers:
            has_own_artwork = False
        locks = _locked_fields(movie.get("metadata"))
        signals[movie_id] = {
            "has_own_artwork": bool(has_own_artwork),
            "artwork_count": len(providers),
            "artwork_provider_ids": provider_set,
            "has_locked_fields": bool(locks),
            "locked_fields": locks,
            "watch_history_count": int(watch_counts.get(movie_id, 0)),
        }
    return signals


def _score_movie(conn, movie_id, by_id):
    """Higher score = keep. Counts user-data columns + related user rows."""
    movie = by_id.get(movie_id, {})
    movie_id = str(movie_id)
    score = 0
    breakdown = {
        "non_empty_movie_fields": {},
        "relation_counts": {},
        "watch_history_count": 0,
        "watch_history_bonus": 0,
        "has_own_artwork": False,
        "has_locked_fields": False,
    }
    for col in USER_DATA_COLUMNS:
        present = movie.get(col) not in (None, "", [])
        breakdown["non_empty_movie_fields"][col] = bool(present)
        if present:
            score += 1
    signals = movie.get("_signals") or {}
    breakdown["watch_history_count"] = int(signals.get("watch_history_count", 0))
    breakdown["watch_history_bonus"] = breakdown["watch_history_count"] * 3
    breakdown["has_own_artwork"] = bool(signals.get("has_own_artwork", False))
    breakdown["has_locked_fields"] = bool(signals.get("has_locked_fields", False))
    # Weight explicit user-preservation signals.
    score += breakdown["watch_history_bonus"]
    if breakdown["has_own_artwork"]:
        score += 3
    if breakdown["has_locked_fields"]:
        score += 3
    with conn.cursor() as cur:
        for table, fk, _conflict in MOVIE_RELATIONS:
            cur.execute(f"SELECT count(*) AS n FROM {table} WHERE {fk} = %s", (movie_id,))
            n = int(cur.fetchone()["n"])
            breakdown["relation_counts"][table] = n
            score += n
    breakdown["total_score"] = score
    return score, breakdown


def _choose_winner(conn, members, by_id):
    """Winner = highest user-data score; ties broken by oldest created_at."""
    scored = []
    reasons = {}
    for mid in members:
        score, breakdown = _score_movie(conn, mid, by_id)
        created = by_id.get(mid, {}).get("created_at") or datetime.max.replace(tzinfo=timezone.utc)
        scored.append((score, created, mid))
        reasons[mid] = breakdown
    # Highest score first, then earliest creation.
    scored.sort(key=lambda t: (-t[0], t[1]))
    winner = scored[0][2]
    losers = [t[2] for t in scored[1:]]
    top_score = scored[0][0]
    tied_top = [mid for s, _c, mid in scored if s == top_score]
    tie_broken_by_oldest = len(tied_top) > 1
    decision_reason = (
        "tie on user-data score; oldest created_at won"
        if tie_broken_by_oldest
        else "highest user-data score won"
    )
    return winner, losers, {mid: s for s, _c, mid in scored}, reasons, decision_reason


def _dedup_group_members(groups):
    """Flatten tiers into a single winner->losers plan, first tier wins.

    A movie can appear in more than one tier (barcode AND titleYear). We process
    tiers in ladder order and skip any movie already claimed by an earlier group
    so each loser is tombstoned exactly once.
    """
    plans = []
    claimed = set()
    for tier in ("barcode", "tmdbEdition", "titleYear"):
        for key, members in groups[tier].items():
            fresh = [m for m in members if m not in claimed]
            if len(fresh) < 2:
                continue
            claimed.update(fresh)
            plans.append({"tier": tier, "key": key, "members": fresh})
    return plans


def build_report(conn):
    groups, by_id, tmdb = detect_groups(conn)
    signals = _movie_signals(conn, by_id)
    for mid, movie in by_id.items():
        movie["_signals"] = signals.get(str(mid), {})
    plans = _dedup_group_members(groups)
    report_groups = []
    total_losers = 0
    for plan in plans:
        winner, losers, scores, reasons, decision_reason = _choose_winner(conn, plan["members"], by_id)
        total_losers += len(losers)
        members = []
        for member_id in plan["members"]:
            movie = by_id.get(member_id, {})
            member_signals = movie.get("_signals") or {}
            members.append(
                {
                    "id": str(member_id),
                    "title": movie.get("title"),
                    "year": movie.get("year"),
                    "format": movie.get("format"),
                    "edition": movie.get("edition"),
                    "barcode": movie.get("barcode"),
                    "client_id": movie.get("client_id"),
                    "tmdb_id": tmdb.get(member_id),
                    "created_at": movie.get("created_at"),
                    "has_own_artwork": bool(member_signals.get("has_own_artwork", False)),
                    "has_locked_fields": bool(member_signals.get("has_locked_fields", False)),
                    "watch_history_count": int(member_signals.get("watch_history_count", 0)),
                    "locked_fields": member_signals.get("locked_fields", []),
                    "artwork_count": int(member_signals.get("artwork_count", 0)),
                    "artwork_provider_ids": member_signals.get("artwork_provider_ids", []),
                    "score_breakdown": reasons.get(member_id, {}),
                }
            )
        report_groups.append(
            {
                "tier": plan["tier"],
                "key": list(plan["key"]) if isinstance(plan["key"], tuple) else plan["key"],
                "winner": str(winner),
                "winner_score": scores.get(winner),
                "winner_reason": decision_reason,
                "losers": [str(m) for m in losers],
                "members": members,
            }
        )
    return {
        **_report_metadata(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "group_counts": {tier: len(groups[tier]) for tier in groups},
        "merge_group_count": len(report_groups),
        "movies_to_tombstone": total_losers,
        "groups": report_groups,
    }


def _next_revision(cur):
    cur.execute(
        """
        UPDATE sync_state SET revision = revision + 1, updated_at = now()
        WHERE id = 'global' RETURNING revision
        """
    )
    row = cur.fetchone()
    return int(row["revision"]) if row else None


def _rehang_relations(cur, winner, loser):
    for table, fk, conflict in MOVIE_RELATIONS:
        if conflict:
            # Drop loser rows that would collide with a winner row on the unique key.
            cols = " AND ".join(f"w.{c} = l.{c}" for c in conflict)
            cur.execute(
                f"""
                DELETE FROM {table} l
                WHERE l.{fk} = %s
                  AND EXISTS (
                      SELECT 1 FROM {table} w
                      WHERE w.{fk} = %s AND {cols}
                  )
                """,
                (loser, winner),
            )
        cur.execute(
            f"UPDATE {table} SET {fk} = %s WHERE {fk} = %s",
            (winner, loser),
        )


def execute_merge(conn, report):
    """Apply the merge plan from ``report`` inside one transaction."""
    tombstoned = 0
    with conn.cursor() as cur:
        for group in report["groups"]:
            winner = group["winner"]
            for loser in group["losers"]:
                _rehang_relations(cur, winner, loser)
                cur.execute(
                    """
                    UPDATE movies
                    SET deleted_at = now(), updated_at = now()
                    WHERE id = %s AND deleted_at IS NULL
                    RETURNING id
                    """,
                    (loser,),
                )
                if cur.fetchone() is None:
                    continue
                tombstoned += 1
                revision = _next_revision(cur)
                if revision is not None:
                    cur.execute(
                        """
                        INSERT INTO sync_changes (revision, entity_type, entity_id, operation, payload)
                        VALUES (%s, 'movie', %s, 'delete', %s)
                        """,
                        (revision, str(loser), json.dumps({"mergedInto": str(winner)})),
                    )
    conn.commit()
    return tombstoned


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the merge. WITHOUT this flag the script only reports (dry-run).",
    )
    parser.add_argument("--report", help="Write the JSON report to this file as well as stdout.")
    args = parser.parse_args(argv)

    conn = _connect()
    try:
        report = build_report(conn)
        text = json.dumps(report, indent=2, default=str)
        print(text)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as fh:
                fh.write(text)

        if not args.execute:
            print(
                f"\n[dry-run] {report['merge_group_count']} group(s), "
                f"{report['movies_to_tombstone']} movie(s) would be tombstoned. "
                "Re-run with --execute after the report is approved.",
                file=sys.stderr,
            )
            return 0

        tombstoned = execute_merge(conn, report)
        print(f"\n[executed] tombstoned {tombstoned} duplicate movie(s).", file=sys.stderr)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
