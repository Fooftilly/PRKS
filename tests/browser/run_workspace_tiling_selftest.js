#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '../..');
const tilingSrc = fs.readFileSync(path.join(rootDir, 'frontend/js/workspace-tiling.js'), 'utf8');

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

function tick() {
    return new Promise(function (resolve) {
        setTimeout(resolve, 0);
    });
}

function el(tag) {
    const attrs = Object.create(null);
    const kids = [];
    const node = {
        tagName: String(tag || 'div').toUpperCase(),
        className: '',
        hidden: false,
        textContent: '',
        innerHTML: '',
        children: kids,
        style: {},
        classList: {
            toggle: function () {},
            add: function () {},
            remove: function () {},
            contains: function () {
                return false;
            },
        },
        setAttribute: function (k, v) {
            attrs[k] = String(v);
        },
        getAttribute: function (k) {
            return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null;
        },
        removeAttribute: function (k) {
            delete attrs[k];
        },
        appendChild: function (child) {
            const i = kids.indexOf(child);
            if (i >= 0) kids.splice(i, 1);
            kids.push(child);
            child.parentNode = node;
            return child;
        },
        removeChild: function (child) {
            const i = kids.indexOf(child);
            if (i >= 0) kids.splice(i, 1);
            return child;
        },
        replaceChildren: function () {
            kids.length = 0;
        },
        querySelectorAll: function () {
            return [];
        },
        querySelector: function (sel) {
            const want = String(sel || '');
            for (let i = 0; i < kids.length; i++) {
                if (want.indexOf('prks-tile-header') !== -1 && kids[i].className.indexOf('prks-tile-header') !== -1) {
                    return kids[i];
                }
                if (want.indexOf('prks-tile__body') !== -1 && kids[i].className.indexOf('prks-tile__body') !== -1) {
                    return kids[i];
                }
                const nested = kids[i].querySelector && kids[i].querySelector(sel);
                if (nested) return nested;
            }
            return null;
        },
        addEventListener: function () {},
        focus: function () {},
    };
    return node;
}

(async function () {
    let roCount = 0;
    const observers = [];
    function FakeRO(cb) {
        roCount += 1;
        this.cb = cb;
        this.observe = function () {
            observers.push(this);
        };
        this.disconnect = function () {};
    }

    const canvas = el('div');
    canvas.className = 'prks-workspace-canvas prks-workspace-canvas--stacked';
    canvas.clientWidth = 1600;

    const pageContent = el('div');
    pageContent.querySelector = function (sel) {
        if (String(sel).indexOf('prks-workspace-canvas') !== -1) return canvas;
        return null;
    };
    pageContent.appendChild(canvas);

    const fallbackCalls = [];
    const ratioReapplyCalls = [];
    let rejectNextNarrow = false;

    const sandbox = {
        window: {},
        document: {
            getElementById: function (id) {
                if (id === 'page-content') return pageContent;
                if (id === 'prks-workspace-tabs') return el('div');
                return null;
            },
            querySelector: function () {
                return null;
            },
            createElement: function (tag) {
                return el(tag);
            },
            addEventListener: function () {},
            body: {
                contains: function () {
                    return true;
                },
                appendChild: function () {},
            },
        },
        ResizeObserver: FakeRO,
        prksWorkspaceSetNarrowFallback: function (narrow) {
            fallbackCalls.push(!!narrow);
            if (narrow && rejectNextNarrow) {
                rejectNextNarrow = false;
                return Promise.resolve(false);
            }
            return Promise.resolve(true);
        },
        prksWorkspaceReapplySplitRatio: function (_canvas, options) {
            ratioReapplyCalls.push(options || {});
        },
        prksWorkspaceSnapshot: function () {
            return {
                mode: 'stacked',
                mainTabId: 't1',
                focusedTabId: 't1',
                tabs: [{ id: 't1', title: 'A', icon: 'file-text' }],
                secondaryTree: null,
            };
        },
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    vm.runInNewContext(tilingSrc, sandbox);

    sandbox.prksWorkspaceInitTiles();
    assertEq('first observe constructs observer', roCount, 1);
    await tick();
    const afterInit = fallbackCalls.length;

    const snap = sandbox.prksWorkspaceSnapshot();
    sandbox.prksWorkspaceSyncTiles(snap, { visualMode: 'stacked' });
    sandbox.prksWorkspaceSyncTiles(snap, { visualMode: 'stacked' });
    sandbox.prksWorkspaceApplyFocus(snap, { visualMode: 'stacked' });
    sandbox.prksWorkspaceSyncTiles(snap, { visualMode: 'tiled' });
    sandbox.prksWorkspaceApplyFocus(snap, { visualMode: 'tiled' });
    assertEq('same canvas does not reconstruct observer', roCount, 1);
    assertEq('paints do not re-evaluate width', fallbackCalls.length, afterInit);

    rejectNextNarrow = true;
    canvas.clientWidth = 500;
    observers[0].cb();
    await tick();
    assertEq('narrow transition evaluated', fallbackCalls.length, afterInit + 1);
    assertEq('narrow transition asked true', fallbackCalls[fallbackCalls.length - 1], true);
    assertEq('passive narrow geometry never commits preferred root ratio', ratioReapplyCalls[ratioReapplyCalls.length - 1].commit, false);

    observers[0].cb();
    await tick();
    sandbox.prksWorkspaceSyncTiles(snap, { visualMode: 'tiled' });
    sandbox.prksWorkspaceApplyFocus(snap, { visualMode: 'tiled' });
    assertEq('rejected narrow does not retry at same width', fallbackCalls.length, afterInit + 1);

    canvas.clientWidth = 1600;
    observers[0].cb();
    await tick();
    assertEq('widen evaluates again', fallbackCalls.length, afterInit + 2);
    assertEq('widen asked false', fallbackCalls[fallbackCalls.length - 1], false);
    assertEq('passive wide geometry never commits preferred root ratio', ratioReapplyCalls[ratioReapplyCalls.length - 1].commit, false);

    canvas.clientWidth = 400;
    observers[0].cb();
    await tick();
    assertEq('later width change evaluates again', fallbackCalls.length, afterInit + 3);
    assertEq('later width asked true', fallbackCalls[fallbackCalls.length - 1], true);

    if (failed) {
        console.log(failed + ' failed, ' + passed + ' passed');
        process.exit(1);
    }
    console.log('All ' + passed + ' workspace tiling observer checks passed');
})().catch(function (err) {
    console.error(err);
    process.exit(1);
});
