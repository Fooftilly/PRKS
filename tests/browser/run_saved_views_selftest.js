#!/usr/bin/env node
'use strict';

const path = require('path');
const fs = require('fs');
const vm = require('vm');

function runScript(rel, sandbox) {
    const file = path.join(__dirname, '..', '..', rel);
    const code = fs.readFileSync(file, 'utf8');
    vm.runInNewContext(code, sandbox, { filename: file });
}

const posts = [];
const patches = [];
const modalCalls = [];
const navCalls = [];

const inputs = {
    name: { value: '', focus: function () { this.focused = true; }, addEventListener: function () {} },
    q: { value: '' },
    tag: { value: '' },
    author: { value: '' },
    publisher: { value: '' },
};

const modalError = {
    textContent: '',
    classList: { add: function () {}, remove: function () {} },
};
const saveButton = {
    disabled: false,
    textContent: '',
    listeners: {},
    addEventListener: function (type, fn) { this.listeners[type] = fn; },
};

const radios = [
    { value: 'all', checked: false, addEventListener: function () {} },
    { value: 'advanced', checked: true, addEventListener: function () {} },
    { value: 'tag', checked: false, addEventListener: function () {} },
];

const document = {
    getElementById: function (id) {
        if (id === 'saved-view-name') return inputs.name;
        if (id === 'saved-view-q') return inputs.q;
        if (id === 'saved-view-tag') return inputs.tag;
        if (id === 'saved-view-author') return inputs.author;
        if (id === 'saved-view-publisher') return inputs.publisher;
        if (id === 'saved-view-modal-title') return { textContent: '' };
        if (id === 'saved-view-modal-helper') return { textContent: '' };
        if (id === 'saved-view-modal-error') return modalError;
        if (id === 'save-saved-view-btn') return saveButton;
        if (id === 'saved-view-modal') return { id: 'saved-view-modal' };
        if (id === 'saved-view-field-q-wrap') return { hidden: false };
        if (id === 'saved-view-q-label') return { textContent: '' };
        if (id === 'saved-view-field-tag-wrap') return { hidden: false };
        if (id === 'saved-view-field-author-wrap') return { hidden: false };
        if (id === 'saved-view-field-publisher-wrap') return { hidden: false };
        return null;
    },
    querySelector: function (sel) {
        if (sel === 'input[name="saved-view-mode"]:checked') {
            return radios.filter(function (r) { return r.checked; })[0] || radios[1];
        }
        return null;
    },
    querySelectorAll: function (sel) {
        if (sel === 'input[name="saved-view-mode"]') return radios;
        return [];
    },
};

const records = {
    fail: null,
    create: function (payload) {
        posts.push(payload);
        if (records.fail) return Promise.reject(records.fail);
        return Promise.resolve({ id: 'SV-NEW', name: payload.name, search: payload.search });
    },
    update: function (id, payload) {
        patches.push({ id: id, payload: payload });
        if (records.fail) return Promise.reject(records.fail);
        return Promise.resolve({ id: id, name: payload.name, search: payload.search });
    },
    actionMessage: function (err, fallback) {
        return err && err.serverText ? err.serverText : fallback;
    },
};

const sandbox = {
    console: console,
    window: null,
    document: document,
    location: { hash: '#/search?any=1&q=critical%20theory' },
    URLSearchParams: URLSearchParams,
    Promise: Promise,
    module: { exports: {} },
    exports: {},
    prksParseRoute: null,
    openModal: function (id) {
        modalCalls.push(id);
    },
    closes: 0,
    requestModalClose: function () { sandbox.closes += 1; },
    prksSavedViewRecords: records,
};

sandbox.window = sandbox;
sandbox.globalThis = sandbox;

runScript('frontend/js/route-model.js', sandbox);
runScript('frontend/js/navigation.js', sandbox);
runScript('frontend/js/search-query-codec.js', sandbox);
runScript('frontend/js/saved-views.js', sandbox);

const root = sandbox;
// navigation.js installs the real prksNavigate; record calls instead.
root.prksNavigate = function (hash, opts) {
    navCalls.push({ hash: hash, replace: !!(opts && opts.replace), tabId: opts && opts.tabId });
};
let passed = 0;
let failed = 0;
function assert(name, ok) {
    if (ok) {
        passed += 1;
        console.log('PASS  ' + name);
    } else {
        failed += 1;
        console.log('FAIL  ' + name);
    }
}
function assertEq(name, a, b) {
    const ok = a === b;
    if (!ok) console.log('      got', JSON.stringify(a), 'want', JSON.stringify(b));
    assert(name, ok);
}

function settle() {
    return new Promise(function (resolve) { setTimeout(resolve, 0); });
}

function save() {
    saveButton.listeners.click();
    return settle();
}

assert(
    'codec bridge is loaded',
    root.prksSearchQueryCodec && typeof root.prksSearchQueryCodec.definitionFromRoute === 'function'
);
root.prksInitSavedViews();
assert('save button is bound', typeof saveButton.listeners.click === 'function');

root.prksOpenSavedViewModalFromCurrentSearch();
assertEq('modal opened', modalCalls[0], 'saved-view-modal');
assertEq('name focused', inputs.name.focused, true);
assertEq('prefill q', inputs.q.value, 'critical theory');

inputs.name.value = 'Adorno — Culture Industry';
radios.forEach(function (r) { r.checked = r.value === 'all'; });
save()
    .then(function () {
        assertEq('create goes through the records service', posts.length, 1);
        assertEq('posted name', posts[0].name, 'Adorno — Culture Industry');
        assertEq('posted mode', posts[0].search.mode, 'all');
        assertEq('posted q', posts[0].search.q, 'critical theory');
        assertEq('create closes the modal', root.closes, 1);
        assertEq('create opens the new view', navCalls[navCalls.length - 1].hash, '#/views/SV-NEW');

        records.fail = { serverText: 'A Saved View with that name already exists.' };
        root.prksOpenSavedViewModal({ name: 'Taken', definition: { mode: 'all', q: 'x', tag: '', author: '', publisher: '' } });
        return save();
    })
    .then(function () {
        assertEq('refused create keeps the modal open', root.closes, 1);
        assertEq('refused create shows the server text', modalError.textContent, 'A Saved View with that name already exists.');
        assertEq('save button is enabled again', saveButton.disabled, false);

        records.fail = {};
        root.prksOpenSavedViewModal({
            viewId: 'SV-1',
            name: 'Critical Theory',
            definition: { mode: 'advanced', q: 'culture industry', tag: '', author: 'Adorno', publisher: '' },
        });
        return save();
    })
    .then(function () {
        assertEq('failed update names the edit', modalError.textContent, 'Could not update Saved View.');
        records.fail = null;
        return save();
    })
    .then(function () {
        assertEq('patch same id', patches[patches.length - 1].id, 'SV-1');
        assertEq('patch name', patches[patches.length - 1].payload.name, 'Critical Theory');
        assertEq('patch mode', patches[patches.length - 1].payload.search.mode, 'advanced');
        assertEq('patch author', patches[patches.length - 1].payload.search.author, 'Adorno');
        assertEq('update closes the modal', root.closes, 2);

        const src = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'js', 'saved-views.js'), 'utf8');
        assert('no results table', src.indexOf('saved_view_works') < 0);
        assert('codec left saved-views.js', src.indexOf('function prksSearchDefinitionFromRoute') < 0);
        assert('modal uses the codec bridge', src.indexOf('prksSearchQueryCodec') >= 0);
        assert('no eval', src.indexOf('eval(') < 0);
        assert('index painters removed',
            src.indexOf('function renderSavedViewsIndex') < 0 &&
            src.indexOf('function bindIndexActions') < 0 &&
            src.indexOf('function openEditById') < 0 &&
            src.indexOf('function confirmDelete') < 0 &&
            src.indexOf('prksOpenSavedViewIndexEdit') < 0);
        assert('delete wrappers left saved-views.js',
            typeof root.prksDeleteSavedViewFromIndex === 'undefined' &&
            typeof root.prksDeleteSavedViewFromDetail === 'undefined' &&
            src.indexOf('deleteSavedView') < 0);
        assert('no raw Saved View HTTP', src.indexOf('/api/saved-views') < 0);

        /* Save View from the Search surface uses that owner's hash, not the URL. */
        root.location.hash = '#/folders';
        root.prksOpenSavedViewModalFromCurrentSearch('#/search?q=owned&author=Benjamin');
        assertEq('save view uses owner hash q', inputs.q.value, 'owned');
        assertEq('save view uses owner hash author', inputs.author.value, 'Benjamin');

        /* Edit this Saved View reads the Main TabContext entity. */
        const beforeEdit = modalCalls.length;
        root.prksGetMainTabContext = function () { return { getEntity: function () { return null; } }; };
        root.prksOpenSavedViewModalForCurrentView();
        assertEq('edit current view without entity is a no-op', modalCalls.length, beforeEdit);
        root.prksGetMainTabContext = function () {
            return {
                getEntity: function (type) {
                    return type === 'savedView'
                        ? { id: 'SV-MAIN', name: 'Main View', search: { mode: 'tag', q: '', tag: 'T', author: '', publisher: '' } }
                        : null;
                },
            };
        };
        root.prksOpenSavedViewModalForCurrentView();
        assertEq('edit current view opens modal', modalCalls.length, beforeEdit + 1);
        assertEq('edit current view fills name', inputs.name.value, 'Main View');
        assertEq('edit current view fills tag', inputs.tag.value, 'T');
        assert('detail painters removed', typeof root.renderSavedViewDetail === 'undefined' &&
            typeof root.renderSavedViewNotFound === 'undefined' &&
            typeof root.prksSearchResultCardsHtml === 'undefined');
        assert('index edit export removed', typeof root.prksOpenSavedViewIndexEdit === 'undefined');
        assert('index painter export removed', typeof root.renderSavedViewsIndex === 'undefined');

        /* The offline guard refuses to open the modal. */
        const beforeOffline = modalCalls.length;
        root.prksOfflineGuardMutation = function () { return true; };
        root.prksOpenSavedViewModal({ name: 'Offline' });
        assertEq('offline guard keeps the modal closed', modalCalls.length, beforeOffline);
    })
    .then(function () {
        console.log(passed + ' passed, ' + failed + ' failed');
        if (failed) process.exit(1);
    })
    .catch(function (err) {
        console.error(err);
        process.exit(1);
    });
