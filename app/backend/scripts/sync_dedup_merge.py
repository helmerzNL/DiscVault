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
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

if __package__ and __package__ != "scripts":
    from ..dedup_identity import (
        media_type_conflicts,
        select_tmdb_identifier,
        title_year_identity_compatible,
    )
    from ..versioning import backend_version, build_sha
elif __package__ == "scripts":  # pragma: no cover - gunicorn top-level imports
    from dedup_identity import (
        media_type_conflicts,
        select_tmdb_identifier,
        title_year_identity_compatible,
    )
    from versioning import backend_version, build_sha
else:  # pragma: no cover - exercised by the published-image CLI path
    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from dedup_identity import (
        media_type_conflicts,
        select_tmdb_identifier,
        title_year_identity_compatible,
    )
    from versioning import backend_version, build_sha


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
    """Detect hard barcode conflicts.

    Missing data must not block an otherwise sound merge candidate, so only
    two *different non-empty* barcodes are considered incompatible.
    """
    left_code = normalize_barcode(left)
    right_code = normalize_barcode(right)
    return bool(left_code and right_code and left_code != right_code)


def _format_conflicts(left_movie, right_movie):
    """Two different non-empty formats are incompatible physical items.

    A missing format on either side is treated as a wildcard so that a
    format-less duplicate (common after barcode-import without enrichment)
    is still matched and merged into its enriched counterpart.
    """
    left_fmt = normalize_format(left_movie.get("format"))
    right_fmt = normalize_format(right_movie.get("format"))
    return bool(left_fmt and right_fmt and left_fmt != right_fmt)


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


def _members_are_compatible(
    left_movie,
    right_movie,
    *,
    enforce_tmdb_sanity,
    enforce_titleyear_identity,
):
    if _barcode_conflicts(left_movie.get("barcode"), right_movie.get("barcode")):
        return False
    if media_type_conflicts(left_movie.get("media_type"), right_movie.get("media_type")):
        return False
    if _format_conflicts(left_movie, right_movie):
        return False
    if enforce_tmdb_sanity and _tmdb_sanity_conflicts(left_movie, right_movie):
        return False
    if enforce_titleyear_identity and not title_year_identity_compatible(
        left_edition=left_movie.get("edition"),
        right_edition=right_movie.get("edition"),
        left_container_ids=left_movie.get("container_ids"),
        right_container_ids=right_movie.get("container_ids"),
    ):
        return False
    return True


def _split_group_members(
    members,
    by_id,
    *,
    enforce_tmdb_sanity,
    enforce_titleyear_identity,
):
    clusters = []
    for movie_id in members:
        movie = by_id.get(movie_id) or {}
        placed = False
        for cluster in clusters:
            if all(
                _members_are_compatible(
                    movie,
                    by_id.get(existing_id) or {},
                    enforce_tmdb_sanity=enforce_tmdb_sanity,
                    enforce_titleyear_identity=enforce_titleyear_identity,
                )
                for existing_id in cluster
            ):
                cluster.append(movie_id)
                placed = True
                break
        if not placed:
            clusters.append([movie_id])
    return [cluster for cluster in clusters if len(cluster) > 1]


def _split_group_map(
    groups,
    by_id,
    *,
    enforce_tmdb_sanity,
    enforce_titleyear_identity=False,
):
    split = {}
    for key, members in groups.items():
        compatible_clusters = _split_group_members(
            members,
            by_id,
            enforce_tmdb_sanity=enforce_tmdb_sanity,
            enforce_titleyear_identity=enforce_titleyear_identity,
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

COLLECTION_LOCK_KEY = 293225158992
COLLECTION_STATE_TABLES = [
    "movies",
    *(table for table, _fk, _conflict in MOVIE_RELATIONS),
    "entity_media",
    "media_assets",
]

# ---------------------------------------------------------------------------
# Container (box-set) dedup — parallel ladder for the `containers` table.
# ---------------------------------------------------------------------------

CONTAINER_RELATIONS = [
    # table, fk_column, conflict_columns
    ("container_identifiers", "container_id", ["provider_id", "identifier_type", "identifier"]),
    ("container_movies", "container_id", ["movie_id"]),
    ("collection_items", "collection_id", ["item_type", "item_id"]),
]

CONTAINER_STATE_TABLES = [
    "containers",
    *(table for table, _fk, _conflict in CONTAINER_RELATIONS),
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


def _fetch_live_containers(conn, *, container_types=None):
    """Live containers, optionally scoped to a set of container_type values.

    Previously hard-scoped to ``container_type = 'box_set'``, which made
    vault/collection duplicates invisible to this whole script -- the
    downstream scoring/rehang/winner functions were always type-agnostic, so
    that one WHERE clause was the only thing hiding them. ``container_types``
    lets an operator narrow a dry-run/execute pass (see --container-type);
    omitted or empty means all three types.
    """
    clauses = ["c.deleted_at IS NULL"]
    params: list = []
    if container_types:
        placeholders = ", ".join(["%s"] * len(container_types))
        clauses.append(f"c.container_type IN ({placeholders})")
        params.extend(container_types)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.*,
                   (SELECT count(*)::int FROM container_movies cm
                    WHERE cm.container_id = c.id) AS movie_count
            FROM containers c
            WHERE {' AND '.join(clauses)}
            ORDER BY c.id
            """,
            tuple(params),
        )
        return cur.fetchall()


def detect_container_groups(conn, *, container_types=None):
    """Return duplicate container groups (box_set/vault/collection) keyed by tier."""
    containers = _fetch_live_containers(conn, container_types=container_types)
    by_id = {c["id"]: c for c in containers}

    barcode_groups: dict = {}
    titletype_groups: dict = {}

    for c in containers:
        cid = c["id"]
        code = normalize_barcode(c.get("barcode"))
        if code:
            barcode_groups.setdefault(code, []).append(cid)

        # Containers have no year concept the way movies do, and a vault
        # named "Kids" must never group with a collection named "Kids" --
        # so this keys on container_type where the movie ladder keys on
        # year. The dict key below stays "titleYear" for compatibility with
        # the rest of this script's report shape and CLI plumbing.
        norm = normalize_title(c.get("title"))
        container_type = c.get("container_type") or ""
        if norm:
            titletype_groups.setdefault((norm, container_type), []).append(cid)

    def _dups(groups):
        return {k: v for k, v in groups.items() if len(v) > 1}

    return {
        "barcode": _dups(barcode_groups),
        "titleYear": _dups(titletype_groups),
    }, by_id


def _score_container(conn, container_id, by_id):
    container = by_id.get(container_id, {})
    score = int(container.get("movie_count") or 0) * 2
    for col in ("notes", "description", "badge_label"):
        if container.get(col):
            score += 1
    with conn.cursor() as cur:
        for table, fk, _ in CONTAINER_RELATIONS:
            if not _table_exists(conn, table):
                continue
            cur.execute(f"SELECT count(*) AS n FROM {table} WHERE {fk} = %s", (str(container_id),))
            score += int((cur.fetchone() or {}).get("n") or 0)
    return score


def _choose_container_winner(conn, members, by_id):
    scored = []
    for cid in members:
        score = _score_container(conn, cid, by_id)
        created = by_id.get(cid, {}).get("created_at") or datetime.max.replace(tzinfo=timezone.utc)
        scored.append((score, created, cid))
    scored.sort(key=lambda t: (-t[0], t[1], str(t[2])))
    winner = scored[0][2]
    losers = [t[2] for t in scored[1:]]
    return winner, losers


def _rehang_container_relations(cur, winner, loser):
    for table, fk, conflict in CONTAINER_RELATIONS:
        if conflict:
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
                (str(loser), str(winner)),
            )
        cur.execute(
            f"UPDATE {table} SET {fk} = %s WHERE {fk} = %s",
            (str(winner), str(loser)),
        )
    cur.execute(
        "UPDATE entity_media SET entity_id = %s WHERE entity_type = 'container' AND entity_id = %s",
        (str(winner), str(loser)),
    )


def lock_collection_snapshot(conn):
    """Block collection writes while an Admin execution revalidates and merges."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (COLLECTION_LOCK_KEY,))
    tables = [
        table_name
        for table_name in COLLECTION_STATE_TABLES
        if _table_exists(conn, table_name)
    ]
    with conn.cursor() as cur:
        cur.execute(f"LOCK TABLE {', '.join(tables)} IN SHARE ROW EXCLUSIVE MODE")


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


def _movie_state_hash(movie):
    movie_snapshot = movie.get("canonical_state")
    if not isinstance(movie_snapshot, dict):
        movie_snapshot = {
            key: value
            for key, value in movie.items()
            if key not in {"_signals", "canonical_state", "container_ids"}
        }
    snapshot = {
        "movie": movie_snapshot,
        "container_ids": sorted(
            str(value) for value in movie.get("container_ids") or ()
        ),
    }
    serialized = json.dumps(
        snapshot,
        default=str,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


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
    """Return immutable image provenance with source-checkout fallbacks."""
    return {
        "script_commit": build_sha(),
        "target_database": _target_database_name(),
        "backend_version": backend_version(),
    }


def _fetch_live_movies(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT movies.*,
                   to_jsonb(movies) AS canonical_state,
                   ARRAY(
                       SELECT cm.container_id::text
                       FROM container_movies cm
                       WHERE cm.movie_id = movies.id
                       ORDER BY cm.container_id
                   ) AS container_ids
            FROM movies
            WHERE deleted_at IS NULL
            ORDER BY id
            """
        )
        return cur.fetchall()


def _fetch_tmdb_ids(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT movie_id, provider_id, identifier_type, identifier
            FROM movie_identifiers
            WHERE lower(provider_id) = 'tmdb'
            ORDER BY movie_id, provider_id, identifier_type, identifier
            """
        )
        rows = cur.fetchall()
    by_movie = {}
    for row in rows:
        by_movie.setdefault(row["movie_id"], []).append(row)
    out = {}
    for movie_id, identifiers in by_movie.items():
        selected = select_tmdb_identifier(identifiers)
        if selected is not None:
            out[movie_id] = selected
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
        if tid:
            edition = (m.get("edition") or "").strip().lower()
            tmdb_groups.setdefault((tid, edition), []).append(mid)

        norm = normalize_title(m.get("title"))
        year = _year_key(m.get("year")) or ""
        if norm:
            titleyear_groups.setdefault((norm, year), []).append(mid)

    def _dups(groups):
        return {k: v for k, v in groups.items() if len(v) > 1}

    return {
        # Barcode groups now go through the splitter too. They used to be
        # returned raw, which meant _members_are_compatible never ran on them --
        # so the media-type veto would not have reached this, the fourth of the
        # contract's four ladder moments (§2.1), and a mixed film/series group
        # sharing a box EAN would still have been offered up for merging.
        #
        # enforce_tmdb_sanity stays False here deliberately. Turning it on would
        # also switch on the contract's tier-2 tmdb/year veto, which this server
        # does not implement yet; that is a separate defect with its own fixture
        # cases, and folding it in silently would make a merge regression
        # unattributable.
        "barcode": _split_group_map(
            _dups(barcode_groups),
            by_id,
            enforce_tmdb_sanity=False,
        ),
        "tmdbEdition": _split_group_map(
            _dups(tmdb_groups),
            by_id,
            enforce_tmdb_sanity=True,
        ),
        "titleYear": _split_group_map(
            _dups(titleyear_groups),
            by_id,
            enforce_tmdb_sanity=False,
            enforce_titleyear_identity=True,
        ),
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
    scored.sort(key=lambda t: (-t[0], t[1], str(t[2])))
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


def build_report(conn, *, container_types=None):
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
                    "container_ids": sorted(
                        str(value) for value in movie.get("container_ids") or ()
                    ),
                    "state_hash": _movie_state_hash(movie),
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

    # Container (box-set) dedup
    container_groups, container_by_id = detect_container_groups(conn, container_types=container_types)
    container_plans = []
    container_claimed: set = set()
    for tier in ("barcode", "titleYear"):
        for key, members in container_groups[tier].items():
            fresh = [c for c in members if c not in container_claimed]
            if len(fresh) < 2:
                continue
            container_claimed.update(fresh)
            container_plans.append({"tier": tier, "key": key, "members": fresh})

    container_report_groups = []
    total_container_losers = 0
    for plan in container_plans:
        winner, losers = _choose_container_winner(conn, plan["members"], container_by_id)
        total_container_losers += len(losers)
        members = []
        for cid in plan["members"]:
            c = container_by_id.get(cid, {})
            members.append(
                {
                    "id": str(cid),
                    "title": c.get("title"),
                    "year": c.get("year"),
                    "barcode": c.get("barcode"),
                    "movie_count": int(c.get("movie_count") or 0),
                    "created_at": c.get("created_at"),
                }
            )
        container_report_groups.append(
            {
                "tier": plan["tier"],
                "key": list(plan["key"]) if isinstance(plan["key"], tuple) else plan["key"],
                "winner": str(winner),
                "losers": [str(c) for c in losers],
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
        "container_group_count": len(container_report_groups),
        "containers_to_tombstone": total_container_losers,
        "container_groups": container_report_groups,
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


def execute_container_merge(conn, report, *, cur):
    """Tombstone duplicate containers and emit sync_change entries. Uses the caller's cursor."""
    tombstoned = 0
    for group in report.get("container_groups", []):
        winner = group["winner"]
        for loser in group["losers"]:
            _rehang_container_relations(cur, winner, loser)
            cur.execute(
                """
                UPDATE containers
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
                    VALUES (%s, 'container', %s, 'delete', %s)
                    """,
                    (revision, str(loser), json.dumps({"mergedInto": str(winner)})),
                )
    return tombstoned


def execute_merge(conn, report, *, commit=True):
    """Apply the merge plan from ``report`` inside one transaction."""
    tombstoned = 0
    container_tombstoned = 0
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
        container_tombstoned = execute_container_merge(conn, report, cur=cur)
    if commit:
        conn.commit()
    return tombstoned, container_tombstoned


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the merge. WITHOUT this flag the script only reports (dry-run).",
    )
    parser.add_argument("--report", help="Write the JSON report to this file as well as stdout.")
    parser.add_argument(
        "--container-type",
        action="append",
        choices=["box_set", "vault", "collection"],
        dest="container_types",
        help=(
            "Scope container dedup to this container_type. Repeatable "
            "(e.g. --container-type vault --container-type collection). "
            "Omit to cover all three types (the default since vault/"
            "collection support was added)."
        ),
    )
    args = parser.parse_args(argv)

    conn = _connect()
    try:
        report = build_report(conn, container_types=args.container_types)
        text = json.dumps(report, indent=2, default=str)
        print(text)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as fh:
                fh.write(text)

        if not args.execute:
            print(
                f"\n[dry-run] {report['merge_group_count']} movie group(s), "
                f"{report['movies_to_tombstone']} movie(s) would be tombstoned; "
                f"{report.get('container_group_count', 0)} container group(s), "
                f"{report.get('containers_to_tombstone', 0)} container(s) would be tombstoned. "
                "Re-run with --execute after the report is approved.",
                file=sys.stderr,
            )
            return 0

        tombstoned, container_tombstoned = execute_merge(conn, report)
        print(
            f"\n[executed] tombstoned {tombstoned} duplicate movie(s), "
            f"{container_tombstoned} duplicate container(s).",
            file=sys.stderr,
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
