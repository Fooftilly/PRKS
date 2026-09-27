#!/usr/bin/env node
'use strict';

/**
 * Migration safety net: the frozen legacy tree oracle versus the typed model
 * and the production adapter. Not loaded by the app. Retire with the oracle
 * once production no longer needs a second tree implementation to compare.
 */

const path = require('path');

const rootDir = path.resolve(__dirname, '../..');
const oracle = require(path.join(rootDir, 'tests/browser/fixtures/workspace-tree-legacy-oracle.js'));
const adapter = require(path.join(rootDir, 'frontend/js/workspace-tree.js'));
const model = require(path.join(rootDir, 'frontend/js/workspace-model.js'));

let passed = 0;
let failed = 0;

function record(name, ok, detail) {
    if (ok) passed += 1;
    else failed += 1;
    console.log((ok ? 'PASS  ' : 'FAIL  ') + name + (detail ? ' ' + detail : ''));
}

function assertEq(name, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    record(name, ok, ok ? '' : 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function freezeDeep(value) {
    if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
    Object.keys(value).forEach(function (key) {
        freezeDeep(value[key]);
    });
    return Object.freeze(value);
}

function clone(value) {
    return JSON.parse(JSON.stringify(value));
}

function tab(id) {
    return {
        id: id,
        route: '#/works',
        title: id,
        icon: 'work',
        history: ['#/works'],
        historyIndex: 0,
    };
}

function workspace(tree, focusedTabId) {
    return {
        version: model.WORKSPACE_STATE_VERSION,
        mode: 'tiled',
        mainTabId: 'A',
        focusedTabId: focusedTabId || 'C',
        secondaryTree: tree,
        tabs: ['A', 'B', 'C', 'D', 'E'].map(tab),
        mainSplitRatio: model.DEFAULT_MAIN_SPLIT_RATIO,
    };
}

function structural(state) {
    return {
        mode: state.mode,
        mainTabId: state.mainTabId,
        focusedTabId: state.focusedTabId,
        secondaryTree: state.secondaryTree,
        tabIds: state.tabs.map(function (item) { return item.id; }),
        mainSplitRatio: state.mainSplitRatio,
    };
}

function legacyHide(state, tabId) {
    if (!oracle.containsTab(state.secondaryTree, tabId)) return null;
    const sibling = oracle.findSiblingLeafTabId(state.secondaryTree, tabId);
    const secondaryTree = oracle.normalizeTree(oracle.removeLeaf(state.secondaryTree, tabId));
    const mode = secondaryTree ? state.mode : 'stacked';
    let focusedTabId = state.focusedTabId;
    if (focusedTabId === tabId) {
        const nextLeaves = oracle.collectLeafTabIds(secondaryTree);
        const preferred = sibling && nextLeaves.indexOf(sibling) !== -1 ? sibling : nextLeaves[0] || null;
        focusedTabId = preferred || state.mainTabId;
    }
    return {
        mode: mode,
        mainTabId: state.mainTabId,
        focusedTabId: focusedTabId,
        secondaryTree: secondaryTree,
        tabIds: state.tabs.map(function (item) { return item.id; }),
        mainSplitRatio: state.mainSplitRatio,
    };
}

function legacyMakeMain(state, tabId, oldMainSupportsTile) {
    const current = state.tabs.find(function (item) { return item.id === tabId; });
    if (!current) return null;
    if (current.id === state.mainTabId) {
        return {
            mode: state.mode,
            mainTabId: state.mainTabId,
            focusedTabId: current.id,
            secondaryTree: state.secondaryTree,
            tabIds: state.tabs.map(function (item) { return item.id; }),
            mainSplitRatio: state.mainSplitRatio,
            coldParkTabId: null,
        };
    }
    if (!oracle.containsTab(state.secondaryTree, current.id)) return null;
    let secondaryTree = state.secondaryTree;
    let coldParkTabId = null;
    if (oldMainSupportsTile) {
        secondaryTree = oracle.replaceTabId(state.secondaryTree, current.id, state.mainTabId);
    } else {
        secondaryTree = oracle.normalizeTree(oracle.removeLeaf(state.secondaryTree, current.id));
        coldParkTabId = state.mainTabId;
    }
    return {
        mode: secondaryTree ? state.mode : 'stacked',
        mainTabId: current.id,
        focusedTabId: current.id,
        secondaryTree: secondaryTree,
        tabIds: state.tabs.map(function (item) { return item.id; }),
        mainSplitRatio: state.mainSplitRatio,
        coldParkTabId: coldParkTabId,
    };
}

function legacyReorder(state, tabId, beforeTabId) {
    const tabs = state.tabs.slice();
    let idx = -1;
    for (let i = 0; i < tabs.length; i++) {
        if (tabs[i].id === tabId) idx = i;
    }
    if (idx < 0 || tabId === beforeTabId) return null;
    const removed = tabs.splice(idx, 1)[0];
    let insertAt = tabs.length;
    if (beforeTabId) {
        for (let j = 0; j < tabs.length; j++) {
            if (tabs[j].id === beforeTabId) insertAt = j;
        }
    }
    tabs.splice(insertAt, 0, removed);
    return {
        mode: state.mode,
        mainTabId: state.mainTabId,
        focusedTabId: state.focusedTabId,
        secondaryTree: state.secondaryTree,
        tabIds: tabs.map(function (item) { return item.id; }),
        mainSplitRatio: state.mainSplitRatio,
    };
}

function pair() {
    oracle.resetSplitIds();
    adapter.resetSplitIds();
    let seq = 0;
    function nextSplitId() {
        seq += 1;
        return 'split-' + seq;
    }
    let legacy = oracle.makeLeaf('B');
    let adapted = adapter.makeLeaf('B');
    let typed = model.makeLeaf('B');
    function split(tabId, options) {
        legacy = oracle.splitLeaf(legacy, tabId, options);
        adapted = adapter.splitLeaf(adapted, tabId, options);
        typed = model.splitLeaf(typed, tabId, options, nextSplitId);
    }
    split('B', { axis: 'left-right', newTabId: 'C', ratio: 0.4 });
    split('C', { axis: 'top-bottom', newTabId: 'D', ratio: 0.25 });
    split('B', { axis: 'left-right', newTabId: 'E', placement: 'first', ratio: 0.7 });
    return { legacy: legacy, adapted: adapted, typed: typed, nextSplitId: nextSplitId };
}

function run() {
    const built = pair();
    assertEq('split trees match oracle', built.adapted, built.legacy);
    assertEq('split trees match typed model', built.typed, built.legacy);
    assertEq(
        'split leaf order',
        oracle.collectLeafTabIds(built.legacy),
        ['E', 'B', 'C', 'D']
    );

    const frozen = freezeDeep(clone(built.legacy));
    const removed = model.normalizeTree(model.removeLeaf(frozen, 'D'));
    assertEq('deep removal collapses', oracle.collectLeafTabIds(removed), ['E', 'B', 'C']);
    assertEq('removal did not mutate input', frozen, built.legacy);
    assertEq(
        'adapter collapse matches oracle',
        adapter.normalizeTree(adapter.removeLeaf(built.adapted, 'D')),
        oracle.normalizeTree(oracle.removeLeaf(built.legacy, 'D'))
    );

    const movedLegacy = oracle.moveLeafRelativeToTarget(built.legacy, 'E', 'C', {
        axis: 'top-bottom',
        placement: 'first',
    });
    const movedTyped = model.moveLeafRelativeToTarget(built.typed, 'E', 'C', {
        axis: 'top-bottom',
        placement: 'first',
    }, built.nextSplitId);
    assertEq('move pane matches oracle', movedTyped, movedLegacy);

    const ratioLegacy = oracle.setSplitRatio(built.legacy, 'split-1', 2);
    const ratioTyped = model.setSplitRatio(built.typed, 'split-1', 2);
    assertEq('ratio upper bound', ratioTyped, ratioLegacy);
    assertEq('ratio lower bound', model.setSplitRatio(built.typed, 'split-1', -1), oracle.setSplitRatio(built.legacy, 'split-1', -1));
    assertEq('non-finite nested ratio', model.setSplitRatio(built.typed, 'split-1', NaN), oracle.setSplitRatio(built.legacy, 'split-1', NaN));

    const current = workspace(built.typed, 'C');
    const before = clone(current);
    freezeDeep(current);
    const hidden = model.planHideLeaf(current, 'C');
    assertEq('hide focused leaf', structural(hidden.state), legacyHide(before, 'C'));
    assertEq('hide input unchanged', current, before);
    const hiddenIdle = model.planHideLeaf(workspace(built.typed, 'E'), 'C');
    assertEq('hide unfocused leaf keeps focus', structural(hiddenIdle.state), legacyHide(workspace(clone(built.legacy), 'E'), 'C'));

    const swapped = model.planMakeMain(workspace(built.typed, 'C'), 'D', true);
    const legacySwapped = legacyMakeMain(workspace(clone(built.legacy), 'C'), 'D', true);
    assertEq('make main swap', structural(swapped.state), {
        mode: legacySwapped.mode,
        mainTabId: legacySwapped.mainTabId,
        focusedTabId: legacySwapped.focusedTabId,
        secondaryTree: legacySwapped.secondaryTree,
        tabIds: legacySwapped.tabIds,
        mainSplitRatio: legacySwapped.mainSplitRatio,
    });
    record('make main swap does not cold-park', swapped.coldParkTabId === null && legacySwapped.coldParkTabId === null);

    const parked = model.planMakeMain(workspace(built.typed, 'C'), 'D', false);
    const legacyParked = legacyMakeMain(workspace(clone(built.legacy), 'C'), 'D', false);
    assertEq('make main cold-park tree', structural(parked.state), {
        mode: legacyParked.mode,
        mainTabId: legacyParked.mainTabId,
        focusedTabId: legacyParked.focusedTabId,
        secondaryTree: legacyParked.secondaryTree,
        tabIds: legacyParked.tabIds,
        mainSplitRatio: legacyParked.mainSplitRatio,
    });
    record('make main cold-park id', parked.coldParkTabId === 'A' && legacyParked.coldParkTabId === 'A');

    const reordered = model.planReorder(workspace(built.typed, 'C'), 'E', 'B');
    assertEq('reorder tabs', structural(reordered.state), legacyReorder(workspace(clone(built.legacy), 'C'), 'E', 'B'));

    const ratioState = model.planMainSplitRatio(workspace(built.typed, 'C'), 1.4);
    record('main ratio clamp', ratioState.ratio === 1 && ratioState.state.mainSplitRatio === 1);
    const badRatio = model.planMainSplitRatio(workspace(built.typed, 'C'), NaN);
    record('main ratio non-finite', badRatio.ratio === model.DEFAULT_MAIN_SPLIT_RATIO);

    const nested = model.planNestedSplitRatio(workspace(built.typed, 'C'), 'split-2', 0.2);
    assertEq(
        'nested ratio planner',
        nested.state.secondaryTree,
        model.setSplitRatio(built.typed, 'split-2', 0.2)
    );

    const wide = { narrowFallback: false };
    const movedPane = model.planMovePane(
        workspace(built.typed, 'C'),
        'D',
        'B',
        'left-right',
        'second',
        wide,
        built.nextSplitId
    );
    const legacyMoved = oracle.moveLeafRelativeToTarget(built.legacy, 'D', 'B', {
        axis: 'left-right',
        placement: 'second',
    });
    assertEq('plan move pane', movedPane.state.secondaryTree, legacyMoved);
    record('plan move keeps focus', movedPane.state.focusedTabId === 'C');

    console.log(passed + ' passed, ' + failed + ' failed');
    if (failed) process.exit(1);
}

run();
