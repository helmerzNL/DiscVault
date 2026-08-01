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
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "distribution-v2.schema.json")
FIXTURE_SHA256 = "fac6de561e22bc7f95dbab61fc6746e5887865e07152acd844b8b42fb723341e"
SCHEMA_SHA256 = "9f25a2b1ccd76dd4f7de89c33beea3250498fc96367bb0540c911bf53267948f"

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


class FakeResponse:
    def __init__(
        self,
        content: bytes,
        headers: dict[str, str] | None = None,
        *,
        status: int = 200,
    ) -> None:
        self.status = status
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


class SequenceOpener:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout: int):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


def release_details_hit(*, external: bool = False) -> dict:
    payload = {
        "contractVersion": "release-technical-1",
        "status": "external_hit" if external else "canonical_hit",
        "verificationStatus": "unreviewed_external" if external else "canonical",
        "film": {
            "title": "Example Film",
            "year": 2024,
            "identifiers": {
                "tmdbMovieId": "123",
                "imdbId": "tt1234567",
            },
            "links": {
                "tmdb": "https://www.themoviedb.org/movie/123",
                "imdb": "https://www.imdb.com/title/tt1234567/",
            },
        },
        "release": {
            "barcodes": [
                {
                    "type": "ean13",
                    "value": "4006381333931",
                    "scope": "package",
                }
            ],
            "title": "Example Film - Collector's Edition",
            "alternateTitles": [],
            "format": "4K UHD",
            "edition": "SteelBook",
            "discCount": 2,
            "regions": ["B"],
            "packaging": ["steelbook"],
            "video": {
                "resolution": "2160p",
                "codecs": ["hevc"],
                "hdrFormats": ["dolby_vision"],
                "aspectRatios": ["2.39:1"],
            },
            "audioTracks": [
                {
                    "languageCode": "en",
                    "codec": "dolby_truehd",
                    "channels": "7.1",
                    "immersiveFormat": "dolby_atmos",
                }
            ],
            "subtitleLanguages": ["en", "nl"],
        },
    }
    if external:
        payload["moderation"] = {
            "candidateId": "discovery_abcdefghijkl",
            "status": "pending",
        }
    return payload


def release_details_poster(*, asset_id: str = "40000000-0000-0000-0000-000000000001") -> dict:
    """The approved poster reference reuses the `distribution-4` poster type
    verbatim, so this mirrors the shape already pinned in
    tests/fixtures/distribution-v4-full.ndjson."""
    return {
        "assetId": asset_id,
        "assetType": "front_cover",
        "attestation": "licensed",
        "license": "cc-by-4.0",
        "thumbnail": {
            "checksum": "a" * 64,
            "path": f"/v2/assets/{asset_id}/thumbnail",
        },
        "display": {
            "checksum": "b" * 64,
            "path": f"/v2/assets/{asset_id}/display",
        },
    }


def release_details_box_set() -> dict:
    return {
        "state": "explicit",
        "title": "Example Collection",
        "alternateTitles": [],
        "format": "4K UHD",
        "barcodes": [
            {
                "type": "ean13",
                "value": "9780201379624",
                "scope": "box_set",
            }
        ],
        "members": [
            {
                "position": 1,
                "title": "Example Film",
                "year": 2024,
                "barcodes": [
                    {
                        "type": "upca",
                        "value": "036000291452",
                        "scope": "member",
                    }
                ],
                "discNumber": 1,
                "discFormat": "4K UHD",
                "identifiers": {
                    "tmdbMovieId": "123",
                    "imdbId": "tt1234567",
                },
            },
            {
                "position": 2,
                "title": "Example Film Two",
                "barcodes": [
                    {
                        "type": "upca",
                        "value": "012345678905",
                        "scope": "disc",
                    }
                ],
                "discNumber": 2,
                "discFormat": "Blu-ray",
                "identifiers": {
                    "tmdbMovieId": "456",
                    "imdbId": "tt7654321",
                },
            },
        ],
    }


class FakePosterConn:
    """Minimal psycopg-shaped connection covering the two statements the
    release-details poster path issues: the idempotent poster-cache insert
    (``_enqueue_poster_cache``) and the cache-status lookup
    (``_poster_status_fields``)."""

    def __init__(self, cache: dict[tuple[str, str], dict]):
        self._cache = cache
        self.enqueued: list[tuple[str, str]] = []
        self._result: dict | None = None

    def transaction(self):
        return self

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: tuple = ()):
        statement = " ".join(sql.split())
        if statement.startswith("INSERT INTO movievault_v2_poster_cache"):
            asset_id, variant, _checksum = params
            self.enqueued.append((asset_id, variant))
            self._result = None  # ON CONFLICT DO NOTHING -> nothing to enqueue
        elif statement.startswith("SELECT status, media_asset_id"):
            self._result = self._cache.get((params[0], params[1]))
        else:
            self._result = None
        return self

    def fetchone(self):
        return self._result


class MovieVaultV2ContractTests(unittest.TestCase):
    def fixture(self) -> bytes:
        with open(FIXTURE_PATH, "rb") as handle:
            return handle.read()

    def test_pinned_fixture_checksum_and_exact_editions(self):
        content = self.fixture()
        canonical_content = content.replace(b"\r\n", b"\n")
        with open(SCHEMA_PATH, "rb") as handle:
            canonical_schema = handle.read().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha256(canonical_content).hexdigest(), FIXTURE_SHA256)
        self.assertEqual(hashlib.sha256(canonical_schema).hexdigest(), SCHEMA_SHA256)

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

    def test_full_snapshot_accepts_unique_revisions_in_publisher_order(self):
        records = self.fixture().splitlines()
        publisher_ordered = b"\n".join((records[2], records[0], records[1])) + b"\n"

        parsed = next_movievault_v2.parse_ndjson(
            publisher_ordered,
            full=True,
            maximum_revision=42,
        )

        self.assertEqual([record["revision"] for record in parsed], [42, 40, 41])

    def test_full_snapshot_rejects_duplicate_or_out_of_range_revisions(self):
        first, second = self.fixture().splitlines()[:2]
        duplicate = json.loads(second)
        duplicate["revision"] = 40
        too_new = json.loads(first)
        too_new["revision"] = 43

        for content in (
            first + b"\n" + json.dumps(duplicate).encode("utf-8") + b"\n",
            json.dumps(too_new).encode("utf-8") + b"\n",
        ):
            with self.subTest(content=content):
                with self.assertRaisesRegex(
                    next_movievault_v2.MovieVaultV2Error,
                    "^revision_invalid$",
                ):
                    next_movievault_v2.parse_ndjson(
                        content,
                        full=True,
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

    def test_release_details_post_is_anonymous_and_minimal(self):
        opener = SequenceOpener(
            [
                FakeResponse(
                    json.dumps(release_details_hit(), separators=(",", ":")).encode("ascii")
                )
            ]
        )

        with patch.object(
            next_movievault_v2.urllib.request,
            "build_opener",
            return_value=opener,
        ):
            result = next_movievault_v2.resolve_release_details(
                {
                    "origin": "https://movievault.example",
                    "requestTimeoutSeconds": 10,
                },
                {
                    "barcode": "4006381333931",
                    "title": "  Example   Film  ",
                    "year": 2024,
                },
            )

        self.assertEqual(result["status"], "canonical_hit")
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, "https://movievault.example/v2/release-details/resolve")
        self.assertEqual(request.method, "POST")
        self.assertEqual(timeout, 10)
        self.assertEqual(
            json.loads(request.data),
            {
                "barcode": "4006381333931",
                "title": "Example Film",
                "year": 2024,
            },
        )
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(
            set(headers),
            {"accept", "content-type", "user-agent"},
        )
        self.assertNotIn("authorization", headers)
        self.assertNotIn("cookie", headers)
        self.assertNotIn("x-instance-id", headers)
        self.assertNotIn("x-contribution-token", headers)

    def test_release_details_pending_polls_only_opaque_resolution_id(self):
        resolution_id = "71a6c771-cd83-478e-a2db-109cb4fd6279"
        opener = SequenceOpener(
            [
                FakeResponse(
                    json.dumps(
                        {
                            "contractVersion": "release-technical-1",
                            "status": "pending",
                            "resolutionId": resolution_id,
                            "retryAfterSeconds": 2,
                        }
                    ).encode("ascii"),
                    status=202,
                ),
                FakeResponse(
                    json.dumps(
                        release_details_hit(external=True),
                        separators=(",", ":"),
                    ).encode("ascii")
                ),
            ]
        )
        waits = []

        with patch.object(
            next_movievault_v2.urllib.request,
            "build_opener",
            return_value=opener,
        ):
            result = next_movievault_v2.resolve_release_details(
                {"origin": "https://movievault.example"},
                {"barcode": "4006381333931"},
                sleep=waits.append,
            )

        self.assertEqual(result["status"], "external_hit")
        self.assertEqual(waits, [2])
        poll_request, _timeout = opener.requests[1]
        self.assertEqual(
            poll_request.full_url,
            f"https://movievault.example/v2/release-details/resolve/{resolution_id}",
        )
        self.assertEqual(poll_request.method, "GET")
        self.assertIsNone(poll_request.data)
        self.assertNotIn(b"4006381333931", poll_request.full_url.encode("ascii"))
        poll_headers = {
            key.lower(): value
            for key, value in poll_request.header_items()
        }
        self.assertEqual(set(poll_headers), {"accept", "user-agent"})

    def test_release_details_accepts_external_hit_without_optional_identifiers(self):
        payload = release_details_hit(external=True)
        payload["film"]["identifiers"] = {}
        payload["film"]["links"] = {}

        result = next_movievault_v2.validate_release_details_response(payload)

        self.assertEqual(result["status"], "external_hit")
        self.assertEqual(result["film"]["title"], "Example Film")
        self.assertEqual(result["film"]["identifiers"], {})
        self.assertEqual(result["film"]["links"], {})

    def test_release_details_rejects_link_without_matching_identifier(self):
        payload = release_details_hit(external=True)
        payload["film"]["identifiers"] = {}
        payload["film"]["links"] = {
            "tmdb": "https://www.themoviedb.org/movie/123",
        }

        with self.assertRaisesRegex(
            next_movievault_v2.MovieVaultV2Error,
            "^release_details_response_invalid$",
        ):
            next_movievault_v2.validate_release_details_response(payload)

    def test_release_details_rejects_provider_owned_or_unknown_fields(self):
        payload = release_details_hit(external=True)
        payload["release"]["description"] = "provider-owned prose"

        with self.assertRaisesRegex(
            next_movievault_v2.MovieVaultV2Error,
            "^release_details_response_invalid$",
        ):
            next_movievault_v2.validate_release_details_response(payload)

    def test_release_details_accepts_strict_ordered_box_set(self):
        payload = release_details_hit(external=True)
        payload["boxSet"] = {
            "state": "explicit",
            "title": "Example Collection",
            "alternateTitles": [],
            "format": "4K UHD",
            "barcodes": [
                {
                    "type": "ean13",
                    "value": "9780201379624",
                    "scope": "box_set",
                }
            ],
            "members": [
                {
                    "position": 1,
                    "title": "Example Film",
                    "year": 2024,
                    "barcodes": [
                        {
                            "type": "upca",
                            "value": "036000291452",
                            "scope": "member",
                        }
                    ],
                    "discNumber": 1,
                    "discFormat": "4K UHD",
                    "identifiers": {
                        "tmdbMovieId": "123",
                        "imdbId": "tt1234567",
                    },
                },
                {
                    "position": 2,
                    "title": "Example Film Two",
                    "barcodes": [
                        {
                            "type": "upca",
                            "value": "012345678905",
                            "scope": "disc",
                        }
                    ],
                    "discNumber": 2,
                    "discFormat": "Blu-ray",
                    "identifiers": {
                        "tmdbMovieId": "456",
                        "imdbId": "tt7654321",
                    },
                },
            ],
        }

        result = next_movievault_v2.validate_release_details_response(payload)

        self.assertEqual(result["boxSet"]["state"], "explicit")
        self.assertEqual(
            [member["position"] for member in result["boxSet"]["members"]],
            [1, 2],
        )

    def test_release_details_accepts_optional_poster_on_both_hit_statuses(self):
        for external in (False, True):
            with self.subTest(external=external):
                payload = release_details_hit(external=external)
                payload["poster"] = release_details_poster()

                result = next_movievault_v2.validate_release_details_response(payload)

                # An external_hit may carry an approved poster: the poster is
                # resolved from the canonical entity, so verificationStatus
                # must not filter it out.
                self.assertEqual(
                    result["poster"]["assetId"],
                    "40000000-0000-0000-0000-000000000001",
                )
                self.assertEqual(result["poster"]["assetType"], "front_cover")
                self.assertEqual(
                    result["poster"]["display"]["path"],
                    "/v2/assets/40000000-0000-0000-0000-000000000001/display",
                )

    def test_release_details_accepts_box_set_poster_alongside_box_set(self):
        payload = release_details_hit()
        payload["boxSet"] = release_details_box_set()
        payload["poster"] = release_details_poster()
        payload["boxSetPoster"] = release_details_poster(
            asset_id="40000000-0000-0000-0000-000000000002"
        )

        result = next_movievault_v2.validate_release_details_response(payload)

        self.assertEqual(
            result["poster"]["assetId"],
            "40000000-0000-0000-0000-000000000001",
        )
        self.assertEqual(
            result["boxSetPoster"]["assetId"],
            "40000000-0000-0000-0000-000000000002",
        )

    def test_release_details_rejects_box_set_poster_without_box_set(self):
        payload = release_details_hit()
        payload["boxSetPoster"] = release_details_poster()

        with self.assertRaisesRegex(
            next_movievault_v2.MovieVaultV2Error,
            "^release_details_response_invalid$",
        ):
            next_movievault_v2.validate_release_details_response(payload)

    def test_release_details_rejects_malformed_poster(self):
        cases = {
            "unknown_field": {"unexpected": "value"},
            "wrong_asset_type": {"assetType": "back_cover"},
            "unapproved_license": {"license": "all-rights-reserved"},
            "unapproved_attestation": {"attestation": "unverified"},
            "off_contract_asset_path": {
                "display": {
                    "checksum": "b" * 64,
                    "path": "https://evil.example/v2/assets/x/display",
                }
            },
            "short_checksum": {
                "display": {
                    "checksum": "b" * 10,
                    "path": "/v2/assets/40000000-0000-0000-0000-000000000001/display",
                }
            },
        }
        for name, override in cases.items():
            with self.subTest(case=name):
                payload = release_details_hit()
                payload["poster"] = {**release_details_poster(), **override}

                with self.assertRaisesRegex(
                    next_movievault_v2.MovieVaultV2Error,
                    "^release_details_response_invalid$",
                ):
                    next_movievault_v2.validate_release_details_response(payload)

    def test_release_details_without_poster_is_unchanged(self):
        payload = release_details_hit()

        result = next_movievault_v2.validate_release_details_response(payload)

        self.assertNotIn("poster", result)
        self.assertNotIn("boxSetPoster", result)

    def test_release_details_accepts_resolver_poster_without_checksum_or_claims(self):
        """Only the v4 catalog publishes a checksum, and the resolver types
        attestation/license as nested objects. Requiring either rejected every
        resolver poster, so a scanned disc never showed its cover."""
        asset_id = "40000000-0000-0000-0000-000000000001"
        cases = {
            "no_checksum_no_claims": {
                "assetId": asset_id,
                "assetType": "front_cover",
                "thumbnail": {"path": f"/v2/assets/{asset_id}/thumbnail"},
                "display": {"path": f"/v2/assets/{asset_id}/display"},
            },
            "nested_claims": {
                "assetId": asset_id,
                "assetType": "front_cover",
                "attestation": {"value": "licensed"},
                "license": {"value": "cc-by-4.0"},
                "thumbnail": {"path": f"/v2/assets/{asset_id}/thumbnail"},
                "display": {"path": f"/v2/assets/{asset_id}/display"},
            },
            "unreadable_claims_degrade_to_absent": {
                "assetId": asset_id,
                "assetType": "front_cover",
                "attestation": {"unexpected": ["shape"]},
                "license": 12,
                "thumbnail": {"path": f"/v2/assets/{asset_id}/thumbnail"},
                "display": {"path": f"/v2/assets/{asset_id}/display"},
            },
        }
        for name, poster in cases.items():
            with self.subTest(case=name):
                payload = release_details_hit()
                payload["poster"] = poster

                result = next_movievault_v2.validate_release_details_response(payload)

                self.assertEqual(result["poster"]["assetId"], asset_id)
                self.assertEqual(
                    result["poster"]["display"]["path"],
                    f"/v2/assets/{asset_id}/display",
                )

    def test_release_details_still_rejects_a_readable_unapproved_claim(self):
        """Leniency covers shape and absence only -- artwork DiscVault is not
        cleared to show must still be refused."""
        asset_id = "40000000-0000-0000-0000-000000000001"
        base = {
            "assetId": asset_id,
            "assetType": "front_cover",
            "thumbnail": {"path": f"/v2/assets/{asset_id}/thumbnail"},
            "display": {"path": f"/v2/assets/{asset_id}/display"},
        }
        for name, override in {
            "plain_license": {"license": "all-rights-reserved"},
            "nested_license": {"license": {"value": "all-rights-reserved"}},
            "plain_attestation": {"attestation": "scraped"},
        }.items():
            with self.subTest(case=name):
                payload = release_details_hit()
                payload["poster"] = {**base, **override}

                with self.assertRaisesRegex(
                    next_movievault_v2.MovieVaultV2Error,
                    "^release_details_response_invalid$",
                ):
                    next_movievault_v2.validate_release_details_response(payload)

    def test_poster_without_checksum_is_served_remotely_and_never_cached(self):
        """Unverifiable bytes must not be written to the artwork cache, so the
        cover is surfaced as MovieVault's stable anonymous asset URL instead."""
        asset_id = "40000000-0000-0000-0000-000000000001"
        poster = {
            "assetId": asset_id,
            "assetType": "front_cover",
            "attestation": None,
            "license": None,
            "thumbnail": {"path": f"/v2/assets/{asset_id}/thumbnail"},
            "display": {"path": f"/v2/assets/{asset_id}/display"},
        }

        class RefusesDatabaseAccess:
            def transaction(self):
                raise AssertionError("an unverifiable poster must not be cached")

            def cursor(self):
                raise AssertionError("an unverifiable poster must not be cached")

        fields = next_movievault_v2._release_details_poster_fields(
            RefusesDatabaseAccess(),
            "https://movies2.vaultstack.eu",
            poster,
        )

        self.assertEqual(
            fields["posterUrl"],
            f"https://movies2.vaultstack.eu/v2/assets/{asset_id}/display",
        )
        self.assertEqual(fields["posterStatus"], "remote")
        self.assertIsNone(fields["posterChecksum"])

    def test_localize_release_details_posters_exposes_only_local_fields(self):
        conn = FakePosterConn(
            {
                ("40000000-0000-0000-0000-000000000001", "b" * 64): {
                    "status": "ready",
                    "media_asset_id": "media-release",
                },
                ("40000000-0000-0000-0000-000000000002", "b" * 64): {
                    "status": "ready",
                    "media_asset_id": "media-box-set",
                },
            }
        )
        result = next_movievault_v2.validate_release_details_response(
            {
                **release_details_hit(),
                "boxSet": release_details_box_set(),
                "poster": release_details_poster(),
                "boxSetPoster": release_details_poster(
                    asset_id="40000000-0000-0000-0000-000000000002"
                ),
            }
        )

        localized = next_movievault_v2.localize_release_details_posters(
            conn, "https://movievault.example", result
        )

        # The remote MovieVault poster reference never reaches a caller.
        self.assertNotIn("poster", localized)
        self.assertNotIn("boxSetPoster", localized)
        self.assertEqual(
            localized["release"]["posterUrl"],
            "/api/next/movievault-v2/posters/media-release",
        )
        self.assertEqual(localized["release"]["posterStatus"], "ready")
        self.assertEqual(localized["release"]["posterChecksum"], "b" * 64)
        # The box-set poster belongs to the box set, never to the member release.
        self.assertEqual(
            localized["boxSet"]["posterUrl"],
            "/api/next/movievault-v2/posters/media-box-set",
        )
        # Both variants are queued through the existing anonymous cache path.
        self.assertEqual(len(conn.enqueued), 4)
        self.assertEqual(
            {(asset_id, variant) for asset_id, variant in conn.enqueued},
            {
                ("40000000-0000-0000-0000-000000000001", "thumbnail"),
                ("40000000-0000-0000-0000-000000000001", "display"),
                ("40000000-0000-0000-0000-000000000002", "thumbnail"),
                ("40000000-0000-0000-0000-000000000002", "display"),
            },
        )

    def test_localize_release_details_posters_drops_a_withdrawn_poster(self):
        conn = FakePosterConn({})
        result = next_movievault_v2.validate_release_details_response(
            release_details_hit()
        )

        localized = next_movievault_v2.localize_release_details_posters(
            conn, "https://movievault.example", result
        )

        # The association comes only from the current response, so a response
        # without a poster reports it as unavailable instead of reusing an
        # earlier one.
        self.assertIsNone(localized["release"]["posterUrl"])
        self.assertEqual(localized["release"]["posterStatus"], "unavailable")
        self.assertIsNone(localized["release"]["posterChecksum"])
        self.assertEqual(conn.enqueued, [])

    def test_localize_release_details_posters_reports_pending_before_cached(self):
        conn = FakePosterConn({})
        result = next_movievault_v2.validate_release_details_response(
            {**release_details_hit(), "poster": release_details_poster()}
        )

        localized = next_movievault_v2.localize_release_details_posters(
            conn, "https://movievault.example", result
        )

        self.assertIsNone(localized["release"]["posterUrl"])
        self.assertEqual(localized["release"]["posterStatus"], "pending")
        self.assertEqual(localized["release"]["posterChecksum"], "b" * 64)

    def test_release_details_callback_survives_poster_cache_failure(self):
        hit = release_details_hit()
        hit["poster"] = release_details_poster()
        resolved = next_movievault_v2.validate_release_details_response(hit)

        class BrokenConn:
            def transaction(self):
                raise RuntimeError("poster cache unavailable")

            def cursor(self):
                raise RuntimeError("poster cache unavailable")

        context = next_movievault_v2.movievault_v2_plugin_context(
            BrokenConn(),
            next_movievault_v2.MOVIEVAULT_V2_PLUGIN_ID,
            {"settings": {}},
        )
        with patch.object(
            next_movievault_v2,
            "resolve_release_details",
            return_value=resolved,
        ):
            result = context["movievaultV2ReleaseDetails"]({"barcode": "4006381333931"})

        # The barcode lookup still returns its technical data; only the poster
        # degrades to unavailable.
        self.assertEqual(result["status"], "canonical_hit")
        self.assertEqual(result["film"]["title"], "Example Film")
        self.assertNotIn("poster", result)
        self.assertEqual(result["release"]["posterStatus"], "unavailable")
        self.assertIsNone(result["release"]["posterUrl"])

    def test_release_details_http_status_maps_to_stable_value_free_error(self):
        with patch.object(
            next_movievault_v2,
            "_release_details_http",
            return_value=(429, b""),
        ):
            with self.assertRaisesRegex(
                next_movievault_v2.MovieVaultV2Error,
                "^release_details_rate_limited$",
            ):
                next_movievault_v2.resolve_release_details(
                    {"origin": "https://movievault.example"},
                    {"barcode": "4006381333931"},
                )

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
        self.assertIs(decorated["movievaultV2BucketFallback"], True)
        self.assertIn("movievaultV2ReleaseDetails", decorated)
        with self.assertRaisesRegex(
            next_movievault_v2.MovieVaultV2Error,
            "^core_bridge_unavailable$",
        ):
            decorated["movievaultV2Sync"]({})

    def test_origin_is_not_user_required_for_any_entrypoint(self):
        plugin = {
            "id": "movievault_v2",
            "manifest": {"requiresSecrets": False},
        }

        # Origin is enforced (default + optional MOVIEVAULT_V2_ORIGIN env), never
        # user-supplied, so v2 requires no user config for any entrypoint — with or
        # without a stored origin.
        self.assertFalse(next_metadata.plugin_requires_config(plugin, {"settings": {}}, "search_title"))
        self.assertFalse(next_worker.plugin_requires_config(plugin, {"settings": {}}, "sync_index"))
        self.assertFalse(
            next_metadata.plugin_requires_config(
                plugin,
                {"settings": {"origin": "https://movievault.example"}},
                "search_title",
            )
        )
        self.assertFalse(next_worker.plugin_requires_config(plugin, {"settings": {}}, "health_check"))
        self.assertIn("sync_index", next_plugin_runtime.PLUGIN_ENTRYPOINTS)


V4_FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "distribution-v4-full.ndjson")


class AudioSubtitleTrackContractTests(unittest.TestCase):
    """distribution-4 audioTracks / subtitleLanguages parsing (PR #159 on
    MovieVault-v2). See test_next_movievault_v2_postgres.py for the
    persistence-level coverage of the same feature."""

    def _v4_release(self, **overrides) -> dict:
        with open(V4_FIXTURE_PATH, "rb") as handle:
            lines = handle.read().splitlines()
        record = None
        for line in lines:
            candidate = json.loads(line)
            if candidate.get("recordType") == "release" and candidate.get("releaseId") == (
                "10000000-0000-0000-0000-000000000001"
            ):
                record = candidate
                break
        assert record is not None
        record.update(overrides)
        return record

    def test_accepts_known_audio_track_and_subtitle_fields(self):
        parsed = next_movievault_v2.validate_record(
            self._v4_release(), contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
        )
        self.assertEqual(
            parsed["audioTracks"],
            [
                {
                    "languageCode": "en",
                    "codec": "dolby_truehd",
                    "channels": "7.1",
                    "immersiveFormat": "dolby_atmos",
                },
                {
                    "languageCode": "nl",
                    "codec": "dolby_digital",
                    "channels": "5.1",
                    "immersiveFormat": None,
                },
            ],
        )
        self.assertEqual(
            parsed["subtitles"],
            [
                {"languageCode": "en", "subtitleType": "full"},
                # The same language twice with different variants - the thing the
                # old bare-language-list shape could not express at all.
                {"languageCode": "en", "subtitleType": "sdh"},
                {"languageCode": "nl", "subtitleType": "full"},
            ],
        )

    def test_v4_release_without_the_technical_fields_still_parses(self):
        """The live feed serves release records that carry `poster` but none of
        the eight technical fields - poster support landed in distribution-4
        before the audio/subtitle/packaging/video work did, and records published
        in between were never re-projected. Demanding them rejected 342 of 359
        records on the production feed, i.e. the whole catalog, for the sake of
        supplementary metadata."""
        record = self._v4_release()
        for key in (
            "audioTracks", "subtitles", "packaging", "videoResolution",
            "videoCodecs", "hdrFormats", "aspectRatios", "discRegions",
        ):
            del record[key]
        parsed = next_movievault_v2.validate_record(
            record, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
        )
        # Absent decodes exactly like "nothing known", so downstream sees one shape.
        self.assertEqual(parsed["audioTracks"], [])
        self.assertEqual(parsed["subtitles"], [])
        self.assertEqual(parsed["packaging"], [])
        self.assertIsNone(parsed["videoResolution"])
        self.assertEqual(parsed["videoCodecs"], [])
        self.assertEqual(parsed["hdrFormats"], [])
        self.assertEqual(parsed["aspectRatios"], [])
        self.assertEqual(parsed["discRegions"], [])
        # The identity of the release is untouched.
        self.assertEqual(parsed["releaseId"], record["releaseId"])
        self.assertIsNotNone(parsed["poster"])

    def test_v4_release_still_requires_its_poster(self):
        # Unlike the technical fields, poster is present on every record the feed
        # serves, so its absence is a genuine contract break rather than history.
        record = self._v4_release()
        del record["poster"]
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                record, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
            )

    def test_v4_technical_fields_are_still_decoded_when_present(self):
        parsed = next_movievault_v2.validate_record(
            self._v4_release(), contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
        )
        self.assertTrue(parsed["audioTracks"])
        self.assertTrue(parsed["subtitles"])

    def test_v3_release_does_not_require_or_accept_the_new_fields(self):
        record = self._v4_release(contractVersion=next_movievault_v2.MOVIEVAULT_V3_CONTRACT)
        for key in (
            "audioTracks",
            "subtitles",
            "packaging",
            "poster",
            "videoResolution",
            "videoCodecs",
            "hdrFormats",
            "aspectRatios",
            "discRegions",
        ):
            del record[key]
        parsed = next_movievault_v2.validate_record(
            record, contract_version=next_movievault_v2.MOVIEVAULT_V3_CONTRACT
        )
        self.assertEqual(parsed["audioTracks"], [])
        self.assertEqual(parsed["subtitles"], [])
        self.assertEqual(parsed["packaging"], [])
        self.assertIsNone(parsed["videoResolution"])
        self.assertEqual(parsed["videoCodecs"], [])
        self.assertEqual(parsed["hdrFormats"], [])
        self.assertEqual(parsed["aspectRatios"], [])
        self.assertEqual(parsed["discRegions"], [])

        record_with_extra = self._v4_release(contractVersion=next_movievault_v2.MOVIEVAULT_V3_CONTRACT)
        del record_with_extra["poster"]
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                record_with_extra, contract_version=next_movievault_v2.MOVIEVAULT_V3_CONTRACT
            )

    def test_rejects_more_than_fifty_audio_tracks(self):
        track = {
            "languageCode": "en",
            "codec": "aac",
            "channels": "2.0",
            "immersiveFormat": None,
        }
        record = self._v4_release(audioTracks=[track] * 51)
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                record, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
            )

    def test_rejects_more_than_fifty_subtitle_languages(self):
        record = self._v4_release(subtitleLanguages=["en"] * 51)
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                record, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
            )

    def _audio_language(self, code):
        track = {
            "languageCode": code,
            "codec": "aac",
            "channels": None,
            "immersiveFormat": None,
        }
        parsed = next_movievault_v2.validate_record(
            self._v4_release(audioTracks=[track]),
            contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
        )
        return parsed["audioTracks"][0]["languageCode"]

    def test_keeps_an_unrecognized_language_code_on_an_audio_track(self):
        """MovieVault applies no pattern to the published languageCode: the v4
        schema allows any string up to 35 characters, and the publisher
        re-asserts audioTracks as raw rows after the model dump, so its own
        BCP-47 pattern never runs on what ships. Rejecting here cost the entire
        release record - and the whole sync - over the casing of a language tag."""
        for code in ("en-US", "pt-BR", "zh-Hans", "EN", "e", "en_US"):
            with self.subTest(language_code=code):
                self.assertEqual(self._audio_language(code), code)

    def test_rejects_an_unusable_language_code_on_an_audio_track(self):
        # Type and length stay strict - those bound what can be stored.
        for bad_code in ("x" * 36, "", None, 42, ["en"]):
            with self.subTest(language_code=bad_code):
                with self.assertRaisesRegex(
                    next_movievault_v2.MovieVaultV2Error, "^record_invalid$"
                ):
                    self._audio_language(bad_code)

    def test_rejects_malformed_subtitle_language_code(self):
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                self._v4_release(subtitleLanguages=["EN"]),
                contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
            )

    def test_rejects_wrong_type_for_codec(self):
        track = {"languageCode": "en", "codec": 123, "channels": None, "immersiveFormat": None}
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                self._v4_release(audioTracks=[track]),
                contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
            )

    def test_unrecognized_codec_is_stored_raw_and_logs_a_warning_instead_of_raising(self):
        track = {
            "languageCode": "en",
            "codec": "dts_x_experimental_future_codec",
            "channels": "5.1",
            "immersiveFormat": None,
        }
        with self.assertLogs("app.backend.next_movievault_v2", level="WARNING") as logs:
            parsed = next_movievault_v2.validate_record(
                self._v4_release(audioTracks=[track]),
                contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
            )
        self.assertEqual(parsed["audioTracks"][0]["codec"], "dts_x_experimental_future_codec")
        self.assertTrue(any("codec" in message for message in logs.output))

    def test_unrecognized_channels_and_immersive_format_are_stored_raw_and_logged(self):
        track = {
            "languageCode": "en",
            "codec": "aac",
            "channels": "9.1",
            "immersiveFormat": "some_future_format",
        }
        with self.assertLogs("app.backend.next_movievault_v2", level="WARNING") as logs:
            parsed = next_movievault_v2.validate_record(
                self._v4_release(audioTracks=[track]),
                contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
            )
        self.assertEqual(parsed["audioTracks"][0]["channels"], "9.1")
        self.assertEqual(parsed["audioTracks"][0]["immersiveFormat"], "some_future_format")
        self.assertEqual(len(logs.output), 2)

    def test_accepts_known_packaging_values(self):
        parsed = next_movievault_v2.validate_record(
            self._v4_release(packaging=["steelbook", "slipcover"]),
            contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
        )
        self.assertEqual(parsed["packaging"], ["steelbook", "slipcover"])

    def test_rejects_more_than_nine_packaging_entries(self):
        record = self._v4_release(packaging=["keep_case"] * 10)
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                record, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
            )

    def test_rejects_wrong_type_for_packaging_entry(self):
        record = self._v4_release(packaging=[123])
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                record, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
            )

    def test_unrecognized_packaging_value_is_stored_raw_and_logs_a_warning(self):
        with self.assertLogs("app.backend.next_movievault_v2", level="WARNING") as logs:
            parsed = next_movievault_v2.validate_record(
                self._v4_release(packaging=["a_future_packaging_type"]),
                contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
            )
        self.assertEqual(parsed["packaging"], ["a_future_packaging_type"])
        self.assertTrue(any("packaging" in message for message in logs.output))

    def test_box_set_record_never_carries_audio_or_subtitle_fields(self):
        with open(V4_FIXTURE_PATH, "rb") as handle:
            lines = handle.read().splitlines()
        box_set = next(
            json.loads(line) for line in lines if json.loads(line).get("recordType") == "box_set"
        )
        parsed = next_movievault_v2.validate_record(
            box_set, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
        )
        self.assertNotIn("audioTracks", parsed)
        self.assertNotIn("subtitleLanguages", parsed)
        self.assertNotIn("packaging", parsed)

        box_set_with_tracks = dict(box_set, audioTracks=[])
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                box_set_with_tracks, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
            )

        box_set_with_packaging = dict(box_set, packaging=[])
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                box_set_with_packaging, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
            )


if __name__ == "__main__":
    unittest.main()


class VideoProfileContractTests(unittest.TestCase):
    """distribution-4's video facts (MovieVault PR #161).

    Note what a rejection costs here: `_exact_keys` makes the v4 key set exhaustive
    and `record_invalid` discards the *whole* release record, not one field. That is
    why every enum below logs-and-stores rather than raising.
    """

    def _v4_release(self, **overrides) -> dict:
        with open(V4_FIXTURE_PATH, "rb") as handle:
            for line in handle.read().splitlines():
                candidate = json.loads(line)
                if candidate.get("recordType") == "release" and candidate.get(
                    "releaseId"
                ) == "10000000-0000-0000-0000-000000000001":
                    candidate.update(overrides)
                    return candidate
        raise AssertionError("fixture release not found")

    def _parse(self, **overrides) -> dict:
        return next_movievault_v2.validate_record(
            self._v4_release(**overrides),
            contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
        )

    def test_accepts_the_full_video_profile(self):
        parsed = self._parse()
        self.assertEqual(parsed["videoResolution"], "2160p")
        self.assertEqual(parsed["videoCodecs"], ["hevc"])
        self.assertEqual(parsed["hdrFormats"], ["dolby_vision", "hdr10"])
        self.assertEqual(parsed["aspectRatios"], ["2.39:1"])
        self.assertEqual(parsed["discRegions"], ["B"])

    def test_a_null_resolution_and_empty_arrays_are_valid(self):
        parsed = self._parse(
            videoResolution=None,
            videoCodecs=[],
            hdrFormats=[],
            aspectRatios=[],
            discRegions=[],
        )
        self.assertIsNone(parsed["videoResolution"])
        self.assertEqual(parsed["videoCodecs"], [])

    def test_every_video_key_is_optional_on_v4(self):
        """Records published before the video work landed omit these entirely.
        An absent key decodes to the same empty value a record with nothing to
        report carries, so the release survives either way."""
        empty = {
            "videoResolution": None,
            "videoCodecs": [],
            "hdrFormats": [],
            "aspectRatios": [],
            "discRegions": [],
        }
        for key, blank in empty.items():
            with self.subTest(key=key):
                record = self._v4_release()
                del record[key]
                parsed = next_movievault_v2.validate_record(
                    record, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
                )
                self.assertEqual(parsed[key], blank)

    def test_a_malformed_video_key_is_still_rejected(self):
        # Tolerating absence must not tolerate a wrong type.
        record = self._v4_release(videoCodecs="hevc")
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            next_movievault_v2.validate_record(
                record, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
            )

    def test_unrecognized_enum_members_are_stored_raw_and_logged(self):
        with self.assertLogs("app.backend.next_movievault_v2", level="WARNING") as logs:
            parsed = self._parse(
                videoResolution="4320p",
                videoCodecs=["vvc"],
                hdrFormats=["hdr_vivid"],
                discRegions=["Z"],
            )
        self.assertEqual(parsed["videoResolution"], "4320p")
        self.assertEqual(parsed["videoCodecs"], ["vvc"])
        self.assertEqual(parsed["hdrFormats"], ["hdr_vivid"])
        self.assertEqual(parsed["discRegions"], ["Z"])
        self.assertEqual(len(logs.output), 4)

    def test_duplicates_are_dropped_rather_than_rejected(self):
        parsed = self._parse(videoCodecs=["hevc", "hevc", "av1"])
        self.assertEqual(parsed["videoCodecs"], ["hevc", "av1"])

    def test_the_documented_maximums_are_enforced(self):
        for key, value in (
            ("videoCodecs", ["hevc"] * 6),
            ("hdrFormats", ["hdr10"] * 6),
            ("aspectRatios", ["2.39:1"] * 9),
            ("discRegions", ["B"] * 13),
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(
                    next_movievault_v2.MovieVaultV2Error, "^record_invalid$"
                ):
                    self._parse(**{key: value})

    def test_twelve_disc_regions_are_accepted(self):
        regions = ["A", "B", "C", "1", "2", "3", "4", "5", "6", "7", "8", "FREE"]
        self.assertEqual(self._parse(discRegions=regions)["discRegions"], regions)

    def test_common_aspect_ratios_validate(self):
        """The old release-details regex was `^(?:1|2)\\.[0-9]{2}:1$`, which rejected
        every one of these - and rejected them by discarding the whole response."""
        ratios = ["16:9", "4:3", "1.375:1", "2.39:1", "1.85:1", "2.76:1"]
        with self.assertNoLogs("app.backend.next_movievault_v2", level="WARNING"):
            self.assertEqual(self._parse(aspectRatios=ratios)["aspectRatios"], ratios)

    def test_a_non_ratio_string_is_kept_but_logged(self):
        with self.assertLogs("app.backend.next_movievault_v2", level="WARNING"):
            self.assertEqual(self._parse(aspectRatios=["widescreen"])["aspectRatios"], ["widescreen"])

    def test_wrong_types_still_raise(self):
        for key, value in (
            ("videoResolution", 2160),
            ("videoCodecs", "hevc"),
            ("hdrFormats", [None]),
            ("aspectRatios", [239]),
            ("discRegions", {"region": "B"}),
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(
                    next_movievault_v2.MovieVaultV2Error, "^record_invalid$"
                ):
                    self._parse(**{key: value})


class SubtitleVariantContractTests(unittest.TestCase):
    """distribution-4's structured subtitles (MovieVault PR #162)."""

    def _v4_release(self, **overrides) -> dict:
        with open(V4_FIXTURE_PATH, "rb") as handle:
            for line in handle.read().splitlines():
                candidate = json.loads(line)
                if candidate.get("recordType") == "release" and candidate.get(
                    "releaseId"
                ) == "10000000-0000-0000-0000-000000000001":
                    candidate.update(overrides)
                    return candidate
        raise AssertionError("fixture release not found")

    def _parse(self, **overrides) -> dict:
        return next_movievault_v2.validate_record(
            self._v4_release(**overrides),
            contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT,
        )

    def test_the_same_language_survives_twice_with_different_variants(self):
        parsed = self._parse(
            subtitles=[
                {"languageCode": "en", "subtitleType": "full"},
                {"languageCode": "en", "subtitleType": "sdh"},
                {"languageCode": "en", "subtitleType": "forced"},
            ]
        )
        self.assertEqual([item["subtitleType"] for item in parsed["subtitles"]], ["full", "sdh", "forced"])

    def test_every_documented_variant_is_accepted(self):
        for variant in ("full", "sdh", "forced", "commentary", "closed_caption"):
            with self.subTest(variant=variant):
                parsed = self._parse(subtitles=[{"languageCode": "en", "subtitleType": variant}])
                self.assertEqual(parsed["subtitles"][0]["subtitleType"], variant)

    def test_an_unknown_variant_is_stored_raw_and_logged(self):
        with self.assertLogs("app.backend.next_movievault_v2", level="WARNING"):
            parsed = self._parse(subtitles=[{"languageCode": "en", "subtitleType": "karaoke"}])
        self.assertEqual(parsed["subtitles"][0]["subtitleType"], "karaoke")

    def test_a_bare_language_string_is_rejected(self):
        """The feed sends objects since PR #162. A string here means the producer and
        this consumer disagree about the contract, which is worth failing loudly."""
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            self._parse(subtitles=["en"])

    def test_a_missing_or_extra_key_on_a_subtitle_is_rejected(self):
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            self._parse(subtitles=[{"languageCode": "en"}])
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            self._parse(
                subtitles=[{"languageCode": "en", "subtitleType": "full", "forced": True}]
            )

    def test_an_unrecognized_language_code_is_kept(self):
        # Same leniency as every other distribution-4 track field: a regional or
        # script subtag must not cost the release record.
        parsed = self._parse(
            subtitles=[{"languageCode": "pt-BR", "subtitleType": "full"}]
        )
        self.assertEqual(parsed["subtitles"][0]["languageCode"], "pt-BR")

    def test_an_unusable_language_code_is_still_rejected(self):
        with self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^record_invalid$"):
            self._parse(subtitles=[{"languageCode": "", "subtitleType": "full"}])


class RecordRejectionDiagnosticsTests(unittest.TestCase):
    """A rejected record fails the whole sync, so the log has to say which record
    and which field. `record_invalid` is value-free by design where it crosses
    the plugin boundary, which previously left an operator with a failed sync and
    nothing at all to go on."""

    def _v4_release(self, **overrides) -> dict:
        with open(V4_FIXTURE_PATH, "rb") as handle:
            for line in handle.read().splitlines():
                candidate = json.loads(line)
                if candidate.get("recordType") == "release" and candidate.get(
                    "releaseId"
                ) == "10000000-0000-0000-0000-000000000001":
                    candidate.update(overrides)
                    return candidate
        raise AssertionError("v4 release fixture not found")

    def _validate(self, record):
        with self.assertLogs("app.backend.next_movievault_v2", level="WARNING") as logs:
            with self.assertRaisesRegex(
                next_movievault_v2.MovieVaultV2Error, "^record_invalid$"
            ):
                next_movievault_v2.validate_record(
                    record, contract_version=next_movievault_v2.MOVIEVAULT_V4_CONTRACT
                )
        return "\n".join(logs.output)

    def test_missing_key_names_the_object_and_the_key(self):
        track = {"languageCode": "en", "codec": "aac", "channels": None}
        output = self._validate(self._v4_release(audioTracks=[track]))
        self.assertIn("audio track rejected", output)
        self.assertIn("immersiveFormat", output)

    def test_unexpected_key_names_the_object_and_the_key(self):
        track = {
            "languageCode": "en",
            "codec": "aac",
            "channels": None,
            "immersiveFormat": None,
            "atmosCertified": True,
        }
        output = self._validate(self._v4_release(audioTracks=[track]))
        self.assertIn("audio track rejected", output)
        self.assertIn("atmosCertified", output)

    def test_every_rejection_identifies_the_record(self):
        output = self._validate(self._v4_release(packaging="not-a-list"))
        self.assertIn("rejected record", output)
        self.assertIn("releaseId=10000000-0000-0000-0000-000000000001", output)
        self.assertIn("recordType=release", output)

    def test_identity_carries_no_free_text(self):
        # The v2 feed is consumed anonymously; the log must stay to identifiers.
        identity = next_movievault_v2._record_identity(
            self._v4_release(canonicalTitle="A Very Identifying Title")
        )
        self.assertNotIn("A Very Identifying Title", identity)
        self.assertIn("releaseId=", identity)

    def test_identity_survives_a_malformed_record(self):
        self.assertEqual(
            next_movievault_v2._record_identity("not-an-object"), "<non-object record>"
        )
        self.assertEqual(
            next_movievault_v2._record_identity({}), "<record without identifiers>"
        )
