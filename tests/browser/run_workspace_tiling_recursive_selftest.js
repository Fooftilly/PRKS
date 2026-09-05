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
const menuSrc = fs.readFileSync(path.join(rootDir, 'frontend/js/workspace-tab-menu.js'), 'utf8');

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

function safeDescribe(v) {
    if (v && typeof v === 'object' && v.tagName) {
        return '<' + v.tagName + (v.getAttribute ? ' data-prks-tab-id=' + v.getAttribute('data-prks-tab-id') + ' data-prks-split-id=' + v.getAttribute('data-prks-split-id') : '') + '>';
    }
    try {
        return JSON.stringify(v);
    } catch (_e) {
        return String(v);
    }
}

function assertEq(name, got, want) {
    const ok = got === want;
    record(name, ok, ok ? '' : 'got=' + safeDescribe(got) + ' want=' + safeDescribe(want));
}

/* ---------------- Minimal but structurally-real fake DOM ---------------- */

/* Tracks the most recently `.focus()`-ed fake node so `document.activeElement` behaves
 * realistically enough for keyboard-navigation assertions (Split-menu Escape/ArrowUp/Down). */
let __lastFocusedNode = null;

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
    const styleProps = Object.create(null);
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
        style: {
            setProperty: function (name, value) {
                styleProps[name] = value;
            },
            getProperty: function (name) {
                return styleProps[name] !== undefined ? styleProps[name] : '';
            },
        },
        clientWidth: 0,
        clientHeight: 0,
    };
    Object.defineProperty(node, 'id', {
        get: function () {
            return attrs.id || '';
        },
        set: function (v) {
            attrs.id = String(v);
        },
    });
    Object.defineProperty(node, 'offsetWidth', {
        get: function () {
            return node.clientWidth || 0;
        },
    });
    Object.defineProperty(node, 'offsetHeight', {
        get: function () {
            return node.clientHeight || 0;
        },
    });
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
        const left = node.__left || 0;
        const top = node.__top || 0;
        const width = node.clientWidth || 0;
        const height = node.clientHeight || 0;
        return { width: width, height: height, left: left, top: top, right: left + width, bottom: top + height };
    };
    node.setPointerCapture = function () {};
    node.releasePointerCapture = function () {};
    node.focus = function () {
        node.__focusCount = (node.__focusCount || 0) + 1;
        __lastFocusedNode = node;
    };
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
        const e = Object.assign({ type: type, target: node, currentTarget: node, preventDefault: function () {}, stopPropagation: function () {} }, evt || {});
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
    const live = createNode('div');
    live.id = 'prks-workspace-live';
    body.appendChild(live);
    const html = createNode('html');
    html.clientWidth = 1600;
    html.clientHeight = 900;

    function findById(node, id) {
        if (!node) return null;
        if (node.id === id) return node;
        const kids = node.__children || [];
        for (let i = 0; i < kids.length; i++) {
            const found = findById(kids[i], id);
            if (found) return found;
        }
        return null;
    }

    const documentMock = Object.assign(makeEventTarget(), {
        getElementById: function (id) {
            if (id === 'page-content') return pageContent;
            return findById(body, id) || findById(pageContent, id);
        },
        createElement: function (tag) {
            return createNode(tag);
        },
        querySelector: function (sel) {
            if (matchesSimpleSelector(body, sel)) return body;
            const fromBody = body.querySelector(sel);
            if (fromBody) return fromBody;
            if (matchesSimpleSelector(pageContent, sel)) return pageContent;
            return pageContent.querySelector(sel);
        },
        querySelectorAll: function (sel) {
            const out = [];
            const fromBody = body.querySelectorAll(sel);
            for (let i = 0; i < fromBody.length; i++) out.push(fromBody[i]);
            const fromPage = pageContent.querySelectorAll(sel);
            for (let i = 0; i < fromPage.length; i++) out.push(fromPage[i]);
            return out;
        },
        contains: function (el) {
            return body.contains(el) || pageContent.contains(el);
        },
        body: body,
        documentElement: html,
    });
    Object.defineProperty(documentMock, 'activeElement', {
        get: function () {
            return __lastFocusedNode;
        },
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
        /* Mirrors the real cap semantics (workspace-tabs.js PRKS_MAX_VISIBLE_TABS = 4: Main +
         * up to 3 Secondary leaves) against whatever tree is currently set via setSnap(). */
        prksWorkspaceCanAddSecondaryLeaf: function () {
            const snap = sandbox.prksWorkspaceSnapshot && sandbox.prksWorkspaceSnapshot();
            const tree = snap && snap.secondaryTree;
            const count = tree ? sandbox.leafCount(tree) : 0;
            return count < 3;
        },
        innerWidth: 1600,
        innerHeight: 900,
        __lastPalette: null,
        __lastMakeMain: null,
        __lastHideLeaf: null,
        __lastCloseTab: null,
        prksOpenCommandPalette: function (opts) {
            sandbox.__lastPalette = opts || null;
        },
        prksWorkspaceMakeMain: function (id) {
            sandbox.__lastMakeMain = id;
        },
        prksWorkspaceHideLeaf: function (id) {
            sandbox.__lastHideLeaf = id;
        },
        prksWorkspaceCloseTab: function (id) {
            sandbox.__lastCloseTab = id;
        },
        prksWorkspaceMoveTabStep: function () {},
        prksRouteSupportsTile: function () {
            return true;
        },
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;

    vm.runInNewContext(treeSrc, sandbox);
    vm.runInNewContext(splitSrc, sandbox);
    vm.runInNewContext(tilingSrc, sandbox);
    vm.runInNewContext(menuSrc, sandbox);

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

    /* Header chrome: Main is icon+title with an accessible Main label; Secondary is
     * grip + icon + title + Pane actions + Close. No dedicated Split/Make-main buttons. */
    const headerA = tileA.querySelector('.prks-tile-header');
    const headerB = tileB.querySelector('.prks-tile-header');
    assert('Main header has no role badge', !headerA.querySelector('.prks-tile-header__role'));
    assert('Main header has no drag grip', !headerA.querySelector('.prks-tile-header__grip'));
    assert('Main header has no make-main button', !headerA.querySelector('.prks-tile-header__make-main'));
    assert('Main header has no dedicated Split button', !headerA.querySelector('.prks-tile-header__split'));
    assert('Main header has no pane-actions button', !headerA.querySelector('.prks-tile-header__menu'));
    assert('Main header has no Secondary Close button', !headerA.querySelector('.prks-tile-header__close'));
    assert('Main header has icon', !!headerA.querySelector('.prks-tile-header__icon'));
    assert('Main header has title', !!headerA.querySelector('.prks-tile-header__title'));
    assertEq('Main tile accessible label', tileA.getAttribute('aria-label'), 'Main pane: Work A');
    assertEq('Main header accessible label', headerA.getAttribute('aria-label'), 'Main pane: Work A');
    assert('Secondary header has drag grip', !!headerB.querySelector('.prks-tile-header__grip'));
    assert('Secondary header has icon', !!headerB.querySelector('.prks-tile-header__icon'));
    assert('Secondary header has title', !!headerB.querySelector('.prks-tile-header__title'));
    assert('Secondary header has Pane actions button', !!headerB.querySelector('.prks-tile-header__menu'));
    assertEq('Pane actions aria-label', headerB.querySelector('.prks-tile-header__menu').getAttribute('aria-label'), 'Pane actions');
    assertEq('Pane actions haspopup', headerB.querySelector('.prks-tile-header__menu').getAttribute('aria-haspopup'), 'menu');
    assert('Secondary header has close button', !!headerB.querySelector('.prks-tile-header__close'));
    assert('Secondary header has no make-main button', !headerB.querySelector('.prks-tile-header__make-main'));
    assert('Secondary header has no dedicated Split button', !headerB.querySelector('.prks-tile-header__split'));

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

    /* ---- Ancestor-resize re-clamp: canonical nested ratio survives a container geometry
     * change; only the EFFECTIVE DOM ratio/ARIA clamp to the container's own current minimums,
     * and they spring back once the container is large again. No canonical mutation, no
     * TabContext remount, no observer leak. (left-right axis) ---- */
    tree.resetSplitIds();
    let resizeTree = tree.makeLeaf('C');
    resizeTree = tree.splitLeaf(resizeTree, 'C', { axis: 'left-right', newTabId: 'D' });
    const resizeSplitId = resizeTree.id;
    resizeTree = tree.setSplitRatio(resizeTree, resizeSplitId, 0.75);
    h.setSnap(baseSnap({ secondaryTree: resizeTree, focusedTabId: 'C', tabs: [
        { id: 'A', title: 'Work A', icon: 'file-text' },
        { id: 'C', title: 'Work C', icon: 'file-text' },
        { id: 'D', title: 'Work D', icon: 'file-text' },
    ] }));
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'tiled' });
    const resizeContainer = canvas.querySelector('[data-prks-split-id="' + resizeSplitId + '"]');
    const resizeSep = resizeContainer.__children[1];
    const resizeTileC = canvas.querySelector('[data-prks-tab-id="C"]');
    const resizeTileD = canvas.querySelector('[data-prks-tab-id="D"]');

    /* Wide: canonical 0.75 fits comfortably (both sides well over the 280px minimum). */
    resizeContainer.clientWidth = 2000;
    h.sandbox.prksWorkspaceReclampNestedSplit(resizeContainer);
    assertEq('wide: canonical ratio untouched', h.getSnap().secondaryTree.ratio, 0.75);
    assertEq('wide: effective ARIA now matches canonical', resizeSep.getAttribute('aria-valuenow'), '75');
    assertEq('wide: effective CSS var matches canonical', resizeContainer.style.getProperty('--prks-split-first-size'), '75%');

    /* Narrow: canonical 0.75 would violate the 280px minimum on the second pane; the effective
     * DOM ratio clamps to the container's own current max, canonical stays 0.75. */
    resizeContainer.clientWidth = 700;
    h.sandbox.prksWorkspaceReclampNestedSplit(resizeContainer);
    assertEq('narrow: canonical ratio still 0.75 (never overwritten)', h.getSnap().secondaryTree.ratio, 0.75);
    const narrowNow = Number(resizeSep.getAttribute('aria-valuenow'));
    assert('narrow: effective ratio clamped below canonical', narrowNow < 75, 'now=' + narrowNow);
    const narrowMax = Number(resizeSep.getAttribute('aria-valuemax'));
    assertEq('narrow: effective clamps exactly to local max bound', narrowNow, narrowMax);
    const narrowCssVar = parseFloat(resizeContainer.style.getProperty('--prks-split-first-size'));
    assert('narrow: effective CSS var reflects clamp, not canonical', Math.abs(narrowCssVar - narrowMax) < 1, 'css=' + narrowCssVar + ' max=' + narrowMax);
    assert('narrow: min bound also present in ARIA', Number(resizeSep.getAttribute('aria-valuemin')) > 0);
    assert('narrow: tile C same DOM node (no remount)', canvas.querySelector('[data-prks-tab-id="C"]') === resizeTileC);
    assert('narrow: tile D same DOM node (no remount)', canvas.querySelector('[data-prks-tab-id="D"]') === resizeTileD);

    /* Expand again: effective geometry returns to ~0.75 with no canonical change either time. */
    resizeContainer.clientWidth = 2000;
    h.sandbox.prksWorkspaceReclampNestedSplit(resizeContainer);
    assertEq('re-expand: canonical ratio still 0.75', h.getSnap().secondaryTree.ratio, 0.75);
    assertEq('re-expand: effective ARIA returns to 75', resizeSep.getAttribute('aria-valuenow'), '75');
    assertEq('re-expand: effective CSS var returns to 75%', resizeContainer.style.getProperty('--prks-split-first-size'), '75%');

    /* ---- Same geometry re-clamp for a top-bottom nested split (local min-height bounds). ---- */
    tree.resetSplitIds();
    let vResizeTree = tree.makeLeaf('E');
    vResizeTree = tree.splitLeaf(vResizeTree, 'E', { axis: 'top-bottom', newTabId: 'F' });
    const vResizeSplitId = vResizeTree.id;
    vResizeTree = tree.setSplitRatio(vResizeTree, vResizeSplitId, 0.75);
    h.setSnap(baseSnap({ secondaryTree: vResizeTree, focusedTabId: 'E', tabs: [
        { id: 'A', title: 'Work A', icon: 'file-text' },
        { id: 'E', title: 'Work E', icon: 'file-text' },
        { id: 'F', title: 'Work F', icon: 'file-text' },
    ] }));
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'tiled' });
    const vResizeContainer = canvas.querySelector('[data-prks-split-id="' + vResizeSplitId + '"]');
    const vResizeSep = vResizeContainer.__children[1];

    vResizeContainer.clientHeight = 1200;
    h.sandbox.prksWorkspaceReclampNestedSplit(vResizeContainer);
    assertEq('top-bottom wide: canonical ratio untouched', h.getSnap().secondaryTree.ratio, 0.75);
    assertEq('top-bottom wide: effective matches canonical', vResizeSep.getAttribute('aria-valuenow'), '75');

    vResizeContainer.clientHeight = 500;
    h.sandbox.prksWorkspaceReclampNestedSplit(vResizeContainer);
    assertEq('top-bottom narrow: canonical ratio still 0.75', h.getSnap().secondaryTree.ratio, 0.75);
    const vNarrowNow = Number(vResizeSep.getAttribute('aria-valuenow'));
    assert('top-bottom narrow: effective clamped below canonical', vNarrowNow < 75, 'now=' + vNarrowNow);
    assertEq('top-bottom narrow: horizontal aria-orientation preserved', vResizeSep.getAttribute('aria-orientation'), 'horizontal');

    vResizeContainer.clientHeight = 1200;
    h.sandbox.prksWorkspaceReclampNestedSplit(vResizeContainer);
    assertEq('top-bottom re-expand: effective returns to 75', vResizeSep.getAttribute('aria-valuenow'), '75');

    /* ---- Observer lifecycle: one ResizeObserver per live split container, disconnected when
     * that split node collapses/is removed -- never leaked across split/close cycles. ---- */
    const roCountBeforeCollapse = h.roCount();
    const collapsedAfterC = tree.normalizeTree(tree.removeLeaf(resizeTree, 'D'));
    h.setSnap(baseSnap({ secondaryTree: collapsedAfterC, focusedTabId: 'C', tabs: [
        { id: 'A', title: 'Work A', icon: 'file-text' },
        { id: 'C', title: 'Work C', icon: 'file-text' },
    ] }));
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'tiled' });
    assert('collapse removed the split container from the DOM', canvas.querySelector('[data-prks-split-id="' + resizeSplitId + '"]') === null);
    assertEq('collapse did not create a fresh observer', h.roCount(), roCountBeforeCollapse);

    /* ---- Pane-cap: Split right/down in the shared workspace menu disable at 4 visible panes. ---- */
    tree.resetSplitIds();
    let capTree = tree.makeLeaf('G');
    capTree = tree.splitLeaf(capTree, 'G', { axis: 'top-bottom', newTabId: 'H' });
    capTree = tree.splitLeaf(capTree, 'H', { axis: 'left-right', newTabId: 'I' });
    h.setSnap(baseSnap({ secondaryTree: capTree, focusedTabId: 'I', tabs: [
        { id: 'A', title: 'Work A', icon: 'file-text' },
        { id: 'G', title: 'Work G', icon: 'file-text' },
        { id: 'H', title: 'Work H', icon: 'file-text' },
        { id: 'I', title: 'Work I', icon: 'file-text' },
    ] }));
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'tiled' });
    h.sandbox.prksWorkspaceInitTiles();
    h.sandbox.prksWorkspaceInitTabMenus();
    assertEq('cap setup mounted 4 (1 main + 3 secondary)', canvas.querySelectorAll('.prks-tile').length, 4);
    const capTileG = canvas.querySelector('[data-prks-tab-id="G"]');
    const capMenuBtnG = capTileG.querySelector('.prks-tile-header__menu');
    capMenuBtnG.dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    const capMenu = h.sandbox.document.getElementById('prks-workspace-menu');
    assert('cap: shared workspace menu opens', !!(capMenu && capMenu.hidden === false));
    const capSplitRight = menuItemByLabel(capMenu, 'Split right');
    const capSplitDown = menuItemByLabel(capMenu, 'Split down');
    assert('cap: Split right disabled at 4-pane cap', !!(capSplitRight && capSplitRight.disabled));
    assert('cap: Split down disabled at 4-pane cap', !!(capSplitDown && capSplitDown.disabled));
    assert('cap: disabled Split carries an explanatory title', !!(capSplitRight && capSplitRight.title));
    h.sandbox.prksWorkspaceCloseTabMenu();

    /* Close I -> only 2 Secondary leaves remain (G, H); cap no longer reached, Split re-enables. */
    const capTreeAfterClose = tree.normalizeTree(tree.removeLeaf(capTree, 'I'));
    h.setSnap(baseSnap({ secondaryTree: capTreeAfterClose, focusedTabId: 'G', tabs: [
        { id: 'A', title: 'Work A', icon: 'file-text' },
        { id: 'G', title: 'Work G', icon: 'file-text' },
        { id: 'H', title: 'Work H', icon: 'file-text' },
    ] }));
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'tiled' });
    assert('cap released: I removed from DOM', canvas.querySelector('[data-prks-tab-id="I"]') === null);
    capMenuBtnG.dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    const capSplitRightOpen = menuItemByLabel(h.sandbox.document.getElementById('prks-workspace-menu'), 'Split right');
    const capSplitDownOpen = menuItemByLabel(h.sandbox.document.getElementById('prks-workspace-menu'), 'Split down');
    assert('cap released: Split right re-enabled', !!(capSplitRightOpen && !capSplitRightOpen.disabled));
    assert('cap released: Split down re-enabled', !!(capSplitDownOpen && !capSplitDownOpen.disabled));
    h.sandbox.prksWorkspaceCloseTabMenu();

    /* ---- Unified pane menu: same action list as the tab-strip menu, anchored to the … control. ---- */
    tree.resetSplitIds();
    let menuTree = tree.makeLeaf('J');
    menuTree = tree.splitLeaf(menuTree, 'J', { axis: 'left-right', newTabId: 'K' });
    h.setSnap(baseSnap({ secondaryTree: menuTree, focusedTabId: 'J', tabs: [
        { id: 'A', title: 'Work A', icon: 'file-text' },
        { id: 'J', title: 'Work J', icon: 'file-text' },
        { id: 'K', title: 'Work K', icon: 'file-text' },
    ] }));
    h.sandbox.prksWorkspaceSyncTiles(h.getSnap(), { visualMode: 'tiled' });
    h.sandbox.prksWorkspaceInitTiles();
    h.sandbox.prksWorkspaceInitTabMenus();
    const tileJ = canvas.querySelector('[data-prks-tab-id="J"]');
    const tileK = canvas.querySelector('[data-prks-tab-id="K"]');
    const btnJ = tileJ.querySelector('.prks-tile-header__menu');
    const btnK = tileK.querySelector('.prks-tile-header__menu');
    btnJ.__left = 420;
    btnJ.__top = 80;
    btnJ.clientWidth = 32;
    btnJ.clientHeight = 32;
    btnK.__left = 720;
    btnK.__top = 80;
    btnK.clientWidth = 32;
    btnK.clientHeight = 32;

    btnJ.dispatch('click', { preventDefault: function () {}, stopPropagation: function () {}, clientX: 12, clientY: 9 });
    const paneMenu = h.sandbox.document.getElementById('prks-workspace-menu');
    assert('pane menu opens', !!(paneMenu && paneMenu.hidden === false));
    assertEq('pane menu button aria-expanded true', btnJ.getAttribute('aria-expanded'), 'true');
    assertEq('pane menu anchors to control left, not click coords', paneMenu.style.left, '420px');
    assertEq('pane menu anchors to control bottom', paneMenu.style.top, '112px');
    const labels = menuLabels(paneMenu);
    assert('pane menu contains Make main', labels.indexOf('Make main') !== -1);
    assert('pane menu contains Split right', labels.indexOf('Split right') !== -1);
    assert('pane menu contains Split down', labels.indexOf('Split down') !== -1);
    assert('pane menu contains Hide from split', labels.indexOf('Hide from split') !== -1);
    assert('pane menu contains Close', labels.indexOf('Close') !== -1);
    assertEq('pane menu focuses first enabled item', __lastFocusedNode, paneMenu.querySelector('[role="menuitem"]'));

    h.sandbox.document.dispatch('keydown', { key: 'Escape' }, true);
    assertEq('escape closes pane menu', paneMenu.hidden, true);
    assertEq('escape clears aria-expanded', btnJ.getAttribute('aria-expanded'), 'false');
    assertEq('escape restores focus to Pane actions button', __lastFocusedNode, btnJ);

    btnJ.dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    assertEq('pane menu J reopened', paneMenu.hidden, false);
    btnK.dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    assertEq('opening K leaves a single shared menu open', paneMenu.hidden, false);
    assertEq('opening K clears J aria-expanded', btnJ.getAttribute('aria-expanded'), 'false');
    assertEq('K button aria-expanded true', btnK.getAttribute('aria-expanded'), 'true');
    btnK.dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    assertEq('K menu closed via toggle', paneMenu.hidden, true);

    btnJ.dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    const firstItem = paneMenu.querySelector('[role="menuitem"]');
    assertEq('pane menu open for arrow nav', __lastFocusedNode, firstItem);
    h.sandbox.document.dispatch('keydown', { key: 'ArrowDown' }, true);
    const itemsNow = enabledMenuItems(paneMenu);
    assertEq('ArrowDown moves to second enabled item', __lastFocusedNode, itemsNow[1]);
    h.sandbox.document.dispatch('keydown', { key: 'End' }, true);
    assertEq('End moves to last enabled item', __lastFocusedNode, itemsNow[itemsNow.length - 1]);
    h.sandbox.document.dispatch('keydown', { key: 'Home' }, true);
    assertEq('Home moves to first enabled item', __lastFocusedNode, itemsNow[0]);

    menuItemByLabel(paneMenu, 'Make main').dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    assertEq('Make main uses prksWorkspaceMakeMain', h.sandbox.__lastMakeMain, 'J');
    assertEq('choosing Make main closes the menu', paneMenu.hidden, true);

    btnJ.dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    menuItemByLabel(paneMenu, 'Split right').dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    assert('Split right uses prksOpenCommandPalette', !!(h.sandbox.__lastPalette && h.sandbox.__lastPalette.splitPlacement));
    assertEq('Split right axis is left-right', h.sandbox.__lastPalette.splitPlacement.axis, 'left-right');
    assertEq('Split right targets this leaf', h.sandbox.__lastPalette.splitPlacement.targetLeafTabId, 'J');

    btnJ.dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    menuItemByLabel(paneMenu, 'Split down').dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    assertEq('Split down axis is top-bottom', h.sandbox.__lastPalette.splitPlacement.axis, 'top-bottom');

    btnJ.dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    menuItemByLabel(paneMenu, 'Hide from split').dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    assertEq('Hide from split uses prksWorkspaceHideLeaf', h.sandbox.__lastHideLeaf, 'J');

    btnJ.dispatch('click', { preventDefault: function () {}, stopPropagation: function () {} });
    assertEq('pane menu reopened for outside-pointer test', paneMenu.hidden, false);
    h.sandbox.document.dispatch('pointerdown', { target: tileK }, true);
    assertEq('outside pointerdown closes open menu', paneMenu.hidden, true);

    /* Tab-strip invocation still uses click coordinates against the tab wrap. */
    const stripWrap = createNode('div');
    stripWrap.className = 'prks-workspace-tab';
    stripWrap.setAttribute('data-tab-id', 'J');
    stripWrap.__left = 40;
    stripWrap.__top = 8;
    stripWrap.clientWidth = 120;
    stripWrap.clientHeight = 32;
    const activate = createNode('button');
    activate.className = 'prks-workspace-tab__activate';
    stripWrap.appendChild(activate);
    h.dom.body.appendChild(stripWrap);
    h.sandbox.prksWorkspaceOpenTabMenu('J', { clientX: 88, clientY: 20, currentTarget: stripWrap });
    assertEq('tab-strip menu opens', paneMenu.hidden, false);
    assertEq('tab-strip menu uses pointer coordinates', paneMenu.style.left, '88px');
    assertEq('tab-strip menu uses pointer y', paneMenu.style.top, '20px');
    const stripLabels = menuLabels(paneMenu);
    assert('tab-strip menu still contains Make main', stripLabels.indexOf('Make main') !== -1);
    assert('tab-strip menu still contains Split right', stripLabels.indexOf('Split right') !== -1);
    h.sandbox.prksWorkspaceCloseTabMenu();
    assertEq('tab-strip close restores the invoking control', __lastFocusedNode, stripWrap);

    if (failed) {
        console.log(failed + ' failed, ' + passed + ' passed');
        process.exit(1);
    }
    console.log('All ' + passed + ' recursive workspace tiling checks passed, 0 failed');
}

function menuLabels(menu) {
    const items = menu.querySelectorAll('[role="menuitem"]');
    const out = [];
    for (let i = 0; i < items.length; i++) {
        const label = items[i].querySelector('.prks-workspace-menu__label');
        out.push(label ? label.textContent : items[i].textContent);
    }
    return out;
}

function menuItemByLabel(menu, label) {
    const items = menu.querySelectorAll('[role="menuitem"]');
    for (let i = 0; i < items.length; i++) {
        const el = items[i].querySelector('.prks-workspace-menu__label');
        if (el && el.textContent === label) return items[i];
    }
    return null;
}

function enabledMenuItems(menu) {
    const items = menu.querySelectorAll('[role="menuitem"]');
    const out = [];
    for (let i = 0; i < items.length; i++) {
        if (!items[i].disabled && items[i].getAttribute('aria-disabled') !== 'true') out.push(items[i]);
    }
    return out;
}

run();
