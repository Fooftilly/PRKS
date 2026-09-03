#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '../..');
const tc = require(path.join(rootDir, 'frontend/js/tab-context.js'));
const worksSrc = fs.readFileSync(path.join(rootDir, 'frontend/js/components/works.js'), 'utf8');

const {
    prksEnsureTabContext,
    prksDestroyAllTabContexts,
} = tc;

let passed = 0;
let failed = 0;
let bannedQuery = 0;

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

function makeWorkspace(id) {
    const styleVars = {};
    const handle = {
        setAttribute: function () {},
        addEventListener: function () {},
        classList: { add: function () {}, remove: function () {} },
        querySelector: function () {
            return null;
        },
    };
    const ws = {
        workId: id,
        classList: {
            contains: function () {
                return false;
            },
            add: function () {},
            remove: function () {},
            toggle: function () {},
        },
        getAttribute: function (k) {
            return k === 'data-work-id' ? id : null;
        },
        querySelector: function (sel) {
            if (sel === '.work-split-handle') return handle;
            if (sel === '.work-notes-pane') return { getBoundingClientRect: function () { return { width: 280, height: 320, top: 0, bottom: 320 }; } };
            return null;
        },
        style: {
            setProperty: function (k, v) {
                styleVars[k] = v;
            },
            getPropertyValue: function (k) {
                return styleVars[k] || '';
            },
        },
        getBoundingClientRect: function () {
            return { width: 800, height: 600, top: 0, bottom: 600 };
        },
        _vars: styleVars,
    };
    return ws;
}

function makeRoot(ws) {
    return {
        querySelector: function (sel) {
            if (String(sel).indexOf('.work-workspace') !== -1) return ws;
            return null;
        },
        querySelectorAll: function () {
            return [];
        },
    };
}

prksDestroyAllTabContexts();
const ctxA = prksEnsureTabContext('notes-a');
const ctxB = prksEnsureTabContext('notes-b');
const wsA = makeWorkspace('WA');
const wsB = makeWorkspace('WB');
ctxA.root = makeRoot(wsA);
ctxB.root = makeRoot(wsB);
ctxA.mounted = true;
ctxB.mounted = true;
ctxA.query = function (sel) {
    return ctxA.root.querySelector(sel);
};
ctxB.query = function (sel) {
    return ctxB.root.querySelector(sel);
};

let refreshA = 0;
let refreshB = 0;
ctxA.setResource('workNotes', {
    codemirror: {
        refresh: function () {
            refreshA += 1;
        },
    },
});
ctxB.setResource('workNotes', {
    codemirror: {
        refresh: function () {
            refreshB += 1;
        },
    },
});

const sandbox = {
    window: {},
    document: {
        documentElement: { classList: { contains: function () { return false; }, toggle: function () {} } },
        getElementById: function () {
            return null;
        },
        querySelector: function () {
            bannedQuery += 1;
            throw new Error('document.querySelector must not be used for Work notes layout');
        },
        querySelectorAll: function () {
            return [];
        },
        addEventListener: function () {},
        createElement: function () {
            return { style: {}, setAttribute: function () {}, classList: { add: function () {}, remove: function () {}, contains: function () { return false; } } };
        },
    },
    localStorage: { getItem: function () { return null; }, setItem: function () {} },
    requestAnimationFrame: function (fn) {
        fn();
    },
    console: console,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    ResizeObserver: function () {
        this.observe = function () {};
        this.disconnect = function () {};
    },
    prksGetFocusedTabContext: function () {
        throw new Error('must not depend on focusedTabId');
    },
    prksFocusedResource: function () {
        throw new Error('must not use focused resource fallback');
    },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.runInNewContext(worksSrc + '\nthis.prksReapplyWorkNotesSplitLayout = prksReapplyWorkNotesSplitLayout;', sandbox);

sandbox.prksReapplyWorkNotesSplitLayout(ctxA);
assert('A workspace height set', !!wsA._vars['--work-notes-height']);
assert('B workspace untouched', !wsB._vars['--work-notes-height']);
assertEq('A editor refreshed', refreshA >= 1, true);
assertEq('B editor not refreshed', refreshB, 0);
assertEq('no document.querySelector', bannedQuery, 0);

sandbox.prksReapplyWorkNotesSplitLayout(ctxB);
assert('B workspace height set', !!wsB._vars['--work-notes-height']);
assertEq('still no document.querySelector', bannedQuery, 0);

prksDestroyAllTabContexts();

if (failed) {
    console.log(failed + ' failed, ' + passed + ' passed');
    process.exit(1);
}
console.log('All ' + passed + ' Work notes layout isolation checks passed');
