import json
import os
import sys
import unittest
import uuid


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend.next_genres import GENRE_CATALOG
from app.backend.next_genres import GENRE_ID_TO_KEY
from app.backend.next_genres import GENRE_KEY_TO_ID
from app.backend.next_genres import GENRE_KEY_TO_LABEL
from app.backend.next_genres import GENRE_KEYS
from app.backend.next_genres import GENRE_TRANSLATIONS
from app.backend.next_genres import canonical_genre_key
from app.backend.next_genres import genre_keys_from_tmdb_ids
from app.backend.next_genres import map_legacy_genre_text
from app.backend.next_genres import normalize_genre_keys
from app.backend.next_genres import split_legacy_genre_text
from app.backend.next_import import NextImporter


class NextGenresCatalogTests(unittest.TestCase):
    def test_catalog_has_exactly_nineteen_tmdb_genres(self):
        self.assertEqual(len(GENRE_CATALOG), 19)
        self.assertEqual(len(GENRE_KEYS), 19)
        self.assertEqual(len(set(GENRE_KEYS)), 19, "genre keys must be unique")
        self.assertEqual(len({item["id"] for item in GENRE_CATALOG}), 19, "tmdb ids must be unique")

    def test_catalog_keys_are_ascii_snake_case(self):
        for key in GENRE_KEYS:
            self.assertRegex(key, r"^[a-z][a-z_]*[a-z]$")
            self.assertEqual(key, key.encode("ascii").decode("ascii"))

    def test_id_and_key_maps_are_inverse_bijections(self):
        self.assertEqual(len(GENRE_ID_TO_KEY), 19)
        self.assertEqual(len(GENRE_KEY_TO_ID), 19)
        for key, genre_id in GENRE_KEY_TO_ID.items():
            self.assertEqual(GENRE_ID_TO_KEY[genre_id], key)

    def test_science_fiction_and_tv_movie_use_tmdb_ids(self):
        # Spot-check a couple of well-known TMDB movie genre ids so a typo
        # in the catalog doesn't silently drift from TMDB's contract.
        self.assertEqual(GENRE_KEY_TO_ID["action"], 28)
        self.assertEqual(GENRE_KEY_TO_ID["science_fiction"], 878)
        self.assertEqual(GENRE_KEY_TO_ID["tv_movie"], 10770)
        self.assertEqual(GENRE_KEY_TO_ID["western"], 37)


class NextGenresTranslationTests(unittest.TestCase):
    def test_every_shipped_locale_translates_all_nineteen_genres(self):
        i18n_dir = os.path.join(repo_root, "app", "frontend", "i18n", "next")
        locale_files = {
            name[:-len(".json")]
            for name in os.listdir(i18n_dir)
            if name.endswith(".json")
        }
        self.assertEqual(set(GENRE_TRANSLATIONS.keys()), locale_files)
        for locale, labels in GENRE_TRANSLATIONS.items():
            self.assertEqual(set(labels.keys()), set(GENRE_KEYS), locale)
            for key, label in labels.items():
                self.assertTrue(label.strip(), f"{locale}:{key} has an empty label")
            with open(os.path.join(i18n_dir, f"{locale}.json"), encoding="utf-8") as handle:
                locale_catalog = json.load(handle)
            self.assertEqual(
                labels,
                {key: locale_catalog[f"genre.{key}"] for key in GENRE_KEYS},
                f"{locale} genre aliases and UI labels must stay identical",
            )


class NextGenresTmdbMappingTests(unittest.TestCase):
    def test_canonical_genre_key_maps_known_tmdb_id(self):
        self.assertEqual(canonical_genre_key(28), "action")
        self.assertEqual(canonical_genre_key("878"), "science_fiction")

    def test_canonical_genre_key_rejects_unknown_or_invalid_ids(self):
        self.assertEqual(canonical_genre_key(999999), "")
        self.assertEqual(canonical_genre_key("not-an-id"), "")
        self.assertEqual(canonical_genre_key(None), "")

    def test_genre_keys_from_tmdb_ids_dedupes_and_preserves_order(self):
        self.assertEqual(
            genre_keys_from_tmdb_ids([28, 12, 28, 99999, 80]),
            ["action", "adventure", "crime"],
        )

    def test_genre_keys_from_tmdb_ids_handles_empty_and_none(self):
        self.assertEqual(genre_keys_from_tmdb_ids([]), [])
        self.assertEqual(genre_keys_from_tmdb_ids(None), [])


class NextGenresNormalizationTests(unittest.TestCase):
    def test_normalize_genre_keys_validates_against_catalog(self):
        self.assertEqual(
            normalize_genre_keys(["action", "not-a-genre", "Action", "Crime"]),
            ["action", "crime"],
        )

    def test_normalize_genre_keys_accepts_hyphen_or_space_variants(self):
        self.assertEqual(normalize_genre_keys(["science-fiction"]), ["science_fiction"])
        self.assertEqual(normalize_genre_keys(["science fiction"]), ["science_fiction"])

    def test_normalize_genre_keys_rejects_non_list_input(self):
        self.assertEqual(normalize_genre_keys(None), [])
        self.assertEqual(normalize_genre_keys(42), [])
        self.assertEqual(normalize_genre_keys({"action": True}), [])


class NextGenresLegacyMigrationTests(unittest.TestCase):
    def test_split_legacy_genre_text_handles_common_separators(self):
        self.assertEqual(
            split_legacy_genre_text("Action, Thriller, Crime"),
            ["Action", "Thriller", "Crime"],
        )
        self.assertEqual(
            split_legacy_genre_text("Action/Thriller|Crime; Drama & War"),
            ["Action", "Thriller", "Crime", "Drama", "War"],
        )

    def test_split_legacy_genre_text_handles_cjk_separators(self):
        self.assertEqual(
            split_legacy_genre_text("アクション、コメディ・ドラマ"),
            ["アクション", "コメディ", "ドラマ"],
        )

    def test_split_legacy_genre_text_handles_empty_input(self):
        self.assertEqual(split_legacy_genre_text(""), [])
        self.assertEqual(split_legacy_genre_text(None), [])

    def test_map_legacy_genre_text_maps_combined_english_string(self):
        matched, unmatched = map_legacy_genre_text("Action, Thriller, Crime")
        self.assertEqual(matched, ["action", "thriller", "crime"])
        self.assertEqual(unmatched, [])

    def test_map_legacy_genre_text_maps_dutch_labels(self):
        matched, unmatched = map_legacy_genre_text("Actie, Avontuur, Komedie")
        self.assertEqual(matched, ["action", "adventure", "comedy"])
        self.assertEqual(unmatched, [])

    def test_map_legacy_genre_text_keeps_unrecognized_tokens_out_of_matched(self):
        matched, unmatched = map_legacy_genre_text("Action, Mockumentary, Crime")
        self.assertEqual(matched, ["action", "crime"])
        self.assertEqual(unmatched, ["Mockumentary"])

    def test_map_legacy_genre_text_dedupes_repeated_aliases(self):
        matched, unmatched = map_legacy_genre_text("Action, Actie, Action")
        self.assertEqual(matched, ["action"])
        self.assertEqual(unmatched, [])

    def test_map_legacy_genre_text_handles_empty_value(self):
        matched, unmatched = map_legacy_genre_text("")
        self.assertEqual(matched, [])
        self.assertEqual(unmatched, [])

    def test_map_legacy_genre_text_maps_many_locale_variants(self):
        # A representative sample across languages, all resolving to the
        # same canonical key, proving the alias table isn't English-only.
        for raw in ("Action", "Actie", "Akcja", "Akční", "Aksiyon", "Екшън", "액션"):
            matched, unmatched = map_legacy_genre_text(raw)
            self.assertEqual(matched, ["action"], raw)
            self.assertEqual(unmatched, [], raw)


class _LegacyGenreCursor:
    def __init__(self, already_imported: bool):
        self.already_imported = already_imported
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query, params):
        self.calls.append((query, params))

    def fetchone(self):
        return (self.already_imported,)


class NextGenresLegacyImporterTests(unittest.TestCase):
    def test_reimport_does_not_restore_genres_after_tmdb_takes_ownership(self):
        importer = NextImporter.__new__(NextImporter)
        importer.Jsonb = lambda value: value
        cursor = _LegacyGenreCursor(already_imported=True)

        importer.import_legacy_genres(
            cursor,
            uuid.uuid4(),
            {"genre": "Action, Crime"},
        )

        self.assertEqual(len(cursor.calls), 1)
        self.assertIn("movie_genre_migration_audit", cursor.calls[0][0])

    def test_first_import_stores_normalized_genres_and_audit(self):
        importer = NextImporter.__new__(NextImporter)
        importer.Jsonb = lambda value: value
        cursor = _LegacyGenreCursor(already_imported=False)

        importer.import_legacy_genres(
            cursor,
            uuid.uuid4(),
            {"genre": "Action, Mockumentary, Crime"},
        )

        statements = [query for query, _params in cursor.calls]
        self.assertEqual(sum("INSERT INTO movie_genres" in query for query in statements), 2)
        self.assertEqual(sum("INSERT INTO movie_genre_migration_audit" in query for query in statements), 1)
        audit_params = cursor.calls[-1][1]
        self.assertEqual(audit_params[1:], ("Action, Mockumentary, Crime", ["action", "crime"], ["Mockumentary"]))


if __name__ == "__main__":
    unittest.main()
