"""Box-set barcodes: approximate matching, the release check, and the merge.

Three claims, each pinned on its own:

1. ``barcode_match_keys`` treats two printed codes as one product only for
   leading zeros (UPC-A / EAN-13 / GTIN-14) and a missing check digit -- and
   never lets two *valid* but different codes collide.
2. ``find_box_set_release_barcode_overlaps`` finds a box-set whose barcode also
   sits on a release, and says which of the two failure modes it is.
3. The admin "Scan & merge" offers and merges box-sets sharing approximately
   one barcode, re-hangs their members, tombstones the loser and publishes the
   winner's new member list -- without folding together box-sets of different
   owners, types or (conflicting) barcodes.

The pure cases run everywhere; the PostgreSQL cases need ``DATABASE_URL`` and
run in the smoke workflow below the migrations.
"""

import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_auth  # noqa: E402
from app.backend.admin_dedup_reports import (  # noqa: E402
    apply_winner_selections,
    canonical_report_payload,
    collection_fingerprint,
)
from app.backend.dedup_identity import (  # noqa: E402
    barcode_match_keys,
    barcodes_conflict,
    barcodes_match,
)
from app.backend.scripts import sync_dedup_merge as merge  # noqa: E402

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:  # pragma: no cover - minimal environments
    psycopg = None
    dict_row = None

DATABASE_URL = os.environ.get("DATABASE_URL")


def with_check_digit(body: str) -> str:
    total = sum(
        int(digit) * (3 if index % 2 == 0 else 1)
        for index, digit in enumerate(reversed(body))
    )
    return body + str((10 - total % 10) % 10)


UPC_A = with_check_digit("01234567890")  # 12 digits, valid
EAN_PADDED = "0" + UPC_A  # the same product as EAN-13
OTHER_EAN = with_check_digit("871234567890")  # a different, valid EAN-13


class BarcodeMatchKeyTests(unittest.TestCase):
    def test_leading_zeros_name_the_same_product(self):
        self.assertTrue(barcodes_match(UPC_A, EAN_PADDED))
        self.assertTrue(barcodes_match(UPC_A, "00" + UPC_A))

    def test_formatting_is_ignored(self):
        spaced = f"{EAN_PADDED[:1]} {EAN_PADDED[1:7]}-{EAN_PADDED[7:]}"
        self.assertTrue(barcodes_match(spaced, EAN_PADDED))

    def test_a_missing_check_digit_still_matches(self):
        self.assertTrue(barcodes_match(OTHER_EAN[:-1], OTHER_EAN))
        self.assertFalse(barcodes_conflict(OTHER_EAN[:-1], OTHER_EAN))

    def test_two_different_valid_codes_never_collide(self):
        self.assertFalse(barcodes_match(UPC_A, OTHER_EAN))
        self.assertTrue(barcodes_conflict(UPC_A, OTHER_EAN))
        # A valid code is not re-read as "missing its check digit": appending
        # one would otherwise let a 12-digit UPC meet an unrelated EAN-13.
        self.assertEqual(barcode_match_keys(UPC_A), frozenset({UPC_A.lstrip("0")}))

    def test_absent_barcodes_are_inconclusive(self):
        self.assertEqual(barcode_match_keys(None), frozenset())
        self.assertEqual(barcode_match_keys("n/a"), frozenset())
        self.assertFalse(barcodes_match(None, UPC_A))
        self.assertFalse(barcodes_conflict(None, UPC_A))


def _container(cid, **kwargs):
    row = {
        "id": cid,
        "title": f"Box {cid}",
        "year": None,
        "barcode": None,
        "container_type": "box_set",
        "owner_id": None,
        "movie_count": 1,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    row.update(kwargs)
    return row


class ContainerGroupDetectionTests(unittest.TestCase):
    def _detect(self, rows):
        with mock.patch.object(merge, "_fetch_live_containers", return_value=rows):
            groups, _by_id = merge.detect_container_groups(mock.MagicMock())
        return groups

    def test_box_sets_with_approximately_the_same_barcode_group(self):
        groups = self._detect(
            [_container("a", barcode=UPC_A), _container("b", barcode=EAN_PADDED)]
        )
        self.assertEqual(list(groups["barcode"].values()), [["a", "b"]])
        by_id = {"a": {"barcode": UPC_A}, "b": {"barcode": EAN_PADDED}}
        self.assertEqual(merge.container_barcode_match(["a", "b"], by_id), "approximate")

    def test_a_check_digit_less_code_links_its_box_sets(self):
        groups = self._detect(
            [
                _container("a", barcode=OTHER_EAN),
                _container("b", barcode=OTHER_EAN[:-1]),
                _container("c", barcode=UPC_A),
            ]
        )
        self.assertEqual(list(groups["barcode"].values()), [["a", "b"]])

    def test_different_owners_are_never_merged(self):
        groups = self._detect(
            [
                _container("a", barcode=UPC_A, owner_id="user-1"),
                _container("b", barcode=UPC_A, owner_id="user-2"),
            ]
        )
        self.assertEqual(groups["barcode"], {})

    def test_a_box_set_and_a_vault_never_share_a_group(self):
        groups = self._detect(
            [
                _container("a", barcode=UPC_A),
                _container("b", barcode=UPC_A, container_type="vault"),
            ]
        )
        self.assertEqual(groups["barcode"], {})

    def test_same_title_with_conflicting_barcodes_is_not_a_duplicate(self):
        groups = self._detect(
            [
                _container("a", title="The Harry Potter Collection", barcode=UPC_A),
                _container("b", title="The Harry Potter Collection", barcode=OTHER_EAN),
            ]
        )
        self.assertEqual(groups["titleYear"], {})

    def test_same_title_without_barcode_on_one_side_still_groups(self):
        groups = self._detect(
            [
                _container("a", title="Marvel Phase One", barcode=UPC_A),
                _container("b", title="Marvel Phase One"),
            ]
        )
        self.assertEqual(len(groups["titleYear"]), 1)


class CanonicalContainerReportTests(unittest.TestCase):
    MOVIE_GROUP = {
        "tier": "barcode",
        "winner": "m1",
        "losers": ["m2"],
        "members": [{"id": "m1"}, {"id": "m2"}],
    }
    CONTAINER_GROUP = {
        "tier": "barcode",
        "match": "approximate",
        "winner": "c1",
        "losers": ["c2"],
        "members": [
            {"id": "c1", "score_breakdown": {"total_score": 4}},
            {"id": "c2", "score_breakdown": {"total_score": 1}},
        ],
    }

    def test_container_groups_get_a_namespaced_id_and_counts(self):
        payload = canonical_report_payload(
            {"groups": [self.MOVIE_GROUP], "container_groups": [self.CONTAINER_GROUP]}
        )
        container_group = payload["container_groups"][0]
        self.assertTrue(container_group["group_id"])
        self.assertNotEqual(container_group["group_id"], payload["groups"][0]["group_id"])
        self.assertEqual(payload["container_group_count"], 1)
        self.assertEqual(payload["containers_to_tombstone"], 1)

    def test_movie_only_fingerprint_is_unchanged_by_an_empty_container_list(self):
        movie_only = {"groups": [self.MOVIE_GROUP]}
        with_empty = {"groups": [self.MOVIE_GROUP], "container_groups": []}
        self.assertEqual(collection_fingerprint(movie_only), collection_fingerprint(with_empty))

    def test_container_state_changes_the_fingerprint(self):
        base = {"groups": [], "container_groups": [self.CONTAINER_GROUP]}
        changed_group = {
            **self.CONTAINER_GROUP,
            "members": [
                {"id": "c1", "score_breakdown": {"total_score": 4}, "state_hash": "x"},
                {"id": "c2", "score_breakdown": {"total_score": 1}},
            ],
        }
        changed = {"groups": [], "container_groups": [changed_group]}
        self.assertNotEqual(collection_fingerprint(base), collection_fingerprint(changed))

    def test_admin_can_pick_the_box_set_winner(self):
        payload = canonical_report_payload(
            {"groups": [], "container_groups": [self.CONTAINER_GROUP]}
        )
        group_id = payload["container_groups"][0]["group_id"]
        selected = apply_winner_selections(
            payload, [{"groupId": group_id, "winnerId": "c2"}]
        )
        group = selected["container_groups"][0]
        self.assertEqual(group["winner"], "c2")
        self.assertEqual(group["losers"], ["c1"])
        self.assertEqual(group["winner_score"], 1)


class MergeCountTests(unittest.TestCase):
    def test_pair_and_legacy_integer_are_both_read(self):
        self.assertEqual(next_auth._dedup_merge_counts((3, 2)), (3, 2))
        self.assertEqual(next_auth._dedup_merge_counts(4), (4, 0))
        self.assertEqual(next_auth._dedup_merge_counts(None), (0, 0))


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class BoxSetBarcodePostgresTests(unittest.TestCase):
    def connect(self, **kwargs):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, **kwargs)

    def setUp(self):
        self.tag = f"box-barcode-probe-{uuid.uuid4().hex[:12]}"
        # Unique-per-run barcodes, so parallel or repeated runs never collide
        # with each other or with the unique index on movies.barcode.
        digits = str(uuid.uuid4().int)[:10]
        self.box_code = with_check_digit("1" + digits)  # 12-digit valid UPC-A
        self.box_code_padded = "0" + self.box_code
        self.movie_ids = []
        self.container_ids = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        with self.connect(autocommit=True) as conn, conn.cursor() as cur:
            if self.container_ids:
                cur.execute(
                    "DELETE FROM sync_changes WHERE entity_id = ANY(%s)",
                    (self.container_ids,),
                )
                cur.execute("DELETE FROM containers WHERE id = ANY(%s::uuid[])", (self.container_ids,))
            if self.movie_ids:
                cur.execute("DELETE FROM movies WHERE id = ANY(%s::uuid[])", (self.movie_ids,))

    def _movie(self, cur, title, barcode=None):
        cur.execute(
            """
            INSERT INTO movies (public_id, title, barcode)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (f"{self.tag}-{uuid.uuid4().hex[:8]}", f"{self.tag} {title}", barcode),
        )
        movie_id = str(cur.fetchone()["id"])
        self.movie_ids.append(movie_id)
        return movie_id

    def _box_set(self, cur, title, barcode, members=()):
        cur.execute(
            """
            INSERT INTO containers (public_id, container_type, title, barcode, owner_id)
            VALUES (%s, 'box_set', %s, %s, NULL)
            RETURNING id
            """,
            (f"{self.tag}-{uuid.uuid4().hex[:8]}", f"{self.tag} {title}", barcode),
        )
        container_id = str(cur.fetchone()["id"])
        self.container_ids.append(container_id)
        for order, movie_id in enumerate(members, start=1):
            cur.execute(
                "INSERT INTO container_movies (container_id, movie_id, sort_order) VALUES (%s, %s, %s)",
                (container_id, movie_id, order),
            )
        return container_id

    def test_release_check_tells_the_two_failure_modes_apart(self):
        from app.backend.next_box_set_barcodes import (
            KIND_BOX_SET_IMPORTED_AS_RELEASE,
            KIND_MEMBER_CARRIES_BOX_BARCODE,
            find_box_set_release_barcode_overlaps,
        )

        with self.connect(autocommit=True) as conn, conn.cursor() as cur:
            # A member film that was given the box's barcode (padded variant).
            member = self._movie(cur, "Part One", barcode=self.box_code_padded)
            # The box itself, also added as if it were one film.
            stray = self._movie(cur, "Trilogy (as a film)")
            cur.execute(
                """
                INSERT INTO movie_product_identifiers (movie_id, identifier_type, identifier_value)
                VALUES (%s, 'upc', %s)
                """,
                (stray, self.box_code),
            )
            box = self._box_set(cur, "Trilogy", self.box_code, members=[member])
            overlaps = find_box_set_release_barcode_overlaps(conn)

        mine = [item for item in overlaps if item["containerId"] == box]
        self.assertEqual(len(mine), 1)
        by_release = {release["movieId"]: release for release in mine[0]["releases"]}
        self.assertEqual(set(by_release), {member, stray})
        self.assertEqual(by_release[member]["kind"], KIND_MEMBER_CARRIES_BOX_BARCODE)
        self.assertEqual(by_release[member]["match"], "approximate")
        self.assertEqual(by_release[stray]["kind"], KIND_BOX_SET_IMPORTED_AS_RELEASE)
        self.assertEqual(by_release[stray]["source"], "product_identifier")
        self.assertEqual(mine[0]["match"], "approximate")

    def test_admin_dashboard_surfaces_both_signals(self):
        from app.backend.next_app import (
            admin_operations_collection_health,
            admin_operations_duplicate_summary,
        )

        with self.connect(autocommit=True) as conn, conn.cursor() as cur:
            self._movie(cur, "Box as film", barcode=self.box_code)
            first = self._box_set(cur, "Dashboard box", self.box_code)
            second = self._box_set(cur, "Dashboard box again", self.box_code_padded)
            duplicates = admin_operations_duplicate_summary(conn)
            health = admin_operations_collection_health(conn, duplicates)

        titles = {f"{self.tag} Dashboard box", f"{self.tag} Dashboard box again"}
        box_groups = [
            group
            for group in duplicates["duplicateBoxSetBarcodes"]
            if set(group["titles"]) == titles
        ]
        self.assertEqual(len(box_groups), 1)
        self.assertEqual(box_groups[0]["match"], "approximate")
        # Both box-sets share their (approximate) barcode with the stray film.
        # The issue list is capped for the dashboard and the smoke database is
        # shared, so the count is what is asserted, not list membership.
        self.assertGreaterEqual(health["counts"]["boxSetBarcodeAsRelease"], 2)
        self.assertIn("boxSetBarcodeAsRelease", health["issues"])
        self.assertNotEqual(first, second)

    def test_scan_and_merge_folds_box_sets_with_approximately_one_barcode(self):
        with self.connect(autocommit=True) as conn, conn.cursor() as cur:
            shared = self._movie(cur, "Shared film")
            first = self._movie(cur, "First film")
            second = self._movie(cur, "Second film")
            rich = self._box_set(cur, "Saga", self.box_code, members=[shared, first])
            poor = self._box_set(cur, "Saga (rescan)", self.box_code_padded, members=[shared, second])
            collection = None
            cur.execute(
                """
                INSERT INTO containers (public_id, container_type, title)
                VALUES (%s, 'collection', %s)
                RETURNING id
                """,
                (f"{self.tag}-col", f"{self.tag} Shelf"),
            )
            collection = str(cur.fetchone()["id"])
            self.container_ids.append(collection)
            cur.execute(
                "INSERT INTO collection_items (collection_id, item_type, item_id) VALUES (%s, 'box_set', %s)",
                (collection, poor),
            )

        with self.connect() as conn:
            report = merge.build_report(conn, container_types=["box_set"])
            conn.rollback()
        mine = [
            group
            for group in report["container_groups"]
            if {member["id"] for member in group["members"]} == {rich, poor}
        ]
        self.assertEqual(len(mine), 1, report["container_groups"])
        group = mine[0]
        self.assertEqual(group["tier"], "barcode")
        self.assertEqual(group["match"], "approximate")
        self.assertEqual(group["winner"], rich)
        self.assertEqual(group["losers"], [poor])

        # Execute only this group: the shared smoke database holds other tests'
        # rows, and a full report would merge those too.
        scoped = {"groups": [], "container_groups": [group]}
        with self.connect() as conn:
            movies_done, containers_done = merge.execute_merge(conn, scoped)
        self.assertEqual((movies_done, containers_done), (0, 1))

        with self.connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("SELECT deleted_at FROM containers WHERE id = %s", (poor,))
            self.assertIsNotNone(cur.fetchone()["deleted_at"])
            cur.execute(
                "SELECT movie_id::text FROM container_movies WHERE container_id = %s",
                (rich,),
            )
            self.assertEqual({row["movie_id"] for row in cur.fetchall()}, {shared, first, second})
            cur.execute("SELECT count(*)::int AS n FROM container_movies WHERE container_id = %s", (poor,))
            self.assertEqual(cur.fetchone()["n"], 0)
            cur.execute(
                "SELECT item_id::text FROM collection_items WHERE collection_id = %s",
                (collection,),
            )
            self.assertEqual([row["item_id"] for row in cur.fetchall()], [rich])
            cur.execute(
                """
                SELECT entity_type, entity_id, operation, payload
                FROM sync_changes
                WHERE entity_id = ANY(%s)
                ORDER BY revision
                """,
                ([rich, poor, collection],),
            )
            changes = cur.fetchall()
        delete = [c for c in changes if c["entity_id"] == poor and c["operation"] == "delete"]
        self.assertEqual(len(delete), 1)
        self.assertEqual(delete[0]["payload"], {"mergedInto": rich})
        memberships = {
            c["entity_id"]: c["payload"]
            for c in changes
            if c["entity_type"] == "container_membership"
        }
        self.assertEqual(set(memberships), {rich, collection})
        self.assertEqual(
            {member["movie_id"] for member in memberships[rich]["members"]},
            {shared, first, second},
        )
        self.assertEqual(
            [member["child_container_id"] for member in memberships[collection]["members"]],
            [rich],
        )


if __name__ == "__main__":
    unittest.main()
