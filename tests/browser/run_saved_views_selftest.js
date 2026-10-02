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
        if (id === 'saved-view-modal-error') return { textContent: '', classList: { add: function () {}, remove: function () {} } };
        if (id === 'save-saved-view-btn') return { disabled: false, textContent: '', addEventListener: function () {} };
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
    prksNavigate: function (hash, opts) {
        navCalls.push({ hash: hash, replace: !!(opts && opts.replace), tabId: opts && opts.tabId });
        sandbox.location.hash = hash;
    },
    openModal: function (id) {
        modalCalls.push(id);
    },
    requestModalClose: function () {},
    createSavedView: function (payload) {
        posts.push(payload);
        return Promise.resolve({ id: 'SV-NEW', name: payload.name, search: payload.search });
    },
    updateSavedView: function (id, payload) {
        patches.push({ id: id, payload: payload });
        return Promise.resolve({ id: id, name: payload.name, search: payload.search });
    },
};

sandbox.window = sandbox;
sandbox.globalThis = sandbox;

runScript('frontend/js/navigation.js', sandbox);
runScript('frontend/js/saved-views.js', sandbox);

const root = sandbox;
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

const parse = root.prksParseRoute;

const allRoute = parse('#/search?any=1&q=critical%20theory');
const allDef = root.prksSearchDefinitionFromRoute(allRoute);
assert('all savable', allDef.ok === true);
assertEq('all mode', allDef.definition.mode, 'all');
assertEq('all q', allDef.definition.q, 'critical theory');

const adv = root.prksSearchDefinitionFromRoute(parse('#/search?q=culture%20industry&author=Adorno'));
assert('advanced savable', adv.ok === true);
assertEq('advanced mode', adv.definition.mode, 'advanced');
assertEq('advanced author', adv.definition.author, 'Adorno');

const tag = root.prksSearchDefinitionFromRoute(parse('#/search?tag=Frankfurt%20School&publisher=Verso'));
assert('tag savable', tag.ok === true);
assertEq('tag mode', tag.definition.mode, 'tag');
assertEq('tag name', tag.definition.tag, 'Frankfurt School');
assertEq('tag q empty', tag.definition.q, '');

const mixed = root.prksSearchDefinitionFromRoute(parse('#/search?any=1&author=Adorno'));
assert('mixed unsavable', mixed.ok === false && mixed.unsavable === true);
assertEq('mixed message', mixed.message, 'This search combination cannot be saved as a view.');

const allHash = root.prksSearchHashFromDefinition(allDef.definition);
assertEq('open as search all', allHash, '#/search?' + new URLSearchParams({ any: '1', q: 'critical theory' }).toString());
const advHash = root.prksSearchHashFromDefinition(adv.definition);
const advParams = new URLSearchParams(advHash.slice('#/search?'.length));
assertEq('open as search adv q', advParams.get('q'), 'culture industry');
assertEq('open as search adv author', advParams.get('author'), 'Adorno');
assert('open as search adv no tag', advParams.get('tag') == null);
const tagHash = root.prksSearchHashFromDefinition(tag.definition);
const tagParams = new URLSearchParams(tagHash.slice('#/search?'.length));
assertEq('open as search tag', tagParams.get('tag'), 'Frankfurt School');
assertEq('open as search tag pub', tagParams.get('publisher'), 'Verso');
assert('open as search tag no q', tagParams.get('q') == null);

assertEq(
    'summary all',
    root.prksSearchSummaryText(allDef.definition),
    'All: critical theory'
);
assertEq(
    'summary advanced',
    root.prksSearchSummaryText(adv.definition),
    'Keywords: culture industry · Author: Adorno'
);
assertEq(
    'summary tag',
    root.prksSearchSummaryText(tag.definition),
    'Tag: Frankfurt School · Publisher: Verso'
);

const mapped = root.prksSearchOptionsFromDefinition(allDef.definition);
assertEq('options all any', mapped.options.any, '1');
assertEq('options all q', mapped.q, 'critical theory');

root.prksOpenSavedViewModalFromCurrentSearch();
assertEq('modal opened', modalCalls[0], 'saved-view-modal');
assertEq('name focused', inputs.name.focused, true);
assertEq('prefill q', inputs.q.value, 'critical theory');

inputs.name.value = 'Adorno — Culture Industry';
Promise.resolve(root.createSavedView({
    name: inputs.name.value,
    search: allDef.definition,
}))
    .then(function () {
        assert('posted create', posts.length === 1);
        assertEq('posted name', posts[0].name, 'Adorno — Culture Industry');
        assertEq('posted mode', posts[0].search.mode, 'all');
        assertEq('posted q', posts[0].search.q, 'critical theory');

        return root.updateSavedView('SV-1', {
            name: 'Critical Theory',
            search: adv.definition,
        });
    })
    .then(function () {
        assertEq('patch same id', patches[0].id, 'SV-1');
        assertEq('patch name', patches[0].payload.name, 'Critical Theory');
        assertEq('patch mode', patches[0].payload.search.mode, 'advanced');

        const src = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'js', 'saved-views.js'), 'utf8');
        assert('no results table', src.indexOf('saved_view_works') < 0);
        assert('uses fetchSearch mapping', src.indexOf('prksSearchOptionsFromDefinition') >= 0);
        assert('no eval', src.indexOf('eval(') < 0);
        assert('index painters removed',
            src.indexOf('function renderSavedViewsIndex') < 0 &&
            src.indexOf('function bindIndexActions') < 0 &&
            src.indexOf('function openEditById') < 0 &&
            src.indexOf('function confirmDelete') < 0 &&
            src.indexOf('prksOpenSavedViewIndexEdit') < 0);
        assert('index delete stays in saved-views.js', src.indexOf('function prksDeleteSavedViewFromIndex') >= 0);

        return Promise.resolve().then(function () {
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

            /* Detail delete: confirm, still fence, delete, still fence, navigate owner. */
            const deletes = [];
            let confirmOpts = null;
            root.prksConfirmDestructive = function (opts) {
                confirmOpts = opts;
                return Promise.resolve(true);
            };
            root.deleteSavedView = function (id) { deletes.push(id); return Promise.resolve(); };
            navCalls.length = 0;
            root.prksNavigate = function (hash, opts) {
                navCalls.push({ hash: hash, replace: !!(opts && opts.replace), tabId: opts && opts.tabId });
            };
            return root.prksDeleteSavedViewFromDetail('SV-A', function () { return false; }, 'tab-1')
                .then(function () {
                    assertEq('stale owner after confirm does not delete', deletes.length, 0);
                    assertEq('stale owner after confirm does not navigate', navCalls.length, 0);
                    let calls = 0;
                    return root.prksDeleteSavedViewFromDetail('SV-B', function () {
                        calls += 1;
                        return calls === 1;
                    }, 'tab-1');
                })
                .then(function () {
                    assertEq('delete started before stale still deletes', deletes.join(','), 'SV-B');
                    assertEq('owner gone after delete does not navigate', navCalls.length, 0);
                    return root.prksDeleteSavedViewFromDetail('SV-C', function () { return true; }, 'tab-1');
                })
                .then(function () {
                    assertEq('current owner delete', deletes.join(','), 'SV-B,SV-C');
                    assertEq('current owner navigates to index', navCalls[0] && navCalls[0].hash, '#/views');
                    assertEq('current owner navigate replaces', navCalls[0] && navCalls[0].replace, true);
                    assertEq('current owner navigate targets tab', navCalls[0] && navCalls[0].tabId, 'tab-1');
                    assertEq('confirm names Delete Saved View', confirmOpts && confirmOpts.confirmLabel, 'Delete Saved View');
                    root.prksConfirmDestructive = function () { return Promise.resolve(false); };
                    return root.prksDeleteSavedViewFromDetail('SV-D', function () { return true; }, 'tab-1');
                })
                .then(function () {
                    assertEq('cancel does not delete', deletes.join(','), 'SV-B,SV-C');
                    assert('detail painters removed', typeof root.renderSavedViewDetail === 'undefined' &&
                        typeof root.renderSavedViewNotFound === 'undefined' &&
                        typeof root.prksSearchResultCardsHtml === 'undefined');
                    assert('index edit export removed', typeof root.prksOpenSavedViewIndexEdit === 'undefined');
                    assert('index painter export removed', typeof root.renderSavedViewsIndex === 'undefined');

                    /* Index delete refreshes the owning tab to #/views. A focused URL elsewhere stays there. */
                    root.prksConfirmDestructive = function () { return Promise.resolve(true); };
                    assert('index refresh does not read the focused hash', src.indexOf('prksCurrentCanonicalHash') < 0);
                    navCalls.length = 0;
                    return root.prksDeleteSavedViewFromIndex('SV-I1', function () { return false; }, 'tab-index');
                })
                .then(function () {
                    assertEq('stale index after confirm does not delete', deletes.join(','), 'SV-B,SV-C');
                    assertEq('stale index after confirm does not refresh', navCalls.length, 0);
                    let calls = 0;
                    return root.prksDeleteSavedViewFromIndex('SV-I2', function () {
                        calls += 1;
                        return calls === 1;
                    }, 'tab-index');
                })
                .then(function () {
                    assertEq('index delete started before stale still deletes', deletes.join(','), 'SV-B,SV-C,SV-I2');
                    assertEq('index owner gone after delete does not refresh', navCalls.length, 0);
                    root.location.hash = '#/folders';
                    return root.prksDeleteSavedViewFromIndex('SV-I3', function () { return true; }, 'tab-index');
                })
                .then(function () {
                    assertEq('current index delete', deletes[deletes.length - 1], 'SV-I3');
                    assertEq('focused folders URL still refreshes the owner to the index', navCalls[0] && navCalls[0].hash, '#/views');
                    assertEq('index refresh replaces', navCalls[0] && navCalls[0].replace, true);
                    assertEq('index refresh targets the owner tab', navCalls[0] && navCalls[0].tabId, 'tab-index');
                    assertEq('index refresh is one navigation', navCalls.length, 1);
                    assertEq('focused hash stays on folders', root.location.hash, '#/folders');
                    root.prksConfirmDestructive = function () { return Promise.resolve(false); };
                    navCalls.length = 0;
                    const before = deletes.length;
                    return root.prksDeleteSavedViewFromIndex('SV-I5', function () { return true; }, 'tab-index')
                        .then(function (outcome) {
                            assertEq('cancelled index delete does not delete', deletes.length, before);
                            assertEq('cancelled index delete does not refresh', navCalls.length, 0);
                            assertEq('cancelled index delete stays quiet', outcome && outcome.reason, 'cancelled');
                            root.prksConfirmDestructive = function () { return Promise.resolve(true); };
                            root.deleteSavedView = function () {
                                return Promise.reject(new Error('Could not delete Saved View.'));
                            };
                            return root.prksDeleteSavedViewFromIndex('SV-I6', function () { return true; }, 'tab-index');
                        })
                        .then(function (outcome) {
                            assertEq('failed index delete does not refresh', navCalls.length, 0);
                            assertEq('failed index delete names the failure', outcome && outcome.reason, 'failed');
                            assertEq('failed index delete keeps the message', outcome && outcome.message, 'Could not delete Saved View.');
                            return root.prksDeleteSavedViewFromIndex('SV-I7', function () { return false; }, 'tab-index');
                        })
                        .then(function (outcome) {
                            assertEq('stale index delete stays quiet', outcome && outcome.reason, 'stale');
                            assertEq('stale index delete does not refresh', navCalls.length, 0);
                            let owns = true;
                            root.deleteSavedView = function () {
                                owns = false;
                                return Promise.reject(new Error('late failure'));
                            };
                            return root.prksDeleteSavedViewFromIndex('SV-I8', function () { return owns; }, 'tab-index');
                        })
                        .then(function (outcome) {
                            assertEq('late failed index delete after the owner leaves stays stale', outcome && outcome.reason, 'stale');
                            assertEq('late failed index delete after the owner leaves does not refresh', navCalls.length, 0);
                            assert('late failed index delete after the owner leaves drops the message', !(outcome && outcome.message));
                        });
                });
        }).then(function () {
            console.log(passed + ' passed, ' + failed + ' failed');
            if (failed) process.exit(1);
        });
    })
    .catch(function (err) {
        console.error(err);
        process.exit(1);
    });
