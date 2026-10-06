#!/usr/bin/env node
'use strict';

/**
 * Migration safety net: the frozen legacy hash parser versus the typed route
 * model, through the production navigation.js aliases. Not loaded by the app.
 * Retire with the oracle once the port no longer needs comparing.
 *
 * The corpus is every hash-like string literal in frontend/js, frontend-app/src,
 * and tests, plus generated route/segment/query combinations and non-string
 * inputs. Each parsed record must be deep-equal, including key order.
 */

const fs = require('fs');
const path = require('path');
const util = require('util');

const rootDir = path.resolve(__dirname, '../..');
const oracle = require(path.join(rootDir, 'tests/browser/fixtures/route-parser-legacy-oracle.js'));
const model = require(path.join(rootDir, 'frontend/js/route-model.js'));
const nav = require(path.join(rootDir, 'frontend/js/navigation.js'));

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    if (!ok) console.log('FAIL  ' + name + (detail ? ' ' + detail : ''));
}

function sameRecord(got, want) {
    if (!util.isDeepStrictEqual(got, want)) return false;
    if (!got || typeof got !== 'object') return true;
    return JSON.stringify(Object.keys(got)) === JSON.stringify(Object.keys(want));
}

function harvest(corpus) {
    const skip = new Set(['node_modules', '__pycache__', 'storybook-static', 'fixtures']);
    const walk = function (dir) {
        fs.readdirSync(dir, { withFileTypes: true }).forEach(function (entry) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) {
                if (!skip.has(entry.name)) walk(full);
                return;
            }
            if (!/\.(js|py|ts|vue)$/.test(entry.name)) return;
            const src = fs.readFileSync(full, 'utf8');
            for (const m of src.matchAll(/['"`](#\/[^'"`\s]*)['"`]/g)) corpus.add(m[1]);
        });
    };
    ['frontend/js', 'frontend-app/src', 'tests'].forEach(function (rel) {
        walk(path.join(rootDir, rel));
    });
}

function generate(corpus) {
    const segs = [
        '', 'x', '12', 'a%20b', '%E0%A4', '..', '/', 'abc/def', 'People', 'Author', 'author', 'Mentioned',
        'In%20Progress', 'in-progress', 'completed', 'concept:abc', 'work:1.2-x', 'bad:1', '?', '?q=1',
        '?status=Planned', '?status=%E0%A4', '?focus=concept:a1', '?focus=bad:1', '?kind=objection',
        '?q=a&author=b&sort=x&tag=1&tag=2&publisher=p&any=1',
    ];
    const bases = [
        '#/', '#/folders/', '#/works/', '#/people/', '#/people/role/', '#/people/groups/', '#/progress/',
        '#/progress', '#/graph', '#/search', '#/arguments/', '#/concepts/', '#/positions/', '#/playlists/',
        '#/publishers/', '#/types/', '#/tags/', '#/views/', '#/settings/', '#/processing-files', '#/recent',
    ];
    Object.keys(oracle.PRKS_ROUTE_META).forEach(function (name) {
        bases.push('#/' + name, '#/' + name + '/');
    });
    bases.forEach(function (base) {
        segs.forEach(function (seg) {
            corpus.add(base + seg);
            corpus.add(base + seg + '/' + seg);
            corpus.add(base.replace('#', '') + seg);
        });
    });
    [null, undefined, '', ' ', '#', '#/', 'folders', '/folders', ' #/works/1 ', 123, 0, {}, [],
        '#/works/1#x', '#/WORKS/1', '#//works//1', '#/works/1?', '#/works/1/'].forEach(function (input) {
        corpus.add(input);
    });
}

const corpus = new Set();
harvest(corpus);
generate(corpus);

let routeDiffs = 0;
corpus.forEach(function (input) {
    const want = oracle.prksParseRoute(input);
    const viaModel = model.parseRoute(input);
    const viaNav = nav.prksParseRoute(input);
    const label = JSON.stringify(input === undefined ? '<undefined>' : input);
    if (!sameRecord(viaModel, want)) {
        routeDiffs += 1;
        record('parseRoute ' + label, false, 'got=' + JSON.stringify(viaModel) + ' want=' + JSON.stringify(want));
    } else {
        record('parseRoute ' + label, true);
    }
    record('navigation alias ' + label, sameRecord(viaNav, want));
    record(
        'isRecognized ' + label,
        nav.prksIsRecognizedRoute(viaNav) === oracle.prksIsRecognizedRoute(want),
    );
});

record('corpus is broad', corpus.size > 3000, 'size=' + corpus.size);
record('registry', sameRecord(model.ROUTE_META, oracle.PRKS_ROUTE_META));
record('navigation registry alias', nav.PRKS_ROUTE_META === model.ROUTE_META);
record('home hash', nav.PRKS_HOME_HASH === oracle.PRKS_HOME_HASH);
record('progress statuses', sameRecord(nav.PRKS_PROGRESS_STATUS_VALUES, oracle.PRKS_PROGRESS_STATUS_VALUES));
record('people roles', sameRecord(nav.PRKS_PEOPLE_ROLES, oracle.PRKS_PEOPLE_ROLES));

[['concept', 'a1'], ['work', '1.2-x'], ['person', 'a b'], ['bad', 'x'], ['work', ''], ['', 'a'], [null, null]]
    .forEach(function (pair) {
        record(
            'graphFocusHash ' + JSON.stringify(pair),
            nav.prksGraphFocusHash(pair[0], pair[1]) === oracle.prksGraphFocusHash(pair[0], pair[1]),
        );
    });
['', 'focus=concept:a1', 'focus=bad:1', 'focus=work:%E0%A4', 'x=1&focus=person:p-1', 'focus=']
    .forEach(function (query) {
        record(
            'parseGraphFocus ' + JSON.stringify(query),
            nav.prksParseGraphFocus(query) === oracle.prksParseGraphFocus(query),
        );
    });

console.log('corpus ' + corpus.size + ', parseRoute diffs ' + routeDiffs);
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);
