"""Exact rules of the shared sync envelope (``backend.sync.envelope``), no database.

The property tests (``test_sync_envelope_properties.py``) prove every input is
refused with a known code or stored safely. These pin the exact answer: which
code each rule raises, each bound's edge, and the canonical form the request
hash is taken over. Mutation testing (``scripts/ci/mutation_testing.py``,
target ``sync-envelope``) found those unpinned.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import time
import unittest
from collections.abc import Iterator
from typing import Any

from backend.sync import envelope as rules


@contextlib.contextmanager
def _local_time_zone(name: str) -> Iterator[None]:
    """Run with ``name`` as the process's local time zone (POSIX ``TZ``)."""
    previous = os.environ.get("TZ")
    try:
        os.environ["TZ"] = name
        time.tzset()
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


OP_ID = "0000000a-0000-4000-8000-00000000000b"
DEVICE_ID = "0000000c-0000-4000-8000-00000000000d"
DEP_A = "0000000e-0000-4000-8000-000000000001"
DEP_B = "0000000e-0000-4000-8000-000000000002"


def _envelope(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "op_id": OP_ID, "device_id": DEVICE_ID, "operation": "CREATE_TAG",
        "entity_type": "tag", "entity_id": "T-1", "payload": {"name": "x"},
        "base_revision": None, "occurred_at": "2026-01-02T03:04:05Z",
        "created_at": "2026-01-02T03:04:05+00:00", "depends_on": [],
    }
    data.update(overrides)
    return data


class EnvelopeRuleTests(unittest.TestCase):
    def assertRefused(self, data: object, code: str = "INVALID_ENVELOPE") -> None:
        with self.assertRaises(ValueError) as raised:
            rules.normalize_envelope(data)
        self.assertEqual(raised.exception.args, (code,))

    def test_canonical_form(self):
        self.assertEqual(rules.normalize_envelope(_envelope(
            op_id=OP_ID.upper(), device_id=DEVICE_ID.upper(),
            occurred_at="2026-01-02T05:04:05+02:00", created_at="2026-01-02T03:04:05Z",
            depends_on=[DEP_A.upper(), DEP_B], base_revision=7,
        )), {
            "op_id": OP_ID, "device_id": DEVICE_ID, "operation": "CREATE_TAG",
            "entity_type": "tag", "entity_id": "T-1", "payload": {"name": "x"},
            "base_revision": 7, "occurred_at": "2026-01-02T03:04:05+00:00",
            "created_at": "2026-01-02T03:04:05+00:00", "depends_on": [DEP_A, DEP_B],
        })

    def test_each_shared_field_rule_raises_invalid_envelope(self):
        missing = _envelope()
        del missing["payload"]
        for label, data in (
            ("not a dict", []),
            ("missing field", missing),
            ("extra field", {**_envelope(), "x": 1}),
            ("op_id not a uuid", _envelope(op_id="nope")),
            ("device_id not a string", _envelope(device_id=1)),
            ("unknown operation", _envelope(operation="NOPE")),
            ("operation not a string", _envelope(operation=1)),
            ("wrong entity type", _envelope(entity_type="work")),
            ("payload not an object", _envelope(payload=[])),
            ("entity id not a string", _envelope(entity_id=1)),
            ("blank entity id", _envelope(entity_id="  ")),
            ("padded entity id", _envelope(entity_id=" T-1")),
            ("long entity id", _envelope(entity_id="T" * (rules.MAX_ID_CHARS + 1))),
        ):
            with self.subTest(label):
                self.assertRefused(data)

    def test_entity_id_length_bound_is_inclusive(self):
        self.assertEqual(
            rules.normalize_envelope(_envelope(entity_id="T" * rules.MAX_ID_CHARS))["entity_id"],
            "T" * rules.MAX_ID_CHARS,
        )

    def test_base_revision_bounds(self):
        for good in (None, 0, rules.MAX_REVISION):
            with self.subTest(good=good):
                self.assertEqual(rules.normalize_envelope(_envelope(base_revision=good))["base_revision"], good)
        for bad in (-1, rules.MAX_REVISION + 1, 1.0, True, "1"):
            with self.subTest(bad=bad):
                self.assertRefused(_envelope(base_revision=bad), "INVALID_BASE_REVISION")

    def test_dependencies(self):
        upper = [f"0000000e-0000-4000-8000-{n:012x}" for n in range(rules.MAX_DEPENDENCIES)]
        self.assertEqual(rules.normalize_dependencies([d.upper() for d in upper], OP_ID), upper)
        for label, value in (
            ("not a list", {}),
            ("too many", upper + [DEP_B.replace("2", "3")]),
            ("not a uuid", ["nope"]),
            ("self", [OP_ID]),
            ("self in another case", [OP_ID.upper()]),
            ("duplicate", [DEP_A, DEP_A]),
            ("duplicate in another case", [DEP_A, DEP_A.upper()]),
        ):
            with self.subTest(label), self.assertRaises(ValueError) as raised:
                rules.normalize_dependencies(value, OP_ID)
            self.assertEqual(raised.exception.args, ("INVALID_ENVELOPE",))
        # The envelope checks against its own canonical op_id.
        self.assertRefused(_envelope(op_id=OP_ID.upper(), depends_on=[OP_ID]))

    @unittest.skipUnless(hasattr(time, "tzset"), "time.tzset is POSIX-only")
    def test_timestamps_are_utc_whatever_the_local_time_zone(self):
        with _local_time_zone("PRKS-2"):  # POSIX: two hours east of UTC, no tzdata needed
            self.assertEqual(rules.normalize_timestamp("2026-07-02T03:04:05+00:00"), "2026-07-02T03:04:05+00:00")
            self.assertEqual(rules.normalize_timestamp("2026-07-02T05:04:05+02:00"), "2026-07-02T03:04:05+00:00")

    def test_timestamps(self):
        self.assertEqual(rules.normalize_timestamp("2026-01-02T03:04:05Z"), "2026-01-02T03:04:05+00:00")
        self.assertEqual(rules.normalize_timestamp("2026-01-02T00:04:05-03:00"), "2026-01-02T03:04:05+00:00")
        forty = "2026-01-02T03:04:05.1234+00:00:00.000000"
        self.assertEqual(len(forty), 40)
        self.assertEqual(rules.normalize_timestamp(forty), "2026-01-02T03:04:05.123400+00:00")
        for bad in (None, "2026-01-02T03:04:05", "yesterday", forty + "0", "2026-01-02T03:04:05z"):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as raised:
                rules.normalize_timestamp(bad)
            self.assertEqual(raised.exception.args, ("INVALID_ENVELOPE",))

    def test_request_hash_is_canonical_json_sha256(self):
        # Sorted keys, no whitespace, ASCII escapes: the bytes a retry must reproduce.
        self.assertEqual(
            rules.request_hash({"b": 1, "a": [1, "é"]}),
            hashlib.sha256(b'{"a":[1,"\\u00e9"],"b":1}').hexdigest(),
        )
        self.assertEqual(rules.request_hash({"a": 1, "b": 2}), rules.request_hash({"b": 2, "a": 1}))


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(rules._ENTITY_TYPES)
        self.addCleanup(lambda: (rules._ENTITY_TYPES.clear(), rules._ENTITY_TYPES.update(self.saved)))

    def test_register_defaults_to_work_and_refuses_duplicates_and_blank_types(self):
        rules.register("TEST_ONLY_OP")
        self.assertEqual(rules._ENTITY_TYPES["TEST_ONLY_OP"], "work")
        self.assertIn("TEST_ONLY_OP", rules.supported_operations())
        with self.assertRaisesRegex(RuntimeError, "^duplicate sync operation: TEST_ONLY_OP$"):
            rules.register("TEST_ONLY_OP", entity_type="tag")
        bad_types: tuple[Any, ...] = ("", " ", None)
        for bad in bad_types:
            with self.subTest(entity_type=bad), self.assertRaisesRegex(
                RuntimeError, "^sync operation entity_type is required$"
            ):
                rules.register("TEST_ONLY_OTHER", entity_type=bad)
        self.assertNotIn("TEST_ONLY_OTHER", rules.supported_operations())


if __name__ == "__main__":
    unittest.main()
