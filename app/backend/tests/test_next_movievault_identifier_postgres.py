"""MovieVault identifier storage against a real database.

`movie_identifiers` has PRIMARY KEY (movie_id, provider_id, identifier_type,
identifier) -- the identifier is part of the key, so insert-if-absent stores one
row per value a movie was ever matched to. For TMDB that is harmless; a film's
TMDB id does not change. For MovieVault it is not: an id names one *release*,
and re-matching a movie to a different pressing is a routine act -- the barcode
fallback picker exists to do exactly that.

`movie_identifiers()` reads the rows ordered by identifier, so accumulated rows
mean the lexicographically smallest UUID wins, which is to say an arbitrary
stale one. Only a real PostgreSQL sees this: a fake cursor cannot enforce a
primary key or an ORDER BY, so the whole failure is invisible to the rest of
the suite.
"""

import os
import sys
import unittest
import uuid


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:
    psycopg = None
    dict_row = None

from app.backend import next_metadata


DATABASE_URL = os.environ.get("DATABASE_URL")

PUBLIC_ID_PREFIX = "mv-identifier-test"

# The pressing a movie is matched to first, then re-matched away from. It
# deliberately sorts *before* RELEASE_B: without superseding both rows survive,
# and a selector reading them in identifier order hands back this stale one.
# With the values the other way round the accumulated row would sort second and
# the selector would look correct by luck, proving nothing.
RELEASE_A = "00000000-0000-4000-8000-00000000000a"
RELEASE_B = "10000000-0000-4000-8000-00000000000b"


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class MovieVaultIdentifierSupersedePostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def setUp(self):
        self.public_id = f"{PUBLIC_ID_PREFIX}-{uuid.uuid4()}"
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO movies (public_id, title)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (self.public_id, "MovieVault Identifier Test"),
                )
                self.movie_id = cur.fetchone()["id"]
            conn.commit()

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM movies WHERE public_id LIKE %s", (f"{PUBLIC_ID_PREFIX}-%",)
                )
            conn.commit()

    def _apply(self, identifiers):
        with self.connect() as conn:
            next_metadata.apply_metadata_proposal(
                conn, self.movie_id, {"identifiers": identifiers}
            )
            conn.commit()

    def _rows(self, provider):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT identifier FROM movie_identifiers
                    WHERE movie_id=%s AND provider_id=%s AND identifier_type='movie_id'
                    ORDER BY identifier
                    """,
                    (self.movie_id, provider),
                )
                return [row["identifier"] for row in cur.fetchall()]

    def test_a_rematch_supersedes_the_previous_release_instead_of_accumulating(self):
        self._apply({"movievault_v2": RELEASE_A})
        self.assertEqual(self._rows("movievault_v2"), [RELEASE_A])

        self._apply({"movievault_v2": RELEASE_B})
        self.assertEqual(self._rows("movievault_v2"), [RELEASE_B])

    def test_the_selector_returns_the_current_release_after_a_rematch(self):
        """The end the storage rule exists for: what a refresh looks up."""
        self._apply({"movievault_v2": RELEASE_A})
        self._apply({"movievault_v2": RELEASE_B})

        with self.connect() as conn:
            values = next_metadata.movie_identifiers(conn, self.movie_id)

        self.assertEqual(values["movieVaultId"], RELEASE_B)

    def test_reapplying_the_same_release_is_idempotent(self):
        self._apply({"movievault_v2": RELEASE_A})
        self._apply({"movievault_v2": RELEASE_A})
        self.assertEqual(self._rows("movievault_v2"), [RELEASE_A])

    def test_the_generations_do_not_evict_each_other(self):
        """Superseding is per provider. A v2 id must not remove a
        `movievault_26` row -- they are separate namespaces, and the older one
        is still what a movie synced from the previous generation carries."""
        self._apply({"movievault_26": "mv-9", "movievault_v2": RELEASE_A})
        self.assertEqual(self._rows("movievault_26"), ["mv-9"])
        self.assertEqual(self._rows("movievault_v2"), [RELEASE_A])

    def test_the_v2_id_is_the_one_a_refresh_resolves(self):
        """`movie_identifiers()` reads rows ordered by provider_id, which sorts
        "movievault_26" first. The selector must still surface the v2 id: it is
        the only generation a v4 catalog lookup can resolve."""
        self._apply({"movievault_26": "mv-9", "movievault_v2": RELEASE_A})

        with self.connect() as conn:
            values = next_metadata.movie_identifiers(conn, self.movie_id)

        self.assertEqual(values["movieVaultId"], RELEASE_A)

    def test_tmdb_still_accumulates_untouched(self):
        """The supersede rule is deliberately scoped to MovieVault providers.
        Changing TMDB's storage behaviour is a different decision with different
        consequences, and this test pins that it was not made here by accident."""
        self._apply({"tmdb": "603"})
        self._apply({"tmdb": "604"})
        self.assertEqual(self._rows("tmdb"), ["603", "604"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
