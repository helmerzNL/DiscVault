"""A disc matched by its own release gets its format corrected, even when that release is thin.

Found after the box-set refresh shipped. A box set's four members each carried their MovieVault
release, the refresh was queued for each, and the format stayed Blu-ray. The format rule of
identity-routing 5a lets a *release-backed* source correct a stored format, but the merge decided
"release-backed" from the technical data a result carries (audio, subtitles, HDR) or from a `release`
block. A release the catalogue holds with nothing but a title and a format -- a box-set member, usually
-- has neither, so the rule never ran and the refresh said "existing value retained".

These tests run MovieVault-shaped results through the real `canonicalize_plugin_result` and
`merge_metadata_results`, because the earlier unit test of `should_apply_format` handed the function the
flag directly and so could not see that the pipeline never set it.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from typing import Any
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_metadata as metadata

_PLUGIN_PATH = os.path.join(repo_root, "app", "backend", "next_plugins", "movievault_v2", "plugin.py")
_spec = importlib.util.spec_from_file_location("movievault_v2_plugin_under_test", _PLUGIN_PATH)
assert _spec is not None and _spec.loader is not None
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)

RELEASE_ID = "9cf215e1-8b25-4776-b3cb-682af9b4f247"
FILM_ID = "7da26da9-78b2-4927-b8b7-dac5597d5d27"

THIN_RELEASE = {
    "releaseId": RELEASE_ID,
    "filmId": FILM_ID,
    "canonicalTitle": "Rocky II",
    "releaseTitle": "Rocky II",
    "format": "4K UHD",
}
RELEASE_WITH_AUDIO = {
    **THIN_RELEASE,
    "audioTracks": [{"languageCode": "en", "codec": "dts_hd_ma", "channels": "5.1"}],
}


def merge(record: dict[str, Any], *, current_format: str, entrypoint: str = "movie_details", release: bool = True) -> dict[str, Any]:
    """What the refresh would do with `record` for a disc stored as `current_format`."""
    raw = {"status": "hit", **plugin._release(record)}
    if not release:
        # The same result with the catalogue's release identity stripped, as another kind of source would send it.
        for key in ("id", "releaseId", "filmId", "sourceRef", "identifiers"):
            raw.pop(key, None)
    canonical = metadata.canonicalize_plugin_result("movievault_v2", entrypoint, raw)
    return metadata.merge_metadata_results(
        current={"format": current_format, "metadata": {}},
        technical_current={},
        results=[canonical],
        overwrite_enabled=False,
        target_format=metadata.normalize_media_format(current_format),
    )


def format_reason(merged: dict[str, Any]) -> str:
    reasons = [item["reason"] for item in merged.get("skipped", []) if item.get("field") == "format"]
    return reasons[0] if reasons else ""


class ThinReleaseFormatCorrectionTests(unittest.TestCase):
    def test_a_thin_release_corrects_the_format_of_the_disc_it_was_matched_to(self) -> None:
        # The regression: title and format only, no technical data, matched by release id.
        merged = merge(THIN_RELEASE, current_format="Blu-ray")
        self.assertEqual(merged["movieUpdates"].get("format"), "4K UHD", format_reason(merged))

    def test_the_same_through_the_barcode_entrypoint(self) -> None:
        merged = merge(THIN_RELEASE, current_format="Blu-ray", entrypoint="search_barcode")
        self.assertEqual(merged["movieUpdates"].get("format"), "4K UHD")

    def test_a_release_with_technical_data_still_corrects_it(self) -> None:
        merged = merge(RELEASE_WITH_AUDIO, current_format="Blu-ray")
        self.assertEqual(merged["movieUpdates"].get("format"), "4K UHD")

    def test_a_title_search_still_may_not_restate_an_unrelated_format(self) -> None:
        # `search_title` can return another pressing of the film, so only the identity entrypoints may
        # replace one format with an unrelated one. Being release-backed does not lift that.
        merged = merge(THIN_RELEASE, current_format="Blu-ray", entrypoint="search_title")
        self.assertNotIn("format", merged["movieUpdates"])
        self.assertEqual(format_reason(merged), "existing value retained")

    def test_a_title_search_may_still_refine_toward_the_fuller_pack(self) -> None:
        merged = merge({**THIN_RELEASE, "format": "4K UHD + Blu-ray"}, current_format="4K UHD", entrypoint="search_title")
        self.assertEqual(merged["movieUpdates"].get("format"), "4K UHD + Blu-ray")

    def test_a_combo_is_never_narrowed_to_one_of_its_discs(self) -> None:
        merged = merge({**THIN_RELEASE, "format": "Blu-ray"}, current_format="4K UHD + Blu-ray")
        self.assertNotIn("format", merged["movieUpdates"])
        self.assertIn("narrow", format_reason(merged))

    def test_a_result_that_names_no_release_gains_nothing(self) -> None:
        merged = merge(THIN_RELEASE, current_format="Blu-ray", release=False)
        self.assertNotIn("format", merged["movieUpdates"])

    def test_an_unknown_format_is_still_refused(self) -> None:
        merged = merge({**THIN_RELEASE, "format": "Mystery disc"}, current_format="Blu-ray")
        self.assertNotIn("format", merged["movieUpdates"])


class ResultNamesAReleaseTests(unittest.TestCase):
    def test_reads_the_release_id_from_the_raw_result(self) -> None:
        self.assertTrue(metadata.result_names_a_release({"raw": {"releaseId": RELEASE_ID}}))

    def test_nothing_else_counts(self) -> None:
        for result in ({}, {"raw": None}, {"raw": {}}, {"raw": {"releaseId": "  "}}, {"raw": {"filmId": FILM_ID}}):
            self.assertFalse(metadata.result_names_a_release(result), result)


class BlastRadiusTests(unittest.TestCase):
    def test_no_field_other_than_the_format_is_decided_differently(self) -> None:
        """The flag is widened for the format alone; every other release field keeps the narrow one."""
        record = {**THIN_RELEASE, "edition": "Steelbook", "studio": "Studio X", "runtimeMinutes": 119}
        actual = merge(record, current_format="Blu-ray")
        with mock.patch.object(metadata, "result_names_a_release", return_value=False):
            before = merge(record, current_format="Blu-ray")

        def without_format(merged: dict[str, Any]) -> dict[str, Any]:
            return {
                "movie": {k: v for k, v in merged["movieUpdates"].items() if k != "format"},
                "metadata": merged["metadataUpdates"],
                "technical": merged["technicalUpdates"],
                "skipped": [item for item in merged.get("skipped", []) if item.get("field") != "format"],
            }

        self.assertEqual(without_format(actual), without_format(before))
        self.assertNotIn("format", before["movieUpdates"], "the old behaviour: refused")
        self.assertEqual(actual["movieUpdates"].get("format"), "4K UHD")


if __name__ == "__main__":
    unittest.main()
