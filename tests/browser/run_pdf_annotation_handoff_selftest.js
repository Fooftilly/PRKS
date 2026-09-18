#!/usr/bin/env node
'use strict';

/**
 * Deterministic regression for catch-up → materialization handoff:
 * user mutation must stay impossible across the independent-gate transition,
 * including while capability resolve / materialization acquisition is paused.
 */

const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); }, {
    get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); },
});

function makeViewer() {
    let enabled = true;
    const log = [];
    return {
        log,
        setMutationEnabled(v) {
            enabled = !!v;
            log.push(['setMutationEnabled', enabled]);
        },
        isUserMutationEnabled() {
            return enabled;
        },
        activateMarkupTool(tool) {
            if (!enabled) return { ok: false, reason: 'mutation_disabled' };
            log.push(['activateMarkupTool', tool]);
            return { ok: true };
        },
        createAnnotation(payload) {
            if (!enabled) return { ok: false, reason: 'mutation_disabled' };
            log.push(['createAnnotation', payload]);
            return { ok: true, id: 'ann-1' };
        },
    };
}

function beginGate(runtime) {
    if (runtime._annotationMaterializing) return false;
    runtime._annotationMaterializing = true;
    let resolveGate = null;
    runtime._annotationMaterializationGate = new Promise((resolve) => {
        resolveGate = resolve;
    });
    runtime._endAnnotationMaterializationGate = function () {
        runtime._annotationMaterializing = false;
        const resolve = resolveGate;
        resolveGate = null;
        runtime._annotationMaterializationGate = null;
        runtime._endAnnotationMaterializationGate = null;
        if (typeof resolve === 'function') resolve();
    };
    return true;
}

function endGate(runtime) {
    if (typeof runtime._endAnnotationMaterializationGate === 'function') {
        runtime._endAnnotationMaterializationGate();
    } else {
        runtime._annotationMaterializing = false;
        runtime._annotationMaterializationGate = null;
    }
}

function beginHandoff(runtime) {
    runtime._annotationMaterializationHandoff = true;
    runtime.annotationMutationAllowed = false;
}

function clearHandoff(runtime) {
    runtime._annotationMaterializationHandoff = false;
}

/**
 * Mirror of prksWaitOutAnnotationMaterialization — deadline/lifecycle must
 * cover the gate Promise itself (no unbounded await gate).
 */
async function waitOutMaterialization(pdf, { deadlineMs = 30000 } = {}) {
    if (!pdf) return;
    const deadline = Date.now() + deadlineMs;

    function lifecycleEscape() {
        if (pdf._destroyed) return true;
        const persistence = pdf.annotationPersistence;
        if (persistence && (persistence.destroyed || persistence.paused)) return true;
        return false;
    }

    function stillBusy() {
        if (pdf._annotationMaterializing || pdf._annotationMaterializationHandoff) {
            return true;
        }
        const gate = pdf._annotationMaterializationGate;
        return !!(gate && typeof gate.then === 'function');
    }

    while (stillBusy()) {
        if (lifecycleEscape()) return;
        if (Date.now() >= deadline) return;
        const gate = pdf._annotationMaterializationGate;
        const slice = Math.min(25, Math.max(0, deadline - Date.now()));
        const timeout = new Promise((resolve) => setTimeout(resolve, slice));
        // Handoff can be true with no gate — never race Promise.resolve() (spin).
        if (gate && typeof gate.then === 'function') {
            await Promise.race([
                Promise.resolve(gate).then(() => {}, () => {}),
                timeout,
            ]);
        } else {
            await timeout;
        }
    }
}

function userMutationStillAllowed(runtime, viewer) {
    if (!runtime) return false;
    if (runtime.annotationMutationAllowed === false) return false;
    if (runtime._annotationCatchUpBlocksMutation) return false;
    if (runtime._annotationMaterializationHandoff) return false;
    if (runtime._annotationMaterializing) return false;
    if (!viewer.isUserMutationEnabled()) return false;
    return true;
}

/**
 * Mirrors maybeCatchUpMaterialization finally + requestFlush handoff when
 * shouldMaterialize is true: keep mutation off, release catch-up gate, then
 * let materialization acquire its own gate.
 */
async function catchUpFinallyHandoff(runtime, viewer, { shouldMaterialize, projectionReady }) {
    if (shouldMaterialize) {
        beginHandoff(runtime);
        viewer.setMutationEnabled(false);
    } else if (projectionReady) {
        viewer.setMutationEnabled(true);
        runtime.annotationMutationAllowed = true;
    }
    endGate(runtime);
}

/**
 * Mirrors materialization finally: await capability while locked, then
 * synchronously clear handoff + enable + end gate (no await between enable
 * and gate end).
 */
async function materializationFinallyUnlock(runtime, viewer, { resolveCapability }) {
    // Capability resolve while controller still locked.
    const cap = await resolveCapability();
    viewer.setMutationEnabled(false);
    const unlockToWork = !!(cap && cap.mode === 'work');
    // Synchronous critical-section exit — no await after enabling.
    clearHandoff(runtime);
    runtime.annotationMutationAllowed = unlockToWork;
    viewer.setMutationEnabled(unlockToWork);
    endGate(runtime);
}

(async function main() {
    const runtime = {
        annotationMutationAllowed: true,
        _annotationMaterializing: false,
        _annotationMaterializationHandoff: false,
        _annotationCatchUpBlocksMutation: false,
    };
    const viewer = makeViewer();

    // Catch-up gate held, projection done, materialization required.
    assert.equal(beginGate(runtime), true);
    viewer.setMutationEnabled(false);
    runtime.annotationMutationAllowed = false;

    await catchUpFinallyHandoff(runtime, viewer, {
        shouldMaterialize: true,
        projectionReady: true,
    });

    // Gap: catch-up gate released, materialization not yet acquired.
    assert.equal(runtime._annotationMaterializing, false);
    assert.equal(runtime._annotationMaterializationHandoff, true);
    assert.equal(viewer.isUserMutationEnabled(), false);
    assert.equal(userMutationStillAllowed(runtime, viewer), false);
    assert.deepEqual(viewer.activateMarkupTool('highlight'), {
        ok: false,
        reason: 'mutation_disabled',
    });
    assert.deepEqual(viewer.createAnnotation({ type: 'highlight' }), {
        ok: false,
        reason: 'mutation_disabled',
    });

    // Pause before materialization acquires its gate (simulated delay).
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(userMutationStillAllowed(runtime, viewer), false);
    assert.equal(viewer.activateMarkupTool('underline').ok, false);

    assert.equal(beginGate(runtime), true);
    viewer.setMutationEnabled(false);

    // Pause capability restoration mid-materialization finally.
    let resumeCapability;
    const capabilityPaused = new Promise((resolve) => {
        resumeCapability = resolve;
    });
    const unlockPromise = materializationFinallyUnlock(runtime, viewer, {
        resolveCapability: async () => {
            await capabilityPaused;
            return { mode: 'work', durable: true, reason: 'online_durable' };
        },
    });

    // While capability is paused, mutation must remain impossible.
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(runtime._annotationMaterializing, true);
    assert.equal(runtime._annotationMaterializationHandoff, true);
    assert.equal(viewer.isUserMutationEnabled(), false);
    assert.equal(userMutationStillAllowed(runtime, viewer), false);
    assert.equal(viewer.createAnnotation({ type: 'highlight' }).ok, false);

    resumeCapability();
    await unlockPromise;

    assert.equal(runtime._annotationMaterializationHandoff, false);
    assert.equal(runtime._annotationMaterializing, false);
    assert.equal(runtime.annotationMutationAllowed, true);
    assert.equal(viewer.isUserMutationEnabled(), true);
    assert.equal(userMutationStillAllowed(runtime, viewer), true);
    assert.equal(viewer.activateMarkupTool('highlight').ok, true);
    assert.equal(viewer.createAnnotation({ type: 'highlight' }).ok, true);

    // No-materialize catch-up restores capability normally.
    const runtime2 = {
        annotationMutationAllowed: false,
        _annotationMaterializing: false,
        _annotationMaterializationHandoff: false,
        _annotationCatchUpBlocksMutation: false,
    };
    const viewer2 = makeViewer();
    assert.equal(beginGate(runtime2), true);
    viewer2.setMutationEnabled(false);
    await catchUpFinallyHandoff(runtime2, viewer2, {
        shouldMaterialize: false,
        projectionReady: true,
    });
    assert.equal(runtime2._annotationMaterializationHandoff, false);
    assert.equal(viewer2.isUserMutationEnabled(), true);
    assert.equal(runtime2.annotationMutationAllowed, true);

    // Unresolved gate + destroy: wait must return without the gate settling.
    const stuck = {
        _destroyed: false,
        _annotationMaterializing: false,
        _annotationMaterializationHandoff: false,
        annotationPersistence: { paused: false, destroyed: false },
    };
    assert.equal(beginGate(stuck), true);
    const unresolvedGate = stuck._annotationMaterializationGate;
    let gateSettled = false;
    unresolvedGate.then(() => { gateSettled = true; });
    stuck._destroyed = true;
    stuck.annotationPersistence.destroyed = true;
    const tDestroy = Date.now();
    await waitOutMaterialization(stuck);
    assert.ok(Date.now() - tDestroy < 500, 'destroy must not wait on unresolved gate');
    assert.equal(gateSettled, false);
    assert.equal(stuck._annotationMaterializing, true);

    // Unresolved gate + paused persistence worker.
    const paused = {
        _destroyed: false,
        _annotationMaterializing: false,
        _annotationMaterializationHandoff: false,
        annotationPersistence: { paused: false, destroyed: false },
    };
    assert.equal(beginGate(paused), true);
    let pausedGateSettled = false;
    paused._annotationMaterializationGate.then(() => { pausedGateSettled = true; });
    paused.annotationPersistence.paused = true;
    const tPause = Date.now();
    await waitOutMaterialization(paused);
    assert.ok(Date.now() - tPause < 500, 'pause must not wait on unresolved gate');
    assert.equal(pausedGateSettled, false);

    // Handoff with no gate: short timer must clear handoff promptly (no microtask spin).
    const handoffGap = {
        _destroyed: false,
        _annotationMaterializing: false,
        _annotationMaterializationHandoff: true,
        _annotationMaterializationGate: null,
        annotationPersistence: { paused: false, destroyed: false },
    };
    setTimeout(() => {
        handoffGap._annotationMaterializationHandoff = false;
    }, 40);
    const tHandoff = Date.now();
    await waitOutMaterialization(handoffGap);
    const handoffElapsed = Date.now() - tHandoff;
    assert.ok(
        handoffElapsed < 500,
        `handoff gap wait must return promptly, got ${handoffElapsed}ms`,
    );
    assert.equal(handoffGap._annotationMaterializationHandoff, false);
    assert.equal(handoffGap._annotationMaterializationGate, null);

    console.log(checks + ' checks passed');
})().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
