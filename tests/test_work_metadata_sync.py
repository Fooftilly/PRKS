"""Field-scoped Work metadata: per-field revisions, conflicts and independence."""
import pathlib
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

    def sample(self, field, index):
        """A value this field will actually accept. Status is validated by
        ALLOWLIST, not by length, so the free-text sample every other field
        takes is a refusal for it."""
        if field in meta.FIELD_CODECS:
            # A codec field accepts only what its codec accepts: free text is
            # a refusal, not a long value.
            current = meta.database_to_wire(field, self.value(field))
            page = (int(current) if current else 0) + 1 + index
            return str(page)
        if field in meta.FIELD_ALLOWLISTS:
            # Deliberately a value the Work does NOT already hold: an
            # allowlist is small, so an index-chosen sample can silently
            # become a no-op and turn "did this advance?" into a test that
            # asserts nothing.
            current = self.value(field)
            allowed = [v for v in sorted(meta.FIELD_ALLOWLISTS[field]) if v != current]
            return allowed[index % len(allowed)]
        return "value-%d" % index

    def value(self, field):
        """The raw column. For `thumb_page` that is an INTEGER or NULL, not a
        string -- see `wire_value()`."""
        return self.db.execute_query(
            "SELECT %s AS v FROM works WHERE id = ?" % field, (self.work,))[0]["v"]

    def wire_value(self, field):
        """The column in the representation the protocol uses. Identical to the
        column for every field but `thumb_page`, where the column is typed and
        the wire is its decimal string."""
        return meta.database_to_wire(field, self.value(field))

    # ---- the field registry ----

    def test_every_supported_field_round_trips(self):
        self.assertEqual(sorted(meta.SYNCED_FIELDS),
                         ["abstract", "author_text", "doc_type", "doi", "edition", "isbn",
                          "issue", "journal", "location", "pages", "published_date",
                          "publisher", "source_url", "status", "thumb_page", "title",
                          "volume", "year"])
        for index, field in enumerate(sorted(meta.SYNCED_FIELDS)):
            with self.subTest(field=field):
                wanted = self.sample(field, index + 1)
                code, result = self.send(field, wanted)
                self.assertEqual((code, result["code"], result["changed"]), (200, "ACKNOWLEDGED", True))
                self.assertEqual(result["server_revision"], 1)
                self.assertEqual(self.wire_value(field), wanted)

    def test_an_unsupported_field_never_reaches_the_column(self):
        """An arbitrary column name from a client is both an injection surface
        and a way to reach fields this milestone deliberately does not
        synchronize."""
        for field in ("id",
                      "doi; DROP TABLE works", "", None, 7):
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

    def test_idempotency_normalization_and_reuse(self):
        """A lost-response retry must replay the ledgered outcome; a reused
        op_id with a different payload must not mutate the field again."""
        op_id = str(uuid.uuid4())
        device_id = str(uuid.uuid4())
        first = sync_protocol.process_operation(
            self.db, self.op("doi", "10.1/once", op_id=op_id, device_id=device_id))
        self.assertEqual(first[0], 200)
        self.assertEqual(first[1]["code"], "ACKNOWLEDGED")
        # Exact same envelope identity (same op_id + device_id + payload)
        # replays the ledgered answer without advancing the field again.
        second = sync_protocol.process_operation(
            self.db, self.op("doi", "10.1/once", op_id=op_id, device_id=device_id))
        self.assertEqual(second, first)
        self.assertEqual(self.state()["doi"]["revision"], 1)
        reused = sync_protocol.process_operation(
            self.db, self.op("doi", "10.1/other", op_id=op_id, device_id=device_id))
        self.assertEqual(reused, (409, {"code": "OP_ID_REUSE"}))
        self.assertEqual(self.value("doi"), "10.1/once")
        self.assertEqual(self.state()["doi"]["revision"], 1)

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

    def test_every_synchronized_field_advances_its_revision_through_the_patch(self):
        """Sampling two fields would not have caught `year` and
        `published_date` arriving: a synchronized field whose online PATCH
        writes the column without moving the counter leaves every offline
        device believing its stale copy is current, and the next save from
        that device silently overwrites the newer value."""
        for index, field in enumerate(sorted(meta.SYNCED_FIELDS)):
            with self.subTest(field=field):
                before = self.state()[field]["revision"]
                wanted = self.sample(field, index + 1)
                self.db.update_work_metadata(self.work, {field: wanted})
                after = self.state()[field]
                self.assertEqual(after["revision"], before + 1,
                                 "%s did not advance through update_work_metadata" % field)
                self.assertEqual(self.wire_value(field), wanted)
                # A device holding the pre-PATCH value must now be told so.
                self.assertEqual(self.send(field, self.sample(field, index + 2), base=before)[1]["code"],
                                 "REVISION_CONFLICT", field)

    # ---- the bulk boundary ----

    def test_bulk_status_advances_the_same_revisions_every_other_path_uses(self):
        """A bulk action is an ordinary canonical mutation wearing a different
        hat. If it wrote the column directly it would change the value while
        leaving the revision where it was, and every offline device holding the
        pre-bulk Status would compare equal revisions, conclude it was current
        and overwrite the newer value on its next save."""
        other = self.db.add_work("Second", status="Planned")
        self.db.update_work_metadata(self.work, {"status": "Planned"})
        before = self.state()["status"]["revision"]
        self.db.bulk_update_works({"work_ids": [self.work, other],
                                   "action": "set_status", "status": "Completed"})
        self.assertEqual(self.value("status"), "Completed")
        self.assertEqual(self.state()["status"]["revision"], before + 1)
        self.assertEqual(self.state(other)["status"], {"value": "Completed", "revision": 1})
        # And nothing else moved: a bulk Status change is not a Work-wide edit.
        for field in ("doi", "year", "abstract"):
            self.assertEqual(self.state()[field]["revision"], 0, field)

    def test_a_no_op_bulk_status_manufactures_no_revision(self):
        """Inflating a counter for a value that did not change would invent
        staleness for every device that already holds it -- and a bulk action
        over a large selection is exactly where most of the rows are already
        in the requested state."""
        self.db.bulk_update_works({"work_ids": [self.work],
                                   "action": "set_status", "status": "Completed"})
        after_first = self.state()["status"]["revision"]
        self.db.bulk_update_works({"work_ids": [self.work],
                                   "action": "set_status", "status": "Completed"})
        self.assertEqual(self.state()["status"]["revision"], after_first)
        # A device holding the current value is still current.
        self.assertEqual(self.send("status", "Paused", base=after_first)[1]["code"],
                         "ACKNOWLEDGED")

    def test_bulk_status_values_and_revisions_roll_back_together(self):
        """Partial application is the one outcome that cannot be recovered
        from: a stored value whose revision did not advance makes every other
        device's staleness check lie, permanently."""
        import sqlite3
        other = self.db.add_work("Second", status="Planned")
        self.db.update_work_metadata(self.work, {"status": "Planned"})
        before = self.state()
        self.db.execute_query(
            "CREATE TRIGGER reject_bulk BEFORE UPDATE OF revision ON sync_entity_revisions "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.bulk_update_works({"work_ids": [self.work, other],
                                       "action": "set_status", "status": "Completed"})
        self.assertEqual(self.value("status"), "Planned")
        self.assertEqual(self.db.get_work(other)["status"], "Planned")
        self.assertEqual(self.state(), before)

    def test_a_bulk_change_makes_an_offline_device_conflict(self):
        """The whole point of routing bulk through the revision boundary. The
        device was away when the bulk action ran; it must be told, not
        silently allowed to overwrite."""
        self.db.update_work_metadata(self.work, {"status": "Planned"})
        observed = self.state()["status"]["revision"]
        self.assertEqual(observed, 1)
        self.db.bulk_update_works({"work_ids": [self.work],
                                   "action": "set_status", "status": "Completed"})
        self.assertEqual(self.state()["status"]["revision"], 2)
        code, result = self.send("status", "Paused", base=observed)
        self.assertEqual((code, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], "Completed")
        self.assertEqual(self.value("status"), "Completed",
                         "the away device did not overwrite the bulk change")

    # ---- the allowlist ----

    def test_status_is_validated_by_allowlist_on_every_mutation_path(self):
        """Three paths, one rule. A value savable by one and refused by another
        is the split contract moving a field to local-first exists to remove --
        and the SQLite CHECK constraint is a last line of defence, not a
        first: it raises an IntegrityError rather than telling the client which
        value it should have sent."""
        for bad in ("Finished", "completed", "", "Done", " "):
            with self.subTest(value=bad):
                # 1. the synchronization operation
                self.assertEqual(sync_protocol.process_operation(
                    self.db, self.op("status", bad)), (400, {"code": "INVALID_ENVELOPE"}))
                # 2. the ordinary PATCH
                with self.assertRaises(ValueError):
                    self.db.update_work_metadata(self.work, {"status": bad})
                # 3. the bulk action
                from backend.db_manager import BulkWorkError
                with self.assertRaises(BulkWorkError):
                    self.db.bulk_update_works({"work_ids": [self.work],
                                               "action": "set_status", "status": bad})
        self.assertEqual(self.value("status"), "Not Started",
                         "not one refusal reached the column")
        self.assertEqual(self.state()["status"]["revision"], 0)
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])
        for good in meta.WORK_STATUSES:
            with self.subTest(value=good):
                self.assertTrue(meta.is_valid_field_value("status", good))

    def test_a_convergent_status_edit_is_not_a_conflict(self):
        """Two devices that independently decided a Work was Completed have
        not disagreed about anything. Telling them they had would demand a
        resolution for a collision that never happened."""
        self.db.update_work_metadata(self.work, {"status": "Planned"})
        base = self.state()["status"]["revision"]
        # Someone else gets there first, with the same answer.
        self.db.update_work_metadata(self.work, {"status": "Completed"})
        advanced = self.state()["status"]["revision"]
        code, result = self.send("status", "Completed", base=base)
        self.assertEqual((code, result["code"], result["changed"]), (200, "ACKNOWLEDGED", False))
        self.assertEqual(self.state()["status"]["revision"], advanced,
                         "a convergent edit advances nothing")

    def test_a_status_conflict_leaves_every_other_field_writable(self):
        """Status shares a displayed card with Year, and nothing else. A
        decision pending on one must not freeze the other."""
        self.db.update_work_metadata(self.work, {"status": "Planned"})
        self.assertEqual(self.send("status", "Paused", base=0)[1]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.send("year", "1998", base=0)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.send("doi", "10.1/x", base=0)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.value("status"), "Planned")

    def test_creation_does_not_manufacture_a_status_revision(self):
        """A revision records a CHANGE to an existing synchronization
        aggregate, not the construction of a new object."""
        created = self.db.add_work("Fresh", status="Planned")
        self.assertEqual(self.db.get_work_metadata_state(created)["fields"]["status"],
                         {"value": "Planned", "revision": 0})

    # ---- author_text: a stored value that is not necessarily the shown one ----

    def test_author_text_has_one_size_contract_on_every_path(self):
        """Before 2I.1 the server accepted any length and the browser's durable
        envelope decided -- so the same value was savable online and impossible
        offline, which is the split contract this architecture exists to
        remove. The limit is now a PRKS field contract, enforced identically by
        the sync handler and the ordinary PATCH."""
        limit = meta.MAX_AUTHOR_TEXT_UTF8_BYTES
        self.assertEqual(limit, 64 * 1024)
        self.assertEqual(meta.SYNCED_FIELDS["author_text"], limit)
        self.assertIn("author_text", meta.BYTE_LIMITED_FIELDS)

        for size, ok in ((10 * 1024, True), (limit, True), (limit + 1, False)):
            with self.subTest(bytes=size):
                self.assertIs(meta.is_valid_field_value("author_text", "x" * size), ok)

        # Exactly at the limit is legitimate, and reaches the column.
        code, result = self.send("author_text", "a" * limit)
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(len(self.value("author_text")), limit)
        # One byte over is refused by BOTH paths, and changes nothing.
        self.assertEqual(sync_protocol.process_operation(
            self.db, self.op("author_text", "a" * (limit + 1),
                             base=result["server_revision"])),
            (400, {"code": "INVALID_ENVELOPE"}))
        with self.assertRaises(ValueError):
            self.db.update_work_metadata(self.work, {"author_text": "a" * (limit + 1)})
        self.assertEqual(len(self.value("author_text")), limit)

    def test_the_author_text_limit_counts_utf8_bytes_not_characters(self):
        """A limit documented in bytes but enforced in characters does not
        exist for the users most likely to reach it. Every CJK character is
        three UTF-8 bytes, so a value well under the limit by `len()` can be
        well over it in storage."""
        limit = meta.MAX_AUTHOR_TEXT_UTF8_BYTES
        over = "漢" * 30000          # 30k characters, 90k bytes
        self.assertLess(len(over), limit)
        self.assertGreater(len(over.encode("utf-8")), limit)
        self.assertFalse(meta.is_valid_field_value("author_text", over))
        self.assertEqual(sync_protocol.process_operation(
            self.db, self.op("author_text", over)), (400, {"code": "INVALID_ENVELOPE"}))
        # And a CJK value that genuinely fits is accepted.
        fits = "漢" * 20000          # 60k bytes
        self.assertLessEqual(len(fits.encode("utf-8")), limit)
        self.assertEqual(self.send("author_text", fits)[1]["code"], "ACKNOWLEDGED")

    def test_whitespace_is_not_stripped_server_side(self):
        """It never was. The EDITOR trims before sending, exactly as it did
        through the old PATCH, so that rule changed location without changing
        meaning."""
        self.assertEqual(self.send("author_text", "  Jane  ")[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.value("author_text"), "  Jane  ")

    def test_a_large_author_text_acknowledgement_stays_out_of_the_ledger(self):
        """`sync_operations.result_json` has no retention policy: whatever goes
        in stays for the life of the library. Echoing the value back would make
        every edit a permanent second copy of it."""
        value = "Q" * (32 * 1024)
        code, result = self.send("author_text", value)
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["value_omitted"])
        self.assertNotIn("value", result)
        stored = self.db.execute_query(
            "SELECT result_json FROM sync_operations "
            "WHERE operation_type = 'SET_WORK_METADATA_FIELD'")[0]["result_json"]
        self.assertNotIn(value, stored)
        self.assertLess(len(stored), 1024, "the ledger row stays small")
        self.assertEqual(self.value("author_text"), value, "while the column has it in full")

    def test_a_large_author_text_conflict_fits_the_durable_result_bound(self):
        """The browser stores a terminal result in the operation row and bounds
        it to 2 KB. Two 64-KiB values in one conflict would mean the client
        could not store the conflict at all -- the operation would fail to
        settle rather than reach the user, which is worse than either value
        winning."""
        import json as _json
        server_value = "S" * (40 * 1024)
        self.db.update_work_metadata(self.work, {"author_text": server_value})
        code, result = self.send("author_text", "D" * (40 * 1024), base=0)
        self.assertEqual((code, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertNotIn("current_value", result)
        self.assertNotIn("requested_value", result)
        self.assertEqual(result["current_bytes"], len(server_value.encode("utf-8")))
        self.assertEqual(result["requested_bytes"], 40 * 1024)
        self.assertLessEqual(len(result["current_preview"]), meta.CONFLICT_PREVIEW_CHARS)
        self.assertLessEqual(len(_json.dumps(result).encode("utf-8")), 2048,
                             "the whole result must fit the durable bound")

    # ---- source_url: provenance, guarded at the mutation boundary (2N) ----

    def test_provenance_url_is_field_scoped_only_where_it_is_provenance(self):
        """On a Work whose kind is explicitly video, this column is one
        spelling of an identity spanning `source_kind`, `provider` and
        `provider_id` -- and `provider_id` outranks it, so changing the column
        alone would leave the stored URL naming one video while the viewer
        plays another. The guard lives on the MUTATION BOUNDARY, because a
        guard that lives only in a form is not a contract."""
        self.assertEqual(meta.FIELD_KIND_GUARDS, {"source_url": "video"})
        pdf = self.db.add_work("Paper", source_kind="pdf", file_path="/api/pdfs/x.pdf")
        video = self.db.add_work("Clip", source_kind="video",
                                 source_url="https://www.youtube.com/watch?v=AAA",
                                 provider="youtube", provider_id="AAA")

        # Provenance on a PDF: an ordinary field-scoped edit.
        envelope = self.op("source_url", "https://example.com/paper")
        envelope["entity_id"] = pdf
        code, result = sync_protocol.process_operation(self.db, envelope)
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(self.db.get_work(pdf)["source_url"], "https://example.com/paper")

        # The same operation on a video is refused, terminally.
        envelope = self.op("source_url", "https://www.youtube.com/watch?v=BBB")
        envelope["entity_id"] = video
        code, result = sync_protocol.process_operation(self.db, envelope)
        self.assertEqual((code, result["code"]), (409, "WRONG_OPERATION_FOR_SOURCE"))
        self.assertNotIn("current_value", result, "there is nothing to choose between")
        work = self.db.get_work(video)
        self.assertEqual(work["source_url"], "https://www.youtube.com/watch?v=AAA")
        self.assertEqual(work["provider_id"], "AAA", "identity is untouched")

        # ...and so is the ordinary PATCH, whichever path a client uses.
        with self.assertRaises(ValueError):
            self.db.update_work_metadata(video, {"source_url": "https://x/B"})
        self.assertEqual(self.db.get_work(video)["source_url"],
                         "https://www.youtube.com/watch?v=AAA")
        self.db.update_work_metadata(pdf, {"source_url": "https://example.com/other"})
        self.assertEqual(self.db.get_work(pdf)["source_url"], "https://example.com/other")

    def test_provenance_url_is_byte_limited_like_the_other_large_scalars(self):
        self.assertEqual(meta.MAX_SOURCE_URL_UTF8_BYTES, 64 * 1024)
        self.assertIn("source_url", meta.BYTE_LIMITED_FIELDS)
        code, result = self.send("source_url", "https://example.com/" + "a" * 1000)
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["value_omitted"])
        self.assertEqual(self.state()["source_url"], {"revision": 1})

    # ---- title: the widest scalar (2M) ----

    def test_title_has_one_size_contract_and_a_compact_acknowledgement(self):
        """Title is byte-limited, so the compact ACK, the revision-only
        projection entry and the bounded conflict all follow from registry
        membership rather than from Title-specific code."""
        self.assertEqual(meta.MAX_TITLE_UTF8_BYTES, 64 * 1024)
        self.assertIn("title", meta.BYTE_LIMITED_FIELDS)
        self.assertTrue(meta.is_valid_field_value("title", "x" * (64 * 1024)))
        self.assertFalse(meta.is_valid_field_value("title", "x" * (64 * 1024 + 1)))
        # Bytes, not characters.
        self.assertFalse(meta.is_valid_field_value("title", "\u65e5" * 30000))

        code, result = self.send("title", "A Renamed Work")
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["value_omitted"])
        self.assertNotIn("value", result)
        self.assertEqual(self.value("title"), "A Renamed Work")
        # The projection carries the revision alone; the Work record has the value.
        self.assertEqual(self.state()["title"], {"revision": 1})
        self.assertEqual(self.db.get_work(self.work)["title"], "A Renamed Work")

    def test_a_large_title_conflict_is_bounded(self):
        self.db.update_work_metadata(self.work, {"title": "S" * 40000})
        code, result = self.send("title", "D" * 40000, base=0)
        self.assertEqual((code, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertNotIn("current_value", result)
        self.assertEqual(result["current_bytes"], 40000)
        self.assertLessEqual(self.durable_bytes(result), meta.MAX_DURABLE_RESULT_BYTES)

    def test_title_revisions_and_independence(self):
        self.db.update_work_metadata(self.work, {"title": "First"})
        base = self.state()["title"]["revision"]
        self.assertEqual(self.send("title", "First", base=base)[1]["changed"], False)
        self.assertEqual(self.send("title", "Second", base=base)[1]["changed"], True)
        self.db.update_work_metadata(self.work, {"title": "Third"})
        self.assertEqual(self.send("title", "Third", base=base)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.send("title", "Mine", base=base)[1]["code"], "REVISION_CONFLICT")
        # A Title conflict blocks nothing else.
        self.assertEqual(self.send("doi", "10.1/x", base=0)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.send("doc_type", "book", base=0)[1]["code"], "ACKNOWLEDGED")

    def test_search_finds_a_renamed_work_after_synchronization(self):
        """`title` is an FTS column kept current by the trigger on `works`, so
        a synchronized write needs no manual index maintenance -- worth proving
        against the real engine rather than assuming."""
        self.db.update_work_metadata(self.work, {"title": "Alpha Treatise"})
        self.assertIn(self.work, [r["id"] for r in self.db.search_works("Alpha Treatise")])
        base = self.state()["title"]["revision"]
        self.assertEqual(self.send("title", "Beta Treatise", base=base)[1]["code"],
                         "ACKNOWLEDGED")
        self.assertIn(self.work, [r["id"] for r in self.db.search_works("Beta Treatise")])
        self.assertNotIn(self.work, [r["id"] for r in self.db.search_works("Alpha Treatise")])

    def test_creation_does_not_manufacture_a_title_revision(self):
        created = self.db.add_work("Fresh Title")
        self.assertEqual(self.db.get_work_metadata_state(created)["fields"]["title"],
                         {"revision": 0})
        self.assertEqual(self.db.get_work(created)["title"], "Fresh Title")

    # ---- doc_type: normalized by PATCH, canonical on the wire (2L) ----

    def test_doc_type_normalizes_on_patch_and_is_strict_on_the_wire(self):
        """Two paths, one stored result, and neither changes behaviour the
        product already had. PATCH has always turned an unrecognized type into
        "misc" rather than refusing it, so that stays. The WIRE is strict,
        because the editor's control offers nothing else -- and because an
        acknowledgement must echo the value the operation carried, which a
        silently rewritten one could not."""
        self.assertEqual(sorted(meta.DOC_TYPE_SET), sorted(meta.DOC_TYPES))
        self.assertIn("misc", meta.DOC_TYPE_SET)
        self.assertEqual(meta.normalize_doc_type("BOOK"), "book")
        self.assertEqual(meta.normalize_doc_type("  Article "), "article")
        for unknown in ("bogus", "", None, "  ", 7):
            self.assertEqual(meta.normalize_doc_type(unknown), "misc", repr(unknown))

        # PATCH normalizes.
        self.db.update_work_metadata(self.work, {"doc_type": "BOOK"})
        self.assertEqual(self.value("doc_type"), "book")
        self.db.update_work_metadata(self.work, {"doc_type": "nonsense"})
        self.assertEqual(self.value("doc_type"), "misc")
        # The wire refuses.
        for bad in ("BOOK", "bogus", "", "  article"):
            with self.subTest(wire=bad):
                self.assertEqual(sync_protocol.process_operation(
                    self.db, self.op("doc_type", bad)), (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.value("doc_type"), "misc", "no refusal reached the column")

    def test_doc_type_revisions_behave_like_every_other_scalar(self):
        self.db.update_work_metadata(self.work, {"doc_type": "article"})
        base = self.state()["doc_type"]["revision"]
        code, result = self.send("doc_type", "book", base=base)
        self.assertEqual((code, result["code"], result["changed"]), (200, "ACKNOWLEDGED", True))
        self.assertEqual(result["value"], "book", "the ACK echoes what was sent")
        self.assertEqual(self.state()["doc_type"]["revision"], base + 1)
        # A no-op advances nothing.
        self.assertEqual(self.send("doc_type", "book", base=base + 1)[1]["changed"], False)
        self.assertEqual(self.state()["doc_type"]["revision"], base + 1)
        # Stale + same desired state converges; stale + different conflicts.
        self.db.update_work_metadata(self.work, {"doc_type": "online"})
        stale = base
        self.assertEqual(self.send("doc_type", "online", base=stale)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.send("doc_type", "manual", base=stale)[1]["code"],
                         "REVISION_CONFLICT")
        # And it does not block another field.
        self.assertEqual(self.send("doi", "10.1/x", base=0)[1]["code"], "ACKNOWLEDGED")

    def test_creation_does_not_manufacture_a_doc_type_revision(self):
        created = self.db.add_work("Fresh", doc_type="book")
        self.assertEqual(self.db.get_work_metadata_state(created)["fields"]["doc_type"],
                         {"value": "book", "revision": 0})

    # ---- thumb_page: the wire is not the column (2J) ----

    def column_type(self, field):
        return self.db.execute_query(
            "SELECT typeof(%s) AS t FROM works WHERE id = ?" % field, (self.work,))[0]["t"]

    def test_the_thumb_page_codec_accepts_only_page_numbers(self):
        """Rejecting a value is not the same as clearing it. `""` is the one
        spelling of "no explicit page"; everything else either parses as a page
        or is refused outright -- the bug an uninterpretable Published Date
        used to have, where refusing to guess was indistinguishable from being
        told to delete."""
        codec = meta.codec_for("thumb_page")
        for good, expected in (("", None), ("1", 1), ("3", 3), ("003", 3),
                               (" 3 ", 3), ("10", 10), ("  ", None)):
            with self.subTest(wire=good):
                self.assertTrue(codec.is_valid_wire(good))
                self.assertEqual(codec.to_database(good), expected)
        for bad in ("0", "-1", "1.5", "abc", "3abc", "+3", "\uff13", "1_0", "٣", "1e3"):
            with self.subTest(wire=bad):
                self.assertFalse(codec.is_valid_wire(bad), bad)
                # The validator and the converter are ONE rule: a spelling the
                # validator refuses must not be quietly converted by the other.
                self.assertIsNone(codec.to_database(bad), bad)
        # Column/entity -> wire.
        self.assertEqual(codec.to_wire(None), "")
        self.assertEqual(codec.to_wire(3), "3")
        self.assertEqual(codec.to_wire(0), "")
        self.assertEqual(codec.to_wire(True), "", "a bool is not a page number")

    def test_an_invalid_thumb_page_never_reaches_the_column(self):
        for bad in ("0", "-1", "1.5", "abc", "+3"):
            with self.subTest(wire=bad):
                self.assertEqual(sync_protocol.process_operation(
                    self.db, self.op("thumb_page", bad)), (400, {"code": "INVALID_ENVELOPE"}))
                with self.assertRaises(ValueError):
                    self.db.update_work_metadata(self.work, {"thumb_page": bad})
        self.assertIsNone(self.value("thumb_page"))
        self.assertEqual(self.db.execute_query("SELECT * FROM sync_operations"), [])

    def test_the_column_stays_typed_whichever_path_writes_it(self):
        """The column is INTEGER NULL and every read model is validated as
        `integer | null`. A wire string stored there would not merely look
        odd -- it would make each cached row fail its own shape validator."""
        self.assertEqual(self.send("thumb_page", "4")[1]["code"], "ACKNOWLEDGED")
        self.assertEqual((self.value("thumb_page"), self.column_type("thumb_page")),
                         (4, "integer"))
        self.db.update_work_metadata(self.work, {"thumb_page": 7})
        self.assertEqual((self.value("thumb_page"), self.column_type("thumb_page")),
                         (7, "integer"))
        # PATCH accepts the native form and the wire form identically.
        self.db.update_work_metadata(self.work, {"thumb_page": "9"})
        self.assertEqual((self.value("thumb_page"), self.column_type("thumb_page")),
                         (9, "integer"))
        # Clearing stores NULL, never "".
        self.db.update_work_metadata(self.work, {"thumb_page": None})
        self.assertEqual((self.value("thumb_page"), self.column_type("thumb_page")),
                         (None, "null"))

    def test_representation_differences_are_not_changes(self):
        """A revision records a change of STATE. Column 3, wire "3" and wire
        "003" are one state, and advancing for a difference in spelling would
        manufacture staleness for every device already holding the value."""
        self.db.update_work_metadata(self.work, {"thumb_page": 3})
        after_first = self.state()["thumb_page"]["revision"]
        self.assertEqual(after_first, 1)
        for same in ("3", "003", " 3 ", 3):
            with self.subTest(value=repr(same)):
                if isinstance(same, str):
                    code, result = self.send("thumb_page", same, base=after_first)
                    self.assertEqual((code, result["changed"]), (200, False))
                else:
                    self.db.update_work_metadata(self.work, {"thumb_page": same})
                self.assertEqual(self.state()["thumb_page"]["revision"], after_first)
        # NULL and "" are likewise one state.
        self.db.update_work_metadata(self.work, {"thumb_page": None})
        cleared = self.state()["thumb_page"]["revision"]
        self.assertEqual(self.send("thumb_page", "", base=cleared)[1]["changed"], False)
        self.assertEqual(self.state()["thumb_page"]["revision"], cleared)

    def test_a_stale_but_convergent_typed_value_is_not_a_conflict(self):
        """Two devices that chose the same page have not disagreed, whichever
        spelling each of them used to say so."""
        self.db.update_work_metadata(self.work, {"thumb_page": 2})
        base = self.state()["thumb_page"]["revision"]
        self.db.update_work_metadata(self.work, {"thumb_page": 5})   # someone else
        for spelling in ("5", "005", " 5 "):
            with self.subTest(wire=spelling):
                code, result = self.send("thumb_page", spelling, base=base)
                self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
                self.assertEqual(result["changed"], False)
        # A genuinely different page still conflicts.
        code, result = self.send("thumb_page", "7", base=base)
        self.assertEqual((code, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], "5", "reported in the WIRE form")
        self.assertEqual(result["requested_value"], "7")
        # ...including a clear against a stored page.
        self.assertEqual(self.send("thumb_page", "", base=base)[1]["code"],
                         "REVISION_CONFLICT")

    def test_metadata_state_reports_the_wire_form_and_the_work_the_typed_one(self):
        """The two projections describe different things. `metadata-state` is
        synchronization bookkeeping: its `value` is what a base revision was
        observed against, in the representation the protocol uses. The Work
        record is the entity. `thumb_page` is where that distinction stops
        being invisible."""
        self.send("thumb_page", "6")
        self.assertEqual(self.state()["thumb_page"], {"value": "6", "revision": 1})
        self.assertEqual(self.db.get_work(self.work)["thumb_page"], 6)
        self.send("thumb_page", "", base=1)
        self.assertEqual(self.state()["thumb_page"], {"value": "", "revision": 2})
        self.assertIsNone(self.db.get_work(self.work)["thumb_page"])

    def test_creation_does_not_manufacture_a_thumb_page_revision(self):
        created = self.db.add_work("Fresh", thumb_page=4)
        state = self.db.get_work_metadata_state(created)["fields"]["thumb_page"]
        self.assertEqual(state, {"value": "4", "revision": 0})
        self.assertEqual(self.db.get_work(created)["thumb_page"], 4)

    # ---- the durable result bound (2I.2) ----

    def durable_bytes(self, result):
        """Exactly what the browser will measure: `JSON.stringify` does not
        escape non-ASCII, so `ensure_ascii=False` is what makes this the same
        number the client computes."""
        import json as _json
        return len(_json.dumps(result, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8"))

    def conflict_over(self, field, server_value, device_value="D" * 64):
        self.db.update_work_metadata(self.work, {field: server_value})
        code, result = self.send(field, device_value, base=0)
        self.assertEqual((code, result["code"]), (409, "REVISION_CONFLICT"))
        return result

    def test_every_conflict_result_fits_what_the_browser_can_store(self):
        """A result the client cannot store is worse than either value winning.
        The durable write fails, the coordinator reads that as a failed sync,
        and the operation returns to pending -- forever, because the same
        oversized result comes back every retry. The user never reaches the
        conflict UI and has no way to resolve anything.

        A character count is not a byte count and neither is a serialized size:
        one control character is one code point, one byte in the column and SIX
        bytes as `\u0001` in JSON.
        """
        limit = meta.MAX_DURABLE_RESULT_BYTES
        self.assertEqual(limit, 2048)
        cases = [
            # (field, server value, what made it overflow before)
            ("author_text", "\x00" * 5000, "NUL characters, six bytes each"),
            ("author_text", "\x01\x1f" * 2500, "other C0 controls"),
            ("author_text", "\x7f" * 5000, "DEL, which is NOT escaped"),
            ("author_text", "\U0001f9ea" * 5000, "astral characters"),
            ("author_text", "日" * 5000, "CJK"),
            ("author_text", '"\\' * 2500, "quotes and backslashes"),
            ("author_text", "e\u0301" * 2500, "combining marks"),
            ("abstract", "\x01" * 20000, "a byte-limited field at scale"),
            # A small scalar carries BOTH values in full, so its worst case is
            # roughly twice as bad: 500 code points x 6 bytes x 2.
            ("journal", "\x01" * meta.SYNCED_FIELDS["journal"], "a small scalar"),
            ("publisher", "\x02" * meta.SYNCED_FIELDS["publisher"], "a small scalar"),
            ("doi", "\x03" * meta.SYNCED_FIELDS["doi"], "a small scalar"),
        ]
        for field, server_value, why in cases:
            with self.subTest(field=field, why=why):
                result = self.conflict_over(field, server_value)
                self.assertLessEqual(self.durable_bytes(result), limit, why)

    def test_a_small_scalar_conflict_degrades_to_previews_only_when_it_must(self):
        """Both values are more useful than a preview -- the client can offer
        "Use server" without a second request -- so the full shape is kept
        whenever it fits, and given up only when keeping it would mean the
        conflict could not be stored at all."""
        ordinary = self.conflict_over("journal", "Nature Physics")
        self.assertEqual(ordinary["current_value"], "Nature Physics")
        self.assertEqual(ordinary["requested_value"], "D" * 64)
        self.assertNotIn("current_preview", ordinary)

        pathological = self.conflict_over("journal", "\x01" * 500)
        self.assertNotIn("current_value", pathological)
        self.assertNotIn("requested_value", pathological)
        self.assertIn("current_preview", pathological)
        self.assertEqual(pathological["current_bytes"], 500)
        self.assertLessEqual(self.durable_bytes(pathological),
                             meta.MAX_DURABLE_RESULT_BYTES)
        # The two shapes are never mixed: which one to trust would be undecided.
        self.assertFalse({"current_value", "current_preview"} <= set(pathological))

    def test_the_preview_is_shortened_only_as_far_as_it_has_to_be(self):
        """An ASCII preview keeps its full character budget; only input that
        actually serializes large is cut, and then to the longest prefix that
        still fits."""
        ascii_case = self.conflict_over("author_text", "A" * 5000)
        self.assertEqual(len(ascii_case["current_preview"]), meta.CONFLICT_PREVIEW_CHARS)

        control = self.conflict_over("author_text", "\x01" * 5000)
        self.assertLess(len(control["current_preview"]), meta.CONFLICT_PREVIEW_CHARS)
        self.assertGreater(len(control["current_preview"]), 0,
                           "something of the server's value still reaches the user")
        # Longest prefix that fits: one more code point would not.
        self.assertLessEqual(self.durable_bytes(control), meta.MAX_DURABLE_RESULT_BYTES)
        oversized = dict(control)
        oversized["current_preview"] = control["current_preview"] + "\x01"
        self.assertGreater(self.durable_bytes(oversized), meta.MAX_DURABLE_RESULT_BYTES)

    def test_a_surrogate_pair_is_never_split_by_the_preview_bound(self):
        """Half a pair serializes to six bytes and renders as a replacement
        glyph. Cutting by code points makes that unrepresentable."""
        result = self.conflict_over("author_text", "\U0001f9ea" * 5000)
        preview = result["current_preview"]
        self.assertTrue(preview)
        for index, ch in enumerate(preview):
            self.assertFalse(0xD800 <= ord(ch) <= 0xDFFF,
                             "lone surrogate at %d" % index)
        self.assertEqual(preview, "\U0001f9ea" * len(preview),
                         "the prefix is whole characters of the server's value")

    def test_the_server_measures_at_least_what_the_browser_stores(self):
        """The client's handler projects the answer down to the allowlisted
        keys before storing it, so `work_id` and `field` never reach durable
        storage. The server measures the whole answer including them, which
        makes its bound CONSERVATIVE -- it truncates slightly sooner than
        strictly necessary. That direction is the safe one, and it is a
        deliberate choice rather than an accident: the alternative is teaching
        the server the client's allowlist, which is a duplication that could
        drift silently in the unsafe direction."""
        import json as _json
        stored_keys = ("code", "current_revision", "current_value", "requested_value",
                       "current_preview", "current_bytes", "requested_bytes")
        result = self.conflict_over("author_text", "\x01" * 5000)
        stored = {k: v for k, v in result.items() if k in stored_keys}
        self.assertLess(len(stored), len(result), "the server says more than is stored")
        stored_bytes = len(_json.dumps(stored, ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8"))
        self.assertLessEqual(stored_bytes, meta.serialized_result_bytes(result))
        self.assertLessEqual(stored_bytes, meta.MAX_DURABLE_RESULT_BYTES)

    def test_the_server_and_the_browser_agree_on_the_result_bound(self):
        """Two numbers that drift would mean the server believing it sent
        something storable and the client refusing it."""
        store = (pathlib.Path(__file__).resolve().parents[1] /
                 "frontend" / "js" / "local-store.js").read_text(encoding="utf-8")
        self.assertIn("const MAX_RESULT_BYTES = %d;" % meta.MAX_DURABLE_RESULT_BYTES, store)

    def test_metadata_state_carries_the_author_text_revision_only(self):
        """The Work record already carries the acknowledged value; duplicating
        up to 64 KiB into a second cached projection would double what this
        endpoint sends, what IndexedDB stores and what every re-read costs."""
        self.send("author_text", "A" * 5000)
        entry = self.state()["author_text"]
        self.assertEqual(entry, {"revision": 1})
        self.assertNotIn("value", entry)
        # The observed base is reconstructed from the Work record instead.
        self.assertEqual(self.value("author_text"), "A" * 5000)
        # Small scalars still carry their value inline.
        self.assertIn("value", self.state()["doi"])

    def test_author_text_conflicts_are_field_scoped_like_any_other_scalar(self):
        self.db.update_work_metadata(self.work, {"author_text": "Alpha"})
        base = self.state()["author_text"]["revision"]
        self.db.update_work_metadata(self.work, {"author_text": "Bob"})
        code, result = self.send("author_text", "Alice", base=base)
        self.assertEqual((code, result["code"]), (409, "REVISION_CONFLICT"))
        # Byte-limited, so the disagreement is reported as a bounded preview.
        self.assertEqual(result["current_preview"], "Bob")
        # Convergence is not a conflict.
        self.assertEqual(self.send("author_text", "Bob", base=base)[1]["code"], "ACKNOWLEDGED")
        # And nothing else is blocked by it.
        self.assertEqual(self.send("doi", "10.1/x", base=0)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual(self.send("status", "Paused", base=0)[1]["code"], "ACKNOWLEDGED")

    def test_creation_does_not_manufacture_an_author_text_revision(self):
        created = self.db.add_work("Fresh", author_text="Jane Smith")
        self.assertEqual(self.db.get_work_metadata_state(created)["fields"]["author_text"],
                         {"revision": 0})
        self.assertEqual(self.db.get_work(created)["author_text"], "Jane Smith",
                         "the value lives on the Work record, not in the projection")

    def test_patch_and_sync_are_the_only_existing_work_author_text_writers(self):
        """The Phase A audit, kept honest. Every OTHER path that sets
        `author_text` -- the Processing import, the video oEmbed fill --
        CREATES a Work, and construction is not a change to an existing
        synchronization aggregate. If a path that mutates an existing Work is
        ever added, it must route through `set_field_on_conn` and this test
        should be the thing that notices."""
        import pathlib as _pathlib
        import re
        backend_dir = _pathlib.Path(__file__).resolve().parents[1] / "backend"
        offenders = []
        pattern = re.compile(r"UPDATE\s+works\s+SET\s+([^\n]*)", re.IGNORECASE)
        for path in sorted(backend_dir.rglob("*.py")):
            if path.name == "work_metadata_sync.py":
                continue
            for clause in pattern.findall(path.read_text(encoding="utf-8")):
                if re.search(r"\bauthor_text\b\s*=", clause):
                    offenders.append("%s: %s" % (path.name, clause.strip()))
        self.assertEqual(offenders, [])
        # The oEmbed fill reaches add_work, never an update.
        server = (backend_dir / "server.py").read_text(encoding="utf-8")
        at = server.index("If author_text not provided, fill from oEmbed author_name.")
        self.assertIn("db.add_work(", server[at: at + 2000])
        self.assertNotIn("update_work_metadata", server[at: at + 2000])

    def test_search_finds_the_new_author_text_after_synchronization(self):
        """`author_text` is an FTS column, kept current by an AFTER UPDATE
        trigger on `works`. The synchronized write is an ordinary UPDATE, so
        this needs no manual index maintenance -- but it is worth proving
        against the real engine rather than assuming it."""
        self.db.update_work_metadata(self.work, {"author_text": "Alpha Person"})
        found = [row["id"] for row in self.db.search_works("Alpha Person")]
        self.assertIn(self.work, found)

        base = self.state()["author_text"]["revision"]
        self.assertEqual(self.send("author_text", "Beta Person", base=base)[1]["code"],
                         "ACKNOWLEDGED")
        self.assertIn(self.work, [row["id"] for row in self.db.search_works("Beta Person")],
                      "the synchronized value is searchable")
        self.assertNotIn(self.work, [row["id"] for row in self.db.search_works("Alpha Person")],
                         "and the value it replaced no longer matches via author_text")

    def test_pending_author_text_is_not_a_server_search_term(self):
        """The documented 2I boundary: the server decides result MEMBERSHIP
        and knows nothing about a pending local value. The client overlays
        effective fields for RENDERING only. Making the server discover an
        unsent value would need a local search index, which is not this
        milestone."""
        self.db.update_work_metadata(self.work, {"author_text": "Alpha Person"})
        # Nothing has been sent, so nothing about "Beta" can be known here.
        self.assertEqual(self.db.search_works("Beta Person"), [])
        self.assertIn(self.work, [row["id"] for row in self.db.search_works("Alpha Person")],
                      "and the Work is still discoverable under its acknowledged value")

    def test_no_other_backend_statement_writes_a_synchronized_column(self):
        """The audit behind the test above, kept honest as the schema grows: a
        second writer that skips `set_field_on_conn` would reintroduce the
        defect for one field while every field-driven test stayed green."""
        import pathlib as _pathlib
        import re
        backend_dir = _pathlib.Path(__file__).resolve().parents[1] / "backend"
        # These are the only sanctioned writers: the synchronization handler
        # itself, and the PATCH path, which routes synchronized fields through
        # it. Creation is exempt -- a brand-new Work has no revision to overtake.
        # Schema MIGRATIONS are not a runtime mutation path: they run once at
        # startup, before the server accepts connections, and backfill columns
        # that had no value at all. A revision records a change made by a
        # device; nobody held one for a row that was empty before the upgrade.
        # `work_source_sync.py` owns the SOURCE aggregate, whose columns
        # overlap the field registry at `source_url`. It is revision-aware in
        # its own scope, which is precisely why the field-scoped path refuses
        # to touch a video Work's URL.
        sanctioned = {"work_metadata_sync.py", "db_migrations.py", "work_source_sync.py"}
        pattern = re.compile(r"UPDATE\s+works\s+SET\s+([^\n]*)", re.IGNORECASE)
        offenders = []
        for path in sorted(backend_dir.rglob("*.py")):
            if path.name in sanctioned:
                continue
            for clause in pattern.findall(path.read_text(encoding="utf-8")):
                for field in meta.SYNCED_FIELDS:
                    if re.search(r"\b%s\b\s*=" % re.escape(field), clause):
                        offenders.append("%s: %s" % (path.name, clause.strip()))
        # db_manager builds its SET clause from a variable, which this cannot
        # read; it is covered by the field-driven revision test above.
        self.assertEqual(offenders, [], "unsanctioned writes to synchronized columns")

    def test_unrelated_metadata_never_advances_a_field_revision(self):
        self.send("doi", "10.1/keep")
        before = self.state()
        self.db.update_work_metadata(self.work, {
            "text_content": "Notes"})
        self.assertEqual(self.state(), before)

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

    # ---- the cross-projection dependency this milestone exists for ----

    def test_recently_added_carries_publisher_for_its_local_filter(self):
        """The pin behind `FIELD_PROJECTIONS`. Recently Added filters locally
        over Publisher, so the value is part of that cached projection's
        semantics even though no Work card renders it -- being invisible is not
        the same as being unused. If this projection ever stops selecting it,
        the client overlay built on top becomes dead weight rather than a bug
        anyone would notice."""
        self.db.update_work_metadata(self.work, {"publisher": "Fixture Press"})
        rows = self.db.get_recently_added_browse()
        row = next(r for r in rows if r["id"] == self.work)
        self.assertEqual(row["publisher"], "Fixture Press")
        self.assertEqual(meta.FIELD_PROJECTIONS["publisher"], ("recently-added",))

    def test_the_projection_map_names_every_dependency(self):
        """Location is the control case: a detail-only field must not drag an
        unrelated cached read model into its reconciliation. Publisher copies a
        value into one list; Abstract DERIVES one; Year and Published Date are
        on every Work card and so reach all three."""
        self.assertEqual(sorted(meta.FIELD_PROJECTIONS),
                         ["abstract", "author_text", "doc_type", "published_date",
                          "publisher", "source_url", "status", "thumb_page", "title",
                          "year"])
        self.assertEqual(meta.FIELD_PROJECTIONS["abstract"], ("works-browse",))
        self.assertEqual(meta.FIELD_PROJECTIONS["publisher"], ("recently-added",))
        # Status is the strongest case for reaching all three: it does not only
        # change what a card SAYS, it changes which Progress group the card
        # belongs to, and Progress reads `works-browse:index`.
        for field in ("year", "published_date", "status", "author_text", "thumb_page",
                      "doc_type", "title", "source_url"):
            self.assertEqual(meta.FIELD_PROJECTIONS[field],
                             ("works-browse", "recent", "recently-added"), field)
        for field in meta.SYNCED_FIELDS:
            if field in ("publisher", "abstract", "year", "published_date", "status",
                         "author_text", "thumb_page", "doc_type", "title", "source_url"):
                continue
            self.assertNotIn(field, meta.FIELD_PROJECTIONS, field)

    def test_embedded_summary_fields_are_named(self):
        """Folder, Person and Playlist details embed Work summaries. A field
        those rows carry has to reach them too -- rendering AND local search."""
        self.assertEqual(sorted(meta.SUMMARY_FIELDS),
                         ["author_text", "doc_type", "published_date", "publisher",
                          "source_url", "status", "thumb_page", "title", "year"])
        self.assertEqual(meta.SUMMARY_ENTITY_KINDS, ("folder", "person", "playlist"))
        for field in meta.SUMMARY_FIELDS:
            self.assertIn(field, meta.SYNCED_FIELDS, field)

    def test_year_and_published_date_are_independent_conflict_units(self):
        """They interact on screen -- a card falls back from Year to the date's
        year -- but that is a RENDERING rule. Conflict is about canonical field
        ownership, so editing one must never collide with the other."""
        self.send("year", "1998")
        self.send("published_date", "2020-05-01")
        state = self.state()
        self.assertEqual(state["year"], {"value": "1998", "revision": 1})
        self.assertEqual(state["published_date"], {"value": "2020-05-01", "revision": 1})
        # A device stale on Published Date still applies its Year edit cleanly.
        self.send("published_date", "2021-06-02", base=1)
        self.assertEqual(self.send("year", "2001", base=1)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual((self.value("year"), self.value("published_date")),
                         ("2001", "2021-06-02"))
        # ...and only a same-field disagreement conflicts.
        self.assertEqual(self.send("year", "1975", base=1)[1]["code"], "REVISION_CONFLICT")

    def test_published_date_is_stored_in_one_canonical_representation(self):
        """The editor shows dd/mm/yyyy; the wire and the column are ISO. The
        sync path must never introduce a second spelling."""
        self.send("published_date", "2026-09-12")
        self.assertEqual(self.value("published_date"), "2026-09-12")
        # Clearing is the empty string, like every other field.
        self.assertTrue(self.send("published_date", "", base=1)[1]["changed"])
        self.assertEqual(self.value("published_date"), "")
        self.assertEqual(self.state()["published_date"], {"value": "", "revision": 2})

    def test_publisher_and_location_behave_like_every_other_field(self):
        """No new machinery: the expanded registry reuses the scalar path."""
        for field in ("publisher", "location"):
            with self.subTest(field=field):
                self.assertEqual(self.send(field, "first")[1]["code"], "ACKNOWLEDGED")
                self.assertEqual(self.state()[field], {"value": "first", "revision": 1})
                self.assertFalse(self.send(field, "first", base=1)[1]["changed"])
                self.assertEqual(self.send(field, "second", base=0)[1]["code"], "REVISION_CONFLICT")
                self.assertEqual(self.send(field, "second", base=1)[1]["code"], "ACKNOWLEDGED")
                self.assertEqual(self.value(field), "second")

    def test_publisher_and_location_are_independent_of_each_other(self):
        self.send("publisher", "Elsevier")
        self.send("location", "Amsterdam")
        self.send("location", "Amsterdam; Boston", base=1)
        # A device stale on Location still applies its Publisher edit cleanly.
        self.assertEqual(self.send("publisher", "Springer", base=1)[1]["code"], "ACKNOWLEDGED")
        self.assertEqual((self.value("publisher"), self.value("location")),
                         ("Springer", "Amsterdam; Boston"))

    def test_new_fields_keep_their_documented_bounds(self):
        """Ordinary PATCH imposes no length limit; the sync envelope does, so
        the bound is a deliberate, tested number rather than an accident."""
        for field in ("publisher", "location"):
            with self.subTest(field=field):
                self.assertEqual(meta.SYNCED_FIELDS[field], 500)
                limit = meta.SYNCED_FIELDS[field]
                self.assertEqual(self.send(field, "x" * limit)[0], 200)
                over = self.op(field, "x" * (limit + 1), base=1)
                self.assertEqual(sync_protocol.process_operation(self.db, over)[0], 400)

    def test_multi_place_locations_are_stored_verbatim(self):
        """PRKS joins semicolon-separated places at BibLaTeX export time and
        stores what the user typed; the sync path must not start parsing."""
        value = "Cambridge, UK; Paris; Berlin"
        self.send("location", value)
        self.assertEqual(self.value("location"), value)

    def test_the_projection_reads_only_this_works_scopes(self):
        """A library-wide scan would grow with the library while the answer
        stays nine rows, and it would hand another Work's revisions to this
        one if a scope key were ever ambiguous."""
        noisy = [self.db.add_work("Noise %d" % i) for i in range(5)]
        for work in noisy:
            self.db.update_work_metadata(work, {"doi": "10.1/%s" % work, "isbn": work})
        self.db.update_work_metadata(self.work, {"doi": "10.1/mine"})

        state = self.state()
        self.assertEqual(state["doi"], {"value": "10.1/mine", "revision": 1})
        self.assertEqual(state["isbn"], {"value": "", "revision": 0},
                         "another Work's ISBN revision must not leak in")
        for work in noisy:
            self.assertEqual(self.db.get_work_metadata_state(work)["fields"]["isbn"]["revision"], 1)

        scanned = []

        class Watched:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *args):
                if "sync_entity_revisions" in sql:
                    scanned.append(sql)
                return self._conn.execute(sql, *args)

        with self.db.connection() as conn:
            observed = meta.get_field_state_on_conn(Watched(conn), self.work)
        self.assertEqual(observed, self.db.get_work_metadata_state(self.work))
        self.assertEqual(len(scanned), 1, "one query, not one per field")
        self.assertIn("scope_id IN", scanned[0], "bounded to this Work's own scope keys")

    # ---- Abstract: one canonical limit, measured in bytes ----

    def test_abstract_limit_is_the_same_contract_on_both_paths(self):
        """If PATCH accepted an Abstract the durable queue would refuse, the
        same edit would be savable online and impossible offline -- the split
        contract that moving a field to local-first exists to remove."""
        limit = meta.MAX_ABSTRACT_UTF8_BYTES
        self.assertEqual(limit, 1024 * 1024)
        at_limit = "x" * limit
        over = "x" * (limit + 1)

        self.assertEqual(self.send("abstract", at_limit)[0], 200)
        self.assertEqual(self.value("abstract"), at_limit)
        self.assertEqual(sync_protocol.process_operation(
            self.db, self.op("abstract", over, base=1)), (400, {"code": "INVALID_ENVELOPE"}))

        self.db.update_work_metadata(self.work, {"abstract": "x" * 1000})
        with self.assertRaises(ValueError):
            self.db.update_work_metadata(self.work, {"abstract": over})
        self.assertEqual(self.value("abstract"), "x" * 1000,
                         "a refused PATCH changes nothing")

    def test_the_abstract_limit_counts_utf8_bytes_not_characters(self):
        """`len()` counts code points. A limit documented in bytes but enforced
        in characters does not exist for the users most likely to reach it."""
        limit = meta.MAX_ABSTRACT_UTF8_BYTES
        # Three bytes per character: well under the limit by character count,
        # well over it in bytes.
        multibyte = "\u65e5" * (limit // 3 + 10)
        self.assertLess(len(multibyte), limit)
        self.assertGreater(len(multibyte.encode("utf-8")), limit)
        self.assertEqual(sync_protocol.process_operation(
            self.db, self.op("abstract", multibyte)), (400, {"code": "INVALID_ENVELOPE"}))
        with self.assertRaises(ValueError):
            self.db.update_work_metadata(self.work, {"abstract": multibyte})
        # One that genuinely fits is accepted.
        fits = "\u65e5" * 1000
        self.assertEqual(self.send("abstract", fits)[0], 200)
        self.assertEqual(self.value("abstract"), fits)

    def test_the_small_scalars_keep_their_code_point_limits(self):
        """Switching them to bytes would quietly shorten every one by a factor
        of three for anyone writing CJK, which nothing here asked for."""
        self.assertNotIn("journal", meta.BYTE_LIMITED_FIELDS)
        self.assertEqual(sorted(meta.BYTE_LIMITED_FIELDS),
                         ["abstract", "author_text", "source_url", "title"])
        # Membership of BYTE_LIMITS is what makes a field byte-limited; the two
        # registries cannot drift because one is derived from the other.
        self.assertEqual(sorted(meta.BYTE_LIMITS), sorted(meta.BYTE_LIMITED_FIELDS))
        for field, limit in meta.BYTE_LIMITS.items():
            self.assertEqual(meta.SYNCED_FIELDS[field], limit, field)
        cjk = "\u65e5" * meta.SYNCED_FIELDS["journal"]
        self.assertGreater(len(cjk.encode("utf-8")), meta.SYNCED_FIELDS["journal"])
        self.assertEqual(self.send("journal", cjk)[0], 200)

    def test_abstract_uses_the_ordinary_scalar_conflict_path(self):
        self.send("abstract", "device text")
        # The projection carries Abstract's REVISION only: the Work record
        # already holds the value, and echoing a megabyte of it here would
        # double every read for something the client already has.
        self.assertEqual(self.state()["abstract"], {"revision": 1})
        self.assertEqual(self.value("abstract"), "device text")
        self.assertFalse(self.send("abstract", "device text", base=1)[1]["changed"])
        self.db.update_work_metadata(self.work, {"abstract": "server text"})
        self.assertEqual(self.state()["abstract"]["revision"], 2)
        conflict = self.send("abstract", "other text", base=1)[1]
        self.assertEqual(conflict["code"], "REVISION_CONFLICT")
        # Convergent: the same text from a stale base is not a collision.
        self.assertEqual(self.send("abstract", "server text", base=1)[1]["code"], "ACKNOWLEDGED")

    def test_an_abstract_conflict_is_small_enough_to_persist(self):
        """The durable operation row bounds a structured result to 2 KB. A
        conflict the browser cannot store is a conflict the user never sees."""
        import json as _json
        big = "s" * (64 * 1024)
        self.db.update_work_metadata(self.work, {"abstract": big})
        conflict = self.send("abstract", "m" * (64 * 1024), base=0)[1]
        self.assertEqual(conflict["code"], "REVISION_CONFLICT")
        self.assertNotIn("current_value", conflict)
        self.assertNotIn("requested_value", conflict)
        self.assertEqual(conflict["current_preview"], "s" * meta.CONFLICT_PREVIEW_CHARS)
        self.assertEqual(conflict["current_bytes"], len(big))
        self.assertEqual(conflict["requested_bytes"], 64 * 1024)
        structured = {k: v for k, v in conflict.items()
                      if k not in ("work_id", "field", "code")}
        self.assertLess(len(_json.dumps(structured).encode()), 2048,
                        "must fit the durable structured-result bound")

    def test_small_fields_still_report_their_values_in_a_conflict(self):
        self.db.update_work_metadata(self.work, {"doi": "10.1/server"})
        conflict = self.send("doi", "10.1/device", base=0)[1]
        self.assertEqual(conflict["current_value"], "10.1/server")
        self.assertEqual(conflict["requested_value"], "10.1/device")
        self.assertNotIn("current_preview", conflict)

    def test_the_ledger_never_becomes_a_second_copy_of_the_abstract(self):
        """`sync_operations` has no retention policy: whatever lands in
        `result_json` stays for the life of the library. Echoing the Abstract
        back would make every edit a permanent duplicate of the text."""
        body = "Q" * (300 * 1024)
        op = self.op("abstract", body)
        status, result = sync_protocol.process_operation(self.db, op)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["value_omitted"])
        self.assertNotIn("value", result)
        # The Work itself did receive the whole thing.
        self.assertEqual(self.value("abstract"), body)

        rows = self.db.execute_query(
            "SELECT result_json FROM sync_operations WHERE operation_type = 'SET_WORK_METADATA_FIELD'")
        self.assertEqual(len(rows), 1)
        stored = rows[0]["result_json"]
        self.assertLess(len(stored), 2048, "the ledger row stays small")
        self.assertNotIn(body[:200], stored, "the Abstract body is not in the ledger")

        # Replay returns the same compact result, and mutates nothing further.
        self.assertEqual(sync_protocol.process_operation(self.db, op), (status, result))
        self.assertEqual(self.db.execute_query(
            "SELECT result_json FROM sync_operations WHERE op_id = ?",
            (op["op_id"],))[0]["result_json"], stored)
        self.assertEqual(self.state()["abstract"], {"revision": 1})

    def test_small_fields_still_echo_their_value(self):
        result = self.send("doi", "10.1/echoed")[1]
        self.assertEqual(result["value"], "10.1/echoed")
        self.assertNotIn("value_omitted", result)

    def test_scope_keys_are_structural(self):
        self.assertNotEqual(meta.scope_key("W-a:b", "doi"), meta.scope_key("W-a", "b:doi"))
