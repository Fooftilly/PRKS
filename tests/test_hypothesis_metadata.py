"""Bounded model-based Hypothesis checks for field-scoped metadata sync (#391).

Exercise real disposable SQLite Work metadata, both ordinary PATCH and durable
sync operations. The small reference model owns only field values/revisions,
not a second copy of PRKS persistence or response semantics.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from contextlib import contextmanager

from hypothesis import example, given, settings, strategies as st

from backend import sync_protocol, work_metadata_sync as meta
from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig

EXAMPLES = 12
FIELDS = ("doi", "isbn", "journal", "publisher", "location", "edition")
ALPHABET = ("a", "Z", "9", " ", "/", ":", "|", '"', "\\", "'", "é", "λ", "中", "🔥")
VALUE = st.text(alphabet=ALPHABET, max_size=14)
ACTION = st.tuples(st.sampled_from(FIELDS), VALUE, st.booleans())
DEVICE_ID = "22222222-2222-4222-8222-222222222222"


@contextmanager
def scratch_metadata():
    with tempfile.TemporaryDirectory(prefix="prks-hypothesis-metadata-") as root:
        db = PRKSDatabase(storage=StorageConfig.for_testing(root))
        work = db.add_work("Hypothesis metadata")
        yield db, work


def operation(work, field, value, base, ordinal):
    return {
        "op_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"prks-meta-invariant-{ordinal}")),
        "device_id": DEVICE_ID,
        "operation": "SET_WORK_METADATA_FIELD",
        "entity_type": "work",
        "entity_id": work,
        "payload": {"field": field, "value": value},
        "base_revision": base,
        "occurred_at": "2026-09-12T10:00:00Z",
        "created_at": "2026-09-12T10:00:00Z",
        "depends_on": [],
    }


class MetadataFieldProperties(unittest.TestCase):
    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(primary=st.sampled_from(FIELDS), tail=st.lists(ACTION, max_size=5))
    # These explicit examples guarantee every field's PATCH -> sync contract;
    # 12 random examples alone might miss a primary field entirely.
    @example(primary="doi", tail=[])
    @example(primary="isbn", tail=[])
    @example(primary="journal", tail=[])
    @example(primary="publisher", tail=[])
    @example(primary="location", tail=[])
    @example(primary="edition", tail=[])
    def test_patch_and_sync_interleavings_follow_independent_field_revisions(self, primary, tail):
        """Changing one field never advances another; replay burns no revisions."""
        # Always exercise online PATCH -> offline sync on one field, with an
        # unrelated PATCH between it and the generated tail. The selected
        # primary varies so the mandatory interleaving covers every field.
        other = next(field for field in FIELDS if field != primary)
        actions = [
            (primary, "prefix-patch", False),
            (primary, "prefix-sync", True),
            (other, "prefix-other", False),
            *tail,
        ]
        with scratch_metadata() as (db, work):
            initial = db.get_work_metadata_state(work)["fields"]
            expected = {field: (initial[field]["value"], initial[field]["revision"])
                        for field in FIELDS}
            for ordinal, (field, value, via_sync) in enumerate(actions):
                old_value, old_revision = expected[field]
                changed = value != old_value
                if via_sync:
                    op = operation(work, field, value, old_revision, ordinal)
                    first = sync_protocol.process_operation(db, op)
                    self.assertEqual((first[0], first[1]["code"]),
                                     (200, "ACKNOWLEDGED"))
                    self.assertEqual(first[1]["changed"], changed)
                    self.assertEqual(first[1]["server_revision"],
                                     old_revision + int(changed))
                    # Duplicate delivery must return the ledger result unchanged.
                    self.assertEqual(sync_protocol.process_operation(db, op), first)
                else:
                    db.update_work_metadata(work, {field: value})
                expected[field] = (value, old_revision + int(changed))
                observed = db.get_work_metadata_state(work)["fields"]
                for name in FIELDS:
                    self.assertEqual(
                        (observed[name]["value"], observed[name]["revision"]),
                        expected[name], f"step={ordinal} updated={field} observed={name}",
                    )
                # A truly idempotent PATCH must also leave every counter alone.
                if not via_sync:
                    db.update_work_metadata(work, {field: value})
                    again = db.get_work_metadata_state(work)["fields"]
                    self.assertEqual(
                        {name: (again[name]["value"], again[name]["revision"])
                         for name in FIELDS}, expected,
                    )

    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(field=st.sampled_from(FIELDS), left=VALUE, right=VALUE)
    def test_conflicting_stale_field_does_not_block_unrelated_sync(self, field, left, right):
        """Stale offline edits cannot overwrite an online PATCH to this field."""
        with scratch_metadata() as (db, work):
            other = next(name for name in FIELDS if name != field)
            server_value = "server:" + left
            device_value = "device:" + right
            db.update_work_metadata(work, {field: server_value})
            self.assertEqual(db.get_work_metadata_state(work)["fields"][field]["revision"], 1)
            op = operation(work, field, device_value, 0, 0)
            first = sync_protocol.process_operation(db, op)
            self.assertEqual((first[0], first[1]["code"]), (409, "REVISION_CONFLICT"))
            self.assertEqual(sync_protocol.process_operation(db, op), first)
            before = db.get_work_metadata_state(work)["fields"]
            self.assertEqual((before[field]["value"], before[field]["revision"]),
                             (server_value, 1))
            independent = operation(work, other, "unrelated:" + right, 0, 1)
            status, result = sync_protocol.process_operation(db, independent)
            self.assertEqual((status, result["code"], result["changed"]),
                             (200, "ACKNOWLEDGED", True))
            after = db.get_work_metadata_state(work)["fields"]
            self.assertEqual(after[field], before[field])
            self.assertEqual(
                (after[other]["value"], after[other]["revision"]),
                ("unrelated:" + right, 1),
            )

    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(fields=st.sampled_from(tuple(
        (left, right) for left in FIELDS for right in FIELDS if left != right
    )), patch_value=VALUE, sync_value=VALUE)
    @example(fields=("doi", "isbn"), patch_value="x", sync_value="y")
    @example(fields=("isbn", "journal"), patch_value="x", sync_value="y")
    @example(fields=("journal", "publisher"), patch_value="x", sync_value="y")
    @example(fields=("publisher", "location"), patch_value="x", sync_value="y")
    @example(fields=("location", "edition"), patch_value="x", sync_value="y")
    @example(fields=("edition", "doi"), patch_value="x", sync_value="y")
    def test_independent_patch_and_sync_commute(self, fields, patch_value, sync_value):
        """Independent online PATCH and durable sync converge in either order.

        Sample all 30 possible ordered pairs of distinct fields, with explicit
        examples guaranteeing that each primary field is tested at least once.
        """
        primary, secondary = fields
        patch_value = "patch:" + patch_value
        sync_value = "sync:" + sync_value
        outcomes = []
        for patch_first in (True, False):
            with scratch_metadata() as (db, work):
                initial = db.get_work_metadata_state(work)["fields"]
                expected = {field: (initial[field]["value"], initial[field]["revision"])
                            for field in FIELDS}
                sync_op = operation(work, secondary, sync_value, 0, 0)
                ack = (200, {
                    "work_id": work,
                    "field": secondary,
                    "code": "ACKNOWLEDGED",
                    "server_revision": 1,
                    "changed": True,
                    "value": sync_value,
                })

                # No closure over mutable loop variables: Codacy checks B023.
                for action in (("patch", "sync") if patch_first else ("sync", "patch")):
                    if action == "patch":
                        db.update_work_metadata(work, {primary: patch_value})
                    else:
                        self.assertEqual(sync_protocol.process_operation(db, sync_op), ack)

                # Replay after BOTH edits, including the sync-first case:
                # an unrelated PATCH cannot change the stored sync outcome.
                self.assertEqual(sync_protocol.process_operation(db, sync_op), ack)
                expected[primary] = (patch_value, 1)
                expected[secondary] = (sync_value, 1)
                observed = db.get_work_metadata_state(work)["fields"]
                actual = {field: (observed[field]["value"], observed[field]["revision"])
                          for field in FIELDS}
                self.assertEqual(actual, expected)
                outcomes.append(actual)
        # Both orderings are independently checked above; this spells out the
        # commutativity contract explicitly for future edits to the model.
        self.assertEqual(outcomes[0], outcomes[1])

    @settings(max_examples=EXAMPLES, deadline=None, database=None)
    @given(work=VALUE, field=VALUE, other=VALUE)
    def test_metadata_scope_keys_preserve_structural_identity(self, work, field, other):
        self.assertEqual(json.loads(meta.scope_key(work, field)), [work, field])
        if field != other:
            self.assertNotEqual(meta.scope_key(work, field), meta.scope_key(work, other))
        if work != other:
            self.assertNotEqual(meta.scope_key(work, field), meta.scope_key(other, field))
