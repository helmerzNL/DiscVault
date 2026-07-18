import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app, next_movievault_v2


FIXTURES = Path(__file__).parent / "fixtures"


class MovieVaultDistributionV4Tests(unittest.TestCase):
    """Strict parser/contract coverage for the distribution-4 poster
    extension, vendored byte-identical from Flux76HQ/MovieVault-v2#61
    (commit 1ae144f, frozen contracts/fixtures at d7bec09)."""

    def test_local_poster_url_uses_the_authenticated_movievault_route(self):
        media_asset_id = "b510f16e-58a9-4329-a696-50545f8133eb"
        self.assertEqual(
            next_movievault_v2._local_media_asset_url(media_asset_id),
            f"/api/next/movievault-v2/posters/{media_asset_id}",
        )
        self.assertFalse(
            next_app.is_public_next_path(
                f"/api/next/movievault-v2/posters/{media_asset_id}"
            )
        )
        self.assertTrue(
            next_app.is_public_next_path(f"/api/next/media/assets/{media_asset_id}")
        )

    def test_frozen_fixtures_match_pinned_byte_digests(self):
        expected = {}
        for line in (FIXTURES / "distribution-v4.sha256").read_text(encoding="ascii").splitlines():
            digest, name = line.split("  ", 1)
            expected[name] = digest
        self.assertEqual(
            set(expected),
            {
                "distribution-v4-manifest.json",
                "distribution-v4-full.ndjson",
                "distribution-v4-delta.ndjson",
                "distribution-v4-bucket.json",
                "distribution-v4-scenario.json",
            },
        )
        for name, digest in expected.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), digest)

    def test_v4_manifest_full_delta_are_strict_and_poster_is_required_nullable(self):
        manifest = next_movievault_v2.validate_manifest(
            json.loads((FIXTURES / "distribution-v4-manifest.json").read_bytes()),
            contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
        )
        full = next_movievault_v2.parse_ndjson(
            (FIXTURES / "distribution-v4-full.ndjson").read_bytes(),
            full=True,
            maximum_revision=manifest["currentRevision"],
            contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
        )
        # The vendored delta fixture models updates discovered *after* the
        # frozen manifest snapshot (revisions 43/44 > manifest revision 42),
        # so the delta parse tracks its own, higher maximum_revision - exactly
        # as the real sync loop advances its recorded cursor/revision after
        # each poll rather than re-using the stale manifest snapshot.
        delta = next_movievault_v2.parse_ndjson(
            (FIXTURES / "distribution-v4-delta.ndjson").read_bytes(),
            full=False,
            maximum_revision=44,
            contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
        )

        box_set, release_with_poster, release_without_poster = full

        self.assertEqual(release_with_poster["studio"], "Example Studio")
        self.assertEqual(release_with_poster["distributor"], "Example Distribution")
        self.assertEqual(release_with_poster["runtimeMinutes"], 122)
        self.assertEqual(
            [member["releaseEdition"] for member in box_set["members"]],
            ["Theatrical", "Director's Cut"],
        )

        # poster is REQUIRED (the key must be present) but its value is
        # nullable independently per record.
        self.assertIn("poster", release_with_poster)
        self.assertIn("poster", release_without_poster)
        self.assertIsNone(release_without_poster["poster"])

        poster = release_with_poster["poster"]
        self.assertEqual(poster["assetType"], "front_cover")
        self.assertIn(poster["attestation"], {"original", "licensed"})
        self.assertIn(poster["license"], {"cc0-1.0", "cc-by-4.0", "cc-by-sa-4.0"})
        for variant in ("thumbnail", "display"):
            self.assertRegex(
                poster[variant]["path"],
                r"^/v2/assets/[0-9a-f-]+/(thumbnail|display)$",
            )
            self.assertEqual(len(poster[variant]["checksum"]), 64)

        box_set_poster = box_set["poster"]
        self.assertIsNotNone(box_set_poster)
        self.assertEqual(box_set_poster["assetType"], "front_cover")

        self.assertEqual(delta[0]["recordType"], "release")
        self.assertIsNotNone(delta[0]["poster"])
        self.assertEqual(delta[1]["recordType"], "box_set")
        self.assertIsNone(delta[1]["poster"])

        # Strict unknown-field rejection extends to distribution-4 the same
        # way it already does for v2/v3.
        invalid = json.loads(
            (FIXTURES / "distribution-v4-full.ndjson").read_bytes().splitlines()[1]
        )
        invalid["unexpected"] = True
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                invalid,
                contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
            )

        # A poster object missing its required sub-fields must be rejected -
        # never silently coerced or dropped.
        broken_poster = dict(invalid)
        broken_poster.pop("unexpected")
        broken_poster["poster"] = dict(broken_poster["poster"])
        del broken_poster["poster"]["license"]
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                broken_poster,
                contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
            )

    def test_v4_is_strict_superset_and_v2_v3_remain_compatible(self):
        v4_record = json.loads(
            (FIXTURES / "distribution-v4-full.ndjson").read_bytes().splitlines()[1]
        )
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^contract_incompatible$"):
            next_movievault_v2.validate_record(v4_record, contract_version=next_movievault_v2.MOVIEVAULT_V2_CONTRACT)
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^contract_incompatible$"):
            next_movievault_v2.validate_record(v4_record, contract_version=next_movievault_v2.MOVIEVAULT_V3_CONTRACT)

        v3_record = json.loads(
            (FIXTURES / "distribution-v3-full.ndjson").read_bytes().splitlines()[0]
        )
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^contract_incompatible$"):
            next_movievault_v2.validate_record(v3_record, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT)

    def test_v4_bucket_uses_v4_endpoint_and_preserves_poster(self):
        content = (FIXTURES / "distribution-v4-bucket.json").read_bytes()
        manifest = json.loads((FIXTURES / "distribution-v4-manifest.json").read_bytes())
        lookup_hash = json.loads(content)["records"][0]["eanHashes"][0]
        raw = hashlib.sha256(content).digest()
        import base64

        headers = {
            "x-content-sha256": raw.hex(),
            "content-digest": f"sha-256=:{base64.b64encode(raw).decode('ascii')}:",
        }
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=manifest),
            patch.object(
                next_movievault_v2,
                "_request",
                return_value=(200, content, headers),
            ) as request,
        ):
            result = next_movievault_v2.bucket_lookup(
                {"origin": "https://movievault.example"},
                {"hash": lookup_hash},
                contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
            )

        self.assertEqual(result["results"][0]["runtimeMinutes"], 122)
        self.assertIsNotNone(result["results"][0]["poster"])
        self.assertEqual(request.call_args.args[0], "https://movievault.example/v4/bucket/9")


if __name__ == "__main__":
    unittest.main()
