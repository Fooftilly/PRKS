#!/usr/bin/env node
'use strict';

/**
 * Node selftests for Library Navigation V1 hierarchy helpers
 * (frontend/js/folder-hierarchy-nav.js).
 */

const path = require('path');

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (ok ? '' : ' ' + (detail || '')));
}

function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : detail || '');
}

function assertEq(name, got, want) {
    const ok = got === want;
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function loadApi() {
    // Prefer require over vm.runInContext: the module exports its pure helpers,
    // and Sonar flags dynamic code execution (javascript:S1523) on new selftests.
    const modPath = path.join(__dirname, '../../frontend/js/folder-hierarchy-nav.js');
    delete require.cache[require.resolve(modPath)];
    return require(modPath);
}

const fixture = [
    { id: 'research', title: 'Research', parent_id: null, child_count: 2 },
    { id: 'philosophy', title: 'Philosophy', parent_id: 'research', child_count: 2 },
    { id: 'ethics', title: 'Ethics', parent_id: 'philosophy', child_count: 0 },
    { id: 'epistemology', title: 'Epistemology', parent_id: 'philosophy', child_count: 0 },
    { id: 'computing', title: 'Computing', parent_id: 'research', child_count: 2 },
    { id: 'ai', title: 'AI', parent_id: 'computing', child_count: 0 },
    { id: 'networking', title: 'Networking', parent_id: 'computing', child_count: 0 },
    {
        id: 'long',
        title: 'A Very Long Folder Name That Should Ellipsize Gracefully In The Switcher',
        parent_id: 'research',
        child_count: 0,
    },
];

const api = loadApi();

(function testContextEthics() {
    const ctx = api.prksFolderHierarchyContext('ethics', fixture);
    assert('ethics found', ctx.found);
    assertEq('ethics title', ctx.current && ctx.current.title, 'Ethics');
    assertEq('ethics parent', ctx.parent && ctx.parent.title, 'Philosophy');
    assertEq('ethics ancestors', ctx.ancestors.map((a) => a.title).join('>'), 'Research>Philosophy');
    assertEq(
        'ethics siblings',
        ctx.siblings.map((s) => s.title).sort().join(','),
        'Epistemology,Ethics'
    );
    assertEq('ethics children', ctx.children.length, 0);
    assertEq('ethics path', ctx.pathParts.join(' › '), 'Research › Philosophy › Ethics');
})();

(function testContextRoot() {
    const ctx = api.prksFolderHierarchyContext('research', fixture);
    assert('root found', ctx.found);
    assert('root no parent', ctx.parent === null);
    assertEq('root ancestors', ctx.ancestors.length, 0);
    assert(
        'root siblings are top-level only',
        ctx.siblings.length === 1 && ctx.siblings[0].title === 'Research'
    );
    assert(
        'root children include Philosophy and Computing',
        ctx.children.some((c) => c.title === 'Philosophy') &&
            ctx.children.some((c) => c.title === 'Computing')
    );
})();

(function testContextLeafNoSiblingsAlone() {
    // Single child under a parent still lists itself as the only sibling.
    const tiny = [
        { id: 'a', title: 'A', parent_id: null, child_count: 1 },
        { id: 'b', title: 'B', parent_id: 'a', child_count: 0 },
    ];
    const ctx = api.prksFolderHierarchyContext('b', tiny);
    assertEq('only-child siblings', ctx.siblings.length, 1);
    assertEq('only-child sibling is self', ctx.siblings[0].id, 'b');
    assertEq('only-child children', ctx.children.length, 0);
})();

(function testMissingFolder() {
    const ctx = api.prksFolderHierarchyContext('deleted', fixture);
    assert('missing not found', !ctx.found);
    assert('missing current null', ctx.current === null);
})();

(function testFilterCrossBranch() {
    const matches = api.prksFolderHierarchyFilter(fixture, 'AI', 40);
    assert('filter finds AI', matches.some((m) => m.id === 'ai'));
    const ai = matches.find((m) => m.id === 'ai');
    assert(
        'filter path includes Computing',
        ai && String(ai.path).indexOf('Computing') !== -1
    );
    const empty = api.prksFolderHierarchyFilter(fixture, 'zzzz-nope', 40);
    assertEq('filter miss empty', empty.length, 0);
})();

(function testFilterBound() {
    const many = [];
    for (let i = 0; i < 100; i++) {
        many.push({ id: 'f' + i, title: 'Folder ' + i, parent_id: null, child_count: 0 });
    }
    const matches = api.prksFolderHierarchyFilter(many, 'Folder', 10);
    assertEq('filter respects limit', matches.length, 10);
})();

(function testFilterRanksExactTitleFirst() {
    const many = [];
    for (let i = 0; i < 50; i++) {
        many.push({
            id: 'child-' + i,
            title: 'Child ' + i,
            parent_id: 'target',
            child_count: 0,
        });
    }
    many.push({ id: 'target', title: 'Research', parent_id: null, child_count: 50 });
    const matches = api.prksFolderHierarchyFilter(many, 'Research', 40);
    assert('exact title survives bound', matches.some((m) => m.id === 'target'));
    assertEq('exact title ranked first', matches[0] && matches[0].id, 'target');
})();

(function testTriggerHtml() {
    const html = api.prksFolderNavTriggerHtml(
        {
            id: 'ethics',
            title: 'Ethics',
            parent: { id: 'philosophy', title: 'Philosophy' },
        },
        null,
        { tabId: 'tab-42' }
    );
    assert('trigger has button', html.indexOf('prks-folder-nav__trigger') !== -1);
    assert('trigger has dialog popup', html.indexOf('aria-haspopup="dialog"') !== -1);
    assert('trigger has current id', html.indexOf('data-prks-folder-nav-current="ethics"') !== -1);
    assert('trigger has tab id', html.indexOf('data-prks-folder-nav-tab-id="tab-42"') !== -1);
    assert(
        'trigger id is instance-local',
        html.indexOf('id="prks-folder-nav-trigger-tab-42"') !== -1
    );
    assert('trigger has sr label', html.indexOf('Open folder navigation') !== -1);
    assert('trigger shows Browse hierarchy', html.indexOf('Browse hierarchy') !== -1);
    assert('band has Location eyebrow', html.indexOf('>Location<') !== -1);
    assert('band has nearby host', html.indexOf('folder-nav-nearby') !== -1);
    assert('crumbs show Library', html.indexOf('>Library<') !== -1);
    assert('crumbs show Philosophy', html.indexOf('>Philosophy<') !== -1);
    assert('crumbs show Ethics', html.indexOf('>Ethics<') !== -1);
})();

(function testEmptyLibrary() {
    const ctx = api.prksFolderHierarchyContext('x', []);
    assert('empty lib not found', !ctx.found);
    const matches = api.prksFolderHierarchyFilter([], 'a', 10);
    assertEq('empty filter', matches.length, 0);
})();

console.log('');
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);
