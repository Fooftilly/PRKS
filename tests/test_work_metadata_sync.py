"""Field-scoped Work metadata: per-field revisions, conflicts and independence."""
import tempfile
import unittest
import uuid

from backend import sync_protocol, work_metadata_sync as meta
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig


class WorkMetadataSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-meta-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.work = self.db.add_work("Metadata work")

    def op(self, field, value, base=0, **changes):
        envelope = dict(op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
                        operation="SET_WORK_METADATA_FIELD", entity_type="work",
                        entity_id=self.work, payload={"field": field, "value": value},
                        base_revision=base, occurred_at="2026-09-12T10:00:00Z",
                        created_at="2026-09-12T10:00:00Z", depends_on=[])
        envelope.update(changes)
        return envelope

    def send(self, field, value, base=0):
        return sync_protocol.process_operation(self.db, self.op(field, value, base))

    def state(self, work=None):
        return self.db.get_work_metadata_state(work or self.work)["fields"]

    def value(self, field):
        return self.db.execute_query(
            "SELECT %s AS v FROM works WHERE id = ?" % field, (self.work,))[0]["v"]

    # ---- the field registry ----

    def test_every_supported_field_round_trips(self):
        self.assertEqual(sorted(meta.SYNCED_FIELDS),
                         ["doi", "edition", "isbn", "issue", "journal", "pages", "volume"])
        for index, field in enumerate(sorted(meta.SYNCED_FIELDS)):
            with self.subTest(field=field):
                status, result = self.send(field, "value-%d" % index)
                self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", True))
                self.assertEqual(result["server_revision"], 1)
                self.assertEqual(self.value(field), "value-%d" % index)

    def test_an_unsupported_field_never_reaches_the_column(self):
        """An arbitrary column name from a client is both an injection surface
        and a way to reach fields this milestone deliberately does not
        synchronize."""
        for field in ("title", "status", "abstract", "id", "doi; DROP TABLE works", "", None, 7):
            with self.subTest(field=field):
                self.assertEqual(sync_protocol.process_operation(self.db, self.op(field, "x")),
                                 (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.db.execute_query(
            "SELECT title FROM works WHERE id = ?", (self.work,))[0]["title"], "Metadata work")
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_value_shape_is_bounded_and_typed(self):
        for value in (None, 7, ["x"], {"a": 1}, True, "x" * (meta.SYNCED_FIELDS["doi"] + 1)):
            with self.subTest(value=repr(value)[:40]):
                self.assertEqual(sync_protocol.process_operation(self.db, self.op("doi", value))[0], 400)
        # Exactly at the limit is legitimate.
        self.assertEqual(self.send("doi", "d" * meta.SYNCED_FIELDS["doi"])[0], 200)

    def test_envelope_requires_a_base_revision_and_an_exact_payload(self):
        for changes in ({"base_revision": None},
                        {"payload": {"field": "doi"}},
                        {"payload": {"field": "doi", "value": "x", "extra": 1}},
                        {"payload": {}},
                        {"entity_type": "concept"}):
            with self.subTest(changes=str(changes)[:50]):
                envelope = self.op("doi", "x")
                envelope.update(changes)
                self.assertEqual(sync_protocol.process_operation(self.db, envelope)[0], 400)
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_values_are_stored_exactly_as_patch_would_store_them(self):
        """Synchronization is not a licence to start normalizing values PRKS
        has never normalized: the two write paths must agree byte for byte."""
        for value in ("10.1234/Example-MiXeD", "978-0-306-40615-7", " leading and trailing ", "12–34"):
            with self.subTest(value=value):
                self.send("doi", value, base=self.state()["doi"]["revision"])
                self.assertEqual(self.value("doi"), value)
        second = self.db.add_work("Patch mirror")
        self.db.update_work_metadata(second, {"doi": "10.1234/Example-MiXeD"})
        self.assertEqual(self.db.execute_query(
            "SELECT doi FROM works WHERE id = ?", (second,))[0]["doi"], "10.1234/Example-MiXeD")

    def test_null_and_empty_are_one_logical_value(self):
        """A column left NULL by an older row and one a user cleared mean the
        same thing, so clearing an already-empty field is not a change."""
        self.db.execute_query("UPDATE works SET doi = NULL WHERE id = ?", (self.work,))
        self.assertEqual(self.state()["doi"], {"value": "", "revision": 0})
        status, result = self.send("doi", "")
        self.assertEqual((status, result["changed"], result["server_revision"]), (200, False, 0))
        self.assertIsNone(self.value("doi"))

    # ---- the conflict rule ----

    def test_scalar_conflict_matrix(self):
        cases = (
            # (base, desired, code, changed, revision after)
            (0, "first", "ACKNOWLEDGED", True, 1),
            (1, "first", "ACKNOWLEDGED", False, 1),
            (1, "second", "ACKNOWLEDGED", True, 2),
            (0, "second", "ACKNOWLEDGED", False, 2),
            (0, "third", "REVISION_CONFLICT", None, 2),
            (9, "fourth", "FUTURE_REVISION", None, 2),
        )
        for base, desired, code, changed, revision in cases:
            with self.subTest(base=base, desired=desired, code=code):
                op = self.op("doi", desired, base)
                status, result = sync_protocol.process_operation(self.db, op)
                self.assertEqual(result["code"], code)
                if changed is not None:
                    self.assertEqual(result["changed"], changed)
                self.assertEqual(self.state()["doi"]["revision"], revision)
                # Replay is exact, whatever the outcome was.
                self.assertEqual(sync_protocol.process_operation(self.db, op), (status, result))
        conflict = sync_protocol.process_operation(self.db, self.op("doi", "third", 0))[1]
        self.assertEqual(conflict["current_value"], "second")
        self.assertEqual(conflict["requested_value"], "third")
        self.assertEqual(conflict["current_revision"], 2)

    def test_a_stale_but_convergent_edit_is_not_a_conflict(self):
        """Two people typing the same DOI have converged, not collided."""
        self.send("doi", "10.1/agreed")
        status, result = self.send("doi", "10.1/agreed", base=0)
        self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        self.assertEqual(self.state()["doi"]["revision"], 1, "convergence advances nothing")

    def test_missing_work_is_terminal(self):
        op = self.op("doi", "x", entity_id="W-gone")
        status, result = sync_protocol.process_operation(self.db, op)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))
        self.assertEqual(sync_protocol.process_operation(self.db, op), (status, result))

    # ---- the point of the milestone ----

    def test_different_fields_never_conflict_with_each_other(self):
        """Two devices editing DOI and ISBN have not disagreed about anything.
        A single Work-level revision would tell them they had."""
        self.send("doi", "10.1/a")
        self.send("isbn", "111")
        self.send("isbn", "222", base=1)
        self.assertEqual(self.state()["doi"]["revision"], 1)
        self.assertEqual(self.state()["isbn"]["revision"], 2)
        # A device that has been offline since DOI revision 1 still applies
        # cleanly, even though ISBN moved twice underneath it.
        status, result = self.send("doi", "10.1/b", base=1)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual((self.value("doi"), self.value("isbn")), ("10.1/b", "222"))

    def test_one_field_conflicting_leaves_the_others_writable(self):
        self.send("doi", "server-doi")
        self.assertEqual(self.send("doi", "device-doi", base=0)[1]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.send("journal", "device-journal", base=0)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.value("journal"), "device-journal")

    # ---- the ordinary PATCH boundary ----

    def test_patch_advances_only_the_fields_it_actually_changed(self):
        """Revisions are canonical history, not sync-endpoint history: an
        offline device can only detect an online edit if the online path moved
        the same counter."""
        self.db.update_work_metadata(self.work, {"doi": "10.1/patched", "isbn": "555"})
        state = self.state()
        self.assertEqual((state["doi"]["revision"], state["isbn"]["revision"]), (1, 1))
        self.db.update_work_metadata(self.work, {"doi": "10.1/patched", "isbn": "666"})
        state = self.state()
        self.assertEqual((state["doi"]["revision"], state["isbn"]["revision"]), (1, 2),
                         "an unchanged field must not manufacture staleness")
        self.assertEqual((state["doi"]["value"], state["isbn"]["value"]), ("10.1/patched", "666"))
        # And a device holding the pre-PATCH DOI now correctly conflicts.
        self.assertEqual(self.send("doi", "10.1/device", base=0)[1]["code"], "REVISION_CONFLICT")

    def test_unrelated_metadata_never_advances_a_field_revision(self):
        self.send("doi", "10.1/keep")
        before = self.state()
        self.db.update_work_metadata(self.work, {
            "title": "Renamed", "status": "Paused", "abstract": "New abstract",
            "year": "1999", "publisher": "Someone", "doc_type": "book"})
        self.assertEqual(self.state(), before)
        self.assertEqual(self.db.execute_query(
            "SELECT title FROM works WHERE id = ?", (self.work,))[0]["title"], "Renamed")

    def test_patch_commits_values_and_revisions_together(self):
        """One transaction: a stored value whose revision did not advance is
        exactly the state that makes every other device's staleness check lie."""
        import sqlite3
        self.db.execute_query(
            "CREATE TRIGGER reject_revisions BEFORE INSERT ON sync_entity_revisions "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.update_work_metadata(self.work, {"title": "Rolled back", "doi": "10.1/rolled"})
        row = self.db.execute_query(
            "SELECT title, doi FROM works WHERE id = ?", (self.work,))[0]
        self.assertEqual((row["title"], row["doi"]), ("Metadata work", ""))
        self.assertEqual(self.state()["doi"]["revision"], 0)

    def test_work_creation_does_not_manufacture_revisions(self):
        created = self.db.add_work("Fresh", doi="10.1/initial", isbn="999")
        state = self.db.get_work_metadata_state(created)["fields"]
        self.assertEqual(state["doi"], {"value": "10.1/initial", "revision": 0})
        self.assertEqual(state["isbn"], {"value": "999", "revision": 0})

    # ---- the projection ----

    def test_projection_lists_every_supported_field_and_nothing_else(self):
        state = self.db.get_work_metadata_state(self.work)
        self.assertEqual(state["work_id"], self.work)
        self.assertEqual(sorted(state["fields"]), sorted(meta.SYNCED_FIELDS))
        self.assertIsNone(self.db.get_work_metadata_state("W-gone"))

    def test_projection_is_scoped_to_its_own_work(self):
        other = self.db.add_work("Other")
        self.db.update_work_metadata(other, {"doi": "10.1/other"})
        self.assertEqual(self.state()["doi"], {"value": "", "revision": 0})
        self.assertEqual(self.state(other)["doi"], {"value": "10.1/other", "revision": 1})

    def test_etag_moves_for_any_value_or_revision_change(self):
        seen = set()

        def snapshot(label):
            etag = self.db.etag_for_representation(
                "work-metadata-state", self.db.get_work_metadata_state(self.work))
            self.assertNotIn(etag, seen, label)
            seen.add(etag)

        snapshot("baseline")
        self.send("doi", "10.1/aaaa"); snapshot("doi value")
        self.send("doi", "10.1/bbbb", base=1); snapshot("same-length replacement")
        self.send("isbn", "111"); snapshot("another field")
        # A revision moving without the value moving still changes the ETag.
        self.db.execute_query(
            "UPDATE sync_entity_revisions SET revision = revision + 5 "
            "WHERE scope_type = 'work-field' AND scope_id = ?",
            (meta.scope_key(self.work, "doi"),))
        snapshot("revision only")

    def test_scope_keys_are_structural(self):
        self.assertNotEqual(meta.scope_key("W-a:b", "doi"), meta.scope_key("W-a", "b:doi"))
