""""When I delete something, I want it gone" -- as a choice, not a decision (#719).

The default stays what `personal-lists-on-deletion.md` §2 settled: watch history
survives its movie, because it records something that happened and deleting the
disc does not un-watch the film. The reporter put the opposite view just as
plainly -- "if I delete something, regardless of any statuses, I want it gone" --
and both readings are defensible *about your own history*. So it is the
preference `delete_removes_watch_history`, off by default.

The tests are mostly about the boundaries of that choice, because a preference
that leaks past its owner is worse than no preference:

  - it is read **per user, not per actor**. A delete is performed by one person
    and reaches every user's lists, so the deleting user's setting must not
    decide what happens to someone else's history;
  - it never touches the *watchlist* rule from #728, which is unconditional;
  - turning it on also hides the entries already stranded -- by a delete that
    predates the preference, or by a sync client's tombstone, which never runs
    the delete path -- and the Lists badge counts them the way the list renders
    them, so the number cannot disagree with the screen (#729);
  - hiding, not deleting, on the read path: rendering a list is not the moment
    to destroy data, and a re-added movie makes the entry live again.

Skipped without DATABASE_URL, like the other post-migration suites.
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
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None
    Jsonb = None

try:
    from app.backend import next_app, next_preferences
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_app = None
    next_preferences = None

DATABASE_URL = os.environ.get("DATABASE_URL")

PREFERENCE = "delete_removes_watch_history"


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and next_app is not None,
    "PostgreSQL test database is not configured",
)
class WatchHistoryDeletionPreferenceTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def setUp(self):
        self.tag = f"whd719-{uuid.uuid4()}"
        self.user_ids = []
        self.movie_ids = []
        self.addCleanup(self._remove_fixture_rows)

    def _remove_fixture_rows(self):
        with self.connect() as conn, conn.cursor() as cur:
            for user_id in self.user_ids:
                cur.execute("DELETE FROM user_sync_changes WHERE user_id=%s", (user_id,))
                cur.execute("DELETE FROM user_sync_state WHERE user_id=%s", (user_id,))
                cur.execute("DELETE FROM user_preferences WHERE user_id=%s", (user_id,))
                cur.execute("DELETE FROM watchlist_items WHERE user_id=%s", (user_id,))
                cur.execute("DELETE FROM watch_history WHERE user_id=%s", (user_id,))
                cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
            for movie_id in self.movie_ids:
                cur.execute("DELETE FROM movies WHERE id=%s", (movie_id,))

    def _make_user(self, conn, *, wants_gone=None):
        user_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, display_name, status) VALUES (%s, %s, 'W', 'active')",
                (user_id, f"{self.tag}-{len(self.user_ids)}"),
            )
        self.user_ids.append(user_id)
        if wants_gone is not None:
            next_preferences.set_app_user_preferences(conn, user_id, {PREFERENCE: wants_gone})
        return user_id

    def _make_movie(self, conn, *, title=None, deleted=False):
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, deleted_at)
                VALUES (%s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END)
                """,
                (movie_id, str(uuid.uuid4()), title or f"{self.tag} movie", deleted),
            )
        self.movie_ids.append(movie_id)
        return movie_id

    def _watch(self, conn, user_id, movie_id, *, title=None):
        entry_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO watch_history (id, user_id, movie_id, watched_at, snapshot)
                VALUES (%s, %s, %s, now(), %s)
                """,
                (entry_id, user_id, movie_id, Jsonb({"movie_title": title or f"{self.tag} movie"})),
            )
        return entry_id

    def _history_rows(self, conn, user_id):
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM watch_history WHERE user_id=%s", (user_id,))
            return [row["id"] for row in cur.fetchall()]

    def _watched_titles(self, conn, user_id):
        rows = next_app.personal_list_movie_entities(conn, user_id, kind="watched")
        return [row.get("title") for row in rows]

    # -- the default is unchanged -----------------------------------------

    def test_the_preference_is_off_by_default(self):
        with self.connect() as conn:
            user = self._make_user(conn)
            self.assertFalse(next_preferences.user_delete_removes_watch_history(conn, user))
            self.assertFalse(next_preferences.APP_PREFERENCE_DEFAULTS[PREFERENCE])

    def test_history_still_survives_a_delete_when_the_preference_is_off(self):
        """§2 of the policy, guarded: the opt-in must not become the default by
        accident."""
        with self.connect() as conn:
            user = self._make_user(conn, wants_gone=False)
            movie = self._make_movie(conn, title=f"{self.tag} kept history")
            entry = self._watch(conn, user, movie, title=f"{self.tag} kept history")

            next_app.delete_movie_records(conn, movie)

            self.assertEqual(self._history_rows(conn, user), [entry])
            self.assertEqual(self._watched_titles(conn, user), [f"{self.tag} kept history"])

    # -- the opt-in ---------------------------------------------------------

    def test_the_delete_removes_history_for_a_user_who_asked_for_it(self):
        with self.connect() as conn:
            user = self._make_user(conn, wants_gone=True)
            movie = self._make_movie(conn)
            self._watch(conn, user, movie)

            _, deleted = next_app.delete_movie_records(conn, movie)

            self.assertEqual(deleted.get("watch_history_entries"), 1)
            self.assertEqual(self._history_rows(conn, user), [])
            self.assertEqual(self._watched_titles(conn, user), [])

    def test_one_users_choice_does_not_reach_another_users_history(self):
        """The constraint that shapes the whole feature. A delete is performed
        by one person and lands on everybody's lists; only the owner of a
        history may decide whether it is history."""
        with self.connect() as conn:
            wants_gone = self._make_user(conn, wants_gone=True)
            keeps_it = self._make_user(conn, wants_gone=False)
            movie = self._make_movie(conn, title=f"{self.tag} shared")
            self._watch(conn, wants_gone, movie, title=f"{self.tag} shared")
            kept_entry = self._watch(conn, keeps_it, movie, title=f"{self.tag} shared")

            next_app.delete_movie_records(conn, movie)

            self.assertEqual(self._history_rows(conn, wants_gone), [])
            self.assertEqual(self._history_rows(conn, keeps_it), [kept_entry])
            self.assertEqual(self._watched_titles(conn, keeps_it), [f"{self.tag} shared"])

    def test_only_the_deleted_movies_history_goes(self):
        with self.connect() as conn:
            user = self._make_user(conn, wants_gone=True)
            doomed = self._make_movie(conn)
            kept = self._make_movie(conn, title=f"{self.tag} other film")
            self._watch(conn, user, doomed)
            kept_entry = self._watch(conn, user, kept, title=f"{self.tag} other film")

            next_app.delete_movie_records(conn, doomed)

            self.assertEqual(self._history_rows(conn, user), [kept_entry])

    def test_the_removal_reaches_that_users_sync_stream(self):
        with self.connect() as conn:
            user = self._make_user(conn, wants_gone=True)
            bystander = self._make_user(conn, wants_gone=True)
            movie = self._make_movie(conn)
            entry = self._watch(conn, user, movie)

            next_app.delete_movie_records(conn, movie)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT operation FROM user_sync_changes
                    WHERE user_id=%s AND entity_type='watch_history' AND entity_id=%s
                    """,
                    (user, str(entry)),
                )
                self.assertEqual([row["operation"] for row in cur.fetchall()], ["delete"])
                cur.execute(
                    "SELECT 1 FROM user_sync_changes WHERE user_id=%s AND entity_type='watch_history'",
                    (bystander,),
                )
                self.assertEqual(cur.fetchall(), [], "a user who never watched it hears nothing")

    def test_every_viewing_of_the_movie_goes_not_just_the_latest(self):
        with self.connect() as conn:
            user = self._make_user(conn, wants_gone=True)
            movie = self._make_movie(conn)
            for _ in range(3):
                self._watch(conn, user, movie)

            _, deleted = next_app.delete_movie_records(conn, movie)

            self.assertEqual(deleted.get("watch_history_entries"), 3)
            self.assertEqual(self._history_rows(conn, user), [])

    # -- entries stranded before the preference existed ---------------------

    def test_turning_it_on_hides_entries_stranded_by_an_earlier_delete(self):
        with self.connect() as conn:
            user = self._make_user(conn, wants_gone=True)
            entry_id = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO watch_history (id, user_id, movie_id, watched_at, snapshot)
                    VALUES (%s, %s, NULL, now(), %s)
                    """,
                    (entry_id, user, Jsonb({"movie_title": f"{self.tag} stranded"})),
                )

            self.assertEqual(self._watched_titles(conn, user), [])
            self.assertEqual(
                self._history_rows(conn, user),
                [entry_id],
                "hidden on the read path, not destroyed while rendering a list",
            )

    def test_a_tombstoned_movie_is_hidden_too(self):
        """A sync client's tombstone never runs the delete path, so the read
        path is the only place that can honour the preference for it."""
        with self.connect() as conn:
            user = self._make_user(conn, wants_gone=True)
            movie = self._make_movie(conn, title=f"{self.tag} tombstoned", deleted=True)
            self._watch(conn, user, movie, title=f"{self.tag} tombstoned")

            self.assertEqual(self._watched_titles(conn, user), [])

    def test_the_same_stranded_entry_stays_visible_for_a_user_who_keeps_history(self):
        with self.connect() as conn:
            user = self._make_user(conn, wants_gone=False)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO watch_history (id, user_id, movie_id, watched_at, snapshot)
                    VALUES (%s, %s, NULL, now(), %s)
                    """,
                    (uuid.uuid4(), user, Jsonb({"movie_title": f"{self.tag} stranded"})),
                )

            self.assertEqual(self._watched_titles(conn, user), [f"{self.tag} stranded"])

    def test_the_badge_counts_what_the_list_shows(self):
        """#729's rule: the one element that could reveal a discrepancy must
        not instead confirm it."""
        with self.connect() as conn:
            hides = self._make_user(conn, wants_gone=True)
            keeps = self._make_user(conn, wants_gone=False)
            for user in (hides, keeps):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO watch_history (id, user_id, movie_id, watched_at, snapshot)
                        VALUES (%s, %s, NULL, now(), %s)
                        """,
                        (uuid.uuid4(), user, Jsonb({"movie_title": f"{self.tag} stranded"})),
                    )
                live = self._make_movie(conn, title=f"{self.tag} live")
                self._watch(conn, user, live, title=f"{self.tag} live")

            hidden_counts = next_app.personal_list_counts(conn, hides)
            kept_counts = next_app.personal_list_counts(conn, keeps)

            self.assertEqual(hidden_counts.get("watchHistory"), len(self._watched_titles(conn, hides)))
            self.assertEqual(hidden_counts.get("watchHistory"), 1)
            self.assertEqual(kept_counts.get("watchHistory"), len(self._watched_titles(conn, keeps)))
            self.assertEqual(kept_counts.get("watchHistory"), 2)

    # -- the watchlist rule is not a preference -----------------------------

    def test_the_watchlist_is_still_cleared_unconditionally(self):
        with self.connect() as conn:
            user = self._make_user(conn, wants_gone=False)
            movie = self._make_movie(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO watchlist_items (id, user_id, movie_id, added_at, snapshot) VALUES (%s, %s, %s, now(), %s)",
                    (uuid.uuid4(), user, movie, Jsonb({})),
                )

            _, deleted = next_app.delete_movie_records(conn, movie)

            self.assertEqual(deleted.get("watchlist_items"), 1)
            self.assertEqual(next_app.personal_list_movie_entities(conn, user, kind="watchlist"), [])


@unittest.skipUnless(next_preferences is not None, "backend module is unavailable")
class PreferenceDeclarationTests(unittest.TestCase):
    """The declaration has to be complete in every table that reads it, and the
    stale duplicate in `next_app` is the specific way that goes wrong."""

    def test_the_preference_is_declared_as_a_boolean_in_a_visible_section(self):
        self.assertIn(PREFERENCE, next_preferences.APP_PREFERENCE_DEFAULTS)
        self.assertIn(PREFERENCE, next_preferences.APP_BOOLEAN_PREFERENCES)
        sections = next_preferences.APP_PREFERENCE_SECTIONS
        placed = [name for name, keys in sections.items() if PREFERENCE in keys]
        self.assertEqual(
            placed,
            ["library"],
            "the Collectors tab is hidden without container-management permission, "
            "which has nothing to do with your own watch history",
        )

    def test_the_duplicate_table_in_next_app_agrees(self):
        if next_app is None:
            self.skipTest("next_app is unavailable")
        # Re-bound at import time, so this is really a check that the re-bind
        # happened -- a preference added only to the stale block would read as
        # present here and be absent at runtime, or the reverse.
        self.assertIn(PREFERENCE, next_app.APP_PREFERENCE_DEFAULTS)
        self.assertIn(PREFERENCE, next_app.APP_BOOLEAN_PREFERENCES)
        self.assertTrue(hasattr(next_app, "user_delete_removes_watch_history"))

    def test_an_unknown_value_is_coerced_to_a_boolean(self):
        self.assertIs(next_preferences.validate_app_preference(PREFERENCE, "true"), True)
        self.assertIs(next_preferences.validate_app_preference(PREFERENCE, "off"), False)


@unittest.skipUnless(next_app is not None, "backend module is unavailable")
class PreferenceUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
        with open(path, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_the_toggle_is_offered_on_the_library_tab(self):
        start = self.source.index("const preferenceLibraryGroups")
        library = self.source[start:self.source.index("const preferenceCollectorGroups")]
        self.assertIn('"delete_removes_watch_history"', library)
        self.assertIn('"preferences.deleteRemovesWatchHistory"', library)
        self.assertIn('"preferences.deleteRemovesWatchHistoryHelp"', library)

    def test_its_row_declares_no_dependency_on_another_preference(self):
        start = self.source.index('["delete_removes_watch_history"')
        row = self.source[start:self.source.index("]", start) + 1]
        self.assertEqual(
            row.count(","),
            2,
            "a fourth element would gate the row behind another preference; this one stands alone",
        )


if __name__ == "__main__":
    unittest.main()
