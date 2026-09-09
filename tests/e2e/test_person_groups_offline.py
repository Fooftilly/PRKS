"""Person Group read models, cache safety, live controls and domain boundaries."""
import json
import os
import unittest
from urllib.parse import urlparse

from tests.e2e import test_offline as o
from tests.e2e.fixtures import PRKSDatabase, StorageConfig, SCHEMA, seed_people_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium


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
        self.assertEqual([p for p in seen if p.startswith('/api/')], [])
        self.assertTrue(page.locator('[data-prks-role="group-mutation-control"]').first.is_disabled())
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
        for button in page.locator('[data-prks-role="group-mutation-control"]').all():
            self.assertTrue(button.is_disabled())
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

    def test_creation_modal_guard_and_disconnect_before_submit(self):
        server, page, context = self.start()
        self.index(page)
        page.wait_for_selector('.prks-group-library')
        page.evaluate("openModal('group-modal')")
        page.locator('#group-name').fill('Disconnected group')
        self.offline(page, context)
        posts = []
        page.on('request', lambda req: posts.append(req.url) if req.method == 'POST' and '/api/person-groups' in req.url else None)
        page.locator('#save-group-btn').click()
        page.locator('#prks-modal-confirm:not(.hidden)').wait_for()
        self.assertEqual(posts, [])
        page.evaluate('closeModals()')
        page.evaluate("openModal('group-modal')")
        self.assertEqual(page.locator('#group-modal:not(.hidden)').count(), 0)
        self.assertEqual(posts, [])

    def test_cached_group_cannot_enter_mutation_modes(self):
        server, page, context = self.start()
        self.cache(page, server.ids)
        context.set_offline(True)
        page.reload(wait_until='domcontentloaded')
        o._wait_offline_banner(page)
        page.evaluate('openPersonGroupEdit()')
        page.evaluate('prksTogglePersonGroupMembersEdit()')
        self.assertEqual(page.locator('.group-sidebar-pane--edit').count(), 0)
        self.assertEqual(page.locator('#group-add-member-btn').count(), 0)
        for button in page.locator('[data-prks-role="group-mutation-control"]').all():
            self.assertTrue(button.is_disabled())

    def test_editor_draft_survives_disconnect_and_controls_restore(self):
        server, page, context = self.start()
        self.detail(page, server.ids['person_group'])
        o._wait_content_contains(page, 'Group description')
        o._open_details_drawer_if_tiled(page)
        page.evaluate('openPersonGroupEdit()')
        page.wait_for_function("!!document.querySelector('#gd-save-btn')?.onclick")
        page.locator('#gd-name').fill('Unsaved name')
        page.locator('#gd-description').fill('Unsaved description')
        page.locator('#gd-parent-search').fill('Unsaved parent')
        self.offline(page, context)
        for field in ('#gd-name','#gd-description','#gd-parent-search','#gd-save-btn','#gd-delete-btn'):
            self.assertTrue(page.locator(field).is_disabled(), field)
        self.assertEqual(page.locator('#gd-name').input_value(), 'Unsaved name')
        self.assertEqual(page.locator('#gd-parent-search').input_value(), 'Unsaved parent')
        self.online(page, context)
        self.assertFalse(page.locator('#gd-save-btn').is_disabled())
        self.assertEqual(page.locator('#gd-description').input_value(), 'Unsaved description')
        self.offline(page, context)
        page.locator('button[onclick="closePersonGroupEdit()"]').click()
        self.assertEqual(page.locator('.group-sidebar-pane--edit').count(), 0)

    def test_member_manager_survives_disconnect_and_done_remains_live(self):
        server, page, context = self.start()
        self.detail(page, server.ids['person_group'])
        o._wait_content_contains(page, 'Group description')
        page.evaluate('prksTogglePersonGroupMembersEdit()')
        page.wait_for_function("!!document.querySelector('#group-add-member-btn')?.onclick")
        page.locator('#group-add-member-search').fill('Unsaved search')
        self.offline(page, context)
        for selector in ('#group-add-member-search','#group-add-member-btn','[data-remove-member]'):
            self.assertTrue(page.locator(selector).first.is_disabled())
        o._wait_content_contains(page, o.PERSON_DISPLAY)
        self.online(page, context)
        self.assertFalse(page.locator('#group-add-member-btn').is_disabled())
        self.assertEqual(page.locator('#group-add-member-search').input_value(), 'Unsaved search')
        self.offline(page, context)
        page.locator('button[onclick="prksTogglePersonGroupMembersEdit()"]').click()
        self.assertEqual(page.locator('#group-add-member-btn').count(), 0)

    def test_direct_group_operations_invalidate_exact_domains(self):
        server, page, context = self.start()
        ids = server.ids
        actions = [
            ("createPersonGroup({name:'Unassigned'})", {'person-groups'}),
            ("updatePersonGroup(ids.person_group,{name:'Renamed group'})", {'people','person-groups'}),
            ("addPersonGroupMember(ids.person_group,ids.person_b)", {'people','person-groups'}),
            ("removePersonGroupMember(ids.person_group,ids.person_b)", {'people','person-groups'}),
            ("deletePersonGroup(ids.group_child)", {'people','person-groups'}),
        ]
        for expression, expected in actions:
            with self.subTest(expression=expression):
                self.cache(page, ids, all_domains=True)
                before = self.generations(page)
                self.assertTrue(page.evaluate('ids => ' + expression, ids)['ok'])
                self.changed(page, before, expected)

    def test_failed_group_operations_retain_good_caches(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        good = o._cached_entity(page, 'person-group', ids['person_group'])
        before = self.generations(page)
        result = page.evaluate("ids => updatePersonGroup(ids.person_group,{name:'Parent Branch',parent_name:'Orphan'})", ids)
        self.assertFalse(result['ok'])
        self.assertNotIn('Orphan', page.evaluate('() => fetchPersonGroups().then(rows => rows.map(r=>r.name))'))
        self.changed(page, before, set())
        self.assertEqual(o._cached_entity(page, 'person-group', ids['person_group']), good)
        page.route('**/api/person-groups/**', lambda route: route.fulfill(status=500, content_type='application/json', body='{}'))
        self.assertFalse(page.evaluate('id => deletePersonGroup(id)', ids['person_group'])['ok'])
        self.changed(page, before, set())

    def test_person_draft_group_creation_invalidates_groups_only(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids, all_domains=True)
        o._open_person(page, ids['person_a'])
        o._wait_content_contains(page, o.PERSON_DISPLAY)
        o._open_details_drawer_if_tiled(page)
        page.evaluate('openPersonProfileEdit()')
        page.wait_for_function("!!document.querySelector('#pd-group-add-btn')?.onclick")
        before = self.generations(page)
        page.locator('#pd-group-search').fill('Draft-only group')
        page.locator('#pd-group-add-btn').click()
        page.locator('#pd-group-chips', has_text='Draft-only group').wait_for()
        self.changed(page, before, {'person-groups'})
        canonical = page.evaluate('id => fetchPersonDetails(id)', ids['person_a'])
        self.assertNotIn('Draft-only group', [g['name'] for g in canonical['groups']])

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
            self.changed(page, before, expected)

    def test_person_create_exclusion_and_delete_membership_coherence(self):
        server, page, context = self.start()
        ids = server.ids
        self.cache(page, ids)
        before = self.generations(page)
        page.evaluate("async () => { await prksQuickCreatePersonForSearchField('New Person','','',''); }")
        self.changed(page, before, {'people'})
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

    def test_stale_index_and_detail_reads_cannot_repopulate(self):
        server, page, context = self.start()
        gid = server.ids['person_group']
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
            if len(held) >= 2: break
            page.wait_for_timeout(50)
        self.assertEqual(len(held), 2)
        page.evaluate("id => updatePersonGroup(id,{description:'Changed while reading'})", gid)
        page.wait_for_function("!prksOfflineIsDomainBlocked('person-groups')")
        for route in held: route.fallback()
        page.evaluate('() => Promise.all([pendingGroups,pendingGroup])')
        self.assertIsNone(o._cached_list(page, 'person-groups:index'))
        self.assertIsNone(o._cached_entity(page, 'person-group', gid))
        page.unroute('**/api/person-groups**', hold)
        self.detail(page, gid)
        o._wait_entity_cached(page, 'person-group', gid)
