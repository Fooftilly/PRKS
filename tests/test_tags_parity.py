"""Tags, aliases, delete, merge and Work/Folder tags: PostgreSQL answers as SQLite does (L-O-1 #633).

Each call, and each Tag semantic operation, runs on this leg's library and
on a reference SQLite library holding the same Works, Folders and Tags, and
the results must be equal after generated ids and timestamps become labels.
Inputs avoid the documented differences (``backend/pg_facade/organization.py``):
names differ beyond ASCII case, lists of several generated ids are compared
as sets, and no create reuses a deleted or merged id. Folders are seeded as
rows on PostgreSQL: the Folder commands are a later slice.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from typing import Any, Optional

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from tests.backend_parity import POSTGRES, SQLITE, parametrize_backends
from tests.people_parity_helpers import PeopleParityTestCase, PeopleSide

_ID = re.compile(r"\b(T|L|F|W|PL|MF|AS)-[0-9A-F]{32}\b")
_ETAG = re.compile(r'(W/"prks-tags-\d+-)[0-9a-f]+(")')
_MOMENT = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d{3})?")
_TAG_OPERATIONS = frozenset({"CREATE_TAG", "DELETE_TAG", "MERGE_TAG"})
_FOLDER_OPERATIONS = frozenset({"ADD_FOLDER_TAG", "REMOVE_FOLDER_TAG", "CREATE_FOLDER", "SET_FOLDER_FIELD",
                                "DELETE_FOLDER"})
_SET_KEYS = frozenset({"assigned", "affected_work_ids", "affected_folder_ids",
                       "affected_tag_options_work_ids", "affected_tag_options_folder_ids"})


class TagSide(PeopleSide):
    """A side whose labels cover Tags, aliases and Folders."""

    def tag(self, label: str, name: Optional[str] = None) -> str:
        return self.label(self.db.add_tag(name or label)["id"], f"<T {label}>")

    def folder(self, label: str) -> str:
        return self.label(self.db.add_folder(label), f"<F {label}>")

    def normal(self, value: Any) -> Any:
        if isinstance(value, dict):
            # Keys too: ``known_absent`` is keyed by Tag id.
            return {self.normal(key): (sorted(self.normal(item), key=repr) if key in _SET_KEYS
                                       else self.normal(item))
                    for key, item in sorted(value.items())}
        if isinstance(value, list):
            return [self.normal(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.normal(item) for item in value)
        if isinstance(value, str):
            text = _ID.sub(lambda match: self.labels.get(match.group(0), "<unknown id>"), value)
            return _MOMENT.sub(lambda match: "<ms>" if match.group(1) else "<s>", text)
        return value


class TagParityTestCase(PeopleParityTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.leg, self.ref = TagSide(self.leg.db), TagSide(self.ref.db)

    def tag_both(self, label: str, name: Optional[str] = None) -> tuple[str, str]:
        return self.leg.tag(label, name), self.ref.tag(label, name)

    def work_both(self, label: str) -> tuple[str, str]:
        return self.leg.work(label, title=label), self.ref.work(label, title=label)

    def folder_both(self, label: str) -> tuple[str, str]:
        return self.leg.folder(label), self.ref.folder(label)

    def same_pair(self, call: str, *pairs: tuple[Any, Any]) -> Any:
        """``call(*ids_of_this_side)`` on each side; asserts equal results."""
        mine = getattr(self.leg.db, call)(*(pair[0] for pair in pairs))
        theirs = getattr(self.ref.db, call)(*(pair[1] for pair in pairs))
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), call)
        return mine

    def refused_pair(self, call: str, *pairs: tuple[Any, Any]) -> str:
        messages = []
        for index, side in enumerate(self.sides()):
            with self.assertRaises(ValueError) as caught:
                getattr(side.db, call)(*(pair[index] for pair in pairs))
            messages.append(str(caught.exception))
        self.assertEqual(messages[0], messages[1], call)
        return messages[0]


def both(value: Any) -> tuple[Any, Any]:
    return value, value


@parametrize_backends
class TagFacadeParityTests(TagParityTestCase):
    def test_names_aliases_and_the_catalog(self) -> None:
        ethics = self.tag_both("Ethics")
        logic = self.tag_both("Logic")
        again = self.same("add_tag", "ETHICS", "#123456")
        self.assertTrue(again["existed"])
        self.same_pair("add_tag_alias", ethics, both("Morals"))
        self.same_pair("add_tag_alias", ethics, both("morals"))
        for pair, alias in ((ethics, "ethics"), (ethics, "LOGIC"), (logic, "MORALS"), (ethics, "  ")):
            with self.subTest(alias=alias):
                self.refused_pair("add_tag_alias", pair, both(alias))
        self.refused_pair("add_tag_alias", both("T-" + "0" * 32), both("x"))
        self.same("add_tag", "Morals")
        self.same("resolve_tag_id_by_label", "MORALS")
        self.same("resolve_tag_id_by_label", "nothing")
        self.same("get_all_tags")
        # The catalog ETag hashes the rows, ids and times included: its shape
        # matches, and it moves with the catalog on both sides.
        before = self.both("etag_tags_all")
        self.assertEqual([_ETAG.sub(r"\1x\2", tag) for tag in before], ['W/"prks-tags-2-x"'] * 2)
        self.same_pair("delete_tag_alias", ethics, both("MORALS"))
        self.same_pair("delete_tag_alias", ethics, both("Morals"))
        self.same("get_all_tags")
        self.assertTrue(all(new != old for new, old in zip(self.both("etag_tags_all"), before)))
        self.refused_pair("add_tag", both("  "))

    def test_work_and_folder_tags_and_their_options(self) -> None:
        work, folder = self.work_both("W"), self.folder_both("F")
        ethics, logic = self.tag_both("Ethics"), self.tag_both("Logic")
        self.same_pair("add_tag_to_work", work, ethics)
        self.same_pair("add_tag_to_work", work, logic)
        self.same_pair("remove_tag_from_work", work, logic)
        self.same_pair("add_tag_to_folder", folder, ethics)
        self.same_pair("add_tag_to_folder", folder, logic)
        self.same_pair("remove_tag_from_folder", folder, logic)
        self.same_pair("remove_tag_from_work", work, logic)
        self.same_pair("get_work_tag_options", work)
        self.same_pair("get_folder_tag_options", folder)
        self.same_pair("get_work_tags", work)
        self.same_pair("get_folder_tags", folder)
        self.same("get_tags_in_use")
        self.assertEqual(self.leg.normal(self.leg.db.get_work(work[0])["tags"]),
                         self.ref.normal(self.ref.db.get_work(work[1])["tags"]))
        self.same_pair("get_work_tag_options", both("W-" + "0" * 32))

    def test_delete(self) -> None:
        work, folder, unlinked = self.work_both("W"), self.folder_both("F"), self.work_both("U")
        ethics = self.tag_both("Ethics")
        self.same_pair("add_tag_alias", ethics, both("Morals"))
        self.same_pair("add_tag_to_work", work, ethics)
        self.same_pair("add_tag_to_folder", folder, ethics)
        self.same_pair("add_tag_to_work", unlinked, ethics)
        self.same_pair("remove_tag_from_work", unlinked, ethics)
        self.same_pair("delete_tag", ethics)
        self.same_pair("get_work_tag_options", work)
        self.same_pair("get_work_tag_options", unlinked)
        self.same_pair("get_folder_tag_options", folder)
        self.same("get_all_tags")
        self.same("resolve_tag_id_by_label", "Morals")
        self.refused_pair("delete_tag", ethics)
        self.refused_pair("delete_tag", both(""))

    def test_merge(self) -> None:
        source, target = self.tag_both("Moral philosophy"), self.tag_both("Ethics")
        self.same_pair("add_tag_alias", source, both("Morals"))
        only, shared, folder = self.work_both("Only"), self.work_both("Shared"), self.folder_both("F")
        for work in (only, shared):
            self.same_pair("add_tag_to_work", work, source)
        self.same_pair("add_tag_to_work", shared, target)
        self.same_pair("add_tag_to_folder", folder, source)
        self.same_pair("merge_tags_into", source, target)
        for work in (only, shared):
            self.same_pair("get_work_tag_options", work)
            self.same_pair("get_work_tags", work)
        self.same_pair("get_folder_tag_options", folder)
        self.same("get_all_tags")
        self.same("get_tags_in_use")
        self.same("resolve_tag_id_by_label", "moral philosophy")
        for pair in ((source, target), (target, target), (both(""), target), (target, both("T-" + "0" * 32))):
            with self.subTest(pair=pair):
                self.refused_pair("merge_tags_into", *pair)


class SyncParityTestCase(TagParityTestCase):
    """Sends one semantic operation to this leg's protocol and to SQLite's (``send_both``)."""

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

    def new_tag_id(self, label: str) -> str:
        tag_id = "T-" + uuid.uuid4().hex.upper()
        for side in self.sides():
            side.label(tag_id, f"<T {label}>")
        return tag_id

    def envelope(self, side: TagSide, operation: str, entity_id: str, payload: dict[str, Any],
                 base: Optional[int], op_id: str) -> dict[str, Any]:
        return {"op_id": op_id, "device_id": self.devices[id(side)], "operation": operation,
                "entity_type": self.entity_type(operation), "entity_id": entity_id, "payload": payload,
                "base_revision": base, "occurred_at": "2026-10-21T10:00:00Z",
                "created_at": "2026-10-21T10:00:00Z", "depends_on": []}

    def entity_type(self, operation: str) -> str:
        return ("tag" if operation in _TAG_OPERATIONS
                else "folder" if operation in _FOLDER_OPERATIONS else "work")

    def send(self, side: TagSide, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
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

    def send_both(self, operation: str, ids: Any, payload: Any, base: Optional[int] = None,
                  op_id: Optional[str] = None) -> tuple[int, dict[str, Any]]:
        """The same operation on both sides; ``ids`` and ``payload`` may be per-side pairs."""
        pair = ids if isinstance(ids, tuple) else (ids, ids)
        payloads = payload if isinstance(payload, tuple) else (payload, payload)
        op_id = op_id or str(uuid.uuid4())
        mine = self.send(self.leg, self.envelope(self.leg, operation, pair[0], payloads[0], base, op_id))
        theirs = self.send(self.ref, self.envelope(self.ref, operation, pair[1], payloads[1], base, op_id))
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), operation)
        return mine



@parametrize_backends
class TagSyncParityTests(SyncParityTestCase):
    def create(self, label: str, name: Optional[str] = None) -> str:
        tag_id = self.new_tag_id(label)
        status, _ = self.send_both("CREATE_TAG", tag_id, {"name": name or label, "color": "#6d6cf7"})
        self.assertEqual(status, 200)
        return tag_id

    def test_create(self) -> None:
        tag_id = self.new_tag_id("Ethics")
        op_id = str(uuid.uuid4())
        payload = {"name": "  Ethics ", "color": "#6d6cf7"}
        self.assertEqual(self.send_both("CREATE_TAG", tag_id, payload, op_id=op_id)[0], 200)
        self.send_both("CREATE_TAG", tag_id, payload, op_id=op_id)  # a replay
        self.send_both("CREATE_TAG", tag_id, {"name": "Other", "color": "#000000"})
        self.send_both("CREATE_TAG", self.new_tag_id("Clash"), {"name": "ETHICS", "color": "#6d6cf7"})
        for side in self.sides():
            side.db.add_tag_alias(tag_id, "Morals")
        self.send_both("CREATE_TAG", self.new_tag_id("Alias clash"), {"name": "morals", "color": "#6d6cf7"})
        self.send_both("CREATE_TAG", "T-1", payload)
        self.send_both("CREATE_TAG", self.new_tag_id("Empty"), {"name": "  ", "color": "#6d6cf7"})
        self.send_both("CREATE_TAG", self.new_tag_id("Base"), payload, base=0)
        self.same("get_all_tags")

    def test_work_and_folder_links(self) -> None:
        tag_id = self.create("Ethics")
        for kind, owner in (("WORK", self.work_both("W")), ("FOLDER", self.folder_both("F"))):
            add, remove = "ADD_%s_TAG" % kind, "REMOVE_%s_TAG" % kind
            with self.subTest(kind=kind):
                self.send_both(add, owner, {"tag_id": tag_id}, 0)
                self.send_both(add, owner, {"tag_id": tag_id}, 0)
                self.send_both(remove, owner, {"tag_id": tag_id}, 0)
                self.send_both(remove, owner, {"tag_id": tag_id}, 5)
                self.send_both(remove, owner, {"tag_id": tag_id}, 1)
                self.send_both(add, owner, {"tag_id": "T-" + "0" * 32}, 0)
                self.send_both(add, both("W-" + "0" * 32 if kind == "WORK" else "F-" + "0" * 32),
                               {"tag_id": tag_id}, 0)
                self.send_both(add, owner, {"tag_id": tag_id}, None)
                self.send_both(add, owner, {"tag_id": " x"}, 0)
        self.same("get_tags_in_use")

    def test_delete_and_merge(self) -> None:
        work, folder = self.work_both("W"), self.folder_both("F")
        source, target, gone = self.create("A"), self.create("B"), self.create("C")
        self.send_both("ADD_WORK_TAG", work, {"tag_id": source}, 0)
        self.send_both("ADD_WORK_TAG", work, {"tag_id": gone}, 0)
        self.send_both("ADD_FOLDER_TAG", folder, {"tag_id": source}, 0)
        self.send_both("DELETE_TAG", gone, {})
        self.send_both("DELETE_TAG", gone, {})
        self.send_both("ADD_WORK_TAG", work, {"tag_id": gone}, 2)
        self.send_both("MERGE_TAG", source, {"target_tag_id": gone})
        self.send_both("MERGE_TAG", gone, {"target_tag_id": source})
        self.send_both("MERGE_TAG", "T-" + "0" * 32, {"target_tag_id": source})
        self.send_both("MERGE_TAG", source, {"target_tag_id": source})
        self.send_both("MERGE_TAG", source, {"target_tag_id": target})
        self.send_both("MERGE_TAG", source, {"target_tag_id": target})
        self.send_both("DELETE_TAG", source, {})
        self.send_both("REMOVE_WORK_TAG", work, {"tag_id": source}, 1)
        third = self.create("D")
        self.send_both("MERGE_TAG", third, {"target_tag_id": source})
        self.send_both("DELETE_TAG", target, {"x": 1})
        self.same_pair("get_work_tag_options", work)
        self.same_pair("get_folder_tag_options", folder)
        self.same("get_all_tags")
