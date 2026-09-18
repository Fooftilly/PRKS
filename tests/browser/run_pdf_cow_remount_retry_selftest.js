#!/usr/bin/env node
'use strict';

/**
 * Behavioral regression for shared-PDF COW remount retry.
 *
 * Staging-first remount keeps the old shared-URL viewer on failure. Retry must
 * NOT bail merely because that viewer still exists — only when the live viewer
 * is bound to the exclusive path (viewerFilePath === filePath). Mutation must
 * stay off on the survivor until replacement succeeds.
 *
 * Mirrors control flow in frontend/js/components/works-pdf.js
 * (scheduleCowViewerRemount / remountPdfViewerAfterCowRetarget /
 * materialization finally unlock / prksApplyPdfAnnotationCapability).
 */

const strict = require('assert/strict');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); }, {
    get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); },
});

function managedPdfApiPath(filePath) {
    const raw = String(filePath || '').split('?')[0].trim();
    return raw.indexOf('/api/pdfs/') === 0 ? raw : '';
}

function viewerBoundToManagedPath(runtime, path) {
    if (!runtime || !runtime.viewer) return false;
    const want = managedPdfApiPath(path);
    const bound = managedPdfApiPath(runtime.viewerFilePath);
    return !!(want && bound && want === bound);
}

function makeViewer(label) {
    let enabled = true;
    const log = [];
    return {
        label,
        log,
        destroyCalls: 0,
        setMutationEnabled(v) {
            enabled = !!v;
            log.push(['setMutationEnabled', enabled]);
        },
        isUserMutationEnabled() {
            return enabled;
        },
        destroy() {
            this.destroyCalls += 1;
            log.push(['destroy']);
        },
    };
}

function lockViewerPendingCowRemount(runtime, path) {
    const pending = managedPdfApiPath(path);
    if (runtime && pending) {
        runtime._cowRemountPendingPath = pending;
    }
    const live = runtime && runtime.viewer;
    if (live && typeof live.setMutationEnabled === 'function') {
        live.setMutationEnabled(false);
    }
    if (runtime) {
        runtime.annotationMutationAllowed = false;
        runtime.annotationMutationReason = 'cow_remount_pending';
    }
}

/**
 * Faithful mirror of scheduleCowViewerRemount. The OLD bug was:
 *   if (liveAnnotationViewer()) return;
 * which aborted every retry while the shared survivor remained mounted.
 */
function scheduleCowViewerRemount(runtime, path, hooks) {
    if (!runtime || runtime._destroyed) return;
    const want = managedPdfApiPath(path);
    if (!want) return;
    lockViewerPendingCowRemount(runtime, want);
    if (runtime._cowRemountRetryScheduled) return;
    runtime._cowRemountRetryScheduled = true;
    const attempt = Number(runtime._cowRemountRetryAttempt) || 0;
    if (attempt >= 5) {
        runtime._cowRemountRetryScheduled = false;
        return;
    }
    runtime._cowRemountRetryAttempt = attempt + 1;
    const delay = hooks.delayMs != null ? hooks.delayMs : 5;
    setTimeout(function () {
        runtime._cowRemountRetryScheduled = false;
        if (!runtime || runtime._destroyed) return;
        // Fixed: path-bound, not mere presence.
        if (viewerBoundToManagedPath(runtime, want)) {
            runtime._cowRemountRetryAttempt = 0;
            runtime._cowRemountPendingPath = '';
            return;
        }
        void (async function () {
            const ok = await hooks.remount(want);
            if (ok) {
                runtime._cowRemountRetryAttempt = 0;
                runtime._cowRemountPendingPath = '';
                if (typeof hooks.onSuccess === 'function') {
                    await hooks.onSuccess();
                }
                return;
            }
            lockViewerPendingCowRemount(runtime, want);
            scheduleCowViewerRemount(runtime, want, hooks);
        })();
    }, delay);
}

/**
 * Mirror of materialization finally unlock with path-bound gate.
 */
function materializationFinallyUnlock(runtime, unlockToWork) {
    const unlockViewer = runtime.viewer;
    const viewerBoundExclusive = viewerBoundToManagedPath(runtime, runtime.filePath);
    if (unlockViewer && typeof unlockViewer.setMutationEnabled === 'function') {
        const allowUnlock = !!(unlockToWork && viewerBoundExclusive);
        runtime.annotationMutationAllowed = allowUnlock;
        unlockViewer.setMutationEnabled(allowUnlock);
        if (!viewerBoundExclusive && managedPdfApiPath(runtime.filePath)) {
            lockViewerPendingCowRemount(runtime, runtime.filePath);
        }
        return { allowUnlock, viewerBoundExclusive };
    }
    return { allowUnlock: false, viewerBoundExclusive };
}

/**
 * Mirror of capability apply: refuse mutation until viewerFilePath matches.
 */
function applyCapability(runtime, capMode) {
    const exclusivePath = managedPdfApiPath(runtime.filePath);
    if (exclusivePath && runtime.viewer && !viewerBoundToManagedPath(runtime, exclusivePath)) {
        runtime.annotationMutationAllowed = false;
        runtime.annotationMutationReason = 'cow_remount_pending';
        if (typeof runtime.viewer.setMutationEnabled === 'function') {
            runtime.viewer.setMutationEnabled(false);
        }
        return { mode: 'preview', reason: 'cow_remount_pending' };
    }
    runtime.annotationMutationAllowed = capMode === 'work';
    if (runtime.viewer && typeof runtime.viewer.setMutationEnabled === 'function') {
        runtime.viewer.setMutationEnabled(capMode === 'work');
    }
    return { mode: capMode, reason: 'online_durable' };
}

function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
}

async function firstFailThenSucceedRetry() {
    const shared = '/api/pdfs/shared.pdf';
    const exclusive = '/api/pdfs/exclusive.pdf';
    const oldViewer = makeViewer('shared');
    oldViewer.setMutationEnabled(true);

    const runtime = {
        viewer: oldViewer,
        viewerFilePath: shared,
        filePath: exclusive, // COW already retargeted
        annotationMutationAllowed: true,
        _cowRemountRetryAttempt: 0,
        _cowRemountRetryScheduled: false,
        _cowRemountPendingPath: '',
    };

    let remountCalls = 0;
    const remountArgs = [];
    const newViewer = makeViewer('exclusive');

    const remount = async (want) => {
        remountCalls += 1;
        remountArgs.push(want);
        // First attempt fails: staging create fails; old viewer stays.
        if (remountCalls === 1) {
            oldViewer.setMutationEnabled(false);
            return false;
        }
        // Second attempt succeeds: replace binding.
        assert.equal(runtime.viewer, oldViewer, 'old viewer still mounted at retry');
        assert.equal(oldViewer.isUserMutationEnabled(), false);
        runtime.viewer = newViewer;
        runtime.viewerFilePath = want;
        newViewer.setMutationEnabled(false); // keepLocked / capability later
        return true;
    };

    // Simulate applyCowPdfRetarget failing remount then scheduling retry.
    const first = await remount(exclusive);
    assert.equal(first, false);
    assert.equal(runtime.viewer, oldViewer);
    assert.equal(oldViewer.destroyCalls, 0);
    scheduleCowViewerRemount(runtime, exclusive, { remount, delayMs: 10 });

    // Materialization finally must NOT unlock the shared survivor.
    const unlock = materializationFinallyUnlock(runtime, true);
    assert.equal(unlock.viewerBoundExclusive, false);
    assert.equal(unlock.allowUnlock, false);
    assert.equal(oldViewer.isUserMutationEnabled(), false);
    assert.equal(runtime.annotationMutationAllowed, false);

    // Capability re-resolve must also refuse.
    const cap = applyCapability(runtime, 'work');
    assert.equal(cap.mode, 'preview');
    assert.equal(cap.reason, 'cow_remount_pending');
    assert.equal(oldViewer.isUserMutationEnabled(), false);

    // Wait for retry — must invoke remount despite old viewer existing.
    await sleep(40);
    assert.equal(remountCalls, 2, 'retry invoked remount while old viewer live');
    assert.deepEqual(remountArgs, [exclusive, exclusive]);
    assert.equal(runtime.viewer, newViewer);
    assert.equal(runtime.viewerFilePath, exclusive);
    assert.equal(runtime._cowRemountPendingPath, '');

    // Old viewer never received setMutationEnabled(true) after the first lock.
    const afterFirstLock = oldViewer.log.indexOf(
        oldViewer.log.find((e) => e[0] === 'setMutationEnabled' && e[1] === false)
    );
    assert.ok(afterFirstLock >= 0);
    for (let i = afterFirstLock; i < oldViewer.log.length; i++) {
        const entry = oldViewer.log[i];
        if (entry[0] === 'setMutationEnabled') {
            assert.equal(entry[1], false, 'old viewer never re-enabled');
        }
    }

    // After successful bind, capability may enable the NEW viewer only.
    const cap2 = applyCapability(runtime, 'work');
    assert.equal(cap2.mode, 'work');
    assert.equal(newViewer.isUserMutationEnabled(), true);
    assert.equal(oldViewer.isUserMutationEnabled(), false);
}

async function oldBugWouldBailOnLiveViewer() {
    // Document the regression: mere-presence bail never retries.
    const shared = '/api/pdfs/shared.pdf';
    const exclusive = '/api/pdfs/exclusive.pdf';
    const oldViewer = makeViewer('shared');
    const runtime = {
        viewer: oldViewer,
        viewerFilePath: shared,
        filePath: exclusive,
        _cowRemountRetryAttempt: 0,
        _cowRemountRetryScheduled: false,
    };
    let remountCalls = 0;
    // Broken schedule (historical bug).
    function brokenSchedule(path) {
        if (runtime._cowRemountRetryScheduled) return;
        runtime._cowRemountRetryScheduled = true;
        setTimeout(function () {
            runtime._cowRemountRetryScheduled = false;
            if (runtime.viewer) return; // THE BUG
            remountCalls += 1;
        }, 5);
    }
    brokenSchedule(exclusive);
    await sleep(25);
    assert.equal(remountCalls, 0, 'old presence-bail never remounts');

    // Fixed schedule does remount.
    remountCalls = 0;
    scheduleCowViewerRemount(runtime, exclusive, {
        delayMs: 5,
        remount: async () => {
            remountCalls += 1;
            runtime.viewerFilePath = exclusive;
            return true;
        },
    });
    await sleep(25);
    assert.equal(remountCalls, 1, 'path-bound schedule remounts despite live viewer');
}

async function applyCowEarlyReturnRequiresBinding() {
    const shared = '/api/pdfs/shared.pdf';
    const exclusive = '/api/pdfs/exclusive.pdf';
    const runtime = {
        viewer: makeViewer('shared'),
        viewerFilePath: shared,
        filePath: exclusive,
    };
    // Path already applied but viewer not bound → must remount (not early-return).
    const path = managedPdfApiPath(exclusive);
    const prev = managedPdfApiPath(runtime.filePath);
    assert.equal(path, prev);
    let shouldRemount = true;
    if (path === prev) {
        if (viewerBoundToManagedPath(runtime, path)) shouldRemount = false;
    }
    assert.equal(shouldRemount, true);

    runtime.viewerFilePath = exclusive;
    shouldRemount = true;
    if (path === prev) {
        if (viewerBoundToManagedPath(runtime, path)) shouldRemount = false;
    }
    assert.equal(shouldRemount, false);
}

(async function main() {
    await firstFailThenSucceedRetry();
    await oldBugWouldBailOnLiveViewer();
    await applyCowEarlyReturnRequiresBinding();
    console.log(`${checks} checks passed`);
})().catch((err) => {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
