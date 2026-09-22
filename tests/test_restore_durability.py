"""The restore journal must describe filesystem state a machine crash can see.

EF-017 / #110. The restore transaction is journaled well enough to recover from
a process that dies between two system calls, but ``os.replace()`` alone does
not put a directory entry on stable storage. A power loss could therefore leave
the journal and the component it describes in different transaction phases.
Restore now follows the ``backend.fs_durability`` convention #112 introduced, so
these tests pin the *ordering* the state machine assumes:

- journal: sync the contents, replace the journal, sync the journal directory,
  and only then treat the phase as persisted;
- component: persist "rename starting", rename, sync every directory the rename
  changed -- both parents when it crosses directories -- and only then persist
  "rename completed".

Real power loss is not something CI can stage, so nothing here pretends to: the
tests spy on the calls and their order, and drive the refusal paths by making a
directory sync answer False the way an unsupported or failing one would.
"""

import errno
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from run_tests import apply_isolated_test_env

apply_isolated_test_env(_PROJECT_DIR)

from backend import fs_durability
from backend.backup_restore import RestoreError
from backend.storage.config import StorageConfig
import backend.backup_restore as backup_module


# Same shape as `secrets.token_urlsafe(16)`, which recovery validates before it
# will use a transaction id as a rollback path segment.
_TXN = "durability-txn-0123456789ab"


class _SyncSpy:
    """Records restore's durability calls in order and can refuse a sync.

    Only ``fs_durability.fsync_directory`` is patched: every directory sync
    restore makes goes through ``_fsync_dirs_under()`` and the real
    ``fsync_directories()``, so the containment check, the batching and the
    de-duplication all stay under test and each sync is recorded once.
    Directories are recorded as ``realpath`` because that is what restore
    resolves them to before opening them.
    """

    def __init__(self):
        self.events = []
        self.refuse = frozenset()  # normalized directories that answer False
        self.dir_sync_errno = None  # what a directory fsync fails with, if any
        self.file_sync_error = None
        self._real_replace = os.replace
        self._real_fsync_directory = fs_durability.fsync_directory

    def fsync_open_file(self, fd):
        if self.file_sync_error is not None:
            raise self.file_sync_error
        self.events.append(("fsync-file", None))
        fs_durability.fsync_open_file(fd)

    def fsync_directory(self, path):
        norm = os.path.realpath(path)
        self.events.append(("fsync-dir", norm))
        if norm in self.refuse:
            return False
        if self.dir_sync_errno is not None:
            # Fail the real helper the way a platform or filesystem would, so
            # its own unsupported/failed distinction is what answers -- and
            # without touching the file sync, which stays a hard requirement.
            with patch.object(fs_durability, "_FULLFSYNC", None), patch.object(
                fs_durability.os, "fsync", side_effect=OSError(self.dir_sync_errno, "x")
            ):
                return self._real_fsync_directory(path)
        return self._real_fsync_directory(path)

    def replace(self, src, dest):
        self.events.append(("replace", os.path.realpath(src), os.path.realpath(dest)))
        return self._real_replace(src, dest)

    def persist(self, state):
        """Stand-in for the journal write, recording the flags it would store."""

        def _persist():
            self.events.append(("persist", dict(state)))

        return _persist

    @property
    def steps(self):
        return [event[0] for event in self.events]

    @property
    def synced_dirs(self):
        return [event[1] for event in self.events if event[0] == "fsync-dir"]

    def persisted(self, flag):
        """The value of ``flag`` at each persist, in order."""
        return [bool(event[1].get(flag)) for event in self.events if event[0] == "persist"]

    def index_of(self, step):
        return self.steps.index(step)


class _DurabilityCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-restore-durability-")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.cfg = StorageConfig.for_testing(self.root)
        self.spy = _SyncSpy()

    def spying(self):
        """Patch every durability call restore can make."""
        return [
            patch.object(backup_module, "fsync_open_file", self.spy.fsync_open_file),
            patch.object(fs_durability, "fsync_directory", self.spy.fsync_directory),
            patch.object(backup_module.os, "replace", self.spy.replace),
        ]

    def spied(self):
        case = self

        class _Ctx:
            def __enter__(self):
                self._patches = case.spying()
                for p in self._patches:
                    p.start()
                return case.spy

            def __exit__(self, *exc):
                for p in reversed(self._patches):
                    p.stop()
                return False

        return _Ctx()

    def write(self, path, data=b"x"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read()


class TestJournalWriteOrdering(_DurabilityCase):
    """file sync -> replace journal -> directory sync -> phase persisted."""

    def _journal(self, phase="moving_old"):
        return {
            "format": "prks-restore-journal",
            "format_version": 1,
            "transaction_id": _TXN,
            "phase": phase,
            "components": {},
            "staging_token": None,
        }

    def test_contents_are_synced_before_the_replace_and_the_directory_after(self):
        with self.spied() as spy:
            backup_module._write_journal(self.cfg, self._journal())

        self.assertEqual(
            spy.steps,
            ["fsync-file", "replace", "fsync-dir"],
            "the journal bytes must be on stable storage before the name points "
            "at them, and the directory entry after it does",
        )
        journal = backup_module.journal_path(self.cfg)
        self.assertEqual(spy.synced_dirs, [os.path.realpath(os.path.dirname(journal))])
        self.assertEqual(json.loads(self.read(journal))["phase"], "moving_old")

    def test_a_same_directory_replace_costs_exactly_one_directory_sync(self):
        """The temporary is a sibling: one entry changed, in one directory."""
        with self.spied() as spy:
            backup_module._write_journal(self.cfg, self._journal())

        replace = [event for event in spy.events if event[0] == "replace"][0]
        self.assertEqual(os.path.dirname(replace[1]), os.path.dirname(replace[2]))
        self.assertEqual(len(spy.synced_dirs), 1)

    def test_the_phase_is_refused_when_the_journal_directory_cannot_be_synced(self):
        journal = backup_module.journal_path(self.cfg)
        backup_module._write_journal(self.cfg, self._journal("prepared"))
        self.spy.refuse = frozenset({os.path.realpath(os.path.dirname(journal))})

        with self.spied():
            with self.assertRaises(RestoreError) as caught:
                backup_module._write_journal(self.cfg, self._journal("moving_old"))

        self.assertEqual(caught.exception.reason, "journal_not_durable")
        self.assertEqual(caught.exception.http_status, 500)
        # The replace itself happened -- what is refused is the claim that the
        # phase is readable after a crash, not the write.
        self.assertEqual(json.loads(self.read(journal))["phase"], "moving_old")

    def test_the_journal_is_not_replaced_when_its_contents_cannot_be_synced(self):
        journal = backup_module.journal_path(self.cfg)
        backup_module._write_journal(self.cfg, self._journal("prepared"))
        self.spy.file_sync_error = OSError(errno.EIO, "I/O error")

        with self.spied() as spy:
            with self.assertRaises(OSError):
                backup_module._write_journal(self.cfg, self._journal("moving_old"))

        self.assertNotIn("replace", spy.steps)
        self.assertEqual(json.loads(self.read(journal))["phase"], "prepared")

    def test_a_directory_fsync_the_platform_lacks_still_counts_as_persisted(self):
        """Unsupported is not refused: there is nothing stronger to ask for."""
        self.spy.dir_sync_errno = errno.ENOSYS

        with self.spied() as spy:
            backup_module._write_journal(self.cfg, self._journal())

        self.assertEqual(spy.steps, ["fsync-file", "replace", "fsync-dir"])
        self.assertTrue(os.path.isfile(backup_module.journal_path(self.cfg)))

    def test_a_directory_fsync_that_fails_is_refused(self):
        """The same call, an error the platform does implement: not durable."""
        self.spy.dir_sync_errno = errno.EIO

        with self.spied():
            with self.assertRaises(RestoreError) as caught:
                backup_module._write_journal(self.cfg, self._journal())

        self.assertEqual(caught.exception.reason, "journal_not_durable")

    def test_staging_metadata_reports_the_weaker_guarantee_without_raising(self):
        """Only the journal treats this write as a boundary; staging does not."""
        meta = os.path.join(self.root, "staging", "meta.json")
        self.spy.refuse = frozenset({os.path.realpath(os.path.dirname(meta))})

        with self.spied():
            durable = backup_module._atomic_write_json(
                meta, {"token": "abc"}, root=self.root
            )

        self.assertFalse(durable)
        self.assertEqual(json.loads(self.read(meta)), {"token": "abc"})


class TestComponentMoveOrdering(_DurabilityCase):
    """persist "starting" -> rename -> directory sync(s) -> persist "completed"."""

    def setUp(self):
        super().setUp()
        self.rollback_root = backup_module._rollback_dir(self.cfg, _TXN)
        os.makedirs(self.rollback_root, exist_ok=True)
        self.state = backup_module._empty_component_state()

    def _move(self, name):
        return backup_module._move_old_component(
            self.cfg,
            name,
            self.rollback_root,
            self.state,
            self.spy.persist(self.state),
            None,
        )

    def test_the_rename_is_durable_before_the_journal_calls_the_move_complete(self):
        os.makedirs(self.cfg.pdfs_dir, exist_ok=True)
        self.write(os.path.join(self.cfg.pdfs_dir, "old.pdf"), b"OLD")

        with self.spied() as spy:
            self._move("pdfs")

        self.assertEqual(
            spy.steps,
            ["persist", "persist", "replace", "fsync-dir", "fsync-dir", "persist"],
            "nothing may be persisted between the rename and its syncs",
        )
        self.assertEqual(
            spy.persisted("old_moved"),
            [False, False, True],
            "only the persist after the directory syncs claims the move completed",
        )
        self.assertEqual(spy.persisted("old_move_started"), [False, True, True])

    def test_a_move_across_directories_syncs_the_source_and_the_destination(self):
        os.makedirs(self.cfg.pdfs_dir, exist_ok=True)

        with self.spied() as spy:
            self._move("pdfs")

        self.assertEqual(
            sorted(spy.synced_dirs),
            sorted({os.path.realpath(self.cfg.root), os.path.realpath(self.rollback_root)}),
            "the name leaves one directory and appears in another; both entries "
            "have to survive the crash",
        )

    def test_a_database_and_its_sidecars_share_one_sync_per_directory(self):
        self.write(self.cfg.db_path, b"DB")
        self.write(self.cfg.db_path + "-wal", b"WAL")
        self.write(self.cfg.db_path + "-shm", b"SHM")

        with self.spied() as spy:
            self._move("database")

        self.assertEqual(spy.steps.count("replace"), 3)
        # The rollback subdirectory is created and synced before the moves;
        # what follows them is one fsync per directory the moves changed.
        after_moves = [
            event[1]
            for event in spy.events[spy.index_of("replace") :]
            if event[0] == "fsync-dir"
        ]
        self.assertEqual(
            len(after_moves),
            2,
            "one fsync per directory covers every entry that changed in it",
        )
        self.assertEqual(len(set(after_moves)), 2)
        rolled = os.path.dirname(
            backup_module._rollback_component_path(self.cfg, self.rollback_root, "database")
        )
        self.assertEqual(self.read(os.path.join(rolled, "prks_data.db-wal")), b"WAL")

    def test_a_refused_sync_stops_before_the_journal_claims_the_move_completed(self):
        os.makedirs(self.cfg.pdfs_dir, exist_ok=True)
        self.write(os.path.join(self.cfg.pdfs_dir, "old.pdf"), b"OLD")
        self.spy.refuse = frozenset({os.path.realpath(self.rollback_root)})

        with self.spied() as spy:
            with self.assertRaises(RestoreError) as caught:
                self._move("pdfs")

        self.assertEqual(caught.exception.reason, "rename_not_durable")
        self.assertFalse(self.state["old_moved"])
        self.assertEqual(spy.persisted("old_moved"), [False, False])
        # Fail-safe: the previous component is in the rollback tree, which is
        # exactly what rollback needs to put it back.
        rolled = backup_module._rollback_component_path(self.cfg, self.rollback_root, "pdfs")
        self.assertEqual(self.read(os.path.join(rolled, "old.pdf")), b"OLD")

    def test_the_rollback_directory_is_durable_before_anything_moves_into_it(self):
        self.write(self.cfg.db_path, b"DB")
        rolled_dir = os.path.join(self.rollback_root, "database")
        self.spy.refuse = frozenset({os.path.realpath(self.rollback_root)})

        with self.spied() as spy:
            with self.assertRaises(RestoreError) as caught:
                self._move("database")

        self.assertEqual(
            caught.exception.reason,
            "restore_dir_not_durable",
            "a destination a crash could take away is refused before the move",
        )
        self.assertNotIn("replace", spy.steps)
        self.assertEqual(spy.synced_dirs, [os.path.realpath(self.rollback_root)])
        self.assertEqual(self.read(self.cfg.db_path), b"DB")
        self.assertTrue(os.path.isdir(rolled_dir))


class TestComponentInstallOrdering(_DurabilityCase):
    """The staged tree moves into live storage under the same rule."""

    def setUp(self):
        super().setUp()
        self.tree_dir = os.path.join(self.root, ".prks-maintenance", "staging", "t", "tree")
        self.state = backup_module._empty_component_state()

    def _install(self, name):
        return backup_module._install_new_component(
            self.cfg,
            name,
            self.tree_dir,
            self.state,
            self.spy.persist(self.state),
            None,
            processing_in_backup=False,
        )

    def _stage_pdfs(self):
        staged = backup_module._component_staged_path(self.tree_dir, "pdfs")
        self.write(os.path.join(staged, "new.pdf"), b"NEW")
        return staged

    def test_the_install_is_durable_before_the_journal_calls_it_installed(self):
        staged = self._stage_pdfs()

        with self.spied() as spy:
            self._install("pdfs")

        self.assertEqual(
            spy.steps,
            ["persist", "replace", "fsync-dir", "fsync-dir", "persist"],
            "the installed entry is synced before the journal records it",
        )
        self.assertEqual(spy.persisted("new_install_started"), [True, True])
        self.assertEqual(spy.persisted("new_installed"), [False, True])
        self.assertEqual(
            sorted(spy.synced_dirs),
            sorted({os.path.realpath(os.path.dirname(staged)), os.path.realpath(self.cfg.root)}),
            "the staging tree and live storage are different parents",
        )
        self.assertEqual(self.read(os.path.join(self.cfg.pdfs_dir, "new.pdf")), b"NEW")

    def test_a_refused_sync_stops_before_the_journal_claims_the_install(self):
        self._stage_pdfs()
        self.spy.refuse = frozenset({os.path.realpath(self.cfg.root)})

        with self.spied():
            with self.assertRaises(RestoreError) as caught:
                self._install("pdfs")

        self.assertEqual(caught.exception.reason, "rename_not_durable")
        self.assertFalse(self.state["new_installed"])
        # `new_install_started` was persisted first, so rollback still knows to
        # remove what is now live and put the previous component back.
        self.assertTrue(self.state["new_install_started"])
        self.assertTrue(os.path.isfile(os.path.join(self.cfg.pdfs_dir, "new.pdf")))

    def test_an_empty_component_directory_is_durable_before_it_is_recorded(self):
        with self.spied() as spy:
            self._install("pdfs")

        self.assertEqual(spy.steps, ["persist", "fsync-dir", "persist"])
        self.assertEqual(spy.persisted("new_installed"), [False, True])
        self.assertTrue(os.path.isdir(self.cfg.pdfs_dir))

    def test_a_refused_sync_leaves_an_empty_directory_unrecorded(self):
        self.spy.refuse = frozenset({os.path.realpath(self.cfg.root)})

        with self.spied():
            with self.assertRaises(RestoreError) as caught:
                self._install("people")

        self.assertEqual(caught.exception.reason, "rename_not_durable")
        self.assertFalse(self.state["new_installed"])


class TestRecoveryDurability(_DurabilityCase):
    """Recovery owns the same boundary in the opposite direction."""

    def _crashed_restore(self):
        """A pre-committed journal whose rollback tree still holds the library."""
        rollback_root = backup_module._rollback_dir(self.cfg, _TXN)
        self.write(
            os.path.join(rollback_root, "database", "prks_data.db"), b"PREVIOUS-DB"
        )
        self.write(os.path.join(rollback_root, "pdfs", "old.pdf"), b"OLD")
        self.write(self.cfg.db_path, b"RESTORED-DB")
        self.write(os.path.join(self.cfg.pdfs_dir, "new.pdf"), b"NEW")
        moved = {
            "old_existed": True,
            "old_move_started": True,
            "old_moved": True,
            "new_install_started": True,
            "new_installed": True,
        }
        journal = {
            "format": "prks-restore-journal",
            "format_version": 1,
            "transaction_id": _TXN,
            "phase": "installing_new",
            "components": {"database": dict(moved), "pdfs": dict(moved)},
        }
        path = backup_module.journal_path(self.cfg)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(journal, handle)
        return rollback_root, path

    def _assert_previous_library(self):
        self.assertEqual(self.read(self.cfg.db_path), b"PREVIOUS-DB")
        self.assertEqual(self.read(os.path.join(self.cfg.pdfs_dir, "old.pdf")), b"OLD")
        self.assertFalse(os.path.exists(os.path.join(self.cfg.pdfs_dir, "new.pdf")))

    def test_putting_the_previous_library_back_syncs_the_directories_it_changed(self):
        rollback_root, journal = self._crashed_restore()

        with self.spied() as spy:
            outcome = backup_module.recover_incomplete_restore(self.cfg)

        self.assertEqual(outcome["outcome"], "restored_previous")
        self._assert_previous_library()
        self.assertFalse(os.path.exists(journal))
        self.assertIn(os.path.realpath(self.cfg.root), spy.synced_dirs)
        self.assertIn(os.path.realpath(rollback_root), spy.synced_dirs)
        last_replace = len(spy.steps) - 1 - spy.steps[::-1].index("replace")
        self.assertGreater(
            len(spy.steps) - 1 - spy.steps[::-1].index("fsync-dir"),
            last_replace,
            "the last move is synced before recovery finishes",
        )

    def test_a_refused_sync_keeps_the_journal_and_the_rollback_tree(self):
        rollback_root, journal = self._crashed_restore()
        self.spy.refuse = frozenset({os.path.realpath(self.cfg.root)})

        with self.spied():
            with self.assertRaises(RestoreError) as caught:
                backup_module.recover_incomplete_restore(self.cfg)

        self.assertEqual(caught.exception.reason, "rollback_not_durable")
        # The previous library is back, but nothing that would make a replay
        # impossible may happen while a crash could still undo it.
        self._assert_previous_library()
        self.assertTrue(os.path.isfile(journal))
        self.assertTrue(os.path.isdir(rollback_root))

    def test_a_later_pass_finishes_recovery_once_the_sync_succeeds(self):
        _rollback_root, journal = self._crashed_restore()
        self.spy.refuse = frozenset({os.path.realpath(self.cfg.root)})
        with self.spied():
            with self.assertRaises(RestoreError):
                backup_module.recover_incomplete_restore(self.cfg)

        outcome = backup_module.recover_incomplete_restore(self.cfg)

        self.assertEqual(outcome["outcome"], "restored_previous")
        self._assert_previous_library()
        self.assertFalse(os.path.exists(journal))

    def test_the_replay_confirms_the_entry_the_refused_pass_left_unsynced(self):
        """The pass that cleans up is the last chance to confirm the move.

        An earlier pass consumed the rollback copy and had its syncs refused,
        so the live entry it created is unconfirmed while the journal and the
        rollback tree -- the only way to redo it -- are about to be removed.
        """
        _rollback_root, journal = self._crashed_restore()
        self.spy.refuse = frozenset({os.path.realpath(self.cfg.root)})
        with self.spied():
            with self.assertRaises(RestoreError):
                backup_module.recover_incomplete_restore(self.cfg)

        self.spy.refuse = frozenset()
        self.spy.events.clear()
        with self.spied() as spy:
            outcome = backup_module.recover_incomplete_restore(self.cfg)

        self.assertEqual(outcome["outcome"], "restored_previous")
        self.assertIn(
            os.path.realpath(self.cfg.root),
            spy.synced_dirs,
            "the live entry the refused pass created is confirmed before cleanup",
        )
        self.assertFalse(os.path.exists(journal))
        self._assert_previous_library()

    def test_replaying_a_consumed_rollback_tree_still_keeps_the_live_library(self):
        """The refused pass must not have made the replay destructive."""
        _rollback_root, journal = self._crashed_restore()
        self.spy.refuse = frozenset({os.path.realpath(self.cfg.root)})
        with self.spied():
            with self.assertRaises(RestoreError):
                backup_module.recover_incomplete_restore(self.cfg)
        # Whatever a second crash did, the journal is still there to replay.
        self.assertTrue(os.path.isfile(journal))

        backup_module.recover_incomplete_restore(self.cfg)

        self._assert_previous_library()


class TestSyncContainment(_DurabilityCase):
    """A restore fsync never reaches outside the tree it belongs to.

    The directories restore syncs are built from a request's staging token and
    from a journal's transaction id, so containment is checked on the value
    that is opened, not on the inputs it came from.
    """

    def test_a_directory_inside_the_root_is_synced(self):
        os.makedirs(self.cfg.pdfs_dir, exist_ok=True)

        with self.spied() as spy:
            self.assertTrue(
                backup_module._fsync_dirs_under(self.root, [self.cfg.pdfs_dir])
            )

        self.assertEqual(spy.synced_dirs, [os.path.realpath(self.cfg.pdfs_dir)])

    def test_the_root_itself_is_synced(self):
        with self.spied() as spy:
            self.assertTrue(backup_module._fsync_dirs_under(self.root, [self.root]))

        self.assertEqual(spy.synced_dirs, [os.path.realpath(self.root)])

    def test_a_directory_outside_the_root_is_refused_and_never_opened(self):
        outside = tempfile.mkdtemp(prefix="prks-restore-durability-outside-")
        self.addCleanup(lambda: os.rmdir(outside))

        with self.spied() as spy:
            self.assertFalse(backup_module._fsync_dirs_under(self.root, [outside]))

        self.assertEqual(spy.synced_dirs, [], "nothing outside the root is opened")

    def test_a_move_reaching_outside_the_root_is_refused_before_it_happens(self):
        """The refusal comes first, so it is the "nothing moved" reason."""
        outside = tempfile.mkdtemp(prefix="prks-restore-durability-outside-")
        self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
        src = self.write(os.path.join(self.root, "pdfs", "old.pdf"), b"OLD")
        escaping = os.path.join(outside, "old.pdf")

        with self.spied() as spy:
            with self.assertRaises(RestoreError) as caught:
                backup_module._replace_durably(self.root, [(src, escaping)])

        self.assertEqual(caught.exception.reason, "restore_dir_not_durable")
        self.assertNotIn("replace", spy.steps, "nothing may be renamed")
        self.assertEqual(self.read(src), b"OLD")

    def test_a_sibling_whose_name_merely_extends_the_root_is_refused(self):
        sibling = self.root + "-elsewhere"
        os.makedirs(sibling)
        self.addCleanup(lambda: os.rmdir(sibling))

        with self.spied() as spy:
            self.assertFalse(backup_module._fsync_dirs_under(self.root, [sibling]))

        self.assertEqual(spy.synced_dirs, [])


class TestDurabilityPrimitives(unittest.TestCase):
    """The shared helpers #110 added to the #129 convention."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-fs-durability-")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.left = os.path.join(self.root, "left")
        self.right = os.path.join(self.root, "right")
        os.makedirs(self.left)
        os.makedirs(self.right)
        # macOS reaches for F_FULLFSYNC, so the os.fsync spies below would see
        # nothing there. Pin the plain-fsync platform the assertions describe.
        patcher = patch.object(fs_durability, "_FULLFSYNC", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_rename_inside_one_directory_is_synced_once(self):
        with patch.object(fs_durability, "fsync_directory", return_value=True) as sync:
            self.assertTrue(fs_durability.fsync_directories(self.left, self.left + "/"))

        self.assertEqual(sync.call_count, 1)

    def test_a_rename_across_directories_syncs_both_parents(self):
        with patch.object(fs_durability, "fsync_directory", return_value=True) as sync:
            self.assertTrue(fs_durability.fsync_directories(self.left, self.right))

        self.assertEqual(
            [call.args[0] for call in sync.call_args_list], [self.left, self.right]
        )

    def test_every_directory_is_attempted_even_after_one_refuses(self):
        attempted = []

        def refuse_left(path):
            attempted.append(path)
            return path != self.left

        with patch.object(fs_durability, "fsync_directory", refuse_left):
            self.assertFalse(fs_durability.fsync_directories(self.left, self.right))

        self.assertEqual(attempted, [self.left, self.right])

    def test_a_directory_fsync_the_platform_lacks_is_not_a_failure(self):
        for code in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP):
            with self.subTest(code=code):
                with patch.object(
                    fs_durability.os, "fsync", side_effect=OSError(code, "nope")
                ):
                    self.assertTrue(
                        fs_durability.fsync_directories(self.left, self.right)
                    )

    def test_a_refused_directory_fsync_is_reported(self):
        with patch.object(
            fs_durability.os, "fsync", side_effect=OSError(errno.EPERM, "denied")
        ):
            self.assertFalse(fs_durability.fsync_directories(self.left))

    def test_an_open_file_is_flushed_through_the_platform_barrier(self):
        path = os.path.join(self.left, "note.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("x")
            handle.flush()
            with patch.object(fs_durability.os, "fsync") as fsync:
                fs_durability.fsync_open_file(handle.fileno())
        self.assertEqual(fsync.call_count, 1)

    def test_an_open_file_that_cannot_be_flushed_raises(self):
        path = os.path.join(self.left, "note.txt")
        with open(path, "w", encoding="utf-8") as handle:
            with patch.object(
                fs_durability.os, "fsync", side_effect=OSError(errno.EIO, "I/O error")
            ):
                with self.assertRaises(OSError):
                    fs_durability.fsync_open_file(handle.fileno())


if __name__ == "__main__":
    unittest.main()
