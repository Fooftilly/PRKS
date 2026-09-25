"""Canonical sync boundaries, rollback, lifecycle and revision protocol."""
import json
import pathlib
import re
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from tests.test_db_migrations import MigrationTestCase, _raw, _version
from tests.work_identity_fixtures import revert_to_v16_schema
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from backend import sync_protocol, work_tag_sync as sync
from backend.db_migrations import LATEST_SCHEMA_VERSION, application_schema_signature


class WorkTagSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-sync-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.work = self.db.add_work("Sync work")
        self.tag = self.db.add_tag("Sync tag")["id"]

    def op(self, present=True, base=0, **changes):
        return dict(op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
                    operation="ADD_WORK_TAG" if present else "REMOVE_WORK_TAG",
                    entity_type="work", entity_id=self.work, payload={"tag_id": self.tag},
                    base_revision=base, occurred_at="2026-09-11T00:00:00Z",
                    created_at="2026-09-11T00:00:00Z", depends_on=[], **changes)

    def revision(self, tag=None, work=None):
        with self.db.connection() as conn:
            return sync.get_revision(conn, work or self.work, tag or self.tag)

    def test_direct_and_bulk_only_changed_pairs_advance(self):
        self.db.add_tag_to_work(self.work, self.tag)
        self.db.add_tag_to_work(self.work, self.tag)
        self.assertEqual(self.revision(), 1)
        second = self.db.add_work("Second")
        args = dict(work_ids=[self.work, second], tag_ids=[self.tag], action="add_tags")
        self.db.bulk_update_works(args)
        self.assertEqual(self.revision(), 1)
        self.assertEqual(self.revision(work=second), 1)
        args["action"] = "remove_tags"
        self.db.bulk_update_works(args)
        self.db.bulk_update_works(args)
        self.db.remove_tag_from_work(self.work, self.tag)
        self.assertEqual(self.revision(), 2)
        self.assertEqual(self.revision(work=second), 2)

    def test_merge_delete_revisions_and_chain(self):
        target = self.db.add_tag("Target")["id"]
        last = self.db.add_tag("Last")["id"]
        second = self.db.add_work("Both")
        for w, t in ((self.work, self.tag), (second, self.tag), (second, target)):
            self.db.add_tag_to_work(w, t)
        self.db.merge_tags_into(self.tag, target)
        self.assertEqual(self.revision(), 2)
        self.assertEqual(self.revision(target), 1)
        self.assertEqual(self.revision(target, second), 1)
        self.db.merge_tags_into(target, last)
        with self.db.connection() as conn:
            self.assertEqual(sync.resolve_lifecycle(conn, self.tag), {"state": "MERGED", "target_tag_id": last})
            self.assertEqual(sync.resolve_lifecycle(conn, last), {"state": "ACTIVE"})
            self.assertEqual(sync.resolve_lifecycle(conn, "unknown"), {"state": "UNKNOWN"})
        op = self.op()
        original = sync_protocol.process_operation(self.db, op)
        self.assertEqual(original[1]["target_tag_id"], last)
        self.db.delete_tag(last)
        self.assertEqual(self.revision(last), 2)
        self.assertEqual(sync_protocol.process_operation(self.db, op), original)
        self.assertEqual(sync_protocol.process_operation(self.db, self.op())[1]["code"], "TAG_DELETED")

    def test_cycle_is_transient_not_ledgered(self):
        self.db.execute_query("UPDATE sync_tag_lifecycle SET state='merged', target_tag_id=tag_id WHERE tag_id=?", (self.tag,))
        with self.assertRaises(RuntimeError):
            sync_protocol.process_operation(self.db, self.op())
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_idempotency_normalization_and_reuse(self):
        op = self.op()
        first = sync_protocol.process_operation(self.db, op)
        self.assertEqual(first[0], 200)
        reordered = dict(reversed(list(op.items())))
        reordered["occurred_at"] = "2026-09-11T00:00:00+00:00"
        self.assertEqual(sync_protocol.process_operation(self.db, reordered), first)
        self.assertEqual(self.revision(), 1)
        op["operation"] = "REMOVE_WORK_TAG"
        self.assertEqual(sync_protocol.process_operation(self.db, op), (409, {"code": "OP_ID_REUSE"}))
        self.assertEqual(self.revision(), 1)

    def test_conflict_matrix_and_future(self):
        for desired, base, code, revision in ((False, 0, "ACKNOWLEDGED", 0),
                (True, 0, "ACKNOWLEDGED", 1), (True, 1, "ACKNOWLEDGED", 1),
                (True, 0, "ACKNOWLEDGED", 1), (False, 0, "REVISION_CONFLICT", 1),
                (False, 2, "FUTURE_REVISION", 1)):
            with self.subTest(desired=desired, base=base, code=code):
                op = self.op(desired, base)
                response = sync_protocol.process_operation(self.db, op)
                self.assertEqual(response[1]["code"], code)
                self.assertEqual(sync_protocol.process_operation(self.db, op), response)
                self.assertEqual(self.revision(), revision)
        conflict = response[1]
        self.assertEqual((conflict["current_state"], conflict["current_revision"], conflict["requested_state"]), (True, 1, False))

    def test_missing_entity_terminal(self):
        for field, value in (("entity_id", "missing"), ("payload", {"tag_id": "missing"})):
            op = self.op(); op[field] = value
            result = sync_protocol.process_operation(self.db, op)
            self.assertEqual(result[0], 404)
            self.assertEqual(result[1]["code"], "ENTITY_NOT_FOUND")
            self.assertEqual(sync_protocol.process_operation(self.db, op), result)

    def test_strict_envelope(self):
        for field, value in (("base_revision", True), ("base_revision", -1), ("base_revision", 1.2),
                ("base_revision", None), ("device_id", "bad"), ("op_id", "bad"),
                ("operation", "RENAME_EVERYTHING"), ("payload", {"tag_id": self.tag, "extra": 1}),
                ("occurred_at", "yesterday"), ("created_at", None), ("depends_on", [str(uuid.uuid4())])):
            op = self.op(); op[field] = value
            self.assertEqual(sync_protocol.process_operation(self.db, op)[0], 400)
        self.assertEqual(self.revision(), 0)
        self.assertEqual(self.db.get_work_tags(self.work), [])
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_atomic_rollback_before_and_during_ledger_insert(self):
        with patch.object(sync_protocol, "insert_result", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                sync_protocol.process_operation(self.db, self.op())
        self.db.execute_query("CREATE TRIGGER reject_sync BEFORE INSERT ON sync_operations BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            sync_protocol.process_operation(self.db, self.op())
        self.assertEqual(self.db.get_work_tags(self.work), [])
        self.assertEqual(self.revision(), 0)
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_projection_tombstones_and_etags(self):
        untouched = self.db.add_tag("Untouched")["id"]
        previous = self.db.get_work_tag_options(self.work)
        def changed():
            nonlocal previous
            current = self.db.get_work_tag_options(self.work)
            self.assertNotEqual(self.db.etag_for_representation("opts", previous), self.db.etag_for_representation("opts", current))
            previous = current
        self.db.add_tag_to_work(self.work, self.tag); changed()
        self.assertEqual(previous["assigned"], [{"tag_id": self.tag, "relation_revision": 1}])
        self.db.remove_tag_from_work(self.work, self.tag); changed()
        self.assertEqual(previous["known_absent"], {self.tag: 2})
        self.assertNotIn(untouched, previous["known_absent"])
        self.db.execute_query("UPDATE tags SET name='Renamed' WHERE id=?", (self.tag,))
        self.assertEqual(self.db.get_work_tag_options(self.work), previous)
        for action in ("add_tags", "remove_tags", "add_tags"):
            self.db.bulk_update_works(dict(action=action, work_ids=[self.work], tag_ids=[self.tag])); changed()
        self.db.merge_tags_into(self.tag, untouched); changed()
        self.db.delete_tag(untouched); changed()

    def against(self, **overrides):
        op = self.op()
        op.update(overrides)
        return op

    def _domain_snapshot(self):
        return (sorted(map(tuple, (r.values() for r in self.db.execute_query("SELECT work_id, tag_id FROM work_tags")))),
                sorted(map(tuple, (r.values() for r in self.db.execute_query(
                    "SELECT scope_type, scope_id, revision FROM sync_entity_revisions")))),
                sorted(map(tuple, (r.values() for r in self.db.execute_query(
                    "SELECT tag_id, state, target_tag_id FROM sync_tag_lifecycle")))))

    def test_ledger_replay_is_exact_after_unrelated_state_changes(self):
        """A replayed op_id returns the outcome the LEDGER recorded, not the
        one today's server would produce. Without that, a device whose response
        was lost re-derives a different answer from a world that has moved on
        -- and a semantic outcome the user already saw resolved changes under
        them."""
        merged = self.db.add_tag("Merged away")["id"]
        target = self.db.add_tag("Merge target")["id"]
        doomed = self.db.add_tag("Doomed")["id"]
        originals = {}

        def record(code, op):
            result = sync_protocol.process_operation(self.db, op)
            self.assertEqual(result[1]["code"], code)
            originals[code] = (op, result)

        record("ACKNOWLEDGED", self.op())
        # Stale base against a relationship the server has since moved.
        record("REVISION_CONFLICT", self.op(present=False, base=0))
        record("ENTITY_NOT_FOUND", self.against(entity_id="W-does-not-exist"))
        self.db.merge_tags_into(merged, target)
        record("TAG_MERGED", self.against(payload={"tag_id": merged}))
        self.db.delete_tag(doomed)
        record("TAG_DELETED", self.against(payload={"tag_id": doomed}))
        record("FUTURE_REVISION", self.op(base=99))

        # The world moves on in every way that could re-derive a new answer:
        # the relationship flips, the merge target is destroyed (so the merge
        # chain now ends in a deleted Tag), and a fresh Tag appears.
        self.db.remove_tag_from_work(self.work, self.tag)
        self.db.add_tag_to_work(self.work, self.tag)
        self.db.delete_tag(target)
        self.db.add_tag("Newcomer")
        before = self._domain_snapshot()
        ledger = self.db.execute_query("SELECT * FROM sync_operations ORDER BY op_id")

        for code, (op, result) in originals.items():
            self.assertEqual(sync_protocol.process_operation(self.db, op), result, code)
        self.assertEqual(self._domain_snapshot(), before, "replay performed a domain mutation")
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations ORDER BY op_id"), ledger)

    def test_structural_scope_key(self):
        self.assertNotEqual(sync.scope_key("a:b", "c"), sync.scope_key("a", "b:c"))


class SyncMigrationTests(MigrationTestCase):
    def test_v13_upgrade_preserves_revision_zero_and_matches_fresh(self):
        db = self._open()
        work = db.add_work("Legacy")
        tag = db.add_tag("Legacy")["id"]
        db.add_tag_to_work(work, tag)
        with db.connection() as conn:
            signature = application_schema_signature(conn)
        raw = sqlite3.connect(db.db_path)
        revert_to_v16_schema(raw)
        raw.close()
        with db.connection() as conn:
            for table in ("sync_operations", "sync_entity_revisions", "sync_tag_lifecycle"):
                conn.execute("DROP TABLE " + table)
            conn.execute("UPDATE schema_version SET version=13")
        db = self._open()
        self.assertEqual(_version(db.db_path), LATEST_SCHEMA_VERSION)
        self.assertEqual(db.get_work_tag_options(work)["assigned"], [{"tag_id": tag, "relation_revision": 0}])
        with db.connection() as conn:
            self.assertEqual(application_schema_signature(conn), signature)
            self.assertEqual(sync.resolve_lifecycle(conn, tag), {"state": "ACTIVE"})


class WorkTagWriterAuditTests(unittest.TestCase):
    """Every canonical relationship write goes through the revision-aware
    boundary. A direct INSERT or DELETE would change state without advancing
    the revision, and every offline device's conflict detection is built on
    that counter: a silent write is a change another device can overwrite
    without ever being told there was a conflict."""

    # db_migrations.py predates the counter -- its case-dedupe runs while
    # upgrading a library that has no revisions yet, and migration 14 starts
    # every existing relationship at zero regardless.
    ALLOWED = frozenset({"backend/work_tag_sync.py", "backend/db_migrations.py"})
    WRITE = re.compile(r"(INSERT\s+(OR\s+\w+\s+)?INTO\s+work_tags"
                       r"|DELETE\s+FROM\s+work_tags"
                       r"|UPDATE\s+work_tags)", re.IGNORECASE)

    def test_only_the_revision_aware_boundary_writes_work_tags(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        offenders = []
        for path in sorted((root / "backend").rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            if relative in self.ALLOWED:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if self.WRITE.search(line):
                    offenders.append("%s:%d" % (relative, number))
        self.assertEqual(offenders, [], "use work_tag_sync.set_state() instead")
