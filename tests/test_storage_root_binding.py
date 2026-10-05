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
from dataclasses import replace
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
        with open(os.path.join(root, "prks_data_testing.db"), "wb") as handle:
            handle.write(root_binding.SQLITE_HEADER)
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

    def test_log_file_never_aliases_an_operational_file(self):
        root = self.path("lib")
        cfg_dir = self.path("PRKS")
        os.makedirs(cfg_dir)
        config_file = os.path.join(cfg_dir, "config.json")
        os.symlink(os.path.join(root, MARKER), self.path("marker-alias.log"))
        aliases = (
            os.path.join(root, MARKER),
            self.path("marker-alias.log"),
            os.path.join(root, root_marker.MAINTENANCE_DIRNAME, root_marker.ROOT_LOCK_NAME),
            os.path.join(root, root_marker.MAINTENANCE_DIRNAME, "errors.log"),
            config_file,
            config_file + ".lock",
            os.path.join(cfg_dir, ".", "config.json"),
        )
        for log_file in aliases:
            with self.subTest(log_file=log_file):
                config = replace(StorageConfig.for_testing(root), log_file=log_file)
                with self.assertRaises(InvalidStorageRoot) as ctx:
                    root_binding.open_storage_root(
                        config, config_file_path=config_file, register=False
                    )
                self.assertEqual(ctx.exception.reason, "log_file_operational")
                # Refused before anything is written.
                self.assertFalse(os.path.lexists(root))
        # A log beside them is fine.
        config = replace(StorageConfig.for_testing(root), log_file=os.path.join(cfg_dir, "prks.log"))
        bound = root_binding.open_storage_root(config, config_file_path=config_file, register=False)
        self._bound.append(bound)

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
        with open(os.path.join(root, "prks_data.db"), "wb") as handle:
            handle.write(root_binding.SQLITE_HEADER)
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

    def test_linked_inbox_inside_the_root_is_refused(self):
        root = self.path("lib")
        os.makedirs(os.path.join(root, "pdfs"))
        inbox = self.path("inbox")
        os.mkdir(inbox)
        _symlink_or_skip(self, inbox, os.path.join(root, "for_processing"))
        with self.assertRaises(StorageRootRefused) as ctx:
            self.open(root)
        self.assertEqual(ctx.exception.reason, "root_contains_link")
        self.assertFalse(os.path.exists(os.path.join(root, MARKER)))

    def test_existing_object_links_inside_components_are_refused(self):
        for component, nested in (
            ("pdfs", ("a.pdf",)),
            ("people", ("p_hash.webp",)),
            ("thumbs", ("W-1_p1.webp",)),
            ("for_processing", ("batch", "queued.pdf")),
        ):
            with self.subTest(component=component):
                root = self.path(f"lib-{component}")
                os.makedirs(os.path.join(root, "pdfs"))
                directory = os.path.join(root, component, *nested[:-1])
                os.makedirs(directory, exist_ok=True)
                target = os.path.join(root, "pdfs", "b.pdf")
                with open(target, "wb") as handle:
                    handle.write(b"%PDF other")
                _symlink_or_skip(self, target, os.path.join(directory, nested[-1]))
                with self.assertRaises(StorageRootRefused) as ctx:
                    self.open(root)
                self.assertEqual(ctx.exception.reason, "root_contains_link")
                self.assertFalse(os.path.exists(os.path.join(root, MARKER)))

    def test_link_inside_maintenance_is_refused(self):
        root = self.path("lib")
        os.makedirs(os.path.join(root, "pdfs"))
        os.makedirs(os.path.join(root, MAINT))
        elsewhere = self.path("rollback-elsewhere")
        os.mkdir(elsewhere)
        _symlink_or_skip(self, elsewhere, os.path.join(root, MAINT, "rollback"))
        with self.assertRaises(StorageRootRefused) as ctx:
            self.open(root)
        self.assertEqual(ctx.exception.reason, "root_contains_link")

    def test_inbox_outside_the_root_is_not_this_roots_business(self):
        root = self.path("lib")
        inbox_target = self.path("inbox-real")
        os.mkdir(inbox_target)
        link = self.path("inbox-link")
        _symlink_or_skip(self, inbox_target, link)
        cfg = replace(StorageConfig.for_testing(root), processing_dir=link)
        bound = root_binding.open_storage_root(cfg, register=False)
        self._bound.append(bound)
        self.assertTrue(bound.created)

    def _other_device_for(self, target):
        real_lstat = root_binding._lstat

        def fake(path):
            st = real_lstat(path)
            if st is not None and path == target:
                values = list(st)
                values[2] = st.st_dev + 1
                return os.stat_result(values)
            return st

        return patch.object(root_binding, "_lstat", fake)

    def test_maintenance_on_another_filesystem_is_refused(self):
        root = self.path("lib")
        os.makedirs(os.path.join(root, "pdfs"))
        with self._other_device_for(os.path.join(root, MAINT)):
            with self.assertRaises(StorageRootRefused) as ctx:
                self.open(root)
        self.assertEqual(ctx.exception.reason, "root_spans_filesystems")
        self.assertFalse(os.path.exists(os.path.join(root, MARKER)))

    def test_maintenance_subtree_on_another_filesystem_is_refused(self):
        root = self.path("lib")
        os.makedirs(os.path.join(root, "pdfs"))
        os.makedirs(os.path.join(root, MAINT, "rollback"))
        with self._other_device_for(os.path.join(root, MAINT, "rollback")):
            with self.assertRaises(StorageRootRefused) as ctx:
                self.open(root)
        self.assertEqual(ctx.exception.reason, "root_spans_filesystems")

    def test_inbox_on_another_filesystem_is_refused(self):
        root = self.path("lib")
        os.makedirs(os.path.join(root, "pdfs"))
        os.makedirs(os.path.join(root, "for_processing"))
        with self._other_device_for(os.path.join(root, "for_processing")):
            with self.assertRaises(StorageRootRefused) as ctx:
                self.open(root)
        self.assertEqual(ctx.exception.reason, "root_spans_filesystems")

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


class _RootCapture(logging.Handler):
    """Records as the process root logger sees them (assertLogs would hide that)."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def __enter__(self):
        root = logging.getLogger()
        self._level = root.level
        root.addHandler(self)
        root.setLevel(logging.INFO)
        return self

    def __exit__(self, *exc):
        root = logging.getLogger()
        root.removeHandler(self)
        root.setLevel(self._level)

    def messages(self):
        return [record.getMessage() for record in self.records]


class TestDeferredStartupLogs(RootTestCase):
    """The process entry opens the root before logging exists (§11.1 diagnostics)."""

    def _uncertain(self):
        return patch.object(root_binding, "detect_filesystem_type", return_value="fuse.unknownfs")

    def test_open_warnings_wait_for_log_binding(self):
        root = self.path("lib")
        with _RootCapture() as seen, self._uncertain():
            bound = self.open(root, defer_logs=True)
            self.assertEqual(seen.messages(), [])
            bound.log_binding()
        messages = seen.messages()
        self.assertEqual(len(messages), 2, messages)
        self.assertIn("storage_root_uncertain_filesystem", messages[0])
        self.assertIn("storage_root_bound source=", messages[1])
        self.assertEqual(seen.records[0].levelno, logging.WARNING)
        self.assertEqual(seen.records[0].name, "prks.storage")
        self.assertTrue(root_binding.LOGGER.propagate)
        self.assertNotIn(seen, root_binding.LOGGER.handlers)

    def test_a_refusal_emits_the_deferred_warnings_at_once(self):
        root = self.path("foreign")
        os.mkdir(root)
        open(os.path.join(root, "holiday.jpg"), "w").close()
        with _RootCapture() as seen, self._uncertain():
            with self.assertRaises(StorageRootRefused):
                self.open(root, defer_logs=True)
        self.assertTrue(
            any("storage_root_uncertain_filesystem" in m for m in seen.messages()),
            seen.messages(),
        )
        self.assertTrue(root_binding.LOGGER.propagate)

    def test_without_deferral_warnings_go_out_while_opening(self):
        root = self.path("lib")
        with _RootCapture() as seen, self._uncertain():
            self.open(root)
        self.assertTrue(any("storage_root_uncertain_filesystem" in m for m in seen.messages()))


class TestWarnings(RootTestCase):
    def test_network_filesystem_detection(self):
        mounts = self.path("mounts")
        with open(mounts, "w", encoding="utf-8") as handle:
            handle.write("/dev/sda1 / ext4 rw 0 0\n")
            handle.write("server:/export /mnt/nas\\040share nfs4 rw 0 0\n")
        self.assertEqual(root_binding.detect_filesystem_type("/mnt/nas share/lib", mounts_file=mounts), "nfs4")
        self.assertEqual(root_binding.detect_filesystem_type("/home/u/lib", mounts_file=mounts), "ext4")
        self.assertIsNone(root_binding.detect_filesystem_type("/x", mounts_file=self.path("absent")))

    def test_certain_network_filesystem_is_refused_before_any_write(self):
        for fs_type in ("nfs", "nfs4", "cifs", "smb3", "fuse.sshfs"):
            with self.subTest(fs_type=fs_type):
                root = self.path(f"lib-{fs_type}")
                with patch.object(root_binding, "detect_filesystem_type", return_value=fs_type):
                    with self.assertRaises(StorageRootRefused) as ctx:
                        self.open(root)
                self.assertEqual(ctx.exception.reason, "root_network_filesystem")
                self.assertFalse(os.path.exists(root))
        existing = self.path("existing")
        os.makedirs(os.path.join(existing, "pdfs"))
        with patch.object(root_binding, "detect_filesystem_type", return_value="cifs"):
            with self.assertRaises(StorageRootRefused):
                self.open(existing)
        self.assertEqual(os.listdir(existing), ["pdfs"])

    def test_uncertain_filesystem_and_low_space_only_warn_and_stay_path_free(self):
        root = self.path("lib")
        usage = shutil.disk_usage(self.tmp)._replace(free=10)
        with (
            patch.object(root_binding, "detect_filesystem_type", return_value="fuse.unknownfs"),
            patch.object(root_binding.shutil, "disk_usage", return_value=usage),
            self.assertLogs("prks.storage", level="INFO") as logs,
        ):
            self.open(root).log_binding()
        text = "\n".join(logs.output)
        self.assertIn("storage_root_uncertain_filesystem fs_type=fuse.unknownfs", text)
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
            # The source report needs logging configured, or INFO is dropped.
            main.index("setup_logging(config)"),
            main.index("bound_root.log_binding()"),
            main.index("run_server("),
        ]
        self.assertEqual(order, sorted(order))

    def test_unused_invalid_config_file_path_does_not_block_a_higher_source(self):
        import prks_app

        root = self.path("lib")
        config = StorageConfig.from_env(cli_root=root, environ={})
        self.assertEqual((config.mode, config.root_source), ("production", "cli"))
        # The real environment cannot carry the NUL that makes the path
        # unparseable, so the resolver's refusal is injected directly.
        unparseable = InvalidStorageRoot("root_unparseable", "PRKS_CONFIG_FILE is not a valid path.")
        with patch.object(prks_app, "bootstrap_config_path", side_effect=unparseable):
            bound = prks_app.open_storage(config)
        self._bound.append(bound)
        self.assertTrue(os.path.isfile(os.path.join(root, MARKER)))

    def test_unsafe_testing_cli_root_is_a_clean_startup_error(self):
        env = dict(os.environ, PRKS_TESTING="1")
        env.pop("PRKS_STORAGE", None)
        proc = subprocess.run(
            [sys.executable, os.path.join(_PROJECT_DIR, "prks_app.py"), "--testing",
             "--storage-root", "/data", "--port", "1"],
            cwd=_PROJECT_DIR, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("PRKS cannot select its storage root", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_open_storage_defers_its_warnings(self):
        import prks_app

        with patch.object(prks_app, "open_storage_root") as opened:
            prks_app.open_storage(StorageConfig.for_testing(self.path("lib")))
        self.assertTrue(opened.call_args.kwargs.get("defer_logs"))

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


class TestAdoptionNeedsProof(RootTestCase):
    """§7.1: adoption is decided by type and content, never by name alone."""

    def test_same_named_entries_of_the_wrong_type_are_foreign(self):
        cases = {
            "file-named-pdfs": lambda root: open(os.path.join(root, "pdfs"), "w").close(),
            "dir-named-db": lambda root: os.mkdir(os.path.join(root, "prks_data.db")),
            "db-without-sqlite-header": lambda root: _write_json(os.path.join(root, "prks_data.db"), {}),
            "arbitrary-maintenance-content": lambda root: (
                os.mkdir(os.path.join(root, MAINT)),
                open(os.path.join(root, MAINT, "notes.txt"), "w").close(),
            ),
        }
        for name, build in cases.items():
            with self.subTest(case=name):
                root = self.path(name)
                os.mkdir(root)
                build(root)
                before = sorted(os.listdir(root))
                with self.assertRaises(StorageRootRefused) as ctx:
                    self.open(root)
                self.assertEqual(ctx.exception.reason, "root_foreign")
                self.assertEqual(sorted(os.listdir(root)), before)

    def test_linked_pdfs_directory_is_not_proof(self):
        root = self.path("lib")
        os.mkdir(root)
        elsewhere = self.path("store")
        os.mkdir(elsewhere)
        _symlink_or_skip(self, elsewhere, os.path.join(root, "pdfs"))
        with self.assertRaises(StorageRootRefused) as ctx:
            self.open(root)
        self.assertEqual(ctx.exception.reason, "root_foreign")


class TestConfigFileRootsAreNeverCreated(RootTestCase):
    """A persisted selection whose disk is gone must not become a new empty library."""

    def _cfg(self, root):
        return replace(StorageConfig.for_testing(root), root_source="config_file")

    def _open_cfg(self, cfg):
        bound = root_binding.open_storage_root(cfg, register=False)
        self._bound.append(bound)
        return bound

    def test_absent_config_file_root_is_refused_and_not_created(self):
        root = self.path("nas", "library")
        os.mkdir(self.path("nas"))  # the unmounted mountpoint
        with self.assertRaises(StorageRootRefused) as ctx:
            self._open_cfg(self._cfg(root))
        self.assertEqual(ctx.exception.reason, "root_missing")
        self.assertFalse(os.path.exists(root))

    def test_empty_config_file_root_is_refused_without_writing(self):
        root = self.path("library")
        os.mkdir(root)
        open(os.path.join(root, ".DS_Store"), "w").close()
        with self.assertRaises(StorageRootRefused) as ctx:
            self._open_cfg(self._cfg(root))
        self.assertEqual(ctx.exception.reason, "root_missing")
        self.assertEqual(os.listdir(root), [".DS_Store"])

    def test_marked_or_adoptable_config_file_roots_still_open(self):
        marked = self.path("marked")
        first = self.open(marked)
        first_id = first.storage_root_id
        first.release()
        self.assertEqual(self._open_cfg(self._cfg(marked)).storage_root_id, first_id)
        legacy = self.path("legacy")
        os.makedirs(os.path.join(legacy, "pdfs"))
        self.assertTrue(self._open_cfg(self._cfg(legacy)).adopted)

    def test_other_sources_keep_first_run_creation(self):
        for source in ("cli", "env", "development_default", "platform_default", None):
            with self.subTest(source=source):
                root = self.path(f"new-{source}")
                cfg = replace(StorageConfig.for_testing(root), root_source=source)
                self.assertTrue(self._open_cfg(cfg).created)


class TestRootSnapshot(RootTestCase):
    """§7.3: a root link is resolved once; runtime paths stay on the leased target."""

    def test_retargeting_the_root_link_does_not_move_runtime_io(self):
        target_a = self.path("disk-a")
        target_b = self.path("disk-b")
        os.mkdir(target_a)
        os.mkdir(target_b)
        link = self.path("library")
        _symlink_or_skip(self, target_a, link)
        cfg = StorageConfig.for_testing(link)
        bound = self.open(link)
        anchored = bound.anchor(cfg)
        os.remove(link)
        os.symlink(target_b, link)

        from backend.db_manager import PRKSDatabase

        PRKSDatabase(storage=anchored, schema_path=os.path.join(_PROJECT_DIR, "backend", "db_schema.sql"))
        os.makedirs(anchored.pdfs_dir, exist_ok=True)
        with open(os.path.join(anchored.pdfs_dir, "x.pdf"), "wb") as handle:
            handle.write(b"%PDF")
        self.assertTrue(os.path.isfile(os.path.join(target_a, "prks_data.db")))
        self.assertTrue(os.path.isfile(os.path.join(target_a, "pdfs", "x.pdf")))
        self.assertEqual(os.listdir(target_b), [])
        self.assertEqual(anchored.configured_root, cfg.configured_root)
        root_binding.assert_config_matches_bound_root(anchored.root)

    def test_retargeting_between_validation_and_lease_stays_on_the_validated_target(self):
        target_a = self.path("disk-a")
        target_b = self.path("disk-b")
        os.mkdir(target_a)
        os.mkdir(target_b)
        link = self.path("library")
        _symlink_or_skip(self, target_a, link)
        real_acquire = root_binding.acquire_root_lease

        def retarget_then_acquire(root_real):
            os.remove(link)
            os.symlink(target_b, link)
            return real_acquire(root_real)

        with patch.object(root_binding, "acquire_root_lease", retarget_then_acquire):
            bound = self.open(link)
        self.assertEqual(bound.root_real, target_a)
        self.assertTrue(os.path.isfile(os.path.join(target_a, MARKER)))
        self.assertTrue(os.path.isfile(os.path.join(target_a, MAINT, "root.lock")))
        self.assertEqual(os.listdir(target_b), [])

    def test_anchoring_rewrites_only_root_relative_components(self):
        cfg = replace(
            StorageConfig.for_testing(self.path("lib")),
            processing_dir="/srv/inbox",
            log_file="/var/log/prks.log",
        )
        anchored = cfg.anchored_to(self.path("real"))
        self.assertEqual(anchored.root, self.path("real"))
        for name in ("db_path", "pdfs_dir", "thumbs_dir", "people_dir", "index_db_path", "research_index_db_path"):
            self.assertTrue(getattr(anchored, name).startswith(self.path("real") + os.sep), name)
        self.assertEqual(anchored.processing_dir, "/srv/inbox")
        self.assertEqual(anchored.log_file, "/var/log/prks.log")

    def test_process_entry_anchors_before_recovery(self):
        with open(os.path.join(_PROJECT_DIR, "prks_app.py"), encoding="utf-8") as handle:
            main = handle.read().split('if __name__ == "__main__":', 1)[1]
        self.assertLess(main.index("bound_root.anchor(config)"), main.index("recover_incomplete_restore(config)"))


class TestWholeRootInvariants(RootTestCase):
    """V13/V7 hold for everything under the root, not only known components."""

    def _marked(self):
        root = self.path("lib")
        self.open(root).release()
        self._bound.clear()
        return root

    def _refused(self, root, reason):
        with self.assertRaises(StorageRootRefused) as ctx:
            self.open(root)
        self.assertEqual(ctx.exception.reason, reason)

    def test_unrelated_top_level_link_is_refused(self):
        root = self._marked()
        _symlink_or_skip(self, self.tmp, os.path.join(root, "shortcut"))
        self._refused(root, "root_contains_link")

    def test_nested_links_anywhere_are_refused(self):
        for parts in (("pdfs", "sub", "x.pdf"), ("people", "sub", "p.webp"),
                      ("thumbs", "sub", "t.webp"), (MAINT, "backup", "old"), ("notes", "deep", "l")):
            with self.subTest(parts=parts):
                root = self._marked()
                directory = os.path.join(root, *parts[:-1])
                os.makedirs(directory, exist_ok=True)
                _symlink_or_skip(self, self.tmp, os.path.join(directory, parts[-1]))
                self._refused(root, "root_contains_link")
                shutil.rmtree(root)

    _VOLUME_METADATA = (".Trashes", ".Spotlight-V100", ".fseventsd", ".Trash-1000",
                        "System Volume Information", "$Recycle.Bin")

    def _unlistable(self, *paths):
        """os.listdir as the owner sees an OS-protected directory (tests run as root)."""
        real_listdir = os.listdir
        blocked = {os.path.realpath(p) for p in paths}

        def fake(path="."):
            if os.path.realpath(path) in blocked:
                raise PermissionError(13, "Permission denied", path)
            return real_listdir(path)

        return patch.object(root_binding.os, "listdir", fake)

    def test_volume_metadata_on_a_fresh_volume_root_makes_a_new_root(self):
        root = self.path("volume")
        os.mkdir(root)
        for name in self._VOLUME_METADATA:
            os.mkdir(os.path.join(root, name))
        with self._unlistable(*(os.path.join(root, n) for n in self._VOLUME_METADATA)):
            bound = self.open(root)
        self.assertTrue(bound.created)
        self.assertTrue(os.path.isfile(os.path.join(root, MARKER)))

    def test_unlistable_volume_metadata_does_not_refuse_a_marked_root(self):
        root = self._marked()
        trashes = os.path.join(root, ".Trashes")
        os.mkdir(trashes)
        with self._unlistable(trashes):
            self.open(root)

    def test_other_unlistable_entries_are_still_refused(self):
        root = self._marked()
        private = os.path.join(root, "private")
        os.mkdir(private)
        with self._unlistable(private):
            self._refused(root, "root_unreadable")

    def test_volume_metadata_names_are_exempt_only_at_the_top(self):
        root = self._marked()
        nested = os.path.join(root, "pdfs", ".Trashes")
        os.makedirs(nested)
        _symlink_or_skip(self, self.tmp, os.path.join(nested, "l"))
        self._refused(root, "root_contains_link")

    def test_os_metadata_at_the_top_is_not_walked(self):
        root = self._marked()
        lost = os.path.join(root, "lost+found")
        os.mkdir(lost)
        _symlink_or_skip(self, self.tmp, os.path.join(lost, "orphan"))
        self.open(root)

    def test_entry_that_cannot_be_inspected_is_refused(self):
        root = self._marked()
        os.makedirs(os.path.join(root, "pdfs"), exist_ok=True)
        target = os.path.join(root, "pdfs", "a.pdf")
        with open(target, "wb") as handle:
            handle.write(b"%PDF")
        real_lstat = root_binding._lstat

        def fake(path):
            if path == target:
                raise PermissionError(13, "Permission denied", path)
            return real_lstat(path)

        with patch.object(root_binding, "_lstat", fake):
            with self.assertRaises(StorageRootRefused) as ctx:
                self.open(root)
        self.assertEqual(ctx.exception.reason, "root_unreadable")
        self.assertIn(target, str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, PermissionError)

    def _file_on_other_device(self, target):
        real_lstat = root_binding._lstat

        def fake(path):
            st = real_lstat(path)
            if st is not None and path == target:
                values = list(st)
                values[2] = st.st_dev + 1
                return os.stat_result(values)
            return st

        return patch.object(root_binding, "_lstat", fake)

    def test_bind_mounted_files_are_refused(self):
        root = self._marked()
        with open(os.path.join(root, "prks_data.db"), "wb") as handle:
            handle.write(root_binding.SQLITE_HEADER)
        os.makedirs(os.path.join(root, "pdfs"), exist_ok=True)
        with open(os.path.join(root, "pdfs", "a.pdf"), "wb") as handle:
            handle.write(b"%PDF")
        # The file-device rule is relaxed on overlayfs (tested below); pin a
        # plain local type so this holds on overlay-backed runners too.
        with patch.object(root_binding, "detect_filesystem_type", return_value="ext4"):
            for target in (os.path.join(root, "prks_data.db"), os.path.join(root, "pdfs", "a.pdf")):
                with self.subTest(target=os.path.basename(target)):
                    with self._file_on_other_device(target):
                        self._refused(root, "root_spans_filesystems")

    def test_overlay_root_relaxes_only_the_file_device_rule(self):
        root = self._marked()
        os.makedirs(os.path.join(root, "pdfs", "sub"), exist_ok=True)
        with open(os.path.join(root, "pdfs", "a.pdf"), "wb") as handle:
            handle.write(b"%PDF")
        with patch.object(root_binding, "detect_filesystem_type", return_value="overlay"):
            with self._file_on_other_device(os.path.join(root, "pdfs", "a.pdf")):
                with self.assertLogs("prks.storage", level="WARNING") as logs:
                    self.open(root).release()
            self.assertIn("storage_root_overlay_file_devices_unchecked", "\n".join(logs.output))
            with self._file_on_other_device(os.path.join(root, "pdfs", "sub")):
                with self.assertLogs("prks.storage", level="WARNING"):
                    self._refused(root, "root_spans_filesystems")


class TestFilesystemDetectionAcrossPlatforms(RootTestCase):
    """V9 classifies roots on macOS and Windows too, and never silently assumes local."""

    MACOS_MOUNT = (
        "/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)\n"
        "/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled, nobrowse)\n"
        "//user@nas/Library on /Volumes/Library (smbfs, nodev, nosuid, mounted by user)\n"
        "nas:/export on /Volumes/NFS Share (nfs, asynchronous)\n"
    )

    def test_bsd_mount_output(self):
        mounts = dict(root_binding.parse_bsd_mount_output(self.MACOS_MOUNT))
        self.assertEqual(mounts["/Volumes/Library"], "smbfs")
        self.assertEqual(mounts["/Volumes/NFS Share"], "nfs")
        detect = root_binding._darwin_filesystem_type
        self.assertEqual(detect("/Volumes/Library/PRKS", mount_output=self.MACOS_MOUNT), "smbfs")
        self.assertEqual(detect("/Volumes/NFS Share/lib", mount_output=self.MACOS_MOUNT), "nfs")
        self.assertEqual(detect("/System/Volumes/Data/Users/u/lib", mount_output=self.MACOS_MOUNT), "apfs")
        for fs_type in ("smbfs", "nfs", "afpfs", "webdav"):
            self.assertIn(fs_type, root_binding.NETWORK_FILESYSTEM_TYPES)

    def test_windows_unc_and_remote_drives(self):
        remote = {"Z:\\": 4}
        drive_type = lambda root: remote.get(root, 3)  # noqa: E731 - DRIVE_FIXED otherwise
        detect = root_binding._windows_filesystem_type
        self.assertEqual(detect("\\\\nas\\share\\PRKS", drive_type=drive_type), root_binding.WINDOWS_REMOTE)
        self.assertEqual(detect("\\\\?\\UNC\\nas\\share", drive_type=drive_type), root_binding.WINDOWS_REMOTE)
        self.assertEqual(detect("Z:\\PRKS", drive_type=drive_type), root_binding.WINDOWS_REMOTE)
        self.assertEqual(detect("C:\\Users\\u\\PRKS", drive_type=drive_type), root_binding.WINDOWS_LOCAL)
        self.assertEqual(detect("\\\\?\\C:\\PRKS", drive_type=drive_type), root_binding.WINDOWS_LOCAL)
        self.assertIn(root_binding.WINDOWS_REMOTE, root_binding.NETWORK_FILESYSTEM_TYPES)

    def test_remote_classification_refuses_the_root(self):
        root = self.path("lib")
        with patch.object(root_binding, "detect_filesystem_type", return_value=root_binding.WINDOWS_REMOTE):
            with self.assertRaises(StorageRootRefused) as ctx:
                self.open(root)
        self.assertEqual(ctx.exception.reason, "root_network_filesystem")
        self.assertFalse(os.path.exists(root))

    def test_unclassifiable_filesystem_is_warned_about_not_assumed_local(self):
        root = self.path("lib")
        with patch.object(root_binding, "detect_filesystem_type", return_value=None):
            with self.assertLogs("prks.storage", level="WARNING") as logs:
                self.open(root)
        self.assertIn("storage_root_filesystem_unclassified", "\n".join(logs.output))


class TestMountBoundaries(RootTestCase):
    """V7 by mount table: same-device bind mounts are mount points too."""

    def _mountinfo(self, *points):
        lines = ["22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw"]
        for i, point in enumerate(points):
            escaped = point.replace(" ", "\\040")
            lines.append(f"{30 + i} 22 8:1 /elsewhere {escaped} rw,relatime shared:1 - ext4 /dev/sda1 rw")
        path = self.path(f"mountinfo-{len(points)}")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return path

    def test_mountinfo_parsing_unescapes_mount_points(self):
        text = "36 35 98:0 /mnt1 /mnt/with\\040space rw - ext3 /dev/root rw\n"
        self.assertEqual(root_binding.parse_mountinfo(text), ["/mnt/with space"])

    def _open_with(self, root, mountinfo):
        real = root_binding._mount_points

        def fake(*, mountinfo_file=root_binding.MOUNTINFO_FILE):
            return real(mountinfo_file=mountinfo)

        with patch.object(root_binding, "_mount_points", fake), patch.object(root_binding.sys, "platform", "linux"):
            return self.open(root)

    def test_same_device_bind_mounts_inside_the_root_are_refused(self):
        for rel in ("pdfs", "prks_data.db", os.path.join("people", "deep dir")):
            with self.subTest(mount=rel):
                root = self.path("lib")
                os.makedirs(os.path.join(root, "pdfs"), exist_ok=True)
                mountinfo = self._mountinfo(os.path.join(os.path.realpath(root), rel))
                with self.assertRaises(StorageRootRefused) as ctx:
                    self._open_with(root, mountinfo)
                self.assertEqual(ctx.exception.reason, "root_contains_mount_point")
                self.assertFalse(os.path.exists(os.path.join(root, MARKER)))
                shutil.rmtree(root)

    def test_root_itself_and_unrelated_mounts_are_fine(self):
        root = self.path("lib")
        os.makedirs(root)
        mountinfo = self._mountinfo(os.path.realpath(root), self.path("lib-sibling"), "/srv/other")
        self.assertTrue(self._open_with(root, mountinfo).created)

    def test_real_mount_table_accepts_a_plain_temporary_root(self):
        if not os.path.exists(root_binding.MOUNTINFO_FILE):  # pragma: no cover - non-Linux
            self.skipTest("no mountinfo")
        self.assertIsNotNone(root_binding._mount_points())
        self.open(self.path("lib"))


class TestMarkerWritesNeedTheLease(RootTestCase):
    """§12: only the holder of a root's own root.lock may write its marker."""

    def test_marker_write_requires_this_roots_held_lease(self):
        root = self.path("lib")
        other = self.path("other")
        bound = self.open(root)
        self.open(other)
        before = self.marker_doc(root)
        document = dict(before, future_field=1)
        foreign = self._bound[-1].lease

        with self.assertRaises(StorageRootRefused) as ctx:
            root_marker.write_marker(root, document, lease=foreign)
        self.assertEqual(ctx.exception.reason, "marker_write_foreign_lease")
        with self.assertRaises(StorageRootRefused) as ctx:
            root_marker.write_marker(root, document, lease=None)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.reason, "marker_write_without_lease")
        with self.assertRaises(TypeError):
            root_marker.write_marker(root, document)  # type: ignore[call-arg]
        self.assertEqual(self.marker_doc(root), before)

        root_marker.write_marker(root, document, lease=bound.lease)
        self.assertEqual(self.marker_doc(root)["future_field"], 1)

        bound.release()
        with self.assertRaises(StorageRootRefused) as ctx:
            root_marker.write_marker(root, before, lease=bound.lease)
        self.assertEqual(ctx.exception.reason, "marker_write_without_lease")
        self.assertEqual(self.marker_doc(root)["future_field"], 1)


if __name__ == "__main__":
    unittest.main()
