#!/usr/bin/env node
'use strict';

/*
 * prksGetWikiLinkAutocompleteContext in isolation.
 *
 * The function only touches cm.getCursor(), cm.getLine() and cm.constructor.Pos,
 * so it runs against a tiny CodeMirror stand-in -- no EasyMDE, no DOM. Sliced
 * out of works.js the same way run_research_graph_offline_selftest.js slices
 * app.js, so the assertions run against the shipped source, not a copy.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '../..');
const worksSrc = fs.readFileSync(path.join(rootDir, 'frontend/js/components/works.js'), 'utf8');

const START = 'function prksGetWikiLinkAutocompleteContext(cm) {';
const END = 'function prksFilterWorksForWikiHint(';
const startAt = worksSrc.indexOf(START);
const endAt = worksSrc.indexOf(END);
if (startAt === -1 || endAt === -1 || endAt < startAt) {
    console.error('FAIL  could not slice prksGetWikiLinkAutocompleteContext out of works.js');
    process.exit(1);
}

const sandbox = { console };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(worksSrc.slice(startAt, endAt), sandbox);
const contextOf = sandbox.prksGetWikiLinkAutocompleteContext;

/* Minimal CodeMirror stand-in: one line, cursor at `ch`. */
class FakeCM {
    constructor(text, ch) {
        this.text = text;
        this.ch = typeof ch === 'number' ? ch : text.length;
    }
    getCursor() {
        return { line: 0, ch: this.ch };
    }
    getLine() {
        return this.text;
    }
    static Pos(line, ch) {
        return { line: line, ch: ch };
    }
}

let passed = 0;
let failed = 0;
const rows = [];

function record(name, ok, detail) {
    rows.push({ name: name, ok: !!ok, detail: detail || '' });
    if (ok) passed += 1;
    else failed += 1;
}

function assertNull(name, text, ch) {
    const ctx = contextOf(new FakeCM(text, ch));
    record(name, ctx === null, ctx === null ? '' : 'got ' + JSON.stringify(ctx));
}

/* A hint is only useful when `query` is the text the completion will replace,
 * i.e. exactly line[from.ch .. cursor). Both halves are asserted together. */
function assertContext(name, text, ch, expectedQuery, expectedFromCh) {
    const cm = new FakeCM(text, ch);
    const ctx = contextOf(cm);
    if (!ctx) {
        record(name, false, 'got null');
        return;
    }
    const replaced = cm.text.slice(ctx.from.ch, ctx.to.ch);
    const ok =
        ctx.query === expectedQuery &&
        ctx.from.ch === expectedFromCh &&
        ctx.from.line === 0 &&
        ctx.to.ch === cm.ch &&
        replaced === ctx.query;
    record(
        name,
        ok,
        ok
            ? ''
            : 'query=' +
                  JSON.stringify(ctx.query) +
                  ' from.ch=' +
                  ctx.from.ch +
                  ' replaced=' +
                  JSON.stringify(replaced)
    );
}

record('function sliced out of works.js', typeof contextOf === 'function');

/* Plain opener. */
assertContext('bare opener yields an empty query', '[[', 2, '', 2);
assertContext('opener plus text', '[[abc', 5, 'abc', 2);
assertContext('opener mid-line', 'See [[ab', 8, 'ab', 6);
assertContext('cursor before the closing text', '[[ab]] tail', 4, 'ab', 2);

/* Not a wiki-link position. */
assertNull('no opener on the line', 'plain notes text', 16);
assertNull('closed link is not an open query', '[[a]] ', 6);
assertNull('pipe ends the query', '[[a|b', 5);
assertNull('reserved concept prefix defers', '[[concept:x', 11);
assertNull('reserved argument prefix defers', '[[argument:', 11);
assertNull('reserved pdf prefix defers', '[[pdf:p', 7);

/* Regression: a second opener on the same line. The query must come from the
 * opener `from` points at (the last one), not the leftmost one -- otherwise the
 * candidate list is filtered on text the completion would not replace. */
assertContext('nested opener queries the last opener', '[[a[[b', 6, 'b', 5);
assertContext('opener after a closed link', '[[a]]x[[b', 9, 'b', 8);
assertNull('nested reserved prefix still defers', '[[x[[concept:', 13);

/* The old /\[\[([^\]|]*)$/ scan was super-linear: a run of openers that cannot
 * produce a match made it retry the tail from every one of them. This input
 * took ~5s through the regex and is immediate through lastIndexOf. Generous
 * bound -- the point is that the cost is no longer quadratic in line length,
 * not a precise timing. */
const longLine = '['.repeat(120000) + ']';
const t0 = Date.now();
const longCtx = contextOf(new FakeCM(longLine, longLine.length));
const elapsed = Date.now() - t0;
record(
    'unmatchable opener run stays linear',
    longCtx === null && elapsed < 1500,
    'ctx=' + JSON.stringify(longCtx) + ' elapsed=' + elapsed + 'ms'
);

rows.forEach(function (r) {
    console.log((r.ok ? 'PASS  ' : 'FAIL  ') + r.name + (r.detail ? ' ' + r.detail : ''));
});
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);
