#!/usr/bin/env node
'use strict';

/* Deterministic coverage for the recursive Secondary tree model (workspace-tree.js).
 * Pure functions, no DOM needed.
 */

const path = require('path');
const rootDir = path.resolve(__dirname, '../..');
const tree = require(path.join(rootDir, 'frontend/js/workspace-tree.js'));

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

function assert(name, ok, detail) {
    record(name, !!ok, ok ? '' : detail || '');
}

function run() {
    tree.resetSplitIds();

    /* ---- Single leaf ---- */
    let t = tree.makeLeaf('B');
    assertEq('single leaf collect', tree.collectLeafTabIds(t), ['B']);
    assert('single leaf contains B', tree.containsTab(t, 'B'));
    assert('single leaf not contains C', !tree.containsTab(t, 'C'));
    assert('single leaf valid', tree.validateTree(t).ok);
    assertEq('null tree valid', tree.validateTree(null).ok, true);

    /* ---- Split leaf right ---- */
    t = tree.splitLeaf(t, 'B', { axis: 'left-right', newTabId: 'C' });
    assert('split right is split', tree.isSplit(t));
    assertEq('split right axis', t.axis, 'left-right');
    assertEq('split right ratio default', t.ratio, 0.5);
    assertEq('split right first is B', t.first.tabId, 'B');
    assertEq('split right second is C', t.second.tabId, 'C');
    assertEq('split right leaf order', tree.collectLeafTabIds(t), ['B', 'C']);
    assert('split right valid', tree.validateTree(t).ok);
    assert('split id stable format', /^split-\d+$/.test(t.id));

    /* ---- Split leaf down (nested) ---- */
    const splitId1 = t.id;
    t = tree.splitLeaf(t, 'C', { axis: 'top-bottom', newTabId: 'D' });
    const cNode = tree.findLeafByTabId(t, 'C');
    assert('nested C still leaf', !!cNode);
    assertEq('nested leaf order', tree.collectLeafTabIds(t), ['B', 'C', 'D']);
    assert('root split id unchanged after nested split', tree.findNodeById(t, splitId1) === t);
    const nested = t.second;
    assert('nested node is split', tree.isSplit(nested));
    assertEq('nested axis top-bottom', nested.axis, 'top-bottom');
    assertEq('nested first C', nested.first.tabId, 'C');
    assertEq('nested second D', nested.second.tabId, 'D');
    assert('nested split has distinct id', nested.id !== splitId1);
    assert('valid nested tree', tree.validateTree(t).ok);

    /* ---- Split placement 'first' ---- */
    let placeFirst = tree.makeLeaf('X');
    placeFirst = tree.splitLeaf(placeFirst, 'X', { axis: 'left-right', newTabId: 'Y', placement: 'first' });
    assertEq('placement first -> Y first', placeFirst.first.tabId, 'Y');
    assertEq('placement first -> X second', placeFirst.second.tabId, 'X');

    /* ---- findLeafByTabId / findNodeById ---- */
    assertEq('find leaf B', tree.findLeafByTabId(t, 'B').tabId, 'B');
    assertEq('find leaf missing', tree.findLeafByTabId(t, 'ZZZ'), null);
    assertEq('find node by id missing', tree.findNodeById(t, 'nope'), null);
    assert('find node by nested id', tree.findNodeById(t, nested.id) === nested);

    /* ---- replaceLeaf ---- */
    const replaced = tree.replaceLeaf(t, 'B', tree.makeLeaf('B2'));
    assertEq('replaceLeaf renames', tree.collectLeafTabIds(replaced), ['B2', 'C', 'D']);
    assert('replaceLeaf did not mutate original', tree.containsTab(t, 'B'));
    assert('replaceLeaf unrelated subtree same ref', replaced.second === t.second);

    /* ---- replaceTabId (Make Main leaf rename) ---- */
    const renamed = tree.replaceTabId(t, 'C', 'C2');
    assertEq('replaceTabId renames C leaf', tree.collectLeafTabIds(renamed), ['B', 'C2', 'D']);
    assert('replaceTabId keeps split structure', tree.isSplit(renamed.second));
    assertEq('replaceTabId keeps axis', renamed.second.axis, 'top-bottom');

    /* ---- setSplitRatio ---- */
    const ratioed = tree.setSplitRatio(t, nested.id, 0.75);
    assertEq('setSplitRatio applies', tree.findNodeById(ratioed, nested.id).ratio, 0.75);
    assertEq('setSplitRatio root unaffected', ratioed.ratio, t.ratio);
    assert('setSplitRatio does not touch original', tree.findNodeById(t, nested.id).ratio === 0.5);
    const clampedHigh = tree.setSplitRatio(t, nested.id, 5);
    assertEq('setSplitRatio clamps above 1', tree.findNodeById(clampedHigh, nested.id).ratio, 1);
    const clampedLow = tree.setSplitRatio(t, nested.id, -5);
    assertEq('setSplitRatio clamps below 0', tree.findNodeById(clampedLow, nested.id).ratio, 0);
    const clampedNaN = tree.setSplitRatio(t, nested.id, NaN);
    assertEq('setSplitRatio NaN falls back to 0.5', tree.findNodeById(clampedNaN, nested.id).ratio, 0.5);

    /* ---- removeLeaf + normalization ----
     *        X
     *       / \
     *      B   Y
     *         / \
     *        C   D
     * Remove C -> X(B, D). Remove B -> D (root itself becomes the surviving leaf).
     */
    assertEq('pre-removal shape', tree.collectLeafTabIds(t), ['B', 'C', 'D']);
    let afterRemoveC = tree.removeLeaf(t, 'C');
    assert('remove C collapses inner split', tree.isLeaf(afterRemoveC.second));
    assertEq('remove C leaves B,D', tree.collectLeafTabIds(afterRemoveC), ['B', 'D']);
    assert('remove C still one split node (root)', tree.isSplit(afterRemoveC));
    assertEq('remove C root axis preserved', afterRemoveC.axis, 'left-right');
    assert('remove C valid', tree.validateTree(afterRemoveC).ok);

    let afterRemoveB = tree.removeLeaf(afterRemoveC, 'B');
    assert('remove B collapses to bare leaf', tree.isLeaf(afterRemoveB));
    assertEq('remove B leaves D only', afterRemoveB.tabId, 'D');

    let afterRemoveLast = tree.removeLeaf(afterRemoveB, 'D');
    assertEq('remove last leaf -> null tree', afterRemoveLast, null);

    /* ---- Deeper collapse: remove from a 3-level tree, collapse repeats upward ----
     *          split1
     *         /      \
     *        A       split2
     *               /      \
     *              B       split3
     *                     /      \
     *                    C        D
     * Remove C -> split1(A, split2(B, D)); Remove B next -> split1(A, D); Remove A -> D.
     */
    let deep = tree.makeLeaf('A');
    deep = tree.splitLeaf(deep, 'A', { axis: 'left-right', newTabId: 'B' });
    deep = tree.splitLeaf(deep, 'B', { axis: 'top-bottom', newTabId: 'C' });
    deep = tree.splitLeaf(deep, 'C', { axis: 'left-right', newTabId: 'D' });
    assertEq('deep tree order', tree.collectLeafTabIds(deep), ['A', 'B', 'C', 'D']);

    let deepAfterC = tree.removeLeaf(deep, 'C');
    assertEq('deep remove C order', tree.collectLeafTabIds(deepAfterC), ['A', 'B', 'D']);
    assert('deep remove C no redundant single-child split', tree.validateTree(deepAfterC).ok);

    let deepAfterB = tree.removeLeaf(deepAfterC, 'B');
    assertEq('deep remove B order', tree.collectLeafTabIds(deepAfterB), ['A', 'D']);
    assert('deep remove B collapsed to one split', tree.isSplit(deepAfterB) && tree.isLeaf(deepAfterB.first) && tree.isLeaf(deepAfterB.second));

    let deepAfterA = tree.removeLeaf(deepAfterB, 'A');
    assert('deep remove A collapses to bare D leaf', tree.isLeaf(deepAfterA) && deepAfterA.tabId === 'D');

    /* ---- findSiblingLeafTabId ---- */
    let sibTree = tree.makeLeaf('B');
    sibTree = tree.splitLeaf(sibTree, 'B', { axis: 'top-bottom', newTabId: 'C' });
    sibTree = tree.splitLeaf(sibTree, 'C', { axis: 'left-right', newTabId: 'D' });
    assertEq('sibling of B is first of (C,D) subtree', tree.findSiblingLeafTabId(sibTree, 'B'), 'C');
    assertEq('sibling of C is D', tree.findSiblingLeafTabId(sibTree, 'C'), 'D');
    assertEq('sibling of D is C', tree.findSiblingLeafTabId(sibTree, 'D'), 'C');
    assertEq('sibling of tree root leaf is null', tree.findSiblingLeafTabId(tree.makeLeaf('Z'), 'Z'), null);
    assertEq('sibling of unknown tab is null', tree.findSiblingLeafTabId(sibTree, 'ZZZ'), null);

    /* ---- duplicate-tab / invalid-node rejection ---- */
    let dupBase = tree.makeLeaf('B');
    dupBase = tree.splitLeaf(dupBase, 'B', { axis: 'left-right', newTabId: 'C' });
    const dupAttempt = tree.splitLeaf(dupBase, 'B', { axis: 'left-right', newTabId: 'C' });
    assert('splitLeaf refuses duplicate newTabId already in tree', dupAttempt === dupBase);

    const badDupTree = {
        type: 'split',
        id: 'split-x',
        axis: 'left-right',
        ratio: 0.5,
        first: { type: 'leaf', tabId: 'B' },
        second: { type: 'leaf', tabId: 'B' },
    };
    const badDupResult = tree.validateTree(badDupTree);
    assert('validateTree rejects duplicate tabId', !badDupResult.ok);
    assert('validateTree duplicate error mentions tabId', badDupResult.errors.some(function (e) { return e.indexOf('duplicate tabId') !== -1; }));

    const badTypeTree = { type: 'octagon', foo: 1 };
    const badTypeResult = tree.validateTree(badTypeTree);
    assert('validateTree rejects unknown node type', !badTypeResult.ok);

    const badArityTree = { type: 'split', id: 'split-y', axis: 'left-right', ratio: 0.5, first: { type: 'leaf', tabId: 'Q' }, second: null };
    assert('validateTree rejects split missing a child', !tree.validateTree(badArityTree).ok);

    const badRatioTree = { type: 'split', id: 'split-z', axis: 'left-right', ratio: 1.5, first: { type: 'leaf', tabId: 'Q' }, second: { type: 'leaf', tabId: 'R' } };
    assert('validateTree rejects out-of-range ratio', !tree.validateTree(badRatioTree).ok);

    const badAxisTree = { type: 'split', id: 'split-w', axis: 'diagonal', ratio: 0.5, first: { type: 'leaf', tabId: 'Q' }, second: { type: 'leaf', tabId: 'R' } };
    assert('validateTree rejects invalid axis', !tree.validateTree(badAxisTree).ok);

    const dupSplitIdTree = tree.makeSplit(
        'left-right',
        tree.makeSplit('top-bottom', tree.makeLeaf('Q'), tree.makeLeaf('R'), 0.5, 'same-id'),
        tree.makeLeaf('S'),
        0.5,
        'same-id'
    );
    assert('validateTree rejects duplicate split ids', !tree.validateTree(dupSplitIdTree).ok);

    /* ---- normalizeTree standalone ---- */
    const alreadyNormal = tree.makeSplit('left-right', tree.makeLeaf('Q'), tree.makeLeaf('R'));
    assert('normalizeTree no-op on healthy tree returns same shape', tree.validateTree(tree.normalizeTree(alreadyNormal)).ok);
    assertEq('normalizeTree null stays null', tree.normalizeTree(null), null);
    assertEq('normalizeTree bare leaf stays leaf', tree.normalizeTree(tree.makeLeaf('Q')).tabId, 'Q');

            /* ---- collectSplitIds ---- */
            assertEq('collectSplitIds null', tree.collectSplitIds(null), []);
            assertEq('collectSplitIds bare leaf', tree.collectSplitIds(tree.makeLeaf('Q')), []);
            assertEq('collectSplitIds order matches deep tree', tree.collectSplitIds(deep).length, 3);
            assert('collectSplitIds returns ids present via findNodeById', tree.collectSplitIds(t).every(function (id) {
                return tree.findNodeById(t, id) !== null;
            }));

            /* ---- leafCount ---- */
    assertEq('leafCount null', tree.leafCount(null), 0);
    assertEq('leafCount single', tree.leafCount(tree.makeLeaf('Q')), 1);
    assertEq('leafCount deep tree', tree.leafCount(deep), 4);

    /* ---- Example from spec #53 verbatim ----
     *        X
     *       / \
     *      B   Y
     *         / \
     *        C   D
     */
    let ex = tree.makeSplit('left-right', tree.makeLeaf('B'), tree.makeSplit('top-bottom', tree.makeLeaf('C'), tree.makeLeaf('D')));
    let exAfterC = tree.removeLeaf(ex, 'C');
    assertEq('spec example remove C', tree.collectLeafTabIds(exAfterC), ['B', 'D']);
    assert('spec example remove C no redundant node', tree.isSplit(exAfterC) && tree.isLeaf(exAfterC.first) && tree.isLeaf(exAfterC.second));
    let exAfterB = tree.removeLeaf(exAfterC, 'B');
    assertEq('spec example remove B -> bare D', exAfterB.tabId, 'D');

    /* ---- Make Main deep-leaf example from spec #14/#56 ----
     * Main A; Secondary: split(B, split(C, D)). Make main on C:
     * replace C's leaf with A; mainTabId becomes C.
     */
    let makeMainTree = tree.makeSplit('top-bottom', tree.makeLeaf('B'), tree.makeSplit('left-right', tree.makeLeaf('C'), tree.makeLeaf('D')));
    const afterMakeMain = tree.replaceTabId(makeMainTree, 'C', 'A');
    assertEq('make main deep leaf order', tree.collectLeafTabIds(afterMakeMain), ['B', 'A', 'D']);
    assert('make main preserves axis of inner split', afterMakeMain.second.axis === 'left-right');
    assert('make main did not touch B subtree identity', afterMakeMain.first === makeMainTree.first);

    /* ---- moveLeafRelativeToTarget: atomic reposition of an existing leaf (drag-and-drop
     * workspace management #17-21/#51) -- remove, normalize, re-locate target, insert, in one
     * pure transaction. Covers the difficult shapes called out in the spec. ---- */

    /* Sibling swap/reposition: split(B, C) -> move B relative to C, placement 'second' (the
     * default -- target C stays first, moved B becomes second) -> split(C, B). */
    let swapBase = tree.makeLeaf('B');
    swapBase = tree.splitLeaf(swapBase, 'B', { axis: 'left-right', newTabId: 'C' });
    let swapped = tree.moveLeafRelativeToTarget(swapBase, 'B', 'C', { axis: 'left-right', placement: 'second' });
    assertEq('move sibling B right of C', tree.collectLeafTabIds(swapped), ['C', 'B']);
    assert('move sibling result valid', tree.validateTree(swapped).ok);

    /* Deep leaf -> ancestor sibling: split1(A, split2(B, split3(C, D))); move D beside A. */
    let moveDeep = tree.makeLeaf('A');
    moveDeep = tree.splitLeaf(moveDeep, 'A', { axis: 'left-right', newTabId: 'B' });
    moveDeep = tree.splitLeaf(moveDeep, 'B', { axis: 'top-bottom', newTabId: 'C' });
    moveDeep = tree.splitLeaf(moveDeep, 'C', { axis: 'left-right', newTabId: 'D' });
    assertEq('move-deep setup order', tree.collectLeafTabIds(moveDeep), ['A', 'B', 'C', 'D']);
    let deepToAncestor = tree.moveLeafRelativeToTarget(moveDeep, 'D', 'A', { axis: 'top-bottom', placement: 'second' });
    assertEq('deep leaf beside ancestor order', tree.collectLeafTabIds(deepToAncestor), ['A', 'D', 'B', 'C']);
    assert('deep leaf beside ancestor valid', tree.validateTree(deepToAncestor).ok);
    assertEq('deep leaf beside ancestor nests under A', deepToAncestor.first.first.tabId, 'A');
    assertEq('deep leaf beside ancestor D nested second', deepToAncestor.first.second.tabId, 'D');
    assertEq('deep leaf beside ancestor B,C unaffected branch', tree.collectLeafTabIds(deepToAncestor.second), ['B', 'C']);

    /* Ancestor-side leaf -> deep target: same tree, move A beside D (the deepest leaf). */
    let ancestorToDeep = tree.moveLeafRelativeToTarget(moveDeep, 'A', 'D', { axis: 'left-right', placement: 'second' });
    assertEq('ancestor leaf beside deep target order', tree.collectLeafTabIds(ancestorToDeep), ['B', 'C', 'D', 'A']);
    assert('ancestor leaf beside deep target valid', tree.validateTree(ancestorToDeep).ok);

    /* First child -> second branch, and second child -> first branch (placement flips). */
    let placementBase = tree.makeLeaf('X');
    placementBase = tree.splitLeaf(placementBase, 'X', { axis: 'left-right', newTabId: 'Y' });
    placementBase = tree.splitLeaf(placementBase, 'Y', { axis: 'top-bottom', newTabId: 'Z' });
    assertEq('placement-base order', tree.collectLeafTabIds(placementBase), ['X', 'Y', 'Z']);
    let firstToSecond = tree.moveLeafRelativeToTarget(placementBase, 'X', 'Z', { axis: 'top-bottom', placement: 'second' });
    assertEq('first child moved into second branch', tree.collectLeafTabIds(firstToSecond), ['Y', 'Z', 'X']);
    let secondToFirst = tree.moveLeafRelativeToTarget(placementBase, 'Z', 'X', { axis: 'left-right', placement: 'first' });
    assertEq('second-branch child moved before first', tree.collectLeafTabIds(secondToFirst), ['Z', 'X', 'Y']);

    /* Move causing the source's own parent split to collapse (2-leaf subtree loses one leaf,
     * parent becomes a bare leaf, then gets re-split at the target). */
    let collapseBase = tree.makeSplit('left-right', tree.makeLeaf('P'), tree.makeSplit('top-bottom', tree.makeLeaf('Q'), tree.makeLeaf('R')));
    let collapseMoved = tree.moveLeafRelativeToTarget(collapseBase, 'Q', 'P', { axis: 'left-right', placement: 'second' });
    assertEq('move causing parent collapse order', tree.collectLeafTabIds(collapseMoved), ['R', 'P', 'Q']);
    assert('move causing parent collapse valid', tree.validateTree(collapseMoved).ok);
    /* R (Q's old sibling, never touched by the move) keeps its exact leaf identity/position --
     * only Q's old split parent, now redundant, actually collapses away. */
    assertEq('move causing parent collapse keeps R as new root leaf-first', collapseMoved.first.tabId, 'R');

    /* Target path changes after source removal: removing the source collapses a split that
     * used to sit BETWEEN the tree root and the target, so the target must be re-located in the
     * post-removal tree, not looked up by a stale pre-removal path. */
    let pathChange = tree.makeSplit('left-right', tree.makeSplit('top-bottom', tree.makeLeaf('M'), tree.makeLeaf('N')), tree.makeLeaf('T'));
    let pathChanged = tree.moveLeafRelativeToTarget(pathChange, 'N', 'T', { axis: 'left-right', placement: 'first' });
    assertEq('target path change order', tree.collectLeafTabIds(pathChanged), ['M', 'N', 'T']);
    assert('target path change valid', tree.validateTree(pathChanged).ok);

    /* Preserve unaffected split IDs: a move inside one branch must not touch a sibling split's
     * id, even though the whole tree is re-published as one new root. */
    let idBase = tree.makeSplit('left-right', tree.makeSplit('top-bottom', tree.makeLeaf('E'), tree.makeLeaf('F')), tree.makeSplit('top-bottom', tree.makeLeaf('G'), tree.makeLeaf('H')));
    const untouchedBranchId = idBase.second.id;
    let idMoved = tree.moveLeafRelativeToTarget(idBase, 'E', 'F', { axis: 'left-right', placement: 'first' });
    assertEq('preserve-id order', tree.collectLeafTabIds(idMoved), ['E', 'F', 'G', 'H']);
    assert('preserve unaffected sibling branch id', idMoved.second.id === untouchedBranchId);
    assert('preserve unaffected sibling branch identity by reference', idMoved.second === idBase.second);

    /* No duplicate tab IDs: source == target, missing source, missing target all reject with
     * the tree entirely unchanged (same reference back, not just structurally equal). */
    assert('moveLeafRelativeToTarget rejects self move', tree.moveLeafRelativeToTarget(swapBase, 'B', 'B', { axis: 'left-right' }) === swapBase);
    assert('moveLeafRelativeToTarget rejects missing source', tree.moveLeafRelativeToTarget(swapBase, 'ZZZ', 'C', { axis: 'left-right' }) === swapBase);
    assert('moveLeafRelativeToTarget rejects missing target', tree.moveLeafRelativeToTarget(swapBase, 'B', 'ZZZ', { axis: 'left-right' }) === swapBase);
    assert('moveLeafRelativeToTarget no duplicate ids after a real move', tree.validateTree(swapped).ok);

    /* ---- No persistence ---- */
    const src = require('fs').readFileSync(path.join(rootDir, 'frontend/js/workspace-tree.js'), 'utf8');
    assert('workspace-tree.js has no localStorage', src.indexOf('localStorage') === -1);
    assert('workspace-tree.js has no sessionStorage', src.indexOf('sessionStorage') === -1);
    assert('workspace-tree.js has no indexedDB', src.indexOf('indexedDB') === -1);
    assert('workspace-tree.js has no DOM access', src.indexOf('document.') === -1);

    if (failed) {
        console.log(failed + ' failed, ' + passed + ' passed');
        process.exit(1);
    }
    console.log('All ' + passed + ' workspace tree checks passed, 0 failed');
}

run();
