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

    def test_scheduler_enqueues_one_due_job(self):
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
                cur.execute(
                    """
                    INSERT INTO plugin_settings (plugin_id, settings)
                    VALUES (%s, %s)
                    """,
                    (
                        "movievault_v2",
                        Jsonb(
                            {
                                "origin": "https://movievault.example",
                                "syncIntervalHours": 6,
                            }
                        ),
                    ),
                )

        with patch.object(next_worker, "connect", side_effect=self.connect):
            next_worker._maybe_enqueue_movievault_v2_sync("postgres-test")
            next_worker._maybe_enqueue_movievault_v2_sync("postgres-test")

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
                row = cur.fetchone()
        self.assertEqual(row["job_count"], 1)
        self.assertEqual(row["source"], "scheduler")

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
                    "name": "bucketFallback",
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
                                "bucketFallback": True,
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
        self.assertTrue(stored["bucketFallback"])

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
                        return_value={"id": "authenticated-user"},
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
                        return_value={"id": "authenticated-user"},
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
                        return_value={"id": "authenticated-user"},
                    ),
                ):
                    public_bypass = client.get(f"/api/next/media/assets/{media_asset_id}")
                self.assertEqual(public_bypass.status_code, 404)

                with (
                    patch.object(next_app, "next_auth_effective_enabled", return_value=True),
                    patch.object(
                        next_app,
                        "next_auth_current_user",
                        return_value={"id": "authenticated-user"},
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
                        return_value={"id": "authenticated-user"},
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


if __name__ == "__main__":
    unittest.main()
