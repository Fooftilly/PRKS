"""Durable whole-document Work notes: two independent aggregates."""
import json
import tempfile
import unittest
import uuid

from backend import research_index, research_network, sync_protocol, work_note_sync as notes
from backend.db_manager import PRKSDatabase
from backend.research_network import ResearchError, list_concepts, save_work_notes
from backend.storage.config import StorageConfig


class WorkNoteSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-notes-")
        self.addCleanup(self.tmp.cleanup)
        self.storage = StorageConfig.for_testing(self.tmp.name)
        self.db = PRKSDatabase(storage=self.storage)
        self.work = self.db.add_work("Noted work")
        self.index = research_index.PRKSResearchIndex(storage=self.storage)

    def envelope(self, operation, text, base=0, work=None, **changes):
        data = dict(op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
                    operation=operation, entity_type="work",
                    entity_id=work or self.work, payload={"text": text},
                    base_revision=base, occurred_at="2026-09-15T10:00:00Z",
                    created_at="2026-09-15T10:00:00Z", depends_on=[])
        data.update(changes)
        return data

    def send(self, operation, text, base=0, work=None):
        return sync_protocol.process_operation(
            self.db, self.envelope(operation, text, base, work))

    def send_research(self, text, base=0, work=None):
        return self.send(notes.RESEARCH_OPERATION, text, base, work)

    def send_private(self, text, base=0, work=None):
        return self.send(notes.PRIVATE_OPERATION, text, base, work)

    def state(self, work=None):
        return self.db.get_work_notes_state(work or self.work)

    def column(self, name, work=None):
        return self.db.execute_query(
            "SELECT %s AS v FROM works WHERE id = ?" % name,
            (work or self.work,))[0]["v"]

    # ---- shared canonical processing ----

    def test_ordinary_and_durable_research_writes_share_canonical_processing(self):
        """Concept auto-creation and the note body commit together, whether the
        write arrived as PATCH or as SET_WORK_RESEARCH_NOTE."""
        save_work_notes(self.db, self.work, "See [[concept:Culture Industry]].")
        names = {row["name"] for row in list_concepts(self.db)}
        self.assertEqual(names, {"Culture Industry"})
        self.assertEqual(self.column("text_content"), "See [[concept:Culture Industry]].")
        self.assertEqual(self.state()["research_note_revision"], 1)

        code, result = self.send_research(
            "Also [[concept:Mass Culture]].",
            base=self.state()["research_note_revision"])
        self.assertEqual((code, result["code"], result["changed"]),
                         (200, "ACKNOWLEDGED", True))
        names = {row["name"] for row in list_concepts(self.db)}
        self.assertEqual(names, {"Culture Industry", "Mass Culture"})
        self.assertEqual(self.column("text_content"), "Also [[concept:Mass Culture]].")

    def test_durable_research_write_still_feeds_reference_indexing(self):
        text = "See [[concept:Culture Industry]]."
        code, result = self.send_research(text)
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.index.sync_work(self.work, text, self.db)
        cid = list_concepts(self.db)[0]["id"]
        self.assertEqual(self.index.mention_count_for_concept(cid), 1)
        refs = self.index.work_research_refs(self.work, self.db)
        self.assertEqual([row["id"] for row in refs["concepts"]], [cid])

    def test_private_notes_do_not_create_concepts_or_index_mentions(self):
        self.send_private("Remind me: [[concept:Should Not Exist]].")
        self.assertEqual(list_concepts(self.db), [])
        self.index.sync_work(self.work, self.column("text_content") or "", self.db)
        self.assertEqual(self.index.work_research_refs(self.work, self.db)["concepts"], [])
        self.assertEqual(self.column("private_notes"),
                         "Remind me: [[concept:Should Not Exist]].")

    # ---- independent revisions ----

    def test_research_and_private_revisions_are_independent(self):
        self.send_research("Research A")
        self.send_private("Private A")
        state = self.state()
        self.assertEqual(state["research_note_revision"], 1)
        self.assertEqual(state["private_note_revision"], 1)

        self.send_research("Research B", base=1)
        state = self.state()
        self.assertEqual(state["research_note_revision"], 2)
        self.assertEqual(state["private_note_revision"], 1,
                         "a Research write must not advance the Private revision")
        self.assertEqual(self.column("private_notes"), "Private A")

        self.send_private("Private B", base=1)
        state = self.state()
        self.assertEqual(state["research_note_revision"], 2)
        self.assertEqual(state["private_note_revision"], 2)
        self.assertEqual(self.column("text_content"), "Research B")

    def test_ordinary_private_patch_advances_only_the_private_revision(self):
        save_work_notes(self.db, self.work, "Research via PATCH")
        self.db.update_work_metadata(self.work, {"private_notes": "Reminder via PATCH"})
        state = self.state()
        self.assertEqual(state["research_note_revision"], 1)
        self.assertEqual(state["private_note_revision"], 1)
        self.assertEqual(self.column("text_content"), "Research via PATCH")
        self.assertEqual(self.column("private_notes"), "Reminder via PATCH")

    # ---- conflicts and convergence ----

    def test_a_stale_research_base_against_a_different_value_conflicts(self):
        self.send_research("First")
        code, result = self.send_research("Second", base=0)
        self.assertEqual((code, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_revision"], 1)
        self.assertEqual(self.column("text_content"), "First")

    def test_equal_desired_canonical_text_converges(self):
        self.send_research("Agreed")
        code, result = self.send_research("Agreed", base=0)
        self.assertEqual((code, result["code"], result["changed"]),
                         (200, "ACKNOWLEDGED", False))
        self.assertEqual(self.state()["research_note_revision"], 1,
                         "convergence advances nothing")
        self.assertEqual(self.column("text_content"), "Agreed")

        self.send_private("Same reminder")
        code, result = self.send_private("Same reminder", base=0)
        self.assertEqual((code, result["code"], result["changed"]),
                         (200, "ACKNOWLEDGED", False))
        self.assertEqual(self.state()["private_note_revision"], 1)

    def test_a_future_revision_is_refused(self):
        code, result = self.send_research("Nope", base=9)
        self.assertEqual((code, result["code"]), (400, "FUTURE_REVISION"))
        self.assertEqual(self.column("text_content") or "", "")

    def test_compact_conflict_results_omit_note_bodies(self):
        body = "Secret prose that must never appear in a durable result."
        other = "A different secret."
        self.send_research(body)
        code, result = self.send_research(other, base=0)
        self.assertEqual((code, result["code"]), (409, "REVISION_CONFLICT"))
        dumped = json.dumps(result)
        self.assertNotIn(body, dumped)
        self.assertNotIn(other, dumped)
        self.assertNotIn("current_value", result)
        self.assertNotIn("current_preview", result)
        self.assertNotIn("text", result)
        self.assertEqual(result["current_bytes"], len(body.encode("utf-8")))
        self.assertEqual(result["requested_bytes"], len(other.encode("utf-8")))
        self.assertEqual(result["current_revision"], 1)
        self.assertLessEqual(len(dumped.encode("utf-8")), 2048)

        self.send_private(body)
        code, result = self.send_private(other, base=0)
        self.assertEqual((code, result["code"]), (409, "REVISION_CONFLICT"))
        dumped = json.dumps(result)
        self.assertNotIn(body, dumped)
        self.assertEqual(result["current_bytes"], len(body.encode("utf-8")))

    def test_acknowledgement_omits_the_body(self):
        code, result = self.send_research("Keep this off the wire")
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["value_omitted"])
        self.assertNotIn("text", result)
        self.assertNotIn("Keep this off the wire", json.dumps(result))

    def test_a_missing_work_is_terminal(self):
        for operation in (notes.RESEARCH_OPERATION, notes.PRIVATE_OPERATION):
            with self.subTest(operation=operation):
                envelope = self.envelope(operation, "gone", work="W-gone")
                status, result = sync_protocol.process_operation(self.db, envelope)
                self.assertEqual(status, 404)
                self.assertEqual(result["code"], "ENTITY_NOT_FOUND")
                self.assertEqual(result["work_id"], "W-gone")
                self.assertEqual(
                    sync_protocol.process_operation(self.db, envelope),
                    (status, result))

    # ---- validation parity ----

    def test_control_character_and_size_validation_parity(self):
        """Ordinary PATCH and the durable handler refuse the same illegal
        bodies. The error spelling differs on purpose: PATCH keeps
        ResearchError, the protocol keeps INVALID_ENVELOPE."""
        control = "See [[concept:Bell\x07 Curve]]."
        with self.assertRaises(ResearchError) as caught:
            save_work_notes(self.db, self.work, control)
        self.assertEqual(caught.exception.code, "invalid_text")
        self.assertEqual(
            self.send_research(control),
            (400, {"code": "INVALID_ENVELOPE"}))
        self.assertEqual(self.column("text_content") or "", "")
        self.assertEqual(list_concepts(self.db), [])

        over_private = "p" * (notes.MAX_PRIVATE_NOTE_UTF8_BYTES + 1)
        self.assertEqual(self.send_private(over_private)[0], 400)
        with self.assertRaises(ValueError):
            self.db.update_work_metadata(self.work, {"private_notes": over_private})
        self.assertFalse(self.column("private_notes"))

        # Research is the large aggregate: a body the generic 64 KiB envelope
        # cap would refuse must still be accepted here.
        large_research = "r" * (64 * 1024 + 1)
        code, result = self.send_research(large_research)
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(len(self.column("text_content")), 64 * 1024 + 1)

        at_private = "q" * notes.MAX_PRIVATE_NOTE_UTF8_BYTES
        code, result = self.send_private(at_private)
        self.assertEqual((code, result["code"]), (200, "ACKNOWLEDGED"))

    def test_the_two_scopes_do_not_share_a_payload_or_a_base(self):
        envelope = self.envelope(notes.RESEARCH_OPERATION, "x", base=None)
        self.assertEqual(sync_protocol.process_operation(self.db, envelope)[1]["code"],
                         "INVALID_BASE_REVISION")
        envelope = self.envelope(notes.RESEARCH_OPERATION, "x")
        envelope["payload"] = {"text": "x", "extra": 1}
        self.assertEqual(sync_protocol.process_operation(self.db, envelope),
                         (400, {"code": "INVALID_ENVELOPE"}))
        envelope = self.envelope(notes.PRIVATE_OPERATION, "x")
        envelope["payload"] = {}
        self.assertEqual(sync_protocol.process_operation(self.db, envelope),
                         (400, {"code": "INVALID_ENVELOPE"}))

    def test_notes_state_starts_at_revision_zero(self):
        fresh = self.db.add_work("Untouched")
        self.assertEqual(self.db.get_work_notes_state(fresh), {
            "work_id": fresh,
            "research_note_revision": 0,
            "private_note_revision": 0,
        })
        self.assertIsNone(self.db.get_work_notes_state("W-gone"))


if __name__ == "__main__":
    unittest.main()
