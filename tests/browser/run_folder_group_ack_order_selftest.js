'use strict';
/* #1081 / #1079 / #1425: a delayed or replayed Folder field, Work filing or
 * Person Group ACK must never replace newer cached values, and a membership
 * ACK must never count a change twice. */
const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); },
    { get: (_target, key) => (...args) => { checks += 1; return strict[key](...args); } });
const { createFakeIndexedDBFactory } = require('./lib/fake_indexeddb.js');
const { createPrksOfflineStore } = require('../../frontend/js/offline-store.js');
const { createPrksOfflineRuntime } = require('../../frontend/js/offline-runtime.js');
globalThis.prksSyncReconcile = require('../../frontend/js/sync-reconcile.js');
require('../../frontend/js/folder-state.js');
require('../../frontend/js/person-group-state.js');

const later = () => new Promise(resolve => setTimeout(resolve, 3));
const createdNow = extra => ({ created_at: new Date().toISOString(), ...(extra || {}) });

function setup() {
    const cache = createPrksOfflineStore({ dbName: 'prks-offline-v2:test', indexedDB: createFakeIndexedDBFactory() });
    const offline = createPrksOfflineRuntime({ store: cache, window: null,
        prksRequest: async () => { throw new Error('no reads in this scenario'); } });
    return { cache, offline };
}
const fields = (names, revision, overrides) => Object.fromEntries(names.map(name =>
    [name, { revision: (overrides && overrides[name]) || revision }]));
const FOLDER_FIELDS = ['title', 'description', 'private_notes', 'parent_id'];
const GROUP_FIELDS = ['name', 'description', 'parent_id'];
const folderAck = revision => ({ code: 'ACKNOWLEDGED', folder_id: 'F', field: 'description',
    server_revision: revision, member_work_ids: [] });

async function folderField() {
    /* Revision-2 description already read everywhere; the revision-1 ACK is late. */
    {
        const { cache, offline } = setup();
        const op = createdNow({ payload: { value: 'old' } });
        await later();
        await cache.putEntity('folder-state', 'F', { folder_id: 'F', fields: fields(FOLDER_FIELDS, 0, { description: 2 }) });
        await cache.putList('folders:index', [{ id: 'F', title: 'T', description: 'new', parent_id: null }], '');
        await cache.putEntity('folder', 'F', { id: 'F', title: 'T', description: 'new' });
        assert.equal(await offline.reconcileFolderField(folderAck(1), op), true, 'the operation retires');
        assert.equal((await cache.getList('folders:index')).value[0].description, 'new', 'index keeps the newer value');
        assert.equal((await cache.getEntity('folder', 'F')).value.description, 'new', 'detail keeps the newer value');
        assert.equal((await cache.getEntity('folder-state', 'F')).value.fields.description.revision, 2);
    }
    /* No folder-state: the newer index and detail cannot be proven and are dropped. */
    {
        const { cache, offline } = setup();
        const op = createdNow({ payload: { value: 'old' } });
        await later();
        await cache.putList('folders:index', [{ id: 'F', title: 'T', description: 'new', parent_id: null }], '');
        await cache.putEntity('folder', 'F', { id: 'F', title: 'T', description: 'new' });
        assert.equal(await offline.reconcileFolderField(folderAck(1), op), true);
        assert.equal(await cache.getList('folders:index'), null, 'an unproven index is dropped');
        assert.equal(await cache.getEntity('folder', 'F'), null, 'an unproven detail is dropped');
    }
    /* Forward ACK on snapshots read before the operation applies. */
    {
        const { cache, offline } = setup();
        await cache.putEntity('folder-state', 'F', { folder_id: 'F', fields: fields(FOLDER_FIELDS, 1) });
        await cache.putList('folders:index', [{ id: 'F', title: 'T', description: 'before', parent_id: null }], '');
        await cache.putEntity('folder', 'F', { id: 'F', title: 'T', description: 'before' });
        await later();
        const op = createdNow({ payload: { value: 'after' } });
        assert(await offline.reconcileFolderField(folderAck(2), op));
        assert.equal((await cache.getList('folders:index')).value[0].description, 'after');
        assert.equal((await cache.getEntity('folder', 'F')).value.description, 'after');
        assert.equal((await cache.getEntity('folder-state', 'F')).value.fields.description.revision, 2,
            'the next edit is measured against the ACK');
    }
}

async function workFiling() {
    const ack = revision => ({ code: 'ACKNOWLEDGED', work_id: 'W', folder_id: 'F-OLD', folder_title: 'Old',
        server_revision: revision, changed: true });
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity('work-folder-state', 'W', { work_id: 'W', folder_id: 'F-NEW', revision: 2 });
        await cache.putEntity('work', 'W', { id: 'W', folder_id: 'F-NEW', folder_title: 'New' });
        await cache.putList('folders:index', [{ id: 'F-NEW', title: 'New', parent_id: null }], '');
        const foldersBefore = offline.currentDomainGeneration('folders');
        assert.equal(await offline.reconcileWorkFolder(ack(1), op), true);
        assert.equal((await cache.getEntity('work', 'W')).value.folder_id, 'F-NEW', 'the Work keeps its newer filing');
        assert.equal((await cache.getEntity('work-folder-state', 'W')).value.revision, 2);
        assert.equal(offline.currentDomainGeneration('folders'), foldersBefore,
            'a superseded filing ACK does not stale the Folder projections');
    }
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity('work', 'W', { id: 'W', folder_id: 'F-NEW', folder_title: 'New' });
        assert(await offline.reconcileWorkFolder(ack(1), op));
        assert.equal(await cache.getEntity('work', 'W'), null, 'an unproven Work is dropped');
    }
    {
        const { cache, offline } = setup();
        await cache.putEntity('work-folder-state', 'W', { work_id: 'W', folder_id: 'F-NEW', revision: 1 });
        await cache.putEntity('work', 'W', { id: 'W', folder_id: 'F-NEW', folder_title: 'New' });
        await later();
        assert(await offline.reconcileWorkFolder(ack(2), createdNow()));
        assert.equal((await cache.getEntity('work', 'W')).value.folder_id, 'F-OLD', 'a forward filing ACK applies');
        assert.equal((await cache.getEntity('work-folder-state', 'W')).value.revision, 2);
    }
}

async function groupField() {
    const ack = revision => ({ code: 'ACKNOWLEDGED', group_id: 'G', field: 'name', server_revision: revision });
    {
        const { cache, offline } = setup();
        const op = createdNow({ payload: { value: 'Old name' } });
        await later();
        await cache.putEntity('person-group-state', 'G',
            { group_id: 'G', fields: fields(GROUP_FIELDS, 0, { name: 2 }), members: [] });
        await cache.putList('person-groups:index', [{ id: 'G', name: 'New name', parent_id: null, member_count: 0 }], '');
        await cache.putEntity('person-group', 'G', { id: 'G', name: 'New name', members: [] });
        await cache.putList('people:index', [{ id: 'P', groups: [{ id: 'G', name: 'New name' }] }], '');
        assert.equal(await offline.reconcilePersonGroupField(ack(1), op), true);
        assert.equal((await cache.getList('person-groups:index')).value[0].name, 'New name', 'catalogue keeps the newer name');
        assert.equal((await cache.getEntity('person-group', 'G')).value.name, 'New name', 'detail keeps the newer name');
        assert.equal((await cache.getList('people:index')).value[0].groups[0].name, 'New name', 'chips keep the newer name');
        assert.equal((await cache.getEntity('person-group-state', 'G')).value.fields.name.revision, 2);
    }
    {
        const { cache, offline } = setup();
        await cache.putEntity('person-group-state', 'G', { group_id: 'G', fields: fields(GROUP_FIELDS, 1), members: [] });
        await cache.putList('person-groups:index', [{ id: 'G', name: 'Before', parent_id: null, member_count: 0 }], '');
        await cache.putEntity('person-group', 'G', { id: 'G', name: 'Before', members: [] });
        await later();
        assert(await offline.reconcilePersonGroupField(ack(2), createdNow({ payload: { value: 'After' } })));
        assert.equal((await cache.getList('person-groups:index')).value[0].name, 'After', 'a forward name ACK applies');
        assert.equal((await cache.getEntity('person-group', 'G')).value.name, 'After');
    }
}

async function groupMembership() {
    const ack = (revision, present) => ({ code: 'ACKNOWLEDGED', group_id: 'G', person_id: 'P',
        present, changed: true, server_revision: revision, group_name: 'G', group_name_revision: 0 });
    const person = { id: 'P', name: 'Ada', groups: [] };
    /* Revision-2 snapshot: removed, count 0. The revision-1 ADD ACK is late. */
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity('person-group-state', 'G', { group_id: 'G', fields: fields(GROUP_FIELDS, 0),
            members: [{ person_id: 'P', revision: 2, present: false }] });
        await cache.putEntity('person-group-memberships', 'P', { person_id: 'P',
            groups: [{ group_id: 'G', revision: 2, present: false }] });
        await cache.putList('person-groups:index', [{ id: 'G', name: 'G', parent_id: null, member_count: 0 }], '');
        await cache.putList('people:index', [{ ...person }], '');
        await cache.putEntity('person-group', 'G', { id: 'G', name: 'G', members: [], member_count: 0 });
        await cache.putEntity('person', 'P', { ...person });
        assert.equal(await offline.reconcilePersonGroupMember(ack(1, true), op), true, 'the operation retires');
        assert.equal((await cache.getList('person-groups:index')).value[0].member_count, 0, 'no stale +1');
        assert.deepEqual((await cache.getEntity('person-group-state', 'G')).value.members,
            [{ person_id: 'P', revision: 2, present: false }], 'group pair state stays newer');
        assert.deepEqual((await cache.getEntity('person-group-memberships', 'P')).value.groups,
            [{ group_id: 'G', revision: 2, present: false }], 'person pair state stays newer');
        assert.deepEqual((await cache.getEntity('person-group', 'G')).value.members, [], 'detail stays newer');
        assert.deepEqual((await cache.getEntity('person', 'P')).value.groups, [], 'chips stay newer');
    }
    /* Only the Person side knows revision 2: it is enough. */
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putEntity('person-group-memberships', 'P', { person_id: 'P',
            groups: [{ group_id: 'G', revision: 2, present: true }] });
        await cache.putList('person-groups:index', [{ id: 'G', name: 'G', parent_id: null, member_count: 1 }], '');
        assert(await offline.reconcilePersonGroupMember(ack(1, false), op));
        assert.equal((await cache.getList('person-groups:index')).value[0].member_count, 1, 'no stale -1');
    }
    /* No pair state, catalogue read after the operation: its count already
     * includes the change, so it is dropped, never counted twice. */
    {
        const { cache, offline } = setup();
        const op = createdNow();
        await later();
        await cache.putList('person-groups:index', [{ id: 'G', name: 'G', parent_id: null, member_count: 1 }], '');
        assert(await offline.reconcilePersonGroupMember(ack(1, true), op));
        assert.equal(await cache.getList('person-groups:index'), null, 'an unproven count is dropped');
    }
    /* Forward ACK on snapshots read before the operation: counted once. */
    {
        const { cache, offline } = setup();
        await cache.putEntity('person-group-state', 'G', { group_id: 'G', fields: fields(GROUP_FIELDS, 0), members: [] });
        await cache.putList('person-groups:index', [{ id: 'G', name: 'G', parent_id: null, member_count: 0 }], '');
        await cache.putList('people:index', [{ ...person }], '');
        await cache.putEntity('person-group', 'G', { id: 'G', name: 'G', members: [], member_count: 0 });
        await later();
        const op = createdNow();
        assert(await offline.reconcilePersonGroupMember(ack(1, true), op));
        assert.equal((await cache.getList('person-groups:index')).value[0].member_count, 1, 'a forward ADD counts once');
        assert.deepEqual((await cache.getList('people:index')).value[0].groups, [{ id: 'G', name: 'G' }]);
        assert.deepEqual((await cache.getEntity('person-group', 'G')).value.members.map(m => m.id), ['P']);
        assert(await offline.reconcilePersonGroupMember(ack(1, true), op), 'a ledger replay settles');
        assert.equal(await cache.getList('person-groups:index'), null,
            'a replay of the same ACK drops the count rather than counting again');
    }
}

/* A snapshot that could not be dropped keeps the operation queued; catalogue
 * writes from creation ACKs keep the read time of the rows around them; a
 * count no projection can remember is never patched. */
async function failuresAndReplays() {
    {
        const { cache, offline } = setup();
        const op = createdNow({ payload: { value: 'old' } });
        await later();
        await cache.putList('folders:index', [{ id: 'F', title: 'T', description: 'new', parent_id: null }], '');
        cache.deleteList = async () => { throw new Error('quota'); };
        assert.equal(await offline.reconcileFolderField(folderAck(1), op), false,
            'an unproven index that could not be dropped keeps the operation queued');
    }
    {
        const { cache, offline } = setup();
        const op = createdNow({ payload: { value: 'old' } });
        await later();
        await cache.putEntity('folder', 'F', { id: 'F', title: 'T', description: 'new' });
        cache.deleteEntity = async () => { throw new Error('quota'); };
        assert.equal(await offline.reconcileFolderField(folderAck(1), op), false,
            'an unproven detail that could not be dropped keeps the operation queued');
    }
    {
        const { cache, offline } = setup();
        const readAt = Date.now() - 60000;
        await cache.putList('folders:index', [{ id: 'A', title: 'Other', parent_id: null }], '', readAt);
        await later();
        const op = createdNow({ payload: { value: 'described' } });
        await later();
        assert(await offline.reconcileCreatedFolder({ folder_id: 'F', changed: false,
            folder: { id: 'F', title: 'New', description: '', parent_id: null } }));
        assert.equal((await cache.getList('folders:index')).observedAt, readAt,
            'a created row does not make the catalogue newer');
        assert(await offline.reconcileFolderField(folderAck(1), op));
        const rows = (await cache.getList('folders:index')).value;
        assert.deepEqual(rows.map(row => row.id), ['A', 'F'], 'the catalogue survives the follow-up edit');
        assert.equal(rows[1].description, 'described');
    }
    {
        const { cache, offline } = setup();
        const readAt = Date.now() - 60000;
        await cache.putList('person-groups:index', [{ id: 'G', name: 'G', parent_id: null, member_count: 0 },
            { id: 'H', name: 'H', parent_id: null, member_count: 0 }], '', readAt);
        await later();
        const op = createdNow();
        await later();
        assert(await offline.reconcileCreatedPersonGroup({ group_id: 'N', changed: false,
            group: { id: 'N', name: 'N', parent_id: null, member_count: 0 } }));
        assert.equal((await cache.getList('person-groups:index')).observedAt, readAt,
            'a created Group does not make the catalogue newer');
        assert(await offline.reconcilePersonGroupMember({ code: 'ACKNOWLEDGED', group_id: 'G',
            person_id: 'P', present: true, changed: true, server_revision: 1, group_name: 'Group', group_name_revision: 0 }, op));
        assert.equal(await cache.getList('person-groups:index'), null,
            'with no membership projection to remember the ACK, the count is dropped, not patched');
    }
    /* The chip takes its name from the ACK: with no catalogue and no Group
     * detail cached, the People index and the Person are patched, not
     * dropped, and no chip is blank. */
    {
        const { cache, offline } = setup();
        const person = { id: 'P', name: 'Ada', groups: [] };
        await cache.putList('people:index', [{ ...person }], '');
        await cache.putEntity('person', 'P', { ...person });
        await later();
        const op = createdNow();
        assert(await offline.reconcilePersonGroupMember({ code: 'ACKNOWLEDGED', group_id: 'G',
            person_id: 'P', present: true, changed: true, server_revision: 1, group_name: 'Group', group_name_revision: 0 }, op));
        assert.deepEqual((await cache.getEntity('person', 'P')).value.groups, [{ id: 'G', name: 'Group' }],
            'the chip is named from the ACK');
        assert.deepEqual((await cache.getList('people:index')).value[0].groups, [{ id: 'G', name: 'Group' }],
            'and the People index is patched, not dropped');
    }
    /* The name is revisioned apart from the membership (#1452 review): a
     * delayed ACK after a rename this device already holds must not bring
     * the old name back, and an ACK that states no name patches no chip.
     * Either way the rows that would need the chip are dropped for the next
     * read, never written with a guess. */
    for (const [label, extra, groupState] of [
        ['a newer held name', { group_name: 'Old name', group_name_revision: 1 },
            { group_id: 'G', fields: { name: { revision: 2 }, description: { revision: 0 },
                parent_id: { revision: 0 } }, members: [] }],
        ['no stated name', {}, null],
    ]) {
        const { cache, offline } = setup();
        const person = { id: 'P', name: 'Ada', groups: [] };
        await cache.putList('people:index', [{ ...person }], '');
        await cache.putEntity('person', 'P', { ...person });
        if (groupState) await cache.putEntity('person-group-state', 'G', groupState);
        await later();
        const op = createdNow();
        assert(await offline.reconcilePersonGroupMember({ code: 'ACKNOWLEDGED', group_id: 'G',
            person_id: 'P', present: true, changed: true, server_revision: 1, ...extra }, op), label);
        assert.equal(await cache.getEntity('person', 'P'), null, label + ': the Person is dropped, not misnamed');
        assert.equal(await cache.getList('people:index'), null, label + ': and so is the People index');
    }
    /* The same ACK with a name at least as new as the held one names the chip. */
    {
        const { cache, offline } = setup();
        const person = { id: 'P', name: 'Ada', groups: [] };
        await cache.putEntity('person', 'P', { ...person });
        await cache.putEntity('person-group-state', 'G', { group_id: 'G', fields: { name: { revision: 2 },
            description: { revision: 0 }, parent_id: { revision: 0 } }, members: [] });
        await later();
        const op = createdNow();
        assert(await offline.reconcilePersonGroupMember({ code: 'ACKNOWLEDGED', group_id: 'G',
            person_id: 'P', present: true, changed: true, server_revision: 1,
            group_name: 'Renamed', group_name_revision: 2 }, op));
        assert.deepEqual((await cache.getEntity('person', 'P')).value.groups, [{ id: 'G', name: 'Renamed' }]);
    }
    /* A People GET started before the ACK cannot republish its older rows. */
    {
        const { cache } = setup();
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async () => {
            await gate;
            return { ok: true, status: 200, json: async () => [{ id: 'P', name: 'Ada', groups: [] }] };
        } });
        await cache.putList('people:index', [{ id: 'P', name: 'Ada', groups: [] }], '');
        const reading = offline.readThroughList('people:index', '/api/people',
            { domain: 'people', validate: Array.isArray });
        await later();
        const op = createdNow();
        assert(await offline.reconcilePersonGroupMember({ code: 'ACKNOWLEDGED', group_id: 'G',
            person_id: 'P', present: true, changed: true, server_revision: 1, group_name: 'Group', group_name_revision: 0 }, op));
        release();
        await reading;
        await later(); await later();
        assert.deepEqual((await cache.getList('people:index')).value[0].groups, [{ id: 'G', name: 'Group' }],
            'the older People read is not published over the patched rows');
    }
    /* With no People index cached, the fence still moves: a GET started
     * before the ACK does not publish rows without the membership. */
    {
        const { cache } = setup();
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async () => {
            await gate;
            return { ok: true, status: 200, json: async () => [{ id: 'P', name: 'Ada', groups: [] }] };
        } });
        const reading = offline.readThroughList('people:index', '/api/people',
            { domain: 'people', validate: Array.isArray });
        await later();
        const op = createdNow();
        assert(await offline.reconcilePersonGroupMember({ code: 'ACKNOWLEDGED', group_id: 'G',
            person_id: 'P', present: true, changed: true, server_revision: 1, group_name: 'Group', group_name_revision: 0 }, op));
        release();
        await reading;
        await later(); await later();
        assert.equal(await cache.getList('people:index'), null, 'the older People read is not published');
    }
    /* Nor a Person detail read started before the ACK, with no profile cached. */
    {
        const { cache } = setup();
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        const offline = createPrksOfflineRuntime({ store: cache, window: null, prksRequest: async () => {
            await gate;
            return { ok: true, status: 200, json: async () => ({ id: 'P', name: 'Ada', groups: [] }) };
        } });
        const reading = offline.readThroughEntity('person', 'P', '/api/people/P', { domain: 'people' });
        await later();
        const op = createdNow();
        assert(await offline.reconcilePersonGroupMember({ code: 'ACKNOWLEDGED', group_id: 'G',
            person_id: 'P', present: true, changed: true, server_revision: 1, group_name: 'Group', group_name_revision: 0 }, op));
        release();
        await reading;
        await later(); await later();
        assert.equal(await cache.getEntity('person', 'P'), null, 'the older Person read is not published');
    }
    /* The replay after a first attempt dropped the catalogue and then failed
     * to write the Person: the chip is still named from the ACK. */
    {
        const { cache, offline } = setup();
        const person = { id: 'P', name: 'Ada', groups: [] };
        await cache.putEntity('person', 'P', { ...person });
        await cache.putEntity('person-group', 'G', { id: 'G', name: 'Group', members: [], member_count: 0 });
        await later();
        const op = createdNow();
        await later();
        await cache.putList('person-groups:index', [{ id: 'G', name: 'Group', parent_id: null, member_count: 1 }], '');
        const putEntity = cache.putEntity.bind(cache);
        cache.putEntity = async (kind, ...rest) => {
            if (kind === 'person') throw new Error('quota');
            return putEntity(kind, ...rest);
        };
        const ackOf = { code: 'ACKNOWLEDGED', group_id: 'G', person_id: 'P', present: true, changed: true,
            server_revision: 1, group_name: 'Group', group_name_revision: 0 };
        assert.equal(await offline.reconcilePersonGroupMember(ackOf, op), false, 'the Person write failed');
        assert.equal(await cache.getList('person-groups:index'), null, 'after the catalogue was dropped');
        cache.putEntity = putEntity;
        assert(await offline.reconcilePersonGroupMember(ackOf, op), 'the replay retires');
        assert.deepEqual((await cache.getEntity('person', 'P')).value.groups, [{ id: 'G', name: 'Group' }],
            'the replayed chip is named from the ACK');
    }
}

async function main() {
    await folderField();
    await workFiling();
    await groupField();
    await groupMembership();
    await failuresAndReplays();
    console.log('All ' + checks + ' Folder and Person Group ACK ordering checks passed');
}

main().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
