"""The Tag vocabulary: the client half, and its parity with the server."""
import pathlib
import subprocess
import unittest

from backend import sync_protocol

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


class TagVocabularyFrontendTests(unittest.TestCase):
    def setUp(self):
        self.store = (FRONTEND / 'local-store.js').read_text()
        self.state = (FRONTEND / 'tag-vocabulary-state.js').read_text()

    def test_runtime_selftests(self):
        proc = subprocess.run(
            ['node', str(ROOT / 'tests' / 'browser' / 'run_tag_vocabulary_selftest.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('checks passed', proc.stdout)

    def test_all_vocabulary_families_are_registered_everywhere(self):
        families = {'CREATE_TAG', 'DELETE_TAG', 'MERGE_TAG'}
        self.assertTrue(families <= set(sync_protocol.supported_operations()))
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        diagnostics = (FRONTEND / 'sync-diagnostics.js').read_text()
        for family in families:
            with self.subTest(family=family):
                self.assertIn("'%s'" % family, self.store)
                self.assertIn('%s:' % family, runtime)
                self.assertIn(family, diagnostics)

    def test_the_vocabulary_has_no_field_editor(self):
        """PRKS has no rename and no colour editor. A field family would be
        inventing product semantics rather than moving existing ones off the
        network -- the gap is recorded, not guessed at."""
        self.assertNotIn('SET_TAG_FIELD', self.store)
        self.assertNotIn('RENAME_TAG', self.store)
        status = (ROOT / 'docs' / 'local-first-rollout-status.md').read_text()
        self.assertIn('rename', status.lower())

    def test_construction_mints_a_permanent_distributed_id(self):
        at = self.store.index('function createTag(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn("generateEntityId('T', uuid)", body)
        self.assertIn('base_revision: null', body)
        self.assertIn('is_distributed(op["entity_id"], "T")',
                      (ROOT / 'backend' / 'tag_sync.py').read_text())

    def test_a_name_collision_is_refused_and_never_redirected(self):
        """Operations already queued behind the creation name the id this
        device minted. Silently pointing them at a different Tag is exactly
        what the Work-Tag family refuses to do for a merged Tag."""
        backend = (ROOT / 'backend' / 'tag_sync.py').read_text()
        at = backend.index('def apply_create(')
        body = backend[at: backend.index('\ndef ', at + 5)]
        self.assertIn('"NAME_TAKEN"', body)
        self.assertIn('target_tag_id', body, 'the client is told what it collided with')
        self.assertNotIn('insert_tag_on_conn(conn, taken', body)
        # And the client refuses an obvious local collision early -- a better
        # error sooner, never the authoritative answer.
        at = self.store.index('function createTag(')
        self.assertIn("'name_taken'", self.store[at: self.store.index('\n        /**', at)])

    def test_an_attachment_waits_for_the_tag_it_names(self):
        at = self.store.index('function coalesceWorkTag(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn('tagCreationDependency(', body)
        self.assertIn('depends_on: createOp ? [createOp.op_id] : []', body)
        self.assertIn('assertTagIsNotBeingDeleted(', body)
        self.assertIn('assertTagIsNotBeingMerged(', body)

    def test_deletion_cancels_only_what_was_never_sent(self):
        at = self.store.index('function deleteTag(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn('base_revision: null', body)
        self.assertIn('const neverSent =', body)
        self.assertIn('depends_on: waitFor', body)

    def test_merge_refuses_while_the_source_is_still_named(self):
        at = self.store.index('function mergeTag(')
        body = self.store[at: self.store.index('\n        function coalesceWorkTag(', at)]
        self.assertIn('base_revision: null', body)
        self.assertIn('operationsNamingTag(', body)
        self.assertIn("'scope_busy'", body)
        self.assertIn('assertTagIsNotBeingMerged(', body)

    def test_a_pending_deletion_is_a_tombstone(self):
        at = self.state.index('function effectiveTagCatalogue(')
        body = self.state[at: self.state.index('\n    /**', at)]
        self.assertIn('pendingDeletions(operations).forEach', body)
        self.assertIn('pendingMergedSources(operations).forEach', body)
        # And the chips that displayed it go too: the relationship rows are
        # still cached, and the chip would show a Tag already removed.
        self.assertIn('function effectiveTagChips(', self.state)

    def test_the_catalogue_every_picker_reads_is_the_effective_one(self):
        """A Tag created here that the picker could not offer would make
        offline creation useless the moment it succeeded."""
        work_tags = (FRONTEND / 'work-tag-state.js').read_text()
        at = work_tags.index('async function readTags(')
        body = work_tags[at: work_tags.index('\n    async function readOptions(', at)]
        self.assertIn('prksEffectiveTagCatalogue(', body)

    def test_the_durable_path_is_the_only_one_the_ui_uses(self):
        ui = (FRONTEND / 'ui.js').read_text()
        tags = (FRONTEND / 'components' / 'tags.js').read_text()
        api = (FRONTEND / 'api.js').read_text()
        self.assertNotIn("prksRequest('/api/tags', {", ui,
                         'creating a Tag goes through the durable store')
        at = ui.index('async function prksSubmitNewTag(')
        body = ui[at: ui.index('\n}', at)]
        self.assertIn('prksCreateTagDurably(', body)
        self.assertNotIn('prksOfflineGuardMutation', body)
        self.assertIn('prksDeleteTagDurably(', tags)
        self.assertNotIn('async function deleteTag(', api)
        merge = api[api.index('async function mergeTags('): api.index('\nasync function',
            api.index('async function mergeTags(') + 5)]
        self.assertIn('prksMergeTagDurably(', merge)
        self.assertNotIn('prksGuardFolderMutation(', merge)
        self.assertNotIn("'/api/tags/merge'", merge)


if __name__ == '__main__':
    unittest.main()
