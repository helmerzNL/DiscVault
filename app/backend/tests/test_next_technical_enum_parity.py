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

from app.backend import next_movievault_v2


UI_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "next_views_ui.py").read_text(
    encoding="utf-8"
)


def js_array(name: str) -> list[str]:
    match = re.search(rf"const {re.escape(name)} = (\[[^\]]*\]);", UI_SOURCE)
    if match is None:
        raise AssertionError(f"{name} is not declared in next_views_ui.py")
    return ast.literal_eval(match.group(1))


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
