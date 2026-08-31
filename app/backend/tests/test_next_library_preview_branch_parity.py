"""``collection_movie_preview_entities`` selects the same columns on both paths.

The library page is built by one function with two SQL branches: the primary one
joins ``entity_media``/``media_assets`` for artwork, and a fallback runs on an
installation where those tables do not exist yet. Everything downstream -- the
card renderer, the export row builder, the advanced-search predicates -- reads
whatever that function returned, and neither branch is exercised by the other's
tests.

So a column added to the artwork branch alone is invisible until someone runs
the fallback, where the field is simply absent and the feature quietly does
nothing. That is the same failure ``attach_library_movie_enrichments`` documents
in its own docstring, one layer down in the same function, and nothing guards
the SELECT lists against it.

The two branches are not identical, and are not meant to be: the fallback cannot
resolve stored artwork, so it omits the eight ``*_asset_*`` columns and
substitutes a NULL for the technical-specs join. That difference is enumerated
below rather than pattern-matched, so widening it is a deliberate edit to this
file instead of a silent divergence.
"""

import os
import re
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_APP_PATH = os.path.join(BACKEND_DIR, "next_app.py")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


# Columns the fallback branch legitimately cannot produce: it runs precisely
# when the tables backing them are absent.
ARTWORK_ONLY_COLUMNS = frozenset(
    {
        "poster_asset_id",
        "poster_asset_storage_backend",
        "poster_asset_storage_key",
        "poster_asset_source_url",
        "backdrop_asset_id",
        "backdrop_asset_storage_backend",
        "backdrop_asset_storage_key",
        "backdrop_asset_source_url",
    }
)


def _read_source() -> str:
    with open(NEXT_APP_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def _preview_function_source(source: str) -> str:
    start = source.index("def collection_movie_preview_entities(")
    # The next top-level `def` ends the function body.
    end = source.index("\ndef ", start + 1)
    return source[start:end]


def _split_top_level_commas(select_body: str) -> list[str]:
    """Split a SELECT list on commas that are not inside parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in select_body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if "".join(current).strip():
        parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _alias_for(expression: str) -> str:
    """The name a row key will carry for one SELECT-list expression."""
    collapsed = " ".join(expression.split())
    aliased = re.search(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", collapsed, re.IGNORECASE)
    if aliased:
        return aliased.group(1)
    # No explicit alias: Postgres names the column after the final identifier,
    # so `m.sort_title` becomes `sort_title` and `mts.content_ratings` becomes
    # `content_ratings`.
    return collapsed.rsplit(".", 1)[-1].strip()


def _select_alias_sets(function_source: str) -> list[set[str]]:
    """Alias sets for each top-level ``SELECT ... FROM movies m`` in the body.

    Matched by walking back from each ``FROM movies m`` to the nearest preceding
    line that is nothing but ``SELECT``. A regex spanning the two cannot do this:
    the artwork branch carries ``LEFT JOIN LATERAL (SELECT ... FROM entity_media
    ...)`` subqueries *after* its own FROM, so a non-greedy match starting at a
    nested SELECT runs straight into the fallback branch's FROM and pairs two
    halves that belong to different queries.
    """
    alias_sets: list[set[str]] = []
    for match in re.finditer(r"\bFROM\s+movies\s+m\b", function_source):
        preceding = function_source[: match.start()]
        select_starts = [
            found.end()
            for found in re.finditer(r"^[ \t]*SELECT[ \t]*$", preceding, re.MULTILINE)
        ]
        if not select_starts:
            continue
        body = preceding[select_starts[-1] :]
        aliases = {_alias_for(part) for part in _split_top_level_commas(body)}
        alias_sets.append({alias for alias in aliases if alias})
    return alias_sets


class LibraryPreviewBranchParityTests(unittest.TestCase):
    def setUp(self):
        self.function_source = _preview_function_source(_read_source())
        self.alias_sets = _select_alias_sets(self.function_source)

    def test_the_function_still_has_exactly_two_movie_select_branches(self):
        # If this fails the parser below is looking at the wrong thing, and the
        # comparison it makes is meaningless rather than merely wrong.
        self.assertEqual(
            len(self.alias_sets),
            2,
            "expected one artwork branch and one fallback branch selecting from movies",
        )

    def test_the_branches_differ_only_by_the_artwork_columns(self):
        artwork_branch, fallback_branch = self.alias_sets
        # Orientation check: the first branch is the richer one.
        self.assertGreater(len(artwork_branch), len(fallback_branch))

        missing_from_fallback = artwork_branch - fallback_branch
        self.assertEqual(
            missing_from_fallback,
            set(ARTWORK_ONLY_COLUMNS),
            "a column reaches the library on one branch only -- add it to both, "
            "or add it to ARTWORK_ONLY_COLUMNS with a reason",
        )

        extra_in_fallback = fallback_branch - artwork_branch
        self.assertEqual(
            extra_in_fallback,
            set(),
            "the fallback branch selects a column the artwork branch does not",
        )

    def test_both_branches_carry_the_columns_the_library_reads(self):
        # A spot check that the parser is extracting real column names rather
        # than fragments, and a floor under both branches.
        expected = {
            "id",
            "public_id",
            "title",
            "sort_title",
            "year",
            "format",
            "media_type",
            "rating",
            "content_ratings",
            "metadata_search",
            "poster_url",
            "backdrop_url",
            "owner_id",
        }
        for index, aliases in enumerate(self.alias_sets):
            with self.subTest(branch=index):
                self.assertTrue(expected.issubset(aliases), expected - aliases)


if __name__ == "__main__":
    unittest.main()
