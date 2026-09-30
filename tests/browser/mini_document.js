'use strict';

/* A tiny document for Node selftests. The unit gate does not install jsdom. */

function Element() {}
function HTMLElement() {}
function Node() {}
HTMLElement.prototype = Object.create(Element.prototype);
Element.prototype = Object.create(Node.prototype);

const byId = new Map();

function isSpace(ch) {
    return ch === ' ' || ch === '\n' || ch === '\t' || ch === '\r' || ch === '\f';
}

function makeClassList(set) {
    return {
        add: function () {
            for (let i = 0; i < arguments.length; i += 1) {
                String(arguments[i] || '').split(/\s+/).forEach(function (name) {
                    if (name) set.add(name);
                });
            }
        },
        remove: function () {
            for (let i = 0; i < arguments.length; i += 1) set.delete(arguments[i]);
        },
        contains: function (name) { return set.has(name); },
        toggle: function (name, force) {
            const on = force === undefined ? !set.has(name) : !!force;
            if (on) set.add(name);
            else set.delete(name);
            return on;
        },
    };
}

function makeEl(tag) {
    const classes = new Set();
    const attrs = Object.create(null);
    const el = Object.create(HTMLElement.prototype);
    el._tag = tag;
    el._children = [];
    el._html = null;
    el._text = '';
    el.parentNode = null;
    el.nodeType = 1;
    el.id = '';
    el.value = '';
    el.title = '';
    el.disabled = false;
    el.hidden = false;
    el.textContent = '';
    el.dataset = {};
    el.style = {};
    el.onclick = null;
    el.classList = makeClassList(classes);
    el.setAttribute = function (name, value) {
        attrs[name] = String(value);
        if (name === 'id') {
            if (el.id) byId.delete(el.id);
            el.id = String(value);
            byId.set(el.id, el);
        } else if (name === 'class') {
            classes.clear();
            el.classList.add(value);
        } else if (name === 'value') {
            el.value = String(value);
        }
    };
    el.getAttribute = function (name) {
        return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null;
    };
    el.removeAttribute = function (name) {
        delete attrs[name];
        if (name === 'id' && el.id) {
            byId.delete(el.id);
            el.id = '';
        }
    };
    el.addEventListener = function () {};
    el.removeEventListener = function () {};
    el.focus = function () {};
    el.after = function (node) {
        const parent = el.parentNode;
        if (!parent) return;
        const at = parent._children.indexOf(el);
        if (at === -1) parent._children.push(node);
        else parent._children.splice(at + 1, 0, node);
        node.parentNode = parent;
    };
    el.appendChild = function (child) {
        el._children.push(child);
        child.parentNode = el;
        return child;
    };
    el.contains = function (other) {
        let node = other;
        while (node) {
            if (node === el) return true;
            node = node.parentNode;
        }
        return false;
    };
    el.querySelector = function (sel) {
        const found = [];
        collect(el, sel, found, false);
        return found[0] || null;
    };
    el.querySelectorAll = function (sel) {
        const found = [];
        collect(el, sel, found, true);
        return found;
    };
    Object.defineProperty(el, 'innerHTML', {
        get: function () { return el._html != null ? el._html : el._text; },
        set: function (value) {
            el._html = String(value);
            el._text = '';
            el._children = [];
            parseInto(el, el._html);
        },
    });
    Object.defineProperty(el, 'className', {
        get: function () { return Array.from(classes).join(' '); },
        set: function (value) {
            classes.clear();
            el.classList.add(value);
            attrs.class = el.className;
        },
    });
    return el;
}

function matches(el, sel) {
    if (!sel) return false;
    const comma = sel.indexOf(',');
    if (comma !== -1) {
        return matches(el, sel.slice(0, comma).trim()) || matches(el, sel.slice(comma + 1).trim());
    }
    if (sel.charAt(0) === '.') return el.classList.contains(sel.slice(1));
    if (sel.charAt(0) === '#') return el.id === sel.slice(1);
    if (sel.charAt(0) === '[' && sel.charAt(sel.length - 1) === ']') {
        const body = sel.slice(1, -1);
        const eq = body.indexOf('=');
        if (eq === -1) return el.getAttribute(body) != null;
        const name = body.slice(0, eq);
        let value = body.slice(eq + 1);
        const quote = value.charAt(0);
        if ((quote === '"' || quote === "'") && value.charAt(value.length - 1) === quote) {
            value = value.slice(1, -1);
        }
        return el.getAttribute(name) === value;
    }
    return el._tag === sel.toLowerCase();
}

function collect(el, sel, found, all) {
    const kids = el._children || [];
    for (let i = 0; i < kids.length; i += 1) {
        const child = kids[i];
        if (matches(child, sel)) {
            found.push(child);
            if (!all) return;
        }
        collect(child, sel, found, all);
        if (!all && found.length) return;
    }
}

function applyAttrs(el, raw) {
    const s = String(raw || '');
    let i = 0;
    while (i < s.length) {
        while (i < s.length && isSpace(s.charAt(i))) i += 1;
        if (i >= s.length) break;
        if (s.charAt(i) === '/') {
            i += 1;
            continue;
        }
        const nameStart = i;
        while (i < s.length && s.charAt(i) !== '=' && !isSpace(s.charAt(i))) i += 1;
        const name = s.slice(nameStart, i);
        if (!name) {
            i += 1;
            continue;
        }
        let value = '';
        if (s.charAt(i) === '=') {
            i += 1;
            const quote = s.charAt(i);
            if (quote === '"' || quote === "'") {
                const end = s.indexOf(quote, i + 1);
                if (end === -1) {
                    value = s.slice(i + 1);
                    i = s.length;
                } else {
                    value = s.slice(i + 1, end);
                    i = end + 1;
                }
            } else {
                const start = i;
                while (i < s.length && !isSpace(s.charAt(i))) i += 1;
                value = s.slice(start, i);
            }
        }
        el.setAttribute(name, value);
    }
}

function appendText(el, text) {
    if (text && text.indexOf('<') === -1) el._text += text;
}

function parseInto(parent, html) {
    const stack = [parent];
    const s = String(html || '');
    let i = 0;
    while (i < s.length) {
        const lt = s.indexOf('<', i);
        if (lt === -1) {
            appendText(stack[stack.length - 1], s.slice(i));
            break;
        }
        if (lt > i) appendText(stack[stack.length - 1], s.slice(i, lt));
        const gt = s.indexOf('>', lt + 1);
        if (gt === -1) break;
        let raw = s.slice(lt + 1, gt).trim();
        i = gt + 1;
        if (!raw) continue;
        const closing = raw.charAt(0) === '/';
        if (closing) raw = raw.slice(1).trim();
        let selfClose = false;
        if (raw.charAt(raw.length - 1) === '/') {
            selfClose = true;
            raw = raw.slice(0, -1).trim();
        }
        let j = 0;
        while (j < raw.length && !isSpace(raw.charAt(j))) j += 1;
        const tag = raw.slice(0, j).toLowerCase();
        if (!tag) continue;
        if (closing) {
            if (stack.length > 1 && stack[stack.length - 1]._tag === tag) stack.pop();
            continue;
        }
        const el = makeEl(tag);
        applyAttrs(el, raw.slice(j));
        const top = stack[stack.length - 1];
        top._children.push(el);
        el.parentNode = top;
        const voidTag = selfClose || tag === 'input' || tag === 'br' || tag === 'img' ||
            tag === 'hr' || tag === 'meta' || tag === 'link';
        if (!voidTag) stack.push(el);
    }
}

function installMiniDocument(html) {
    byId.clear();
    const body = makeEl('body');
    parseInto(body, html);
    const documentElement = makeEl('html');
    documentElement.appendChild(body);
    const storage = new Map();
    const document = {
        body: body,
        documentElement: documentElement,
        activeElement: null,
        nodeType: 9,
        getElementById: function (id) { return byId.get(String(id)) || null; },
        createElement: function (tag) { return makeEl(String(tag || 'div').toLowerCase()); },
        querySelector: function (sel) { return body.querySelector(sel); },
        querySelectorAll: function (sel) { return body.querySelectorAll(sel); },
        addEventListener: function () {},
        removeEventListener: function () {},
        contains: function (node) { return body.contains(node) || node === body; },
    };
    const root = typeof globalThis !== 'undefined' ? globalThis : global;
    root.window = root;
    root.document = document;
    root.HTMLElement = HTMLElement;
    root.Node = Node;
    root.Element = Element;
    root.localStorage = {
        getItem: function (key) { return storage.has(String(key)) ? storage.get(String(key)) : null; },
        setItem: function (key, value) { storage.set(String(key), String(value)); },
        removeItem: function (key) { storage.delete(String(key)); },
    };
    root.getComputedStyle = function () {
        return { getPropertyValue: function () { return ''; } };
    };
    root.location = { hash: '', href: 'http://127.0.0.1:8765/', origin: 'http://127.0.0.1:8765', pathname: '/' };
    root.requestAnimationFrame = function (fn) {
        return setTimeout(function () { fn(Date.now()); }, 0);
    };
    root.cancelAnimationFrame = function (id) { clearTimeout(id); };
    return document;
}

module.exports = { installMiniDocument };
