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
        navCalls.push({ hash: hash, replace: !!(opts && opts.replace) });
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

        console.log(passed + ' passed, ' + failed + ' failed');
        if (failed) process.exit(1);
    })
    .catch(function (err) {
        console.error(err);
        process.exit(1);
    });
