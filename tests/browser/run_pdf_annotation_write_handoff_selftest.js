#!/usr/bin/env node
'use strict';

/**
 * A durable annotation write must not wait on the materialization handoff.
 *
 * The handoff marks a claimed materialization that has not yet taken its
 * gate. Its pass drains `_annotationDurableWriteChain` before taking the
 * gate, so a write on that chain that waited for the handoff to clear would
 * wait on the pass while the pass waited on it, until the 30 s deadline.
 * A comment saved while one materialization runs queues exactly such a write.
 *
 * A user edit has the opposite race: its wait ends when one pass releases
 * the gate, and the next pass (an earlier save's acknowledgement) can take
 * it again before the edit resumes. That edit must wait again, not be
 * refused as though annotation editing were unavailable.
 *
 * Drives the real wait helpers from works-pdf.js.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const strict = require('assert/strict');

let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); }, {
    get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); },
});

const rootDir = path.resolve(__dirname, '../..');
global.window = global;

let pdfSource = fs.readFileSync(path.join(rootDir, 'frontend/js/components/works-pdf.js'), 'utf8');
pdfSource = pdfSource.replace(
    "import { prksPdfCacheInstallOutcome, prksPostPdfCacheInstall } from '/js/pdf-cache-install.js';\nimport { createPrksPdfViewer } from '/js/pdf-viewer-runtime.js';\n",
    'const prksPdfCacheInstallOutcome = null;\nconst prksPostPdfCacheInstall = null;\nconst createPrksPdfViewer = null;\n'
);
pdfSource = pdfSource.replace('export function initPdfViewerForWork', 'function initPdfViewerForWork');
const scriptDir = fs.mkdtempSync(path.join(os.tmpdir(), 'prks-pdf-write-handoff-'));
const scriptFile = path.join(scriptDir, 'works-pdf.cjs');
fs.writeFileSync(scriptFile, pdfSource + '\nmodule.exports = { prksWaitOutAnnotationMaterialization, prksBeginAnnotationMaterializationGate, prksEndAnnotationMaterializationGate, prksAnnotationMaterializationReclaimed, prksAnnotationMutationWaitDeadline };\n');
const {
    prksWaitOutAnnotationMaterialization: waitOut,
    prksBeginAnnotationMaterializationGate: beginGate,
    prksEndAnnotationMaterializationGate: endGate,
    prksAnnotationMaterializationReclaimed: reclaimed,
    prksAnnotationMutationWaitDeadline: waitDeadline,
} = require(scriptFile);

// The user-edit wait in works-pdf.js (comment save, deletes). `between`
// stands for another task that runs after a wait returns and before the
// edit resumes: here, the next materialization pass taking the gate.
async function waitForUserEdit(pdf, until, between) {
    do {
        const waited = waitOut(pdf, { deadline: until });
        if (between) waited.then(between);
        await waited;
    } while (reclaimed(pdf, until));
}

// The next pass takes the gate once, between a wait and the edit, and holds it `ms`.
function nextPassBetween(pdf, ms) {
    let taken = false;
    return function () {
        if (taken || pdf._annotationMaterializing) return;
        taken = true;
        assert.equal(beginGate(pdf), true);
        setTimeout(() => endGate(pdf), ms);
    };
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function elapsed(fn) {
    const started = Date.now();
    await fn();
    return Date.now() - started;
}

async function main() {
    // Default callers (user mutation, catch-up) still wait out the handoff.
    const held = { _annotationMaterializationHandoff: true };
    setTimeout(() => { held._annotationMaterializationHandoff = false; }, 150);
    const heldFor = await elapsed(() => waitOut(held));
    assert.ok(heldFor >= 140, `default wait must hold for the handoff, returned after ${heldFor}ms`);

    // A durable write does not: the handoff stays raised and it returns at once.
    const handoff = { _annotationMaterializationHandoff: true };
    const writeFor = await elapsed(() => waitOut(handoff, { handoff: false }));
    assert.ok(writeFor < 100, `a write must not wait on the handoff, waited ${writeFor}ms`);
    assert.equal(handoff._annotationMaterializationHandoff, true);

    // It still waits out a gate that is actually held.
    const gated = { _annotationMaterializationHandoff: true };
    assert.equal(beginGate(gated), true);
    setTimeout(() => endGate(gated), 150);
    const gateFor = await elapsed(() => waitOut(gated, { handoff: false }));
    assert.ok(gateFor >= 140, `a write must wait out a held gate, returned after ${gateFor}ms`);
    assert.equal(gated._annotationMaterializing, false);

    // The deadlock itself: a pass that drains the write chain before taking
    // its gate, and a write queued behind the handoff on that chain.
    const runtime = { _annotationMaterializationHandoff: true, _annotationDurableWriteChain: null };
    const order = [];
    runtime._annotationDurableWriteChain = (async () => {
        await waitOut(runtime, { handoff: false });
        order.push('write');
    })();
    const pass = (async () => {
        await runtime._annotationDurableWriteChain;
        assert.equal(beginGate(runtime), true);
        runtime._annotationMaterializationHandoff = false;
        order.push('materialize');
        endGate(runtime);
    })();
    const settled = await Promise.race([pass.then(() => 'done'), sleep(1000).then(() => 'stuck')]);
    assert.equal(settled, 'done');
    assert.deepEqual(order, ['write', 'materialize']);

    // A single wait can resume inside the next pass: that was the refused
    // back-to-back comment save.
    const single = {};
    assert.equal(beginGate(single), true);
    setTimeout(() => endGate(single), 60);
    const once = waitOut(single);
    once.then(nextPassBetween(single, 120));
    await once;
    assert.equal(single._annotationMaterializing, true, 'the next pass took the gate before the edit resumed');
    await waitOut(single);

    // The user-edit loop waits that pass out too.
    const looped = {};
    assert.equal(beginGate(looped), true);
    setTimeout(() => endGate(looped), 60);
    const loopedFor = await elapsed(() =>
        waitForUserEdit(looped, waitDeadline(), nextPassBetween(looped, 120)));
    assert.equal(looped._annotationMaterializing, false, 'the edit runs only once no pass holds the gate');
    assert.ok(loopedFor >= 170, `the edit must wait out both passes, returned after ${loopedFor}ms`);

    // The loop is still bounded: past its deadline, or once the runtime is
    // destroyed, the caller's own capability checks decide.
    const stuck = {};
    assert.equal(beginGate(stuck), true);
    const stuckFor = await elapsed(() => waitForUserEdit(stuck, Date.now() + 80));
    assert.ok(stuckFor < 500, `the edit wait must end at its deadline, took ${stuckFor}ms`);
    assert.equal(reclaimed(stuck, Date.now() + 1000), true);
    stuck._destroyed = true;
    assert.equal(reclaimed(stuck, Date.now() + 1000), false);
    endGate(stuck);

    console.log(`PDF annotation write/handoff selftest: ${checks} checks passed`);
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
