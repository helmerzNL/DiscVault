"""Listing the stored backups must not re-read every archive.

`GET /api/next/backup/status` is fetched on **every** load of the admin panel,
not only when the backup tab is open -- it sits in the same parallel batch as
the other admin requests (`next_views_ui.py`, `appAdmin` bootstrap). It called
`list_backup_archives`, which per archive opened the ZIP, parsed every backup
table out of it into memory, validated the relationships between those tables,
and then hashed the whole file a second time.

Measured on ten archives of a 2,000-title collection (1.26 GB total), warm page
cache, before this change: **2.31 s per request**, split almost evenly between
`validate_backup_zip` (0.128 s per archive) and `sha256_file` (0.112 s per
archive). The published issue names only the hash; the validation is the other
half and was not mentioned at all. Both go away for the same reason -- neither
answer can change while the file does not.

With two Gunicorn workers, that is one of the two occupied for the whole
duration, while the thirteen sibling admin requests contend for the other. After
this change the same call is **0.001 s**, and the response is byte-identical.

The cache is keyed on the archive's size and modification time, so an archive
replaced or edited on the host reads as a miss. What it cannot see is an edit
that preserves both; `?refresh=1` exists for exactly that, and the last test
here pins that limit rather than pretending it away.
"""

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_backup
from app.backend.next_backup import BACKUP_FORMAT
from app.backend.next_backup import BACKUP_FORMAT_VERSION
from app.backend.next_backup import BACKUP_SUMMARY_CACHE_VERSION
from app.backend.next_backup import BACKUP_SUMMARY_DIR_NAME
from app.backend.next_backup import BACKUP_TABLES
from app.backend.next_backup import backup_summary_cache_path
from app.backend.next_backup import list_backup_archives
from app.backend.next_backup import sha256_file
from app.backend.next_backup import stored_backup_path


def write_archive(path: Path, *, movie_count: int = 2, description: str = "Test backup") -> None:
    movies = [
        {
            "id": f"00000000-0000-4000-8000-{index:012d}",
            "title": f"Archive Feature {index}",
            "year": 2000 + index,
        }
        for index in range(movie_count)
    ]
    tables = {name: [] for name in BACKUP_TABLES}
    tables["movies"] = movies
    manifest = {
        "format": BACKUP_FORMAT,
        "formatVersion": BACKUP_FORMAT_VERSION,
        "scope": "functional_collection",
        "createdAt": "2026-08-14T00:00:00Z",
        "generator": {"service": "unit-test", "description": description},
        "excludedScopes": [],
        "tables": {name: {"count": len(rows)} for name, rows in tables.items()},
        "media": {"mode": "embedded_local_files"},
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name in BACKUP_TABLES:
            zf.writestr(f"data/{name}.json", json.dumps(tables[name]))


class BackupSummaryCacheTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.backup_dir = Path(self._temp.name)
        self.archive = self.backup_dir / "discvault-next-movies-groups-20260814-120000.zip"
        write_archive(self.archive)

    def _count_reads(self):
        return mock.patch.object(
            next_backup,
            "validate_backup_zip",
            wraps=next_backup.validate_backup_zip,
        )

    def _count_hashes(self):
        return mock.patch.object(
            next_backup,
            "sha256_file",
            wraps=next_backup.sha256_file,
        )

    def test_the_second_listing_does_not_reopen_the_archive(self):
        # The defect, stated as an assertion: every call used to do this work.
        list_backup_archives(self.backup_dir)
        with self._count_reads() as validate, self._count_hashes() as digest:
            list_backup_archives(self.backup_dir)
        self.assertEqual(validate.call_count, 0)
        self.assertEqual(digest.call_count, 0)

    def test_the_first_listing_still_does_the_work_once(self):
        with self._count_reads() as validate, self._count_hashes() as digest:
            list_backup_archives(self.backup_dir)
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(digest.call_count, 1)

    def test_the_cached_answer_is_the_answer_it_replaces(self):
        # A faster wrong answer is not a fix. Nothing in the response may move.
        fresh = list_backup_archives(self.backup_dir, refresh=True)
        cached = list_backup_archives(self.backup_dir)
        self.assertEqual(cached, fresh)

    def test_the_reported_checksum_is_the_checksum_of_the_file(self):
        entry = list_backup_archives(self.backup_dir)[0]
        self.assertEqual(entry["sha256"], sha256_file(self.archive))

    def test_an_archive_replaced_on_the_host_is_recomputed(self):
        first = list_backup_archives(self.backup_dir)[0]
        write_archive(self.archive, movie_count=9, description="Replaced backup")
        second = list_backup_archives(self.backup_dir)[0]
        self.assertNotEqual(second["sha256"], first["sha256"])
        self.assertEqual(second["description"], "Replaced backup")
        self.assertEqual(second["tables"]["movies"]["count"], 9)

    def test_a_touched_archive_of_the_same_size_is_recomputed(self):
        # Size alone is a weak key -- an edit that happens to preserve it is not
        # exotic. The modification time carries the rest.
        list_backup_archives(self.backup_dir)
        stat = self.archive.stat()
        os.utime(self.archive, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        with self._count_reads() as validate:
            list_backup_archives(self.backup_dir)
        self.assertEqual(validate.call_count, 1)

    def test_refresh_re_reads_even_when_the_entry_still_matches(self):
        list_backup_archives(self.backup_dir)
        with self._count_reads() as validate:
            list_backup_archives(self.backup_dir, refresh=True)
        self.assertEqual(validate.call_count, 1)

    def test_an_entry_from_an_older_build_is_a_miss_not_a_wrong_answer(self):
        list_backup_archives(self.backup_dir)
        path = backup_summary_cache_path(self.archive)
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["cacheVersion"] = BACKUP_SUMMARY_CACHE_VERSION + 1
        path.write_text(json.dumps(stored), encoding="utf-8")
        with self._count_reads() as validate:
            entry = list_backup_archives(self.backup_dir)[0]
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(entry["sha256"], sha256_file(self.archive))

    def test_an_unreadable_entry_costs_a_recompute_and_nothing_else(self):
        list_backup_archives(self.backup_dir)
        backup_summary_cache_path(self.archive).write_text("{not json", encoding="utf-8")
        entry = list_backup_archives(self.backup_dir)[0]
        self.assertTrue(entry["valid"])
        self.assertEqual(entry["sha256"], sha256_file(self.archive))

    def test_a_backup_directory_that_cannot_be_written_still_lists(self):
        # A read-only mount must degrade to the old cost, not to an error.
        with mock.patch.object(next_backup, "write_backup_summary_cache", return_value=False):
            first = list_backup_archives(self.backup_dir)
            second = list_backup_archives(self.backup_dir)
        self.assertEqual(first, second)
        self.assertTrue(first[0]["valid"])

    def test_the_sidecar_is_not_offered_as_a_backup(self):
        list_backup_archives(self.backup_dir)
        names = {entry["fileName"] for entry in list_backup_archives(self.backup_dir)}
        self.assertEqual(names, {self.archive.name})
        with self.assertRaises(next_backup.BackupError):
            stored_backup_path(self.backup_dir, f"{BACKUP_SUMMARY_DIR_NAME}/{self.archive.name}.json")

    def test_deleting_an_archive_takes_its_entry_with_it(self):
        list_backup_archives(self.backup_dir)
        path = backup_summary_cache_path(self.archive)
        self.assertTrue(path.exists())
        self.archive.unlink()
        self.assertEqual(list_backup_archives(self.backup_dir), [])
        self.assertFalse(path.exists())

    def test_an_invalid_archive_is_reported_from_the_entry_too(self):
        broken = self.backup_dir / "discvault-next-movies-groups-20260813-120000.zip"
        broken.write_bytes(b"this is not a ZIP file")
        entries = {entry["fileName"]: entry for entry in list_backup_archives(self.backup_dir)}
        self.assertFalse(entries[broken.name]["valid"])
        self.assertTrue(entries[broken.name]["errors"])
        with self._count_reads() as validate:
            again = {entry["fileName"]: entry for entry in list_backup_archives(self.backup_dir)}
        self.assertEqual(validate.call_count, 0)
        self.assertEqual(again[broken.name], entries[broken.name])

    def test_an_edit_that_preserves_size_and_timestamp_is_the_known_limit(self):
        # Pinned rather than papered over: the key is the file's metadata, not
        # its bytes, so an edit that restores both is invisible until someone
        # asks for a refresh. That is what `?refresh=1` on the status route is
        # for, and this test is the record of why it exists.
        stale = list_backup_archives(self.backup_dir)[0]
        stat = self.archive.stat()
        write_archive(self.archive, movie_count=2, description="Test backup")
        os.utime(self.archive, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(self.archive.stat().st_size, stat.st_size)

        self.assertEqual(list_backup_archives(self.backup_dir)[0], stale)
        refreshed = list_backup_archives(self.backup_dir, refresh=True)[0]
        self.assertEqual(refreshed["sha256"], sha256_file(self.archive))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
