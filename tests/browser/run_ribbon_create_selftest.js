#!/usr/bin/env node
'use strict';

const path = require('path');
const fs = require('fs');
const vm = require('vm');

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (ok ? '' : ' ' + (detail || '')));
}

function assertEq(name, got, want) {
    const ok = got === want;
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : detail || '');
}

function makeClassList(el) {
    return {
        contains: function (c) {
            return String(el.className || '')
                .split(/\s+/)
                .filter(Boolean)
                .indexOf(c) >= 0;
        },
        add: function (c) {
            if (!this.contains(c)) el.className = (el.className ? el.className + ' ' : '') + c;
        },
        remove: function (c) {
            el.className = String(el.className || '')
                .split(/\s+/)
                .filter(function (x) {
                    return x && x !== c;
                })
                .join(' ');
        },
        toggle: function (c, on) {
            if (on === undefined) on = !this.contains(c);
            if (on) this.add(c);
            else this.remove(c);
        },
    };
}

function makeEl(tag) {
    const el = {
        tagName: String(tag || 'div').toUpperCase(),
        nodeType: 1,
        className: '',
        id: '',
        hidden: false,
        disabled: false,
        tabIndex: 0,
        textContent: '',
        innerHTML: '',
        children: [],
        parentElement: null,
        style: {},
        attributes: Object.create(null),
        listeners: [],
        classList: null,
    };
    el.classList = makeClassList(el);
    el.getAttribute = function (k) {
        if (k === 'class') return el.className;
        if (k === 'id') return el.id;
        if (Object.prototype.hasOwnProperty.call(el.attributes, k)) return el.attributes[k];
        return null;
    };
    el.setAttribute = function (k, v) {
        if (k === 'class') el.className = String(v);
        else if (k === 'id') el.id = String(v);
        else el.attributes[k] = String(v);
    };
    el.removeAttribute = function (k) {
        delete el.attributes[k];
    };
    el.appendChild = function (child) {
        child.parentElement = el;
        el.children.push(child);
        return child;
    };
    el.replaceChildren = function () {
        el.children.forEach(function (c) {
            c.parentElement = null;
        });
        el.children = [];
    };
    el.contains = function (node) {
        if (node === el) return true;
        for (let i = 0; i < el.children.length; i++) {
            if (el.children[i].contains(node)) return true;
        }
        return false;
    };
    el.addEventListener = function (type, fn) {
        el.listeners.push({ type: type, fn: fn });
    };
    el.click = function () {
        el.listeners
            .filter(function (l) {
                return l.type === 'click';
            })
            .forEach(function (l) {
                l.fn({
                    preventDefault: function () {},
                    stopPropagation: function () {},
                    target: el,
                });
            });
    };
    el.focus = function () {
        el.ownerDocument.activeElement = el;
    };
    el.getBoundingClientRect = function () {
        return { left: 10, right: 40, top: 10, bottom: 40, width: 30, height: 30 };
    };
    el.querySelectorAll = function (sel) {
        const out = [];
        function walk(n) {
            n.children.forEach(function (c) {
                if (sel === '[role="menuitem"]' && c.getAttribute('role') === 'menuitem') out.push(c);
                walk(c);
            });
        }
        walk(el);
        return out;
    };
    return el;
}

function harness() {
    const byId = Object.create(null);
    const docListeners = [];
    const body = makeEl('body');
    const html = makeEl('html');
    html.clientWidth = 1200;
    html.clientHeight = 800;
    const main = makeEl('button');
    main.id = 'prks-ribbon-new-file';
    main.setAttribute('id', 'prks-ribbon-new-file');
    const chevron = makeEl('button');
    chevron.id = 'prks-ribbon-new-more';
    chevron.setAttribute('id', 'prks-ribbon-new-more');
    chevron.setAttribute('aria-expanded', 'false');
    body.appendChild(main);
    body.appendChild(chevron);
    byId['prks-ribbon-new-file'] = main;
    byId['prks-ribbon-new-more'] = chevron;

    const document = {
        body: body,
        documentElement: html,
        activeElement: body,
        getElementById: function (id) {
            if (byId[id]) return byId[id];
            let found = null;
            function walk(n) {
                if (n.id === id) found = n;
                n.children.forEach(walk);
            }
            walk(body);
            return found;
        },
        createElement: function (tag) {
            const el = makeEl(tag);
            el.ownerDocument = document;
            return el;
        },
        contains: function (n) {
            return body.contains(n);
        },
        addEventListener: function (type, fn, cap) {
            docListeners.push({ type: type, fn: fn, cap: cap });
        },
    };
    main.ownerDocument = document;
    chevron.ownerDocument = document;
    body.ownerDocument = document;

    const modalCalls = [];
    const sandbox = {
        window: null,
        document: document,
        module: { exports: {} },
        console: console,
        openModal: function (id) {
            modalCalls.push(id);
        },
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    sandbox.root = sandbox;
    vm.createContext(sandbox);
    const src = fs.readFileSync(
        path.join(__dirname, '../../frontend/js/ribbon-create.js'),
        'utf8'
    );
    vm.runInContext(src, sandbox);
    sandbox.prksInitRibbonCreate();
    byId['prks-create-menu'] = document.getElementById('prks-create-menu');
    return { sandbox: sandbox, document: document, main: main, chevron: chevron, modalCalls: modalCalls, docListeners: docListeners };
}

function keyEvent(key) {
    return {
        key: key,
        preventDefault: function () {
            this.prevented = true;
        },
        stopPropagation: function () {},
        target: null,
    };
}

function fireDoc(h, type, ev) {
    h.docListeners
        .filter(function (l) {
            return l.type === type;
        })
        .forEach(function (l) {
            l.fn(ev);
        });
}

function menuItems(h) {
    const menu = h.document.getElementById('prks-create-menu');
    return menu ? menu.querySelectorAll('[role="menuitem"]') : [];
}

const h = harness();
assert('init creates menu', !!h.document.getElementById('prks-create-menu'));
assertEq('menu starts hidden', h.document.getElementById('prks-create-menu').hidden, true);

h.main.click();
assertEq('primary click does not open menu', h.document.getElementById('prks-create-menu').hidden, true);
assertEq('primary click does not create via menu', h.modalCalls.length, 0);

h.chevron.click();
assertEq('chevron opens menu', h.document.getElementById('prks-create-menu').hidden, false);
assertEq('aria-expanded', h.chevron.getAttribute('aria-expanded'), 'true');
const items = menuItems(h);
assertEq('four create actions', items.length, 4);
assertEq('first label', items[0].textContent.indexOf('New File') >= 0 || items[0].children[1].textContent === 'New File', true);
assertEq(
    'labels',
    items.map(function (b) {
        return b.children[1] ? b.children[1].textContent : b.textContent;
    }).join('|'),
    'New File|New Folder|New Person|New Group'
);

fireDoc(h, 'keydown', keyEvent('ArrowDown'));
assert('arrow down moves', items[1].classList.contains('is-active'));
fireDoc(h, 'keydown', keyEvent('End'));
assert('End last', items[3].classList.contains('is-active'));
fireDoc(h, 'keydown', keyEvent('Home'));
assert('Home first', items[0].classList.contains('is-active'));

fireDoc(h, 'keydown', keyEvent('Escape'));
assertEq('escape closes', h.document.getElementById('prks-create-menu').hidden, true);
assertEq('escape restores focus target', h.document.activeElement, h.chevron);

h.chevron.click();
items[1].click();
assertEq('folder action', h.modalCalls[0], 'folder-modal');
assertEq('menu closed after action', h.document.getElementById('prks-create-menu').hidden, true);

h.modalCalls.length = 0;
h.chevron.click();
menuItems(h)[2].click();
assertEq('person action', h.modalCalls[0], 'person-modal');

h.modalCalls.length = 0;
h.chevron.click();
menuItems(h)[3].click();
assertEq('group action', h.modalCalls[0], 'group-modal');

h.modalCalls.length = 0;
h.chevron.click();
menuItems(h)[0].click();
assertEq('file action same modal', h.modalCalls[0], 'work-modal');

h.chevron.click();
fireDoc(h, 'pointerdown', { target: h.document.body });
assertEq('outside click closes', h.document.getElementById('prks-create-menu').hidden, true);

assertEq('create action count', h.sandbox.PRKS_RIBBON_CREATE_ACTIONS.length, 4);

console.log('\n' + passed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);
