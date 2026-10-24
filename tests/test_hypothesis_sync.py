"""Bounded Hypothesis pilot for canonical Work-Tag revisions and sync replay (#854).

These tests use real SQLite domain/ledger operations with disposable storage.
A new database is built *inside each generated example*, because unittest
setUp() is not repeated by Hypothesis between @given examples.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from contextlib import contextmanager

from hypothesis import given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from backend import folder_tag_sync, sync_protocol, work_tag_sync
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig

# Small default profiles deliberately limit extra time in the existing
# Unit / API / contract Test Gate, including its three nightly repetitions.
EXAMPLES = 12
STEPS = 8


@contextmanager
def scratch_work_tag():
    with tempfile.TemporaryDirectory(prefix="prks-hypothesis-sync-") as root:
        db = PRKSDatabase(storage=StorageConfig.for_testing(root))
        work_id = db.add_work("Hypothesis sync work")
        tag_id = db.add_tag("Hypothesis sync tag")["id"]
        yield db, work_id, tag_id


def tag_state(db: PRKSDatabase, work_id: str, tag_id: str) -> tuple[bool, int]:
    with db.connection() as conn:
        present = conn.execute(
            "SELECT 1 FROM work_tags WHERE work_id = ? AND tag_id = ?",
            (work_id, tag_id),
        ).fetchone() is not None
        revision = work_tag_sync.get_revision(conn, work_id, tag_id)
    return present, revision


def operation(work_id: str, tag_id: str, *, present: bool, base: int, ordinal: int) -> dict:
    return {
        "op_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"hypothesis-prks-operation-{ordinal}")),
        "device_id": "11111111-1111-4111-8111-111111111111",
        "operation": "ADD_WORK_TAG" if present else "REMOVE_WORK_TAG",
        "entity_type": "work",
        "entity_id": work_id,
        "payload": {"tag_id": tag_id},
        "base_revision": base,
        "occurred_at": "2026-09-11T00:00:00Z",
        "created_at": "2026-09-11T00:00:00Z",
        "depends_on": [],
    }


class WorkTagProperties(unittest.TestCase):
    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(
        left=st.text(max_size=32),
        right=st.text(max_size=32),
        other=st.text(max_size=32),
    )
    def test_relation_scope_keys_are_structural_and_round_trip(self, left, right, other):
        # Quotes, backslashes, separators, and Unicode must not alias pairs.
        for scope_key in (work_tag_sync.scope_key, folder_tag_sync.scope_key):
            self.assertEqual(json.loads(scope_key(left, right)), [left, right])
            if right != other:
                self.assertNotEqual(scope_key(left, right), scope_key(left, other))
            if left != other:
                self.assertNotEqual(scope_key(left, right), scope_key(other, right))

    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(actions=st.lists(st.booleans(), min_size=1, max_size=10))
    def test_direct_revisions_advance_only_for_real_changes(self, actions):
        with scratch_work_tag() as (db, work, tag):
            expected_present = False
            expected_revision = 0
            for desired in actions:
                with db.connection() as conn:
                    changed = work_tag_sync.set_state(conn, work, tag, desired)
                self.assertEqual(changed, desired != expected_present)
                expected_revision += int(changed)
                expected_present = desired
                self.assertEqual(tag_state(db, work, tag), (expected_present, expected_revision))
                # A repeated edit is a no-op and must not burn a revision.
                with db.connection() as conn:
                    self.assertFalse(work_tag_sync.set_state(conn, work, tag, desired))
                self.assertEqual(tag_state(db, work, tag), (expected_present, expected_revision))

    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(actions=st.lists(st.booleans(), min_size=1, max_size=8))
    def test_ledger_replays_without_reapplying_or_changing_revisions(self, actions):
        with scratch_work_tag() as (db, work, tag):
            expected_present, expected_revision = False, 0
            for ordinal, desired in enumerate(actions):
                op = operation(work, tag, present=desired, base=expected_revision, ordinal=ordinal)
                first = sync_protocol.process_operation(db, op)
                self.assertEqual(first[0], 200)
                self.assertEqual(first[1]["code"], "ACKNOWLEDGED")
                changed = desired != expected_present
                expected_revision += int(changed)
                expected_present = desired
                self.assertEqual(first[1]["changed"], changed)
                self.assertEqual(first[1]["server_revision"], expected_revision)
                self.assertEqual(sync_protocol.process_operation(db, op), first)
                self.assertEqual(tag_state(db, work, tag), (expected_present, expected_revision))
                reused = dict(op, operation="REMOVE_WORK_TAG" if desired else "ADD_WORK_TAG")
                self.assertEqual(
                    sync_protocol.process_operation(db, reused),
                    (409, {"code": "OP_ID_REUSE"}),
                )
                self.assertEqual(tag_state(db, work, tag), (expected_present, expected_revision))

    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(desired=st.booleans())
    def test_stale_revision_converges_only_if_state_already_matches(self, desired):
        with scratch_work_tag() as (db, work, tag):
            with db.connection() as conn:
                self.assertTrue(work_tag_sync.set_state(conn, work, tag, True))
            status, response = sync_protocol.process_operation(
                db, operation(work, tag, present=desired, base=0, ordinal=0),
            )
            if desired:
                self.assertEqual((status, response["code"]), (200, "ACKNOWLEDGED"))
                self.assertFalse(response["changed"])
                self.assertEqual(response["server_revision"], 1)
            else:
                self.assertEqual((status, response["code"]), (409, "REVISION_CONFLICT"))
                self.assertEqual(response["current_revision"], 1)
                self.assertTrue(response["current_state"])
            self.assertEqual(tag_state(db, work, tag), (True, 1))

    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(desired=st.booleans(), ahead=st.integers(min_value=1, max_value=20))
    def test_future_base_is_refused_without_mutating_state(self, desired, ahead):
        with scratch_work_tag() as (db, work, tag):
            with db.connection() as conn:
                self.assertTrue(work_tag_sync.set_state(conn, work, tag, True))
            status, response = sync_protocol.process_operation(
                db, operation(work, tag, present=desired, base=1 + ahead, ordinal=0),
            )
            self.assertEqual((status, response["code"]), (400, "FUTURE_REVISION"))
            self.assertEqual(response["current_revision"], 1)
            self.assertEqual(tag_state(db, work, tag), (True, 1))

    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(actions=st.lists(st.tuples(st.booleans(), st.booleans()), min_size=1, max_size=8))
    def test_other_tag_scope_does_not_advance_on_unrelated_edit(self, actions):
        with scratch_work_tag() as (db, work, tag):
            other = db.add_tag("Independent Hypothesis tag")["id"]
            expected = {tag: (False, 0), other: (False, 0)}
            for use_other, desired in actions:
                chosen = other if use_other else tag
                old_present, old_revision = expected[chosen]
                with db.connection() as conn:
                    changed = work_tag_sync.set_state(conn, work, chosen, desired)
                expected[chosen] = (desired, old_revision + int(changed))
                self.assertEqual(changed, desired != old_present)
                for key in (tag, other):
                    self.assertEqual(tag_state(db, work, key), expected[key])


class WorkTagRevisionMachine(RuleBasedStateMachine):
    """Model alternating direct changes and sync requests against one conflict unit."""

    def __init__(self):
        super().__init__()
        self._temp = tempfile.TemporaryDirectory(prefix="prks-hypothesis-machine-")
        self.db = PRKSDatabase(storage=StorageConfig.for_testing(self._temp.name))
        self.work = self.db.add_work("Hypothesis model work")
        self.tag = self.db.add_tag("Hypothesis model tag")["id"]
        self.present = False
        self.revision = 0
        self.ordinal = 0

    @staticmethod
    def check_equal(actual, expected):
        # Unlike bare asserts, this remains effective under python -O.
        if actual != expected:
            raise AssertionError(f"expected {expected!r}, got {actual!r}")

    @rule(desired=st.booleans())
    def direct_edit(self, desired):
        with self.db.connection() as conn:
            changed = work_tag_sync.set_state(conn, self.work, self.tag, desired)
        self.check_equal(changed, desired != self.present)
        self.revision += int(changed)
        self.present = desired

    @rule(desired=st.booleans(), base_kind=st.sampled_from(("current", "stale", "future")))
    def sync_request(self, desired, base_kind):
        base = {
            "current": self.revision,
            "stale": max(0, self.revision - 1),
            "future": self.revision + 1,
        }[base_kind]
        op = operation(self.work, self.tag, present=desired, base=base, ordinal=self.ordinal)
        self.ordinal += 1
        response = sync_protocol.process_operation(self.db, op)
        if base > self.revision:
            self.check_equal((response[0], response[1]["code"]), (400, "FUTURE_REVISION"))
        elif base < self.revision and desired != self.present:
            self.check_equal((response[0], response[1]["code"]), (409, "REVISION_CONFLICT"))
        else:
            self.check_equal((response[0], response[1]["code"]), (200, "ACKNOWLEDGED"))
            changed = desired != self.present
            self.check_equal(response[1]["changed"], changed)
            self.revision += int(changed)
            self.present = desired
            self.check_equal(response[1]["server_revision"], self.revision)
        # A duplicate op_id must replay the original result, never modify state.
        self.check_equal(sync_protocol.process_operation(self.db, op), response)

    @invariant()
    def canonical_state_matches_model(self):
        self.check_equal(tag_state(self.db, self.work, self.tag), (self.present, self.revision))

    def teardown(self):
        self._temp.cleanup()


TestWorkTagRevisionMachine = WorkTagRevisionMachine.TestCase
TestWorkTagRevisionMachine.settings = settings(
    max_examples=12, stateful_step_count=STEPS, deadline=None, database=None,
)


if __name__ == "__main__":
    unittest.main()
