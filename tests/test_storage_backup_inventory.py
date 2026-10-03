"""Backup inventory classification of Phase A operational state (§4.1, §7.1, §9.3).

The root marker, the root lease file and the bootstrap configuration are
durable **operational** state: they identify, guard or locate a root and are
never backup payload. A restore keeps the *target* root's identity.
"""

import os
import sys
import unittest
import zipfile

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import tests.test_backup_restore as backup_tests
from backend import backup_restore
from backend.backup_restore import apply_restore, backup_storage_inventory, create_backup
from backend.server import bind_storage
from backend.storage import root_binding, root_marker
from backend.storage.config import StorageConfig


class TestInventoryClassification(unittest.TestCase):
    def test_marker_lease_and_bootstrap_config_are_operational(self):
        inv = backup_storage_inventory()
        self.assertIn("prks-root.json", inv.operational_root_entries)
        self.assertIn(".prks-maintenance/root.lock", inv.operational_root_entries)
        self.assertIn("bootstrap_config", inv.external_operational)
        self.assertIn("bootstrap_config_lock", inv.external_operational)
        payload_classes = inv.canonical + inv.derived + inv.conditional + inv.container + inv.operational
        for name in inv.operational_root_entries + inv.external_operational:
            self.assertNotIn(name, payload_classes)

    def test_operational_entries_can_never_be_payload_paths(self):
        inv = backup_storage_inventory()
        for entry in inv.operational_root_entries:
            for arcname in (entry, "files/" + entry, "data/" + entry):
                with self.subTest(arcname=arcname):
                    self.assertFalse(backup_restore._payload_allowed(arcname, processing_allowed=True))

    def test_one_maintenance_directory_name(self):
        self.assertEqual(backup_restore.MAINTENANCE_DIRNAME, root_binding.MAINTENANCE_DIRNAME)
        self.assertEqual(root_marker.MARKER_FILENAME, "prks-root.json")

    def test_inventory_still_covers_every_storage_config_path_field(self):
        self.assertEqual(
            backup_restore.classified_storage_field_names(),
            backup_restore.storage_config_path_field_names(),
        )


class TestMarkerAcrossBackupAndRestore(backup_tests.BackupRestoreTestCase):
    def _mark(self, cfg):
        bound = root_binding.open_storage_root(StorageConfig.for_testing(cfg.root), register=False)
        self.addCleanup(bound.release)
        return bound

    def test_backup_never_archives_the_marker_or_lease(self):
        source = self._bind_library(processing_name="queued.pdf")
        marked = self._mark(source["cfg"])
        result = create_backup(source["cfg"])
        self.assertTrue(result.verified)
        with zipfile.ZipFile(result.archive_path) as archive:
            names = archive.namelist()
        self.assertTrue(any(n.startswith("files/pdfs/") for n in names))
        for name in names:
            self.assertNotIn("prks-root.json", name)
            self.assertNotIn("root.lock", name)
            self.assertNotIn(".prks-maintenance", name)
        self.assertNotIn(marked.storage_root_id, str(result.manifest))

    def test_restore_keeps_the_target_roots_identity_and_lease(self):
        source = self._bind_library()
        source_id = self._mark(source["cfg"]).storage_root_id
        backup = create_backup(source["cfg"])

        dest_root = self._tmpdir()
        dest_bound = self._mark(StorageConfig.for_testing(dest_root))
        dest = bind_storage(StorageConfig.for_testing(dest_root))
        staged = self._stage_copy(dest, backup.archive_path)
        self.assertTrue(staged.verified)
        out = apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])

        after = root_marker.read_marker(dest_root)
        self.assertEqual(after.storage_root_id, dest_bound.storage_root_id)
        self.assertNotEqual(after.storage_root_id, source_id)
        self.assertTrue(after.bindable)
        self.assertTrue(os.path.isfile(os.path.join(dest_root, ".prks-maintenance", "root.lock")))
        # The lease survived the restore's component renames.
        self.assertTrue(dest_bound.lease.held)
        from backend.storage.errors import StorageRootInUse

        with self.assertRaises(StorageRootInUse):
            root_binding.open_storage_root(StorageConfig.for_testing(dest_root), register=False)
        # And the restored library is really there.
        with open(os.path.join(dest.pdfs_dir, source["pdf_name"]), "rb") as handle:
            self.assertEqual(handle.read(), source["pdf_bytes"])

    def test_startup_sweeps_leave_the_lease_file_alone(self):
        source = self._bind_library()
        self._mark(source["cfg"])
        lock = os.path.join(source["cfg"].root, ".prks-maintenance", "root.lock")
        backup_restore.recover_incomplete_restore(source["cfg"])
        backup_restore.cleanup_stale_staging(source["cfg"], now=10**12)
        backup_restore.cleanup_orphan_rollback(source["cfg"], now=10**12)
        backup_restore.cleanup_expired_backup_jobs(source["cfg"], now=10**12)
        self.assertTrue(os.path.isfile(lock))
        self.assertTrue(os.path.isfile(os.path.join(source["cfg"].root, "prks-root.json")))


if __name__ == "__main__":
    unittest.main()
