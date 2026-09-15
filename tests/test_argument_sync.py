"""Arguments and Stances: five shapes, and parity with the ordinary endpoints.

Two decisions are worth pinning here rather than describing. Construction is
ATOMIC with its initial sources and targets, because the online endpoint has
never been able to leave a disconnected Argument behind. And targets are ONE
aggregate spanning two tables, because the user chose one ordered list.
"""
import pathlib
import tempfile
import unittest
import uuid

from backend import argument_sync as arguments, entity_ids, sync_protocol
from backend.db_manager import PRKSDatabase
from backend.research_network import (
    ResearchError,
    create_argument,
    create_position,
    delete_argument,
    get_argument,
    replace_argument_sources,
    replace_argument_targets,
    update_argument,
)
from backend.storage.config import StorageConfig

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ArgumentSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prks-argument-")
        self.addCleanup(self.tmp.cleanup)
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self.tmp.name))
        self.device = str(uuid.uuid4())

    # ---- helpers ----------------------------------------------------------

    def send(self, operation, entity_id, payload, base=None, op_id=None):
        return sync_protocol.process_operation(self.db, dict(
            op_id=op_id or str(uuid.uuid4()), device_id=self.device, operation=operation,
            entity_type="argument", entity_id=entity_id, payload=payload,
            base_revision=base, occurred_at="2026-09-15T10:00:00Z",
            created_at="2026-09-15T10:00:00Z", depends_on=[]))

    def create(self, name="A claim", kind="argument", main_text="",
               sources=None, targets=None, argument_id=None):
        aid = argument_id or entity_ids.generate("A")
        status, result = self.send("CREATE_ARGUMENT", aid, dict(
            name=name, kind=kind, main_text=main_text,
            sources=sources or [], targets=targets or []), None)
        return aid, status, result

    def field(self, argument_id, name, value, base):
        return self.send("SET_ARGUMENT_FIELD", argument_id,
                         dict(field=name, value=value), base)

    def sources(self, argument_id, rows, base):
        return self.send("SET_ARGUMENT_SOURCES", argument_id, dict(sources=rows), base)

    def targets(self, argument_id, rows, base):
        return self.send("SET_ARGUMENT_TARGETS", argument_id, dict(targets=rows), base)

    def work(self, title="A paper"):
        return self.db.add_work(title=title)

    def position(self, name="Realism is false"):
        return create_position(self.db, name)["id"]

    def stored(self, argument_id):
        rows = self.db.execute_query("SELECT * FROM arguments WHERE id = ?", (argument_id,))
        return rows[0] if rows else None

    def revision(self, argument_id, field):
        with self.db.connection() as conn:
            return arguments.get_revision(conn, argument_id, field)

    def sources_revision(self, argument_id):
        with self.db.connection() as conn:
            return arguments.get_sources_revision(conn, argument_id)

    def targets_revision(self, argument_id):
        with self.db.connection() as conn:
            return arguments.get_targets_revision(conn, argument_id)

    def current_targets(self, argument_id):
        with self.db.connection() as conn:
            return arguments.current_targets(conn, argument_id)

    def current_sources(self, argument_id):
        with self.db.connection() as conn:
            return arguments.current_sources(conn, argument_id)

    # ---- construction -----------------------------------------------------

    def test_an_argument_is_created_under_the_id_the_client_minted(self):
        aid, status, result = self.create("Realism fails", "argument", "Body.")
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        row = self.stored(aid)
        self.assertEqual((row["name"], row["kind"], row["main_text"]),
                         ("Realism fails", "argument", "Body."))
        for name in arguments.FIELDS:
            self.assertEqual(self.revision(aid, name), 0, name)
        self.assertEqual(self.sources_revision(aid), 0)
        self.assertEqual(self.targets_revision(aid), 0)

    def test_a_stance_is_the_same_family_with_a_different_kind(self):
        aid, status, _result = self.create("I hold this", "stance")
        self.assertEqual(status, 200)
        self.assertEqual(self.stored(aid)["kind"], "stance")

    def test_construction_carries_its_initial_sources_and_targets(self):
        """"Create response" and "Create from Work" both produce an Argument
        that is already connected, and that is one operation, not three."""
        work = self.work()
        pos = self.position()
        aid, status, _result = self.create(
            "Answered", sources=[{"work_id": work, "pages": "3"}],
            targets=[{"type": "position", "id": pos, "verdict_id": "supports"}])
        self.assertEqual(status, 200)
        self.assertEqual(self.current_sources(aid), [{"work_id": work, "pages": "3"}])
        self.assertEqual(self.current_targets(aid),
                         [{"type": "position", "id": pos, "verdict_id": "supports"}])
        # Construction is not mutation: nothing "changed" three times.
        self.assertEqual(self.sources_revision(aid), 0)
        self.assertEqual(self.targets_revision(aid), 0)

    def test_a_refused_initial_target_creates_no_argument_at_all(self):
        """The load-bearing half of atomic construction.

        If the row survived a refused connection the user would be left with a
        standalone Argument they never asked for -- an outcome the ordinary
        endpoint cannot produce.
        """
        aid, status, result = self.create(
            "Answered",
            targets=[{"type": "position", "id": "P-NOPE", "verdict_id": "supports"}])
        self.assertEqual((status, result["code"]), (404, "POSITION_NOT_FOUND"))
        self.assertIsNone(self.stored(aid))

    def test_a_refused_initial_source_creates_no_argument_at_all(self):
        aid, status, result = self.create(
            "Cited", sources=[{"work_id": "W-NOPE", "pages": ""}])
        self.assertEqual((status, result["code"]), (404, "WORK_NOT_FOUND"))
        self.assertIsNone(self.stored(aid))

    def test_a_replayed_creation_is_idempotent_not_a_refusal(self):
        aid, _status, _result = self.create("Once")
        status, result = self.send("CREATE_ARGUMENT", aid, dict(
            name="Once", kind="argument", main_text="", sources=[], targets=[]), None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_a_server_minted_id_is_refused_as_an_envelope_error(self):
        status, result = self.send("CREATE_ARGUMENT", "A-1234ABCD", dict(
            name="Short id", kind="argument", main_text="", sources=[], targets=[]), None)
        self.assertEqual(result["code"], "INVALID_ENVELOPE")
        self.assertEqual(status, 400)

    def test_an_unknown_kind_is_an_envelope_error_not_a_refusal(self):
        aid = entity_ids.generate("A")
        status, result = self.send("CREATE_ARGUMENT", aid, dict(
            name="Bad", kind="opinion", main_text="", sources=[], targets=[]), None)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))
        self.assertIsNone(self.stored(aid))

    def test_construction_requires_every_key_including_the_empty_lists(self):
        aid = entity_ids.generate("A")
        status, result = self.send("CREATE_ARGUMENT", aid,
                                   dict(name="Bad", kind="argument", main_text=""), None)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_construction_carries_no_base_revision(self):
        aid = entity_ids.generate("A")
        status, result = self.send("CREATE_ARGUMENT", aid, dict(
            name="Bad", kind="argument", main_text="", sources=[], targets=[]), 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    # ---- scalar fields ----------------------------------------------------

    def test_each_field_advances_only_its_own_revision(self):
        aid, _s, _r = self.create("Before", "argument", "Body.")
        self.assertEqual(self.field(aid, "name", "After", 0)[0], 200)
        self.assertEqual(self.revision(aid, "name"), 1)
        self.assertEqual(self.revision(aid, "kind"), 0)
        self.assertEqual(self.revision(aid, "main_text"), 0)

    def test_three_fields_compose_without_seeing_each_other(self):
        aid, _s, _r = self.create("Before", "argument", "Body.")
        self.assertEqual(self.field(aid, "name", "After", 0)[0], 200)
        self.assertEqual(self.field(aid, "kind", "stance", 0)[0], 200)
        self.assertEqual(self.field(aid, "main_text", "New body.", 0)[0], 200)
        row = self.stored(aid)
        self.assertEqual((row["name"], row["kind"], row["main_text"]),
                         ("After", "stance", "New body."))

    def test_a_stale_base_on_a_different_value_conflicts(self):
        aid, _s, _r = self.create("Before")
        self.field(aid, "name", "After", 0)
        status, result = self.field(aid, "name", "Something else", 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], "After")
        self.assertEqual(result["current_revision"], 1)

    def test_a_stale_base_on_the_same_value_converges(self):
        aid, _s, _r = self.create("Before")
        self.field(aid, "name", "After", 0)
        status, result = self.field(aid, "name", "After", 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_a_base_ahead_of_the_server_is_a_future_revision(self):
        aid, _s, _r = self.create("Before")
        status, result = self.field(aid, "name", "After", 7)
        self.assertEqual((status, result["code"]), (400, "FUTURE_REVISION"))

    def test_a_field_edit_on_a_missing_argument_is_terminal(self):
        status, result = self.field(entity_ids.generate("A"), "name", "X", 0)
        self.assertEqual((status, result["code"]), (404, "ENTITY_NOT_FOUND"))

    def test_a_field_edit_requires_a_base_revision(self):
        aid, _s, _r = self.create("Before")
        status, result = self.send("SET_ARGUMENT_FIELD", aid,
                                   dict(field="name", value="After"), None)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    def test_an_unknown_field_is_an_envelope_error(self):
        aid, _s, _r = self.create("Before")
        status, result = self.send("SET_ARGUMENT_FIELD", aid,
                                   dict(field="colour", value="red"), 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_an_unknown_kind_on_a_field_edit_is_an_envelope_error(self):
        aid, _s, _r = self.create("Before")
        status, result = self.field(aid, "kind", "opinion", 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_the_acknowledgement_omits_the_value_it_was_given(self):
        aid, _s, _r = self.create("Before")
        _status, result = self.field(aid, "main_text", "x" * 5000, 0)
        self.assertTrue(result["value_omitted"])
        self.assertNotIn("current_value", result)

    # ---- sources aggregate ------------------------------------------------

    def test_the_whole_citation_list_is_replaced_under_one_revision(self):
        a, b = self.work("A"), self.work("B")
        aid, _s, _r = self.create("Cited")
        status, result = self.sources(aid, [{"work_id": a, "pages": "1"},
                                            {"work_id": b, "pages": "2"}], 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(result["server_revision"], 1)
        self.assertEqual(self.current_sources(aid),
                         [{"work_id": a, "pages": "1"}, {"work_id": b, "pages": "2"}])

    def test_reordering_the_same_works_is_a_different_citation_list(self):
        a, b = self.work("A"), self.work("B")
        aid, _s, _r = self.create("Cited")
        self.sources(aid, [{"work_id": a, "pages": ""}, {"work_id": b, "pages": ""}], 0)
        status, _result = self.sources(
            aid, [{"work_id": b, "pages": ""}, {"work_id": a, "pages": ""}], 1)
        self.assertEqual(status, 200)
        self.assertEqual([r["work_id"] for r in self.current_sources(aid)], [b, a])
        self.assertEqual(self.sources_revision(aid), 2)

    def test_two_devices_choosing_different_citations_is_one_conflict(self):
        a, b = self.work("A"), self.work("B")
        aid, _s, _r = self.create("Cited")
        self.sources(aid, [{"work_id": a, "pages": ""}], 0)
        status, result = self.sources(aid, [{"work_id": b, "pages": ""}], 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_count"], 1)

    def test_the_same_citation_list_from_a_stale_base_converges(self):
        a = self.work("A")
        aid, _s, _r = self.create("Cited")
        self.sources(aid, [{"work_id": a, "pages": "9"}], 0)
        status, result = self.sources(aid, [{"work_id": a, "pages": "9"}], 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(self.sources_revision(aid), 1)

    def test_a_citation_naming_a_missing_work_is_a_named_refusal(self):
        aid, _s, _r = self.create("Cited")
        status, result = self.sources(aid, [{"work_id": "W-GONE", "pages": ""}], 0)
        self.assertEqual((status, result["code"]), (404, "WORK_NOT_FOUND"))

    def test_a_duplicate_citation_is_an_envelope_error(self):
        a = self.work("A")
        aid, _s, _r = self.create("Cited")
        status, result = self.sources(aid, [{"work_id": a, "pages": ""},
                                            {"work_id": a, "pages": "2"}], 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_a_citation_list_requires_a_base_revision(self):
        aid, _s, _r = self.create("Cited")
        status, result = self.send("SET_ARGUMENT_SOURCES", aid, dict(sources=[]), None)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    # ---- targets aggregate ------------------------------------------------

    def test_positions_and_arguments_are_replaced_as_one_list(self):
        """The reason targets are not two families.

        One replacement holding both kinds must survive as both kinds: two
        per-table families would each have deleted the other's half.
        """
        pos = self.position()
        other, _s, _r = self.create("Other")
        aid, _s2, _r2 = self.create("Mixed")
        rows = [{"type": "position", "id": pos, "verdict_id": "supports"},
                {"type": "argument", "id": other, "verdict_id": "opposes"}]
        status, _result = self.targets(aid, rows, 0)
        self.assertEqual(status, 200)
        self.assertEqual(self.current_targets(aid), rows)

    def test_replacing_with_only_positions_drops_the_argument_targets(self):
        pos = self.position()
        other, _s, _r = self.create("Other")
        aid, _s2, _r2 = self.create("Mixed")
        self.targets(aid, [{"type": "position", "id": pos, "verdict_id": "supports"},
                           {"type": "argument", "id": other, "verdict_id": "opposes"}], 0)
        status, _result = self.targets(
            aid, [{"type": "position", "id": pos, "verdict_id": "qualifies"}], 1)
        self.assertEqual(status, 200)
        self.assertEqual(self.current_targets(aid),
                         [{"type": "position", "id": pos, "verdict_id": "qualifies"}])

    def test_a_verdict_change_alone_is_a_new_target_list(self):
        pos = self.position()
        aid, _s, _r = self.create("Verdicts")
        self.targets(aid, [{"type": "position", "id": pos, "verdict_id": "supports"}], 0)
        status, result = self.targets(
            aid, [{"type": "position", "id": pos, "verdict_id": "opposes"}], 1)
        self.assertEqual(status, 200)
        self.assertTrue(result["changed"])
        self.assertEqual(self.targets_revision(aid), 2)

    def test_two_devices_choosing_different_targets_is_one_conflict(self):
        one, two = self.position("One"), self.position("Two")
        aid, _s, _r = self.create("Targeted")
        self.targets(aid, [{"type": "position", "id": one, "verdict_id": "supports"}], 0)
        status, result = self.targets(
            aid, [{"type": "position", "id": two, "verdict_id": "supports"}], 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))

    def test_the_same_target_list_from_a_stale_base_converges(self):
        pos = self.position()
        aid, _s, _r = self.create("Targeted")
        rows = [{"type": "position", "id": pos, "verdict_id": "supports"}]
        self.targets(aid, rows, 0)
        status, result = self.targets(aid, rows, 0)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])
        self.assertEqual(self.targets_revision(aid), 1)

    def test_a_cycle_is_a_named_refusal_the_client_could_not_have_known(self):
        a, _s, _r = self.create("A")
        b, _s2, _r2 = self.create("B")
        self.targets(b, [{"type": "argument", "id": a, "verdict_id": "opposes"}], 0)
        status, result = self.targets(
            a, [{"type": "argument", "id": b, "verdict_id": "opposes"}], 0)
        self.assertEqual((status, result["code"]), (409, "ARGUMENT_CYCLE"))

    def test_targeting_itself_is_the_shortest_cycle(self):
        aid, _s, _r = self.create("Self")
        status, result = self.targets(
            aid, [{"type": "argument", "id": aid, "verdict_id": "opposes"}], 0)
        self.assertEqual((status, result["code"]), (409, "ARGUMENT_CYCLE"))

    def test_a_missing_position_target_keeps_its_own_name(self):
        aid, _s, _r = self.create("Targeted")
        status, result = self.targets(
            aid, [{"type": "position", "id": "P-GONE", "verdict_id": "supports"}], 0)
        self.assertEqual((status, result["code"]), (404, "POSITION_NOT_FOUND"))

    def test_a_missing_argument_target_keeps_its_own_name(self):
        aid, _s, _r = self.create("Targeted")
        status, result = self.targets(
            aid, [{"type": "argument", "id": entity_ids.generate("A"),
                   "verdict_id": "opposes"}], 0)
        self.assertEqual((status, result["code"]), (404, "TARGET_NOT_FOUND"))

    def test_an_unknown_verdict_is_a_named_refusal_not_an_envelope_error(self):
        """The vocabulary lives in a table, so an offline client cannot check
        it -- which is exactly what makes it a refusal rather than a shape
        error."""
        pos = self.position()
        aid, _s, _r = self.create("Targeted")
        status, result = self.targets(
            aid, [{"type": "position", "id": pos, "verdict_id": "vibes"}], 0)
        self.assertEqual((status, result["code"]), (409, "INVALID_VERDICT"))

    def test_an_unknown_target_type_is_an_envelope_error(self):
        aid, _s, _r = self.create("Targeted")
        status, result = self.targets(
            aid, [{"type": "concept", "id": "C-1", "verdict_id": "supports"}], 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_a_duplicate_target_of_one_kind_is_an_envelope_error(self):
        pos = self.position()
        aid, _s, _r = self.create("Targeted")
        status, result = self.targets(
            aid, [{"type": "position", "id": pos, "verdict_id": "supports"},
                  {"type": "position", "id": pos, "verdict_id": "opposes"}], 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_ENVELOPE"))

    def test_the_two_aggregates_do_not_share_a_revision(self):
        work = self.work()
        pos = self.position()
        aid, _s, _r = self.create("Both")
        self.sources(aid, [{"work_id": work, "pages": ""}], 0)
        self.assertEqual(self.sources_revision(aid), 1)
        self.assertEqual(self.targets_revision(aid), 0)
        self.targets(aid, [{"type": "position", "id": pos, "verdict_id": "supports"}], 0)
        self.assertEqual(self.sources_revision(aid), 1)
        self.assertEqual(self.targets_revision(aid), 1)

    def test_an_aggregate_never_advances_a_field_revision(self):
        work = self.work()
        aid, _s, _r = self.create("Both")
        self.sources(aid, [{"work_id": work, "pages": ""}], 0)
        for name in arguments.FIELDS:
            self.assertEqual(self.revision(aid, name), 0, name)

    # ---- destruction ------------------------------------------------------

    def test_deleting_an_argument_removes_it(self):
        aid, _s, _r = self.create("Gone")
        status, result = self.send("DELETE_ARGUMENT", aid, {}, None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertTrue(result["changed"])
        self.assertIsNone(self.stored(aid))

    def test_deleting_an_argument_twice_is_idempotent(self):
        aid, _s, _r = self.create("Gone")
        self.send("DELETE_ARGUMENT", aid, {}, None)
        status, result = self.send("DELETE_ARGUMENT", aid, {}, None)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertFalse(result["changed"])

    def test_an_argument_another_one_answers_is_refused_by_name(self):
        target, _s, _r = self.create("Target")
        answer, _s2, _r2 = self.create("Answer")
        self.targets(answer, [{"type": "argument", "id": target,
                               "verdict_id": "opposes"}], 0)
        status, result = self.send("DELETE_ARGUMENT", target, {}, None)
        self.assertEqual((status, result["code"]), (409, "ARGUMENT_TARGETED"))
        self.assertIsNotNone(self.stored(target))

    def test_a_deletion_carries_no_base_revision(self):
        aid, _s, _r = self.create("Gone")
        status, result = self.send("DELETE_ARGUMENT", aid, {}, 0)
        self.assertEqual((status, result["code"]), (400, "INVALID_BASE_REVISION"))

    # ---- parity with the ordinary endpoints -------------------------------

    def test_ordinary_creation_goes_through_the_shared_primitive(self):
        work = self.work()
        ordinary = create_argument(self.db, name="  Spaced   out  ", kind="argument",
                                   main_text="Body.",
                                   sources=[{"work_id": work, "pages": "4"}])
        aid, _s, _r = self.create("  Spaced   out  ", "argument", "Body.",
                                  sources=[{"work_id": work, "pages": "4"}])
        self.assertEqual(ordinary["name"], self.stored(aid)["name"])
        self.assertEqual(self.current_sources(ordinary["id"]), self.current_sources(aid))

    def test_ordinary_creation_advances_no_revision_either(self):
        ordinary = create_argument(self.db, name="Fresh", kind="argument")
        for name in arguments.FIELDS:
            self.assertEqual(self.revision(ordinary["id"], name), 0, name)
        self.assertEqual(self.sources_revision(ordinary["id"]), 0)
        self.assertEqual(self.targets_revision(ordinary["id"]), 0)

    def test_an_ordinary_edit_advances_only_the_fields_it_was_given(self):
        """The parity that makes offline conflict detection mean anything: an
        online rename must move the revision the offline client measured
        against, and must not move the ones it did not touch."""
        aid, _s, _r = self.create("Before", "argument", "Body.")
        update_argument(self.db, aid, name="After")
        self.assertEqual(self.revision(aid, "name"), 1)
        self.assertEqual(self.revision(aid, "kind"), 0)
        self.assertEqual(self.revision(aid, "main_text"), 0)

    def test_an_ordinary_edit_to_the_same_value_advances_nothing(self):
        aid, _s, _r = self.create("Same")
        update_argument(self.db, aid, name="Same")
        self.assertEqual(self.revision(aid, "name"), 0)

    def test_an_ordinary_edit_then_a_stale_durable_edit_conflicts(self):
        aid, _s, _r = self.create("Before")
        update_argument(self.db, aid, name="Renamed online")
        status, result = self.field(aid, "name", "Renamed offline", 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))
        self.assertEqual(result["current_value"], "Renamed online")

    def test_an_ordinary_source_replacement_advances_the_same_revision(self):
        work = self.work()
        aid, _s, _r = self.create("Cited")
        replace_argument_sources(self.db, aid, [{"work_id": work, "pages": "7"}])
        self.assertEqual(self.sources_revision(aid), 1)
        status, result = self.sources(aid, [], 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))

    def test_an_ordinary_target_replacement_advances_the_same_revision(self):
        pos = self.position()
        aid, _s, _r = self.create("Targeted")
        replace_argument_targets(
            self.db, aid, [{"type": "position", "id": pos, "verdict_id": "supports"}])
        self.assertEqual(self.targets_revision(aid), 1)
        status, result = self.targets(aid, [], 0)
        self.assertEqual((status, result["code"]), (409, "REVISION_CONFLICT"))

    def test_an_ordinary_replacement_to_the_same_list_advances_nothing(self):
        pos = self.position()
        aid, _s, _r = self.create("Targeted")
        rows = [{"type": "position", "id": pos, "verdict_id": "supports"}]
        replace_argument_targets(self.db, aid, rows)
        replace_argument_targets(self.db, aid, rows)
        self.assertEqual(self.targets_revision(aid), 1)

    def test_the_ordinary_deletion_keeps_raising_its_own_error(self):
        target, _s, _r = self.create("Target")
        answer, _s2, _r2 = self.create("Answer")
        self.targets(answer, [{"type": "argument", "id": target,
                               "verdict_id": "opposes"}], 0)
        with self.assertRaises(ResearchError) as caught:
            delete_argument(self.db, target)
        self.assertEqual(caught.exception.code, "argument_targeted")

    def test_the_ordinary_deletion_still_removes_a_free_argument(self):
        aid, _s, _r = self.create("Free")
        delete_argument(self.db, aid)
        self.assertIsNone(get_argument(self.db, aid))

    def test_only_the_revision_aware_boundary_writes_argument_rows(self):
        """A writer that skips the boundary is a writer no offline device can
        see, so the audit is over the source rather than over behaviour."""
        source = (ROOT / "backend" / "research_network.py").read_text()
        for statement in ("INSERT INTO arguments", "UPDATE arguments SET name",
                          "UPDATE arguments SET kind", "UPDATE arguments SET main_text",
                          "DELETE FROM arguments"):
            self.assertNotIn(statement, source, statement)

    def test_the_sync_state_endpoint_reports_every_revision(self):
        work = self.work()
        aid, _s, _r = self.create("Stateful")
        self.field(aid, "name", "Renamed", 0)
        self.sources(aid, [{"work_id": work, "pages": ""}], 0)
        state = self.db.get_argument_sync_state(aid)
        self.assertEqual(state["argument_id"], aid)
        self.assertEqual(state["fields"]["name"]["revision"], 1)
        self.assertEqual(state["fields"]["kind"]["revision"], 0)
        self.assertEqual(state["sources"]["revision"], 1)
        self.assertEqual(state["targets"]["revision"], 0)

    def test_the_sync_state_of_a_missing_argument_is_none(self):
        self.assertIsNone(self.db.get_argument_sync_state(entity_ids.generate("A")))

    def test_the_sync_state_carries_revisions_and_no_values(self):
        work = self.work()
        aid, _s, _r = self.create("Stateful", main_text="Body.")
        self.sources(aid, [{"work_id": work, "pages": "3"}], 0)
        blob = repr(self.db.get_argument_sync_state(aid))
        self.assertNotIn("Body.", blob)
        self.assertNotIn(work, blob)

    # ---- registration -----------------------------------------------------

    def test_every_family_is_registered_against_the_argument_entity_type(self):
        for operation in ("CREATE_ARGUMENT", "SET_ARGUMENT_FIELD", "SET_ARGUMENT_SOURCES",
                          "SET_ARGUMENT_TARGETS", "DELETE_ARGUMENT"):
            self.assertIn(operation, sync_protocol.supported_operations())
            self.assertEqual(sync_protocol._ENTITY_TYPES[operation], "argument", operation)

    def test_a_wrong_entity_type_is_refused(self):
        aid = entity_ids.generate("A")
        status, result = sync_protocol.process_operation(self.db, dict(
            op_id=str(uuid.uuid4()), device_id=self.device, operation="CREATE_ARGUMENT",
            entity_type="position", entity_id=aid,
            payload=dict(name="X", kind="argument", main_text="", sources=[], targets=[]),
            base_revision=None, occurred_at="2026-09-15T10:00:00Z",
            created_at="2026-09-15T10:00:00Z", depends_on=[]))
        self.assertEqual(status, 400)
        self.assertIsNone(self.stored(aid))


if __name__ == "__main__":
    unittest.main()
