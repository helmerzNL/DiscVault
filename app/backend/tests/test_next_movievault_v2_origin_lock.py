"""Behavioural tests for the enforced (non-UI-editable) MovieVault v2 origin.

These tests deliberately avoid a module-level ``MOVIEVAULT_V2_ORIGIN`` override
so they can assert the default/enforcement behaviour directly. Each test that
needs a specific environment controls it locally with ``patch.dict``.
"""

import json
import os
import unittest
from unittest.mock import patch
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app
from app.backend import next_metadata
from app.backend import next_plugin_runtime
from app.backend.next_movievault_v2 import (
    DEFAULT_MOVIEVAULT_V2_BUCKET_FALLBACK,
    DEFAULT_MOVIEVAULT_V2_ORIGIN,
    MOVIEVAULT_V2_BUCKET_FALLBACK_ENV,
    MOVIEVAULT_V2_ORIGIN_ENV,
    MOVIEVAULT_V2_PLUGIN_ID,
    enforced_bucket_fallback,
    enforced_origin,
    normalize_origin,
)


BUNDLED_PLUGIN_DIR = os.path.join(repo_root, "app", "backend", "next_plugins")


def _bundled_manifest(plugin_id):
    with open(
        os.path.join(BUNDLED_PLUGIN_DIR, plugin_id, "manifest.json"), encoding="utf-8"
    ) as handle:
        return json.load(handle)


class EnforcedOriginResolverTests(unittest.TestCase):
    def test_default_when_env_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(MOVIEVAULT_V2_ORIGIN_ENV, None)
            self.assertEqual(
                enforced_origin(), normalize_origin(DEFAULT_MOVIEVAULT_V2_ORIGIN)
            )

    def test_env_override_wins(self):
        with patch.dict(
            os.environ, {MOVIEVAULT_V2_ORIGIN_ENV: "https://movievault.example"}
        ):
            self.assertEqual(enforced_origin(), "https://movievault.example")

    def test_env_override_is_normalized(self):
        with patch.dict(
            os.environ, {MOVIEVAULT_V2_ORIGIN_ENV: "https://Movies2.VaultStack.eu/"}
        ):
            self.assertEqual(enforced_origin(), "https://movies2.vaultstack.eu")

    def test_invalid_env_falls_back_to_default(self):
        for bad in ("not-a-url", "ftp://movievault.example", "https://x/path"):
            with self.subTest(bad=bad):
                with patch.dict(os.environ, {MOVIEVAULT_V2_ORIGIN_ENV: bad}):
                    self.assertEqual(
                        enforced_origin(),
                        normalize_origin(DEFAULT_MOVIEVAULT_V2_ORIGIN),
                    )

    def test_blank_env_falls_back_to_default(self):
        with patch.dict(os.environ, {MOVIEVAULT_V2_ORIGIN_ENV: "   "}):
            self.assertEqual(
                enforced_origin(), normalize_origin(DEFAULT_MOVIEVAULT_V2_ORIGIN)
            )


class EnforcedSettingsSchemaTests(unittest.TestCase):
    def test_origin_stripped_from_v2_list_schema(self):
        manifest = {
            "id": MOVIEVAULT_V2_PLUGIN_ID,
            "settingsSchema": {
                "settings": [
                    {"name": "origin", "type": "string"},
                    {"name": "keep_me", "type": "string"},
                ]
            },
        }
        result = next_plugin_runtime.enforced_settings_schema(manifest)
        names = [item.get("name") for item in result["settings"]]
        self.assertNotIn("origin", names)
        self.assertIn("keep_me", names)

    def test_origin_stripped_from_v2_dict_schema(self):
        manifest = {
            "id": MOVIEVAULT_V2_PLUGIN_ID,
            "settingsSchema": {
                "settings": {
                    "origin": {"type": "string"},
                    "keep_me": {"type": "string"},
                }
            },
        }
        result = next_plugin_runtime.enforced_settings_schema(manifest)
        self.assertNotIn("origin", result["settings"])
        self.assertIn("keep_me", result["settings"])

    def test_other_plugin_schema_unchanged(self):
        manifest = {
            "id": "some_other_plugin",
            "settingsSchema": {"settings": [{"name": "origin"}]},
        }
        result = next_plugin_runtime.enforced_settings_schema(manifest)
        self.assertEqual([i.get("name") for i in result["settings"]], ["origin"])

    def test_does_not_mutate_input(self):
        schema = {"settings": [{"name": "origin"}, {"name": "keep_me"}]}
        manifest = {"id": MOVIEVAULT_V2_PLUGIN_ID, "settingsSchema": schema}
        next_plugin_runtime.enforced_settings_schema(manifest)
        self.assertEqual(
            [i["name"] for i in schema["settings"]], ["origin", "keep_me"]
        )


class EnforcedBucketFallbackTests(unittest.TestCase):
    """The anonymous bucket fallback is what resolves a disc the locally synced
    index does not carry yet, so it is enforced on rather than switchable."""

    def test_on_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(enforced_bucket_fallback())
            self.assertTrue(DEFAULT_MOVIEVAULT_V2_BUCKET_FALLBACK)

    def test_env_can_turn_it_off(self):
        for value in ("0", "false", "FALSE", "no", "Off", " off "):
            with self.subTest(value=value):
                with patch.dict(
                    os.environ, {MOVIEVAULT_V2_BUCKET_FALLBACK_ENV: value}, clear=True
                ):
                    self.assertFalse(enforced_bucket_fallback())

    def test_unrecognised_env_falls_back_to_the_default(self):
        # A misconfigured deployment must never silently lose the fallback.
        for value in ("", "   ", "maybe", "1", "true", "yes"):
            with self.subTest(value=value):
                with patch.dict(
                    os.environ, {MOVIEVAULT_V2_BUCKET_FALLBACK_ENV: value}, clear=True
                ):
                    self.assertTrue(enforced_bucket_fallback())

    def test_bucket_fallback_stripped_from_v2_list_schema(self):
        manifest = {
            "id": MOVIEVAULT_V2_PLUGIN_ID,
            "settingsSchema": {
                "settings": [
                    {"name": "bucketFallback", "type": "boolean"},
                    {"name": "keep_me", "type": "string"},
                ]
            },
        }
        result = next_plugin_runtime.enforced_settings_schema(manifest)
        names = [item.get("name") for item in result["settings"]]
        self.assertNotIn("bucketFallback", names)
        self.assertIn("keep_me", names)

    def test_bucket_fallback_stripped_from_v2_dict_schema(self):
        manifest = {
            "id": MOVIEVAULT_V2_PLUGIN_ID,
            "settingsSchema": {
                "settings": {
                    "bucketFallback": {"type": "boolean"},
                    "keep_me": {"type": "string"},
                }
            },
        }
        result = next_plugin_runtime.enforced_settings_schema(manifest)
        self.assertNotIn("bucketFallback", result["settings"])
        self.assertIn("keep_me", result["settings"])

    def test_other_plugin_keeps_its_bucket_fallback(self):
        manifest = {
            "id": "some_other_plugin",
            "settingsSchema": {"settings": [{"name": "bucketFallback"}]},
        }
        result = next_plugin_runtime.enforced_settings_schema(manifest)
        self.assertEqual(
            [i.get("name") for i in result["settings"]], ["bucketFallback"]
        )

    def test_bundled_manifest_still_resolves_as_configured(self):
        """Stripping must not empty the schema: plugin_settings_configured()
        returns False for an empty field list, which would make enabling the
        plugin fail with "configuration is incomplete"."""
        manifest = _bundled_manifest(MOVIEVAULT_V2_PLUGIN_ID)
        schema = next_plugin_runtime.enforced_settings_schema(manifest)
        names = [i.get("name") for i in schema["settings"]]
        self.assertNotIn("origin", names)
        self.assertNotIn("bucketFallback", names)
        self.assertTrue(names)
        self.assertTrue(
            next_plugin_runtime.plugin_settings_configured(
                schema, next_plugin_runtime.plugin_setting_defaults(schema)
            )
        )


class RequiresConfigTests(unittest.TestCase):
    def test_metadata_v2_never_requires_config(self):
        plugin = {"id": MOVIEVAULT_V2_PLUGIN_ID}
        self.assertFalse(
            next_metadata.plugin_requires_config(plugin, {}, "resolve_metadata")
        )

    def test_app_v2_never_requires_config(self):
        plugin = {"id": MOVIEVAULT_V2_PLUGIN_ID}
        self.assertFalse(
            next_app.plugin_requires_config_for_entrypoint(
                plugin, {}, "resolve_metadata"
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
