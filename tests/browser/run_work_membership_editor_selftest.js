#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { installMiniDocument } = require('./mini_document');

const rootDir = path.resolve(__dirname, '../..');

/* The unit gate runs this file with Node and does not install frontend-app
 * dependencies. A small document is enough for the Work-panel membership path. */
installMiniDocument([
    '<div id="modal-backdrop" class="hidden"></div>',
    '<div id="folder-modal" class="modal hidden">',
    '<input id="folder-title" />',
    '<p id="folder-title-error" class="hidden"></p>',
    '<textarea id="folder-description"></textarea>',
    '<input id="folder-parent-search" />',
    '<input id="folder-parent-id" />',
    '<div id="folder-parent-results" class="hidden"></div>',
    '<button id="save-folder-btn" type="button">Save Folder</button>',
    '</div>',
    '<div id="playlist-modal" class="modal hidden">',
    '<input id="playlist-title" />',
    '<textarea id="playlist-description"></textarea>',
    '<p id="playlist-error" class="hidden"></p>',
    '<button id="save-playlist-btn" type="button">Save</button>',
    '</div>',
    '<button id="save-work-btn" type="button">Save</button>',
    '<button id="save-role-btn" type="button">Create Link</button>',
    '<div id="panel-content"></div>',
    '<div id="main-host"></div><div id="side-host"></div>',
].join(''));

global.prksOfflineRuntimeSubscribe = function () { return function () {}; };
global.prksOfflineRuntimeState = function () { return 'online'; };

const syncListeners = [];
const tagWrites = [];
global.prksSync = {
    subscribe: function (fn) {
        syncListeners.push(fn);
        return function () {};
    },
    changed: function () {},
    store: {
        listOperations: async function () { return []; },
        coalesceWorkTag: async function () {
            tagWrites.push(Array.prototype.slice.call(arguments));
        },
    },
};

let focusedId = 'main';
global.prksWorkspaceSnapshot = function () {
    return { focusedTabId: focusedId, mainTabId: 'main' };
};

function load(rel) {
    (0, eval)(fs.readFileSync(path.join(rootDir, rel), 'utf8'));
}

load('frontend/js/tab-context.js');
load('frontend/js/ui.js');
const apiSource = fs.readFileSync(path.join(rootDir, 'frontend/js/api.js'), 'utf8');
(0, eval)(apiSource.slice(
    apiSource.indexOf('function prksFolderSaveMessage'),
    apiSource.indexOf('async function createFolder'),
));
load('frontend/js/components/folders.js');
load('frontend/js/components/playlists.js');
load('frontend/js/work-tag-editor.js');
const app = fs.readFileSync(path.join(rootDir, 'frontend/js/app.js'), 'utf8');
(0, eval)(app.slice(app.indexOf('function initForms()')));
initForms();

const folderWrites = [];
const playlistWrites = [];
const created = [];
let folderReads = 0;
let playlistReads = 0;
let folderGate = null;
let playlistGate = null;
let paints = 0;
let refreshes = 0;

global.fetchFolders = async function () { return []; };
global.fetchWorkDetails = async function (id) {
    return { id: id, title: 'fetched-' + id, tags: [] };
};
global.prksAcknowledgedWorkFolder = async function () {
    folderReads += 1;
    if (folderGate) await folderGate;
    return { folder_id: '', revision: 4 };
};
global.prksSetWorkFolderDurably = async function () {
    folderWrites.push(Array.prototype.slice.call(arguments));
};
global.prksAcknowledgedWorkPlaylist = async function () {
    playlistReads += 1;
    if (playlistGate) await playlistGate;
    return { playlist_id: '', revision: 4 };
};
global.prksSetWorkPlaylistDurably = async function () {
    playlistWrites.push(Array.prototype.slice.call(arguments));
};
global.createFolder = async function (title) {
    created.push({ kind: 'folder', title: String(title || '') });
    return 'folder-new';
};
global.createPlaylist = async function (title) {
    created.push({ kind: 'playlist', title: String(title || '') });
    return 'playlist-new';
};
global.updatePanelContent = function () { paints += 1; };
global.prksNavigate = function () { paints += 1; };
global.prksAlertMessage = async function () {};
global.__prksRefreshPlaylistSelects = async function () { refreshes += 1; };
global.__prksRefreshAllPlaylistSelects = async function () { refreshes += 1; };
global.prksEffectiveWorkTags = function (work) { return (work && work.tags) || []; };
global.renderWorkTagsChips = function () { return '<span id="legacy-tags">Alpha</span>'; };
global.prksWorkTagBase = function () { return { present: false, revision: 1 }; };
global.prksOfflineDomainGeneration = function () { return 7; };

let passed = 0;
let failed = 0;
function assert(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (ok || !detail ? '' : ' ' + detail));
}
function panel() { return document.getElementById('panel-content'); }
function focus(tabId) { focusedId = tabId; }
function ownPanel(ctx) {
    const node = panel();
    node.dataset.prksOwnerTabId = String(ctx.tabId);
    node.dataset.prksOwnerGeneration = String(ctx.generation);
}
function showWork(ctx, workId, extra) {
    const work = Object.assign({
        id: workId,
        title: workId,
        tags: [{ id: 't1', name: 'Alpha', color: '' }],
    }, extra || {});
    ctx.setEntity('work', work);
    const route = { name: 'work', params: { workId: workId } };
    ctx.route = route;
    ctx.lastResolvedRoute = route;
    return work;
}
function folderHtml() {
    return [
        '<button type="button" id="prks-work-folder-edit-btn">Edit</button>',
        '<p id="prks-work-folder-status"></p>',
        '<input id="prks-work-folder-search" />',
        '<input id="prks-work-folder-id" />',
        '<div id="prks-work-folder-results"></div>',
        '<button type="button" id="prks-work-folder-set-btn">Set folder</button>',
        '<button type="button" id="prks-work-folder-clear-btn">Clear</button>',
        '<button type="button" id="prks-work-folder-new-btn">New...</button>',
    ].join('');
}
function playlistHtml() {
    return [
        '<button type="button" id="prks-work-playlist-edit-btn">Edit</button>',
        '<div id="prks-work-playlist-nav"></div>',
        '<p id="prks-work-playlist-status"></p>',
        '<input id="prks-work-playlist-search" />',
        '<input id="prks-work-playlist-id" />',
        '<div id="prks-work-playlist-results"></div>',
        '<button type="button" id="prks-work-playlist-set-btn">Set playlist</button>',
        '<button type="button" id="prks-work-playlist-clear-btn">Clear</button>',
        '<button type="button" id="prks-work-playlist-new-btn">New…</button>',
    ].join('');
}
function arm(kind) {
    let release;
    const gate = new Promise(function (resolve) { release = resolve; });
    if (kind === 'folder') {
        folderReads = 0;
        folderGate = gate;
    } else {
        playlistReads = 0;
        playlistGate = gate;
    }
    return function () {
        release();
        if (kind === 'folder') folderGate = null;
        else playlistGate = null;
    };
}
async function until(pred, label) {
    for (let i = 0; i < 40; i += 1) {
        if (pred()) return;
        await new Promise(function (resolve) { setTimeout(resolve, 0); });
    }
    throw new Error('timed out waiting for ' + label);
}
function resetCounts() {
    folderWrites.length = 0;
    playlistWrites.length = 0;
    created.length = 0;
    paints = 0;
    refreshes = 0;
}

async function mountFolder(ctx, work) {
    ctx.ui.workFolderEditing = true;
    panel().innerHTML = folderHtml();
    await mountFolderAttachControlsForWork(work, ctx);
}

async function mountPlaylist(ctx, work) {
    ctx.ui.workPlaylistEditing = true;
    panel().innerHTML = playlistHtml();
    await mountPlaylistAttachControls(work, ctx);
}

async function deferredFolder(kind) {
    resetCounts();
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    const work = showWork(main, 'work-a');
    ownPanel(main);
    await mountFolder(main, work);
    if (kind === 'set') document.getElementById('prks-work-folder-id').value = 'folder-1';
    const release = arm('folder');
    const button = document.getElementById(kind === 'set' ? 'prks-work-folder-set-btn' : 'prks-work-folder-clear-btn');
    const pending = button.onclick();
    await until(function () { return folderReads >= 1; }, 'folder base read');
    main.beginRoute({ name: 'work', params: { workId: 'work-b' } });
    showWork(main, 'work-b');
    ownPanel(main);
    const snapshot = JSON.stringify(main.getEntity('work'));
    release();
    await pending;
    assert('folder ' + kind + ' does not call the durable setter after Work A is replaced',
        folderWrites.length === 0, 'writes=' + folderWrites.length);
    assert('folder ' + kind + ' does not mutate or repaint Work B',
        paints === 0 && JSON.stringify(main.getEntity('work')) === snapshot,
        'paints=' + paints);
    prksDestroyAllTabContexts();
}

async function deferredPlaylist(kind) {
    resetCounts();
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    const work = showWork(main, 'work-a', { playlist_id: 'pl-1', playlist_title: 'Old' });
    ownPanel(main);
    await mountPlaylist(main, work);
    if (kind === 'set') document.getElementById('prks-work-playlist-id').value = 'pl-2';
    const release = arm('playlist');
    const button = document.getElementById(kind === 'set' ? 'prks-work-playlist-set-btn' : 'prks-work-playlist-clear-btn');
    const pending = button.onclick();
    await until(function () { return playlistReads >= 1; }, 'playlist base read');
    main.beginRoute({ name: 'work', params: { workId: 'work-b' } });
    showWork(main, 'work-b');
    ownPanel(main);
    const snapshot = JSON.stringify(main.getEntity('work'));
    release();
    await pending;
    assert('playlist ' + kind + ' does not call the durable setter after Work A is replaced',
        playlistWrites.length === 0, 'writes=' + playlistWrites.length);
    assert('playlist ' + kind + ' does not mutate or repaint Work B',
        paints === 0 && JSON.stringify(main.getEntity('work')) === snapshot,
        'paints=' + paints);
    prksDestroyAllTabContexts();
}

async function newFolderDuringRead(geometry) {
    resetCounts();
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    const side = geometry === 'secondary'
        ? prksMountTabContext('side', document.getElementById('side-host')) : null;
    const work = showWork(main, 'work-a');
    if (side) showWork(side, 'work-b');
    ownPanel(main);
    await mountFolder(main, work);
    document.getElementById('prks-work-folder-new-btn').onclick();
    const pendingAttach = global.__prksPendingWorkFolderAttach;
    assert('new folder records the opener tab, generation, and Work',
        pendingAttach && pendingAttach.workId === 'work-a' && pendingAttach.tabId === 'main' &&
        pendingAttach.generation === main.generation);
    document.getElementById('folder-title').value = 'Created folder';
    const release = arm('folder');
    const saving = document.getElementById('save-folder-btn').onclick();
    await until(function () { return folderReads >= 1; }, 'new folder base read');
    let victim;
    if (geometry === 'secondary') {
        focus('side');
        ownPanel(side);
        victim = side;
    } else {
        main.beginRoute({ name: 'work', params: { workId: 'work-b' } });
        showWork(main, 'work-b');
        ownPanel(main);
        victim = main;
    }
    const snapshot = JSON.stringify(victim.getEntity('work'));
    release();
    await saving;
    assert('new folder ' + geometry + ' still creates the folder',
        created.some(function (row) { return row.kind === 'folder'; }), JSON.stringify(created));
    assert('new folder ' + geometry + ' does not write membership for stale Work A',
        folderWrites.length === 0, 'writes=' + folderWrites.length);
    assert('new folder ' + geometry + ' does not mutate or repaint Work B',
        paints === 0 && refreshes === 0 && JSON.stringify(victim.getEntity('work')) === snapshot,
        'paints=' + paints + ' refreshes=' + refreshes);
    prksDestroyAllTabContexts();
}

async function newPlaylistDuringRead(geometry) {
    resetCounts();
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    const side = geometry === 'secondary'
        ? prksMountTabContext('side', document.getElementById('side-host')) : null;
    const work = showWork(main, 'work-a');
    if (side) showWork(side, 'work-b');
    ownPanel(main);
    await mountPlaylist(main, work);
    await document.getElementById('prks-work-playlist-new-btn').onclick();
    const pendingAttach = global.__prksPendingPlaylistAttach;
    assert('new playlist records the opener tab, generation, and Work',
        pendingAttach && pendingAttach.workId === 'work-a' && pendingAttach.tabId === 'main' &&
        pendingAttach.generation === main.generation);
    document.getElementById('playlist-title').value = 'Created playlist';
    const release = arm('playlist');
    const saving = document.getElementById('save-playlist-btn').onclick();
    await until(function () { return playlistReads >= 1; }, 'new playlist base read');
    let victim;
    if (geometry === 'secondary') {
        focus('side');
        ownPanel(side);
        victim = side;
    } else {
        main.beginRoute({ name: 'work', params: { workId: 'work-b' } });
        showWork(main, 'work-b');
        ownPanel(main);
        victim = main;
    }
    const snapshot = JSON.stringify(victim.getEntity('work'));
    release();
    await saving;
    assert('new playlist ' + geometry + ' still creates the playlist',
        created.some(function (row) { return row.kind === 'playlist'; }), JSON.stringify(created));
    assert('new playlist ' + geometry + ' does not write membership for stale Work A',
        playlistWrites.length === 0, 'writes=' + playlistWrites.length);
    assert('new playlist ' + geometry + ' does not mutate or repaint Work B',
        paints === 0 && refreshes === 0 && JSON.stringify(victim.getEntity('work')) === snapshot,
        'paints=' + paints + ' refreshes=' + refreshes);
    prksDestroyAllTabContexts();
}

async function tagBridgeAndOwner() {
    resetCounts();
    tagWrites.length = 0;
    focus('main');
    const main = prksMountTabContext('main', document.getElementById('main-host'));
    showWork(main, 'work-a');
    ownPanel(main);
    main.ui.workDetailsMode = 'view';
    delete global.prksVueRefreshWorkPanelRead;
    panel().innerHTML = '<div id="work-tags-list"></div>';
    const before = syncListeners.length;
    prksMountWorkTags(main, 'work-a');
    await until(function () {
        const list = document.getElementById('work-tags-list');
        return !!(list && String(list.innerHTML).indexOf('legacy-tags') !== -1);
    }, 'legacy tag paint');
    assert('view mode paints the legacy tag list when the Vue bridge is unavailable',
        String(document.getElementById('work-tags-list').innerHTML).indexOf('legacy-tags') !== -1);

    global.prksVueRefreshWorkPanelRead = function () { return false; };
    panel().innerHTML = '<div id="work-tags-list">VUE-OWNED</div>';
    const listeners = syncListeners.slice(before);
    await Promise.all(listeners.map(function (fn) { return Promise.resolve(fn({})); }));
    const owned = document.getElementById('work-tags-list');
    assert('a false Vue refresh does not overwrite a Vue-owned tag target',
        owned && String(owned.innerHTML).indexOf('VUE-OWNED') !== -1 &&
        String(owned.innerHTML).indexOf('legacy-tags') === -1,
        owned && owned.innerHTML);

    const side = prksMountTabContext('side', document.getElementById('side-host'));
    showWork(side, 'work-b');
    const state = main.getResource('workTagEditor');
    state.options = { assigned: [], known_absent: {} };
    state.catalog = [{ id: 't1', name: 'Alpha' }];
    state.catalogGeneration = 7;
    let releasePrepare;
    state.preparing = new Promise(function (resolve) { releasePrepare = resolve; });
    focus('side');
    const edit = prksWorkTagEdit(main, 't1', true);
    await new Promise(function (resolve) { setTimeout(resolve, 0); });
    releasePrepare();
    await edit;
    assert('tag edit does not coalesce after the opener loses focus', tagWrites.length === 0,
        'writes=' + tagWrites.length);

    focus('main');
    ownPanel(main);
    state.preparing = null;
    await prksWorkTagEdit(main, 't1', true);
    assert('tag edit coalesces while the opener still owns the panel', tagWrites.length === 1,
        'writes=' + tagWrites.length);
    prksDestroyAllTabContexts();
}

(async function main() {
    try {
        await deferredFolder('set');
        await deferredFolder('clear');
        await deferredPlaylist('set');
        await deferredPlaylist('clear');
        await newFolderDuringRead('replaced');
        await newFolderDuringRead('secondary');
        await newPlaylistDuringRead('replaced');
        await newPlaylistDuringRead('secondary');
        await tagBridgeAndOwner();
    } catch (error) {
        failed += 1;
        console.log('FAIL  runtime exception ' + (error && error.stack || error));
    }
    console.log(passed + ' checks passed, ' + failed + ' failed');
    if (failed) process.exit(1);
})();
