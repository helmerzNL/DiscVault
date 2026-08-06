"""Import-time normalization of shop-style collection exports.

Blu-ray.com exports describe the *disc*, not the film: the format is glued to
the title, the date column is the pressing date, and there is no box-set column
at all. These tests pin the three rules that turn such a row into film data.
"""

import os
import sys
import unittest
from pathlib import Path


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_plugins._collection_import_base import (
    CollectionImportPlugin,
    detect_box_set_title,
    parse_release_date,
    split_title_format_token,
    sum_disc_counts,
)
from app.backend.next_plugins.import_bluray_com.plugin import SOURCE as BLURAY_SOURCE


SOURCE_FILE = Path("bluray_collection.csv")


def bluray_plugin() -> CollectionImportPlugin:
    return CollectionImportPlugin(BLURAY_SOURCE)


def bluray_row(**overrides):
    row = {
        "Title": "Basic Instinct 4K",
        "Studio": "Studio Canal",
        "Country code": "FR",
        "UPC": "",
        "EAN": "5053083230142",
        "Release date": "March 1 2023",
        "Casing": "Standard Blu-ray case",
        "Blu-ray discs": "1",
        "DVD discs": "0",
        "Date added": "November 26 2025",
        "Watched": "0",
    }
    row.update(overrides)
    return row


class TitleFormatTokenTests(unittest.TestCase):
    def test_trailing_4k_moves_from_title_to_format(self):
        self.assertEqual(split_title_format_token("Basic Instinct 4K"), ("Basic Instinct", "4K UHD"))
        self.assertEqual(split_title_format_token("Inferno 4K UHD"), ("Inferno", "4K UHD"))
        self.assertEqual(split_title_format_token("Die Hard - Ultra HD"), ("Die Hard", "4K UHD"))

    def test_title_without_a_format_token_is_untouched(self):
        for title in ("Back to the Future", "Queen: Rock Montreal & Live Aid", "Snow White"):
            self.assertEqual(split_title_format_token(title), (title, ""))

    def test_token_inside_the_title_is_not_stripped(self):
        # Only a *trailing* token is packaging noise; mid-title text is the name.
        self.assertEqual(split_title_format_token("4K Video Essentials"), ("4K Video Essentials", ""))

    def test_title_that_is_only_a_format_token_survives(self):
        # Nothing would be left to identify the film, so keep the row as-is.
        self.assertEqual(split_title_format_token("4K"), ("4K", ""))

    def test_row_keeps_the_original_title_as_provenance(self):
        movie = bluray_plugin().normalize_row(bluray_row(), SOURCE_FILE, 1)
        self.assertEqual(movie["title"], "Basic Instinct")
        self.assertEqual(movie["sourceTitle"], "Basic Instinct 4K")
        self.assertEqual(movie["format"], "4K UHD")

    def test_explicit_format_column_beats_the_title_token(self):
        plugin = CollectionImportPlugin(
            {
                "id": "import_test_source",
                "name": "Test Source",
                "sourceKind": "test_export",
                "defaultPath": "/data/import/test",
                "aliases": {},
                "defaultFormat": "Blu-ray",
            }
        )
        movie = plugin.normalize_row({"Title": "Inferno 4K", "Format": "DVD"}, SOURCE_FILE, 1)
        self.assertEqual(movie["title"], "Inferno")
        self.assertEqual(movie["format"], "DVD")


class DiscReleaseDateTests(unittest.TestCase):
    def test_long_form_dates_are_parsed(self):
        self.assertEqual(parse_release_date("March 1 2023"), "2023-03-01")
        self.assertEqual(parse_release_date("August 15 2012"), "2012-08-15")
        self.assertEqual(parse_release_date("15 augustus 2012"), "2012-08-15")
        self.assertEqual(parse_release_date("Aug 15, 2012"), "2012-08-15")
        self.assertEqual(parse_release_date("2012-08-15"), "2012-08-15")

    def test_unparseable_date_stays_empty(self):
        self.assertEqual(parse_release_date("soon"), "")
        self.assertEqual(parse_release_date("2012"), "")

    def test_disc_date_never_becomes_the_film_year(self):
        # The 4K disc of Basic Instinct was pressed in 2023; the film is from
        # 1992. A year of 2023 would also send every metadata lookup to the
        # wrong film, so the source must supply no year at all here.
        movie = bluray_plugin().normalize_row(bluray_row(), SOURCE_FILE, 1)
        self.assertNotIn("year", movie)
        self.assertNotIn("releaseDate", movie)
        self.assertEqual(movie["editionReleaseDate"], "2023-03-01")
        self.assertEqual(movie["editionReleaseYear"], "2023")

    def test_a_real_year_column_is_still_used(self):
        movie = bluray_plugin().normalize_row(bluray_row(Year="1992"), SOURCE_FILE, 1)
        self.assertEqual(movie["year"], "1992")
        self.assertEqual(movie["editionReleaseDate"], "2023-03-01")

    def test_sources_with_a_film_date_column_keep_the_old_behaviour(self):
        plugin = CollectionImportPlugin(
            {
                "id": "import_test_source",
                "name": "Test Source",
                "sourceKind": "test_export",
                "defaultPath": "/data/import/test",
                "aliases": {},
            }
        )
        movie = plugin.normalize_row({"Title": "Heat", "Release Date": "1995-12-15"}, SOURCE_FILE, 1)
        self.assertEqual(movie["year"], "1995")
        self.assertEqual(movie["releaseDate"], "1995-12-15")
        self.assertNotIn("editionReleaseDate", movie)


class DiscCountTests(unittest.TestCase):
    def test_disc_columns_are_summed_and_negatives_ignored(self):
        # Blu-ray.com writes -1 for "unknown", which must not subtract discs.
        self.assertEqual(sum_disc_counts({"Blu-ray discs": "6", "DVD discs": "-1"}, ("Blu-ray discs", "DVD discs")), 6)
        self.assertEqual(sum_disc_counts({"Blu-ray discs": "2", "DVD discs": "1"}, ("Blu-ray discs", "DVD discs")), 3)

    def test_missing_disc_columns_read_as_unknown_not_zero(self):
        self.assertIsNone(sum_disc_counts({"Title": "Heat"}, ("Blu-ray discs", "DVD discs")))


class BoxSetTitleDetectionTests(unittest.TestCase):
    def test_strong_phrases_stand_on_their_own(self):
        for title in (
            "The Dark Knight Trilogy",
            "Jurassic Park - Trilogie",
            "The Bourne Complete Collection",
            "Mission : Impossible - Collection 6 films",
            "Alien Anthology",
            "Rambo Box Set",
        ):
            self.assertTrue(detect_box_set_title(title, None), title)

    def test_weak_phrases_need_a_disc_count_that_can_hold_several_films(self):
        # A 4K release of one film already ships 4K + Blu-ray + bonus discs, so
        # only a higher count corroborates a bare "Collection".
        self.assertFalse(detect_box_set_title("A Nightmare on Elm Street Collection", None))
        self.assertFalse(detect_box_set_title("A Nightmare on Elm Street Collection", 3))
        self.assertTrue(detect_box_set_title("A Nightmare on Elm Street Collection", 7))
        self.assertTrue(detect_box_set_title("Guardians of the Galaxy 1-3", 6))

    def test_single_films_are_not_box_sets(self):
        for title, discs in (
            ("Back to the Future", 1),
            ("Inferno", 3),
            ("Queen: Rock Montreal & Live Aid", 1),
            ("Hungarian Rhapsody: Queen Live In Budapest", 1),
            ("Mission: Impossible - Dead Reckoning Part One", 2),
        ):
            self.assertFalse(detect_box_set_title(title, discs), title)

    def test_detected_box_set_is_a_candidate_needing_confirmation(self):
        movie = bluray_plugin().normalize_row(
            bluray_row(Title="Guardians of the Galaxy 1-3 4K", EAN="3701432037721", **{"Blu-ray discs": "6", "DVD discs": "-1"}),
            SOURCE_FILE,
            1,
        )
        self.assertTrue(movie["isBoxSet"])
        self.assertEqual(movie["itemType"], "box_set")
        self.assertEqual(movie["boxSetTitle"], "Guardians of the Galaxy 1-3")
        evidence = movie["boxSetProposal"]["boxSetEvidence"]
        self.assertEqual(evidence["detectionSource"], "title_phrase")
        self.assertEqual(evidence["discCount"], 6)
        # No member titles exist in the export, so the proposal must stay a
        # candidate: it is not auto-importable and lands in the review queue.
        self.assertTrue(evidence["detectedWithoutMembers"])
        self.assertFalse(evidence["membersAreExplicit"])
        self.assertEqual(evidence["memberConfidence"], "needs_member_confirmation")

    def test_explicit_box_set_columns_still_win(self):
        movie = bluray_plugin().normalize_row(
            bluray_row(Title="Back to the Future 4K", **{"Is Box Set": "1", "Box Set Members": "Part I|Part II|Part III"}),
            SOURCE_FILE,
            1,
        )
        self.assertTrue(movie["isBoxSet"])
        self.assertEqual(len(movie["boxSetMembers"]), 3)
        self.assertNotIn("detectionSource", movie["boxSetProposal"]["boxSetEvidence"])

    def test_detection_can_be_disabled_per_source(self):
        plugin = CollectionImportPlugin(
            {
                "id": "import_test_source",
                "name": "Test Source",
                "sourceKind": "test_export",
                "defaultPath": "/data/import/test",
                "aliases": {},
                "detectBoxSetsFromTitle": False,
            }
        )
        movie = plugin.normalize_row({"Title": "The Dark Knight Trilogy"}, SOURCE_FILE, 1)
        self.assertNotIn("isBoxSet", movie)


if __name__ == "__main__":
    unittest.main()
