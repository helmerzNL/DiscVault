import os
import sys
import unittest
import uuid


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_common
    from app.backend import next_app
except ModuleNotFoundError as exc:  # Local minimal test environments may omit optional backend deps.
    if exc.name not in {"flask", "psycopg"}:
        raise
    next_common = None
    next_app = None


@unittest.skipIf(next_common is None, "Flask is not installed in this test environment")
class NextCommonHelperTests(unittest.TestCase):
    def test_shared_helpers_are_reexported_from_next_app(self):
        names = [
            "NextApiError",
            "json_ready",
            "response",
            "parse_int_arg",
            "parse_uuid",
            "parse_bool_value",
            "parse_uuid_list",
            "table_exists",
            "count_table",
        ]
        for name in names:
            self.assertIs(getattr(next_app, name), getattr(next_common, name), name)

    def test_json_ready_normalizes_nested_values(self):
        identifier = uuid.uuid4()
        result = next_common.json_ready({"id": identifier, "items": (1, 2)})
        self.assertEqual(result, {"id": str(identifier), "items": [1, 2]})

    def test_parse_bool_value_accepts_common_truthy_tokens(self):
        self.assertTrue(next_common.parse_bool_value("yes"))
        self.assertFalse(next_common.parse_bool_value("off"))
        self.assertTrue(next_common.parse_bool_value(None, default=True))

    def test_parse_uuid_rejects_invalid_value(self):
        with self.assertRaises(next_common.NextApiError) as ctx:
            next_common.parse_uuid("not-a-uuid", "movieId")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_parse_uuid_list_deduplicates_and_validates(self):
        identifier = uuid.uuid4()
        parsed = next_common.parse_uuid_list([str(identifier), str(identifier)], "movieIds")
        self.assertEqual(parsed, [identifier])

    def test_count_table_returns_zero_for_missing_table(self):
        class _FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, *args):
                self._row = {"table_name": None}

            def fetchone(self):
                return self._row

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

        self.assertEqual(next_common.count_table(_FakeConn(), "missing_table"), 0)


if __name__ == "__main__":
    unittest.main()
