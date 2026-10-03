#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { installMiniDocument } = require('./mini_document');

const rootDir = path.resolve(__dirname, '../..');
const EDITOR_KINDS = [
    'workRoleEditor',
    'workTagEditor',
    'workSourceEditor',
    'workMetadataEditor',
    'folderTagEditor',
];

installMiniDocument([
    '<div id="app-container">',
    '<div id="right-panel">',
    '<div class="tabs">',
    '<button class="tab-btn" data-target="details" type="button">Details</button>',
    '<button class="tab-btn" data-target="annotations" type="button">Annotations</button>',
    '</div>',
    '<div id="panel-content"></div>',
    '</div>',
    '<div id="main-host"></div>',
    '<div id="side-host"></div>',
    '<div id="page-content"></div>',
    '<div id="prks-tab-warm-parking"></div>',
    '</div>',
].join(''));

const listeners = [];
let subscribeCount = 0;
let unsubscribeCount = 0;
let doubleUnsub = 0;
const roleWrites = [];
const tagWrites = [];
let resolveConflictCalls = 0;
let processListenerMark = [];

function track(fn) {
    subscribeCount += 1;
    const rec = { fn: fn, dead: false };
    listeners.push(rec);
    return function stop() {
        if (rec.dead) {
            doubleUnsub += 1;
            throw new Error('double unsubscribe');
        }
        rec.dead = true;
        unsubscribeCount += 1;
    };
}

global.prksSync = {
    subscribe: function (fn) { return track(fn); },
    changed: function () {},
    store: {
        listOperations: async function () { return []; },
        saveWorkPersonRole: async function () {
            roleWrites.push(Array.prototype.slice.call(arguments));
        },
        coalesceWorkTag: async function () {
            tagWrites.push(Array.prototype.slice.call(arguments));
        },
        resolveConflict: async function () {
            resolveConflictCalls += 1;
        },
    },
};
global.prksOfflineRuntimeSubscribe = function (fn) { return track(fn); };
global.prksOfflineRuntimeState = function () { return 'online'; };
global.prksPendingWorkCreates = function () { return []; };
global.prksWorkFieldUtf8Bytes = function (value) {
    return Buffer.byteLength(String(value || ''), 'utf8');
};
global.PRKS_MAX_CREDIT_NAME_BYTES = 1000;
global.prksOfflineDomainGeneration = function () { return 1; };
global.prksReadWorkTagOptions = async function () {
    return { value: { assigned: [], known_absent: {} } };
};
global.prksReadFolderTagOptions = async function () {
    return { value: { assigned: [], known_absent: {} } };
};
global.prksReadTagsIndex = async function () {
    return { value: [{ id: 'tag-1', name: 'Alpha' }] };
};
global.prksWorkTagBase = function () { return { present: false, revision: 0 }; };
global.prksFolderTagBase = function () { return { present: false, revision: 0 }; };
global.prksRefreshPendingWorkRoles = async function () { return []; };
global.prksWorkRoleOperations = function () { return []; };
global.prksEffectiveWorkDetailRoles = function (work) { return work; };
global.prksRefreshOwnedWorkPanelRead = function () { return false; };
global.buildWorkLinkedPersonsHtml = function () {
    rolePaints += 1;
    return '<span data-prks-role-paint="1">roles</span>';
};
global.prksEffectiveWorkTags = function (work) { return (work && work.tags) || []; };
global.prksEffectiveFolderTags = function (folder) { return (folder && folder.tags) || []; };
global.prksRefreshOwnedWorkPanelTags = function () { return null; };
global.prksRefreshPendingWorkSources = async function () { return []; };
global.prksWorkSourceOperations = function () { return []; };
global.prksRefreshPendingWorkMetadata = async function () { return []; };
global.prksWorkMetadataFieldOperations = function () { return []; };
global.prksEffectiveWorkMetadata = function (work) { return work || {}; };
global.prksPendingWorkMetadataState = function () { return 'ready'; };
global.prksReadWorkMetadataState = async function () {
    return { value: { fields: { title: 'Alpha' } } };
};

let readGate = null;
let readStarted = 0;
global.prksOfflineReadEntity = async function (kind, id) {
    readStarted += 1;
    if (readGate && kind === 'work-people-state') await readGate;
    if (kind === 'work-people-state') {
        return { source: 'cache', value: { work_id: id, scopes: [] } };
    }
    return { source: 'cache', value: { id: id, roles: [], tags: [] } };
};

let focusedId = 'main';
let mainId = 'main';
global.prksWorkspaceSnapshot = function () {
    return { focusedTabId: focusedId, mainTabId: mainId };
};

let pdfInits = 0;
let rolePaints = 0;
global.initPdfViewerForWork = function () { pdfInits += 1; };

global.prksOwnerResource = require(path.join(rootDir, 'frontend/js/owner-resource.js'));
require(path.join(rootDir, 'frontend/js/tab-context.js'));
(0, eval)(fs.readFileSync(path.join(rootDir, 'frontend/js/ui.js'), 'utf8'));

global.renderWorkMetaTab = function () {
    return '<div id="work-tags-list"></div>' +
        '<div class="work-linked-persons-by-role"></div>' +
        '<input id="work-tag-search" />' +
        '<div id="work-tag-search-results"></div>' +
        '<div data-prks-role="work-source-editor"></div>' +
        '<div data-prks-role="work-bib-rows"></div>';
};
global.renderPrksPrivateNotesCard = function () { return ''; };
global.initPrksPrivateNotesEditor = function () {};
global.prksPublishWorkPanelRead = function () {};
global.prksEffectiveWorkTags = function (work) { return (work && work.tags) || []; };
global.prksEffectiveFolderTags = function (folder) { return (folder && folder.tags) || []; };
global.prksEffectiveWorkMetadata = function (work) { return work || {}; };
global.prksWorkBibRowsHtml = function () { return ''; };
global.buildWorkLinkedPersonsHtml = function () {
    rolePaints += 1;
    return '<span data-prks-role-paint="1">roles</span>';
};

require(path.join(rootDir, 'frontend/js/work-role-editor.js'));
require(path.join(rootDir, 'frontend/js/work-tag-editor.js'));
require(path.join(rootDir, 'frontend/js/folder-tag-editor.js'));
require(path.join(rootDir, 'frontend/js/work-source-editor.js'));
require(path.join(rootDir, 'frontend/js/work-metadata-editor.js'));

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
function assertEq(name, got, want) {
    const ok = got === want;
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function panel() { return document.getElementById('panel-content'); }
function focus(tabId) { focusedId = tabId; }
function own(ctx) {
    focus(ctx.tabId);
    mainId = ctx.tabId;
    const node = panel();
    node.dataset.prksOwnerTabId = String(ctx.tabId);
    node.dataset.prksOwnerGeneration = String(ctx.generation);
}
function hosts() {
    const node = panel();
    node.innerHTML = [
        '<div id="work-tags-list"></div>',
        '<input id="work-tag-search" />',
        '<div id="work-tag-search-results"></div>',
        '<div class="work-linked-persons-by-role"></div>',
        '<div id="folder-panel-tags-list"></div>',
        '<input id="folder-tag-search" />',
        '<div id="folder-tag-search-results"></div>',
        '<div data-prks-role="work-source-editor"></div>',
        '<div data-prks-role="work-bib-rows"></div>',
    ].join('');
    return node;
}
function workRecord(id) {
    return { id: id, title: id, tags: [], roles: [], private_notes: '', source_url: '' };
}
function showWork(ctx, workId) {
    const work = workRecord(workId);
    ctx.setEntity('work', work);
    const route = { name: 'work', params: { workId: workId }, hash: '#/works/' + workId };
    ctx.route = route;
    ctx.lastResolvedRoute = route;
    ctx.ui.workDetailsMode = 'view';
    ctx.ui.rightPanelTab = 'details';
    return work;
}
function mountTab(tabId, hostId) {
    const host = document.getElementById(hostId || 'main-host');
    return prksMountTabContext(tabId, host);
}
function liveSubs() {
    return listeners.filter(function (rec) { return !rec.dead; }).length;
}
async function flush() {
    for (let i = 0; i < 8; i += 1) {
        await new Promise(function (resolve) { setTimeout(resolve, 0); });
    }
}
function source(rel) {
    return fs.readFileSync(path.join(rootDir, rel), 'utf8');
}
function mountSlice(text, start, end) {
    const at = text.indexOf(start);
    const stop = text.indexOf(end, at + start.length);
    return text.slice(at, stop === -1 ? undefined : stop);
}

function findButton(label) {
    const buttons = panel().querySelectorAll('button');
    for (let i = 0; i < buttons.length; i += 1) {
        if (buttons[i].textContent === label) return buttons[i];
    }
    return null;
}

function emitLive(event) {
    listeners.forEach(function (rec) {
        if (rec.dead || processListenerMark.indexOf(rec) !== -1) return;
        rec.fn(event);
    });
}

async function replaceDuringTagReconcile() {
    prksDestroyAllTabContexts();
    hosts();
    resolveConflictCalls = 0;
    const workId = 'work-tag-c';
    const ctx = mountTab('tag-conflict');
    showWork(ctx, workId);
    ctx.setEntity('work', Object.assign(workRecord(workId), { tags: [] }));
    own(ctx);
    const op = {
        op_id: 'op-tag-1',
        entity_type: 'work',
        entity_id: workId,
        operation: 'ADD_WORK_TAG',
        status: 'conflict',
        payload: { tag_id: 't1' },
        local_context: { tag: { id: 't1', name: 'Server Tag' } },
        server_result: { code: 'REVISION_CONFLICT', current_state: true, current_revision: 4 },
    };
    const previousList = prksSync.store.listOperations;
    prksSync.store.listOperations = async function () { return [op]; };
    let releaseGate = null;
    let reconcileStarted = 0;
    const gate = new Promise(function (resolve) { releaseGate = resolve; });
    const previousReconcile = global.prksOfflineReconcileWorkTag;
    global.prksOfflineReconcileWorkTag = async function () {
        reconcileStarted += 1;
        await gate;
        return true;
    };
    try {
        prksMountWorkTags(ctx, workId);
        await flush();
        const state = ctx.getResource('workTagEditor');
        state.error = 'kept';
        state.options = { sentinel: 'old' };
        const button = findButton('Use server state');
        assert('work tag conflict exposes Use server state', !!button);
        if (!button) return;
        const pending = button.onclick();
        await Promise.resolve();
        const fresh = {
            workId: workId,
            generation: ctx.generation,
            options: { sentinel: 'fresh' },
            error: 'kept',
            operations: [],
        };
        const replaced = ctx.registerResource(ctx.resourceTicket(), {
            kind: 'workTagEditor',
            value: fresh,
            suspendable: false,
            dispose: function () {},
        });
        assertEq('work tag replacement during reconciliation reports replaced', replaced, 'replaced');
        releaseGate(true);
        await pending;
        await flush();
        assertEq('work tag reconciliation started', reconcileStarted, 1);
        assertEq('replaced work tag session does not resolve the conflict', resolveConflictCalls, 0);
        assertEq('replaced work tag session does not clear the old error', state.error, 'kept');
        assertEq('replaced work tag session does not clear the old options', state.options.sentinel, 'old');
        assertEq('replaced work tag session leaves the fresh options', fresh.options.sentinel, 'fresh');
        assertEq('replaced work tag session leaves the fresh error', fresh.error, 'kept');
        assertEq('replaced work tag session does not patch tags', ctx.getEntity('work').tags.length, 0);
    } finally {
        prksSync.store.listOperations = previousList;
        global.prksOfflineReconcileWorkTag = previousReconcile;
        prksDestroyTabContext('tag-conflict');
    }
}

async function warmParkDuringFolderReconcile() {
    prksDestroyAllTabContexts();
    hosts();
    resolveConflictCalls = 0;
    const folderId = 'folder-c';
    const ctx = mountTab('folder-conflict');
    ctx.setEntity('folder', { id: folderId, tags: [] });
    own(ctx);
    const pdf = { id: 'folder-pdf', resize: function () {} };
    ctx.registerResource(ctx.resourceTicket(), {
        kind: 'pdf', value: pdf, suspendable: true, dispose: function () {},
    });
    const op = {
        op_id: 'op-folder-1',
        entity_type: 'folder',
        entity_id: folderId,
        operation: 'ADD_FOLDER_TAG',
        status: 'conflict',
        payload: { tag_id: 't9' },
        local_context: { tag: { id: 't9', name: 'Server Folder Tag' } },
        server_result: { code: 'REVISION_CONFLICT', current_state: true, current_revision: 4 },
    };
    const previousList = prksSync.store.listOperations;
    prksSync.store.listOperations = async function () { return [op]; };
    let releaseGate = null;
    let reconcileStarted = 0;
    const gate = new Promise(function (resolve) { releaseGate = resolve; });
    const previousReconcile = global.prksOfflineReconcileFolderTag;
    global.prksOfflineReconcileFolderTag = async function () {
        reconcileStarted += 1;
        await gate;
        return true;
    };
    try {
        prksMountFolderTags(ctx, folderId);
        await flush();
        const state = ctx.getResource('folderTagEditor');
        state.error = 'kept';
        state.options = { sentinel: 'old' };
        const button = findButton('Use server state');
        assert('folder tag conflict exposes Use server state', !!button);
        if (!button) return;
        const pending = button.onclick();
        await Promise.resolve();
        assert('folder conflict warm park disposes the editor', prksWarmParkTabContext(ctx.tabId) === true);
        assertEq('folder tag session is absent while parked', ctx.getResource('folderTagEditor'), undefined);
        const resumed = prksResumeWarmTabContext(ctx.tabId, document.getElementById('main-host'));
        assert('folder conflict resume returns the same owner', resumed === ctx);
        const fresh = {
            folderId: folderId,
            generation: ctx.generation,
            options: { sentinel: 'fresh' },
            error: 'kept',
            operations: [],
        };
        const attached = ctx.registerResource(ctx.resourceTicket(), {
            kind: 'folderTagEditor',
            value: fresh,
            suspendable: false,
            dispose: function () {},
        });
        assertEq('resumed folder owner accepts the new tag session', attached, 'attached');
        releaseGate(true);
        await pending;
        await flush();
        assertEq('folder tag reconciliation started', reconcileStarted, 1);
        assertEq('parked folder tag session does not resolve the conflict', resolveConflictCalls, 0);
        assertEq('parked folder tag session does not clear the old error', state.error, 'kept');
        assertEq('parked folder tag session does not clear the old options', state.options.sentinel, 'old');
        assertEq('parked folder tag session leaves the fresh options', fresh.options.sentinel, 'fresh');
        assertEq('parked folder tag session leaves the fresh error', fresh.error, 'kept');
        assertEq('parked folder tag session does not patch tags', ctx.getEntity('folder').tags.length, 0);
    } finally {
        prksSync.store.listOperations = previousList;
        global.prksOfflineReconcileFolderTag = previousReconcile;
        prksDestroyTabContext('folder-conflict');
    }
}

function armMetadataSession(ctx, workId) {
    showWork(ctx, workId);
    ctx.ui.workDetailsMode = 'metadata';
    ctx.ui.workMetaEditSession = 4;
    ctx.ui.workMetaDraftWorkId = workId;
    ctx.ui.workMetaDraft = { title: 'Local Title' };
    ctx.setEntity('work', Object.assign(workRecord(workId), { title: 'Local Title' }));
    own(ctx);
}

function metadataConflictOp(workId, opId) {
    return {
        op_id: opId,
        entity_type: 'work',
        entity_id: workId,
        operation: 'SET_WORK_METADATA_FIELD',
        status: 'conflict',
        payload: { field: 'title', value: 'Local Title' },
        server_result: { current_value: 'Server Title', current_revision: 4 },
    };
}

async function metadataResolveDuringGate(tabId, workId, opId, during) {
    prksDestroyAllTabContexts();
    hosts();
    resolveConflictCalls = 0;
    const ctx = mountTab(tabId);
    armMetadataSession(ctx, workId);
    let releaseGate = null;
    let reconcileStarted = 0;
    const gate = new Promise(function (resolve) { releaseGate = resolve; });
    const previousReconcile = global.prksOfflineReconcileWorkField;
    global.prksOfflineReconcileWorkField = async function () {
        reconcileStarted += 1;
        await gate;
        return true;
    };
    try {
        prksMountWorkMetadataEditor(ctx, workId);
        await flush();
        const state = ctx.getResource('workMetadataEditor');
        state.operations = [metadataConflictOp(workId, opId)];
        state.observed = { fields: { title: { revision: 1, value: 'Local Title' } } };
        state.error = 'kept';
        state.readVersion = 2;
        const pending = prksResolveWorkMetadataFieldConflict(opId, false, 'bib');
        await Promise.resolve();
        await during(ctx, state);
        releaseGate(true);
        await pending;
        await flush();
        assertEq(tabId + ' reconciliation started', reconcileStarted, 1);
        assertEq(tabId + ' does not resolve the conflict', resolveConflictCalls, 0);
        const current = ctx.getResource('workMetadataEditor');
        if (current && current !== state) {
            assertEq(tabId + ' leaves the fresh observed title', current.observed.fields.title.value, 'Fresh');
            assertEq(tabId + ' leaves the fresh error', current.error, 'kept');
        }
        assertEq(tabId + ' does not change the old read', state.readVersion, 2);
        assertEq(tabId + ' does not change the old error', state.error, 'kept');
        assertEq(tabId + ' does not change the old observed title', state.observed.fields.title.value, 'Local Title');
        assertEq(tabId + ' does not patch the work title', ctx.getEntity('work').title, 'Local Title');
    } finally {
        global.prksOfflineReconcileWorkField = previousReconcile;
        prksDestroyTabContext(tabId);
    }
}

async function warmAcknowledgementSurvivesActivation() {
    const previousSnapshot = global.prksWorkspaceSnapshot;
    const previousHtml = global.buildWorkLinkedPersonsHtml;
    const previousPublish = global.prksPublishWorkPanelRead;
    const previousList = prksSync.store.listOperations;
    if (typeof createPrksWorkspaceTabs !== 'function') {
        require(path.join(rootDir, 'frontend/js/workspace-tree.js'));
        require(path.join(rootDir, 'frontend/js/workspace-tabs.js'));
    }
    require(path.join(rootDir, 'frontend/js/work-role-state.js'));
    global.buildWorkLinkedPersonsHtml = function (work) {
        rolePaints += 1;
        const roles = (work && work.roles) || [];
        return roles.map(function (role) {
            return '<span class="credit">' + (role && role.credit_name || '') + '</span>';
        }).join('');
    };
    global.prksPublishWorkPanelRead = function (_ctx, work) {
        const panelNode = document.getElementById('panel-content');
        const host = panelNode && panelNode.querySelector('.work-linked-persons-by-role');
        if (!host) return;
        const effective = typeof prksEffectiveWorkDetailRoles === 'function'
            ? prksEffectiveWorkDetailRoles(work) : work;
        const roles = (effective && effective.roles) || [];
        host.innerHTML = roles.map(function (role) {
            return '<span class="credit">' + (role && role.credit_name || '') + '</span>';
        }).join('');
    };
    const pendingOp = {
        op_id: 'op-role-park',
        entity_type: 'work',
        entity_id: 'WA',
        operation: 'ADD_WORK_PERSON_ROLE',
        status: 'pending',
        payload: { person_id: 'p1', role_type: 'Author', credit_name: 'Pending Ada' },
        local_context: { person: { id: 'p1', first_name: 'Ada', last_name: 'Lovelace' } },
    };
    let roleOps = [pendingOp];
    prksSync.store.listOperations = async function () { return roleOps.slice(); };
    let hash = '#/works/WA';
    let href = 'http://127.0.0.1/#/works/WA';
    let historyState = null;
    const hist = {
        getHash: function () { return hash; },
        getHref: function () { return href; },
        getState: function () { return historyState; },
        pushState: function (next, url) {
            historyState = next;
            href = String(url);
            const at = href.indexOf('#');
            if (at >= 0) hash = href.slice(at);
        },
        replaceState: function (next, url) {
            historyState = next;
            href = String(url);
            const at = href.indexOf('#');
            if (at >= 0) hash = href.slice(at);
        },
    };
    let renders = 0;
    const ws = createPrksWorkspaceTabs({
        parseRoute: function (raw) {
            const value = raw || '#/folders';
            const work = /^#\/works\/([^/?#]+)/.exec(value);
            if (work) {
                return {
                    canonicalHash: '#/works/' + work[1],
                    hash: value,
                    name: 'work',
                    params: { workId: work[1] },
                };
            }
            return { canonicalHash: value, hash: value, name: 'folders', params: {} };
        },
        routeLoadingTitle: function () { return 'Loading'; },
        routeTabIcon: function () { return 'file-text'; },
        homeHash: '#/folders',
        historyAdapter: hist,
        supportsTile: function () { return false; },
        canLeave: function () { return true; },
        loadSnapshot: function () { return null; },
        renderRoute: function () { renders += 1; },
        refreshFocusedPanel: function () { prksRefreshFocusedRightPanel(); },
        publishMainShell: function () {},
        announce: function () {},
        onChange: function () {},
    });
    try {
        prksDestroyAllTabContexts();
        hosts();
        pdfInits = 0;
        ws.bootstrap('#/works/WA');
        global.prksWorkspaceSnapshot = function () { return ws.snapshot(); };
        const snap = ws.snapshot();
        const tabA = snap.mainTabId;
        const ctx = prksGetTabContext(tabA);
        showWork(ctx, 'WA');
        ctx.setEntity('work', Object.assign(workRecord('WA'), { roles: [] }));
        const pdf = { id: 'pdf-wa', resized: 0, resize: function () { pdf.resized += 1; } };
        ctx.registerResource(ctx.resourceTicket(), {
            kind: 'pdf', value: pdf, suspendable: true, dispose: function () {},
        });
        prksRefreshFocusedRightPanel();
        await flush();
        const hostBefore = panel().querySelector('.work-linked-persons-by-role');
        assert('setup refresh paints the pending credit', !!hostBefore && hostBefore.innerHTML.indexOf('Pending Ada') !== -1);
        assert('setup refresh mounts the role editor', !!ctx.getResource('workRoleEditor'));
        const opened = await ws.openTab('#/works/WB', { activate: false });
        await ws.activateTab(opened.id);
        await flush();
        assert('activation warm-parks the PDF work', ctx.suspended === true && ctx.getResource('pdf') === pdf);
        assertEq('warm park drops the role editor', ctx.getResource('workRoleEditor'), undefined);
        assert('parked panel still shows the pending credit',
            hostBefore.innerHTML.indexOf('Pending Ada') !== -1 && hostBefore.innerHTML.indexOf('Server Ada') === -1);
        roleOps = [];
        emitLive({
            operation: 'ADD_WORK_PERSON_ROLE',
            acknowledged: {
                work_id: 'WA',
                person_id: 'p1',
                role_type: 'Author',
                present: true,
                credit_name: 'Server Ada',
                first_name: 'Ada',
                last_name: 'Lovelace',
                server_revision: 3,
            },
        });
        const parkedRoles = ctx.getEntity('work').roles;
        assertEq('parked acknowledgement patches the work credit',
            parkedRoles && parkedRoles[0] && parkedRoles[0].credit_name, 'Server Ada');
        assert('parked acknowledgement does not refresh the panel',
            hostBefore.innerHTML.indexOf('Pending Ada') !== -1);
        const rendersBeforeResume = renders;
        await ws.activateTab(tabA);
        await flush();
        const hostAfter = panel().querySelector('.work-linked-persons-by-role');
        const afterHtml = hostAfter ? hostAfter.innerHTML : '';
        const effective = prksEffectiveWorkDetailRoles(ctx.getEntity('work'));
        const effectiveCredit = effective && effective.roles && effective.roles[0]
            ? effective.roles[0].credit_name : '';
        assertEq('resumed entity keeps the acknowledged credit',
            ctx.getEntity('work').roles[0].credit_name, 'Server Ada');
        assert('resumed panel shows the acknowledged credit', afterHtml.indexOf('Server Ada') !== -1);
        assert('resumed panel drops the pending credit', afterHtml.indexOf('Pending Ada') === -1);
        assertEq('resumed panel has no pending role overlay', effectiveCredit, 'Server Ada');
        assertEq('warm resume keeps the PDF object', ctx.getResource('pdf'), pdf);
        assertEq('warm resume does not init a PDF viewer', pdfInits, 0);
        assertEq('workspace activation does not route-render the resumed work', renders, rendersBeforeResume);
    } finally {
        global.prksWorkspaceSnapshot = previousSnapshot;
        global.buildWorkLinkedPersonsHtml = previousHtml;
        global.prksPublishWorkPanelRead = previousPublish;
        prksSync.store.listOperations = previousList;
        if (typeof prksRefreshPendingWorkRoles === 'function') {
            roleOps = [];
            await prksRefreshPendingWorkRoles();
        }
        prksDestroyAllTabContexts();
    }
}

async function main() {
    processListenerMark = listeners.slice();
    function editorLive() {
        return listeners.filter(function (rec) {
            return !rec.dead && processListenerMark.indexOf(rec) === -1;
        }).length;
    }
    const unhandled = [];
    process.on('unhandledRejection', function (error) {
        unhandled.push(error && error.stack ? error.stack : String(error));
    });

    const editorFiles = {
        workRoleEditor: 'frontend/js/work-role-editor.js',
        workTagEditor: 'frontend/js/work-tag-editor.js',
        workSourceEditor: 'frontend/js/work-source-editor.js',
        workMetadataEditor: 'frontend/js/work-metadata-editor.js',
        folderTagEditor: 'frontend/js/folder-tag-editor.js',
    };
    EDITOR_KINDS.forEach(function (kind) {
        const text = source(editorFiles[kind]);
        assert(kind + ' production mount does not call setResource', text.indexOf('setResource(') === -1);
        const mount = mountSlice(text, 'function mount(', 'async function');
        const ticketAt = mount.indexOf('ctx.resourceTicket()');
        const registerAt = mount.indexOf('ctx.registerResource(ticket');
        assert(kind + ' captures a ticket before register', ticketAt !== -1 && registerAt > ticketAt);
        const live = mountSlice(text, 'function live(', 'function ');
        assert(kind + ' liveness requires resource identity',
            live.indexOf("getResource('" + kind + "') === state") !== -1);
    });

    const registrySrc = source('frontend-app/src/lifecycle/owner-resource.ts');
    EDITOR_KINDS.forEach(function (kind) {
        assert(kind + ' is a typed registry kind', registrySrc.indexOf("'" + kind + "'") !== -1);
    });
    assert('editor sessions are non-suspendable in the registry',
        registrySrc.indexOf('isEditorSessionKind(registration.kind) && registration.suspendable === true') !== -1);
    const built = source('frontend/js/owner-resource.js');
    assert('classic owner-resource region is the TypeScript source',
        built.indexOf('src/lifecycle/owner-resource.ts') !== -1);
    EDITOR_KINDS.forEach(function (kind) {
        assert('built registry names ' + kind, built.indexOf('"' + kind + '"') !== -1);
    });

    const tabs = source('frontend/js/workspace-tabs.js');
    const activate = mountSlice(tabs, 'function activateTab', 'function closeTab');
    const resumedAt = activate.indexOf('if (resumed)');
    const resumedBody = activate.slice(resumedAt, activate.indexOf('invokeRender', resumedAt));
    assert('warm resume refresh runs before route render',
        resumedBody.indexOf('refreshFocusedPanel()') !== -1 && resumedBody.indexOf('return true') !== -1);
    const resumeFn = mountSlice(source('frontend/js/tab-context.js'),
        'function prksResumeWarmTabContext', 'function prksUnmountTabContext');
    assert('warm resume does not recreate the PDF viewer', resumeFn.indexOf('initPdfViewerForWork') === -1);
    const panelUpdate = mountSlice(source('frontend/js/ui.js'), 'function updatePanelContent', 'function renderRouteContextSidebar');
    ['prksMountWorkMetadataEditor', 'prksMountWorkSourceEditor', 'prksMountWorkRoleEditor', 'initWorkTagCombobox'].forEach(function (name) {
        assert('focused work panel reconstructs via ' + name, panelUpdate.indexOf(name) !== -1);
    });
    const tagResolve = mountSlice(source('frontend/js/work-tag-editor.js'),
        'await root.prksOfflineReconcileWorkTag', 'await paint(ctx, state)');
    assert('work tag rechecks identity after reconciliation',
        tagResolve.indexOf('if (!live(ctx, state)) return;') !== -1 &&
        tagResolve.indexOf('if (!live(ctx, state)) return;') < tagResolve.indexOf('store.resolveConflict'));
    const folderResolve = mountSlice(source('frontend/js/folder-tag-editor.js'),
        'await root.prksOfflineReconcileFolderTag', 'await paint(ctx, state)');
    assert('folder tag rechecks identity after reconciliation',
        folderResolve.indexOf('if (!live(ctx, state)) return;') !== -1 &&
        folderResolve.indexOf('if (!live(ctx, state)) return;') < folderResolve.indexOf('store.resolveConflict'));
    const metaResolve = mountSlice(source('frontend/js/work-metadata-editor.js'),
        'async function actionResolve', 'root.prksResolveWorkMetadataFieldConflict');
    assert('metadata rechecks the edit session before resolveConflict',
        metaResolve.indexOf('if (!still() || !live(ctx, state)) return;') !== -1 &&
        metaResolve.indexOf('if (!still() || !live(ctx, state)) return;') < metaResolve.indexOf('store.resolveConflict'));
    assert('metadata still() return stays after the sync change',
        metaResolve.indexOf('prksSync.changed()') < metaResolve.indexOf('if (!still()) return;'));
    const sourceResolve = mountSlice(source('frontend/js/work-source-editor.js'),
        'async function resolveSource', 'function writeInput');
    const sourceResolveAt = sourceResolve.indexOf('store.resolveConflict');
    const sourceBeforeResolve = sourceResolve.slice(0, sourceResolve.lastIndexOf('\n', sourceResolveAt));
    assert('work source does not await reconciliation before resolveConflict',
        sourceBeforeResolve.indexOf('await ') === -1);
    assert('work role has no conflict resolver',
        source('frontend/js/work-role-editor.js').indexOf('resolveConflict') === -1);

    prksDestroyAllTabContexts();
    hosts();
    const typed = mountTab('typed');
    showWork(typed, 'work-typed');
    own(typed);
    const unknown = typed.registerResource(typed.resourceTicket(), {
        kind: 'notAKind', value: { id: 'nope' }, suspendable: false, dispose: function () {},
    });
    assertEq('unknown kind is rejected', unknown, 'rejected');
    const beforeTyped = subscribeCount;
    prksMountWorkRoleEditor(typed, 'work-typed');
    prksMountWorkTags(typed, 'work-typed');
    prksMountWorkSourceEditor(typed, 'work-typed');
    prksMountWorkMetadataEditor(typed, 'work-typed');
    typed.setEntity('folder', { id: 'folder-typed', tags: [] });
    prksMountFolderTags(typed, 'folder-typed');
    typed.setEntity('work', workRecord('work-typed'));
    await flush();
    EDITOR_KINDS.forEach(function (kind) {
        const value = typed.getResource(kind);
        assert(kind + ' is registered', !!value);
        assertEq(kind + ' getResource reads the registry slot', value, typed.readResource(kind));
        assert(kind + ' is not stranded on the legacy map', !typed.resources.has(kind));
    });
    assert('production mounts subscribed', subscribeCount > beforeTyped);

    const roleState = typed.getResource('workRoleEditor');
    let roleClears = 0;
    const roleTicket = typed.resourceTicket();
    typed.registerResource(roleTicket, {
        kind: 'workRoleEditor',
        value: roleState,
        suspendable: false,
        dispose: function () { roleClears += 1; },
    });
    typed.clearResource('workRoleEditor');
    assertEq('clearResource disposes the registry slot once', roleClears, 1);
    assertEq('clearResource drops the slot', typed.getResource('workRoleEditor'), undefined);
    assertEq('clearResource drops the registry read', typed.readResource('workRoleEditor'), undefined);

    let stranded = 0;
    let bridge = 0;
    typed.resources.set('workSourceEditor', {
        value: { stranded: true },
        disposer: function () { stranded += 1; },
    });
    typed.setResource('workSourceEditor', { id: 'bridge' }, function () { bridge += 1; });
    assertEq('compatibility setResource disposes a stranded legacy entry', stranded, 1);
    assert('compatibility setResource leaves no legacy copy', !typed.resources.has('workSourceEditor'));
    assertEq('compatibility setResource installs the registry value', typed.getResource('workSourceEditor').id, 'bridge');
    assertEq('compatibility value is the registry slot', typed.getResource('workSourceEditor'), typed.readResource('workSourceEditor'));
    typed.registerResource(typed.resourceTicket(), {
        kind: 'workSourceEditor',
        value: { id: 'next' },
        suspendable: false,
        dispose: function () {},
    });
    assertEq('replacement disposes the compatibility session once', bridge, 1);
    assertEq('replacement installs only the new state', typed.getResource('workSourceEditor').id, 'next');
    prksDestroyTabContext('typed');

    prksDestroyAllTabContexts();
    hosts();
    let releaseRead = null;
    readGate = new Promise(function (resolve) { releaseRead = resolve; });
    readStarted = 0;
    rolePaints = 0;
    const replaceCtx = mountTab('replace');
    showWork(replaceCtx, 'work-replace');
    own(replaceCtx);
    const subsBeforeReplace = subscribeCount;
    const unsubBeforeReplace = unsubscribeCount;
    prksMountWorkRoleEditor(replaceCtx, 'work-replace');
    const first = replaceCtx.getResource('workRoleEditor');
    const firstListeners = listeners.slice(subsBeforeReplace);
    for (let wait = 0; readStarted < 1 && wait < 20; wait += 1) await flush();
    assert('replacement read started before the session was replaced', readStarted >= 1);
    const second = {
        workId: 'work-replace',
        generation: replaceCtx.generation,
        observed: 'kept',
        operations: [],
        error: null,
        editable: false,
    };
    let secondDisposed = 0;
    const replaced = replaceCtx.registerResource(replaceCtx.resourceTicket(), {
        kind: 'workRoleEditor',
        value: second,
        suspendable: false,
        dispose: function () { secondDisposed += 1; },
    });
    assertEq('same-generation replacement reports replaced', replaced, 'replaced');
    assertEq('role mount keeps the owner acknowledgement beside the session', firstListeners.length, 3);
    const replacedListeners = firstListeners.slice(0, -1);
    assertEq('same-generation replacement disposes the previous session once',
        unsubscribeCount - unsubBeforeReplace, replacedListeners.length);
    assert('same-generation replacement unsubscribes every previous editor listener',
        replacedListeners.every(function (rec) { return rec.dead; }));
    assert('same-generation replacement keeps the owner acknowledgement',
        !firstListeners[firstListeners.length - 1].dead);
    assertEq('same-generation replacement installs only the new state', replaceCtx.getResource('workRoleEditor'), second);
    panel().innerHTML = 'BEFORE';
    releaseRead();
    readGate = null;
    await flush();
    assertEq('stale read does not replace the new observed base', second.observed, 'kept');
    assert('stale read does not paint the previous session', panel().innerHTML === 'BEFORE' && rolePaints === 0);
    assertEq('replacement disposer has not run yet', secondDisposed, 0);
    prksDestroyTabContext('replace');

    prksDestroyAllTabContexts();
    hosts();
    const routeCtx = mountTab('route');
    showWork(routeCtx, 'work-a');
    own(routeCtx);
    const routeSubAt = listeners.length;
    prksMountWorkMetadataEditor(routeCtx, 'work-a');
    const routeSession = routeCtx.getResource('workMetadataEditor');
    const routeTicket = routeCtx.resourceTicket();
    const routeUnsub = unsubscribeCount;
    const routeListeners = listeners.slice(routeSubAt);
    routeCtx.beginRoute({ name: 'work', params: { workId: 'work-b' } });
    showWork(routeCtx, 'work-b');
    assertEq('route replacement releases the old session', routeCtx.getResource('workMetadataEditor'), undefined);
    assert('route replacement unsubscribes the old listeners',
        routeListeners.every(function (rec) { return rec.dead; }) && unsubscribeCount > routeUnsub);
    assertEq('route replacement rejects the old ticket',
        routeCtx.registerResource(routeTicket, {
            kind: 'workMetadataEditor', value: routeSession, suspendable: false, dispose: function () {},
        }), 'rejected');
    assert('the old session is not the mounted metadata editor', routeCtx.getResource('workMetadataEditor') !== routeSession);
    prksDestroyTabContext('route');

    prksDestroyAllTabContexts();
    hosts();
    const cold = mountTab('cold');
    showWork(cold, 'work-cold');
    own(cold);
    const coldTicket = cold.resourceTicket();
    prksMountWorkTagEditorSafe(cold);
    assert('cold session is mounted', !!cold.getResource('workTagEditor'));
    prksUnmountTabContext('cold');
    assertEq('cold park releases the session', cold.getResource('workTagEditor'), undefined);
    assert('cold park invalidates the ticket', cold.resourceRegistry.accepts(coldTicket) === false);
    prksMountTabContext('cold', document.getElementById('main-host'));
    showWork(cold, 'work-cold');
    own(cold);
    assert('remount does not revive the old ticket', cold.resourceRegistry.accepts(coldTicket) === false);
    const revived = cold.registerResource(cold.resourceTicket(), {
        kind: 'workTagEditor', value: { id: 'fresh' }, suspendable: false, dispose: function () {},
    });
    assertEq('remount accepts a new ticket', revived, 'attached');
    assertEq('remount installs the new session', cold.getResource('workTagEditor').id, 'fresh');
    prksDestroyTabContext('cold');

    prksDestroyAllTabContexts();
    hosts();
    const paneA = mountTab('pane-a', 'main-host');
    const paneB = mountTab('pane-b', 'side-host');
    showWork(paneA, 'work-a');
    showWork(paneB, 'work-b');
    own(paneA);
    prksMountWorkSourceEditor(paneA, 'work-a');
    own(paneB);
    prksMountWorkSourceEditor(paneB, 'work-b');
    const kept = paneB.getResource('workSourceEditor');
    const keptUnsub = unsubscribeCount;
    prksDestroyTabContext('pane-a');
    assertEq('destroying one pane keeps the other session', paneB.getResource('workSourceEditor'), kept);
    assert('destroying one pane does not dispose the other session',
        unsubscribeCount >= keptUnsub && paneB.getResource('workSourceEditor') === kept && !paneB.destroyed);
    const paneBStillLive = listeners.filter(function (rec) { return !rec.dead; }).length > 0;
    assert('the other pane still has a live subscription', paneBStillLive);
    prksDestroyTabContext('pane-b');

    prksDestroyAllTabContexts();
    hosts();
    mainId = 'main';
    const main = mountTab('main', 'main-host');
    const side = mountTab('side', 'side-host');
    showWork(main, 'work-main');
    showWork(side, 'work-side');
    own(main);
    const mainSubAt = subscribeCount;
    prksMountWorkRoleEditor(main, 'work-main');
    const mainListener = listeners[mainSubAt];
    own(side);
    prksMountWorkRoleEditor(side, 'work-side');
    await flush();
    panel().innerHTML = 'SENTINEL';
    mainListener.fn({});
    await flush();
    assertEq('a background pane cannot paint the focused right panel', panel().innerHTML, 'SENTINEL');
    prksDestroyAllTabContexts();

    prksDestroyAllTabContexts();
    hosts();
    pdfInits = 0;
    const warm = mountTab('warm');
    showWork(warm, 'work-warm');
    own(warm);
    const pdf = { id: 'pdf-runtime', resized: 0, resize: function () { pdf.resized += 1; } };
    let pdfDisposed = 0;
    warm.registerResource(warm.resourceTicket(), {
        kind: 'pdf', value: pdf, suspendable: true, dispose: function () { pdfDisposed += 1; },
    });
    const parkedSubs = subscribeCount;
    prksMountWorkRoleEditor(warm, 'work-warm');
    prksMountWorkTags(warm, 'work-warm');
    prksMountWorkSourceEditor(warm, 'work-warm');
    prksMountWorkMetadataEditor(warm, 'work-warm');
    warm.setEntity('folder', { id: 'folder-warm', tags: [] });
    prksMountFolderTags(warm, 'folder-warm');
    showWork(warm, 'work-warm');
    own(warm);
    await flush();
    const editorSubs = subscribeCount - parkedSubs;
    assertEq('warm owner subscribed the five editor sessions and the owner acknowledgement', editorSubs, 9);
    const unsubAtPark = unsubscribeCount;
    assert('warm park keeps the PDF and releases editors', prksWarmParkTabContext('warm') === true);
    EDITOR_KINDS.forEach(function (kind) {
        assertEq('warm park releases ' + kind, warm.getResource(kind), undefined);
    });
    assertEq('warm park keeps the PDF object', warm.getResource('pdf'), pdf);
    assertEq('warm park does not dispose the PDF', pdfDisposed, 0);
    assertEq('warm park unsubscribes editor listeners and keeps the owner acknowledgement',
        unsubscribeCount - unsubAtPark, editorSubs - 1);
    assertEq('warm park keeps the owner acknowledgement subscription', editorLive(), 1);
    const rejectedSubs = subscribeCount;
    prksMountWorkRoleEditor(warm, 'work-warm');
    prksMountWorkTags(warm, 'work-warm');
    prksMountWorkSourceEditor(warm, 'work-warm');
    prksMountWorkMetadataEditor(warm, 'work-warm');
    prksMountFolderTags(warm, 'folder-warm');
    assertEq('rejected registration while parked establishes no subscriptions', subscribeCount, rejectedSubs);
    EDITOR_KINDS.forEach(function (kind) {
        assertEq('rejected registration does not install ' + kind, warm.getResource(kind), undefined);
    });
    const resumed = prksResumeWarmTabContext('warm', document.getElementById('main-host'));
    assert('warm resume returns the same owner', resumed === warm);
    await flush();
    assertEq('warm resume keeps the PDF object', warm.getResource('pdf'), pdf);
    assert('warm resume resizes the PDF', pdf.resized >= 1);
    assertEq('warm resume does not init a PDF viewer', pdfInits, 0);
    const resumeSubs = subscribeCount;
    prksRefreshFocusedRightPanel();
    await flush();
    ['workRoleEditor', 'workTagEditor', 'workSourceEditor', 'workMetadataEditor'].forEach(function (kind) {
        assert('warm resume recreates ' + kind, !!warm.getResource(kind));
    });
    assertEq('warm resume does not recreate the folder tag session', warm.getResource('folderTagEditor'), undefined);
    assertEq('warm resume adds one subscription set for the work editors', subscribeCount - resumeSubs, 7);
    const recreated = {
        workRoleEditor: warm.getResource('workRoleEditor'),
        workTagEditor: warm.getResource('workTagEditor'),
        workSourceEditor: warm.getResource('workSourceEditor'),
        workMetadataEditor: warm.getResource('workMetadataEditor'),
    };
    const secondSubs = subscribeCount;
    prksRefreshFocusedRightPanel();
    await flush();
    assertEq('second refresh adds no subscriptions', subscribeCount, secondSubs);
    Object.keys(recreated).forEach(function (kind) {
        assertEq('second refresh reuses ' + kind, warm.getResource(kind), recreated[kind]);
    });
    assertEq('warm refresh does not recreate the PDF', warm.getResource('pdf'), pdf);
    assertEq('warm refresh does not init a PDF viewer', pdfInits, 0);
    prksDestroyTabContext('warm');

    prksDestroyAllTabContexts();
    hosts();
    const durable = mountTab('durable');
    showWork(durable, 'work-durable');
    own(durable);
    durable.setEntity('work', Object.assign(workRecord('work-durable'), {
        roles: [],
        tags: [],
    }));
    prksMountWorkRoleEditor(durable, 'work-durable');
    await flush();
    const saved = await prksSaveWorkPersonRoleDurably('work-durable', 'person-1', 'Author', 'Ada', { id: 'person-1' }, null, null);
    assertEq('current role session still reaches the durable store', saved && saved.code, 'saved');
    assertEq('role durable write happened once', roleWrites.length, 1);
    durable.ui.workDetailsMode = 'tags';
    hosts();
    own(durable);
    prksMountWorkTags(durable, 'work-durable');
    await flush();
    await prksWorkTagEdit(durable, 'tag-1', true);
    assertEq('current tag session still reaches coalesceWorkTag', tagWrites.length, 1);
    prksDestroyTabContext('durable');

    await replaceDuringTagReconcile();
    await warmParkDuringFolderReconcile();
    await metadataResolveDuringGate('meta-replace', 'work-meta-replace', 'op-meta-replace', async function (ctx, state) {
        const fresh = {
            workId: state.workId,
            generation: ctx.generation,
            operations: [],
            observed: { fields: { title: { revision: 9, value: 'Fresh' } } },
            error: 'kept',
        };
        const replaced = ctx.registerResource(ctx.resourceTicket(), {
            kind: 'workMetadataEditor',
            value: fresh,
            suspendable: false,
            dispose: function () {},
        });
        assertEq('metadata replacement during reconciliation reports replaced', replaced, 'replaced');
        assertEq('metadata replacement installs the fresh observed title', fresh.observed.fields.title.value, 'Fresh');
    });
    await metadataResolveDuringGate('meta-fence', 'work-meta-fence', 'op-meta-fence', async function (ctx) {
        ctx.ui.workMetaEditSession += 1;
    });
    await warmAcknowledgementSurvivesActivation();

    prksDestroyAllTabContexts();
    hosts();
    const leakStartUnsub = unsubscribeCount;
    const leakStartSub = subscribeCount;
    for (let i = 0; i < 3; i += 1) {
        const id = 'leak-' + i;
        const ctx = mountTab(id);
        showWork(ctx, 'work-' + i);
        own(ctx);
        prksMountWorkRoleEditor(ctx, 'work-' + i);
        prksMountWorkTags(ctx, 'work-' + i);
        prksMountWorkSourceEditor(ctx, 'work-' + i);
        prksMountWorkMetadataEditor(ctx, 'work-' + i);
        ctx.registerResource(ctx.resourceTicket(), {
            kind: 'workRoleEditor',
            value: { workId: 'work-' + i, generation: ctx.generation, observed: 'next' },
            suspendable: false,
            dispose: function () {},
        });
        ctx.beginRoute({ name: 'folders' });
        prksUnmountTabContext(id);
        prksMountTabContext(id, document.getElementById('main-host'));
        prksDestroyTabContext(id);
    }
    await flush();
    assertEq('repeated cycles leave no live subscriptions', editorLive(), 0);
    assertEq('repeated cycles unsubscribe every subscription', unsubscribeCount - leakStartUnsub, subscribeCount - leakStartSub);
    assertEq('repeated cycles do not double-unsubscribe', doubleUnsub, 0);
    const snap = prksTabContextDebugSnapshot();
    assertEq('repeated cycles leave no mounted owners', snap.mountedCount, 0);
    assertEq('repeated cycles leave no contexts', snap.contexts.length, 0);
    assertEq('no unhandled rejection', unhandled.length, 0);

    console.log(passed + ' checks passed, ' + failed + ' failed');
    if (failed) process.exit(1);
}

function prksMountWorkTagEditorSafe(ctx) {
    prksMountWorkTags(ctx, 'work-cold');
}

main().catch(function (error) {
    console.error(error && error.stack ? error.stack : error);
    process.exit(1);
});
