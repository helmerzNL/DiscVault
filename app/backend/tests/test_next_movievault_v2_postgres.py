from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:
    psycopg = None
    dict_row = None
    Jsonb = None

from app.backend import next_app
from app.backend import next_metadata
from app.backend import next_movievault_v2
from app.backend import next_movievault_v2_posters
from app.backend import next_plugin_runtime
from app.backend import next_worker
from app.backend.next_plugin_runtime import PluginDiscovery


DATABASE_URL = os.environ.get("DATABASE_URL")
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "distribution-v2.ndjson"
V3_FULL_PATH = Path(__file__).parent / "fixtures" / "distribution-v3-full.ndjson"
V3_DELTA_PATH = Path(__file__).parent / "fixtures" / "distribution-v3-delta.ndjson"
V4_FULL_PATH = Path(__file__).parent / "fixtures" / "distribution-v4-full.ndjson"
V4_DELTA_PATH = Path(__file__).parent / "fixtures" / "distribution-v4-delta.ndjson"
DATASET_CHECKSUM = hashlib.sha256(b"approved lookup hash dataset").hexdigest()

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


def _v4_manifest(*, revision=42, cursor="fixture-distribution-4-r42", checksum=None):
    return {
        "contractVersion": "distribution-4",
        "currentRevision": revision,
        "currentCursor": cursor,
        "bucketPrefixLength": 1,
        "hashAlgorithm": "sha256",
        "datasetChecksum": checksum or ("b" * 64),
        "deltaPath": "/v4/index/delta",
        "bucketPathTemplate": "/v4/bucket/{prefix}",
    }


def _png_bytes(size=(10, 10)):
    import io as _io

    from PIL import Image as _Image

    buffer = _io.BytesIO()
    _Image.new("RGB", size, color=(4, 5, 6)).save(buffer, format="PNG")
    return buffer.getvalue()


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class MovieVaultV2PostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)

    def _clear_state(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    TRUNCATE
                        movievault_v2_lookup_hashes,
                        movievault_v2_box_set_members,
                        movievault_v2_box_sets,
                        movievault_v2_releases,
                        movievault_v2_poster_cache,
                        movievault_v2_sync_state
                    CASCADE
                    """
                )
                cur.execute(
                    """
                    DELETE FROM background_jobs
                    WHERE payload ->> 'pluginId' = %s
                       OR job_type IN (%s, %s)
                    """,
                    (
                        next_movievault_v2.MOVIEVAULT_V2_PLUGIN_ID,
                        next_movievault_v2.POSTER_CACHE_JOB_TYPE,
                        next_movievault_v2.POSTER_CLEANUP_JOB_TYPE,
                    ),
                )
                cur.execute(
                    "DELETE FROM plugins WHERE id = %s",
                    (next_movievault_v2.MOVIEVAULT_V2_PLUGIN_ID,),
                )
                cur.execute(
                    """
                    DELETE FROM media_assets
                    WHERE provider_id LIKE 'movievault_v2:%'
                       OR provider_id = 'test:movievault-v2-public-media'
                    """
                )

    def setUp(self):
        self._clear_state()

    def tearDown(self):
        self._clear_state()

    def fixture(self) -> bytes:
        return FIXTURE_PATH.read_bytes()

    def publisher_ordered_fixture(self) -> bytes:
        records = [json.loads(line) for line in self.fixture().splitlines()]
        records.sort(
            key=lambda record: (
                record["recordType"],
                record.get("releaseId") or record.get("boxSetId"),
            )
        )
        return b"".join(
            json.dumps(
                record,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            + b"\n"
            for record in records
        )

    def table_exists(self, conn, table_name):
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s) AS table_name", (f"public.{table_name}",))
            row = cur.fetchone()
        return bool(row and row.get("table_name"))

    def manifest(self, *, revision=42, cursor="cursor-value-long-enough", checksum=None):
        return {
            "contractVersion": "distribution-2",
            "currentRevision": revision,
            "currentCursor": cursor,
            "bucketPrefixLength": 4,
            "hashAlgorithm": "sha256",
            "datasetChecksum": checksum or DATASET_CHECKSUM,
            "deltaPath": "/v2/index/delta",
            "bucketPathTemplate": "/v2/bucket/{prefix}",
        }

    def test_sync_state_admits_every_supported_contract(self):
        """The CHECK on `contract_version` is a fourth place the vocabulary is
        enumerated, and nothing links it to SUPPORTED_CONTRACTS.

        distribution-5 was parsed, capped, migrated (071) and declared in the
        plugin manifest while 039 still allowed only up to -4. Every record of
        the bootstrap was downloaded and written correctly; the single row
        recording the success was rejected, which rolled all of it back. The
        sync could never complete and the feed stayed on revision 3391.

        Asserting the constraint against the tuple rather than against a list
        of names is deliberate: a future distribution-6 fails here, in a second,
        instead of on a production bootstrap an hour in.
        """
        for contract_version in next_movievault_v2.SUPPORTED_CONTRACTS:
            with self.subTest(contract_version=contract_version):
                with self.connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO movievault_v2_sync_state (
                                plugin_id, origin, contract_version, status
                            )
                            VALUES (%s, %s, %s, 'current')
                            ON CONFLICT (plugin_id) DO UPDATE
                            SET contract_version = EXCLUDED.contract_version
                            """,
                            (
                                next_movievault_v2.MOVIEVAULT_V2_PLUGIN_ID,
                                "https://movievault.example",
                                contract_version,
                            ),
                        )
                    conn.commit()
        self._clear_state()

    def test_full_delta_current_tombstone_and_failed_digest_are_atomic(self):
        fixture = self.fixture()
        full_fixture = self.publisher_ordered_fixture()
        fixture_digest = hashlib.sha256(full_fixture).hexdigest()
        settings = {"origin": "https://movievault.example"}
        initial_manifest = self.manifest()
        full_revisions = [json.loads(line)["revision"] for line in full_fixture.splitlines()]
        self.assertTrue(
            any(current <= prior for prior, current in zip(full_revisions, full_revisions[1:]))
        )

        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=initial_manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    full_fixture,
                    {
                        "x-content-sha256": fixture_digest,
                        "x-next-cursor": "cursor-value-long-enough",
                    },
                ),
            ),
        ):
            full = next_movievault_v2.run_sync(self.connect, settings)

        self.assertEqual(full["mode"], "full")
        self.assertEqual(full["recordsApplied"], 3)
        self.assertNotEqual(initial_manifest["datasetChecksum"], fixture_digest)
        release_hash = json.loads(fixture.splitlines()[0])["eanHashes"][0]
        with self.connect() as conn:
            barcode = next_movievault_v2.local_lookup(
                conn,
                {"kind": "barcode", "hash": release_hash, "limit": 10},
            )
            title = next_movievault_v2.local_lookup(
                conn,
                {"kind": "title", "query": "Example", "limit": 10},
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT dataset_checksum
                    FROM movievault_v2_sync_state
                    WHERE plugin_id = %s
                    """,
                    (next_movievault_v2.MOVIEVAULT_V2_PLUGIN_ID,),
                )
                stored_dataset_checksum = cur.fetchone()["dataset_checksum"]
        self.assertEqual(stored_dataset_checksum, initial_manifest["datasetChecksum"])
        self.assertEqual(
            {item["recordType"] for item in barcode["results"]},
            {"release", "box_set"},
        )
        self.assertEqual(
            {item["recordType"] for item in title["results"]},
            {"release", "box_set"},
        )
        box = next(item for item in barcode["results"] if item["recordType"] == "box_set")
        self.assertEqual(
            [member["releaseEdition"] for member in box["members"]],
            ["Theatrical", "Director's Cut"],
        )

        box_delta = copy.deepcopy(json.loads(fixture.splitlines()[2]))
        box_delta["revision"] = 43
        box_delta["members"][1]["releaseEdition"] = "Final Cut"
        delta_content = json.dumps(
            box_delta,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii") + b"\n"
        delta_digest = hashlib.sha256(delta_content).hexdigest()
        delta_manifest = self.manifest(
            revision=43,
            cursor="cursor-value-next-long-enough",
            checksum="f" * 64,
        )
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=delta_manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    delta_content,
                    {
                        "x-content-sha256": delta_digest,
                        "x-next-cursor": delta_manifest["currentCursor"],
                    },
                ),
            ),
        ):
            delta = next_movievault_v2.run_sync(self.connect, settings)

        self.assertEqual(delta["mode"], "delta")
        with self.connect() as conn:
            exact_box = next_movievault_v2.local_lookup(
                conn,
                {
                    "kind": "box_set",
                    "boxSetId": box_delta["boxSetId"],
                    "limit": 1,
                },
            )["results"][0]
        self.assertEqual(exact_box["members"][1]["releaseEdition"], "Final Cut")

        current_manifest = self.manifest(
            revision=43,
            cursor="cursor-value-next-long-enough",
            checksum="f" * 64,
        )
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=current_manifest),
            patch.object(next_movievault_v2, "_fetch_feed", return_value=(204, b"", {})),
        ):
            current = next_movievault_v2.run_sync(self.connect, settings)
        self.assertEqual(current["mode"], "current")

        failed_manifest = self.manifest(
            revision=44,
            cursor="cursor-value-failed-long-enough",
            checksum="e" * 64,
        )
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=failed_manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(200, b"invalid", {"x-content-sha256": "0" * 64}),
            ),
            self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^checksum_invalid$"),
        ):
            next_movievault_v2.run_sync(self.connect, settings)
        with self.connect() as conn:
            state = next_movievault_v2.sync_status(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cursor, active_generation FROM movievault_v2_sync_state WHERE plugin_id = %s",
                    (next_movievault_v2.MOVIEVAULT_V2_PLUGIN_ID,),
                )
                stored = cur.fetchone()
        self.assertEqual(state["state"], "error")
        self.assertEqual(state["errorCode"], "checksum_invalid")
        self.assertEqual(stored["cursor"], "cursor-value-next-long-enough")
        self.assertIsNotNone(stored["active_generation"])

        tombstone = {
            "contractVersion": "distribution-2",
            "recordType": "release",
            "operation": "delete",
            "revision": 44,
            "entityId": "10000000-0000-0000-0000-000000000001",
        }
        tombstone_content = json.dumps(
            tombstone,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii") + b"\n"
        tombstone_digest = hashlib.sha256(tombstone_content).hexdigest()
        tombstone_manifest = self.manifest(
            revision=44,
            cursor="cursor-value-tombstone-long-enough",
            checksum="d" * 64,
        )
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=tombstone_manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    tombstone_content,
                    {
                        "x-content-sha256": tombstone_digest,
                        "x-next-cursor": tombstone_manifest["currentCursor"],
                    },
                ),
            ),
        ):
            next_movievault_v2.run_sync(self.connect, settings)
        with self.connect() as conn:
            removed = next_movievault_v2.local_lookup(
                conn,
                {
                    "kind": "release",
                    "releaseId": tombstone["entityId"],
                    "limit": 1,
                },
            )
        self.assertEqual(removed["results"], [])

    def test_cursor_conflict_forces_shadow_generation_full_resync(self):
        fixture = self.publisher_ordered_fixture()
        digest = hashlib.sha256(fixture).hexdigest()
        settings = {"origin": "https://movievault.example"}
        first_manifest = self.manifest()
        self.assertNotEqual(first_manifest["datasetChecksum"], digest)
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=first_manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    fixture,
                    {"x-content-sha256": digest, "x-next-cursor": first_manifest["currentCursor"]},
                ),
            ),
        ):
            next_movievault_v2.run_sync(self.connect, settings)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT active_generation FROM movievault_v2_sync_state WHERE plugin_id = %s",
                    (next_movievault_v2.MOVIEVAULT_V2_PLUGIN_ID,),
                )
                first_generation = cur.fetchone()["active_generation"]

        replacement_manifest = self.manifest(
            revision=43,
            cursor="cursor-value-replacement-long-enough",
        )
        self.assertNotEqual(replacement_manifest["datasetChecksum"], digest)
        responses = [
            (409, b"", {}),
            (
                200,
                fixture,
                {
                    "x-content-sha256": digest,
                    "x-next-cursor": replacement_manifest["currentCursor"],
                },
            ),
        ]
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=replacement_manifest),
            patch.object(next_movievault_v2, "_fetch_feed", side_effect=responses),
        ):
            result = next_movievault_v2.run_sync(self.connect, settings)

        self.assertEqual(result["mode"], "full")
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT active_generation FROM movievault_v2_sync_state WHERE plugin_id = %s",
                    (next_movievault_v2.MOVIEVAULT_V2_PLUGIN_ID,),
                )
                second_generation = cur.fetchone()["active_generation"]
                cur.execute(
                    """
                    SELECT count(DISTINCT generation) AS generations
                    FROM (
                        SELECT generation FROM movievault_v2_releases
                        UNION ALL
                        SELECT generation FROM movievault_v2_box_sets
                    ) AS indexed
                    """
                )
                generation_count = cur.fetchone()["generations"]
        self.assertNotEqual(first_generation, second_generation)
        self.assertEqual(generation_count, 1)

    def test_delta_more_than_one_publish_behind_walks_the_cursor_chain(self):
        """The delta feed serves one MovieVault publication segment per request. An
        instance more than one publish behind gets an intermediate segment back -
        its next-cursor is the next hop, not the manifest's head. A single run_sync()
        call must walk the whole chain rather than reject the first hop short of the
        head as `cursor_invalid` (which used to fail every sync permanently, since
        the stored cursor never advanced past the first hop)."""
        settings = {"origin": "https://movievault.example"}
        full_fixture = self.publisher_ordered_fixture()
        full_digest = hashlib.sha256(full_fixture).hexdigest()
        initial_manifest = self.manifest()
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=initial_manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    full_fixture,
                    {
                        "x-content-sha256": full_digest,
                        "x-next-cursor": "cursor-value-long-enough",
                    },
                ),
            ),
        ):
            next_movievault_v2.run_sync(self.connect, settings)

        release = json.loads(self.fixture().splitlines()[1])
        hop_one = copy.deepcopy(release)
        hop_one["revision"] = 43
        hop_one["edition"] = "Hop One Cut"
        hop_one_content = json.dumps(
            hop_one, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii") + b"\n"
        hop_one_digest = hashlib.sha256(hop_one_content).hexdigest()

        hop_two = copy.deepcopy(release)
        hop_two["revision"] = 44
        hop_two["edition"] = "Hop Two Cut"
        hop_two_content = json.dumps(
            hop_two, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii") + b"\n"
        hop_two_digest = hashlib.sha256(hop_two_content).hexdigest()

        head_manifest = self.manifest(revision=44, cursor="cursor-value-hop-two-long-enough")
        responses = [
            (
                200,
                hop_one_content,
                {
                    "x-content-sha256": hop_one_digest,
                    # Intermediate hop: not the manifest's head cursor.
                    "x-next-cursor": "cursor-value-hop-one-long-enough",
                },
            ),
            (
                200,
                hop_two_content,
                {
                    "x-content-sha256": hop_two_digest,
                    "x-next-cursor": head_manifest["currentCursor"],
                },
            ),
        ]
        fetch_mock = patch.object(next_movievault_v2, "_fetch_feed", side_effect=responses)
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=head_manifest),
            fetch_mock as mocked_fetch_feed,
        ):
            result = next_movievault_v2.run_sync(self.connect, settings)

        self.assertEqual(mocked_fetch_feed.call_count, 2)
        first_call_kwargs = mocked_fetch_feed.call_args_list[0].kwargs
        second_call_kwargs = mocked_fetch_feed.call_args_list[1].kwargs
        self.assertEqual(first_call_kwargs["cursor"], "cursor-value-long-enough")
        self.assertEqual(second_call_kwargs["cursor"], "cursor-value-hop-one-long-enough")

        self.assertEqual(result["mode"], "delta")
        self.assertEqual(result["state"], "current")
        self.assertEqual(result["revision"], 44)
        self.assertEqual(result["recordsApplied"], 2)

        with self.connect() as conn:
            state = next_movievault_v2.sync_status(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cursor, revision FROM movievault_v2_sync_state WHERE plugin_id = %s",
                    (next_movievault_v2.MOVIEVAULT_V2_PLUGIN_ID,),
                )
                stored = cur.fetchone()
            exact = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": release["releaseId"], "limit": 1},
            )["results"][0]
        self.assertEqual(state["state"], "current")
        self.assertEqual(stored["cursor"], "cursor-value-hop-two-long-enough")
        self.assertEqual(stored["revision"], 44)
        self.assertEqual(exact["edition"], "Hop Two Cut")

    def test_v3_replaces_v2_cursor_then_applies_delta_atomically(self):
        settings = {"origin": "https://movievault.example"}
        v2_content = self.publisher_ordered_fixture()
        v2_digest = hashlib.sha256(v2_content).hexdigest()
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=self.manifest()),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    v2_content,
                    {
                        "x-content-sha256": v2_digest,
                        "x-next-cursor": "cursor-value-long-enough",
                    },
                ),
            ),
        ):
            next_movievault_v2.run_sync(self.connect, settings)

        v3_full = V3_FULL_PATH.read_bytes()
        v3_full_digest = hashlib.sha256(v3_full).digest()
        v3_full_cursor = "distribution-3-full-cursor"
        v3_full_manifest = {
            "contractVersion": "distribution-3",
            "currentRevision": 42,
            "currentCursor": v3_full_cursor,
            "bucketPrefixLength": 4,
            "hashAlgorithm": "sha256",
            "datasetChecksum": "c" * 64,
            "deltaPath": "/v3/index/delta",
            "bucketPathTemplate": "/v3/bucket/{prefix}",
        }
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=v3_full_manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    v3_full,
                    {
                        "x-content-sha256": v3_full_digest.hex(),
                        "content-digest": (
                            f"sha-256=:{base64.b64encode(v3_full_digest).decode('ascii')}:"
                        ),
                        "x-next-cursor": v3_full_cursor,
                    },
                ),
            ) as fetch_full,
        ):
            result = next_movievault_v2.run_sync(
                self.connect,
                settings,
                contract_version="distribution-3",
            )
        self.assertEqual(result["mode"], "full")
        self.assertIsNone(fetch_full.call_args.kwargs["cursor"])

        v3_delta = V3_DELTA_PATH.read_bytes()
        v3_delta_digest = hashlib.sha256(v3_delta).digest()
        v3_delta_cursor = "distribution-3-delta-cursor"
        v3_delta_manifest = {
            **v3_full_manifest,
            "currentRevision": 45,
            "currentCursor": v3_delta_cursor,
            "datasetChecksum": "d" * 64,
        }
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=v3_delta_manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    v3_delta,
                    {
                        "x-content-sha256": v3_delta_digest.hex(),
                        "content-digest": (
                            f"sha-256=:{base64.b64encode(v3_delta_digest).decode('ascii')}:"
                        ),
                        "x-next-cursor": v3_delta_cursor,
                    },
                ),
            ),
        ):
            result = next_movievault_v2.run_sync(
                self.connect,
                settings,
                contract_version="distribution-3",
            )
        self.assertEqual(result["mode"], "delta")

        with self.connect() as conn:
            release = next_movievault_v2.local_lookup(
                conn,
                {
                    "kind": "release",
                    "releaseId": "10000000-0000-0000-0000-000000000002",
                    "limit": 1,
                },
            )["results"][0]
            box_set = next_movievault_v2.local_lookup(
                conn,
                {
                    "kind": "box_set",
                    "boxSetId": "30000000-0000-0000-0000-000000000001",
                    "limit": 1,
                },
            )["results"][0]
            state = next_movievault_v2._sync_state(conn)
        self.assertEqual(state["contract_version"], "distribution-3")
        self.assertEqual(state["cursor"], v3_delta_cursor)
        self.assertEqual(release["studio"], "Example Studio")
        self.assertEqual(release["distributor"], "New Distributor")
        self.assertEqual(release["runtimeMinutes"], 128)
        self.assertEqual(
            [member["releaseEdition"] for member in box_set["members"]],
            ["Theatrical", "Director's Cut"],
        )

        failed_manifest = {
            **v3_delta_manifest,
            "currentRevision": 46,
            "currentCursor": "distribution-3-failed-cursor",
        }
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=failed_manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    b"invalid",
                    {
                        "x-content-sha256": hashlib.sha256(b"invalid").hexdigest(),
                        "content-digest": "sha-256=:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=:",
                    },
                ),
            ),
            self.assertRaisesRegex(next_movievault_v2.MovieVaultV2Error, "^checksum_invalid$"),
        ):
            next_movievault_v2.run_sync(
                self.connect,
                settings,
                contract_version="distribution-3",
            )
        with self.connect() as conn:
            state = next_movievault_v2._sync_state(conn)
        self.assertEqual(state["cursor"], v3_delta_cursor)

    def _seed_enabled_v2_plugin(self, settings=None):
        """Install movievault_v2 as enabled, optionally with a settings row."""
        manifest = {
            "id": "movievault_v2",
            "name": "MovieVault v2",
            "version": "1.0.0",
            "categories": ["metadata_source"],
            "capabilities": ["sync_index"],
        }
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO plugins (
                        id, name, version, enabled, installed, categories,
                        capabilities, manifest
                    )
                    VALUES (%s, %s, %s, true, true, %s, %s, %s)
                    """,
                    (
                        "movievault_v2",
                        "MovieVault v2",
                        "1.0.0",
                        Jsonb(["metadata_source"]),
                        Jsonb(["sync_index"]),
                        Jsonb(manifest),
                    ),
                )
                if settings is not None:
                    cur.execute(
                        """
                        INSERT INTO plugin_settings (plugin_id, settings)
                        VALUES (%s, %s)
                        """,
                        ("movievault_v2", Jsonb(settings)),
                    )

    def _queued_sync_jobs(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*) AS job_count, min(payload ->> 'source') AS source
                    FROM background_jobs
                    WHERE payload ->> 'pluginId' = %s
                      AND payload ->> 'entrypoint' = 'sync_index'
                    """,
                    ("movievault_v2",),
                )
                return cur.fetchone()

    def test_scheduler_enqueues_one_due_job(self):
        self._seed_enabled_v2_plugin(
            {"origin": "https://movievault.example", "syncIntervalHours": 6}
        )

        with patch.object(next_worker, "connect", side_effect=self.connect):
            next_worker._maybe_enqueue_movievault_v2_sync("postgres-test")
            next_worker._maybe_enqueue_movievault_v2_sync("postgres-test")

        row = self._queued_sync_jobs()
        self.assertEqual(row["job_count"], 1)
        self.assertEqual(row["source"], "scheduler")

    def test_scheduler_enqueues_without_a_stored_origin(self):
        """The origin is enforced by enforced_origin() and stripped from the
        settings schema, so it is absent on every install and says nothing about
        whether a sync can run. Gating on it left the index permanently empty."""
        self._seed_enabled_v2_plugin({"syncIntervalHours": 6})

        with patch.object(next_worker, "connect", side_effect=self.connect):
            next_worker._maybe_enqueue_movievault_v2_sync("postgres-test")

        self.assertEqual(self._queued_sync_jobs()["job_count"], 1)

    def test_scheduler_enqueues_without_any_plugin_settings_row(self):
        """A plugin whose config was never saved has no plugin_settings row at
        all; readiness is decided by installed + enabled, nothing more."""
        self._seed_enabled_v2_plugin(settings=None)

        with patch.object(next_worker, "connect", side_effect=self.connect):
            next_worker._maybe_enqueue_movievault_v2_sync("postgres-test")

        self.assertEqual(self._queued_sync_jobs()["job_count"], 1)

    def test_registry_materializes_defaults_without_overwriting_operator_values(self):
        settings_schema = {
            "settings": [
                {
                    "name": "origin",
                    "type": "url",
                    "required": True,
                    "default": "https://movies2.vaultstack.eu",
                },
                {
                    "name": "syncIntervalHours",
                    "type": "number",
                    "default": 6,
                    "minimum": 1,
                    "maximum": 168,
                },
                {
                    "name": "someToggle",
                    "type": "boolean",
                    "default": False,
                },
            ],
            "secrets": [],
        }
        manifest = {
            "id": "movievault_v2",
            "name": "MovieVault v2",
            "version": "1.0.4",
            "categories": ["metadata_source"],
            "capabilities": ["sync_index"],
            "orderIndex": 52,
            "requiresSecrets": False,
            "settingsSchema": settings_schema,
            "entitlements": {},
        }
        discovery = PluginDiscovery(
            manifest=manifest,
            path=Path("movievault_v2"),
            module_path=None,
            runtime={"loaded": True, "entrypoints": ["sync_index"], "error": None},
        )
        with (
            patch.object(
                next_plugin_runtime,
                "discover_plugins",
                return_value={"plugins": [discovery], "paths": [], "errors": []},
            ),
            self.connect() as conn,
        ):
            next_plugin_runtime.sync_plugin_registry(
                conn,
                self.table_exists,
                Jsonb,
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE plugin_settings
                    SET settings=settings || %s
                    WHERE plugin_id=%s
                    """,
                    (
                        Jsonb(
                            {
                                "origin": "https://custom.example",
                                "someToggle": True,
                            }
                        ),
                        "movievault_v2",
                    ),
                )

            updated_discovery = PluginDiscovery(
                manifest={
                    **manifest,
                    "settingsSchema": {
                        **settings_schema,
                        "settings": [
                            {
                                **field,
                                **({"default": 12} if field["name"] == "syncIntervalHours" else {}),
                            }
                            for field in settings_schema["settings"]
                        ],
                    },
                },
                path=Path("movievault_v2"),
                module_path=None,
                runtime=discovery.runtime,
            )
            with patch.object(
                next_plugin_runtime,
                "discover_plugins",
                return_value={
                    "plugins": [updated_discovery],
                    "paths": [],
                    "errors": [],
                },
            ):
                next_plugin_runtime.sync_plugin_registry(
                    conn,
                    self.table_exists,
                    Jsonb,
                )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT settings FROM plugin_settings WHERE plugin_id=%s",
                    ("movievault_v2",),
                )
                stored = cur.fetchone()["settings"]

        self.assertEqual(stored["origin"], "https://custom.example")
        self.assertEqual(stored["syncIntervalHours"], 6)
        self.assertTrue(stored["someToggle"])

    def test_initial_sync_job_is_duplicate_safe_and_skips_current_index(self):
        actor = {"id": None, "username": "owner", "role": "owner"}
        with patch.object(next_app, "audit_event"):
            with self.connect() as conn:
                with conn.transaction():
                    first, duplicate, skipped = next_app.queue_movievault_v2_sync_job(
                        conn,
                        actor=actor,
                        source="enable",
                        skip_when_indexed=True,
                    )
                with conn.transaction():
                    second, second_duplicate, second_skipped = next_app.queue_movievault_v2_sync_job(
                        conn,
                        actor=actor,
                        source="enable",
                        skip_when_indexed=True,
                    )

        self.assertIsNotNone(first)
        self.assertFalse(duplicate)
        self.assertFalse(skipped)
        self.assertEqual(second["id"], first["id"])
        self.assertTrue(second_duplicate)
        self.assertFalse(second_skipped)

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM background_jobs WHERE payload ->> 'pluginId'=%s",
                    ("movievault_v2",),
                )
                cur.execute(
                    """
                    INSERT INTO movievault_v2_sync_state (
                        plugin_id, origin, active_generation, status
                    )
                    VALUES (%s, %s, %s, 'current')
                    """,
                    (
                        "movievault_v2",
                        "https://movies2.vaultstack.eu",
                        "40000000-0000-0000-0000-000000000001",
                    ),
                )
            with patch.object(next_app, "audit_event"):
                with conn.transaction():
                    job, duplicate, skipped = next_app.queue_movievault_v2_sync_job(
                        conn,
                        actor=actor,
                        source="enable",
                        skip_when_indexed=True,
                    )

        self.assertIsNone(job)
        self.assertFalse(duplicate)
        self.assertTrue(skipped)

    def test_v4_full_sync_persists_poster_and_enqueues_pending_jobs_without_fetching(self):
        settings = {"origin": "https://movievault.example"}
        v4_full = V4_FULL_PATH.read_bytes()
        v4_full_digest = hashlib.sha256(v4_full).digest()
        manifest = _v4_manifest()
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    v4_full,
                    {
                        "x-content-sha256": v4_full_digest.hex(),
                        "content-digest": (
                            f"sha-256=:{base64.b64encode(v4_full_digest).decode('ascii')}:"
                        ),
                        "x-next-cursor": manifest["currentCursor"],
                    },
                ),
            ),
            patch.object(next_movievault_v2_posters, "_request") as fetch,
        ):
            result = next_movievault_v2.run_sync(
                self.connect,
                settings,
                contract_version="distribution-4",
            )
        self.assertEqual(result["mode"], "full")
        fetch.assert_not_called()  # index sync never itself fetches remote poster bytes

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT asset_id, variant, checksum, status FROM movievault_v2_poster_cache ORDER BY variant"
                )
                cache_rows = cur.fetchall()
                cur.execute(
                    "SELECT count(*) AS n FROM background_jobs WHERE job_type = %s",
                    (next_movievault_v2.POSTER_CACHE_JOB_TYPE,),
                )
                job_count = cur.fetchone()["n"]
            release_with_poster = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000001", "limit": 1},
            )["results"][0]
            release_without_poster = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000002", "limit": 1},
            )["results"][0]

        # Two posters (release + box_set) each carry a thumbnail + display
        # variant -> exactly 4 distinct cache identities, all still pending.
        self.assertEqual(len(cache_rows), 4)
        self.assertTrue(all(row["status"] == "pending" for row in cache_rows))
        self.assertEqual(job_count, 4)
        self.assertEqual(release_with_poster["posterStatus"], "pending")
        self.assertIsNone(release_with_poster["posterUrl"])
        # A record whose poster is required-but-null persists and reports as
        # unavailable, never as an error.
        self.assertEqual(release_without_poster["posterStatus"], "unavailable")
        self.assertIsNone(release_without_poster["posterUrl"])

    def _sync_full(self, path: Path, *, revision=42, cursor="fixture-distribution-4-r42"):
        settings = {"origin": "https://movievault.example"}
        content = path.read_bytes()
        digest = hashlib.sha256(content).digest()
        manifest = _v4_manifest(revision=revision, cursor=cursor)
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    content,
                    {
                        "x-content-sha256": digest.hex(),
                        "content-digest": f"sha-256=:{base64.b64encode(digest).decode('ascii')}:",
                        "x-next-cursor": manifest["currentCursor"],
                    },
                ),
            ),
        ):
            return next_movievault_v2.run_sync(self.connect, settings, contract_version="distribution-4")

    def _apply_delta_ndjson(self, records: list[dict], *, revision, cursor):
        settings = {"origin": "https://movievault.example"}
        content = ("\n".join(json.dumps(record) for record in records) + "\n").encode("utf-8")
        digest = hashlib.sha256(content).digest()
        manifest = _v4_manifest(revision=revision, cursor=cursor)
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    content,
                    {
                        "x-content-sha256": digest.hex(),
                        "content-digest": f"sha-256=:{base64.b64encode(digest).decode('ascii')}:",
                        "x-next-cursor": manifest["currentCursor"],
                    },
                ),
            ),
        ):
            return next_movievault_v2.run_sync(self.connect, settings, contract_version="distribution-4")

    def test_v4_full_sync_persists_audio_tracks_and_subtitles_matching_fixture(self):
        self._sync_full(V4_FULL_PATH)
        with self.connect() as conn:
            release = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000001", "limit": 1},
            )["results"][0]
        self.assertEqual(
            release["audioTracks"],
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
            release["subtitles"],
            [
                {"languageCode": "en", "subtitleType": "full"},
                {"languageCode": "en", "subtitleType": "sdh"},
                {"languageCode": "nl", "subtitleType": "full"},
            ],
        )

    def test_v4_full_sync_persists_packaging_matching_fixture(self):
        self._sync_full(V4_FULL_PATH)
        with self.connect() as conn:
            release_with = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000001", "limit": 1},
            )["results"][0]
            release_without = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000002", "limit": 1},
            )["results"][0]
        self.assertEqual(release_with["packaging"], ["steelbook", "slipcover"])
        self.assertEqual(release_without["packaging"], [])

    def test_v4_delta_with_different_packaging_replaces_the_value(self):
        self._sync_full(V4_FULL_PATH)
        base = json.loads(V4_DELTA_PATH.read_bytes().splitlines()[0])
        self.assertEqual(base["releaseId"], "10000000-0000-0000-0000-000000000001")
        base["revision"] = 44
        base["packaging"] = ["digipak"]

        self._apply_delta_ndjson([base], revision=44, cursor="fixture-distribution-4-r44")

        with self.connect() as conn:
            release = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000001", "limit": 1},
            )["results"][0]
        self.assertEqual(release["packaging"], ["digipak"])

    def test_v4_full_sync_persists_empty_tracks_for_a_release_without_them(self):
        self._sync_full(V4_FULL_PATH)
        with self.connect() as conn:
            release = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000002", "limit": 1},
            )["results"][0]
        self.assertEqual(release["audioTracks"], [])
        self.assertEqual(release["subtitles"], [])

    def test_v4_delta_with_unchanged_tracks_still_replaces_rows_cleanly(self):
        # Mirrors MovieVault-v2's own shipped fixture pair: the delta only
        # changes the poster, tracks/subtitles are carried forward unchanged.
        # Asserts the replace-then-reinsert in _upsert_release doesn't
        # duplicate rows when the incoming values happen to be identical.
        self._sync_full(V4_FULL_PATH)
        self._apply_delta_ndjson(
            [json.loads(line) for line in V4_DELTA_PATH.read_bytes().splitlines() if line.strip()],
            revision=44,
            cursor="fixture-distribution-4-r44",
        )
        with self.connect() as conn:
            release = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000001", "limit": 1},
            )["results"][0]
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM movievault_v2_release_audio_tracks"
                    " WHERE release_id = '10000000-0000-0000-0000-000000000001'"
                )
                audio_row_count = cur.fetchone()["n"]
        self.assertEqual(audio_row_count, 2)
        self.assertEqual(
            release["subtitles"],
            [
                {"languageCode": "en", "subtitleType": "full"},
                {"languageCode": "en", "subtitleType": "sdh"},
                {"languageCode": "nl", "subtitleType": "full"},
            ],
        )

    def test_v4_delta_with_different_tracks_replaces_not_appends(self):
        """The shipped MovieVault-v2 fixture only exercises 'poster changed,
        tracks unchanged' - this synthetic delta exercises an actual change
        to audioTracks/subtitles content on top of a prior full sync,
        asserting replace-not-append semantics end to end."""
        self._sync_full(V4_FULL_PATH)
        base = json.loads(V4_DELTA_PATH.read_bytes().splitlines()[0])
        self.assertEqual(base["releaseId"], "10000000-0000-0000-0000-000000000001")
        base["revision"] = 44
        base["audioTracks"] = [
            {
                "languageCode": "ja",
                "codec": "aac",
                "channels": "2.0",
                "immersiveFormat": None,
            }
        ]
        base["subtitles"] = [{"languageCode": "ja", "subtitleType": "forced"}]

        self._apply_delta_ndjson([base], revision=44, cursor="fixture-distribution-4-r44")

        with self.connect() as conn:
            release = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000001", "limit": 1},
            )["results"][0]
        self.assertEqual(
            release["audioTracks"],
            [{"languageCode": "ja", "codec": "aac", "channels": "2.0", "immersiveFormat": None}],
        )
        self.assertEqual(
            release["subtitles"],
            [{"languageCode": "ja", "subtitleType": "forced"}],
        )

    def test_v4_delta_with_empty_arrays_replaces_existing_tracks_with_nothing(self):
        """An empty array is meaningful (all tracks removed upstream) - the
        delete-then-reinsert must still run even when there is nothing to
        reinsert."""
        self._sync_full(V4_FULL_PATH)
        base = json.loads(V4_DELTA_PATH.read_bytes().splitlines()[0])
        base["revision"] = 44
        base["audioTracks"] = []
        base["subtitles"] = []

        self._apply_delta_ndjson([base], revision=44, cursor="fixture-distribution-4-r44")

        with self.connect() as conn:
            release = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000001", "limit": 1},
            )["results"][0]
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM movievault_v2_release_audio_tracks"
                    " WHERE release_id = '10000000-0000-0000-0000-000000000001'"
                )
                remaining = cur.fetchone()["n"]
        self.assertEqual(release["audioTracks"], [])
        self.assertEqual(release["subtitles"], [])
        self.assertEqual(remaining, 0)

    def test_v4_release_tombstone_cascades_to_audio_and_subtitle_tables(self):
        self._sync_full(V4_FULL_PATH)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM movievault_v2_release_audio_tracks"
                    " WHERE release_id = '10000000-0000-0000-0000-000000000001'"
                )
                self.assertEqual(cur.fetchone()["n"], 2)

        tombstone = {
            "contractVersion": "distribution-4",
            "recordType": "release",
            "operation": "delete",
            "revision": 44,
            "entityId": "10000000-0000-0000-0000-000000000001",
        }
        self._apply_delta_ndjson([tombstone], revision=44, cursor="fixture-distribution-4-r44")

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM movievault_v2_release_audio_tracks"
                    " WHERE release_id = '10000000-0000-0000-0000-000000000001'"
                )
                audio_count = cur.fetchone()["n"]
                cur.execute(
                    "SELECT count(*) AS n FROM movievault_v2_release_subtitle_languages"
                    " WHERE release_id = '10000000-0000-0000-0000-000000000001'"
                )
                subtitle_count = cur.fetchone()["n"]
        self.assertEqual(audio_count, 0)
        self.assertEqual(subtitle_count, 0)

    def test_local_lookup_never_triggers_a_remote_poster_fetch(self):
        settings = {"origin": "https://movievault.example"}
        v4_full = V4_FULL_PATH.read_bytes()
        v4_full_digest = hashlib.sha256(v4_full).digest()
        manifest = _v4_manifest()
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    v4_full,
                    {
                        "x-content-sha256": v4_full_digest.hex(),
                        "content-digest": (
                            f"sha-256=:{base64.b64encode(v4_full_digest).decode('ascii')}:"
                        ),
                        "x-next-cursor": manifest["currentCursor"],
                    },
                ),
            ),
        ):
            next_movievault_v2.run_sync(self.connect, settings, contract_version="distribution-4")

        with patch.object(next_movievault_v2_posters, "_request") as fetch:
            with self.connect() as conn:
                for _ in range(3):
                    next_movievault_v2.local_lookup(
                        conn,
                        {
                            "kind": "release",
                            "releaseId": "10000000-0000-0000-0000-000000000001",
                            "limit": 1,
                        },
                    )
        fetch.assert_not_called()

    def test_poster_cache_job_activates_media_asset_and_local_lookup_reports_local_url(self):
        content = _png_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as data_dir:
            with patch.dict(os.environ, {"DISCVAULT_LEGACY_DATA_DIR": data_dir}):
                with self.connect() as conn:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO movievault_v2_poster_cache (asset_id, variant, checksum, status)
                                VALUES (%s, 'display', %s, 'pending')
                                RETURNING id
                                """,
                                ("poster-asset-live", checksum),
                            )
                            cache_id = cur.fetchone()["id"]
                    with patch.object(
                        next_movievault_v2_posters,
                        "_request",
                        return_value=(200, content, {"content-type": "image/png"}),
                    ):
                        outcome = next_movievault_v2_posters.run_poster_cache_job(
                            conn,
                            {
                                "cacheId": str(cache_id),
                                "assetId": "poster-asset-live",
                                "variant": "display",
                                "checksum": checksum,
                                "origin": "https://movievault.example",
                                "path": "/v2/assets/poster-asset-live/display",
                            },
                        )
                    self.assertEqual(outcome["outcome"], "ready")
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT status, media_asset_id FROM movievault_v2_poster_cache WHERE id = %s",
                            (cache_id,),
                        )
                        row = cur.fetchone()
                self.assertEqual(row["status"], "ready")
                self.assertIsNotNone(row["media_asset_id"])

                # A release whose poster references this exact (assetId, checksum)
                # must now resolve to the authenticated local media-asset URL.
                with self.connect() as conn:
                    fields = next_movievault_v2._poster_status_fields(
                        conn,
                        {
                            "assetId": "poster-asset-live",
                            "display": {"checksum": checksum, "path": "/v2/assets/poster-asset-live/display"},
                        },
                    )
                self.assertEqual(fields["posterStatus"], "ready")
                self.assertEqual(
                    fields["posterUrl"],
                    f"/api/next/movievault-v2/posters/{row['media_asset_id']}",
                )
                self.assertNotIn("movievault.example", fields["posterUrl"])

    def test_poster_cache_job_failure_preserves_prior_ready_media_asset(self):
        content = _png_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        with self.connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO media_assets (
                            kind, variant, storage_backend, storage_key, provider_id,
                            content_type, width, height, size_bytes, sha256
                        )
                        VALUES ('poster', 'display', 'local', 'media/existing.png',
                                'movievault_v2:poster-asset-prior', 'image/png', 10, 10, 123, %s)
                        RETURNING id
                        """,
                        ("a" * 64,),
                    )
                    prior_media_asset_id = cur.fetchone()["id"]
                    cur.execute(
                        """
                        INSERT INTO movievault_v2_poster_cache (
                            asset_id, variant, checksum, status, media_asset_id
                        )
                        VALUES (%s, 'display', %s, 'ready', %s)
                        RETURNING id
                        """,
                        ("poster-asset-prior", "a" * 64, prior_media_asset_id),
                    )
                    cache_id = cur.fetchone()["id"]

            # The job's declared checksum intentionally does not match the
            # fetched bytes - a corrupted/interrupted remote replacement.
            wrong_checksum = ("0" if checksum[0] != "0" else "1") + checksum[1:]
            with patch.object(
                next_movievault_v2_posters,
                "_request",
                return_value=(200, content, {"content-type": "image/png"}),
            ):
                outcome = next_movievault_v2_posters.run_poster_cache_job(
                    conn,
                    {
                        "cacheId": str(cache_id),
                        "assetId": "poster-asset-prior",
                        "variant": "display",
                        "checksum": wrong_checksum,
                        "origin": "https://movievault.example",
                        "path": "/v2/assets/poster-asset-prior/display",
                    },
                )
            self.assertEqual(outcome["outcome"], "failed")
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, media_asset_id, attempts FROM movievault_v2_poster_cache WHERE id = %s",
                    (cache_id,),
                )
                row = cur.fetchone()
        # The row being retried is untouched by this new-checksum job payload
        # (a distinct cache identity would be created for it); this asserts
        # the mechanism that DOES apply when the same row is retried after a
        # transient failure: media_asset_id is never overwritten on failure.
        self.assertEqual(row["media_asset_id"], prior_media_asset_id)
        self.assertEqual(row["attempts"], 1)

    def test_poster_cleanup_removes_only_unreferenced_stale_cache_rows_and_files(self):
        content = _png_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as data_dir:
            with patch.dict(os.environ, {"DISCVAULT_LEGACY_DATA_DIR": data_dir}):
                with self.connect() as conn:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO movievault_v2_poster_cache (asset_id, variant, checksum, status)
                                VALUES (%s, 'display', %s, 'pending')
                                RETURNING id
                                """,
                                ("poster-orphan", checksum),
                            )
                            cache_id = cur.fetchone()["id"]
                    with patch.object(
                        next_movievault_v2_posters,
                        "_request",
                        return_value=(200, content, {"content-type": "image/png"}),
                    ):
                        next_movievault_v2_posters.run_poster_cache_job(
                            conn,
                            {
                                "cacheId": str(cache_id),
                                "assetId": "poster-orphan",
                                "variant": "display",
                                "checksum": checksum,
                                "origin": "https://movievault.example",
                                "path": "/v2/assets/poster-orphan/display",
                            },
                        )
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT media_asset_id FROM movievault_v2_poster_cache WHERE id = %s",
                            (cache_id,),
                        )
                        media_asset_id = cur.fetchone()["media_asset_id"]
                        cur.execute(
                            "SELECT storage_key FROM media_assets WHERE id = %s",
                            (media_asset_id,),
                        )
                        storage_key = cur.fetchone()["storage_key"]
                        stored_file = Path(data_dir) / storage_key
                        self.assertTrue(stored_file.exists())
                        # Backdate past the retention window - this cache row
                        # is not referenced by any release/box_set poster.
                        cur.execute(
                            "UPDATE movievault_v2_poster_cache SET checked_at = now() - interval '30 days' WHERE id = %s",
                            (cache_id,),
                        )
                    with conn.transaction():
                        cleanup_result = next_movievault_v2_posters.run_poster_cleanup(
                            conn, retention_seconds=604800
                        )
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT 1 FROM movievault_v2_poster_cache WHERE id = %s", (cache_id,)
                        )
                        cache_gone = cur.fetchone() is None
                        cur.execute(
                            "SELECT 1 FROM media_assets WHERE id = %s", (media_asset_id,)
                        )
                        media_asset_gone = cur.fetchone() is None
        self.assertGreaterEqual(cleanup_result["removed"], 1)
        self.assertTrue(cache_gone)
        self.assertTrue(media_asset_gone)
        self.assertFalse(stored_file.exists())

    def test_poster_cleanup_keeps_rows_still_referenced_by_a_current_poster(self):
        settings = {"origin": "https://movievault.example"}
        v4_full = V4_FULL_PATH.read_bytes()
        v4_full_digest = hashlib.sha256(v4_full).digest()
        manifest = _v4_manifest()
        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=manifest),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    v4_full,
                    {
                        "x-content-sha256": v4_full_digest.hex(),
                        "content-digest": (
                            f"sha-256=:{base64.b64encode(v4_full_digest).decode('ascii')}:"
                        ),
                        "x-next-cursor": manifest["currentCursor"],
                    },
                ),
            ),
        ):
            next_movievault_v2.run_sync(self.connect, settings, contract_version="distribution-4")

        with self.connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # Every freshly-enqueued cache row is still referenced by
                    # the release/box_set poster jsonb that produced it, no
                    # matter how far checked_at is backdated.
                    cur.execute(
                        "UPDATE movievault_v2_poster_cache SET checked_at = now() - interval '30 days'"
                    )
            with conn.transaction():
                cleanup_result = next_movievault_v2_posters.run_poster_cleanup(
                    conn, retention_seconds=604800
                )
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) AS n FROM movievault_v2_poster_cache")
                remaining = cur.fetchone()["n"]
        self.assertEqual(cleanup_result["removed"], 0)
        self.assertEqual(remaining, 4)

    def test_movievault_poster_route_requires_auth_and_rejects_unlinked_generic_media(self):
        content = _png_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        public_content = _png_bytes(size=(11, 11))
        public_checksum = hashlib.sha256(public_content).hexdigest()
        with tempfile.TemporaryDirectory() as data_dir:
            with patch.dict(os.environ, {"DISCVAULT_LEGACY_DATA_DIR": data_dir}):
                public_storage_key = "media/public/poster.png"
                public_path = Path(data_dir) / public_storage_key
                public_path.parent.mkdir(parents=True, exist_ok=True)
                public_path.write_bytes(public_content)
                with self.connect() as conn:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO movievault_v2_poster_cache (asset_id, variant, checksum, status)
                                VALUES (%s, 'display', %s, 'pending')
                                RETURNING id
                                """,
                                ("poster-asset-http", checksum),
                            )
                            cache_id = cur.fetchone()["id"]
                            cur.execute(
                                """
                                INSERT INTO media_assets (
                                    kind, variant, storage_backend, storage_key, provider_id,
                                    content_type, width, height, size_bytes, sha256
                                )
                                VALUES (
                                    'poster', 'display', 'local', %s, 'test:movievault-v2-public-media',
                                    'image/png', 11, 11, %s, %s
                                )
                                RETURNING id
                                """,
                                (public_storage_key, len(public_content), public_checksum),
                            )
                            public_media_asset_id = cur.fetchone()["id"]
                            forged_user_id = uuid.uuid4()
                            cur.execute(
                                """
                                INSERT INTO users (id, username, display_name)
                                VALUES (%s, %s, %s)
                                """,
                                (
                                    forged_user_id,
                                    f"media-forgery-{forged_user_id.hex}",
                                    "Media forgery test",
                                ),
                            )
                            cur.execute(
                                """
                                INSERT INTO wishlist_items (user_id, title, poster_url, snapshot)
                                VALUES (%s, %s, %s, %s)
                                RETURNING id
                                """,
                                (
                                    forged_user_id,
                                    "Forged poster reference",
                                    f"/api/next/media/assets/{public_media_asset_id}",
                                    Jsonb({}),
                                ),
                            )
                            forged_wishlist_item_id = cur.fetchone()["id"]

                    def cleanup_forged_wishlist_user():
                        with self.connect() as cleanup_conn:
                            with cleanup_conn.transaction():
                                with cleanup_conn.cursor() as cur:
                                    cur.execute("DELETE FROM users WHERE id=%s", (forged_user_id,))

                    self.addCleanup(cleanup_forged_wishlist_user)
                    authenticated_actor = {"id": str(forged_user_id)}
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM users WHERE id=%s", (authenticated_actor["id"],))
                        self.assertEqual(cur.fetchone()["id"], forged_user_id)
                    with patch.object(
                        next_movievault_v2_posters,
                        "_request",
                        return_value=(200, content, {"content-type": "image/png"}),
                    ):
                        outcome = next_movievault_v2_posters.run_poster_cache_job(
                            conn,
                            {
                                "cacheId": str(cache_id),
                                "assetId": "poster-asset-http",
                                "variant": "display",
                                "checksum": checksum,
                                "origin": "https://movievault.example",
                                "path": "/v2/assets/poster-asset-http/display",
                            },
                        )
                    self.assertEqual(outcome["outcome"], "ready")
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT media_asset_id FROM movievault_v2_poster_cache WHERE id = %s",
                            (cache_id,),
                        )
                        media_asset_id = cur.fetchone()["media_asset_id"]

                app = next_app.create_app()
                client = app.test_client()
                protected_path = f"/api/next/movievault-v2/posters/{media_asset_id}"
                self.assertFalse(next_app.is_public_next_path(protected_path))
                self.assertFalse(next_app.is_public_next_path(f"/api/next/media/assets/{media_asset_id}"))

                with (
                    patch.object(next_app, "next_auth_effective_enabled", return_value=True),
                    patch.object(next_app, "next_auth_current_user", return_value=None),
                ):
                    anonymous = client.get(protected_path)
                self.assertEqual(anonymous.status_code, 401)

                with (
                    patch.object(next_app, "next_auth_effective_enabled", return_value=True),
                    patch.object(
                        next_app,
                        "next_auth_current_user",
                        return_value=authenticated_actor,
                    ),
                ):
                    response = client.get(protected_path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, content)
                self.assertIn("ETag", response.headers)
                self.assertIn("private", response.headers.get("Cache-Control", ""))
                self.assertIn("no-store", response.headers.get("Cache-Control", ""))
                self.assertNotIn("public", response.headers.get("Cache-Control", ""))
                # The authenticated local route never leaks the MovieVault origin,
                # any contribution token, or a raw remote asset path to the client.
                header_blob = " ".join(f"{k}:{v}" for k, v in response.headers.items())
                self.assertNotIn("movievault", header_blob.lower())
                self.assertNotIn("poster-asset-http", header_blob)
                response.close()

                with self.connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE movievault_v2_poster_cache
                            SET status = 'degraded'
                            WHERE id = %s
                            """,
                            (cache_id,),
                        )
                with (
                    patch.object(next_app, "next_auth_effective_enabled", return_value=True),
                    patch.object(
                        next_app,
                        "next_auth_current_user",
                        return_value=authenticated_actor,
                    ),
                ):
                    degraded_fallback = client.get(protected_path)
                self.assertEqual(degraded_fallback.status_code, 200)
                self.assertEqual(degraded_fallback.data, content)
                degraded_fallback.close()

                with (
                    patch.object(next_app, "next_auth_effective_enabled", return_value=True),
                    patch.object(
                        next_app,
                        "next_auth_current_user",
                        return_value=authenticated_actor,
                    ),
                ):
                    public_bypass = client.get(f"/api/next/media/assets/{media_asset_id}")
                self.assertEqual(public_bypass.status_code, 404)

                with (
                    patch.object(next_app, "next_auth_effective_enabled", return_value=True),
                    patch.object(
                        next_app,
                        "next_auth_current_user",
                        return_value=authenticated_actor,
                    ),
                ):
                    protected_non_movievault = client.get(
                        f"/api/next/movievault-v2/posters/{public_media_asset_id}"
                    )
                self.assertEqual(protected_non_movievault.status_code, 404)

                with (
                    patch.object(next_app, "next_auth_effective_enabled", return_value=True),
                    patch.object(
                        next_app,
                        "next_auth_current_user",
                        return_value=authenticated_actor,
                    ),
                ):
                    unlinked_response = client.get(f"/api/next/media/assets/{public_media_asset_id}")
                self.assertEqual(unlinked_response.status_code, 404)

                wishlist_actor = {
                    "id": str(forged_user_id),
                    "permissions": ["watchlist.manage"],
                }
                with (
                    patch.object(next_app, "next_auth_effective_enabled", return_value=True),
                    patch.object(next_app, "next_auth_current_user", return_value=wishlist_actor),
                ):
                    forged_wishlist_response = client.get(
                        f"/api/next/media/assets/{public_media_asset_id}"
                    )
                self.assertEqual(forged_wishlist_response.status_code, 404)

                with self.connect() as conn:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE wishlist_items
                                SET snapshot=%s
                                WHERE id=%s AND user_id=%s
                                """,
                                (
                                    Jsonb(
                                        {
                                            next_app.WISHLIST_POSTER_ASSET_SNAPSHOT_KEY: str(
                                                public_media_asset_id
                                            )
                                        }
                                    ),
                                    forged_wishlist_item_id,
                                    forged_user_id,
                                ),
                            )
                with (
                    patch.object(next_app, "next_auth_effective_enabled", return_value=True),
                    patch.object(next_app, "next_auth_current_user", return_value=wishlist_actor),
                ):
                    trusted_wishlist_response = client.get(
                        f"/api/next/media/assets/{public_media_asset_id}"
                    )
                self.assertEqual(trusted_wishlist_response.status_code, 200)
                self.assertEqual(trusted_wishlist_response.data, public_content)
                trusted_wishlist_response.close()

                with self.connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM media_assets WHERE id = %s", (public_media_asset_id,))

    def test_v4_full_sync_persists_the_video_profile(self):
        self._sync_full(V4_FULL_PATH)
        with self.connect() as conn:
            release = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000001", "limit": 1},
            )["results"][0]
        self.assertEqual(release["videoResolution"], "2160p")
        self.assertEqual(release["videoCodecs"], ["hevc"])
        self.assertEqual(release["hdrFormats"], ["dolby_vision", "hdr10"])
        self.assertEqual(release["aspectRatios"], ["2.39:1"])
        self.assertEqual(release["discRegions"], ["B"])

    def test_v4_full_sync_persists_an_absent_video_profile_as_null_and_empty(self):
        self._sync_full(V4_FULL_PATH)
        with self.connect() as conn:
            release = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000002", "limit": 1},
            )["results"][0]
        self.assertIsNone(release["videoResolution"])
        self.assertEqual(release["videoCodecs"], [])
        self.assertEqual(release["hdrFormats"], [])
        self.assertEqual(release["aspectRatios"], [])
        self.assertEqual(release["discRegions"], [])

    def test_v4_delta_replaces_the_video_profile_rather_than_merging_it(self):
        self._sync_full(V4_FULL_PATH)
        base = self._fixture_release("10000000-0000-0000-0000-000000000001")
        base["revision"] = 43
        base["videoResolution"] = "1080p"
        base["videoCodecs"] = ["h264"]
        base["hdrFormats"] = []
        base["aspectRatios"] = ["16:9"]
        base["discRegions"] = ["A", "B"]
        self._apply_delta_ndjson([base], revision=43, cursor="fixture-distribution-4-r43")

        with self.connect() as conn:
            release = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000001", "limit": 1},
            )["results"][0]
        self.assertEqual(release["videoResolution"], "1080p")
        self.assertEqual(release["videoCodecs"], ["h264"])
        # Emptied, not left at its previous ["dolby_vision", "hdr10"].
        self.assertEqual(release["hdrFormats"], [])
        self.assertEqual(release["aspectRatios"], ["16:9"])
        self.assertEqual(release["discRegions"], ["A", "B"])

    def test_v4_sync_keeps_two_variants_of_the_same_subtitle_language_as_separate_rows(self):
        """The mirror's primary key is (generation, release_id, position), so repeating
        a language was always representable - only the variant column was missing."""
        self._sync_full(V4_FULL_PATH)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT language_code, subtitle_type"
                    " FROM movievault_v2_release_subtitle_languages"
                    " WHERE release_id = '10000000-0000-0000-0000-000000000001'"
                    " ORDER BY position"
                )
                rows = [(row["language_code"], row["subtitle_type"]) for row in cur.fetchall()]
        self.assertEqual(rows, [("en", "full"), ("en", "sdh"), ("nl", "full")])

    def test_v4_delta_replaces_subtitle_variants_rather_than_appending(self):
        self._sync_full(V4_FULL_PATH)
        base = self._fixture_release("10000000-0000-0000-0000-000000000001")
        base["revision"] = 43
        base["subtitles"] = [{"languageCode": "de", "subtitleType": "closed_caption"}]
        self._apply_delta_ndjson([base], revision=43, cursor="fixture-distribution-4-r43")

        with self.connect() as conn:
            release = next_movievault_v2.local_lookup(
                conn,
                {"kind": "release", "releaseId": "10000000-0000-0000-0000-000000000001", "limit": 1},
            )["results"][0]
        self.assertEqual(
            release["subtitles"], [{"languageCode": "de", "subtitleType": "closed_caption"}]
        )

    def _fixture_release(self, release_id: str) -> dict:
        for line in V4_FULL_PATH.read_bytes().splitlines():
            record = json.loads(line)
            if record.get("recordType") == "release" and record.get("releaseId") == release_id:
                return record
        raise AssertionError(f"fixture release {release_id} not found")


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured"
)
class SeriesLinkPostgresTests(unittest.TestCase):
    """Linking a disc to the series a feed named, against a real database.

    Every claim here is about SQL and schema constraints, which is exactly what a
    stub cannot be trusted for. Migration 063 makes the ordering structural --
    `movies_series_requires_show`, and a composite foreign key that demands the
    disc already carry the series before a season may reference it -- so a test
    with a fake cursor would happily accept writes the database refuses.
    """

    SERIES_TITLE = "Series Link Test Show"

    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def _clear(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM movies WHERE title LIKE %s", (f"{self.SERIES_TITLE}%",)
            )
            # Before the series rows, because the job payload names them and the
            # id is the only way to tell this suite's jobs from anyone else's.
            #
            # Linking a series queues a description refresh, so these tests put
            # real work on a shared queue. Leaving it behind is not a tidiness
            # problem: `next_worker run-once` processes exactly one job, and the
            # CI smoke that follows asserts the job it then finds is its own. An
            # orphan from here makes that unrelated step fail, pointing at
            # neither the test nor the smoke.
            cur.execute(
                """
                DELETE FROM background_jobs
                WHERE job_type = %s
                  AND payload ->> 'seriesId' IN (
                      SELECT id::text FROM series WHERE title = %s
                  )
                """,
                (next_metadata.SERIES_METADATA_REFRESH_JOB_TYPE, self.SERIES_TITLE),
            )
            cur.execute("DELETE FROM series WHERE title = %s", (self.SERIES_TITLE,))

    def setUp(self):
        self._clear()

    def tearDown(self):
        self._clear()

    def _movie(self, conn, *, media_type: str = "SHOW", suffix: str = "") -> uuid.UUID:
        movie_id = uuid.uuid4()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, media_type)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    movie_id,
                    f"next-movie-{movie_id.hex[:12]}",
                    f"{self.SERIES_TITLE}{suffix or ' Disc'}",
                    media_type,
                ),
            )
        return movie_id

    def _stated(self, *, seasons: list[dict] | None = None) -> dict:
        return {
            "provider": next_movievault_v2.MOVIEVAULT_V2_PLUGIN_ID,
            "tmdbTvId": "1399",
            "title": self.SERIES_TITLE,
            "seasons": seasons
            if seasons is not None
            else [
                {"seasonNumber": 1, "title": "Season One", "episodeCount": 10},
                {"seasonNumber": 2},
            ],
        }

    def _links(self, conn, movie_id) -> list[dict]:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ms.series_id, ms.sort_order, ss.season_number
                FROM movie_seasons ms
                JOIN series_seasons ss ON ss.id = ms.season_id
                WHERE ms.movie_id = %s
                ORDER BY ms.sort_order
                """,
                (movie_id,),
            )
            return list(cur.fetchall())

    def test_a_series_is_created_once_and_reused_by_identifier(self):
        """The claim the whole design rests on.

        A second release of the same show must land on the same series. If this
        resolved by title -- or resolved twice -- a collector with three
        pressings of one show would end up with three series, and no screen would
        make that obviously wrong.
        """
        with self.connect() as conn:
            first = self._movie(conn, suffix=" Blu-ray")
            second = self._movie(conn, suffix=" 4K")
            a = next_metadata.apply_movie_series_link(conn, first, self._stated())
            b = next_metadata.apply_movie_series_link(conn, second, self._stated())

            self.assertEqual(a["seriesId"], b["seriesId"])
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM series WHERE title = %s", (self.SERIES_TITLE,)
                )
                self.assertEqual(cur.fetchone()["n"], 1)
                cur.execute(
                    """
                    SELECT count(*) AS n FROM series_identifiers
                    WHERE identifier_type = 'tmdb_tv' AND identifier = '1399'
                    """
                )
                self.assertEqual(cur.fetchone()["n"], 1)

    def test_season_links_carry_the_series_and_the_curators_order(self):
        with self.connect() as conn:
            movie_id = self._movie(conn)
            applied = next_metadata.apply_movie_series_link(conn, movie_id, self._stated())
            links = self._links(conn, movie_id)

        self.assertEqual([row["season_number"] for row in links], [1, 2])
        self.assertEqual([row["sort_order"] for row in links], [0, 1])
        # The composite foreign key would have refused a mismatch outright, so
        # asserting it is asserting that the write actually happened as claimed.
        self.assertEqual(
            {str(row["series_id"]) for row in links}, {applied["seriesId"]}
        )

    def test_a_series_with_no_stated_seasons_links_nothing(self):
        """A show nobody has curated seasons for is still worth linking, and the
        absence of seasons must not become an invented season."""
        with self.connect() as conn:
            movie_id = self._movie(conn)
            applied = next_metadata.apply_movie_series_link(
                conn, movie_id, self._stated(seasons=[])
            )
            self.assertIsNotNone(applied)
            self.assertEqual(applied["seasonCount"], 0)
            self.assertEqual(self._links(conn, movie_id), [])

    def test_a_film_is_never_linked(self):
        """`movies_series_requires_show` forbids it, so a link here would be a
        constraint violation rather than a wrong-but-valid row. This is also what
        makes a locked type protect itself: the type never becomes SHOW, so the
        link never happens, with no second rule to keep in step."""
        with self.connect() as conn:
            movie_id = self._movie(conn, media_type="MOVIE")
            self.assertIsNone(
                next_metadata.apply_movie_series_link(conn, movie_id, self._stated())
            )
            with conn.cursor() as cur:
                cur.execute("SELECT series_id FROM movies WHERE id = %s", (movie_id,))
                self.assertIsNone(cur.fetchone()["series_id"])

    def test_silence_never_unlinks(self):
        """A provider with no opinion has not disagreed with a link a user made."""
        with self.connect() as conn:
            movie_id = self._movie(conn)
            applied = next_metadata.apply_movie_series_link(conn, movie_id, self._stated())
            self.assertIsNotNone(applied)
            self.assertIsNone(next_metadata.apply_movie_series_link(conn, movie_id, None))
            with conn.cursor() as cur:
                cur.execute("SELECT series_id FROM movies WHERE id = %s", (movie_id,))
                self.assertEqual(str(cur.fetchone()["series_id"]), applied["seriesId"])
            self.assertEqual(len(self._links(conn, movie_id)), 2)

    def test_a_disc_already_on_another_series_is_left_alone(self):
        """Re-pointing a disc is a decision, not a refresh."""
        with self.connect() as conn:
            movie_id = self._movie(conn)
            other = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO series (id, public_id, title) VALUES (%s, %s, %s)",
                    (other, f"next-series-{other.hex[:12]}", self.SERIES_TITLE),
                )
                cur.execute(
                    "UPDATE movies SET series_id = %s WHERE id = %s", (other, movie_id)
                )
            self.assertIsNone(
                next_metadata.apply_movie_series_link(conn, movie_id, self._stated())
            )
            with conn.cursor() as cur:
                cur.execute("SELECT series_id FROM movies WHERE id = %s", (movie_id,))
                self.assertEqual(cur.fetchone()["series_id"], other)

    def test_a_soft_deleted_series_is_not_resurrected(self):
        """A deletion is a statement. Reviving it because a feed mentioned the
        show again would overrule whoever deleted it, with no trace."""
        with self.connect() as conn:
            first = self._movie(conn, suffix=" Blu-ray")
            applied = next_metadata.apply_movie_series_link(conn, first, self._stated())
            with conn.cursor() as cur:
                # Links first, then the disc's series_id. The composite key
                # points at (movies.id, movies.series_id), so clearing the disc
                # while a link still cites it is a foreign key violation -- the
                # same ordering the edit API comments on, met here by writing
                # the test the wrong way round once.
                cur.execute("DELETE FROM movie_seasons WHERE movie_id = %s", (first,))
                cur.execute(
                    "UPDATE movies SET series_id = NULL WHERE id = %s", (first,)
                )
                cur.execute(
                    "UPDATE series SET deleted_at = now() WHERE id = %s",
                    (applied["seriesId"],),
                )
            second = self._movie(conn, suffix=" 4K")
            again = next_metadata.apply_movie_series_link(conn, second, self._stated())

        self.assertIsNotNone(again)
        self.assertNotEqual(again["seriesId"], applied["seriesId"])


if __name__ == "__main__":
    unittest.main()
