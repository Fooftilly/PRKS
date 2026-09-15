"""Person profile editing: the client half, and its parity with the server."""
import pathlib
import re
import subprocess
import unittest

from backend import person_metadata_sync, person_sync

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


def js_string_list(source, name):
    body = source[source.index(name):]
    body = body[: body.index(']')]
    return re.findall(r"'([a-z_]+)'", body)


class PersonMetadataSyncFrontendTests(unittest.TestCase):
    def test_runtime_selftests(self):
        proc = subprocess.run(
            ['node', str(ROOT / 'tests' / 'browser' / 'run_person_metadata_sync_selftest.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('checks passed', proc.stdout)

    def test_the_two_sides_synchronize_the_same_fields(self):
        """A field one side can carry and the other cannot is a split contract:
        the same edit would be savable through one path and impossible through
        the other."""
        client = js_string_list(
            (FRONTEND / 'person-metadata-state.js').read_text(), 'const FIELDS =')
        self.assertEqual(sorted(client), sorted(person_metadata_sync.SYNCED_FIELDS))
        self.assertEqual(sorted(client), sorted(person_sync.FIELDS),
                         'and it is the creation family\'s vocabulary, not a second one')
        editor = js_string_list(
            (FRONTEND / 'components' / 'people.js').read_text(),
            'const PRKS_PERSON_PROFILE_FIELDS =')
        self.assertEqual(sorted(editor), sorted(client),
                         'the form cannot change a field the queue cannot carry')

    def test_every_field_has_a_label_in_the_users_words(self):
        """Diagnostics is where a conflict on a Person whose page is no longer
        cached is reached. `link_stanford_encyclopedia = "..."` there is the
        protocol talking, not the product."""
        source = (FRONTEND / 'person-metadata-state.js').read_text()
        labels = source[source.index('const LABELS = Object.freeze({'):]
        labels = labels[: labels.index('});')]
        for field in person_metadata_sync.SYNCED_FIELDS:
            with self.subTest(field=field):
                self.assertRegex(labels, r'\b%s:' % field)

    def test_editing_is_mutation_and_carries_a_base_revision(self):
        store = (FRONTEND / 'local-store.js').read_text()
        at = store.index('function savePersonMetadataFields(')
        body = store[at: store.index('\n        function ', at + 10)]
        self.assertIn('base_revision: observed.revision', body)
        # And it is ordered behind an unsynchronized creation by the GENERIC
        # mechanism -- never by a rule private to this family.
        self.assertIn('personCreationDependency(', body)
        self.assertIn('depends_on: createOp ? [createOp.op_id] : []', body)

    def test_the_acknowledgement_contract_matches_the_server(self):
        """The server omits the value; the client must require that omission.

        Accepting an echoed value would mean the two disagree about what a
        well-formed answer is -- and the one that carries a biography is the
        one the client cannot durably store.
        """
        source = (FRONTEND / 'person-metadata-state.js').read_text()
        at = source.index('function isResult(')
        body = source[at: source.index('\n    }', at)]
        self.assertIn('data.value_omitted === true', body)
        backend = (ROOT / 'backend' / 'person_metadata_sync.py').read_text()
        self.assertIn('value_omitted=True', backend)
        self.assertNotIn('value=desired', backend)

    def test_the_durable_path_is_the_only_one_the_editor_uses(self):
        """Online and offline are the same feature or they are two features."""
        people = (FRONTEND / 'components' / 'people.js').read_text()
        at = people.index('async function savePersonProfile(')
        body = people[at: at + 7000]
        self.assertIn('prksSavePersonFieldsDurably(', body)
        self.assertNotIn('prksPersonMutationBlocked', body,
                         'a connectivity guard would make this two features')
        self.assertNotIn('window.location.reload', body)
        self.assertNotIn('fetchPersonDetails(', body,
                         'a refetch as the completion condition would fail Save offline')

    def test_unknown_profile_state_is_not_treated_as_empty(self):
        """G5. Guessing revision 0 for a Person whose revisions this device has
        never read would silently overwrite whatever another device wrote --
        the one thing a base revision exists to prevent. The exception is a
        Person the server has never heard of, where revision 0 is known."""
        body = self.base_helper()
        self.assertIn('prksPendingPersonCreates(', body)
        self.assertIn('if (!state) return null', body)
        save = self.save_body()
        self.assertIn('if (!base) {', save)

    def base_helper(self):
        source = (FRONTEND / 'person-metadata-state.js').read_text()
        at = source.index('async function acknowledgedPersonBase(')
        return source[at: source.index('\n    }', at)]

    def save_body(self):
        people = (FRONTEND / 'components' / 'people.js').read_text()
        return people[people.index('async function savePersonProfile('):][:9000]

    def test_the_base_is_the_acknowledged_record_not_the_one_on_screen(self):
        """Three concepts, and the editor must not collapse two of them.

        The Person a component holds is the EFFECTIVE record: acknowledged plus
        this device's unsynchronized intent. Measuring an edit against it would
        make a pending value indistinguishable from the server's own, so
        editing a field back to what the server actually holds would look like
        a change -- and leave behind an operation asking for a value nobody
        changed.
        """
        body = self.base_helper()
        self.assertIn("prksOfflineReadEntity('person'", body,
                      'the acknowledged record comes from the cache, not from a caller')
        people = (FRONTEND / 'components' / 'people.js').read_text()
        at = people.index('async function prksReadPersonProfileBase(')
        reader = people[at: people.index('\n}', at)]
        self.assertNotIn("getEntity('person')", reader)
        self.assertIn('prksAcknowledgedPersonBase(', reader)

    def test_a_pending_creation_is_its_own_construction_base(self):
        """Revision 0 is KNOWN for a Person the server has never heard of --
        and the values are the ones the creation will construct."""
        body = self.base_helper()
        self.assertIn('prksPersonCatalogRowFromOp(', body)
        self.assertIn('newPersonMetadataState(personId)', body)

    def test_only_the_fields_this_session_changed_are_saved(self):
        """Sending all eleven fields on every save would let ONE syncing or
        conflicted field refuse the whole form -- destroying exactly the
        independence a per-field conflict unit exists to give."""
        save = self.save_body()
        self.assertIn('prksDirtyPersonFields(personId, desired, base, operations)', save)
        self.assertIn('if (Object.keys(changes).length) {', save)
        source = (FRONTEND / 'person-metadata-state.js').read_text()
        at = source.index('function dirtyPersonFields(')
        body = source[at: source.index('\n    }', at)]
        # Measured against what the form was SHOWING: the pending value where
        # there is one, so an untouched field never looks dirty.
        self.assertIn('pending.has(field) ? pending.get(field) : observed.value', body)

    def test_group_membership_stayed_out_of_the_profile_vocabulary(self):
        """A membership is a relationship, not a profile scalar.

        It became durable with its own family, whose conflict unit is the
        `(group, person)` PAIR -- so two devices that added different people to
        one group have not collided. Folding it into the profile would have
        given every membership on a Person one shared conflict, and would have
        made a single busy biography block a group change.
        """
        self.assertNotIn('group_ids', person_metadata_sync.SYNCED_FIELDS)
        people = (FRONTEND / 'components' / 'people.js').read_text()
        at = people.index('async function prksSavePersonGroupMemberships(')
        body = people[at: people.index('\n}', at)]
        self.assertIn('prksSetPersonGroupMembership(', body, 'its own durable family')
        self.assertIn('added.concat(removed)', body, 'one intent per pair, not a replacement')

    def test_the_rename_overlay_is_applied_last_and_only_to_names(self):
        """Three overlays, one order. The first two decide which people are
        credited and what the scalar fields say; this one only corrects the
        name of whoever ended up there."""
        app = (FRONTEND / 'app.js').read_text()
        at = app.index('function prksEffectiveWorkRows(')
        body = app[at: app.index('\n}', at)]
        self.assertLess(body.index('prksEffectiveWorkRolesRows'),
                        body.index('prksEffectiveWorkRowPersonNames'))
        state = (FRONTEND / 'person-metadata-state.js').read_text()
        displayed = state[state.index('const DISPLAY_FIELDS ='):]
        self.assertEqual(re.findall(r"'([a-z_]+)'", displayed[: displayed.index(';')]),
                         ['first_name', 'last_name'])

    def test_the_pending_name_map_is_hydrated_with_the_other_overlays(self):
        """A card renders synchronously. An overlay that had to await the store
        could only correct itself after the first paint."""
        app = (FRONTEND / 'app.js').read_text()
        at = app.index('async function prksHydratePendingWorkMetadata(')
        body = app[at: app.index('\n}', at)]
        self.assertIn('prksRefreshPendingPersonNames', body)


if __name__ == '__main__':
    unittest.main()
