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

// Import the real production helpers using works.js's existing Node export
// guard. This avoids executing extracted source strings in a test VM.
global.window = global;
const workApi = require('../../frontend/js/components/works.js');
for (const name of ['Category]] Theory',
    'Math [A] ' + String.fromCharCode(92) + ' Slash ]] name',
    'Odd]] [[concept:Synthetic Memory Systems|pipe 1022',
    'Foucault [1977]', 'Set]', 'Foo\\Bar\u0091',
    'Ordinary Concept']) {
    const marker = workApi.prksFormatConceptMarkup(name);
    const parsed = api.prksParseResearchMarkup(marker).conceptRefs;
    record('picker round-trips name: ' + name,
        parsed.length === 1 && parsed[0].name === name && parsed[0].raw === marker);
    if (name.indexOf(']]') >= 0) {
        record('picker avoids truncated Concept for ' + name,
            marker.startsWith('[[concept-encoded:') && !marker.includes('[[concept:' ));
    }
}
const unresolvedName = 'Category]] Theory';
const encodedRef = workApi.prksFormatConceptMarkup(unresolvedName);
const unresolvedHtml = api.prksReplaceResearchRefs(encodedRef, { concepts: [], arguments: [] });
record('unresolved encoded name renders without markup escapes',
    unresolvedHtml.includes(unresolvedName) && !unresolvedHtml.includes('%5D') &&
    unresolvedHtml.includes('wiki-link-unresolved'));

// Execute the shipped autocomplete code, with a current Work notes editor
// as owner; completion must replace the entire opening marker.
const partial = '[[concept:Pick';
const cmCalls = [];
const fakeCM = {
    constructor: { Pos: (line, ch) => ({ line, ch }) },
    getCursor: () => ({ line: 0, ch: partial.length }),
    getLine: () => partial,
    replaceRange: (value, from, to, origin) => cmCalls.push({ value, from, to, origin }),
};
fakeCM.__prksOwnerCtx = {
    getResource: (key) => {
        if (key === 'workNotes') return { editor: { codemirror: fakeCM } };
        if (key === 'conceptHintList') return [{ id: 'C-1', name: 'Pick]] Me' }];
        return null;
    },
};
const completionCtx = workApi.prksGetConceptLinkAutocompleteContext(fakeCM);
workApi.prksConceptLinkCompletionPick(fakeCM, completionCtx, {
    text: 'Pick]] Me',
    from: completionCtx.from,
    to: completionCtx.to,
});
record('autocomplete replaces entire marker for delimiter-heavy Concept',
    cmCalls.length === 1 &&
    cmCalls[0].from.ch === 0 &&
    cmCalls[0].value === '[[concept-encoded:Pick%5D%5D%20Me]]' &&
    cmCalls[0].origin === 'complete');

// The actual picker uses the same declarative options; inject only the DOM
// insertion boundary so Node can execute its real selection/Create callbacks.
const inserted = [];
const pickerOptions = workApi.prksConceptPickerOptions(
    fakeCM, (_cm, markup) => { inserted.push(markup); });
pickerOptions.onPick('C-1');
pickerOptions.onCreate('Odd]] [[concept:Injected|pipe');
record('existing Concept picker inserts encoded reference', inserted[0] ===
    '[[concept-encoded:Pick%5D%5D%20Me]]');
const chosen = api.prksParseResearchMarkup(inserted[1]);
record('Create picker does not insert nested/bogus references',
    chosen.conceptRefs.length === 1 &&
    chosen.conceptRefs[0].name === 'Odd]] [[concept:Injected|pipe');


// Later wiki/PDF passes must only touch original text, never a generated
// Concept name containing [[Work]] or [[pdf:id]] delimiters.
global.prksReplaceResearchRefs = api.prksReplaceResearchRefs;
const innerName = 'Compare [[Hamlet]]';
const innerMarker = workApi.prksFormatConceptMarkup(innerName);
for (const found of [true, false]) {
    const preview = workApi.prksRenderResearchNotesLinks(innerMarker,
        { concepts: found ? [{ id: 'C-COMPARE', name: innerName }] : [] },
        { hamlet: 'W-HAMLET' });
    record((found ? 'resolved' : 'unresolved') + ' Concept label cannot create nested Work link',
        preview.includes(innerName) &&
        !preview.includes('#/works/W-HAMLET') &&
        preview.includes(found ? '#/concepts/C-COMPARE' : 'wiki-link-unresolved'));
}
const pdfName = 'Compare [[pdf:A-PDF]]';
const pdfPreview = workApi.prksRenderResearchNotesLinks(
    workApi.prksFormatConceptMarkup(pdfName),
    { concepts: [{ id: 'C-PDF', name: pdfName }] }, {});
record('Concept label cannot create a nested PDF annotation link',
    pdfPreview.includes(pdfName) && !pdfPreview.includes('wiki-link-pdf-ann'));
const literalMarkers = '\\[[concept-encoded:Test%20One]] and `[[concept-encoded:Test%20One]]`';
const literalPreview = workApi.prksRenderResearchNotesLinks(
    literalMarkers, { concepts: [] }, { 'concept-encoded:test%20one': 'W-WRONG' });
record('escaped and inline-code encoded markers stay literal in Work preview',
    literalPreview === literalMarkers && !literalPreview.includes('#/works/'));
const normalPreview = workApi.prksRenderResearchNotesLinks(
    'A normal [[Hamlet]] reference.', { concepts: [] }, { hamlet: 'W-HAMLET' });
record('ordinary Work wiki markers still resolve', normalPreview.includes('#/works/W-HAMLET'));

rows.forEach(function (r) {
    console.log((r.ok ? 'PASS  ' : 'FAIL  ') + r.name + (r.detail ? ' ' + r.detail : ''));
});
console.log(passed + ' passed, ' + failed + ' failed');
process.exit(failed ? 1 : 0);
