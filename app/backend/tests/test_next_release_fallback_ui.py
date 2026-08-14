"""The Import Center's "Which pressing is this?" picker, client side.

Two things the picker owes the person holding the disc, and both are read off
the source here because the client is a template string rather than a module:

* it must not claim the barcode is unconfirmed when MovieVault said it *is*
  confirmed, and it must keep saying so when MovieVault said nothing at all;
* the poster under the film line is decoration, so nothing about the choice may
  depend on the lookup that fetches it.
"""

import os
import unittest


NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "next_views_ui.py",
    )
)


class NextReleaseFallbackUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def function_body(self, declaration: str) -> str:
        start = self.source.index(declaration)
        end = self.source.index("\n    }\n", start)
        return self.source[start:end]

    # -- the help line -----------------------------------------------------

    def test_the_confirmed_help_line_is_chosen_only_on_an_explicit_true(self):
        body = self.function_body("function releaseFallbackPickEditionHelp()")
        self.assertIn('importCenter.releaseFallback?.barcodeConfirmed === true', body)
        self.assertIn('"releaseFallback.pickEditionHelpConfirmed"', body)
        self.assertIn('"releaseFallback.pickEditionHelp"', body)

    def test_the_picker_asks_for_the_help_line_rather_than_hardcoding_one(self):
        # A literal `pickEditionHelp` in the card would be the unconditional
        # sentence again, whatever the helper decides.
        self.assertIn(
            '<div class="release-fallback-meta">${escapeHtml(releaseFallbackPickEditionHelp())}</div>',
            self.source,
        )

    def test_an_unstated_confirmation_is_kept_as_null_and_not_as_false(self):
        # `barcodeConfirmed` is tri-state on the wire. Reading absent as `false`
        # would make the client assert the barcode is unconfirmed because an
        # older MovieVault stayed silent.
        self.assertIn(
            'barcodeConfirmed: typeof result.barcodeConfirmed === "boolean" ? result.barcodeConfirmed : null,',
            self.source,
        )

    # -- the poster --------------------------------------------------------

    def test_the_poster_lookup_names_the_resolved_identity(self):
        """Without the flag the television namespace answers too.

        MovieVault already tied the barcode to a film, so the namespace is
        settled; asking with a bare id offers an unrelated series beside the
        right film when nobody asked about television.
        """
        body = self.function_body("async function loadReleaseFallbackPoster()")
        self.assertIn('"/api/next/metadata/lookup"', body)
        self.assertIn("resolvedIdentity: true", body)
        self.assertIn("if (imdbId) request.imdbId = imdbId;", body)
        self.assertIn("if (tmdbId) request.tmdbId = tmdbId;", body)

    def test_the_poster_lookup_is_skipped_without_an_identifier(self):
        body = self.function_body("async function loadReleaseFallbackPoster()")
        self.assertIn("if (!token || (!imdbId && !tmdbId)) return;", body)

    def test_a_failed_poster_lookup_leaves_the_picker_alone(self):
        # Decoration: no message, no state change, and in particular no
        # `releases` or `phase` written from the catch.
        body = self.function_body("async function loadReleaseFallbackPoster()")
        catch = body[body.index("} catch (error) {"):]
        self.assertIn('console.warn("release-details poster lookup failed", error);', catch)
        self.assertNotIn("importCenter.releaseFallback =", catch)
        self.assertNotIn("setImportCenterMessage", catch)

    def test_a_late_poster_never_lands_on_a_later_scan(self):
        body = self.function_body("async function loadReleaseFallbackPoster()")
        self.assertIn("if (!poster || importCenter.releaseFallback !== token) return;", body)

    def test_the_picker_is_not_held_up_by_the_poster_lookup(self):
        # Fired, not awaited: the list renders immediately and the poster fills
        # in behind it, so a slow lookup cannot delay the choice.
        body = self.function_body("async function runReleaseDetailsFallback(query)")
        self.assertIn("\n        loadReleaseFallbackPoster();\n", body)
        self.assertNotIn("await loadReleaseFallbackPoster()", body)

    def test_the_picker_renders_in_full_without_a_poster(self):
        """No poster is a complete card, not a degraded one.

        The image is the only part guarded on `posterUrl`; the film line, the
        help text, the candidate list and the actions are unconditional.
        """
        body = self.function_body("function renderReleaseFallback()")
        card = body[body.index("const posterUrl ="):]
        self.assertIn(
            '${posterUrl\n            ? `<div class="release-fallback-poster">'
            '<img src="${escapeHtml(posterUrl)}" alt="${escapeHtml(film.title || "")}" loading="lazy"></div>`\n'
            '            : ""}',
            card,
        )
        for unconditional in (
            "${escapeHtml(releaseFallbackPickEditionHelp())}",
            '<div class="release-fallback-list">',
            "${candidates.map(releaseFallbackOptionHtml).join(\"\")}",
            'data-release-fallback-manual="1"',
        ):
            self.assertIn(unconditional, card, unconditional)

    def test_one_poster_for_the_film_and_not_one_per_pressing(self):
        # Two 4K pressings of one film share a cover, so a poster per row draws
        # a difference that is not there.
        option = self.function_body("function releaseFallbackOptionHtml(candidate, index)")
        self.assertNotIn("posterUrl", option)

    def test_the_poster_has_a_style_rule(self):
        self.assertIn(".release-fallback-poster img {", self.source)


if __name__ == "__main__":
    unittest.main()
