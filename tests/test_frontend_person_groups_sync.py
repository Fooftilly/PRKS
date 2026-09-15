"""Person Groups: the client half, and its parity with the server."""
import pathlib
import re
import subprocess
import unittest

from backend import person_group_sync

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


def js_string_list(source, name):
    body = source[source.index(name):]
    body = body[: body.index(']')]
    return re.findall(r"'([a-z_]+)'", body)


class PersonGroupSyncFrontendTests(unittest.TestCase):
    def setUp(self):
        self.store = (FRONTEND / 'local-store.js').read_text()
        self.state = (FRONTEND / 'person-group-state.js').read_text()

    def test_runtime_selftests(self):
        proc = subprocess.run(
            ['node', str(ROOT / 'tests' / 'browser' / 'run_person_group_sync_selftest.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('checks passed', proc.stdout)

    def test_the_two_sides_synchronize_the_same_fields(self):
        """A field one side can carry and the other cannot is a split contract:
        the same edit would be savable through one path and impossible through
        the other."""
        client = js_string_list(self.store, 'const PERSON_GROUP_FIELDS =')
        self.assertEqual(sorted(client), sorted(person_group_sync.FIELDS))
        labels = self.state[self.state.index('const LABELS = Object.freeze({'):]
        labels = labels[: labels.index('});')]
        for field in person_group_sync.FIELDS:
            with self.subTest(field=field):
                self.assertRegex(labels, r'\b%s:' % field)

    def test_every_family_is_registered_on_both_sides(self):
        from backend import sync_protocol
        families = {'CREATE_PERSON_GROUP', 'SET_PERSON_GROUP_FIELD',
                    'ADD_PERSON_GROUP_MEMBER', 'REMOVE_PERSON_GROUP_MEMBER',
                    'DELETE_PERSON_GROUP'}
        self.assertTrue(families <= set(sync_protocol.supported_operations()))
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        diagnostics = (FRONTEND / 'sync-diagnostics.js').read_text()
        for family in families:
            with self.subTest(family=family):
                # A family the store can write but the coordinator cannot send
                # is an operation that queues forever.
                self.assertIn("'%s'" % family, self.store)
                self.assertIn('%s:' % family, runtime)
                # ... and one Diagnostics cannot describe is a change the user
                # can neither understand nor decide about.
                self.assertIn(family, diagnostics)

    def test_construction_mints_a_permanent_distributed_id(self):
        """No temporary local id is ever remapped to a server id: the id in the
        envelope is the id SQLite stores."""
        at = self.store.index('function createPersonGroup(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn("generateEntityId('PG', uuid)", body)
        self.assertIn('base_revision: null', body)
        self.assertIn('is_distributed(op["entity_id"], "PG")',
                      (ROOT / 'backend' / 'person_group_sync.py').read_text())

    def test_deletion_addresses_an_identity_and_carries_no_revision(self):
        """Absence is idempotent -- there is no second state for two devices to
        disagree about -- so a manufactured revision conflict would be a
        question with no answer."""
        at = self.store.index('function deletePersonGroup(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn('base_revision: null', body)
        backend = (ROOT / 'backend' / 'person_group_sync.py').read_text()
        at = backend.index('def validate_delete(')
        self.assertIn('if op["base_revision"] is not None',
                      backend[at: backend.index('\ndef ', at + 5)])

    def test_deletion_cancels_only_what_was_never_sent(self):
        """An envelope that may be on the wire stays immutable -- rewriting one
        is the one way to apply it twice -- so the deletion queues behind it
        instead."""
        at = self.store.index('function deletePersonGroup(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn('const neverSent =', body)
        self.assertIn('waitFor.push(row.op_id)', body)
        self.assertIn('depends_on: waitFor', body)

    def test_a_group_being_deleted_accepts_nothing_else(self):
        """The only possible outcome would be ENTITY_NOT_FOUND -- born
        unsendable, and better refused in the terms the user was working in."""
        at = self.store.index('function assertGroupIsNotBeingDeleted(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn("'entity_deleted'", body)
        for writer in ('function savePersonGroupFields(', 'function setPersonGroupMember('):
            at = self.store.index(writer)
            with self.subTest(writer=writer):
                self.assertIn('assertGroupIsNotBeingDeleted(',
                              self.store[at: self.store.index('\n        /**', at)])

    def test_membership_is_a_pair_and_never_a_replacement(self):
        """Two devices that added different people to one group have not
        collided. A profile-wide replacement would say they had."""
        at = self.store.index('function setPersonGroupMember(')
        body = self.store[at: self.store.index('\n        /* A group already', at)]
        self.assertIn('r.payload.person_id === personId', body)
        self.assertIn('base_revision: observed.revision', body)
        self.assertEqual(person_group_sync.MEMBER_SCOPE_TYPE, 'person-group-member')

    def test_a_terminal_result_stays_inside_the_stores_vocabulary(self):
        """The durable result is a CLOSED vocabulary the store validates. A
        free-text server message could not be stored at all -- the coordinator
        would read the failure as a failed sync and retry it forever."""
        allowed = set(js_string_list(self.store, 'const STRUCTURED_RESULT_KEYS ='))
        used = set(re.findall(r'out\.([a-z_]+) =', self.state))
        self.assertTrue(used <= allowed, used - allowed)
        self.assertNotIn('message', used)

    def test_diagnostics_names_every_refusal_this_family_can_produce(self):
        """G8: "Needs a decision" with no reason is the stranded state the
        dependency work exists to prevent."""
        diagnostics = (FRONTEND / 'sync-diagnostics.js').read_text()
        named = diagnostics[diagnostics.index('const NAMED_REFUSALS'):]
        named = named[: named.index('});')]
        for code in ('NAME_TAKEN', 'PARENT_NOT_FOUND', 'PARENT_CYCLE', 'PERSON_NOT_FOUND'):
            with self.subTest(code=code):
                self.assertIn(code + ':', named)

    def test_the_durable_path_is_the_only_one_the_ui_uses(self):
        """Online and offline are the same feature or they are two features."""
        groups = (FRONTEND / 'components' / 'people-groups.js').read_text()
        # Not one connectivity guard left in the Group component: creating,
        # renaming, moving, deleting and membership are all durable now.
        self.assertNotIn('prksOfflineGuardMutation', groups)
        self.assertNotIn('prksPersonGroupMutationBlocked', groups)
        people = (FRONTEND / 'components' / 'people.js').read_text()
        at = people.index('async function prksSavePersonGroupMemberships(')
        membership = people[at: people.index('\n}', at)]
        self.assertNotIn('prksPersonMutationBlocked', membership)
        self.assertNotIn("prksPersonRuntimeState() !== 'online'", membership)
        # Deleting a Person is the one Person mutation still server-bound, and
        # keeps its guard deliberately.
        at = people.index('async function deletePerson(')
        self.assertIn('prksPersonMutationBlocked(', people[at: at + 3000])
        # Save re-renders from the intent just written; a refetch as the
        # completion condition would make Save fail offline for a reason the
        # user cannot act on.
        self.assertIn('prksRerenderPersonGroupDetail(', groups)

    def test_the_base_is_acknowledged_and_unknown_is_not_empty(self):
        at = self.state.index('async function acknowledgedGroupBase(')
        body = self.state[at: self.state.index('\n    }', at)]
        self.assertIn('if (!state) return null', body)
        self.assertIn('catalogRowFromOp(creating)', body,
                      'a group created here is its own construction base')
        self.assertIn('newGroupState(groupId)', body)

    def test_a_pending_deletion_is_a_tombstone(self):
        """Nothing is destroyed locally: a refusal restores the group by doing
        nothing at all."""
        at = self.state.index('function effectivePersonGroups(')
        body = self.state[at: self.state.index('\n    /**', at)]
        self.assertIn('out.filter(row => !deleted.has(row.id))', body)
        self.assertIn('parent_id: parent', body, 'children reparent, never orphan')


if __name__ == '__main__':
    unittest.main()
