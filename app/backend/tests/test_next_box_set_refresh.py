"""A box set can be refreshed as a whole: every member follows the release the box names for it.

The case behind it: the Italian *Rocky I-IV 4K Knockout Collection* (barcode 5051891191549) was
scanned while MovieVault still called one disc a Blu-ray. MovieVault was corrected, but the member
had been imported without its release id, so its refresh asked MovieVault about a release it could
not name and reported success. The format never arrived.

A later server showed two more problems. The member Rocky I held one MovieVault id, the stale release of
a single-disc Blu-ray, written when the box was imported from a catalogue copy that was not yet current.
The box in the catalogue names another release. The refresh did not move it: the first version asked for
the box proposal without `force`, so it read the answer the lookup cache had stored for six hours, which
still named the stale release, and counted the member as already linked. The box page said "0 linked, 0
not matched". The route now forces the lookup. And because the box names its own releases, the refresh
removes every other MovieVault id of a matched member (`repointed`) instead of reporting a conflict.

These tests pin five things:

* the matching: what is paired with what, inside one box, and what is left alone;
* the identity outcome per member, including the one that must not be guessed (`no_release_id`: no
  id is ever removed without a replacement) and the one that replaces (`repointed`);
* that the refresh writes MovieVault `movie_id` identifiers and queues jobs and does nothing else. In
  particular it never writes a format: the queued refresh applies the format rule of identity-routing
  5a, and a second copy of that rule here would drift from it;
* that a re-point is undoable: the ids it replaced come back in the result;
* that the route's lookup bypasses the lookup cache, and why that is needed.
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
    OUTCOME_IDENTIFIED,
    OUTCOME_NO_RELEASE_ID,
    OUTCOME_REPOINTED,
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

    def test_a_release_outside_the_box_is_replaced_by_the_boxs_own(self) -> None:
        # Rocky I on a real server: the id of a single-disc Blu-ray, not one of the box's four releases.
        other = "99999999-0000-0000-0000-000000000009"
        library = [stored(1, "Rocky", other), stored(2, "Rocky II"), stored(3, "Rocky III"), stored(4, "Rocky IV")]
        plan = plan_member_repairs(library, ROCKY_PROPOSAL)
        self.assertEqual(plan.decisions[0].outcome, OUTCOME_REPOINTED)
        self.assertEqual(plan.decisions[0].release_id, RELEASE[1])
        self.assertEqual(plan.decisions[0].previous_release_ids, (other,))
        self.assertIn(OUTCOME_REPOINTED, refresh.REFRESHABLE_OUTCOMES)

    def test_a_stale_id_beside_the_right_one_is_removed_and_the_right_one_kept(self) -> None:
        other = "99999999-0000-0000-0000-000000000009"
        plan = plan_member_repairs([stored(1, "Rocky", other, RELEASE[1])], [proposed(1, "Rocky"), proposed(2, "Rocky II")])
        decision = plan.decisions[0]
        self.assertEqual(decision.outcome, OUTCOME_REPOINTED)
        self.assertEqual(decision.previous_release_ids, (other,))

    def test_the_refresh_would_have_asked_about_the_stale_id_first(self) -> None:
        # The mechanism behind "already linked, still wrong": identifiers are read in
        # `provider_id, identifier_type, identifier` order and the first MovieVault one is used. The
        # stale Blu-ray release sorts before the box's release, so holding the right id was not enough.
        from app.backend.dedup_identity import select_movievault_identifier

        stale, right = "265825d0-b52b-4014-b32b-9568d3fbd1fa", "5c8b5b9e-3323-4d82-821b-f25f29dabcd2"
        rows = [
            {"provider_id": PROVIDER, "identifier_type": "movie_id", "identifier": value}
            for value in sorted([right, stale])
        ]
        self.assertEqual(select_movievault_identifier(rows), stale)
        plan = plan_member_repairs([stored(1, "Rocky", right, stale)], [proposed(1, "Rocky", right), proposed(2, "Rocky II")])
        self.assertEqual(plan.decisions[0].outcome, OUTCOME_REPOINTED)
        self.assertEqual(plan.decisions[0].previous_release_ids, (stale,))

    def test_every_stale_id_is_listed(self) -> None:
        one, two = "99999999-0000-0000-0000-000000000001", "99999999-0000-0000-0000-000000000002"
        plan = plan_member_repairs([stored(1, "Rocky", one, two)], [proposed(1, "Rocky"), proposed(2, "Rocky II")])
        self.assertEqual(plan.decisions[0].previous_release_ids, (one, two))

    def test_nothing_is_replaced_when_the_source_names_no_release(self) -> None:
        # An id is never removed without a replacement.
        other = "99999999-0000-0000-0000-000000000009"
        plan = plan_member_repairs([stored(1, "Rocky", other)], [ProposedMember(1, "Rocky"), ProposedMember(2, "Rocky II")])
        decision = plan.decisions[0]
        self.assertEqual(decision.outcome, OUTCOME_NO_RELEASE_ID)
        self.assertEqual(decision.previous_release_ids, ())

    def test_a_member_the_box_cannot_be_matched_to_keeps_its_id(self) -> None:
        other = "99999999-0000-0000-0000-000000000009"
        plan = plan_member_repairs([stored(1, "Something else", other)], ROCKY_PROPOSAL)
        decision = plan.decisions[0]
        self.assertEqual(decision.outcome, OUTCOME_UNMATCHED)
        self.assertEqual(decision.previous_release_ids, ())

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
        other = "99999999-0000-0000-0000-000000000009"
        conn = FakeConnection(box_set_container(), [member_row(1, "Rocky", other), member_row(2, "Rocky II")])
        self.run_refresh(conn, self.source()[:2])
        writes = [sql for sql, _ in conn.statements if sql.split(" ", 1)[0] in {"UPDATE", "DELETE", "INSERT"}]
        self.assertTrue(writes, "expected the identifier writes")
        for sql in writes:
            # The only tables it writes, and only the MovieVault `movie_id` rows of them.
            self.assertTrue(
                sql.startswith("INSERT INTO movie_identifiers") or sql.startswith("DELETE FROM movie_identifiers"), sql
            )
            self.assertFalse(sql.startswith("UPDATE"), sql)
        for sql, params in conn.statements:
            if sql.startswith("DELETE FROM movie_identifiers"):
                self.assertIn("identifier_type = 'movie_id'", sql)
                self.assertEqual(params[1], PROVIDER)
        self.assertFalse(any("format" in sql.lower() for sql in writes))

    def test_a_member_that_points_outside_the_box_is_repointed_refreshed_and_announced_once(self) -> None:
        other = "99999999-0000-0000-0000-000000000009"
        conn = FakeConnection(
            box_set_container(),
            [member_row(1, "Rocky", other), member_row(2, "Rocky II", RELEASE[2]), member_row(3, "Rocky III", RELEASE[3]), member_row(4, "Rocky IV", RELEASE[4])],
        )
        result, calls = self.run_refresh(conn, self.source())
        self.assertEqual(result["repointed"], 1)
        self.assertEqual(result["identified"], 0)
        self.assertEqual(result["counts"][OUTCOME_REPOINTED], 1)
        self.assertEqual(result["counts"][OUTCOME_ALREADY_IDENTIFIED], 3)
        # Every matched member is refreshed from MovieVault, the re-pointed one included.
        self.assertEqual(result["jobsQueued"], 4)
        self.assertEqual(sorted(payload["movieId"] for _, payload in calls["jobs"]), [f"movie-{n}" for n in (1, 2, 3, 4)])
        # One sync change, for the member whose identifiers changed.
        self.assertEqual(calls["emitted"], [("movie-1", "upsert")])
        deletes = [params for sql, params in conn.statements if sql.startswith("DELETE FROM movie_identifiers")]
        self.assertEqual(deletes, [("movie-1", PROVIDER, [other])])
        inserts = [params for sql, params in conn.statements if sql.startswith("INSERT INTO movie_identifiers")]
        self.assertEqual(inserts, [("movie-1", PROVIDER, RELEASE[1])])
        # The delete comes before the insert, so the member never holds two ids for a moment.
        order = [sql.split(" ", 1)[0] for sql, _ in conn.statements if sql.startswith(("DELETE FROM movie_identifiers", "INSERT INTO movie_identifiers"))]
        self.assertEqual(order, ["DELETE", "INSERT"])
        member = result["members"][0]
        self.assertEqual((member["outcome"], member["releaseId"], member["previousReleaseIds"]), (OUTCOME_REPOINTED, RELEASE[1], [other]))

    def test_a_correct_set_is_refreshed_without_writing_any_identifier(self) -> None:
        conn = FakeConnection(
            box_set_container(),
            [member_row(n, t, RELEASE[n]) for n, t in ((1, "Rocky"), (2, "Rocky II"), (3, "Rocky III"), (4, "Rocky IV"))],
        )
        result, calls = self.run_refresh(conn, self.source())
        self.assertEqual(result["jobsQueued"], 4)
        self.assertEqual(calls["emitted"], [])
        self.assertFalse(any(sql.split(" ", 1)[0] in {"INSERT", "DELETE", "UPDATE"} for sql, _ in conn.statements))

    def test_a_member_with_a_refresh_already_pending_is_not_queued_twice(self) -> None:
        conn = FakeConnection(
            box_set_container(),
            [member_row(1, "Rocky", RELEASE[1]), member_row(2, "Rocky II", RELEASE[2])],
            pending={"movie-1"},
        )
        result, calls = self.run_refresh(conn, self.source()[:2])
        self.assertEqual(result["identified"], 0)
        self.assertEqual(result["repointed"], 0)
        self.assertEqual(result["jobsQueued"], 1)
        self.assertEqual([payload["movieId"] for _, payload in calls["jobs"]], ["movie-2"])
        self.assertEqual(calls["emitted"], [])

    def test_unmatched_members_are_reported_and_not_queued_and_keep_their_id(self) -> None:
        other = "99999999-0000-0000-0000-000000000009"
        conn = FakeConnection(
            box_set_container(),
            [member_row(1, "Rocky", other), member_row(2, "Something else", other), member_row(3, "Rocky III")],
        )
        result, calls = self.run_refresh(conn, self.source())
        self.assertEqual(result["counts"][OUTCOME_REPOINTED], 1)
        self.assertEqual(result["counts"][OUTCOME_UNMATCHED], 1)
        self.assertEqual(result["counts"][OUTCOME_IDENTIFIED], 1)
        self.assertEqual(result["jobsQueued"], 2)
        self.assertEqual(result["proposalOnlyTitles"], ["Rocky II", "Rocky IV"])
        # Only the matched member lost an id; the unmatched one still holds its own.
        deletes = [params[0] for sql, params in conn.statements if sql.startswith("DELETE FROM movie_identifiers")]
        self.assertEqual(deletes, ["movie-1"])

    def test_a_source_without_releases_removes_nothing(self) -> None:
        other = "99999999-0000-0000-0000-000000000009"
        conn = FakeConnection(box_set_container(), [member_row(1, "Rocky", other), member_row(2, "Rocky II", other)])
        result, calls = self.run_refresh(conn, [{"position": 1, "title": "Rocky"}, {"position": 2, "title": "Rocky II"}])
        self.assertEqual(result["counts"][OUTCOME_NO_RELEASE_ID], 2)
        self.assertEqual(result["jobsQueued"], 0)
        self.assertFalse(any(sql.split(" ", 1)[0] in {"INSERT", "DELETE", "UPDATE"} for sql, _ in conn.statements))

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


class LookupCacheTests(unittest.TestCase):
    """The route must ask the catalogue, not the cache.

    The cache answers `search_barcode` and `box_set_candidates` for six hours. A box set refreshed
    from an answer cached before the catalogue copy was current keeps the stale release of a member
    and still reports success.
    """

    def test_the_cache_serves_a_stale_answer_unless_the_lookup_is_forced(self) -> None:
        from unittest import mock

        from app.backend import next_metadata as metadata

        stale = {"status": "ok", "result": {"boxSet": {"members": [{"releaseId": "stale"}]}}}
        fresh = {"status": "ok", "result": {"boxSet": {"members": [{"releaseId": "fresh"}]}}}
        for entrypoint in ("box_set_candidates", "search_barcode"):
            self.assertIn(entrypoint, metadata.CACHEABLE_LOOKUP_ENTRYPOINTS)
            with mock.patch.object(metadata, "read_metadata_lookup_cache", return_value=stale), \
                    mock.patch.object(metadata, "write_metadata_lookup_cache"), \
                    mock.patch.object(metadata, "release_read_transaction"), \
                    mock.patch.object(metadata, "run_plugin_entrypoint", return_value=fresh) as asked:
                unforced = metadata.run_cached_plugin_entrypoint(object(), "movievault_v2", entrypoint, {"barcode": "1"}, {})
                forced = metadata.run_cached_plugin_entrypoint(object(), "movievault_v2", entrypoint, {"barcode": "1"}, {}, force=True)
            self.assertIs(unforced, stale, entrypoint)
            self.assertIs(forced, fresh, entrypoint)
            self.assertEqual(asked.call_count, 1, "only the forced call reached the plugin")

    def test_the_box_set_route_forces_its_lookup(self) -> None:
        source = open(os.path.join(repo_root, "app", "backend", "next_app.py"), encoding="utf-8").read()
        start = source.index("def container_box_set_refresh(")
        end = source.index('@flask_app.get("/api/next/containers/<container_id>/view")', start)
        route = source[start:end]
        call = route[route.index("lookup_metadata_sources("):]
        call = call[: call.index(")") + 1]
        self.assertIn("force=True", call)


class PwaWiringTests(unittest.TestCase):
    """The existing "Refresh metadata" button of a box set also calls the new route."""

    def test_the_container_refresh_calls_the_box_set_route_and_reports_the_counts(self) -> None:
        from app.backend import next_views_ui

        source = open(next_views_ui.__file__, encoding="utf-8").read()
        self.assertIn("/box-set/refresh", source)
        self.assertIn("containerDetail.boxSetRefreshResult", source)
        # The count of linked members includes the re-pointed ones, and a conflict no longer exists.
        self.assertIn("counts.repointed", source)
        self.assertNotIn("counts.conflict", source)
        # The container refresh has already succeeded, so a set that cannot be matched is not an error.
        for code in ("box_set_refresh_no_barcode", "box_set_refresh_no_proposal", "box_set_refresh_not_a_box_set"):
            self.assertIn(code, source)


if __name__ == "__main__":
    unittest.main()
