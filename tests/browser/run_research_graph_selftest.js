#!/usr/bin/env node
'use strict';

const path = require('path');
const fs = require('fs');
const vm = require('vm');

function record(rows, name, ok, detail) {
    rows.push({ name: name, ok: !!ok, detail: detail || '' });
}

function assertEq(rows, name, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want) || got === want;
    record(rows, name, ok, ok ? '' : 'got ' + JSON.stringify(got) + ' want ' + JSON.stringify(want));
}

function assert(rows, name, cond, detail) {
    record(rows, name, !!cond, detail);
}

const src = fs.readFileSync(
    path.join(__dirname, '..', '..', 'frontend', 'js', 'components', 'research-graph.js'),
    'utf8'
);

const navigated = [];
const fakeElements = [];
let liveInstances = 0;

function fakeCollection(items) {
    const arr = items || [];
    return {
        length: arr.length,
        empty: function () {
            return !arr.length;
        },
        forEach: function (fn) {
            arr.forEach(fn);
        },
        unselect: function () {},
        id: function () {
            return arr[0] ? arr[0].id : '';
        },
        select: function () {},
        addClass: function () {},
        removeClass: function () {},
        style: function () {},
        source: function () {
            return { id: function () { return arr[0] && arr[0].source; } };
        },
        target: function () {
            return { id: function () { return arr[0] && arr[0].target; } };
        },
        selected: function () {
            return false;
        },
    };
}

function fakeCytoscape(opts) {
    liveInstances += 1;
    const nodes = (opts.elements || []).filter((e) => e.group === 'nodes');
    const edges = (opts.elements || []).filter((e) => e.group === 'edges');
    const cy = {
        destroyed: false,
        _handlers: 0,
        destroy: function () {
            this.destroyed = true;
            liveInstances -= 1;
        },
        layout: function () {
            return {
                run: function () {},
                one: function (_ev, fn) {
                    if (typeof fn === 'function') fn();
                    return this;
                },
            };
        },
        on: function () {
            this._handlers += 1;
        },
        batch: function (fn) {
            fn();
        },
        nodes: function () {
            return fakeCollection(nodes.map((n) => ({ id: n.data.id })));
        },
        edges: function () {
            return fakeCollection(edges.map((e) => ({ id: e.data.id, source: e.data.source, target: e.data.target })));
        },
        elements: function () {
            return fakeCollection([]);
        },
        getElementById: function (id) {
            const hit = nodes.find((n) => n.data.id === id);
            if (!hit) return fakeCollection([]);
            const el = fakeCollection([{ id: id }]);
            el.select = function () {};
            return el;
        },
        animate: function () {},
        fit: function () {},
    };
    fakeElements.push(cy);
    return cy;
}

const sandbox = {
    window: {},
    global: {},
    module: { exports: {} },
    cytoscape: fakeCytoscape,
    prksNavigate: function (hash) {
        navigated.push(hash);
    },
    prksEscapeHtml: function (s) {
        return String(s == null ? '' : s);
    },
    fetchResearchGraph: async function () {
        return fixture;
    },
    console: console,
};
sandbox.window = sandbox;
sandbox.global = sandbox;
sandbox.root = sandbox;

vm.runInNewContext(src, sandbox);

const g = sandbox.module.exports;

const fixture = {
    nodes: [
        {
            id: 'concept:C-1',
            record_id: 'C-1',
            type: 'concept',
            label: 'Culture Industry',
            route: '#/concepts/C-1',
        },
        {
            id: 'position:P-1',
            record_id: 'P-1',
            type: 'position',
            label: 'Standardization thesis',
            route: '#/positions/P-1',
        },
        {
            id: 'argument:A-1',
            record_id: 'A-1',
            type: 'argument',
            kind: 'argument',
            label: 'Standardization argument',
            route: '#/arguments/A-1',
        },
        {
            id: 'work:W-1',
            record_id: 'W-1',
            type: 'work',
            label: 'Dialectic of Enlightenment',
            route: '#/works/W-1',
            doc_type: 'book',
        },
        {
            id: 'person:P-1',
            record_id: 'P-1',
            type: 'person',
            label: 'Max Horkheimer',
            route: '#/people/P-1',
        },
    ],
    edges: [
        {
            id: 'argument_position:argument:A-1>position:P-1',
            type: 'argument_position',
            source: 'argument:A-1',
            target: 'position:P-1',
            verdict_id: 'supports',
            verdict_label: 'Supports',
        },
        {
            id: 'argument_source:argument:A-1>work:W-1',
            type: 'argument_source',
            source: 'argument:A-1',
            target: 'work:W-1',
            pages: '12-14',
        },
        {
            id: 'mentions_concept:work:W-1>concept:C-1',
            type: 'mentions_concept',
            source: 'work:W-1',
            target: 'concept:C-1',
            count: 3,
        },
        {
            id: 'work_author:person:P-1>work:W-1',
            type: 'work_author',
            source: 'person:P-1',
            target: 'work:W-1',
        },
    ],
    meta: { derived_note_edges_available: true, people_included: true },
};

const rows = [];
const els = g.toCytoscapeElements(fixture);
const nodeEl = els.find((e) => e.data.id === 'concept:C-1');
const stanceCheck = g.nodeClasses({ type: 'argument', kind: 'stance' });
const sourceEdge = els.find((e) => e.data.type === 'argument_source');
const mentionEdge = els.find((e) => e.data.type === 'mentions_concept');
assert(rows, 'node class mapping', nodeEl && nodeEl.classes.indexOf('graph-node--concept') >= 0);
assert(rows, 'stance marker class', stanceCheck.indexOf('graph-node--stance') >= 0);
assert(rows, 'source edge class', sourceEdge && sourceEdge.classes.indexOf('graph-edge--source') >= 0);
assert(rows, 'mention edge class', mentionEdge && mentionEdge.classes.indexOf('graph-edge--mentions') >= 0);
assertEq(rows, 'verdict label', sourceEdge.data.pages, '12-14');
assertEq(
    rows,
    'mention display label',
    g.displayLabelForEdge(fixture.edges[2]),
    'Mentioned in research notes (3)'
);
assertEq(rows, 'source display label', g.displayLabelForEdge(fixture.edges[1]), 'Made/taken in');
assertEq(rows, 'verdict display label', g.displayLabelForEdge(fixture.edges[0]), 'Supports');
assertEq(rows, 'concept route', nodeEl.data.route, '#/concepts/C-1');
assert(rows, 'source vs mention distinct classes', sourceEdge.classes !== mentionEdge.classes);

const allOn = g.defaultGraphFilters();
allOn.people = true;
const vis = g.visibleGraph(fixture, allOn);
assertEq(rows, 'all nodes visible', vis.nodes.length, 5);
const noWorks = Object.assign({}, allOn, { works: false });
const hidden = g.visibleGraph(fixture, noWorks);
assert(
    rows,
    'works hidden',
    hidden.nodes.every((n) => n.type !== 'work')
);
assert(
    rows,
    'work edges hidden',
    hidden.edges.every((e) => e.source.indexOf('work:') !== 0 && e.target.indexOf('work:') !== 0)
);
const again = g.visibleGraph(fixture, allOn);
assertEq(rows, 'works restored from model', again.nodes.length, 5);
assertEq(rows, 'fixture nodes unchanged', fixture.nodes.length, 5);

const hits = g.findNodesByLabel(fixture, 'culture');
assertEq(rows, 'find culture', hits.length, 1);
assertEq(rows, 'find culture id', hits[0].id, 'concept:C-1');
const none = g.findNodesByLabel(fixture, 'zzzz-no-match');
assertEq(rows, 'find no match', none.length, 0);

const insp = g.inspectorModel(fixture, 'concept:C-1');
assertEq(rows, 'inspector type', insp.typeLabel, 'Concept');
assertEq(rows, 'inspector label', insp.label, 'Culture Industry');
assertEq(rows, 'inspector open', insp.openLabel, 'Open Concept');
assert(
    rows,
    'inspector note-linked works',
    insp.stats.some((s) => s.label === 'Note-linked works' && s.value === 1)
);

g.openSelectedRecord(fixture.nodes[0]);
assertEq(rows, 'open concept navigates', navigated[navigated.length - 1], '#/concepts/C-1');
g.openSelectedRecord(fixture.nodes[1]);
assertEq(rows, 'open position navigates', navigated[navigated.length - 1], '#/positions/P-1');
g.openSelectedRecord(fixture.nodes[2]);
assertEq(rows, 'open argument navigates', navigated[navigated.length - 1], '#/arguments/A-1');

sandbox.destroyResearchGraph();
sandbox.destroyResearchGraph();
assertEq(rows, 'destroy is idempotent', sandbox.__prksResearchGraphLiveCount || 0, 0);

const passed = rows.filter((r) => r.ok).length;
const failed = rows.filter((r) => !r.ok).length;
rows.forEach((r) => {
    console.log((r.ok ? 'PASS' : 'FAIL') + '  ' + r.name + (r.detail ? ' — ' + r.detail : ''));
});
console.log(passed + ' passed, ' + failed + ' failed');
if (failed) process.exit(1);
