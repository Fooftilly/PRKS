"""The recorded SQLite reference of the parity tests (PG-C2 #641, ``tests/parity_reference.py``)."""

from __future__ import annotations

import glob
import importlib
import json
import os
import sys
import unittest
from unittest import mock

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend.domain.works.rules import DeletedWorkRecord
from tests import parity_reference
from tests.parity_reference import RECORD_ENV, reference_for


class _Live:
    """A stand-in reference library: returns what the test tells it to."""

    def __init__(self) -> None:
        self.answers: list[object] = []
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __getattr__(self, name: str):
        def call(*args: object, **kwargs: object) -> object:
            self.calls.append((name, args))
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        return call


class _Case(unittest.TestCase):
    def test_x(self) -> None:  # the id the references are recorded under
        pass


W = "W-" + "A" * 32
OP = "0f0e0d0c-0b0a-4908-8706-050403020100"


class RecordAndReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recorded: dict[str, dict[str, list[dict[str, object]]]] = {}
        patcher = mock.patch.object(parity_reference, "_recorded", self.recorded)
        patcher.start()
        self.addCleanup(patcher.stop)

    def record(self, live: _Live, script) -> list[dict[str, object]]:
        case = _Case("test_x")
        with mock.patch.dict(os.environ, {RECORD_ENV: "1"}):
            ref = reference_for(case, lambda: live)
            script(ref)
        case.doCleanups()
        (steps,) = self.recorded[__name__.rsplit(".", 1)[-1]].values()
        return steps

    def replay(self, steps: list[dict[str, object]], script) -> None:
        case = _Case("test_x")
        module = __name__.rsplit(".", 1)[-1]
        with mock.patch.dict(os.environ, {RECORD_ENV: ""}), \
                mock.patch.object(parity_reference, "_cache", {module: {"_Case.test_x": steps}}):
            ref = reference_for(case, lambda: self.fail("a replay opens no SQLite library"))
            script(ref)
            ref.close()  # what the test's cleanup runs

    def test_values_ids_and_errors_round_trip(self) -> None:
        live = _Live()
        live.answers = [W, (1, b"\x00", DeletedWorkRecord(W, "", False)), ValueError("Work not found")]

        def script(ref) -> object:
            work = ref.add_work(title="T", op=OP)
            self.assertEqual(ref.delete_work_record(work)[2], DeletedWorkRecord(work, "", False))
            with self.assertRaisesRegex(ValueError, "Work not found"):
                ref.get_work(work)
            return work

        steps = self.record(live, script)
        self.assertEqual(steps[0]["result"], "<W#1>")  # the op UUID came first, as <~uuid#0>
        self.assertNotIn(W, json.dumps(steps))
        # A replay gets an id of the same kind, minted for the run, never the recorded one.
        seen: list[str] = []

        def replayed(ref) -> None:
            work = ref.add_work(title="T", op="11111111-2222-4333-8444-555555555555")
            seen.append(work)
            self.assertTrue(work.startswith("W-") and work != W)
            self.assertEqual(ref.delete_work_record(work)[:2], (1, b"\x00"))
            with self.assertRaisesRegex(ValueError, "Work not found"):
                ref.get_work(work)

        self.replay(steps, replayed)
        self.assertEqual(len(seen), 1)

    def test_an_uppercase_uuid_opening_in_a_to_f_is_a_uuid_not_an_entity_id(self) -> None:
        varying = parity_reference._Varying()
        upper, lower = "ABCDEFAB-1234-4567-89AB-123456789ABC", "abcdefab-1234-4567-89ab-123456789abc"
        self.assertEqual(varying.hide("%s %s %s" % (upper, lower, W)), "<~UUID#0> <~uuid#1> <W#2>")
        minted = parity_reference._Varying().show("<~UUID#0> <W#1>").split()
        self.assertEqual((len(minted[0]), minted[0], minted[1][:2]), (36, minted[0].upper(), "W-"))

    def test_the_recording_day_replays_as_the_replaying_day(self) -> None:
        # The date of a timestamp, and a longer number, are not the day.
        untouched = "2026-11-10 23:59:59 2026-11-10T01:02:03Z 2026-11-100 12026-11-10"
        live = _Live()
        live.answers = ["urldate = {2026-11-10},", untouched]

        def script(ref) -> None:
            ref.generate_bibtex(W)
            ref.get_work(W)

        with mock.patch.object(parity_reference, "_today", return_value="2026-11-10"):
            steps = self.record(live, script)
        self.assertEqual(steps[0]["result"], "urldate = {<~today>},")
        self.assertEqual(steps[1]["result"], untouched)
        answers: list[object] = []

        def replayed(ref) -> None:
            answers.extend((ref.generate_bibtex(W), ref.get_work(W)))

        with mock.patch.object(parity_reference, "_today", return_value="2026-11-11"):
            self.replay(steps, replayed)
        self.assertEqual(answers, ["urldate = {2026-11-11},", untouched])

    def test_a_replay_refuses_other_calls_and_leftover_steps(self) -> None:
        live = _Live()
        live.answers = [W, None]

        def both(ref) -> None:
            ref.delete_work_record(ref.add_work(title="T"))

        steps = self.record(live, both)
        self.replay(steps, both)
        with self.assertRaisesRegex(AssertionError, "differs from the recording"):
            self.replay(steps, lambda ref: ref.add_work(title="Other"))
        with self.assertRaisesRegex(AssertionError, "1 recorded calls were not replayed"):
            self.replay(steps, lambda ref: ref.add_work(title="T"))
        with self.assertRaisesRegex(AssertionError, "was never recorded"):
            self.replay(steps, lambda ref: (both(ref), ref.get_work(W)))


class CrossReferenceOrderTests(unittest.TestCase):
    """A library and its index replay in the order they were recorded (the review on #1187)."""

    def setUp(self) -> None:
        self.recorded: dict[str, dict[str, list[dict[str, object]]]] = {}
        patcher = mock.patch.object(parity_reference, "_recorded", self.recorded)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.module = __name__.rsplit(".", 1)[-1]

    def record(self, script) -> dict[str, list[dict[str, object]]]:
        library, index = _Live(), _Live()
        library.answers, index.answers = ["C-" + "B" * 32], [None]
        case = _Case("test_x")
        with mock.patch.dict(os.environ, {RECORD_ENV: "1"}):
            script(reference_for(case, lambda: library), reference_for(case, lambda: index, name="index"))
        case.doCleanups()
        return dict(self.recorded[self.module])

    def replay(self, recording: dict[str, list[dict[str, object]]], script) -> None:
        case = _Case("test_x")
        with mock.patch.dict(os.environ, {RECORD_ENV: ""}), \
                mock.patch.object(parity_reference, "_cache", {self.module: recording}):
            library = reference_for(case, lambda: self.fail("a replay opens no SQLite library"))
            index = reference_for(case, lambda: self.fail("a replay opens no SQLite index"), name="index")
            script(library, index)
            library.close()
            index.close()

    @staticmethod
    def create_then_sync(library, index) -> None:
        library.create_concept("Virtue")
        index.sync_work(W)

    @staticmethod
    def sync_then_create(library, index) -> None:
        index.sync_work(W)
        library.create_concept("Virtue")

    def test_swapping_two_references_calls_is_a_mismatch(self) -> None:
        recording = self.record(self.create_then_sync)
        self.assertEqual(sorted(recording), ["_Case.test_x", "_Case.test_x#index"])
        self.assertEqual([step["at"] for steps in recording.values() for step in steps], [0, 1])
        self.replay(recording, self.create_then_sync)
        with self.assertRaisesRegex(AssertionError, "ran in another order"):
            self.replay(recording, self.sync_then_create)

    def test_a_recorded_reference_the_test_no_longer_creates_is_refused(self) -> None:
        recording = self.record(self.create_then_sync)
        case = _Case("test_x")
        with mock.patch.dict(os.environ, {RECORD_ENV: ""}), \
                mock.patch.object(parity_reference, "_cache", {self.module: recording}):
            library = reference_for(case, lambda: self.fail("a replay opens no SQLite library"))
            library.create_concept("Virtue")
            library.close()  # the library's own steps were all replayed
            with self.assertRaisesRegex(AssertionError, "the index reference.*never created"):
                parity_reference.check_every_reference_created(case)
            self.assertFalse(case.doCleanups())  # the check also runs at the test's cleanup
            # A test that creates both passes the same check.
            both = _Case("test_x")
            self.create_then_sync(reference_for(both, lambda: None),
                                  reference_for(both, lambda: None, name="index"))
            self.assertTrue(both.doCleanups())

    def test_a_recording_of_two_references_without_their_order_is_refused(self) -> None:
        recording = self.record(self.create_then_sync)
        unordered = {key: [{name: value for name, value in step.items() if name != "at"} for step in steps]
                     for key, steps in recording.items()}
        with self.assertRaisesRegex(AssertionError, "does not order them"):
            self.replay(unordered, self.create_then_sync)
        # One reference needs no order: recordings made before it still replay.
        single = {"_Case.test_x": unordered["_Case.test_x"]}
        case = _Case("test_x")
        with mock.patch.dict(os.environ, {RECORD_ENV: ""}), \
                mock.patch.object(parity_reference, "_cache", {self.module: single}):
            reference_for(case, lambda: None).create_concept("Virtue")
        case.doCleanups()


class RecordingsTests(unittest.TestCase):
    def test_every_recording_belongs_to_a_test_that_exists(self) -> None:
        directory = os.path.join(_PROJECT_DIR, "tests", "parity_reference")
        files = sorted(glob.glob(os.path.join(directory, "*.json")))
        self.assertTrue(files)
        for path in files:
            module = importlib.import_module("tests." + os.path.basename(path)[:-5])
            with open(path, encoding="utf-8") as handle:
                recorded = json.load(handle)
            for key in recorded:
                test_id = key.split("#", 1)[0]
                class_name, _, method = test_id.partition(".")
                with self.subTest(file=os.path.basename(path), test=key):
                    self.assertTrue(callable(getattr(getattr(module, class_name, None), method, None)),
                                    "a recording of a test that no longer exists")


if __name__ == "__main__":
    unittest.main()
