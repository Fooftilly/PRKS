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

(function testFolderStructureOpsFingerprint() {
    const fp = api.prksFolderHierarchyOpsFingerprintForTests;
    assertEq('empty ops fingerprint', fp([]), '');
    assertEq(
        'ignores non-folder ops',
        fp([{ operation: 'CREATE_TAG', entity_type: 'tag', op_id: 't1', status: 'pending', sequence: 1 }]),
        ''
    );
    assertEq(
        'ignores acknowledged folder ops',
        fp([
            {
                operation: 'CREATE_FOLDER',
                entity_type: 'folder',
                op_id: 'f1',
                status: 'acknowledged',
                sequence: 1,
            },
        ]),
        ''
    );
    const pending = fp([
        {
            operation: 'SET_FOLDER_FIELD',
            entity_type: 'folder',
            op_id: 'f2',
            status: 'pending',
            sequence: 2,
        },
        {
            operation: 'CREATE_FOLDER',
            entity_type: 'folder',
            op_id: 'f1',
            status: 'syncing',
            sequence: 1,
        },
    ]);
    assert(
        'pending CREATE+SET fingerprint stable/sorted',
        pending === 'f1:syncing:1|f2:pending:2'
    );
})();

async function testLoadHierarchyBehaviour() {
    const root = globalThis;
    const prev = {
        prksOfflineListFetch: root.prksOfflineListFetch,
        prksResolveOfflineFoldersIndex: root.prksResolveOfflineFoldersIndex,
        prksEffectiveFolderRows: root.prksEffectiveFolderRows,
        fetchFolders: root.fetchFolders,
        prksSync: root.prksSync,
    };

    function restore() {
        Object.keys(prev).forEach(function (k) {
            if (prev[k] === undefined) delete root[k];
            else root[k] = prev[k];
        });
    }

    try {
        // --- unavailable must not collapse via fetchFolders() → [] ---
        let fetchFoldersCalls = 0;
        root.prksOfflineListFetch = async function () {
            return { value: null, source: 'unavailable', cachedAt: null };
        };
        root.prksResolveOfflineFoldersIndex = function (result) {
            if (!result || result.source === 'unavailable') return null;
            return Array.isArray(result.value) ? result.value : null;
        };
        root.fetchFolders = async function () {
            fetchFoldersCalls += 1;
            return [];
        };
        root.prksEffectiveFolderRows = async function (rows) {
            return rows;
        };
        root.prksSync = {
            subscribe: function () {
                return function () {};
            },
            store: {
                listOperations: async function () {
                    return [];
                },
            },
        };

        delete require.cache[require.resolve(path.join(__dirname, '../../frontend/js/folder-hierarchy-nav.js'))];
        const loadApiFresh = require(path.join(__dirname, '../../frontend/js/folder-hierarchy-nav.js'));
        loadApiFresh.prksFolderHierarchyNavResetForTests();

        const unavailable = await loadApiFresh.prksFolderHierarchyNavLoadForTests(false, null);
        assert('unavailable load returns null', unavailable === null);
        assertEq('unavailable does not call fetchFolders', fetchFoldersCalls, 0);

        const sticky = await loadApiFresh.prksFolderHierarchyNavLoadForTests(false, null);
        assert('sticky load-error stays null', sticky === null);
        assertEq('sticky still skips fetchFolders', fetchFoldersCalls, 0);

        // --- genuine empty catalogue is success, not load-error ---
        loadApiFresh.prksFolderHierarchyNavResetForTests();
        root.prksOfflineListFetch = async function () {
            return { value: [], source: 'server', cachedAt: null };
        };
        const empty = await loadApiFresh.prksFolderHierarchyNavLoadForTests(false, null);
        assert('empty catalogue is array', Array.isArray(empty));
        assertEq('empty catalogue length 0', empty.length, 0);

        // --- cached base re-projects pending CREATE on reuse (no refetch) ---
        loadApiFresh.prksFolderHierarchyNavResetForTests();
        let fetchCount = 0;
        let pendingCreates = [];
        root.prksOfflineListFetch = async function () {
            fetchCount += 1;
            return {
                value: [{ id: 'a', title: 'A', parent_id: null, child_count: 0 }],
                source: 'server',
                cachedAt: null,
            };
        };
        root.prksEffectiveFolderRows = async function (rows) {
            const out = Array.isArray(rows) ? rows.slice() : [];
            pendingCreates.forEach(function (row) {
                out.push(row);
            });
            return out;
        };
        const first = await loadApiFresh.prksFolderHierarchyNavLoadForTests(false, null);
        assertEq('initial fetch once', fetchCount, 1);
        assertEq('initial without pending create', first.length, 1);

        pendingCreates = [{ id: 'new', title: 'New', parent_id: null, child_count: 0 }];
        const second = await loadApiFresh.prksFolderHierarchyNavLoadForTests(false, null);
        assertEq('reuse does not refetch base', fetchCount, 1);
        assert(
            'reuse re-projects pending create',
            second.some(function (r) {
                return r && r.id === 'new';
            })
        );

        // --- fingerprint change invalidates base (simulates sync subscribe) ---
        // Force a reload path by resetting fingerprint via a fresh module load
        // after mutating ops is covered by the export unit test above; here we
        // force-reload and confirm a new fetch happens.
        const forced = await loadApiFresh.prksFolderHierarchyNavLoadForTests(true, null);
        assertEq('force reload refetches', fetchCount, 2);
        assert(
            'force reload still projects pending',
            forced.some(function (r) {
                return r && r.id === 'new';
            })
        );

        // --- sync fingerprint change invalidates base and forces refetch ---
        loadApiFresh.prksFolderHierarchyNavResetForTests();
        fetchCount = 0;
        pendingCreates = [];
        let syncListener = null;
        let ops = [];
        root.prksSync = {
            subscribe: function (fn) {
                syncListener = fn;
                return function () {
                    syncListener = null;
                };
            },
            store: {
                listOperations: async function () {
                    return ops;
                },
            },
        };
        delete require.cache[require.resolve(path.join(__dirname, '../../frontend/js/folder-hierarchy-nav.js'))];
        const loadApiSync = require(path.join(__dirname, '../../frontend/js/folder-hierarchy-nav.js'));
        loadApiSync.prksFolderHierarchyNavResetForTests();
        await loadApiSync.prksFolderHierarchyNavLoadForTests(false, null);
        assertEq('sync-path initial fetch', fetchCount, 1);
        assert('sync listener bound', typeof syncListener === 'function');

        ops = [
            {
                operation: 'CREATE_FOLDER',
                entity_type: 'folder',
                op_id: 'c1',
                status: 'pending',
                sequence: 1,
            },
        ];
        syncListener();
        // Allow the async invalidate to settle.
        await new Promise(function (resolve) {
            setImmediate(resolve);
        });
        await loadApiSync.prksFolderHierarchyNavLoadForTests(false, null);
        assertEq('fingerprint change refetches base', fetchCount, 2);

        // --- in-flight fetch must not re-cache after invalidate (generation) ---
        // Warm the cache + fingerprint first, then hang a forced reload, invalidate
        // mid-flight, and confirm the superseded response does not stick in cache.
        // Gate with Promises (no polling loop) so Sonar Reliability stays clean.
        loadApiSync.prksFolderHierarchyNavResetForTests();
        fetchCount = 0;
        ops = [];
        let releaseFetch = null;
        let signalEntered = null;
        const staleBody = {
            value: [{ id: 'stale', title: 'Stale', parent_id: null, child_count: 0 }],
            source: 'server',
            cachedAt: null,
        };
        const freshBody = {
            value: [{ id: 'fresh', title: 'Fresh', parent_id: null, child_count: 0 }],
            source: 'server',
            cachedAt: null,
        };
        let nextBody = staleBody;
        let hangSecondFetch = false;
        root.prksOfflineListFetch = async function () {
            fetchCount += 1;
            const body = nextBody;
            if (hangSecondFetch && fetchCount === 2) {
                await new Promise(function (resolve) {
                    releaseFetch = resolve;
                    if (typeof signalEntered === 'function') signalEntered();
                });
            }
            return body;
        };
        let raceListener = null;
        root.prksSync = {
            subscribe: function (fn) {
                raceListener = fn;
                return function () {
                    raceListener = null;
                };
            },
            store: {
                listOperations: async function () {
                    return ops;
                },
            },
        };
        delete require.cache[require.resolve(path.join(__dirname, '../../frontend/js/folder-hierarchy-nav.js'))];
        const loadApiRace = require(path.join(__dirname, '../../frontend/js/folder-hierarchy-nav.js'));
        loadApiRace.prksFolderHierarchyNavResetForTests();
        await loadApiRace.prksFolderHierarchyNavLoadForTests(false, null);
        assertEq('race warm fetch', fetchCount, 1);
        assert('race sync listener bound', typeof raceListener === 'function');

        nextBody = staleBody;
        hangSecondFetch = true;
        releaseFetch = null;
        const enteredGate = new Promise(function (resolve) {
            signalEntered = resolve;
        });
        const inFlight = loadApiRace.prksFolderHierarchyNavLoadForTests(true, null);
        await enteredGate;
        assertEq('race fetch gated', fetchCount, 2);
        assert('race release handle ready', typeof releaseFetch === 'function');
        ops = [
            {
                operation: 'CREATE_FOLDER',
                entity_type: 'folder',
                op_id: 'c-race',
                status: 'pending',
                sequence: 2,
            },
        ];
        raceListener();
        await new Promise(function (resolve) {
            setImmediate(resolve);
        });
        nextBody = freshBody;
        hangSecondFetch = false;
        releaseFetch();
        const superseded = await inFlight;
        assert(
            'superseded flight still returns its rows',
            Array.isArray(superseded) &&
                superseded.some(function (r) {
                    return r && r.id === 'stale';
                })
        );
        const after = await loadApiRace.prksFolderHierarchyNavLoadForTests(false, null);
        assertEq('invalidate mid-flight forces refetch', fetchCount, 3);
        assert(
            'cached base is the post-invalidate fetch',
            Array.isArray(after) &&
                after.some(function (r) {
                    return r && r.id === 'fresh';
                }) &&
                !after.some(function (r) {
                    return r && r.id === 'stale';
                })
        );
    } finally {
        restore();
    }
}

testLoadHierarchyBehaviour()
    .then(function () {
        console.log('');
        console.log(passed + ' passed, ' + failed + ' failed');
        process.exit(failed ? 1 : 0);
    })
    .catch(function (err) {
        console.error(err);
        process.exit(1);
    });

