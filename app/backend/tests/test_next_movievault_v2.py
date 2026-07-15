import hashlib
import json
import os
import sys
import unittest
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_movievault_v2
from app.backend import next_metadata
from app.backend import next_plugin_runtime
from app.backend import next_worker


FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "distribution-v2.ndjson")
FIXTURE_SHA256 = "e1cd182a2b40ad3a6beb2a8bfa99aed6e048e73e37726933e65b33a1cd0cebcb"


class FakeResponse:
    def __init__(self, content: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = 200
        self._content = content
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def read(self, maximum: int) -> bytes:
        return self._content[:maximum]


class FakeOpener:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.request = None

    def open(self, request, timeout: int):
        self.request = request
        self.timeout = timeout
        return self.response


class MovieVaultV2ContractTests(unittest.TestCase):
    def fixture(self) -> bytes:
        with open(FIXTURE_PATH, "rb") as handle:
            return handle.read()

    def test_pinned_fixture_checksum_and_exact_editions(self):
        content = self.fixture()
        self.assertEqual(hashlib.sha256(content).hexdigest(), FIXTURE_SHA256)

        records = next_movievault_v2.parse_ndjson(
            content,
            full=True,
            maximum_revision=42,
        )

        self.assertEqual(len(records), 3)
        members = records[2]["members"]
        self.assertEqual([member["position"] for member in members], [1, 2])
        self.assertEqual(
            [member["releaseEdition"] for member in members],
            ["Theatrical", "Director's Cut"],
        )
        self.assertEqual(members[0]["filmId"], members[1]["filmId"])
        self.assertNotEqual(members[0]["releaseId"], members[1]["releaseId"])

    def test_contract_rejects_unknown_fields_and_raw_barcodes(self):
        record = json.loads(self.fixture().splitlines()[0])
        record["ean"] = "8712345678901"

        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(record)

    def test_delta_revisions_must_advance_strictly(self):
        records = self.fixture().splitlines()[:2]
        second = json.loads(records[1])
        second["revision"] = 40
        content = records[0] + b"\n" + json.dumps(second).encode("utf-8") + b"\n"

        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^revision_invalid$"):
            next_movievault_v2.parse_ndjson(
                content,
                full=False,
                maximum_revision=42,
            )

    def test_full_snapshot_rejects_tombstones(self):
        tombstone = {
            "contractVersion": "distribution-2",
            "recordType": "release",
            "operation": "delete",
            "revision": 43,
            "entityId": "10000000-0000-0000-0000-000000000001",
        }

        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^artifact_invalid$"):
            next_movievault_v2.parse_ndjson(
                json.dumps(tombstone).encode("ascii"),
                full=True,
                maximum_revision=43,
            )

    def test_origin_is_restricted_to_an_origin_without_credentials(self):
        self.assertEqual(
            next_movievault_v2.normalize_origin("HTTPS://MovieVault.Example/"),
            "https://movievault.example",
        )
        for invalid in (
            "https://user:secret@movievault.example",
            "https://movievault.example/private",
            "https://movievault.example?instance=1",
            "file:///data/index",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    next_movievault_v2.MovieVaultV2Error,
                    "^origin_invalid$",
                ):
                    next_movievault_v2.normalize_origin(invalid)

    def test_anonymous_request_has_no_auth_cookie_or_instance_headers(self):
        content = b"{}"
        digest = hashlib.sha256(content).hexdigest()
        opener = FakeOpener(
            FakeResponse(
                content,
                {
                    "X-Content-SHA256": digest,
                },
            )
        )

        with patch.object(next_movievault_v2.urllib.request, "build_opener", return_value=opener):
            status, body, _headers = next_movievault_v2._request(
                "https://movievault.example/v2/index/manifest",
                accept="application/json",
                timeout_seconds=10,
                maximum_bytes=1024,
            )

        self.assertEqual(status, 200)
        self.assertEqual(body, content)
        headers = {key.lower(): value for key, value in opener.request.header_items()}
        self.assertEqual(set(headers), {"accept", "user-agent"})
        self.assertNotIn("authorization", headers)
        self.assertNotIn("cookie", headers)
        self.assertNotIn("x-instance-id", headers)
        self.assertNotIn("x-contribution-token", headers)

    def test_digest_mismatch_fails_without_exposing_content(self):
        with self.assertRaisesRegex(
            next_movievault_v2.MovieVaultV2Error,
            "^checksum_invalid$",
        ):
            next_movievault_v2._verify_digest(
                b"sensitive-barcode",
                {"x-content-sha256": "0" * 64},
            )

    def test_bucket_fallback_filters_by_complete_hash(self):
        content = self.fixture()
        fixture_records = [json.loads(line) for line in content.splitlines()]
        lookup_hash = fixture_records[0]["eanHashes"][0]
        bucket = json.dumps(
            {
                "contractVersion": "distribution-2",
                "prefix": lookup_hash[:4],
                "records": fixture_records,
            },
            separators=(",", ":"),
        ).encode("ascii")
        bucket_digest = hashlib.sha256(bucket).hexdigest()
        manifest = {
            "contractVersion": "distribution-2",
            "currentRevision": 42,
            "currentCursor": "cursor-value-long-enough",
            "bucketPrefixLength": 4,
            "hashAlgorithm": "sha256",
            "datasetChecksum": FIXTURE_SHA256,
            "deltaPath": "/v2/index/delta",
            "bucketPathTemplate": "/v2/bucket/{prefix}",
        }

        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=manifest),
            patch.object(
                next_movievault_v2,
                "_request",
                return_value=(
                    200,
                    bucket,
                    {"x-content-sha256": bucket_digest},
                ),
            ),
        ):
            result = next_movievault_v2.bucket_lookup(
                {"origin": "https://movievault.example"},
                {"hash": lookup_hash},
            )

        self.assertEqual(result["state"], "remote_bucket")
        self.assertEqual(
            [(item["recordType"], item.get("releaseId") or item.get("boxSetId")) for item in result["results"]],
            [
                ("release", "10000000-0000-0000-0000-000000000001"),
                ("box_set", "30000000-0000-0000-0000-000000000001"),
            ],
        )

    def test_context_callbacks_are_only_injected_for_movievault_v2(self):
        context = {"settings": {"origin": "https://movievault.example"}}

        untouched = next_movievault_v2.movievault_v2_plugin_context(
            object(),
            "movievault_26",
            context,
        )
        decorated = next_movievault_v2.movievault_v2_plugin_context(
            object(),
            "movievault_v2",
            context,
        )

        self.assertIs(untouched, context)
        self.assertIn("movievaultV2Lookup", decorated)
        self.assertIn("movievaultV2Status", decorated)
        self.assertIn("movievaultV2Sync", decorated)
        self.assertIn("movievaultV2BucketLookup", decorated)
        with self.assertRaisesRegex(
            next_movievault_v2.MovieVaultV2Error,
            "^core_bridge_unavailable$",
        ):
            decorated["movievaultV2Sync"]({})

    def test_origin_is_required_for_non_health_entrypoints(self):
        plugin = {
            "id": "movievault_v2",
            "manifest": {"requiresSecrets": False},
        }

        self.assertTrue(next_metadata.plugin_requires_config(plugin, {"settings": {}}, "search_title"))
        self.assertTrue(next_worker.plugin_requires_config(plugin, {"settings": {}}, "sync_index"))
        self.assertFalse(
            next_metadata.plugin_requires_config(
                plugin,
                {"settings": {"origin": "https://movievault.example"}},
                "search_title",
            )
        )
        self.assertFalse(next_worker.plugin_requires_config(plugin, {"settings": {}}, "health_check"))
        self.assertIn("sync_index", next_plugin_runtime.PLUGIN_ENTRYPOINTS)


if __name__ == "__main__":
    unittest.main()
