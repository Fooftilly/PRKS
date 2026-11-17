"""Fuzzing the sync operation envelope and every family's payload validator.

``backend.sync.protocol.validate_operation`` is everything ``POST
/api/sync/operations`` decides before it touches the ledger, and it needs no
database. These properties feed it arbitrary JSON and, to reach the family
validators' deeper branches, mutations of real envelopes recorded from the
PostgreSQL sync suites (``tests/fixtures/sync_envelope_seeds.json``).

The contract checked for every input:

* the answer is a refusal ``(400, {"code": c})`` with ``c`` in
  ``REFUSAL_CODES`` -- never another exception (a 500 on the wire), and never a
  library's message as the code;
* an accepted operation can be stored: no NUL, no lone surrogate, valid UTF-8,
  and JSON PostgreSQL ``jsonb`` takes (no NaN or Infinity);
* normalizing is idempotent and the request hash ignores key order, so a
  retried operation is recognized as the same one.

Profiles and replay: ``tests/hypothesis_profiles.py``. On PostgreSQL the
surrogate refusal is pinned by ``tests/pg/test_sync_protocol.py``.
"""

from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path
from typing import Any

from hypothesis import example, given, strategies as st

from backend import sync_protocol as rules
from backend.sync import protocol
from tests.hypothesis_profiles import FUZZ

_SEEDS_PATH = Path(__file__).resolve().parent / "fixtures" / "sync_envelope_seeds.json"
SEEDS: list[dict[str, Any]] = json.loads(_SEEDS_PATH.read_text(encoding="utf-8"))["envelopes"]

OP_ID = "00000000-0000-4000-8000-000000000001"
DEVICE_ID = "00000000-0000-4000-8000-000000000002"
TAG_ID = "T-" + "0" * 32

# Characters that broke or nearly broke a boundary before: NUL and lone
# surrogates (PostgreSQL cannot store them), whitespace PRKS folds, a BOM, a
# combining mark (NFC), and astral and right-to-left text.
SPECIAL_CHARS = ("\x00", "\ud800", "\udfff", "\udbff\udc00", " ", "\t", "\n", "\u00a0", "\u2028",
                 "\u3000", "\ufeff", "\u0301", "\U0001f600", "\u202e", "[[", "]]", "\\", '"')

nasty_text = st.lists(st.one_of(st.characters(), st.sampled_from(SPECIAL_CHARS)), max_size=12).map("".join)
_scalars = st.one_of(
    st.none(), st.booleans(),
    st.integers(min_value=-(2**64), max_value=2**64),
    st.sampled_from((0, -1, 1, rules.MAX_REVISION, rules.MAX_REVISION + 1)),
    st.floats(allow_nan=True, allow_infinity=True),
    nasty_text,
)
# What json.loads can produce (it accepts NaN, Infinity and \\ud800 escapes).
json_values = st.recursive(
    _scalars,
    lambda inner: st.one_of(st.lists(inner, max_size=4),
                            st.dictionaries(nasty_text, inner, max_size=4)),
    max_leaves=12,
)


def _key_pool() -> list[str]:
    keys: set[str] = set(rules.FIELDS)
    stack: list[Any] = list(SEEDS)
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            keys.update(node)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return sorted(keys)


KEY_POOL = _key_pool()


def _slots(node: Any) -> list[tuple[Any, Any]]:
    """Every (container, key-or-index) in ``node``, the root's fields included."""
    out: list[tuple[Any, Any]] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                out.append((current, key))
                stack.append(value)
        elif isinstance(current, list):
            for index, value in enumerate(current):
                out.append((current, index))
                stack.append(value)
    return out


@st.composite
def mutated_seeds(draw: st.DrawFn) -> dict[str, Any]:
    envelope = copy.deepcopy(draw(st.sampled_from(SEEDS)))
    for _ in range(draw(st.integers(min_value=1, max_value=3))):
        slots = _slots(envelope)
        container, key = draw(st.sampled_from(slots))
        current = container[key]
        kind = draw(st.sampled_from(("replace", "edit_text", "drop", "add", "swap_seed_value")))
        if kind == "replace":
            container[key] = draw(json_values)
        elif kind == "edit_text" and isinstance(current, str):
            insert = draw(st.sampled_from(SPECIAL_CHARS))
            where = draw(st.integers(min_value=0, max_value=len(current)))
            container[key] = current[:where] + insert + current[where:]
        elif kind == "drop":
            del container[key]
        elif kind == "add" and isinstance(current, dict):
            current[draw(st.one_of(st.sampled_from(KEY_POOL), nasty_text))] = draw(json_values)
        elif kind == "swap_seed_value":
            # A well-typed value from another family: reaches checks a random
            # value of the wrong type stops before.
            donor = draw(st.sampled_from(SEEDS))
            donor_slots = [(c, k) for c, k in _slots(donor) if k == key]
            if donor_slots:
                c, k = draw(st.sampled_from(donor_slots))
                container[key] = copy.deepcopy(c[k])
    return envelope


def _storable(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" not in value and not any("\ud800" <= ch <= "\udfff" for ch in value)
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_storable(k) and _storable(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_storable(v) for v in value)
    return True


def _envelope(operation: str, entity_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"op_id": OP_ID, "device_id": DEVICE_ID, "operation": operation,
            "entity_type": "tag", "entity_id": entity_id, "payload": payload,
            "base_revision": None, "depends_on": [],
            "occurred_at": "2026-01-01T00:00:00Z", "created_at": "2026-01-01T00:00:00Z"}


class SyncEnvelopePropertyTests(unittest.TestCase):
    def check(self, data: Any) -> None:
        result = protocol.validate_operation(copy.deepcopy(data))
        if isinstance(result, tuple):
            status, body = result
            self.assertEqual(status, 400)
            self.assertEqual(set(body), {"code"})
            self.assertIn(body["code"], protocol.REFUSAL_CODES)
            return
        op = result
        self.assertTrue(_storable(op), op)
        json.dumps(op, ensure_ascii=False, allow_nan=False).encode("utf-8")
        again = protocol.validate_operation(copy.deepcopy(op))
        self.assertIsInstance(again, dict)
        assert isinstance(again, dict)
        self.assertEqual(rules.request_hash(again), rules.request_hash(op))
        reordered = dict(reversed(list(op.items())))
        self.assertEqual(rules.request_hash(reordered), rules.request_hash(op))

    def test_every_operation_has_a_handler_and_a_seed_that_still_validates(self) -> None:
        self.assertEqual(protocol.supported_operations(), rules.supported_operations())
        self.assertEqual({seed["operation"] for seed in SEEDS}, set(rules.supported_operations()))
        for seed in SEEDS:
            with self.subTest(operation=seed["operation"]):
                self.assertIsInstance(protocol.validate_operation(copy.deepcopy(seed)), dict)

    def test_every_seed_slot_takes_every_wrong_shape(self) -> None:
        """Deterministic sweep: each value in each seed, swapped for each awkward JSON shape."""
        shapes: tuple[Any, ...] = ([], [[]], {}, None, True, 1.5, float("nan"), float("inf"),
                                   -1, 2**70, "", " ", "\x00", "\ud800")
        for seed in SEEDS:
            for index in range(len(_slots(seed))):
                for shape in shapes:
                    envelope = copy.deepcopy(seed)
                    container, key = _slots(envelope)[index]
                    container[key] = shape
                    with self.subTest(operation=seed["operation"], key=key, shape=repr(shape)):
                        self.check(envelope)

    @FUZZ
    @given(data=json_values)
    def test_any_json_body_is_refused_with_a_known_code(self, data: Any) -> None:
        self.check(data)

    @FUZZ
    @given(data=mutated_seeds())
    # A lone surrogate in an id no validator inspects reached psycopg (a 500).
    @example(data=_envelope("DELETE_TAG", "T-\ud800", {}))
    # The UTF-8 length check raised UnicodeEncodeError, answered {"code": "utf-8"}.
    @example(data=_envelope("CREATE_TAG", TAG_ID, {"name": "a\ud800", "color": "#000000"}))
    @example(data=_envelope("CREATE_TAG", TAG_ID, {"name": "a\x00", "color": "#000000"}))
    # Found by the nightly profile: a list where a role name belongs raised
    # TypeError (unhashable) in the set lookup, a 500.
    @example(data={**_envelope("ADD_WORK_PERSON_ROLE", "W-" + "0" * 32,
                               {"person_id": "P-" + "0" * 32, "role_type": [], "credit_name": ""}),
                   "entity_type": "work", "base_revision": 0})
    def test_mutated_envelopes_are_refused_or_storable(self, data: dict[str, Any]) -> None:
        self.check(data)


if __name__ == "__main__":
    unittest.main()
