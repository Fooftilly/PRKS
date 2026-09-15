"""Person Group read models, cache safety, live controls and domain boundaries."""
import json
import os
import unittest
from urllib.parse import urlparse

from tests.e2e import test_offline as o
from tests.e2e.fixtures import PRKSDatabase, StorageConfig, SCHEMA, seed_people_library
from tests.e2e.harness import (AppServer, open_app_page, require_chromium,
                               wait_for_async)


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close()
    _PW.stop()


def seed_groups(root):
    ids = seed_people_library(root)
    db = PRKSDatabase(storage=StorageConfig.for_testing(root), schema_path=str(SCHEMA))
    parent = db.add_person_group('Parent Branch', description='Parent description')
    group = ids['person_group']
    db.update_person_group(group, {'parent_id': parent, 'description': 'Group description'})
    child = db.add_person_group('Child Branch', group, 'Child description')
    unseen = db.add_person_group('Unvisited Branch', parent)
    db.add_person_to_group(ids['person_unvisited'], group)
    ids.update(group_parent=parent, group_child=child, group_unvisited=unseen)
    return ids


class PersonGroupsOfflineTests(unittest.TestCase):
    def start(self, seed=seed_groups):
        server = AppServer(seed_fn=seed)
        self.addCleanup(server.stop)
        server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin, service_workers='allow')
        self.addCleanup(context.close)
        o._wait_sw_active(page)
        return server, page, context

    def index(self, page):
        page.evaluate("() => prksNavigate('#/people/groups')")

    def detail(self, page, gid):
        page.evaluate("id => prksNavigate('#/people/groups/' + id)", gid)

    def cache(self, page, ids, all_domains=False):
        if all_domains:
            o._open_concept(page, ids['concept_child'])
            o._wait_entity_cached(page, 'concept', ids['concept_child'])
            o._open_position(page, ids['position_a'])
            o._wait_entity_cached(page, 'position', ids['position_a'])
            o._open_argument(page, ids['argument_a'])
            o._wait_entity_cached(page, 'argument', ids['argument_a'])
        o._open_people_index(page)
        o._wait_list_cached(page, 'people:index')
        self.index(page)
        o._wait_list_cached(page, 'person-groups:index')
        self.detail(page, ids['person_group'])
        o._wait_entity_cached(page, 'person-group', ids['person_group'])

    def generations(self, page):
        return {d: o._domain_generation(page, d) for d in
                ('concepts', 'positions', 'arguments', 'people', 'person-groups')}

    def changed(self, page, before, expected):
        for domain, generation in before.items():
            with self.subTest(domain=domain):
                if domain in expected:
                    self.assertGreater(o._domain_generation(page, domain), generation)
                else:
                    self.assertEqual(o._domain_generation(page, domain), generation)
        if 'person-groups' in expected:
            o._wait_list_uncached(page, 'person-groups:index')
        else:
            self.assertIsNotNone(o._cached_list(page, 'person-groups:index'))

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate("async () => { try { await prksRequest('/api/settings'); } catch (_) {} }")
        page.wait_for_function("prksOfflineRuntimeState() === 'offline'")

    def online(self, page, context):
        context.set_offline(False)
        page.evaluate("async () => { await prksRequest('/api/settings'); }")
        page.wait_for_function("prksOfflineRuntimeState() === 'online'")

    def watch(self, page, methods, fragment='/api/person-groups'):
        """Records every canonical Group request of the given methods."""
        seen = []
        page.on('request', lambda req: seen.append(req.method + ' ' + urlparse(req.url).path)
                if req.method in methods and fragment in urlparse(req.url).path else None)
        return seen

    def test_hierarchy_search_expansion_and_no_detail_prefetch(self):
        server, page, context = self.start()
        seen = []
        page.on('request', lambda req: seen.append(urlparse(req.url).path))
        self.index(page)
        o._wait_list_cached(page, 'person-groups:index')
        self.assertFalse(any(p.startswith('/api/person-groups/') for p in seen))
        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_banner(page)
        seen.clear()
        parent = page.locator('.prks-group-tree__row[data-group-id="%s"]' % server.ids['group_parent'])
        parent.locator('button').click()
        self.assertEqual(parent.get_attribute('aria-expanded'), 'true')
        parent.locator('button').click()
        self.assertEqual(parent.get_attribute('aria-expanded'), 'false')
        page.locator('#prks-group-library-search').fill('Child Branch')
        page.wait_for_selector('.prks-group-tree__row--match')
        o._wait_content_contains(page, 'Parent Branch')
        o._wait_content_contains(page, o.PERSON_GROUP_NAME)
        o._wait_content_contains(page, 'Child Branch')
        # The connectivity probe (`/api/settings`) runs on its own backoff
        # while offline and is not something this page issued; everything else
        # under /api/ would be.
        self.assertEqual(
            [p for p in seen if p.startswith('/api/') and p != '/api/settings'], [])
        # New Group stays live offline: it writes a durable intent under an id
        # this device mints, so it is the same feature with or without PRKS.
        self.assertFalse(page.locator('[data-prks-role="group-mutation-control"]').first.is_disabled())
        page.locator('a.prks-group-tree__link', has_text='Child Branch').click()
        o._wait_offline_unavailable(page)
        self.assertIn('Group not available offline', o._content_text(page))

    def test_empty_index_and_missing_cache_are_distinct(self):
        def empty(root):
            ids = seed_people_library(root)
            db = PRKSDatabase(storage=StorageConfig.for_testing(root), schema_path=str(SCHEMA))
            db.delete_person_group(ids['person_group'])
            return ids
        server, page, context = self.start(empty)
        self.index(page)
        o._wait_list_cached(page, 'person-groups:index')
        self.assertEqual(o._cached_list(page, 'person-groups:index')['value'], [])
        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_banner(page)
        self.assertIn('No Person Groups yet.', o._content_text(page))
        # And New Group stays live: a group is created durably under an id this
        # device mints, so an empty library offline is a library you can start
        # filling rather than a dead end.
        for button in page.locator('[data-prks-role="group-mutation-control"]').all():
            self.assertFalse(button.is_disabled())
        o._clear_cached_list(page, 'person-groups:index')
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_unavailable(page)
        self.assertIn('Person Groups not available offline', o._content_text(page))
        self.assertNotIn('No Person Groups yet.', o._content_text(page))

    def test_cached_relationships_and_member_navigation(self):
        server, page, context = self.start()
        ids = server.ids
        for key in ('group_parent', 'group_child', 'person_group'):
            self.detail(page, ids[key])
            o._wait_entity_cached(page, 'person-group', ids[key])
        o._open_person(page, ids['person_a'])
        o._wait_entity_cached(page, 'person', ids['person_a'])
        self.offline(page, context)
        self.detail(page, ids['person_group'])
        o._wait_offline_banner(page)
        for text in ('Group description', 'Parent Branch', 'Child Branch', o.PERSON_DISPLAY, o.PERSON_UNVISITED_DISPLAY):
            self.assertIn(text, o._content_text(page))
        for key in ('group_child', 'person_group', 'group_parent', 'person_group'):
            page.locator('a[href="#/people/groups/%s"]' % ids[key]).first.click()
            o._wait_offline_banner(page)
            page.wait_for_function('id => location.hash.endsWith(id)', arg=ids[key])
        page.locator('a[href="#/people/%s"]' % ids['person_a']).click()
        o._wait_offline_banner(page)
        o._wait_content_contains(page, o.PERSON_A_ABOUT)
        self.detail(page, ids['person_group'])
        o._wait_offline_banner(page)
        page.locator('a[href="#/people/%s"]' % ids['person_unvisited']).click()
        o._wait_offline_unavailable(page)
        self.assertIn('Person not available offline', o._content_text(page))
        self.detail(page, ids['group_parent'])
        o._wait_offline_banner(page)
        page.locator('a[href="#/people/groups/%s"]' % ids['group_unvisited']).click()
        o._wait_offline_unavailable(page)
        self.assertIn('Group not available offline', o._content_text(page))

    def test_predicates_protect_all_nested_rows_and_scalar_types(self):
        server, page, context = self.start()
        result = page.evaluate("""() => {
            const summary = () => ({id:'G', name:'', description:null, parent_id:null, member_count:0});
            const index = () => ({...summary(), child_count:0});
            const detail = () => ({...summary(), parent:null, children:[], members:[]});
            const bad = [null, [], 'x', {}, {id:''}];
            const failures = [];
            function check(ok, label) { if (!ok) failures.push(label); }
            check(prksIsPersonGroupsIndexShape([]), 'empty index');
            check(prksIsPersonGroupsIndexShape([index()]), 'sparse index');
            check(prksIsPersonGroupShape(detail(), 'G'), 'sparse detail');
            check(!prksIsPersonGroupShape(detail(), 'wrong'), 'requested id');
            for (const value of bad) {
                check(!prksIsPersonGroupsIndexShape([value]), 'index row');
                check(!prksIsPersonGroupShape({...detail(), children:[value]}, 'G'), 'child');
                check(!prksIsPersonGroupShape({...detail(), members:[value]}, 'G'), 'member');
                if (value !== null) check(!prksIsPersonGroupShape({...detail(), parent:value}, 'G'), 'parent');
            }
            for (const field of ['name','description']) for (const value of [{},[],1,true]) {
                check(!prksIsPersonGroupsIndexShape([{...index(),[field]:value}]), field);
                check(!prksIsPersonGroupShape({...detail(),[field]:value}, 'G'), field);
            }
            for (const value of [undefined, '', [], {}, 1, false])
                check(!prksIsPersonGroupsIndexShape([{...index(),parent_id:value}]), 'parent_id');
            for (const value of [undefined, null, '1', [], {}, -1, Infinity, NaN, true]) {
                check(!prksIsPersonGroupsIndexShape([{...index(),member_count:value}]), 'member count');
                check(!prksIsPersonGroupsIndexShape([{...index(),child_count:value}]), 'child count');
            }
            const member = {id:'P',assigned_roles:[],groups:[]};
            check(prksIsPersonGroupShape({...detail(),members:[member]}, 'G'), 'sparse member');
            for (const field of ['first_name','aliases','about','links_other'])
                check(!prksIsPersonGroupShape({...detail(),members:[{...member,[field]:[]}]}, 'G'), field);
            check(!prksIsPersonGroupShape({...detail(),members:[{...member,groups:[null]}]}, 'G'), 'member groups');
            return failures;
        }""")
        self.assertEqual(result, [])

    def test_malformed_authoritative_index_preserves_cache(self):
        server, page, context = self.start()
        self.index(page)
        o._wait_list_cached(page, 'person-groups:index')
        good = o._cached_list(page, 'person-groups:index')
        body = {'value': []}
        page.route('**/api/person-groups', lambda route: route.fulfill(
            status=200, content_type='application/json', body=json.dumps(body['value'])))
        for patch in ({'id':''}, {'parent_id':{}}, {'member_count':'2'}, {'child_count':False}):
            with self.subTest(patch=patch):
                body['value'] = [dict(good['value'][0], **patch)]
                page.evaluate("() => prksNavigate('#/folders')")
                self.index(page)
                page.wait_for_selector('#prks-route-retry')
                self.assertEqual(o._connectivity_state(page), 'online')
                self.assertEqual(o._cached_list(page, 'person-groups:index'), good)
        page.unroute('**/api/person-groups')
        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_banner(page)
        o._wait_content_contains(page, 'Parent Branch')

    def test_malformed_detail_preserves_cache_and_http_errors_stay_online(self):
        server, page, context = self.start()
        gid = server.ids['person_group']
        self.detail(page, gid)
        o._wait_entity_cached(page, 'person-group', gid)
        good = o._cached_entity(page, 'person-group', gid)
        reply = {'status':200, 'value':None}
        page.route('**/api/person-groups/' + gid, lambda route: route.fulfill(
            status=reply['status'], content_type='application/json', body=json.dumps(reply['value'])))
        patches = [{'parent':[]}, {'parent':{'id':''}}, {'children':[None]},
                   {'members':[{'id':'P', 'assigned_roles':[], 'groups':[], 'aliases':[]}]}]
        for patch in patches + [None, None]:
            if patch is None:
                reply['status'] = 403 if reply['status'] == 200 else 500
            reply['value'] = dict(good['value'], **(patch or {}))
            self.index(page)
            self.detail(page, gid)
            page.wait_for_selector('#prks-route-retry')
            self.assertEqual(o._connectivity_state(page), 'online')
            self.assertEqual(o._cached_entity(page, 'person-group', gid), good)
        page.unroute('**/api/person-groups/' + gid)
        self.detail(page, 'PG-MISSING')
        o._wait_content_contains(page, 'Group not found')
        self.offline(page, context)
        self.detail(page, gid)
        o._wait_offline_banner(page)
        o._wait_content_contains(page, 'Group description')

    def test_corrupted_cached_nested_rows_deleted_before_render(self):
        server, page, context = self.start()
        gid = server.ids['person_group']
        self.detail(page, gid)
        o._wait_entity_cached(page, 'person-group', gid)
        good = o._cached_entity(page, 'person-group', gid)['value']
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        context.set_offline(True)
        for patch in ({'children':[None]}, {'members':[{'id':''}]}, {'parent':[]}):
            with self.subTest(patch=patch):
                page.evaluate("([id,value]) => createPrksOfflineStore().putEntity('person-group',id,value,'')",
                              [gid, dict(good, **patch)])
                page.reload(wait_until='domcontentloaded')
                o._wait_offline_unavailable(page)
                o._wait_entity_uncached(page, 'person-group', gid)
                self.assertNotIn('Group not found', o._content_text(page))
                self.assertEqual(page.locator('.document-view--group-detail').count(), 0)
                self.assertEqual(errors, [])

    def enqueued(self, page, operation):
        return page.evaluate(
            "op => prksSync.store.listOperations().then(rows => rows.filter("
            "  o => o.operation === op).length)", operation)

    def test_the_creation_modal_opens_offline_and_records_a_durable_intent(self):
        """A group is created under an id this device mints, so it exists the
        moment it is saved. No POST is issued while offline -- but an operation
        is, which is the difference between "blocked" and "durable"."""
        server, page, context = self.start()
        self.index(page)
        page.wait_for_selector('.prks-group-library')
        page.evaluate("openModal('group-modal')")
        page.locator('#group-name').fill('Disconnected group')
        self.offline(page, context)
        posts = self.watch(page, ('POST',))
        page.locator('#save-group-btn').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'CREATE_PERSON_GROUP'))",
            timeout=30000, message='the group was never recorded durably')
        self.assertEqual(posts, [], 'nothing is sent while there is no server')
        o._wait_content_contains(page, 'Disconnected group')

    def test_global_new_group_surfaces_stay_usable_offline(self):
        """The ribbon and the command palette both route through
        openModal('group-modal'), so one global surface is exercised offline to
        prove the whole set is live."""
        server, page, context = self.start()
        self.index(page)
        page.wait_for_selector('.prks-group-library')
        self.offline(page, context)
        page.locator('#prks-ribbon-new-more').click()
        page.wait_for_selector('#prks-create-menu:not([hidden])')
        page.locator("#prks-create-menu [role='menuitem']", has_text='New Group').click()
        page.wait_for_selector('#group-modal:not(.hidden)')
        self.assertEqual(page.locator('#prks-modal-confirm:not(.hidden)').count(), 0,
                         'no "requires a connection" dialog: it no longer does')

    def test_a_cached_group_enters_every_mutation_mode_offline(self):
        server, page, context = self.start()
        self.cache(page, server.ids)
        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_banner(page)
        o._open_details_drawer_if_tiled(page)
        page.evaluate('openPersonGroupEdit()')
        page.wait_for_selector('.group-sidebar-pane--edit')
        page.evaluate('closePersonGroupEdit()')
        page.evaluate('prksTogglePersonGroupMembersEdit()')
        page.wait_for_selector('#group-add-member-btn')
        for button in page.locator('[data-prks-role="group-mutation-control"]').all():
            self.assertFalse(button.is_disabled())

    def test_the_editor_stays_usable_after_a_disconnect(self):
        """The binding only reacts to CHANGES, so an editor opened while
        connected and then disconnected is where a stale guard would show."""
        server, page, context = self.start()
        self.detail(page, server.ids['person_group'])
        o._wait_content_contains(page, 'Group description')
        o._open_details_drawer_if_tiled(page)
        page.evaluate('openPersonGroupEdit()')
        page.wait_for_function("!!document.querySelector('#gd-save-btn')?.onclick")
        page.locator('#gd-name').fill('Unsaved name')
        page.locator('#gd-description').fill('Unsaved description')
        self.offline(page, context)
        for field in ('#gd-name', '#gd-description', '#gd-parent-search',
                      '#gd-save-btn', '#gd-delete-btn'):
            self.assertFalse(page.locator(field).is_disabled(), field)
        self.assertEqual(page.locator('#gd-name').input_value(), 'Unsaved name')
        self.assertEqual(page.locator('#gd-description').input_value(),
                         'Unsaved description')
        page.locator('button[onclick="closePersonGroupEdit()"]').click()
        self.assertEqual(page.locator('.group-sidebar-pane--edit').count(), 0)

    def test_the_member_manager_stays_usable_after_a_disconnect(self):
        server, page, context = self.start()
        self.detail(page, server.ids['person_group'])
        o._wait_content_contains(page, 'Group description')
        page.evaluate('prksTogglePersonGroupMembersEdit()')
        page.wait_for_function("!!document.querySelector('#group-add-member-btn')?.onclick")
        page.locator('#group-add-member-search').fill('Unsaved search')
        self.offline(page, context)
        for selector in ('#group-add-member-search', '#group-add-member-btn',
                         '[data-remove-member]'):
            self.assertFalse(page.locator(selector).first.is_disabled(), selector)
        o._wait_content_contains(page, o.PERSON_DISPLAY)
        self.assertEqual(page.locator('#group-add-member-search').input_value(),
                         'Unsaved search')
        page.locator('button[onclick="prksTogglePersonGroupMembersEdit()"]').click()
        self.assertEqual(page.locator('#group-add-member-btn').count(), 0)

    def test_a_save_after_a_disconnect_records_an_intent_and_sends_no_patch(self):
        server, page, context = self.start()
        self.detail(page, server.ids['person_group'])
        o._wait_content_contains(page, 'Group description')
        o._open_details_drawer_if_tiled(page)
        page.evaluate("id => { void prksReadPersonGroupState(id); }",
                      server.ids['person_group'])
        o._wait_entity_cached(page, 'person-group-state', server.ids['person_group'])
        page.evaluate('openPersonGroupEdit()')
        page.wait_for_function("!!document.querySelector('#gd-save-btn')?.onclick")
        page.locator('#gd-name').fill('Renamed while connected')
        self.offline(page, context)
        seen = self.watch(page, ('PATCH',))
        page.locator('#gd-save-btn').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'SET_PERSON_GROUP_FIELD'))",
            timeout=30000, message='the rename was never recorded durably')
        self.assertEqual(seen, [], 'the durable path never issues the old PATCH')
        o._wait_content_contains(page, 'Renamed while connected')

    def test_a_delete_confirmed_after_a_disconnect_records_a_tombstone(self):
        """The connection can drop while the confirmation dialog is open. The
        deletion is durable either way, so what matters is that it is recorded
        and that no DELETE is attempted."""
        server, page, context = self.start()
        self.detail(page, server.ids['person_group'])
        o._wait_content_contains(page, 'Group description')
        o._open_details_drawer_if_tiled(page)
        page.evaluate('openPersonGroupEdit()')
        page.wait_for_function("!!document.querySelector('#gd-delete-btn')?.onclick")
        # Invoked directly: Delete lives in a collapsed advanced section, so
        # which markup happens to expose it is not what this proves.
        page.evaluate("() => { void document.getElementById('gd-delete-btn').onclick(); }")
        page.locator('#prks-modal-confirm:not(.hidden)').wait_for()
        self.offline(page, context)
        seen = self.watch(page, ('DELETE',))
        page.locator('#prks-modal-confirm-ok').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'DELETE_PERSON_GROUP'))",
            timeout=30000, message='the deletion was never recorded durably')
        self.assertEqual(seen, [])

    def test_a_member_removal_after_a_disconnect_records_an_intent(self):
        server, page, context = self.start()
        gid = server.ids['person_group']
        self.detail(page, gid)
        o._wait_content_contains(page, 'Group description')
        page.evaluate("id => { void prksReadPersonGroupState(id); }", gid)
        o._wait_entity_cached(page, 'person-group-state', gid)
        page.evaluate('prksTogglePersonGroupMembersEdit()')
        page.wait_for_function("!!document.querySelector('#group-add-member-btn')?.onclick")
        page.locator('[data-remove-member]').first.click()
        page.locator('#prks-modal-confirm:not(.hidden)').wait_for()
        self.offline(page, context)
        seen = self.watch(page, ('DELETE',))
        page.locator('#prks-modal-confirm-ok').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'REMOVE_PERSON_GROUP_MEMBER'))",
            timeout=30000, message='the removal was never recorded durably')
        self.assertEqual(seen, [])

    def test_a_member_added_after_a_disconnect_records_an_intent(self):
        server, page, context = self.start()
        gid = server.ids['person_group']
        self.detail(page, gid)
        o._wait_content_contains(page, 'Group description')
        page.evaluate("id => { void prksReadPersonGroupState(id); }", gid)
        o._wait_entity_cached(page, 'person-group-state', gid)
        page.evaluate('prksTogglePersonGroupMembersEdit()')
        page.wait_for_function("!!document.querySelector('#group-add-member-btn')?.onclick")
        page.evaluate("pid => { document.getElementById('group-add-member-id').value = pid; }",
                      server.ids['person_b'])
        self.offline(page, context)
        seen = self.watch(page, ('POST',))
        page.locator('#group-add-member-btn').click()
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === 'ADD_PERSON_GROUP_MEMBER'))",
            timeout=30000, message='the addition was never recorded durably')
        self.assertEqual([u for u in seen if '/members' in u], [])

    def durable(self, page, expression, arg=None):
        """Run one durable Group write and drain the queue.

        The canonical change a coherence rule follows is the ACKNOWLEDGEMENT,
        not the click, so nothing is asserted until the queue is empty.
        """
        page.evaluate(expression, arg)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => "
            "  rows.every(o => o.status === 'conflict'))",
            timeout=30000, message='the durable write never drained')

    def patched(self, page, before, expected):
        """Like `changed`, but the catalogue is PATCHED rather than dropped.

        The acknowledgement carries the exact new state, so discarding the
        cached catalogue would cost the user a list they cannot rebuild
        offline -- for a change already known in full.
        """
        for domain, generation in before.items():
            with self.subTest(domain=domain):
                if domain in expected:
                    self.assertGreater(o._domain_generation(page, domain), generation)
                else:
                    self.assertEqual(o._domain_generation(page, domain), generation)
        self.assertIsNotNone(o._cached_list(page, 'person-groups:index'))

    def test_group_acknowledgements_patch_the_caches_they_own(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        before = self.generations(page)
        # A brand-new unassigned Group cannot appear in any Person's read model.
        self.durable(page, "() => prksCreatePersonGroupDurably("
                           "  { name: 'Unassigned', description: '' })")
        self.patched(page, before, {'person-groups'})
        self.assertIn('Unassigned', [row['name'] for row in
                                     o._cached_list(page, 'person-groups:index')['value']])

        # A rename reaches the chips embedded in the People rows.
        before = self.generations(page)
        self.durable(page, """async id => {
            const ops = await prksSync.store.listOperations();
            const base = await prksAcknowledgedPersonGroupBase(id, ops);
            await prksSavePersonGroupFieldsDurably(id, { name: 'Renamed group' }, base);
        }""", ids['person_group'])
        self.patched(page, before, {'people', 'person-groups'})

        # A membership reaches both ends.
        before = self.generations(page)
        self.durable(page, """async ([g, p]) => {
            const ops = await prksSync.store.listOperations();
            const observed = await prksAcknowledgedPersonGroupMembership(g, p, ops);
            await prksSetPersonGroupMemberDurably(g, p, true, observed);
        }""", [ids['person_group'], ids['person_b']])
        self.patched(page, before, {'people', 'person-groups'})

        # A deletion stales every cached Person detail that may carry the chip.
        before = self.generations(page)
        self.durable(page, "id => prksDeletePersonGroupDurably(id)", ids['group_child'])
        self.assertGreater(o._domain_generation(page, 'person-groups'),
                           before['person-groups'])
        self.assertGreater(o._domain_generation(page, 'people'), before['people'])
        for domain in ('concepts', 'positions', 'arguments'):
            self.assertEqual(o._domain_generation(page, domain), before[domain], domain)

    def test_a_refused_group_change_becomes_a_decision_and_keeps_the_cache(self):
        """The server owns name uniqueness. A collision is not a lost edit: it
        is a conflict the user resolves, and nothing cached is discarded for
        it."""
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        good = o._cached_entity(page, 'person-group', ids['person_group'])
        before = self.generations(page)
        page.evaluate("""async id => {
            const ops = await prksSync.store.listOperations();
            const base = await prksAcknowledgedPersonGroupBase(id, ops);
            await prksSavePersonGroupFieldsDurably(id, { name: 'Parent Branch' }, base);
        }""", ids['person_group'])
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.status === 'conflict'))",
            timeout=30000, message='the refusal never became a decision')
        state = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.status === 'conflict') || {}).server_result)")
        self.assertEqual(state['code'], 'NAME_TAKEN')
        self.changed(page, before, set())
        # The VALUE, not the envelope: a read-through that re-cached the same
        # body moves `cachedAt` without changing anything the user sees.
        self.assertEqual(
            o._cached_entity(page, 'person-group', ids['person_group'])['value'],
            good['value'])

    def test_person_draft_group_creation_is_durable_and_not_yet_a_membership(self):
        """Typing a new group name in the Person editor creates a real group --
        it has an id this device minted -- but joining it is a separate
        decision that only Save records."""
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        o._open_person(page, ids['person_a'])
        o._wait_content_contains(page, o.PERSON_DISPLAY)
        o._open_details_drawer_if_tiled(page)
        page.evaluate('openPersonProfileEdit()')
        page.wait_for_function("!!document.querySelector('#pd-group-add-btn')?.onclick")
        page.locator('#pd-group-search').fill('Draft-only group')
        page.locator('#pd-group-add-btn').click()
        page.locator('#pd-group-chips', has_text='Draft-only group').wait_for()
        # The group is REAL: created durably under an id this device minted,
        # and -- connected -- already acknowledged.
        wait_for_async(
            page,
            "() => fetchPersonGroups().then(rows => rows.some("
            "  r => r.name === 'Draft-only group'))",
            timeout=30000, message='the group was never created')
        self.assertEqual(
            page.evaluate("() => prksSync.store.listOperations().then(rows => rows.filter("
                          "  o => o.operation === 'ADD_PERSON_GROUP_MEMBER').length)"), 0,
            'joining it is a separate decision, recorded only on Save')

    def test_person_profile_name_and_biography_coherence(self):
        server, page, context = self.start()
        ids = server.ids
        for field, value, expected in (
            ('#pd-about', 'Changed biography', {'people','person-groups'}),
            ('#pd-first-name', 'Renamed', {'people','person-groups','arguments'}),
        ):
            self.cache(page, ids, all_domains=True)
            o._open_person(page, ids['person_a'])
            o._wait_entity_cached(page, 'person', ids['person_a'])
            o._open_details_drawer_if_tiled(page)
            page.evaluate('openPersonProfileEdit()')
            page.wait_for_selector(field)
            page.locator(field).fill(value)
            before = self.generations(page)
            page.locator('#pd-save-btn').click()
            page.wait_for_selector('.person-panel-edit', state='detached')
            # Coherence follows the CANONICAL change, and for a profile field
            # that is now the acknowledgement rather than the Save click.
            o._wait_sync_settled(page)
            self.changed(page, before, expected)

    def test_person_create_exclusion_and_delete_membership_coherence(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        before = self.generations(page)
        page.evaluate("async () => { await prksQuickCreatePersonForSearchField('New Person','','',''); }")
        # Creating a Person is durable-first now, so it RECONCILES rather than
        # invalidating: discarding the cached People index would make every
        # Person the user can already see vanish until the next successful
        # read -- offline, that is until the server returns. The new Person is
        # visible through the pending overlay instead, and the acknowledgement
        # patches the snapshot in place.
        self.changed(page, before, set())
        # This test is about COHERENCE BOUNDARIES, not about the creation
        # itself: which cached domains a Person create may disturb. That it
        # records a durable operation, and that the new Person is immediately
        # visible, are proved in tests/e2e/test_person_create_offline.py --
        # online both would be races, because the creation is acknowledged
        # within milliseconds.
        self.cache(page, ids)
        o._open_person(page, ids['person_unvisited'])
        o._wait_content_contains(page, o.PERSON_UNVISITED_DISPLAY)
        before = self.generations(page)
        page.evaluate('() => { void deletePerson(); }')
        page.locator('#prks-modal-confirm-ok').click()
        page.wait_for_function('prksOfflineDomainGeneration("person-groups") > 0')
        o._wait_list_uncached(page, 'person-groups:index')
        self.changed(page, before, {'people','person-groups'})

    def test_work_role_changes_and_metadata_exclusion(self):
        server, page, context = self.start()
        ids = server.ids
        for role in ('Reviewer','Author'):
            self.cache(page, ids, all_domains=True)
            before = self.generations(page)
            page.evaluate("""async ([ids,role]) => {
                const res = await prksRequest('/api/roles', {method:'POST',
                    headers:{'Content-Type':'application/json'}, body:JSON.stringify({
                        person_id:ids.person_unvisited,work_id:ids.work_b,role_type:role})});
                if (!res.ok) throw Error('role create failed');
                prksMarkWorkRoleChanged(ids.work_b,role);
            }""", [ids, role])
            self.changed(page, before, {'people','person-groups'} | ({'arguments'} if role == 'Author' else set()))
        self.cache(page, ids, all_domains=True)
        before = self.generations(page)
        page.evaluate("""async ids => {
            await prksRequest('/api/works/'+ids.work_b, {method:'PATCH',
                headers:{'Content-Type':'application/json'}, body:JSON.stringify({title:'New title'})});
            prksMarkWorkTitleChanged(ids.work_b);
        }""", ids)
        self.changed(page, before, {'concepts','arguments','people'})
        self.assertIsNotNone(o._cached_entity(page, 'person-group', ids['person_group']))
        before = self.generations(page)
        page.evaluate("id => bulkUpdateWorks({action:'set_status',work_ids:[id],status:'Completed'})", ids['work_b'])
        self.changed(page, before, {'people'})

    def test_unrelated_research_changes_preserve_groups(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        before = o._domain_generation(page, 'person-groups')
        page.evaluate("""async ids => {
            await updateConcept(ids.concept_child,{description:'Changed'});
            await updatePosition(ids.position_a,{description:'Changed'});
            await putArgumentSources(ids.argument_a,[]);
        }""", ids)
        o._open_work_from_home(page, o.WORK_A_TITLE)
        page.locator('.work-notes-editor-wrap .CodeMirror').first.click()
        page.keyboard.press('Control+A')
        page.keyboard.insert_text('Changed research notes')
        page.locator('[data-prks-role="editor-status"]', has_text='All changes saved').wait_for()
        self.assertEqual(o._domain_generation(page, 'person-groups'), before)
        self.assertIsNotNone(o._cached_list(page, 'person-groups:index'))

    def test_a_stale_read_cannot_undo_what_an_acknowledgement_patched(self):
        """A GET that began before the change carries the OLD body.

        The durable path PATCHES the catalogue on acknowledgement rather than
        dropping it -- so what has to hold is that a read already in flight
        cannot write its stale answer over the patched one afterwards.
        """
        server, page, context = self.start()
        gid = server.ids['person_group']
        self.cache(page, server.ids)
        # The base is read BEFORE the route is held: it is itself a GET under
        # /api/person-groups, and holding it would deadlock the save.
        base = page.evaluate("""async id => {
            const ops = await prksSync.store.listOperations();
            return await prksAcknowledgedPersonGroupBase(id, ops);
        }""", gid)
        self.assertIsNotNone(base)

        held = []

        def hold(route):
            if route.request.method == 'GET':
                held.append(route)
            else:
                route.fallback()

        page.route('**/api/person-groups**', hold)
        page.evaluate("""id => {
            window.pendingGroups = prksOfflineReadList('person-groups:index','/api/person-groups',
                {domain:'person-groups',validate:prksIsPersonGroupsIndexShape});
            window.pendingGroup = prksOfflineReadEntity('person-group',id,'/api/person-groups/'+id,
                {domain:'person-groups',validate:v=>prksIsPersonGroupShape(v,id)});
        }""", gid)
        for _ in range(100):
            if len(held) >= 2:
                break
            page.wait_for_timeout(50)
        self.assertEqual(len(held), 2)

        page.evaluate(
            "([id, base]) => prksSavePersonGroupFieldsDurably("
            "  id, { description: 'Changed while reading' }, base)",
            [gid, base])
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=30000, message='the rename never reached the server')

        for route in held:
            route.fallback()
        page.evaluate('() => Promise.all([pendingGroups,pendingGroup])')
        cached = o._cached_entity(page, 'person-group', gid)
        self.assertIsNotNone(cached, 'the patched snapshot is not dropped')
        self.assertEqual(cached['value']['description'], 'Changed while reading',
                         'a read that began earlier cannot publish its older body')
        page.unroute('**/api/person-groups**', hold)
