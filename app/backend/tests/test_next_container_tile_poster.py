"""A container's tile shows what its page shows.

A vault and a collection rendered a cover on their own page and an empty tile in
the library. The page resolves a container's poster through a chain — the
container's own artwork, then the artwork aggregated from its members — while
the tile read only the `poster_url` column the list query fills from
`entity_media`. A container that owns no artwork has nothing in that column, so
the two surfaces disagreed about the same container and the empty one read as a
bug rather than as a gap.

`containerTilePosterUrl` is the shared resolver that closed the gap. These are
source-text assertions, the idiom the other inline-script tests here use: the
PWA is one large inline `<script>` with no JS test harness in this repository,
so what can be pinned is that both tile renderers go through the resolver and
that its rungs stay in the order the reasoning requires.
"""

from __future__ import annotations

import os
import re
import unittest


NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
)


class ContainerTilePosterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()
        match = re.search(
            r"function containerTilePosterUrl\(container\) \{(.*?)\n    \}",
            cls.source,
            re.S,
        )
        assert match, "containerTilePosterUrl is missing from the inline script"
        cls.resolver = match.group(1)

    # --- both tile renderers go through the resolver -------------------------

    def test_the_poster_tile_uses_the_resolver(self):
        self.assertIn(
            "const poster = containerTilePosterUrl(container);",
            self.source,
        )

    def test_the_grouped_item_tile_uses_the_resolver(self):
        self.assertIn(
            'if (item?.kind === "container") return containerTilePosterUrl(item.container);',
            self.source,
        )

    def test_the_library_hero_uses_the_resolver(self):
        """`selectContainer` fills the big preview panel beside the grid. It had
        the identical gap, so selecting a vault showed an empty hero."""
        self.assertIn(
            "const poster = containerTilePosterUrl(container);\n      document.getElementById(\"heroTitle\")",
            self.source,
        )

    def test_the_hero_backdrop_no_longer_short_circuits(self):
        self.assertIn(
            "const backdrop = usableImage(container.backdrop_url) || usableImage(container.poster_url);",
            self.source,
        )

    def test_no_tile_reads_the_container_column_directly_any_more(self):
        """The shape that caused the bug: own column, then straight to backdrop."""
        self.assertNotIn(
            "usableImage(container.poster_url || container.backdrop_url)",
            self.source,
        )
        self.assertNotIn(
            "usableImage(item.container?.poster_url || item.container?.backdrop_url)",
            self.source,
        )

    def test_the_resolver_is_declared_once(self):
        self.assertEqual(self.source.count("function containerTilePosterUrl("), 1)

    # --- the rungs, and their order ------------------------------------------

    def test_every_rung_is_present(self):
        for fragment in (
            "usableImage(container?.poster_url)",
            "usableImage(metadata.poster_url)",
            "containerMemberMovies(container?.id)",
            "childContainersOf(container?.id)",
            "usableImage(container?.backdrop_url)",
        ):
            self.assertIn(fragment, self.resolver, fragment)

    def test_own_artwork_outranks_anything_borrowed(self):
        own = self.resolver.index("usableImage(container?.poster_url)")
        member = self.resolver.index("containerMemberMovies(container?.id)")
        self.assertLess(own, member)

    def test_a_member_film_outranks_a_child_container(self):
        member = self.resolver.index("containerMemberMovies(container?.id)")
        child = self.resolver.index("childContainersOf(container?.id)")
        self.assertLess(member, child)

    def test_the_backdrop_stands_in_last(self):
        child = self.resolver.index("childContainersOf(container?.id)")
        backdrop = self.resolver.index("usableImage(container?.backdrop_url)")
        self.assertLess(child, backdrop)

    def test_own_artwork_returns_before_a_member_is_consulted(self):
        """`if (own) return own;` is what makes the first rung outrank the rest."""
        self.assertIn("if (own) return own;", self.resolver)
        self.assertIn("if (memberPoster) return memberPoster;", self.resolver)
        self.assertIn("if (childPoster) return childPoster;", self.resolver)

    # --- the short-circuit trap the series resolver documents -----------------

    def test_each_rung_is_its_own_usable_image_call(self):
        """`usableImage(a || b)` short-circuits on a truthy-but-unusable `a`
        and drops a good poster on the floor -- the lesson `itemPosterUrl`
        records for series. No rung in the resolver may take that shape."""
        self.assertIsNone(
            re.search(r"usableImage\([^)]*\|\|[^)]*\)", self.resolver),
            "a rung passes an || expression into usableImage",
        )

    def test_the_member_scan_maps_through_usable_image_before_find(self):
        """Same trap: `.map(m => m.poster_url).find(Boolean)` picks the first
        truthy value, usable or not, and never reaches a later good one."""
        self.assertIn(".map((movie) => usableImage(movie?.poster_url)", self.resolver)
        self.assertIn(".map((child) => usableImage(child?.poster_url)", self.resolver)

    # --- the child hop -------------------------------------------------------

    def test_child_lookup_is_declared_and_reads_the_membership_rows(self):
        match = re.search(
            r"function childContainersOf\(containerId\) \{(.*?)\n    \}",
            self.source,
            re.S,
        )
        self.assertIsNotNone(match, "childContainersOf is missing")
        body = match.group(1)
        self.assertIn("containerMembershipRows()", body)
        self.assertIn("child_container_id", body)

    def test_the_child_hop_does_not_recurse(self):
        """One hop only: cycles predating the write guard are known to be
        possible, so the child's own artwork is read directly rather than by
        calling the resolver again."""
        match = re.search(
            r"function childContainersOf\(containerId\) \{(.*?)\n    \}",
            self.source,
            re.S,
        )
        self.assertNotIn("containerTilePosterUrl", match.group(1))
        self.assertEqual(self.resolver.count("containerTilePosterUrl"), 0)

    # --- what must not change ------------------------------------------------

    def test_the_missing_poster_filter_still_asks_about_own_artwork(self):
        """Borrowing is a display fallback, not ownership: the advanced-search
        "missing poster" filter must keep reading the container's own value, or
        a container with no artwork stops being findable as one."""
        self.assertIn(
            'if (filters.artwork === "missingPoster" && containerPosterValue(container)) return false;',
            self.source,
        )
        match = re.search(
            r"function containerPosterValue\(container\) \{(.*?)\n    \}",
            self.source,
            re.S,
        )
        self.assertIsNotNone(match)
        self.assertNotIn("containerMemberMovies", match.group(1))


if __name__ == "__main__":
    unittest.main()
