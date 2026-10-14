"""Root marker pairing fields, the root claim and the start-generation bump.

``docs/postgresql-architecture.md`` §7.5 and §11.1 (L-ID-1, #626): the shared
``root.lock``, the exclusive ``root.claim`` lock, ``claim_root`` and
``bump_start_generation``. These are file-level rules and need no database;
the database side lives in ``tests/pg/test_root_pairing.py``.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend.backup_restore import backup_storage_inventory
from backend.storage import root_binding, root_marker
from backend.storage.config import StorageConfig
from backend.storage.errors import StorageRootInUse, StorageRootRefused
from backend.storage.file_lock import ExclusiveFileLock, LockBusy, SharedFileLock
from backend.storage.root_marker import StartState

INSTANCE = "IN-" + "A" * 32
OTHER_INSTANCE = "IN-" + "B" * 32
TOKEN = "1" * 32
OTHER_TOKEN = "2" * 32
NEXT_TOKEN = "3" * 32
MAINT = root_marker.MAINTENANCE_DIRNAME


def _no_objects() -> bool:
    return False


def _has_objects() -> bool:
    return True


@unittest.skipIf(sys.platform == "win32", "flock shared mode; Windows refuses shared locks")
class RootClaimTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-rootclaim-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = os.path.realpath(self._tmp.name)
        self.root = self.new_root("lib")
        self.lease = self.shared(self.root)

    def new_root(self, name):
        root = os.path.join(self.tmp, name)
        bound = root_binding.open_storage_root(StorageConfig.for_testing(root), register=False)
        bound.release()
        return root

    def shared(self, root):
        lease = root_binding.acquire_shared_root_lease(root)
        self.addCleanup(lease.release)
        return lease

    def marker_bytes(self, root=None):
        with open(root_marker.marker_path(root or self.root), "rb") as handle:
            return handle.read()

    def set_pairing(self, root, **fields):
        path = root_marker.marker_path(root)
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
        for key, value in fields.items():
            if value is None:
                doc.pop(key, None)
            else:
                doc[key] = value
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(doc, handle)

    def claim(self, root=None, *, instance_id=INSTANCE, token=TOKEN, lease=None, objects=_no_objects, **kw):
        root = root or self.root
        return root_marker.claim_root(
            root,
            instance_id=instance_id,
            start_token=token,
            lease=lease or self.lease,
            holds_objects=objects,
            **kw,
        )

    def assert_refused(self, reason, fn, *args, **kwargs):
        with self.assertRaises(StorageRootRefused) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.reason, reason)
        return ctx.exception


class TestSharedLease(RootClaimTestCase):
    def test_shared_holders_coexist_and_exclude_exclusive_holders(self):
        second = self.shared(self.root)
        self.assertTrue(second.held)
        lock_path = root_marker.lease_path(self.root)
        with self.assertRaises(LockBusy):
            ExclusiveFileLock.acquire(lock_path)
        self.lease.release()
        second.release()
        exclusive = ExclusiveFileLock.acquire(lock_path)
        self.addCleanup(exclusive.release)
        with self.assertRaises(LockBusy):
            SharedFileLock.acquire(lock_path)

    def test_single_process_server_and_shared_lease_exclude_each_other(self):
        with self.assertRaises(StorageRootInUse) as ctx:
            root_binding.open_storage_root(StorageConfig.for_testing(self.root), register=False)
        self.assertEqual(ctx.exception.reason, "root_in_use")
        self.lease.release()
        bound = root_binding.open_storage_root(StorageConfig.for_testing(self.root), register=False)
        self.addCleanup(bound.release)
        with self.assertRaises(StorageRootInUse):
            root_binding.acquire_shared_root_lease(self.root)

    def test_a_shared_lease_never_passes_as_the_exclusive_marker_lease(self):
        self.assertFalse(isinstance(self.lease, ExclusiveFileLock))
        doc = root_marker.read_marker(self.root).document
        self.assert_refused(
            "marker_write_without_lease", root_marker.write_marker, self.root, doc, lease=self.lease
        )

    def test_claim_and_bump_need_this_roots_held_shared_lease(self):
        before = self.marker_bytes()
        exclusive_root = self.new_root("other")
        exclusive = ExclusiveFileLock.acquire(root_marker.lease_path(exclusive_root))
        self.addCleanup(exclusive.release)
        self.assert_refused("marker_write_without_lease", self.claim, lease=exclusive)
        foreign = self.shared(self.new_root("third"))
        self.assert_refused("marker_write_foreign_lease", self.claim, lease=foreign)
        released = self.shared(self.root)
        released.release()
        self.assert_refused("marker_write_without_lease", self.claim, lease=released)
        self.assert_refused(
            "marker_write_without_lease",
            root_marker.bump_start_generation,
            self.root, lease=released, instance_id=INSTANCE,
            expected=StartState(1, TOKEN), to=StartState(2, NEXT_TOKEN),
        )
        self.assertEqual(self.marker_bytes(), before)


class TestPairingFields(RootClaimTestCase):
    def test_unclaimed_marker_has_generation_zero_and_no_token(self):
        marker = root_marker.read_marker(self.root)
        self.assertIsNone(marker.instance_id)
        self.assertEqual(marker.start_state, StartState(0, None))

    def test_malformed_pairing_fields_are_refused(self):
        cases = {
            "instance_id_invalid": {"instance_id": "IN-" + "a" * 32, "start_generation": 1, "start_token": TOKEN},
            "start_generation_invalid": {"start_generation": -1},
            "start_token_invalid": {"instance_id": INSTANCE, "start_generation": 1, "start_token": "XYZ"},
            "pairing_incomplete": {"instance_id": INSTANCE, "start_generation": 0, "start_token": TOKEN},
        }
        for reason, fields in cases.items():
            with self.subTest(reason=reason):
                doc = dict(root_marker.read_marker(self.root).document)
                doc.update(fields)
                payload = json.dumps(doc).encode("utf-8")
                self.assert_refused("marker_" + reason, root_marker.parse_marker, payload)
        doc = dict(root_marker.read_marker(self.root).document)
        doc["start_generation"] = True
        self.assert_refused(
            "marker_start_generation_invalid", root_marker.parse_marker, json.dumps(doc).encode()
        )
        doc = dict(root_marker.read_marker(self.root).document)
        doc["start_token"] = TOKEN
        self.assert_refused(
            "marker_pairing_incomplete", root_marker.parse_marker, json.dumps(doc).encode()
        )

    def test_exclusive_writer_keeps_pairing_fields_and_cannot_change_them(self):
        self.claim()
        self.lease.release()
        lock = ExclusiveFileLock.acquire(root_marker.lease_path(self.root))
        self.addCleanup(lock.release)
        doc = dict(root_marker.read_marker(self.root).document)
        doc["unrelated"] = "kept"
        root_marker.write_marker(self.root, doc, lease=lock)
        marker = root_marker.read_marker(self.root)
        self.assertEqual((marker.instance_id, marker.start_state), (INSTANCE, StartState(1, TOKEN)))
        before = self.marker_bytes()
        for change in (
            {"start_generation": 5},
            {"instance_id": OTHER_INSTANCE},
            {"start_token": OTHER_TOKEN},
        ):
            with self.subTest(change=change):
                lowered = dict(doc, **change)
                self.assert_refused(
                    "marker_pairing_change_refused", root_marker.write_marker, self.root, lowered, lease=lock
                )
        dropped = {k: v for k, v in doc.items() if k not in root_marker.PAIRING_FIELDS}
        self.assert_refused(
            "marker_pairing_change_refused", root_marker.write_marker, self.root, dropped, lease=lock
        )
        self.assertEqual(self.marker_bytes(), before)

    def test_single_process_server_start_keeps_a_claim(self):
        self.claim()
        self.lease.release()
        bound = root_binding.open_storage_root(StorageConfig.for_testing(self.root), register=False)
        bound.release()
        marker = root_marker.read_marker(self.root)
        self.assertEqual((marker.instance_id, marker.start_state), (INSTANCE, StartState(1, TOKEN)))

    def test_relocation_carries_pairing_fields_unchanged(self):
        self.claim()
        source = root_marker.read_marker(self.root)
        destination = dict(source.document, relocation={"id": "r1", "role": "destination"})
        destination.update({"instance_id": OTHER_INSTANCE, "start_generation": 9, "start_token": OTHER_TOKEN})
        carried = root_marker.carry_pairing_fields(source, destination)
        self.assertEqual(carried["instance_id"], INSTANCE)
        self.assertEqual(carried["start_generation"], 1)
        self.assertEqual(carried["start_token"], TOKEN)
        self.assertEqual(carried["relocation"], {"id": "r1", "role": "destination"})
        unclaimed = root_marker.read_marker(self.new_root("fresh"))
        stripped = root_marker.carry_pairing_fields(unclaimed, destination)
        for name in root_marker.PAIRING_FIELDS:
            self.assertNotIn(name, stripped)


class TestClaimRoot(RootClaimTestCase):
    def test_unclaimed_empty_root_is_claimed_at_generation_one(self):
        marker = self.claim()
        self.assertEqual(marker.instance_id, INSTANCE)
        self.assertEqual(marker.start_state, StartState(1, TOKEN))
        on_disk = root_marker.read_marker(self.root)
        self.assertEqual((on_disk.instance_id, on_disk.start_state), (INSTANCE, StartState(1, TOKEN)))
        self.assertEqual(on_disk.storage_root_id, marker.storage_root_id)
        self.assertTrue(os.path.isfile(root_marker.claim_lock_path(self.root)))

    def test_retry_of_the_same_claim_writes_nothing(self):
        self.claim()
        before = os.stat(root_marker.marker_path(self.root))
        marker = self.claim()
        after = os.stat(root_marker.marker_path(self.root))
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual(marker.start_state, StartState(1, TOKEN))

    def test_refusals_leave_the_marker_unchanged(self):
        self.claim()
        claimed = self.marker_bytes()
        cases = [
            ("root_claimed_by_other", dict(instance_id=OTHER_INSTANCE, token=OTHER_TOKEN)),
            ("root_moved_on", dict(token=OTHER_TOKEN)),
            ("root_moved_on", dict(objects=_has_objects)),
        ]
        for reason, kwargs in cases:
            with self.subTest(reason=reason, kwargs=kwargs):
                self.assert_refused(reason, self.claim, **kwargs)
                self.assertEqual(self.marker_bytes(), claimed)
        # A claimed root that has run since (generation 2) never goes back to 1.
        self.set_pairing(self.root, start_generation=2, start_token=OTHER_TOKEN)
        moved = self.marker_bytes()
        self.assert_refused("root_moved_on", self.claim)
        self.assertEqual(self.marker_bytes(), moved)

    def test_used_roots_are_refused(self):
        before = self.marker_bytes()
        error = self.assert_refused("root_used", self.claim, objects=_has_objects)
        self.assertIn("disaster recovery", str(error))
        self.assertEqual(self.marker_bytes(), before)
        self.set_pairing(self.root, start_generation=3)
        before = self.marker_bytes()
        self.assert_refused("root_used", self.claim)
        self.assertEqual(self.marker_bytes(), before)

    def test_unmarked_and_unbindable_roots_are_refused(self):
        os.remove(root_marker.marker_path(self.root))
        self.assert_refused("root_unmarked", self.claim)
        fenced = self.new_root("fenced")
        lease = self.shared(fenced)
        self.set_pairing(fenced, state="fenced", relocation={"id": "r1", "role": "source"})
        before = self.marker_bytes(fenced)
        self.assert_refused("root_fenced", self.claim, fenced, lease=lease)
        self.assertEqual(self.marker_bytes(fenced), before)

    def test_invalid_ids_are_refused_before_anything(self):
        with self.assertRaises(ValueError):
            self.claim(instance_id="IN-x")
        with self.assertRaises(ValueError):
            self.claim(token="short")

    def test_a_claim_that_cannot_be_made_durable_is_refused(self):
        real_replace = root_marker.replace_file_atomically

        def replace_without_directory_sync(path, data):
            real_replace(path, data)
            return False

        with patch.object(
            root_marker, "replace_file_atomically", side_effect=replace_without_directory_sync
        ), patch.object(root_marker, "fsync_directory", return_value=False):
            self.assert_refused("marker_not_durable", self.claim)
            # Its retry proves durability too before bootstrap may go on.
            self.assert_refused("marker_not_durable", self.claim)
        self.assertEqual(self.claim().start_state, StartState(1, TOKEN))

    def test_a_failed_marker_write_leaves_the_root_unclaimed(self):
        before = self.marker_bytes()
        with patch.object(root_marker, "replace_file_atomically", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.claim()
        self.assertEqual(self.marker_bytes(), before)
        self.assertEqual(self.claim().instance_id, INSTANCE)

    def test_two_claims_are_serialized_by_the_claim_lock(self):
        paused = threading.Event()
        resume = threading.Event()
        second_done = threading.Event()
        results = {}

        def hold():
            paused.set()
            if not resume.wait(10):
                raise AssertionError("first claim never resumed")

        def first():
            results["first"] = self.claim(before_write=hold)

        def second():
            try:
                self.claim(instance_id=OTHER_INSTANCE, token=OTHER_TOKEN)
            except StorageRootRefused as exc:
                results["second"] = exc
            finally:
                second_done.set()

        t1 = threading.Thread(target=first)
        t1.start()
        self.assertTrue(paused.wait(10))
        t2 = threading.Thread(target=second)
        t2.start()
        # The second claim waits on root.claim while the first sits between
        # its re-read and its write.
        self.assertFalse(second_done.wait(0.5))
        resume.set()
        t1.join(10)
        t2.join(10)
        self.assertEqual(results["first"].instance_id, INSTANCE)
        self.assertEqual(results["second"].reason, "root_claimed_by_other")
        marker = root_marker.read_marker(self.root)
        self.assertEqual((marker.instance_id, marker.start_state), (INSTANCE, StartState(1, TOKEN)))

    def test_claim_lock_wait_is_bounded(self):
        held = ExclusiveFileLock.acquire(root_marker.claim_lock_path(self.root))
        self.addCleanup(held.release)
        before = self.marker_bytes()
        self.assert_refused("root_claim_busy", self.claim, timeout=0.1)
        self.assertEqual(self.marker_bytes(), before)


class TestBumpStartGeneration(RootClaimTestCase):
    def bump(self, expected, to, *, instance_id=INSTANCE):
        return root_marker.bump_start_generation(
            self.root, lease=self.lease, instance_id=instance_id, expected=expected, to=to
        )

    def setUp(self):
        super().setUp()
        self.claim()

    def test_bump_moves_expected_to_next(self):
        marker, durable = self.bump(StartState(1, TOKEN), StartState(2, NEXT_TOKEN))
        self.assertTrue(durable)
        self.assertEqual(marker.start_state, StartState(2, NEXT_TOKEN))
        self.assertEqual(root_marker.read_marker(self.root).start_state, StartState(2, NEXT_TOKEN))
        self.assertEqual(root_marker.read_marker(self.root).instance_id, INSTANCE)

    def test_bump_already_at_target_is_a_no_op(self):
        self.bump(StartState(1, TOKEN), StartState(2, NEXT_TOKEN))
        before = os.stat(root_marker.marker_path(self.root))
        marker, _ = self.bump(StartState(1, TOKEN), StartState(2, NEXT_TOKEN))
        after = os.stat(root_marker.marker_path(self.root))
        self.assertEqual(marker.start_state, StartState(2, NEXT_TOKEN))
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))

    def test_bump_from_an_unexpected_state_or_instance_is_refused_unchanged(self):
        before = self.marker_bytes()
        self.assert_refused(
            "marker_generation_moved", self.bump, StartState(1, OTHER_TOKEN), StartState(2, NEXT_TOKEN)
        )
        self.assert_refused(
            "marker_generation_moved", self.bump, StartState(0, None), StartState(1, NEXT_TOKEN)
        )
        self.assert_refused(
            "root_claimed_by_other",
            self.bump, StartState(1, TOKEN), StartState(2, NEXT_TOKEN), instance_id=OTHER_INSTANCE,
        )
        self.assertEqual(self.marker_bytes(), before)

    def test_bump_never_skips_or_lowers_a_generation(self):
        for to in (StartState(3, NEXT_TOKEN), StartState(1, NEXT_TOKEN), StartState(2, None)):
            with self.subTest(to=to):
                with self.assertRaises(ValueError):
                    self.bump(StartState(1, TOKEN), to)

    def test_bump_reports_a_marker_it_could_not_make_durable(self):
        real_replace = root_marker.replace_file_atomically

        def replace_without_directory_sync(path, data):
            real_replace(path, data)
            return False

        with patch.object(
            root_marker, "replace_file_atomically", side_effect=replace_without_directory_sync
        ), patch.object(root_marker, "fsync_directory", return_value=False):
            _, durable = self.bump(StartState(1, TOKEN), StartState(2, NEXT_TOKEN))
            self.assertFalse(durable)
            # The retry finds the marker at its target and must prove it too.
            _, durable = self.bump(StartState(1, TOKEN), StartState(2, NEXT_TOKEN))
            self.assertFalse(durable)
        with patch.object(root_marker, "fsync_directory", return_value=True) as sync:
            _, durable = self.bump(StartState(1, TOKEN), StartState(2, NEXT_TOKEN))
        self.assertTrue(durable)
        sync.assert_called_once()
        # A failed directory sync during the write is retried once.
        with patch.object(
            root_marker, "replace_file_atomically", side_effect=replace_without_directory_sync
        ), patch.object(root_marker, "fsync_directory", return_value=True):
            _, durable = self.bump(StartState(2, NEXT_TOKEN), StartState(3, OTHER_TOKEN))
        self.assertTrue(durable)

    def test_failed_marker_write_leaves_the_marker_unchanged(self):
        before = self.marker_bytes()
        with patch.object(root_marker, "replace_file_atomically", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.bump(StartState(1, TOKEN), StartState(2, NEXT_TOKEN))
        self.assertEqual(self.marker_bytes(), before)


class TestClaimLockInventory(RootClaimTestCase):
    def test_lone_claim_lock_is_scaffold_of_a_new_root(self):
        root = os.path.join(self.tmp, "scaffold")
        os.makedirs(os.path.join(root, MAINT))
        for name in (root_marker.CLAIM_LOCK_NAME, root_marker.ROOT_LOCK_NAME):
            open(os.path.join(root, MAINT, name), "w").close()
        bound = root_binding.open_storage_root(StorageConfig.for_testing(root), register=False)
        self.addCleanup(bound.release)
        self.assertTrue(bound.created)

    def test_claim_lock_that_is_not_a_file_is_not_scaffold(self):
        root = os.path.join(self.tmp, "odd")
        os.makedirs(os.path.join(root, MAINT, root_marker.CLAIM_LOCK_NAME))
        self.assertFalse(root_binding._maintenance_is_scaffold(os.path.join(root, MAINT)))

    def test_claim_lock_is_operational_state_never_backup_payload(self):
        inv = backup_storage_inventory()
        self.assertIn(".prks-maintenance/root.claim", inv.operational_root_entries)

    def test_log_file_cannot_be_the_claim_lock(self):
        root = os.path.join(self.tmp, "logged")
        config = StorageConfig.for_testing(root)
        from dataclasses import replace

        config = replace(config, log_file=os.path.join(root, MAINT, root_marker.CLAIM_LOCK_NAME))
        with self.assertRaises(Exception) as ctx:
            root_binding.open_storage_root(config, register=False)
        self.assertEqual(getattr(ctx.exception, "reason", None), "log_file_operational")


if __name__ == "__main__":
    unittest.main()
