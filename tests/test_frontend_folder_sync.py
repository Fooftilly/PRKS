"""Folders: the client half, and its parity with the server."""
import pathlib
import re
import subprocess
import unittest

from backend import folder_sync, sync_protocol

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


def js_string_list(source, name):
    body = source[source.index(name):]
    body = body[: body.index(']')]
    return re.findall(r"'([a-z_]+)'", body)


class FolderSyncFrontendTests(unittest.TestCase):
    def setUp(self):
        self.store = (FRONTEND / 'local-store.js').read_text()
        self.state = (FRONTEND / 'folder-state.js').read_text()

    def test_runtime_selftests(self):
        proc = subprocess.run(
            ['node', str(ROOT / 'tests' / 'browser' / 'run_folder_sync_selftest.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('checks passed', proc.stdout)

    def test_the_two_sides_synchronize_the_same_fields(self):
        client = js_string_list(self.store, 'const FOLDER_FIELDS =')
        self.assertEqual(sorted(client), sorted(folder_sync.FIELDS))
        labels = self.state[self.state.index('const LABELS = Object.freeze({'):]
        labels = labels[: labels.index('});')]
        for field in folder_sync.FIELDS:
            with self.subTest(field=field):
                self.assertRegex(labels, r'\b%s:' % field)

    def test_every_family_is_registered_on_both_sides(self):
        families = {'CREATE_FOLDER', 'SET_FOLDER_FIELD', 'DELETE_FOLDER', 'SET_WORK_FOLDER'}
        self.assertTrue(families <= set(sync_protocol.supported_operations()))
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        diagnostics = (FRONTEND / 'sync-diagnostics.js').read_text()
        for family in families:
            with self.subTest(family=family):
                self.assertIn("'%s'" % family, self.store)
                self.assertIn('%s:' % family, runtime)
                self.assertIn(family, diagnostics)

    def test_construction_mints_a_permanent_distributed_id(self):
        at = self.store.index('function createFolder(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn("generateEntityId('F', uuid)", body)
        self.assertIn('base_revision: null', body)
        self.assertIn('is_distributed(op["entity_id"], "F")',
                      (ROOT / 'backend' / 'folder_sync.py').read_text())

    def test_moving_a_folder_is_a_field_not_a_structure(self):
        """The hierarchy is a parent pointer on ONE row, so a move changes one
        value. Modelling it as an aggregate would make an unrelated rename
        conflict with it."""
        self.assertIn('parent_id', folder_sync.FIELDS)
        self.assertEqual(folder_sync.FIELD_SCOPE_TYPE, 'folder-field')
        backend = (ROOT / 'backend' / 'folder_sync.py').read_text()
        at = backend.index('def set_field_on_conn(')
        body = backend[at: backend.index('\ndef ', at + 5)]
        self.assertIn('assert_parent_usable(conn, folder_id, desired)', body)
        # A title is unique WITHIN its parent, so a move can collide exactly as
        # a rename can -- both fields are judged together.
        self.assertIn('assert_title_free(', body)

    def test_a_works_folder_is_a_scalar_on_the_work(self):
        """A Work is in at most one folder, so filing, moving and clearing are
        one operation with different values -- not membership of a set."""
        self.assertEqual(folder_sync.WORK_SCOPE_TYPE, 'work-folder')
        at = self.store.index('function setWorkFolder(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn('base_revision: observed.revision', body)
        self.assertIn("if (desired === observed.folder_id) { setResult(null); return; }", body)
        api = (FRONTEND / 'api.js').read_text()
        # Both historical wrapper names reach the one family.
        for fn in ('async function addWorkToFolder(', 'async function patchWorkFolder('):
            at = api.index(fn)
            self.assertIn('prksFileWorkInFolder(', api[at: api.index('\n}', at)])

    def test_deletion_cancels_only_what_was_never_sent(self):
        at = self.store.index('function deleteFolder(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn('base_revision: null', body)
        self.assertIn('const neverSent =', body)
        self.assertIn('depends_on: waitFor', body)
        # A file filed INTO the folder counts as naming it: the deletion would
        # fail otherwise, because a folder holding files is protected.
        at = self.store.index('function operationsNamingFolder(')
        self.assertIn("row.operation === 'SET_WORK_FOLDER'",
                      self.store[at: self.store.index('\n    }', at)])

    def test_the_empty_only_rule_stays_canonical(self):
        backend = (ROOT / 'backend' / 'folder_sync.py').read_text()
        at = backend.index('def delete_folder_on_conn(')
        body = backend[at: backend.index('\ndef ', at + 5)]
        self.assertIn('"FOLDER_NOT_EMPTY"', body)
        self.assertIn('"FOLDER_HAS_SUBFOLDERS"', body)

    def test_the_base_is_acknowledged_and_unknown_is_not_empty(self):
        at = self.state.index('async function acknowledgedFolderBase(')
        body = self.state[at: self.state.index('\n    }', at)]
        self.assertIn('if (!state) return null', body)
        self.assertIn('catalogRowFromOp(creating)', body)
        self.assertIn('newFolderState(folderId)', body)

    def test_a_pending_deletion_is_a_tombstone(self):
        at = self.state.index('function effectiveFolders(')
        body = self.state[at: self.state.index('\n    /**', at)]
        self.assertIn('pendingDeletions(operations).forEach', body)
        # The count moves between TWO folders and this projection sees only the
        # hierarchy, so it is deliberately left as the server stated it.
        self.assertIn('work_count', body)
        self.assertNotIn('work_count: Math.max', body)

    def test_a_pending_filing_is_hydrated_with_the_other_overlays(self):
        """A card renders synchronously. An overlay that had to await the store
        could only correct itself after the first paint."""
        app = (FRONTEND / 'app.js').read_text()
        at = app.index('async function prksHydratePendingWorkMetadata(')
        body = app[at: app.index('\n}', at)]
        self.assertIn('prksRefreshPendingWorkFolders', body)
        at = app.index('function prksEffectiveWorkRows(')
        rows = app[at: app.index('\n}', at)]
        self.assertIn('prksApplyPendingWorkFolders(', rows)


if __name__ == '__main__':
    unittest.main()
