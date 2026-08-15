"""The user's own images against a real database.

This is where the two rules that make the feature work are actually provable,
because both are about what happens when more than one device touches the same
set and neither can be checked against a fake cursor:

1. **The set is a union.** Uploading never removes what another device
   uploaded. Five images from a phone and three from the PWA make eight.
2. **A genuine conflict is settled by arrival order.** Which image is the
   representative one, hidden or not, deleted or not -- last writer wins, and
   the arbiter is the server revision rather than any device's clock.

Everything below is either one of those two or a trap on the way to them: the
re-upload that must revive a deleted row rather than silently do nothing, the
retry that must not become a second photograph, and the ceiling that must block
adding without ever hiding what is stored.
"""

import io
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:
    psycopg = None
    dict_row = None

from app.backend import next_app


DATABASE_URL = os.environ.get("DATABASE_URL")

PREFIX = "own-images-test"


def _png(colour, size=(8, 12)):
    """A distinct image per colour.

    The colour is what makes two uploads two *different* photographs:
    `media_assets` deduplicates on the content hash, so identical bytes are one
    image by design and a test that reused them would be testing the opposite of
    what it says.
    """
    try:
        from PIL import Image
    except ModuleNotFoundError:  # pragma: no cover - Pillow ships with the backend
        return None
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class OwnImagesPostgresTests(unittest.TestCase):
    def setUp(self):
        self.client = next_app.app.test_client()
        # An upload writes a real file under the data directory, which defaults
        # to `/data` and is not writable on a CI runner.
        self.data_dir = tempfile.mkdtemp(prefix="own-images-")
        self.addCleanup(shutil.rmtree, self.data_dir, True)
        self._env = patch.dict(os.environ, {"DISCVAULT_LEGACY_DATA_DIR": self.data_dir})
        self._env.start()
        self.addCleanup(self._env.stop)

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def tearDown(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                # `entity_media.entity_id` has no foreign key -- migration 003
                # leaves `entity_type` free text -- so a link deleted after its
                # owner is an orphan that outlives the run and is counted by the
                # next one.
                for table, entity_type in (("movies", "movie"), ("containers", "container"), ("series", "series")):
                    cur.execute(
                        f"""
                        DELETE FROM entity_media WHERE entity_type=%s AND entity_id IN (
                            SELECT id FROM {table} WHERE public_id LIKE %s
                        )
                        """,
                        (entity_type, f"{PREFIX}-%"),
                    )
                cur.execute("DELETE FROM media_assets WHERE storage_key LIKE %s", ("media/scans/%",))
                cur.execute("DELETE FROM movies WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM containers WHERE public_id LIKE %s", (f"{PREFIX}-%",))
                cur.execute("DELETE FROM series WHERE public_id LIKE %s", (f"{PREFIX}-%",))
            conn.commit()

    # --- fixtures -----------------------------------------------------------

    def _movie(self, title="A Release"):
        movie_id = uuid.uuid4()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO movies (id, public_id, title, sort_title)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (movie_id, f"{PREFIX}-{movie_id}", title, title),
                )
            conn.commit()
        return movie_id

    def _container(self, title="A Box Set"):
        container_id = uuid.uuid4()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO containers (id, public_id, title, container_type)
                    VALUES (%s, %s, %s, 'box_set')
                    """,
                    (container_id, f"{PREFIX}-{container_id}", title),
                )
            conn.commit()
        return container_id

    def _series(self, title="A Show"):
        series_id = uuid.uuid4()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO series (id, public_id, title, sort_title)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (series_id, f"{PREFIX}-{series_id}", title, title),
                )
            conn.commit()
        return series_id

    def _upload(self, segment, entity_id, colour, *, label="other", client_id=None, expect=201):
        png = _png(colour)
        if png is None:  # pragma: no cover - Pillow is a backend dependency
            self.skipTest("Pillow is not installed")
        data = {"file": (io.BytesIO(png), f"{colour}.png"), "label": label}
        if client_id:
            data["clientId"] = client_id
        response = self.client.post(
            f"/api/next/{segment}/{entity_id}/images",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, expect, response.data[:300])
        return response.get_json()

    def _images(self, segment, entity_id):
        """Everything the route publishes, tombstones included.

        Deliberately not filtered here: the route answers with the same
        snapshot the sync feed carries, and a caller that applies that answer
        has to see the removals or it puts the deleted images straight back.
        """
        response = self.client.get(f"/api/next/{segment}/{entity_id}/images")
        self.assertEqual(response.status_code, 200, response.data[:300])
        return response.get_json()["images"]

    def _live(self, segment, entity_id):
        """What the gallery draws."""
        return [image for image in self._images(segment, entity_id) if not image["deleted"]]

    # --- the two rules ------------------------------------------------------

    def test_two_devices_uploading_produce_the_union_not_the_last_one(self):
        """The rule the whole design exists for.

        The failure this guards against is not hypothetical: it is what happens
        the moment a client is allowed to push its own idea of the whole set. A
        phone that uploaded five and then heard about three more from the PWA
        would push five back and delete the other three, and nothing would
        report an error.
        """
        movie_id = self._movie()
        for colour in ((10, 20, 30), (40, 50, 60), (70, 80, 90)):
            self._upload("movies", movie_id, colour)
        for colour in ((100, 110, 120), (130, 140, 150)):
            self._upload("movies", movie_id, colour)

        images = self._live("movies", movie_id)
        self.assertEqual(len(images), 5)
        self.assertEqual(len({image["mediaId"] for image in images}), 5)

    def test_the_last_device_to_choose_a_primary_wins(self):
        """Two devices both pick a representative image; exactly one stays.

        No merge of two chosen images exists -- it is one user with two devices
        -- so the only sane answer is the later one, and the arbiter is the
        order the server received them in rather than either device's clock.
        """
        movie_id = self._movie()
        first = self._upload("movies", movie_id, (10, 20, 30))["mediaId"]
        second = self._upload("movies", movie_id, (40, 50, 60))["mediaId"]

        self.client.post(f"/api/next/movies/{movie_id}/images/{first}/primary")
        self.client.post(f"/api/next/movies/{movie_id}/images/{second}/primary")

        images = {image["mediaId"]: image for image in self._live("movies", movie_id)}
        self.assertFalse(images[first]["isPrimary"])
        self.assertTrue(images[second]["isPrimary"])
        self.assertEqual(sum(1 for image in images.values() if image["isPrimary"]), 1)

    # --- the traps on the way there ----------------------------------------

    def test_re_uploading_a_deleted_image_brings_it_back(self):
        """The trap that a plain `ON CONFLICT DO NOTHING` walks into.

        The link is keyed on (entity, media, role) and the media row is keyed on
        the content hash, so re-uploading the same photograph hits an existing,
        soft-deleted row. Skipping it leaves the image deleted: the user
        uploads, is told `201`, and sees nothing at all.
        """
        movie_id = self._movie()
        media_id = self._upload("movies", movie_id, (10, 20, 30))["mediaId"]
        self.assertEqual(
            self.client.delete(f"/api/next/movies/{movie_id}/images/{media_id}").status_code, 200
        )
        self.assertEqual(self._live("movies", movie_id), [])

        again = self._upload("movies", movie_id, (10, 20, 30))
        self.assertEqual(again["mediaId"], media_id)
        images = self._live("movies", movie_id)
        self.assertEqual([image["mediaId"] for image in images], [media_id])

    def test_a_retried_upload_with_the_same_token_is_one_image(self):
        """A token persisted before the request goes out is what makes an upload
        safe to retry over a connection that dropped after the write and before
        the answer."""
        movie_id = self._movie()
        token = f"device-a-{uuid.uuid4()}"
        first = self._upload("movies", movie_id, (10, 20, 30), client_id=token)
        second = self._upload("movies", movie_id, (10, 20, 30), client_id=token)

        self.assertEqual(first["mediaId"], second["mediaId"])
        self.assertEqual(len(self._live("movies", movie_id)), 1)
        self.assertEqual(self._live("movies", movie_id)[0]["clientId"], token)

    def test_the_token_is_set_once(self):
        """A second device matching the same photograph on content must not
        renumber a record the first device is still tracking."""
        movie_id = self._movie()
        first_token = f"device-a-{uuid.uuid4()}"
        self._upload("movies", movie_id, (10, 20, 30), client_id=first_token)
        self._upload("movies", movie_id, (10, 20, 30), client_id=f"device-b-{uuid.uuid4()}")

        images = self._live("movies", movie_id)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["clientId"], first_token)

    def test_the_ceiling_blocks_adding_and_never_hides_what_is_stored(self):
        """A gate may refuse the next image. It may not withhold one the user
        already has -- that is the rule the paid tiers are built on, and it is
        the one an implementation loses first because hiding a section is less
        work than disabling a write path."""
        movie_id = self._movie()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app_settings (key, value, is_secret) VALUES ('artwork_scan_limit_per_entity', '2'::jsonb, false)
                    ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
                    """
                )
            conn.commit()
        self.addCleanup(self._restore_limit)

        self._upload("movies", movie_id, (10, 20, 30))
        self._upload("movies", movie_id, (40, 50, 60))
        self._upload("movies", movie_id, (70, 80, 90), expect=409)

        images = self._live("movies", movie_id)
        self.assertEqual(len(images), 2)
        self.assertTrue(all(image["url"] for image in images))

    def _restore_limit(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app_settings (key, value, is_secret) VALUES ('artwork_scan_limit_per_entity', '10'::jsonb, false)
                    ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
                    """
                )
            conn.commit()

    def test_hiding_keeps_the_image_and_drops_only_its_place_on_the_page(self):
        movie_id = self._movie()
        media_id = self._upload("movies", movie_id, (10, 20, 30))["mediaId"]
        self.client.post(f"/api/next/movies/{movie_id}/images/{media_id}/primary")

        self.assertEqual(
            self.client.post(f"/api/next/movies/{movie_id}/images/{media_id}/hide").status_code, 200
        )
        hidden = self._live("movies", movie_id)[0]
        self.assertTrue(hidden["hidden"])
        self.assertFalse(hidden["isPrimary"])
        self.assertFalse(hidden["deleted"])
        # Still listed, so it can be brought back. A hidden image that vanished
        # from the read would be unreachable and therefore un-hideable.
        self.assertEqual(
            self.client.post(f"/api/next/movies/{movie_id}/images/{media_id}/unhide").status_code, 200
        )
        self.assertFalse(self._live("movies", movie_id)[0]["hidden"])

    def test_a_label_can_be_changed_after_the_upload(self):
        movie_id = self._movie()
        media_id = self._upload("movies", movie_id, (10, 20, 30), label="front")["mediaId"]
        self.assertEqual(self._live("movies", movie_id)[0]["label"], "front")

        response = self.client.patch(
            f"/api/next/movies/{movie_id}/images/{media_id}", json={"label": "Spine"}
        )
        self.assertEqual(response.status_code, 200, response.data[:200])
        self.assertEqual(self._live("movies", movie_id)[0]["label"], "spine")

    # --- what the other side of the wire sees -------------------------------

    def test_every_write_publishes_the_whole_set_as_one_change(self):
        """A snapshot rather than a per-image event.

        This is what makes the last writer win without a field-level merge:
        whoever wrote last wrote the snapshot everybody receives, so duplicated
        or out-of-order delivery still converges.
        """
        movie_id = self._movie()
        self._upload("movies", movie_id, (10, 20, 30))
        self._upload("movies", movie_id, (40, 50, 60))

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT revision, payload
                    FROM sync_changes
                    WHERE entity_type='movie_scans' AND entity_id=%s
                    ORDER BY revision DESC
                    LIMIT 1
                    """,
                    (str(movie_id),),
                )
                change = cur.fetchone()
        self.assertIsNotNone(change, "no movie_scans change was published")
        self.assertEqual(len(change["payload"]["images"]), 2)
        self.assertEqual(change["payload"]["entityType"], "movie")

    def test_a_delete_reaches_the_wire_as_a_tombstone(self):
        """An absence would read as "not received yet" and the receiving device
        would put the image straight back."""
        movie_id = self._movie()
        media_id = self._upload("movies", movie_id, (10, 20, 30))["mediaId"]
        self.client.delete(f"/api/next/movies/{movie_id}/images/{media_id}")

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT payload
                    FROM sync_changes
                    WHERE entity_type='movie_scans' AND entity_id=%s
                    ORDER BY revision DESC
                    LIMIT 1
                    """,
                    (str(movie_id),),
                )
                payload = cur.fetchone()["payload"]
        self.assertEqual(len(payload["images"]), 1)
        self.assertTrue(payload["images"][0]["deleted"])

    def test_the_bootstrap_carries_own_images_and_leaves_tombstones_out(self):
        """A snapshot is the whole truth for a client that holds nothing, and a
        record of things that no longer exist means nothing to it."""
        movie_id = self._movie()
        kept = self._upload("movies", movie_id, (10, 20, 30))["mediaId"]
        gone = self._upload("movies", movie_id, (40, 50, 60))["mediaId"]
        self.client.delete(f"/api/next/movies/{movie_id}/images/{gone}")

        bootstrap = self.client.get("/api/next/sync/bootstrap").get_json()
        self.assertIn("ownImages", bootstrap["payload"])
        self.assertIn("ownImages", bootstrap["collections"])
        entry = next(
            item for item in bootstrap["payload"]["ownImages"] if item["entityId"] == str(movie_id)
        )
        self.assertEqual([image["mediaId"] for image in entry["images"]], [kept])

    def test_a_queued_decision_replays_without_becoming_a_second_change(self):
        """The offline queue's half of the contract. A device that hid an image
        while offline offers the mutation again after a dropped connection, and
        the idempotency key is what stops the retry counting twice."""
        movie_id = self._movie()
        media_id = self._upload("movies", movie_id, (10, 20, 30))["mediaId"]
        mutation = {
            "clientMutationId": str(uuid.uuid4()),
            "entityType": "ownImage",
            "operation": "update",
            "payload": {
                "entityType": "movie",
                "entityId": str(movie_id),
                "mediaId": media_id,
                "hidden": True,
            },
        }
        body = {"clientId": f"{PREFIX}-device", "mutations": [mutation]}
        first = self.client.post("/api/next/sync/mutations", json=body)
        self.assertEqual(first.status_code, 200, first.data[:300])
        second = self.client.post("/api/next/sync/mutations", json=body)
        self.assertEqual(second.status_code, 200, second.data[:300])
        self.assertTrue(second.get_json()["results"][0].get("replayed"))
        self.assertTrue(self._live("movies", movie_id)[0]["hidden"])

    def test_a_queued_delete_of_an_already_deleted_image_is_a_success(self):
        """Two devices may remove the same image, and a queued delete may be
        offered again. Both must land on the state the caller asked for rather
        than on a 404 that a queue has no way to clear."""
        movie_id = self._movie()
        media_id = self._upload("movies", movie_id, (10, 20, 30))["mediaId"]
        self.client.delete(f"/api/next/movies/{movie_id}/images/{media_id}")

        response = self.client.post(
            "/api/next/sync/mutations",
            json={
                "clientId": f"{PREFIX}-device",
                "mutations": [
                    {
                        "clientMutationId": str(uuid.uuid4()),
                        "entityType": "ownImage",
                        "operation": "delete",
                        "payload": {
                            "entityType": "movie",
                            "entityId": str(movie_id),
                            "mediaId": media_id,
                        },
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.data[:300])
        self.assertEqual(response.get_json()["results"][0]["status"], "applied")

    # --- the other two entities --------------------------------------------

    def test_a_container_and_a_series_carry_their_own_images_too(self):
        """Registered in a loop precisely so this cannot drift: the gap that
        would otherwise appear is "images work on films and not on box sets",
        and nothing fails to say so."""
        container_id = self._container()
        series_id = self._series()

        self._upload("containers", container_id, (10, 20, 30), label="front")
        self._upload("series", series_id, (40, 50, 60), label="spine")

        self.assertEqual(len(self._live("containers", container_id)), 1)
        self.assertEqual(len(self._live("series", series_id)), 1)

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT entity_type FROM entity_media
                    WHERE role='scan' AND entity_id IN (%s, %s)
                    ORDER BY entity_type
                    """,
                    (container_id, series_id),
                )
                # `entity_type` is unconstrained text, so a typo would insert
                # cleanly, report success, and never be read back by anything.
                self.assertEqual(
                    [row["entity_type"] for row in cur.fetchall()], ["container", "series"]
                )

    def test_an_own_image_never_becomes_the_shelf_cover(self):
        """`isPrimary` here means "the one that stands for the user's own
        photographs", not "the poster".

        The pin behind the shelf cover is already one stored truth spelled four
        ways across the server, the wire and the clients. A fifth writer is
        exactly the mistake that rule exists to prevent.
        """
        movie_id = self._movie()
        media_id = self._upload("movies", movie_id, (10, 20, 30))["mediaId"]
        self.client.post(f"/api/next/movies/{movie_id}/images/{media_id}/primary")

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT metadata FROM movies WHERE id=%s", (movie_id,))
                metadata = cur.fetchone()["metadata"] or {}
        self.assertNotIn("poster_url", metadata)
        self.assertNotIn("poster_locked", metadata)


if __name__ == "__main__":
    unittest.main()
