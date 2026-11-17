"""Field-scoped Work metadata revisions as properties, on PostgreSQL (PG-C2 #641).

The PostgreSQL form of the retired SQLite Hypothesis checks
(``tests/test_hypothesis_metadata.py``, #391). The PostgreSQL test job
installs only the runtime requirements, so the sequences are not drawn by
Hypothesis: every ordered pair of fields is enumerated, and the model run
replays seeded random sequences, so a failure names its seed and sequence.
The model holds only each field's value and revision. Scope keys are pure and
checked in ``tests/test_work_metadata_rules.py``.
"""

from __future__ import annotations

import random
from typing import Any

from tests.pg.test_works_metadata_sync import MetadataSyncTestCase

FIELDS = ("doi", "isbn", "journal", "publisher", "location", "edition")
ALPHABET = ("a", "Z", "9", " ", "/", ":", "|", '"', "\\", "'", "é", "λ", "中", "🔥")
SEEDS = range(12)


def text(rng: random.Random) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 14)))


class MetadataPropertyTests(MetadataSyncTestCase):
    def fresh(self) -> str:
        return self.library.add_work("Property work")

    def observed(self, work: str) -> dict[str, tuple[str, int]]:
        state = self.state(work)
        return {field: (state[field]["value"], state[field]["revision"]) for field in FIELDS}

    def test_patch_and_sync_interleavings_follow_independent_field_revisions(self) -> None:
        for seed in SEEDS:
            rng = random.Random(seed)
            primary = FIELDS[seed % len(FIELDS)]
            other = next(field for field in FIELDS if field != primary)
            actions: list[tuple[str, str, bool]] = [(primary, "prefix-patch", False), (primary, "prefix-sync", True),
                                                    (other, "prefix-other", False)]
            actions += [(rng.choice(FIELDS), text(rng), rng.random() < 0.5) for _ in range(rng.randint(0, 5))]
            work = self.fresh()
            expected = self.observed(work)
            with self.subTest(seed=seed, actions=actions):
                for step, (field, value, via_sync) in enumerate(actions):
                    old_value, old_revision = expected[field]
                    changed = value != old_value
                    if via_sync:
                        data = self.op(field, value, old_revision, work)
                        first = self.send(data)
                        self.assertEqual((first[0], first[1]["code"], first[1]["changed"], first[1]["server_revision"]),
                                         (200, "ACKNOWLEDGED", changed, old_revision + int(changed)))
                        self.assertEqual(self.send(data), first)
                    else:
                        self.patch({field: value}, work)
                    expected[field] = (value, old_revision + int(changed))
                    self.assertEqual(self.observed(work), expected, "step %d" % step)
                    if not via_sync:
                        self.patch({field: value}, work)  # an idempotent PATCH moves nothing
                        self.assertEqual(self.observed(work), expected, "step %d repeated" % step)

    def test_a_conflicting_stale_field_never_blocks_another(self) -> None:
        for seed in SEEDS:
            rng = random.Random(seed)
            field = FIELDS[seed % len(FIELDS)]
            other = next(name for name in FIELDS if name != field)
            server, device, unrelated = "server:" + text(rng), "device:" + text(rng), "unrelated:" + text(rng)
            work = self.fresh()
            with self.subTest(seed=seed, field=field):
                self.patch({field: server}, work)
                data = self.op(field, device, 0, work)
                first = self.send(data)
                self.assertEqual((first[0], first[1]["code"]), (409, "REVISION_CONFLICT"))
                self.assertEqual(self.send(data), first)
                before = self.observed(work)
                self.assertEqual(before[field], (server, 1))
                status, result = self.put(other, unrelated, 0, work)
                self.assertEqual((status, result["code"], result["changed"]), (200, "ACKNOWLEDGED", True))
                after = self.observed(work)
                self.assertEqual((after[field], after[other]), (before[field], (unrelated, 1)))

    def test_independent_patch_and_sync_commute(self) -> None:
        pairs = [(left, right) for left in FIELDS for right in FIELDS if left != right]
        for primary, secondary in pairs:
            outcomes: list[dict[str, tuple[str, int]]] = []
            with self.subTest(primary=primary, secondary=secondary):
                for patch_first in (True, False):
                    work = self.fresh()
                    expected = self.observed(work)
                    data = self.op(secondary, "sync:y", 0, work)
                    ack: tuple[int, dict[str, Any]] = (200, {"work_id": work, "field": secondary,
                                                             "code": "ACKNOWLEDGED", "server_revision": 1,
                                                             "changed": True, "value": "sync:y"})
                    for action in (("patch", "sync") if patch_first else ("sync", "patch")):
                        if action == "patch":
                            self.patch({primary: "patch:x"}, work)
                        else:
                            self.assertEqual(self.send(data), ack)
                    self.assertEqual(self.send(data), ack)  # an unrelated PATCH changes no stored outcome
                    expected.update({primary: ("patch:x", 1), secondary: ("sync:y", 1)})
                    self.assertEqual(self.observed(work), expected)
                    outcomes.append(self.observed(work))
                self.assertEqual(outcomes[0], outcomes[1])
