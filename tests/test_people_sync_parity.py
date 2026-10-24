"""People semantic operations: PostgreSQL answers as SQLite does (L-P-1 #632, PR 4).

Each operation is sent to this leg's protocol and to the SQLite protocol of
a reference library holding the same People, and the two ``(status,
result)`` pairs must be equal after ids and timestamps become labels; then
the People and their revision states are compared. Client-minted ids are
the same on both sides, and inputs avoid the documented differences
(``backend/pg_facade/people.py``): a revision state is not compared where a
delete left a tombstone SQLite does not. ``CREATE_WORK``'s role filing composes with the
Organization lane's folder filing (``tests/test_folders_parity.py``); its
PostgreSQL tests are in ``tests/pg/test_people_sync.py`` and
``tests/pg/test_folders_sync.py``.
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any, Optional

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from tests.backend_parity import POSTGRES, SQLITE, parametrize_backends
from tests.people_parity_helpers import PeopleParityTestCase, PeopleSide

_FIELDS = ("first_name", "last_name", "aliases", "about", "image_url", "link_wikipedia",
           "link_stanford_encyclopedia", "link_iep", "links_other", "birth_date", "death_date")
_GROUP_OPERATIONS = ("CREATE_PERSON_GROUP", "SET_PERSON_GROUP_FIELD", "ADD_PERSON_GROUP_MEMBER",
                     "REMOVE_PERSON_GROUP_MEMBER", "DELETE_PERSON_GROUP")
_PERSON_OPERATIONS = ("CREATE_PERSON", "DELETE_PERSON", "SET_PERSON_METADATA_FIELD")
_MISSING_PERSON = "P-" + "0" * 32
_MISSING_GROUP = "PG-" + "0" * 32


def person_payload(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {name: "" for name in _FIELDS}
    payload.update(changes)
    return payload


def group_payload(name: str, **changes: Any) -> dict[str, Any]:
    return {"name": name, "description": "", "parent_id": "", **changes}


@parametrize_backends
class PeopleSyncParityTests(PeopleParityTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.devices = {id(self.leg): self._leg_device(), id(self.ref): str(uuid.uuid4())}

    def _leg_device(self) -> str:
        if self.parity_backend == POSTGRES:
            from backend.pg_facade.context import current_context

            device = current_context().device_id
            assert device is not None
            return str(device)
        return str(uuid.uuid4())

    def new_id(self, prefix: str, label: str) -> str:
        entity_id = prefix + "-" + uuid.uuid4().hex.upper()
        for side in self.sides():
            side.label(entity_id, f"<{prefix} {label}>")
        return entity_id

    def envelope(self, side: PeopleSide, operation: str, entity_id: str, payload: dict[str, Any],
                 base: Optional[int], op_id: Optional[str] = None) -> dict[str, Any]:
        entity_type = ("person-group" if operation in _GROUP_OPERATIONS
                       else "person" if operation in _PERSON_OPERATIONS else "work")
        return {"op_id": op_id or str(uuid.uuid4()), "device_id": self.devices[id(side)],
                "operation": operation, "entity_type": entity_type, "entity_id": entity_id,
                "payload": payload, "base_revision": base, "occurred_at": "2026-10-21T10:00:00Z",
                "created_at": "2026-10-21T10:00:00Z", "depends_on": []}

    def send(self, side: PeopleSide, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if side is self.ref or self.parity_backend == SQLITE:
            from backend import sync_protocol

            return sync_protocol.process_operation(side.db, data)
        from backend.pg_facade import NotPortedError
        from backend.pg_facade.context import current_context
        from backend.sync import protocol
        from backend.sync.families import Environment

        try:
            return protocol.process_operation(side.db.parity_library.database, current_context(), data,
                                              Environment(pdfs_dir=os.path.join(self.tmp, "leg-pdfs")))
        except NotPortedError as exc:
            self.parity_log.unported.add(exc.method)
            self.parity_log.refusals += 1
            raise

    def send_both(self, operation: str, ids: Any, payload: dict[str, Any], base: Optional[int] = None,
                  ) -> tuple[int, dict[str, Any]]:
        """The same operation on both sides; ``ids`` is one client-minted id or a pair."""
        pair = ids if isinstance(ids, tuple) else (ids, ids)
        op_id = str(uuid.uuid4())
        mine = self.send(self.leg, self.envelope(self.leg, operation, pair[0], payload, base, op_id))
        theirs = self.send(self.ref, self.envelope(self.ref, operation, pair[1], payload, base, op_id))
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), operation)
        return mine

    def create_person(self, label: str, **fields: Any) -> str:
        person_id = self.new_id("P", label)
        status, _ = self.send_both("CREATE_PERSON", person_id,
                                   person_payload(last_name=label, **fields))
        self.assertEqual(status, 200)
        return person_id

    def create_group(self, name: str, **fields: Any) -> str:
        group_id = self.new_id("PG", name)
        status, _ = self.send_both("CREATE_PERSON_GROUP", group_id, group_payload(name, **fields))
        self.assertEqual(status, 200)
        return group_id

    def same_states(self, person_ids: tuple[str, ...] = (), group_ids: tuple[str, ...] = (),
                    work_ids: tuple[tuple[str, str], ...] = ()) -> None:
        for person_id in person_ids:
            self.same_for("get_person_metadata_state", (person_id, person_id))
            self.same_for("get_person_groups_state", (person_id, person_id))
            self.same_for("get_person", (person_id, person_id))
        for group_id in group_ids:
            self.same_for("get_person_group_sync_state", (group_id, group_id))
            self.same_for("get_person_group", (group_id, group_id))
        for pair in work_ids:
            self.same_for("get_work_roles_state", pair)
            self.same_for("get_work_roles", pair)

    # -- Persons ----------------------------------------------------------------

    def test_create_person(self) -> None:
        person_id = self.new_id("P", "Lee")
        payload = person_payload(first_name="Ann", last_name="Lee", aliases="A. Lee, Annie",
                                 image_url="  https://example.org/ann.jpg  ", about="Bio")
        first = self.send_both("CREATE_PERSON", person_id, payload)
        self.assertEqual((first[0], first[1]["changed"]), (200, True))
        again = self.send_both("CREATE_PERSON", person_id, person_payload(last_name="Other"))
        self.assertEqual((again[1]["changed"], again[1]["person"]["last_name"]), (False, "Lee"))
        for bad_id, bad_payload, base in (("P-not-distributed", payload, None),
                                          (person_id, payload, 0),
                                          (person_id, {"first_name": "Ann"}, None),
                                          (person_id, person_payload(about=7), None),
                                          (person_id, person_payload(image_url="javascript:x"), None)):
            with self.subTest(payload=bad_payload, base=base):
                self.assertEqual(self.send_both("CREATE_PERSON", bad_id, bad_payload, base)[0], 400)
        self.same_states(person_ids=(person_id,))
        self.same("get_all_persons")

    def test_person_metadata_decision_table(self) -> None:
        person_id = self.create_person("Lee", first_name="Ann")
        rows = [
            ("about", "Bio", 0, 200),          # a change
            ("about", "Bio", 1, 200),          # a no-op
            ("about", "Bio", 0, 200),          # a stale base that agrees
            ("about", "Other", 0, 409),        # a stale base that disagrees
            ("about", "Other", 9, 400),        # a base from the future
            ("image_url", " https://example.org/a.png ", 0, 200),
            ("aliases", "A. Lee, Annie", 0, 200),
            ("last_name", "", 0, 200),
        ]
        for field, value, base, status in rows:
            with self.subTest(field=field, value=value, base=base):
                result = self.send_both("SET_PERSON_METADATA_FIELD", person_id,
                                        {"field": field, "value": value}, base)
                self.assertEqual(result[0], status, result)
        for payload, base in (({"field": "id", "value": "x"}, 0),
                              ({"field": "about", "value": 1}, 0),
                              ({"field": "image_url", "value": "javascript:x"}, 0),
                              ({"field": "about", "value": "x"}, None)):
            with self.subTest(payload=payload, base=base):
                self.assertEqual(self.send_both("SET_PERSON_METADATA_FIELD", person_id, payload, base)[0], 400)
        missing = self.send_both("SET_PERSON_METADATA_FIELD", _MISSING_PERSON,
                                 {"field": "about", "value": "x"}, 0)
        self.assertEqual(missing[1]["code"], "ENTITY_NOT_FOUND")
        self.same_states(person_ids=(person_id,))

    def test_delete_person(self) -> None:
        person_id = self.create_person("Lee")
        group_id = self.create_group("Readers")
        work = self.leg.work("essay", title="Essay"), self.ref.work("essay", title="Essay")
        self.send_both("ADD_PERSON_GROUP_MEMBER", group_id, {"person_id": person_id}, 0)
        self.send_both("ADD_WORK_PERSON_ROLE", work, {"person_id": person_id, "role_type": "Author",
                                                      "credit_name": ""}, 0)
        linked = self.send_both("DELETE_PERSON", person_id, {})
        self.assertEqual((linked[0], linked[1]["code"]), (409, "PERSON_HAS_LINKS"))
        self.send_both("REMOVE_WORK_PERSON_ROLE", work, {"person_id": person_id, "role_type": "Author"}, 1)
        self.assertEqual(self.send_both("DELETE_PERSON", person_id, {})[1]["changed"], True)
        self.assertEqual(self.send_both("DELETE_PERSON", person_id, {})[1]["changed"], False)
        for payload, base in (({"x": 1}, None), ({}, 0)):
            with self.subTest(payload=payload, base=base):
                self.assertEqual(self.send_both("DELETE_PERSON", person_id, payload, base)[0], 400)
        self.same_states(group_ids=(group_id,))
        # The role scope is a documented difference: PostgreSQL tombstones
        # every role scope naming a deleted Person (backend/pg_facade/people.py).
        self.same_for("get_work_roles", work)

    # -- Person Groups ----------------------------------------------------------

    def test_create_person_group(self) -> None:
        parent = self.create_group("Parent")
        child = self.new_id("PG", "Child")
        created = self.send_both("CREATE_PERSON_GROUP", child,
                                 group_payload("  Child  ", parent_id=parent, description=" About "))
        self.assertEqual((created[0], created[1]["changed"]), (200, True))
        self.assertEqual(self.send_both("CREATE_PERSON_GROUP", child, group_payload("Else"))[1]["changed"],
                         False)
        taken = self.send_both("CREATE_PERSON_GROUP", self.new_id("PG", "dup"), group_payload("PARENT"))
        self.assertEqual((taken[0], taken[1]["code"]), (409, "NAME_TAKEN"))
        orphan = self.send_both("CREATE_PERSON_GROUP", self.new_id("PG", "orphan"),
                                group_payload("Orphan", parent_id=_MISSING_GROUP))
        self.assertEqual((orphan[0], orphan[1]["code"]), (404, "PARENT_NOT_FOUND"))
        for group_id, payload, base in (("PG-not-distributed", group_payload("X"), None),
                                        (self.new_id("PG", "x"), group_payload(""), None),
                                        (self.new_id("PG", "y"), group_payload("Y"), 0),
                                        (self.new_id("PG", "z"), {"name": "Z"}, None)):
            with self.subTest(payload=payload, base=base):
                self.assertEqual(self.send_both("CREATE_PERSON_GROUP", group_id, payload, base)[0], 400)
        self.same_states(group_ids=(parent, child))
        self.same("get_all_person_groups")

    def test_person_group_field_decision_table(self) -> None:
        group_id = self.create_group("Readers")
        other = self.create_group("Writers")
        child = self.create_group("Child", parent_id=group_id)
        rows = [
            ("name", "Circle", 0, 200),
            ("name", " Circle ", 1, 200),        # the same name, re-spaced: a no-op
            ("name", "Circle", 0, 200),          # a stale base that agrees
            ("name", "Ring", 0, 409),            # a stale base that disagrees
            ("name", "Ring", 5, 400),
            ("name", "writers", 1, 409),         # NAME_TAKEN
            ("description", "About", 0, 200),
            ("parent_id", child, 0, 409),        # PARENT_CYCLE
            ("parent_id", group_id, 0, 409),     # its own parent
            ("parent_id", _MISSING_GROUP, 0, 404),
            ("parent_id", other, 0, 200),
            ("parent_id", "", 1, 200),
        ]
        for field, value, base, status in rows:
            with self.subTest(field=field, value=value, base=base):
                result = self.send_both("SET_PERSON_GROUP_FIELD", group_id, {"field": field, "value": value},
                                        base)
                self.assertEqual(result[0], status, result)
        missing = self.send_both("SET_PERSON_GROUP_FIELD", _MISSING_GROUP, {"field": "name", "value": "x"}, 0)
        self.assertEqual(missing[1]["code"], "ENTITY_NOT_FOUND")
        for payload, base in (({"field": "name", "value": ""}, 0), ({"field": "x", "value": "y"}, 0),
                              ({"field": "name", "value": "x"}, None)):
            with self.subTest(payload=payload, base=base):
                self.assertEqual(self.send_both("SET_PERSON_GROUP_FIELD", group_id, payload, base)[0], 400)
        self.same_states(group_ids=(group_id, other, child))

    def test_person_group_members(self) -> None:
        group_id = self.create_group("Readers")
        ann = self.create_person("Lee")
        rows = [
            ("ADD_PERSON_GROUP_MEMBER", ann, 0, 200),
            ("ADD_PERSON_GROUP_MEMBER", ann, 1, 200),     # present already
            ("ADD_PERSON_GROUP_MEMBER", ann, 0, 200),     # a stale base that agrees
            ("REMOVE_PERSON_GROUP_MEMBER", ann, 0, 409),  # a stale base that disagrees
            ("REMOVE_PERSON_GROUP_MEMBER", ann, 4, 400),
            ("REMOVE_PERSON_GROUP_MEMBER", ann, 1, 200),
            ("REMOVE_PERSON_GROUP_MEMBER", ann, 2, 200),  # absent already
            ("ADD_PERSON_GROUP_MEMBER", _MISSING_PERSON, 0, 404),
        ]
        for operation, person_id, base, status in rows:
            with self.subTest(operation=operation, base=base):
                result = self.send_both(operation, group_id, {"person_id": person_id}, base)
                self.assertEqual(result[0], status, result)
        missing = self.send_both("ADD_PERSON_GROUP_MEMBER", _MISSING_GROUP, {"person_id": ann}, 0)
        self.assertEqual(missing[1]["code"], "ENTITY_NOT_FOUND")
        for payload, base in (({"person_id": " "}, 0), ({}, 0), ({"person_id": ann}, None)):
            with self.subTest(payload=payload, base=base):
                self.assertEqual(self.send_both("ADD_PERSON_GROUP_MEMBER", group_id, payload, base)[0], 400)
        self.send_both("ADD_PERSON_GROUP_MEMBER", group_id, {"person_id": ann}, 2)
        self.same_states(person_ids=(ann,), group_ids=(group_id,))

    def test_delete_person_group(self) -> None:
        parent = self.create_group("Parent")
        group_id = self.create_group("Middle", parent_id=parent)
        child = self.create_group("Child", parent_id=group_id)
        ann = self.create_person("Lee")
        self.send_both("ADD_PERSON_GROUP_MEMBER", group_id, {"person_id": ann}, 0)
        self.assertEqual(self.send_both("DELETE_PERSON_GROUP", group_id, {})[1]["changed"], True)
        self.assertEqual(self.send_both("DELETE_PERSON_GROUP", group_id, {})[1]["changed"], False)
        self.assertEqual(self.send_both("DELETE_PERSON_GROUP", group_id, {}, 0)[0], 400)
        self.same_states(group_ids=(parent, child))
        # The membership pair is a documented difference: PostgreSQL advances
        # a deleted group's pairs once more (backend/pg_facade/people.py).
        self.same_for("get_person", (ann, ann))
        self.same("get_all_person_groups")

    # -- Roles ------------------------------------------------------------------

    def test_role_decision_table(self) -> None:
        ann = self.create_person("Lee", first_name="Ann")
        work = self.leg.work("essay", title="Essay"), self.ref.work("essay", title="Essay")

        def role(operation: str, base: int, credit: Optional[str] = None, person_id: str = ann,
                 role_type: str = "Author") -> tuple[int, dict[str, Any]]:
            payload: dict[str, Any] = {"person_id": person_id, "role_type": role_type}
            if credit is not None:
                payload["credit_name"] = credit
            return self.send_both(operation, work, payload, base)

        self.assertEqual(role("SET_WORK_PERSON_ROLE_CREDIT", 0, "x")[1]["code"], "ROLE_NOT_PRESENT")
        added = role("ADD_WORK_PERSON_ROLE", 0, "A. Lee")
        self.assertEqual((added[1]["server_revision"], added[1]["aliases_revision"]), (1, 1))
        self.assertEqual(role("ADD_WORK_PERSON_ROLE", 1, "A. Lee")[1]["changed"], False)
        self.assertEqual(role("ADD_WORK_PERSON_ROLE", 0, "A. Lee")[0], 200)   # a stale base that agrees
        self.assertEqual(role("SET_WORK_PERSON_ROLE_CREDIT", 0, "Ann L.")[0], 409)
        self.assertEqual(role("SET_WORK_PERSON_ROLE_CREDIT", 7, "Ann L.")[0], 400)
        self.assertEqual(role("SET_WORK_PERSON_ROLE_CREDIT", 1, "")[1]["server_revision"], 2)
        self.assertEqual(role("ADD_WORK_PERSON_ROLE", 0, "", role_type="Editor")[0], 200)
        self.assertEqual(role("REMOVE_WORK_PERSON_ROLE", 2)[1]["changed"], True)
        self.assertEqual(role("REMOVE_WORK_PERSON_ROLE", 3)[1]["changed"], False)
        self.assertEqual(role("ADD_WORK_PERSON_ROLE", 0, "", person_id=_MISSING_PERSON)[1]["code"],
                         "PERSON_NOT_FOUND")
        missing_work = self.send_both("ADD_WORK_PERSON_ROLE", "W-" + "0" * 32,
                                      {"person_id": ann, "role_type": "Author", "credit_name": ""}, 0)
        self.assertEqual(missing_work[1]["code"], "ENTITY_NOT_FOUND")
        for payload, base in (({"person_id": ann, "role_type": "Boss", "credit_name": ""}, 0),
                              ({"person_id": ann, "role_type": "Author"}, 0),
                              ({"person_id": ann, "role_type": "Author", "credit_name": "x" * 501}, 0),
                              ({"person_id": ann, "role_type": "Author", "credit_name": ""}, None)):
            with self.subTest(payload=payload, base=base):
                self.assertEqual(self.send_both("ADD_WORK_PERSON_ROLE", work, payload, base)[0], 400)
        self.same_states(person_ids=(ann,), work_ids=(work,))
