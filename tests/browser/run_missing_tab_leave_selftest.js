#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const appPath = path.resolve(__dirname, '../../frontend/js/app.js');
const app = fs.readFileSync(appPath, 'utf8');
const start = app.indexOf('function prksLeaveDecisionReason');
const end = app.indexOf('async function prksCommitTabRouteRender');
if (start < 0 || end < start) {
    console.log('FAIL  could not slice route leave guards');
    process.exit(1);
}

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

const context = {
    committed: 0,
    prksParseRoute: function (hash) {
        return { name: 'work', canonicalHash: hash, hash: hash };
    },
    prksCommitTabRouteRender: function () {
        context.committed += 1;
        return { committed: true };
    },
};
vm.createContext(context);
context.window = context;
vm.runInContext(app.slice(start, end), context);

async function run() {
    const missing = await vm.runInContext(
        `(async function () {
            const ctx = { root: {}, destroyed: false, tabId: 't', generation: 1 };
            const direct = await prksRenderTabRoute(ctx, '#/works/W1', {});
            const approved = await prksCanLeaveTabContext(ctx, '#/folders');
            const refresh = await prksRenderTabRoute(ctx, '#/works/W1', { internalRefresh: true });
            const already = await prksRenderTabRoute(ctx, '#/works/W1', { leaveApproved: true });
            return { direct: direct, approved: approved, refresh: refresh, already: already, committed: committed };
        })()`,
        context
    );
    assertEq('missing script does not throw', missing.direct && missing.direct.cancelled, true);
    assertEq('missing script cancels the direct route', missing.direct && missing.direct.reason, 'cancelled');
    assertEq('missing script does not approve the direct route', missing.committed, 2);
    assertEq('missing script does not approve canLeave', missing.approved, false);
    assertEq('internal refresh still commits without a leave', missing.refresh && missing.refresh.committed, true);
    assertEq('leaveApproved still commits', missing.already && missing.already.committed, true);

    context.prksTabLeave = {
        calls: 0,
        run: function () {
            context.prksTabLeave.calls += 1;
            return Promise.resolve({ status: 'rejected-unsaved-edit' });
        },
        assessOwner: function () {
            return null;
        },
        flushOwner: function () {},
    };
    const present = await vm.runInContext(
        `(async function () {
            const before = committed;
            const ctx = { root: {}, destroyed: false, tabId: 't', generation: 1 };
            const direct = await prksRenderTabRoute(ctx, '#/folders', {});
            return { direct: direct, calls: prksTabLeave.calls, committed: committed - before };
        })()`,
        context
    );
    assertEq('present engine is asked', present.calls, 1);
    assertEq('present engine rejection stays cancelled', present.direct && present.direct.reason, 'unsaved-edit');
    assertEq('present engine rejection does not commit', present.committed, 0);

    console.log('\n' + passed + ' passed, ' + failed + ' failed');
    process.exit(failed ? 1 : 0);
}

run().catch(function (error) {
    console.log('FAIL  missing tab-leave selftest threw ' + (error && error.stack ? error.stack : error));
    process.exit(1);
});
