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

function installDom() {
    const links = [
        makeLink('#/folders'),
        makeLink('#/recent'),
        makeLink('#/types'),
        makeLink('#/playlists'),
        makeLink('#/tags'),
        makeLink('#/publishers'),
        makeLink('#/people'),
        makeLink('#/people/role/Author'),
        makeLink('#/people/groups'),
        makeLink('#/progress?status=Paused', 'Paused'),
        makeLink('#/processing-files'),
    ];
    links.forEach((l) => {
        l.classList._el = l;
    });
    const main = { id: 'main-content', scrollTop: 0 };
    const all = links.slice();
    global.document = {
        title: 'PRKS - Personal Research Knowledge System',
        getElementById: function (id) {
            if (id === 'main-content') return main;
            return null;
        },
        querySelector: function (sel) {
            if (sel === '.nav-link[aria-current="page"]') {
                return all.find((l) => l.attrs['aria-current'] === 'page') || null;
            }
            return null;
        },
        querySelectorAll: function (sel) {
            if (sel === '.nav-link') return all;
            if (sel === '.nav-link[aria-current="page"]') return all.filter((l) => l.attrs['aria-current'] === 'page');
            return [];
        },
    };
    return { main: main, links: links };
}

global.sessionStorage = new MemoryStorage();
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
