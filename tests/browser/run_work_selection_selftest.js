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
        if (p === ':checked') {
            if (!el.checked) return false;
            continue;
        }
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
    };
    el.classList = makeClassList(el);
    el.getAttribute = function (k) {
        if (k === 'class') return el.className;
        if (k === 'id') return el.id;
        if (Object.prototype.hasOwnProperty.call(el.attributes, k)) return el.attributes[k];
        return null;
    };
    el.hasAttribute = function (k) {
        if (k === 'class') return !!el.className;
        if (k === 'id') return !!el.id;
        if (k === 'hidden') return !!el.hidden;
        return Object.prototype.hasOwnProperty.call(el.attributes, k);
    };
    el.setAttribute = function (k, v) {
        const val = String(v);
        if (k === 'class') el.className = val;
        else if (k === 'id') el.id = val;
        else if (k === 'hidden') el.hidden = true;
        else el.attributes[k] = val;
    };
    el.removeAttribute = function (k) {
        if (k === 'hidden') el.hidden = false;
        delete el.attributes[k];
    };
    el.appendChild = function (child) {
        child.parentElement = el;
        el.children.push(child);
        el.firstChild = el.children[0] || null;
        return child;
    };
    el.insertBefore = function (child, ref) {
        child.parentElement = el;
        const i = el.children.indexOf(ref);
        if (i < 0) el.children.push(child);
        else el.children.splice(i, 0, child);
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
    el.remove = function () {
        if (el.parentElement) el.parentElement.removeChild(el);
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
    el.click = function () {};
    Object.defineProperty(el, 'innerHTML', {
        get: function () {
            return el._innerHTML || '';
        },
        set: function (html) {
            el._innerHTML = String(html || '');
            el.children = [];
            el.firstChild = null;
            const re =
                /<([a-z0-9]+)([^>]*)>([\s\S]*?)<\/\1>|<([a-z0-9]+)([^>]*)\/>|<([a-z0-9]+)([^>]*)>/gi;
            let m;
            const src = el._innerHTML;
            while ((m = re.exec(src))) {
                const tag = m[1] || m[4] || m[6];
                const attrs = m[2] || m[5] || m[7] || '';
                const child = makeEl(tag);
                child.ownerDocument = el.ownerDocument;
                const am = attrs.match(/(?:data-[\w-]+|aria-[\w-]+|id|class|type|name|role|hidden)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/g) || [];
                am.forEach(function (pair) {
                    const kv = pair.split('=');
                    const key = kv[0].trim();
                    let val = kv.slice(1).join('=').trim();
                    if ((val[0] === '"' || val[0] === "'") && val[val.length - 1] === val[0]) {
                        val = val.slice(1, -1);
                    }
                    if (key === 'class') child.className = val;
                    else if (key === 'id') child.id = val;
                    else if (key === 'type') child.type = val;
                    else if (key === 'name') child.name = val;
                    else child.setAttribute(key, val);
                });
                const text = m[3];
                if (text && text.indexOf('<') < 0) child.textContent = text.replace(/&times;/g, '×').replace(/&amp;/g, '&');
                el.appendChild(child);
            }
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
    main.scrollTop = 0;
    body.appendChild(main);
    main.appendChild(page);
    html.appendChild(body);
    body.parentElement = html;

    const listeners = [];
    const document = {
        documentElement: html,
        body: body,
        readyState: 'complete',
        title: 'PRKS',
        createElement: function (tag) {
            const el = makeEl(tag);
            el.ownerDocument = document;
            return el;
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
    page.ownerDocument = document;
    main.ownerDocument = document;
    return { document: document, page: page, main: main, body: body };
}

const { document, page, main } = installDom();

const history = {
    length: 1,
    replaceState: function () {},
    pushState: function () {
        this.length += 1;
    },
};

const location = {
    hash: '#/progress?status=Paused',
    href: 'http://127.0.0.1:8070/#/progress?status=Paused',
};

const sandbox = {
    console: console,
    window: null,
    globalThis: null,
    document: document,
    location: location,
    history: history,
    sessionStorage: new MemoryStorage(),
    localStorage: new MemoryStorage(),
    getComputedStyle: function () {
        return { display: 'block', visibility: 'visible', position: 'static' };
    },
    CSS: { escape: function (s) { return String(s); } },
    module: { exports: {} },
    exports: {},
    require: require,
};

sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.root = sandbox;

function runScript(rel) {
    const file = path.join(__dirname, '..', '..', rel);
    const code = fs.readFileSync(file, 'utf8');
    vm.runInNewContext(code, sandbox, { filename: file });
}

runScript('frontend/js/route-model.js');
runScript('frontend/js/navigation.js');
runScript('frontend/js/components/work-cards.js');
runScript('frontend/js/work-selection.js');

const root = sandbox;
root.__prksRouteGen = 1;
root.prksWorkSelectionInit();

function addCard(id, title, hidden) {
    const card = document.createElement('div');
    card.className = 'project-card project-card--work-card';
    card.setAttribute('data-work-id', id);
    card.setAttribute('data-prks-route', '#/works/' + id);
    card.hidden = !!hidden;
    const link = document.createElement('a');
    link.className = 'work-card__link';
    link.setAttribute('href', '#/works/' + id);
    link.setAttribute('aria-label', title || id);
    const t = document.createElement('div');
    t.className = 'card-title';
    t.textContent = title || id;
    link.appendChild(t);
    card.appendChild(link);
    page.appendChild(card);
    return card;
}

const rows = [];
let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    rows.push({ name: name, ok: !!ok, detail: detail || '' });
    if (ok) passed += 1;
    else failed += 1;
}

function assert(name, cond, detail) {
    record(name, !!cond, detail);
}

function assertEq(name, got, want) {
    const ok = got === want;
    record(name, ok, ok ? '' : 'got ' + JSON.stringify(got) + ' want ' + JSON.stringify(want));
}

/* Card markup / selection anchors are owned by PrksWorkCard (Vitest). This
 * harness exercises selection behavior against Vue-shaped DOM via addCard(). */

assert('folder-detail supported', root.prksWorkSelectionIsSupportedRoute({ name: 'folder-detail' }));
assert('recent supported', root.prksWorkSelectionIsSupportedRoute({ name: 'recent' }));
assert('type-detail supported', root.prksWorkSelectionIsSupportedRoute({ name: 'type-detail' }));
assert('progress supported', root.prksWorkSelectionIsSupportedRoute({ name: 'progress' }));
assert('search supported', root.prksWorkSelectionIsSupportedRoute({ name: 'search' }));
assert('work not supported', !root.prksWorkSelectionIsSupportedRoute({ name: 'work' }));
assert('person not supported', !root.prksWorkSelectionIsSupportedRoute({ name: 'person' }));
assert('playlist-detail not supported', !root.prksWorkSelectionIsSupportedRoute({ name: 'playlist-detail' }));
assert('saved-view-detail supported', root.prksWorkSelectionIsSupportedRoute({ name: 'saved-view-detail' }));
assert('processing-files not supported', !root.prksWorkSelectionIsSupportedRoute({ name: 'processing-files' }));

page.innerHTML = '';
page.id = 'page-content';
main.appendChild(page);
const c1 = addCard('W-1', 'One');
const c2 = addCard('W-2', 'Two');
const c3 = addCard('W-3', 'Hidden', true);

root.prksWorkSelectionResetForTests();
root.prksWorkSelectionEnter();
assert('enter activates', root.prksWorkSelectionIsActive());
root.prksWorkSelectionToggle('W-1');
assertEq('count 1', root.prksWorkSelectionCountText(), '1 selected');
root.prksWorkSelectionToggle('W-2');
root.prksWorkSelectionToggle('W-3');
assertEq('count 3', root.prksWorkSelectionCountText(), '3 selected');
root.prksWorkSelectionToggle('W-2');
assertEq('deselect to 2', root.prksWorkSelectionCountText(), '2 selected');
root.prksWorkSelectionClear();
assertEq('clear 0', root.prksWorkSelectionCountText(), '0 selected');
assert('still active after clear', root.prksWorkSelectionIsActive());

root.prksWorkSelectionToggle('W-1');
root.prksWorkSelectionSelectAllVisible();
const visibleIds = root.prksWorkSelectionGetIds().slice().sort();
assert('select-all skips hidden', visibleIds.join(',') === 'W-1,W-2', visibleIds.join(','));

const ev = {
    key: 'Escape',
    preventDefault: function () { this.prevented = true; },
    stopPropagation: function () { this.stopped = true; },
};
document._dispatch('keydown', ev);
assert('escape exits', !root.prksWorkSelectionIsActive());
assert('escape does not navigate', location.hash.indexOf('#/progress') === 0);

root.prksWorkSelectionEnter();
root.prksWorkSelectionToggle('W-1');
root.prksWorkSelectionOnRouteWillChange('#/search?q=A', '#/recent');
assert('route change exits', !root.prksWorkSelectionIsActive());
assertEq('route change clears ids', root.prksWorkSelectionGetIds().join(','), '');

const clickEv = {
    target: c1,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    button: 0,
    preventDefault: function () { this.prevented = true; },
    stopPropagation: function () { this.stopped = true; },
};
root.prksWorkSelectionEnter();
document._dispatch('click', clickEv);
assert('click toggles select', root.prksWorkSelectionGetIds().indexOf('W-1') >= 0);
assert('click stopped nav', !!clickEv.stopped);
const hashBefore = location.hash;
document._dispatch('click', clickEv);
assert('click again deselects', root.prksWorkSelectionGetIds().indexOf('W-1') < 0);
assertEq('hash unchanged', location.hash, hashBefore);

/* Bulk selection: Enter/Space toggles; Work href is not a tab stop. */
root.prksWorkSelectionEnter();
const c1Link = c1.querySelector('a.work-card__link');
assertEq('selection removes leftover card role', c1.getAttribute('role'), null);
assertEq('selection parks card tab stop', c1.getAttribute('tabindex'), '-1');
assertEq('selection parks work-link tab stop', c1Link.getAttribute('tabindex'), '-1');
assert('selection has checkbox', !!c1.querySelector('.work-card__checkbox'));
assert('checkbox stays outside the work link', !c1.querySelector('a.work-card__link .work-card__select'));
assert('checkbox is a card child', !!c1.querySelector('.work-card__select') && c1.children[0].classList.contains('work-card__select'));
const keyNavCalls = { n: 0 };
root.prksNavigate = function () { keyNavCalls.n += 1; };
const keyEv = {
    key: 'Enter',
    target: c1Link,
    preventDefault: function () { this.prevented = true; },
    stopPropagation: function () { this.stopped = true; },
};
document._dispatch('keydown', keyEv);
assert('enter on work link toggles select', root.prksWorkSelectionGetIds().indexOf('W-1') >= 0);
assert('enter prevented nav default', !!keyEv.prevented);
assertEq('enter did not navigate', keyNavCalls.n, 0);
const spaceEv = {
    key: ' ',
    target: c1Link,
    preventDefault: function () { this.prevented = true; },
    stopPropagation: function () { this.stopped = true; },
};
document._dispatch('keydown', spaceEv);
assert('space on work link deselects', root.prksWorkSelectionGetIds().indexOf('W-1') < 0);
root.prksWorkSelectionExit();
assertEq('exit does not invent a link role', c1.getAttribute('role'), null);
assertEq('exit restores work-link tab stop', c1Link.getAttribute('tabindex'), null);

let navCalls = 0;
let navReplace = false;
root.prksNavigate = function (_hash, opts) {
    navCalls += 1;
    navReplace = !!(opts && opts.replace);
};
root.prksCaptureCurrentRouteState = function () {
    root._captured = true;
};
root.__prksRouteGen = 4;
location.hash = '#/progress?status=Paused';

root.prksWorkSelectionEnter();
root.prksWorkSelectionToggle('W-1');
root.prksWorkSelectionToggle('W-2');
root.bulkUpdateWorks = async function () {
    return { status: 'updated', action: 'set_status', requested: 2, updated: 2 };
};
root.prksWorkSelectionSubmitPayload({
    work_ids: ['W-1', 'W-2'],
    action: 'set_status',
    status: 'Completed',
}).then(function (res) {
    assert('success ok', res && res.ok);
    assert('success clears selection', !root.prksWorkSelectionIsActive());
    assertEq('success ids empty', root.prksWorkSelectionGetIds().join(','), '');
    assert('success captured scroll', root._captured === true);
    assert('success navigates replace', navCalls === 1 && navReplace);

    root.prksWorkSelectionEnter();
    root.prksWorkSelectionToggle('W-1');
    root.bulkUpdateWorks = async function () {
        throw new Error('One or more selected files no longer exist.');
    };
    return Promise.resolve(root.prksWorkSelectionOpenSheet('status')).then(function () {
        const sheet = document.getElementById('prks-bulk-sheet');
        const body = sheet && sheet.querySelector('.prks-bulk-sheet__body');
        const stBtn = document.createElement('button');
        stBtn.className = 'prks-segmented__btn prks-segmented__btn--active';
        stBtn.setAttribute('data-value', 'Completed');
        if (body) body.appendChild(stBtn);
        else if (sheet) sheet.appendChild(stBtn);
        let applyBtn = sheet && sheet.querySelector('[data-bulk-apply]');
        if (!applyBtn && sheet) {
            applyBtn = document.createElement('button');
            applyBtn.setAttribute('data-bulk-apply', '1');
            applyBtn.textContent = 'Apply';
            sheet.appendChild(applyBtn);
        }
        return root.prksWorkSelectionSubmitPayload({
            work_ids: ['W-1'],
            action: 'set_status',
            status: 'Completed',
        });
    });
}).then(function (res) {
    assert('fail keeps mode', root.prksWorkSelectionIsActive());
    assertEq('fail keeps id', root.prksWorkSelectionGetIds().join(','), 'W-1');
    assert('fail not ok', res && res.ok === false);
    const sheet = document.getElementById('prks-bulk-sheet');
    const applyBtn = sheet && sheet.querySelector('[data-bulk-apply]');
    assert('fail apply exists', !!applyBtn);
    assertEq('fail apply label', applyBtn ? applyBtn.textContent : '', 'Apply');
    assert('fail apply not working', applyBtn && applyBtn.textContent !== 'Working…');

    navCalls = 0;
    root.__prksRouteGen = 9;
    location.hash = '#/progress?status=Paused';
    root.prksWorkSelectionEnter();
    root.prksWorkSelectionToggle('W-1');
    root.bulkUpdateWorks = async function () {
        root.__prksRouteGen = 10;
        location.hash = '#/recent';
        return { status: 'updated', action: 'set_status', requested: 1, updated: 1 };
    };
    return root.prksWorkSelectionSubmitPayload({
        work_ids: ['W-1'],
        action: 'set_status',
        status: 'Completed',
    });
}).then(async function (res) {
    assert('stale marked', res && res.stale === true);
    assertEq('stale did not extra-navigate', navCalls, 0);

    // Exercise a failed catalogue with a valid bulk POST still available.
    root.prksWorkSelectionResetForTests();
    root.prksWorkSelectionEnter();
    root.prksWorkSelectionToggle('W-1');
    let bulkCalls = 0;
    root.bulkUpdateWorks = async function () { bulkCalls++; return { status: 'updated' }; };
    // This minimal DOM fixture does not parse nested markup: attach actual
    // sheet sub-elements for the catalogue-loading path.
    const fixture = document.getElementById('prks-bulk-sheet');
    for (const [cls, tag] of [
        ['prks-bulk-sheet__title', 'h3'], ['prks-bulk-sheet__body', 'div'],
        ['prks-bulk-sheet__confirm', 'p'], ['prks-bulk-sheet__error', 'p'],
    ]) {
        if (!fixture.querySelector('.' + cls)) {
            const el = document.createElement(tag);
            el.className = cls;
            fixture.appendChild(el);
        }
    }
    const requests = [];
    root.fetchFolders = async opts => {
        requests.push(opts);
        throw new Error('HTTP 500');
    };
    await root.prksWorkSelectionOpenSheet('folder');
    const sheet = document.getElementById('prks-bulk-sheet');
    const body = sheet.querySelector('.prks-bulk-sheet__body');
    const error = sheet.querySelector('.prks-bulk-sheet__error');
    const apply = sheet.querySelector('[data-bulk-apply]');
    assert('Folder reader requests strict source-aware results',
        !!requests[0].requireAvailable && !!requests[0].withSource);
    assert('failed Folder read shows retry, never No folder',
        body.innerHTML.includes('data-bulk-retry="folder"') &&
        !body.innerHTML.includes('No folder'));
    assert('failed Folder read is visible',
        error.textContent.includes('Could not load folders'));
    assert('Apply disabled for failed Folder read', !!apply.disabled);
    // Even if stale UI retained a checked radio it cannot unfile Works.
    const forged = document.createElement('input');
    forged.name = 'prks-bulk-folder';
    forged.setAttribute('name', 'prks-bulk-folder');
    forged.checked = true;
    forged.value = '';
    body.appendChild(forged);
    sheet.listeners.find(x => x.type === 'click').fn({ target: apply });
    await Promise.resolve();
    assertEq('failed Folder read emits no bulk POST', bulkCalls, 0);
    assert('failed catalogue remains closed even with a stale radio', apply.disabled);
    root.fetchFolders = async () => ({rows: [], source: 'network'});
    const retry = body.querySelector('[data-bulk-retry]');
    sheet.listeners.find(x => x.type === 'click').fn({ target: retry });
    await new Promise(resolve => setImmediate(resolve));
    assert('genuinely empty Folder catalogue offers No folder',
        body.innerHTML.includes('No folder'));
    root.fetchFolders = async () => ({
        rows: [{id: 'F-1',title:'Actual folder'}],source:'cache',
    });
    await root.prksWorkSelectionOpenSheet('folder');
    assert('cached Folder catalogue is labeled',
        body.innerHTML.includes('Using cached folders.') &&
        body.innerHTML.includes('Actual folder'));
    root.fetchTags = async () => { throw new Error('HTTP 500'); };
    await root.prksWorkSelectionOpenSheet('tags');
    assert('failed Tag read shows Retry instead of fake empty list',
        body.innerHTML.includes('data-bulk-retry="tags"') &&
        !body.innerHTML.includes('No tags in the library yet.'));
    assert('Tag failure disables Apply', !!apply.disabled);
    root.fetchTags = async () => ({rows:[],source:'network'});
    await root.prksWorkSelectionOpenSheet('tags');
    assert('genuinely empty Tag catalogue is recognized',
        body.innerHTML.includes('No tags in the library yet.'));
    let finishOld;
    root.fetchFolders = () => new Promise(resolve => { finishOld = resolve; });
    const pending = root.prksWorkSelectionOpenSheet('folder');
    await root.prksWorkSelectionOpenSheet('tags');
    finishOld({rows:[{id:'F-LATE',title:'Late folder'}],source:'network'});
    await pending;
    assert('stale Folder response cannot overwrite a different sheet',
        !body.innerHTML.includes('Late folder') &&
        body.innerHTML.includes('No tags in the library yet.'));
    assertEq('no unintended bulk mutation after failed/stale reads', bulkCalls, 0);

    // Run the production catalogue readers against controlled HTTP/cache
    // results: a failed domain read must remain distinct from valid empty.
    const appSource = fs.readFileSync(path.join(__dirname, '../../frontend/js/app.js'), 'utf8');
    const apiSource = fs.readFileSync(path.join(__dirname, '../../frontend/js/api.js'), 'utf8');
    function evaluateReader(source, marker, context) {
        const begin = source.indexOf(marker);
        const end = source.indexOf('\n}', begin) + 2;
        if (begin < 0 || end < 2) throw new Error('Unable to find ' + marker);
        vm.runInNewContext(source.slice(begin, end), context);
    }
    const catalog = {
        PRKS_FOLDERS_LIST_KEY: 'folders:index',
        PRKS_FOLDERS_DOMAIN: 'folders',
        prksEffectiveFolderRows: async rows => rows,
        prksOfflineReadList: async () => { throw new Error('HTTP 500'); },
    };
    // Evaluate actual production validators, not a permissive test stub:
    // malformed HTTP and older malformed cache must both fail closed.
    for (const helper of ['prksHasUsableRowId', 'prksIsOptionalString',
        'prksIsFolderCount', 'prksIsFolderParentId',
        'prksIsFolderSummaryShape', 'prksIsFoldersIndexShape']) {
        evaluateReader(appSource, 'function ' + helper + '(', catalog);
    }
    evaluateReader(appSource, 'async function prksEffectiveFolderCatalogue(', catalog);
    const strict = { requireAvailable: true, withSource: true };
    let rejected = false;
    try { await catalog.prksEffectiveFolderCatalogue(undefined, strict); }
    catch (_e) { rejected = true; }
    assert('Folder HTTP 500 propagates through strict catalogue reader', rejected);
    let received = await catalog.prksEffectiveFolderCatalogue();
    assert('other callers retain their historical unavailable fallback',
        Array.isArray(received) && received.length === 0);
    catalog.prksOfflineReadList = async () => ({value:null,source:'unavailable'});
    rejected = false;
    try { await catalog.prksEffectiveFolderCatalogue(undefined, strict); }
    catch (_e) { rejected = true; }
    assert('null unavailable Folder result fails strict read', rejected);
    let folderReadOptions;
    catalog.prksOfflineReadList = async (_key, _path, opts) => {
        folderReadOptions = opts;
        return {value:[],source:'network'};
    };
    received = await catalog.prksEffectiveFolderCatalogue(undefined, strict);
    assert('Folder reader enforces the same domain and schema as the Library',
        folderReadOptions.domain === 'folders' &&
        folderReadOptions.validate === catalog.prksIsFoldersIndexShape);
    assert('Folder validator rejects an array with missing IDs',
        !folderReadOptions.validate([{ title: 'Broken' }]));
    assert('valid empty Folder result succeeds strict read',
        Array.isArray(received.rows) && received.rows.length === 0 &&
        received.source === 'network');
    const validFolder = {id:'F-CACHED',title:'Cached',parent_id:null,
        work_count:0,child_count:0};
    catalog.prksOfflineReadList = async () => ({
        value:[validFolder],source:'cache',
    });
    received = await catalog.prksEffectiveFolderCatalogue(undefined, strict);
    assert('Folder cache result retains provenance',
        received.source === 'cache' && received.rows.length === 1);
    const malformed = [{title:'Missing ID and parent/count fields'}];
    for (const origin of ['server', 'cache']) {
        catalog.prksOfflineReadList = async () => ({value:malformed,source:origin});
        rejected = false;
        try { await catalog.prksEffectiveFolderCatalogue(undefined, strict); }
        catch (_e) { rejected = true; }
        assert('malformed ' + origin + ' Folder catalogue rejects strict bulk reads',
            rejected);
        received = await catalog.prksEffectiveFolderCatalogue();
        assert('malformed ' + origin + ' Folder catalogue is not presented as data',
            Array.isArray(received) && received.length === 0);
    }
    catalog.prksOfflineReadList = async () => ({value:[validFolder],source:'cache'});

    const apiContext = {
        URLSearchParams, prksApiErrorOwner: () => null, prksAbortFallback: () => false,
        prksSetApiError: () => {}, prksReportApiClientError: () => {},
        prksEffectiveFolderCatalogue: (...args) =>
            catalog.prksEffectiveFolderCatalogue(...args),
        prksReadTagsIndex: async () => ({value:null,source:'unavailable'}),
        prksApiSignal: () => undefined, prksCatalogReadPolicy: () => ({}),
        prksRequest: async () => ({ok:false,json:async()=>({})}),
        prksParseJsonResponse: async () => [],
    };
    evaluateReader(apiSource, 'async function fetchFolders(', apiContext);
    evaluateReader(apiSource, 'async function fetchTags(', apiContext);
    received = await apiContext.fetchFolders(strict);
    assert('strict Folder API propagates cache metadata',
        received.source === 'cache' && received.rows.length === 1);
    rejected = false;
    try { await apiContext.fetchTags(strict); } catch (_e) { rejected = true; }
    assert('Tag unavailable index fails strict read', rejected);
    received = await apiContext.fetchTags();
    assert('ordinary Tags callers retain empty fallback',
        Array.isArray(received) && received.length === 0);
    apiContext.prksReadTagsIndex = async () => ({value:[],source:'network'});
    received = await apiContext.fetchTags(strict);
    assert('valid empty Tag list succeeds strict read',
        received.source === 'network' && received.rows.length === 0);
    apiContext.prksReadTagsIndex = async () => ({
        value:[{id:'T-CACHED',name:'Known tag'}],source:'cache',
    });
    received = await apiContext.fetchTags(strict);
    assert('Tag cached result retains source',
        received.source === 'cache' && received.rows.length === 1);
    apiContext.prksReadTagsIndex = undefined;
    rejected = false;
    try { await apiContext.fetchTags(strict); } catch (_e) { rejected = true; }
    assert('direct HTTP failure for Tags fails strict read', rejected);
    apiContext.prksRequest = async () => ({
        ok:true,json:async()=>({not:'an array'}),
    });
    rejected = false;
    try { await apiContext.fetchTags(strict); } catch (_e) { rejected = true; }
    assert('invalid HTTP Tag JSON is not a valid empty catalogue', rejected);

    // Direct HTTP is used for used:true and when the index reader is absent.
    // withSource must never silently revert to an array-only result.
    const tagRows = [{id:'T-1',name:'Topic'}];
    apiContext.prksReportClientError = () => {};
    apiContext.prksApiErrorSource = () => 'tags';
    evaluateReader(apiSource, 'async function prksParseJsonResponse(', apiContext);
    apiContext.prksRequest = async () => ({ok:true,json:async()=>tagRows});
    received = await apiContext.fetchTags({used:true,withSource:true});
    assert('used Tags with source returns provenance alongside rows',
        received.source === 'network' && received.rows.length === 1);
    received = await apiContext.fetchTags({used:true});
    assert('used Tags without source retain the legacy array shape',
        Array.isArray(received) && received.length === 1);
    apiContext.prksRequest = async () => ({ok:false,status:503,json:async()=>({})});
    received = await apiContext.fetchTags({used:true,withSource:true});
    assert('non-strict HTTP failure has explicitly unavailable provenance',
        received.source === 'unavailable' && received.rows.length === 0);
    apiContext.prksRequest = async () => ({ok:true,status:200,
        json:async()=>{ throw new SyntaxError('Truncated Tag JSON'); }});
    received = await apiContext.fetchTags({used:true,withSource:true});
    assert('malformed HTTP 200 Tag JSON is unavailable rather than empty network data',
        received.source === 'unavailable' && received.rows.length === 0);
    received = await apiContext.fetchTags({used:true});
    assert('malformed HTTP 200 keeps legacy array fallback',
        Array.isArray(received) && received.length === 0);
    apiContext.prksRequest = async () => { throw new Error('Network offline'); };
    received = await apiContext.fetchTags({used:true,withSource:true});
    assert('non-strict Tags transport failure keeps the source envelope',
        received.source === 'unavailable' && received.rows.length === 0);

    const storeKeys = Object.keys(sandbox.localStorage.store);
    assert('no localStorage selection', storeKeys.every(function (k) { return k.indexOf('select') < 0; }));

    rows.forEach(function (r) {
        const mark = r.ok ? 'PASS' : 'FAIL';
        const extra = r.detail ? ' — ' + r.detail : '';
        console.log(mark + '  ' + r.name + extra);
    });
    console.log(passed + ' passed, ' + failed + ' failed');
    if (failed) process.exit(1);
}).catch(function (err) {
    console.error(err);
    process.exit(1);
});
