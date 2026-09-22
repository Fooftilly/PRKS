import errno
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend import fs_durability
from backend.backup_restore import (
    ARCHIVE_DB_PATH,
    DISK_MARGIN_BYTES,
    FORMAT_ID,
    FORMAT_VERSION,
    IO_CHUNK_SIZE,
    MANIFEST_NAME,
    BackupError,
    RestoreCrash,
    RestoreError,
    apply_restore,
    backup_additional_bytes,
    backup_storage_inventory,
    classified_storage_field_names,
    create_backup,
    hash_and_copy,
    iter_file_chunks,
    recover_incomplete_restore,
    require_restore_upload_space,
    stage_restore,
    storage_config_path_field_names,
    verify_backup,
)
from backend.db_manager import PRKS_SCHEMA_VERSION, PRKSDatabase
from backend.db_migrations import Migration
from backend.server import bind_storage
from backend.storage.config import StorageConfig
from backend.text_index import get_text_index, reset_text_index
import backend.backup_restore as backup_module
import backend.server as server_module

# Restore journals carry a `secrets.token_urlsafe(16)` transaction id, and
# recovery now validates that shape before using it as a rollback path segment.
# Fixtures use real-shaped ids so they exercise the same code path production
# does.
_JOURNAL_TXN_A = "fixture-txn-aaaaaaaaaa"
_JOURNAL_TXN_B = "fixture-txn-bbbbbbbbbb"
_JOURNAL_TXN_C = "fixture-txn-canonical0"


def _fspath_or_none(path):
    try:
        return os.fspath(path)
    except TypeError:
        return None


def _unlink_if_link(path):
    if os.path.islink(path):
        os.unlink(path)


def _capture_bind():
    try:
        previous_index = get_text_index()
    except RuntimeError:
        previous_index = None
    return (
        server_module._bound_storage,
        server_module.pdfs_dir,
        server_module.thumbs_dir,
        server_module.processing_dir,
        server_module.db,
        server_module.text_index,
        previous_index,
    )


def _restore_bind(snapshot):
    (
        server_module._bound_storage,
        server_module.pdfs_dir,
        server_module.thumbs_dir,
        server_module.processing_dir,
        server_module.db,
        server_module.text_index,
        previous_index,
    ) = snapshot
    if previous_index is None:
        reset_text_index()
    else:
        from backend.text_index import replace_text_index

        replace_text_index(previous_index)


def _pdf_bytes(text: str) -> bytes:
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text or "")
    out = doc.tobytes()
    doc.close()
    return out


def _copy_backup(src: str, dest_dir: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))
    shutil.copy2(src, dest)
    return dest


def _zip_namelist(path: str) -> list[str]:
    with zipfile.ZipFile(path, "r") as zf:
        return zf.namelist()


def _read_manifest(path: str) -> dict:
    with zipfile.ZipFile(path, "r") as zf:
        return json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))


def _rewrite_backup(src: str, dest: str, mutator) -> None:
    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(dest, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            name, payload, zipinfo = mutator(info, data)
            if name is None:
                continue
            zipinfo.filename = name
            zout.writestr(zipinfo, payload)


class BackupRestoreTestCase(unittest.TestCase):
    def setUp(self):
        self._prev = _capture_bind()
        self._tmps = []

    def tearDown(self):
        _restore_bind(self._prev)
        for path in self._tmps:
            shutil.rmtree(path, ignore_errors=True)

    def _tmpdir(self, prefix="prks-backup-"):
        path = tempfile.mkdtemp(prefix=prefix)
        self._tmps.append(path)
        return path

    def _cfg(self, root=None):
        if root is None:
            root = self._tmpdir()
        return StorageConfig.for_testing(root)

    def _bind_library(
        self,
        *,
        title="Alpha Work",
        pdf_text="searchable unique token alpha",
        pdf_name="alpha.pdf",
        person_bytes=b"PERSON-BYTES-A",
        author="Backup Author",
        extra_pdf_name=None,
        extra_pdf_bytes=None,
        processing_name=None,
        processing_bytes=b"QUEUE",
    ):
        root = self._tmpdir()
        cfg = self._cfg(root)
        bound = bind_storage(cfg)
        os.makedirs(bound.pdfs_dir, exist_ok=True)
        os.makedirs(bound.people_dir, exist_ok=True)
        os.makedirs(bound.thumbs_dir, exist_ok=True)
        os.makedirs(bound.processing_dir, exist_ok=True)
        pdf_bytes = _pdf_bytes(pdf_text)
        with open(os.path.join(bound.pdfs_dir, pdf_name), "wb") as handle:
            handle.write(pdf_bytes)
        if extra_pdf_name:
            with open(os.path.join(bound.pdfs_dir, extra_pdf_name), "wb") as handle:
                handle.write(extra_pdf_bytes or b"%PDF-1.4 orphan\n%%EOF\n")
        person_path = os.path.join(bound.people_dir, "portrait.webp")
        with open(person_path, "wb") as handle:
            handle.write(person_bytes)
        with open(os.path.join(bound.thumbs_dir, "stale-thumb.webp"), "wb") as handle:
            handle.write(b"OLD-THUMB")
        if processing_name:
            with open(os.path.join(bound.processing_dir, processing_name), "wb") as handle:
                handle.write(processing_bytes)
        db = server_module.db
        work_id = db.add_work(title, file_path=f"/api/pdfs/{pdf_name}", source_kind="pdf")
        person_id = db.add_person("Ada", "Lovelace")
        db.add_role(person_id, work_id, "Author")
        folder_id = db.add_folder("Research")
        db.add_work_to_folder(folder_id, work_id)
        tag = db.add_tag("rhetoric")
        db.add_tag_to_work(work_id, tag["id"])
        db.save_work_annotations(
            work_id,
            json.dumps(
                [
                    {
                        "id": "a1",
                        "type": "highlight",
                        "content": "note",
                        "pageIndex": 2,
                        "color": "#FFCD45",
                        "rect": {
                            "origin": {"x": 1, "y": 2},
                            "size": {"width": 3, "height": 4},
                        },
                    }
                ]
            ),
        )
        db.patch_app_settings({"annotation_author": author})
        server_module.text_index.sync_work(work_id, f"/api/pdfs/{pdf_name}")
        return {
            "cfg": bound,
            "work_id": work_id,
            "person_id": person_id,
            "folder_id": folder_id,
            "pdf_name": pdf_name,
            "pdf_bytes": pdf_bytes,
            "person_bytes": person_bytes,
            "title": title,
            "author": author,
            "pdf_text": pdf_text,
        }

    def _stage_copy(self, cfg, archive_path):
        copied = _copy_backup(archive_path, os.path.join(self._tmpdir(), "upload"))
        return stage_restore(cfg, copied)


class TestBackupPathSafety(BackupRestoreTestCase):
    """The destructive boundary: validated state -> proven subroot -> removal."""

    def _symlink_or_skip(self, target, link, *, directory=False):
        try:
            os.symlink(target, link, target_is_directory=directory)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")

    def _staging_root(self, cfg):
        root = backup_module._maintenance_subroot(
            cfg, *backup_module._STAGING_SUBROOT
        )
        os.makedirs(root, exist_ok=True)
        return root

    def _maintenance_dir(self, cfg):
        root = backup_module._maintenance_subroot(cfg)
        os.makedirs(root, exist_ok=True)
        return root

    def _outside_victim(self):
        outside = self._tmpdir("prks-outside-")
        victim = os.path.join(outside, "victim.txt")
        with open(victim, "w", encoding="utf-8") as handle:
            handle.write("keep")
        return outside, victim

    def test_scoped_remove_accepts_a_real_child_tree(self):
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)
        child = os.path.join(staging_root, "fixture-stage-aaaaaaaa")
        os.makedirs(os.path.join(child, "tree", "files"))
        with open(os.path.join(child, "tree", "files", "a.bin"), "wb") as handle:
            handle.write(b"x")

        backup_module._remove_maintenance_child(
            cfg, backup_module._STAGING_SUBROOT, child
        )

        self.assertFalse(os.path.lexists(child))
        self.assertTrue(os.path.isdir(staging_root))

    def test_scoped_remove_rejects_intermediate_symlink_escape(self):
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)
        outside, victim = self._outside_victim()
        link = os.path.join(staging_root, "escape")
        self._symlink_or_skip(outside, link, directory=True)

        with self.assertRaises(ValueError):
            backup_module._remove_maintenance_child(
                cfg, backup_module._STAGING_SUBROOT, os.path.join(link, "victim.txt")
            )

        self.assertTrue(os.path.isfile(victim))

    def test_scoped_remove_unlinks_leaf_symlink_without_following_target(self):
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)
        _outside, victim = self._outside_victim()
        link = os.path.join(staging_root, "leaf")
        self._symlink_or_skip(victim, link)

        backup_module._remove_maintenance_child(
            cfg, backup_module._STAGING_SUBROOT, link
        )

        self.assertFalse(os.path.lexists(link))
        self.assertTrue(os.path.isfile(victim))

    def test_scoped_remove_rejects_a_symlinked_subroot(self):
        """The allowed root must be proven, not merely resolved.

        A containment check resolves both operands, so a symlink planted at
        restore-staging would make every "scoped" removal pass while operating
        on an arbitrary directory.
        """
        cfg = self._cfg()
        self._maintenance_dir(cfg)
        outside, victim = self._outside_victim()
        staging_root = backup_module._maintenance_subroot(
            cfg, *backup_module._STAGING_SUBROOT
        )
        self._symlink_or_skip(outside, staging_root, directory=True)

        with self.assertRaises(ValueError):
            backup_module._remove_maintenance_child(
                cfg,
                backup_module._STAGING_SUBROOT,
                os.path.join(outside, "victim.txt"),
            )
        with self.assertRaises(ValueError):
            backup_module._remove_maintenance_child(
                cfg,
                backup_module._STAGING_SUBROOT,
                os.path.join(staging_root, "victim.txt"),
            )

        self.assertTrue(os.path.isfile(victim))

    def test_scoped_remove_cannot_reach_another_subroot(self):
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)
        maintenance = self._maintenance_dir(cfg)
        rollback_root = os.path.join(maintenance, "rollback")
        os.makedirs(rollback_root, exist_ok=True)
        staged = os.path.join(staging_root, "fixture-stage-aaaaaaaa")
        os.makedirs(staged)
        journal = os.path.join(maintenance, "restore-journal.json")
        with open(journal, "w", encoding="utf-8") as handle:
            handle.write("{}")

        for subroot, target in (
            (backup_module._ROLLBACK_SUBROOT, staged),
            (backup_module._JOURNAL_SUBROOT, staged),
            (backup_module._STAGING_SUBROOT, journal),
            (backup_module._STAGING_SUBROOT, rollback_root),
        ):
            with self.subTest(subroot=subroot, target=target):
                with self.assertRaises(ValueError):
                    backup_module._remove_maintenance_child(cfg, subroot, target)

        self.assertTrue(os.path.isdir(staged))
        self.assertTrue(os.path.isfile(journal))
        self.assertTrue(os.path.isdir(rollback_root))

    def test_scoped_remove_rejects_ambiguous_and_nested_paths(self):
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)
        nested = os.path.join(staging_root, "fixture-stage-aaaaaaaa", "tree")
        os.makedirs(nested)

        for unsafe in (
            nested,
            os.path.join(staging_root, ".."),
            os.path.join(staging_root, "."),
            os.path.join(staging_root, "fixture-stage-aaaaaaaa", "..", "..", "x"),
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    backup_module._remove_maintenance_child(
                        cfg, backup_module._STAGING_SUBROOT, unsafe
                    )

        self.assertTrue(os.path.isdir(nested))

    def test_safe_remove_unlinks_symlinks_inside_a_tree(self):
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)
        _outside, victim = self._outside_victim()
        child = os.path.join(staging_root, "fixture-stage-aaaaaaaa")
        os.makedirs(os.path.join(child, "tree"))
        self._symlink_or_skip(victim, os.path.join(child, "tree", "link"))

        backup_module._remove_maintenance_child(
            cfg, backup_module._STAGING_SUBROOT, child
        )

        self.assertFalse(os.path.lexists(child))
        self.assertTrue(os.path.isfile(victim))

    def test_staging_token_cannot_follow_a_symlink_outside_maintenance(self):
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)
        outside = self._tmpdir("prks-stage-outside-")
        token = "fixture-stage-aaaaaaaa"
        self._symlink_or_skip(
            outside, os.path.join(staging_root, token), directory=True
        )

        with self.assertRaises(RestoreError) as ctx:
            backup_module._staging_dir(cfg, token)

        self.assertEqual(ctx.exception.reason, "unknown_token")

    def test_staging_dir_rejects_a_symlinked_staging_root(self):
        cfg = self._cfg()
        self._maintenance_dir(cfg)
        outside = self._tmpdir("prks-stage-outside-")
        self._symlink_or_skip(
            outside,
            backup_module._maintenance_subroot(cfg, *backup_module._STAGING_SUBROOT),
            directory=True,
        )

        with self.assertRaises(RestoreError) as ctx:
            backup_module._staging_dir(cfg, "fixture-stage-aaaaaaaa")

        self.assertEqual(ctx.exception.reason, "unknown_token")

    def test_rollback_dir_rejects_a_symlinked_rollback_root(self):
        cfg = self._cfg()
        self._maintenance_dir(cfg)
        outside = self._tmpdir("prks-rollback-outside-")
        self._symlink_or_skip(
            outside,
            backup_module._maintenance_subroot(cfg, *backup_module._ROLLBACK_SUBROOT),
            directory=True,
        )

        with self.assertRaises(RestoreError) as ctx:
            backup_module._rollback_dir(cfg, _JOURNAL_TXN_A)

        self.assertEqual(ctx.exception.reason, "journal_invalid")

    def test_stale_staging_cleanup_skips_a_symlinked_staging_root(self):
        cfg = self._cfg()
        self._maintenance_dir(cfg)
        outside, victim = self._outside_victim()
        stale = os.path.join(outside, "fixture-stage-aaaaaaaa")
        os.makedirs(stale)
        self._symlink_or_skip(
            outside,
            backup_module._maintenance_subroot(cfg, *backup_module._STAGING_SUBROOT),
            directory=True,
        )

        backup_module.cleanup_stale_staging(cfg)

        self.assertTrue(os.path.isdir(stale))
        self.assertTrue(os.path.isfile(victim))

    def test_scoped_remove_stays_correct_without_descriptor_support(self):
        """The portable fallback must behave identically on a platform without
        O_DIRECTORY/O_NOFOLLOW (native Windows), where touching those flags at
        all would raise AttributeError during startup cleanup."""
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)
        _outside, victim = self._outside_victim()
        child = os.path.join(staging_root, "fixture-stage-aaaaaaaa")
        os.makedirs(child)
        link = os.path.join(staging_root, "leaf")
        self._symlink_or_skip(victim, link)

        with patch.object(backup_module, "_SUPPORTS_DIR_FD", False):
            backup_module.cleanup_stale_staging(cfg)
            backup_module._remove_maintenance_child(
                cfg, backup_module._STAGING_SUBROOT, child
            )
            backup_module._remove_maintenance_child(
                cfg, backup_module._STAGING_SUBROOT, link
            )
            with self.assertRaises(ValueError):
                backup_module._remove_maintenance_child(
                    cfg,
                    backup_module._STAGING_SUBROOT,
                    os.path.join(staging_root, "sub", "deep"),
                )

        self.assertFalse(os.path.lexists(child))
        self.assertFalse(os.path.lexists(link))
        self.assertTrue(os.path.isfile(victim))
        self.assertTrue(os.path.isdir(staging_root))

    def test_scoped_remove_rejects_a_symlinked_subroot_without_descriptors(self):
        cfg = self._cfg()
        self._maintenance_dir(cfg)
        outside, victim = self._outside_victim()
        self._symlink_or_skip(
            outside,
            backup_module._maintenance_subroot(cfg, *backup_module._STAGING_SUBROOT),
            directory=True,
        )

        with patch.object(backup_module, "_SUPPORTS_DIR_FD", False):
            with self.assertRaises(ValueError):
                backup_module._remove_maintenance_child(
                    cfg,
                    backup_module._STAGING_SUBROOT,
                    os.path.join(outside, "victim.txt"),
                )
            backup_module.cleanup_stale_staging(cfg)

        self.assertTrue(os.path.isfile(victim))

    def test_verified_subroot_rejects_a_directory_reparse_point(self):
        """NTFS junctions are directories that os.path.islink() reports False for.

        Patched, because junctions cannot be created on POSIX: the contract under
        test is that verification consults the junction check at all, since the
        Windows fallback's os.walk(followlinks=False) would recurse into one.
        """
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)

        with patch.object(
            backup_module.os.path, "isjunction", lambda p: p == staging_root
        ):
            with self.assertRaises(ValueError):
                backup_module._verified_maintenance_subroot(
                    cfg, *backup_module._STAGING_SUBROOT
                )
            with patch.object(backup_module, "_SUPPORTS_DIR_FD", False):
                with self.assertRaises(ValueError):
                    backup_module._remove_maintenance_child(
                        cfg,
                        backup_module._STAGING_SUBROOT,
                        os.path.join(staging_root, "fixture-stage-aaaaaaaa"),
                    )

    def test_portable_removal_never_traverses_a_nested_reparse_point(self):
        """A junction inside the tree must be removed, not descended into.

        os.path.islink() is False for an NTFS junction and
        os.walk(followlinks=False) does not treat one as a link, so the portable
        path needs its own reparse-aware teardown. Patched, since junctions
        cannot be created on POSIX; the assertion is that the walk never scans
        the entry.
        """
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)
        child = os.path.join(staging_root, "fixture-stage-aaaaaaaa")
        junction = os.path.join(child, "junction")
        os.makedirs(junction)

        scanned = []
        real_scandir = os.scandir

        def spy_scandir(target):
            scanned.append(os.fspath(target))
            return real_scandir(target)

        with patch.object(backup_module, "_SUPPORTS_DIR_FD", False), patch.object(
            backup_module.os.path, "isjunction", lambda p: p == junction
        ), patch.object(backup_module.os, "scandir", spy_scandir):
            backup_module._remove_maintenance_child(
                cfg, backup_module._STAGING_SUBROOT, child
            )

        self.assertFalse(os.path.lexists(child))
        self.assertIn(child, scanned)
        self.assertNotIn(junction, scanned)

    def test_portable_removal_unlinks_a_nested_symlink_without_following(self):
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)
        _outside, victim = self._outside_victim()
        child = os.path.join(staging_root, "fixture-stage-aaaaaaaa")
        os.makedirs(os.path.join(child, "tree"))
        self._symlink_or_skip(victim, os.path.join(child, "tree", "link"))

        with patch.object(backup_module, "_SUPPORTS_DIR_FD", False):
            backup_module._remove_maintenance_child(
                cfg, backup_module._STAGING_SUBROOT, child
            )

        self.assertFalse(os.path.lexists(child))
        self.assertTrue(os.path.isfile(victim))

    def test_opening_an_absent_subroot_does_not_leak_a_descriptor(self):
        """A missing component is a normal path, not an exceptional one.

        The early return for it does not reach an `except` clause, so relying on
        one leaked a descriptor per call -- and discarding rollback state that is
        already gone hits this on every restore.
        """
        if not backup_module._SUPPORTS_DIR_FD:
            self.skipTest("descriptor-relative removal unavailable")
        fd_dir = "/proc/self/fd"
        if not os.path.isdir(fd_dir):
            self.skipTest("no /proc/self/fd to count open descriptors")
        cfg = self._cfg()
        self._maintenance_dir(cfg)  # 'rollback' deliberately absent

        # Counting is necessary: a lowest-free-descriptor probe is blind here,
        # because the leak accumulates above the descriptor the probe reclaims.
        before = len(os.listdir(fd_dir))
        for _ in range(20):
            self.assertIsNone(
                backup_module._open_maintenance_subroot(
                    cfg, *backup_module._ROLLBACK_SUBROOT
                )
            )
        self.assertEqual(len(os.listdir(fd_dir)), before)

    def test_portable_removal_resolves_the_storage_root_only_once(self):
        """Authorization and removal must share one canonical root snapshot.

        Resolving config.root twice let a symlink or junction retargeted between
        the two point verification and deletion at different trees. Counting the
        resolutions is the direct way to pin that, since after the fix there is
        no second resolution for a race to land in.
        """
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)
        child = os.path.join(staging_root, "fixture-stage-aaaaaaaa")
        os.makedirs(child)

        real_realpath = os.path.realpath
        resolutions = []

        def spy_realpath(target, *args, **kwargs):
            if os.fspath(target) == cfg.root:
                resolutions.append(target)
            return real_realpath(target, *args, **kwargs)

        with patch.object(backup_module, "_SUPPORTS_DIR_FD", False), patch.object(
            backup_module.os.path, "realpath", spy_realpath
        ):
            backup_module._remove_maintenance_child(
                cfg, backup_module._STAGING_SUBROOT, child
            )

        self.assertFalse(os.path.lexists(child))
        self.assertEqual(
            len(resolutions),
            1,
            f"storage root resolved {len(resolutions)} times, expected once",
        )

    def test_descriptor_removal_anchors_to_the_root_snapshot(self):
        """The descriptor descent must start from the authorized snapshot.

        Opening config.root re-resolves the operator symlink independently of
        the snapshot the authorization check used, so a retarget between the two
        could bind the descriptor to a different maintenance tree. The portable
        test cannot catch this: it patches _SUPPORTS_DIR_FD off.
        """
        if not backup_module._SUPPORTS_DIR_FD:
            self.skipTest("descriptor-relative removal unavailable")
        real_root = self._tmpdir("prks-real-root-")
        link_root = os.path.join(self._tmpdir("prks-link-"), "storage")
        self._symlink_or_skip(real_root, link_root, directory=True)
        cfg = self._cfg(link_root)
        staging_root = backup_module._maintenance_subroot(
            cfg, *backup_module._STAGING_SUBROOT
        )
        os.makedirs(staging_root, exist_ok=True)
        child = os.path.join(staging_root, "fixture-stage-aaaaaaaa")
        os.makedirs(child)

        opened = []
        real_open = os.open

        def spy_open(path, *args, **kwargs):
            opened.append(_fspath_or_none(path))
            return real_open(path, *args, **kwargs)

        with patch.object(backup_module.os, "open", spy_open):
            backup_module._remove_maintenance_child(
                cfg, backup_module._STAGING_SUBROOT, child
            )

        self.assertFalse(os.path.lexists(child))
        self.assertNotIn(
            link_root,
            opened,
            "descriptor descent re-resolved config.root instead of the snapshot",
        )
        self.assertIn(os.path.realpath(link_root), opened)

    def _root_swap_fixture(self):
        """A pending removal plus a decoy the canonical root can be replaced with.

        ``_remove_maintenance_child()`` authorizes against a pathname and then
        acts through it. Whoever can rename the root's target can make the two
        refer to different directories. The decoy stands in for the root, and
        holds a maintenance tree with a same-named child, so a removal that
        still runs has an obvious victim to destroy.

        ``vacate()`` renames the real root away; ``swap(install)`` vacates and
        then calls ``install(vacated, decoy)``; ``survived()`` probes the decoy
        child *through* ``root_real``, which by then is the decoy itself or a
        link to it, so one probe covers every swap shape.
        """
        cfg = self._cfg()
        leaf = "fixture-stage-aaaaaaaa"
        child = os.path.join(self._staging_root(cfg), leaf)
        os.makedirs(child)
        root_real = backup_module._resolved_storage_root(cfg)
        decoy = os.path.realpath(self._tmpdir("prks-decoy-"))
        victim = (
            backup_module.MAINTENANCE_DIRNAME,
            backup_module._STAGING_SUBROOT[0],
            leaf,
        )
        os.makedirs(os.path.join(decoy, *victim))
        moved = root_real + "-moved-away"
        self.addCleanup(shutil.rmtree, moved, ignore_errors=True)
        self.addCleanup(_unlink_if_link, root_real)

        def vacate():
            os.rename(root_real, moved)

        def swap(install):
            vacate()
            install(root_real, decoy)

        def survived():
            # Probed at both, because the decoy may still be where it started
            # (a refusal before the swap ever lands), or may have become
            # root_real, or may be what root_real links to.
            return any(
                os.path.isdir(os.path.join(base, *victim))
                for base in (root_real, decoy)
            )

        return SimpleNamespace(
            cfg=cfg,
            child=child,
            root_real=root_real,
            decoy=decoy,
            vacate=vacate,
            swap=swap,
            survived=survived,
        )

    def _assert_swapped_root_refused(self, cfg, child, survived, *, swapped=None):
        """Demand a refusal, and that the swapped-in tree was left alone.

        ``swapped`` guards against a vacuous run for the cases whose swap is
        driven from inside the removal; a case that vacates the root up front
        needs no such guard, and must not assert one, since the fix can refuse
        before the swap is ever reached.
        """
        refusal = None
        try:
            backup_module._remove_maintenance_child(
                cfg, backup_module._STAGING_SUBROOT, child
            )
        except ValueError as exc:
            refusal = exc
        if swapped is not None:
            self.assertTrue(
                swapped(), "the swap never happened; the test proves nothing"
            )
        # Checked before the refusal so a regression reports the data loss
        # rather than the missing exception.
        self.assertTrue(
            survived(),
            "removal ran inside the directory swapped in after authorization",
        )
        self.assertIsNotNone(refusal, "removal accepted a swapped storage root")
        return str(refusal)

    def _descriptor_swap_refused(self, install_replacement):
        """Swap the root at the descent's first open of it, and demand a refusal."""
        if not backup_module._SUPPORTS_DIR_FD:
            self.skipTest("descriptor-relative removal unavailable")
        fixture = self._root_swap_fixture()
        return self._refused_with_open_spy(
            fixture, lambda: fixture.swap(install_replacement)
        )

    def _refused_with_open_spy(self, fixture, at_first_root_open, *, guard=True):
        """Run the removal, firing ``at_first_root_open`` at the root's open."""
        done = []
        real_open = os.open

        def spy_open(path, *args, **kwargs):
            if not done and _fspath_or_none(path) == fixture.root_real:
                done.append(True)
                at_first_root_open()
            return real_open(path, *args, **kwargs)

        guarded = (lambda: bool(done)) if guard else None
        with patch.object(backup_module.os, "open", spy_open):
            return self._assert_swapped_root_refused(
                fixture.cfg, fixture.child, fixture.survived, swapped=guarded
            )

    def test_descriptor_removal_refuses_a_root_link_swapped_in_after_authorization(self):
        """A resolved root is never a symlink, so the descent must not follow one."""
        def install(vacated, decoy):
            self._symlink_or_skip(decoy, vacated, directory=True)

        self.assertIn("real directory", self._descriptor_swap_refused(install))

    def test_descriptor_removal_refuses_a_root_directory_swapped_in_after_authorization(self):
        """No-follow cannot see a real directory renamed into place; identity can."""
        self.assertIn(
            "changed identity",
            self._descriptor_swap_refused(
                lambda vacated, decoy: os.rename(decoy, vacated)
            ),
        )

    def test_portable_removal_refuses_a_root_swapped_in_after_authorization(self):
        """The fallback has no descriptor to bind to, so it must check identity.

        Without descriptors every step is path-based, so a real directory
        renamed into the canonical root's place is invisible to verification --
        it simply describes the replacement. Only the identity captured before
        authorization can tell the two apart.
        """
        fixture = self._root_swap_fixture()
        done = []
        real_verify = backup_module._verified_maintenance_subroot_from

        def swapping_verify(*args, **kwargs):
            if not done:
                done.append(True)
                fixture.swap(lambda vacated, decoy: os.rename(decoy, vacated))
            return real_verify(*args, **kwargs)

        with patch.object(backup_module, "_SUPPORTS_DIR_FD", False), patch.object(
            backup_module, "_verified_maintenance_subroot_from", swapping_verify
        ):
            refusal = self._assert_swapped_root_refused(
                fixture.cfg, fixture.child, fixture.survived,
                swapped=lambda: bool(done),
            )
        self.assertIn("changed identity", refusal)

    def test_removal_refuses_a_root_swapped_in_before_it_was_canonicalized(self):
        """The identity must be anchored ahead of canonicalization.

        Capturing it after realpath() captures whatever is at the canonical
        pathname by then. A real directory moved in between the two would
        therefore be recorded as the identity to trust, and authorization, the
        descriptor fstat and the portable re-check would all faithfully agree
        with the replacement. Taking the identity first turns that window into
        two observations that have to match.
        """
        fixture = self._root_swap_fixture()
        done = []
        real_resolve = backup_module._resolved_storage_root

        def swapping_resolve(*args, **kwargs):
            if not done:
                done.append(True)
                fixture.swap(lambda vacated, decoy: os.rename(decoy, vacated))
            return real_resolve(*args, **kwargs)

        with patch.object(
            backup_module, "_resolved_storage_root", swapping_resolve
        ):
            refusal = self._assert_swapped_root_refused(
                fixture.cfg, fixture.child, fixture.survived,
                swapped=lambda: bool(done),
            )

        self.assertIn("identity", refusal)

    def test_removal_refuses_a_root_that_was_gone_when_identity_was_captured(self):
        """A stat that fails is the rename window, not a benign absence.

        Vacating the root before the capture used to disable the whole identity
        check: os.stat() raised, the capture yielded None, and the comparison
        was skipped. Authorization still passed -- os.path.realpath() is
        non-strict and hands back the pathname of a missing directory -- so a
        real directory renamed into that pathname was descended into and its
        matching child deleted.
        """
        if not backup_module._SUPPORTS_DIR_FD:
            self.skipTest("descriptor-relative removal unavailable")
        fixture = self._root_swap_fixture()
        fixture.vacate()

        # No swap guard: with the root already gone, the capture refuses before
        # the descent ever opens anything, so nothing fires the spy.
        refusal = self._refused_with_open_spy(
            fixture,
            lambda: os.rename(fixture.decoy, fixture.root_real),
            guard=False,
        )

        self.assertIn("identity", refusal)

    def test_an_absent_component_does_not_downgrade_to_path_based_removal(self):
        """A vanished component must not hand the removal to the weaker branch.

        _open_maintenance_subroot_from() returns None both when the platform has
        no descriptors and when a component was absent during the O_NOFOLLOW
        descent. Treating the second like the first lets an actor who renames a
        maintenance component away and back downgrade a descriptor-bound removal
        to a path-based one -- and that branch is the raceable one.

        The subroot stays on disk throughout; only the descent's open of it
        fails, which is what a rename away and back looks like from here.
        """
        if not backup_module._SUPPORTS_DIR_FD:
            self.skipTest("descriptor-relative removal unavailable")
        cfg = self._cfg()
        child = os.path.join(self._staging_root(cfg), "fixture-stage-aaaaaaaa")
        os.makedirs(child)
        subroot_name = backup_module._STAGING_SUBROOT[0]
        real_open = os.open

        def vanishing_open(path, *args, **kwargs):
            if kwargs.get("dir_fd") is not None and path == subroot_name:
                raise FileNotFoundError(2, "No such file or directory", path)
            return real_open(path, *args, **kwargs)

        with patch.object(backup_module.os, "open", vanishing_open):
            backup_module._remove_maintenance_child(
                cfg, backup_module._STAGING_SUBROOT, child
            )

        self.assertTrue(
            os.path.isdir(child),
            "an absent component downgraded the removal to the path-based branch",
        )

    def test_stale_staging_cleanup_removes_expired_entries(self):
        cfg = self._cfg()
        staging_root = self._staging_root(cfg)
        expired = os.path.join(staging_root, "fixture-stage-aaaaaaaa")
        os.makedirs(expired)
        upload = os.path.join(staging_root, ".upload-old")
        with open(upload, "wb") as handle:
            handle.write(b"x")
        old = time.time() - (backup_module.STAGING_TTL_SECONDS * 2)
        os.utime(upload, (old, old))

        backup_module.cleanup_stale_staging(cfg)

        self.assertFalse(os.path.lexists(expired))
        self.assertFalse(os.path.lexists(upload))
        self.assertTrue(os.path.isdir(staging_root))


class TestBackupInventory(BackupRestoreTestCase):
    def test_every_storage_config_path_is_classified(self):
        self.assertEqual(storage_config_path_field_names(), classified_storage_field_names())
        inv = backup_storage_inventory()
        self.assertIn("db_path", inv.canonical)
        self.assertIn("pdfs_dir", inv.canonical)
        self.assertIn("people_dir", inv.canonical)
        self.assertIn("thumbs_dir", inv.derived)
        self.assertIn("index_db_path", inv.derived)
        self.assertIn("research_index_db_path", inv.derived)
        self.assertIn("log_file", inv.operational)
        self.assertIn("processing_dir", inv.conditional)

    def test_non_path_fields_are_explicit(self):
        names = {f.name for f in fields(StorageConfig)}
        leftover = names - classified_storage_field_names()
        self.assertEqual(leftover, {"mode", "processing_fallback_allowed"})


class TestBackupRoundTrip(BackupRestoreTestCase):
    def test_round_trip_to_empty_storage(self):
        source = self._bind_library(extra_pdf_name="orphan.pdf")
        from backend.sync_protocol import process_operation
        import uuid
        sync_db = server_module.db
        sync_tag = sync_db.add_tag("Sync backup")["id"]
        sync_op = dict(op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()), operation="ADD_WORK_TAG",
                       entity_type="work", entity_id=source["work_id"], payload={"tag_id": sync_tag},
                       base_revision=0, occurred_at="2026-09-11T00:00:00Z", created_at="2026-09-11T00:00:00Z", depends_on=[])
        self.assertEqual(process_operation(sync_db, sync_op)[0], 200)
        sync_db.delete_tag(sync_tag)
        sync_tables = ("sync_operations", "sync_entity_revisions", "sync_tag_lifecycle")
        sync_before = {table: sync_db.execute_query("SELECT * FROM " + table) for table in sync_tables}
        backup = create_backup(source["cfg"])
        self.assertTrue(backup.verified)
        names = _zip_namelist(backup.archive_path)
        self.assertIn(MANIFEST_NAME, names)
        self.assertIn(ARCHIVE_DB_PATH, names)
        self.assertIn(f"files/pdfs/{source['pdf_name']}", names)
        self.assertIn("files/pdfs/orphan.pdf", names)
        self.assertIn("files/people/portrait.webp", names)
        self.assertTrue(all(not n.startswith("thumbs/") for n in names))
        self.assertNotIn("prks_text_index.db", " ".join(names))
        self.assertNotIn("prks_research_index.db", " ".join(names))
        self.assertFalse(any(".prks-maintenance" in n for n in names))
        self.assertFalse(any("prks-errors.log" in n for n in names))
        manifest = _read_manifest(backup.archive_path)
        blob = json.dumps(manifest)
        self.assertNotIn(str(source["cfg"].root), blob)
        self.assertEqual(manifest["format"], FORMAT_ID)
        self.assertEqual(manifest["format_version"], FORMAT_VERSION)
        self.assertTrue(backup.filename.startswith("prks-backup-"))
        self.assertTrue(backup.filename.endswith(".prks-backup"))
        self.assertNotIn("Alpha", backup.filename)

        dest_root = self._tmpdir()
        dest = bind_storage(self._cfg(dest_root))
        staged = self._stage_copy(dest, backup.archive_path)
        self.assertTrue(staged.verified)
        out = apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])
        live = server_module.db
        for table in sync_tables:
            self.assertEqual(live.execute_query("SELECT * FROM " + table), sync_before[table])
        rows = live.execute_query("SELECT title FROM works")
        self.assertEqual([r["title"] for r in rows], [source["title"]])
        restored_pdf = os.path.join(dest.pdfs_dir, source["pdf_name"])
        with open(restored_pdf, "rb") as handle:
            self.assertEqual(handle.read(), source["pdf_bytes"])
        with open(os.path.join(dest.people_dir, "portrait.webp"), "rb") as handle:
            self.assertEqual(handle.read(), source["person_bytes"])
        settings = live.get_app_settings_response()
        self.assertEqual(settings["annotation_author"], source["author"])
        folders = live.execute_query("SELECT title FROM folders WHERE title = ?", ("Research",))
        self.assertEqual(len(folders), 1)
        tags = live.execute_query("SELECT name FROM tags WHERE name = ?", ("rhetoric",))
        self.assertEqual(len(tags), 1)
        roles = live.execute_query("SELECT role_type FROM roles")
        self.assertTrue(roles)
        canonical = live.execute_query(
            """
            SELECT id, type, content, page_index, color, geometry_json
            FROM annotations WHERE work_id = ? ORDER BY id
            """,
            (source["work_id"],),
        )
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0]["id"], "a1")
        self.assertEqual(canonical[0]["type"], "highlight")
        self.assertEqual(canonical[0]["content"], "note")
        self.assertEqual(canonical[0]["page_index"], 2)
        self.assertEqual(canonical[0]["color"], "#FFCD45")
        geom = json.loads(canonical[0]["geometry_json"] or "{}")
        self.assertEqual(geom["rect"]["origin"]["x"], 1)
        anns = json.loads(live.get_work_annotations(source["work_id"]))
        self.assertEqual(len(anns), 1)
        self.assertEqual(anns[0]["id"], "a1")
        self.assertEqual(anns[0]["type"], "highlight")
        self.assertEqual(anns[0]["contents"], "note")
        self.assertEqual(anns[0]["pageIndex"], 2)
        self.assertEqual(anns[0]["rect"]["size"]["width"], 3)
        tables = live.execute_query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='work_annotations'"
        )
        self.assertEqual(tables, [])
        thumbs = os.listdir(dest.thumbs_dir) if os.path.isdir(dest.thumbs_dir) else []
        self.assertNotIn("stale-thumb.webp", thumbs)
        hits = server_module.text_index.search_work_ids("unique token alpha")
        self.assertIn(source["work_id"], hits)
        with server_module.text_index._connection() as conn:
            version = conn.execute(
                "SELECT value FROM text_index_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            legacy = conn.execute(
                "SELECT COUNT(*) FROM work_text_index WHERE extraction_status = 'legacy'"
            ).fetchone()[0]
        self.assertEqual(int(version), 2)
        self.assertEqual(int(legacy), 0)

    def test_restore_replaces_existing_library(self):
        lib_a = self._bind_library(
            title="Library A",
            pdf_text="only in A",
            pdf_name="a.pdf",
            person_bytes=b"PERSON-A",
            author="Author A",
        )
        lib_b = self._bind_library(
            title="Library B",
            pdf_text="only in B searchable",
            pdf_name="b.pdf",
            person_bytes=b"PERSON-B",
            author="Author B",
        )
        backup_b = create_backup(lib_b["cfg"])
        bind_storage(lib_a["cfg"])
        staged = self._stage_copy(lib_a["cfg"], backup_b.archive_path)
        apply_restore(lib_a["cfg"], staged.token, "RESTORE", rebind=bind_storage)
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Library B"])
        self.assertFalse(os.path.isfile(os.path.join(lib_a["cfg"].pdfs_dir, "a.pdf")))
        self.assertTrue(os.path.isfile(os.path.join(lib_a["cfg"].pdfs_dir, "b.pdf")))
        with open(os.path.join(lib_a["cfg"].people_dir, "portrait.webp"), "rb") as handle:
            self.assertEqual(handle.read(), b"PERSON-B")
        self.assertFalse(os.path.isfile(os.path.join(lib_a["cfg"].thumbs_dir, "stale-thumb.webp")))
        hits = server_module.text_index.search_work_ids("only in B")
        self.assertTrue(hits)
        self.assertFalse(server_module.text_index.search_work_ids("only in A"))
        with server_module.text_index._connection() as conn:
            version = conn.execute(
                "SELECT value FROM text_index_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            legacy = conn.execute(
                "SELECT COUNT(*) FROM work_text_index WHERE extraction_status = 'legacy'"
            ).fetchone()[0]
        self.assertEqual(int(version), 2)
        self.assertEqual(int(legacy), 0)

    def test_sqlite_online_backup_is_used(self):
        src = Path(_PROJECT_DIR, "backend", "backup_restore.py").read_text(encoding="utf-8")
        self.assertIn("source.backup(dest)", src)
        self.assertNotIn("shutil.copy2(config.db_path", src)
        self.assertNotIn("shutil.copy(config.db_path", src)

    def test_processing_included_when_under_root(self):
        lib = self._bind_library(processing_name="inbox.pdf", processing_bytes=b"QUEUE-PDF")
        backup = create_backup(lib["cfg"])
        manifest = _read_manifest(backup.archive_path)
        self.assertTrue(manifest["components"]["processing"])
        self.assertIn("files/for_processing/inbox.pdf", _zip_namelist(backup.archive_path))

    def test_external_processing_not_included(self):
        lib = self._bind_library()
        outside = self._tmpdir()
        cfg = replace(lib["cfg"], processing_dir=outside)
        with open(os.path.join(outside, "secret.bin"), "wb") as handle:
            handle.write(b"SECRET")
        backup = create_backup(cfg)
        manifest = _read_manifest(backup.archive_path)
        self.assertFalse(manifest["components"]["processing"])
        names = _zip_namelist(backup.archive_path)
        self.assertFalse(any(n.startswith("files/for_processing/") and n != "files/for_processing/" for n in names))
        self.assertTrue(any("outside PRKS storage" in w for w in backup.warnings))

    def test_symlink_in_pdfs_is_not_followed(self):
        lib = self._bind_library()
        target = os.path.join(self._tmpdir(), "outside.bin")
        with open(target, "wb") as handle:
            handle.write(b"OUTSIDE")
        os.symlink(target, os.path.join(lib["cfg"].pdfs_dir, "link.bin"))
        backup = create_backup(lib["cfg"])
        names = _zip_namelist(backup.archive_path)
        self.assertNotIn("files/pdfs/link.bin", names)
        self.assertTrue(any("symbolic link" in w for w in backup.warnings))

    def test_missing_referenced_pdf_is_warning(self):
        lib = self._bind_library()
        os.remove(os.path.join(lib["cfg"].pdfs_dir, lib["pdf_name"]))
        backup = create_backup(lib["cfg"])
        self.assertTrue(backup.verified)
        self.assertTrue(any("already missing" in w for w in backup.warnings))

    def test_progress_callback_reports_phases(self):
        lib = self._bind_library()
        events = []
        backup = create_backup(lib["cfg"], progress=events.append)
        self.assertTrue(backup.verified)
        phases = [ev["phase"] for ev in events]
        self.assertIn("snapshot", phases)
        self.assertIn("archiving", phases)
        self.assertIn("verifying", phases)
        self.assertTrue(all("path" not in ev for ev in events))
        percents = [ev["percent"] for ev in events]
        self.assertGreaterEqual(percents[-1], percents[0])
        self.assertLessEqual(max(percents), 99)

    def test_cancel_stops_backup_and_deletes_temps(self):
        lib = self._bind_library()
        big = os.path.join(lib["cfg"].pdfs_dir, "big.bin")
        with open(big, "wb") as handle:
            handle.write(b"x" * (IO_CHUNK_SIZE * 4))
        cancel = threading.Event()

        def on_progress(ev):
            if ev.get("phase") == "archiving" and int(ev.get("bytes_done") or 0) > 0:
                cancel.set()

        with self.assertRaises(BackupError) as ctx:
            create_backup(lib["cfg"], progress=on_progress, cancel_event=cancel)
        self.assertEqual(ctx.exception.reason, "cancelled")
        maint = os.path.join(lib["cfg"].root, ".prks-maintenance", "backup")
        leftovers = []
        if os.path.isdir(maint):
            for name in os.listdir(maint):
                leftovers.append(name)
        self.assertFalse(any(name.endswith(".prks-backup") for name in leftovers))

    def test_orphan_annotations_are_backup_warning(self):
        lib = self._bind_library()
        conn = sqlite3.connect(lib["cfg"].db_path)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            INSERT INTO annotations (id, work_id, type, content, page_index, color, geometry_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("orphan-ann", "W-MISSING", "highlight", "x", 0, "", "{}"),
        )
        conn.commit()
        conn.close()
        backup = create_backup(lib["cfg"])
        self.assertTrue(backup.verified)
        self.assertTrue(any("foreign-key" in w for w in backup.warnings))
        dest = bind_storage(self._cfg(self._tmpdir()))
        staged = self._stage_copy(dest, backup.archive_path)
        out = apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])
        self.assertTrue(any("foreign-key" in w for w in staged.warnings))

    def test_live_schema_ahead_of_constant_can_backup_and_restore_locally(self):
        lib = self._bind_library()
        ahead = PRKS_SCHEMA_VERSION + 1
        conn = sqlite3.connect(lib["cfg"].db_path)
        conn.execute("UPDATE schema_version SET version = ?", (ahead,))
        conn.commit()
        conn.close()
        backup = create_backup(lib["cfg"])
        self.assertTrue(backup.verified)
        fresh = bind_storage(self._cfg(self._tmpdir()))
        with self.assertRaises(RestoreError) as ctx:
            self._stage_copy(fresh, backup.archive_path)
        self.assertEqual(ctx.exception.reason, "schema_newer")
        staged = self._stage_copy(lib["cfg"], backup.archive_path)
        with self.assertRaises(RestoreError) as ctx:
            apply_restore(lib["cfg"], staged.token, "RESTORE", rebind=bind_storage)
        self.assertEqual(ctx.exception.reason, "restore_failed")
        self.assertTrue(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, lib["pdf_name"])))
        live = sqlite3.connect(lib["cfg"].db_path)
        try:
            self.assertEqual(
                live.execute("SELECT version FROM schema_version").fetchone()[0],
                ahead,
            )
        finally:
            live.close()

    def test_schema_9_backup_migrates_on_restore(self):
        source = self._bind_library(title="Incoming V9", pdf_name="v9.pdf")
        conn = sqlite3.connect(source["cfg"].db_path)
        conn.execute("DROP INDEX IF EXISTS idx_saved_views_name_nocase")
        conn.execute("DROP TABLE IF EXISTS saved_views")
        conn.execute("DROP TABLE IF EXISTS argument_target_arguments")
        conn.execute("DROP TABLE IF EXISTS argument_target_positions")
        conn.execute("DROP TABLE IF EXISTS argument_sources")
        conn.execute("DROP TABLE IF EXISTS argument_verdicts")
        conn.execute("DROP TABLE IF EXISTS positions")
        conn.execute("DROP TABLE IF EXISTS concept_parents")
        conn.execute("DROP TABLE IF EXISTS concept_aliases")
        # A synthetic pre-v14 library: the sync tables are what migration 14 adds.
        for table in ("sync_operations", "sync_entity_revisions", "sync_tag_lifecycle"):
            conn.execute("DROP TABLE IF EXISTS " + table)
        conn.execute("UPDATE schema_version SET version = 9")
        conn.commit()
        conn.close()
        backup = create_backup(source["cfg"])
        dest = bind_storage(self._cfg(self._tmpdir()))
        staged = self._stage_copy(dest, backup.archive_path)
        out = apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])
        live = server_module.db
        versions = live.execute_query("SELECT version FROM schema_version")
        self.assertEqual([row["version"] for row in versions], [PRKS_SCHEMA_VERSION])
        titles = [row["title"] for row in live.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Incoming V9"])
        self.assertTrue(os.path.isfile(os.path.join(dest.pdfs_dir, "v9.pdf")))
        tables = [
            row["name"]
            for row in live.execute_query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='saved_views'"
            )
        ]
        self.assertEqual(tables, ["saved_views"])
        self.assertEqual(live.get_saved_views(), [])

    def test_schema_12_backup_with_work_annotations_restores_and_migrates(self):
        source = self._bind_library(title="Incoming V12", pdf_name="v12.pdf")
        work_id = source["work_id"]
        conn = sqlite3.connect(source["cfg"].db_path)
        conn.execute(
            """
            CREATE TABLE work_annotations (
                work_id TEXT PRIMARY KEY,
                annotations_json TEXT NOT NULL DEFAULT '[]',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "INSERT INTO work_annotations (work_id, annotations_json) VALUES (?, ?)",
            (work_id, json.dumps([{"id": "ann-stale", "contents": "OLD-C"}])),
        )
        # A synthetic pre-v14 library: the sync tables are what migration 14 adds.
        for table in ("sync_operations", "sync_entity_revisions", "sync_tag_lifecycle"):
            conn.execute("DROP TABLE IF EXISTS " + table)
        conn.execute("UPDATE schema_version SET version = 12")
        conn.commit()
        conn.close()
        backup = create_backup(source["cfg"])
        self.assertTrue(backup.verified)
        dest = bind_storage(self._cfg(self._tmpdir()))
        staged = self._stage_copy(dest, backup.archive_path)
        self.assertTrue(staged.verified)
        out = apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])
        live = server_module.db
        versions = live.execute_query("SELECT version FROM schema_version")
        self.assertEqual([row["version"] for row in versions], [PRKS_SCHEMA_VERSION])
        self.assertEqual(PRKS_SCHEMA_VERSION, 15)
        titles = [row["title"] for row in live.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Incoming V12"])
        canonical = live.execute_query(
            "SELECT id, type, content FROM annotations WHERE work_id = ? ORDER BY id",
            (work_id,),
        )
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0]["id"], "a1")
        self.assertEqual(canonical[0]["content"], "note")
        leftover = live.execute_query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='work_annotations'"
        )
        self.assertEqual(leftover, [])
        reconstructed = json.loads(live.get_work_annotations(work_id))
        self.assertEqual([item["id"] for item in reconstructed], ["a1"])
        self.assertNotIn("ann-stale", json.dumps(reconstructed))

    def test_saved_views_survive_backup_restore(self):
        source = self._bind_library(title="View Library", pdf_name="view.pdf")
        view = server_module.db.create_saved_view(
            "Adorno — culture industry",
            {
                "mode": "advanced",
                "q": "culture industry",
                "tag": "",
                "author": "Adorno",
                "publisher": "",
            },
        )
        backup = create_backup(source["cfg"])
        dest = bind_storage(self._cfg(self._tmpdir()))
        staged = self._stage_copy(dest, backup.archive_path)
        apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        restored = server_module.db.get_saved_views()
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["name"], "Adorno — culture industry")
        self.assertEqual(restored[0]["search"], view["search"])
        self.assertEqual(restored[0]["id"], view["id"])

    def test_v10_backup_migrates_saved_views_on_restore(self):
        source = self._bind_library(title="Incoming V10", pdf_name="v10.pdf")
        conn = sqlite3.connect(source["cfg"].db_path)
        conn.execute("DROP INDEX IF EXISTS idx_saved_views_name_nocase")
        conn.execute("DROP TABLE IF EXISTS saved_views")
        conn.execute("DROP TABLE IF EXISTS argument_target_arguments")
        conn.execute("DROP TABLE IF EXISTS argument_target_positions")
        conn.execute("DROP TABLE IF EXISTS argument_sources")
        conn.execute("DROP TABLE IF EXISTS argument_verdicts")
        conn.execute("DROP TABLE IF EXISTS positions")
        conn.execute("DROP TABLE IF EXISTS concept_parents")
        conn.execute("DROP TABLE IF EXISTS concept_aliases")
        # A synthetic pre-v14 library: the sync tables are what migration 14 adds.
        for table in ("sync_operations", "sync_entity_revisions", "sync_tag_lifecycle"):
            conn.execute("DROP TABLE IF EXISTS " + table)
        conn.execute("UPDATE schema_version SET version = 10")
        conn.commit()
        conn.close()
        backup = create_backup(source["cfg"])
        dest = bind_storage(self._cfg(self._tmpdir()))
        staged = self._stage_copy(dest, backup.archive_path)
        out = apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])
        versions = server_module.db.execute_query("SELECT version FROM schema_version")
        self.assertEqual([row["version"] for row in versions], [PRKS_SCHEMA_VERSION])
        tables = [
            row["name"]
            for row in server_module.db.execute_query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='saved_views'"
            )
        ]
        self.assertEqual(tables, ["saved_views"])
        self.assertEqual(server_module.db.get_saved_views(), [])
        titles = [row["title"] for row in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Incoming V10"])

    def test_restore_migration_failure_restores_previous_library(self):
        lib = self._bind_library(
            title="Keep Me",
            pdf_name="keep.pdf",
            person_bytes=b"KEEP-PORTRAIT",
        )
        source = self._bind_library(
            title="Incoming",
            pdf_name="new.pdf",
            person_bytes=b"NEW-PORTRAIT",
        )
        conn = sqlite3.connect(source["cfg"].db_path)
        conn.execute("UPDATE schema_version SET version = 9")
        conn.commit()
        conn.close()
        backup = create_backup(source["cfg"])
        bind_storage(lib["cfg"])
        staged = self._stage_copy(lib["cfg"], backup.archive_path)

        def exploding(_conn):
            raise RuntimeError("injected_migration_failure")

        with patch(
            "backend.db_migrations.MIGRATIONS",
            (Migration(10, "ordered_migration_baseline", exploding),),
        ):
            with self.assertRaises(RestoreError) as ctx:
                apply_restore(lib["cfg"], staged.token, "RESTORE", rebind=bind_storage)
        self.assertEqual(ctx.exception.reason, "restore_failed")
        live = sqlite3.connect(lib["cfg"].db_path)
        try:
            titles = [row[0] for row in live.execute("SELECT title FROM works")]
            version = live.execute("SELECT version FROM schema_version").fetchone()[0]
        finally:
            live.close()
        self.assertEqual(titles, ["Keep Me"])
        self.assertEqual(version, PRKS_SCHEMA_VERSION)
        self.assertTrue(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "keep.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "new.pdf")))
        with open(os.path.join(lib["cfg"].people_dir, "portrait.webp"), "rb") as handle:
            self.assertEqual(handle.read(), b"KEEP-PORTRAIT")
        self.assertFalse(
            os.path.isfile(os.path.join(lib["cfg"].root, ".prks-maintenance", "restore-journal.json"))
        )


class TestBackupCorruption(BackupRestoreTestCase):
    def _valid_backup(self):
        lib = self._bind_library()
        backup = create_backup(lib["cfg"])
        return lib, backup

    def test_corrupt_pdf_payload_fails_before_mutation(self):
        lib, backup = self._valid_backup()
        dest_root = self._tmpdir()
        dest = bind_storage(self._cfg(dest_root))
        live_marker = os.path.join(dest.pdfs_dir, "keep-me.pdf")
        os.makedirs(dest.pdfs_dir, exist_ok=True)
        with open(live_marker, "wb") as handle:
            handle.write(b"KEEP")
        broken = os.path.join(self._tmpdir(), "broken.prks-backup")

        def mutate(info, data):
            if info.filename.startswith("files/pdfs/"):
                return info.filename, data + b"x", info
            return info.filename, data, info

        _rewrite_backup(backup.archive_path, broken, mutate)
        with self.assertRaises(RestoreError):
            stage_restore(dest, broken)
        self.assertTrue(os.path.isfile(live_marker))

    def test_corrupt_db_payload_fails(self):
        lib, backup = self._valid_backup()
        dest = bind_storage(self._cfg(self._tmpdir()))
        broken = os.path.join(self._tmpdir(), "broken.prks-backup")

        def mutate(info, data):
            if info.filename == ARCHIVE_DB_PATH:
                return info.filename, data + b"corrupt", info
            return info.filename, data, info

        _rewrite_backup(backup.archive_path, broken, mutate)
        with self.assertRaises(RestoreError):
            stage_restore(dest, broken)

    def test_corrupt_manifest_hash_fails(self):
        lib, backup = self._valid_backup()
        dest = bind_storage(self._cfg(self._tmpdir()))
        broken = os.path.join(self._tmpdir(), "broken.prks-backup")

        def mutate(info, data):
            if info.filename == MANIFEST_NAME:
                manifest = json.loads(data.decode("utf-8"))
                manifest["entries"][0]["sha256"] = "0" * 64
                return info.filename, json.dumps(manifest).encode("utf-8"), info
            return info.filename, data, info

        _rewrite_backup(backup.archive_path, broken, mutate)
        with self.assertRaises(RestoreError):
            stage_restore(dest, broken)

    def test_corrupt_manifest_size_fails(self):
        lib, backup = self._valid_backup()
        dest = bind_storage(self._cfg(self._tmpdir()))
        broken = os.path.join(self._tmpdir(), "broken.prks-backup")

        def mutate(info, data):
            if info.filename == MANIFEST_NAME:
                manifest = json.loads(data.decode("utf-8"))
                manifest["entries"][0]["size"] = 1
                return info.filename, json.dumps(manifest).encode("utf-8"), info
            return info.filename, data, info

        _rewrite_backup(backup.archive_path, broken, mutate)
        with self.assertRaises(RestoreError):
            stage_restore(dest, broken)

    def test_unknown_format_version_fails(self):
        lib, backup = self._valid_backup()
        dest = bind_storage(self._cfg(self._tmpdir()))
        broken = os.path.join(self._tmpdir(), "broken.prks-backup")

        def mutate(info, data):
            if info.filename == MANIFEST_NAME:
                manifest = json.loads(data.decode("utf-8"))
                manifest["format_version"] = 2
                return info.filename, json.dumps(manifest).encode("utf-8"), info
            return info.filename, data, info

        _rewrite_backup(backup.archive_path, broken, mutate)
        with self.assertRaises(RestoreError) as ctx:
            stage_restore(dest, broken)
        self.assertEqual(ctx.exception.reason, "unsupported_format_version")

    def test_newer_schema_is_refused(self):
        lib, backup = self._valid_backup()
        dest = bind_storage(self._cfg(self._tmpdir()))
        work = self._tmpdir()
        extracted = os.path.join(work, "tree")
        os.makedirs(extracted, exist_ok=True)
        with zipfile.ZipFile(backup.archive_path, "r") as zf:
            for info in zf.infolist():
                if info.filename.endswith("/"):
                    continue
                dest_path = os.path.join(extracted, *info.filename.split("/"))
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with zf.open(info) as src, open(dest_path, "wb") as out:
                    out.write(src.read())
        db_path = os.path.join(extracted, "data", "prks_data.db")
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE schema_version SET version = ?", (PRKS_SCHEMA_VERSION + 5,))
        conn.commit()
        conn.close()
        manifest_path = os.path.join(extracted, MANIFEST_NAME)
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["db_schema_version"] = PRKS_SCHEMA_VERSION + 5
        for entry in manifest["entries"]:
            if entry["path"] == ARCHIVE_DB_PATH:
                size, digest = 0, hashlib.sha256()
                with open(db_path, "rb") as handle:
                    while True:
                        chunk = handle.read(65536)
                        if not chunk:
                            break
                        digest.update(chunk)
                        size += len(chunk)
                entry["size"] = size
                entry["sha256"] = digest.hexdigest()
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        broken = os.path.join(work, "newer.prks-backup")
        with zipfile.ZipFile(broken, "w") as zf:
            zf.write(manifest_path, MANIFEST_NAME)
            zf.write(db_path, ARCHIVE_DB_PATH)
            pdfs = os.path.join(extracted, "files", "pdfs")
            for name in os.listdir(pdfs):
                zf.write(os.path.join(pdfs, name), f"files/pdfs/{name}")
            people = os.path.join(extracted, "files", "people")
            for name in os.listdir(people):
                zf.write(os.path.join(people, name), f"files/people/{name}")
            # refresh hashes for rewritten files except we already updated db; others unchanged
        # Rebuild entries from actual files for hash match
        rebuilt = os.path.join(work, "newer-fixed.prks-backup")
        entries = []
        with zipfile.ZipFile(broken, "r") as zin, zipfile.ZipFile(rebuilt, "w") as zout:
            members = [i for i in zin.infolist() if i.filename != MANIFEST_NAME]
            for info in members:
                data = zin.read(info.filename)
                zout.writestr(info.filename, data)
                entries.append(
                    {
                        "path": info.filename,
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
            manifest["entries"] = entries
            zout.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
        with self.assertRaises(RestoreError) as ctx:
            stage_restore(dest, rebuilt)
        self.assertEqual(ctx.exception.reason, "schema_newer")
        self.assertIn("Update PRKS before restoring it", ctx.exception.message)
        self.assertTrue(os.path.isfile(dest.db_path))


class TestZipTraversal(BackupRestoreTestCase):
    def _stage_malicious(self, members):
        dest = bind_storage(self._cfg(self._tmpdir()))
        path = os.path.join(self._tmpdir(), "evil.prks-backup")
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in members:
                zf.writestr(name, data)
        with self.assertRaises(RestoreError):
            stage_restore(dest, path)
        root = dest.root
        for dirpath, dirnames, filenames in os.walk(root):
            if ".prks-maintenance" in dirpath:
                continue
            for name in filenames:
                self.assertNotEqual(name, "secret")
        return dest

    def test_parent_traversal_rejected(self):
        self._stage_malicious(
            [
                (MANIFEST_NAME, b"{}"),
                ("../prks_data.db", b"nope"),
            ]
        )

    def test_absolute_path_rejected(self):
        self._stage_malicious([(MANIFEST_NAME, b"{}"), ("/data/file", b"x")])

    def test_windows_drive_rejected(self):
        self._stage_malicious([(MANIFEST_NAME, b"{}"), (r"C:\file", b"x")])

    def test_nested_dotdot_rejected(self):
        self._stage_malicious(
            [(MANIFEST_NAME, b"{}"), ("files/pdfs/../../secret", b"x")]
        )

    def test_duplicate_members_rejected(self):
        dest = bind_storage(self._cfg(self._tmpdir()))
        path = os.path.join(self._tmpdir(), "dup.prks-backup")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(ARCHIVE_DB_PATH, b"a")
            zf.writestr(ARCHIVE_DB_PATH, b"b")
            zf.writestr(MANIFEST_NAME, b"{}")
        with self.assertRaises(RestoreError):
            stage_restore(dest, path)

    def test_symlink_member_rejected(self):
        dest = bind_storage(self._cfg(self._tmpdir()))
        path = os.path.join(self._tmpdir(), "link.prks-backup")
        info = zipfile.ZipInfo("files/pdfs/link")
        info.create_system = 3
        info.external_attr = (0o120777 & 0xFFFF) << 16
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(MANIFEST_NAME, b"{}")
            zf.writestr(info, b"/etc/passwd")
        with self.assertRaises(RestoreError):
            stage_restore(dest, path)

    def test_plain_zip_with_manifest_rejected(self):
        dest = bind_storage(self._cfg(self._tmpdir()))
        path = os.path.join(self._tmpdir(), "plain.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(MANIFEST_NAME, json.dumps({"hello": "world"}).encode("utf-8"))
        with self.assertRaises(RestoreError):
            stage_restore(dest, path)

    def test_module_never_calls_extractall(self):
        src = Path(_PROJECT_DIR, "backend", "backup_restore.py").read_text(encoding="utf-8")
        self.assertNotIn("extractall(", src)


class TestRestoreRollback(BackupRestoreTestCase):
    def test_fail_after_old_db_moved_restores_original(self):
        lib = self._bind_library(title="Keep Me", pdf_name="keep.pdf")
        other = self._bind_library(title="Incoming", pdf_name="new.pdf", pdf_text="incoming")
        backup = create_backup(other["cfg"])
        bind_storage(lib["cfg"])
        staged = self._stage_copy(lib["cfg"], backup.archive_path)
        with self.assertRaises(RestoreError):
            apply_restore(
                lib["cfg"],
                staged.token,
                "RESTORE",
                rebind=bind_storage,
                fail_after="old_moved",
            )
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Keep Me"])
        self.assertTrue(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "keep.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "new.pdf")))

    def test_fail_after_new_pdfs_installed_restores_original(self):
        lib = self._bind_library(title="Keep Me", pdf_name="keep.pdf")
        other = self._bind_library(title="Incoming", pdf_name="new.pdf", pdf_text="incoming")
        backup = create_backup(other["cfg"])
        bind_storage(lib["cfg"])
        staged = self._stage_copy(lib["cfg"], backup.archive_path)
        with self.assertRaises(RestoreError):
            apply_restore(
                lib["cfg"],
                staged.token,
                "RESTORE",
                rebind=bind_storage,
                fail_after="new_installed",
            )
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Keep Me"])
        self.assertTrue(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "keep.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "new.pdf")))

    def test_stage_keeps_in_flight_upload_file(self):
        lib = self._bind_library()
        backup = create_backup(lib["cfg"])
        dest = bind_storage(self._cfg(self._tmpdir()))
        staging = os.path.join(dest.root, ".prks-maintenance", "restore-staging")
        os.makedirs(staging, exist_ok=True)
        upload = os.path.join(staging, ".upload-inflight")
        shutil.copy2(backup.archive_path, upload)
        staged = stage_restore(dest, upload)
        self.assertTrue(staged.verified)

    def test_confirm_required(self):
        lib = self._bind_library()
        backup = create_backup(lib["cfg"])
        dest = bind_storage(self._cfg(self._tmpdir()))
        staged = self._stage_copy(dest, backup.archive_path)
        with self.assertRaises(RestoreError) as ctx:
            apply_restore(dest, staged.token, "yes", rebind=bind_storage)
        self.assertEqual(ctx.exception.reason, "confirmation_required")

    def test_token_is_single_use(self):
        lib = self._bind_library()
        backup = create_backup(lib["cfg"])
        dest = bind_storage(self._cfg(self._tmpdir()))
        staged = self._stage_copy(dest, backup.archive_path)
        apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        dest2 = bind_storage(self._cfg(self._tmpdir()))
        with self.assertRaises(RestoreError) as ctx:
            apply_restore(dest2, staged.token, "RESTORE", rebind=bind_storage)
        self.assertEqual(ctx.exception.reason, "unknown_token")


class TestCrashJournalRecovery(BackupRestoreTestCase):
    def _incomplete_journal_state(self):
        """A pre-committed journal with its rollback material still intact."""
        lib = self._bind_library(title="Original", pdf_name="orig.pdf")
        cfg = lib["cfg"]
        maint = os.path.join(cfg.root, ".prks-maintenance", "rollback", _JOURNAL_TXN_A)
        os.makedirs(os.path.join(maint, "database"), exist_ok=True)
        os.makedirs(os.path.join(maint, "pdfs"), exist_ok=True)
        os.makedirs(os.path.join(maint, "people"), exist_ok=True)
        db_name = os.path.basename(cfg.db_path)
        os.replace(cfg.db_path, os.path.join(maint, "database", db_name))
        os.replace(cfg.pdfs_dir, os.path.join(maint, "pdfs"))
        os.replace(cfg.people_dir, os.path.join(maint, "people"))
        os.makedirs(cfg.pdfs_dir, exist_ok=True)
        with open(os.path.join(cfg.pdfs_dir, "partial.pdf"), "wb") as handle:
            handle.write(b"PARTIAL")
        journal = {
            "format": "prks-restore-journal",
            "format_version": 1,
            "transaction_id": _JOURNAL_TXN_A,
            "phase": "installing_new",
            "components": {
                "database": {"old_moved": True, "new_installed": False},
                "pdfs": {"old_moved": True, "new_installed": True},
                "people": {"old_moved": True, "new_installed": False},
            },
        }
        os.makedirs(os.path.join(cfg.root, ".prks-maintenance"), exist_ok=True)
        journal_file = backup_module.journal_path(cfg)
        with open(journal_file, "w", encoding="utf-8") as handle:
            json.dump(journal, handle)
        return cfg, journal_file, maint, journal

    def _assert_previous_library(self, cfg):
        """The library is the pre-restore one, not the half-installed state."""
        bind_storage(cfg)
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Original"])
        self.assertTrue(os.path.isfile(os.path.join(cfg.pdfs_dir, "orig.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(cfg.pdfs_dir, "partial.pdf")))

    def _unenumerable_journal_dir(self):
        """Patch os.listdir so the directory holding the journal cannot be read.

        Models a transient failure, or a Windows ACL that permits access to a
        known file but not enumeration of its directory.
        """
        real_listdir = os.listdir

        def failing_listdir(target):
            entries = real_listdir(target)
            if backup_module.JOURNAL_FILENAME in entries:
                raise OSError(13, "Permission denied")
            return entries

        return patch.object(backup_module.os, "listdir", failing_listdir)

    def test_journal_removal_surfaces_an_enumeration_failure(self):
        """A directory that cannot be listed is not proof the child is gone."""
        cfg, journal_file, _maint, _journal = self._incomplete_journal_state()

        with self._unenumerable_journal_dir():
            with self.assertRaises(OSError):
                backup_module._remove_maintenance_child(
                    cfg, backup_module._JOURNAL_SUBROOT, journal_file
                )

        self.assertTrue(os.path.isfile(journal_file))

    def test_recovery_keeps_rollback_material_when_journal_removal_fails(self):
        """Rollback state must outlive the journal.

        If recovery reported success while leaving the journal behind, the next
        startup would replay it: _rollback_from_journal() removes the live
        components it believes are half-installed, then finds nothing to restore
        because the rollback tree was already cleaned -- losing canonical data.
        """
        cfg, journal_file, maint, _journal = self._incomplete_journal_state()

        with self._unenumerable_journal_dir():
            with self.assertRaises(RestoreError) as ctx:
                recover_incomplete_restore(cfg)
        self.assertEqual(ctx.exception.reason, "journal_not_removed")

        # Failure is explicit, and nothing that replay depends on was discarded.
        self.assertTrue(os.path.isfile(journal_file))
        self.assertTrue(os.path.isdir(maint))

        # Recovery stays possible once enumeration works again, and the library
        # is the previous one rather than the half-installed state.
        out = recover_incomplete_restore(cfg)
        self.assertEqual(out["outcome"], "restored_previous")
        self.assertFalse(os.path.lexists(journal_file))
        self._assert_previous_library(cfg)

    def _aged_rollback_tree(self, cfg, txn, age_seconds):
        """A rollback tree holding previous-library bytes, aged by the clock."""
        tree = os.path.join(cfg.root, ".prks-maintenance", "rollback", txn)
        os.makedirs(os.path.join(tree, "pdfs"), exist_ok=True)
        with open(os.path.join(tree, "pdfs", "previous.pdf"), "wb") as handle:
            handle.write(b"PREVIOUS-LIBRARY")
        stamp = time.time() - age_seconds
        os.utime(tree, (stamp, stamp))
        return tree

    def test_startup_reclaims_a_rollback_tree_no_journal_can_name(self):
        """The window the journal-first ordering leaves must not leak storage.

        Exit after journal removal is confirmed but before its tree is removed,
        and the next startup finds no journal -- so nothing names the tree, and
        _rollback_from_journal() is its only reader. It is unreachable garbage
        holding an entire previous library.
        """
        cfg = self._cfg()
        tree = self._aged_rollback_tree(
            cfg, _JOURNAL_TXN_B, backup_module.ROLLBACK_ORPHAN_TTL_SECONDS * 2
        )

        result = recover_incomplete_restore(cfg)

        self.assertFalse(result["performed"])
        self.assertFalse(os.path.lexists(tree))

    def test_startup_leaves_a_fresh_rollback_tree_alone(self):
        """apply_restore() creates the tree just before it writes the journal.

        The age guard is what keeps this sweep from reaching into that window
        and deleting the rollback material of a restore still in flight.
        """
        cfg = self._cfg()
        tree = self._aged_rollback_tree(cfg, _JOURNAL_TXN_B, 0)

        recover_incomplete_restore(cfg)

        self.assertTrue(os.path.isdir(tree))

    def test_a_failed_journal_removal_keeps_even_an_aged_rollback_tree(self):
        """The sweep must be unreachable while a journal is still on disk.

        Age alone must never authorize the removal: a tree a surviving journal
        still names is replay material, not garbage, however old it looks.
        """
        cfg, journal_file, maint, _journal = self._incomplete_journal_state()
        stamp = time.time() - backup_module.ROLLBACK_ORPHAN_TTL_SECONDS * 2
        os.utime(maint, (stamp, stamp))

        with self._unenumerable_journal_dir():
            with self.assertRaises(RestoreError):
                recover_incomplete_restore(cfg)

        self.assertTrue(os.path.isfile(journal_file))
        self.assertTrue(os.path.isdir(maint))

    def test_replaying_a_journal_without_rollback_material_keeps_live_data(self):
        """Replay must not delete what it cannot put back.

        This is the state a silently-failed journal removal used to leave
        behind: journal present, rollback copies gone.
        """
        cfg, journal_file, maint, _journal = self._incomplete_journal_state()
        recover_incomplete_restore(cfg)
        self.assertFalse(os.path.lexists(journal_file))

        # Restore the dangerous combination by hand: the journal is back, but
        # the rollback material has been consumed by the first recovery.
        with open(journal_file, "w", encoding="utf-8") as handle:
            json.dump(_journal, handle)
        for name in ("database", "pdfs", "people"):
            self.assertFalse(
                os.path.lexists(
                    backup_module._rollback_component_path(cfg, maint, name)
                )
            )

        out = recover_incomplete_restore(cfg)
        self.assertEqual(out["outcome"], "restored_previous")
        self._assert_previous_library(cfg)

    def test_recovery_completes_database_sidecars_after_a_partial_pass(self):
        """A crash between moving the rollback database and its sidecars.

        The main rollback file is already on the live path, so the "no material"
        guard would skip the whole component and the WAL -- which can hold
        committed pages -- would be deleted along with the rollback tree.
        """
        cfg, journal_file, maint, _journal = self._incomplete_journal_state()
        db_name = os.path.basename(cfg.db_path)
        rolled_db = os.path.join(maint, "database", db_name)
        os.replace(rolled_db, cfg.db_path)
        with open(rolled_db + "-wal", "wb") as handle:
            handle.write(b"WAL-PAGES")

        out = recover_incomplete_restore(cfg)

        self.assertEqual(out["outcome"], "restored_previous")
        self.assertTrue(os.path.isfile(cfg.db_path))
        self.assertTrue(os.path.isfile(cfg.db_path + "-wal"))
        with open(cfg.db_path + "-wal", "rb") as handle:
            self.assertEqual(handle.read(), b"WAL-PAGES")
        self.assertFalse(os.path.lexists(journal_file))

    def test_incomplete_journal_restores_previous(self):
        cfg, _journal_file, _maint, _journal = self._incomplete_journal_state()
        out = recover_incomplete_restore(cfg)
        self.assertEqual(out["outcome"], "restored_previous")
        self._assert_previous_library(cfg)

    def test_journal_with_an_unusable_transaction_id_is_refused(self):
        """The transaction id becomes a path segment under maintenance_root and
        recovery then removes and re-installs whatever it finds there. A journal
        naming something else must be refused, not followed."""
        lib = self._bind_library(title="Original", pdf_name="orig.pdf")
        cfg = lib["cfg"]
        maintenance = os.path.join(cfg.root, ".prks-maintenance")
        os.makedirs(maintenance, exist_ok=True)
        journal_file = os.path.join(maintenance, "restore-journal.json")
        for bad in ("../../../../tmp", "..", "with/slash", "short", ""):
            journal = {
                "format": "prks-restore-journal",
                "format_version": 1,
                "transaction_id": bad,
                "phase": "installing_new",
                "components": {"database": {"old_moved": True}},
            }
            with open(journal_file, "w") as handle:
                json.dump(journal, handle)
            with self.assertRaises(RestoreError) as ctx:
                recover_incomplete_restore(cfg)
            self.assertEqual(ctx.exception.reason, "journal_invalid", bad)
        self.assertTrue(os.path.isfile(journal_file), "a refused journal is kept")

    def test_committed_journal_keeps_new_and_cleans_rollback(self):
        lib = self._bind_library(title="New Library", pdf_name="new.pdf")
        cfg = lib["cfg"]
        rollback = os.path.join(cfg.root, ".prks-maintenance", "rollback", _JOURNAL_TXN_B)
        os.makedirs(rollback, exist_ok=True)
        with open(os.path.join(rollback, "leftover"), "w") as handle:
            handle.write("old")
        journal = {
            "format": "prks-restore-journal",
            "format_version": 1,
            "transaction_id": _JOURNAL_TXN_B,
            "phase": "committed",
            "components": {
                "database": {"old_moved": True, "new_installed": True},
                "pdfs": {"old_moved": True, "new_installed": True},
                "people": {"old_moved": True, "new_installed": True},
            },
        }
        with open(os.path.join(cfg.root, ".prks-maintenance", "restore-journal.json"), "w") as handle:
            json.dump(journal, handle)
        out = recover_incomplete_restore(cfg)
        self.assertEqual(out["outcome"], "keep_restored")
        self.assertFalse(out["needs_reindex"])
        self.assertFalse(os.path.isdir(rollback))
        self.assertFalse(
            os.path.isfile(os.path.join(cfg.root, ".prks-maintenance", "restore-journal.json"))
        )
        bind_storage(cfg)
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["New Library"])

    def test_canonical_installed_journal_restores_previous(self):
        orig = self._bind_library(title="Original", pdf_name="orig.pdf", person_bytes=b"OLD-PORTRAIT")
        incoming = self._bind_library(
            title="Incoming",
            pdf_name="new.pdf",
            pdf_text="incoming",
            person_bytes=b"NEW-PORTRAIT",
        )
        cfg = orig["cfg"]
        txn = _JOURNAL_TXN_C
        rollback = os.path.join(cfg.root, ".prks-maintenance", "rollback", txn)
        os.makedirs(os.path.join(rollback, "database"), exist_ok=True)
        db_name = os.path.basename(cfg.db_path)
        os.replace(cfg.db_path, os.path.join(rollback, "database", db_name))
        os.replace(cfg.pdfs_dir, os.path.join(rollback, "pdfs"))
        os.replace(cfg.people_dir, os.path.join(rollback, "people"))
        shutil.copy2(incoming["cfg"].db_path, cfg.db_path)
        shutil.copytree(incoming["cfg"].pdfs_dir, cfg.pdfs_dir)
        shutil.copytree(incoming["cfg"].people_dir, cfg.people_dir)
        flags = {
            "old_existed": True,
            "old_move_started": True,
            "old_moved": True,
            "new_install_started": True,
            "new_installed": True,
        }
        journal = {
            "format": "prks-restore-journal",
            "format_version": 1,
            "transaction_id": txn,
            "phase": "canonical_installed",
            "components": {
                "database": dict(flags),
                "pdfs": dict(flags),
                "people": dict(flags),
            },
        }
        os.makedirs(os.path.join(cfg.root, ".prks-maintenance"), exist_ok=True)
        with open(os.path.join(cfg.root, ".prks-maintenance", "restore-journal.json"), "w") as handle:
            json.dump(journal, handle)
        out = recover_incomplete_restore(cfg)
        self.assertEqual(out["outcome"], "restored_previous")
        self.assertFalse(out["needs_reindex"])
        bind_storage(cfg)
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Original"])
        self.assertTrue(os.path.isfile(os.path.join(cfg.pdfs_dir, "orig.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(cfg.pdfs_dir, "new.pdf")))
        with open(os.path.join(cfg.people_dir, "portrait.webp"), "rb") as handle:
            self.assertEqual(handle.read(), b"OLD-PORTRAIT")


class TestRestoreCrashWindows(BackupRestoreTestCase):
    def _crash_and_recover_old_library(self, fail_after):
        lib = self._bind_library(title="Keep Me", pdf_name="keep.pdf", person_bytes=b"KEEP-PORTRAIT")
        other = self._bind_library(
            title="Incoming",
            pdf_name="new.pdf",
            pdf_text="incoming",
            person_bytes=b"NEW-PORTRAIT",
        )
        backup = create_backup(other["cfg"])
        bind_storage(lib["cfg"])
        staged = self._stage_copy(lib["cfg"], backup.archive_path)
        with self.assertRaises(RestoreCrash):
            apply_restore(
                lib["cfg"],
                staged.token,
                "RESTORE",
                rebind=bind_storage,
                fail_after=fail_after,
            )
        out = recover_incomplete_restore(lib["cfg"])
        self.assertEqual(out["outcome"], "restored_previous")
        self.assertFalse(out["needs_reindex"])
        bind_storage(lib["cfg"])
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Keep Me"])
        self.assertTrue(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "keep.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "new.pdf")))
        with open(os.path.join(lib["cfg"].people_dir, "portrait.webp"), "rb") as handle:
            self.assertEqual(handle.read(), b"KEEP-PORTRAIT")
        settings = server_module.db.get_app_settings_response()
        self.assertEqual(settings["annotation_author"], "Backup Author")
        self.assertFalse(
            os.path.isfile(os.path.join(lib["cfg"].root, ".prks-maintenance", "restore-journal.json"))
        )

    def test_crash_after_old_move_started_database(self):
        self._crash_and_recover_old_library("old_move_started:database")

    def test_crash_after_old_renamed_before_old_moved_database(self):
        self._crash_and_recover_old_library("old_renamed:database")

    def test_crash_after_new_install_started_database(self):
        self._crash_and_recover_old_library("new_install_started:database")

    def test_crash_after_new_renamed_before_new_installed_database(self):
        self._crash_and_recover_old_library("new_renamed:database")

    def test_crash_after_old_move_started_pdfs(self):
        self._crash_and_recover_old_library("old_move_started:pdfs")

    def test_crash_after_old_renamed_before_old_moved_pdfs(self):
        self._crash_and_recover_old_library("old_renamed:pdfs")

    def test_crash_after_new_install_started_pdfs(self):
        self._crash_and_recover_old_library("new_install_started:pdfs")

    def test_crash_after_new_renamed_before_new_installed_pdfs(self):
        self._crash_and_recover_old_library("new_renamed:pdfs")


class TestRestoreDurabilityBoundary(BackupRestoreTestCase):
    """EF-017 / #110: a persistence boundary restore cannot establish stops it.

    Ordering and the refusal paths are pinned in `test_restore_durability.py`.
    This is the whole transaction: a directory fsync that answers False must
    leave the library the user already had, still recoverable.
    """

    def _refusing_directory_sync(self, directory):
        """Answer False for one directory and sync every other one for real.

        Restore resolves a directory before opening it, so the refusal matches
        on `realpath` too.
        """
        refused = os.path.realpath(directory)
        real = fs_durability.fsync_directory

        def refuse(path):
            if os.path.realpath(path) == refused:
                return False
            return real(path)

        return patch.object(fs_durability, "fsync_directory", refuse)

    def test_a_refused_directory_sync_keeps_the_previous_library_recoverable(self):
        lib = self._bind_library(title="Keep Me", pdf_name="keep.pdf")
        other = self._bind_library(title="Incoming", pdf_name="new.pdf", pdf_text="incoming")
        backup = create_backup(other["cfg"])
        bind_storage(lib["cfg"])
        staged = self._stage_copy(lib["cfg"], backup.archive_path)
        journal = backup_module.journal_path(lib["cfg"])

        with self._refusing_directory_sync(lib["cfg"].root):
            with self.assertRaises(RestoreError) as caught:
                apply_restore(lib["cfg"], staged.token, "RESTORE", rebind=bind_storage)

        self.assertEqual(caught.exception.reason, "rename_not_durable")
        # Rollback ran, but its own moves were not durable either, so the
        # journal and the rollback tree stay for the next startup rather than
        # being removed while a crash could still undo them.
        self.assertTrue(os.path.isfile(journal))

        out = recover_incomplete_restore(lib["cfg"])

        self.assertEqual(out["outcome"], "restored_previous")
        self.assertFalse(os.path.exists(journal))
        bind_storage(lib["cfg"])
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Keep Me"])
        self.assertTrue(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "keep.pdf")))
        self.assertFalse(os.path.isfile(os.path.join(lib["cfg"].pdfs_dir, "new.pdf")))

    def test_an_unconfirmed_commit_record_does_not_roll_back_a_finished_restore(self):
        """The one transition where refusing must not start a rollback.

        Everything is installed and bound by the commit write, and its replace
        has already happened when only the directory sync is refused. Rolling
        back then is the unsafe move: a crash during that rollback could leave
        a surviving "committed" journal describing a half-rolled-back library.
        """
        lib = self._bind_library(title="Keep Me", pdf_name="keep.pdf")
        other = self._bind_library(title="Incoming", pdf_name="new.pdf", pdf_text="incoming")
        backup = create_backup(other["cfg"])
        bind_storage(lib["cfg"])
        staged = self._stage_copy(lib["cfg"], backup.archive_path)
        journal = backup_module.journal_path(lib["cfg"])
        real_write = backup_module._journal_written_durably

        def refuse_the_commit_record(config, payload):
            durable = real_write(config, payload)
            return False if payload.get("phase") == "committed" else durable

        with patch.object(
            backup_module, "_journal_written_durably", refuse_the_commit_record
        ):
            out = apply_restore(lib["cfg"], staged.token, "RESTORE", rebind=bind_storage)

        self.assertTrue(out["restored"])
        self.assertTrue(
            any("commit record" in w for w in out["warnings"]),
            f"the weaker guarantee must be reported, got {out['warnings']}",
        )
        # Kept, not cleaned: the next startup resolves whichever phase survived.
        self.assertTrue(os.path.isfile(journal))
        with open(journal, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["phase"], "committed")
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Incoming"], "no rollback may have started")

        out = recover_incomplete_restore(lib["cfg"])

        self.assertEqual(out["outcome"], "keep_restored")
        self.assertFalse(os.path.exists(journal))
        bind_storage(lib["cfg"])
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Incoming"])

    def test_a_sidecar_rename_that_fails_leaves_the_previous_library_intact(self):
        """The database and its sidecars move as one batch, and can fail mid-way.

        The first rename is already on disk when the second raises. Rollback
        has to put the whole component back -- database and sidecars -- and the
        entries the completed rename created must have been synced on the way
        out. `rebind` is a no-op here so nothing opens SQLite and rewrites the
        WAL before the assertions can look at it.
        """
        lib = self._bind_library(title="Keep Me", pdf_name="keep.pdf")
        other = self._bind_library(title="Incoming", pdf_name="new.pdf", pdf_text="incoming")
        backup = create_backup(other["cfg"])
        bind_storage(lib["cfg"])
        wal_path = lib["cfg"].db_path + "-wal"
        with open(wal_path, "wb") as handle:
            handle.write(b"PREVIOUS-WAL")
        with open(lib["cfg"].db_path, "rb") as handle:
            db_bytes = handle.read()
        staged = self._stage_copy(lib["cfg"], backup.archive_path)

        real_replace = os.replace
        synced = []
        real_sync = fs_durability.fsync_directory

        def record_sync(path):
            synced.append(os.path.realpath(path))
            return real_sync(path)

        def fail_the_sidecar(src, dest):
            if str(src).endswith("-wal"):
                raise OSError(errno.EIO, "I/O error")
            return real_replace(src, dest)

        with patch.object(fs_durability, "fsync_directory", record_sync), \
                patch.object(backup_module.os, "replace", fail_the_sidecar):
            with self.assertRaises(RestoreError) as caught:
                apply_restore(
                    lib["cfg"], staged.token, "RESTORE", rebind=lambda cfg: cfg
                )

        self.assertEqual(caught.exception.reason, "restore_failed")
        self.assertIn(
            os.path.realpath(lib["cfg"].root),
            synced,
            "the completed database rename must have been synced on the way out",
        )
        with open(lib["cfg"].db_path, "rb") as handle:
            self.assertEqual(handle.read(), db_bytes, "the previous database is back")
        with open(wal_path, "rb") as handle:
            self.assertEqual(handle.read(), b"PREVIOUS-WAL", "its sidecar came with it")

    def test_a_restore_survives_a_symlinked_storage_root(self):
        """`PRKS_STORAGE` may legitimately be a symlink.

        Live paths keep the link in them while the maintenance tree anchors to
        the resolved root, so a whole restore exercises both namespaces.
        """
        target = self._tmpdir(prefix="prks-backup-link-target-")
        link = os.path.join(self._tmpdir(), "storage-link")
        try:
            os.symlink(target, link, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        source = self._bind_library(title="Incoming", pdf_name="new.pdf", pdf_text="incoming")
        backup = create_backup(source["cfg"])

        linked = bind_storage(StorageConfig.for_testing(link))
        os.makedirs(linked.pdfs_dir, exist_ok=True)
        os.makedirs(linked.people_dir, exist_ok=True)
        server_module.db.add_work("Keep Me", file_path="/api/pdfs/keep.pdf", source_kind="pdf")
        with open(os.path.join(linked.pdfs_dir, "keep.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4 keep\n%%EOF\n")
        staged = self._stage_copy(linked, backup.archive_path)

        out = apply_restore(linked, staged.token, "RESTORE", rebind=bind_storage)

        self.assertTrue(out["restored"])
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Incoming"])
        self.assertTrue(os.path.isfile(os.path.join(linked.pdfs_dir, "new.pdf")))
        self.assertFalse(
            os.path.exists(backup_module.journal_path(linked)),
            "a committed restore cleans its journal up",
        )

    def test_every_staged_payload_file_is_flushed_before_it_can_be_installed(self):
        """A staged file becomes canonical by rename, so it owes the same sync."""
        lib = self._bind_library(title="Incoming", pdf_name="new.pdf", pdf_text="incoming")
        backup = create_backup(lib["cfg"])
        target = self._bind_library(title="Keep Me", pdf_name="keep.pdf")
        bind_storage(target["cfg"])
        synced_inodes = set()
        real_sync = backup_module.fsync_open_file

        def record(fd):
            synced_inodes.add(os.fstat(fd).st_ino)
            return real_sync(fd)

        with patch.object(backup_module, "fsync_open_file", record):
            staged = self._stage_copy(target["cfg"], backup.archive_path)

        tree = os.path.join(
            backup_module._staging_dir(target["cfg"], staged.token), "tree"
        )
        payload = [
            os.path.join(parent, name)
            for parent, _dirs, files in os.walk(tree)
            for name in files
        ]
        self.assertTrue(payload, "the staged tree must hold the extracted payload")
        for path in payload:
            self.assertIn(
                os.stat(path).st_ino,
                synced_inodes,
                "every extracted payload file must reach stable storage",
            )

    def test_staging_refuses_a_tree_whose_directory_entries_cannot_be_synced(self):
        lib = self._bind_library(title="Incoming", pdf_name="new.pdf", pdf_text="incoming")
        backup = create_backup(lib["cfg"])
        target = self._bind_library(title="Keep Me", pdf_name="keep.pdf")
        bind_storage(target["cfg"])
        real = fs_durability.fsync_directory

        def refuse_the_staged_tree(path):
            if os.path.basename(os.path.realpath(path)) == "tree":
                return False
            return real(path)

        with patch.object(fs_durability, "fsync_directory", refuse_the_staged_tree):
            with self.assertRaises(RestoreError) as caught:
                self._stage_copy(target["cfg"], backup.archive_path)

        self.assertEqual(caught.exception.reason, "staging_not_durable")
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Keep Me"], "staging never touches the live library")


class TestDiskAccounting(BackupRestoreTestCase):
    def _file_bytes(self, cfg):
        total = backup_module._dir_size_bytes(cfg.pdfs_dir)
        total += backup_module._dir_size_bytes(cfg.people_dir)
        if backup_module.processing_is_under_storage(cfg):
            total += backup_module._dir_size_bytes(cfg.processing_dir)
        return total

    def test_backup_does_not_reserve_two_copies_of_pdf_library(self):
        blob = b"%PDF-1.4\n" + (b"X" * (2 * 1024 * 1024)) + b"\n%%EOF\n"
        lib = self._bind_library(extra_pdf_name="big.pdf", extra_pdf_bytes=blob)
        cfg = lib["cfg"]
        db_bytes = backup_module._db_on_disk_bytes(cfg)
        file_bytes = self._file_bytes(cfg)
        needed = backup_additional_bytes(db_bytes, file_bytes)
        old_needed = (db_bytes + file_bytes) * 2 + DISK_MARGIN_BYTES
        self.assertLess(needed, old_needed)
        with patch.object(backup_module, "_free_bytes", return_value=needed):
            backup = create_backup(cfg)
        self.assertTrue(os.path.isfile(backup.archive_path))

    def test_backup_rejects_when_snapshot_archive_margin_unavailable(self):
        lib = self._bind_library()
        cfg = lib["cfg"]
        needed = backup_additional_bytes(backup_module._db_on_disk_bytes(cfg), self._file_bytes(cfg))
        with patch.object(backup_module, "_free_bytes", return_value=needed - 1):
            with self.assertRaises(BackupError) as ctx:
                create_backup(cfg)
        self.assertEqual(ctx.exception.reason, "insufficient_storage")

    def test_restore_upload_space_uses_content_length_not_live_library(self):
        lib = self._bind_library()
        with patch.object(backup_module, "_free_bytes", return_value=0):
            with self.assertRaises(RestoreError) as ctx:
                require_restore_upload_space(lib["cfg"], 1024)
        self.assertEqual(ctx.exception.reason, "insufficient_storage")
        with patch.object(backup_module, "_free_bytes", return_value=1024 + DISK_MARGIN_BYTES):
            require_restore_upload_space(lib["cfg"], 1024)

    def test_extract_space_uses_declared_uncompressed_not_archive_again(self):
        lib = self._bind_library()
        backup = create_backup(lib["cfg"])
        dest = bind_storage(self._cfg(self._tmpdir()))
        copied = _copy_backup(backup.archive_path, os.path.join(self._tmpdir(), "upload"))
        with patch.object(backup_module, "_free_bytes", return_value=DISK_MARGIN_BYTES):
            with self.assertRaises(RestoreError) as ctx:
                stage_restore(dest, copied)
        self.assertEqual(ctx.exception.reason, "insufficient_storage")

    def test_commit_rename_does_not_require_live_plus_staged_copy(self):
        blob = b"%PDF-1.4\n" + (b"X" * (512 * 1024)) + b"\n%%EOF\n"
        lib = self._bind_library(title="Keep Me", pdf_name="keep.pdf")
        other = self._bind_library(
            title="Incoming",
            pdf_name="new.pdf",
            pdf_text="incoming",
            extra_pdf_name="big.pdf",
            extra_pdf_bytes=blob,
        )
        backup = create_backup(other["cfg"])
        bind_storage(lib["cfg"])
        staged = self._stage_copy(lib["cfg"], backup.archive_path)
        tree_dir = os.path.join(
            lib["cfg"].root, ".prks-maintenance", "restore-staging", staged.token, "tree"
        )
        live_size = backup_module._db_on_disk_bytes(lib["cfg"]) + self._file_bytes(lib["cfg"])
        staged_size = backup_module._dir_size_bytes(tree_dir)
        old_needed = live_size + staged_size + DISK_MARGIN_BYTES
        self.assertGreater(old_needed, DISK_MARGIN_BYTES)
        with patch.object(backup_module, "_free_bytes", return_value=DISK_MARGIN_BYTES):
            out = apply_restore(lib["cfg"], staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["Incoming"])


class TestSelfVerificationAndChunks(BackupRestoreTestCase):
    def test_invalid_hash_is_not_served(self):
        lib = self._bind_library()

        def corrupt(archive_path):
            with open(archive_path, "r+b") as handle:
                handle.seek(-8, os.SEEK_END)
                handle.write(b"XXXXXXXX")

        with self.assertRaises(BackupError):
            create_backup(lib["cfg"], post_archive_hook=corrupt)

    def test_chunked_copy_never_reads_whole_file(self):
        payload = b"A" * (IO_CHUNK_SIZE * 3 + 17)
        src = io.BytesIO(payload)
        dest = io.BytesIO()
        reads = []
        original_read = src.read

        def spy(n=-1):
            reads.append(n)
            if n is None or n < 0:
                raise AssertionError("unbounded read")
            return original_read(n)

        src.read = spy
        size, digest = hash_and_copy(src, dest, chunk_size=IO_CHUNK_SIZE)
        self.assertEqual(size, len(payload))
        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
        self.assertTrue(reads)
        self.assertTrue(all(r == IO_CHUNK_SIZE for r in reads[:-1]))
        self.assertLessEqual(max(reads), IO_CHUNK_SIZE)

    def test_iter_file_chunks_bounded(self):
        src = io.BytesIO(b"abcdef")
        chunks = list(iter_file_chunks(src, chunk_size=2))
        self.assertEqual(chunks, [b"ab", b"cd", b"ef"])

    def test_tests_never_use_production_storage(self):
        with self.assertRaises(RuntimeError):
            StorageConfig.for_testing("/data")
        repo_data = os.path.join(_PROJECT_DIR, "data")
        with self.assertRaises(RuntimeError):
            StorageConfig.for_testing(repo_data)


class TestBackupRestoreHTTP(BackupRestoreTestCase):
    def test_download_and_restore_via_http(self):
        import http.client
        import socket

        lib = self._bind_library(title="HTTP Work", pdf_text="http unique phrase")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        payload = b"{}"
        conn.request(
            "POST",
            "/api/backups/progress",
            body=payload,
            headers={
                "Host": "127.0.0.1",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        res = conn.getresponse()
        body = res.read()
        self.assertEqual(res.status, 200)
        events = [json.loads(line) for line in body.decode("utf-8").splitlines() if line.strip()]
        self.assertEqual(events[-1]["phase"], "ready")
        token = events[-1]["token"]
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        conn.request(
            "GET",
            "/api/backups/download?token=" + token,
            headers={"Host": "127.0.0.1"},
        )
        res = conn.getresponse()
        body = res.read()
        self.assertEqual(res.status, 200)
        self.assertIn(".prks-backup", res.getheader("Content-Disposition") or "")
        archive = os.path.join(self._tmpdir(), "from-http.prks-backup")
        with open(archive, "wb") as handle:
            handle.write(body)
        conn.close()

        dest = bind_storage(self._cfg(self._tmpdir()))
        staged = self._stage_copy(dest, archive)
        out = apply_restore(dest, staged.token, "RESTORE", rebind=bind_storage)
        self.assertTrue(out["restored"])
        titles = [r["title"] for r in server_module.db.execute_query("SELECT title FROM works")]
        self.assertEqual(titles, ["HTTP Work"])

    def test_progress_stream_then_token_download(self):
        import http.client
        import socket

        self._bind_library(title="HTTP Progress Work")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        payload = b"{}"
        conn.request(
            "POST",
            "/api/backups/progress",
            body=payload,
            headers={
                "Host": "127.0.0.1",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        res = conn.getresponse()
        body = res.read().decode("utf-8")
        self.assertEqual(res.status, 200)
        self.assertIn("ndjson", (res.getheader("Content-Type") or ""))
        conn.close()
        events = [json.loads(line) for line in body.splitlines() if line.strip()]
        self.assertTrue(events)
        self.assertEqual(events[-1]["phase"], "ready")
        token = events[-1]["token"]
        self.assertTrue(token)
        self.assertTrue(all("path" not in ev for ev in events))

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        conn.request(
            "GET",
            "/api/backups/download?token=" + token,
            headers={"Host": "127.0.0.1"},
        )
        res = conn.getresponse()
        blob = res.read()
        self.assertEqual(res.status, 200)
        self.assertIn(".prks-backup", res.getheader("Content-Disposition") or "")
        self.assertGreater(len(blob), 64)
        conn.close()

    def test_restore_post_requires_origin_when_present(self):
        import http.client
        import socket

        self._bind_library()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")
        payload = json.dumps({"token": "x", "confirm": "RESTORE"}).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/backups/restore",
            body=payload,
            headers={
                "Host": "127.0.0.1",
                "Origin": "http://evil.example",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        res = conn.getresponse()
        res.read()
        conn.close()
        self.assertEqual(res.status, 403)

    def test_download_without_token_does_not_create_backup(self):
        import http.client
        import socket

        self._bind_library()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")

        with patch.object(backup_module, "create_backup") as mocked:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/backups/download", headers={"Host": "127.0.0.1"})
            res = conn.getresponse()
            res.read()
            conn.close()
            self.assertEqual(res.status, 400)
            mocked.assert_not_called()

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "GET",
                "/api/backups/download?token=aaaaaaaaaaaaaaaa",
                headers={"Host": "127.0.0.1"},
            )
            res = conn.getresponse()
            res.read()
            conn.close()
            self.assertEqual(res.status, 404)
            mocked.assert_not_called()

    def test_get_progress_does_not_create_backup(self):
        import http.client
        import socket

        self._bind_library()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/backups/progress", headers={"Host": "127.0.0.1"})
        res = conn.getresponse()
        res.read()
        conn.close()
        self.assertEqual(res.status, 404)

    def test_progress_post_requires_json_content_type(self):
        import http.client
        import socket

        self._bind_library()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")
        payload = b"{}"
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/backups/progress",
            body=payload,
            headers={
                "Host": "127.0.0.1",
                "Content-Length": str(len(payload)),
            },
        )
        res = conn.getresponse()
        res.read()
        conn.close()
        self.assertEqual(res.status, 415)

    def test_stage_rejects_upload_when_content_length_exceeds_free_space(self):
        import http.client
        import socket

        self._bind_library()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        thread = threading.Thread(
            target=server_module.run_server, args=(port, "127.0.0.1"), daemon=True
        )
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request("GET", "/api/works")
                res = conn.getresponse()
                res.read()
                conn.close()
                if res.status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("server did not start")
        payload = b"not-a-zip"
        with patch.object(backup_module, "_free_bytes", return_value=0):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/api/backups/stage",
                body=payload,
                headers={
                    "Host": "127.0.0.1",
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(payload)),
                },
            )
            res = conn.getresponse()
            body = res.read()
            conn.close()
        self.assertEqual(res.status, 400)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data.get("reason"), "insufficient_storage")


if __name__ == "__main__":
    unittest.main()
