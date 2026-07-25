"""CLI-level fail-closed tests for the identity fixture validator."""

from __future__ import annotations

from contextlib import redirect_stderr
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

try:
    from ..scripts import validate_identity_ladder_fixture as validator
    from .test_identity_ladder_fixture import IdentityLadderFixtureRunner
except ImportError:  # pragma: no cover - backend working-directory CI imports
    from scripts import validate_identity_ladder_fixture as validator
    from tests.test_identity_ladder_fixture import IdentityLadderFixtureRunner


class IdentityLadderValidatorCliTests(unittest.TestCase):
    def test_missing_fixture_is_a_hard_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "identity-ladder.json"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = validator.main(["--fixture", str(missing)])

        self.assertEqual(result, 1)
        self.assertIn("Identity ladder fixture coverage failed", stderr.getvalue())
        self.assertIn(str(missing), stderr.getvalue())

    def test_unknown_array_category_names_the_failure(self):
        payload = copy.deepcopy(IdentityLadderFixtureRunner.load().payload)
        payload["future_cases"] = [{"id": "not-dispatched"}]

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "identity-ladder.json"
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = validator.main(["--fixture", str(fixture)])

        self.assertEqual(result, 1)
        self.assertIn("future_cases", stderr.getvalue())

    def test_missing_supported_category_is_a_hard_failure(self):
        payload = copy.deepcopy(IdentityLadderFixtureRunner.load().payload)
        payload.pop("identifier_cases")

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "identity-ladder.json"
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = validator.main(["--fixture", str(fixture)])

        self.assertEqual(result, 1)
        self.assertIn("identifier_cases", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
