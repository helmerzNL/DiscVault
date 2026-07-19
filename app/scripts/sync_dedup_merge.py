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


# ---------------------------------------------------------------------------
# Title normalization — kept byte-for-byte in step with next_app.normalize_title
# so the merge groups the exact same records the live ladder would match.
# ---------------------------------------------------------------------------

_TITLE_ARTICLES = {"the", "a", "an", "de", "het", "een", "le", "la", "les", "el", "los", "las"}


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


def _fetch_live_movies(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, barcode, title, year, format, edition, created_at
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

        fmt = (m.get("format") or "").strip().lower()
        tid = tmdb.get(mid)
        if tid and fmt:
            edition = (m.get("edition") or "").strip().lower()
            tmdb_groups.setdefault((tid, fmt, edition), []).append(mid)

        norm = normalize_title(m.get("title"))
        year = (str(m.get("year")).strip() if m.get("year") is not None else "")
        if norm and year and fmt:
            titleyear_groups.setdefault((norm, year, fmt), []).append(mid)

    def _dups(groups):
        return {k: v for k, v in groups.items() if len(v) > 1}

    return {
        "barcode": _dups(barcode_groups),
        "tmdbEdition": _dups(tmdb_groups),
        "titleYear": _dups(titleyear_groups),
    }, by_id


def _score_movie(conn, movie_id, by_id):
    """Higher score = keep. Counts user-data columns + related user rows."""
    movie = by_id.get(movie_id, {})
    score = 0
    for col in USER_DATA_COLUMNS:
        if movie.get(col) not in (None, "", []):
            score += 1
    with conn.cursor() as cur:
        for table, fk, _conflict in MOVIE_RELATIONS:
            cur.execute(f"SELECT count(*) AS n FROM {table} WHERE {fk} = %s", (movie_id,))
            score += int(cur.fetchone()["n"])
    return score


def _choose_winner(conn, members, by_id):
    """Winner = highest user-data score; ties broken by oldest created_at."""
    scored = []
    for mid in members:
        score = _score_movie(conn, mid, by_id)
        created = by_id.get(mid, {}).get("created_at") or datetime.max.replace(tzinfo=timezone.utc)
        scored.append((score, created, mid))
    # Highest score first, then earliest creation.
    scored.sort(key=lambda t: (-t[0], t[1]))
    winner = scored[0][2]
    losers = [t[2] for t in scored[1:]]
    return winner, losers, {mid: s for s, _c, mid in scored}


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
    groups, by_id = detect_groups(conn)
    plans = _dedup_group_members(groups)
    report_groups = []
    total_losers = 0
    for plan in plans:
        winner, losers, scores = _choose_winner(conn, plan["members"], by_id)
        total_losers += len(losers)
        report_groups.append(
            {
                "tier": plan["tier"],
                "key": list(plan["key"]) if isinstance(plan["key"], tuple) else plan["key"],
                "winner": str(winner),
                "winner_score": scores.get(winner),
                "losers": [str(m) for m in losers],
                "member_titles": {
                    str(m): by_id.get(m, {}).get("title") for m in plan["members"]
                },
            }
        )
    return {
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
