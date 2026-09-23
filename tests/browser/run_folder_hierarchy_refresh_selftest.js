#!/usr/bin/env node
'use strict';

/**
 * Same-route Folder hierarchy refresh ownership (#161).
 *
 * Exercises TabContext mode-aware tokens + commitAllowed (no folders.js load,
 * no vm sandbox — Sonar S1523). Mirrors prksFillFolderDetailTree's gate with a
 * deferred-load harness.
 */

const path = require('path');
const rootDir = path.resolve(__dirname, '../..');
const tc = require(path.join(rootDir, 'frontend/js/tab-context.js'));
const {
    createPrksTabContext,
    prksDestroyAllTabContexts,
    prksFolderHierarchyTreeCommitAllowed,
} = tc;

const tally = { pass: 0, fail: 0 };
function ok(label, cond, msg) {
    if (cond) {
        tally.pass += 1;
        console.log('PASS  ' + label);
        return;
    }
    tally.fail += 1;
    console.log('FAIL  ' + label + (msg ? ' — ' + msg : ''));
}
function same(label, a, b) {
    ok(label, Object.is(a, b), 'expected ' + JSON.stringify(b) + ', got ' + JSON.stringify(a));
}

function gate() {
    let unlock;
    const p = new Promise(function (r) {
        unlock = r;
    });
    return { p: p, unlock: unlock };
}

function treeSlot() {
    const slot = { marker: 'empty', selectedId: null };
    slot.getAttribute = function (k) {
        return k === 'data-prks-folder-detail-tree-host' ? '' : null;
    };
    const box = {
        isConnected: true,
        querySelector: function (sel) {
            return String(sel).indexOf('data-prks-folder-detail-tree-host') !== -1 ? slot : null;
        },
    };
    return { box: box, slot: slot };
}

/**
 * @param {'full'|'selection'} mode
 */
async function fillOnce(ctx, box, slot, loadFn, folderId, mode) {
    const signal =
        ctx && ctx.abortController && ctx.abortController.signal
            ? ctx.abortController.signal
            : null;
    const token = ctx.beginFolderHierarchyRefresh(mode || 'full');
    if (token == null) return 'destroyed';
    let rows = null;
    try {
        rows = await loadFn();
    } catch (_e) {
        rows = null;
    }
    if (!prksFolderHierarchyTreeCommitAllowed(ctx, token, box, signal)) return 'stale';
    const live =
        ctx.getEntity && ctx.getEntity('folder') && ctx.getEntity('folder').id != null
            ? String(ctx.getEntity('folder').id)
            : String(folderId);
    if (mode === 'selection') {
        // Selection-only: move selection, leave topology marker untouched.
        slot.selectedId = live;
        return 'committed';
    }
    slot.marker = Array.isArray(rows)
        ? rows
              .map(function (r) {
                  return r && r.title;
              })
              .join('|')
        : 'error';
    slot.selectedId = live;
    return 'committed';
}

function nextTick() {
    return new Promise(function (r) {
        setImmediate(r);
    });
}

async function run() {
    prksDestroyAllTabContexts();

    // --- Mode-aware TabContext tokens ---
    {
        const mountHost = { appendChild: function () {}, children: [] };
        const ctx = createPrksTabContext('gen');
        ctx.mount(mountHost);
        same('full gen starts 0', ctx.folderHierarchyFullGeneration, 0);
        same('sel gen starts 0', ctx.folderHierarchySelectionGeneration, 0);
        const f1 = ctx.beginFolderHierarchyRefresh('full');
        const f2 = ctx.beginFolderHierarchyRefresh('full');
        same('full token 1', f1.gen, 1);
        same('full token 2', f2.gen, 2);
        ok('older full stale', !ctx.isFolderHierarchyRefreshCurrent(f1));
        ok('newest full current', ctx.isFolderHierarchyRefreshCurrent(f2));
        const s1 = ctx.beginFolderHierarchyRefresh('selection');
        ok('selection does not bump full', ctx.folderHierarchyFullGeneration === 2);
        ok('selection current while full pending', ctx.isFolderHierarchyRefreshCurrent(s1));
        ok('full still current after selection', ctx.isFolderHierarchyRefreshCurrent(f2));
        const beforeRouteFull = ctx.folderHierarchyFullGeneration;
        const beforeRouteSel = ctx.folderHierarchySelectionGeneration;
        ctx.beginRoute({ name: 'folder-detail' });
        ok('route advances full gen', ctx.folderHierarchyFullGeneration === beforeRouteFull + 1);
        ok('route advances sel gen', ctx.folderHierarchySelectionGeneration === beforeRouteSel + 1);
        ok('pre-route full invalidated', !ctx.isFolderHierarchyRefreshCurrent(f2));
        ok('pre-route sel invalidated', !ctx.isFolderHierarchyRefreshCurrent(s1));
        const f3 = ctx.beginFolderHierarchyRefresh('full');
        ok('post-route full current', ctx.isFolderHierarchyRefreshCurrent(f3));
        ctx.unmount();
        ok('unmounted not current', !ctx.isFolderHierarchyRefreshCurrent(f3));
        const dead = createPrksTabContext('dead');
        dead.mount(mountHost);
        const gd = dead.beginFolderHierarchyRefresh('full');
        dead.destroy();
        same('destroyed begin null', dead.beginFolderHierarchyRefresh('full'), null);
        ok('destroyed not current', !dead.isFolderHierarchyRefreshCurrent(gd));
        prksDestroyAllTabContexts();
    }

    // --- beginRoute without AbortController: ownership still invalidates ---
    {
        const savedAC = global.AbortController;
        try {
            delete global.AbortController;
            const ctx = createPrksTabContext('no-ac');
            ctx.mount({ appendChild: function () {}, children: [] });
            const tree = treeSlot();
            const g = gate();
            const pending = fillOnce(ctx, tree.box, tree.slot, function () {
                return g.p;
            }, 'x', 'full');
            await nextTick();
            same('stub never aborts', ctx.abortController.signal.aborted, false);
            const inFlight = { mode: 'full', gen: 1 };
            ok('in-flight full current before route', ctx.isFolderHierarchyRefreshCurrent(inFlight));
            ctx.beginRoute({ name: 'folder-detail' });
            same('stub still not aborted', ctx.abortController.signal.aborted, false);
            ok('route bump stale without abort', !ctx.isFolderHierarchyRefreshCurrent(inFlight));
            g.unlock([{ id: 'x', title: 'Old' }]);
            same('pre-route fill stale', await pending, 'stale');
            same('no topology write', tree.slot.marker, 'empty');
            const post = ctx.beginFolderHierarchyRefresh('full');
            ok('post-route token current', ctx.isFolderHierarchyRefreshCurrent(post));
            // Without bump, reset-to-0 would reuse gen=1 and both could look current.
            ok('post-route gen not reused as 1', post.gen !== 1);
        } finally {
            if (savedAC) global.AbortController = savedAC;
            else delete global.AbortController;
            prksDestroyAllTabContexts();
        }
    }

    // --- commitAllowed contracts ---
    {
        const ctx = createPrksTabContext('allowed');
        const tree = treeSlot();
        ctx.mount({ appendChild: function () {}, children: [] });
        const tok = ctx.beginFolderHierarchyRefresh('full');
        ok(
            'allowed current',
            prksFolderHierarchyTreeCommitAllowed(ctx, tok, tree.box, { aborted: false })
        );
        ctx.beginFolderHierarchyRefresh('full');
        ok(
            'refused stale token',
            !prksFolderHierarchyTreeCommitAllowed(ctx, tok, tree.box, { aborted: false })
        );
        const tok2 = ctx.beginFolderHierarchyRefresh('full');
        ok(
            'refused aborted',
            !prksFolderHierarchyTreeCommitAllowed(ctx, tok2, tree.box, { aborted: true })
        );
        tree.box.isConnected = false;
        ok(
            'refused disconnected',
            !prksFolderHierarchyTreeCommitAllowed(ctx, tok2, tree.box, { aborted: false })
        );
        prksDestroyAllTabContexts();
    }

    // --- Overlapping full A then B; B first; stale A cannot overwrite ---
    {
        const ctx = createPrksTabContext('race');
        const tree = treeSlot();
        ctx.mount({ appendChild: function () {}, children: [] });
        ctx.setEntity('folder', { id: 'alpha', title: 'Alpha' });
        const gates = [];
        const load = function () {
            const g = gate();
            gates.push(g);
            return g.p;
        };
        const pA = fillOnce(ctx, tree.box, tree.slot, load, 'alpha', 'full');
        const pB = fillOnce(ctx, tree.box, tree.slot, load, 'alpha', 'full');
        await nextTick();
        same('race gated', gates.length, 2);
        gates[1].unlock([
            { id: 'alpha', title: 'Alpha-NEW' },
            { id: 'child', title: 'Child-NEW' },
        ]);
        same('B commits', await pB, 'committed');
        same('B topology', tree.slot.marker, 'Alpha-NEW|Child-NEW');
        gates[0].unlock([
            { id: 'alpha', title: 'Alpha-OLD' },
            { id: 'legacy', title: 'Legacy' },
        ]);
        same('A stale', await pA, 'stale');
        same('A did not overwrite', tree.slot.marker, 'Alpha-NEW|Child-NEW');
        prksDestroyAllTabContexts();
    }

    // --- P1: full starts first; selection-only finishes first; full still commits ---
    {
        const ctx = createPrksTabContext('full-vs-sel');
        const tree = treeSlot();
        tree.slot.marker = 'PRE-SYNC';
        tree.slot.selectedId = 'old-sel';
        ctx.mount({ appendChild: function () {}, children: [] });
        ctx.setEntity('folder', { id: 'old-sel', title: 'Old' });
        const gates = [];
        const load = function () {
            const g = gate();
            gates.push(g);
            return g.p;
        };
        const pFull = fillOnce(ctx, tree.box, tree.slot, load, 'old-sel', 'full');
        ctx.setEntity('folder', { id: 'new-sel', title: 'New' });
        const pSel = fillOnce(ctx, tree.box, tree.slot, load, 'new-sel', 'selection');
        await nextTick();
        same('full+sel gated', gates.length, 2);
        // Selection-only resolves first: may move selection, must not steal full ownership.
        gates[1].unlock([
            { id: 'old-sel', title: 'StaleTopo' },
            { id: 'new-sel', title: 'StaleTopoB' },
        ]);
        same('sel commits', await pSel, 'committed');
        same('sel moved id', tree.slot.selectedId, 'new-sel');
        same('sel left topology', tree.slot.marker, 'PRE-SYNC');
        ok('full still current after sel', ctx.isFolderHierarchyRefreshCurrent(
            // Reconstruct token shape matching first full (gen 1).
            { mode: 'full', gen: 1 }
        ));
        gates[0].unlock([
            { id: 'old-sel', title: 'PostCreate' },
            { id: 'new-sel', title: 'PostCreateChild' },
            { id: 'extra', title: 'Extra' },
        ]);
        same('full commits after sel', await pFull, 'committed');
        same('full topology applied', tree.slot.marker, 'PostCreate|PostCreateChild|Extra');
        same('full used live selection', tree.slot.selectedId, 'new-sel');
        prksDestroyAllTabContexts();
    }

    // --- Selection race: newer full selection path; stale full cannot restore ---
    {
        const ctx = createPrksTabContext('sel');
        const tree = treeSlot();
        ctx.mount({ appendChild: function () {}, children: [] });
        const gates = [];
        const load = function () {
            const g = gate();
            gates.push(g);
            return g.p;
        };
        ctx.setEntity('folder', { id: 'a-sel', title: 'A' });
        const pA = fillOnce(ctx, tree.box, tree.slot, load, 'a-sel', 'full');
        ctx.setEntity('folder', { id: 'b-sel', title: 'B' });
        const pB = fillOnce(ctx, tree.box, tree.slot, load, 'b-sel', 'full');
        await nextTick();
        gates[1].unlock([
            { id: 'b-sel', title: 'Select-B' },
            { id: 'c-sel', title: 'Select-C' },
        ]);
        same('sel B commits', await pB, 'committed');
        same('sel B id', tree.slot.selectedId, 'b-sel');
        same('sel B marker', tree.slot.marker, 'Select-B|Select-C');
        gates[0].unlock([
            { id: 'a-sel', title: 'Select-A' },
            { id: 'b-sel', title: 'Select-B' },
        ]);
        same('sel A stale', await pA, 'stale');
        same('sel still b-sel', tree.slot.selectedId, 'b-sel');
        same('sel kept C', tree.slot.marker, 'Select-B|Select-C');
        prksDestroyAllTabContexts();
    }

    // --- Destroy mid-flight: no commit, no throw ---
    {
        const ctx = createPrksTabContext('unmount');
        const tree = treeSlot();
        tree.slot.marker = 'LOADING';
        ctx.mount({ appendChild: function () {}, children: [] });
        ctx.setEntity('folder', { id: 'u1', title: 'U' });
        const g = gate();
        const pending = fillOnce(
            ctx,
            tree.box,
            tree.slot,
            function () {
                return g.p;
            },
            'u1',
            'full'
        );
        await nextTick();
        ctx.destroy();
        g.unlock([{ id: 'u1', title: 'Should-Not-Commit' }]);
        let threw = false;
        let outcome = null;
        try {
            outcome = await pending;
        } catch (_e) {
            threw = true;
        }
        ok('destroy no throw', !threw);
        ok('destroy outcome stale/destroyed', outcome === 'stale' || outcome === 'destroyed');
        same('destroy no commit', tree.slot.marker, 'LOADING');
        prksDestroyAllTabContexts();
    }

    // --- Rapid CREATE/rename/DELETE-style triggers: final = latest ---
    {
        const ctx = createPrksTabContext('rapid');
        const tree = treeSlot();
        ctx.mount({ appendChild: function () {}, children: [] });
        ctx.setEntity('folder', { id: 'root', title: 'Root' });
        const gates = [];
        const load = function () {
            const g = gate();
            gates.push(g);
            return g.p;
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
            return fillOnce(ctx, tree.box, tree.slot, load, 'root', 'full');
        });
        await nextTick();
        same('rapid gated', gates.length, 3);
        gates[1].unlock(snaps[1]);
        await pending[1];
        gates[0].unlock(snaps[0]);
        await pending[0];
        gates[2].unlock(snaps[2]);
        await pending[2];
        same('final marker', tree.slot.marker, 'v3-final|Other');
        prksDestroyAllTabContexts();
    }
}

run()
    .then(function () {
        console.log('');
        console.log(tally.pass + ' passed, ' + tally.fail + ' failed');
        process.exit(tally.fail ? 1 : 0);
    })
    .catch(function (err) {
        console.error(err);
        process.exit(1);
    });
