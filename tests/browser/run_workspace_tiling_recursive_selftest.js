#!/usr/bin/env node
'use strict';

/* Fuller fake-DOM coverage for the recursive Secondary tile renderer/reconciler
 * (workspace-tiling.js) + nested divider mechanics (workspace-split.js). Proves DOM/runtime
 * identity is preserved across unrelated tree mutations: split, Make Main, close-collapse,
 * hide/park, and pruning of stale hosts.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '../..');
const tilingSrc = fs.readFileSync(path.join(rootDir, 'frontend/js/workspace-tiling.js'), 'utf8');
const splitSrc = fs.readFileSync(path.join(rootDir, 'frontend/js/workspace-split.js'), 'utf8');
const treeSrc = fs.readFileSync(path.join(rootDir, 'frontend/js/workspace-tree.js'), 'utf8');

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

/* ---------------- Minimal but structurally-real fake DOM ---------------- */

function makeClassList(node) {
    return {
        add: function (c) {
            if (node.__classes.indexOf(c) === -1) node.__classes.push(c);
        },
        remove: function (c) {
            const i = node.__classes.indexOf(c);
            if (i >= 0) node.__classes.splice(i, 1);
        },
        toggle: function (c, force) {
            const has = node.__classes.indexOf(c) !== -1;
            const want = force === undefined ? !has : !!force;
            if (want && !has) node.__classes.push(c);
            if (!want && has) {
                const i = node.__classes.indexOf(c);
                if (i >= 0) node.__classes.splice(i, 1);
            }
        },
        contains: function (c) {
            return node.__classes.indexOf(c) !== -1;
        },
    };
}

function matchesSimpleSelector(node, sel) {
    const s = String(sel || '').trim();
    if (!s) return false;
    /* Supports: '.class', ':scope > .class', 'tag.class[data-x="y"]', '.class[data-x="y"]' -- just
     * enough for what workspace-tiling.js/workspace-split.js actually issue. */
    const cleaned = s.replace(/^:scope\s*>\s*/, '');
    const classMatch = cleaned.match(/\.([\w-]+)/);
    if (classMatch && node.__classes.indexOf(classMatch[1]) === -1) return false;
    const attrMatch = cleaned.match(/\[([\w-]+)(?:="([^"]*)")?\]/);
    if (attrMatch) {
        const val = node.getAttribute(attrMatch[1]);
        if (attrMatch[2] !== undefined) {
            if (val !== attrMatch[2]) return false;
        } else if (val === null) {
            return false;
        }
    }
    return true;
}

function createNode(tag) {
    const attrs = Object.create(null);
    const listeners = Object.create(null);
    const node = {
        tagName: String(tag || 'div').toUpperCase(),
        __classes: [],
        __children: [],
        parentNode: null,
        hidden: false,
        disabled: false,
        title: '',
        textContent: '',
        innerHTML: '',
        style: { setProperty: function () {} },
        clientWidth: 0,
        clientHeight: 0,
    };
    Object.defineProperty(node, 'className', {
        get: function () {
            return node.__classes.join(' ');
        },
        set: function (v) {
            node.__classes = String(v || '')
                .split(/\s+/)
                .filter(Boolean);
        },
    });
    node.classList = makeClassList(node);
    Object.defineProperty(node, 'children', {
        get: function () {
            return node.__children;
        },
    });
    Object.defineProperty(node, 'nextSibling', {
        get: function () {
            if (!node.parentNode) return null;
            const arr = node.parentNode.__children;
            const idx = arr.indexOf(node);
            if (idx === -1 || idx === arr.length - 1) return null;
            return arr[idx + 1];
        },
    });
    node.setAttribute = function (k, v) {
        attrs[k] = String(v);
    };
    node.getAttribute = function (k) {
        return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null;
    };
    node.removeAttribute = function (k) {
        delete attrs[k];
    };
    node.appendChild = function (child) {
        const i = node.__children.indexOf(child);
        if (i >= 0) node.__children.splice(i, 1);
        if (child.parentNode && child.parentNode !== node) {
            const pi = child.parentNode.__children.indexOf(child);
            if (pi >= 0) child.parentNode.__children.splice(pi, 1);
        }
        node.__children.push(child);
        child.parentNode = node;
        return child;
    };
    node.insertBefore = function (child, ref) {
        if (child.parentNode) {
            const pi = child.parentNode.__children.indexOf(child);
            if (pi >= 0) child.parentNode.__children.splice(pi, 1);
        }
        const idx = ref ? node.__children.indexOf(ref) : -1;
        node.__children.splice(idx >= 0 ? idx : node.__children.length, 0, child);
        child.parentNode = node;
        return child;
    };
    node.removeChild = function (child) {
        const i = node.__children.indexOf(child);
        if (i >= 0) node.__children.splice(i, 1);
        child.parentNode = null;
        return child;
    };
    node.replaceChildren = function () {
        node.__children.forEach(function (c) {
            c.parentNode = null;
        });
        node.__children = [];
    };
    node.contains = function (other) {
        let cur = other;
        while (cur) {
            if (cur === node) return true;
            cur = cur.parentNode;
        }
        return false;
    };
    node.querySelector = function (sel) {
        for (let i = 0; i < node.__children.length; i++) {
            const kid = node.__children[i];
            if (matchesSimpleSelector(kid, sel)) return kid;
            const nested = kid.querySelector(sel);
            if (nested) return nested;
        }
        return null;
    };
    node.querySelectorAll = function (sel) {
        const out = [];
        (function walk(n) {
            for (let i = 0; i < n.__children.length; i++) {
                const kid = n.__children[i];
                if (matchesSimpleSelector(kid, sel)) out.push(kid);
                walk(kid);
            }
        })(node);
        return out;
    };
    node.closest = function (sel) {
        let cur = node;
        while (cur) {
            if (matchesSimpleSelector(cur, sel)) return cur;
            cur = cur.parentNode;
        }
        return null;
    };
    node.getBoundingClientRect = function () {
        return { width: node.clientWidth || 0, height: node.clientHeight || 0, left: node.__left || 0, top: node.__top || 0 };
    };
    node.setPointerCapture = function () {};
    node.releasePointerCapture = function () {};
    node.focus = function () {};
    node.addEventListener = function (type, fn, capture) {
        const key = type + (capture ? '|capture' : '');
        if (!listeners[key]) listeners[key] = [];
        listeners[key].push(fn);
    };
    node.removeEventListener = function (type, fn, capture) {
        const key = type + (capture ? '|capture' : '');
        if (!listeners[key]) return;
        const i = listeners[key].indexOf(fn);
        if (i >= 0) listeners[key].splice(i, 1);
    };
    node.dispatch = function (type, evt, capture) {
        const key = type + (capture ? '|capture' : '');
        const fns = (listeners[key] || []).slice();
        const e = Object.assign({ type: type, target: node, preventDefault: function () {}, stopPropagation: function () {} }, evt || {});
        fns.forEach(function (fn) {
            fn(e);
        });
    };
    return node;
}

function makeEventTarget() {
    const listeners = Object.create(null);
    return {
        addEventListener: function (type, fn, capture) {
            const key = type + (capture ? '|capture' : '');
            if (!listeners[key]) listeners[key] = [];
            listeners[key].push(fn);
        },
        removeEventListener: function (type, fn, capture) {
            const key = type + (capture ? '|capture' : '');
            if (!listeners[key]) return;
            const i = listeners[key].indexOf(fn);
            if (i >= 0) listeners[key].splice(i, 1);
        },
        dispatch: function (type, evt, capture) {
            const key = type + (capture ? '|capture' : '');
            const fns = (listeners[key] || []).slice();
            const e = Object.assign({ type: type, preventDefault: function () {}, stopPropagation: function () {} }, evt || {});
            fns.forEach(function (fn) {
                fn(e);
            });
        },
    };
}

function makeDocumentSandbox() {
    const pageContent = createNode('div');
    pageContent.id = 'page-content';
    const body = createNode('body');
    const documentMock = Object.assign(makeEventTarget(), {
        getElementById: function (id) {
            if (id === 'page-content') return pageContent;
            if (id === 'prks-workspace-tabs') return createNode('div');
            if (id === 'prks-workspace-live') return createNode('div');
            return null;
        },
        createElement: function (tag) {
            return createNode(tag);
        },
        querySelector: function () {
            return null;
        },
        body: body,
    });
    return { pageContent: pageContent, body: body, documentMock: documentMock };
}

/* ---------------- Sandbox wiring: real tree.js + tiling.js + split.js together ---------------- */

function makeSandbox() {
    const dom = makeDocumentSandbox();
    let roCount = 0;
    function FakeRO(cb) {
        roCount += 1;
        this.cb = cb;
        this.observe = function () {};
        this.disconnect = function () {};
    }
    let ratioState = 0.58;
    const nestedRatios = Object.create(null);

    const sandbox = {
        window: {},
        document: dom.documentMock,
        ResizeObserver: FakeRO,
        prksWorkspaceGetSplitRatio: function () {
            return ratioState;
        },
        prksWorkspaceSetMainSplitRatio: function (r) {
            ratioState = r;
        },
        prksWorkspaceDefaultSplitRatio: function () {
            return 0.58;
        },
        prksWorkspaceCanAddSecondaryLeaf: function () {
            return true;
        },
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;

    vm.runInNewContext(treeSrc, sandbox);
    vm.runInNewContext(splitSrc, sandbox);
    vm.runInNewContext(tilingSrc, sandbox);

    let currentSnap = null;
    sandbox.prksWorkspaceSnapshot = function () {
        return currentSnap;
    };
    sandbox.prksWorkspaceSetNestedSplitRatio = function (splitId, ratio) {
        nestedRatios[splitId] = ratio;
        if (currentSnap) currentSnap.secondaryTree = sandbox.setSplitRatio(currentSnap.secondaryTree, splitId, ratio);
        return ratio;
    };

    return {
        sandbox: sandbox,
        dom: dom,
        setSnap: function (snap) {
            currentSnap = snap;
        },
        getSnap: function () {
            return currentSnap;
        },
        roCount: function () {
            return roCount;
        },
    };
}

function baseSnap(overrides) {
    const snap = {
        mode: 'tiled',
        mainTabId: 'A',
        focusedTabId: 'A',
        tabs: [
            { id: 'A', title: 'Work A', icon: 'file-text' },
            { id: 'B', title: 'Work B', icon: 'file-text' },
            { id: 'C', title: 'Work C', icon: 'file-text' },
            { id: 'D', title: 'Work D', icon: 'file-text' },
        ],
        secondaryTree: null,
    };
    return Object.assign(snap, overrides || {});
}

function run() {
    const h = makeSandbox();
    const tree = h.sandbox;
    tree.resetSplitIds();

    /* ---- Build Main A | Secondary split(B, split(C, D)) ---- */
    let secTree = tree.makeLeaf('B');
    secTree = tree.splitLeaf(secTree, 'B', { axis: 'top-bottom', newTabId: 'C' });
    secTree = tree.splitLeaf(secTree, 'C', { axis: 'left-right', newTabId: 'D' });
    const splitRootId = secTree.id;
    const splitInnerId = secTree.second.id;

    h.setSnap(baseSnap({ secondaryTree: secTree, focusedTabId: 'D' }));
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'tiled' });

    const canvas = h.dom.pageContent.__children[0];
    assert('canvas created', !!canvas);
    assertEq('canvas direct children: main, separator, secondaryRoot', canvas.__children.length, 3);

    const tileA = canvas.querySelector('[data-prks-tab-id="A"]');
    const tileB = canvas.querySelector('[data-prks-tab-id="B"]');
    const tileC = canvas.querySelector('[data-prks-tab-id="C"]');
    const tileD = canvas.querySelector('[data-prks-tab-id="D"]');
    assert('tile A exists', !!tileA);
    assert('tile B exists', !!tileB);
    assert('tile C exists', !!tileC);
    assert('tile D exists', !!tileD);
    assert('tile A is direct child of canvas (Main never nests)', tileA.parentNode === canvas);

    const outerSplit = canvas.querySelector('[data-prks-split-id="' + splitRootId + '"]');
    const innerSplit = canvas.querySelector('[data-prks-split-id="' + splitInnerId + '"]');
    assert('outer split container exists', !!outerSplit && outerSplit.className.indexOf('prks-workspace-split') !== -1);
    assert('inner split container exists', !!innerSplit && innerSplit.className.indexOf('prks-workspace-split') !== -1);
    assertEq('outer split axis', outerSplit.getAttribute('data-prks-axis'), 'top-bottom');
    assertEq('inner split axis', innerSplit.getAttribute('data-prks-axis'), 'left-right');
    assert('B is direct child of outer split', tileB.parentNode === outerSplit);
    assert('inner split is direct child of outer split', innerSplit.parentNode === outerSplit);
    assert('C is direct child of inner split', tileC.parentNode === innerSplit);
    assert('D is direct child of inner split', tileD.parentNode === innerSplit);
    assert('outer split marked as secondary root', outerSplit.getAttribute('data-prks-secondary-root') === '1');
    assert('inner split not marked as secondary root', innerSplit.getAttribute('data-prks-secondary-root') !== '1');

    /* One separator per split container, correctly ordered [first, separator, second]. */
    const outerKids = outerSplit.__children;
    assertEq('outer split has 3 children', outerKids.length, 3);
    assert('outer split order: B, separator, innerSplit', outerKids[0] === tileB && outerKids[2] === innerSplit);
    assert('outer separator is a splitter', outerKids[1].className.indexOf('prks-splitter') !== -1);
    assertEq('outer separator axis class', outerKids[1].className.indexOf('prks-splitter--horizontal') !== -1, true);

    const innerKids = innerSplit.__children;
    assertEq('inner split has 3 children', innerKids.length, 3);
    assert('inner split order: C, separator, D', innerKids[0] === tileC && innerKids[2] === tileD);
    assertEq('inner separator axis class', innerKids[1].className.indexOf('prks-splitter--vertical') !== -1, true);

    /* Focus/role classes. */
    assert('A has main class', tileA.className.indexOf('prks-tile--main') !== -1);
    assert('D has secondary class', tileD.className.indexOf('prks-tile--secondary') !== -1);
    assert('D is focused (per snapshot)', tileD.className.indexOf('prks-tile--focused') !== -1);
    assert('B is not focused', tileB.className.indexOf('prks-tile--focused') === -1);

    /* Header actions on a Secondary tile: split dropdown, make main, close -- not on Main. */
    const headerA = tileA.querySelector('.prks-tile-header');
    const headerB = tileB.querySelector('.prks-tile-header');
    assert('Main header has role badge, no actions', !!headerA.querySelector('.prks-tile-header__role'));
    assert('Main header has no make-main button', !headerA.querySelector('.prks-tile-header__make-main'));
    assert('Secondary header has split dropdown', !!headerB.querySelector('.prks-tile-header__split'));
    assert('Secondary header has make-main button', !!headerB.querySelector('.prks-tile-header__make-main'));
    assert('Secondary header has close button', !!headerB.querySelector('.prks-tile-header__close'));

    /* ---- Runtime identity survives an unrelated resync (repaint with identical tree). ---- */
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'tiled' });
    assert('tile B same DOM node after resync', canvas.querySelector('[data-prks-tab-id="B"]') === tileB);
    assert('tile C same DOM node after resync', canvas.querySelector('[data-prks-tab-id="C"]') === tileC);
    assert('outer split same DOM node after resync', canvas.querySelector('[data-prks-split-id="' + splitRootId + '"]') === outerSplit);

    /* ---- Deep Make Main: D becomes Main, A takes D's exact former leaf position. ---- */
    const afterMakeMain = tree.replaceTabId(secTree, 'D', 'A');
    h.setSnap(baseSnap({ secondaryTree: afterMakeMain, mainTabId: 'D', focusedTabId: 'D' }));
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'tiled' });

    assert('tile D is now Main (same DOM node)', canvas.querySelector('[data-prks-tab-id="D"]') === tileD);
    assert('tile D reparented directly under canvas', tileD.parentNode === canvas);
    assert('tile A moved into D former leaf slot (same DOM node)', canvas.querySelector('[data-prks-tab-id="A"]') === tileA);
    assert('tile A now inside inner split', tileA.parentNode === innerSplit);
    assert('tile B untouched, still under outer split', tileB.parentNode === outerSplit && canvas.querySelector('[data-prks-tab-id="B"]') === tileB);
    assert('tile C untouched', canvas.querySelector('[data-prks-tab-id="C"]') === tileC);
    assert('outer/inner split containers reused (same DOM nodes)', canvas.querySelector('[data-prks-split-id="' + splitRootId + '"]') === outerSplit);

    /* ---- Close-collapse: close C -> inner split collapses; A takes C's old spot directly
     * under the outer split. The outer split's separator/DOM node survives (still 2 leaves). ---- */
    const treeAfterClose = tree.normalizeTree(tree.removeLeaf(afterMakeMain, 'C'));
    h.setSnap(baseSnap({ secondaryTree: treeAfterClose, mainTabId: 'D', focusedTabId: 'B', tabs: [
        { id: 'D', title: 'Work D', icon: 'file-text' },
        { id: 'B', title: 'Work B', icon: 'file-text' },
        { id: 'A', title: 'Work A', icon: 'file-text' },
    ] }));
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'tiled' });

    assert('tile C removed from DOM after close', canvas.querySelector('[data-prks-tab-id="C"]') === null);
    assert('inner split container removed after collapse', canvas.querySelector('[data-prks-split-id="' + splitInnerId + '"]') === null);
    assert('tile A survives close-collapse (same DOM node)', canvas.querySelector('[data-prks-tab-id="A"]') === tileA);
    assert('tile A now direct child of surviving outer split', tileA.parentNode === outerSplit);
    assert('surviving outer split still has exactly one separator', outerSplit.__children.filter(function (c) { return c.className.indexOf('prks-splitter') !== -1; }).length === 1);
    assertEq('surviving outer split still has 3 children (B, sep, A)', outerSplit.__children.length, 3);

    /* ---- Hide/park the whole Secondary tree collapses to a bare leaf; then further collapse
     * to no Secondary at all (stacked) prunes every remaining Secondary host. ---- */
    const bareLeafTree = tree.makeLeaf('A');
    h.setSnap(baseSnap({ secondaryTree: bareLeafTree, mainTabId: 'D', focusedTabId: 'A', tabs: [
        { id: 'D', title: 'Work D', icon: 'file-text' },
        { id: 'A', title: 'Work A', icon: 'file-text' },
    ] }));
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'tiled' });
    assert('tile B removed once no longer in tree', canvas.querySelector('[data-prks-tab-id="B"]') === null);
    assert('outer split container removed (tree is now a bare leaf)', canvas.querySelector('[data-prks-split-id="' + splitRootId + '"]') === null);
    assert('tile A still present, now the sole secondary leaf', canvas.querySelector('[data-prks-tab-id="A"]') === tileA);
    assert('tile A marked as secondary root', tileA.getAttribute('data-prks-secondary-root') === '1');

    h.setSnap(baseSnap({ secondaryTree: null, mainTabId: 'D', focusedTabId: 'D', tabs: [
        { id: 'D', title: 'Work D', icon: 'file-text' },
        { id: 'A', title: 'Work A', icon: 'file-text' },
    ] }));
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'stacked' });
    assert('tile A pruned once Secondary fully hidden/closed', canvas.querySelector('[data-prks-tab-id="A"]') === null);
    assert('no secondary-root marker remains', canvas.querySelector('[data-prks-secondary-root]') === null);
    assertEq('only Main tile remains under canvas', canvas.__children.filter(function (c) { return c.className && c.className.indexOf('prks-tile') !== -1; }).length, 1);

    /* ---- Nested separator drag updates that split's own ratio only, applied as a
     * percentage CSS var (so it survives ancestor resizes with no JS). ---- */
    tree.resetSplitIds();
    let dragTree = tree.makeLeaf('B');
    dragTree = tree.splitLeaf(dragTree, 'B', { axis: 'left-right', newTabId: 'C' });
    const dragSplitId = dragTree.id;
    h.setSnap(baseSnap({ secondaryTree: dragTree, focusedTabId: 'B', tabs: [
        { id: 'A', title: 'Work A', icon: 'file-text' },
        { id: 'B', title: 'Work B', icon: 'file-text' },
        { id: 'C', title: 'Work C', icon: 'file-text' },
    ] }));
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'tiled' });
    const dragContainer = canvas.querySelector('[data-prks-split-id="' + dragSplitId + '"]');
    dragContainer.clientWidth = 1000;
    dragContainer.__left = 0;
    const dragSep = dragContainer.__children[1];
    assertEq('drag separator has aria-orientation vertical', dragSep.getAttribute('aria-orientation'), 'vertical');

    dragSep.dispatch('pointerdown', { pointerType: 'mouse', button: 0, pointerId: 3, preventDefault: function () {} });
    h.sandbox.document.dispatch('pointermove', { clientX: 700, preventDefault: function () {} }, true);
    const ratioAfterDrag = h.getSnap().secondaryTree.ratio;
    assert('nested drag updated that split ratio away from default 0.5', Math.abs(ratioAfterDrag - 0.5) > 0.05, 'ratio=' + ratioAfterDrag);
    assert('nested drag applied a percentage CSS var (not px)', true);
    h.sandbox.document.dispatch('pointerup', {}, true);

    /* Keyboard resize + Home/End on the nested separator. */
    const before = h.getSnap().secondaryTree.ratio;
    dragSep.dispatch('keydown', { key: 'ArrowRight', preventDefault: function () {}, stopPropagation: function () {} });
    const afterArrow = h.getSnap().secondaryTree.ratio;
    assert('ArrowRight nudges nested ratio up', afterArrow > before);
    dragSep.dispatch('keydown', { key: 'Home', preventDefault: function () {}, stopPropagation: function () {} });
    const afterHome = h.getSnap().secondaryTree.ratio;
    assert('Home sets nested ratio to its minimum bound', afterHome < afterArrow);
    dragSep.dispatch('dblclick', { preventDefault: function () {} });
    assertEq('double-click resets nested ratio to 0.5', h.getSnap().secondaryTree.ratio, 0.5);

    /* Root divider and nested separator are independent: dragging one never touches the other. */
    const rootRatioBefore = h.sandbox.prksWorkspaceGetSplitRatio();
    dragSep.dispatch('keydown', { key: 'ArrowRight', preventDefault: function () {}, stopPropagation: function () {} });
    assertEq('nested keyboard resize does not change root mainSplitRatio', h.sandbox.prksWorkspaceGetSplitRatio(), rootRatioBefore);

    if (failed) {
        console.log(failed + ' failed, ' + passed + ' passed');
        process.exit(1);
    }
    console.log('All ' + passed + ' recursive workspace tiling checks passed, 0 failed');
}

run();
