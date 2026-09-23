#!/usr/bin/env node
'use strict';

/**
 * Deterministic same-route Folder hierarchy refresh ownership (#161).
 *
 * Route AbortSignal / ctx.generation cover A→B→C remounts. Overlapping live
 * full refills for one mounted Folder context need a separate refresh token
 * so an older async result cannot overwrite a newer committed tree.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '../..');

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (ok ? '' : ' ' + (detail || '')));
}

function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : detail || '');
}

function assertEq(name, got, want) {
    const ok = got === want;
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function makeDom() {
    const byId = Object.create(null);

    function classListFor(el) {
        const set = new Set(
            String(el.className || '')
                .split(/\s+/)
                .filter(Boolean)
        );
        return {
            add(c) {
                set.add(c);
                el.className = Array.from(set).join(' ');
            },
            remove(c) {
                set.delete(c);
                el.className = Array.from(set).join(' ');
            },
            toggle(c, force) {
                if (force === true) set.add(c);
                else if (force === false) set.delete(c);
                else if (set.has(c)) set.delete(c);
                else set.add(c);
                el.className = Array.from(set).join(' ');
            },
            contains(c) {
                return set.has(c);
            },
        };
    }

    function matches(el, sel) {
        if (!sel || !el) return false;
        const attrSuffix = sel.match(/^([^\[\]]+)(\[[^\]]+\])$/);
        if (attrSuffix) {
            return matches(el, attrSuffix[1]) && matches(el, attrSuffix[2]);
        }
        if (sel.charAt(0) === '#') return el.id === sel.slice(1);
        if (sel.charAt(0) === '.') {
            return sel
                .slice(1)
                .split('.')
                .filter(Boolean)
                .every((c) => el.classList.contains(c));
        }
        const attr = sel.match(/^\[([^=\]]+)(?:=\"([^\"]*)\")?\]$/);
        if (attr) {
            const v = el.getAttribute(attr[1]);
            if (attr[2] === undefined) return v != null;
            return v === attr[2];
        }
        if (sel.indexOf('.') !== -1) {
            const [tag, ...classes] = sel.split('.');
            if (el.tagName.toLowerCase() !== tag.toLowerCase()) return false;
            return classes.every((c) => el.classList.contains(c));
        }
        return el.tagName.toLowerCase() === sel.toLowerCase();
    }

    function createElement(tag) {
        const attrs = Object.create(null);
        const children = [];
        let idValue = '';
        let html = '';
        const el = {
            tagName: String(tag).toUpperCase(),
            nodeType: 1,
            className: '',
            children,
            childNodes: children,
            parentNode: null,
            get id() {
                return idValue;
            },
            set id(v) {
                const s = String(v == null ? '' : v);
                if (idValue && byId[idValue] === el) delete byId[idValue];
                idValue = s;
                if (s) byId[s] = el;
            },
            get classList() {
                return classListFor(el);
            },
            getAttribute(k) {
                if (k === 'id') return idValue || null;
                if (k === 'class') return el.className || null;
                return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null;
            },
            setAttribute(k, v) {
                const s = String(v);
                if (k === 'id') el.id = s;
                else if (k === 'class') el.className = s;
                else attrs[k] = s;
            },
            removeAttribute(k) {
                if (k === 'id') el.id = '';
                else delete attrs[k];
            },
            appendChild(child) {
                children.push(child);
                child.parentNode = el;
                return child;
            },
            removeChild(child) {
                const i = children.indexOf(child);
                if (i >= 0) children.splice(i, 1);
                child.parentNode = null;
                return child;
            },
            querySelector(sel) {
                const walk = (node) => {
                    for (const c of node.children || []) {
                        if (matches(c, sel)) return c;
                        const hit = walk(c);
                        if (hit) return hit;
                    }
                    return null;
                };
                return walk(el);
            },
            querySelectorAll(sel) {
                const out = [];
                const walk = (node) => {
                    for (const c of node.children || []) {
                        if (matches(c, sel)) out.push(c);
                        walk(c);
                    }
                };
                walk(el);
                return out;
            },
            contains(other) {
                let n = other;
                while (n) {
                    if (n === el) return true;
                    n = n.parentNode;
                }
                return false;
            },
            get isConnected() {
                let n = el;
                while (n) {
                    if (n === body || n === documentObj) return true;
                    n = n.parentNode;
                }
                return false;
            },
            get dataset() {
                return new Proxy(
                    {},
                    {
                        get(_t, prop) {
                            const attr =
                                'data-' +
                                String(prop).replace(/[A-Z]/g, (m) => '-' + m.toLowerCase());
                            return attrs[attr];
                        },
                        set(_t, prop, v) {
                            const attr =
                                'data-' +
                                String(prop).replace(/[A-Z]/g, (m) => '-' + m.toLowerCase());
                            if (v == null || v === '') delete attrs[attr];
                            else attrs[attr] = String(v);
                            return true;
                        },
                    }
                );
            },
            get innerHTML() {
                return html;
            },
            set innerHTML(v) {
                html = String(v == null ? '' : v);
                children.length = 0;
                // Lightweight parse so selectionOnly can detect an existing tree.
                if (html.indexOf('prks-folder-tree--detail-nav') !== -1) {
                    const tree = createElement('div');
                    tree.className = 'prks-folder-tree prks-folder-tree--detail-nav';
                    const rowRe =
                        /class="prks-folder-tree__row([^"]*)"[^>]*data-folder-id="([^"]+)"/g;
                    let m;
                    while ((m = rowRe.exec(html))) {
                        const row = createElement('div');
                        row.className = ('prks-folder-tree__row' + m[1]).trim();
                        row.setAttribute('data-folder-id', m[2]);
                        const link = createElement('a');
                        link.className = 'prks-folder-tree__link';
                        if (row.className.indexOf('is-selected') !== -1) {
                            link.setAttribute('aria-current', 'page');
                        }
                        row.appendChild(link);
                        tree.appendChild(row);
                    }
                    el.appendChild(tree);
                }
            },
            addEventListener() {},
            removeEventListener() {},
        };
        return el;
    }

    let documentObj = null;
    const body = createElement('body');
    documentObj = {
        body: body,
        documentElement: body,
        createElement: createElement,
        getElementById(id) {
            return byId[id] || null;
        },
        querySelector(sel) {
            return body.querySelector(sel);
        },
        querySelectorAll(sel) {
            return body.querySelectorAll(sel);
        },
        contains(node) {
            return body.contains(node);
        },
    };

    return { document: documentObj, body: body, createElement: createElement };
}

function loadFoldersSandbox(dom, extras) {
    const sandbox = {
        console: console,
        setTimeout: setTimeout,
        clearTimeout: clearTimeout,
        setImmediate: setImmediate,
        Promise: Promise,
        Map: Map,
        Set: Set,
        Array: Array,
        Object: Object,
        String: String,
        Number: Number,
        Boolean: Boolean,
        JSON: JSON,
        Error: Error,
        encodeURIComponent: encodeURIComponent,
        decodeURIComponent: decodeURIComponent,
        CSS: { escape: (s) => String(s) },
        document: dom.document,
        window: null,
        globalThis: null,
        module: { exports: {} },
        exports: {},
        require: require,
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    Object.assign(sandbox, extras || {});
    // TabContext module
    const tcSrc = fs.readFileSync(path.join(rootDir, 'frontend/js/tab-context.js'), 'utf8');
    vm.runInNewContext(tcSrc, sandbox, { filename: 'tab-context.js' });
    const foldersSrc = fs.readFileSync(
        path.join(rootDir, 'frontend/js/components/folders.js'),
        'utf8'
    );
    vm.runInNewContext(foldersSrc, sandbox, { filename: 'folders.js' });
    return sandbox;
}

function selectedMarker(html) {
    const m = String(html || '').match(
        /class="[^"]*is-selected[^"]*"[^>]*data-folder-id="([^"]+)"/
    );
    return m ? m[1] : null;
}

function deferred() {
    let release;
    let reject;
    const promise = new Promise(function (resolve, rej) {
        release = resolve;
        reject = rej;
    });
    return { promise: promise, release: release, reject: reject };
}

async function run() {
    // --- TabContext refresh generation helpers ---
    {
        const tc = require(path.join(rootDir, 'frontend/js/tab-context.js'));
        tc.prksDestroyAllTabContexts();
        const host = { appendChild() {}, children: [] };
        const ctx = tc.createPrksTabContext('refresh-gen');
        ctx.mount(host);
        assertEq('refresh gen starts at 0', ctx.folderHierarchyRefreshGeneration, 0);
        const g1 = ctx.beginFolderHierarchyRefresh();
        const g2 = ctx.beginFolderHierarchyRefresh();
        assertEq('first token', g1, 1);
        assertEq('second token', g2, 2);
        assert('older not current', !ctx.isFolderHierarchyRefreshCurrent(g1));
        assert('newest current', ctx.isFolderHierarchyRefreshCurrent(g2));
        assert('non-number not current', !ctx.isFolderHierarchyRefreshCurrent('2'));
        ctx.beginRoute({ name: 'folder-detail', hash: '#/folders/x' });
        assertEq('beginRoute resets refresh gen', ctx.folderHierarchyRefreshGeneration, 0);
        const g3 = ctx.beginFolderHierarchyRefresh();
        assertEq('post-route token', g3, 1);
        assert('post-route current', ctx.isFolderHierarchyRefreshCurrent(g3));
        ctx.unmount();
        assert('unmounted not current', !ctx.isFolderHierarchyRefreshCurrent(g3));
        const ctx2 = tc.createPrksTabContext('refresh-destroy');
        ctx2.mount(host);
        const gd = ctx2.beginFolderHierarchyRefresh();
        ctx2.destroy();
        assert('destroyed begin returns -1', ctx2.beginFolderHierarchyRefresh() === -1);
        assert('destroyed not current', !ctx2.isFolderHierarchyRefreshCurrent(gd));
        tc.prksDestroyAllTabContexts();
    }

    // --- Overlapping full refresh race (B wins; stale A cannot overwrite) ---
    {
        const dom = makeDom();
        const gates = [];
        const sandbox = loadFoldersSandbox(dom, {
            prksLoadFolderHierarchyCatalogue: async function () {
                const gate = deferred();
                gates.push(gate);
                return gate.promise;
            },
            prksRefreshIcons: function () {},
            prksIcon: function () {
                return '';
            },
        });
        const ctx = sandbox.createPrksTabContext('race-ctx');
        ctx.mount(dom.body);
        const host = dom.createElement('div');
        host.setAttribute('data-prks-folder-detail-tree-host', '');
        host.innerHTML = '<p>Loading folders…</p>';
        ctx.root.appendChild(host);
        ctx.setEntity('folder', { id: 'alpha', title: 'Alpha' });
        const container = ctx.root;

        const rowsA = [
            { id: 'alpha', title: 'Alpha-OLD', parent_id: null, child_count: 0 },
            { id: 'legacy', title: 'Legacy', parent_id: null, child_count: 0 },
        ];
        const rowsB = [
            { id: 'alpha', title: 'Alpha-NEW', parent_id: null, child_count: 1 },
            { id: 'child', title: 'Child-NEW', parent_id: 'alpha', child_count: 0 },
        ];

        const pA = sandbox.prksFillFolderDetailTree(ctx, { id: 'alpha', title: 'Alpha' }, container, {
            selectionOnly: false,
        });
        const pB = sandbox.prksFillFolderDetailTree(ctx, { id: 'alpha', title: 'Alpha' }, container, {
            selectionOnly: false,
        });
        await new Promise(function (r) {
            setImmediate(r);
        });
        assertEq('two overlapping loads gated', gates.length, 2);

        // Newer (B) resolves first.
        gates[1].release(rowsB);
        await pB;
        const afterB = host.innerHTML;
        assert(
            'B committed newer topology',
            afterB.indexOf('Alpha-NEW') !== -1 && afterB.indexOf('Child-NEW') !== -1,
            afterB.slice(0, 200)
        );
        assert('B does not show legacy title', afterB.indexOf('Alpha-OLD') === -1);

        // Older (A) resolves last — must not overwrite.
        gates[0].release(rowsA);
        await pA;
        const afterA = host.innerHTML;
        assert(
            'stale A did not overwrite B topology',
            afterA.indexOf('Alpha-NEW') !== -1 && afterA.indexOf('Child-NEW') !== -1,
            afterA.slice(0, 200)
        );
        assert('stale A did not restore old titles', afterA.indexOf('Alpha-OLD') === -1);
        assert('stale A did not restore Legacy row', afterA.indexOf('Legacy') === -1);
        sandbox.prksDestroyAllTabContexts();
    }

    // --- Selection: stale A cannot restore old selection after B ---
    {
        const dom = makeDom();
        const gates = [];
        const sandbox = loadFoldersSandbox(dom, {
            prksLoadFolderHierarchyCatalogue: async function () {
                const gate = deferred();
                gates.push(gate);
                return gate.promise;
            },
            prksRefreshIcons: function () {},
            prksIcon: function () {
                return '';
            },
        });
        const ctx = sandbox.createPrksTabContext('sel-ctx');
        ctx.mount(dom.body);
        const host = dom.createElement('div');
        host.setAttribute('data-prks-folder-detail-tree-host', '');
        ctx.root.appendChild(host);

        const rowsA = [
            { id: 'a-sel', title: 'Select-A', parent_id: null, child_count: 0 },
            { id: 'b-sel', title: 'Select-B', parent_id: null, child_count: 0 },
        ];
        const rowsB = [
            { id: 'b-sel', title: 'Select-B', parent_id: null, child_count: 0 },
            { id: 'c-sel', title: 'Select-C', parent_id: null, child_count: 0 },
        ];

        // A was started while entity still pointed at a-sel
        ctx.setEntity('folder', { id: 'a-sel', title: 'A' });
        const pA = sandbox.prksFillFolderDetailTree(ctx, { id: 'a-sel', title: 'A' }, ctx.root, {
            selectionOnly: false,
        });
        // Then entity moves / newer refresh for b-sel
        ctx.setEntity('folder', { id: 'b-sel', title: 'B' });
        const pB = sandbox.prksFillFolderDetailTree(ctx, { id: 'b-sel', title: 'B' }, ctx.root, {
            selectionOnly: false,
        });
        await new Promise(function (r) {
            setImmediate(r);
        });
        assertEq('selection race gated', gates.length, 2);
        gates[1].release(rowsB);
        await pB;
        let html = host.innerHTML;
        assert(
            'B selected after newer commit',
            html.indexOf('data-folder-id="b-sel"') !== -1 && html.indexOf('is-selected') !== -1,
            html.slice(0, 240)
        );
        const selB = selectedMarker(html);
        assertEq('selected id is b-sel', selB, 'b-sel');

        gates[0].release(rowsA);
        await pA;
        html = host.innerHTML;
        assert(
            'stale A did not restore a-sel selection',
            selectedMarker(html) === 'b-sel',
            'selected=' + selectedMarker(html) + ' html=' + html.slice(0, 240)
        );
        assert('stale A did not drop Select-C', html.indexOf('Select-C') !== -1);
        sandbox.prksDestroyAllTabContexts();
    }

    // --- Unmount / destroy mid-flight: no DOM commit, no throw ---
    {
        const dom = makeDom();
        const gate = deferred();
        const sandbox = loadFoldersSandbox(dom, {
            prksLoadFolderHierarchyCatalogue: async function () {
                return gate.promise;
            },
            prksRefreshIcons: function () {},
            prksIcon: function () {
                return '';
            },
        });
        const ctx = sandbox.createPrksTabContext('unmount-ctx');
        const host = dom.createElement('div');
        host.setAttribute('data-prks-folder-detail-tree-host', '');
        host.innerHTML = 'LOADING-MARKER';
        ctx.mount(dom.body);
        ctx.root.appendChild(host);
        ctx.setEntity('folder', { id: 'u1', title: 'U' });
        const pending = sandbox.prksFillFolderDetailTree(ctx, { id: 'u1', title: 'U' }, ctx.root, {
            selectionOnly: false,
        });
        await new Promise(function (r) {
            setImmediate(r);
        });
        ctx.destroy();
        gate.release([{ id: 'u1', title: 'Should-Not-Commit', parent_id: null, child_count: 0 }]);
        let threw = false;
        try {
            await pending;
        } catch (_e) {
            threw = true;
        }
        assert('destroy mid-flight does not throw', !threw);
        assertEq(
            'destroy mid-flight does not commit',
            host.innerHTML,
            'LOADING-MARKER'
        );
        sandbox.prksDestroyAllTabContexts();
    }

    // --- Rapid CREATE/rename/reparent/DELETE-style triggers: final = latest ---
    {
        const dom = makeDom();
        const gates = [];
        const sandbox = loadFoldersSandbox(dom, {
            prksLoadFolderHierarchyCatalogue: async function () {
                const gate = deferred();
                gates.push(gate);
                return gate.promise;
            },
            prksRefreshIcons: function () {},
            prksIcon: function () {
                return '';
            },
        });
        const ctx = sandbox.createPrksTabContext('rapid-ctx');
        const host = dom.createElement('div');
        host.setAttribute('data-prks-folder-detail-tree-host', '');
        ctx.mount(dom.body);
        ctx.root.appendChild(host);
        ctx.setEntity('folder', { id: 'root', title: 'Root' });

        const snapshots = [
            [{ id: 'root', title: 'v1', parent_id: null, child_count: 0 }],
            [
                { id: 'root', title: 'v2-renamed', parent_id: null, child_count: 1 },
                { id: 'kid', title: 'Kid', parent_id: 'root', child_count: 0 },
            ],
            [
                { id: 'root', title: 'v3-final', parent_id: null, child_count: 0 },
                { id: 'other', title: 'Other', parent_id: null, child_count: 0 },
            ],
        ];
        const pending = snapshots.map(function () {
            return sandbox.prksFillFolderDetailTree(
                ctx,
                { id: 'root', title: 'Root' },
                ctx.root,
                { selectionOnly: false }
            );
        });
        await new Promise(function (r) {
            setImmediate(r);
        });
        assertEq('rapid triggers gated', gates.length, 3);
        // Resolve out of order: middle, first, last.
        gates[1].release(snapshots[1]);
        await pending[1];
        gates[0].release(snapshots[0]);
        await pending[0];
        gates[2].release(snapshots[2]);
        await pending[2];
        const html = host.innerHTML;
        assert(
            'final tree matches latest snapshot',
            html.indexOf('v3-final') !== -1 && html.indexOf('Other') !== -1,
            html.slice(0, 240)
        );
        assert('final tree dropped intermediate kid', html.indexOf('Kid') === -1);
        assert('final tree dropped v1', html.indexOf('>v1<') === -1 && html.indexOf('v1') === -1);
        sandbox.prksDestroyAllTabContexts();
    }

    // --- commitAllowed helper contracts ---
    {
        const dom = makeDom();
        const sandbox = loadFoldersSandbox(dom, {
            prksLoadFolderHierarchyCatalogue: async function () {
                return [];
            },
        });
        const ctx = sandbox.createPrksTabContext('allowed-ctx');
        const host = dom.createElement('div');
        host.setAttribute('data-prks-folder-detail-tree-host', '');
        ctx.mount(dom.body);
        ctx.root.appendChild(host);
        const gen = ctx.beginFolderHierarchyRefresh();
        assert(
            'commit allowed for current gen',
            sandbox.prksFolderHierarchyTreeCommitAllowed(ctx, gen, ctx.root, {
                aborted: false,
            })
        );
        ctx.beginFolderHierarchyRefresh();
        assert(
            'commit refused for stale gen',
            !sandbox.prksFolderHierarchyTreeCommitAllowed(ctx, gen, ctx.root, {
                aborted: false,
            })
        );
        const gen2 = ctx.folderHierarchyRefreshGeneration;
        assert(
            'commit refused when aborted',
            !sandbox.prksFolderHierarchyTreeCommitAllowed(ctx, gen2, ctx.root, {
                aborted: true,
            })
        );
        sandbox.prksDestroyAllTabContexts();
    }

    // --- selectionOnly also takes a token (cannot clobber newer full refill) ---
    {
        const dom = makeDom();
        const gates = [];
        const sandbox = loadFoldersSandbox(dom, {
            prksLoadFolderHierarchyCatalogue: async function () {
                const gate = deferred();
                gates.push(gate);
                return gate.promise;
            },
            prksRefreshIcons: function () {},
            prksIcon: function () {
                return '';
            },
        });
        const ctx = sandbox.createPrksTabContext('selonly-ctx');
        const host = dom.createElement('div');
        host.setAttribute('data-prks-folder-detail-tree-host', '');
        ctx.mount(dom.body);
        ctx.root.appendChild(host);
        ctx.setEntity('folder', { id: 'keep', title: 'Keep' });

        const rowsOld = [
            { id: 'keep', title: 'Keep', parent_id: null, child_count: 0 },
            { id: 'gone', title: 'Gone', parent_id: null, child_count: 0 },
        ];
        const rowsNew = [
            { id: 'keep', title: 'Keep', parent_id: null, child_count: 0 },
            { id: 'added', title: 'Added', parent_id: null, child_count: 0 },
        ];

        const pSel = sandbox.prksFillFolderDetailTree(ctx, { id: 'keep', title: 'Keep' }, ctx.root, {
            selectionOnly: true,
        });
        const pFull = sandbox.prksFillFolderDetailTree(ctx, { id: 'keep', title: 'Keep' }, ctx.root, {
            selectionOnly: false,
        });
        await new Promise(function (r) {
            setImmediate(r);
        });
        assertEq('selectionOnly vs full gated', gates.length, 2);
        gates[1].release(rowsNew);
        await pFull;
        assert(
            'full refresh committed Added',
            host.innerHTML.indexOf('Added') !== -1
        );
        gates[0].release(rowsOld);
        await pSel;
        assert(
            'stale selectionOnly did not restore Gone / drop Added',
            host.innerHTML.indexOf('Added') !== -1 && host.innerHTML.indexOf('Gone') === -1,
            host.innerHTML.slice(0, 240)
        );
        sandbox.prksDestroyAllTabContexts();
    }
}

run()
    .then(function () {
        console.log('');
        console.log(passed + ' passed, ' + failed + ' failed');
        process.exit(failed ? 1 : 0);
    })
    .catch(function (err) {
        console.error(err);
        process.exit(1);
    });
