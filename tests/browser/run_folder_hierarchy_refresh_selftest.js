#!/usr/bin/env node
'use strict';

/**
 * Same-route Folder hierarchy refresh ownership (#161).
 *
 * TabContext mode-aware tokens + commitAllowed, plus race coverage against the
 * real prksFillFolderDetailTree (deferred loads via prksFolderHierarchyNavLoadForTests).
 * No vm sandbox — Sonar S1523.
 */

const path = require('path');
const rootDir = path.resolve(__dirname, '../..');

globalThis.window = globalThis;
if (typeof globalThis.document === 'undefined') {
    globalThis.document = {
        getElementById: function () {
            return null;
        },
        querySelector: function () {
            return null;
        },
        querySelectorAll: function () {
            return [];
        },
    };
}

const tc = require(path.join(rootDir, 'frontend/js/tab-context.js'));
const {
    createPrksTabContext,
    prksDestroyAllTabContexts,
    prksFolderHierarchyTreeCommitAllowed,
} = tc;

// Browser script: assigns window.prksFillFolderDetailTree (and helpers) on load.
require(path.join(rootDir, 'frontend/js/components/folders.js'));
const fillTree = globalThis.prksFillFolderDetailTree;

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
function hasTitle(html, title) {
    // Require the full span-title boundary so PRE-SYNC-B cannot satisfy PRE-SYNC.
    return String(html || '').indexOf('>' + title + '<') !== -1;
}
function selectedIdFromHtml(html) {
    const m = String(html || '').match(
        /prks-folder-tree__row[^>]*is-selected[^>]*data-folder-id="([^"]+)"|prks-folder-tree__row[^>]*data-folder-id="([^"]+)"[^>]*is-selected/
    );
    return m ? m[1] || m[2] : null;
}

function gate() {
    let unlock;
    const p = new Promise(function (r) {
        unlock = r;
    });
    return { p: p, unlock: unlock };
}

function nextTick() {
    return new Promise(function (r) {
        setImmediate(r);
    });
}

/** Queue deferred catalogue loads; each call pushes a gate for the test to unlock. */
function installDeferredLoad(gates) {
    delete globalThis.prksLoadFolderHierarchyCatalogue;
    globalThis.prksFolderHierarchyNavLoadForTests = function () {
        const g = gate();
        gates.push(g);
        return g.p;
    };
}

function installSyncLoad(rows) {
    delete globalThis.prksLoadFolderHierarchyCatalogue;
    globalThis.prksFolderHierarchyNavLoadForTests = async function () {
        return rows;
    };
}

/**
 * Minimal container + liveHost for prksFillFolderDetailTree.
 * Supports innerHTML paint and selection-only class moves (no full HTML parser).
 */
function detailTreeDom(initialHtml) {
    let html = initialHtml != null ? String(initialHtml) : '';
    const rowCache = Object.create(null);

    function linkFor(row) {
        return {
            setAttribute: function (k, v) {
                row.linkAttrs[k] = String(v);
            },
            removeAttribute: function (k) {
                delete row.linkAttrs[k];
            },
            getAttribute: function (k) {
                return Object.prototype.hasOwnProperty.call(row.linkAttrs, k)
                    ? row.linkAttrs[k]
                    : null;
            },
        };
    }

    function ensureRow(id, selected) {
        let row = rowCache[id];
        if (!row) {
            row = {
                folderId: id,
                classes: new Set(['prks-folder-tree__row']),
                attrs: { 'data-folder-id': id },
                linkAttrs: {},
            };
            row.classList = {
                add: function (c) {
                    row.classes.add(c);
                    rewriteSelectionInHtml();
                },
                remove: function (c) {
                    row.classes.delete(c);
                    rewriteSelectionInHtml();
                },
                contains: function (c) {
                    return row.classes.has(c);
                },
                toggle: function (c, on) {
                    if (on === undefined) on = !row.classes.has(c);
                    if (on) row.classes.add(c);
                    else row.classes.delete(c);
                },
            };
            row.setAttribute = function (k, v) {
                row.attrs[k] = String(v);
            };
            row.getAttribute = function (k) {
                return Object.prototype.hasOwnProperty.call(row.attrs, k) ? row.attrs[k] : null;
            };
            row.querySelector = function (sel) {
                return String(sel).indexOf('prks-folder-tree__link') !== -1 ? linkFor(row) : null;
            };
            rowCache[id] = row;
        }
        if (selected) {
            row.classes.add('is-selected');
            row.linkAttrs['aria-current'] = 'page';
        }
        return row;
    }

    function rebuildRowCacheFromHtml() {
        Object.keys(rowCache).forEach(function (k) {
            delete rowCache[k];
        });
        const re = /data-folder-id="([^"]+)"/g;
        let m;
        while ((m = re.exec(html))) {
            const id = m[1];
            if (rowCache[id]) continue;
            const start = Math.max(0, m.index - 120);
            const around = html.slice(start, m.index + 40);
            ensureRow(id, around.indexOf('is-selected') !== -1);
        }
    }

    function rewriteSelectionInHtml() {
        Object.keys(rowCache).forEach(function (id) {
            const row = rowCache[id];
            const wantSel = row.classes.has('is-selected');
            html = html.replace(
                new RegExp(
                    '(<div class="prks-folder-tree__row)([^"]*)"([^>]*data-folder-id="' +
                        id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') +
                        '")',
                    'g'
                ),
                function (_all, open, cls, rest) {
                    const parts = String(cls || '')
                        .split(/\s+/)
                        .filter(Boolean)
                        .filter(function (c) {
                            return c !== 'is-selected';
                        });
                    if (wantSel) parts.push('is-selected');
                    return open + (parts.length ? ' ' + parts.join(' ') : '') + '"' + rest;
                }
            );
        });
    }

    rebuildRowCacheFromHtml();

    const liveHost = {
        getAttribute: function (k) {
            return k === 'data-prks-folder-detail-tree-host' ? '' : null;
        },
        querySelector: function (sel) {
            const s = String(sel || '');
            if (s.indexOf('prks-folder-tree--detail-nav') !== -1) {
                return html.indexOf('prks-folder-tree--detail-nav') !== -1 ? {} : null;
            }
            if (s.indexOf('prks-folder-tree__branch') !== -1) return null;
            const m = s.match(/data-folder-id="([^"]+)"/);
            if (m) {
                rebuildRowCacheFromHtml();
                return rowCache[m[1]] || null;
            }
            return null;
        },
        querySelectorAll: function (sel) {
            const s = String(sel || '');
            rebuildRowCacheFromHtml();
            if (s.indexOf('is-selected') !== -1) {
                return Object.keys(rowCache)
                    .filter(function (id) {
                        return rowCache[id].classes.has('is-selected');
                    })
                    .map(function (id) {
                        return rowCache[id];
                    });
            }
            return [];
        },
    };
    Object.defineProperty(liveHost, 'innerHTML', {
        get: function () {
            return html;
        },
        set: function (v) {
            html = String(v || '');
            rebuildRowCacheFromHtml();
        },
    });

    const container = {
        isConnected: true,
        querySelector: function (sel) {
            return String(sel).indexOf('data-prks-folder-detail-tree-host') !== -1
                ? liveHost
                : null;
        },
    };
    return { container: container, liveHost: liveHost };
}

function mountCtx(id, folder) {
    const ctx = createPrksTabContext(id);
    ctx.mount({ appendChild: function () {}, children: [] });
    if (folder) ctx.setEntity('folder', folder);
    return ctx;
}

function row(id, title, parentId, childCount) {
    return {
        id: id,
        title: title,
        parent_id: parentId == null ? null : parentId,
        child_count: childCount || 0,
    };
}

function assertTitles(prefix, html, present, absent) {
    (present || []).forEach(function (t) {
        ok(prefix + ' has ' + t, hasTitle(html, t));
    });
    (absent || []).forEach(function (t) {
        ok(prefix + ' lacks ' + t, !hasTitle(html, t));
    });
}

/** Start fills under one deferred-load queue. Each step may setEntity then fill. */
async function startDeferredRace(ctx, container, steps) {
    const gates = [];
    installDeferredLoad(gates);
    const pending = steps.map(function (step) {
        if (step.entity) ctx.setEntity('folder', step.entity);
        const folder = step.folder || step.entity;
        return fillTree(ctx, folder, container, {
            selectionOnly: !!step.selectionOnly,
        });
    });
    await nextTick();
    return { gates: gates, pending: pending };
}

async function unlockAwait(gates, pending, index, rows) {
    gates[index].unlock(rows);
    await pending[index];
}

/**
 * Classic two-fill race: settle newer (index 1) first, snapshot, settle older,
 * assert HTML unchanged and optional title/selection checks.
 */
async function settleNewerWins(label, race, liveHost, newerRows, olderRows, afterNewer) {
    same(label + ' gated', race.gates.length, 2);
    await unlockAwait(race.gates, race.pending, 1, newerRows);
    const html = liveHost.innerHTML;
    if (afterNewer) afterNewer(html);
    const snap = liveHost.innerHTML;
    await unlockAwait(race.gates, race.pending, 0, olderRows);
    same(label + ' older did not overwrite', liveHost.innerHTML, snap);
    return snap;
}

async function run() {
    ok('prksFillFolderDetailTree exported', typeof fillTree === 'function');
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
            const loadingHtml = '<p class="prks-inline-message">Loading…</p>';
            const ctx = mountCtx('no-ac', { id: 'x', title: 'Old' });
            const dom = detailTreeDom(loadingHtml);
            const race = await startDeferredRace(ctx, dom.container, [
                { folder: { id: 'x', title: 'Old' } },
            ]);
            same('stub never aborts', ctx.abortController.signal.aborted, false);
            const inFlight = { mode: 'full', gen: 1 };
            ok('in-flight full current before route', ctx.isFolderHierarchyRefreshCurrent(inFlight));
            ctx.beginRoute({ name: 'folder-detail' });
            same('stub still not aborted', ctx.abortController.signal.aborted, false);
            ok('route bump stale without abort', !ctx.isFolderHierarchyRefreshCurrent(inFlight));
            await unlockAwait(race.gates, race.pending, 0, [row('x', 'Old')]);
            same('no topology write', dom.liveHost.innerHTML, loadingHtml);
            const post = ctx.beginFolderHierarchyRefresh('full');
            ok('post-route token current', ctx.isFolderHierarchyRefreshCurrent(post));
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
        const dom = detailTreeDom('');
        ctx.mount({ appendChild: function () {}, children: [] });
        const tok = ctx.beginFolderHierarchyRefresh('full');
        ok(
            'allowed current',
            prksFolderHierarchyTreeCommitAllowed(ctx, tok, dom.container, { aborted: false })
        );
        ctx.beginFolderHierarchyRefresh('full');
        ok(
            'refused stale token',
            !prksFolderHierarchyTreeCommitAllowed(ctx, tok, dom.container, { aborted: false })
        );
        const tok2 = ctx.beginFolderHierarchyRefresh('full');
        ok(
            'refused aborted',
            !prksFolderHierarchyTreeCommitAllowed(ctx, tok2, dom.container, { aborted: true })
        );
        dom.container.isConnected = false;
        ok(
            'refused disconnected',
            !prksFolderHierarchyTreeCommitAllowed(ctx, tok2, dom.container, { aborted: false })
        );
        prksDestroyAllTabContexts();
    }

    // --- Overlapping full A then B; B first; stale A cannot overwrite ---
    {
        const folder = { id: 'alpha', title: 'Alpha' };
        const ctx = mountCtx('race', folder);
        const dom = detailTreeDom('');
        const race = await startDeferredRace(ctx, dom.container, [
            { folder: folder },
            { folder: folder },
        ]);
        await settleNewerWins(
            'race',
            race,
            dom.liveHost,
            [row('alpha', 'Alpha-NEW', null, 1), row('child', 'Child-NEW', 'alpha')],
            [row('alpha', 'Alpha-OLD', null, 1), row('legacy', 'Legacy', 'alpha')],
            function (html) {
                assertTitles('B topology', html, ['Alpha-NEW', 'Child-NEW']);
            }
        );
        assertTitles('after A', dom.liveHost.innerHTML, null, ['Alpha-OLD']);
        prksDestroyAllTabContexts();
    }

    // --- P1: full starts first; selection-only finishes first; full still commits ---
    {
        const oldF = { id: 'old-sel', title: 'Old' };
        const newF = { id: 'new-sel', title: 'New' };
        const ctx = mountCtx('full-vs-sel', oldF);
        const dom = detailTreeDom('');
        installSyncLoad([row('old-sel', 'PRE-SYNC'), row('new-sel', 'PRE-SYNC-B')]);
        await fillTree(ctx, oldF, dom.container, { selectionOnly: false });
        ok('seed has PRE-SYNC', hasTitle(dom.liveHost.innerHTML, 'PRE-SYNC'));
        same('seed selected old-sel', selectedIdFromHtml(dom.liveHost.innerHTML), 'old-sel');

        const race = await startDeferredRace(ctx, dom.container, [
            { folder: oldF },
            { entity: newF, selectionOnly: true },
        ]);
        same('full+sel gated', race.gates.length, 2);
        await unlockAwait(race.gates, race.pending, 1, [
            row('old-sel', 'SEL-ROWS-A'),
            row('new-sel', 'SEL-ROWS-B'),
        ]);
        assertTitles('sel left seed', dom.liveHost.innerHTML, ['PRE-SYNC', 'PRE-SYNC-B'], [
            'SEL-ROWS-A',
        ]);
        same('sel moved id', selectedIdFromHtml(dom.liveHost.innerHTML), 'new-sel');
        ok(
            'full still current after sel',
            ctx.isFolderHierarchyRefreshCurrent({ mode: 'full', gen: 2 })
        );
        await unlockAwait(race.gates, race.pending, 0, [
            row('old-sel', 'PostCreate'),
            row('new-sel', 'PostCreateChild'),
            row('extra', 'Extra'),
        ]);
        assertTitles('full topology', dom.liveHost.innerHTML, ['PostCreate', 'Extra']);
        same('full used live selection', selectedIdFromHtml(dom.liveHost.innerHTML), 'new-sel');
        prksDestroyAllTabContexts();
    }

    // --- P1: selection-only fallback rebuild claims full; older full A rejected ---
    // Empty/loading host → select fails → B rebuilds and supersedes in-flight full A.
    {
        const oldF = { id: 'old', title: 'Old' };
        const newF = { id: 'new', title: 'New' };
        const ctx = mountCtx('sel-fallback', oldF);
        const dom = detailTreeDom('<p class="loading">LOADING</p>');
        const race = await startDeferredRace(ctx, dom.container, [
            { folder: oldF },
            { entity: newF, selectionOnly: true },
        ]);
        await settleNewerWins(
            'fallback',
            race,
            dom.liveHost,
            [row('old', 'NEW-TOPO-OLD'), row('new', 'NEW-TOPO-NEW')],
            [row('old', 'OLD-TOPO')],
            function (html) {
                assertTitles('B rebuilt', html, ['NEW-TOPO-NEW', 'NEW-TOPO-OLD']);
                same('B selected new', selectedIdFromHtml(html), 'new');
                ok(
                    'full A stale after B fallback claim',
                    !ctx.isFolderHierarchyRefreshCurrent({ mode: 'full', gen: 1 })
                );
            }
        );
        assertTitles('after A', dom.liveHost.innerHTML, null, ['OLD-TOPO']);
        prksDestroyAllTabContexts();
    }

    // --- Selection race: newer full; stale full cannot restore ---
    {
        const aF = { id: 'a-sel', title: 'A' };
        const bF = { id: 'b-sel', title: 'B' };
        const ctx = mountCtx('sel', aF);
        const dom = detailTreeDom('');
        const race = await startDeferredRace(ctx, dom.container, [
            { folder: aF },
            { entity: bF },
        ]);
        await settleNewerWins(
            'sel',
            race,
            dom.liveHost,
            [row('b-sel', 'Select-B'), row('c-sel', 'Select-C')],
            [row('a-sel', 'Select-A'), row('b-sel', 'Select-B')],
            function (html) {
                assertTitles('sel B', html, ['Select-B', 'Select-C']);
                same('sel B id', selectedIdFromHtml(html), 'b-sel');
            }
        );
        same('sel still b-sel', selectedIdFromHtml(dom.liveHost.innerHTML), 'b-sel');
        prksDestroyAllTabContexts();
    }

    // --- Destroy mid-flight: no commit, no throw ---
    {
        const loadingHtml = '<p class="loading">LOADING</p>';
        const ctx = mountCtx('unmount', { id: 'u1', title: 'U' });
        const dom = detailTreeDom(loadingHtml);
        const race = await startDeferredRace(ctx, dom.container, [
            { folder: { id: 'u1', title: 'U' } },
        ]);
        ctx.destroy();
        let threw = false;
        try {
            await unlockAwait(race.gates, race.pending, 0, [row('u1', 'Should-Not-Commit')]);
        } catch (_e) {
            threw = true;
        }
        ok('destroy no throw', !threw);
        same('destroy no commit', dom.liveHost.innerHTML, loadingHtml);
        prksDestroyAllTabContexts();
    }

    // --- Rapid CREATE/rename/DELETE-style triggers: final = latest ---
    {
        const root = { id: 'root', title: 'Root' };
        const ctx = mountCtx('rapid', root);
        const dom = detailTreeDom('');
        const snaps = [
            [row('root', 'v1')],
            [row('root', 'v2-renamed', null, 1), row('kid', 'Kid', 'root')],
            [row('root', 'v3-final', null, 1), row('other', 'Other', 'root')],
        ];
        const race = await startDeferredRace(
            ctx,
            dom.container,
            snaps.map(function () {
                return { folder: root };
            })
        );
        same('rapid gated', race.gates.length, 3);
        const order = [1, 0, 2];
        for (let i = 0; i < order.length; i++) {
            const idx = order[i];
            await unlockAwait(race.gates, race.pending, idx, snaps[idx]);
        }
        assertTitles('final', dom.liveHost.innerHTML, ['v3-final', 'Other'], ['v1']);
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
