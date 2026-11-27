"""A box set can be refreshed as a whole, and a member without a release is matched back to one.

The case behind it: the Italian *Rocky I-IV 4K Knockout Collection* (barcode 5051891191549) was
scanned while MovieVault still called one disc a Blu-ray. MovieVault was corrected, but the member
had been imported without its release id, so its refresh asked MovieVault about a release it could
not name and reported success. The format never arrived.

These tests pin three things:

* the matching: what is paired with what, inside one box, and what is left alone;
* the identity outcome per member, including the two that must not be guessed (`conflict`,
  `no_release_id`);
* that the refresh writes identifiers and queues jobs and does nothing else. In particular it never
  writes a format: the queued refresh applies the format rule of identity-routing 5a, and a second
  copy of that rule here would drift from it.
"""

from __future__ import annotations

import os
import sys
import unittest
from typing import Any
from uuid import UUID, uuid4


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_box_set_refresh as refresh
from app.backend.next_box_set_refresh import (
    OUTCOME_ALREADY_IDENTIFIED,
    OUTCOME_CONFLICT,
    OUTCOME_IDENTIFIED,
    OUTCOME_NO_RELEASE_ID,
    OUTCOME_UNMATCHED,
    ProposedMember,
    StoredMember,
    plan_member_repairs,
    proposed_members_from_source,
)
from app.backend.next_common import NextApiError

PROVIDER = "movievault_v2"
RELEASE = {
    1: "10000000-0000-0000-0000-000000000001",
    2: "10000000-0000-0000-0000-000000000002",
    3: "10000000-0000-0000-0000-000000000003",
    4: "10000000-0000-0000-0000-000000000004",
}


def stored(position: int, title: str, *release_ids: str) -> StoredMember:
    return StoredMember(movie_id=f"movie-{position}", title=title, sort_order=position, release_ids=tuple(release_ids))


def proposed(position: int, title: str, release_id: str = "") -> ProposedMember:
    return ProposedMember(position=position, title=title, release_id=release_id or RELEASE.get(position, ""))


ROCKY_PROPOSAL = [
    proposed(1, "Rocky"),
    proposed(2, "Rocky II"),
    proposed(3, "Rocky III"),
    proposed(4, "Rocky IV"),
]


class NormaliseTitleTests(unittest.TestCase):
    def test_case_accents_and_punctuation_do_not_matter(self) -> None:
        self.assertEqual(refresh.normalise_title("Léon: The Professional"), refresh.normalise_title("leon the professional"))

    def test_a_title_of_only_punctuation_matches_nothing(self) -> None:
        self.assertEqual(refresh.normalise_title("?!"), "")

    def test_non_latin_titles_survive(self) -> None:
        self.assertNotEqual(refresh.normalise_title("千と千尋の神隠し"), "")


class RealBarcodeTests(unittest.TestCase):
    def test_a_real_ean_passes_with_formatting_stripped(self) -> None:
        self.assertEqual(refresh.real_barcode("5051891191549"), "5051891191549")
        self.assertEqual(refresh.real_barcode("5051 8911-91549"), "5051891191549")

    def test_a_synthetic_import_barcode_is_not_one(self) -> None:
        # The digits of IMPORT-5050582369601-BOX-03 glued together are fifteen long: the real EAN with
        # the tail attached. The letters keep it out, and so would the length.
        self.assertEqual(refresh.real_barcode("IMPORT-5050582369601-BOX-03"), "")

    def test_wrong_length_and_empty_are_refused(self) -> None:
        self.assertEqual(refresh.real_barcode("12345"), "")
        self.assertEqual(refresh.real_barcode(None), "")


class PlanMemberRepairsTests(unittest.TestCase):
    def outcomes(self, plan) -> list[str]:
        return [decision.outcome for decision in plan.decisions]

    def test_members_without_a_release_id_are_identified_by_title(self) -> None:
        library = [stored(1, "Rocky"), stored(2, "Rocky II"), stored(3, "Rocky III"), stored(4, "Rocky IV")]
        plan = plan_member_repairs(library, ROCKY_PROPOSAL)
        self.assertEqual(self.outcomes(plan), [OUTCOME_IDENTIFIED] * 4)
        self.assertEqual([d.release_id for d in plan.decisions], [RELEASE[n] for n in (1, 2, 3, 4)])
        self.assertTrue(all(d.matched_by == refresh.MATCHED_BY_TITLE for d in plan.decisions))
        self.assertEqual(plan.proposal_only_titles, ())

    def test_a_member_that_already_names_its_release_is_left_alone_but_still_refreshable(self) -> None:
        library = [stored(1, "Rocky", RELEASE[1]), stored(2, "Rocky II"), stored(3, "Rocky III"), stored(4, "Rocky IV")]
        plan = plan_member_repairs(library, ROCKY_PROPOSAL)
        self.assertEqual(plan.decisions[0].outcome, OUTCOME_ALREADY_IDENTIFIED)
        self.assertIn(plan.decisions[0].outcome, refresh.REFRESHABLE_OUTCOMES)

    def test_a_different_stored_release_is_reported_not_overwritten(self) -> None:
        other = "99999999-0000-0000-0000-000000000009"
        library = [stored(1, "Rocky", other), stored(2, "Rocky II"), stored(3, "Rocky III"), stored(4, "Rocky IV")]
        plan = plan_member_repairs(library, ROCKY_PROPOSAL)
        self.assertEqual(plan.decisions[0].outcome, OUTCOME_CONFLICT)
        self.assertNotIn(OUTCOME_CONFLICT, refresh.REFRESHABLE_OUTCOMES)

    def test_a_source_that_names_no_release_cannot_identify_anyone(self) -> None:
        # The live resolver names members by title and position only. Matching them would be possible and
        # useless, and saying "identified" would be the success-that-did-nothing this module exists to end.
        library = [stored(1, "Rocky"), stored(2, "Rocky II")]
        plan = plan_member_repairs(library, [ProposedMember(1, "Rocky"), ProposedMember(2, "Rocky II")])
        self.assertEqual(self.outcomes(plan), [OUTCOME_NO_RELEASE_ID, OUTCOME_NO_RELEASE_ID])

    def test_a_localised_title_falls_back_to_position_when_the_counts_agree(self) -> None:
        library = [stored(1, "Rocky"), stored(2, "Rocky 2 (NL)"), stored(3, "Rocky III"), stored(4, "Rocky IV")]
        plan = plan_member_repairs(library, ROCKY_PROPOSAL)
        self.assertEqual(self.outcomes(plan), [OUTCOME_IDENTIFIED] * 4)
        self.assertEqual(plan.decisions[1].matched_by, refresh.MATCHED_BY_POSITION)
        self.assertEqual(plan.decisions[1].release_id, RELEASE[2])

    def test_position_is_not_used_when_the_counts_differ(self) -> None:
        library = [stored(1, "Rocky"), stored(2, "Rocky 2 (NL)"), stored(3, "Rocky III")]
        plan = plan_member_repairs(library, ROCKY_PROPOSAL)
        self.assertEqual(plan.decisions[1].outcome, OUTCOME_UNMATCHED)
        # What the source names and the library lacks is reported, never created.
        self.assertEqual(plan.proposal_only_titles, ("Rocky II", "Rocky IV"))

    def test_a_reordered_set_does_not_pair_a_leftover_with_a_taken_position(self) -> None:
        # B was moved to position 1 and A, whose stored title is localised, to position 2. B matches by
        # title and takes proposal position 2... the leftover A (sort_order 2) must not also claim it.
        library = [stored(1, "Rocky II"), stored(2, "Rocky (localised)")]
        plan = plan_member_repairs(library, [proposed(1, "Rocky"), proposed(2, "Rocky II")])
        self.assertEqual(plan.decisions[0].outcome, OUTCOME_IDENTIFIED)
        self.assertEqual(plan.decisions[0].release_id, RELEASE[2])
        self.assertEqual(plan.decisions[1].outcome, OUTCOME_UNMATCHED)

    def test_duplicate_titles_are_never_matched_by_title(self) -> None:
        library = [stored(1, "Alien"), stored(2, "Alien")]
        plan = plan_member_repairs(library, [proposed(1, "Alien"), proposed(2, "Alien")])
        # Equal counts, so position decides; each release is claimed exactly once.
        self.assertEqual([d.release_id for d in plan.decisions], [RELEASE[1], RELEASE[2]])
        self.assertTrue(all(d.matched_by == refresh.MATCHED_BY_POSITION for d in plan.decisions))

    def test_a_similar_title_is_not_a_match(self) -> None:
        plan = plan_member_repairs([stored(1, "Rocky Balboa")], [proposed(1, "Rocky"), proposed(2, "Rocky II")])
        self.assertEqual(plan.decisions[0].outcome, OUTCOME_UNMATCHED)


class ProposedMembersTests(unittest.TestCase):
    def test_all_three_spellings_of_the_release_id_are_read(self) -> None:
        members = proposed_members_from_source(
            [
                {"position": 1, "title": "A", "releaseId": "r1"},
                {"position": "2", "title": "B", "movieVaultId": "r2"},
                {"title": "C", "release_id": "r3"},
            ]
        )
        self.assertEqual([m.release_id for m in members], ["r1", "r2", "r3"])
        self.assertEqual([m.position for m in members], [1, 2, None])

    def test_a_member_without_a_title_is_dropped(self) -> None:
        self.assertEqual(proposed_members_from_source([{"position": 1}, "junk", None]), [])


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self.conn = conn
        self.rows: list[dict[str, Any]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        text = " ".join(sql.split())
        self.conn.statements.append((text, params))
        self.rows = []
        if text.startswith("SELECT id, container_type, title, barcode FROM containers"):
            self.rows = [dict(self.conn.container)] if self.conn.container else []
        elif "FROM container_movies cm" in text:
            self.rows = [dict(row) for row in self.conn.members]
        elif "to_regclass" in text:
            self.rows = [{"present": True}]
        elif text.startswith("SELECT EXISTS"):
            self.rows = [{"pending": params[1] in self.conn.pending_movie_ids}]

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.rows)


class FakeConnection:
    def __init__(self, container: dict[str, Any] | None, members: list[dict[str, Any]], pending: set[str] | None = None) -> None:
        self.container = container
        self.members = members
        self.pending_movie_ids = pending or set()
        self.statements: list[tuple[str, Any]] = []
        self.events: list[str] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.events.append("commit")


def member_row(position: int, title: str, *release_ids: str) -> dict[str, Any]:
    return {"movie_id": f"movie-{position}", "title": title, "sort_order": position, "release_ids": list(release_ids)}


def box_set_container(barcode: str | None = "5051891191549", kind: str = "box_set") -> dict[str, Any]:
    return {"id": uuid4(), "container_type": kind, "title": "Rocky I-IV", "barcode": barcode}


class RefreshBoxSetMembersTests(unittest.TestCase):
    def run_refresh(self, conn: FakeConnection, source_members: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[Any]]]:
        calls: dict[str, list[Any]] = {"lookup": [], "jobs": [], "emitted": []}

        def lookup(lookup_conn: Any, barcode: str, actor: Any) -> list[dict[str, Any]]:
            # The network call must not run inside the caller's transaction.
            lookup_conn.events.append("lookup")
            calls["lookup"].append(barcode)
            return source_members

        def create_job(job_conn: Any, *, job_type: str, payload: dict[str, Any]) -> None:
            calls["jobs"].append((job_type, payload))

        def emit(emit_conn: Any, movie_id: str, *, operation: str) -> None:
            calls["emitted"].append((movie_id, operation))

        result = refresh.refresh_box_set_members(
            conn,
            UUID(int=1),
            actor={"id": "actor"},
            lookup_members=lookup,
            create_job=create_job,
            emit_identifiers_change=emit,
            job_type="metadata.refresh",
            provider_id=PROVIDER,
        )
        return result, calls

    def source(self) -> list[dict[str, Any]]:
        return [{"position": n, "title": t, "releaseId": RELEASE[n]} for n, t in ((1, "Rocky"), (2, "Rocky II"), (3, "Rocky III"), (4, "Rocky IV"))]

    def test_it_identifies_members_and_queues_one_refresh_each(self) -> None:
        conn = FakeConnection(
            box_set_container(),
            [member_row(1, "Rocky"), member_row(2, "Rocky II"), member_row(3, "Rocky III"), member_row(4, "Rocky IV")],
        )
        result, calls = self.run_refresh(conn, self.source())
        self.assertEqual(calls["lookup"], ["5051891191549"])
        self.assertEqual(result["identified"], 4)
        self.assertEqual(result["jobsQueued"], 4)
        self.assertEqual(result["counts"][OUTCOME_IDENTIFIED], 4)
        self.assertEqual(sorted(movie for movie, _ in calls["emitted"]), [f"movie-{n}" for n in (1, 2, 3, 4)])
        inserts = [params for sql, params in conn.statements if sql.startswith("INSERT INTO movie_identifiers")]
        self.assertEqual([p[2] for p in inserts], [RELEASE[n] for n in (1, 2, 3, 4)])
        self.assertTrue(all(p[1] == PROVIDER for p in inserts))
        self.assertEqual({payload["reason"] for _, payload in calls["jobs"]}, {"box_set_refresh"})

    def test_the_transaction_is_closed_before_the_source_is_asked(self) -> None:
        conn = FakeConnection(box_set_container(), [member_row(1, "Rocky"), member_row(2, "Rocky II")])
        self.run_refresh(conn, self.source()[:2])
        self.assertLess(conn.events.index("commit"), conn.events.index("lookup"))

    def test_it_never_writes_a_format_or_touches_the_members_themselves(self) -> None:
        conn = FakeConnection(box_set_container(), [member_row(1, "Rocky"), member_row(2, "Rocky II")])
        self.run_refresh(conn, self.source()[:2])
        writes = [sql for sql, _ in conn.statements if sql.split(" ", 1)[0] in {"UPDATE", "DELETE", "INSERT"}]
        self.assertTrue(writes, "expected the identifier inserts")
        for sql in writes:
            self.assertTrue(sql.startswith("INSERT INTO movie_identifiers"), sql)
        self.assertFalse(any("format" in sql.lower() for sql in writes))

    def test_a_member_with_a_refresh_already_pending_is_not_queued_twice(self) -> None:
        conn = FakeConnection(
            box_set_container(),
            [member_row(1, "Rocky", RELEASE[1]), member_row(2, "Rocky II", RELEASE[2])],
            pending={"movie-1"},
        )
        result, calls = self.run_refresh(conn, self.source()[:2])
        self.assertEqual(result["identified"], 0)
        self.assertEqual(result["jobsQueued"], 1)
        self.assertEqual([payload["movieId"] for _, payload in calls["jobs"]], ["movie-2"])
        self.assertEqual(calls["emitted"], [])

    def test_unmatched_and_conflicting_members_are_reported_and_not_queued(self) -> None:
        conn = FakeConnection(
            box_set_container(),
            [member_row(1, "Rocky", "99999999-0000-0000-0000-000000000009"), member_row(2, "Something else"), member_row(3, "Rocky III")],
        )
        result, calls = self.run_refresh(conn, self.source())
        self.assertEqual(result["counts"][OUTCOME_CONFLICT], 1)
        self.assertEqual(result["counts"][OUTCOME_UNMATCHED], 1)
        self.assertEqual(result["counts"][OUTCOME_IDENTIFIED], 1)
        self.assertEqual(result["jobsQueued"], 1)
        self.assertEqual(result["proposalOnlyTitles"], ["Rocky II", "Rocky IV"])

    def test_a_container_that_is_not_a_box_set_is_refused_with_a_stable_code(self) -> None:
        conn = FakeConnection(box_set_container(kind="collection"), [])
        with self.assertRaises(NextApiError) as caught:
            self.run_refresh(conn, [])
        self.assertEqual((caught.exception.status_code, caught.exception.code), (422, refresh.ERROR_NOT_A_BOX_SET))
        self.assertEqual(conn.events, [], "nothing is looked up for a container it will refuse")

    def test_a_synthetic_or_missing_barcode_is_refused_before_any_lookup(self) -> None:
        for barcode in (None, "", "IMPORT-5050582369601-BOX-03"):
            conn = FakeConnection(box_set_container(barcode=barcode), [])
            with self.assertRaises(NextApiError) as caught:
                self.run_refresh(conn, [])
            self.assertEqual(caught.exception.code, refresh.ERROR_NO_BARCODE)
            self.assertNotIn("lookup", conn.events)

    def test_fewer_than_two_proposed_members_changes_nothing(self) -> None:
        conn = FakeConnection(box_set_container(), [member_row(1, "Rocky")])
        with self.assertRaises(NextApiError) as caught:
            self.run_refresh(conn, [{"position": 1, "title": "Rocky", "releaseId": RELEASE[1]}])
        self.assertEqual(caught.exception.code, refresh.ERROR_NO_PROPOSAL)
        self.assertFalse(any(sql.startswith("INSERT") for sql, _ in conn.statements))

    def test_an_unknown_container_is_a_404(self) -> None:
        with self.assertRaises(NextApiError) as caught:
            self.run_refresh(FakeConnection(None, []), [])
        self.assertEqual(caught.exception.status_code, 404)


class PwaWiringTests(unittest.TestCase):
    """The existing "Refresh metadata" button of a box set also calls the new route."""

    def test_the_container_refresh_calls_the_box_set_route_and_reports_the_counts(self) -> None:
        from app.backend import next_views_ui

        source = open(next_views_ui.__file__, encoding="utf-8").read()
        self.assertIn("/box-set/refresh", source)
        self.assertIn("containerDetail.boxSetRefreshResult", source)
        # The container refresh has already succeeded, so a set that cannot be matched is not an error.
        for code in ("box_set_refresh_no_barcode", "box_set_refresh_no_proposal", "box_set_refresh_not_a_box_set"):
            self.assertIn(code, source)


if __name__ == "__main__":
    unittest.main()
