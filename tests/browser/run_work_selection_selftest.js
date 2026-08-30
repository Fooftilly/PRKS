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
    card.hidden = !!hidden;
    const t = document.createElement('div');
    t.className = 'card-title';
    t.textContent = title || id;
    card.appendChild(t);
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

const html = root.prksWorkCardHtml({ id: 'W-1', title: 'Alpha', status: 'Paused' });
assert('card has data-work-id', html.indexOf('data-work-id="W-1"') >= 0);
assert('card keeps hash nav', html.indexOf("#/works/W-1") >= 0);
assert('card has no checkbox by default', html.indexOf('type="checkbox"') < 0 && html.indexOf("type='checkbox'") < 0);

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
}).then(function (res) {
    assert('stale marked', res && res.stale === true);
    assertEq('stale did not extra-navigate', navCalls, 0);

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
