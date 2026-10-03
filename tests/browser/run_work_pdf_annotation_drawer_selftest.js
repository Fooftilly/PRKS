#!/usr/bin/env node
'use strict';

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
globalThis.prksOwnerResource = require(path.join(rootDir, 'frontend/js/owner-resource.js'));
const tabContext = require(path.join(rootDir, 'frontend/js/tab-context.js'));
const pdfRuntime = require(path.join(rootDir, 'frontend/js/pdf-work-runtime.js'));

const {
    prksMountTabContext,
    prksDestroyAllTabContexts,
    prksWarmParkTabContext,
    prksResumeWarmTabContext,
} = tabContext;
const {
    createWorkPdfRuntime,
    prksPdfAnnotationDrawerZoomAction,
    prksApplyPdfAnnotationDrawerChrome,
    PRKS_PDF_DRAWER_PIN_MIN_PANE,
} = pdfRuntime;

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (detail ? ' ' + detail : ''));
}

function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : detail || '');
}

function assertEq(name, got, want) {
    const ok = got === want;
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function viewerStub(id) {
    return {
        id: id,
        zoomCalls: 0,
        pageCalls: [],
        resizes: 0,
        destroyed: false,
        drawerOpen: null,
        zoomIn: function () { this.zoomCalls += 1; },
        goToPage: function (page) { this.pageCalls.push(page); },
        resize: function () { this.resizes += 1; },
        destroy: function () { this.destroyed = true; },
        setAnnotationDrawerOpen: function (open) { this.drawerOpen = !!open; },
    };
}

function mount(tabId) {
    const host = {
        ownerDocument: null,
        appendChild: function () {},
        querySelector: function () { return null; },
        querySelectorAll: function () { return []; },
    };
    return prksMountTabContext(tabId, host);
}

function testOpenAndCloseDoNotTouchTheViewer() {
    const runtime = createWorkPdfRuntime({ workId: 'work-a' });
    const viewer = viewerStub('v1');
    runtime.viewer = viewer;
    runtime.viewerSetupToken = 4;
    runtime.pageSession.pageNumber = 3;
    runtime.annotationCache = {
        items: [{ id: 'ann-a', custom: { metadataLabels: ['Topic'] } }],
        listPublished: true,
    };
    globalThis.prksProjectPdfAnnotationDrawerItem = function (item, index) {
        return {
            id: String(item.id),
            index: index,
            text: 'Highlight',
            comment: '',
            pageLabel: 'Page 2',
            pageIndex: 1,
            wikiLink: '[[pdf:' + item.id + '|Highlight]]',
            metadataLabels: item.custom && item.custom.metadataLabels ? item.custom.metadataLabels.slice() : [],
        };
    };
    const closed = runtime.readAnnotationDrawer();
    assertEq('closed items', closed.items.length, 0);
    assertEq('closed published', closed.published, false);
    assert('open', runtime.openAnnotationDrawer());
    const read = runtime.readAnnotationDrawer();
    assertEq('open flag', read.open, true);
    assertEq('epoch', read.epoch, 1);
    assertEq('token', read.viewerToken, 4);
    assertEq('status', read.status, '1 annotation');
    assertEq('published', read.published, true);
    assertEq('row id', read.items[0] && read.items[0].id, 'ann-a');
    assertEq('wiki', read.items[0] && read.items[0].wikiLink, '[[pdf:ann-a|Highlight]]');
    assertEq('metadata', read.items[0] && read.items[0].metadataLabels[0], 'Topic');
    assertEq('no selection', read.selectedId, '');
    assert('edit opens', runtime.openAnnotationPopup({ annId: 'ann-a' }));
    assertEq('edit selects', runtime.readAnnotationDrawer().selectedId, 'ann-a');
    assert('edit closes', runtime.closeAnnotationPopup('ann-a'));
    assertEq('selection cleared', runtime.readAnnotationDrawer().selectedId, '');
    assert('reopen keeps epoch', runtime.openAnnotationDrawer());
    assertEq('epoch held', runtime.readAnnotationDrawer().epoch, 1);
    assertEq('same viewer', runtime.viewer, viewer);
    assertEq('token held', runtime.viewerSetupToken, 4);
    assertEq('page held', runtime.pageSession.pageNumber, 3);
    assertEq('no zoom', viewer.zoomCalls, 0);
    assertEq('no page jump', viewer.pageCalls.length, 0);
    assertEq('no resize', viewer.resizes, 0);
    assertEq('viewer not destroyed', viewer.destroyed, false);
    assertEq('open does not set chrome', viewer.drawerOpen, null);
    assert('close', runtime.closeAnnotationDrawer());
    assertEq('closed', runtime.readAnnotationDrawer().open, false);
    assertEq('closed epoch', runtime.readAnnotationDrawer().epoch, 2);
    assertEq('viewer after close', runtime.viewer, viewer);
    assertEq('token after close', runtime.viewerSetupToken, 4);
    runtime.destroy();
    assert('destroyed refuses open', runtime.openAnnotationDrawer() === false);
    delete globalThis.prksProjectPdfAnnotationDrawerItem;
}

function testStaleEpochAndReplacedViewer() {
    const runtime = createWorkPdfRuntime({ workId: 'work-a' });
    const viewer = viewerStub('v1');
    runtime.viewer = viewer;
    runtime.viewerSetupToken = 2;
    runtime.annotationCache = {
        items: [{ id: 'ann-a' }],
        listPublished: true,
    };
    assertEq('unpublished fallback id', runtime.readAnnotationDrawer().items.length, 0);
    runtime.openAnnotationDrawer();
    const ticket = {
        epoch: runtime.readAnnotationDrawer().epoch,
        viewerToken: runtime.viewerSetupToken,
        viewer: viewer,
    };
    assert('current drawer matches', runtime.annotationDrawerStill(ticket) === true);
    const row = runtime.readAnnotationDrawer().items[0];
    assertEq('fallback wiki', row && row.wikiLink, '[[pdf:ann-a]]');
    assertEq('fallback metadata', row && row.metadataLabels.length, 0);
    runtime.closeAnnotationDrawer();
    assert('closed ticket fails', runtime.annotationDrawerStill(ticket) === false);
    runtime.openAnnotationDrawer();
    assert('reopened epoch fails', runtime.annotationDrawerStill(ticket) === false);
    const next = {
        epoch: runtime.readAnnotationDrawer().epoch,
        viewerToken: runtime.viewerSetupToken,
        viewer: viewer,
    };
    assert('new epoch matches', runtime.annotationDrawerStill(next) === true);
    runtime.viewer = viewerStub('v2');
    runtime.viewerSetupToken = 3;
    assert('replaced viewer fails', runtime.annotationDrawerStill(next) === false);
    runtime.destroy();
}

function testMainAndSecondarySessionsStayApart() {
    prksDestroyAllTabContexts();
    const main = mount('main');
    const side = mount('side');
    main.beginRoute({ name: 'work' });
    side.beginRoute({ name: 'work' });
    const mainPdf = createWorkPdfRuntime({ workId: 'work-a' });
    const sidePdf = createWorkPdfRuntime({ workId: 'work-b' });
    mainPdf.viewer = viewerStub('main');
    sidePdf.viewer = viewerStub('side');
    mainPdf.viewerSetupToken = 1;
    sidePdf.viewerSetupToken = 1;
    mainPdf.annotationCache = { items: [{ id: 'A' }], listPublished: true };
    sidePdf.annotationCache = { items: [{ id: 'B' }], listPublished: true };
    main.setResource('pdf', mainPdf, function () { mainPdf.destroy(); });
    side.setResource('pdf', sidePdf, function () { sidePdf.destroy(); });
    mainPdf.openAnnotationDrawer();
    sidePdf.openAnnotationDrawer();
    const sideEpoch = sidePdf.readAnnotationDrawer().epoch;
    assert('close main', mainPdf.closeAnnotationDrawer());
    assertEq('side still open', sidePdf.readAnnotationDrawer().open, true);
    assertEq('side row', sidePdf.readAnnotationDrawer().items[0].id, 'B');
    assertEq('side epoch', sidePdf.readAnnotationDrawer().epoch, sideEpoch);
    assertEq('side viewer', sidePdf.viewer.id, 'side');
    assertEq('side token', sidePdf.viewerSetupToken, 1);
    mainPdf.openAnnotationDrawer();
    assertEq('main reopened', mainPdf.readAnnotationDrawer().open, true);
    main.beginRoute({ name: 'work', hash: '#/works/other' });
    assert('route replacement destroys main runtime', mainPdf._destroyed);
    assertEq('route replacement closes main drawer', mainPdf.readAnnotationDrawer().open, false);
    assertEq('side survives main route replacement', sidePdf.readAnnotationDrawer().items[0].id, 'B');
    sidePdf.destroy();
    assertEq('side closed on destroy', sidePdf.readAnnotationDrawer().open, false);
    prksDestroyAllTabContexts();
}

function memoryStorage() {
    const data = {};
    return {
        getItem: function (key) {
            return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null;
        },
        setItem: function (key, value) {
            data[key] = String(value);
        },
    };
}

function wideFrame() {
    return { paneWidth: 1200, mobile: false };
}

function hostBox() {
    const kids = [];
    return {
        children: kids,
        appendChild: function (child) {
            if (child.parentNode && typeof child.parentNode.removeChild === 'function') {
                try { child.parentNode.removeChild(child); } catch (_e) {}
            }
            kids.push(child);
            child.parentNode = this;
            return child;
        },
        removeChild: function (child) {
            const index = kids.indexOf(child);
            if (index >= 0) kids.splice(index, 1);
            child.parentNode = null;
            return child;
        },
    };
}

function paneStub() {
    const style = {
        props: {},
        setProperty: function (key, value) { this.props[key] = value; },
        getPropertyValue: function (key) { return this.props[key] || ''; },
        removeProperty: function (key) { delete this.props[key]; },
    };
    return { dataset: {}, style: style };
}

function testPinResizePreservesViewerIdentity() {
    const storage = memoryStorage();
    const runtime = createWorkPdfRuntime({ workId: 'work-a', drawerStorage: storage });
    const viewer = viewerStub('v1');
    viewer.fitWidthCalls = 0;
    viewer.fitPageCalls = 0;
    viewer.fitWidth = function () { this.fitWidthCalls += 1; };
    viewer.fitPage = function () { this.fitPageCalls += 1; };
    viewer.getZoomLayout = function () { return { kind: 'percent', percent: 125 }; };
    runtime.viewer = viewer;
    runtime.viewerSetupToken = 7;
    runtime.pageSession.pageNumber = 4;
    runtime.annotationCache = { items: [{ id: 'ann-a' }], listPublished: true };
    runtime.syncState = { pendingChanges: true, inFlight: false, lastError: '' };
    runtime.annotationMutationDurable = true;
    runtime.openAnnotationDrawer();
    runtime.openAnnotationPopup({ annId: 'ann-a' });
    runtime.noteAnnotationDrawerFrame(wideFrame());
    const before = runtime.readAnnotationDrawer();
    assertEq('wide overlay before pin', before.placement, 'overlay');
    assertEq('pin available', before.pinEnabled, true);
    assertEq('selected before pin', before.selectedId, 'ann-a');
    assert('pin', runtime.setAnnotationDrawerPinned(true));
    const pinned = runtime.annotationDrawerLayoutEffect();
    assertEq('pin places the drawer in layout', pinned.read.placement, 'pinned');
    assert('pin resizes the viewer box', pinned.resized === true);
    runtime.resize();
    const pane = paneStub();
    assert('chrome marks the pane pinned', prksApplyPdfAnnotationDrawerChrome(pane, pinned.read) === true);
    assertEq('pane placement', pane.dataset.prksAnnotationDrawer, 'pinned');
    assertEq('remembered width variable', pane.style.getPropertyValue('--pdf-annotation-drawer-width'), '352px');
    assert('resize width', runtime.setAnnotationDrawerWidth(410, { persist: false }));
    const resized = runtime.annotationDrawerLayoutEffect();
    assert('width change resizes while pinned', resized.resized === true);
    assertEq('clamped width', resized.read.width, 410);
    runtime.resize();
    assert('commit width', runtime.setAnnotationDrawerWidth(10, { persist: true }));
    assertEq('width floor', runtime.readAnnotationDrawer().width, 240);
    assert('unpin', runtime.setAnnotationDrawerPinned(false));
    const unpinned = runtime.annotationDrawerLayoutEffect();
    assertEq('unpin returns to overlay', unpinned.read.placement, 'overlay');
    assert('unpin resizes back', unpinned.resized === true);
    runtime.resize();
    assert('pin again', runtime.setAnnotationDrawerPinned(true));
    assert('second pin resizes', runtime.annotationDrawerLayoutEffect().resized === true);
    runtime.resize();
    assertEq('same viewer', runtime.viewer, viewer);
    assertEq('token held', runtime.viewerSetupToken, 7);
    assertEq('page held', runtime.pageSession.pageNumber, 4);
    assertEq('selection held', runtime.readAnnotationDrawer().selectedId, 'ann-a');
    assertEq('annotation kept', runtime.annotationCache.items[0].id, 'ann-a');
    assertEq('durable flag held', runtime.annotationMutationDurable, true);
    assertEq('pending sync held', runtime.syncState.pendingChanges, true);
    assertEq('no fit width', viewer.fitWidthCalls, 0);
    assertEq('no fit page', viewer.fitPageCalls, 0);
    assertEq('no zoom in', viewer.zoomCalls, 0);
    assertEq('no page jump', viewer.pageCalls.length, 0);
    assertEq('viewer not destroyed', viewer.destroyed, false);
    assert('viewer resized with the box', viewer.resizes > 0);
    assertEq('percent is preserved', prksPdfAnnotationDrawerZoomAction(viewer.getZoomLayout()), 'preserve');
    assertEq('fit width recomputes from the box', prksPdfAnnotationDrawerZoomAction({ kind: 'fit-width' }), 'recompute');
    assertEq('fit page recomputes from the box', prksPdfAnnotationDrawerZoomAction({ kind: 'fit-page' }), 'recompute');
    const remembered = createWorkPdfRuntime({ workId: 'work-a', drawerStorage: storage });
    assertEq('next runtime remembers pin', remembered.readAnnotationDrawer().pinned, true);
    assertEq('next runtime remembers width', remembered.readAnnotationDrawer().width, 240);
    remembered.destroy();
    runtime.destroy();
}

function testNarrowAndMobileDoNotPinTheViewer() {
    const runtime = createWorkPdfRuntime({ workId: 'work-a' });
    const viewer = viewerStub('v1');
    runtime.viewer = viewer;
    runtime.viewerSetupToken = 2;
    runtime.openAnnotationDrawer();
    runtime.setAnnotationDrawerPinned(true);
    runtime.noteAnnotationDrawerFrame({ paneWidth: PRKS_PDF_DRAWER_PIN_MIN_PANE - 1, mobile: false });
    const narrow = runtime.annotationDrawerLayoutEffect();
    assertEq('narrow stays overlay', narrow.read.placement, 'overlay');
    assertEq('narrow pin disabled', narrow.read.pinEnabled, false);
    assert('narrow does not resize', narrow.resized === false);
    runtime.noteAnnotationDrawerFrame({ paneWidth: 1200, mobile: true });
    const sheet = runtime.annotationDrawerLayoutEffect();
    assertEq('mobile is a sheet', sheet.read.placement, 'sheet');
    assert('sheet does not resize from overlay', sheet.resized === false);
    runtime.noteAnnotationDrawerFrame({ paneWidth: 1200, mobile: false });
    runtime.annotationDrawerLayoutEffect();
    runtime.noteAnnotationDrawerFrame({ paneWidth: 1200, mobile: true });
    const leavingPin = runtime.annotationDrawerLayoutEffect();
    assertEq('mobile leaves pin', leavingPin.read.placement, 'sheet');
    assert('leaving pin resizes the viewer', leavingPin.resized === true);
    const pane = paneStub();
    prksApplyPdfAnnotationDrawerChrome(pane, sheet.read);
    assertEq('sheet attribute', pane.dataset.prksAnnotationDrawer, 'sheet');
    assertEq('same viewer', runtime.viewer, viewer);
    assertEq('token held', runtime.viewerSetupToken, 2);
    assertEq('no resize calls', viewer.resizes, 0);
    runtime.setAnnotationDrawerWidth(480, { persist: true });
    runtime.noteAnnotationDrawerFrame({ paneWidth: 700, mobile: false });
    const capped = runtime.annotationDrawerLayoutEffect();
    assertEq('wide preference still pins', capped.read.placement, 'pinned');
    assertEq('preference stays 480', capped.read.width, 480);
    assertEq('painted width leaves the viewer its minimum', capped.read.layoutWidth, 380);
    assertEq('interaction max is the pane cap', capped.read.interactionMax, 380);
    const cappedPane = paneStub();
    prksApplyPdfAnnotationDrawerChrome(cappedPane, capped.read);
    assertEq('capped width variable', cappedPane.style.getPropertyValue('--pdf-annotation-drawer-width'), '380px');
    runtime.noteAnnotationDrawerFrame({ paneWidth: 1100, mobile: false });
    const grew = runtime.annotationDrawerLayoutEffect();
    assert('same drawer width still resizes when the pane grows', grew.resized === true);
    assertEq('preference held while the pane grows', grew.read.width, 480);
    assertEq('wider pane paints the full preference', grew.read.layoutWidth, 480);
    assertEq('wider pane restores the global interaction max', grew.read.interactionMax, 480);
    runtime.destroy();
}

function testPanesStayIsolatedAcrossParkAndRouteReplacement() {
    prksDestroyAllTabContexts();
    const storage = memoryStorage();
    const mainHost = hostBox();
    const sideHost = hostBox();
    const main = prksMountTabContext('main', mainHost);
    const side = prksMountTabContext('side', sideHost);
    main.beginRoute({ name: 'work' });
    side.beginRoute({ name: 'work' });
    const mainPdf = createWorkPdfRuntime({ workId: 'work-a', drawerStorage: storage });
    const sidePdf = createWorkPdfRuntime({ workId: 'work-b', drawerStorage: storage });
    const mainViewer = viewerStub('main');
    const sideViewer = viewerStub('side');
    mainPdf.viewer = mainViewer;
    sidePdf.viewer = sideViewer;
    mainPdf.viewerSetupToken = 3;
    sidePdf.viewerSetupToken = 5;
    main.setResource('pdf', mainPdf, function () { mainPdf.destroy(); });
    side.setResource('pdf', sidePdf, function () { sidePdf.destroy(); });
    mainPdf.openAnnotationDrawer();
    sidePdf.openAnnotationDrawer();
    mainPdf.noteAnnotationDrawerFrame(wideFrame());
    sidePdf.noteAnnotationDrawerFrame(wideFrame());
    mainPdf.setAnnotationDrawerPinned(true);
    mainPdf.setAnnotationDrawerWidth(400, { persist: true });
    assert('main pin resizes', mainPdf.annotationDrawerLayoutEffect().resized === true);
    assertEq('side stays overlay', sidePdf.readAnnotationDrawer().placement, 'overlay');
    assertEq('side width stays default', sidePdf.readAnnotationDrawer().width, 352);
    assertEq('side viewer', sidePdf.viewer, sideViewer);
    assertEq('side token', sidePdf.viewerSetupToken, 5);
    const parking = hostBox();
    assert('warm park', prksWarmParkTabContext('main', parking) === true);
    assertEq('park keeps the viewer', mainPdf.viewer, mainViewer);
    assertEq('park keeps pin', mainPdf.readAnnotationDrawer().placement, 'pinned');
    assert('viewer alive while parked', mainViewer.destroyed === false);
    const resizesBeforeResume = mainViewer.resizes;
    const visible = hostBox();
    const resumed = prksResumeWarmTabContext('main', visible);
    assert('resume returns main', resumed === main);
    assertEq('resume keeps the viewer', mainPdf.viewer, mainViewer);
    assertEq('resume keeps the token', mainPdf.viewerSetupToken, 3);
    assert('resume resizes without a new viewer', mainViewer.resizes === resizesBeforeResume + 1);
    assertEq('side untouched by resume', sidePdf.viewer, sideViewer);
    main.beginRoute({ name: 'work', hash: '#/works/other' });
    assert('route replacement destroys main', mainPdf._destroyed);
    assertEq('route replacement closes main', mainPdf.readAnnotationDrawer().open, false);
    assertEq('side survives', sidePdf.readAnnotationDrawer().open, true);
    assertEq('side viewer survives', sidePdf.viewer, sideViewer);
    assertEq('side token survives', sidePdf.viewerSetupToken, 5);
    sidePdf.destroy();
    prksDestroyAllTabContexts();
}

testOpenAndCloseDoNotTouchTheViewer();
testStaleEpochAndReplacedViewer();
testMainAndSecondarySessionsStayApart();
testPinResizePreservesViewerIdentity();
testNarrowAndMobileDoNotPinTheViewer();
testPanesStayIsolatedAcrossParkAndRouteReplacement();

console.log(passed + ' passed, ' + failed + ' failed');
if (failed) process.exit(1);
