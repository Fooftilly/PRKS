#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { installMiniDocument } = require('./mini_document');

const rootDir = path.resolve(__dirname, '../..');

/* The unit gate runs this file with Node and does not install frontend-app
 * dependencies. A small document is enough for the role-modal path. */
installMiniDocument([
    '<div id="modal-backdrop" class="hidden"></div>',
    '<div id="role-modal" class="modal hidden">',
    '<input id="role-person-search" />',
    '<input id="role-person-id" />',
    '<input id="role-work-search" />',
    '<input id="role-work-id" />',
    '<input id="role-type" value="Author" />',
    '<button id="save-role-btn" type="button">Create Link</button>',
    '</div>',
    '<button id="save-work-btn" type="button">Save</button>',
    '<div id="panel-content"></div>',
    '<button id="credit-btn" type="button"></button>',
    '<button id="unlink-btn" type="button"></button>',
    '<div id="main-host"></div><div id="side-host"></div>',
].join(''));

const writes = [];
global.prksSync = {
    subscribe: function () { return function () {}; },
    changed: function () {},
    store: {
        listOperations: async function () { return []; },
        saveWorkPersonRole: async function () {
            writes.push(Array.prototype.slice.call(arguments));
        },
    },
};
global.prksWorkFieldUtf8Bytes = function (value) {
    return Buffer.byteLength(String(value || ''), 'utf8');
};
global.prksOfflineRuntimeSubscribe = function () { return function () {}; };
global.prksOfflineRuntimeState = function () { return 'online'; };
global.prksPendingWorkCreates = function () { return []; };

let readGate = null;
let readStarted = 0;
global.prksOfflineReadEntity = async function (kind, id) {
    readStarted += 1;
    if (readGate) await readGate;
    if (kind === 'work-people-state') {
        return { source: 'cache', value: { work_id: id, scopes: [] } };
    }
    return { source: 'cache', value: { id: id, roles: [] } };
};

let focusedId = 'main';
global.prksWorkspaceSnapshot = function () {
    return { focusedTabId: focusedId, mainTabId: 'main' };
};

global.prksOwnerResource = require(path.join(rootDir, 'frontend/js/owner-resource.js'));
require(path.join(rootDir, 'frontend/js/tab-context.js'));
require(path.join(rootDir, 'frontend/js/work-role-state.js'));
require(path.join(rootDir, 'frontend/js/work-notes-state.js'));
(0, eval)(fs.readFileSync(path.join(rootDir, 'frontend/js/ui.js'), 'utf8'));
require(path.join(rootDir, 'frontend/js/work-role-editor.js'));

const roleAlerts = [];
// The modal's real initializer is tested separately before the existing
// owner/epoch harness overrides it to isolate save behavior.
const actualPrepareRoleModal = global.prepareRoleModal;
global.prepareRoleModal = async function () {};
global.prksAlertDialog = async function (opts) {
    roleAlerts.push(String(opts && opts.message || ''));
};
global.prksAlertMessage = async function () {};
global.updatePanelContent = function () {};
global.prksPromptTextDialog = async function () { return null; };
global.prksConfirmDestructive = async function () { return false; };

const app = fs.readFileSync(path.join(rootDir, 'frontend/js/app.js'), 'utf8');
const initAt = app.indexOf('function initForms()');
(0, eval)(app.slice(initAt));
initForms();

let passed = 0;
let failed = 0;
function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (detail ? ' ' + detail : ''));
}
function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : (detail || ''));
}

async function roleModalCatalogsLoadConcurrently() {
    // The real initializer runs against a tiny document and controlled
    // projections. Test the timing contract without network timeouts.
    const originals = {};
    const replace = (key, value) => {
        originals[key] = global[key];
        global[key] = value;
    };
    let peopleRelease;
    let worksRelease;
    const starts = [];
    const personPromise = new Promise(resolve => { peopleRelease = resolve; });
    const workPromise = new Promise(resolve => { worksRelease = resolve; });
    const previousHash = global.location && global.location.hash;
    try {
        replace('prksOfflinePeopleFetch', () => {
            starts.push('people');
            return personPromise;
        });
        replace('prksOfflineWorksBrowseFetch', () => {
            starts.push('works');
            return workPromise;
        });
        replace('fetchPersons', async () => { throw new Error('unexpected People fallback'); });
        replace('fetchWorks', async () => { throw new Error('unexpected Work fallback'); });
        replace('initSearchableCombobox', () => {});
        replace('prksBindRoleCreditPicker', () => {});
        replace('prksMountLinkRoleSegmented', () => {});
        replace('prksRefreshRoleCreditPicker', () => {});
        replace('prksParseRoute', () => ({ name: 'work', params: { workId: 'work-a' } }));
        replace('prksFocusedEntity', () => ({ id: 'work-a', title: 'Focused Work' }));
        replace('personDisplayName', p => p.first_name + ' ' + p.last_name);
        if (!global.location) global.location = {};
        global.location.hash = '#/works/work-a';
        let settled = false;
        const pending = actualPrepareRoleModal().then(() => { settled = true; });
        assert('role modal starts both independent catalog reads before awaiting',
            starts.join(',') === 'people,works', starts.join(','));
        peopleRelease({ value: [{ id: 'person-1', first_name: 'Ada', last_name: 'Offline' }] });
        await Promise.resolve();
        assert('role modal does not settle before the Work catalog is ready', !settled);
        worksRelease({ value: [{ id: 'work-a', title: 'Work A' }] });
        await pending;
        assert('role modal selects the current Work from the projected catalog',
            document.getElementById('role-work-id').value === 'work-a' &&
            document.getElementById('role-work-search').value === 'Work A');
        assert('role modal keeps the effective People catalog',
            Array.isArray(global.allPersons) && global.allPersons.length === 1 &&
            global.allPersons[0].id === 'person-1');

        // The initial network/cache projections may be unavailable. Keep the
        // ordinary fallback functions and the focused Work as an independent
        // preselection source; concurrency must not drop either.
        let fallbackPeople = 0;
        let fallbackWorks = 0;
        global.prksOfflinePeopleFetch = async () => ({ source: 'unavailable', value: null });
        global.prksOfflineWorksBrowseFetch = async () => ({ source: 'unavailable', value: null });
        global.fetchPersons = async () => {
            fallbackPeople += 1;
            return [{ id: 'person-1', first_name: 'Ada', last_name: 'Offline' }];
        };
        global.fetchWorks = async () => {
            fallbackWorks += 1;
            return [{ id: 'work-b', title: 'Other Work' }];
        };
        await actualPrepareRoleModal();
        assert('role modal preserves both unavailable-catalog fallbacks',
            fallbackPeople === 1 && fallbackWorks === 1,
            'people=' + fallbackPeople + ' works=' + fallbackWorks);
        assert('role modal falls back to its focused Work when catalog lacks it',
            document.getElementById('role-work-id').value === 'work-a' &&
            document.getElementById('role-work-search').value === 'Focused Work');

        // A Person route should preselect that Person even without a focused
        // Work, while never guessing a Work association for the new link.
        global.prksOfflinePeopleFetch = async () => ({
            value: [{ id: 'person-1', first_name: 'Ada', last_name: 'Offline' }],
        });
        global.prksOfflineWorksBrowseFetch = async () => ({ value: [] });
        global.prksParseRoute = () => ({ name: 'person', params: { personId: 'person-1' } });
        global.prksFocusedEntity = () => null;
        global.location.hash = '#/people/person-1';
        await actualPrepareRoleModal();
        assert('role modal retains Person-route preselection',
            document.getElementById('role-person-id').value === 'person-1' &&
            document.getElementById('role-person-search').value === 'Ada Offline');
        assert('role modal does not invent a selected Work on the Person route',
            document.getElementById('role-work-id').value === '');
        assert('valid projected catalogs do not fall back to network fetches',
            fallbackPeople === 1 && fallbackWorks === 1);
    } finally {
        for (const [key, value] of Object.entries(originals)) global[key] = value;
        if (global.location) global.location.hash = previousHash || '#/folders';
    }
}

function focus(tabId) { focusedId = tabId; }
function panel() { return document.getElementById('panel-content'); }
function ownPanel(ctx) {
    const node = panel();
    node.dataset.prksOwnerTabId = String(ctx.tabId);
    node.dataset.prksOwnerGeneration = String(ctx.generation);
}
function showWork(ctx, workId, roles) {
    const work = {
        id: workId,
        title: workId,
        roles: roles || [],
    };
    ctx.setEntity('work', work);
    const route = { name: 'work', params: { workId: workId } };
    ctx.route = route;
    ctx.lastResolvedRoute = route;
    return work;
}
function fillRole(personId, workId) {
    document.getElementById('role-person-id').value = personId;
    document.getElementById('role-work-id').value = workId;
    document.getElementById('role-type').value = 'Author';
}
function saveClick() {
    return document.getElementById('save-role-btn').onclick();
}
async function until(pred) {
    for (let i = 0; i < 30; i += 1) {
        if (pred()) return;
        await new Promise(function (resolve) { setTimeout(resolve, 0); });
    }
    throw new Error('timed out waiting for the role save to reach its base read');
}
function resetModal() {
    const modal = document.getElementById('role-modal');
    modal.classList.add('hidden');
    modal.dataset.prksOpenGeneration = '0';
    global.__prksRoleModalOrigin = null;
}

async function openedOnAThenOwnerChangesBeforeSave() {
    writes.length = 0;
    resetModal();
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    showWork(main, 'work-a');
    ownPanel(main);
    openModal('role-modal');
    fillRole('person-1', 'work-a');
    main.beginRoute({ name: 'work', params: { workId: 'work-b' } });
    showWork(main, 'work-b');
    ownPanel(main);
    await saveClick();
    assert('role modal opened on A does not write after the owner changes', writes.length === 0,
        'writes=' + writes.length);
    prksDestroyAllTabContexts();
}

async function differentTargetChangesDuringBaseRead() {
    writes.length = 0;
    roleAlerts.length = 0;
    resetModal();
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    showWork(main, 'work-a');
    ownPanel(main);
    openModal('role-modal');
    fillRole('person-1', 'work-c');
    let release;
    readStarted = 0;
    readGate = new Promise(function (resolve) { release = resolve; });
    const pending = saveClick();
    await until(function () { return readStarted >= 1; });
    main.beginRoute({ name: 'work', params: { workId: 'work-b' } });
    showWork(main, 'work-b');
    ownPanel(main);
    release();
    readGate = null;
    await pending;
    assert('a deferred base read does not write after the opener changes', writes.length === 0,
        'writes=' + writes.length);
    assert('a stale opener does not show the offline-prep alert',
        roleAlerts.length === 0 && roleAlerts.every(function (message) {
            return message.indexOf('Open it once') === -1;
        }),
        roleAlerts.join(' | '));
    prksDestroyAllTabContexts();
}

async function focusMoveLeavesSecondaryMode() {
    writes.length = 0;
    resetModal();
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    const side = prksMountTabContext('side', document.getElementById('side-host'));
    showWork(main, 'work-a');
    showWork(side, 'work-b');
    side.ui.workDetailsMode = 'tags';
    const mainMode = main.ui.workDetailsMode;
    ownPanel(main);
    openModal('role-modal');
    fillRole('person-1', 'work-a');
    let release;
    readStarted = 0;
    readGate = new Promise(function (resolve) { release = resolve; });
    const pending = saveClick();
    await until(function () { return readStarted >= 1; });
    focus('side');
    ownPanel(side);
    release();
    readGate = null;
    await pending;
    assert('a parked opener still settles the durable role write', writes.length === 1,
        'writes=' + writes.length);
    assert('Secondary workDetailsMode stays put', side.ui.workDetailsMode === 'tags',
        'mode=' + side.ui.workDetailsMode);
    assert('Main workDetailsMode stays put', main.ui.workDetailsMode === mainMode,
        'mode=' + main.ui.workDetailsMode);
    assert('the role modal stays open for the parked owner',
        !document.getElementById('role-modal').classList.contains('hidden'));
    prksDestroyAllTabContexts();
}

async function staleCreditAndUnlinkDoNotSave() {
    let durable = 0;
    const real = global.prksSaveWorkPersonRoleDurably;
    global.prksSaveWorkPersonRoleDurably = async function () {
        durable += 1;
        return real.apply(this, arguments);
    };
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    showWork(main, 'work-a');
    ownPanel(main);
    const credit = document.getElementById('credit-btn');
    credit.setAttribute('data-work-id', 'work-a');
    credit.setAttribute('data-person-id', 'person-1');
    credit.setAttribute('data-role-type', 'Author');
    credit.setAttribute('data-canonical-name', 'Jane Doe');
    credit.setAttribute('data-display-name', 'Jane');
    global.prksPromptTextDialog = async function () {
        main.beginRoute({ name: 'work', params: { workId: 'work-b' } });
        showWork(main, 'work-b');
        ownPanel(main);
        return 'Pen Name';
    };
    await prksEditRoleCreditOnWork(credit);
    assert('a stale credit prompt does not save', durable === 0, 'calls=' + durable);

    showWork(main, 'work-a');
    ownPanel(main);
    const unlink = document.getElementById('unlink-btn');
    unlink.setAttribute('data-work-id', 'work-a');
    unlink.setAttribute('data-person-id', 'person-1');
    unlink.setAttribute('data-role-type', 'Author');
    global.prksConfirmDestructive = async function () {
        main.beginRoute({ name: 'work', params: { workId: 'work-b' } });
        showWork(main, 'work-b');
        ownPanel(main);
        return true;
    };
    await prksRemoveWorkRoleLink(unlink);
    assert('a stale unlink confirmation does not save', durable === 0, 'calls=' + durable);
    global.prksSaveWorkPersonRoleDurably = real;
    global.prksConfirmDestructive = async function () { return false; };
    prksDestroyAllTabContexts();
}

async function personUnlinkDuringBaseReadDoesNotSave() {
    writes.length = 0;
    const messages = [];
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    main.setEntity('person', { id: 'person-1', works: [{ id: 'work-a', title: 'File A' }] });
    const route = { name: 'person', params: { personId: 'person-1' } };
    main.route = route;
    main.lastResolvedRoute = route;
    ownPanel(main);
    const unlink = document.getElementById('unlink-btn');
    unlink.setAttribute('data-work-id', 'work-a');
    unlink.setAttribute('data-person-id', 'person-1');
    unlink.setAttribute('data-role-type', 'Author');
    global.prksConfirmDestructive = async function () { return true; };
    global.prksAlertMessage = async function (message) {
        messages.push(String(message || ''));
    };
    let release;
    readStarted = 0;
    readGate = new Promise(function (resolve) { release = resolve; });
    const pending = prksRemoveWorkRoleLink(unlink);
    await until(function () { return readStarted >= 1; });
    main.beginRoute({ name: 'person', params: { personId: 'person-2' } });
    const replaced = { name: 'person', params: { personId: 'person-2' } };
    main.route = replaced;
    main.lastResolvedRoute = replaced;
    main.setEntity('person', { id: 'person-2', works: [] });
    ownPanel(main);
    release();
    readGate = null;
    await pending;
    assert('a replaced Person owner does not unlink after the base read',
        writes.length === 0 && messages.length === 0,
        'writes=' + writes.length + ' alerts=' + messages.join(' | '));
    global.prksConfirmDestructive = async function () { return false; };
    global.prksAlertMessage = async function () {};
    prksDestroyAllTabContexts();
}

async function olderRoleModalDoesNotSettleNewerOpening() {
    writes.length = 0;
    roleAlerts.length = 0;
    resetModal();
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    showWork(main, 'work-a');
    ownPanel(main);
    openModal('role-modal');
    fillRole('person-1', 'work-a');
    const firstGeneration = document.getElementById('role-modal').dataset.prksOpenGeneration;
    const realSave = global.prksSync.store.saveWorkPersonRole;
    let failWrites = 1;
    global.prksSync.store.saveWorkPersonRole = async function () {
        if (failWrites > 0) {
            failWrites -= 1;
            throw new Error('role save failed');
        }
        return realSave.apply(this, arguments);
    };
    let releaseFirst;
    let releaseSecond;
    readStarted = 0;
    readGate = new Promise(function (resolve) { releaseFirst = resolve; });
    const firstSave = saveClick();
    await until(function () { return readStarted >= 2; });
    readGate = new Promise(function (resolve) { releaseSecond = resolve; });
    openModal('role-modal');
    fillRole('person-1', 'work-a');
    const secondSave = saveClick();
    await until(function () { return readStarted >= 4; });
    releaseFirst();
    await firstSave;
    const button = document.getElementById('save-role-btn');
    const modal = document.getElementById('role-modal');
    assert('an older role-modal failure leaves the newer opening busy',
        button.disabled === true && button.getAttribute('aria-busy') === 'true' &&
        String(button.textContent || '').indexOf('Linking') !== -1,
        'disabled=' + button.disabled + ' aria=' + button.getAttribute('aria-busy') +
        ' label=' + button.textContent);
    assert('an older role-modal failure does not alert into the newer opening',
        roleAlerts.length === 0, roleAlerts.join(' | '));
    assert('an older role-modal failure does not close the newer opening',
        !modal.classList.contains('hidden') &&
        String(modal.dataset.prksOpenGeneration) !== String(firstGeneration),
        'generation=' + modal.dataset.prksOpenGeneration);
    releaseSecond();
    readGate = null;
    await secondSave;
    global.prksSync.store.saveWorkPersonRole = realSave;
    prksDestroyAllTabContexts();
}

async function mountedSaveRefusesReplacedOwner() {
    writes.length = 0;
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    showWork(main, 'work-a');
    ownPanel(main);
    let release;
    main.registerResource(main.resourceTicket(), {
        kind: 'workRoleEditor',
        value: {
            workId: 'work-a',
            generation: main.generation,
            observed: { work_id: 'work-a', scopes: [] },
            preparing: new Promise(function (resolve) { release = resolve; }),
            editable: true,
            operations: [],
        },
        suspendable: false,
        dispose: function () {},
    });
    const pending = prksSaveWorkPersonRoleDurably('work-a', 'person-1', 'Author', '', null, null);
    main.beginRoute({ name: 'work', params: { workId: 'work-b' } });
    showWork(main, 'work-b');
    release();
    const replaced = await pending;
    assert('a replaced generation does not call store.saveWorkPersonRole',
        writes.length === 0 && replaced.code !== 'saved',
        'code=' + replaced.code + ' writes=' + writes.length);

    writes.length = 0;
    showWork(main, 'work-a');
    ownPanel(main);
    let releasePanel;
    main.registerResource(main.resourceTicket(), {
        kind: 'workRoleEditor',
        value: {
            workId: 'work-a',
            generation: main.generation,
            observed: { work_id: 'work-a', scopes: [] },
            preparing: new Promise(function (resolve) { releasePanel = resolve; }),
            editable: true,
            operations: [],
        },
        suspendable: false,
        dispose: function () {},
    });
    const pendingPanel = prksSaveWorkPersonRoleDurably('work-a', 'person-1', 'Author', '', null, null);
    panel().dataset.prksOwnerTabId = 'side';
    releasePanel();
    const otherPanel = await pendingPanel;
    assert('a replaced panel owner does not call store.saveWorkPersonRole',
        writes.length === 0 && otherPanel.code !== 'saved',
        'code=' + otherPanel.code + ' writes=' + writes.length);
    prksDestroyAllTabContexts();
}

function ownedReadPublishesEffectiveRoles() {
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    const side = prksMountTabContext('side', document.getElementById('side-host'));
    const workA = showWork(main, 'work-a', [{
        id: 'person-1', role_type: 'Author', credit_name: '',
        first_name: 'Ada', last_name: 'Lovelace', order_index: 0,
    }]);
    const workB = showWork(side, 'work-b', []);
    main.ui.workDetailsMode = 'view';
    side.ui.workDetailsMode = 'view';
    ownPanel(main);
    prksSetPendingWorkRoles([{
        operation: 'ADD_WORK_PERSON_ROLE',
        entity_type: 'work',
        entity_id: 'work-a',
        status: 'pending',
        payload: { person_id: 'person-2', role_type: 'Editor', credit_name: '' },
        local_context: {
            person: {
                id: 'person-2', first_name: 'Grace', last_name: 'Hopper',
                canonical_name: 'Grace Hopper',
            },
        },
    }]);
    const painted = prksRefreshOwnedWorkPanelRead(main, workA);
    const request = panel().__prksWorkPanelReadRequest;
    const ids = request && request.effectiveWork && Array.isArray(request.effectiveWork.roles)
        ? request.effectiveWork.roles.map(function (role) { return String(role.id); })
        : [];
    assert('owned refresh publishes Work A for Main',
        painted === true && request && request.workId === 'work-a' && request.ownerTabId === 'main',
        JSON.stringify(request && { workId: request.workId, ownerTabId: request.ownerTabId }));
    assert('owned refresh includes the pending role for that Work',
        ids.indexOf('person-2') !== -1, 'ids=' + ids.join(','));
    const sidePainted = prksRefreshOwnedWorkPanelRead(side, workB);
    assert('a different owner does not replace the published read',
        sidePainted === false && panel().__prksWorkPanelReadRequest === request);
    prksSetPendingWorkRoles([]);
    prksDestroyAllTabContexts();
}

async function tagsModeUpdatesClassicHost() {
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    showWork(main, 'work-a', [{
        id: 'person-1', role_type: 'Author', credit_name: '',
        first_name: 'Ada', last_name: 'Lovelace', order_index: 0,
    }]);
    main.ui.workDetailsMode = 'tags';
    ownPanel(main);
    const node = panel();
    node.innerHTML = '<div class="work-linked-persons-by-role">OLD</div>';
    prksMountWorkRoleEditor(main, 'work-a', { editable: false });
    let html = 'OLD';
    for (let i = 0; i < 50; i += 1) {
        const host = node.querySelector('.work-linked-persons-by-role');
        html = host ? host.innerHTML : '';
        if (html && html !== 'OLD') break;
        await new Promise(function (resolve) { setTimeout(resolve, 10); });
    }
    assert('tags mode still rewrites the classic people host',
        html !== 'OLD' && html.indexOf('Ada') !== -1, html.slice(0, 180));
    prksDestroyAllTabContexts();
}

void (async function () {
    try {
        await roleModalCatalogsLoadConcurrently();
        await openedOnAThenOwnerChangesBeforeSave();
        await differentTargetChangesDuringBaseRead();
        await focusMoveLeavesSecondaryMode();
        await staleCreditAndUnlinkDoNotSave();
        await personUnlinkDuringBaseReadDoesNotSave();
        await olderRoleModalDoesNotSettleNewerOpening();
        await mountedSaveRefusesReplacedOwner();
        ownedReadPublishesEffectiveRoles();
        await tagsModeUpdatesClassicHost();
    } catch (error) {
        failed += 1;
        console.log('FAIL  runtime harness ' + (error && error.stack ? error.stack : error));
    }
    console.log(passed + ' checks passed');
    if (failed) process.exit(1);
})();
