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

from app.backend import next_app


def _fake_response(status_code=200, json_body=None, text=""):
    return types.SimpleNamespace(
        status_code=status_code,
        json=lambda: json_body if json_body is not None else {},
        text=text,
    )


class UpdateVersionTupleTests(unittest.TestCase):
    def test_parses_semver(self):
        self.assertEqual(next_app._update_version_tuple("26.2.45"), (26, 2, 45))

    def test_strips_leading_v_and_prerelease(self):
        self.assertEqual(next_app._update_version_tuple("v26.3.0-beta"), (26, 3, 0))

    def test_blank_is_empty_tuple(self):
        self.assertEqual(next_app._update_version_tuple(""), ())
        self.assertEqual(next_app._update_version_tuple(None), ())

    def test_ordering(self):
        self.assertGreater(
            next_app._update_version_tuple("26.2.45"),
            next_app._update_version_tuple("26.2.0"),
        )


class NormalizeUpdateChannelTests(unittest.TestCase):
    def test_known_channels(self):
        for value in ("auto", "beta", "stable"):
            self.assertEqual(next_app.normalize_update_channel(value), value)

    def test_legacy_v26_maps_to_stable(self):
        self.assertEqual(next_app.normalize_update_channel("v26"), "stable")
        self.assertEqual(next_app.normalize_update_channel("V26"), "stable")

    def test_unknown_falls_back_to_auto(self):
        self.assertEqual(next_app.normalize_update_channel("nightly"), "auto")
        self.assertEqual(next_app.normalize_update_channel(None), "auto")

    def test_case_insensitive(self):
        self.assertEqual(next_app.normalize_update_channel("BETA"), "beta")


class ResolveUpdateChannelTests(unittest.TestCase):
    def test_explicit_request_wins(self):
        channel, source = next_app.resolve_update_channel(None, "stable")
        self.assertEqual((channel, source), ("stable", "request"))

    def test_stored_setting_used_when_request_auto(self):
        with mock.patch.object(next_app, "app_setting_value", return_value="v26"):
            channel, source = next_app.resolve_update_channel(None, "auto")
        # Legacy "v26" preference is remapped to its successor "stable".
        self.assertEqual((channel, source), ("stable", "setting"))

    def test_env_var_used_when_setting_auto(self):
        with mock.patch.object(next_app, "app_setting_value", return_value="auto"), \
                mock.patch.dict(os.environ, {"DISCVAULT_UPDATE_CHANNEL": "beta"}, clear=False):
            channel, source = next_app.resolve_update_channel(None, "auto")
        self.assertEqual((channel, source), ("beta", "env"))

    def test_heuristic_beta_when_running_ahead_of_stable(self):
        with mock.patch.object(next_app, "app_setting_value", return_value="auto"), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(next_app, "build_version", return_value="26.2.45"), \
                mock.patch.object(
                    next_app, "github_latest_stable_release", return_value={"version": "26.2.0"}
                ):
            channel, source = next_app.resolve_update_channel(None, "auto")
        self.assertEqual((channel, source), ("beta", "auto"))

    def test_heuristic_stable_when_running_not_ahead(self):
        with mock.patch.object(next_app, "app_setting_value", return_value="auto"), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(next_app, "build_version", return_value="26.2.0"), \
                mock.patch.object(
                    next_app, "github_latest_stable_release", return_value={"version": "26.2.0"}
                ):
            channel, source = next_app.resolve_update_channel(None, "auto")
        self.assertEqual((channel, source), ("stable", "auto"))

    def test_heuristic_falls_back_to_stable_when_release_unavailable(self):
        with mock.patch.object(next_app, "app_setting_value", return_value="auto"), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(next_app, "github_latest_stable_release", return_value=None):
            channel, source = next_app.resolve_update_channel(None, "auto")
        self.assertEqual((channel, source), ("stable", "auto-fallback"))


class PerformUpdateCheckTests(unittest.TestCase):
    def test_update_available_on_beta_branch(self):
        with mock.patch.object(next_app, "build_version", return_value="26.2.45"), \
                mock.patch.object(next_app, "resolve_update_channel", return_value=("beta", "setting")), \
                mock.patch.object(
                    next_app,
                    "github_branch_version",
                    return_value={"version": "26.2.50", "releaseUrl": "https://example/notes", "publishedAt": None, "source": "https://example/VERSION"},
                ):
            result = next_app.perform_update_check(None, "beta")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["channel"], "beta")
        self.assertEqual(result["latestVersion"], "26.2.50")
        self.assertTrue(result["updateAvailable"])
        self.assertEqual(result["imageRef"], "ghcr.io/helmerznl/discvault:v26-beta")
        self.assertIn("docker compose", result["updateCommand"])

    def test_up_to_date(self):
        with mock.patch.object(next_app, "build_version", return_value="26.2.50"), \
                mock.patch.object(next_app, "resolve_update_channel", return_value=("beta", "setting")), \
                mock.patch.object(
                    next_app,
                    "github_branch_version",
                    return_value={"version": "26.2.50", "releaseUrl": None, "publishedAt": None, "source": "x"},
                ):
            result = next_app.perform_update_check(None, "beta")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["updateAvailable"])

    def test_stable_reads_main_branch_version(self):
        with mock.patch.object(next_app, "build_version", return_value="26.2.45"), \
                mock.patch.object(next_app, "resolve_update_channel", return_value=("stable", "request")), \
                mock.patch.object(
                    next_app,
                    "github_branch_version",
                    return_value={"version": "26.3.0", "releaseUrl": "https://example/notes", "publishedAt": None, "source": "https://example/VERSION"},
                ) as branch_mock:
            result = next_app.perform_update_check(None, "stable")
        branch_mock.assert_called_once_with("main")
        self.assertEqual(result["channel"], "stable")
        self.assertEqual(result["imageRef"], "ghcr.io/helmerznl/discvault:stable")
        self.assertTrue(result["updateAvailable"])

    def test_error_when_latest_unavailable(self):
        with mock.patch.object(next_app, "build_version", return_value="26.2.45"), \
                mock.patch.object(next_app, "resolve_update_channel", return_value=("beta", "setting")), \
                mock.patch.object(next_app, "github_branch_version", return_value=None):
            result = next_app.perform_update_check(None, "beta")
        self.assertEqual(result["status"], "error")
        self.assertIn("currentVersion", result)

    def test_network_failure_does_not_raise(self):
        with mock.patch.object(next_app, "build_version", return_value="26.2.45"), \
                mock.patch.object(
                    next_app, "resolve_update_channel", side_effect=RuntimeError("boom")
                ):
            result = next_app.perform_update_check(None, "auto")
        self.assertEqual(result["status"], "error")
        self.assertIn("boom", result["message"])


class GithubFetchTests(unittest.TestCase):
    def test_latest_stable_release_parses_tag(self):
        body = {"tag_name": "v26.2.0", "html_url": "https://github.com/x/releases/v26.2.0", "published_at": "2026-06-10T00:00:00Z"}
        with mock.patch.object(next_app.http_requests, "get", return_value=_fake_response(200, json_body=body)):
            result = next_app.github_latest_stable_release()
        self.assertEqual(result["version"], "26.2.0")
        self.assertEqual(result["releaseUrl"], body["html_url"])

    def test_latest_stable_release_handles_non_200(self):
        with mock.patch.object(next_app.http_requests, "get", return_value=_fake_response(404)):
            self.assertIsNone(next_app.github_latest_stable_release())

    def test_branch_version_reads_text(self):
        with mock.patch.object(next_app.http_requests, "get", return_value=_fake_response(200, text="26.2.45\n")):
            result = next_app.github_branch_version("release/v26-beta")
        self.assertEqual(result["version"], "26.2.45")

    def test_branch_version_handles_non_200(self):
        with mock.patch.object(next_app.http_requests, "get", return_value=_fake_response(500, text="")):
            self.assertIsNone(next_app.github_branch_version("release/v26-beta"))


if __name__ == "__main__":
    unittest.main()
