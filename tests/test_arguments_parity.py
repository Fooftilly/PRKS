"""Positions, Arguments and Stances: PostgreSQL answers as SQLite does (L-R-1 #634).

Each call, and each Position and Argument semantic operation, runs on this
leg's library and on a reference SQLite library holding the same records,
and the results must be equal after generated ids and timestamps become
labels. Inputs avoid the documented differences
(``backend/pg_facade/research.py``): no Work has a second Version, no
record is created again under a deleted id, and no revision state is
compared after a cited Work's delete.
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
from tests.test_concepts_parity import ConceptParityTestCase, ConceptSide
from tests.test_tags_parity import both

_RESEARCH_ID = re.compile(r"\b[PA]-[0-9A-F]{32}\b")
_POSITION_OPERATIONS = frozenset({"CREATE_POSITION", "SET_POSITION_FIELD", "DELETE_POSITION"})
_ARGUMENT_OPERATIONS = frozenset({"CREATE_ARGUMENT", "SET_ARGUMENT_FIELD", "SET_ARGUMENT_SOURCES",
                                  "SET_ARGUMENT_TARGETS", "DELETE_ARGUMENT"})
_MISSING_POSITION = "P-" + "0" * 32
_MISSING_ARGUMENT = "A-" + "0" * 32
_MISSING_WORK = "W-" + "0" * 32


class ArgumentSide(ConceptSide):
    """A side whose labels cover Positions, Arguments and Persons too."""

    def position(self, label: str, description: str = "") -> str:
        return self.label(self.db.create_position(label, description)["id"], f"<P {label}>")

    def argument(self, label: str, kind: str = "argument", **lists: Any) -> str:
        return self.label(self.db.create_argument(name=label, kind=kind, **lists)["id"], f"<A {label}>")

    def normal(self, value: Any) -> Any:
        if isinstance(value, str):
            value = _RESEARCH_ID.sub(lambda match: self.labels.get(match.group(0), "<unknown id>"), value)
        return super().normal(value)


class ArgumentParityTestCase(ConceptParityTestCase):
    def setUp(self) -> None:
        super().setUp()
        leg, ref = ArgumentSide(self.leg.db), ArgumentSide(self.ref.db)
        self.devices = {id(leg): self.devices[id(self.leg)], id(ref): self.devices[id(self.ref)]}
        self.leg, self.ref = leg, ref

    def entity_type(self, operation: str) -> str:
        if operation in _POSITION_OPERATIONS:
            return "position"
        if operation in _ARGUMENT_OPERATIONS:
            return "argument"
        return super().entity_type(operation)

    def position_both(self, label: str, description: str = "") -> tuple[str, str]:
        return self.leg.position(label, description), self.ref.position(label, description)

    def argument_both(self, label: str, kind: str = "argument", **lists: Any) -> tuple[str, str]:
        """``lists`` holds per-side pairs (``sources=(mine, theirs)``)."""
        return (self.leg.argument(label, kind, **{key: pair[0] for key, pair in lists.items()}),
                self.ref.argument(label, kind, **{key: pair[1] for key, pair in lists.items()}))

    def refused_split(self, call: str, *pairs: tuple[Any, Any], **pair_kwargs: tuple[Any, Any]) -> str:
        """``refused_concept`` whose keyword arguments are per-side pairs too."""
        errors = []
        for index, side in enumerate(self.sides()):
            with self.assertRaises(ResearchError) as caught:
                getattr(side.db, call)(*(pair[index] for pair in pairs),
                                       **{key: pair[index] for key, pair in pair_kwargs.items()})
            errors.append((caught.exception.code, str(caught.exception), caught.exception.http_status))
        self.assertEqual(errors[0], errors[1], call)
        return errors[0][0]

    def new_id(self, prefix: str, label: str) -> str:
        entity_id = f"{prefix}-" + uuid.uuid4().hex.upper()
        for side in self.sides():
            side.label(entity_id, f"<{prefix} {label}>")
        return entity_id


def sources(*entries: tuple[tuple[str, str], str]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Per-side citation lists from ``(work_pair, pages)`` entries."""
    return ([{"work_id": work[0], "pages": pages} for work, pages in entries],
            [{"work_id": work[1], "pages": pages} for work, pages in entries])


def targets(*entries: tuple[str, tuple[str, str], str]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Per-side target lists from ``(type, id_pair, verdict)`` entries."""
    return ([{"type": kind, "id": ids[0], "verdict_id": verdict} for kind, ids, verdict in entries],
            [{"type": kind, "id": ids[1], "verdict_id": verdict} for kind, ids, verdict in entries])


@parametrize_backends
class ArgumentFacadeParityTests(ArgumentParityTestCase):
    def test_positions(self) -> None:
        claim = self.position_both("  A   claim ", "About")
        self.position_both("b claim")
        for args in (("  ",), ("a\x01",), ("x" * 241,), ("Ok", "\x07")):
            with self.subTest(args=[arg[:8] for arg in args]):
                self.refused_concept("create_position", *(both(arg) for arg in args))
        self.same_concept("update_position", claim, name="Renamed")
        self.same_concept("update_position", claim, name="Renamed", description="New")
        for pair, kwargs in ((claim, {}), (both(_MISSING_POSITION), {"name": "X"}), (both(" "), {"name": "X"}),
                             (claim, {"name": ""}), (claim, {"description": 3})):
            with self.subTest(kwargs=kwargs):
                self.refused_concept("update_position", pair, **kwargs)
        answer = self.argument_both("Answer", targets=targets(("position", claim, "opposes")))
        self.same_concept("get_position", claim)
        self.same_concept("get_position", both(_MISSING_POSITION))
        self.same_concept("get_position_sync_state", claim)
        self.same("list_positions")
        self.refused_concept("delete_position", claim)
        self.same_concept("replace_argument_targets", answer, both([]))
        self.same_concept("delete_position", claim)
        self.refused_concept("delete_position", claim)
        self.same("list_positions")

    def test_arguments_and_their_lists(self) -> None:
        one, two = self.work_both("One"), self.work_both("Two")
        author = self.leg.person("Plato"), self.ref.person("Plato")
        self.same_concept("add_role", author, one, both("Author"))
        claim = self.position_both("Claim")
        base = self.argument_both("Base", "stance")
        reply = self.argument_both("Reply", sources=sources((one, " 514a "), (two, "")),
                                   targets=targets(("argument", base, "opposes"), ("position", claim, "supports")))
        for pair in (base, reply):
            self.same_concept("get_argument", pair)
            self.same_concept("get_argument_sync_state", pair)
        self.same_concept("get_argument", both(_MISSING_ARGUMENT))
        self.same_concept("get_position", claim)
        self.same("list_arguments")
        self.same("list_arguments", "stance")
        self.same("list_verdicts")
        self.refused_concept("list_arguments", both("essay"))
        self.same_concept("update_argument", reply, name="Reply 2", kind="stance", main_text="Body")
        self.refused_concept("update_argument", reply, name="X", kind="essay")
        self.refused_concept("update_argument", reply)
        self.refused_concept("update_argument", both(_MISSING_ARGUMENT), name="X")
        self.same_concept("replace_argument_sources", reply, sources((two, "3"), (one, "514a")))
        for listed in (both("x"), both([3]), both([{"pages": ""}]), sources((one, ""), (one, "2")),
                       sources((one, "x" * 101)), both([{"work_id": _MISSING_WORK, "pages": ""}])):
            with self.subTest(sources=str(listed[0])[:30]):
                self.refused_concept("replace_argument_sources", reply, listed)
        self.same_concept("replace_argument_targets", reply, targets(("position", claim, "holds"),
                                                                     ("argument", base, "opposes")))
        for listed in (both("x"), both([3]), both([{"type": "work", "id": "x", "verdict_id": "holds"}]),
                       both([{"type": "position", "id": " ", "verdict_id": "holds"}]),
                       targets(("position", claim, "")), targets(("position", claim, "maybe")),
                       targets(("position", claim, "holds"), ("position", claim, "opposes")),
                       targets(("position", both(_MISSING_POSITION), "holds")),
                       targets(("argument", both("A-x!"), "holds")),
                       targets(("argument", both(_MISSING_ARGUMENT), "holds")),
                       targets(("argument", reply, "holds"))):
            with self.subTest(targets=str(listed[0])[:40]):
                self.refused_concept("replace_argument_targets", reply, listed)
        self.refused_concept("replace_argument_targets", base, targets(("argument", reply, "holds")))
        for pair in (base, reply):
            self.same_concept("get_argument", pair)
            self.same_concept("get_argument_sync_state", pair)
        self.same("list_arguments")

    def test_refused_creates(self) -> None:
        one = self.work_both("One")
        claim = self.position_both("Claim")
        for lists in ({"name": both(" ")}, {"kind": both("essay")}, {"main_text": both("\x07")},
                      {"sources": both("x")}, {"sources": both([{"work_id": _MISSING_WORK}])},
                      {"targets": targets(("position", claim, "maybe"))},
                      {"sources": sources((one, "")),
                       "targets": targets(("argument", both(_MISSING_ARGUMENT), "holds"))}):
            with self.subTest(lists=sorted(lists)):
                self.refused_split("create_argument", **{"name": both("X"), "kind": both("argument"), **lists})
        self.same("list_arguments")

    def test_delete(self) -> None:
        a = self.argument_both("A")
        b = self.argument_both("B", targets=targets(("argument", a, "supports")))
        self.refused_concept("delete_argument", a)
        self.same_concept("replace_argument_targets", b, both([]))
        work = self.work_both("W")
        self.send_both("SET_WORK_RESEARCH_NOTE", work, ({"text": f"[[argument:{a[0]}]]"},
                                                       {"text": f"[[argument:{a[1]}]]"}), 0)
        self.refused_concept("delete_argument", a)
        self.send_both("SET_WORK_RESEARCH_NOTE", work, ({"text": f"`[[argument:{a[0]}]]`"},
                                                       {"text": f"`[[argument:{a[1]}]]`"}), 1)
        self.same_concept("delete_argument", a)
        self.refused_concept("delete_argument", a)
        self.refused_concept("delete_argument", both(" "))
        self.same_concept("get_argument", b)
        self.same("list_arguments")

    def test_a_work_delete_drops_its_citations(self) -> None:
        one, two = self.work_both("One"), self.work_both("Two")
        a = self.argument_both("A", sources=sources((one, "1"), (two, "")))
        self.leg.db.delete_work_record(one[0])
        self.ref.db.delete_work_record(one[1])
        self.same_concept("get_argument", a)
        self.same("list_arguments")


@parametrize_backends
class ArgumentSyncParityTests(ArgumentParityTestCase):
    def test_positions(self) -> None:
        position_id = self.new_id("P", "Claim")
        payload = {"name": "  A   claim ", "description": ""}
        op_id = str(uuid.uuid4())
        self.send_both("CREATE_POSITION", position_id, payload, op_id=op_id)
        self.send_both("CREATE_POSITION", position_id, payload, op_id=op_id)  # a replay
        self.send_both("CREATE_POSITION", position_id, {"name": "Other", "description": ""})
        self.send_both("CREATE_POSITION", "P-1", payload)
        self.send_both("CREATE_POSITION", self.new_id("P", "Bad"), {"name": " ", "description": ""})
        self.send_both("CREATE_POSITION", self.new_id("P", "Base"), payload, base=0)
        field = {"field": "name", "value": "B"}
        self.send_both("SET_POSITION_FIELD", position_id, field, 0)
        self.send_both("SET_POSITION_FIELD", position_id, field, 0)  # stale, the same value
        self.send_both("SET_POSITION_FIELD", position_id, {"field": "name", "value": "C"}, 0)
        self.send_both("SET_POSITION_FIELD", position_id, {"field": "name", "value": "C"}, 3)
        self.send_both("SET_POSITION_FIELD", position_id, {"field": "kind", "value": "C"}, 1)
        self.send_both("SET_POSITION_FIELD", _MISSING_POSITION, field, 0)
        self.same_concept("get_position_sync_state", both(position_id))
        argument_id = self.new_id("A", "Answer")
        self.send_both("CREATE_ARGUMENT", argument_id, {
            "name": "Answer", "kind": "argument", "main_text": "", "sources": [],
            "targets": [{"type": "position", "id": position_id, "verdict_id": "holds"}]})
        self.send_both("DELETE_POSITION", position_id, {})
        self.send_both("DELETE_ARGUMENT", argument_id, {})
        self.send_both("DELETE_POSITION", position_id, {})
        self.send_both("DELETE_POSITION", position_id, {})
        self.send_both("DELETE_POSITION", position_id, {"x": 1})
        self.same("list_positions")

    def test_arguments(self) -> None:
        one, two = self.work_both("One"), self.work_both("Two")
        claim = self.position_both("Claim")
        base = self.argument_both("Base")
        argument_id = self.new_id("A", "Reply")

        def payload(**lists: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            fields = {"name": "Reply", "kind": "argument", "main_text": ""}
            return ({**fields, "sources": [], "targets": [], **{k: v[0] for k, v in lists.items()}},
                    {**fields, "sources": [], "targets": [], **{k: v[1] for k, v in lists.items()}})

        self.send_both("CREATE_ARGUMENT", argument_id, payload(sources=sources((one, "9"))))
        self.send_both("CREATE_ARGUMENT", argument_id, payload())  # converges
        self.send_both("CREATE_ARGUMENT", self.new_id("A", "Missing"),
                       payload(sources=both([{"work_id": _MISSING_WORK, "pages": ""}])))
        self.send_both("CREATE_ARGUMENT", self.new_id("A", "Unknown"),
                       payload(targets=targets(("position", both(_MISSING_POSITION), "holds"))))
        self.send_both("CREATE_ARGUMENT", self.new_id("A", "Verdict"),
                       payload(targets=targets(("position", claim, "maybe"))))
        self.send_both("CREATE_ARGUMENT", self.new_id("A", "Dup"), payload(sources=sources((one, ""), (one, ""))))
        self.send_both("CREATE_ARGUMENT", self.new_id("A", "Kind"), (payload()[0] | {"kind": "essay"},
                                                                       payload()[1] | {"kind": "essay"}))
        self.send_both("SET_ARGUMENT_FIELD", argument_id, {"field": "main_text", "value": "Body"}, 0)
        self.send_both("SET_ARGUMENT_FIELD", argument_id, {"field": "main_text", "value": "Other"}, 0)
        self.send_both("SET_ARGUMENT_FIELD", argument_id, {"field": "kind", "value": "stance"}, 0)
        listed = sources((two, ""), (one, "9"))
        self.send_both("SET_ARGUMENT_SOURCES", argument_id, ({"sources": listed[0]}, {"sources": listed[1]}), 0)
        self.send_both("SET_ARGUMENT_SOURCES", argument_id, ({"sources": listed[0]}, {"sources": listed[1]}), 0)
        stale = sources((one, ""))
        self.send_both("SET_ARGUMENT_SOURCES", argument_id, ({"sources": stale[0]}, {"sources": stale[1]}), 0)
        self.send_both("SET_ARGUMENT_SOURCES", argument_id, ({"sources": stale[0]}, {"sources": stale[1]}), 5)
        missing = {"sources": [{"work_id": _MISSING_WORK, "pages": ""}]}
        self.send_both("SET_ARGUMENT_SOURCES", argument_id, missing, 1)
        chosen = targets(("argument", base, "opposes"), ("position", claim, "supports"))
        self.send_both("SET_ARGUMENT_TARGETS", argument_id, ({"targets": chosen[0]}, {"targets": chosen[1]}), 0)
        back = targets(("argument", (argument_id, argument_id), "opposes"))
        self.send_both("SET_ARGUMENT_TARGETS", base, ({"targets": back[0]}, {"targets": back[1]}), 0)
        self.send_both("SET_ARGUMENT_TARGETS", argument_id, ({"targets": chosen[0][:1]},
                                                             {"targets": chosen[1][:1]}), 0)
        self.send_both("SET_ARGUMENT_TARGETS", _MISSING_ARGUMENT, {"targets": []}, 0)
        self.send_both("DELETE_ARGUMENT", base, {})
        self.same_concept("get_argument_sync_state", both(argument_id))
        self.same_concept("get_argument", both(argument_id))
        self.send_both("DELETE_ARGUMENT", argument_id, {})
        self.send_both("DELETE_ARGUMENT", base, {})
        self.send_both("DELETE_ARGUMENT", base, {}, base=0)
        self.same("list_arguments")
