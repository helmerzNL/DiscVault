"""The technical vocabularies exist twice: as Python sets used to validate what
comes in from MovieVault, and as JavaScript arrays used to build the edit form's
selects and checkboxes.

Duplication is unavoidable - the browser cannot import the Python - but silent
drift is not. If the two disagree, the failure is quiet and nasty: a value the
backend happily stores becomes one the user cannot select back, so an unrelated
save rewrites it. This test makes that drift loud instead.
"""

import ast
import os
import pathlib
import re
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_discs
from app.backend import next_movievault_v2


UI_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "next_views_ui.py").read_text(
    encoding="utf-8"
)


def js_array(name: str) -> list[str]:
    match = re.search(rf"const {re.escape(name)} = (\[[^\]]*\]);", UI_SOURCE)
    if match is None:
        raise AssertionError(f"{name} is not declared in next_views_ui.py")
    return ast.literal_eval(match.group(1))


def js_object(name: str) -> dict:
    """Read a flat JS object literal whose values are string arrays.

    The same trick `js_array` uses: the browser cannot import Python, so the
    only way to keep the two copies honest is to parse the one that lives in
    the page. Deliberately narrow — a `{...}` whose values are anything but
    string arrays is not something this should silently accept.
    """
    match = re.search(rf"const {re.escape(name)} = (\{{[^}}]*\}});", UI_SOURCE)
    if match is None:
        raise AssertionError(f"{name} is not declared in next_views_ui.py")
    return ast.literal_eval(match.group(1))


def js_format_options() -> list[str]:
    """The `value:` strings of MOVIE_FORMAT_OPTIONS, in declaration order."""
    match = re.search(r"const MOVIE_FORMAT_OPTIONS = \[(.*?)\];", UI_SOURCE, re.S)
    if match is None:
        raise AssertionError("MOVIE_FORMAT_OPTIONS is not declared in next_views_ui.py")
    return re.findall(r'value:\s*"([^"]+)"', match.group(1))


class TechnicalEnumParityTests(unittest.TestCase):
    def test_audio_codecs_match(self):
        self.assertEqual(
            set(js_array("AUDIO_TRACK_CODEC_VALUES")),
            next_movievault_v2.AUDIO_TRACK_CODECS,
        )

    def test_audio_channels_match(self):
        self.assertEqual(
            set(js_array("AUDIO_TRACK_CHANNEL_VALUES")),
            next_movievault_v2.AUDIO_TRACK_CHANNELS,
        )

    def test_immersive_formats_match(self):
        self.assertEqual(
            set(js_array("AUDIO_TRACK_IMMERSIVE_VALUES")),
            next_movievault_v2.AUDIO_TRACK_IMMERSIVE_FORMATS,
        )

    def test_subtitle_types_match(self):
        self.assertEqual(
            set(js_array("SUBTITLE_TYPE_VALUES")),
            next_movievault_v2.SUBTITLE_TYPES,
        )

    def test_video_codecs_match(self):
        self.assertEqual(set(js_array("VIDEO_CODEC_VALUES")), next_movievault_v2.VIDEO_CODECS)

    def test_hdr_formats_match(self):
        self.assertEqual(set(js_array("HDR_FORMAT_VALUES")), next_movievault_v2.HDR_FORMATS)

    def test_video_resolutions_match(self):
        self.assertEqual(
            set(js_array("VIDEO_RESOLUTION_VALUES")), next_movievault_v2.VIDEO_RESOLUTIONS
        )

    def test_disc_regions_match(self):
        self.assertEqual(set(js_array("DISC_REGION_VALUES")), next_movievault_v2.DISC_REGIONS)

    def test_disc_types_match(self):
        """The per-disc medium list. Drift here is worse than for the release-level
        enums: an unknown disc type is refused outright by ``normalize_disc_type``
        rather than stored raw, so a value the form can offer but the backend does
        not know turns an ordinary save into a 400."""
        self.assertEqual(set(js_array("DISC_TYPE_VALUES")), set(next_discs.DISC_TYPES))

    def test_disc_roles_match(self):
        self.assertEqual(set(js_array("DISC_ROLE_VALUES")), set(next_discs.DISC_ROLES))

    def test_the_format_seed_map_only_names_disc_types_the_backend_accepts(self):
        """`MOVIE_FORMAT_DISC_TYPES` pre-fills the disc editor from the release's
        format. A value here that `normalize_disc_type` does not know is not
        stored raw — it is refused — so the drift would turn the Add disc button
        into a 400 on save, having already filled the form in."""
        seeded = {value for values in js_object("MOVIE_FORMAT_DISC_TYPES").values()
                  for value in values}
        self.assertTrue(seeded)
        self.assertEqual(seeded - set(next_discs.DISC_TYPES), set())

    def test_the_format_seed_map_is_keyed_on_formats_that_can_be_selected(self):
        """A key nobody can produce is dead code. `normalizedMovieFormatValue`
        folds every stored spelling onto one of these seven, so the map has to
        speak the same seven."""
        self.assertEqual(
            set(js_object("MOVIE_FORMAT_DISC_TYPES")) - set(js_format_options()), set()
        )

    def test_the_combo_format_seeds_both_of_its_discs(self):
        """The one format that already names two discs. Splitting it is reading
        the record rather than guessing — and it is the case where retyping the
        second disc's medium by hand would be most obviously silly."""
        self.assertEqual(
            js_object("MOVIE_FORMAT_DISC_TYPES")["4K UHD + Blu-ray"],
            ["uhd_bluray", "bluray"],
        )

    def test_the_two_disc_axes_have_their_own_option_lists(self):
        """Medium and content are asked as two selects. Sharing a value between
        them would let one answer satisfy both questions -- the exact confusion
        migration 067 split `packaging` to end."""
        self.assertEqual(
            set(js_array("DISC_TYPE_VALUES")) & set(js_array("DISC_ROLE_VALUES")), set()
        )

    def test_every_enum_value_has_a_translation_key_in_the_source_locale(self):
        import json

        locale_path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "frontend"
            / "i18n"
            / "next"
            / "en-US.json"
        )
        messages = json.loads(locale_path.read_text(encoding="utf-8"))

        def camel(value: str) -> str:
            head, *rest = value.split("_")
            return head + "".join(part[:1].upper() + part[1:] for part in rest)

        expected = [
            ("movieAudioCodec", next_movievault_v2.AUDIO_TRACK_CODECS),
            ("movieAudioImmersive", next_movievault_v2.AUDIO_TRACK_IMMERSIVE_FORMATS),
            ("movieVideoCodec", next_movievault_v2.VIDEO_CODECS),
            ("movieHdrFormat", next_movievault_v2.HDR_FORMATS),
            ("movieSubtitleType", next_movievault_v2.SUBTITLE_TYPES),
            ("movieDiscType", set(next_discs.DISC_TYPES)),
            ("movieDiscRole", set(next_discs.DISC_ROLES)),
        ]
        for prefix, values in expected:
            for value in sorted(values):
                key = f"{prefix}.{camel(value)}"
                with self.subTest(key=key):
                    self.assertIn(key, messages)
                    self.assertTrue(messages[key].strip())

    def test_resolutions_and_regions_are_deliberately_not_translated(self):
        """`2160p`, `5.1` and region letters read the same in every locale;
        inventing keys for them would be 29 files of noise. Region *free* is the
        one exception, because it is a word."""
        import json

        locale_path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "frontend"
            / "i18n"
            / "next"
            / "en-US.json"
        )
        messages = json.loads(locale_path.read_text(encoding="utf-8"))
        self.assertNotIn("movieVideoResolution.2160p", messages)
        self.assertIn("movieDiscRegion.free", messages)


if __name__ == "__main__":
    unittest.main()
