#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '../..');
const src = fs.readFileSync(path.join(rootDir, 'frontend/js/research-links.js'), 'utf8');
const fixtures = JSON.parse(
    fs.readFileSync(path.join(rootDir, 'tests/fixtures/research_markup.json'), 'utf8')
);

const sandbox = { console, module: { exports: {} }, exports: {}, require };
sandbox.global = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const api = sandbox.module.exports;

let passed = 0;
let failed = 0;
const rows = [];

function record(name, ok, detail) {
    rows.push({ name, ok: !!ok, detail: detail || '' });
    if (ok) passed += 1;
    else failed += 1;
}

fixtures.forEach(function (row) {
    const markup = api.prksParseResearchMarkup(row.text);
    const names = markup.conceptRefs.map(function (r) { return r.name; });
    const args = markup.argumentRefs.map(function (r) { return r.argumentId; });
    const okNames = JSON.stringify(names) === JSON.stringify(row.concepts);
    const okArgs = JSON.stringify(args) === JSON.stringify(row.arguments);
    record(row.id + ' concepts', okNames, okNames ? '' : JSON.stringify(names));
    record(row.id + ' arguments', okArgs, okArgs ? '' : JSON.stringify(args));
});

const xss = api.prksReplaceResearchRefs(
    'See [[concept:<script>alert(1)</script>]] and [[argument:A-1|<img src=x onerror=alert(1)>]].',
    {
        concepts: [{ id: 'C-1', name: '<script>alert(1)</script>' }],
        arguments: [{ id: 'A-1', name: 'safe' }],
    }
);
record('escapes angle brackets', xss.indexOf('<script') === -1);
record('no raw img tag', xss.indexOf('<img') === -1);
record('no javascript url', xss.toLowerCase().indexOf('javascript:') === -1);
record('hash destinations', xss.indexOf('#/concepts/C-1') >= 0 && xss.indexOf('#/arguments/A-1') >= 0);

const missingArg = api.prksReplaceResearchRefs(
    'See [[argument:A-MISSING]] and [[argument:A-REAL|ok]].',
    {
        concepts: [],
        arguments: [{ id: 'A-REAL', name: 'ok' }],
    }
);
record(
    'missing argument unresolved',
    missingArg.indexOf('wiki-link-unresolved') >= 0 &&
        missingArg.indexOf('#/arguments/A-MISSING') === -1
);
record('valid argument remains linked', missingArg.indexOf('#/arguments/A-REAL') >= 0);

rows.forEach(function (r) {
    console.log((r.ok ? 'PASS  ' : 'FAIL  ') + r.name + (r.detail ? ' ' + r.detail : ''));
});
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);
