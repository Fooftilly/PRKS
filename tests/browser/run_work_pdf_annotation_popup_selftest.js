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
        updates: [],
        destroyed: false,
        updateAnnotation: function (annId) { this.updates.push(annId); },
        destroy: function () { this.destroyed = true; },
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

function testOpenAndCloseDoNotReplaceTheViewer() {
    const runtime = createWorkPdfRuntime({ workId: 'work-a' });
    const viewer = viewerStub('v1');
    const cache = runtime.annotationCache;
    runtime.viewer = viewer;
    runtime.viewerSetupToken = 4;
    assert('open', runtime.openAnnotationPopup({
        annId: 'ann-a',
        comment: 'hello',
        pageIndex: 2,
        meta: 'Page 3 · Highlight',
        generation: 1,
    }));
    const read = runtime.readAnnotationPopup();
    assertEq('open flag', read.open, true);
    assertEq('comment', read.comment, 'hello');
    assertEq('page', read.pageIndex, 2);
    assertEq('epoch', read.epoch, 1);
    assertEq('editor id', runtime.annotationEditorState.annId, 'ann-a');
    assertEq('same viewer', runtime.viewer, viewer);
    assertEq('token', runtime.viewerSetupToken, 4);
    assertEq('cache', runtime.annotationCache, cache);
    assert('same annotation keeps epoch', runtime.openAnnotationPopup({
        annId: 'ann-a',
        comment: 'replaced',
        pageIndex: 2,
    }));
    assertEq('epoch unchanged', runtime.readAnnotationPopup().epoch, 1);
    assertEq('baseline comment kept', runtime.readAnnotationPopup().comment, 'hello');
    assert('close', runtime.closeAnnotationPopup('ann-a'));
    assertEq('closed', runtime.readAnnotationPopup().open, false);
    assertEq('editor cleared', runtime.annotationEditorState, null);
    assertEq('viewer after close', runtime.viewer, viewer);
    assertEq('token after close', runtime.viewerSetupToken, 4);
    assertEq('viewer not destroyed', viewer.destroyed, false);
    runtime.destroy();
    assert('destroyed refuses open', runtime.openAnnotationPopup({ annId: 'later', comment: '' }) === false);
}

function testStaleCompletionDoesNotMatchPopupB() {
    const runtime = createWorkPdfRuntime({ workId: 'work-a' });
    const viewer = viewerStub('v1');
    runtime.viewer = viewer;
    runtime.viewerSetupToken = 2;
    runtime.openAnnotationPopup({ annId: 'A', comment: 'from A', pageIndex: 0, generation: 3 });
    const ticketA = runtime.captureAnnotationPopupTicket({ annId: 'A', epoch: 1, generation: 3 });
    assert('captured A', !!ticketA);
    runtime.openAnnotationPopup({ annId: 'B', comment: 'from B', pageIndex: 1, generation: 3 });
    assert('A no longer paints', runtime.annotationPopupStill(ticketA) === false);
    assert('A may still write on this viewer', runtime.annotationPopupWriteStill(ticketA) === true);
    assert('note does not land on B', runtime.noteAnnotationPopupComment(ticketA, 'stale') === false);
    assertEq('B comment', runtime.readAnnotationPopup().comment, 'from B');
    assertEq('B id', runtime.readAnnotationPopup().annId, 'B');
    const ticketB = runtime.captureAnnotationPopupTicket({
        annId: 'B',
        epoch: runtime.readAnnotationPopup().epoch,
        generation: 3,
    });
    assert('B paints', runtime.annotationPopupStill(ticketB) === true);
    assert('stale epoch rejected', runtime.captureAnnotationPopupTicket({
        annId: 'B',
        epoch: ticketA.epoch,
        generation: 3,
    }) === null);
    runtime.viewer = viewerStub('v2');
    runtime.viewerSetupToken = 3;
    assert('replaced viewer rejects the write', runtime.annotationPopupWriteStill(ticketA) === false);
    assert('replaced viewer rejects B paint', runtime.annotationPopupStill(ticketB) === false);
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
    main.setResource('pdf', mainPdf, function () { mainPdf.destroy(); });
    side.setResource('pdf', sidePdf, function () { sidePdf.destroy(); });
    mainPdf.openAnnotationPopup({ annId: 'A', comment: 'main', generation: main.generation });
    sidePdf.openAnnotationPopup({ annId: 'B', comment: 'side', generation: side.generation });
    const sideEpoch = sidePdf.readAnnotationPopup().epoch;
    assert('close main', mainPdf.closeAnnotationPopup('A'));
    assertEq('side still open', sidePdf.readAnnotationPopup().open, true);
    assertEq('side id', sidePdf.readAnnotationPopup().annId, 'B');
    assertEq('side comment', sidePdf.readAnnotationPopup().comment, 'side');
    assertEq('side epoch', sidePdf.readAnnotationPopup().epoch, sideEpoch);
    assertEq('side viewer', sidePdf.viewer.id, 'side');
    mainPdf.openAnnotationPopup({ annId: 'A', comment: 'main again', generation: main.generation });
    assertEq('main reopened', mainPdf.readAnnotationPopup().open, true);
    main.beginRoute({ name: 'work', hash: '#/works/other' });
    assert('route replacement destroys main runtime', mainPdf._destroyed);
    assertEq('route replacement closes main popup', mainPdf.readAnnotationPopup().open, false);
    assertEq('side survives main route replacement', sidePdf.readAnnotationPopup().annId, 'B');
    sidePdf.destroy();
    assertEq('side closed on destroy', sidePdf.readAnnotationPopup().open, false);
    prksDestroyAllTabContexts();
}

function testCloseOtherIdDoesNotClearTheOpenPopup() {
    const runtime = createWorkPdfRuntime({ workId: 'work-a' });
    runtime.viewer = viewerStub('v1');
    runtime.openAnnotationPopup({ annId: 'B', comment: 'keep', generation: 1 });
    const epoch = runtime.readAnnotationPopup().epoch;
    assert('foreign close', runtime.closeAnnotationPopup('A') === false);
    assertEq('still B', runtime.readAnnotationPopup().annId, 'B');
    assertEq('epoch held', runtime.readAnnotationPopup().epoch, epoch);
    runtime.destroy();
}

testOpenAndCloseDoNotReplaceTheViewer();
testStaleCompletionDoesNotMatchPopupB();
testMainAndSecondarySessionsStayApart();
testCloseOtherIdDoesNotClearTheOpenPopup();

console.log(passed + ' passed, ' + failed + ' failed');
if (failed) process.exit(1);
