"""Two cuts of one film are two discs in the box.

IMDb files an alternate cut under the original title, so *The Godfather Coda:
The Death of Michael Corleone* and *The Godfather Part III* share one tt id
while TMDB gives them an entry each. Collapsing a box set's members on a single
shared key therefore imported the Godfather trilogy set as three films and left
nothing behind to say a fourth had been dropped.

These tests pin the rule that replaced it: a collision counts only when nothing
distinguishes the two members, and carrying the same kind of id with different
values is what distinguishes them.
"""

from __future__ import annotations

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app


def _titles(members):
    return [member["title"] for member in members]


class GodfatherTrilogyTests(unittest.TestCase):
    """The reported set: barcode 4020628667276, four discs, three imported."""

    MEMBERS = [
        {"title": "The Godfather", "year": "1972", "tmdbId": "238", "imdbId": "tt0068646"},
        {"title": "The Godfather: Part II", "year": "1974", "tmdbId": "240", "imdbId": "tt0071562"},
        {
            "title": "The Godfather, Coda: The Death of Michael Corleone",
            "year": "2020",
            "tmdbId": "719534",
            "imdbId": "tt0099674",
        },
        {"title": "The Godfather: Part III", "year": "1990", "tmdbId": "242", "imdbId": "tt0099674"},
    ]

    def test_all_four_members_survive(self):
        result = next_app.dedupe_box_set_members(self.MEMBERS)
        self.assertEqual(len(result), 4)
        self.assertIn("The Godfather: Part III", _titles(result))
        self.assertIn("The Godfather, Coda: The Death of Michael Corleone", _titles(result))

    def test_member_order_is_preserved(self):
        self.assertEqual(_titles(next_app.dedupe_box_set_members(self.MEMBERS)), _titles(self.MEMBERS))

    def test_the_shared_imdb_id_alone_no_longer_collapses_them(self):
        coda, part_three = self.MEMBERS[2], self.MEMBERS[3]
        self.assertTrue(
            next_app.box_set_member_dedupe_keys(coda) & next_app.box_set_member_dedupe_keys(part_three),
            "the two members must still collide on the shared IMDb id",
        )
        self.assertTrue(
            next_app.box_set_members_are_distinguished(
                next_app.box_set_member_strong_ids(coda),
                next_app.box_set_member_strong_ids(part_three),
            ),
            "differing TMDB ids must distinguish them",
        )


class StillCollapsesRealDuplicatesTests(unittest.TestCase):
    def test_identical_identifiers_collapse(self):
        members = [
            {"title": "The Godfather", "year": "1972", "tmdbId": "238", "imdbId": "tt0068646"},
            {"title": "The Godfather", "year": "1972", "tmdbId": "238", "imdbId": "tt0068646"},
        ]
        self.assertEqual(len(next_app.dedupe_box_set_members(members)), 1)

    def test_shared_id_collapses_when_nothing_distinguishes(self):
        """One side missing the other id is not evidence of a second film."""
        members = [
            {"title": "The Godfather Part III", "year": "1990", "imdbId": "tt0099674"},
            {"title": "The Godfather: Part III", "year": "1990", "tmdbId": "242", "imdbId": "tt0099674"},
        ]
        self.assertEqual(len(next_app.dedupe_box_set_members(members)), 1)

    def test_same_title_and_year_without_ids_collapses(self):
        members = [
            {"title": "The Godfather", "year": "1972"},
            {"title": "the godfather", "year": "1972"},
        ]
        self.assertEqual(len(next_app.dedupe_box_set_members(members)), 1)

    def test_same_title_without_year_collapses(self):
        members = [{"title": "Alien"}, {"title": "alien"}]
        self.assertEqual(len(next_app.dedupe_box_set_members(members)), 1)


class DistinguishedByIdTests(unittest.TestCase):
    def test_same_title_but_different_tmdb_ids_are_two_films(self):
        """A remake shares its title; the ids say they are not one disc."""
        members = [
            {"title": "Nosferatu", "tmdbId": "653"},
            {"title": "Nosferatu", "tmdbId": "426063"},
        ]
        self.assertEqual(len(next_app.dedupe_box_set_members(members)), 2)

    def test_same_title_and_year_but_different_imdb_ids_are_two_films(self):
        members = [
            {"title": "The Guest", "year": "2014", "imdbId": "tt2980592"},
            {"title": "The Guest", "year": "2014", "imdbId": "tt3319182"},
        ]
        self.assertEqual(len(next_app.dedupe_box_set_members(members)), 2)

    def test_a_third_member_is_compared_against_every_kept_member(self):
        """The pairwise comparison must not stop at the first kept member."""
        members = [
            {"title": "The Godfather", "year": "1972", "tmdbId": "238", "imdbId": "tt0068646"},
            {"title": "The Godfather Coda", "year": "2020", "tmdbId": "719534", "imdbId": "tt0099674"},
            {"title": "The Godfather Part III", "year": "1990", "tmdbId": "242", "imdbId": "tt0099674"},
            {"title": "The Godfather Part III", "year": "1990", "tmdbId": "242", "imdbId": "tt0099674"},
        ]
        result = next_app.dedupe_box_set_members(members)
        self.assertEqual(len(result), 3, "the exact repeat collapses, the two cuts do not")


class EdgeCaseTests(unittest.TestCase):
    def test_members_without_any_key_are_all_kept(self):
        members = [{"title": ""}, {"title": ""}]
        self.assertEqual(len(next_app.dedupe_box_set_members(members)), 2)

    def test_empty_list(self):
        self.assertEqual(next_app.dedupe_box_set_members([]), [])

    def test_snake_case_identifier_fields_are_read(self):
        members = [
            {"title": "Coda", "year": "2020", "tmdb_id": "719534", "imdb_id": "tt0099674"},
            {"title": "Part III", "year": "1990", "tmdb_id": "242", "imdb_id": "tt0099674"},
        ]
        self.assertEqual(len(next_app.dedupe_box_set_members(members)), 2)

    def test_identifier_case_is_ignored_when_collapsing(self):
        members = [
            {"title": "The Godfather", "year": "1972", "imdbId": "TT0068646"},
            {"title": "The Godfather", "year": "1972", "imdbId": "tt0068646"},
        ]
        self.assertEqual(len(next_app.dedupe_box_set_members(members)), 1)


class ProposalMemberListTests(unittest.TestCase):
    """The dedupe is reached through the one gate every proposal reader uses."""

    def test_proposal_member_list_keeps_both_cuts(self):
        proposal = {"members": GodfatherTrilogyTests.MEMBERS}
        self.assertEqual(len(next_app.box_set_proposal_member_list(proposal)), 4)


if __name__ == "__main__":
    unittest.main()
