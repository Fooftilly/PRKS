"""Root marker, startup validation subset and the single-process lease (Phase A).

Every root here is a scratch directory; configurations come from
``StorageConfig.for_testing`` so the testing-safety guard applies throughout.
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend.storage import distribution, paths, preflight, root_binding, root_marker
from backend.storage.config import StorageConfig
from backend.storage.errors import InvalidStorageRoot, StorageRootInUse, StorageRootRefused
from backend.storage.file_lock import ExclusiveFileLock, LockBusy, LockUnavailable

MARKER = root_marker.MARKER_FILENAME
MAINT = root_binding.MAINTENANCE_DIRNAME


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path, doc):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(doc, handle)


def _symlink_or_skip(testcase, target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover
        testcase.skipTest(f"symlinks unavailable: {exc}")


class RootTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-rootbind-")
        self.tmp = os.path.realpath(self._tmp.name)
        self._bound = []

    def tearDown(self):
        for bound in self._bound:
            bound.release()
        self._tmp.cleanup()

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def open(self, root, **kwargs):
        kwargs.setdefault("register", False)
        bound = root_binding.open_storage_root(StorageConfig.for_testing(root), **kwargs)
        self._bound.append(bound)
        return bound

    def marker_doc(self, root):
        return _read_json(os.path.join(root, MARKER))

    def assert_untouched(self, root, expected_names):
        self.assertEqual(sorted(os.listdir(root)), sorted(expected_names))


class TestNewAndAdoptedRoots(RootTestCase):
    def test_absent_root_with_existing_parent_is_created_with_marker(self):
        root = self.path("lib")
        bound = self.open(root)
        self.assertTrue(bound.created)
        self.assertFalse(bound.adopted)
        doc = self.marker_doc(root)
        self.assertRegex(doc["storage_root_id"], r"^SR-[0-9a-f]{32}$")
        self.assertEqual(doc["format"], 1)
        self.assertEqual(doc["layout_version"], 1)
        self.assertEqual(doc["state"], "active")
        self.assertIsNone(doc["relocation"])
        self.assertEqual(doc["active_process"]["pid"], os.getpid())
        self.assertIn("directory_fsync", doc["filesystem_probe"])
        self.assertEqual(sorted(os.listdir(os.path.join(root, MAINT))), ["root.lock"])
        self.assertEqual(sorted(os.listdir(root)), sorted([MARKER, MAINT]))
        if os.name == "posix":
            self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)

    def test_missing_parent_is_refused_and_nothing_is_created(self):
        root = self.path("missing", "lib")
        with self.assertRaises(InvalidStorageRoot) as ctx:
            self.open(root)
        self.assertEqual(ctx.exception.reason, "root_parent_missing")
        self.assertFalse(os.path.exists(self.path("missing")))

    def test_file_in_place_of_root_is_refused(self):
        root = self.path("lib")
        open(root, "w").close()
        with self.assertRaises(InvalidStorageRoot) as ctx:
            self.open(root)
        self.assertEqual(ctx.exception.reason, "root_not_directory")

    def test_empty_directory_and_os_metadata_and_scaffold_make_a_new_root(self):
        root = self.path("lib")
        os.makedirs(os.path.join(root, MAINT, "preflight", "a"))
        open(os.path.join(root, ".DS_Store"), "w").close()
        os.mkdir(os.path.join(root, "lost+found"))
        open(os.path.join(root, MAINT, "root.lock"), "w").close()
        open(os.path.join(root, MAINT, "preflight", ".prks-probe-1"), "w").close()
        open(os.path.join(root, MAINT, "preflight", "a", ".prks-probe-2"), "w").close()
        open(os.path.join(root, ".prks-write-abc.tmp"), "w").close()
        bound = self.open(root)
        self.assertTrue(bound.created)
        self.assertFalse(os.path.exists(os.path.join(root, MAINT, "preflight")))

    def test_unmarked_library_with_database_is_adopted_in_place(self):
        root = self.path("lib")
        os.mkdir(root)
        with open(os.path.join(root, "prks_data.db"), "wb") as handle:
            handle.write(b"SQLite format 3\x00")
        os.mkdir(os.path.join(root, "people"))
        open(os.path.join(root, "notes.txt"), "w").close()
        bound = self.open(root)
        self.assertTrue(bound.adopted)
        self.assertFalse(bound.created)
        with open(os.path.join(root, "prks_data.db"), "rb") as handle:
            self.assertEqual(handle.read(), b"SQLite format 3\x00")
        self.assertTrue(os.path.isfile(os.path.join(root, "notes.txt")))

    def test_unmarked_library_with_only_pdfs_dir_is_adopted(self):
        root = self.path("lib")
        os.makedirs(os.path.join(root, "pdfs"))
        self.assertTrue(self.open(root).adopted)

    def test_unmarked_root_mid_restore_is_adopted_so_recovery_can_run(self):
        root = self.path("lib")
        os.makedirs(os.path.join(root, MAINT, "rollback"))
        _write_json(os.path.join(root, MAINT, "restore-journal.json"), {"phase": "moving_old"})
        self.assertTrue(self.open(root).adopted)

    def test_classification_uses_the_modes_database_name(self):
        root = self.path("lib")
        os.mkdir(root)
        open(os.path.join(root, "prks_data_testing.db"), "w").close()
        self.assertEqual(
            root_binding.classify_unmarked_root(root, db_filename="prks_data_testing.db"),
            root_binding.UNMARKED_PRKS,
        )
        self.assertEqual(
            root_binding.classify_unmarked_root(root, db_filename="prks_data.db"),
            root_binding.UNMARKED_FOREIGN,
        )

    def test_foreign_non_empty_directory_is_refused_before_any_write(self):
        root = self.path("lib")
        os.mkdir(root)
        open(os.path.join(root, "server.stdout"), "w").close()
        os.mkdir(os.path.join(root, "photos"))
        with self.assertRaises(StorageRootRefused) as ctx:
            self.open(root)
        self.assertEqual(ctx.exception.reason, "root_foreign")
        self.assert_untouched(root, ["server.stdout", "photos"])

    def test_storage_root_id_is_stable_across_binds(self):
        root = self.path("lib")
        first = self.open(root)
        first_id = first.storage_root_id
        created_at = self.marker_doc(root)["created_at"]
        first.release()
        second = self.open(root)
        self.assertEqual(second.storage_root_id, first_id)
        self.assertFalse(second.created or second.adopted)
        self.assertEqual(self.marker_doc(root)["created_at"], created_at)

    def test_unknown_marker_fields_and_moved_from_survive_rebinding(self):
        root = self.path("lib")
        self.open(root).release()
        doc = self.marker_doc(root)
        doc["moved_from"] = {"id": "rel-9", "peer_hint": "/old", "residuals": True}
        doc["future_field"] = {"x": 1}
        _write_json(os.path.join(root, MARKER), doc)
        self.open(root)
        after = self.marker_doc(root)
        self.assertEqual(after["moved_from"], doc["moved_from"])
        self.assertEqual(after["future_field"], {"x": 1})

    def test_new_marker_must_be_durable(self):
        root = self.path("lib")
        with patch.object(root_marker, "replace_file_atomically", return_value=False):
            with self.assertRaises(StorageRootRefused) as ctx:
                self.open(root)
        self.assertEqual(ctx.exception.reason, "marker_not_durable")
        # The lease was released on the way out.
        self.open(root)


class TestMarkerRefusals(RootTestCase):
    def _root_with_marker(self, **changes):
        root = self.path("lib")
        self.open(root).release()
        self._bound.clear()
        doc = self.marker_doc(root)
        doc.update(changes)
        _write_json(os.path.join(root, MARKER), doc)
        with open(os.path.join(root, MARKER), "rb") as handle:
            return root, handle.read()

    def _refused(self, root, reason, raw=None, **kwargs):
        with self.assertRaises(StorageRootRefused) as ctx:
            self.open(root, **kwargs)
        self.assertEqual(ctx.exception.reason, reason)
        if raw is not None:
            with open(os.path.join(root, MARKER), "rb") as handle:
                self.assertEqual(handle.read(), raw, "a refused marker must not be rewritten")
        return ctx.exception

    def test_retired_root_is_refused_with_pointer(self):
        root, raw = self._root_with_marker(
            state="retired", relocation={"id": "rel-1", "role": "source", "peer_hint": "/mnt/new-lib"}
        )
        exc = self._refused(root, "root_retired", raw)
        self.assertIn("/mnt/new-lib", exc.message)

    def test_fenced_source_is_refused(self):
        root, raw = self._root_with_marker(
            state="fenced", relocation={"id": "rel-2", "role": "source", "peer_hint": "/mnt/dest"}
        )
        exc = self._refused(root, "root_fenced", raw)
        self.assertIn("rel-2", exc.message)

    def test_staging_destination_is_refused_in_every_phase(self):
        for phase in (None, "committed", "aborted"):
            with self.subTest(phase=phase):
                relocation = {"id": "rel-3", "role": "destination", "peer_hint": "/src"}
                if phase:
                    relocation["phase"] = phase
                root, raw = self._root_with_marker(state="staging", relocation=relocation)
                self._refused(root, "root_staging", raw)
                shutil.rmtree(root)

    def test_active_marker_with_relocation_role_is_refused(self):
        root, raw = self._root_with_marker(relocation={"id": "r", "role": "destination"})
        self._refused(root, "root_not_active", raw)

    def test_malformed_markers_are_refused(self):
        cases = {
            "marker_unparseable": b"{nope",
            "marker_storage_root_id_invalid": json.dumps(
                {"format": 1, "storage_root_id": "SR-XYZ", "layout_version": 1, "state": "active",
                 "created_at": "t", "relocation": None}).encode(),
            "marker_state_invalid": json.dumps(
                {"format": 1, "storage_root_id": "SR-" + "0" * 32, "layout_version": 1, "state": "weird",
                 "created_at": "t", "relocation": None}).encode(),
            "marker_format_invalid": json.dumps({"format": "1"}).encode(),
            "marker_relocation_invalid": json.dumps(
                {"format": 1, "storage_root_id": "SR-" + "0" * 32, "layout_version": 1, "state": "staging",
                 "created_at": "t", "relocation": {"id": "r", "role": "sideways"}}).encode(),
        }
        for reason, raw in cases.items():
            with self.subTest(reason=reason):
                root = self.path(reason)
                os.mkdir(root)
                with open(os.path.join(root, MARKER), "wb") as handle:
                    handle.write(raw)
                self._refused(root, reason, raw)
                self.assertFalse(os.path.exists(os.path.join(root, MAINT)))

    def test_newer_format_and_layout_are_refused(self):
        root, raw = self._root_with_marker(format=2)
        self._refused(root, "marker_format_newer", raw)
        shutil.rmtree(root)
        root, raw = self._root_with_marker(layout_version=2)
        self._refused(root, "layout_version_newer", raw)

    def test_marker_that_is_a_link_or_directory_is_refused(self):
        root = self.path("lib")
        os.mkdir(root)
        real = self.path("elsewhere.json")
        _write_json(real, root_marker.new_marker_document())
        _symlink_or_skip(self, real, os.path.join(root, MARKER))
        self._refused(root, "marker_is_link")
        other = self.path("lib2")
        os.makedirs(os.path.join(other, MARKER))
        self._refused(other, "marker_not_a_file")

    def test_expected_storage_root_id_mismatch_is_foreign(self):
        root = self.path("lib")
        self.open(root).release()
        self._refused(root, "root_foreign", expected_storage_root_id="SR-" + "1" * 32)
        unmarked = self.path("unmarked")
        os.makedirs(os.path.join(unmarked, "pdfs"))
        self._refused(unmarked, "root_foreign", expected_storage_root_id="SR-" + "1" * 32)
        self.assertFalse(os.path.exists(os.path.join(unmarked, MARKER)))


class TestPlacement(RootTestCase):
    """V11/V12 path checks run before anything is written."""

    def test_nested_inside_another_root(self):
        outer = self.path("outer")
        self.open(outer)
        inner = os.path.join(outer, "sub")
        with self.assertRaises(InvalidStorageRoot) as ctx:
            self.open(inner)
        self.assertEqual(ctx.exception.reason, "root_nested")
        self.assertFalse(os.path.exists(inner))

    def test_containing_another_root(self):
        inner = self.path("outer", "a", "inner")
        os.makedirs(os.path.dirname(inner))
        self.open(inner)
        with self.assertRaises(InvalidStorageRoot) as ctx:
            self.open(self.path("outer"))
        self.assertEqual(ctx.exception.reason, "root_contains_root")
        self.assertFalse(os.path.exists(self.path("outer", MARKER)))

    def test_inside_a_maintenance_directory(self):
        root = self.path("x", MAINT, "lib")
        os.makedirs(os.path.dirname(root))
        with self.assertRaises(InvalidStorageRoot) as ctx:
            self.open(root)
        self.assertEqual(ctx.exception.reason, "root_inside_maintenance")

    def test_home_directory_and_filesystem_root(self):
        home = self.path("home")
        os.mkdir(home)
        with self.assertRaises(InvalidStorageRoot) as ctx:
            self.open(home, home=home)
        self.assertEqual(ctx.exception.reason, "root_is_home")
        with self.assertRaises(InvalidStorageRoot) as ctx:
            root_binding._check_placement(
                os.path.abspath(os.sep), testing=False, config_file_path=None,
                distribution=distribution.SOURCE, home=home,
            )
        self.assertEqual(ctx.exception.reason, "root_is_filesystem_root")

    def test_root_must_not_contain_the_bootstrap_config_file(self):
        root = self.path("lib")
        with self.assertRaises(InvalidStorageRoot) as ctx:
            self.open(root, config_file_path=os.path.join(root, "cfg", "config.json"))
        self.assertEqual(ctx.exception.reason, "root_contains_config")
        # Beside the config file (the macOS/Windows default shape) is fine.
        os.makedirs(self.path("PRKS"))
        self.open(self.path("PRKS", "Library"), config_file_path=self.path("PRKS", "config.json"))

    def test_install_directory_rules(self):
        fake_repo = self.path("checkout")
        os.makedirs(os.path.join(fake_repo, "lib"))
        with patch.object(paths, "repo_root", return_value=fake_repo):
            with self.assertRaises(InvalidStorageRoot) as ctx:
                root_binding._check_placement(
                    self.tmp, testing=False, config_file_path=None,
                    distribution=distribution.SOURCE, home=self.path("h"),
                )
            self.assertEqual(ctx.exception.reason, "root_contains_install")
            with self.assertRaises(InvalidStorageRoot) as ctx:
                root_binding._check_placement(
                    os.path.join(fake_repo, "lib"), testing=False, config_file_path=None,
                    distribution=distribution.PACKAGED, home=self.path("h"),
                )
            self.assertEqual(ctx.exception.reason, "root_inside_install")
            # A source checkout keeps its development default and existing
            # self-hosted locations inside the checkout.
            root_binding._check_placement(
                os.path.join(fake_repo, "lib"), testing=False, config_file_path=None,
                distribution=distribution.SOURCE, home=self.path("h"),
            )

    def test_production_never_binds_the_testing_tree(self):
        fake_repo = self.path("checkout")
        os.makedirs(os.path.join(fake_repo, "data_testing"))
        with patch.object(paths, "repo_root", return_value=fake_repo):
            with self.assertRaises(InvalidStorageRoot) as ctx:
                root_binding._check_placement(
                    os.path.join(fake_repo, "data_testing"), testing=False, config_file_path=None,
                    distribution=distribution.SOURCE, home=self.path("h"),
                )
        self.assertEqual(ctx.exception.reason, "production_testing_root")

    def test_testing_refuses_production_trees(self):
        with self.assertRaises(InvalidStorageRoot) as ctx:
            root_binding._check_placement(
                "/data", testing=True, config_file_path=None,
                distribution=distribution.SOURCE, home=self.path("h"),
            )
        self.assertEqual(ctx.exception.reason, "testing_unsafe_root")


class TestLinksAndFilesystems(RootTestCase):
    """V13 (no links inside the root) and V7 (one filesystem)."""

    def test_root_itself_may_be_a_link(self):
        target = self.path("real")
        os.mkdir(target)
        link = self.path("link")
        _symlink_or_skip(self, target, link)
        bound = self.open(link)
        self.assertEqual(bound.root_real, target)
        self.assertTrue(os.path.isfile(os.path.join(target, MARKER)))

    def test_canonical_component_link_is_refused(self):
        root = self.path("lib")
        os.mkdir(root)
        elsewhere = self.path("pdf-store")
        os.mkdir(elsewhere)
        _symlink_or_skip(self, elsewhere, os.path.join(root, "pdfs"))
        with self.assertRaises(StorageRootRefused) as ctx:
            self.open(root)
        self.assertEqual(ctx.exception.reason, "root_contains_link")
        self.assertFalse(os.path.exists(os.path.join(root, MARKER)))

    def test_maintenance_link_is_refused(self):
        root = self.path("lib")
        os.makedirs(os.path.join(root, "pdfs"))
        elsewhere = self.path("maint")
        os.mkdir(elsewhere)
        _symlink_or_skip(self, elsewhere, os.path.join(root, MAINT))
        with self.assertRaises(StorageRootRefused) as ctx:
            self.open(root)
        self.assertEqual(ctx.exception.reason, "root_contains_link")
        self.assertEqual(os.listdir(elsewhere), [])

    def test_linked_inbox_is_kept_working_with_a_warning(self):
        root = self.path("lib")
        os.makedirs(os.path.join(root, "pdfs"))
        inbox = self.path("inbox")
        os.mkdir(inbox)
        _symlink_or_skip(self, inbox, os.path.join(root, "for_processing"))
        with self.assertLogs("prks.storage", level="WARNING") as logs:
            self.open(root)
        self.assertTrue(any("storage_root_component_is_link component=inbox" in m for m in logs.output))
        self.assertFalse(any(self.tmp in m for m in logs.output))

    def test_component_on_another_filesystem_is_refused(self):
        root = self.path("lib")
        os.makedirs(os.path.join(root, "pdfs"))
        real_lstat = root_binding._lstat
        pdfs = os.path.join(root, "pdfs")

        def other_device(path):
            st = real_lstat(path)
            if st is not None and path == pdfs:
                values = list(st)
                values[2] = st.st_dev + 1
                return os.stat_result(values)
            return st

        with patch.object(root_binding, "_lstat", other_device):
            with self.assertRaises(StorageRootRefused) as ctx:
                self.open(root)
        self.assertEqual(ctx.exception.reason, "root_spans_filesystems")


class TestPreflight(RootTestCase):
    def test_unwritable_root_is_refused_and_lease_released(self):
        root = self.path("lib")
        with patch.object(preflight, "_write_probe", side_effect=OSError(30, "EROFS")):
            with self.assertRaises(StorageRootRefused) as ctx:
                self.open(root)
        self.assertEqual(ctx.exception.reason, "root_not_writable")
        self.assertFalse(os.path.exists(os.path.join(root, MARKER)))
        self.assertFalse(os.path.exists(os.path.join(root, MAINT, "preflight")))
        self.open(root)

    def test_capability_probes_run_once_per_device(self):
        root = self.path("lib")
        seen = []
        real = root_binding.run_preflight

        def spy(maintenance, *, capabilities):
            seen.append(capabilities)
            return real(maintenance, capabilities=capabilities)

        with patch.object(root_binding, "run_preflight", spy):
            self.open(root).release()
            self.open(root).release()
            doc = self.marker_doc(root)
            doc["filesystem_probe"]["device"] = -1
            _write_json(os.path.join(root, MARKER), doc)
            self.open(root).release()
        self.assertEqual(seen, [True, False, True])

    def test_exclusive_create_failure_is_detected(self):
        maintenance = self.path("m")
        os.mkdir(maintenance)
        real_open = os.open

        def no_excl(path, flags, *args):
            if os.path.basename(path).startswith(preflight.PROBE_PREFIX) and os.path.exists(path):
                flags &= ~os.O_EXCL
            return real_open(path, flags, *args)

        with patch.object(preflight.os, "open", no_excl):
            with self.assertRaises(StorageRootRefused) as ctx:
                preflight.run_preflight(maintenance, capabilities=True)
        self.assertEqual(ctx.exception.reason, "exclusive_create_unsupported")
        self.assertFalse(os.path.exists(os.path.join(maintenance, "preflight")))

    def test_leftover_scaffold_with_foreign_content_is_not_deleted(self):
        maintenance = self.path("m")
        os.makedirs(os.path.join(maintenance, "preflight"))
        keep = os.path.join(maintenance, "preflight", "my-notes.txt")
        open(keep, "w").close()
        with self.assertRaises(StorageRootRefused) as ctx:
            preflight.run_preflight(maintenance, capabilities=False)
        self.assertEqual(ctx.exception.reason, "preflight_unexpected_content")
        self.assertTrue(os.path.isfile(keep))


class TestWarnings(RootTestCase):
    def test_network_filesystem_detection(self):
        mounts = self.path("mounts")
        with open(mounts, "w", encoding="utf-8") as handle:
            handle.write("/dev/sda1 / ext4 rw 0 0\n")
            handle.write("server:/export /mnt/nas\\040share nfs4 rw 0 0\n")
        self.assertEqual(root_binding.detect_filesystem_type("/mnt/nas share/lib", mounts_file=mounts), "nfs4")
        self.assertEqual(root_binding.detect_filesystem_type("/home/u/lib", mounts_file=mounts), "ext4")
        self.assertIsNone(root_binding.detect_filesystem_type("/x", mounts_file=self.path("absent")))

    def test_network_and_low_space_warnings_are_path_free(self):
        root = self.path("lib")
        usage = shutil.disk_usage(self.tmp)._replace(free=10)
        with (
            patch.object(root_binding, "detect_filesystem_type", return_value="nfs"),
            patch.object(root_binding.shutil, "disk_usage", return_value=usage),
            self.assertLogs("prks.storage", level="INFO") as logs,
        ):
            self.open(root)
        text = "\n".join(logs.output)
        self.assertIn("storage_root_network_filesystem fs_type=nfs", text)
        self.assertIn("storage_root_low_free_space", text)
        self.assertIn("storage_root_bound", text)
        self.assertNotIn(self.tmp, text)
        self.assertNotIn(self.marker_doc(root)["storage_root_id"], text)


class TestLeaseInProcess(RootTestCase):
    def test_second_bind_of_one_root_is_refused_until_release(self):
        root = self.path("lib")
        first = self.open(root)
        with self.assertRaises(StorageRootInUse) as ctx:
            self.open(root)
        self.assertEqual(ctx.exception.reason, "root_in_use")
        self.assertIn(f"process {os.getpid()}", ctx.exception.message)
        first.release()
        self.open(root)

    def test_diagnostic_holder_is_never_the_authority(self):
        root = self.path("lib")
        self.open(root).release()
        doc = self.marker_doc(root)
        # A recorded holder that is this very process, or a PID that does not
        # exist, changes nothing: only the OS lock decides.
        doc["active_process"] = {"pid": 999999, "host": "elsewhere", "started_at": "x"}
        _write_json(os.path.join(root, MARKER), doc)
        bound = self.open(root)
        doc = self.marker_doc(root)
        doc["active_process"] = {"pid": None}
        _write_json(os.path.join(root, MARKER), doc)
        with self.assertRaises(StorageRootInUse):
            self.open(root)
        bound.release()

    def test_registered_root_guards_bind_storage(self):
        from backend import server

        root = self.path("lib")
        other = self.path("other")
        bound = root_binding.open_storage_root(StorageConfig.for_testing(root))
        self._bound.append(bound)
        self.assertIs(root_binding.active_bound_root(), bound)
        root_binding.assert_config_matches_bound_root(root)
        with self.assertRaises(StorageRootRefused) as ctx:
            server.bind_storage(StorageConfig.for_testing(other))
        self.assertEqual(ctx.exception.reason, "root_not_leased")
        self.assertFalse(os.path.exists(other))
        bound.release()
        self.assertIsNone(root_binding.active_bound_root())
        root_binding.assert_config_matches_bound_root(other)


class TestFileLock(RootTestCase):
    def test_second_handle_in_one_process_conflicts(self):
        path = self.path("x.lock")
        lock = ExclusiveFileLock.acquire(path)
        with self.assertRaises(LockBusy):
            ExclusiveFileLock.acquire(path)
        lock.release()
        lock.release()  # idempotent
        with ExclusiveFileLock.acquire(path) as again:
            self.assertTrue(again.held)
        self.assertFalse(again.held)

    def test_timeout_waits_then_reports_busy(self):
        path = self.path("x.lock")
        lock = ExclusiveFileLock.acquire(path)
        started = time.monotonic()
        with self.assertRaises(LockBusy):
            ExclusiveFileLock.acquire(path, timeout=0.3)
        self.assertGreaterEqual(time.monotonic() - started, 0.25)
        lock.release()

    def test_link_and_non_regular_lock_files_are_refused(self):
        target = self.path("target.lock")
        open(target, "w").close()
        link = self.path("link.lock")
        _symlink_or_skip(self, target, link)
        with self.assertRaises(LockUnavailable) as ctx:
            ExclusiveFileLock.acquire(link)
        self.assertEqual(ctx.exception.reason, "lock_file_is_link")
        os.mkdir(self.path("dir.lock"))
        with self.assertRaises(LockUnavailable) as ctx:
            ExclusiveFileLock.acquire(self.path("dir.lock"))
        self.assertEqual(ctx.exception.reason, "lock_file_not_regular")


_HOLDER = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {project!r})
    from backend.storage.config import StorageConfig
    from backend.storage.root_binding import open_storage_root
    bound = open_storage_root(StorageConfig.for_testing(sys.argv[1]))
    print(bound.storage_root_id, flush=True)
    sys.stdin.readline()
    """
)


class TestLeaseAcrossProcesses(RootTestCase):
    def _spawn_holder(self, root):
        proc = subprocess.Popen(
            [sys.executable, "-c", _HOLDER.format(project=_PROJECT_DIR), root],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        line = proc.stdout.readline().strip()
        if not line.startswith("SR-"):
            proc.kill()
            _out, err = proc.communicate(timeout=30)
            self.fail(f"holder failed to bind: {err}")
        self.addCleanup(self._reap, proc)
        return proc, line

    @staticmethod
    def _reap(proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=30)

    def test_second_process_is_refused_and_crash_releases_the_lease(self):
        root = self.path("lib")
        holder, root_id = self._spawn_holder(root)
        with self.assertRaises(StorageRootInUse) as ctx:
            self.open(root)
        self.assertIn(f"process {holder.pid}", ctx.exception.message)
        holder.kill()  # no orderly release: the kernel drops the lock
        holder.wait(30)
        bound = self.open(root)
        self.assertEqual(bound.storage_root_id, root_id)

    @unittest.skipUnless(hasattr(signal, "SIGSTOP"), "needs POSIX job control")
    def test_suspended_holder_keeps_the_lease(self):
        root = self.path("lib")
        holder, _ = self._spawn_holder(root)
        os.kill(holder.pid, signal.SIGSTOP)
        try:
            time.sleep(0.2)
            with self.assertRaises(StorageRootInUse):
                self.open(root)
        finally:
            os.kill(holder.pid, signal.SIGCONT)
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(30)
        self.open(root)

    def test_orderly_exit_releases_the_lease(self):
        root = self.path("lib")
        holder, _ = self._spawn_holder(root)
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(30)
        self.open(root)


class TestLoggingOnRefusal(RootTestCase):
    def test_refusals_never_log_paths(self):
        root = self.path("lib")
        os.mkdir(root)
        open(os.path.join(root, "unrelated.txt"), "w").close()
        logger = logging.getLogger("prks.storage")
        with patch.object(logger, "warning") as warn, patch.object(logger, "error") as err:
            with self.assertRaises(StorageRootRefused):
                self.open(root)
        for call in warn.call_args_list + err.call_args_list:
            self.assertNotIn(self.tmp, " ".join(map(str, call.args)))


if __name__ == "__main__":
    unittest.main()


class TestProcessEntry(RootTestCase):
    def test_storage_root_option(self):
        from prks_app import build_parser

        self.assertIsNone(build_parser().parse_args([]).storage_root)
        args = build_parser().parse_args(["--testing", "--storage-root", "/srv/lib"])
        self.assertEqual(args.storage_root, "/srv/lib")

    def test_root_is_opened_before_restore_recovery_and_binding(self):
        with open(os.path.join(_PROJECT_DIR, "prks_app.py"), encoding="utf-8") as handle:
            src = handle.read()
        main = src[src.index('if __name__ == "__main__":'):]
        order = [
            main.index("ensure_runtime_or_exit()"),
            main.index("StorageConfig.from_env(cli_root=args.storage_root)"),
            main.index("open_storage(config)"),
            main.index("recover_incomplete_restore(config)"),
            main.index("bind_storage(config)"),
            main.index("run_server("),
        ]
        self.assertEqual(order, sorted(order))

    def test_refused_root_exits_cleanly_before_touching_storage(self):
        foreign = self.path("foreign")
        os.mkdir(foreign)
        open(os.path.join(foreign, "holiday.jpg"), "w").close()
        env = dict(os.environ, PRKS_TESTING="1", PRKS_STORAGE=self.path("ignored"))
        env.pop("PRKS_LOG_FILE", None)
        proc = subprocess.run(
            [sys.executable, os.path.join(_PROJECT_DIR, "prks_app.py"), "--testing",
             "--storage-root", foreign, "--port", "1"],
            cwd=_PROJECT_DIR, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("PRKS cannot open its storage root", proc.stderr)
        self.assertIn("does not look like a PRKS library", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(os.listdir(foreign), ["holiday.jpg"])
        # --storage-root won over PRKS_STORAGE: the env root was never created.
        self.assertFalse(os.path.exists(self.path("ignored")))

    def test_invalid_cli_root_is_a_clean_startup_error(self):
        env = dict(os.environ, PRKS_TESTING="1")
        proc = subprocess.run(
            [sys.executable, os.path.join(_PROJECT_DIR, "prks_app.py"), "--testing",
             "--storage-root", "   ", "--port", "1"],
            cwd=_PROJECT_DIR, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("PRKS cannot select its storage root", proc.stderr)
