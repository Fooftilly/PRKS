#!/usr/bin/env node
'use strict';

/**
 * The shipped stale-snapshot catch-up retry (works-pdf.js) is single-flight:
 * overlapping callers schedule one retry, and its settle flag stays set until
 * that retry is dispatched. E2E settle checks read the flag, so a flag cleared
 * while another retry was still queued would let them see the tools settled
 * just before a catch-up locks them again.
 */

const strict = require('assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let checks = 0;
const assert = new Proxy(function (...args) { checks += 1; return strict(...args); }, {
    get: (_t, k) => (...args) => { checks += 1; return strict[k](...args); },
});

function shippedScheduler() {
    const source = fs.readFileSync(
        path.join(__dirname, '../../frontend/js/components/works-pdf.js'), 'utf8');
    const marker = 'function scheduleStaleSnapshotCatchUp(';
    const at = source.indexOf(marker);
    assert.ok(at >= 0 && source.indexOf(marker, at + 1) < 0, 'one shipped scheduler');
    const end = source.indexOf('\n    }\n', at);
    assert.ok(end > at, 'the scheduler body closes');
    return source.slice(at, end + 6);
}

function harness() {
    const timers = [];
    const catchUps = [];
    const ctx = {
        Number,
        runtime: {},
        stillLive: () => true,
        maybeCatchUpMaterialization: () => { catchUps.push(ctx.runtime._annotationStaleSnapshotRetryScheduled); },
        setTimeout: (fn, ms) => { timers.push({ fn, ms }); },
    };
    vm.createContext(ctx);
    vm.runInContext(shippedScheduler(), ctx);
    return { ctx, timers, catchUps };
}

function overlappingCallersScheduleOneRetry() {
    const { ctx, timers, catchUps } = harness();
    ctx.scheduleStaleSnapshotCatchUp();
    ctx.scheduleStaleSnapshotCatchUp();
    assert.equal(timers.length, 1, 'a second caller joins the scheduled retry');
    assert.equal(ctx.runtime._annotationStaleSnapshotRetryScheduled, true);
    assert.equal(ctx.runtime._annotationStaleSnapshotRetries, 1, 'one attempt is counted');

    timers.shift().fn();
    assert.deepEqual(catchUps, [false], 'the flag clears as its only retry dispatches');
    assert.equal(ctx.runtime._annotationStaleSnapshotRetryScheduled, false);

    ctx.scheduleStaleSnapshotCatchUp();
    assert.equal(timers.length, 1, 'a later stale read schedules again');
    assert.equal(timers[0].ms, 100, 'with the next backoff step');
    assert.equal(ctx.runtime._annotationStaleSnapshotRetryScheduled, true);
}

function retriesStopAtTheBound() {
    const { ctx, timers } = harness();
    for (let i = 0; i < 7; i += 1) {
        ctx.scheduleStaleSnapshotCatchUp();
        if (timers.length) timers.shift().fn();
    }
    assert.equal(ctx.runtime._annotationStaleSnapshotRetries, 5, 'at most five retries');
    assert.equal(ctx.runtime._annotationStaleSnapshotRetryScheduled, false,
        'and no flag is left set past the bound');
}

overlappingCallersScheduleOneRetry();
retriesStopAtTheBound();
console.log('All ' + checks + ' stale snapshot retry checks passed');
