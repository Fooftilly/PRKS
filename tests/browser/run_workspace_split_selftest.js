#!/usr/bin/env node
'use strict';

/* Deterministic ratio-state + bounds coverage for the Main/Secondary divider.
 * No DOM/browser needed: pure bounds math (workspace-split.js) and the
 * canonical ratio state machine (workspace-tabs.js) are both plain JS.
 */

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const nav = require(path.join(rootDir, 'frontend/js/navigation.js'));
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
    assertEq('replace Secondary preserves ratio', h.ws.snapshot().mainSplitRatio, 0.65);

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

    /* No persistence: canonical ratio API must not touch storage. */
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
