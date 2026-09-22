#!/usr/bin/env node
'use strict';

const path = require('path');
const fs = require('fs');
const vm = require('vm');

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

function tokenizeSelector(sel) {
    return String(sel || '').trim();
}

function matchSimple(el, sel) {
    if (!el || el.nodeType !== 1) return false;
    let rest = String(sel || '').trim();
    if (!rest) return false;
    if (rest === ':scope') return false;
    const parts = rest.split(/(?=[.#\[:])/);
    for (let i = 0; i < parts.length; i++) {
        const p = parts[i];
        if (!p) continue;
        if (p[0] === '#') {
            if (el.id !== p.slice(1)) return false;
        } else if (p[0] === '.') {
            if (!el.classList.contains(p.slice(1))) return false;
        } else if (p[0] === '[') {
            const m = p.match(/^\[([^=\]:]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]+)))?\]$/);
            if (!m) return false;
            const key = m[1];
            const want = m[2] != null ? m[2] : m[3] != null ? m[3] : m[4];
            const got = el.getAttribute(key);
            if (want == null) {
                if (got == null) return false;
            } else if (String(got) !== String(want)) return false;
        } else if (p[0] === ':') {
            continue;
        } else if (p.toUpperCase() !== el.tagName) {
            return false;
        }
    }
    return true;
}

function walk(node, fn) {
    if (!node) return;
    fn(node);
    const kids = node.children || [];
    for (let i = 0; i < kids.length; i++) walk(kids[i], fn);
}

function qs(root, sel, all) {
    const raw = tokenizeSelector(sel);
    if (!raw) return all ? [] : null;
    if (raw[0] === '#') {
        const id = raw.slice(1).split(/[.\[]/)[0];
        let found = null;
        walk(root.documentElement || root, function (n) {
            if (!found && n.id === id) found = n;
        });
        if (raw === '#' + id) return all ? (found ? [found] : []) : found;
    }
    const out = [];
    walk(root, function (n) {
        if (n === root) return;
        if (matchSimple(n, raw)) out.push(n);
    });
    return all ? out : out[0] || null;
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
        checked: false,
        disabled: false,
        isContentEditable: false,
        type: '',
        name: '',
        value: '',
        textContent: '',
        children: [],
        parentElement: null,
        style: {},
        attributes: Object.create(null),
        listeners: [],
        ownerDocument: null,
        classList: null,
        firstChild: null,
        dataset: null,
    };
    el.classList = makeClassList(el);
    el.dataset = new Proxy(
        {},
        {
            get: function (_t, prop) {
                const key = 'data-' + String(prop).replace(/[A-Z]/g, function (c) {
                    return '-' + c.toLowerCase();
                });
                return el.getAttribute(key);
            },
            set: function (_t, prop, v) {
                const key = 'data-' + String(prop).replace(/[A-Z]/g, function (c) {
                    return '-' + c.toLowerCase();
                });
                el.setAttribute(key, v);
                return true;
            },
        }
    );
    el.getAttribute = function (k) {
        if (k === 'class') return el.className;
        if (k === 'id') return el.id;
        if (Object.prototype.hasOwnProperty.call(el.attributes, k)) return el.attributes[k];
        return null;
    };
    el.setAttribute = function (k, v) {
        const val = String(v);
        if (k === 'class') el.className = val;
        else if (k === 'id') el.id = val;
        else if (k === 'hidden') el.hidden = true;
        else if (k === 'type') el.type = val;
        else el.attributes[k] = val;
    };
    el.removeAttribute = function (k) {
        if (k === 'hidden') el.hidden = false;
        delete el.attributes[k];
        if (k === 'id') el.id = '';
    };
    el.appendChild = function (child) {
        child.parentElement = el;
        el.children.push(child);
        el.firstChild = el.children[0] || null;
        return child;
    };
    el.removeChild = function (child) {
        el.children = el.children.filter(function (c) {
            return c !== child;
        });
        child.parentElement = null;
        el.firstChild = el.children[0] || null;
        return child;
    };
    el.contains = function (other) {
        if (other === el) return true;
        let found = false;
        walk(el, function (n) {
            if (n === other) found = true;
        });
        return found;
    };
    el.closest = function (sel) {
        let cur = el;
        while (cur) {
            if (matchSimple(cur, sel)) return cur;
            cur = cur.parentElement;
        }
        return null;
    };
    el.querySelector = function (sel) {
        return qs(el, sel, false);
    };
    el.querySelectorAll = function (sel) {
        return qs(el, sel, true);
    };
    el.addEventListener = function (type, fn) {
        el.listeners.push({ type: type, fn: fn });
    };
    el.focus = function () {
        if (el.ownerDocument) el.ownerDocument.activeElement = el;
    };
    el.click = function () {
        el.listeners
            .filter(function (l) {
                return l.type === 'click';
            })
            .forEach(function (l) {
                l.fn({ target: el, preventDefault: function () {}, stopPropagation: function () {} });
            });
    };
    el.scrollIntoView = function () {};
    Object.defineProperty(el, 'innerHTML', {
        get: function () {
            return el._innerHTML || '';
        },
        set: function (html) {
            el._innerHTML = String(html || '');
        },
    });
    return el;
}

function installDom() {
    const body = makeEl('body');
    const html = makeEl('html');
    const page = makeEl('div');
    page.id = 'page-content';
    const main = makeEl('main');
    main.id = 'main-content';
    const launch = makeEl('button');
    launch.id = 'prks-command-palette-launch';
    const newMore = makeEl('button');
    newMore.id = 'prks-ribbon-new-more';
    const newFile = makeEl('button');
    newFile.id = 'prks-ribbon-new-file';
    const hint = makeEl('kbd');
    hint.setAttribute('data-palette-shortcut-hint', '1');
    hint.textContent = 'Ctrl K';
    launch.appendChild(hint);

    const peopleWrap = makeEl('li');
    peopleWrap.setAttribute('data-nav-disclosure', 'people');
    peopleWrap.className = 'nav-disclosure';
    const peopleLink = makeEl('a');
    peopleLink.className = 'nav-link nav-disclosure__link';
    peopleLink.setAttribute('href', '#/people');
    peopleLink.textContent = 'People';
    const peopleBtn = makeEl('button');
    peopleBtn.setAttribute('data-nav-disclosure-toggle', 'people');
    peopleBtn.setAttribute('aria-expanded', 'false');
    peopleBtn.setAttribute('aria-controls', 'prks-nav-people-children');
    const peopleKids = makeEl('ul');
    peopleKids.id = 'prks-nav-people-children';
    peopleKids.hidden = true;
    peopleKids.setAttribute('hidden', '');
    const authorLink = makeEl('a');
    authorLink.className = 'nav-link nav-link--sub';
    authorLink.setAttribute('href', '#/people/role/Author');
    authorLink.textContent = 'Authors';
    const groupsLink = makeEl('a');
    groupsLink.className = 'nav-link nav-link--sub';
    groupsLink.setAttribute('href', '#/people/groups');
    peopleKids.appendChild(authorLink);
    peopleKids.appendChild(groupsLink);
    peopleWrap.appendChild(peopleLink);
    peopleWrap.appendChild(peopleBtn);
    peopleWrap.appendChild(peopleKids);

    const progressWrap = makeEl('li');
    progressWrap.setAttribute('data-nav-disclosure', 'progress');
    progressWrap.className = 'nav-disclosure';
    const progressBtn = makeEl('button');
    progressBtn.setAttribute('data-nav-disclosure-toggle', 'progress');
    progressBtn.setAttribute('aria-expanded', 'false');
    progressBtn.setAttribute('aria-controls', 'prks-nav-progress-children');
    const progressKids = makeEl('ul');
    progressKids.id = 'prks-nav-progress-children';
    progressKids.hidden = true;
    progressKids.setAttribute('hidden', '');
    const pausedLink = makeEl('a');
    pausedLink.className = 'nav-link progress-filter';
    pausedLink.setAttribute('href', '#/progress?status=Paused');
    pausedLink.setAttribute('data-status', 'Paused');
    progressKids.appendChild(pausedLink);
    progressWrap.appendChild(progressBtn);
    progressWrap.appendChild(progressKids);

    const foldersLink = makeEl('a');
    foldersLink.className = 'nav-link';
    foldersLink.setAttribute('href', '#/folders');
    const recentLink = makeEl('a');
    recentLink.className = 'nav-link';
    recentLink.setAttribute('href', '#/recent');

    const sidebar = makeEl('nav');
    sidebar.id = 'sidebar';
    sidebar.appendChild(foldersLink);
    sidebar.appendChild(recentLink);
    sidebar.appendChild(peopleWrap);
    sidebar.appendChild(progressWrap);

    const modal = makeEl('div');
    modal.id = 'work-modal';
    modal.className = 'modal hidden';
    const personModal = makeEl('div');
    personModal.id = 'person-modal';
    personModal.className = 'modal hidden';
    const settingsModal = makeEl('div');
    settingsModal.id = 'settings-modal';
    settingsModal.className = 'modal hidden';
    const roleModal = makeEl('div');
    roleModal.id = 'role-modal';
    roleModal.className = 'modal hidden';
    const unsaved = makeEl('div');
    unsaved.id = 'prks-modal-unsaved-confirm';
    unsaved.className = 'prks-modal-unsaved-confirm hidden';
    unsaved.setAttribute('aria-modal', 'true');
    const bulk = makeEl('div');
    bulk.id = 'prks-bulk-sheet';
    bulk.className = 'prks-bulk-sheet hidden';
    bulk.setAttribute('aria-modal', 'true');

    body.appendChild(main);
    body.appendChild(sidebar);
    body.appendChild(launch);
    body.appendChild(newFile);
    body.appendChild(newMore);
    body.appendChild(modal);
    body.appendChild(personModal);
    body.appendChild(settingsModal);
    body.appendChild(roleModal);
    body.appendChild(unsaved);
    body.appendChild(bulk);
    main.appendChild(page);
    html.appendChild(body);
    body.parentElement = html;

    const listeners = [];
    const document = {
        documentElement: html,
        body: body,
        readyState: 'complete',
        title: 'PRKS',
        activeElement: body,
        createElement: function (tag) {
            const n = makeEl(tag);
            n.ownerDocument = document;
            return n;
        },
        getElementById: function (id) {
            let found = null;
            walk(html, function (n) {
                if (!found && n.id === id) found = n;
            });
            return found;
        },
        querySelector: function (sel) {
            if (sel === 'body') return body;
            return qs(html, sel, false);
        },
        querySelectorAll: function (sel) {
            return qs(html, sel, true);
        },
        addEventListener: function (type, fn, opts) {
            listeners.push({ type: type, fn: fn, capture: !!(opts && opts.capture === true) || opts === true });
        },
        contains: function (n) {
            return html.contains(n);
        },
        _listeners: listeners,
        _dispatch: function (type, event) {
            listeners
                .filter(function (l) {
                    return l.type === type;
                })
                .forEach(function (l) {
                    l.fn(event);
                });
        },
    };
    body.ownerDocument = document;
    walk(html, function (n) {
        n.ownerDocument = document;
    });
    return {
        document: document,
        launch: launch,
        newMore: newMore,
        peopleBtn: peopleBtn,
        peopleKids: peopleKids,
        peopleWrap: peopleWrap,
        progressBtn: progressBtn,
        progressKids: progressKids,
        authorLink: authorLink,
        pausedLink: pausedLink,
        modal: modal,
        unsaved: unsaved,
        bulk: bulk,
        textarea: null,
    };
}

const installed = installDom();
const { document, launch, newMore, peopleBtn, peopleKids, peopleWrap, progressBtn, progressKids, authorLink, pausedLink, modal, unsaved, bulk } = installed;

const textarea = document.createElement('textarea');
document.body.appendChild(textarea);

const location = {
    hash: '#/folders',
    href: 'http://127.0.0.1:8070/#/folders',
};

const navCalls = [];
const modalCalls = [];
const localStorage = new MemoryStorage();

const sandbox = {
    console: console,
    window: null,
    globalThis: null,
    document: document,
    location: location,
    localStorage: localStorage,
    sessionStorage: new MemoryStorage(),
    navigator: { platform: 'Linux x86_64', userAgent: 'node' },
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    URLSearchParams: URLSearchParams,
    Promise: Promise,
    module: { exports: {} },
    exports: {},
    require: require,
    prksNavigate: function (hash) {
        navCalls.push({ hash: hash, replace: false });
        location.hash = hash;
    },
    openModal: function (id) {
        modalCalls.push(id);
        const el = document.getElementById(id);
        if (el) el.classList.remove('hidden');
    },
    prksAnyModalOpen: function () {
        const nodes = document.querySelectorAll('.modal');
        for (let i = 0; i < nodes.length; i++) {
            if (!nodes[i].classList.contains('hidden')) return true;
        }
        return false;
    },
    prksIsModalUnsavedConfirmOpen: function () {
        return !!(unsaved && !unsaved.classList.contains('hidden'));
    },
    personDisplayName: function (p) {
        return String((p.first_name || '') + ' ' + (p.last_name || '')).trim();
    },
    fetchSearch: function () {
        return Promise.resolve([]);
    },
    fetchFolders: function () {
        return Promise.resolve([]);
    },
    fetchPersons: function () {
        return Promise.resolve([]);
    },
    fetchPersonGroups: function () {
        return Promise.resolve([]);
    },
    fetchPlaylists: function () {
        return Promise.resolve([]);
    },
    fetchSavedViews: function () {
        return Promise.resolve([]);
    },
};

sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.root = sandbox;

function runScript(rel) {
    const file = path.join(__dirname, '..', '..', rel);
    const code = fs.readFileSync(file, 'utf8');
    vm.runInNewContext(code, sandbox, { filename: file });
}

runScript('frontend/js/navigation.js');
runScript('frontend/js/saved-views.js');
runScript('frontend/js/work-selection.js');
runScript('frontend/js/command-palette.js');

const root = sandbox;
root.prksNavigate = function (hash, opts) {
    navCalls.push({ hash: String(hash || ''), replace: !!(opts && opts.replace) });
    location.hash = String(hash || '');
};
root.__prksPaletteDebounceMs = 0;
root.prksInitNavDisclosures();
root.prksInitCommandPalette();

let passed = 0;
let failed = 0;
function assert(name, ok) {
    if (ok) {
        passed += 1;
        console.log('PASS  ' + name);
    } else {
        failed += 1;
        console.log('FAIL  ' + name);
    }
}
function assertEq(name, a, b) {
    const ok = a === b;
    if (!ok) console.log('      got', JSON.stringify(a), 'want', JSON.stringify(b));
    assert(name, ok);
}

function keyEvent(key, mods) {
    const m = mods || {};
    let prevented = false;
    return {
        key: key,
        ctrlKey: !!m.ctrl,
        metaKey: !!m.meta,
        altKey: !!m.alt,
        shiftKey: !!m.shift,
        target: m.target || document.activeElement,
        preventDefault: function () {
            prevented = true;
        },
        stopPropagation: function () {},
        get defaultPrevented() {
            return prevented;
        },
    };
}

function ids() {
    return root.prksCommandPaletteGetResults().map(function (r) {
        return r.id;
    });
}

root.PRKS_PALETTE_COMMANDS.forEach(function (cmd) {
    if (cmd.kind !== 'navigate') return;
    const parsed = root.prksParseRoute(cmd.hash);
    assert('hash recognized ' + cmd.id, parsed && parsed.name && parsed.name !== 'unknown');
});

const sentinel = 'Adorno & Horkheimer';
const hashes = {
    all: root.prksPaletteSearchHash('all', sentinel),
    keywords: root.prksPaletteSearchHash('keywords', sentinel),
    people: root.prksPaletteSearchHash('people', sentinel),
    publisher: root.prksPaletteSearchHash('publisher', sentinel),
};
assert('search all prefix', hashes.all.indexOf('#/search?') === 0);
['all', 'keywords', 'people', 'publisher'].forEach(function (kind) {
    const rec = root.prksParseRoute(hashes[kind]);
    assert('search route ' + kind, rec.name === 'search');
    const q = hashes[kind].slice('#/search?'.length);
    const p = new URLSearchParams(q);
    if (kind === 'all') {
        assertEq('all any', p.get('any'), '1');
        assertEq('all q', p.get('q'), sentinel);
    } else if (kind === 'keywords') {
        assertEq('kw q', p.get('q'), sentinel);
        assert('kw no any', p.get('any') == null);
    } else if (kind === 'people') {
        assertEq('people author', p.get('author'), sentinel);
    } else {
        assertEq('pub publisher', p.get('publisher'), sentinel);
    }
});

const peopleCmd = root.PRKS_PALETTE_COMMANDS.filter(function (c) {
    return c.id === 'navigate-people';
})[0];
const newPerson = root.PRKS_PALETTE_COMMANDS.filter(function (c) {
    return c.id === 'new-person';
})[0];
assert('people label beats keyword', root.prksPaletteScoreCommand(peopleCmd, 'people') > root.prksPaletteScoreCommand(newPerson, 'people'));
const rankedNew = root.prksPaletteFilterCommands('new per', { scope: 'all' });
assert('new per → New Person first', rankedNew[0] && rankedNew[0].id === 'new-person');

root.prksOpenCommandPalette({ scope: 'all' });
assert('open first active', root.prksCommandPaletteIsOpen() && root.prksCommandPaletteGetActiveIndex() === 0);
const n0 = root.prksCommandPaletteGetResults().length;
root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
assertEq('arrow down', root.prksCommandPaletteGetActiveIndex(), 1);
root.prksCommandPaletteHandleKey(keyEvent('ArrowUp'));
assertEq('arrow up', root.prksCommandPaletteGetActiveIndex(), 0);
navCalls.length = 0;
root.prksCommandPaletteExecuteActive();
assert('enter navigates', navCalls.length === 1 && navCalls[0].hash === '#/folders');
assert('enter closes', !root.prksCommandPaletteIsOpen());
assert('navigate not replace', navCalls[0].replace !== true);

root.prksOpenCommandPalette();
root.prksCommandPaletteHandleKey(keyEvent('Escape'));
assert('escape closes', !root.prksCommandPaletteIsOpen());

document.activeElement = textarea;
const ignored = keyEvent('k', { ctrl: true, target: textarea });
root.prksCommandPaletteHandleDocumentKey(ignored);
assert('textarea guard', !root.prksCommandPaletteIsOpen() && !ignored.defaultPrevented);

const inputEl = document.createElement('input');
document.body.appendChild(inputEl);
const ignoredIn = keyEvent('k', { ctrl: true, target: inputEl });
root.prksCommandPaletteHandleDocumentKey(ignoredIn);
assert('input guard', !root.prksCommandPaletteIsOpen());

const ce = document.createElement('div');
ce.isContentEditable = true;
ce.setAttribute('contenteditable', 'true');
document.body.appendChild(ce);
root.prksCommandPaletteHandleDocumentKey(keyEvent('k', { ctrl: true, target: ce }));
assert('contenteditable guard', !root.prksCommandPaletteIsOpen());

const cm = document.createElement('div');
cm.className = 'CodeMirror';
document.body.appendChild(cm);
root.prksCommandPaletteHandleDocumentKey(keyEvent('k', { ctrl: true, target: cm }));
assert('CodeMirror guard', !root.prksCommandPaletteIsOpen());

modal.classList.remove('hidden');
const blocked = keyEvent('k', { ctrl: true, target: document.body });
root.prksCommandPaletteHandleDocumentKey(blocked);
assert('modal guard', !root.prksCommandPaletteIsOpen() && !blocked.defaultPrevented);
modal.classList.add('hidden');

unsaved.classList.remove('hidden');
root.prksCommandPaletteHandleDocumentKey(keyEvent('k', { ctrl: true, target: document.body }));
assert('unsaved guard', !root.prksCommandPaletteIsOpen());
unsaved.classList.add('hidden');

bulk.classList.remove('hidden');
root.prksCommandPaletteHandleDocumentKey(keyEvent('k', { ctrl: true, target: document.body }));
assert('bulk sheet guard', !root.prksCommandPaletteIsOpen());
bulk.classList.add('hidden');

const taken = keyEvent('k', { ctrl: true, target: document.body });
root.prksCommandPaletteHandleDocumentKey(taken);
assert('ctrl-k opens', root.prksCommandPaletteIsOpen() && taken.defaultPrevented);
root.prksCloseCommandPalette();

const takenMeta = keyEvent('k', { meta: true, target: document.body });
root.prksCommandPaletteHandleDocumentKey(takenMeta);
assert('cmd-k opens', root.prksCommandPaletteIsOpen() && takenMeta.defaultPrevented);
root.prksCloseCommandPalette();

launch.focus();
root.prksOpenCommandPalette();
root.prksCommandPaletteHandleKey(keyEvent('Escape'));
assert('focus returns to launcher', document.activeElement === launch);

let resolveA;
let resolveB;
root.fetchSearch = function (q) {
    if (q === 'aa') return new Promise(function (r) { resolveA = r; });
    if (q === 'ab') return new Promise(function (r) { resolveB = r; });
    return Promise.resolve([]);
};
root.prksOpenCommandPalette();
root.prksCommandPaletteSetQuery('aa');
root.prksCommandPaletteSetQuery('ab');
Promise.resolve()
    .then(function () {
        resolveB([{ id: 'W-B', title: 'Later' }]);
        return new Promise(function (r) { setTimeout(r, 0); });
    })
    .then(function () {
        resolveA([{ id: 'W-A', title: 'Stale' }]);
        return new Promise(function (r) { setTimeout(r, 0); });
    })
    .then(function () {
        const got = ids();
        assert('stale ignored', got.indexOf('open-work-W-A') < 0);
        assert('fresh work kept', got.indexOf('open-work-W-B') >= 0);

        let resolveFoldA;
        let resolveFoldB;
        let nFold = 0;
        root.fetchSearch = function () {
            return Promise.resolve([]);
        };
        root.fetchFolders = function () {
            nFold += 1;
            if (nFold === 1) return new Promise(function (r) { resolveFoldA = r; });
            return new Promise(function (r) { resolveFoldB = r; });
        };
        root.prksCloseCommandPalette();
        root.prksOpenCommandPalette();
        root.prksCommandPaletteSetQuery('alpha');
        return Promise.resolve()
            .then(function () {
                root.prksCloseCommandPalette();
                root.prksOpenCommandPalette();
                root.prksCommandPaletteSetQuery('alpha');
                return Promise.resolve();
            })
            .then(function () {
                resolveFoldB([{ id: 'F-FRESH', title: 'Alpha fresh' }]);
                return new Promise(function (r) { setTimeout(r, 0); });
            })
            .then(function () {
                resolveFoldA([{ id: 'F-STALE', title: 'Alpha stale' }]);
                return new Promise(function (r) { setTimeout(r, 0); });
            });
    })
    .then(function () {
        const catalogIds = ids();
        assert('stale catalog ignored', catalogIds.indexOf('open-folder-F-STALE') < 0);
        assert('fresh catalog kept', catalogIds.indexOf('open-folder-F-FRESH') >= 0);

        root.fetchSearch = function () {
            const many = [];
            for (let i = 0; i < 40; i++) many.push({ id: 'W-' + i, title: 'Work ' + i });
            return Promise.resolve(many);
        };
        const folders = [];
        for (let i = 0; i < 40; i++) folders.push({ id: 'F-' + i, title: 'Alpha folder ' + i });
        root.fetchFolders = function () {
            return Promise.resolve(folders);
        };
        root.prksCloseCommandPalette();
        root.prksOpenCommandPalette();
        root.prksCommandPaletteSetQuery('alpha');
        return new Promise(function (r) { setTimeout(r, 0); });
    })
    .then(function () {
        const rows = root.prksCommandPaletteGetResults();
        const workN = rows.filter(function (r) { return r.entity === 'work'; }).length;
        const folderN = rows.filter(function (r) { return r.entity === 'folder'; }).length;
        assert('work cap', workN <= root.PRKS_PALETTE_MAX_WORKS);
        assert('folder cap', folderN <= root.PRKS_PALETTE_MAX_FOLDERS);
        assert('total cap', rows.length <= root.PRKS_PALETTE_MAX_OPTIONS);

        root.fetchSearch = function () {
            return Promise.resolve([{ id: 'W-1', title: 'Retorika', author_text: 'Aristotle', year: '1991' }]);
        };
        root.fetchFolders = function () {
            return Promise.resolve([{ id: 'F-1', title: 'Rhetoric' }]);
        };
        root.fetchPersons = function () {
            return Promise.resolve([{ id: 'P-1', first_name: 'Theodor', last_name: 'Adorno' }]);
        };
        root.fetchPersonGroups = function () {
            return Promise.resolve([{ id: 'G-1', title: 'Frankfurt' }]);
        };
        root.fetchPlaylists = function () {
            return Promise.resolve([{ id: 'PL-1', title: 'Lectures' }]);
        };
        root.fetchSavedViews = function () {
            return Promise.resolve([
                {
                    id: 'SV-1',
                    name: 'Culture Industry',
                    search: { mode: 'all', q: 'PRIVATE_SAVED_QUERY_X9Q7', tag: '', author: '', publisher: '' },
                },
            ]);
        };
        root.prksCloseCommandPalette();
        root.prksOpenCommandPalette();
        navCalls.length = 0;
        root.prksCommandPaletteSetQuery('retorika');
        return new Promise(function (r) { setTimeout(r, 0); });
    })
    .then(function () {
        const rows = root.prksCommandPaletteGetResults();
        const work = rows.filter(function (r) { return r.id === 'open-work-W-1'; })[0];
        assert('work row present', !!work);
        assert('work subtitle carries the acknowledged year',
            String(work.subtitle).indexOf('1991') !== -1);
        const idx = rows.indexOf(work);
        navCalls.length = 0;
        while (root.prksCommandPaletteGetActiveIndex() !== idx) {
            root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
            if (root.prksCommandPaletteGetActiveIndex() === 0 && idx !== 0) break;
        }
        root.prksCommandPaletteExecuteActive();
        assertEq('work navigates hash', navCalls[0] && navCalls[0].hash, '#/works/W-1');

        /* Search results come from the SERVER, so a pending Year edit is not in
         * them. The palette must read the same effective-Work overlay every
         * other surface uses rather than showing a year the user already
         * changed -- and must not grow its own reading of the durable queue. */
        root.prksEffectiveWorkSync = function (w) {
            return w && w.id === 'W-1' ? Object.assign({}, w, { year: '2026' }) : w;
        };
        root.prksCloseCommandPalette();
        root.prksOpenCommandPalette();
        root.prksCommandPaletteSetQuery('retorika');
        return new Promise(function (r) { setTimeout(r, 0); });
    })
    .then(function () {
        const work = root.prksCommandPaletteGetResults()
            .filter(function (r) { return r.id === 'open-work-W-1'; })[0];
        assert('pending year reaches the palette subtitle',
            String(work.subtitle).indexOf('2026') !== -1);
        assert('the value it replaced is gone',
            String(work.subtitle).indexOf('1991') === -1);
        assert('the author is still there', String(work.subtitle).indexOf('Aristotle') !== -1);

        // Cleared Year, pending Published Date: the displayed year follows it.
        root.prksEffectiveWorkSync = function (w) {
            return w && w.id === 'W-1'
                ? Object.assign({}, w, { year: '', published_date: '1954-06-07' }) : w;
        };
        root.prksCloseCommandPalette();
        root.prksOpenCommandPalette();
        root.prksCommandPaletteSetQuery('retorika');
        return new Promise(function (r) { setTimeout(r, 0); });
    })
    .then(function () {
        const work = root.prksCommandPaletteGetResults()
            .filter(function (r) { return r.id === 'open-work-W-1'; })[0];
        assert('a cleared Year falls back to the Published Date',
            String(work.subtitle).indexOf('1954') !== -1);
        delete root.prksEffectiveWorkSync;

        root.prksOpenCommandPalette();
        root.prksCommandPaletteSetQuery('rhetoric');
        return new Promise(function (r) { setTimeout(r, 0); });
    })
    .then(function () {
        navCalls.length = 0;
        const folder = root.prksCommandPaletteGetResults().filter(function (r) { return r.entity === 'folder'; })[0];
        assert('folder row', !!folder);
        const rows = root.prksCommandPaletteGetResults();
        let i = 0;
        while (rows[root.prksCommandPaletteGetActiveIndex()] !== folder && i < 20) {
            root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
            i += 1;
        }
        root.prksCommandPaletteExecuteActive();
        assertEq('folder hash', navCalls[0] && navCalls[0].hash, '#/folders/F-1');

        root.prksOpenCommandPalette();
        root.prksCommandPaletteSetQuery('adorno');
        return new Promise(function (r) { setTimeout(r, 0); });
    })
    .then(function () {
        navCalls.length = 0;
        const person = root.prksCommandPaletteGetResults().filter(function (r) { return r.entity === 'person'; })[0];
        assert('person row', !!person);
        const rows = root.prksCommandPaletteGetResults();
        let i = 0;
        while (rows[root.prksCommandPaletteGetActiveIndex()] !== person && i < 20) {
            root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
            i += 1;
        }
        root.prksCommandPaletteExecuteActive();
        assertEq('person hash', navCalls[0] && navCalls[0].hash, '#/people/P-1');

        root.prksOpenCommandPalette();
        root.prksCommandPaletteSetQuery('frankfurt');
        return new Promise(function (r) { setTimeout(r, 0); });
    })
    .then(function () {
        navCalls.length = 0;
        const g = root.prksCommandPaletteGetResults().filter(function (r) { return r.entity === 'group'; })[0];
        assert('group row', !!g);
        const rows = root.prksCommandPaletteGetResults();
        let i = 0;
        while (rows[root.prksCommandPaletteGetActiveIndex()] !== g && i < 20) {
            root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
            i += 1;
        }
        root.prksCommandPaletteExecuteActive();
        assertEq('group hash', navCalls[0] && navCalls[0].hash, '#/people/groups/G-1');

        root.prksOpenCommandPalette();
        root.prksCommandPaletteSetQuery('lectures');
        return new Promise(function (r) { setTimeout(r, 0); });
    })
    .then(function () {
        navCalls.length = 0;
        const p = root.prksCommandPaletteGetResults().filter(function (r) { return r.entity === 'playlist'; })[0];
        assert('playlist row', !!p);
        const rows = root.prksCommandPaletteGetResults();
        let i = 0;
        while (rows[root.prksCommandPaletteGetActiveIndex()] !== p && i < 20) {
            root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
            i += 1;
        }
        root.prksCommandPaletteExecuteActive();
        assertEq('playlist hash', navCalls[0] && navCalls[0].hash, '#/playlists/PL-1');

        root.prksOpenCommandPalette({ scope: 'create' });
        assertEq('create scope', root.prksCommandPaletteScope(), 'create');
        const createIds = ids();
        assert('create only create cmds', createIds.every(function (id) { return id.indexOf('new-') === 0; }));
        assert('create no search', createIds.indexOf('search-all') < 0);
        modalCalls.length = 0;
        const personCmd = root.prksCommandPaletteGetResults().filter(function (r) { return r.id === 'new-person'; })[0];
        const crows = root.prksCommandPaletteGetResults();
        i = 0;
        while (crows[root.prksCommandPaletteGetActiveIndex()] !== personCmd && i < 10) {
            root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
            i += 1;
        }
        root.prksCommandPaletteExecuteActive();
        assert('create closes palette', !root.prksCommandPaletteIsOpen());
        assertEq('new person modal', modalCalls[0], 'person-modal');

        root.prksOpenCommandPalette();
        root.prksCommandPaletteSetQuery('settings');
        modalCalls.length = 0;
        const settings = root.prksCommandPaletteGetResults().filter(function (r) { return r.id === 'settings'; })[0];
        i = 0;
        const srows = root.prksCommandPaletteGetResults();
        while (srows[root.prksCommandPaletteGetActiveIndex()] !== settings && i < 20) {
            root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
            i += 1;
        }
        root.prksCommandPaletteExecuteActive();
        assertEq('settings modal', modalCalls[0], 'settings-modal');

        root.prksOpenCommandPalette();
        root.prksCommandPaletteSetQuery('link person');
        modalCalls.length = 0;
        const link = root.prksCommandPaletteGetResults().filter(function (r) { return r.id === 'link-person'; })[0];
        i = 0;
        const lrows = root.prksCommandPaletteGetResults();
        while (lrows[root.prksCommandPaletteGetActiveIndex()] !== link && i < 20) {
            root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
            i += 1;
        }
        root.prksCommandPaletteExecuteActive();
        assertEq('link person modal', modalCalls[0], 'role-modal');
        ['work-modal', 'person-modal', 'settings-modal', 'role-modal'].forEach(function (id) {
            const el = document.getElementById(id);
            if (el) el.classList.add('hidden');
        });

        newMore.click();
        assertEq('New… does not open palette', root.prksCommandPaletteIsOpen(), false);
        root.prksOpenCommandPalette({ scope: 'create' });
        assertEq('create scope via API', root.prksCommandPaletteScope(), 'create');
        root.prksCloseCommandPalette();

        location.hash = '#/folders';
        localStorage.setItem('prks.nav.peopleExpanded', '0');
        localStorage.setItem('prks.nav.progressExpanded', '0');
        root.prksSyncSidebarActive(root.prksParseRoute('#/folders'));
        assert('people collapsed', peopleKids.hidden === true);
        assertEq('people aria collapsed', peopleBtn.getAttribute('aria-expanded'), 'false');
        peopleBtn.click();
        assert('people toggle open', peopleKids.hidden === false);
        assertEq('people aria open', peopleBtn.getAttribute('aria-expanded'), 'true');
        peopleBtn.click();
        assert('people toggle closed', peopleKids.hidden === true);

        progressBtn.click();
        assert('progress toggle open', progressKids.hidden === false);
        progressBtn.click();
        assert('progress toggle closed', progressKids.hidden === true);

        // Unset preference: the active family route auto-expands its shortcuts.
        localStorage.removeItem('prks.nav.peopleExpanded');
        location.hash = '#/people/role/Author';
        root.prksSyncSidebarActive(root.prksParseRoute('#/people/role/Author'));
        assert('author auto-opens people on unset pref', peopleKids.hidden === false);
        assertEq('author current', authorLink.getAttribute('aria-current'), 'page');
        assertEq('one current on author', document.querySelectorAll('.nav-link[aria-current="page"]').length, 1);
        assert('disclosure btn not current', peopleBtn.getAttribute('aria-current') == null);

        location.hash = '#/folders';
        root.prksSyncSidebarActive(root.prksParseRoute('#/folders'));
        assert('people collapses after leaving unset family route', peopleKids.hidden === true);

        // Explicit collapse now wins over the route family — no forced-open no-op.
        localStorage.setItem('prks.nav.peopleExpanded', '0');
        location.hash = '#/people/role/Author';
        root.prksSyncSidebarActive(root.prksParseRoute('#/people/role/Author'));
        assert('explicit collapse beats author route', peopleKids.hidden === true);
        assertEq('pref still collapsed', localStorage.getItem('prks.nav.peopleExpanded'), '0');
        assert(
            'people family still shows contains-current while collapsed',
            peopleWrap.classList.contains('nav-disclosure--contains-current')
        );

        localStorage.removeItem('prks.nav.progressExpanded');
        location.hash = '#/progress?status=Paused';
        root.prksSyncSidebarActive(root.prksParseRoute('#/progress?status=Paused'));
        assert('paused auto-opens progress on unset pref', progressKids.hidden === false);
        assertEq('paused current', pausedLink.getAttribute('aria-current'), 'page');
        location.hash = '#/recent';
        root.prksSyncSidebarActive(root.prksParseRoute('#/recent'));
        assert('progress collapses after leaving unset family route', progressKids.hidden === true);

        localStorage.setItem('prks.nav.progressExpanded', '0');
        location.hash = '#/progress?status=Paused';
        root.prksSyncSidebarActive(root.prksParseRoute('#/progress?status=Paused'));
        assert('explicit collapse beats paused route', progressKids.hidden === true);
        assertEq('progress pref still collapsed', localStorage.getItem('prks.nav.progressExpanded'), '0');

        location.hash = '#/recent';
        root.prksOpenCommandPalette();
        const select = root.prksCommandPaletteGetResults().filter(function (r) { return r.id === 'select-files'; })[0];
        assert('select files on recent', !!select);
        root.prksCloseCommandPalette();

        location.hash = '#/folders';
        root.prksOpenCommandPalette();
        const selectFolders = root.prksCommandPaletteGetResults().filter(function (r) { return r.id === 'select-files'; })[0];
        assert('no select on folders index', !selectFolders);
        root.prksCloseCommandPalette();

        location.hash = '#/search?q=culture';
        root.prksOpenCommandPalette();
        const saveView = root.prksCommandPaletteGetResults().filter(function (r) { return r.id === 'save-search-view'; })[0];
        assert('save current search as view', !!saveView);
        root.prksCloseCommandPalette();

        root.prksOpenCommandPalette();
        root.prksCommandPaletteSetQuery('culture');
        return new Promise(function (r) { setTimeout(r, 0); })
            .then(function () {
                const svRows = root.prksCommandPaletteGetResults().filter(function (r) {
                    return r.entity === 'saved-view';
                });
                assert('saved view by name', svRows.some(function (r) { return r.entityId === 'SV-1'; }));
                root.prksCloseCommandPalette();
                root.prksOpenCommandPalette();
                root.prksCommandPaletteSetQuery('PRIVATE_SAVED_QUERY_X9Q7');
                return new Promise(function (r) { setTimeout(r, 0); });
            })
            .then(function () {
                const leak = root.prksCommandPaletteGetResults().filter(function (r) {
                    return r.entity === 'saved-view';
                });
                assert('saved view not matched by definition', leak.length === 0);
                root.prksCloseCommandPalette();

                const tileCalls = [];
                root.prksWorkspaceSnapshot = function () {
                    return {
                        mode: 'stacked',
                        mainTabId: 'tab-1',
                        focusedTabId: 'tab-1',
                        secondaryTree: null,
                        tabs: [
                            { id: 'tab-1', title: 'Work A', route: '#/works/WA', icon: 'file-text' },
                            { id: 'tab-2', title: 'Work B', route: '#/works/WB', icon: 'file-text' },
                            { id: 'tab-3', title: 'Folders', route: '#/folders', icon: 'folder' },
                        ],
                    };
                };
                root.prksWorkspaceVisualTiled = function () { return false; };
                root.prksWorkspaceTileTab = function (id) {
                    tileCalls.push(id);
                    return Promise.resolve(true);
                };
                root.prksWorkspaceFindTabByRoute = function (hash, opts) {
                    const snap = root.prksWorkspaceSnapshot();
                    for (let i = 0; i < snap.tabs.length; i++) {
                        const t = snap.tabs[i];
                        if (opts && opts.excludeMain && t.id === snap.mainTabId) continue;
                        if (t.route === hash) return t;
                    }
                    return null;
                };
                root.prksOpenCommandPalette({ navigationTarget: 'tile' });
                assertEq('split palette title', document.getElementById('prks-command-palette-title').textContent, 'Open in split view');
                const splitRows = root.prksCommandPaletteGetResults();
                assert('open tabs heading first', splitRows[0] && splitRows[0].section === 'open-tabs');
                assert('open tab is B', splitRows.some(function (r) { return r.workspaceTabId === 'tab-2'; }));
                assert('open tabs skip main', !splitRows.some(function (r) { return r.workspaceTabId === 'tab-1'; }));
                assert('open tabs skip folders tab', !splitRows.some(function (r) { return r.workspaceTabId === 'tab-3'; }));
                assert('split palette no folders goto', !splitRows.some(function (r) { return r.id === 'navigate-folders'; }));
                assert('split palette no people list', !splitRows.some(function (r) { return r.id === 'navigate-people'; }));
                assert('split palette no search-all', !splitRows.some(function (r) { return r.id === 'search-all'; }));
                navCalls.length = 0;
                tileCalls.length = 0;
                const idxB = splitRows.findIndex(function (r) { return r.workspaceTabId === 'tab-2'; });
                for (let i = 0; i < idxB; i++) root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
                root.prksCommandPaletteHandleKey(keyEvent('Enter'));
                assert('split open-tab uses tileTab', tileCalls[0] === 'tab-2');
                assert('split open-tab no navigate', navCalls.length === 0);

                /* Split Down/Right lifecycle bug regression: the pane menu's explicit
                 * splitPlacement must be snapshotted by executeRow() BEFORE closePalette()
                 * clears state.splitPlacement, and executeTileSelection must receive that
                 * snapshot rather than re-reading (now-null) transient state. See AGENTS.md's
                 * command-palette operation-state invariant. */
                root.prksCloseCommandPalette();
                const splitLeafCalls = [];
                root.prksWorkspaceSplitLeaf = function (targetTabId, axis, opts) {
                    splitLeafCalls.push({ targetTabId: targetTabId, axis: axis, opts: opts });
                    return Promise.resolve(true);
                };
                root.prksOpenCommandPalette({
                    navigationTarget: 'tile',
                    splitPlacement: { targetLeafTabId: 'tab-9', axis: 'top-bottom', placement: 'second' },
                });
                const capturedBeforeClose = root.prksCommandPaletteSplitPlacement();
                assert('splitPlacement present while palette open', !!capturedBeforeClose);
                assertEq('captured target leaf', capturedBeforeClose.targetLeafTabId, 'tab-9');
                assertEq('captured axis', capturedBeforeClose.axis, 'top-bottom');
                assertEq('captured placement', capturedBeforeClose.placement, 'second');

                const splitRowsForDown = root.prksCommandPaletteGetResults();
                const idxSplitB = splitRowsForDown.findIndex(function (r) { return r.workspaceTabId === 'tab-2'; });
                for (let i = 0; i < idxSplitB; i++) root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
                root.prksCommandPaletteHandleKey(keyEvent('Enter'));

                assertEq('splitPlacement cleared once palette closes', root.prksCommandPaletteSplitPlacement(), null);
                assertEq('one split-leaf call', splitLeafCalls.length, 1);
                assertEq('split-leaf received the captured target', splitLeafCalls[0].targetTabId, 'tab-9');
                assertEq('split-leaf received the captured axis', splitLeafCalls[0].axis, 'top-bottom');
                assertEq('split-leaf received the captured placement', splitLeafCalls[0].opts.placement, 'second');
                assertEq('split-leaf reused the selected open tab', splitLeafCalls[0].opts.tabId, 'tab-2');

                /* Ordinary Tile (no explicit splitPlacement) must be unaffected: it still goes
                 * through prksWorkspaceTileTab, not prksWorkspaceSplitLeaf. */
                tileCalls.length = 0;
                splitLeafCalls.length = 0;
                root.prksOpenCommandPalette({ navigationTarget: 'tile' });
                assertEq('ordinary tile has no splitPlacement', root.prksCommandPaletteSplitPlacement(), null);
                const ordinaryRows = root.prksCommandPaletteGetResults();
                const idxOrdinaryB = ordinaryRows.findIndex(function (r) { return r.workspaceTabId === 'tab-2'; });
                for (let i = 0; i < idxOrdinaryB; i++) root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
                root.prksCommandPaletteHandleKey(keyEvent('Enter'));
                assertEq('ordinary tile still uses tileTab', tileCalls[0], 'tab-2');
                assertEq('ordinary tile does not call split-leaf', splitLeafCalls.length, 0);

                /* #136: split-mode empty / no-match guidance (eligibility unchanged). */
                root.prksCloseCommandPalette();
                root.prksWorkspaceSnapshot = function () {
                    return {
                        mode: 'stacked',
                        mainTabId: 'tab-1',
                        focusedTabId: 'tab-1',
                        secondaryTree: null,
                        tabs: [{ id: 'tab-1', title: 'Work A', route: '#/works/WA', icon: 'file-text' }],
                    };
                };
                root.prksOpenCommandPalette({ navigationTarget: 'tile' });
                const resultsEl = document.getElementById('prks-command-palette-results');
                function emptyHtml() {
                    return String((resultsEl && resultsEl.innerHTML) || '');
                }
                assert('split initial empty present', /prks-command-palette__empty/.test(emptyHtml()));
                assert('split initial empty role', /role="status"/.test(emptyHtml()));
                assert(
                    'split initial empty mentions detail pages',
                    /detail page that supports split/i.test(emptyHtml())
                );
                assert(
                    'split initial empty lists examples',
                    /Work, Person, Playlist, Concept, Position, or Argument/.test(emptyHtml())
                );
                assertEq(
                    'split initial empty helper',
                    root.prksPaletteSplitEmptyMessage(),
                    'Search for a detail page that supports split — Work, Person, Playlist, Concept, Position, or Argument.'
                );
                assertEq('split initial empty not a result row', root.prksCommandPaletteGetResults().length, 0);
                root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
                assertEq('guidance not selectable via ArrowDown', root.prksCommandPaletteGetActiveIndex(), 0);
                assertEq('still no selectable results', root.prksCommandPaletteGetResults().length, 0);

                function assertBlockedNav(query, labelNeedle) {
                    root.prksCommandPaletteSetQuery(query);
                    const html = emptyHtml();
                    assert('blocked empty for ' + query, /prks-command-palette__empty/.test(html));
                    assert('blocked role for ' + query, /role="status"/.test(html));
                    assert(
                        'blocked names destination for ' + query,
                        html.indexOf(labelNeedle) !== -1
                    );
                    assert(
                        'blocked says cannot open for ' + query,
                        /can.t open in split view/i.test(html)
                    );
                    assert(
                        'blocked does not look generic-broken for ' + query,
                        html.indexOf('Search for a page that can open in split view.') === -1
                    );
                    assertEq('no tile results for ' + query, root.prksCommandPaletteGetResults().length, 0);
                    const match = root.prksPaletteFindNonTileableMatch(query);
                    assert('helper finds non-tileable for ' + query, !!(match && match.label === labelNeedle));
                }
                assertBlockedNav('Folders', 'Folders');
                assertBlockedNav('Recent', 'Recent');
                assertBlockedNav('Progress', 'Progress');

                root.prksCommandPaletteSetQuery('zzzz-no-such-page-9qx');
                assert(
                    'nonsense no-results copy',
                    /No matching pages that can open in split view/.test(emptyHtml())
                );
                assert(
                    'nonsense does not claim a named destination',
                    emptyHtml().indexOf("can’t open in split view") === -1
                );
                assertEq('nonsense helper finds nothing', root.prksPaletteFindNonTileableMatch('zzzz-no-such-page-9qx'), null);

                root.prksCloseCommandPalette();
                root.fetchSearch = function () {
                    return Promise.resolve([
                        { id: 'W-SPLIT', title: 'SplitCapable Work Title', author_text: 'Author', year: '2020' },
                    ]);
                };
                root.fetchPersons = function () {
                    return Promise.resolve([{ id: 'P-SPLIT', first_name: 'Split', last_name: 'Person' }]);
                };
                root.fetchPlaylists = function () {
                    return Promise.resolve([{ id: 'PL-SPLIT', title: 'Split Playlist' }]);
                };
                root.fetchConcepts = function () {
                    return Promise.resolve([{ id: 'C-SPLIT', name: 'Split Concept', aliases: [] }]);
                };
                root.fetchPositions = function () {
                    return Promise.resolve([{ id: 'POS-SPLIT', name: 'Split Position' }]);
                };
                root.fetchArguments = function () {
                    return Promise.resolve([{ id: 'A-SPLIT', name: 'Split Argument', kind: 'argument' }]);
                };
                root.prksOpenCommandPalette({ navigationTarget: 'tile' });
                root.prksCommandPaletteSetQuery('SplitCapable');
                return new Promise(function (r) { setTimeout(r, 0); });
            })
            .then(function () {
                const workRows = root.prksCommandPaletteGetResults().filter(function (r) {
                    return r.entity === 'work' && r.entityId === 'W-SPLIT';
                });
                assert('tile-capable work appears', workRows.length >= 1);
                assert(
                    'no empty while work matches',
                    !/prks-command-palette__empty/.test(
                        String((document.getElementById('prks-command-palette-results') || {}).innerHTML || '')
                    )
                );

                navCalls.length = 0;
                const workIdx = root.prksCommandPaletteGetResults().findIndex(function (r) {
                    return r.entity === 'work' && r.entityId === 'W-SPLIT';
                });
                stateActiveTo(workIdx);
                root.prksCommandPaletteHandleKey(keyEvent('Enter'));
                assertEq('work open used tile navigate', navCalls[0] && navCalls[0].hash, '#/works/W-SPLIT');

                root.prksOpenCommandPalette({ navigationTarget: 'tile' });
                const tileEntityChecks = [
                    ['person', 'P-SPLIT', 'Split Person'],
                    ['playlist', 'PL-SPLIT', 'Split Playlist'],
                    ['concept', 'C-SPLIT', 'Split Concept'],
                ];
                function runTileEntityCheck(i) {
                    if (i >= tileEntityChecks.length) return Promise.resolve();
                    const spec = tileEntityChecks[i];
                    root.prksCommandPaletteSetQuery(spec[2]);
                    return new Promise(function (r) { setTimeout(r, 0); }).then(function () {
                        assert(
                            'tile-capable ' + spec[0] + ' appears',
                            root.prksCommandPaletteGetResults().some(function (row) {
                                return row.entity === spec[0] && row.entityId === spec[1];
                            })
                        );
                        return runTileEntityCheck(i + 1);
                    });
                }
                return runTileEntityCheck(0);
            })
            .then(function () {
                /* Normal Ctrl+K palette still surfaces sidebar destinations. */
                root.prksCloseCommandPalette();
                root.prksOpenCommandPalette();
                root.prksCommandPaletteSetQuery('Folders');
                assert(
                    'normal palette lists Folders',
                    root.prksCommandPaletteGetResults().some(function (r) {
                        return r.id === 'navigate-folders';
                    })
                );
                root.prksCommandPaletteSetQuery('Recent');
                assert(
                    'normal palette lists Recent',
                    root.prksCommandPaletteGetResults().some(function (r) {
                        return r.id === 'navigate-recent';
                    })
                );

                const src = fs.readFileSync(path.join(__dirname, '..', '..', 'frontend', 'js', 'command-palette.js'), 'utf8');
                assert('no eval', src.indexOf('eval(') < 0);
                assert('no new Function', src.indexOf('new Function') < 0);
                assert('no dynamic window call', src.indexOf('window[') < 0);
                assert('uses prksRouteSupportsTile for eligibility', src.indexOf('prksRouteSupportsTile') >= 0);
                assert('no second TILE_ROUTE allowlist in palette', src.indexOf('PRKS_TILE_ROUTE_NAMES') < 0);

                console.log(passed + ' passed, ' + failed + ' failed');
                if (failed) process.exit(1);
            });
    })
    .catch(function (err) {
        console.error(err);
        process.exit(1);
    });

function stateActiveTo(idx) {
    const cur = root.prksCommandPaletteGetActiveIndex();
    if (idx < 0) return;
    if (idx >= cur) {
        for (let i = cur; i < idx; i++) root.prksCommandPaletteHandleKey(keyEvent('ArrowDown'));
    } else {
        for (let i = cur; i > idx; i--) root.prksCommandPaletteHandleKey(keyEvent('ArrowUp'));
    }
}
