"""Work-Tag revisions and ledger replay as properties, on PostgreSQL (PG-C2 #641).

The PostgreSQL form of the retired SQLite Hypothesis pilot
(``tests/test_hypothesis_sync.py``, #854). The PostgreSQL test job installs
only the runtime requirements, so the sequences are not drawn by Hypothesis:
short ones are enumerated exhaustively, and the model run replays seeded
random sequences, so a failure names its seed and sequence. One Work and Tag
pair is made per sequence, in the test's Library.
"""

from __future__ import annotations

import itertools
import random
import uuid
from typing import Any

from backend.domain.organization import links
from tests.pg.test_tags_sync import TagSyncTestCase

_BASES = ("current", "stale", "future")


class WorkTagPropertyTests(TagSyncTestCase):
    def pair(self) -> tuple[str, str]:
        return self.library.add_work("Property work"), self.create_tag("Property " + uuid.uuid4().hex)

    def state(self, work_id: str, tag_id: str) -> tuple[bool, int]:
        present = any(row["id"] == tag_id for row in self.library.get_work_tags(work_id))
        return present, self.revisions("work-tag").get(links.WORK.scope_id(work_id, tag_id), 0)

    def direct(self, work_id: str, tag_id: str, desired: bool) -> None:
        if desired:
            self.library.add_tag_to_work(work_id, tag_id)
        else:
            self.library.remove_tag_from_work(work_id, tag_id)

    def op(self, work_id: str, tag_id: str, desired: bool, base: int) -> dict[str, Any]:
        return self.link("ADD_WORK_TAG" if desired else "REMOVE_WORK_TAG", work_id, tag_id, base)

    def test_direct_edits_advance_only_on_a_real_change(self) -> None:
        for length in range(1, 5):
            for actions in itertools.product((False, True), repeat=length):
                work_id, tag_id = self.pair()
                present, revision = False, 0
                with self.subTest(actions=actions):
                    for desired in actions:
                        self.direct(work_id, tag_id, desired)
                        revision += int(desired != present)
                        present = desired
                        self.assertEqual(self.state(work_id, tag_id), (present, revision))
                        self.direct(work_id, tag_id, desired)  # a repeat is a no-op
                        self.assertEqual(self.state(work_id, tag_id), (present, revision))

    def test_a_replay_never_reapplies_and_a_reuse_never_executes(self) -> None:
        for actions in itertools.product((False, True), repeat=4):
            work_id, tag_id = self.pair()
            present, revision = False, 0
            with self.subTest(actions=actions):
                for desired in actions:
                    data = self.op(work_id, tag_id, desired, revision)
                    first = self.send(data)
                    changed = desired != present
                    revision += int(changed)
                    present = desired
                    self.assertEqual((first[0], first[1]["code"], first[1]["changed"], first[1]["server_revision"]),
                                     (200, "ACKNOWLEDGED", changed, revision))
                    self.assertEqual(self.send(data), first)
                    reused = dict(data, operation="REMOVE_WORK_TAG" if desired else "ADD_WORK_TAG")
                    self.assertEqual(self.send(reused), (409, {"code": "OP_ID_REUSE"}))
                    self.assertEqual(self.state(work_id, tag_id), (present, revision))

    def test_a_stale_or_future_base_decides_without_writing(self) -> None:
        for desired, ahead in itertools.product((False, True), (1, 2, 20)):
            work_id, tag_id = self.pair()
            self.direct(work_id, tag_id, True)
            with self.subTest(desired=desired, ahead=ahead):
                status, result = self.send(self.op(work_id, tag_id, desired, 0))
                if desired:
                    self.assertEqual((status, result["code"], result["changed"], result["server_revision"]),
                                     (200, "ACKNOWLEDGED", False, 1))
                else:
                    self.assertEqual((status, result["code"], result["current_revision"], result["current_state"]),
                                     (409, "REVISION_CONFLICT", 1, True))
                status, result = self.send(self.op(work_id, tag_id, desired, 1 + ahead))
                self.assertEqual((status, result["code"], result["current_revision"]), (400, "FUTURE_REVISION", 1))
                self.assertEqual(self.state(work_id, tag_id), (True, 1))

    def test_another_tags_scope_never_moves(self) -> None:
        for actions in itertools.product((False, True), repeat=3):
            work_id, tag_id = self.pair()
            other = self.create_tag("Other " + uuid.uuid4().hex)
            with self.subTest(actions=actions):
                expected = {tag_id: (False, 0), other: (False, 0)}
                for index, desired in enumerate(actions * 2):
                    chosen = other if index % 2 else tag_id
                    present, revision = expected[chosen]
                    self.direct(work_id, chosen, desired)
                    expected[chosen] = (desired, revision + int(desired != present))
                    for key in (tag_id, other):
                        self.assertEqual(self.state(work_id, key), expected[key])

    def test_direct_edits_and_sync_requests_follow_the_model(self) -> None:
        for seed in range(12):
            rng = random.Random(641_000 + seed)
            work_id, tag_id = self.pair()
            present, revision, steps = False, 0, []
            for _ in range(8):
                desired = rng.random() < 0.5
                if rng.random() < 0.4:
                    steps.append(("direct", desired))
                    self.direct(work_id, tag_id, desired)
                    revision += int(desired != present)
                    present = desired
                else:
                    kind = rng.choice(_BASES)
                    base = {"current": revision, "stale": max(0, revision - 1), "future": revision + 1}[kind]
                    steps.append(("sync", desired, kind))
                    data = self.op(work_id, tag_id, desired, base)
                    status, result = self.send(data)
                    if base > revision:
                        expected: tuple[Any, ...] = (400, "FUTURE_REVISION")
                    elif base < revision and desired != present:
                        expected = (409, "REVISION_CONFLICT")
                    else:
                        expected = (200, "ACKNOWLEDGED")
                        self.assertEqual(result["changed"], desired != present, (seed, steps))
                        revision += int(desired != present)
                        present = desired
                        self.assertEqual(result["server_revision"], revision, (seed, steps))
                    self.assertEqual((status, result["code"]), expected, (seed, steps))
                    self.assertEqual(self.send(data), (status, result), (seed, steps))
                self.assertEqual(self.state(work_id, tag_id), (present, revision), (seed, steps))
