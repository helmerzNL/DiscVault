"""A malformed query argument is the caller's mistake, and must be told so.

PERF-04 called these endpoints "unbounded and unclamped". Read against the code
as it stands, the first half is mostly wrong and the second half is mostly
right in a different way: nineteen routes parsed `limit` or `offset` with a
bare `int(request.args.get(...))`, and almost all of them *did* clamp
afterwards with `min(max(...))`. Sizes were fine.

What none of them had was **validation**, and the three behaviours they had
instead were all wrong in different directions:

* fifteen raised `ValueError` inside the handler, which the catch-all error
  handler turned into **500** -- the server blaming itself for the caller's
  typo;
* one (`next_profile`) swallowed it and silently used the default, so the
  response looked correct while answering a different question than the one
  asked;
* one (`/api/next/people`) clamped internally but echoed the *requested* limit
  back, so asking for 5000 was answered with `"limit": 5000` and 500 rows.

`parse_int_arg` already existed in `next_common.py` and already did the right
thing. It was used by eleven routes and bypassed by nineteen. This module holds
the resulting rule: one parser, and nothing parses its own.

**One deliberate behaviour change** is pinned here rather than left to be
discovered: an empty value (`?limit=`) counts as absent and yields the default.
Five of the converted sites were written as `int(... or 100)`, which already
meant that, and a client emitting a bare `?limit=` must not start receiving
400s because the parsing moved.
"""

import pathlib
import re
import sys
import unittest


BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import flask

    from app.backend.next_common import NextApiError, parse_int_arg
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "cbor2", "argon2", "jwt", "segno", "PIL"}:
        raise
    parse_int_arg = None


@unittest.skipIf(parse_int_arg is None, "backend dependencies are required")
class ParseIntArgTests(unittest.TestCase):
    def setUp(self):
        self.app = flask.Flask(__name__)

    def parse(self, query, **kwargs):
        with self.app.test_request_context(f"/?{query}"):
            return parse_int_arg("limit", kwargs.pop("default", 100), **kwargs)

    def test_a_number_is_returned(self):
        self.assertEqual(self.parse("limit=42", minimum=1, maximum=500), 42)

    def test_a_missing_argument_is_the_default(self):
        self.assertEqual(self.parse("", default=120, minimum=1, maximum=500), 120)

    def test_an_empty_value_is_the_default_rather_than_an_error(self):
        # The deliberate behaviour change. Five converted sites were written as
        # `int(request.args.get("limit") or 100)`, which already meant this.
        self.assertEqual(self.parse("limit=", default=120, minimum=1, maximum=500), 120)
        self.assertEqual(self.parse("limit=%20%20", default=120, minimum=1, maximum=500), 120)

    def test_a_non_numeric_value_is_a_400_not_a_500(self):
        with self.assertRaises(NextApiError) as caught:
            self.parse("limit=abc", minimum=1, maximum=500)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("limit", str(caught.exception))

    def test_a_float_is_refused_rather_than_truncated(self):
        # Silently flooring 10.9 to 10 would answer a question nobody asked.
        with self.assertRaises(NextApiError) as caught:
            self.parse("limit=10.9", minimum=1, maximum=500)
        self.assertEqual(caught.exception.status_code, 400)

    def test_it_clamps_at_both_ends(self):
        self.assertEqual(self.parse("limit=999999", minimum=1, maximum=500), 500)
        self.assertEqual(self.parse("limit=0", minimum=1, maximum=500), 1)
        self.assertEqual(self.parse("limit=-5", minimum=1, maximum=500), 1)

    def test_a_negative_offset_becomes_zero(self):
        with self.app.test_request_context("/?offset=-1"):
            self.assertEqual(parse_int_arg("offset", 0, minimum=0, maximum=9_000_000), 0)


class NothingParsesItsOwnTests(unittest.TestCase):
    """The invariant. A new route is how this comes back."""

    MODULES = (
        "next_app.py",
        "next_people.py",
        "next_profile.py",
        "next_notifications.py",
        "next_common.py",
    )

    def test_no_module_parses_a_query_argument_by_hand(self):
        pattern = re.compile(r"^(?!\s*#).*\bint\(\s*request\.args", re.M)
        for name in self.MODULES:
            source = (BACKEND / name).read_text(encoding="utf-8")
            # The parser's own docstring quotes the pattern it replaced.
            source = re.sub(r'""".*?"""', "", source, flags=re.S)
            offenders = [
                source[m.start() : source.index("\n", m.start())].strip()
                for m in pattern.finditer(source)
            ]
            self.assertEqual(
                offenders,
                [],
                f"{name} parses a query argument by hand; use parse_int_arg so a "
                "malformed value is a 400 rather than a 500",
            )

    def test_the_people_route_states_the_ceiling_it_enforces(self):
        # It clamped to 500 inside people_list_entities and echoed back whatever
        # was asked for. The echo is part of the response contract, so the
        # ceiling has to be visible where the value is read.
        source = (BACKEND / "next_people.py").read_text(encoding="utf-8")
        start = source.index('@flask_app.get("/api/next/people")')
        body = source[start : start + 1400]
        self.assertIn('parse_int_arg("limit", 120, minimum=1, maximum=500)', body)
        self.assertIn('"limit": limit', body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
