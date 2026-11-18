"""Crash points across PostgreSQL write workflows (#1284; #391 §8, #651 C1).

Each test runs one workflow in a child process (``tests/pg/crash_point_child.py``)
that ``os._exit``s at a named point: no ``finally``, rollback or cleanup of
that process runs, as for a killed container. The parent then restarts the
way a new process does (backup/restore recovery, the orphan sweep, the
cleanup claims settled) and checks one shared set of invariants
(:meth:`CrashPointTestCase.assert_consistent`):

- every key a catalogue row names (an Asset's working or pristine source, a
  ``ready`` Processing item's object) exists in storage;
- every stored object is named by a row: nothing orphaned survives restart;
- no write temporary is left beside the objects;
- no cleanup claim is left once nothing is owed;
- an imported item names a live Work, and every ``ready`` item has its draft.

Each workflow also checks its own outcome: the old or the new state, never
both and never half; a retry of an acknowledged-but-unanswered request
returns the first answer and applies nothing twice.

The kill points name the step the process died *after*, so a failing
test's id says where to look.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import shutil
import tempfile
import uuid
from typing import Any
from unittest import mock

import psycopg

from backend.db.errors import DatabaseUnavailableError
from backend.domain.maintenance import backup as backup_module
from backend.domain.maintenance import jobs as backup_jobs
from backend.domain.processing import imports
from backend.domain.works import metadata
from tests.pg.crash_point_child import KILLED_EXIT
from tests.pg.test_processing_queue import QueueTestCase, pdf
from tests.pg.test_works_sync import SyncTestCase

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Large enough that the store reads it in several chunks, so "mid_write" is mid-stream.
BIG = 200 * 1024
_REFERENCED_SQL = """
SELECT storage_locator AS name FROM prks.assets WHERE storage_locator IS NOT NULL
UNION SELECT source_locator FROM prks.assets WHERE source_locator IS NOT NULL
UNION SELECT object_locator FROM prks.processing_items WHERE status = 'ready'
"""


def big_pdf(label: str) -> bytes:
    head = b"%PDF-1.4\n% " + label.encode() + b"\n"
    return head + b"0" * (BIG - len(head) - 7) + b"\n%%EOF\n"


class CrashPointTestCase(QueueTestCase):
    # -- the child -----------------------------------------------------------------

    def crash(self, workflow: str, kill_at: str, **args: Any) -> None:
        """Run ``workflow`` in a child that is killed at ``kill_at``; fail unless it was."""
        spec = {
            "conninfo": self.db.config.conninfo, "root": self.storage.root,
            "library_id": self.actor.library_id, "user_id": self.actor.user_id,
            "device_id": str(self.actor.device_id), "workflow": workflow, "kill_at": kill_at, **args,
        }
        child = subprocess.run([sys.executable, "-m", "tests.pg.crash_point_child"], input=json.dumps(spec).encode(),
                               cwd=_PROJECT_DIR, capture_output=True, timeout=120, env=dict(os.environ))
        self.assertEqual(child.returncode, KILLED_EXIT,
                         f"{workflow} was not killed at {kill_at}: {child.stderr.decode(errors='replace')}")

    def restart(self) -> None:
        """What a new process and its maintenance do: recovery, the sweep, every claim settled."""
        backup_jobs.recover_now(self.db.config.conninfo, self.storage.root)
        self.sweep()
        self.settle_all()

    # -- the invariants -------------------------------------------------------------

    def referenced(self) -> set[str]:
        return {row["name"] for row in self.sql(_REFERENCED_SQL)}

    def temporaries(self) -> list[str]:
        directory = os.path.join(self.storage.root, "pdfs")
        return sorted(name for name in os.listdir(directory) if name.startswith(".prks-"))

    def assert_consistent(self) -> None:
        referenced = self.referenced()
        stored = self.names()
        self.assertEqual(referenced - stored, set(), "a row names a key that is not stored")
        self.assertEqual(stored - referenced, set(), "an object nothing names survived the restart")
        self.assertEqual(self.temporaries(), [], "a write temporary survived the restart")
        self.assertEqual(self.claims(), set(), "a cleanup claim is left with nothing owed")
        self.assertEqual(self.sql(
            "SELECT i.id FROM prks.processing_items i LEFT JOIN prks.works w"
            " ON w.library_id = i.library_id AND w.id = i.imported_work_id"
            " WHERE i.status = 'imported' AND i.imported_work_id IS NOT NULL AND w.id IS NULL"), [])
        self.assertEqual(self.sql(
            "SELECT i.id FROM prks.processing_items i LEFT JOIN prks.processing_item_drafts d"
            " ON d.library_id = i.library_id AND d.item_id = i.id"
            " WHERE i.status = 'ready' AND d.item_id IS NULL"), [])

    def items(self) -> list[dict[str, Any]]:
        return self.sql("SELECT id, status, upload_key, object_locator, imported_work_id"
                        " FROM prks.processing_items ORDER BY created_at, id")

    def works(self) -> list[str]:
        return [row["id"] for row in self.sql("SELECT id FROM prks.works ORDER BY id")]


class UploadCrashTests(CrashPointTestCase):
    def test_killed_mid_write_publishes_nothing_and_the_retry_creates_one_item(self) -> None:
        data, key = big_pdf("mid"), uuid.uuid4().hex
        self.crash("upload", "mid_write", data=data.decode("latin-1"), upload_key=key)
        self.assertEqual((self.items(), self.names()), ([], set()))
        self.restart()
        self.assert_consistent()
        retried = self.upload(data, key=key)
        self.assertTrue(retried.created)
        self.assertEqual(self.bytes_of(self.row(retried.item["id"])["object_locator"]), data)
        self.assert_consistent()

    def test_killed_after_the_store_leaves_an_orphan_the_sweep_removes(self) -> None:
        data, key = pdf("stored"), uuid.uuid4().hex
        self.crash("upload", "stored", data=data.decode("latin-1"), upload_key=key)
        self.assertEqual(self.items(), [])
        self.assertEqual(len(self.names()), 1)  # published, adopted by nothing
        self.restart()
        self.assert_consistent()
        self.assertEqual(self.names(), set())
        retried = self.upload(data, key=key)
        self.assertTrue(retried.created)
        self.assert_consistent()

    def test_killed_after_the_commit_the_retry_answers_with_the_same_item(self) -> None:
        data, key, item_id = pdf("committed"), uuid.uuid4().hex, "PI-" + uuid.uuid4().hex.upper()
        self.crash("upload", "committed", data=data.decode("latin-1"), upload_key=key, item_id=item_id)
        self.restart()
        self.assert_consistent()
        retried = self.upload(data, key=key)
        self.assertFalse(retried.created)
        self.assertFalse(retried.body_read)
        self.assertEqual(retried.item["id"], item_id)
        self.assertEqual([row["id"] for row in self.items()], [item_id])
        self.assert_consistent()


class ImportCrashTests(CrashPointTestCase):
    def test_killed_mid_transaction_leaves_the_item_ready_and_the_retry_imports_once(self) -> None:
        item = self.ready(pdf("import mid"))
        locator = self.row(item["id"])["object_locator"]
        self.crash("import", "mid_transaction", item_id=item["id"])
        (row,) = self.items()
        self.assertEqual((row["status"], row["imported_work_id"]), ("ready", None))
        self.assertEqual(self.works(), [])
        self.restart()
        self.assert_consistent()
        done = self.import_(item["id"])
        self.assertTrue(done.created)
        self.assertEqual(self.works(), [done.work_id])
        self.assertEqual(self.asset_of(done.work_id)["storage_locator"], locator)
        self.assert_consistent()

    def test_killed_after_the_commit_the_retry_answers_with_the_same_work(self) -> None:
        item = self.ready(pdf("import committed"))
        self.crash("import", "committed", item_id=item["id"])
        (row,) = self.items()
        self.assertEqual(row["status"], "imported")
        self.restart()
        self.assert_consistent()
        again = self.import_(item["id"])
        self.assertEqual(again, imports.Imported(item["id"], str(row["imported_work_id"]), False))
        self.assertEqual(self.works(), [again.work_id])
        self.assert_consistent()


class ReplaceCrashTests(CrashPointTestCase):
    def work(self) -> tuple[str, dict[str, Any]]:
        done = self.import_(self.ready(pdf("original"))["id"])
        return done.work_id, self.asset_of(done.work_id)

    def test_killed_after_the_store_keeps_the_old_document(self) -> None:
        work_id, before = self.work()
        self.crash("replace", "stored", work_id=work_id, data=pdf("replacement").decode("latin-1"))
        self.assertEqual(self.asset_of(work_id), before)
        self.restart()
        self.assert_consistent()
        self.assertEqual(self.names(), {before["storage_locator"]})

    def test_killed_inside_the_command_keeps_the_old_document(self) -> None:
        work_id, before = self.work()
        self.crash("replace", "adopted", work_id=work_id, data=pdf("replacement").decode("latin-1"))
        self.assertEqual(self.asset_of(work_id), before)
        self.restart()
        self.assert_consistent()
        self.assertEqual(self.bytes_of(before["storage_locator"]), pdf("original"))

    def test_killed_after_the_commit_has_the_new_document_and_no_old_bytes(self) -> None:
        work_id, before = self.work()
        new = pdf("replacement")
        self.crash("replace", "committed", work_id=work_id, data=new.decode("latin-1"))
        after = self.asset_of(work_id)
        self.assertNotEqual(after["storage_locator"], before["storage_locator"])
        self.restart()
        self.assert_consistent()
        self.assertEqual(self.bytes_of(after["storage_locator"]), new)
        self.assertNotIn(before["storage_locator"], self.names())


class SettleCrashTests(CrashPointTestCase):
    def test_killed_after_the_unlink_the_next_settle_finishes_the_claim(self) -> None:
        item = self.ready(pdf("deleted"))
        name = self.row(item["id"])["object_locator"]
        self.delete(item["id"])  # the marker stays; the key is claimed
        self.assertEqual(self.claims(), {name})
        self.crash("settle", "unlinked", name=name)
        # The bytes are gone but the claim's delete never committed.
        self.assertNotIn(name, self.names())
        self.assertEqual(self.claims(), {name})
        self.restart()
        self.assert_consistent()


class BackupCrashTests(CrashPointTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.out = tempfile.mkdtemp(prefix="prks-pg-crash-backup-")
        self.addCleanup(shutil.rmtree, self.out, True)

    def test_killed_mid_archive_publishes_nothing_and_recovery_removes_the_partial_file(self) -> None:
        self.import_(self.ready(pdf("backed up"))["id"])
        self.crash("backup", "objects", dest_dir=self.out)
        (run,) = self.sql("SELECT id, state FROM prks.backup_runs")
        self.assertEqual(run["state"], "running")
        partial = os.path.join(self.out, f"{run['id']}{backup_module.BACKUP_EXTENSION}")
        self.assertTrue(os.path.exists(partial))
        # Nothing offers the partial archive: only a published run is downloadable.
        self.assertIsNone(backup_jobs.published_archive(self.db, self.actor.context, self.storage.root, run["id"]))
        # Its lease runs out; the next recovery fails the run and the prune removes the file.
        self.sql("UPDATE prks.backup_runs SET locked_until = now() - interval '1 second'")
        backup_jobs.recover_now(self.db.config.conninfo)
        self.assertEqual(backup_jobs.prune_archives(self.db.config.conninfo, self.out), 1)
        self.assertEqual(self.sql("SELECT state, error_code FROM prks.backup_runs"),
                         [{"state": "failed", "error_code": "lease_expired"}])
        self.assertEqual(os.listdir(self.out), [])
        # The killed backup's shared objects lock went with its session: a claim settles.
        item = self.ready(pdf("after"))
        self.delete(item["id"])
        self.restart()
        self.assert_consistent()
        # And the next backup publishes.
        result = backup_module.write_backup(self.db.config.conninfo, self.objects, self.actor.library_id,
                                            self.out, prks_version="0.8.0-test")
        self.assertEqual(os.listdir(self.out), [os.path.basename(result.path)])


class DroppedConnectionTests(SyncTestCase):
    """The server ends the session in the middle of a sync write (§7.1), as a failover or restart does."""

    def test_a_write_whose_connection_drops_commits_nothing_and_the_retry_applies_once(self) -> None:
        work_id = self.library.add_work("Before")
        data = self.title_op(work_id, "After", 0)
        real_set_field = metadata.apply_field_operation

        def drop_then_set(scope: Any, *args: Any, **kwargs: Any) -> Any:
            result = real_set_field(scope, *args, **kwargs)
            # The write is done inside the transaction; then the server ends this session.
            with psycopg.connect(self.db_conninfo, autocommit=True) as admin:
                pid = scope.execute("SELECT pg_backend_pid() AS pid FROM prks.libraries"
                                    " WHERE id = %(library_id)s", {}).fetchone()["pid"]
                admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
            return result

        with mock.patch.object(metadata, "apply_field_operation", drop_then_set):
            with self.assertRaises(DatabaseUnavailableError):
                self.send(data)
        self.assertEqual(self.ledger(), [])
        self.assertEqual(self.sql("SELECT title FROM prks.works WHERE id = %s", (work_id,)),
                         [{"title": "Before"}])
        first = self.send(data)
        self.assertEqual(first[1]["server_revision"], 1)
        # The client never saw an answer for the dropped attempt and sends it again.
        self.assertEqual(self.send(data), first)
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.sql("SELECT title FROM prks.works WHERE id = %s", (work_id,)),
                         [{"title": "After"}])

