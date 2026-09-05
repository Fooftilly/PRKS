#!/usr/bin/env node
'use strict';

/* Deterministic ratio-state + bounds coverage for the Main/Secondary divider.
 * No DOM/browser needed: pure bounds math (workspace-split.js) and the
 * canonical ratio state machine (workspace-tabs.js) are both plain JS.
 */

const path = require('path');
const fs = require('fs');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '../..');
const nav = require(path.join(rootDir, 'frontend/js/navigation.js'));
require(path.join(rootDir, 'frontend/js/workspace-tree.js'));
const wsApi = require(path.join(rootDir, 'frontend/js/workspace-tabs.js'));
const splitApi = require(path.join(rootDir, 'frontend/js/workspace-split.js'));

const { createPrksWorkspaceTabs } = wsApi;
const { prksSplitComputeBounds, prksSplitClampRatio } = splitApi;

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (detail ? ' ' + detail : ''));
}

function assertEq(name, got, want) {
    const ok = got === want;
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : detail || '');
}

function assertClose(name, got, want, tol) {
    const ok = Math.abs(got - want) <= (tol == null ? 1e-9 : tol);
    record(name, ok, ok ? '' : 'got=' + got + ' want=' + want);
}

function makeHistory(initialHash) {
    let hash = initialHash;
    let href = 'http://127.0.0.1/' + initialHash;
    let state = null;
    return {
        getHash: function () {
            return hash;
        },
        getHref: function () {
            return href;
        },
        getState: function () {
            return state;
        },
        pushState: function (s, url) {
            const u = new URL(url);
            hash = u.hash;
            href = url;
            state = s;
        },
        replaceState: function (s, url) {
            if (url) {
                const u = new URL(url);
                hash = u.hash;
                href = url;
            }
            state = s;
        },
    };
}

function makeHarness(opts) {
    opts = opts || {};
    const hist = makeHistory(opts.hash || '#/folders');
    const ws = createPrksWorkspaceTabs({
        parseRoute: nav.prksParseRoute,
        routeLoadingTitle: nav.prksRouteLoadingTitle,
        routeTabIcon: nav.prksRouteTabIcon,
        homeHash: '#/folders',
        historyAdapter: hist,
        supportsTile: nav.prksRouteSupportsTile,
        canLeave: function () {
            return true;
        },
        renderRoute: function () {},
        announce: function () {},
        publishMainShell: function () {},
    });
    ws.bootstrap(opts.hash || '#/folders');
    return { ws: ws, hist: hist };
}

/* ---- Minimal fake DOM for the commit-option + active-drag-cleanup regressions below.
 * workspace-split.js is loaded via vm into this fake DOM (mirrors run_workspace_tiling_selftest.js)
 * so pointerdown/pointermove/removal can be driven deterministically without a browser. ---- */

function makeEventTarget() {
    const listeners = Object.create(null);
    return {
        addEventListener: function (type, fn, capture) {
            listeners[type] = listeners[type] || [];
            listeners[type].push({ fn: fn, capture: !!capture });
        },
        removeEventListener: function (type, fn, capture) {
            const arr = listeners[type];
            if (!arr) return;
            for (let i = arr.length - 1; i >= 0; i--) {
                if (arr[i].fn === fn && arr[i].capture === !!capture) arr.splice(i, 1);
            }
        },
        dispatch: function (type, evt) {
            const arr = (listeners[type] || []).slice();
            for (let i = 0; i < arr.length; i++) arr[i].fn(evt);
        },
    };
}

function fakeEl(tag) {
    const attrs = Object.create(null);
    const kids = [];
    let classes = [];
    const node = Object.assign({}, makeEventTarget(), {
        tagName: String(tag || 'div').toUpperCase(),
        clientWidth: 0,
        style: {
            setProperty: function () {},
        },
        classList: {
            add: function (c) {
                if (classes.indexOf(c) === -1) classes.push(c);
            },
            remove: function (c) {
                const i = classes.indexOf(c);
                if (i >= 0) classes.splice(i, 1);
            },
            contains: function (c) {
                return classes.indexOf(c) !== -1;
            },
        },
        parentNode: null,
        children: kids,
        setAttribute: function (k, v) {
            attrs[k] = String(v);
        },
        getAttribute: function (k) {
            return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null;
        },
        appendChild: function (child) {
            const i = kids.indexOf(child);
            if (i >= 0) kids.splice(i, 1);
            kids.push(child);
            child.parentNode = node;
            return child;
        },
        insertBefore: function (child, ref) {
            const existingIdx = kids.indexOf(child);
            if (existingIdx >= 0) kids.splice(existingIdx, 1);
            const idx = kids.indexOf(ref);
            kids.splice(idx >= 0 ? idx : kids.length, 0, child);
            child.parentNode = node;
            return child;
        },
        removeChild: function (child) {
            const i = kids.indexOf(child);
            if (i >= 0) kids.splice(i, 1);
            child.parentNode = null;
            return child;
        },
        querySelector: function (sel) {
            const cls = String(sel || '').replace(/^\./, '');
            for (let i = 0; i < kids.length; i++) {
                if (kids[i].className && kids[i].className.indexOf(cls) !== -1) return kids[i];
                const nested = kids[i].querySelector && kids[i].querySelector(sel);
                if (nested) return nested;
            }
            return null;
        },
        closest: function (sel) {
            const cls = String(sel || '').replace(/^\./, '');
            let cur = node;
            while (cur) {
                if (cur.className && cur.className.indexOf(cls) !== -1) return cur;
                cur = cur.parentNode;
            }
            return null;
        },
        getBoundingClientRect: function () {
            return { width: node.clientWidth || 0, left: 0 };
        },
        setPointerCapture: function () {},
        releasePointerCapture: function () {},
        focus: function () {},
    });
    Object.defineProperty(node, 'nextSibling', {
        get: function () {
            if (!node.parentNode) return null;
            const arr = node.parentNode.children;
            const idx = arr.indexOf(node);
            if (idx === -1 || idx === arr.length - 1) return null;
            return arr[idx + 1];
        },
    });
    /* className is backed by the same `classes` array `classList` mutates, mirroring real DOM
     * behavior: a direct `el.className = '...'` assignment (as createSeparator does) and later
     * `el.classList.add(...)` calls (as drag/keyboard handlers do) must not clobber each other. */
    Object.defineProperty(node, 'className', {
        get: function () {
            return classes.join(' ');
        },
        set: function (v) {
            classes = String(v || '')
                .split(/\s+/)
                .filter(Boolean);
        },
    });
    return node;
}

function runDomRegressions() {
    const splitSrcCode = fs.readFileSync(path.join(rootDir, 'frontend/js/workspace-split.js'), 'utf8');

    const canvas = fakeEl('div');
    canvas.className = 'prks-workspace-canvas prks-workspace-canvas--tiled';
    canvas.clientWidth = 1600;

    const body = fakeEl('body');
    const documentMock = Object.assign({}, makeEventTarget(), {
        createElement: function (tag) {
            return fakeEl(tag);
        },
        getElementById: function () {
            return null;
        },
        body: body,
    });

    let ratioState = 0.58;
    const sandbox = {
        window: {},
        document: documentMock,
        prksWorkspaceGetSplitRatio: function () {
            return ratioState;
        },
        prksWorkspaceSetMainSplitRatio: function (r) {
            ratioState = r;
        },
        prksWorkspaceDefaultSplitRatio: function () {
            return 0.58;
        },
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    vm.runInNewContext(splitSrcCode, sandbox);

    ratioState = 0.68;
    sandbox.prksWorkspaceSyncSplitSeparator(canvas, true, { mainSplitRatio: 0.68 });
    const sep = canvas.querySelector('.prks-splitter');
    assert('dom: separator created', !!sep);

    /* --- 2/3: commit:false must clamp the DOM/ARIA safely but never mutate the canonical ratio. --- */
    canvas.clientWidth = 480; /* 360 + 320 > 479px: impossible split at this width, collapses to a safe midpoint */
    sandbox.prksWorkspaceReapplySplitRatio(canvas, { commit: false });
    assertEq('commit:false preserves canonical ratio during narrow/in-transition width', ratioState, 0.68);
    const nowAfterNoCommit = Number(sep.getAttribute('aria-valuenow'));
    assert(
        'commit:false still clamps the rendered ARIA for the narrow width',
        Math.abs(nowAfterNoCommit - 68) > 1,
        'now=' + nowAfterNoCommit
    );

    /* --- Ordinary wide-layout resizing may commit a reclamp (default commit:true). --- */
    sandbox.prksWorkspaceReapplySplitRatio(canvas, { commit: true });
    assert(
        'commit:true reclamps and commits the canonical ratio',
        Math.abs(ratioState - 0.68) > 0.01,
        'ratio=' + ratioState
    );

    /* --- 7/8: active-drag cleanup must be externally terminable when the separator is removed mid-drag. --- */
    canvas.clientWidth = 1600;
    ratioState = 0.58;
    sandbox.prksWorkspaceSyncSplitSeparator(canvas, true, { mainSplitRatio: 0.58 });

    sep.dispatch('pointerdown', { pointerType: 'mouse', button: 0, pointerId: 7, preventDefault: function () {} });
    assert('drag: is-dragging set on pointerdown', sep.classList.contains('is-dragging'));
    assert('drag: body resizing state set on pointerdown', body.classList.contains('prks-resizing-split'));

    documentMock.dispatch('pointermove', { clientX: 1000, preventDefault: function () {} });
    assert('drag: pointermove committed a ratio change', Math.abs(ratioState - 0.58) > 0.01, 'ratio=' + ratioState);

    /* Controlled test seam: the split disappears mid-drag (narrow fallback / hide split / Secondary closes). */
    sandbox.prksWorkspaceSyncSplitSeparator(canvas, false, {});

    assert('drag: separator removed from the canvas', canvas.querySelector('.prks-splitter') === null);
    assert('drag: is-dragging cleared by removal cleanup', !sep.classList.contains('is-dragging'));
    assert('drag: body resizing state cleared by removal cleanup', !body.classList.contains('prks-resizing-split'));

    const ratioAfterRemoval = ratioState;
    documentMock.dispatch('pointermove', { clientX: 1400, preventDefault: function () {} });
    assertEq('drag: stray pointermove after removal does not mutate ratio', ratioState, ratioAfterRemoval);
}

async function run() {
    /* ---- Pure bounds math ---- */
    const wide = prksSplitComputeBounds(2000);
    assertClose('wide minRatio', wide.minRatio, 360 / 2000);
    assertClose('wide maxRatio', wide.maxRatio, 1 - 320 / 2000);
    assert('wide minRatio < maxRatio', wide.minRatio < wide.maxRatio);

    const narrowFit = prksSplitComputeBounds(680);
    assert('exact-fit min <= max', narrowFit.minRatio <= narrowFit.maxRatio + 1e-9);

    const impossible = prksSplitComputeBounds(400);
    assertEq('impossible collapses minRatio==maxRatio', impossible.minRatio, impossible.maxRatio);
    assert('impossible bounds within [0,1]', impossible.minRatio >= 0 && impossible.minRatio <= 1);

    const zero = prksSplitComputeBounds(0);
    assertEq('zero width minRatio', zero.minRatio, 0);
    assertEq('zero width maxRatio', zero.maxRatio, 1);

    const neg = prksSplitComputeBounds(-100);
    assertEq('negative width minRatio', neg.minRatio, 0);
    assertEq('negative width maxRatio', neg.maxRatio, 1);

    assertEq('clamp within bounds unchanged', prksSplitClampRatio(0.5, wide), 0.5);
    assertEq('clamp below min floors', prksSplitClampRatio(-1, wide), wide.minRatio);
    assertEq('clamp above max ceils', prksSplitClampRatio(2, wide), wide.maxRatio);
    assertEq('clamp NaN floors to min', prksSplitClampRatio(NaN, wide), wide.minRatio);

    /* Several artificial canvas widths: Main/Secondary pixel widths must respect the minimums. */
    [500, 680, 700, 900, 1200, 1600, 2400].forEach(function (usableWidth) {
        const bounds = prksSplitComputeBounds(usableWidth);
        [-1, 0, 0.3, 0.58, 0.7, 1, 2].forEach(function (raw) {
            const ratio = prksSplitClampRatio(raw, bounds);
            const mainPx = usableWidth * ratio;
            const secPx = usableWidth * (1 - ratio);
            const impossibleSplit = 360 + 320 > usableWidth;
            if (!impossibleSplit) {
                assert(
                    'main>=min @' + usableWidth + '/' + raw,
                    mainPx >= 360 - 1e-6,
                    'mainPx=' + mainPx
                );
                assert(
                    'secondary>=min @' + usableWidth + '/' + raw,
                    secPx >= 320 - 1e-6,
                    'secPx=' + secPx
                );
            }
            assert('main non-negative @' + usableWidth + '/' + raw, mainPx >= -1e-6);
            assert('secondary non-negative @' + usableWidth + '/' + raw, secPx >= -1e-6);
            assert('main does not overflow @' + usableWidth + '/' + raw, mainPx <= usableWidth + 1e-6);
        });
    });

    /* Home/End: Home == minRatio, End == maxRatio, for a representative width. */
    const homeEndBounds = prksSplitComputeBounds(1200);
    assertEq('Home reaches minRatio', prksSplitClampRatio(0, homeEndBounds), homeEndBounds.minRatio);
    assertEq('End reaches maxRatio', prksSplitClampRatio(1, homeEndBounds), homeEndBounds.maxRatio);

    /* ---- Fake-DOM regressions: commit option + active-drag cleanup ownership ---- */
    try {
        runDomRegressions();
    } catch (err) {
        record('dom regressions ran without throwing', false, String((err && err.stack) || err));
    }

    /* ---- Canonical ratio-state contract (workspace-tabs.js) ---- */
    const h = makeHarness({ hash: '#/folders' });
    assertEq('default ratio 0.58', h.ws.snapshot().mainSplitRatio, 0.58);

    h.ws.setMainSplitRatio(0.7, { paint: false });
    assertEq('set ratio updates state', h.ws.snapshot().mainSplitRatio, 0.7);

    h.ws.setMainSplitRatio(-3, { paint: false });
    assertEq('set ratio clamps below 0', h.ws.snapshot().mainSplitRatio, 0);
    h.ws.setMainSplitRatio(9, { paint: false });
    assertEq('set ratio clamps above 1', h.ws.snapshot().mainSplitRatio, 1);
    h.ws.setMainSplitRatio(NaN, { paint: false });
    assertEq('set ratio rejects NaN to default', h.ws.snapshot().mainSplitRatio, 0.58);

    h.ws.setMainSplitRatio(0.65, { paint: false });
    await h.ws.navigate('#/works/W1', { target: 'tile' });
    assertEq('opening split preserves ratio', h.ws.snapshot().mainSplitRatio, 0.65);

    await h.ws.setMode('stacked');
    assertEq('hide split preserves ratio', h.ws.snapshot().mainSplitRatio, 0.65);
    await h.ws.setMode('tiled');
    assertEq('show split preserves ratio', h.ws.snapshot().mainSplitRatio, 0.65);

    const secId = h.ws.snapshot().secondaryTree.tabId;
    h.ws.makeMain(secId);
    assertEq('Make Main preserves ratio', h.ws.snapshot().mainSplitRatio, 0.65);

    await h.ws.tileTab(await h.ws
        .openTab('#/people/P9', { activate: false })
        .then(function (t) {
            return t.id;
        }));
    assertEq('split Secondary preserves root ratio', h.ws.snapshot().mainSplitRatio, 0.65);

    await h.ws.closeTab(h.ws.snapshot().secondaryTree.tabId);
    assertEq('close Secondary preserves ratio (session)', h.ws.snapshot().mainSplitRatio, 0.65);

    await h.ws.navigate('#/works/W2', { target: 'tile' });
    assertEq('open new Secondary preserves session ratio', h.ws.snapshot().mainSplitRatio, 0.65);

    h.ws.bootstrap('#/folders');
    assertEq('bootstrap restores default ratio', h.ws.snapshot().mainSplitRatio, 0.58);

    const reset = makeHarness();
    reset.ws.setMainSplitRatio(0.8, { paint: false });
    reset.ws.resetMainSplitRatio({ paint: false });
    assertEq('resetMainSplitRatio restores default', reset.ws.snapshot().mainSplitRatio, 0.58);

    /* Canonical ratio API lives in workspace-tabs.js; localStorage stays in
     * workspace-persistence.js only. */
    const src = require('fs').readFileSync(path.join(rootDir, 'frontend/js/workspace-tabs.js'), 'utf8');
    assert('workspace-tabs.js still has no localStorage', src.indexOf('localStorage') === -1);
    const splitSrc = require('fs').readFileSync(path.join(rootDir, 'frontend/js/workspace-split.js'), 'utf8');
    assert('workspace-split.js has no localStorage', splitSrc.indexOf('localStorage') === -1);
    assert('workspace-split.js has no sessionStorage', splitSrc.indexOf('sessionStorage') === -1);
    assert('workspace-split.js has no indexedDB', splitSrc.indexOf('indexedDB') === -1);

    if (failed) {
        console.log(failed + ' failed, ' + passed + ' passed');
        process.exit(1);
    }
    console.log('All ' + passed + ' workspace split ratio checks passed, 0 failed');
}

run().catch(function (err) {
    console.error(err);
    process.exit(1);
});
