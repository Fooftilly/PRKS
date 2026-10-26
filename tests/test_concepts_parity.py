"""Concepts, their aliases and hierarchy: PostgreSQL answers as SQLite does (L-R-1 #634).

Each call, and each Concept semantic operation, runs on this leg's library
and on a reference SQLite library holding the same Concepts, and the
results must be equal after generated ids and timestamps become labels.
Inputs avoid the documented differences (``backend/pg_facade/research.py``):
names differ beyond ASCII case, no two aliases of a Concept share a
``LOWER()`` spelling, and no Concept is created again under a deleted id.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.research_network import ResearchError
from tests.backend_parity import parametrize_backends
from tests.test_tags_parity import SyncParityTestCase, TagSide, both

_CONCEPT_ID = re.compile(r"\bC-[0-9A-F]{32}\b")
_CONCEPT_OPERATIONS = frozenset({"CREATE_CONCEPT", "SET_CONCEPT_FIELD", "SET_CONCEPT_IDENTITY",
                                 "SET_CONCEPT_PARENTS", "DELETE_CONCEPT"})
_MISSING = "C-" + "0" * 32


class ConceptSide(TagSide):
    """A side whose labels cover Concepts too."""

    def concept(self, label: str, description: str = "") -> str:
        return self.label(self.db.create_concept(label, description)["id"], f"<C {label}>")

    def normal(self, value: Any) -> Any:
        if isinstance(value, str):
            value = _CONCEPT_ID.sub(lambda match: self.labels.get(match.group(0), "<unknown id>"), value)
        return super().normal(value)


class ConceptParityTestCase(SyncParityTestCase):
    def setUp(self) -> None:
        super().setUp()
        leg, ref = ConceptSide(self.leg.db), ConceptSide(self.ref.db)
        self.devices = {id(leg): self.devices[id(self.leg)], id(ref): self.devices[id(self.ref)]}
        self.leg, self.ref = leg, ref

    def entity_type(self, operation: str) -> str:
        return "concept" if operation in _CONCEPT_OPERATIONS else super().entity_type(operation)

    def concept_both(self, label: str, description: str = "") -> tuple[str, str]:
        return self.leg.concept(label, description), self.ref.concept(label, description)

    def new_concept_id(self, label: str) -> str:
        concept_id = "C-" + uuid.uuid4().hex.upper()
        for side in self.sides():
            side.label(concept_id, f"<C {label}>")
        return concept_id

    def same_concept(self, call: str, *pairs: tuple[Any, Any], **kwargs: Any) -> Any:
        mine = getattr(self.leg.db, call)(*(pair[0] for pair in pairs), **kwargs)
        theirs = getattr(self.ref.db, call)(*(pair[1] for pair in pairs), **kwargs)
        self.assertEqual(self.leg.normal(mine), self.ref.normal(theirs), call)
        return mine

    def refused_concept(self, call: str, *pairs: tuple[Any, Any], **kwargs: Any) -> str:
        """``call`` raises one ``ResearchError`` (code, message, status) on both sides; returns its code."""
        errors = []
        for index, side in enumerate(self.sides()):
            with self.assertRaises(ResearchError) as caught:
                getattr(side.db, call)(*(pair[index] for pair in pairs), **kwargs)
            errors.append((caught.exception.code, str(caught.exception), caught.exception.http_status))
        self.assertEqual(errors[0], errors[1], call)
        return errors[0][0]


@parametrize_backends
class ConceptFacadeParityTests(ConceptParityTestCase):
    def test_create_read_and_list(self) -> None:
        ethics = self.concept_both("Ethics", "The study of *conduct*.")
        self.concept_both("logic")
        for args in (("ETHICS",), ("  ",), ("a\tb\x01",), ("x" * 161,), ("Ok", "bad\x00")):
            with self.subTest(args=[arg[:8] for arg in args]):
                self.refused_concept("create_concept", *(both(arg) for arg in args))
        self.same_concept("get_concept", ethics)
        self.same_concept("get_concept", both(_MISSING))
        self.same_concept("get_concept", both("  "))
        self.same_concept("get_concept_sync_state", ethics)
        self.same_concept("get_concept_sync_state", both(_MISSING))
        self.same("list_concepts")

    def test_identity_and_definition(self) -> None:
        ethics, logic = self.concept_both("Ethics"), self.concept_both("Logic")
        self.same_concept("replace_concept_aliases", ethics, both(["Morals", "morals", "ethics", "Moral  code"]))
        self.same_concept("update_concept", ethics, name="Moral philosophy", description="About")
        self.same_concept("update_concept", ethics, name="MORALS")
        self.same_concept("update_concept", ethics, name="Morals")
        for pair, kwargs in ((logic, {"name": "ethics"}), (logic, {"name": "moral philosophy"}),
                             (ethics, {}), (both(_MISSING), {"name": "X"}), (both(" "), {"name": "X"}),
                             (ethics, {"description": "\x00"}), (ethics, {"name": ""})):
            with self.subTest(kwargs=kwargs):
                self.refused_concept("update_concept", pair, **kwargs)
        for aliases in (["LOGIC"], "Logic", [1], ["\x07"]):
            with self.subTest(aliases=aliases):
                self.refused_concept("replace_concept_aliases", ethics, both(aliases))
        self.refused_concept("replace_concept_aliases", logic, both(["moral code"]))
        self.refused_concept("replace_concept_aliases", both(_MISSING), both([]))
        self.same_concept("get_concept_sync_state", ethics)
        self.same_concept("get_concept_sync_state", logic)
        self.same("list_concepts")

    def test_hierarchy(self) -> None:
        a, b, c = self.concept_both("A"), self.concept_both("B"), self.concept_both("C")
        self.same_concept("replace_concept_parents", b, ([a[0]], [a[1]]))
        self.same_concept("replace_concept_parents", c, ([b[0], a[0], " " + a[0]], [b[1], a[1], " " + a[1]]))
        for pair, parents in ((a, (c[0], c[1])), (a, (a[0], a[1])), (a, (_MISSING, _MISSING))):
            with self.subTest(parents=parents):
                self.refused_concept("replace_concept_parents", pair, ([parents[0]], [parents[1]]))
        for parents in ("A", [""], [3]):
            with self.subTest(parents=parents):
                self.refused_concept("replace_concept_parents", a, both(parents))
        self.refused_concept("replace_concept_parents", both(_MISSING), both([]))
        for concept in (a, b, c):
            self.same_concept("get_concept", concept)
            self.same_concept("get_concept_sync_state", concept)
        self.same("list_concepts")

    def test_delete(self) -> None:
        a, b, c = self.concept_both("A"), self.concept_both("B"), self.concept_both("C")
        self.same_concept("replace_concept_parents", b, ([a[0]], [a[1]]))
        self.same_concept("replace_concept_parents", c, ([b[0]], [b[1]]))
        work = self.work_both("W")
        self.send_both("SET_WORK_RESEARCH_NOTE", work, {"text": "On [[concept:b]]."}, 0)
        self.refused_concept("delete_concept", b)
        self.send_both("SET_WORK_RESEARCH_NOTE", work, {"text": "On `[[concept:b]]`."}, 1)
        self.same_concept("delete_concept", b)
        self.refused_concept("delete_concept", b)
        self.refused_concept("delete_concept", both(" "))
        self.same_concept("get_concept", a)
        self.same_concept("get_concept", c)
        self.same_concept("get_concept_sync_state", a)
        self.same_concept("get_concept_sync_state", c)
        self.same("list_concepts")


@parametrize_backends
class ConceptSyncParityTests(ConceptParityTestCase):
    def create(self, label: str, description: str = "") -> str:
        concept_id = self.new_concept_id(label)
        status, _ = self.send_both("CREATE_CONCEPT", concept_id, {"name": label, "description": description})
        self.assertEqual(status, 200)
        return concept_id

    def test_create(self) -> None:
        concept_id = self.new_concept_id("Ethics")
        op_id = str(uuid.uuid4())
        payload = {"name": "  Ethics ", "description": "About"}
        self.assertEqual(self.send_both("CREATE_CONCEPT", concept_id, payload, op_id=op_id)[0], 200)
        self.send_both("CREATE_CONCEPT", concept_id, payload, op_id=op_id)  # a replay
        self.send_both("CREATE_CONCEPT", concept_id, {"name": "Other", "description": ""})
        self.send_both("CREATE_CONCEPT", self.new_concept_id("Clash"), {"name": "ETHICS", "description": ""})
        self.send_both("CREATE_CONCEPT", "C-1", payload)
        self.send_both("CREATE_CONCEPT", self.new_concept_id("Empty"), {"name": "  ", "description": ""})
        self.send_both("CREATE_CONCEPT", self.new_concept_id("Extra"), {**payload, "x": 1})
        self.send_both("CREATE_CONCEPT", self.new_concept_id("Base"), payload, base=0)
        self.same("list_concepts")

    def test_field(self) -> None:
        concept_id = self.create("Ethics")
        field = {"field": "description", "value": "First"}
        self.send_both("SET_CONCEPT_FIELD", concept_id, field, 0)
        self.send_both("SET_CONCEPT_FIELD", concept_id, field, 0)  # a stale base, the same value
        self.send_both("SET_CONCEPT_FIELD", concept_id, {"field": "description", "value": "Other"}, 0)
        self.send_both("SET_CONCEPT_FIELD", concept_id, {"field": "description", "value": "Other"}, 5)
        self.send_both("SET_CONCEPT_FIELD", concept_id, {"field": "name", "value": "X"}, 1)
        self.send_both("SET_CONCEPT_FIELD", concept_id, {"field": "description", "value": "\x07"}, 1)
        self.send_both("SET_CONCEPT_FIELD", _MISSING, field, 0)
        self.send_both("SET_CONCEPT_FIELD", concept_id, field, None)
        self.same_concept("get_concept_sync_state", both(concept_id))

    def test_identity(self) -> None:
        ethics, logic = self.create("Ethics"), self.create("Logic")
        self.send_both("SET_CONCEPT_IDENTITY", ethics, {"name": "Ethics", "aliases": ["Morals"]}, 0)
        self.send_both("SET_CONCEPT_IDENTITY", ethics, {"name": "Moral philosophy", "aliases": ["Morals"]}, 1)
        self.send_both("SET_CONCEPT_IDENTITY", ethics, {"name": "Ethics", "aliases": []}, 1)  # stale
        self.send_both("SET_CONCEPT_IDENTITY", ethics, {"name": "Ethics", "aliases": []}, 9)  # future
        self.send_both("SET_CONCEPT_IDENTITY", logic, {"name": "MORALS", "aliases": []}, 0)
        self.send_both("SET_CONCEPT_IDENTITY", logic, {"name": "Logic", "aliases": ["ethics"]}, 0)
        self.send_both("SET_CONCEPT_IDENTITY", logic, {"name": "Logic", "aliases": ["x" * 161]}, 0)
        self.send_both("SET_CONCEPT_IDENTITY", logic, {"name": "Logic", "aliases": ["a"] * 65}, 0)
        self.send_both("SET_CONCEPT_IDENTITY", _MISSING, {"name": "X", "aliases": []}, 0)
        self.send_both("SET_CONCEPT_IDENTITY", logic, {"name": "Logic", "aliases": []}, None)
        for concept_id in (ethics, logic):
            self.same_concept("get_concept_sync_state", both(concept_id))

    def test_parents(self) -> None:
        a, b, c = self.create("A"), self.create("B"), self.create("C")
        self.send_both("SET_CONCEPT_PARENTS", c, {"parent_ids": [a]}, 0)
        self.send_both("SET_CONCEPT_PARENTS", c, {"parent_ids": [a]}, 0)  # stale, the same set
        self.send_both("SET_CONCEPT_PARENTS", c, {"parent_ids": [b]}, 0)  # stale, another set
        self.send_both("SET_CONCEPT_PARENTS", c, {"parent_ids": [b, a]}, 1)
        self.send_both("SET_CONCEPT_PARENTS", a, {"parent_ids": [c]}, 1)  # a cycle
        self.send_both("SET_CONCEPT_PARENTS", a, {"parent_ids": [_MISSING]}, 1)
        self.send_both("SET_CONCEPT_PARENTS", a, {"parent_ids": [" " + b]}, 1)
        self.send_both("SET_CONCEPT_PARENTS", a, {"parent_ids": [b]}, 7)
        self.send_both("SET_CONCEPT_PARENTS", _MISSING, {"parent_ids": []}, 0)
        for concept_id in (a, b, c):
            self.same_concept("get_concept_sync_state", both(concept_id))

    def test_delete(self) -> None:
        a, b = self.create("A"), self.create("B")
        self.send_both("SET_CONCEPT_PARENTS", b, {"parent_ids": [a]}, 0)
        work = self.work_both("W")
        self.send_both("SET_WORK_RESEARCH_NOTE", work, {"text": "[[concept:A]]"}, 0)
        self.send_both("DELETE_CONCEPT", a, {})
        self.send_both("SET_WORK_RESEARCH_NOTE", work, {"text": ""}, 1)
        self.send_both("DELETE_CONCEPT", a, {})
        self.send_both("DELETE_CONCEPT", a, {})
        self.send_both("DELETE_CONCEPT", b, {"x": 1})
        self.send_both("DELETE_CONCEPT", b, {}, base=0)
        self.same_concept("get_concept_sync_state", both(b))
        self.same("list_concepts")
