#!/usr/bin/env node
'use strict';

/**
 * Same-route Folder hierarchy refresh ownership (#161).
 *
 * Prefer require over vm.runInContext: Sonar flags dynamic code execution
 * (javascript:S1523) on new selftests. Production fill lives in folders.js;
 * ownership tokens + commitAllowed live on TabContext and are exercised here
 * with a deferred load harness that mirrors prksFillFolderDetailTree's gate.
 */

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const tc = require(path.join(rootDir, 'frontend/js/tab-context.js'));

const {
    createPrksTabContext,
    prksDestroyAllTabContexts,
    prksFolderHierarchyTreeCommitAllowed,
} = tc;

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

function deferred() {
    let release;
    const promise = new Promise(function (resolve) {
        release = resolve;
    });
    return { promise: promise, release: release };
}

/** Minimal connected host with a tree slot (no shared makeDom clone). */
function makeTreeHost() {
    const host = {
        marker: 'empty',
        selectedId: null,
        getAttribute: function (k) {
            return k === 'data-prks-folder-detail-tree-host' ? '' : null;
        },
    };
    const container = {
        isConnected: true,
        querySelector: function (sel) {
            if (String(sel).indexOf('data-prks-folder-detail-tree-host') !== -1) return host;
            return null;
        },
    };
    return { container: container, host: host };
}

/**
 * Mirrors prksFillFolderDetailTree ownership: begin token → await load →
 * commitAllowed → write host.marker / selectedId. Does not load folders.js.
 */
async function simulatedFill(ctx, container, host, loadFn, folderId) {
    const signal =
        ctx && ctx.abortController && ctx.abortController.signal
            ? ctx.abortController.signal
            : null;
    const refreshGen = ctx.beginFolderHierarchyRefresh();
    if (refreshGen === -1) return 'destroyed';
    let rows = null;
    try {
        rows = await loadFn();
    } catch (_e) {
        rows = null;
    }
    if (!prksFolderHierarchyTreeCommitAllowed(ctx, refreshGen, container, signal)) {
        return 'stale';
    }
    const live =
        ctx.getEntity && ctx.getEntity('folder') && ctx.getEntity('folder').id != null
            ? String(ctx.getEntity('folder').id)
            : String(folderId);
    host.marker = Array.isArray(rows)
        ? rows
              .map(function (r) {
                  return r && r.title;
              })
              .join('|')
        : 'error';
    host.selectedId = live;
    return 'committed';
}

function tick() {
    return new Promise(function (resolve) {
        setImmediate(resolve);
    });
}

async function run() {
    prksDestroyAllTabContexts();

    // --- TabContext refresh generation ---
    {
        const host = { appendChild: function () {}, children: [] };
        const ctx = createPrksTabContext('gen');
        ctx.mount(host);
        assertEq('gen starts 0', ctx.folderHierarchyRefreshGeneration, 0);
        const g1 = ctx.beginFolderHierarchyRefresh();
        const g2 = ctx.beginFolderHierarchyRefresh();
        assertEq('token 1', g1, 1);
        assertEq('token 2', g2, 2);
        assert('older stale', !ctx.isFolderHierarchyRefreshCurrent(g1));
        assert('newest current', ctx.isFolderHierarchyRefreshCurrent(g2));
        ctx.beginRoute({ name: 'folder-detail' });
        assertEq('route resets gen', ctx.folderHierarchyRefreshGeneration, 0);
        const g3 = ctx.beginFolderHierarchyRefresh();
        assert('post-route current', ctx.isFolderHierarchyRefreshCurrent(g3));
        ctx.unmount();
        assert('unmounted not current', !ctx.isFolderHierarchyRefreshCurrent(g3));
        const dead = createPrksTabContext('dead');
        dead.mount(host);
        const gd = dead.beginFolderHierarchyRefresh();
        dead.destroy();
        assertEq('destroyed begin', dead.beginFolderHierarchyRefresh(), -1);
        assert('destroyed not current', !dead.isFolderHierarchyRefreshCurrent(gd));
        prksDestroyAllTabContexts();
    }

    // --- commitAllowed contracts ---
    {
        const ctx = createPrksTabContext('allowed');
        const tree = makeTreeHost();
        ctx.mount({ appendChild: function () {}, children: [] });
        const gen = ctx.beginFolderHierarchyRefresh();
        assert(
            'allowed current',
            prksFolderHierarchyTreeCommitAllowed(ctx, gen, tree.container, { aborted: false })
        );
        ctx.beginFolderHierarchyRefresh();
        assert(
            'refused stale gen',
            !prksFolderHierarchyTreeCommitAllowed(ctx, gen, tree.container, { aborted: false })
        );
        const gen2 = ctx.folderHierarchyRefreshGeneration;
        assert(
            'refused aborted',
            !prksFolderHierarchyTreeCommitAllowed(ctx, gen2, tree.container, { aborted: true })
        );
        tree.container.isConnected = false;
        assert(
            'refused disconnected',
            !prksFolderHierarchyTreeCommitAllowed(ctx, gen2, tree.container, { aborted: false })
        );
        prksDestroyAllTabContexts();
    }

    // --- Overlapping A then B; B resolves first; stale A cannot overwrite ---
    {
        const ctx = createPrksTabContext('race');
        const tree = makeTreeHost();
        ctx.mount({ appendChild: function () {}, children: [] });
        ctx.setEntity('folder', { id: 'alpha', title: 'Alpha' });
        const gates = [];
        const load = function () {
            const g = deferred();
            gates.push(g);
            return g.promise;
        };
        const pA = simulatedFill(ctx, tree.container, tree.host, load, 'alpha');
        const pB = simulatedFill(ctx, tree.container, tree.host, load, 'alpha');
        await tick();
        assertEq('race gated', gates.length, 2);
        gates[1].release([
            { id: 'alpha', title: 'Alpha-NEW' },
            { id: 'child', title: 'Child-NEW' },
        ]);
        assertEq('B commits', await pB, 'committed');
        assertEq('B topology', tree.host.marker, 'Alpha-NEW|Child-NEW');
        gates[0].release([
            { id: 'alpha', title: 'Alpha-OLD' },
            { id: 'legacy', title: 'Legacy' },
        ]);
        assertEq('A stale', await pA, 'stale');
        assertEq('A did not overwrite', tree.host.marker, 'Alpha-NEW|Child-NEW');
        prksDestroyAllTabContexts();
    }

    // --- Selection: live entity advances; stale A cannot restore old selection ---
    {
        const ctx = createPrksTabContext('sel');
        const tree = makeTreeHost();
        ctx.mount({ appendChild: function () {}, children: [] });
        const gates = [];
        const load = function () {
            const g = deferred();
            gates.push(g);
            return g.promise;
        };
        ctx.setEntity('folder', { id: 'a-sel', title: 'A' });
        const pA = simulatedFill(ctx, tree.container, tree.host, load, 'a-sel');
        ctx.setEntity('folder', { id: 'b-sel', title: 'B' });
        const pB = simulatedFill(ctx, tree.container, tree.host, load, 'b-sel');
        await tick();
        gates[1].release([
            { id: 'b-sel', title: 'Select-B' },
            { id: 'c-sel', title: 'Select-C' },
        ]);
        assertEq('sel B commits', await pB, 'committed');
        assertEq('sel B id', tree.host.selectedId, 'b-sel');
        assertEq('sel B marker', tree.host.marker, 'Select-B|Select-C');
        gates[0].release([
            { id: 'a-sel', title: 'Select-A' },
            { id: 'b-sel', title: 'Select-B' },
        ]);
        assertEq('sel A stale', await pA, 'stale');
        assertEq('sel still b-sel', tree.host.selectedId, 'b-sel');
        assertEq('sel kept C', tree.host.marker, 'Select-B|Select-C');
        prksDestroyAllTabContexts();
    }

    // --- Destroy mid-flight: no commit, no throw ---
    {
        const ctx = createPrksTabContext('unmount');
        const tree = makeTreeHost();
        tree.host.marker = 'LOADING';
        ctx.mount({ appendChild: function () {}, children: [] });
        ctx.setEntity('folder', { id: 'u1', title: 'U' });
        const gate = deferred();
        const pending = simulatedFill(ctx, tree.container, tree.host, function () {
            return gate.promise;
        }, 'u1');
        await tick();
        ctx.destroy();
        gate.release([{ id: 'u1', title: 'Should-Not-Commit' }]);
        let threw = false;
        let outcome = null;
        try {
            outcome = await pending;
        } catch (_e) {
            threw = true;
        }
        assert('destroy no throw', !threw);
        assertEq('destroy outcome stale/destroyed', outcome === 'stale' || outcome === 'destroyed', true);
        assertEq('destroy no commit', tree.host.marker, 'LOADING');
        prksDestroyAllTabContexts();
    }

    // --- Rapid CREATE/rename/DELETE-style triggers: final = latest ---
    {
        const ctx = createPrksTabContext('rapid');
        const tree = makeTreeHost();
        ctx.mount({ appendChild: function () {}, children: [] });
        ctx.setEntity('folder', { id: 'root', title: 'Root' });
        const gates = [];
        const load = function () {
            const g = deferred();
            gates.push(g);
            return g.promise;
        };
        const snaps = [
            [{ id: 'root', title: 'v1' }],
            [
                { id: 'root', title: 'v2-renamed' },
                { id: 'kid', title: 'Kid' },
            ],
            [
                { id: 'root', title: 'v3-final' },
                { id: 'other', title: 'Other' },
            ],
        ];
        const pending = snaps.map(function () {
            return simulatedFill(ctx, tree.container, tree.host, load, 'root');
        });
        await tick();
        assertEq('rapid gated', gates.length, 3);
        gates[1].release(snaps[1]);
        await pending[1];
        gates[0].release(snaps[0]);
        await pending[0];
        gates[2].release(snaps[2]);
        await pending[2];
        assertEq('final marker', tree.host.marker, 'v3-final|Other');
        prksDestroyAllTabContexts();
    }
}

run()
    .then(function () {
        console.log('');
        console.log(passed + ' passed, ' + failed + ' failed');
        process.exit(failed ? 1 : 0);
    })
    .catch(function (err) {
        console.error(err);
        process.exit(1);
    });
