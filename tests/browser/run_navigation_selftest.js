#!/usr/bin/env node
'use strict';

const path = require('path');

class MemoryStorage {
    constructor() {
        this.store = Object.create(null);
    }
    getItem(k) {
        return Object.prototype.hasOwnProperty.call(this.store, k) ? this.store[k] : null;
    }
    setItem(k, v) {
        this.store[k] = String(v);
    }
    removeItem(k) {
        delete this.store[k];
    }
}

function makeLink(href, status) {
    const attrs = { href: href };
    if (status) attrs['data-status'] = status;
    return {
        className: 'nav-link',
        attrs: attrs,
        classList: {
            add: function (c) {
                if (this._el.className.indexOf(c) < 0) this._el.className += ' ' + c;
            },
            remove: function (c) {
                this._el.className = this._el.className
                    .split(/\s+/)
                    .filter((x) => x && x !== c)
                    .join(' ');
            },
        },
        getAttribute: function (k) {
            return this.attrs[k] == null ? null : this.attrs[k];
        },
        setAttribute: function (k, v) {
            this.attrs[k] = v;
        },
        removeAttribute: function (k) {
            delete this.attrs[k];
        },
        _el: null,
    };
}

/** Minimal element for the nav-disclosure fixtures: attrs, class list, hidden, and addEventListener. */
function makeDisclosureEl(attrs, className) {
    const el = {
        className: className || '',
        attrs: Object.assign({}, attrs || {}),
        hidden: false,
        dataset: {},
        _listeners: {},
        getAttribute: function (k) {
            return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
        },
        setAttribute: function (k, v) {
            this.attrs[k] = String(v);
        },
        removeAttribute: function (k) {
            delete this.attrs[k];
        },
        addEventListener: function (type, fn) {
            (this._listeners[type] = this._listeners[type] || []).push(fn);
        },
        click: function () {
            const evt = { preventDefault: function () {}, stopPropagation: function () {} };
            (this._listeners.click || []).forEach((fn) => fn(evt));
        },
    };
    el.classList = {
        add: function (c) {
            const parts = el.className.split(/\s+/).filter(Boolean);
            if (parts.indexOf(c) < 0) parts.push(c);
            el.className = parts.join(' ');
        },
        remove: function (c) {
            el.className = el.className
                .split(/\s+/)
                .filter((x) => x && x !== c)
                .join(' ');
        },
        toggle: function (c, on) {
            if (on) el.classList.add(c);
            else el.classList.remove(c);
        },
        contains: function (c) {
            return el.className.split(/\s+/).indexOf(c) >= 0;
        },
    };
    return el;
}

function makeDisclosureFixture(which, listId, includeLink) {
    const wrap = makeDisclosureEl({ 'data-nav-disclosure': which }, 'nav-disclosure');
    const btn = makeDisclosureEl(
        {
            'data-nav-disclosure-toggle': which,
            'aria-expanded': 'false',
            'aria-controls': listId,
        },
        'nav-disclosure__toggle'
    );
    btn.dataset.bound = undefined;
    const list = makeDisclosureEl({ id: listId }, 'nav-disclosure__children');
    list.hidden = true;
    const link = includeLink ? makeLink('#/' + which) : null;
    if (link) link.classList._el = link;
    return { wrap: wrap, btn: btn, list: list, link: link };
}

function installDom() {
    const links = [
        makeLink('#/folders'),
        makeLink('#/recent'),
        makeLink('#/views'),
        makeLink('#/types'),
        makeLink('#/playlists'),
        makeLink('#/tags'),
        makeLink('#/publishers'),
        makeLink('#/people'),
        makeLink('#/people/role/Author'),
        makeLink('#/people/role/Reviewer'),
        makeLink('#/people/groups'),
        makeLink('#/progress?status=Paused', 'Paused'),
        makeLink('#/processing-files'),
        makeLink('#/graph'),
        makeLink('#/concepts'),
    ];
    links.forEach((l) => {
        l.classList._el = l;
    });

    const people = makeDisclosureFixture('people', 'prks-nav-people-children', true);
    const research = makeDisclosureFixture('research', 'prks-nav-research-children', false);
    const progress = makeDisclosureFixture('progress', 'prks-nav-progress-children', false);
    const disclosures = { people: people, research: research, progress: progress };

    const main = { id: 'main-content', scrollTop: 0 };
    const all = links.slice();
    const toggleButtons = [people.btn, research.btn, progress.btn];

    global.document = {
        title: 'PRKS - Personal Research Knowledge System',
        getElementById: function (id) {
            if (id === 'main-content') return main;
            for (const which of Object.keys(disclosures)) {
                if (disclosures[which].list.attrs.id === id) return disclosures[which].list;
            }
            return null;
        },
        querySelector: function (sel) {
            if (sel === '.nav-link[aria-current="page"]') {
                return all.find((l) => l.attrs['aria-current'] === 'page') || null;
            }
            let m = sel.match(/^\[data-nav-disclosure-toggle="([^"]+)"\]$/);
            if (m) return disclosures[m[1]] ? disclosures[m[1]].btn : null;
            m = sel.match(/^\[data-nav-disclosure="([^"]+)"\]$/);
            if (m) return disclosures[m[1]] ? disclosures[m[1]].wrap : null;
            return null;
        },
        querySelectorAll: function (sel) {
            if (sel === '.nav-link') return all;
            if (sel === '.nav-link[aria-current="page"]') return all.filter((l) => l.attrs['aria-current'] === 'page');
            if (sel === '[data-nav-disclosure-toggle]') return toggleButtons;
            return [];
        },
    };
    return { main: main, links: links, disclosures: disclosures };
}

global.sessionStorage = new MemoryStorage();
global.localStorage = new MemoryStorage();
global.location = { hash: '' };
installDom();

require(path.join(__dirname, '..', '..', 'frontend', 'js', 'navigation.js'));
require(path.join(__dirname, 'navigation_selftest.js'));

const result = global.prksRunNavigationSelfTests();
result.rows.forEach((r) => {
    const mark = r.ok ? 'PASS' : 'FAIL';
    const extra = r.detail ? ' — ' + r.detail : '';
    console.log(mark + '  ' + r.name + extra);
});
console.log(result.passed + ' passed, ' + result.failed + ' failed');
if (result.failed) process.exit(1);
