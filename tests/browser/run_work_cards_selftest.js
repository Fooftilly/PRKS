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

const sandbox = {
    console: console,
    window: null,
    globalThis: null,
    localStorage: localStorageShim,
    prksInferWorkSourceKind: prksInferWorkSourceKind,
    module: { exports: {} },
    require: require,
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
