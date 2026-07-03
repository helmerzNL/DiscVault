import os
import sys
import types
import unittest
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

sys.modules.setdefault(
    "requests",
    types.SimpleNamespace(
        get=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests")),
        post=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("requests is stubbed in tests")),
    ),
)

from app.backend.next_metadata import canonicalize_plugin_result
from app.backend.next_metadata import _clean_scanned_title
from app.backend.next_metadata import _parse_import_country
from app.backend.next_metadata import external_metadata_barcode
from app.backend.next_metadata import metadata_field_decisions_with_write_state
from app.backend.next_metadata import metadata_fetch_audit_payload
from app.backend.next_metadata import metadata_source_plugin_allowed
from app.backend.next_metadata import merge_metadata_results
from app.backend.next_metadata import normalize_media_format
from app.backend.next_metadata import plugin_execution_plan
from app.backend.next_metadata import query_from_payload
from app.backend.next_metadata import receiver_contribution_payload
from app.backend.next_metadata import summarize_metadata_execution
from app.backend.next_plugins.bluray_com import plugin as bluray_com_plugin

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None


class NextScannedTitleTests(unittest.TestCase):
    def test_bluray_movie_title_from_release_title_strips_format_and_country(self):
        cleaner = bluray_com_plugin._movie_title_from_release_title
        self.assertEqual(
            cleaner("Inception 4K Blu-ray (4K Ultra HD + Blu-ray) (France)"),
            "Inception",
        )
        self.assertEqual(
            cleaner("A Star Is Born 4K Blu-ray (4K Ultra HD + Blu-ray + Digital) (France)"),
            "A Star Is Born",
        )
        self.assertEqual(cleaner("Skyfall 4K Blu-ray"), "Skyfall")
        self.assertEqual(cleaner("The Matrix DVD"), "The Matrix")
        self.assertEqual(cleaner("The Dark Knight Blu-ray (United States)"), "The Dark Knight")
        # A plain film title is returned untouched.
        self.assertEqual(cleaner("Heat"), "Heat")

    def test_clean_scanned_title_strips_packaging_noise(self):
        self.assertEqual(
            _clean_scanned_title("John Wick (4K Ultra HD + Blu-ray) (UK Import)"),
            "John Wick",
        )
        self.assertEqual(_clean_scanned_title("Heat [Steelbook]"), "Heat")
        self.assertEqual(
            _clean_scanned_title("Blade Runner 2049 - Limited Edition"),
            "Blade Runner 2049",
        )
        # Space-separated multi-token format tails collapse to the film title.
        self.assertEqual(_clean_scanned_title("Inception 4K Blu-ray"), "Inception")
        self.assertEqual(_clean_scanned_title("Dune Ultra HD Blu-ray 3D"), "Dune")
        # A trailing region tag after a format group strands a bare format token
        # ("4K Blu-ray") that the format-noise stripper alone cannot reach. The
        # exact shape Blu-ray.com returns for an Inception 4K disc.
        self.assertEqual(
            _clean_scanned_title("Inception 4K Blu-ray (4K Ultra HD + Blu-ray) (France)"),
            "Inception",
        )
        self.assertEqual(
            _clean_scanned_title(
                "A Star Is Born 4K Blu-ray (4K Ultra HD + Blu-ray + Digital) (France)"
            ),
            "A Star Is Born",
        )
        # Multi-word country tags strip too, without eating a leading title word.
        self.assertEqual(
            _clean_scanned_title("Skyfall Blu-ray (United Kingdom)"), "Skyfall"
        )
        self.assertEqual(
            _clean_scanned_title("United 93 Blu-ray (United States)"), "United 93"
        )
        # A subtitle after a colon must be preserved.
        self.assertEqual(_clean_scanned_title("Mad Max: Fury Road (Blu-ray)"), "Mad Max: Fury Road")
        # Titles without noise are returned unchanged.
        self.assertEqual(_clean_scanned_title("Inception"), "Inception")
        # Never return an empty string when only noise is present.
        self.assertTrue(_clean_scanned_title("(Blu-ray)"))

    def test_parse_import_country_maps_known_hints(self):
        self.assertEqual(_parse_import_country("John Wick (UK Import)"), ("United Kingdom", ""))
        self.assertEqual(_parse_import_country("Suspiria (Italian Import)"), ("Italy", ""))
        self.assertEqual(_parse_import_country("The Matrix (US Import)"), ("United States", ""))
        # Bare country tag in brackets (no "Import" keyword), e.g. Blu-ray.com.
        self.assertEqual(
            _parse_import_country("Inception 4K Blu-ray (4K Ultra HD + Blu-ray) (France)"),
            ("France", ""),
        )
        self.assertEqual(_parse_import_country("Skyfall Blu-ray (United Kingdom)"), ("United Kingdom", ""))
        # Unknown nationality falls back to a free-text region note.
        self.assertEqual(_parse_import_country("Some Film (Xyz Import)"), ("", "Xyz Import"))
        # Region codes land in the region note.
        self.assertEqual(_parse_import_country("Some Film (Region B)"), ("", "Region B"))
        # No hint at all.
        self.assertEqual(_parse_import_country("Plain Title"), ("", ""))

    def test_canonicalize_splits_release_title_from_clean_title(self):
        normalized = canonicalize_plugin_result(
            "bluray_com",
            "movie_details",
            {
                "status": "hit",
                "releaseTitle": "John Wick (4K Ultra HD + Blu-ray) (UK Import)",
                "movie": {"title": "John Wick (4K Ultra HD + Blu-ray) (UK Import)"},
            },
        )
        movie_updates = normalized["movieUpdates"]
        self.assertEqual(movie_updates["title"], "John Wick")
        self.assertEqual(
            movie_updates["release_title"],
            "John Wick (4K Ultra HD + Blu-ray) (UK Import)",
        )
        self.assertEqual(movie_updates["country"], "United Kingdom")
        self.assertEqual(normalized["releaseTitle"], "John Wick (4K Ultra HD + Blu-ray) (UK Import)")
        self.assertEqual(normalized["cleanTitle"], "John Wick")

    def test_canonicalize_keeps_clean_title_untouched_without_noise(self):
        normalized = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {"status": "hit", "movie": {"title": "Inception"}},
        )
        self.assertEqual(normalized["movieUpdates"]["title"], "Inception")
        self.assertNotIn("release_title", normalized["movieUpdates"])

    def test_canonicalize_bluray_release_with_trailing_country(self):
        # Reproduces the real Blu-ray.com payload for the Inception 4K disc when
        # MovieVault is disabled: the plugin's movie.title still carries format
        # noise and the raw release title ends in a "(France)" region tag. The
        # canonical title must still resolve to the bare film title.
        normalized = canonicalize_plugin_result(
            "bluray_com",
            "search_barcode",
            {
                "status": "hit",
                "releaseTitle": "Inception 4K Blu-ray (4K Ultra HD + Blu-ray) (France)",
                "movie": {"title": "Inception 4K Blu-ray (4K Ultra HD + Blu-ray)"},
                "release": {
                    "title": "Inception 4K Blu-ray (4K Ultra HD + Blu-ray) (France)"
                },
            },
        )
        movie_updates = normalized["movieUpdates"]
        self.assertEqual(movie_updates["title"], "Inception")
        self.assertEqual(
            movie_updates["release_title"],
            "Inception 4K Blu-ray (4K Ultra HD + Blu-ray) (France)",
        )
        self.assertEqual(movie_updates["country"], "France")

    def test_canonicalize_region_hint_lands_as_regions_array(self):
        normalized = canonicalize_plugin_result(
            "bluray_com",
            "movie_details",
            {
                "status": "hit",
                "releaseTitle": "Some Film (Region B)",
                "movie": {"title": "Some Film (Region B)"},
            },
        )
        # `regions` must be a JSONB-friendly list, not a bare string.
        self.assertEqual(normalized["technicalUpdates"]["regions"], ["Region B"])
        self.assertEqual(normalized["movieUpdates"]["title"], "Some Film")


class NextMetadataPolicyTests(unittest.TestCase):
    def test_bluray_box_set_explicit_member_links_are_exposed_as_confirmed_candidates(self):
        if BeautifulSoup is None:
            self.skipTest("BeautifulSoup is not available")
        soup = BeautifulSoup(
            """
            <html><body>
              <div id="movie_info">
                <p>This Blu-ray bundle includes the following titles:</p>
                <a class="hoverlink" data-globalparentid="999" data-productid="123" href="/movies/RoboCop-Blu-ray/123/">
                  <img src="/covers/robocop.jpg" alt="RoboCop Blu-ray (1987)" />
                  RoboCop Blu-ray
                </a>
                <a class="hoverlink" data-globalparentid="999" data-productid="456" href="/movies/Saving-Private-Ryan-Blu-ray/456/">
                  <img src="/covers/saving-private-ryan.jpg" alt="Saving Private Ryan Blu-ray (1998)" />
                  Saving Private Ryan Blu-ray
                </a>
              </div>
            </body></html>
            """,
            "html.parser",
        )

        members = bluray_com_plugin._extract_explicit_box_set_members(
            soup,
            current_url="https://www.blu-ray.com/movies/Example-Box-Set-Blu-ray/999/",
            parent_title="Example Box Set Blu-ray",
            release_format="Blu-ray",
        )

        self.assertEqual([member["title"] for member in members], ["RoboCop", "Saving Private Ryan"])
        self.assertEqual([member["format"] for member in members], ["Blu-ray", "Blu-ray"])
        self.assertEqual([member["year"] for member in members], ["1987", "1998"])
        self.assertEqual(members[0]["posterUrl"], "https://www.blu-ray.com/covers/robocop.jpg")
        self.assertEqual(members[0]["memberConfidence"], "needs_member_confirmation")

    def test_bluray_box_set_related_links_are_not_treated_as_members(self):
        if BeautifulSoup is None:
            self.skipTest("BeautifulSoup is not available")
        soup = BeautifulSoup(
            """
            <html><body>
              <div id="movie_info">
                <p>Jurassic Park Trilogie Blu-ray collection.</p>
                <div id="related">
                  <a class="hoverlink" data-globalparentid="999" data-productid="123" href="/movies/Jurassic-Park-Blu-ray/123/">
                    <img src="/covers/jurassic-park.jpg" alt="Jurassic Park Blu-ray (1993)" />
                    Jurassic Park
                  </a>
                </div>
              </div>
            </body></html>
            """,
            "html.parser",
        )

        members = bluray_com_plugin._extract_explicit_box_set_members(
            soup,
            current_url="https://www.blu-ray.com/movies/Jurassic-Park-Trilogie-Blu-ray/999/",
            parent_title="Jurassic Park Trilogie Blu-ray",
            release_format="Blu-ray",
        )

        self.assertEqual(members, [])

    def test_bluray_box_set_candidates_ignore_regular_release_pages(self):
        with mock.patch.object(
            bluray_com_plugin,
            "technical_specs",
            return_value={
                "status": "hit",
                "provider": "bluray_com",
                "isBoxSetCandidate": False,
                "movie": {"title": "RoboCop", "format": "Blu-ray"},
            },
        ):
            result = bluray_com_plugin.box_set_candidates({"title": "RoboCop", "format": "Blu-ray"})

        self.assertEqual(result["status"], "miss")
        self.assertEqual(result["boxSetProposal"], {})

    def test_bluray_box_set_candidates_confirm_explicit_members(self):
        members = [
            {"title": "RoboCop", "source": "Blu-ray.com", "sortOrder": 1},
            {"title": "RoboCop 2", "source": "Blu-ray.com", "sortOrder": 2},
        ]
        with mock.patch.object(
            bluray_com_plugin,
            "technical_specs",
            return_value={
                "status": "hit",
                "provider": "bluray_com",
                "sourceUrl": "https://www.blu-ray.com/movies/RoboCop-Trilogy-Blu-ray/999/",
                "isBoxSetCandidate": True,
                "boxSetMembers": members,
                "movie": {"title": "RoboCop Trilogy", "format": "Blu-ray"},
            },
        ):
            result = bluray_com_plugin.box_set_candidates({"title": "RoboCop Trilogy", "format": "Blu-ray"})

        proposal = result["boxSetProposal"]
        self.assertEqual(result["status"], "hit")
        self.assertFalse(proposal["detectedWithoutMembers"])
        self.assertEqual(proposal["memberConfidence"], "needs_member_confirmation")
        self.assertEqual(proposal["memberSource"], "Blu-ray.com release page")
        self.assertEqual(proposal["members"], members)
        self.assertEqual(proposal["movies"], members)
        self.assertEqual(proposal["memberCount"], 2)
        self.assertTrue(proposal["boxSetEvidence"]["membersAreExplicit"])
        self.assertFalse(proposal["boxSetEvidence"]["detectedWithoutMembers"])

    def test_bluray_box_set_candidate_fallback_is_marked_candidate_only_evidence(self):
        members = [
            {"title": "RoboCop", "source": "Blu-ray.com candidate search", "sortOrder": 1},
            {"title": "RoboCop 2", "source": "Blu-ray.com candidate search", "sortOrder": 2},
        ]
        with (
            mock.patch.object(
                bluray_com_plugin,
                "technical_specs",
                return_value={
                    "status": "hit",
                    "provider": "bluray_com",
                    "sourceUrl": "https://www.blu-ray.com/movies/RoboCop-Trilogy-Blu-ray/999/",
                    "isBoxSetCandidate": True,
                    "boxSetMembers": [],
                    "movie": {"title": "RoboCop Trilogy", "format": "Blu-ray"},
                },
            ),
            mock.patch.object(bluray_com_plugin, "_candidate_members_from_search", return_value=members),
        ):
            result = bluray_com_plugin.box_set_candidates({"title": "RoboCop Trilogy", "format": "Blu-ray"})

        evidence = result["boxSetProposal"]["boxSetEvidence"]
        self.assertEqual(evidence["memberConfidence"], "candidate")
        self.assertFalse(evidence["membersAreExplicit"])
        self.assertTrue(evidence["detectedWithoutMembers"])

    def test_is_box_set_candidate_keys_only_on_release_title_not_page_text(self):
        # Regression (barcode 0085391176572 "The Dark Knight Blu-ray"): a single-disc
        # release whose page merely *mentions* a trilogy/collection in related products
        # must NOT be classified as a box-set. Only the product's own release title counts.
        self.assertFalse(
            bluray_com_plugin._is_box_set_candidate(
                "The Dark Knight Blu-ray",
                "Customers also bought: The Dark Knight Trilogy 4K Collection",
            )
        )
        self.assertTrue(bluray_com_plugin._is_box_set_candidate("The Dark Knight Trilogy Blu-ray"))
        self.assertTrue(bluray_com_plugin._is_box_set_candidate("Alien Anthology"))

    def test_bluray_single_release_page_mentioning_box_set_is_not_a_candidate(self):
        if BeautifulSoup is None:
            self.skipTest("BeautifulSoup is not available")
        html = """
        <html><head>
          <meta property="og:title" content="The Dark Knight Blu-ray" />
          <meta property="og:image" content="/covers/the-dark-knight.jpg" />
        </head><body>
          <h1>The Dark Knight Blu-ray</h1>
          <div id="similar">
            <p>Customers who bought this also bought:</p>
            <a class="hoverlink" data-globalparentid="1" data-productid="2" href="/movies/The-Dark-Knight-Trilogy-4K-Blu-ray/12345/">
              <img src="/covers/trilogy.jpg" alt="The Dark Knight Trilogy 4K" />
              The Dark Knight Trilogy 4K
            </a>
          </div>
        </body></html>
        """

        class _Resp:
            text = html

            def raise_for_status(self):
                return None

        with mock.patch.object(bluray_com_plugin.requests, "get", return_value=_Resp()):
            parsed = bluray_com_plugin._parse_page(
                "https://www.blu-ray.com/movies/The-Dark-Knight-Blu-ray/743/"
            )

        self.assertEqual(parsed["status"], "hit")
        self.assertFalse(parsed["isBoxSetCandidate"])
        self.assertEqual(parsed["boxSetMembers"], [])
        self.assertNotIn("boxSetEvidence", parsed)

    def test_format_keys_on_url_and_title_not_page_text(self):
        # Regression (barcode 0085391176572 "The Dark Knight Blu-ray"): a Blu-ray release
        # page that mentions a 4K/UHD edition elsewhere must still resolve to Blu-ray. The
        # URL slug and og:title are authoritative; page text is only a last resort.
        bluray = bluray_com_plugin._format_from_url_title_text(
            "https://www.blu-ray.com/movies/The-Dark-Knight-Blu-ray/743/",
            "The Dark Knight Blu-ray",
            "Also available on 4K UHD. The Dark Knight Trilogy 4K Ultra HD bundle.",
        )
        self.assertEqual(bluray, "Blu-ray")

        uhd = bluray_com_plugin._format_from_url_title_text(
            "https://www.blu-ray.com/movies/The-Dark-Knight-4K-Blu-ray/12345/",
            "The Dark Knight 4K (Blu-ray)",
            "",
        )
        self.assertEqual(uhd, "4K UHD")

        dvd = bluray_com_plugin._format_from_url_title_text(
            "https://www.blu-ray.com/dvd/The-Dark-Knight-DVD/678/",
            "The Dark Knight DVD",
            "Also available on 4K UHD.",
        )
        self.assertEqual(dvd, "DVD")

        # Inconclusive URL/title falls back to page text rather than guessing nothing.
        fallback = bluray_com_plugin._format_from_url_title_text(
            "https://www.blu-ray.com/movies/Some-Release/999/",
            "Some Release",
            "Format: Blu-ray",
        )
        self.assertEqual(fallback, "Blu-ray")

    def test_bluray_blu_ray_page_mentioning_4k_resolves_as_blu_ray(self):
        if BeautifulSoup is None:
            self.skipTest("BeautifulSoup is not available")
        html = """
        <html><head>
          <meta property="og:title" content="The Dark Knight Blu-ray" />
          <meta property="og:image" content="/covers/the-dark-knight.jpg" />
        </head><body>
          <h1>The Dark Knight Blu-ray</h1>
          <p>Also available on 4K UHD Ultra HD Blu-ray.</p>
        </body></html>
        """

        class _Resp:
            text = html

            def raise_for_status(self):
                return None

        with mock.patch.object(bluray_com_plugin.requests, "get", return_value=_Resp()):
            parsed = bluray_com_plugin._parse_page(
                "https://www.blu-ray.com/movies/The-Dark-Knight-Blu-ray/743/"
            )

        self.assertEqual(parsed["format"], "Blu-ray")
        self.assertEqual(parsed["movie"]["format"], "Blu-ray")
        self.assertEqual(parsed["release"]["format"], "Blu-ray")

    def test_canonicalize_plugin_result_normalizes_box_set_evidence_contract(self):
        result = canonicalize_plugin_result(
            "movievault_26",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "MovieVault 26",
                "sourceRef": "barcode:5050582369601",
                "boxSetProposal": {
                    "title": "Back to the Future Trilogy DVD",
                    "barcode": "5050582369601",
                    "format": "DVD",
                    "members": [
                        {"title": "Back to the Future", "year": "1985"},
                        {"title": "Back to the Future Part II", "year": "1989"},
                        {"title": "Back to the Future Part III", "year": "1990"},
                    ],
                    "boxSetEvidence": {
                        "barcodeMatch": True,
                        "entityType": "box_set",
                        "memberSource": "MovieVault 26",
                        "memberConfidence": "identified",
                        "membersAreExplicit": True,
                    },
                },
            },
        )

        evidence = result["boxSetProposal"]["boxSetEvidence"]
        self.assertTrue(evidence["barcodeMatch"])
        self.assertEqual(evidence["entityType"], "box_set")
        self.assertEqual(evidence["memberCount"], 3)
        self.assertTrue(evidence["membersAreExplicit"])
        self.assertEqual(evidence["format"], "DVD")

    def test_canonicalize_synthesizes_candidate_for_single_movie_hit(self):
        result = canonicalize_plugin_result(
            "bluray_com",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "sourceRef": "https://www.blu-ray.com/movies/Lethal-Weapon",
                "movie": {"title": "Lethal Weapon", "year": "1987", "posterUrl": "https://images.example/lethal.jpg"},
                "release": {"title": "Lethal Weapon 4K UHD"},
                "format": "4K UHD",
            },
        )

        candidates = result["candidates"]
        self.assertEqual(len(candidates), 1)
        synthesized = candidates[0]
        self.assertTrue(synthesized.get("synthesized"))
        self.assertEqual(synthesized["title"], "Lethal Weapon")
        self.assertEqual(synthesized["provider"], "bluray_com")
        self.assertEqual(synthesized["format"], "4K UHD")
        self.assertEqual(synthesized["sourceRef"], "https://www.blu-ray.com/movies/Lethal-Weapon")
        self.assertEqual(synthesized["posterUrl"], "https://images.example/lethal.jpg")

    def test_canonicalize_does_not_synthesize_candidate_on_miss(self):
        result = canonicalize_plugin_result(
            "dvd_fr",
            "search_barcode",
            {"status": "miss", "sourceLabel": "DVDFr"},
        )

        self.assertEqual(result["candidates"], [])

    def test_canonicalize_does_not_synthesize_candidate_for_box_set(self):
        result = canonicalize_plugin_result(
            "movievault_26",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "MovieVault 26",
                "sourceRef": "barcode:5050582369601",
                "movie": {"title": "Back to the Future Trilogy"},
                "boxSetProposal": {
                    "title": "Back to the Future Trilogy DVD",
                    "barcode": "5050582369601",
                    "format": "DVD",
                    "members": [
                        {"title": "Back to the Future", "year": "1985"},
                        {"title": "Back to the Future Part II", "year": "1989"},
                    ],
                },
            },
        )

        self.assertEqual(result["candidates"], [])

    def test_canonicalize_preserves_explicit_candidates(self):
        explicit = [{"title": "Heat", "year": "1995", "provider": "tmdb"}]
        result = canonicalize_plugin_result(
            "tmdb",
            "search_title",
            {
                "status": "hit",
                "sourceLabel": "TMDB",
                "items": explicit,
                "movie": {"title": "Heat"},
            },
        )

        self.assertEqual(result["candidates"], explicit)

    def test_release_specs_do_not_upgrade_across_formats(self):
        current = {
            "title": "Example",
            "format": "Blu-ray",
            "metadata": {},
        }
        technical = {
            "audio_tracks": ["English DTS-HD MA 5.1"],
            "subtitles": ["English"],
        }
        result = canonicalize_plugin_result(
            "bluray_com",
            "technical_specs",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "technicalSpecs": {
                    "format": "4K UHD",
                    "audioTracks": ["English Dolby Atmos"],
                    "subtitles": ["English SDH"],
                    "hdr": "HDR10",
                },
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current=technical,
            results=[result],
            overwrite_enabled=True,
            target_format="Blu-ray",
        )

        self.assertEqual(merged["technicalUpdates"], {})
        self.assertTrue(any("format mismatch" in item["reason"] for item in merged["skipped"]))

    def test_same_format_release_specs_may_refresh_technical_fields(self):
        current = {"title": "Example", "format": "DVD", "metadata": {}}
        technical = {"audio_tracks": ["English Dolby Digital 2.0"]}
        result = canonicalize_plugin_result(
            "bluray_com",
            "technical_specs",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "technicalSpecs": {
                    "format": "DVD",
                    "audioTracks": ["English Dolby Digital 5.1"],
                    "subtitles": ["Dutch"],
                },
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current=technical,
            results=[result],
            overwrite_enabled=False,
            target_format="DVD",
        )

        self.assertEqual(merged["technicalUpdates"]["audio_tracks"], ["English Dolby Digital 5.1"])
        self.assertEqual(merged["technicalUpdates"]["subtitles"], ["Dutch"])

    def test_release_source_cannot_replace_canonical_display_title(self):
        current = {"title": "A Minecraft Movie", "format": "4K UHD", "metadata": {}}
        result = canonicalize_plugin_result(
            "bluray_com",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "movie": {
                    "title": "A Minecraft Movie 4K Blu-ray (SteelBook) (France)",
                    "format": "4K UHD",
                },
                "release": {
                    "title": "A Minecraft Movie 4K Blu-ray (SteelBook) (France)",
                    "format": "4K UHD",
                },
                "technicalSpecs": {"format": "4K UHD", "subtitles": ["French"]},
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=True,
            target_format="4K UHD",
        )

        self.assertNotIn("title", merged["movieUpdates"])
        self.assertEqual(merged["technicalUpdates"]["subtitles"], ["French"])
        self.assertTrue(
            any(item["field"] == "title" and "release source" in item["reason"] for item in merged["skipped"])
        )

    def test_release_source_seeds_canonical_title_when_movie_has_none(self):
        # Mirrors a real barcode scan where MovieVault 500s and TMDb misses, so
        # the only hit is Blu-ray.com (a release source). Its plugin already
        # derives a clean movie.title with the raw packaging string in
        # release.title; the clean title must seed the empty canonical title
        # instead of leaving it blank.
        current = {"title": "", "format": "4K UHD", "metadata": {}}
        result = canonicalize_plugin_result(
            "bluray_com",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "movie": {
                    "title": "Inception",
                    "format": "4K UHD",
                },
                "release": {
                    "title": "Inception 4K Blu-ray (UK Import)",
                    "format": "4K UHD",
                },
                "technicalSpecs": {"format": "4K UHD", "subtitles": ["English"]},
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=False,
            target_format="4K UHD",
        )

        self.assertEqual(merged["movieUpdates"]["title"], "Inception")
        self.assertEqual(
            merged["movieUpdates"]["release_title"],
            "Inception 4K Blu-ray (UK Import)",
        )
        self.assertEqual(merged["movieUpdates"]["country"], "United Kingdom")
        self.assertFalse(
            any(item["field"] == "title" and "release source" in item["reason"] for item in merged["skipped"])
        )

    def test_release_source_still_cannot_overwrite_existing_canonical_title(self):
        current = {"title": "Inception", "format": "4K UHD", "metadata": {}}
        result = canonicalize_plugin_result(
            "bluray_com",
            "search_barcode",
            {
                "status": "hit",
                "sourceLabel": "Blu-ray.com",
                "movie": {"title": "Inception 4K Blu-ray (UK Import)", "format": "4K UHD"},
                "release": {"title": "Inception 4K Blu-ray (UK Import)", "format": "4K UHD"},
                "technicalSpecs": {"format": "4K UHD"},
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=True,
            target_format="4K UHD",
        )

        self.assertNotIn("title", merged["movieUpdates"])
        self.assertTrue(
            any(
                item["field"] == "title" and "release source cannot overwrite" in item["reason"]
                for item in merged["skipped"]
            )
        )

    def test_manual_fields_are_protected_without_preferred_overwrite(self):
        current = {
            "title": "Manual Title",
            "overview": "Manual overview",
            "format": "4K UHD",
            "metadata": {"poster_url": "https://local/poster.jpg"},
        }
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "movie": {
                    "title": "Provider Title",
                    "overview": "Provider overview",
                    "posterUrl": "https://provider/poster.jpg",
                    "rating": "8.1",
                },
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=False,
            target_format="4K UHD",
        )

        self.assertNotIn("title", merged["movieUpdates"])
        self.assertNotIn("overview", merged["movieUpdates"])
        self.assertNotIn("poster_url", merged["metadataUpdates"])
        self.assertEqual(merged["mediaUpdates"]["poster"]["sourceUrl"], "https://provider/poster.jpg")
        self.assertEqual(merged["movieUpdates"]["rating"], "8.1")

    def test_plugin_credits_are_kept_in_metadata_refresh_proposal(self):
        current = {"title": "Aladdin", "format": "4K UHD", "metadata": {}}
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "sourceLabel": "TMDb",
                "sourceRef": "tmdb:420817",
                "credits": [
                    {"role": "actor", "name": "Will Smith", "character": "Genie", "tmdbId": 2888, "sortOrder": 0},
                    {"role": "crew", "name": "Guy Ritchie", "job": "Director", "tmdbId": 956, "sortOrder": 0},
                ],
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=False,
            target_format="4K UHD",
        )

        self.assertEqual(len(result["credits"]), 2)
        self.assertEqual(len(merged["credits"]), 2)
        self.assertEqual(merged["credits"][0]["name"], "Will Smith")
        self.assertEqual(merged["credits"][0]["role"], "actor")
        self.assertEqual(merged["credits"][1]["job"], "Director")
        self.assertEqual(merged["credits"][1]["sourceLabel"], "TMDb")

    def test_preferred_overwrite_allows_provider_to_replace_display_fields(self):
        current = {
            "title": "Manual Title",
            "overview": "Manual overview",
            "format": "4K UHD",
            "metadata": {"poster_url": "https://local/poster.jpg"},
        }
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "movie": {
                    "title": "Provider Title",
                    "overview": "Provider overview",
                    "posterUrl": "https://provider/poster.jpg",
                },
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=True,
            target_format="4K UHD",
        )

        self.assertEqual(merged["movieUpdates"]["title"], "Provider Title")
        self.assertEqual(merged["movieUpdates"]["overview"], "Provider overview")
        self.assertEqual(merged["metadataUpdates"]["poster_url"], "https://provider/poster.jpg")
        self.assertEqual(merged["mediaUpdates"]["poster"]["sourceUrl"], "https://provider/poster.jpg")

    def test_field_decisions_record_priority_winner_for_conflicting_fields(self):
        current = {"title": "Manual Title", "overview": "Manual overview", "format": "4K UHD", "metadata": {}}
        tmdb_result = {
            "pluginId": "tmdb",
            "entrypoint": "lookup_external_id",
            "sourceLabel": "TMDb",
            "sourceRef": "tmdb:1",
            "movieUpdates": {"overview": "TMDb overview"},
        }
        movievault_result = {
            "pluginId": "movievault_26",
            "entrypoint": "movie_details",
            "sourceLabel": "MovieVault 26",
            "sourceRef": "movievault:1",
            "movieUpdates": {"overview": "MovieVault overview"},
        }

        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[tmdb_result, movievault_result],
            overwrite_enabled=True,
            target_format="4K UHD",
        )

        decision = next(item for item in merged["fieldDecisions"] if item["target"] == "movie" and item["field"] == "overview")
        self.assertEqual(merged["movieUpdates"]["overview"], "TMDb overview")
        self.assertTrue(decision["conflict"])
        self.assertEqual(decision["winner"]["pluginId"], "tmdb")
        self.assertEqual(decision["finalValue"], "TMDb overview")
        self.assertEqual(len(decision["candidates"]), 2)
        self.assertTrue(decision["candidates"][0]["winner"])
        self.assertFalse(decision["candidates"][1]["accepted"])
        self.assertEqual(decision["candidates"][1]["reason"], "higher-priority provider already selected this field")

    def test_field_decisions_mark_later_provider_rejected_without_overwrite(self):
        current = {"title": "Manual Title", "format": "4K UHD", "metadata": {}}
        tmdb_result = {
            "pluginId": "tmdb",
            "entrypoint": "lookup_external_id",
            "sourceLabel": "TMDb",
            "movieUpdates": {"rating": "7.1"},
        }
        omdb_result = {
            "pluginId": "omdb",
            "entrypoint": "lookup_external_id",
            "sourceLabel": "OMDb",
            "movieUpdates": {"rating": "7.2"},
        }

        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[tmdb_result, omdb_result],
            overwrite_enabled=False,
            target_format="4K UHD",
        )

        decision = next(item for item in merged["fieldDecisions"] if item["target"] == "movie" and item["field"] == "rating")
        self.assertEqual(merged["movieUpdates"]["rating"], "7.1")
        self.assertEqual(decision["winner"]["pluginId"], "tmdb")
        self.assertEqual(decision["acceptedCandidateCount"], 1)
        self.assertFalse(decision["candidates"][1]["accepted"])
        self.assertEqual(decision["candidates"][1]["reason"], "higher-priority provider already selected this field")

    def test_tmdb_content_ratings_are_format_neutral(self):
        current = {"title": "Manual Title", "format": "4K UHD", "metadata": {}}
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "sourceRef": "tmdb:105",
                "movie": {"title": "Back to the Future"},
                "technicalSpecs": {"contentRatings": {"NL": "AL", "US": "PG"}},
            },
        )

        self.assertEqual(result["technicalUpdates"]["content_ratings"], {"NL": "AL", "US": "PG"})

        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=False,
            target_format="4K UHD",
        )

        self.assertEqual(merged["technicalUpdates"]["content_ratings"], {"NL": "AL", "US": "PG"})
        decision = next(item for item in merged["fieldDecisions"] if item["target"] == "technical" and item["field"] == "content_ratings")
        self.assertEqual(decision["winner"]["pluginId"], "tmdb")
        self.assertEqual(decision["winner"]["reason"], "current field is empty")

    def test_provider_image_options_are_kept_as_media_choices(self):
        current = {"title": "Manual Title", "format": "4K UHD", "metadata": {"poster_url": "https://local/poster.jpg"}}
        result = canonicalize_plugin_result(
            "tmdb",
            "movie_details",
            {
                "status": "hit",
                "movie": {
                    "posterUrl": "https://provider/poster-main.jpg",
                    "posters": [
                        "https://provider/poster-main.jpg",
                        "https://provider/poster-alt.jpg",
                    ],
                },
            },
        )
        merged = merge_metadata_results(
            current=current,
            technical_current={},
            results=[result],
            overwrite_enabled=False,
            target_format="4K UHD",
        )

        self.assertNotIn("poster_url", merged["metadataUpdates"])
        self.assertEqual(merged["mediaUpdates"]["poster"]["sourceUrl"], "https://provider/poster-main.jpg")
        self.assertEqual(
            merged["mediaUpdates"]["poster"]["options"],
            ["https://provider/poster-main.jpg", "https://provider/poster-alt.jpg"],
        )
        decision = next(item for item in merged["fieldDecisions"] if item["target"] == "media" and item["field"] == "poster")
        self.assertEqual(decision["winner"]["pluginId"], "tmdb")
        self.assertEqual(decision["finalValue"], "https://provider/poster-main.jpg")

    def test_field_decisions_are_marked_written_from_applied_payload(self):
        decisions = [
            {
                "target": "movie",
                "field": "overview",
                "winner": {"pluginId": "tmdb"},
                "candidates": [],
            },
            {
                "target": "metadata",
                "field": "poster_url",
                "winner": {"pluginId": "tmdb"},
                "candidates": [],
            },
        ]

        enriched = metadata_field_decisions_with_write_state(
            decisions,
            applied={
                "changed": True,
                "applied": {
                    "movieUpdates": {"overview": "Fresh overview"},
                    "metadataUpdates": {},
                },
            },
            dry_run=False,
        )

        self.assertTrue(enriched[0]["written"])
        self.assertEqual(enriched[0]["writeState"], "written")
        self.assertFalse(enriched[1]["written"])
        self.assertEqual(enriched[1]["writeState"], "not_written")

    def test_synthetic_barcodes_are_not_sent_to_external_sources(self):
        self.assertEqual(external_metadata_barcode("IMPORT-BACK_TO_THE_FUTURE-1985"), "")
        self.assertEqual(external_metadata_barcode("032429316110-BOX-01"), "")
        self.assertEqual(external_metadata_barcode("8717418557683"), "8717418557683")

    def test_import_lookup_can_request_box_set_candidates(self):
        query = query_from_payload({"barcode": "5051892000000", "detectBoxSets": True})
        plan = plugin_execution_plan(
            {"capabilities": ["search_barcode", "movie_details", "box_set_candidates"]},
            query,
        )

        self.assertIn("box_set_candidates", [item["entrypoint"] for item in plan])

    def test_preview_lookup_uses_fast_barcode_plan(self):
        query = query_from_payload({"barcode": "5051892000000", "detectBoxSets": True, "previewMode": True})
        plan = plugin_execution_plan(
            {"capabilities": ["search_barcode", "movie_details", "box_set_candidates"]},
            query,
        )

        self.assertEqual([item["entrypoint"] for item in plan], ["search_barcode", "box_set_candidates"])

    def test_preview_title_lookup_can_request_box_set_candidates(self):
        query = query_from_payload({"title": "Back to the Future Trilogy", "detectBoxSets": True, "previewMode": True})
        plan = plugin_execution_plan(
            {"capabilities": ["search_title", "movie_details", "box_set_candidates"]},
            query,
        )

        self.assertEqual([item["entrypoint"] for item in plan], ["search_title", "movie_details", "box_set_candidates"])

    def test_preview_barcode_and_title_is_title_driven(self):
        query = query_from_payload({
            "barcode": "5051892000000",
            "title": "Lethal Weapon",
            "detectBoxSets": True,
            "previewMode": True,
        })
        plan = plugin_execution_plan(
            {"capabilities": ["search_barcode", "search_title", "movie_details", "box_set_candidates"]},
            query,
        )

        entrypoints = [item["entrypoint"] for item in plan]
        self.assertEqual(entrypoints, ["search_title", "movie_details", "box_set_candidates"])
        self.assertNotIn("search_barcode", entrypoints)
        for item in plan:
            self.assertNotIn("externalBarcode", item["payload"])
            self.assertNotIn("barcode", item["payload"])
            self.assertEqual(item["payload"].get("title"), "Lethal Weapon")

    def test_bootstrap_metadata_source_runs_for_public_barcode_queries(self):
        plugin = {
            "id": "upcitemdb",
            "categories": ["metadata_source", "metadata_bootstrap"],
            "capabilities": ["search_barcode", "title_hint", "bootstrap_lookup"],
            "manifest": {"bootstrap": {"metadataSource": True}},
        }

        self.assertTrue(
            metadata_source_plugin_allowed(
                plugin,
                query_from_payload({"barcode": "5051892000000", "previewMode": True}),
            )
        )
        self.assertTrue(
            metadata_source_plugin_allowed(
                plugin,
                query_from_payload({"barcode": "5051892000000"}),
            )
        )
        self.assertTrue(
            metadata_source_plugin_allowed(
                plugin,
                query_from_payload({"barcode": "5051892000000", "title": "Alien", "previewMode": True}),
            )
        )
        self.assertTrue(
            metadata_source_plugin_allowed(
                plugin,
                query_from_payload({"barcode": "5051892000000", "tmdbId": "348", "previewMode": True}),
            )
        )
        self.assertFalse(
            metadata_source_plugin_allowed(
                plugin,
                query_from_payload({"title": "Alien", "previewMode": True}),
            )
        )

    def test_regular_metadata_source_is_allowed_outside_bootstrap_context(self):
        plugin = {
            "id": "tmdb",
            "categories": ["metadata_source"],
            "capabilities": ["search_title", "lookup_external_id", "movie_details"],
        }

        self.assertTrue(metadata_source_plugin_allowed(plugin, query_from_payload({"title": "Alien"})))

    def test_media_format_normalization(self):
        self.assertEqual(normalize_media_format("Ultra HD Blu-ray"), "4K UHD")
        self.assertEqual(normalize_media_format("Blu ray"), "Blu-ray")
        self.assertEqual(normalize_media_format("DVD Video"), "DVD")

    def test_identifier_list_and_source_format_are_normalized(self):
        result = canonicalize_plugin_result(
            "movievault",
            "movie_details",
            {
                "sourceFormat": "Ultra HD Blu-ray",
                "identifiers": [
                    {"provider_id": "tmdb", "identifier_type": "movie_id", "identifier": "123"},
                    {"provider": "imdb", "identifierType": "movie_id", "value": "tt1234567"},
                ],
            },
        )

        self.assertEqual(result["normalizedSourceFormat"], "4K UHD")
        self.assertEqual(result["identifiers"], {"tmdb": "123", "imdb": "tt1234567"})

    def test_technical_list_strings_are_split_and_deduped(self):
        result = canonicalize_plugin_result(
            "bluray_com",
            "technical_specs",
            {
                "technicalSpecs": {
                    "format": "4K UHD",
                    "audioTracks": "English: Dolby TrueHD 7.1 (48kHz, 24-bit), Spanish: Dolby Digital 5.1",
                    "subtitles": ["English SDH, French, Japanese, Spanish", "French"],
                    "regions": "A, B, C",
                },
            },
        )

        self.assertEqual(
            result["technicalUpdates"]["audio_tracks"],
            ["English: Dolby TrueHD 7.1 (48kHz, 24-bit)", "Spanish: Dolby Digital 5.1"],
        )
        self.assertEqual(result["technicalUpdates"]["subtitles"], ["English SDH", "French", "Japanese", "Spanish"])
        self.assertEqual(result["technicalUpdates"]["regions"], ["A", "B", "C"])

    def test_audio_track_codec_comma_is_preserved_without_parentheses(self):
        result = canonicalize_plugin_result(
            "bluray_com",
            "technical_specs",
            {
                "technicalSpecs": {
                    "format": "4K UHD",
                    "audioTracks": "English: Dolby TrueHD 7.1 48kHz, 24-bit, Spanish: Dolby Digital 5.1",
                },
            },
        )

        self.assertEqual(
            result["technicalUpdates"]["audio_tracks"],
            ["English: Dolby TrueHD 7.1 48kHz, 24-bit", "Spanish: Dolby Digital 5.1"],
        )

    def test_execution_summary_marks_format_blocked_and_applied_sources(self):
        plugins = [
            {"id": "tmdb", "name": "TMDb", "order_index": 10},
            {"id": "bluray_com", "name": "Blu-ray.com", "order_index": 20},
        ]
        executions = [
            {"pluginId": "tmdb", "entrypoint": "lookup_external_id", "status": "skipped", "state": "needs_configuration"},
            {"pluginId": "bluray_com", "entrypoint": "technical_specs", "status": "ok", "resultStatus": "hit", "elapsedMs": 20},
        ]
        proposal = {
            "provenance": [{"pluginId": "bluray_com", "field": "audio_tracks"}],
            "skipped": [{"pluginId": "bluray_com", "field": "hdr", "reason": "format mismatch: target=Blu-ray, source=4K UHD"}],
        }

        summary = summarize_metadata_execution(plugins=plugins, executions=executions, results=[], proposal=proposal)

        self.assertEqual(summary[0]["state"], "needs_configuration")
        self.assertEqual(summary[1]["state"], "applied")
        self.assertEqual(summary[1]["formatBlockedFields"], 1)

    def test_metadata_fetch_audit_payload_keeps_provider_field_details(self):
        movie = {"title": "Aladdin", "barcode": "8717418557683", "format": "4K UHD"}
        preview = {
            "sourceOrder": ["movievault_26", "tmdb"],
            "executions": [
                {
                    "pluginId": "movievault_26",
                    "entrypoint": "search_barcode",
                    "status": "ok",
                    "resultStatus": "hit",
                    "candidateCount": 1,
                    "elapsedMs": 50,
                }
            ],
            "results": [
                {
                    "pluginId": "movievault_26",
                    "sourceLabel": "MovieVault 26",
                    "entrypoint": "search_barcode",
                    "status": "hit",
                    "movieUpdates": {"rating": "7.1"},
                    "metadataUpdates": {"poster_url": "https://example/poster.jpg"},
                    "technicalUpdates": {"hdr": "HDR10"},
                    "mediaUpdates": {"poster": {"sourceUrl": "https://example/poster.jpg"}},
                    "identifiers": {"tmdb": "420817"},
                    "credits": [{"role": "crew", "name": "Guy Ritchie", "job": "Director"}],
                    "candidates": [{}],
                }
            ],
            "proposal": {
                "provenance": [{"pluginId": "movievault_26", "field": "rating", "target": "movie"}],
                "skipped": [{"pluginId": "tmdb", "field": "title", "reason": "existing value retained"}],
                "credits": [{"role": "crew", "name": "Guy Ritchie", "job": "Director"}],
            },
            "proposalStats": {"acceptedFields": 1, "skippedFields": 1},
        }

        payload = metadata_fetch_audit_payload(
            movie_id="2b9e",
            movie=movie,
            dry_run=False,
            preview=preview,
            applied={"changed": True, "revision": 12, "applied": {"movieUpdates": {"rating": "7.1"}}},
        )

        self.assertEqual(payload["sourceOrder"], ["movievault_26", "tmdb"])
        self.assertEqual(payload["providerResults"][0]["pluginId"], "movievault_26")
        self.assertEqual(payload["providerResults"][0]["movieFields"], ["rating"])
        self.assertEqual(payload["providerResults"][0]["metadataFields"], ["poster_url"])
        self.assertEqual(payload["providerResults"][0]["technicalFields"], ["hdr"])
        self.assertEqual(payload["providerResults"][0]["mediaKinds"], ["poster"])
        self.assertEqual(payload["providerResults"][0]["identifierProviders"], ["tmdb"])
        self.assertEqual(payload["providerResults"][0]["creditCount"], 1)
        self.assertEqual(payload["creditStats"]["proposed"], 1)
        self.assertNotIn("Authorization", str(payload))
        self.assertNotIn("apiToken", str(payload))

    def test_receiver_contribution_payload_uses_public_applied_metadata(self):
        movie = {
            "id": "2b9e",
            "public_id": "legacy-movie-95",
            "title": "Aladdin",
            "original_title": "Aladdin",
            "year": "2019",
            "barcode": "8717418557683",
            "format": "4K UHD",
        }
        preview = {
            "proposal": {
                "movieUpdates": {"rating": "7.1"},
                "metadataUpdates": {"genre": "Adventure"},
                "technicalUpdates": {"hdr": "HDR10"},
                "mediaUpdates": {"poster": {"sourceUrl": "https://example/poster.jpg"}},
                "identifiers": {"tmdb": "420817", "imdb": "tt6139732"},
                "provenance": [
                    {"pluginId": "tmdb", "field": "rating"},
                    {"pluginId": "bluray_com", "field": "hdr"},
                ],
            },
            "results": [
                {
                    "pluginId": "tmdb",
                    "sourceLabel": "TMDb",
                    "movieUpdates": {
                        "title": "Aladdin TMDb",
                        "original_title": "Aladdin Original TMDb",
                    },
                }
            ],
        }

        payload = receiver_contribution_payload(
            movie_id="2b9e",
            movie=movie,
            preview=preview,
            applied={"changed": True, "applied": {"movieUpdates": {"rating": "7.1"}}},
        )

        self.assertEqual(payload["entityType"], "movie")
        self.assertEqual(payload["identity"], "legacy-movie-95")
        self.assertEqual(payload["sourceReference"]["barcode"], "8717418557683")
        self.assertEqual(payload["payload"]["title"], "Aladdin")
        self.assertEqual(payload["payload"]["rating"], "7.1")
        self.assertEqual(payload["payload"]["hdr"], "HDR10")
        self.assertEqual(payload["payload"]["tmdbId"], "420817")
        self.assertEqual(payload["metadata"]["sourceProviders"], ["bluray_com", "tmdb"])
        self.assertEqual(payload["metadata"]["tmdbTitle"], "Aladdin TMDb")
        self.assertEqual(payload["metadata"]["tmdbOriginalTitle"], "Aladdin Original TMDb")
        self.assertEqual(
            payload["metadata"]["providerTitleHints"],
            [
                {
                    "pluginId": "tmdb",
                    "sourceLabel": "TMDb",
                    "title": "Aladdin TMDb",
                    "originalTitle": "Aladdin Original TMDb",
                }
            ],
        )
        self.assertNotIn("watchHistory", str(payload))
        self.assertNotIn("privateNotes", str(payload))

    def test_receiver_contribution_payload_includes_full_current_metadata(self):
        movie = {
            "id": "9f12",
            "public_id": "legacy-movie-73",
            "title": "A Star Is Born",
            "original_title": "A Star Is Born",
            "sort_title": "Star Is Born, A",
            "year": "2018",
            "barcode": "0883929598083",
            "format": "4K UHD",
            "edition": "Special Edition",
            "country": "US",
            "language": "en",
            "runtime_minutes": 136,
            "overview": "A musician helps a young singer find fame.",
            "rating": "7.5",
            "release_date": "2018-10-05",
        }
        preview = {
            "proposal": {
                "technicalUpdates": {"audio_tracks": ["English: Dolby Atmos"]},
                "identifiers": {"tmdb": "332562", "imdb": "tt1517451"},
                "provenance": [{"pluginId": "bluray_com", "field": "audio_tracks"}],
            },
            "results": [],
        }
        credits = [
            {"name": "Bradley Cooper", "credit_type": "crew", "job": "Director", "character": ""},
            {"name": "Lady Gaga", "credit_type": "actor", "job": "", "character": "Ally"},
            {"name": "Bradley Cooper", "credit_type": "actor", "job": "", "character": "Jackson"},
        ]

        payload = receiver_contribution_payload(
            movie_id="9f12",
            movie=movie,
            preview=preview,
            applied={"changed": True, "applied": {}},
            credits=credits,
        )

        fields = payload["payload"]
        self.assertEqual(fields["overview"], "A musician helps a young singer find fame.")
        self.assertEqual(fields["releaseDate"], "2018-10-05")
        self.assertEqual(fields["runtimeMinutes"], 136)
        self.assertEqual(fields["country"], "US")
        self.assertEqual(fields["language"], "en")
        self.assertEqual(fields["rating"], "7.5")
        self.assertEqual(fields["edition"], "Special Edition")
        self.assertEqual(fields["director"], "Bradley Cooper")
        self.assertIn("Lady Gaga", fields["actor"])
        self.assertEqual(fields["audio_tracks"], ["English: Dolby Atmos"])
        self.assertEqual(len(fields["credits"]), 3)

    def test_receiver_contribution_payload_release_date_object_serialized(self):
        from datetime import date

        movie = {
            "id": "aa01",
            "public_id": "legacy-movie-12",
            "title": "Dune",
            "year": "2021",
            "format": "4K UHD",
            "release_date": date(2021, 10, 22),
        }
        payload = receiver_contribution_payload(
            movie_id="aa01",
            movie=movie,
            preview={"proposal": {}, "results": []},
            applied={"changed": True, "applied": {}},
        )
        self.assertEqual(payload["payload"]["releaseDate"], "2021-10-22")


    def test_receiver_contribution_payload_excludes_locked_fields(self):
        movie = {
            "id": "cc05",
            "public_id": "legacy-movie-44",
            "title": "Heat",
            "original_title": "Heat",
            "year": "1995",
            "barcode": "0883929000000",
            "format": "4K UHD",
            "overview": "Hand-curated synopsis kept by the collector.",
            "country": "US",
            "language": "en",
            "metadata": {
                "director": "Michael Mann",
                "distributor": "Warner",
                "field_locks": ["overview", "director", "country"],
            },
        }
        preview = {
            "proposal": {
                "metadataUpdates": {"director": "Provider Director", "genre": "Crime"},
                "provenance": [{"pluginId": "tmdb", "field": "genre"}],
            },
            "results": [],
        }
        payload = receiver_contribution_payload(
            movie_id="cc05",
            movie=movie,
            preview=preview,
            applied={"changed": True, "applied": {}},
        )
        sent = payload["payload"]
        self.assertNotIn("overview", sent)
        self.assertNotIn("director", sent)
        self.assertNotIn("country", sent)
        self.assertEqual(sent.get("genre"), "Crime")
        self.assertEqual(sent.get("title"), "Heat")
        self.assertEqual(sent.get("language"), "en")

    def test_receiver_contribution_payload_includes_localizations(self):
        movie = {
            "id": "dd07",
            "public_id": "legacy-movie-77",
            "title": "Spirited Away",
            "year": "2001",
            "format": "4K UHD",
        }
        preview = {
            "proposal": {
                "localizations": [
                    {"lang": "fr", "title": "Le Voyage de Chihiro", "overview": "Une fille..."},
                    {"lang": "de-DE", "title": "Chihiros Reise", "overview": "Ein Maedchen..."},
                    {"lang": "pt", "overview": ""},
                ],
            },
            "results": [],
        }
        payload = receiver_contribution_payload(
            movie_id="dd07",
            movie=movie,
            preview=preview,
            applied={"changed": True, "applied": {}},
        )
        localizations = payload["payload"]["localizations"]
        self.assertEqual(len(localizations), 2)
        by_lang = {item["lang"]: item for item in localizations}
        self.assertEqual(by_lang["fr"]["title"], "Le Voyage de Chihiro")
        self.assertEqual(by_lang["fr"]["overview"], "Une fille...")
        self.assertEqual(by_lang["de-DE"]["title"], "Chihiros Reise")
        self.assertNotIn("pt", by_lang)

    def test_receiver_contribution_payload_localizations_respect_locks(self):
        movie = {
            "id": "ee08",
            "public_id": "legacy-movie-78",
            "title": "Heat",
            "year": "1995",
            "format": "4K UHD",
            "metadata": {"field_locks": ["overview"]},
        }
        preview = {
            "proposal": {
                "localizations": [
                    {"lang": "fr", "title": "Heat FR", "overview": "Synopsis FR"},
                    {"lang": "es", "overview": "Sinopsis ES"},
                ],
            },
            "results": [],
        }
        payload = receiver_contribution_payload(
            movie_id="ee08",
            movie=movie,
            preview=preview,
            applied={"changed": True, "applied": {}},
        )
        localizations = payload["payload"]["localizations"]
        by_lang = {item["lang"]: item for item in localizations}
        self.assertIn("fr", by_lang)
        self.assertEqual(by_lang["fr"].get("title"), "Heat FR")
        self.assertNotIn("overview", by_lang["fr"])
        self.assertNotIn("es", by_lang)


if __name__ == "__main__":
    unittest.main()
