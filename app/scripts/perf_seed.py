#!/usr/bin/env python3
"""Fill an EMPTY DiscVault Next database with a fixed library for measuring.

This is the data half of the measurement harness (D-0 of the performance
roadmap in App-Guidance, `shared/plans/2026-11-11-discvault-performance-roadmap.md`).
`perf_measure.py` is the other half. A number without a data size is not a
number, so the size is an argument and the content is a pure function of
`--movies` and `--seed`: two runs with the same arguments produce the same rows,
ids included.

What it writes, for `--movies N`:

  locations          45        5 rooms, 8 shelves each
  people             1.6 * N   unique names
  movies             N         ~80% distinct works; the rest are a second
                               edition of an existing work (same title and
                               year, another format) -- the tie groups that
                               make OFFSET paging hard (library-hydration-paging §4)
  movie_credits      ~18 * N   8 to 22 cast, a director, two writers, a producer
  movie_genres       1 to 3 per movie
  movie_identifiers  tmdb for ~85%, imdb for ~70%
  media_assets       a poster for ~90%, a backdrop for ~30%
  entity_media       one row per asset
  containers         N/100 box sets (3 to 8 discs), N/2500 collections (40)

What it does NOT write, and the measurement therefore does not cover: users
(every request runs as the built-in system owner, so the group-visibility
predicate is never exercised), series and episodes, personal lists, custom
fields, and the sync stream (`republish_sync_stream.py` builds that from these
rows with the app's own code).

Artwork URLs point at `posters.invalid`, a reserved name that never resolves:
nothing this script writes can make a browser or the server call a real host.

Safety: it refuses a database that already holds a movie. This is a tool for a
throwaway database, never for an install with a library in it.

    DATABASE_URL=postgresql://... python app/backend/next_database.py migrate
    DATABASE_URL=postgresql://... python app/scripts/perf_seed.py --movies 50000
"""

from __future__ import annotations

import argparse
import os
import sys
import time

DEFAULT_MOVIES = 50_000
DEFAULT_SEED = "discvault-perf-1"
PUBLIC_ID_PREFIX = "perf-"

FIRST_NAMES = (
    "Anna Bram Clara Daan Eva Finn Greta Hugo Iris Jan Karin Lars Mila Noah Olga Pim Quinn Rosa Sam Tess "
    "Umar Vera Wout Xena Yara Zeb Alba Boris Cato Dirk Elin Floor Gijs Hanna Ivo Jet Koen Lotte Mats Nina "
    "Otto Puck Ruben Sanne Teun Ula Victor Wende Yusuf Zoe Abel Bente Cas Dewi Emil Fenna Guus Hilde Isa Joep "
    "Kiki Luuk Maud Niek Oda Pien Rik Sofie Thijs Uma Vince Willa Xavi Ymke Zara Aron Bibi Coen Doris Esra "
    "Freek Gwen Hein Imke Jorn Kaat Levi Merel Nout Okke Pleun Roel Stijn Tara Ugo Vik Wies Yoran Zita Ada"
).split()
LAST_A = "Van Ber Kor Mel Ros Sten Vel Wes Hol Dijk".split()
LAST_B = "der en ing an el aar um ou ie o".split()
LAST_C = "man berg stra veld hof sma dam".split()
LAST_D = ("", "s")

TITLE_A = (
    "Silent Broken Golden Last Hidden Crimson Midnight Lost Savage Eternal Frozen Burning Distant Wild Iron "
    "Paper Glass Hollow Electric Velvet Scarlet Pale Bitter Northern Southern Secret Final Second Blind Quiet "
    "Black White Blue Red Green Dark Bright Cold Warm Empty Endless Fallen Rising Sleeping Running Dancing "
    "Stolen Forgotten Sacred Wicked Gentle Lonely Brave Royal Rusty Hungry Lucky Crooked"
).split()
TITLE_B = (
    "River Mountain City Garden Empire Station Harbor Island Kingdom Shadow Mirror Window Bridge Tower Forest "
    "Desert Ocean Storm Winter Summer Morning Night Voyage Promise Memory Letter Stranger Soldier Detective "
    "Daughter Brother Sister Father Mother Witness Passenger Neighbor Teacher Doctor Hunter Dancer Singer "
    "Painter Sailor Pilot Driver Thief Queen King Prince Angel Ghost Wolf Tiger Raven Falcon Dragon Lion "
    "Circus Carnival Hotel Motel Cinema Theater Library Museum Factory Railway Highway Avenue Street Alley "
    "Valley Canyon Meadow Orchard Vineyard Lighthouse Cathedral Castle Palace Cottage Cabin Mansion Apartment "
    "Frontier Border Horizon Eclipse Comet Planet Galaxy Machine Engine Signal Code Cipher Ledger Contract "
    "Verdict Trial Heist Escape Chase Return Reunion Wedding Funeral Holiday Weekend Affair Scandal Rumor "
    "Legend Myth Fable Ballad Symphony Sonata Waltz Tango Rhapsody Requiem"
).split()
OVERVIEW_WORDS = (
    "a the when after before while young old woman man family town war love secret past future journey must "
    "find lose escape return discover betray protect follow leave remember forget against between across "
    "small quiet violent strange ordinary final first last night day year summer winter and but until because"
).split()
STUDIOS = (
    "Northlight Pictures", "Harbor Street Films", "Blue Canal Studios", "Windmill Entertainment",
    "Atlas Film Company", "Paper Moon Productions", "Red Brick Cinema", "Polder Pictures",
    "Lantern Film Group", "Seven Bridges Media", "Old Quay Films", "Silver Dune Studios",
)
GENRES = (
    "action adventure animation comedy crime documentary drama family fantasy history horror music mystery "
    "romance science_fiction tv_movie thriller war western"
).split()


class SeedRefused(RuntimeError):
    """The target database is not one this script may fill."""


def _sql_array(values) -> str:
    quoted = ",".join("'" + str(value).replace("'", "''") + "'" for value in values)
    return f"ARRAY[{quoted}]::text[]"


def seed_statements(movies: int, people: int) -> list[tuple[str, str]]:
    """The seed as (label, SQL) steps. `pg_temp.h(tag, n)` is the only source of variety."""
    works = max(1, int(movies * 0.8))
    box_sets = max(1, movies // 100)
    collections = max(1, movies // 2500)
    title_a, title_b = len(TITLE_A), len(TITLE_B)
    first = len(FIRST_NAMES)
    return [
        (
            "locations",
            f"""
            INSERT INTO locations (id, public_id, parent_id, name, qr_token, sort_order)
            SELECT pg_temp.uid('loc', n), '{PUBLIC_ID_PREFIX}loc-' || n,
                   CASE WHEN n <= 5 THEN NULL ELSE pg_temp.uid('loc', ((n - 6) / 8) + 1) END,
                   CASE WHEN n <= 5 THEN 'Room ' || n ELSE 'Shelf ' || (((n - 6) % 8) + 1) END,
                   '{PUBLIC_ID_PREFIX}qr-' || n, n
            FROM generate_series(1, 45) AS n
            """,
        ),
        (
            "people",
            f"""
            INSERT INTO people (id, public_id, name, known_for)
            SELECT pg_temp.uid('person', n), '{PUBLIC_ID_PREFIX}p-' || n,
                   ({_sql_array(FIRST_NAMES)})[(n % {first}) + 1] || ' ' ||
                   ({_sql_array(LAST_A)})[((n / {first}) % 10) + 1] ||
                   ({_sql_array(LAST_B)})[((n / {first * 10}) % 10) + 1] ||
                   ({_sql_array(LAST_C)})[((n / {first * 100}) % 7) + 1] ||
                   ({_sql_array(LAST_D)})[((n / {first * 700}) % 2) + 1] ||
                   CASE WHEN n >= {first * 1400} THEN ' ' || (n / {first * 1400} + 1) ELSE '' END,
                   CASE WHEN n % 4 = 0 THEN 'Directing' ELSE 'Acting' END
            FROM generate_series(0, {people - 1}) AS n
            """,
        ),
        (
            "movies",
            f"""
            WITH numbered AS (
                SELECT n,
                       -- The last 20% are another edition of an earlier work.
                       CASE WHEN n <= {works} THEN n ELSE (pg_temp.h('edition', n) % {works}) + 1 END AS w
                FROM generate_series(1, {movies}) AS n
            ), titled AS (
                SELECT n, w,
                       ({_sql_array(TITLE_A)})[(pg_temp.h('ta', w) % {title_a}) + 1] || ' ' ||
                       ({_sql_array(TITLE_B)})[(pg_temp.h('tb', w) % {title_b}) + 1] ||
                       CASE WHEN pg_temp.h('seq', w) % 5 = 0
                            THEN ' ' || ((pg_temp.h('seqn', w) % 5) + 2) ELSE '' END AS base,
                       pg_temp.h('the', w) % 4 = 0 AS has_article
                FROM numbered
            )
            INSERT INTO movies (
                id, public_id, barcode, title, sort_title, original_title, year, release_date, format, edition,
                country, language, original_language, runtime_minutes, overview, rating, rating_votes,
                purchase_date, purchase_price, location_id, metadata, created_at, updated_at
            )
            SELECT pg_temp.uid('movie', n), '{PUBLIC_ID_PREFIX}m-' || n,
                   CASE WHEN n % 10 = 0 THEN NULL ELSE '87' || lpad(n::text, 11, '0') END,
                   CASE WHEN has_article THEN 'The ' || base ELSE base END,
                   base,
                   CASE WHEN pg_temp.h('orig', w) % 6 = 0 THEN 'De ' || base ELSE NULL END,
                   (1930 + pg_temp.h('year', w) % 96)::text,
                   make_date(1930 + pg_temp.h('year', w) % 96, (pg_temp.h('mon', w) % 12) + 1, (pg_temp.h('day', w) % 28) + 1),
                   (ARRAY['DVD','DVD','DVD','DVD','DVD','DVD','DVD','DVD','DVD','Blu-ray','Blu-ray','Blu-ray',
                          'Blu-ray','Blu-ray','Blu-ray','Blu-ray','4K UHD','4K UHD','4K UHD','VHS'])[(pg_temp.h('fmt', n) % 20) + 1],
                   CASE pg_temp.h('ed', n) % 12 WHEN 0 THEN 'Steelbook' WHEN 1 THEN 'Collector''s Edition'
                        WHEN 2 THEN 'Director''s Cut' ELSE NULL END,
                   (ARRAY['NL','US','GB','DE','FR'])[(pg_temp.h('ctry', n) % 5) + 1],
                   (ARRAY['nl','en','en','de','fr'])[(pg_temp.h('lang', n) % 5) + 1],
                   (ARRAY['en','en','en','fr','de','it','ja','nl','es','sv'])[(pg_temp.h('olang', w) % 10) + 1],
                   70 + pg_temp.h('run', w) % 110,
                   (SELECT string_agg(({_sql_array(OVERVIEW_WORDS)})[(pg_temp.h('ov' || k, w) % {len(OVERVIEW_WORDS)}) + 1], ' ' ORDER BY k)
                    FROM generate_series(1, 30 + pg_temp.h('ovl', w) % 40) AS k),
                   round((3 + (pg_temp.h('rate', w) % 60) / 10.0)::numeric, 1)::text,
                   pg_temp.h('votes', w) % 200000,
                   CASE WHEN n % 5 < 3 THEN make_date(2005 + pg_temp.h('py', n) % 21, (pg_temp.h('pm', n) % 12) + 1, 1) END,
                   CASE WHEN n % 5 < 3 THEN round((3 + (pg_temp.h('price', n) % 3000) / 100.0)::numeric, 2) END,
                   CASE WHEN n % 10 < 7 THEN pg_temp.uid('loc', 6 + pg_temp.h('loc', n) % 40) END,
                   jsonb_build_object(
                       'studios', ({_sql_array(STUDIOS)})[(pg_temp.h('studio', w) % {len(STUDIOS)}) + 1],
                       'audience_rating', (ARRAY['AL','6','9','12','16','18'])[(pg_temp.h('aud', w) % 6) + 1]
                   ),
                   timestamptz '2016-01-01 00:00:00+00' + make_interval(secs => (pg_temp.h('created', n) % 315360000)),
                   timestamptz '2026-01-01 00:00:00+00' + make_interval(secs => (pg_temp.h('updated', n) % 25920000))
            FROM titled
            """,
        ),
        (
            "movie_credits",
            f"""
            WITH slots AS (
                SELECT n, k
                FROM generate_series(1, {movies}) AS n
                CROSS JOIN LATERAL generate_series(1, 12 + pg_temp.h('cast', n) % 15) AS k
            ), picked AS (
                -- DISTINCT ON keeps a person from holding the same slot kind twice in one film.
                SELECT DISTINCT ON (n, kind, person)
                       n, k, kind, person
                FROM (
                    SELECT n, k,
                           CASE WHEN k = 1 THEN 'director' WHEN k <= 3 THEN 'writer' WHEN k = 4 THEN 'producer' ELSE 'cast' END AS kind,
                           -- Square the draw so a few people appear in very many films, like real filmographies.
                           ((pg_temp.h('who' || k, n) % 1000)::bigint * (pg_temp.h('who2' || k, n) % 1000) * {people} / 1000000)::int AS person
                    FROM slots
                ) drawn
                ORDER BY n, kind, person, k
            )
            INSERT INTO movie_credits (id, movie_id, person_id, credit_type, character, job, sort_order)
            SELECT pg_temp.uid('credit', n::bigint * 100 + k), pg_temp.uid('movie', n), pg_temp.uid('person', person),
                   CASE WHEN kind = 'cast' THEN 'cast' ELSE 'crew' END,
                   CASE WHEN kind = 'cast' THEN 'Character ' || k END,
                   CASE kind WHEN 'director' THEN 'Director' WHEN 'writer' THEN 'Writer' WHEN 'producer' THEN 'Producer' END,
                   k
            FROM picked
            """,
        ),
        (
            # The library reads director/actor/writer/producer from movies.metadata, so they
            # have to agree with the credits above the way a metadata refresh leaves them.
            "movies.metadata credits",
            """
            UPDATE movies m
            SET metadata = m.metadata || c.names
            FROM (
                SELECT mc.movie_id,
                       jsonb_strip_nulls(jsonb_build_object(
                           'director', string_agg(p.name, ', ' ORDER BY mc.sort_order) FILTER (WHERE mc.job = 'Director'),
                           'writer', string_agg(p.name, ', ' ORDER BY mc.sort_order) FILTER (WHERE mc.job = 'Writer'),
                           'producer', string_agg(p.name, ', ' ORDER BY mc.sort_order) FILTER (WHERE mc.job = 'Producer'),
                           'actor', string_agg(p.name, ', ' ORDER BY mc.sort_order) FILTER (WHERE mc.credit_type = 'cast' AND mc.sort_order <= 9)
                       )) AS names
                FROM movie_credits mc JOIN people p ON p.id = mc.person_id
                GROUP BY mc.movie_id
            ) c
            WHERE c.movie_id = m.id
            """,
        ),
        (
            "movie_genres",
            f"""
            INSERT INTO movie_genres (movie_id, genre_key)
            SELECT DISTINCT pg_temp.uid('movie', n),
                   ({_sql_array(GENRES)})[(pg_temp.h('genre' || k, n) % {len(GENRES)}) + 1]
            FROM generate_series(1, {movies}) AS n
            CROSS JOIN LATERAL generate_series(1, 1 + pg_temp.h('genres', n) % 3) AS k
            """,
        ),
        (
            "movie_identifiers",
            f"""
            INSERT INTO movie_identifiers (movie_id, provider_id, identifier, identifier_type)
            SELECT pg_temp.uid('movie', n), 'tmdb', (100000 + n)::text, 'movie_id'
            FROM generate_series(1, {movies}) AS n WHERE pg_temp.h('tmdb', n) % 100 < 85
            UNION ALL
            SELECT pg_temp.uid('movie', n), 'imdb', 'tt' || lpad(n::text, 7, '0'), 'movie_id'
            FROM generate_series(1, {movies}) AS n WHERE pg_temp.h('imdb', n) % 100 < 70
            """,
        ),
        (
            "media_assets",
            f"""
            INSERT INTO media_assets (id, kind, variant, storage_backend, storage_key, source_url, provider_id,
                                      content_type, width, height, sha256)
            SELECT pg_temp.uid('poster', n), 'poster', 'original', 'remote', '{PUBLIC_ID_PREFIX}poster-' || n,
                   'https://posters.invalid/p/' || n || '.jpg', 'tmdb', 'image/jpeg', 2000, 3000, md5('poster' || n)
            FROM generate_series(1, {movies}) AS n WHERE pg_temp.h('poster', n) % 100 < 90
            UNION ALL
            SELECT pg_temp.uid('backdrop', n), 'backdrop', 'original', 'remote', '{PUBLIC_ID_PREFIX}backdrop-' || n,
                   'https://posters.invalid/b/' || n || '.jpg', 'tmdb', 'image/jpeg', 3840, 2160, md5('backdrop' || n)
            FROM generate_series(1, {movies}) AS n WHERE pg_temp.h('backdrop', n) % 100 < 30
            """,
        ),
        (
            "entity_media",
            f"""
            INSERT INTO entity_media (entity_type, entity_id, media_id, role, is_primary)
            SELECT 'movie', pg_temp.uid('movie', n), pg_temp.uid('poster', n), 'primary', true
            FROM generate_series(1, {movies}) AS n WHERE pg_temp.h('poster', n) % 100 < 90
            UNION ALL
            SELECT 'movie', pg_temp.uid('movie', n), pg_temp.uid('backdrop', n), 'backdrop', false
            FROM generate_series(1, {movies}) AS n WHERE pg_temp.h('backdrop', n) % 100 < 30
            """,
        ),
        (
            "containers",
            f"""
            INSERT INTO containers (id, public_id, container_type, title, year)
            SELECT pg_temp.uid('container', c), '{PUBLIC_ID_PREFIX}c-' || c,
                   CASE WHEN c <= {box_sets} THEN 'box_set' ELSE 'collection' END,
                   CASE WHEN c <= {box_sets} THEN 'Box Set ' ELSE 'Collection ' END ||
                   ({_sql_array(TITLE_B)})[(pg_temp.h('ct', c) % {title_b}) + 1] || ' ' || c,
                   (1990 + pg_temp.h('cy', c) % 36)::text
            FROM generate_series(1, {box_sets + collections}) AS c
            """,
        ),
        (
            "container_movies",
            f"""
            INSERT INTO container_movies (container_id, movie_id, sort_order)
            SELECT DISTINCT ON (c, movie) pg_temp.uid('container', c), pg_temp.uid('movie', movie), k
            FROM (
                SELECT c, k, (pg_temp.h('member' || k, c) % {movies}) + 1 AS movie
                FROM generate_series(1, {box_sets + collections}) AS c
                CROSS JOIN LATERAL generate_series(
                    1, CASE WHEN c <= {box_sets} THEN 3 + pg_temp.h('size', c) % 6 ELSE 40 END
                ) AS k
            ) members
            ORDER BY c, movie, k
            """,
        ),
    ]


COUNTED_TABLES = (
    "locations", "people", "movies", "movie_credits", "movie_genres", "movie_identifiers",
    "media_assets", "entity_media", "containers", "container_movies",
)


def seed(conn, *, movies: int = DEFAULT_MOVIES, seed_text: str = DEFAULT_SEED, log=None) -> dict[str, int]:
    """Fill the database behind `conn` and return the row count per table.

    Runs in the caller's transaction and commits nothing itself: a failure
    half-way leaves an empty database, not a half-filled one.
    """
    if movies < 1:
        raise ValueError("movies must be at least 1")
    people = max(10, int(movies * 1.6))
    with conn.cursor() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM movies)")
        row = cur.fetchone()
        if (row[0] if not isinstance(row, dict) else next(iter(row.values()))):
            raise SeedRefused(
                "this database already holds movies; perf_seed.py only fills an empty, throwaway database"
            )
        # Every "random" choice is a hash of (seed, tag, n): same arguments, same rows.
        cur.execute("SELECT set_config('perf_seed.seed', %s, false)", (seed_text,))
        cur.execute(
            """
            CREATE OR REPLACE FUNCTION pg_temp.h(tag text, n bigint) RETURNS integer
            LANGUAGE sql STABLE AS $$
                SELECT (('x' || substr(md5(current_setting('perf_seed.seed') || ':' || tag || ':' || n), 1, 8))::bit(32)::bigint
                        & 2147483647)::integer
            $$
            """
        )
        cur.execute(
            """
            CREATE OR REPLACE FUNCTION pg_temp.uid(tag text, n bigint) RETURNS uuid
            LANGUAGE sql STABLE AS $$
                SELECT md5(current_setting('perf_seed.seed') || ':' || tag || ':' || n)::uuid
            $$
            """
        )
        for label, statement in seed_statements(movies, people):
            started = time.perf_counter()
            cur.execute(statement)
            if log:
                log(f"{label}: {cur.rowcount} rows in {time.perf_counter() - started:.1f}s")
        counts: dict[str, int] = {}
        for table in COUNTED_TABLES:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            row = cur.fetchone()
            counts[table] = int(row[0] if not isinstance(row, dict) else next(iter(row.values())))
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--movies", type=int, default=DEFAULT_MOVIES, help="number of movies (default 50000)")
    parser.add_argument("--seed", default=DEFAULT_SEED, help="text mixed into every choice (default %(default)s)")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        return 2
    import psycopg

    try:
        with psycopg.connect(url) as conn:
            counts = seed(conn, movies=args.movies, seed_text=args.seed, log=print)
            conn.commit()
        # Fresh statistics, or the first measurement times the planner's guesses instead of the data.
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("VACUUM (ANALYZE)")
    except SeedRefused as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for table, count in counts.items():
        print(f"{table:20} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
