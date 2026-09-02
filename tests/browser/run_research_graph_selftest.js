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

function fakeElement(data) {
    const id = data && data.id;
    return {
        length: 1,
        empty: function () {
            return false;
        },
        id: function () {
            return id;
        },
        style: function () {
            return this;
        },
        select: function () {},
        unselect: function () {},
        addClass: function () {},
        removeClass: function () {},
        selected: function () {
            return false;
        },
        source: function () {
            return { id: function () { return data && data.source; } };
        },
        target: function () {
            return { id: function () { return data && data.target; } };
        },
    };
}

function fakeCollection(items) {
    const arr = (items || []).map(function (item) {
        if (item && typeof item.id === 'function') return item;
        return fakeElement(item);
    });
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
            return arr[0] ? arr[0].id() : '';
        },
        select: function () {
            arr.forEach(function (el) {
                el.select();
            });
        },
        addClass: function () {},
        removeClass: function () {},
        style: function () {},
        source: function () {
            return arr[0] ? arr[0].source() : { id: function () { return ''; } };
        },
        target: function () {
            return arr[0] ? arr[0].target() : { id: function () { return ''; } };
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
            return fakeCollection([{ id: id }]);
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
            id: 'person:P-123',
            record_id: 'P-123',
            type: 'person',
            label: 'Max Horkheimer',
            route: '#/people/P-123',
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
            id: 'work_author:person:P-123>work:W-1',
            type: 'work_author',
            source: 'person:P-123',
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
    'mention inspector label',
    g.inspectorLabelForEdge(fixture.edges[2]),
    'Mentioned in research notes'
);
assertEq(
    rows,
    'mention canvas label',
    g.canvasLabelForEdge(fixture.edges[2]),
    'Note mention ×3'
);
assertEq(rows, 'source display label', g.displayLabelForEdge(fixture.edges[1]), 'Made/taken in');
assertEq(rows, 'source canvas label', g.canvasLabelForEdge(fixture.edges[1]), 'Source');
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

assert(
    rows,
    'node select does not mass-label incident edges',
    g.edgeShouldShowCanvasLabel(mentionEdge.data.id, {
        selectedNodeId: 'work:W-1',
        selectedEdgeId: '',
        hoverEdgeId: '',
    }) === false
);
assert(
    rows,
    'hovered edge shows canvas label',
    g.edgeShouldShowCanvasLabel(mentionEdge.data.id, {
        selectedNodeId: 'work:W-1',
        selectedEdgeId: '',
        hoverEdgeId: mentionEdge.data.id,
    }) === true
);
assert(
    rows,
    'selected edge shows canvas label',
    g.edgeShouldShowCanvasLabel(mentionEdge.data.id, {
        selectedNodeId: '',
        selectedEdgeId: mentionEdge.data.id,
        hoverEdgeId: '',
    }) === true
);
const ctx = g.selectionContextIds(fixture, 'work:W-1', '');
assert(rows, 'work context includes concept', !!ctx.nodeIds['concept:C-1']);
assert(rows, 'work context includes mention edge', !!ctx.edgeIds[mentionEdge.data.id]);
assert(rows, 'work context dims unrelated position', !ctx.nodeIds['position:P-1']);
const edgeInsp = g.inspectorEdgeModel(fixture, mentionEdge.data.id);
assertEq(rows, 'edge inspector relation', edgeInsp && edgeInsp.relation, 'Mentioned in research notes');
assertEq(rows, 'edge inspector source', edgeInsp && edgeInsp.source && edgeInsp.source.id, 'work:W-1');
assertEq(rows, 'edge inspector target', edgeInsp && edgeInsp.target && edgeInsp.target.id, 'concept:C-1');
const styles = g.cytoscapeStyle();
const edgeBase = styles.find((s) => s.selector === 'edge');
assertEq(rows, 'edge labels do not autorotate', edgeBase && edgeBase.style['text-rotation'], 'none');
assert(
    rows,
    'label-on uses canvasLabel',
    styles.some((s) => s.selector === 'edge.graph-edge--label-on' && s.style.label === 'data(canvasLabel)')
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

function makeGraphHost() {
    function noop() {}
    const peopleBox = {
        checked: false,
        getAttribute: function (name) {
            return name === 'data-graph-filter' ? 'people' : null;
        },
        addEventListener: noop,
    };
    const inspector = { innerHTML: '' };
    const canvas = { id: 'prks-graph-canvas' };
    const graphRoot = {
        addEventListener: noop,
        removeEventListener: noop,
        querySelector: function (sel) {
            if (sel === '#prks-graph-find') return { value: '', addEventListener: noop };
            if (sel === '#prks-graph-find-results') return { innerHTML: '', hidden: true };
            if (sel === '#prks-graph-inspector') return inspector;
            if (sel === '#prks-graph-canvas') return canvas;
            if (sel === '[data-graph-filter="people"]') return peopleBox;
            return null;
        },
        querySelectorAll: function (sel) {
            if (sel === '[data-graph-filter]') return [peopleBox];
            return [];
        },
        parentNode: null,
    };
    const host = {
        innerHTML: '',
        querySelector: function (sel) {
            if (sel === '.research-graph') return graphRoot;
            if (sel === '#prks-graph-canvas') return canvas;
            return graphRoot.querySelector(sel);
        },
        querySelectorAll: function (sel) {
            return graphRoot.querySelectorAll(sel);
        },
        _peopleBox: peopleBox,
        _inspector: inspector,
    };
    graphRoot.parentNode = host;
    return host;
}

(async function () {
    assertEq(rows, 'person focus requires people', g.peopleRequiredForFocus('person:P-123'), true);
    assertEq(rows, 'concept focus does not require people', g.peopleRequiredForFocus('concept:C-1'), false);
    assertEq(rows, 'empty focus does not require people', g.peopleRequiredForFocus(''), false);

    const fetchCalls = [];
    let fetchImpl = async function () {
        return fixture;
    };
    sandbox.fetchResearchGraph = async function (opts) {
        fetchCalls.push(opts || {});
        return fetchImpl(opts);
    };

    const personHost = makeGraphHost();
    await g.renderResearchGraph(personHost, { focus: 'person:P-123' });
    assert(
        rows,
        'person focus initial request includes people',
        fetchCalls[0] && fetchCalls[0].people === true
    );
    assertEq(rows, 'person focus selects person node', g.getSelectedGraphNodeId(), 'person:P-123');
    assertEq(rows, 'person focus checks People filter', personHost._peopleBox.checked, true);

    fetchCalls.length = 0;
    const conceptHost = makeGraphHost();
    await g.renderResearchGraph(conceptHost, { focus: 'concept:C-1' });
    assert(
        rows,
        'concept focus initial request excludes people',
        fetchCalls[0] && fetchCalls[0].people === false
    );
    assertEq(rows, 'concept focus selects concept node', g.getSelectedGraphNodeId(), 'concept:C-1');

    const cyBefore = fakeElements[fakeElements.length - 1];
    fetchImpl = async function () {
        const err = new Error('too large');
        err.code = 'graph_too_large';
        throw err;
    };
    conceptHost._peopleBox.checked = true;
    const tooLargeOk = await g.reloadGraph(true);
    assertEq(rows, 'people reload graph_too_large returns false', tooLargeOk, false);
    assert(
        rows,
        'people reload requested people',
        fetchCalls[fetchCalls.length - 1] && fetchCalls[fetchCalls.length - 1].people === true
    );
    assertEq(rows, 'people checkbox restored after graph_too_large', conceptHost._peopleBox.checked, false);
    assertEq(rows, 'prior graph kept after graph_too_large', cyBefore.destroyed, false);
    assertEq(rows, 'selection kept after failed people reload', g.getSelectedGraphNodeId(), 'concept:C-1');
    assert(
        rows,
        'graph_too_large status shown',
        String(conceptHost._inspector.innerHTML).indexOf('too large to render as a single snapshot') >= 0
    );

    fetchImpl = async function () {
        throw new Error('network');
    };
    conceptHost._peopleBox.checked = true;
    const loadFailOk = await g.reloadGraph(true);
    assertEq(rows, 'people reload ordinary failure returns false', loadFailOk, false);
    assertEq(rows, 'people checkbox restored after load failure', conceptHost._peopleBox.checked, false);
    assertEq(rows, 'prior graph kept after load failure', cyBefore.destroyed, false);
    assert(
        rows,
        'ordinary load failure status shown',
        String(conceptHost._inspector.innerHTML).indexOf('Could not load Research Graph.') >= 0
    );

    const leaveHost = makeGraphHost();
    fetchImpl = async function () {
        return fixture;
    };
    await g.renderResearchGraph(leaveHost, {});
    let resolveLeave;
    fetchImpl = function () {
        return new Promise(function (resolve) {
            resolveLeave = resolve;
        });
    };
    const leavePending = g.reloadGraph(true);
    g.destroyResearchGraph();
    leaveHost.innerHTML = 'WORKS PAGE';
    resolveLeave(fixture);
    const leaveResult = await leavePending;
    assertEq(rows, 'stale reload after leave returns false', leaveResult, false);
    assertEq(rows, 'stale reload does not replace left page', leaveHost.innerHTML, 'WORKS PAGE');

    const raceHost = makeGraphHost();
    fetchImpl = async function () {
        return fixture;
    };
    await g.renderResearchGraph(raceHost, {});
    const deferred = [];
    fetchImpl = function (opts) {
        return new Promise(function (resolve, reject) {
            deferred.push({ opts: opts || {}, resolve: resolve, reject: reject });
        });
    };
    const onPending = g.reloadGraph(true);
    const offPending = g.reloadGraph(false);
    assertEq(rows, 'rapid toggle started two fetches', deferred.length, 2);
    deferred[1].resolve(fixture);
    const offResult = await offPending;
    assertEq(rows, 'latest people-off reload applies', offResult, true);
    assertEq(rows, 'people checkbox follows latest toggle', raceHost._peopleBox.checked, false);
    raceHost._inspector.innerHTML = '';
    const tooLarge = new Error('too large');
    tooLarge.code = 'graph_too_large';
    deferred[0].reject(tooLarge);
    const onResult = await onPending;
    assertEq(rows, 'older people-on reload is ignored', onResult, false);
    assertEq(rows, 'stale people-on does not restore people', raceHost._peopleBox.checked, false);
    assert(
        rows,
        'stale people-on failure does not paint error',
        String(raceHost._inspector.innerHTML).indexOf('too large') < 0
    );

    const passed = rows.filter((r) => r.ok).length;
    const failed = rows.filter((r) => !r.ok).length;
    rows.forEach((r) => {
        console.log((r.ok ? 'PASS' : 'FAIL') + '  ' + r.name + (r.detail ? ' — ' + r.detail : ''));
    });
    console.log(passed + ' passed, ' + failed + ' failed');
    if (failed) process.exit(1);
})().catch(function (err) {
    console.error(err && err.stack ? err.stack : err);
    process.exit(1);
});
