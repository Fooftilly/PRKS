/**
 * Secondary-region tree model. Pure, dependency-free helpers for the recursive
 * Secondary layout: `null` (no Secondary), a single leaf `{type:'leaf', tabId}`,
 * or a recursive `{type:'split', id, axis, ratio, first, second}`.
 *
 * `ratio` on a split node means `first child size / usable split-node size` and
 * belongs to that split node only -- never to a tab, and never to the root
 * Main/Secondary ratio (that lives in workspace-tabs.js as `mainSplitRatio`).
 *
 * These helpers never touch DOM, TabContexts, history, or workspace state.
 * workspace-tabs.js owns when/why the tree changes. workspace-persistence.js
 * may call makeLeaf/makeSplit when rehydrating a stored tree (fresh split IDs).
 * All mutation helpers are pure: they return a new root and never mutate the
 * node objects passed in (unrelated subtrees are returned by reference so
 * DOM/host reconciliation can key off `tabId`/`split.id`, not object identity).
 */
(function (root) {
    'use strict';

    const AXES = { 'left-right': true, 'top-bottom': true };
    const DEFAULT_SPLIT_RATIO = 0.5;

    let splitSeq = 0;

    function resetSplitIds() {
        splitSeq = 0;
    }

    function nextSplitId() {
        splitSeq += 1;
        return 'split-' + splitSeq;
    }

    function makeLeaf(tabId) {
        return { type: 'leaf', tabId: String(tabId) };
    }

    function clampRatio01(value) {
        const n = Number(value);
        if (!Number.isFinite(n)) return DEFAULT_SPLIT_RATIO;
        return Math.max(0, Math.min(1, n));
    }

    function makeSplit(axis, first, second, ratio, id) {
        return {
            type: 'split',
            id: id || nextSplitId(),
            axis: axis === 'top-bottom' ? 'top-bottom' : 'left-right',
            ratio: clampRatio01(ratio == null ? DEFAULT_SPLIT_RATIO : ratio),
            first: first,
            second: second,
        };
    }

    function isLeaf(node) {
        return !!(node && node.type === 'leaf' && node.tabId);
    }

    function isSplit(node) {
        return !!(node && node.type === 'split' && node.id && node.first && node.second);
    }

    /** Depth-first (first before second) leaf tabIds. Deterministic tree order. */
    function collectLeafTabIds(tree) {
        const out = [];
        (function walk(node) {
            if (!node) return;
            if (isLeaf(node)) {
                out.push(node.tabId);
                return;
            }
            if (isSplit(node)) {
                walk(node.first);
                walk(node.second);
            }
        })(tree);
        return out;
    }

        /** Depth-first (first before second) split-node IDs. Used for DOM-host pruning. */
        function collectSplitIds(tree) {
            const out = [];
            (function walk(node) {
                if (!node) return;
                if (isSplit(node)) {
                    out.push(node.id);
                    walk(node.first);
                    walk(node.second);
                }
            })(tree);
            return out;
        }

        function containsTab(tree, tabId) {
        if (!tabId) return false;
        return collectLeafTabIds(tree).indexOf(tabId) !== -1;
    }

    function findLeafByTabId(tree, tabId) {
        if (!tabId) return null;
        let found = null;
        (function walk(node) {
            if (found || !node) return;
            if (isLeaf(node)) {
                if (node.tabId === tabId) found = node;
                return;
            }
            if (isSplit(node)) {
                walk(node.first);
                walk(node.second);
            }
        })(tree);
        return found;
    }

    function findNodeById(tree, nodeId) {
        if (!nodeId) return null;
        let found = null;
        (function walk(node) {
            if (found || !node) return;
            if (isSplit(node)) {
                if (node.id === nodeId) {
                    found = node;
                    return;
                }
                walk(node.first);
                walk(node.second);
            }
        })(tree);
        return found;
    }

    /** Rebuilds the path from root to the node matching `predicate`, shallow-copying only
     * the nodes on that path. Returns { tree, replaced } where `replaced` is true if a
     * replacement occurred. `transform(node)` returns the replacement node (or null to remove). */
    function rewritePath(tree, predicate, transform) {
        let replaced = false;

        function walk(node) {
            if (!node) return node;
            if (predicate(node)) {
                replaced = true;
                return transform(node);
            }
            if (!isSplit(node)) return node;
            const nextFirst = walk(node.first);
            const nextSecond = walk(node.second);
            if (nextFirst === node.first && nextSecond === node.second) return node;
            if (!nextFirst && !nextSecond) return null;
            if (!nextFirst) return nextSecond;
            if (!nextSecond) return nextFirst;
            return makeSplit(node.axis, nextFirst, nextSecond, node.ratio, node.id);
        }

        const nextTree = walk(tree);
        return { tree: nextTree, replaced: replaced };
    }

    /** Replaces the leaf node with the given tabId by an arbitrary replacement node
     * (leaf or subtree). Returns the new tree root, or the original tree if not found. */
    function replaceLeaf(tree, tabId, replacement) {
        if (!tabId) return tree;
        const result = rewritePath(
            tree,
            function (node) {
                return isLeaf(node) && node.tabId === tabId;
            },
            function () {
                return replacement;
            }
        );
        return result.tree;
    }

    /** Renames a leaf's tabId in place without restructuring the tree. Used by Make Main's
     * in-place role swap: the promoted leaf's exact tree position receives the old Main tabId. */
    function replaceTabId(tree, oldTabId, newTabId) {
        return replaceLeaf(tree, oldTabId, makeLeaf(newTabId));
    }

    /**
     * Splits the leaf identified by `tabId` into a new split node.
     * `options.axis`: 'left-right' | 'top-bottom'.
     * `options.newTabId`: the tab going into the new leaf.
     * `options.placement`: 'first' | 'second' (default 'second' -- existing leaf stays first).
     * `options.ratio`: optional initial ratio (default 0.5).
     * Returns the new tree root, or the original tree if `tabId` is not a leaf in it.
     */
    function splitLeaf(tree, tabId, options) {
        const opts = options || {};
        if (!containsTab(tree, tabId) || containsTab(tree, opts.newTabId)) return tree;
        const axis = opts.axis === 'top-bottom' ? 'top-bottom' : 'left-right';
        const existing = makeLeaf(tabId);
        const incoming = makeLeaf(opts.newTabId);
        const first = opts.placement === 'first' ? incoming : existing;
        const second = opts.placement === 'first' ? existing : incoming;
        const splitNode = makeSplit(axis, first, second, opts.ratio);
        return replaceLeaf(tree, tabId, splitNode);
    }

    /**
     * Atomically repositions an existing leaf (`sourceTabId`) relative to another existing
     * leaf (`targetTabId`) in one pure transaction: remove `sourceTabId` from a temporary tree,
     * normalize it, re-locate `targetTabId` in that normalized tree (its path may have changed
     * -- a collapse can promote a different node to root, or shift which split now holds it),
     * then insert `sourceTabId` next to it via the same `splitLeaf` primitive used for ordinary
     * insertion. Returns one final valid tree; never a sequence of intermediate published trees.
     * `options.axis`: 'left-right' | 'top-bottom'. `options.placement`: 'first' | 'second'
     * (default 'second' -- target stays first, moved leaf becomes second).
     * Nodes outside the affected path are returned by reference (via `removeLeaf`/`splitLeaf`'s
     * own path-rewriting), so unrelated split IDs are preserved automatically.
     * No-op (returns the original tree unchanged) when: source/target are the same, either is
     * missing from `tree`, or (defensively) target can no longer be found after removing source.
     */
    function moveLeafRelativeToTarget(tree, sourceTabId, targetTabId, options) {
        if (!sourceTabId || !targetTabId || sourceTabId === targetTabId) return tree;
        if (!containsTab(tree, sourceTabId) || !containsTab(tree, targetTabId)) return tree;
        const opts = options || {};
        const axis = opts.axis === 'top-bottom' ? 'top-bottom' : 'left-right';
        const placement = opts.placement === 'first' ? 'first' : 'second';
        const withoutSource = normalizeTree(removeLeaf(tree, sourceTabId));
        if (!containsTab(withoutSource, targetTabId)) return tree;
        return splitLeaf(withoutSource, targetTabId, {
            axis: axis,
            newTabId: sourceTabId,
            placement: placement,
            ratio: opts.ratio,
        });
    }

    /** Removes the leaf with `tabId`, collapsing any split left with a single child
     * (repeated upward). Returns the new tree root, or `null` if the whole tree collapsed
     * (i.e. the removed leaf was the entire tree). */
    function removeLeaf(tree, tabId) {
        if (!tree || !tabId) return tree;
        const result = rewritePath(
            tree,
            function (node) {
                return isLeaf(node) && node.tabId === tabId;
            },
            function () {
                return null;
            }
        );
        return result.replaced ? result.tree : tree;
    }

    /** The other leaf/subtree's first leaf tabId at the immediate parent of `tabId`'s leaf.
     * Used to focus "the closest surviving sibling" after a close/collapse. Null if `tabId`
     * is the tree root (no parent) or not found. */
    function findSiblingLeafTabId(tree, tabId) {
        let sibling = null;
        (function walk(node) {
            if (sibling || !isSplit(node)) return;
            if (isLeaf(node.first) && node.first.tabId === tabId) {
                sibling = collectLeafTabIds(node.second)[0] || null;
                return;
            }
            if (isLeaf(node.second) && node.second.tabId === tabId) {
                sibling = collectLeafTabIds(node.first)[0] || null;
                return;
            }
            walk(node.first);
            walk(node.second);
        })(tree);
        return sibling;
    }

    function setSplitRatio(tree, splitId, ratio) {
        if (!splitId) return tree;
        const result = rewritePath(
            tree,
            function (node) {
                return isSplit(node) && node.id === splitId;
            },
            function (node) {
                return makeSplit(node.axis, node.first, node.second, clampRatio01(ratio), node.id);
            }
        );
        return result.tree;
    }

    /** Collapses any split node left with only one meaningful child. Structural safety net;
     * `removeLeaf` already normalizes, but this is exposed for defensive re-validation. */
    function normalizeTree(tree) {
        if (!tree) return null;
        if (isLeaf(tree)) return tree;
        if (!isSplit(tree)) return null;
        const first = normalizeTree(tree.first);
        const second = normalizeTree(tree.second);
        if (!first && !second) return null;
        if (!first) return second;
        if (!second) return first;
        if (first === tree.first && second === tree.second) return tree;
        return makeSplit(tree.axis, first, second, tree.ratio, tree.id);
    }

    /**
     * Validates structural invariants that this module can check on its own (duplicate
     * tabIds, unknown node types, split arity, ratio validity, unique split IDs). Callers
     * (workspace-tabs.js) additionally check workspace-level invariants such as "no Main tab
     * inside secondaryTree" and "every leaf references an existing logical tab", since this
     * module has no notion of the tab list or mainTabId.
     * Returns { ok, errors: string[] }.
     */
    function validateTree(tree) {
        const errors = [];
        if (tree == null) return { ok: true, errors: errors };
        const seenTabIds = Object.create(null);
        const seenSplitIds = Object.create(null);

        (function walk(node, path) {
            if (node == null) {
                errors.push('null node at ' + path);
                return;
            }
            if (node.type === 'leaf') {
                if (!node.tabId) {
                    errors.push('leaf missing tabId at ' + path);
                    return;
                }
                if (seenTabIds[node.tabId]) {
                    errors.push('duplicate tabId ' + node.tabId + ' at ' + path);
                }
                seenTabIds[node.tabId] = true;
                return;
            }
            if (node.type === 'split') {
                if (!node.id) errors.push('split missing id at ' + path);
                else if (seenSplitIds[node.id]) errors.push('duplicate split id ' + node.id + ' at ' + path);
                else seenSplitIds[node.id] = true;
                if (!AXES[node.axis]) errors.push('invalid axis ' + node.axis + ' at ' + path);
                if (!Number.isFinite(node.ratio) || node.ratio < 0 || node.ratio > 1) {
                    errors.push('invalid ratio ' + node.ratio + ' at ' + path);
                }
                if (!node.first || !node.second) {
                    errors.push('split missing a child at ' + path);
                    return;
                }
                walk(node.first, path + '.first');
                walk(node.second, path + '.second');
                return;
            }
            errors.push('unknown node type ' + node.type + ' at ' + path);
        })(tree, 'root');

        return { ok: errors.length === 0, errors: errors };
    }

    function leafCount(tree) {
        return collectLeafTabIds(tree).length;
    }

    const api = {
        resetSplitIds: resetSplitIds,
        nextSplitId: nextSplitId,
        makeLeaf: makeLeaf,
        makeSplit: makeSplit,
        isLeaf: isLeaf,
        isSplit: isSplit,
        collectLeafTabIds: collectLeafTabIds,
        collectSplitIds: collectSplitIds,
        containsTab: containsTab,
        findLeafByTabId: findLeafByTabId,
        findNodeById: findNodeById,
        replaceLeaf: replaceLeaf,
        replaceTabId: replaceTabId,
        splitLeaf: splitLeaf,
        removeLeaf: removeLeaf,
        moveLeafRelativeToTarget: moveLeafRelativeToTarget,
        findSiblingLeafTabId: findSiblingLeafTabId,
        setSplitRatio: setSplitRatio,
        normalizeTree: normalizeTree,
        validateTree: validateTree,
        leafCount: leafCount,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
