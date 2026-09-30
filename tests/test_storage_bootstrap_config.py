"""Bootstrap configuration file: reader and the guarded compare-and-set writer (§5.1)."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend.storage import bootstrap_config as bc
from backend.storage.errors import (
    BootstrapConfigConflict,
    BootstrapConfigError,
    BootstrapConfigLockTimeout,
)


class BootstrapTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-bootstrap-")
        self.tmp = os.path.realpath(self._tmp.name)
        self.path = os.path.join(self.tmp, "cfg", "config.json")
        self.store = bc.BootstrapConfigStore(self.path, lock_timeout=0.5)

    def tearDown(self):
        self._tmp.cleanup()

    def root(self, name):
        return os.path.join(self.tmp, name)


class TestReader(BootstrapTestCase):
    def test_absent_file_is_none(self):
        self.assertIsNone(bc.read_bootstrap_config(self.path))

    def test_round_trip_and_normalized_root(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"format": 1, "storage": {"local_root": self.root("a") + "/./"}}, handle)
        cfg = bc.read_bootstrap_config(self.path)
        self.assertEqual(cfg.local_root, self.root("a"))
        self.assertEqual(cfg.backend, "local")
        self.assertIsNone(cfg.relocation)

    def test_directory_in_place_of_file_is_an_error(self):
        os.makedirs(self.path)
        with self.assertRaises(BootstrapConfigError) as ctx:
            bc.read_bootstrap_config(self.path)
        self.assertEqual(ctx.exception.reason, "not_a_file")


class TestCompareAndSet(BootstrapTestCase):
    def test_first_write_expects_absence(self):
        written = self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("a"))
        self.assertEqual(written.local_root, self.root("a"))
        with open(self.path, encoding="utf-8") as handle:
            on_disk = json.load(handle)
        self.assertEqual(
            on_disk, {"format": 1, "storage": {"backend": "local", "local_root": self.root("a"), "relocation": None}}
        )
        with self.assertRaises(BootstrapConfigConflict):
            self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("b"))
        self.assertEqual(self.store.read().local_root, self.root("a"))

    def test_stale_read_modify_write_cannot_overwrite_newer_selection(self):
        self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("a"))
        seen_by_user = self.store.read()
        # Someone else changes the selection after the user looked.
        self.store.compare_and_set(expect=bc.expect_unchanged(seen_by_user), local_root=self.root("b"))
        with self.assertRaises(BootstrapConfigConflict) as ctx:
            self.store.compare_and_set(expect=bc.expect_unchanged(seen_by_user), local_root=self.root("c"))
        self.assertEqual(ctx.exception.reason, "bootstrap_config_conflict")
        self.assertEqual(self.store.read().local_root, self.root("b"))

    def test_relocation_state_is_part_of_the_expected_state(self):
        rel = {"id": "rel-1", "phase": "copying", "from": "/o", "to": "/n"}
        self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("a"), relocation=rel)
        with self.assertRaises(BootstrapConfigConflict):
            self.store.compare_and_set(
                expect=bc.expect_storage(local_root=self.root("a"), relocation=None),
                local_root=self.root("b"),
            )
        advanced = dict(rel, phase="verified")
        self.store.compare_and_set(
            expect=bc.expect_storage(local_root=self.root("a"), relocation=rel),
            local_root=self.root("a"),
            relocation=advanced,
        )
        self.assertEqual(self.store.read().relocation["phase"], "verified")

    def test_check_happens_under_the_lock_after_a_fresh_read(self):
        self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("a"))
        snapshot = self.store.read()
        with self.store.transaction() as txn:
            # A write that slipped in before the lock is still seen: write()
            # re-reads under the lock instead of trusting the caller's copy.
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump({"format": 1, "storage": {"local_root": self.root("z")}}, handle)
            with self.assertRaises(BootstrapConfigConflict):
                txn.write(expect=bc.expect_unchanged(snapshot), local_root=self.root("b"))
        self.assertEqual(self.store.read().local_root, self.root("z"))

    def test_multi_step_transaction_holds_the_lock_throughout(self):
        self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("a"))
        other = bc.BootstrapConfigStore(self.path, lock_timeout=0.2)
        with self.store.transaction() as txn:
            snapshot = txn.read()
            with self.assertRaises(BootstrapConfigLockTimeout):
                other.compare_and_set(expect=bc.expect_unchanged(snapshot), local_root=self.root("x"))
            txn.write(expect=bc.expect_unchanged(snapshot), local_root=self.root("b"))
        self.assertEqual(self.store.read().local_root, self.root("b"))

    def test_malformed_current_file_is_never_overwritten(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        with self.assertRaises(BootstrapConfigError):
            self.store.compare_and_set(expect=lambda _c: True, local_root=self.root("a"))
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "{broken")

    def test_newer_format_is_never_overwritten(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"format": 2, "storage": {}}, handle)
        with self.assertRaises(BootstrapConfigError) as ctx:
            self.store.compare_and_set(expect=lambda _c: True, local_root=self.root("a"))
        self.assertEqual(ctx.exception.reason, "format_newer")

    def test_unknown_keys_survive_a_write(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(
                {"format": 1, "ui": {"theme": "dark"}, "storage": {"local_root": self.root("a"), "future": 1}},
                handle,
            )
        self.store.compare_and_set(expect=bc.expect_storage(local_root=self.root("a")), local_root=self.root("b"))
        with open(self.path, encoding="utf-8") as handle:
            doc = json.load(handle)
        self.assertEqual(doc["ui"], {"theme": "dark"})
        self.assertEqual(doc["storage"]["future"], 1)
        self.assertEqual(doc["storage"]["local_root"], self.root("b"))

    def test_writer_refuses_relative_or_invalid_values(self):
        with self.assertRaises(BootstrapConfigError):
            self.store.compare_and_set(expect=bc.expect_absent(), local_root="relative")
        with self.assertRaises(BootstrapConfigError):
            self.store.compare_and_set(
                expect=bc.expect_absent(), local_root=self.root("a"), relocation={"id": "x", "phase": "nope"}
            )
        self.assertIsNone(self.store.read())

    def test_write_is_atomic_and_durable(self):
        calls = []
        from backend import fs_durability

        real_sync = fs_durability.fsync_open_file

        def spy(fd):
            calls.append("file")
            real_sync(fd)

        with patch.object(fs_durability, "fsync_open_file", spy):
            self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("a"))
        self.assertEqual(calls, ["file"])
        leftovers = [n for n in os.listdir(os.path.dirname(self.path)) if n.startswith(".prks-write-")]
        self.assertEqual(leftovers, [])

    def test_failed_content_sync_leaves_previous_file(self):
        from backend import fs_durability

        self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("a"))

        def fail(_fd):
            raise OSError(5, "EIO")

        with patch.object(fs_durability, "fsync_open_file", fail):
            with self.assertRaises(BootstrapConfigError) as ctx:
                self.store.compare_and_set(expect=bc.expect_storage(local_root=self.root("a")), local_root=self.root("b"))
        self.assertEqual(ctx.exception.reason, "write_failed")
        self.assertEqual(self.store.read().local_root, self.root("a"))

    def test_undurable_directory_entry_is_reported_as_failure(self):
        with patch.object(bc, "replace_file_atomically", return_value=False):
            with self.assertRaises(BootstrapConfigError) as ctx:
                self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("a"))
        self.assertEqual(ctx.exception.reason, "write_not_durable")

    def test_threads_racing_from_one_snapshot_have_exactly_one_winner(self):
        self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("start"))
        snapshot = self.store.read()
        results = []
        barrier = threading.Barrier(8)

        def attempt(i):
            store = bc.BootstrapConfigStore(self.path, lock_timeout=10)
            barrier.wait()
            try:
                store.compare_and_set(expect=bc.expect_unchanged(snapshot), local_root=self.root(f"t{i}"))
                results.append(("ok", i))
            except BootstrapConfigConflict:
                results.append(("conflict", i))

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        winners = [i for kind, i in results if kind == "ok"]
        self.assertEqual(len(results), 8)
        self.assertEqual(len(winners), 1)
        self.assertEqual(self.store.read().local_root, self.root(f"t{winners[0]}"))


_CHILD = textwrap.dedent(
    """
    import os, sys, time
    sys.path.insert(0, {project!r})
    from backend.storage import bootstrap_config as bc
    from backend.storage.errors import BootstrapConfigConflict
    path, target, go = sys.argv[1], sys.argv[2], sys.argv[3]
    store = bc.BootstrapConfigStore(path, lock_timeout=30)
    snapshot = store.read()
    while not os.path.exists(go):
        time.sleep(0.005)
    try:
        store.compare_and_set(expect=bc.expect_unchanged(snapshot), local_root=target)
        print("ok")
    except BootstrapConfigConflict:
        print("conflict")
    """
)


class TestCrossProcess(BootstrapTestCase):
    def test_processes_racing_from_one_snapshot_have_exactly_one_winner(self):
        self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("start"))
        go = os.path.join(self.tmp, "go")
        script = _CHILD.format(project=_PROJECT_DIR)
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", script, self.path, self.root(f"p{i}"), go],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for i in range(4)
        ]
        # Let every child read its snapshot before any of them may write.
        time.sleep(1.0)
        open(go, "w").close()
        outputs = [p.communicate(timeout=60) for p in procs]
        verdicts = [out.strip() for out, _err in outputs]
        self.assertEqual(sorted(verdicts), ["conflict", "conflict", "conflict", "ok"], outputs)
        winner = verdicts.index("ok")
        self.assertEqual(self.store.read().local_root, self.root(f"p{winner}"))

    def test_lock_held_by_another_process_times_out_instead_of_writing(self):
        os.makedirs(os.path.dirname(self.path))
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                textwrap.dedent(
                    f"""
                    import sys, time
                    sys.path.insert(0, {_PROJECT_DIR!r})
                    from backend.storage.file_lock import ExclusiveFileLock
                    lock = ExclusiveFileLock.acquire(sys.argv[1])
                    print("held", flush=True)
                    sys.stdin.readline()
                    """
                ),
                self.path + ".lock",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            with self.assertRaises(BootstrapConfigLockTimeout):
                self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("a"))
            self.assertIsNone(self.store.read())
        finally:
            holder.stdin.close()
            holder.wait(30)
            holder.stdout.close()
        self.store.compare_and_set(expect=bc.expect_absent(), local_root=self.root("a"))
        self.assertEqual(self.store.read().local_root, self.root("a"))


if __name__ == "__main__":
    unittest.main()
