#!/usr/bin/env node
'use strict';

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const tabContext = require(path.join(rootDir, 'frontend/js/tab-context.js'));
const pdfRuntime = require(path.join(rootDir, 'frontend/js/pdf-work-runtime.js'));

const { prksMountTabContext, prksDestroyAllTabContexts } = tabContext;
const { createWorkPdfRuntime } = pdfRuntime;

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

testOpenAndCloseDoNotTouchTheViewer();
testStaleEpochAndReplacedViewer();
testMainAndSecondarySessionsStayApart();

console.log(passed + ' passed, ' + failed + ' failed');
if (failed) process.exit(1);
