#!/usr/bin/env node
'use strict';

const path = require('path');
const vm = require('vm');

/** Minimal stand-in for api.js's real prksInferWorkSourceKind (kept in sync manually). */
function prksInferWorkSourceKind(work) {
    if (!work || typeof work !== 'object') return '';
    const sk = String(work.source_kind || '').trim().toLowerCase();
    if (sk === 'video') return 'video';
    if (sk === 'pdf') return 'pdf';
    const fp = String(work.file_path || '').trim();
    if (fp) return 'pdf';
    if (String(work.source_url || '').trim()) return 'video';
    return sk;
}

const memoryStore = Object.create(null);
const localStorageShim = {
    getItem(k) {
        return Object.prototype.hasOwnProperty.call(memoryStore, k) ? memoryStore[k] : null;
    },
    setItem(k, v) {
        memoryStore[k] = String(v);
    },
    removeItem(k) {
        delete memoryStore[k];
    },
};

/** Tiny DOM enough for preview show → hide → show selftests. */
function makeDom() {
    const byId = Object.create(null);
    const bodyChildren = [];

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
        // Compound ".foo[bar]" / "div.foo[bar=baz]" — split attr suffix first.
        const attrSuffix = sel.match(/^([^\[\]]+)(\[[^\]]+\])$/);
        if (attrSuffix) {
            return matches(el, attrSuffix[1]) && matches(el, attrSuffix[2]);
        }
        if (sel.charAt(0) === '#') {
            return el.id === sel.slice(1);
        }
        if (sel.charAt(0) === '.') {
            const classes = sel.slice(1).split('.').filter(Boolean);
            return classes.every((c) => el.classList.contains(c));
        }
        const attr = sel.match(/^\[([^=\]]+)(?:=\"([^\"]*)\")?\]$/);
        if (attr) {
            const v = el.getAttribute(attr[1]);
            if (attr[2] === undefined) return v != null && v !== '';
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
        const el = {
            tagName: String(tag).toUpperCase(),
            nodeType: 1,
            className: '',
            alt: '',
            hidden: false,
            style: {},
            children,
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
                if (k === 'id') {
                    el.id = s;
                } else if (k === 'class') {
                    el.className = s;
                } else {
                    attrs[k] = s;
                }
            },
            removeAttribute(k) {
                if (k === 'src') delete attrs.src;
                else if (k === 'id') el.id = '';
                else delete attrs[k];
            },
            appendChild(child) {
                children.push(child);
                child.parentNode = el;
                if (child.id) byId[child.id] = child;
                return child;
            },
            replaceChild(next, prev) {
                const i = children.indexOf(prev);
                if (i >= 0) {
                    children[i] = next;
                    next.parentNode = el;
                    prev.parentNode = null;
                    if (prev.id && byId[prev.id] === prev) delete byId[prev.id];
                    if (next.id) byId[next.id] = next;
                }
                return prev;
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
            closest(sel) {
                let n = el;
                while (n) {
                    if (matches(n, sel)) return n;
                    n = n.parentNode;
                }
                return null;
            },
            getBoundingClientRect() {
                return { top: 10, left: 10, right: 110, bottom: 110, width: 100, height: 100 };
            },
            get src() {
                return Object.prototype.hasOwnProperty.call(attrs, 'src') ? attrs.src : '';
            },
            set src(v) {
                attrs.src = String(v);
            },
            get offsetWidth() {
                return 320;
            },
            get offsetHeight() {
                return 240;
            },
        };
        return el;
    }

    const body = createElement('body');
    body.appendChild = function (child) {
        bodyChildren.push(child);
        child.parentNode = body;
        if (child.id) byId[child.id] = child;
        return child;
    };

    const document = {
        body,
        documentElement: { clientWidth: 1024, clientHeight: 768 },
        createElement,
        getElementById(id) {
            return byId[id] || null;
        },
        addEventListener() {},
        querySelector() {
            return null;
        },
    };

    return { document, createElement, byId };
}

const dom = makeDom();

const sandbox = {
    console: console,
    window: null,
    globalThis: null,
    localStorage: localStorageShim,
    document: dom.document,
    URL: URL,
    Map: Map,
    WeakMap: WeakMap,
    encodeURIComponent: encodeURIComponent,
    decodeURIComponent: decodeURIComponent,
    prksInferWorkSourceKind: prksInferWorkSourceKind,
    module: { exports: {} },
    require: require,
    innerWidth: 1024,
    innerHeight: 768,
    matchMedia() {
        return { matches: false };
    },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.root = sandbox;
vm.createContext(sandbox);

function runScript(rel) {
    const file = path.join(__dirname, '..', '..', rel);
    const code = require('fs').readFileSync(file, 'utf8');
    vm.runInContext(code, sandbox, { filename: file });
}

runScript('frontend/js/components/work-cards.js');
runScript('tests/browser/work_cards_selftest.js');

const result = sandbox.prksRunWorkCardSelfTests();
result.rows.forEach((r) => {
    const mark = r.ok ? 'PASS' : 'FAIL';
    const extra = r.detail ? ' — ' + r.detail : '';
    console.log(mark + '  ' + r.name + extra);
});
console.log(result.passed + ' passed, ' + result.failed + ' failed');
if (result.failed) process.exit(1);
