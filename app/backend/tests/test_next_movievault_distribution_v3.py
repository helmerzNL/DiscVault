import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import zipfile


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_movievault_v2


FIXTURES = Path(__file__).parent / "fixtures"

# The MovieVault v2 origin is now enforced at runtime (never read from stored
# settings). Inject the test origin via the MOVIEVAULT_V2_ORIGIN env override so
# existing URL assertions built from "https://movievault.example" stay valid.
_MOVIEVAULT_V2_ORIGIN_ENV_PATCH = patch.dict(
    os.environ, {"MOVIEVAULT_V2_ORIGIN": "https://movievault.example"}
)


def setUpModule():
    _MOVIEVAULT_V2_ORIGIN_ENV_PATCH.start()


def tearDownModule():
    _MOVIEVAULT_V2_ORIGIN_ENV_PATCH.stop()


def digest_headers(content: bytes) -> dict[str, str]:
    raw = hashlib.sha256(content).digest()
    return {
        "x-content-sha256": raw.hex(),
        "content-digest": f"sha-256=:{base64.b64encode(raw).decode('ascii')}:",
    }


class MovieVaultDistributionV3Tests(unittest.TestCase):
    def test_frozen_fixtures_match_pinned_byte_digests(self):
        expected = {}
        for line in (FIXTURES / "distribution-v3.sha256").read_text(encoding="ascii").splitlines():
            digest, name = line.split("  ", 1)
            expected[name] = digest
        self.assertEqual(
            set(expected),
            {
                "distribution-v3-manifest.json",
                "distribution-v3-full.ndjson",
                "distribution-v3-delta.ndjson",
                "distribution-v3-bucket.json",
                "distribution-v3-scenario.json",
            },
        )
        for name, digest in expected.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), digest)

    def test_v3_manifest_full_delta_member_asset_and_tombstone_are_strict(self):
        manifest = next_movievault_v2.validate_manifest(
            json.loads((FIXTURES / "distribution-v3-manifest.json").read_bytes()),
            contract_version=next_movievault_v2.MOVIEVAULT_V3_CONTRACT,
        )
        full = next_movievault_v2.parse_ndjson(
            (FIXTURES / "distribution-v3-full.ndjson").read_bytes(),
            full=True,
            maximum_revision=manifest["currentRevision"],
            contract_version=next_movievault_v2.MOVIEVAULT_V3_CONTRACT,
        )
        delta = next_movievault_v2.parse_ndjson(
            (FIXTURES / "distribution-v3-delta.ndjson").read_bytes(),
            full=False,
            maximum_revision=manifest["currentRevision"],
            contract_version=next_movievault_v2.MOVIEVAULT_V3_CONTRACT,
        )

        self.assertEqual(full[0]["studio"], "Example Studio")
        self.assertEqual(full[0]["distributor"], "Example Distribution")
        self.assertEqual(full[0]["runtimeMinutes"], 121)
        self.assertEqual(full[1]["studio"], None)
        self.assertEqual(
            [member["releaseEdition"] for member in full[2]["members"]],
            ["Theatrical", "Director's Cut"],
        )
        # MovieVault's public asset paths are deliberately /v2/assets/... for
        # both the v3 and v4 distribution contracts - only the index/manifest
        # routes are versioned. Regression guard against re-introducing an
        # incorrect /v3/assets/... expectation.
        display_path = full[0]["assets"][0]["display"]["path"]
        self.assertTrue(display_path)
        self.assertRegex(display_path, r"^/v2/assets/[0-9a-f-]+/display$")
        self.assertEqual(display_path.split("/")[1], "v2")
        self.assertEqual(delta[-1]["operation"], "delete")

        invalid = json.loads((FIXTURES / "distribution-v3-full.ndjson").read_bytes().splitlines()[0])
        invalid["unexpected"] = True
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                invalid,
                contract_version=next_movievault_v2.MOVIEVAULT_V3_CONTRACT,
            )

    def test_v3_digest_requires_matching_legacy_and_content_digest_headers(self):
        content = (FIXTURES / "distribution-v3-full.ndjson").read_bytes()
        headers = digest_headers(content)
        self.assertEqual(
            next_movievault_v2._verify_digest(
                content,
                headers,
                contract_version=next_movievault_v2.MOVIEVAULT_V3_CONTRACT,
            ),
            hashlib.sha256(content).hexdigest(),
        )
        next_movievault_v2._verify_digest(
            content,
            {
                **headers,
                "content-digest": headers["content-digest"].replace(
                    "sha-256",
                    "  SHA-256",
                ),
            },
            contract_version=next_movievault_v2.MOVIEVAULT_V3_CONTRACT,
        )
        for invalid in (
            {"x-content-sha256": headers["x-content-sha256"]},
            {**headers, "content-digest": "sha-256=:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=:"},
            {**headers, "x-content-sha256": "0" * 64},
        ):
            with self.subTest(headers=invalid):
                with self.assertRaisesRegex(
                    next_movievault_v2.MovieVaultV2Error,
                    "^checksum_invalid$",
                ):
                    next_movievault_v2._verify_digest(
                        content,
                        invalid,
                        contract_version=next_movievault_v2.MOVIEVAULT_V3_CONTRACT,
                    )

    def test_contracts_never_cross_parse_and_v3_requires_pair_negotiation(self):
        v3_record = json.loads(
            (FIXTURES / "distribution-v3-full.ndjson").read_bytes().splitlines()[0]
        )
        with self.assertRaisesRegex(
            next_movievault_v2.MovieVaultV2Error,
            "^contract_incompatible$",
        ):
            next_movievault_v2.validate_record(v3_record)

        legacy = next_movievault_v2.movievault_v2_plugin_context(
            object(),
            "movievault_v2",
            {"settings": {"origin": "https://movievault.example"}},
        )
        compatible = next_movievault_v2.movievault_v2_plugin_context(
            object(),
            "movievault_v2",
            {
                "settings": {"origin": "https://movievault.example"},
                "distributionContractRange": {
                    "minimum": "distribution-2",
                    "maximum": "distribution-3",
                },
            },
        )
        self.assertEqual(legacy["movievaultDistributionContract"], "distribution-2")
        self.assertEqual(compatible["movievaultDistributionContract"], "distribution-3")
        for operation, arguments in (
            (
                next_movievault_v2.run_sync,
                (lambda: None, {"origin": "https://movievault.example"}),
            ),
            (
                next_movievault_v2.bucket_lookup,
                (
                    {"origin": "https://movievault.example"},
                    {"hash": "0" * 64},
                ),
            ),
        ):
            with self.subTest(operation=operation.__name__):
                # distribution-5 does not exist yet; distribution-4 is now a
                # legitimately supported contract (see the v4 tests) so it no
                # longer belongs in this "unsupported contract" regression.
                with self.assertRaisesRegex(
                    next_movievault_v2.MovieVaultV2Error,
                    "^contract_incompatible$",
                ):
                    operation(*arguments, contract_version="distribution-5")

    def test_v3_bucket_uses_v3_endpoint_and_dual_digest(self):
        content = (FIXTURES / "distribution-v3-bucket.json").read_bytes()
        manifest = json.loads((FIXTURES / "distribution-v3-manifest.json").read_bytes())
        lookup_hash = json.loads(content)["records"][0]["eanHashes"][0]
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=manifest),
            patch.object(
                next_movievault_v2,
                "_request",
                return_value=(200, content, digest_headers(content)),
            ) as request,
        ):
            result = next_movievault_v2.bucket_lookup(
                {"origin": "https://movievault.example"},
                {"hash": lookup_hash},
                contract_version=next_movievault_v2.MOVIEVAULT_V3_CONTRACT,
            )

        self.assertEqual(result["results"][0]["runtimeMinutes"], 121)
        self.assertEqual(request.call_args.args[0], "https://movievault.example/v3/bucket/9c10")

    def test_feature_package_is_deterministic_and_declares_contract_range(self):
        archive_path = Path(repo_root) / "dist" / "plugins" / "movievault_v2_1.1.0.zip"
        checksum_path = archive_path.with_suffix(".zip.sha256")
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        self.assertEqual(
            checksum_path.read_text(encoding="ascii"),
            f"{digest}  {archive_path.name}\n",
        )
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            self.assertEqual({item.date_time for item in archive.infolist()}, {(1980, 1, 1, 0, 0, 0)})
            self.assertTrue(all(name.startswith("movievault_v2/") for name in names))
            manifest = json.loads(archive.read("movievault_v2/manifest.json"))
            source = archive.read("movievault_v2/plugin.py")

        self.assertEqual(
            manifest["distributionContractRange"],
            {"minimum": "distribution-2", "maximum": "distribution-3"},
        )
        self.assertFalse(manifest["requiresSecrets"])
        self.assertEqual(manifest["settingsSchema"]["secrets"], [])
        module = types.ModuleType("movievault_v2_feature_package")
        exec(compile(source, str(archive_path), "exec"), module.__dict__)
        release = json.loads(
            (FIXTURES / "distribution-v3-full.ndjson").read_bytes().splitlines()[0]
        )
        result = module.movie_details(
            {"releaseId": release["releaseId"]},
            {
                "movievaultV2Lookup": lambda _request: {
                    "state": "current",
                    "results": [release],
                }
            },
        )
        self.assertEqual(result["studio"], "Example Studio")
        self.assertEqual(result["distributor"], "Example Distribution")
        self.assertEqual(result["runtimeMinutes"], 121)


if __name__ == "__main__":
    unittest.main()
