"""Library backup format 2: export (L-B-1 #639; §11.3, §11.4, §13.3).

The archive layout and manifest, text ``COPY`` of stored columns only, the
Library's rows and nobody else's, credential-free identities, objects copied
under the shared objects lock and checked against the catalogue (a missing,
unreadable or changed object fails the backup: SQLite audit #963), one
snapshot for the whole archive, verification before publish, refusal during
maintenance, and the run row.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import uuid
import zipfile
from typing import Any, Optional
from unittest import mock

import psycopg

from backend.domain.maintenance import backup, tables
from backend.domain.maintenance.backup import BackupFailed, BackupResult
from backend.domain.objects import lifecycle
from tests.pg.test_objects_lifecycle import ObjectsTestCase

VERSION = "0.8.0-test"


class BackupTestCase(ObjectsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.out = tempfile.mkdtemp(prefix="prks-pg-backup-")
        self.addCleanup(shutil.rmtree, self.out, True)

    @property
    def conninfo(self) -> str:
        return self.db.config.conninfo

    def backup(self, library_id: Optional[str] = None, **kwargs: Any) -> BackupResult:
        return backup.write_backup(self.conninfo, self.objects, library_id or self.actor.library_id,
                                   self.out, prks_version=VERSION, **kwargs)

    def member(self, result: BackupResult, name: str) -> bytes:
        with zipfile.ZipFile(result.path) as archive:
            return archive.read(name)

    def rows(self, result: BackupResult, name: str) -> list[dict[str, str]]:
        return list(csv.DictReader(io.StringIO(self.member(result, name).decode("utf-8"))))

    def runs(self) -> list[dict[str, Any]]:
        return self.sql("SELECT id, state, error_code, archive_bytes, archive_sha256 FROM prks.backup_runs "
                        "ORDER BY started_at")

    def ledger_row(self, actor: Any, entity_id: str = "W-1") -> None:
        self.sql("INSERT INTO prks.sync_operations (op_id, library_id, device_id, user_id, operation_type, "
                 "entity_type, entity_id, request_sha256, status, http_status, result) "
                 "VALUES (%s, %s, %s, %s, 'UPDATE_WORK', 'work', %s, %s, 'applied', 200, '{}')",
                 (uuid.uuid4(), actor.library_id, actor.device_id, actor.user_id, entity_id,
                  hashlib.sha256(b"x").digest()))


class ArchiveLayoutTests(BackupTestCase):
    def test_every_content_table_and_identity_is_a_member_listed_with_its_hash(self) -> None:
        obj = self.put(b"%PDF-1.4 one")
        work_id = self.create(obj)
        result = self.backup()
        manifest = result.manifest
        self.assertEqual(manifest["format"], "prks-backup")
        self.assertEqual(manifest["format_version"], 2)
        self.assertEqual(manifest["library_id"], self.actor.library_id)
        self.assertEqual(manifest["prks_version"], VERSION)
        self.assertRegex(manifest["schema_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(list(manifest["tables"]), list(tables.BACKUP_CONTENT_TABLES))
        with zipfile.ZipFile(result.path) as archive:
            infos = archive.infolist()
            self.assertEqual(sorted(i.filename for i in infos), sorted(backup.members(manifest)))
            self.assertTrue(all(i.compress_type == zipfile.ZIP_STORED for i in infos))
            self.assertEqual(json.loads(archive.read("manifest.json")), manifest)
            for table, entry in manifest["tables"].items():
                data = archive.read(backup.table_member(table))
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"], table)
        works = self.rows(result, "db/works.csv")
        self.assertEqual([w["id"] for w in works], [work_id])
        self.assertEqual(manifest["tables"]["prks.works"]["rows"], 1)
        self.assertEqual(manifest["objects"], {f"asset-objects/{obj.key.name}": {"size": obj.size,
                                                                                 "sha256": obj.sha256}})
        self.assertEqual(self.member(result, f"objects/asset-objects/{obj.key.name}"), b"%PDF-1.4 one")

    def test_the_interchange_contract_and_csv_headers_name_stored_columns_only(self) -> None:
        self.create(self.put())
        result = self.backup()
        contract = result.manifest["interchange"]
        for table in tables.BACKUP_CONTENT_TABLES:
            header = self.member(result, backup.table_member(table)).decode().splitlines()[0].split(",")
            self.assertEqual(header, [c["column"] for c in contract[table]], table)
        self.assertNotIn("name_key", [c["column"] for c in contract["prks.tags"]])
        self.assertEqual({c["column"]: c["type"] for c in contract["prks.sync_operations"]}["request_sha256"],
                         "bytes")

    def test_values_have_one_textual_form(self) -> None:
        self.create(self.put())
        self.ledger_row(self.actor)
        with mock.patch.dict(os.environ, {"PGTZ": "America/New_York"}):
            result = self.backup()
        (op,) = self.rows(result, "db/sync_operations.csv")
        self.assertTrue(op["applied_at"].endswith("+00"), op["applied_at"])
        self.assertEqual(op["request_sha256"], "\\x" + hashlib.sha256(b"x").hexdigest())

    def test_only_this_librarys_rows_are_exported(self) -> None:
        mine = self.create(self.put(b"mine"))
        other = self.other_actor()
        theirs = self.create(self.put(b"theirs"), ctx=other.context)
        self.ledger_row(other)
        result = self.backup()
        self.assertEqual([w["id"] for w in self.rows(result, "db/works.csv")], [mine])
        self.assertNotIn(theirs, self.member(result, "db/entity_revisions.csv").decode())
        self.assertEqual(self.rows(result, "db/sync_operations.csv"), [])
        self.assertEqual(len(result.manifest["objects"]), 1)


class IdentityTests(BackupTestCase):
    def test_users_and_devices_named_by_the_ledger_travel_without_credentials(self) -> None:
        self.ledger_row(self.actor)
        self.sql("UPDATE prks.users SET display_name = 'Ana', login_name = 'ana-signs-in' WHERE id = %s",
                 (self.actor.user_id,))
        result = self.backup()
        self.assertEqual(self.rows(result, "identities/users.csv"),
                         [{"archive_user_id": self.actor.user_id, "display_name": "Ana"}])
        (device,) = self.rows(result, "identities/devices.csv")
        self.assertEqual((device["device_id"], device["archive_user_id"]),
                         (str(self.actor.device_id), self.actor.user_id))
        with zipfile.ZipFile(result.path) as archive:
            everything = b"".join(archive.read(n) for n in archive.namelist())
        self.assertNotIn(b"ana-signs-in", everything)
        self.assertNotIn(b"argon2", everything)
        self.assertEqual(result.manifest["identities"]["users"]["rows"], 1)

    def test_a_library_without_attributed_rows_exports_no_identities(self) -> None:
        result = self.backup()
        self.assertEqual(self.rows(result, "identities/users.csv"), [])
        self.assertEqual(self.rows(result, "identities/devices.csv"), [])


class ObjectSafetyTests(BackupTestCase):
    def assert_failed(self, reason: str, **kwargs: Any) -> None:
        with self.assertRaises(BackupFailed) as caught:
            self.backup(**kwargs)
        self.assertEqual(caught.exception.reason, reason)
        (run,) = self.runs()
        self.assertEqual((run["state"], run["error_code"]), ("failed", reason))
        self.assertEqual(os.listdir(self.out), [])

    def test_a_missing_object_fails_the_backup(self) -> None:
        obj = self.put()
        self.create(obj)
        self.objects.delete(obj.key)  # the bytes vanish behind the catalogue's back
        self.assert_failed("object_missing")

    def test_an_unreadable_object_fails_the_backup(self) -> None:
        obj = self.put()
        self.create(obj)
        with mock.patch.object(self.objects, "open_read", side_effect=PermissionError("denied")):
            self.assert_failed("object_unreadable")

    def test_an_object_whose_bytes_changed_fails_the_backup(self) -> None:
        obj = self.put(b"original bytes")
        self.create(obj)
        changed = io.BytesIO(b"tampered bytes")
        with mock.patch.object(self.objects, "open_read", return_value=changed):
            self.assert_failed("object_changed")

    def test_a_changed_pristine_source_fails_the_backup(self) -> None:
        self.create(self.put(b"%PDF working"))
        source = self.put(b"%PDF damaged")  # the source key now holds other bytes than the upload's
        self.sql("UPDATE prks.assets SET source_locator = %s, ingest_sha256 = %s",
                 (source.key.name, hashlib.sha256(b"%PDF original").hexdigest()))
        self.assert_failed("object_changed")

    def test_a_disk_error_while_writing_is_a_backup_failure(self) -> None:
        self.create(self.put())
        with mock.patch.object(backup, "fsync_open_file", side_effect=OSError(28, "No space left on device")):
            self.assert_failed("io")

    def test_an_unsynced_archive_directory_is_a_backup_failure(self) -> None:
        self.create(self.put())
        with mock.patch.object(backup, "fsync_directory", return_value=False):
            self.assert_failed("io")

    def test_the_archive_is_synced_only_once_its_zip_directory_is_written(self) -> None:
        self.create(self.put())
        complete: list[bool] = []
        real = backup.fsync_open_file

        def check_then_sync(fd: int) -> None:
            with open(f"/proc/self/fd/{fd}", "rb") as handle:
                complete.append(zipfile.is_zipfile(handle))
            real(fd)

        with mock.patch.object(backup, "fsync_open_file", check_then_sync):
            self.backup()
        self.assertEqual(complete, [True])

    def test_the_lease_is_renewed_while_one_large_member_streams(self) -> None:
        self.create(self.put(b"x" * (backup.IO_CHUNK_SIZE * 8)))
        ticks = iter(range(0, 10**9, 1000))  # every reading is a whole lease later
        renewed: list[float] = []
        real = backup._Lease.tick

        def recording(lease: Any) -> None:
            before = lease._renewed
            real(lease)  # renews in the database; raises lease_lost when the update matches no run
            if lease._renewed != before:
                renewed.append(lease._renewed)

        with mock.patch.object(backup._Lease.__init__, "__defaults__", (lambda: float(next(ticks)),)), \
                mock.patch.object(backup._Lease, "tick", recording):
            result = self.backup()
        # More renewals than members: they also happen between the chunks of one member.
        self.assertGreater(len(renewed), len(list(backup.members(result.manifest))) + 8)

    def test_a_lease_renewal_failure_during_an_object_copy_is_a_database_failure(self) -> None:
        self.create(self.put(b"x" * (backup.IO_CHUNK_SIZE * 4)))
        real = backup._Lease.tick

        def failing_in_objects(lease: Any) -> None:
            if objects_reached:
                raise psycopg.OperationalError("connection lost")
            real(lease)

        objects_reached: list[bool] = []
        with mock.patch.object(backup._Lease, "tick", failing_in_objects):
            self.assert_failed("database", hooks={backup.PHASE_OBJECTS: lambda: objects_reached.append(True)})

    def test_a_failed_listing_never_publishes(self) -> None:
        self.create(self.put())
        with mock.patch.object(self.objects, "stat", side_effect=OSError("I/O error")):
            self.assert_failed("object_unreadable")


class ExclusionTests(BackupTestCase):
    def test_a_backup_is_refused_while_the_library_is_in_maintenance(self) -> None:
        self.sql("UPDATE prks.libraries SET maintenance = 'restore', maintenance_run = %s WHERE id = %s",
                 (uuid.uuid4(), self.actor.library_id))
        with self.assertRaises(BackupFailed) as caught:
            self.backup()
        self.assertEqual(caught.exception.reason, "MAINTENANCE")
        self.assertEqual(self.runs(), [])

    def test_cleanup_skips_the_library_while_a_backup_holds_the_objects_lock(self) -> None:
        obj = self.put()
        work_id = self.create(obj)
        results: dict[str, Any] = {}
        reached = threading.Event()
        release = threading.Event()

        def pause() -> None:
            reached.set()
            release.wait(10)

        thread = threading.Thread(target=lambda: results.setdefault(
            "result", self.backup(hooks={backup.PHASE_OBJECTS: pause})))
        thread.start()
        self.assertTrue(reached.wait(10))
        try:
            # The Work is deleted after the snapshot: its key is now claimed,
            # but settle cannot take the objects lock while the copy runs.
            self.library.delete_work_record(work_id)
            self.assertEqual(lifecycle.settle(self.db, self.objects, obj.key), lifecycle.Settled.BUSY)
            self.assertTrue(self.stored(obj.key.name))
        finally:
            release.set()
            thread.join(30)
        result = results["result"]
        self.assertIn(f"asset-objects/{obj.key.name}", result.manifest["objects"])
        self.assertEqual(lifecycle.settle(self.db, self.objects, obj.key), lifecycle.Settled.REMOVED)

    def test_the_whole_archive_is_one_snapshot(self) -> None:
        first = self.create(self.put(b"first"))
        later: dict[str, str] = {}

        def write_meanwhile() -> None:
            later["work"] = self.create(self.put(b"later"))

        # The hook runs after the snapshot transaction's first statement, so
        # the snapshot is already taken when this Work commits.
        result = self.backup(hooks={backup.PHASE_SNAPSHOT: write_meanwhile})
        self.assertEqual([w["id"] for w in self.rows(result, "db/works.csv")], [first])
        self.assertEqual(len(result.manifest["objects"]), 1)
        self.assertEqual(result.manifest["tables"]["prks.assets"]["rows"], 1)
        self.assertIn("work", later)


class RunTests(BackupTestCase):
    def test_a_published_run_records_the_verified_archive(self) -> None:
        result = self.backup()
        (run,) = self.runs()
        with open(result.path, "rb") as handle:
            data = handle.read()
        self.assertEqual((run["id"], run["state"]), (result.run_id, "published"))
        self.assertEqual((run["archive_bytes"], run["archive_sha256"]),
                         (len(data), hashlib.sha256(data).hexdigest()))
        self.assertEqual(os.path.basename(result.path), f"{result.run_id}.prks-backup")
        self.assertEqual(os.stat(result.path).st_mode & 0o777, 0o600)

    def test_an_archive_that_does_not_read_back_is_never_published(self) -> None:
        def corrupt() -> None:
            (name,) = os.listdir(self.out)
            with open(os.path.join(self.out, name), "r+b") as handle:
                data = handle.read()
                at = data.index(b"id,") + 10
                handle.seek(at)
                handle.write(b"#")

        self.create(self.put())
        with self.assertRaises(BackupFailed) as caught:
            self.backup(hooks={backup.PHASE_VERIFY: corrupt})
        self.assertEqual(caught.exception.reason, "verify")
        self.assertEqual(self.runs()[0]["state"], "failed")
        self.assertEqual(os.listdir(self.out), [])

    def test_the_backup_connection_keeps_no_lock_afterwards(self) -> None:
        self.backup()
        held = self.sql("SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory'")[0]["n"]
        self.assertEqual(held, 0)

    def test_a_database_error_fails_the_run(self) -> None:
        with mock.patch.object(backup, "_object_refs", side_effect=psycopg.errors.QueryCanceled("x")):
            with self.assertRaises(BackupFailed) as caught:
                self.backup()
        self.assertEqual(caught.exception.reason, "database")
        self.assertEqual(self.runs()[0]["error_code"], "database")

    def test_unknown_library_ids_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.backup("LIB-'; DROP TABLE x; --")
        with self.assertRaises(BackupFailed) as caught:
            self.backup("LIB-" + "F" * 32)
        self.assertEqual(caught.exception.reason, "library_missing")

