#!/usr/bin/env node
'use strict';

/**
 * Execute the real PDF byte-adoption branch from works-pdf.js with a paused
 * unresolved-annotation-ops query. The test intentionally holds setupEligible
 * true so the connectivity guard itself, not an unrelated setup lifecycle
 * guard, must prevent a POST after an online -> offline transition.
 */
const fs = require('node:fs');
const path = require('node:path');
const strict = require('node:assert/strict');

const source = fs.readFileSync(
    path.resolve(__dirname, '../../frontend/js/components/works-pdf.js'), 'utf8'
);
const startMarker = '    // Slice G: adopt byte-only user markup from PDF bytes into canonical';
const endMarker = '    // Project acknowledged (+ pending durable) annotations into the';
const start = source.indexOf(startMarker);
const end = source.indexOf(endMarker, start);
strict.ok(start !== -1 && end > start, 'source adoption boundaries are present');
strict.equal(source.indexOf(startMarker, start + 1), -1, 'unique adoption branch');

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const actualAdoptionBranch = new AsyncFunction(
    'window', 'viewer', 'runtime', 'workId', 'setupEligible', 'abandonSetup',
    'prksViewerAnnotationObjects', 'isLikelyAnnotationObject', 'prksRequest',
    'applyAnnotationSnapshotToRuntime', 'isOnlineRuntime',
    source.slice(start, end)
);

let checks = 0;

async function runCase(initialState, stateAfterPending, hasUnresolvedOps = false) {
    let state = initialState;
    let pendingReadCount = 0;
    let subscriptionCount = 0;
    const listeners = new Set();
    const requests = [];
    let releasePending;
    const pendingRead = new Promise((resolve) => { releasePending = resolve; });
    const window = {
        prksOfflineRuntimeState: () => state,
        prksOfflineRuntimeSubscribe: (fn) => {
            listeners.add(fn);
            return () => { listeners.delete(fn); subscriptionCount += 1; };
        },
        prksWorkHasUnresolvedPdfAnnotationOps: () => {
            pendingReadCount += 1;
            return pendingRead;
        },
    };
    const viewer = { getAnnotations: () => [] };
    const runtime = {
        acknowledgedAnnotationSetRevision: 0,
        materializedPdfAnnotationRevision: 0,
    };
    const working = actualAdoptionBranch(
        window, viewer, runtime, 'W-ADOPT',
        () => true, () => strict.fail('unexpected setup abandonment'),
        () => [], () => true,
        async (url, init, policy) => {
            requests.push({ url, method: init.method, stateAtRequest: state,
                hasAbortSignal: !!init.signal,
                cancellable: policy && policy.cancellableMutation === true });
            return { ok: true, json: async () => ({ adopted_count: 0 }) };
        },
        () => strict.fail('unexpected projection'),
        () => state === 'online'
    );
    // A real async pending-ops query suspends this branch after the initial
    // online check. Switch network state while that Promise is unsettled.
    checks += 1;
    strict.equal(pendingReadCount, initialState === 'online' ? 1 : 0);
    state = stateAfterPending;
    releasePending(hasUnresolvedOps);
    await working;
    if (requests.length) {
        strict.equal(subscriptionCount, 1, 'adoption must unsubscribe from offline runtime');
    }
    strict.equal(listeners.size, 0, 'adoption never leaks an offline listener');
    return requests;
}

/* Suspend the real adoption branch at request dispatch and then signal an
 * offline transition. Browser network cancellation is asserted separately by
 * the coordinator tests; here the shipped caller must own a live signal and
 * retire its subscription when the request settles. */
async function adoptionAbortAfterQueue() {
    let state = 'online';
    const listeners = new Set();
    let resolveRequest;
    let signal = null;
    let policy = null;
    let requestCount = 0;
    const window = {
        prksOfflineRuntimeState: () => state,
        prksOfflineRuntimeSubscribe: (listener) => {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
        prksWorkHasUnresolvedPdfAnnotationOps: async () => false,
    };
    const viewer = { getAnnotations: () => [] };
    const runtime = {
        acknowledgedAnnotationSetRevision: 0,
        materializedPdfAnnotationRevision: 0,
    };
    const working = actualAdoptionBranch(
        window, viewer, runtime, 'W-ADOPT',
        () => true, () => strict.fail('unexpected abandon'),
        () => [], () => true,
        async (_url, init, requestPolicy) => {
            requestCount += 1;
            signal = init.signal;
            policy = requestPolicy;
            return new Promise((resolve) => { resolveRequest = resolve; });
        },
        () => strict.fail('unexpected projection'),
        () => state === 'online'
    );
    for (let i = 0; i < 8 && !signal; i++) await Promise.resolve();
    strict.ok(signal, 'adoption dispatched with an AbortSignal');
    strict.equal(listeners.size, 1);
    strict.equal(policy.cancellableMutation, true);
    state = 'offline';
    listeners.forEach((listener) => listener('offline'));
    strict.equal(signal.aborted, true, 'offline transition cancels the queued adoption');
    resolveRequest({ ok: true, json: async () => ({ adopted_count: 1 }) });
    await working;
    strict.equal(requestCount, 1, 'late aborted response must not fetch a new snapshot');
    strict.equal(listeners.size, 0, 'subscription is cleaned up after cancellation');
    checks += 6;
}

(async () => {
    await adoptionAbortAfterQueue();
    let calls = await runCase('online', 'online');
    strict.equal(calls.length, 1);
    strict.equal(calls[0].method, 'POST');
    strict.equal(calls[0].url, '/api/works/W-ADOPT/annotations/adopt');
    strict.equal(calls[0].stateAtRequest, 'online');
    strict.equal(calls[0].cancellable, true, 'adoption opts in to cancellation');
    strict.equal(calls[0].hasAbortSignal, true, 'adoption provides abort signal');
    checks += 6;

    for (const destination of ['offline', 'reconnecting']) {
        calls = await runCase('online', destination);
        strict.deepEqual(calls, [], destination + ' must never emit adoption POST');
        checks += 1;
    }

    calls = await runCase('offline', 'offline');
    strict.deepEqual(calls, [], 'an initially offline viewer must not adopt');
    checks += 1;

    calls = await runCase('online', 'online', true);
    strict.deepEqual(calls, [], 'unresolved durable ops must still block adoption');
    checks += 1;

    process.stdout.write(`${checks} checks passed (PDF adoption offline race)\n`);
})().catch((err) => {
    process.stderr.write(String(err.stack || err) + '\n');
    process.exitCode = 1;
});
