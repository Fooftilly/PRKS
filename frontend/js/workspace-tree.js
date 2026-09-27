/**
 * Secondary-region tree adapter.
 *
 * The typed workspace model (frontend/js/workspace-model.js, built from
 * frontend-app/src/workspace/) is the only tree implementation. This file
 * keeps the legacy global names and owns the per-runtime split-id counter.
 * Split ids are not persisted. Do not mutate trees here.
 */
(function (root) {
    'use strict';

    function workspaceModel() {
        if (typeof require === 'function' && typeof module !== 'undefined' && module.exports) {
            return require('./workspace-model.js');
        }
        if (!root.prksWorkspaceModel) {
            throw new Error('PRKS workspace model is not loaded');
        }
        return root.prksWorkspaceModel;
    }

    let splitSeq = 0;

    function resetSplitIds() {
        splitSeq = 0;
    }

    function nextSplitId() {
        splitSeq += 1;
        return 'split-' + splitSeq;
    }

    function makeLeaf(tabId) {
        return workspaceModel().makeLeaf(tabId);
    }

    function makeSplit(axis, first, second, ratio, id) {
        return workspaceModel().makeSplit(axis, first, second, ratio, id || nextSplitId());
    }

    function splitLeaf(tree, tabId, options) {
        return workspaceModel().splitLeaf(tree, tabId, options, nextSplitId);
    }

    function moveLeafRelativeToTarget(tree, sourceTabId, targetTabId, options) {
        return workspaceModel().moveLeafRelativeToTarget(tree, sourceTabId, targetTabId, options, nextSplitId);
    }

    const api = {
        resetSplitIds: resetSplitIds,
        nextSplitId: nextSplitId,
        makeLeaf: makeLeaf,
        makeSplit: makeSplit,
        isLeaf: function (node) {
            return workspaceModel().isLeaf(node);
        },
        isSplit: function (node) {
            return workspaceModel().isSplit(node);
        },
        collectLeafTabIds: function (tree) {
            return workspaceModel().collectLeafTabIds(tree);
        },
        collectSplitIds: function (tree) {
            return workspaceModel().collectSplitIds(tree);
        },
        containsTab: function (tree, tabId) {
            return workspaceModel().containsTab(tree, tabId);
        },
        findLeafByTabId: function (tree, tabId) {
            return workspaceModel().findLeafByTabId(tree, tabId);
        },
        findNodeById: function (tree, nodeId) {
            return workspaceModel().findNodeById(tree, nodeId);
        },
        replaceLeaf: function (tree, tabId, replacement) {
            return workspaceModel().replaceLeaf(tree, tabId, replacement);
        },
        replaceTabId: function (tree, oldTabId, newTabId) {
            return workspaceModel().replaceTabId(tree, oldTabId, newTabId);
        },
        splitLeaf: splitLeaf,
        removeLeaf: function (tree, tabId) {
            return workspaceModel().removeLeaf(tree, tabId);
        },
        moveLeafRelativeToTarget: moveLeafRelativeToTarget,
        findSiblingLeafTabId: function (tree, tabId) {
            return workspaceModel().findSiblingLeafTabId(tree, tabId);
        },
        setSplitRatio: function (tree, splitId, ratio) {
            return workspaceModel().setSplitRatio(tree, splitId, ratio);
        },
        normalizeTree: function (tree) {
            return workspaceModel().normalizeTree(tree);
        },
        validateTree: function (tree) {
            return workspaceModel().validateTree(tree);
        },
        leafCount: function (tree) {
            return workspaceModel().leafCount(tree);
        },
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
