"""The generic operation protocol on PostgreSQL (the retired ``tests/test_sync_protocol.py``).

``backend/sync/protocol.py`` normalizes and hashes the envelope, checks
dependencies, ledgers the outcome and dispatches to exactly one registered
family handler. These tests cover what is generic: the registered families,
refusals that never reach a handler or the ledger, per-family base revision
rules, equivalent spellings as one operation, and dependencies.
"""

from __future__ import annotations

import importlib
import pkgutil
import uuid
from typing import Any, Optional

from backend import sync_protocol as rules
from backend.sync import handlers, protocol
from backend.sync.handlers import work_open
from tests.pg.test_works_sync import SyncTestCase


class ProtocolTests(SyncTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.work = self.library.add_work("Protocol work")
        self.tag = self.library.add_tag("Protocol tag")["id"]

    def op(self, operation: str, payload: dict[str, Any], base: Optional[int], **changes: Any) -> dict[str, Any]:
        return self.envelope(operation, self.work, payload, base, **changes)

    def test_every_operation_of_the_protocol_has_one_postgresql_handler(self) -> None:
        self.assertEqual(protocol.supported_operations(), frozenset(rules.supported_operations()))
        self.assertEqual(len(protocol.supported_operations()), 50)
        for name in ("MARK_WORK_OPENED", "SET_WORK_METADATA_FIELD", "CREATE_PDF_ANNOTATION", "MERGE_TAG",
                     "SET_CONCEPT_PARENTS", "REORDER_PLAYLIST_ITEMS", "SET_PERSON_METADATA_FIELD"):
            self.assertIn(name, protocol.supported_operations())

    def test_registration_refuses_a_second_owner_and_an_unknown_operation(self) -> None:
        with self.assertRaises(RuntimeError):
            protocol.register("ADD_WORK_TAG", work_open.HANDLER)
        with self.assertRaises(RuntimeError):
            protocol.register("MOVE_WORK", work_open.HANDLER)

    def test_envelope_refusals_never_reach_a_handler_or_the_ledger(self) -> None:
        for operation in ("MOVE_WORK", "UPDATE_CONCEPT", "", "mark_work_opened"):
            with self.subTest(operation=operation):
                self.assertEqual(self.send(self.op(operation, {}, None)), (400, {"code": "INVALID_ENVELOPE"}))
        wrong_type = self.op("MARK_WORK_OPENED", {}, None, entity_type="person")
        self.assertEqual(self.send(wrong_type), (400, {"code": "INVALID_ENVELOPE"}))
        self_dependent = self.op("MARK_WORK_OPENED", {}, None)
        self_dependent["depends_on"] = [self_dependent["op_id"]]
        self.assertEqual(self.send(self_dependent), (400, {"code": "INVALID_ENVELOPE"}))
        # Each family decides which base revision spelling its semantics can honour.
        self.assertEqual(self.send(self.op("ADD_WORK_TAG", {"tag_id": self.tag}, None)),
                         (400, {"code": "INVALID_BASE_REVISION"}))
        self.assertEqual(self.send(self.op("MARK_WORK_OPENED", {}, 0)), (400, {"code": "INVALID_BASE_REVISION"}))
        self.assertEqual(self.ledger(), [])

    def test_each_family_answers_only_its_own_shape(self) -> None:
        tag_ack = self.send(self.op("ADD_WORK_TAG", {"tag_id": self.tag}, 0))[1]
        open_ack = self.send(self.op("MARK_WORK_OPENED", {}, None))[1]
        self.assertEqual(set(tag_ack) & {"recent_item", "effective_opened_at"}, set())
        self.assertEqual(set(open_ack) & {"tag", "tag_id", "server_revision", "present"}, set())
        self.assertIn("tag", tag_ack)
        self.assertIn("recent_item", open_ack)

    def test_equivalent_spellings_are_one_operation(self) -> None:
        data = self.op("MARK_WORK_OPENED", {}, None)
        first = self.send(data)
        variant = dict(reversed(list(data.items())))
        variant.update(op_id=data["op_id"].upper(), device_id=data["device_id"].upper(),
                       occurred_at="2026-09-11T12:00:00+02:00", created_at="2026-09-11T10:00:00+00:00")
        self.assertEqual(self.send(variant), first)
        self.assertEqual(len(self.ledger()), 1)

    def test_a_dependency_must_be_an_acknowledged_operation_of_the_same_device(self) -> None:
        missing = self.op("MARK_WORK_OPENED", {}, None, depends_on=[str(uuid.uuid4())])
        self.assertEqual(self.send(missing), (400, {"code": "UNSATISFIED_DEPENDENCY"}))
        self.assertEqual(self.ledger(), [])
        first = self.op("MARK_WORK_OPENED", {}, None)
        self.assertEqual(self.send(first)[1]["code"], "ACKNOWLEDGED")
        other_device = self.op("ADD_WORK_TAG", {"tag_id": self.tag}, 0, device_id=str(uuid.uuid4()),
                               depends_on=[first["op_id"]])
        self.assertEqual(self.send(other_device), (400, {"code": "UNSATISFIED_DEPENDENCY"}))
        self.assertEqual(len(self.ledger()), 1)
        same_device = self.op("ADD_WORK_TAG", {"tag_id": self.tag}, 0, depends_on=[first["op_id"]])
        status, result = self.send(same_device)
        self.assertEqual((status, result["code"]), (200, "ACKNOWLEDGED"))
        self.assertEqual(len(self.ledger()), 2)

    def test_family_handlers_do_not_reimplement_the_protocol(self) -> None:
        for info in pkgutil.iter_modules(handlers.__path__):
            module = importlib.import_module("backend.sync.handlers." + info.name)
            for name in ("process_operation", "insert_result", "normalize_envelope", "request_hash", "register"):
                self.assertFalse(hasattr(module, name), module.__name__ + "." + name)
