from __future__ import annotations

import copy
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
from app.backend import next_plugin_runtime
from app.backend import next_worker
from app.backend.next_plugin_runtime import PluginDiscovery


DATABASE_URL = os.environ.get("DATABASE_URL")
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "distribution-v2.ndjson"


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
                        movievault_v2_sync_state
                    CASCADE
                    """
                )
                cur.execute(
                    """
                    DELETE FROM background_jobs
                    WHERE payload ->> 'pluginId' = %s
                    """,
                    (next_movievault_v2.MOVIEVAULT_V2_PLUGIN_ID,),
                )
                cur.execute(
                    "DELETE FROM plugins WHERE id = %s",
                    (next_movievault_v2.MOVIEVAULT_V2_PLUGIN_ID,),
                )

    def setUp(self):
        self._clear_state()

    def tearDown(self):
        self._clear_state()

    def fixture(self) -> bytes:
        return FIXTURE_PATH.read_bytes()

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
            "datasetChecksum": checksum or hashlib.sha256(self.fixture()).hexdigest(),
            "deltaPath": "/v2/index/delta",
            "bucketPathTemplate": "/v2/bucket/{prefix}",
        }

    def test_full_delta_current_tombstone_and_failed_digest_are_atomic(self):
        fixture = self.fixture()
        fixture_digest = hashlib.sha256(fixture).hexdigest()
        settings = {"origin": "https://movievault.example"}

        with (
            patch.object(next_movievault_v2, "fetch_manifest", return_value=self.manifest()),
            patch.object(
                next_movievault_v2,
                "_fetch_feed",
                return_value=(
                    200,
                    fixture,
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
        fixture = self.fixture()
        digest = hashlib.sha256(fixture).hexdigest()
        settings = {"origin": "https://movievault.example"}
        first_manifest = self.manifest()
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


if __name__ == "__main__":
    unittest.main()
