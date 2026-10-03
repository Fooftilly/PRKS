#!/usr/bin/env node
'use strict';

const path = require('path');
const fs = require('fs');
const vm = require('vm');

const ownerResource = require(path.join(__dirname, '..', '..', 'frontend', 'js', 'owner-resource.js'));

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
        resize: function () {
            this._resizes = (this._resizes || 0) + 1;
        },
    };
    fakeElements.push(cy);
    return cy;
}

function attachRegistry(ctx) {
    const token = {};
    if (typeof ctx.generation !== 'number') ctx.generation = 1;
    if (typeof ctx.destroyed !== 'boolean') ctx.destroyed = false;
    const host = {
        ownerId: String(ctx.tabId || ''),
        ownerToken: token,
        generation: function () { return ctx.generation; },
        alive: function () { return !ctx.destroyed; },
    };
    ctx.ownerToken = token;
    ctx.resourceRegistry = ownerResource.createOwnerResourceRegistry(host);
    ctx.resourceTicket = function (generation) {
        return ownerResource.resourceTicket(host, generation);
    };
    ctx.readResource = function (kind) {
        return ctx.resourceRegistry.get(kind);
    };
    ctx.getResource = function (name) {
        if (String(name) === 'researchGraph') return ctx.resourceRegistry.get('researchGraph');
        return undefined;
    };
    ctx.clearResource = function (name) {
        if (String(name) === 'researchGraph') ctx.resourceRegistry.dispose('researchGraph');
    };
    ctx.beginRoute = function () {
        ctx.resourceRegistry.releaseAll();
        ctx.generation += 1;
        return ctx.generation;
    };
    ctx.warmSuspend = function () {
        ctx.resourceRegistry.warmSuspend();
    };
    ctx.resumeOwner = function () {
        ctx.resourceRegistry.resume();
    };
    ctx.releaseOwner = function () {
        ctx.resourceRegistry.releaseAll();
        ctx.destroyed = true;
    };
    return ctx;
}

const _mockCtx = attachRegistry({
    tabId: 'mock-tab',
    mounted: true,
    destroyed: false,
    domId: function (local) { return 'prks-tab-mock-tab-' + local; },
    query: function () { return null; },
});

const pendingFrames = new Map();
const resizeObservers = [];
let nextFrameId = 1;

const sandbox = {
    window: {},
    global: {},
    module: { exports: {} },
    cytoscape: fakeCytoscape,
    requestAnimationFrame: function (fn) {
        const id = nextFrameId++;
        pendingFrames.set(id, fn);
        return id;
    },
    cancelAnimationFrame: function (id) {
        pendingFrames.delete(id);
    },
    ResizeObserver: function (callback) {
        const obs = {
            callback: callback,
            target: null,
            disconnected: false,
            observe: function (el) {
                obs.target = el;
                obs.disconnected = false;
            },
            disconnect: function () {
                obs.disconnected = true;
                obs.target = null;
            },
        };
        resizeObservers.push(obs);
        return obs;
    },
    prksNavigate: function (hash) {
        navigated.push(hash);
    },
    prksEscapeHtml: function (s) {
        return String(s == null ? '' : s);
    },
    prksGetFocusedTabContext: function () {
        return _mockCtx;
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
    let bound = 0;
    function track(el) {
        el.addEventListener = function () { bound += 1; };
        el.removeEventListener = function () { bound = Math.max(0, bound - 1); };
    }
    const peopleBox = {
        checked: false,
        getAttribute: function (name) {
            return name === 'data-graph-filter' ? 'people' : null;
        },
    };
    track(peopleBox);
    const inspector = { innerHTML: '' };
    const status = { textContent: '', hidden: true };
    const canvas = { id: 'prks-graph-canvas' };
    const graphRoot = {
        querySelector: function (sel) {
            if (sel === '#prks-graph-find' || sel === '[data-prks-role="graph-find"]') {
                if (!graphRoot._find) {
                    graphRoot._find = { value: '' };
                    track(graphRoot._find);
                }
                return graphRoot._find;
            }
            if (sel === '#prks-graph-find-results' || sel === '[data-prks-role="graph-find-results"]') {
                return { innerHTML: '', hidden: true };
            }
            if (sel === '#prks-graph-inspector') return inspector;
            if (sel === '[data-prks-role="graph-status"]') return status;
            if (sel === '#prks-graph-canvas' || sel === '[data-prks-role="graph-canvas"]') return canvas;
            if (sel === '[data-graph-filter="people"]') return peopleBox;
            return null;
        },
        querySelectorAll: function (sel) {
            if (sel === '[data-graph-filter]') return [peopleBox];
            return [];
        },
        parentNode: null,
    };
    track(graphRoot);
    const host = {
        innerHTML: '',
        hasAttribute: function (name) {
            return name === 'data-prks-research-graph';
        },
        getAttribute: function (name) {
            return name === 'data-prks-research-graph' ? '' : null;
        },
        querySelector: function (sel) {
            if (sel === '.research-graph') return graphRoot;
            if (sel === '#prks-graph-canvas' || sel === '[data-prks-role="graph-canvas"]') return canvas;
            return graphRoot.querySelector(sel);
        },
        querySelectorAll: function (sel) {
            return graphRoot.querySelectorAll(sel);
        },
        _peopleBox: peopleBox,
        _inspector: inspector,
        _status: status,
        _bound: function () { return bound; },
    };
    graphRoot.parentNode = host;
    return host;
}

function makeCtx(tabId) {
    return attachRegistry({
        tabId: tabId,
        domId: function (local) {
            return 'prks-tab-' + tabId + '-' + local;
        },
        query: function () {
            return null;
        },
    });
}

(async function () {
    assertEq(rows, 'person focus requires people', g.peopleRequiredForFocus('person:P-123'), true);
    assertEq(rows, 'concept focus does not require people', g.peopleRequiredForFocus('concept:C-1'), false);
    assertEq(rows, 'empty focus does not require people', g.peopleRequiredForFocus(''), false);

    const fetchCalls = [];
    let fetchImpl = async function (_opts) {
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
    assertEq(
        rows,
        'person focus reports inspector selection',
        g.prksResearchGraphHasInspectorSelection(),
        true
    );
    assert(
        rows,
        'person inspector renders clear-selection control',
        String(personHost._inspector.innerHTML).indexOf('data-graph-clear-selection') >= 0
    );
    g.clearGraphSelection();
    assertEq(rows, 'clear selection empties inspector', personHost._inspector.innerHTML, '');
    assertEq(
        rows,
        'clear selection reports no inspector selection',
        g.prksResearchGraphHasInspectorSelection(),
        false
    );

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
        String(conceptHost._status.textContent).indexOf('too large to render as a single snapshot') >= 0
    );
    assertEq(rows, 'graph_too_large status visible', conceptHost._status.hidden, false);

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
        String(conceptHost._status.textContent).indexOf('Could not load Research Graph.') >= 0
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
    g.destroyResearchGraph(leaveHost);
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
    raceHost._status.textContent = '';
    const tooLarge = new Error('too large');
    tooLarge.code = 'graph_too_large';
    deferred[0].reject(tooLarge);
    const onResult = await onPending;
    assertEq(rows, 'older people-on reload is ignored', onResult, false);
    assertEq(rows, 'stale people-on does not restore people', raceHost._peopleBox.checked, false);
    assert(
        rows,
        'stale people-on failure does not paint error',
        String(raceHost._inspector.innerHTML).indexOf('too large') < 0 &&
            String(raceHost._status.textContent).indexOf('too large') < 0
    );

    fetchImpl = async function () {
        return fixture;
    };
    const liveBeforeOwners = sandbox.__prksResearchGraphLiveCount || 0;
    assertEq(rows, 'focused pane still holds one graph', liveBeforeOwners, 1);
    const cyMark = fakeElements.length;
    const mainCtx = makeCtx('main');
    const sideCtx = makeCtx('side');
    const mainHost = makeGraphHost();
    const sideHost = makeGraphHost();
    await g.renderResearchGraph(mainHost, { ctx: mainCtx });
    await g.renderResearchGraph(sideHost, { ctx: sideCtx });
    const mainCy = fakeElements[cyMark];
    const sideCy = fakeElements[cyMark + 1];
    const mainCanvas = mainHost.querySelector('[data-prks-role="graph-canvas"]');
    const sideCanvas = sideHost.querySelector('[data-prks-role="graph-canvas"]');
    assertEq(rows, 'two owners retain two live graphs', sandbox.__prksResearchGraphLiveCount, liveBeforeOwners + 2);
    assertEq(rows, 'main cytoscape stays mounted', mainCy.destroyed, false);
    assertEq(rows, 'secondary cytoscape stays mounted', sideCy.destroyed, false);
    const mainObs = resizeObservers.filter(function (obs) {
        return obs.target === mainCanvas && !obs.disconnected;
    });
    const sideObs = resizeObservers.filter(function (obs) {
        return obs.target === sideCanvas && !obs.disconnected;
    });
    assertEq(rows, 'main canvas has one observer', mainObs.length, 1);
    assertEq(rows, 'secondary canvas has one observer', sideObs.length, 1);

    const framesBefore = pendingFrames.size;
    const mainRuntime = mainCtx.getResource('researchGraph');
    mainRuntime.selectNode('concept:C-1');
    mainRuntime.selectNode('work:W-1');
    assertEq(rows, 'owner keeps one resize frame', pendingFrames.size, framesBefore + 1);

    g.destroyResearchGraph(mainHost);
    assertEq(rows, 'destroying main leaves secondary', sandbox.__prksResearchGraphLiveCount, liveBeforeOwners + 1);
    assertEq(rows, 'main cytoscape destroyed', mainCy.destroyed, true);
    assertEq(rows, 'secondary cytoscape kept', sideCy.destroyed, false);
    assertEq(rows, 'main observer disconnected', mainObs[0].disconnected, true);
    assertEq(rows, 'secondary observer kept', sideObs[0].disconnected, false);
    assertEq(rows, 'destroy cancels the owner frame', pendingFrames.size, framesBefore);
    assert(rows, 'secondary resource remains', sideCtx.getResource('researchGraph') != null);

    g.destroyResearchGraph(sideHost);
    assertEq(rows, 'both owners released', sandbox.__prksResearchGraphLiveCount, liveBeforeOwners);
    assertEq(rows, 'secondary cytoscape destroyed', sideCy.destroyed, true);
    assertEq(rows, 'secondary observer disconnected', sideObs[0].disconnected, true);
    assertEq(rows, 'secondary resource cleared', sideCtx.getResource('researchGraph'), undefined);

    const staleCtx = makeCtx('stale');
    const staleHost = makeGraphHost();
    let resolveStale;
    fetchImpl = function () {
        return new Promise(function (resolve) {
            resolveStale = resolve;
        });
    };
    const countAtStaleStart = sandbox.__prksResearchGraphLiveCount || 0;
    const stalePending = g.renderResearchGraph(staleHost, { ctx: staleCtx });
    assertEq(rows, 'in-flight start has not created cytoscape', sandbox.__prksResearchGraphLiveCount, countAtStaleStart);
    staleCtx.clearResource('researchGraph');
    staleHost.innerHTML = 'LEFT';
    resolveStale(fixture);
    await stalePending;
    assertEq(rows, 'stale start does not replace left page', staleHost.innerHTML, 'LEFT');
    assertEq(rows, 'stale start does not mount', sandbox.__prksResearchGraphLiveCount, countAtStaleStart);
    assertEq(rows, 'stale owner stays empty', staleCtx.getResource('researchGraph'), undefined);

    const sharedInspector = {
        innerHTML: '',
        _attrs: {},
        getAttribute: function (name) {
            return sharedInspector._attrs[name] || null;
        },
        setAttribute: function (name, value) {
            sharedInspector._attrs[name] = String(value);
        },
        removeAttribute: function (name) {
            delete sharedInspector._attrs[name];
        },
        addEventListener: function () {},
    };
    const savedFocused = sandbox.prksGetFocusedTabContext;
    const savedDocument = sandbox.document;
    const savedVisibility = sandbox.prksRefreshFocusedRightPanelVisibility;
    let visibilityCalls = 0;
    let focusedOwner = null;
    sandbox.document = {
        getElementById: function (id) {
            if (id !== 'panel-content') return null;
            return {
                querySelector: function (sel) {
                    return sel === '#prks-graph-inspector' ? sharedInspector : null;
                },
            };
        },
    };
    sandbox.prksGetFocusedTabContext = function () {
        return focusedOwner;
    };
    sandbox.prksRefreshFocusedRightPanelVisibility = function () {
        visibilityCalls += 1;
    };
    try {
        fetchImpl = async function () {
            return fixture;
        };
        const focusMain = makeCtx('focus-main');
        const focusSide = makeCtx('focus-side');
        focusedOwner = focusMain;
        const focusMainHost = makeGraphHost();
        const focusSideHost = makeGraphHost();
        await g.renderResearchGraph(focusMainHost, { ctx: focusMain, focus: 'person:P-123' });
        const mainPaint = sharedInspector.innerHTML;
        assert(
            rows,
            'focused main paints its inspector',
            mainPaint.indexOf('Max Horkheimer') >= 0
        );
        assertEq(rows, 'focused main reports its selection', g.getSelectedGraphNodeId(), 'person:P-123');
        visibilityCalls = 0;
        await g.renderResearchGraph(focusSideHost, { ctx: focusSide, focus: 'concept:C-1' });
        assertEq(rows, 'unfocused secondary leaves the inspector', sharedInspector.innerHTML, mainPaint);
        assert(
            rows,
            'unfocused secondary does not paint its node',
            sharedInspector.innerHTML.indexOf('Culture Industry') < 0
        );
        assertEq(rows, 'unfocused secondary skips panel visibility', visibilityCalls, 0);
        assertEq(
            rows,
            'secondary keeps its own selection',
            focusSide.getResource('researchGraph').getSelectedId(),
            'concept:C-1'
        );
        assertEq(rows, 'main selection stays pane-local', g.getSelectedGraphNodeId(), 'person:P-123');
        focusedOwner = focusSide;
        g.renderGraphInspector();
        assert(
            rows,
            'focusing secondary paints its inspector',
            sharedInspector.innerHTML.indexOf('Culture Industry') >= 0
        );
        assert(rows, 'focusing secondary replaces the main inspector', sharedInspector.innerHTML !== mainPaint);
        g.destroyResearchGraph(focusMainHost);
        g.destroyResearchGraph(focusSideHost);
    } finally {
        sandbox.prksGetFocusedTabContext = savedFocused;
        sandbox.prksRefreshFocusedRightPanelVisibility = savedVisibility;
        if (savedDocument === undefined) delete sandbox.document;
        else sandbox.document = savedDocument;
    }

    fetchImpl = async function () {
        return fixture;
    };
    const lifeCtx = makeCtx('life');
    const lifeHost = makeGraphHost();
    const liveAtReplace = sandbox.__prksResearchGraphLiveCount || 0;
    const cyAtReplace = fakeElements.length;
    await g.renderResearchGraph(lifeHost, { ctx: lifeCtx });
    const graphA = fakeElements[cyAtReplace];
    const runtimeA = lifeCtx.getResource('researchGraph');
    await g.renderResearchGraph(lifeHost, { ctx: lifeCtx });
    const graphB = fakeElements[cyAtReplace + 1];
    assertEq(rows, 'replacement disposes the previous cytoscape once', graphA.destroyed, true);
    assertEq(rows, 'replacement keeps one live graph', sandbox.__prksResearchGraphLiveCount, liveAtReplace + 1);
    assert(rows, 'replacement installs the new runtime', lifeCtx.getResource('researchGraph') !== runtimeA);
    assertEq(rows, 'replacement cytoscape stays mounted', graphB.destroyed, false);

    const destroyCtx = makeCtx('destroy-once');
    const destroyHost = makeGraphHost();
    const liveBeforeDestroy = sandbox.__prksResearchGraphLiveCount || 0;
    await g.renderResearchGraph(destroyHost, { ctx: destroyCtx });
    const destroyCy = fakeElements[fakeElements.length - 1];
    destroyCtx.releaseOwner();
    destroyCtx.releaseOwner();
    assertEq(rows, 'owner release disposes the graph once', sandbox.__prksResearchGraphLiveCount, liveBeforeDestroy);
    assertEq(rows, 'released cytoscape is destroyed', destroyCy.destroyed, true);
    assertEq(rows, 'released owner has no graph', destroyCtx.getResource('researchGraph'), undefined);

    const paneMain = makeCtx('pane-main');
    const paneSide = makeCtx('pane-side');
    const paneMainHost = makeGraphHost();
    const paneSideHost = makeGraphHost();
    await g.renderResearchGraph(paneMainHost, { ctx: paneMain });
    await g.renderResearchGraph(paneSideHost, { ctx: paneSide });
    const paneMainCy = fakeElements[fakeElements.length - 2];
    const paneSideCy = fakeElements[fakeElements.length - 1];
    paneMain.releaseOwner();
    assertEq(rows, 'one pane release keeps the other cytoscape', paneSideCy.destroyed, false);
    assertEq(rows, 'released pane cytoscape is gone', paneMainCy.destroyed, true);
    assert(rows, 'other pane still owns its graph', paneSide.getResource('researchGraph') != null);
    paneSide.releaseOwner();

    const warmCtx = makeCtx('warm-graph');
    const warmHost = makeGraphHost();
    const cyBeforeWarm = fakeElements.length;
    await g.renderResearchGraph(warmHost, { ctx: warmCtx });
    const warmCy = fakeElements[cyBeforeWarm];
    const warmLive = sandbox.__prksResearchGraphLiveCount || 0;
    warmCtx.warmSuspend();
    warmCtx.warmSuspend();
    assertEq(rows, 'warm park releases the non-suspendable graph once', warmCy.destroyed, true);
    assertEq(rows, 'warm park drops one live graph', sandbox.__prksResearchGraphLiveCount, warmLive - 1);
    assertEq(rows, 'warm park clears the graph resource', warmCtx.getResource('researchGraph'), undefined);
    warmCtx.resumeOwner();
    assertEq(rows, 'warm resume does not create another cytoscape', fakeElements.length, cyBeforeWarm + 1);
    assertEq(rows, 'warm resume leaves the graph unregistered', warmCtx.getResource('researchGraph'), undefined);

    const coldCtx = makeCtx('cold-graph');
    const coldHost = makeGraphHost();
    await g.renderResearchGraph(coldHost, { ctx: coldCtx });
    const coldCy = fakeElements[fakeElements.length - 1];
    coldCtx.resourceRegistry.releaseAll();
    coldCtx.resumeOwner();
    assertEq(rows, 'cold park destroys the cytoscape', coldCy.destroyed, true);
    assertEq(rows, 'cold park leaves no graph to resume', coldCtx.getResource('researchGraph'), undefined);

    const routeCtx = makeCtx('route-swap');
    const routeHostA = makeGraphHost();
    let resolveRouteA;
    fetchImpl = function () {
        return new Promise(function (resolve) {
            resolveRouteA = resolve;
        });
    };
    const cyBeforeRoute = fakeElements.length;
    const pendingRouteA = g.renderResearchGraph(routeHostA, { ctx: routeCtx, routeGen: routeCtx.generation });
    routeCtx.beginRoute();
    fetchImpl = async function () {
        return fixture;
    };
    const routeHostB = makeGraphHost();
    await g.renderResearchGraph(routeHostB, { ctx: routeCtx, routeGen: routeCtx.generation });
    const routeCyB = fakeElements[fakeElements.length - 1];
    resolveRouteA(fixture);
    await pendingRouteA;
    assertEq(rows, 'stale route completion does not mount a second cytoscape', fakeElements.length, cyBeforeRoute + 1);
    assertEq(rows, 'replacement route keeps its cytoscape', routeCyB.destroyed, false);
    assert(rows, 'stale completion does not own the replacement', routeCtx.getResource('researchGraph') !== undefined);
    assertEq(rows, 'stale host was not rewritten', routeHostA.innerHTML, '');

    const repeatCtx = makeCtx('repeat');
    const repeatHost = makeGraphHost();
    const framesAtRepeat = pendingFrames.size;
    for (let pass = 0; pass < 3; pass++) {
        await g.renderResearchGraph(repeatHost, { ctx: repeatCtx });
        repeatCtx.getResource('researchGraph').selectNode('concept:C-1');
        g.destroyResearchGraph(repeatHost);
        assertEq(rows, 'pass ' + pass + ' releases listeners', repeatHost._bound(), 0);
        assertEq(rows, 'pass ' + pass + ' releases the resize frame', pendingFrames.size, framesAtRepeat);
    }
    const repeatCanvas = repeatHost.querySelector('[data-prks-role="graph-canvas"]');
    const repeatLiveObs = resizeObservers.filter(function (obs) {
        return obs.target === repeatCanvas && !obs.disconnected;
    });
    assertEq(rows, 'repeated mount leaves no live observer', repeatLiveObs.length, 0);
    assertEq(rows, 'repeated mount leaves no graph resource', repeatCtx.getResource('researchGraph'), undefined);

    const roleMain = makeCtx('role-main');
    const roleSide = makeCtx('role-side');
    const roleMainHost = makeGraphHost();
    const roleSideHost = makeGraphHost();
    await g.renderResearchGraph(roleMainHost, { ctx: roleMain });
    await g.renderResearchGraph(roleSideHost, { ctx: roleSide });
    const roleMainRuntime = roleMain.getResource('researchGraph');
    const roleSideRuntime = roleSide.getResource('researchGraph');
    const roleMainCy = fakeElements[fakeElements.length - 2];
    const promoted = roleSide;
    const demoted = roleMain;
    assert(rows, 'role swap keeps the promoted runtime', promoted.getResource('researchGraph') === roleSideRuntime);
    assert(rows, 'role swap keeps the demoted runtime', demoted.getResource('researchGraph') === roleMainRuntime);
    assertEq(rows, 'role swap does not destroy the demoted cytoscape', roleMainCy.destroyed, false);
    roleMain.releaseOwner();
    roleSide.releaseOwner();

    const oldOwner = makeCtx('same-tab');
    const oldHost = makeGraphHost();
    let resolveOld;
    fetchImpl = function () {
        return new Promise(function (resolve) {
            resolveOld = resolve;
        });
    };
    const pendingOld = g.renderResearchGraph(oldHost, { ctx: oldOwner, routeGen: oldOwner.generation });
    const oldTicket = oldOwner.resourceTicket(oldOwner.generation);
    oldOwner.releaseOwner();
    const newOwner = makeCtx('same-tab');
    fetchImpl = async function () {
        return fixture;
    };
    const newHost = makeGraphHost();
    await g.renderResearchGraph(newHost, { ctx: newOwner, routeGen: newOwner.generation });
    const newCy = fakeElements[fakeElements.length - 1];
    const planted = { id: 'stale-plant' };
    let plantedDisposes = 0;
    const plantedResult = newOwner.resourceRegistry.register(oldTicket, {
        kind: 'researchGraph',
        value: planted,
        dispose: function () { plantedDisposes += 1; },
    });
    resolveOld(fixture);
    await pendingOld;
    assertEq(rows, 'old owner ticket cannot register on the replacement', plantedResult, 'rejected');
    assertEq(rows, 'rejected plant is not disposed', plantedDisposes, 0);
    assertEq(rows, 'replacement cytoscape survives the old completion', newCy.destroyed, false);
    assert(rows, 'replacement still owns its own graph', newOwner.getResource('researchGraph') !== planted);
    newOwner.releaseOwner();

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
