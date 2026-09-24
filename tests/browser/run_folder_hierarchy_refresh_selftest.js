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
            const ctx = mountCtx('no-ac', { id: 'x', title: 'Old' });
            const dom = detailTreeDom('<p class="prks-inline-message">Loading…</p>');
            const gates = [];
            installDeferredLoad(gates);
            const pending = fillTree(ctx, { id: 'x', title: 'Old' }, dom.container, {
                selectionOnly: false,
            });
            await nextTick();
            same('stub never aborts', ctx.abortController.signal.aborted, false);
            const inFlight = { mode: 'full', gen: 1 };
            ok('in-flight full current before route', ctx.isFolderHierarchyRefreshCurrent(inFlight));
            ctx.beginRoute({ name: 'folder-detail' });
            same('stub still not aborted', ctx.abortController.signal.aborted, false);
            ok('route bump stale without abort', !ctx.isFolderHierarchyRefreshCurrent(inFlight));
            gates[0].unlock([{ id: 'x', title: 'Old', parent_id: null, child_count: 0 }]);
            await pending;
            same('no topology write', dom.liveHost.innerHTML, '<p class="prks-inline-message">Loading…</p>');
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

    // --- Overlapping full A then B against real fill; B first; stale A cannot overwrite ---
    {
        const ctx = mountCtx('race', { id: 'alpha', title: 'Alpha' });
        const dom = detailTreeDom('');
        const gates = [];
        installDeferredLoad(gates);
        const pA = fillTree(ctx, { id: 'alpha', title: 'Alpha' }, dom.container, {
            selectionOnly: false,
        });
        const pB = fillTree(ctx, { id: 'alpha', title: 'Alpha' }, dom.container, {
            selectionOnly: false,
        });
        await nextTick();
        same('race gated', gates.length, 2);
        gates[1].unlock([
            { id: 'alpha', title: 'Alpha-NEW', parent_id: null, child_count: 1 },
            { id: 'child', title: 'Child-NEW', parent_id: 'alpha', child_count: 0 },
        ]);
        await pB;
        ok('B topology Alpha-NEW', hasTitle(dom.liveHost.innerHTML, 'Alpha-NEW'));
        ok('B topology Child-NEW', hasTitle(dom.liveHost.innerHTML, 'Child-NEW'));
        const afterB = dom.liveHost.innerHTML;
        gates[0].unlock([
            { id: 'alpha', title: 'Alpha-OLD', parent_id: null, child_count: 1 },
            { id: 'legacy', title: 'Legacy', parent_id: 'alpha', child_count: 0 },
        ]);
        await pA;
        same('A did not overwrite', dom.liveHost.innerHTML, afterB);
        ok('A titles absent', !hasTitle(dom.liveHost.innerHTML, 'Alpha-OLD'));
        prksDestroyAllTabContexts();
    }

    // --- P1: full starts first; selection-only finishes first; full still commits ---
    {
        const ctx = mountCtx('full-vs-sel', { id: 'old-sel', title: 'Old' });
        const seedRows = [
            { id: 'old-sel', title: 'PRE-SYNC', parent_id: null, child_count: 0 },
            { id: 'new-sel', title: 'PRE-SYNC-B', parent_id: null, child_count: 0 },
        ];
        installSyncLoad(seedRows);
        const dom = detailTreeDom('');
        await fillTree(ctx, { id: 'old-sel', title: 'Old' }, dom.container, {
            selectionOnly: false,
        });
        ok('seed has PRE-SYNC', hasTitle(dom.liveHost.innerHTML, 'PRE-SYNC'));
        same('seed selected old-sel', selectedIdFromHtml(dom.liveHost.innerHTML), 'old-sel');

        const gates = [];
        installDeferredLoad(gates);
        const pFull = fillTree(ctx, { id: 'old-sel', title: 'Old' }, dom.container, {
            selectionOnly: false,
        });
        ctx.setEntity('folder', { id: 'new-sel', title: 'New' });
        const pSel = fillTree(ctx, { id: 'new-sel', title: 'New' }, dom.container, {
            selectionOnly: true,
        });
        await nextTick();
        same('full+sel gated', gates.length, 2);
        // Distinct titles: if selection-only rebuilt, these would appear.
        gates[1].unlock([
            { id: 'old-sel', title: 'SEL-ROWS-A', parent_id: null, child_count: 0 },
            { id: 'new-sel', title: 'SEL-ROWS-B', parent_id: null, child_count: 0 },
        ]);
        await pSel;
        // Selection may move is-selected / aria-current; titles/structure must stay.
        ok('sel left PRE-SYNC title', hasTitle(dom.liveHost.innerHTML, 'PRE-SYNC'));
        ok('sel left PRE-SYNC-B title', hasTitle(dom.liveHost.innerHTML, 'PRE-SYNC-B'));
        ok('sel did not rebuild from its rows', !hasTitle(dom.liveHost.innerHTML, 'SEL-ROWS-A'));
        same('sel moved id', selectedIdFromHtml(dom.liveHost.innerHTML), 'new-sel');
        ok(
            'full still current after sel',
            ctx.isFolderHierarchyRefreshCurrent({ mode: 'full', gen: 2 })
        );
        gates[0].unlock([
            { id: 'old-sel', title: 'PostCreate', parent_id: null, child_count: 0 },
            { id: 'new-sel', title: 'PostCreateChild', parent_id: null, child_count: 0 },
            { id: 'extra', title: 'Extra', parent_id: null, child_count: 0 },
        ]);
        await pFull;
        ok('full topology PostCreate', hasTitle(dom.liveHost.innerHTML, 'PostCreate'));
        ok('full topology Extra', hasTitle(dom.liveHost.innerHTML, 'Extra'));
        same('full used live selection', selectedIdFromHtml(dom.liveHost.innerHTML), 'new-sel');
        prksDestroyAllTabContexts();
    }

    // --- P1: selection-only fallback rebuild claims full; older full A rejected ---
    // Tree still loading (no detail-nav / destination absent) → select fails →
    // B rebuilds with newer rows and must supersede in-flight full A.
    {
        const ctx = mountCtx('sel-fallback', { id: 'old', title: 'Old' });
        const dom = detailTreeDom('<p class="loading">LOADING</p>');
        const gates = [];
        installDeferredLoad(gates);
        const pFull = fillTree(ctx, { id: 'old', title: 'Old' }, dom.container, {
            selectionOnly: false,
        });
        ctx.setEntity('folder', { id: 'new', title: 'New' });
        const pSel = fillTree(ctx, { id: 'new', title: 'New' }, dom.container, {
            selectionOnly: true,
        });
        await nextTick();
        same('fallback gated', gates.length, 2);
        gates[1].unlock([
            { id: 'old', title: 'NEW-TOPO-OLD', parent_id: null, child_count: 0 },
            { id: 'new', title: 'NEW-TOPO-NEW', parent_id: null, child_count: 0 },
        ]);
        await pSel;
        ok('B rebuilt NEW-TOPO-NEW', hasTitle(dom.liveHost.innerHTML, 'NEW-TOPO-NEW'));
        ok('B rebuilt NEW-TOPO-OLD', hasTitle(dom.liveHost.innerHTML, 'NEW-TOPO-OLD'));
        same('B selected new', selectedIdFromHtml(dom.liveHost.innerHTML), 'new');
        ok(
            'full A stale after B fallback claim',
            !ctx.isFolderHierarchyRefreshCurrent({ mode: 'full', gen: 1 })
        );
        const afterB = dom.liveHost.innerHTML;
        gates[0].unlock([{ id: 'old', title: 'OLD-TOPO', parent_id: null, child_count: 0 }]);
        await pFull;
        same('A did not overwrite B fallback', dom.liveHost.innerHTML, afterB);
        ok('OLD-TOPO absent', !hasTitle(dom.liveHost.innerHTML, 'OLD-TOPO'));
        prksDestroyAllTabContexts();
    }

    // --- Selection race: newer full; stale full cannot restore ---
    {
        const ctx = mountCtx('sel', { id: 'a-sel', title: 'A' });
        const dom = detailTreeDom('');
        const gates = [];
        installDeferredLoad(gates);
        const pA = fillTree(ctx, { id: 'a-sel', title: 'A' }, dom.container, {
            selectionOnly: false,
        });
        ctx.setEntity('folder', { id: 'b-sel', title: 'B' });
        const pB = fillTree(ctx, { id: 'b-sel', title: 'B' }, dom.container, {
            selectionOnly: false,
        });
        await nextTick();
        gates[1].unlock([
            { id: 'b-sel', title: 'Select-B', parent_id: null, child_count: 0 },
            { id: 'c-sel', title: 'Select-C', parent_id: null, child_count: 0 },
        ]);
        await pB;
        ok('sel B marker', hasTitle(dom.liveHost.innerHTML, 'Select-B'));
        ok('sel B has C', hasTitle(dom.liveHost.innerHTML, 'Select-C'));
        same('sel B id', selectedIdFromHtml(dom.liveHost.innerHTML), 'b-sel');
        const afterB = dom.liveHost.innerHTML;
        gates[0].unlock([
            { id: 'a-sel', title: 'Select-A', parent_id: null, child_count: 0 },
            { id: 'b-sel', title: 'Select-B', parent_id: null, child_count: 0 },
        ]);
        await pA;
        same('sel kept B tree', dom.liveHost.innerHTML, afterB);
        same('sel still b-sel', selectedIdFromHtml(dom.liveHost.innerHTML), 'b-sel');
        prksDestroyAllTabContexts();
    }

    // --- Destroy mid-flight: no commit, no throw ---
    {
        const ctx = mountCtx('unmount', { id: 'u1', title: 'U' });
        const dom = detailTreeDom('<p class="loading">LOADING</p>');
        const gates = [];
        installDeferredLoad(gates);
        const pending = fillTree(ctx, { id: 'u1', title: 'U' }, dom.container, {
            selectionOnly: false,
        });
        await nextTick();
        ctx.destroy();
        gates[0].unlock([{ id: 'u1', title: 'Should-Not-Commit', parent_id: null, child_count: 0 }]);
        let threw = false;
        try {
            await pending;
        } catch (_e) {
            threw = true;
        }
        ok('destroy no throw', !threw);
        same('destroy no commit', dom.liveHost.innerHTML, '<p class="loading">LOADING</p>');
        prksDestroyAllTabContexts();
    }

    // --- Rapid CREATE/rename/DELETE-style triggers: final = latest ---
    {
        const ctx = mountCtx('rapid', { id: 'root', title: 'Root' });
        const dom = detailTreeDom('');
        const gates = [];
        installDeferredLoad(gates);
        const snaps = [
            [{ id: 'root', title: 'v1', parent_id: null, child_count: 0 }],
            [
                { id: 'root', title: 'v2-renamed', parent_id: null, child_count: 1 },
                { id: 'kid', title: 'Kid', parent_id: 'root', child_count: 0 },
            ],
            [
                { id: 'root', title: 'v3-final', parent_id: null, child_count: 1 },
                { id: 'other', title: 'Other', parent_id: 'root', child_count: 0 },
            ],
        ];
        const pending = snaps.map(function () {
            return fillTree(ctx, { id: 'root', title: 'Root' }, dom.container, {
                selectionOnly: false,
            });
        });
        await nextTick();
        same('rapid gated', gates.length, 3);
        gates[1].unlock(snaps[1]);
        await pending[1];
        gates[0].unlock(snaps[0]);
        await pending[0];
        gates[2].unlock(snaps[2]);
        await pending[2];
        ok('final v3-final', hasTitle(dom.liveHost.innerHTML, 'v3-final'));
        ok('final Other', hasTitle(dom.liveHost.innerHTML, 'Other'));
        ok('not v1', !hasTitle(dom.liveHost.innerHTML, 'v1'));
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
