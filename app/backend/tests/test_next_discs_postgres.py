"""Discs against a real database: the guarantees only the schema can make.

Migration 075 layers a disc under a release. Almost everything worth asserting
about that is a foreign key rather than a code rule, because a code rule drifts
and a foreign key does not:

* a disc cannot carry a season the release is not recorded as covering;
* a disc cannot be named under a release it does not belong to;
* dropping a season from the release drops it from every disc in one statement.

The rest is about identity. A disc is pointed at by ``movie_disc_episodes``, so
saving a release twice has to leave the same disc rows in place -- the failure
this guards against is silent, and looks exactly like "the episodes I entered
vanished".
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
except ModuleNotFoundError:  # pragma: no cover - depends on environment
    psycopg = None
    dict_row = None

from app.backend import next_app
from app.backend import next_discs
from app.backend.next_common import NextApiError


DATABASE_URL = os.environ.get("DATABASE_URL")
PREFIX = "movie-discs-test"


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class MovieDiscTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                # movie_disc_* cascade off both parents, so the two parents are
                # enough -- but movie_episodes cites series_episodes as well as
                # movies, so it goes before either.
                cur.execute(
                    "DELETE FROM movie_episodes WHERE movie_id IN "
                    "(SELECT id FROM movies WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute(
                    "DELETE FROM movie_seasons WHERE movie_id IN "
                    "(SELECT id FROM movies WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute("DELETE FROM movies WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute(
                    "DELETE FROM series_episodes WHERE public_id LIKE %s", (f"{PREFIX}-%",)
                )
                cur.execute(
                    "DELETE FROM series_seasons WHERE public_id LIKE %s", (f"{PREFIX}-%",)
                )
                cur.execute("DELETE FROM series WHERE public_id LIKE %s", (f"{PREFIX}-%",))
            conn.commit()

    # -- fixtures ----------------------------------------------------------

    def _film(self, conn):
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO movies (id, public_id, title, sort_title) VALUES (%s,%s,'Alien','Alien')",
                (movie_id, f"{PREFIX}-{movie_id}"),
            )
        conn.commit()
        return movie_id

    def _show(self, conn):
        """A two-season series with two episodes in season 1, on a SHOW release."""
        ids = {key: uuid.uuid4() for key in ("series", "s1", "s2", "e1", "e2", "movie")}
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO series (id, public_id, title) VALUES (%s,%s,'Twin Peaks')",
                (ids["series"], f"{PREFIX}-{ids['series']}"),
            )
            for key, number in (("s1", 1), ("s2", 2)):
                cur.execute(
                    "INSERT INTO series_seasons (id, public_id, series_id, season_number)"
                    " VALUES (%s,%s,%s,%s)",
                    (ids[key], f"{PREFIX}-{ids[key]}", ids["series"], number),
                )
            for key, number in (("e1", 1), ("e2", 2)):
                cur.execute(
                    "INSERT INTO series_episodes"
                    " (id, public_id, series_id, season_id, episode_number, title)"
                    " VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        ids[key],
                        f"{PREFIX}-{ids[key]}",
                        ids["series"],
                        ids["s1"],
                        number,
                        f"Episode {number}",
                    ),
                )
            cur.execute(
                "INSERT INTO movies (id, public_id, title, sort_title, media_type, series_id)"
                " VALUES (%s,%s,'Box','Box','SHOW',%s)",
                (ids["movie"], f"{PREFIX}-{ids['movie']}", ids["series"]),
            )
        conn.commit()
        return ids

    def _write(self, conn, movie_id, body, *, media_type="MOVIE"):
        discs = next_discs.discs_payload(body)
        with conn.cursor() as cur:
            next_app.apply_movie_discs(cur, movie_id, discs, media_type=media_type)
        conn.commit()
        return next_app.movie_disc_entities(conn, movie_id)

    # -- the release keeps what belongs to the release ---------------------

    def test_a_disc_narrows_the_release_without_touching_its_barcode(self):
        """The requirement in one assertion: discs are added *to* a release, and
        the identity of the release is untouched by them."""
        with self.connect() as conn:
            movie_id = self._film(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE movies SET barcode = %s WHERE id = %s",
                    (f"{PREFIX}-5050582889055", movie_id),
                )
            conn.commit()
            self._write(
                conn,
                movie_id,
                {
                    "discs": [
                        {"discType": "uhd_bluray", "discRole": "feature", "hdr": "dolby_vision"},
                        {"discType": "bluray", "discRole": "bonus", "label": "Bonus Disc"},
                    ]
                },
            )
            with conn.cursor() as cur:
                cur.execute("SELECT barcode FROM movies WHERE id = %s", (movie_id,))
                self.assertEqual(cur.fetchone()["barcode"], f"{PREFIX}-5050582889055")

    def test_two_discs_of_one_release_hold_different_specs(self):
        """The whole point. One flattened release-level answer could not say that
        the UHD carries Dolby Vision and the Blu-ray beside it does not."""
        with self.connect() as conn:
            movie_id = self._film(conn)
            discs = self._write(
                conn,
                movie_id,
                {
                    "discs": [
                        {
                            "discType": "uhd_bluray",
                            "videoResolution": "2160p",
                            "hdr": ["dolby_vision", "hdr10"],
                            "regions": ["FREE"],
                            "audioTracks": [
                                {
                                    "languageCode": "en",
                                    "codec": "dolby_truehd",
                                    "channels": "7.1",
                                    "immersiveFormat": "dolby_atmos",
                                }
                            ],
                        },
                        {
                            "discType": "bluray",
                            "videoResolution": "1080p",
                            "regions": ["B"],
                            "subtitles": ["en", "nl"],
                        },
                    ]
                },
            )
            self.assertEqual([d["discType"] for d in discs], ["uhd_bluray", "bluray"])
            self.assertEqual(discs[0]["hdr"], ["dolby_vision", "hdr10"])
            self.assertEqual(discs[1]["hdr"], [])
            self.assertEqual(discs[0]["regions"], ["FREE"])
            self.assertEqual(discs[1]["regions"], ["B"])
            self.assertEqual(discs[0]["audioTracks"][0]["immersiveFormat"], "dolby_atmos")
            self.assertEqual(
                [s["languageCode"] for s in discs[1]["subtitles"]], ["en", "nl"]
            )

    def test_the_free_text_type_survives_a_round_trip(self):
        with self.connect() as conn:
            movie_id = self._film(conn)
            discs = self._write(
                conn,
                movie_id,
                {"discs": [{"discType": "other", "discTypeOther": "MiniDisc"}]},
            )
            self.assertEqual(discs[0]["discTypeOther"], "MiniDisc")

    def test_the_schema_refuses_free_text_under_a_known_type(self):
        """The payload turns this into a 400, but the constraint is what makes it
        impossible -- including for a writer that never goes through the payload."""
        with self.connect() as conn:
            movie_id = self._film(conn)
            with self.assertRaises(psycopg.errors.CheckViolation):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO movie_discs (public_id, movie_id, disc_type, disc_type_other)"
                        " VALUES (%s,%s,'bluray','MiniDisc')",
                        (f"{PREFIX}-check-{uuid.uuid4()}", movie_id),
                    )
            conn.rollback()

    # -- identity ----------------------------------------------------------

    def test_saving_twice_keeps_the_same_disc_rows(self):
        """A disc is pointed at by movie_disc_episodes, so replacing the list
        wholesale on every save would empty every disc's episode list while
        looking, from the outside, like nothing happened."""
        with self.connect() as conn:
            movie_id = self._film(conn)
            first = self._write(conn, movie_id, {"discs": [{"discType": "dvd"}]})
            disc_id = first[0]["id"]
            second = self._write(
                conn, movie_id, {"discs": [{"id": disc_id, "discType": "dvd", "label": "Disc 1"}]}
            )
            self.assertEqual(second[0]["id"], disc_id)
            self.assertEqual(second[0]["label"], "Disc 1")

    def test_a_disc_nobody_named_is_removed(self):
        with self.connect() as conn:
            movie_id = self._film(conn)
            discs = self._write(
                conn, movie_id, {"discs": [{"discType": "dvd"}, {"discType": "bluray"}]}
            )
            kept = discs[1]["id"]
            after = self._write(conn, movie_id, {"discs": [{"id": kept, "discType": "bluray"}]})
            self.assertEqual([d["id"] for d in after], [kept])

    def test_an_absent_discs_key_leaves_the_discs_alone(self):
        """The compatibility rule, end to end: an older client saving a title
        must not delete a disc list it does not know about."""
        with self.connect() as conn:
            movie_id = self._film(conn)
            self._write(conn, movie_id, {"discs": [{"discType": "dvd"}]})
            self._write(conn, movie_id, {"title": "Alien"})
            self.assertEqual(len(next_app.movie_disc_entities(conn, movie_id)), 1)

    def test_an_explicit_empty_list_clears_them(self):
        with self.connect() as conn:
            movie_id = self._film(conn)
            self._write(conn, movie_id, {"discs": [{"discType": "dvd"}]})
            self._write(conn, movie_id, {"discs": []})
            self.assertEqual(next_app.movie_disc_entities(conn, movie_id), [])

    def test_the_disc_you_just_added_survives_a_save_you_did_not_fill_in(self):
        """What the edit form produces the moment someone breaks a release down:
        Disc 1 carrying the release's details, Disc 2 named but not yet
        described. Both have to land — the click that produced Disc 2 *is* the
        statement that it exists."""
        with self.connect() as conn:
            movie_id = self._film(conn)
            discs = self._write(
                conn,
                movie_id,
                {"discs": [{"discType": "dvd", "videoResolution": "576p"}, {}]},
            )
            self.assertEqual(len(discs), 2)
            self.assertEqual([d["discType"] for d in discs], ["dvd", None])

    def test_a_list_of_nothing_but_blank_rows_stores_none_of_them(self):
        with self.connect() as conn:
            movie_id = self._film(conn)
            self.assertEqual(self._write(conn, movie_id, {"discs": [{}, {}]}), [])

    def test_a_disc_id_from_another_release_is_refused(self):
        """Treating a stale id as a new disc is how a duplicate appears with
        nothing to explain it."""
        with self.connect() as conn:
            first = self._film(conn)
            second = self._film(conn)
            foreign = self._write(conn, first, {"discs": [{"discType": "dvd"}]})[0]["id"]
            with self.assertRaises(NextApiError) as caught:
                self._write(conn, second, {"discs": [{"id": foreign, "discType": "dvd"}]})
            conn.rollback()
            self.assertIn(foreign, str(caught.exception))

    def test_the_schema_refuses_a_disc_named_under_the_wrong_release(self):
        """The composite key doing the work the code check would drift away
        from -- the same device 063 and 074 already use."""
        with self.connect() as conn:
            first = self._film(conn)
            second = self._film(conn)
            disc_id = self._write(conn, first, {"discs": [{"discType": "dvd"}]})[0]["id"]
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO movie_disc_seasons (disc_id, movie_id, season_id)"
                        " VALUES (%s,%s,%s)",
                        (disc_id, second, uuid.uuid4()),
                    )
            conn.rollback()

    # -- disc_count --------------------------------------------------------

    def test_disc_count_is_filled_when_nobody_has_answered_it(self):
        with self.connect() as conn:
            movie_id = self._film(conn)
            self._write(
                conn, movie_id, {"discs": [{"discType": "uhd_bluray"}, {"discType": "bluray"}]}
            )
            with conn.cursor() as cur:
                cur.execute("SELECT disc_count FROM movies WHERE id = %s", (movie_id,))
                self.assertEqual(cur.fetchone()["disc_count"], 2)

    def test_a_stated_disc_count_is_never_overwritten(self):
        """It is routinely stated -- by MovieVault, or by hand -- before anyone
        enumerates the discs, so a half-entered list must not correct a correct
        value downwards. The disagreement is surfaced in the form instead."""
        with self.connect() as conn:
            movie_id = self._film(conn)
            with conn.cursor() as cur:
                cur.execute("UPDATE movies SET disc_count = 4 WHERE id = %s", (movie_id,))
            conn.commit()
            self._write(conn, movie_id, {"discs": [{"discType": "uhd_bluray"}]})
            with conn.cursor() as cur:
                cur.execute("SELECT disc_count FROM movies WHERE id = %s", (movie_id,))
                self.assertEqual(cur.fetchone()["disc_count"], 4)

    # -- television --------------------------------------------------------

    def test_a_disc_says_which_season_it_holds(self):
        with self.connect() as conn:
            ids = self._show(conn)
            discs = self._write(
                conn,
                ids["movie"],
                {
                    "discs": [
                        {"discType": "bluray", "seasonIds": [str(ids["s1"])]},
                        {"discType": "bluray", "seasonIds": [str(ids["s2"])]},
                    ]
                },
                media_type="SHOW",
            )
            self.assertEqual([d["seasons"][0]["seasonNumber"] for d in discs], [1, 2])

    def test_a_season_may_span_two_discs(self):
        """The reason the link is keyed on the pair. Season 1 across discs 1 and
        2 is the ordinary shape of a box set, not an edge case."""
        with self.connect() as conn:
            ids = self._show(conn)
            discs = self._write(
                conn,
                ids["movie"],
                {
                    "discs": [
                        {"discType": "dvd", "seasonIds": [str(ids["s1"])]},
                        {"discType": "dvd", "seasonIds": [str(ids["s1"])]},
                    ]
                },
                media_type="SHOW",
            )
            self.assertEqual([d["seasonIds"] for d in discs], [[str(ids["s1"])]] * 2)

    def test_a_disc_carrying_episodes_records_them_on_the_release_too(self):
        """Migration 074 created movie_episodes and left it unwritten because the
        release *was* the disc. This is the level the statement can be made at,
        so it is also what finally populates it -- and what makes `onDisc` in
        season_episode_entities mean something."""
        with self.connect() as conn:
            ids = self._show(conn)
            discs = self._write(
                conn,
                ids["movie"],
                {"discs": [{"discType": "bluray", "episodeIds": [str(ids["e1"])]}]},
                media_type="SHOW",
            )
            self.assertEqual(discs[0]["episodes"][0]["episodeNumber"], 1)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT episode_id FROM movie_episodes WHERE movie_id = %s",
                    (ids["movie"],),
                )
                self.assertEqual([r["episode_id"] for r in cur.fetchall()], [ids["e1"]])
            episodes = next_app.season_episode_entities(conn, ids["s1"])
            on_disc = {e["episodeNumber"]: e["onDisc"] for e in episodes}
            self.assertEqual(on_disc, {1: True, 2: False})

    def test_removing_a_disc_stops_claiming_its_episodes(self):
        """`onDisc` answers "is this episode anywhere in the collection". The disc
        list is the only thing that writes movie_episodes, so an episode no disc
        carries any more has to stop being claimed -- otherwise deleting the disc
        it was on leaves the answer stuck on yes forever."""
        with self.connect() as conn:
            ids = self._show(conn)
            discs = self._write(
                conn,
                ids["movie"],
                {
                    "discs": [
                        {"discType": "bluray", "episodeIds": [str(ids["e1"])]},
                        {"discType": "bluray", "episodeIds": [str(ids["e2"])]},
                    ]
                },
                media_type="SHOW",
            )
            kept = discs[0]["id"]
            self._write(
                conn,
                ids["movie"],
                {"discs": [{"id": kept, "discType": "bluray", "episodeIds": [str(ids["e1"])]}]},
                media_type="SHOW",
            )
            episodes = next_app.season_episode_entities(conn, ids["s1"])
            self.assertEqual(
                {e["episodeNumber"]: e["onDisc"] for e in episodes}, {1: True, 2: False}
            )

    def test_a_season_outlives_the_disc_that_named_it(self):
        """The deliberate asymmetry with episodes above. The release states its own
        season list in the same form, so "this box covers season 1" stays true even
        after the disc that carried it is deleted -- unlike an episode, which has
        no release-level input of its own."""
        with self.connect() as conn:
            ids = self._show(conn)
            self._write(
                conn,
                ids["movie"],
                {"discs": [{"discType": "bluray", "seasonIds": [str(ids["s1"])]}]},
                media_type="SHOW",
            )
            self._write(conn, ids["movie"], {"discs": []}, media_type="SHOW")
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT season_id FROM movie_seasons WHERE movie_id = %s", (ids["movie"],)
                )
                self.assertEqual([r["season_id"] for r in cur.fetchall()], [ids["s1"]])

    def test_naming_an_episode_pulls_in_its_season(self):
        """Picking episodes and never thinking about seasons is the natural way
        to fill this in; refusing it would put a validation error in front of
        somebody stating something true."""
        with self.connect() as conn:
            ids = self._show(conn)
            self._write(
                conn,
                ids["movie"],
                {"discs": [{"discType": "bluray", "episodeIds": [str(ids["e2"])]}]},
                media_type="SHOW",
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT season_id FROM movie_seasons WHERE movie_id = %s", (ids["movie"],)
                )
                self.assertEqual([r["season_id"] for r in cur.fetchall()], [ids["s1"]])

    def test_a_disc_cannot_carry_an_episode_of_another_series(self):
        with self.connect() as conn:
            ids = self._show(conn)
            stranger = uuid.uuid4()
            with self.assertRaises(NextApiError) as caught:
                self._write(
                    conn,
                    ids["movie"],
                    {"discs": [{"discType": "bluray", "episodeIds": [str(stranger)]}]},
                    media_type="SHOW",
                )
            conn.rollback()
            self.assertIn(str(stranger), str(caught.exception))

    def test_a_film_cannot_carry_seasons(self):
        with self.connect() as conn:
            ids = self._show(conn)
            with self.assertRaises(NextApiError):
                self._write(
                    conn,
                    ids["movie"],
                    {"discs": [{"discType": "dvd", "seasonIds": [str(ids["s1"])]}]},
                    media_type="MOVIE",
                )
            conn.rollback()

    def test_dropping_a_season_from_the_release_drops_it_from_the_disc(self):
        """The cascade is the point of pointing at movie_seasons rather than at
        series_seasons: the two levels cannot end up contradicting each other."""
        with self.connect() as conn:
            ids = self._show(conn)
            self._write(
                conn,
                ids["movie"],
                {
                    "discs": [
                        {
                            "discType": "bluray",
                            "seasonIds": [str(ids["s1"]), str(ids["s2"])],
                        }
                    ]
                },
                media_type="SHOW",
            )
            with conn.cursor() as cur:
                next_app.apply_movie_series_assignment(
                    cur,
                    ids["movie"],
                    {"series_id": ids["series"], "season_ids": [ids["s1"]]},
                    media_type="SHOW",
                )
            conn.commit()
            discs = next_app.movie_disc_entities(conn, ids["movie"])
            self.assertEqual(discs[0]["seasonIds"], [str(ids["s1"])])

    def test_restating_the_same_seasons_keeps_the_per_disc_links(self):
        """The regression the surgical rewrite of apply_movie_series_assignment
        exists for. Deleting and re-inserting an unchanged season list would
        cascade every per-disc link away, and the re-insert does not bring them
        back."""
        with self.connect() as conn:
            ids = self._show(conn)
            self._write(
                conn,
                ids["movie"],
                {"discs": [{"discType": "bluray", "seasonIds": [str(ids["s1"])]}]},
                media_type="SHOW",
            )
            with conn.cursor() as cur:
                next_app.apply_movie_series_assignment(
                    cur,
                    ids["movie"],
                    {"series_id": ids["series"], "season_ids": [ids["s1"]]},
                    media_type="SHOW",
                )
            conn.commit()
            discs = next_app.movie_disc_entities(conn, ids["movie"])
            self.assertEqual(discs[0]["seasonIds"], [str(ids["s1"])])

    def test_the_discs_travel_on_the_movie_entity(self):
        """A sync change stores the movie entity verbatim as its payload, so a
        disc list that is not on the entity never reaches an existing install."""
        with self.connect() as conn:
            movie_id = self._film(conn)
            self._write(conn, movie_id, {"discs": [{"discType": "dvd", "label": "Only"}]})
            entity = next_app.movie_entity(conn, movie_id)
            self.assertEqual([d["label"] for d in entity["discs"]], ["Only"])


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class MovieDiscSyncWireTests(unittest.TestCase):
    """Discs over the sync wire, end to end against the real apply path.

    Sync-contract §4.9. What has to hold: a mutation can create discs, the
    minted ids come back in the upsert response so the client can adopt them, a
    later mutation without the key leaves the discs alone, and a season may be
    referenced by its public_id -- the identifier a sync client is as likely to
    hold as the uuid, since the wire carries both.
    """

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM movie_episodes WHERE movie_id IN "
                    "(SELECT id FROM movies WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute(
                    "DELETE FROM movie_seasons WHERE movie_id IN "
                    "(SELECT id FROM movies WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute(
                    "DELETE FROM sync_changes WHERE entity_id IN "
                    "(SELECT id::text FROM movies WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute(
                    "DELETE FROM client_id_mappings WHERE entity_id IN "
                    "(SELECT id FROM movies WHERE public_id LIKE %s)",
                    (f"{PREFIX}-%",),
                )
                cur.execute("DELETE FROM movies WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute(
                    "DELETE FROM series_seasons WHERE public_id LIKE %s", (f"{PREFIX}-%",)
                )
                cur.execute("DELETE FROM series WHERE public_id LIKE %s", (f"{PREFIX}-%",))
            conn.commit()

    def _push(self, conn, payload, *, entity_id=None, mutation_id):
        mutation = {
            "clientMutationId": mutation_id,
            "clientEntityId": f"{PREFIX}-client-{mutation_id}",
            "payload": payload,
        }
        if entity_id is not None:
            mutation["entityId"] = str(entity_id)
        result = next_app.apply_movie_upsert(
            conn,
            client_id=f"{PREFIX}-device",
            idem_key=f"{PREFIX}-{mutation_id}",
            mutation=mutation,
        )
        conn.commit()
        return result

    def test_a_mutation_creates_discs_and_the_response_carries_their_ids(self):
        with self.connect() as conn:
            result = self._push(
                conn,
                {
                    "title": "Wire Box",
                    "discs": [
                        {"discType": "uhd_bluray", "discRole": "feature",
                         "hdr": ["dolby_vision"]},
                        {"discType": "bluray", "discRole": "bonus"},
                    ],
                },
                mutation_id="create-1",
            )
            self.assertEqual(result["status"], "applied")
            discs = result["entity"]["discs"]
            self.assertEqual([d["discType"] for d in discs], ["uhd_bluray", "bluray"])
            # The minted ids are in the response entity: that is how a client
            # that pushed id-less discs adopts them, exactly as it adopts the
            # movie's own entityId.
            self.assertTrue(all(d["id"] for d in discs))
            # Marker so tearDown finds the row.
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE movies SET public_id = %s WHERE id = %s",
                    (f"{PREFIX}-{result['entityId']}", result["entityId"]),
                )
            conn.commit()

    def test_a_mutation_without_the_key_leaves_the_discs_alone(self):
        """The compatibility half of §4.9: an iOS or Android build that predates
        discs pushes movie edits with no `discs` key, and must not delete a
        disc list it cannot see."""
        with self.connect() as conn:
            created = self._push(
                conn,
                {"title": "Wire Box", "discs": [{"discType": "dvd"}]},
                mutation_id="create-2",
            )
            entity_id = created["entityId"]
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE movies SET public_id = %s WHERE id = %s",
                    (f"{PREFIX}-{entity_id}", entity_id),
                )
            conn.commit()
            updated = self._push(
                conn,
                {"title": "Wire Box renamed"},
                entity_id=entity_id,
                mutation_id="update-2",
            )
            self.assertEqual(len(updated["entity"]["discs"]), 1)
            self.assertEqual(updated["entity"]["title"], "Wire Box renamed")

    def test_an_explicit_empty_list_clears_them_over_sync_too(self):
        with self.connect() as conn:
            created = self._push(
                conn,
                {"title": "Wire Box", "discs": [{"discType": "dvd"}]},
                mutation_id="create-3",
            )
            entity_id = created["entityId"]
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE movies SET public_id = %s WHERE id = %s",
                    (f"{PREFIX}-{entity_id}", entity_id),
                )
            conn.commit()
            updated = self._push(
                conn,
                {"title": "Wire Box", "discs": []},
                entity_id=entity_id,
                mutation_id="update-3",
            )
            self.assertEqual(updated["entity"]["discs"], [])

    def _linked_show(self, conn, *, suffix):
        """A SHOW release already linked to a series, the server-side way.

        Deliberately not via a mutation: the series link itself is server-owned
        and read-only on the wire in this contract version (changelog 1.15), so
        the state a syncing client really is in when it pushes a disc with
        seasons is "the link already exists" -- made in the PWA, or by
        enrichment. The disc mutation must work from there, and only from there.
        """
        series_id, season_id, movie_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        season_public = f"{PREFIX}-season-{season_id.hex[:8]}"
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO series (id, public_id, title) VALUES (%s,%s,'Wire Show')",
                (series_id, f"{PREFIX}-{series_id}"),
            )
            cur.execute(
                "INSERT INTO series_seasons (id, public_id, series_id, season_number)"
                " VALUES (%s,%s,%s,1)",
                (season_id, season_public, series_id),
            )
            cur.execute(
                "INSERT INTO movies (id, public_id, title, sort_title, media_type, series_id)"
                " VALUES (%s,%s,'Wire Show S1','Wire Show S1','SHOW',%s)",
                (movie_id, f"{PREFIX}-{suffix}-{movie_id}", series_id),
            )
        conn.commit()
        return movie_id, season_id, season_public

    def test_a_season_may_be_referenced_by_its_public_id(self):
        """The wire publishes both `id` and `publicId` for a season; a client is
        entitled to hold either, so the write path resolves both spellings onto
        the same row."""
        with self.connect() as conn:
            movie_id, season_id, season_public = self._linked_show(conn, suffix="pub")
            updated = self._push(
                conn,
                {"title": "Wire Show S1",
                 "discs": [{"discType": "bluray", "seasonIds": [season_public]}]},
                entity_id=movie_id,
                mutation_id="update-4",
            )
            self.assertEqual(
                updated["entity"]["discs"][0]["seasonIds"], [str(season_id)]
            )

    def test_an_unknown_reference_is_named_rather_than_skipped(self):
        with self.connect() as conn:
            movie_id, _, _ = self._linked_show(conn, suffix="unknown")
            with self.assertRaises(NextApiError) as caught:
                self._push(
                    conn,
                    {"title": "Wire Show S1",
                     "discs": [{"discType": "bluray",
                                "seasonIds": ["next-season-does-not-exist"]}]},
                    entity_id=movie_id,
                    mutation_id="update-5",
                )
            conn.rollback()
            self.assertIn("next-season-does-not-exist", str(caught.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
