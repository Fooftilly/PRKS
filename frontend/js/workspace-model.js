var prksWorkspaceModel = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	//#region src/workspace/constants.ts
	/** In-memory workspace snapshot version. Not the persistence schema version. */
	var WORKSPACE_STATE_VERSION = 1;
	/** Main region width / usable root split width. */
	var DEFAULT_MAIN_SPLIT_RATIO = .58;
	/** Nested Secondary split ratio (`first / this split`). */
	var DEFAULT_NESTED_SPLIT_RATIO = .5;
	/** 1 Main + at most 3 Secondary leaves mounted at once. */
	var MAX_VISIBLE_PANES = 4;
	//#endregion
	//#region src/workspace/types.ts
	function asTabId(value) {
		return String(value);
	}
	function asSplitId(value) {
		return String(value);
	}
	//#endregion
	//#region src/workspace/tree.ts
	/**
	* Pure Secondary-tree transforms. No DOM, history, TabContext, or module-level
	* id counter. Callers pass `nextSplitId` when a new split node is created.
	* Unchanged subtrees are returned by reference so host reconciliation can key
	* off tab id / split id rather than object identity.
	*/
	var AXES = {
		"left-right": true,
		"top-bottom": true
	};
	function clampNestedRatio(value) {
		const n = Number(value);
		if (!Number.isFinite(n)) return DEFAULT_NESTED_SPLIT_RATIO;
		return Math.max(0, Math.min(1, n));
	}
	function makeLeaf(tabId) {
		return {
			type: "leaf",
			tabId: asTabId(String(tabId))
		};
	}
	function makeSplit(axis, first, second, ratio, id) {
		return {
			type: "split",
			id: asSplitId(id),
			axis: axis === "top-bottom" ? "top-bottom" : "left-right",
			ratio: clampNestedRatio(ratio == null ? DEFAULT_NESTED_SPLIT_RATIO : ratio),
			first,
			second
		};
	}
	function isLeaf(node) {
		if (!node || typeof node !== "object") return false;
		const record = node;
		return record.type === "leaf" && !!record.tabId;
	}
	function isSplit(node) {
		if (!node || typeof node !== "object") return false;
		const record = node;
		return record.type === "split" && !!record.id && !!record.first && !!record.second;
	}
	function collectLeafTabIds(tree) {
		const out = [];
		const walk = (node) => {
			if (!node) return;
			if (isLeaf(node)) {
				out.push(node.tabId);
				return;
			}
			if (isSplit(node)) {
				walk(node.first);
				walk(node.second);
			}
		};
		walk(tree);
		return out;
	}
	function collectSplitIds(tree) {
		const out = [];
		const walk = (node) => {
			if (!node) return;
			if (isSplit(node)) {
				out.push(node.id);
				walk(node.first);
				walk(node.second);
			}
		};
		walk(tree);
		return out;
	}
	function containsTab(tree, tabId) {
		if (!tabId) return false;
		return collectLeafTabIds(tree).indexOf(tabId) !== -1;
	}
	function findLeafByTabId(tree, tabId) {
		if (!tabId) return null;
		let found = null;
		const walk = (node) => {
			if (found || !node) return;
			if (isLeaf(node)) {
				if (node.tabId === tabId) found = node;
				return;
			}
			if (isSplit(node)) {
				walk(node.first);
				walk(node.second);
			}
		};
		walk(tree);
		return found;
	}
	function findNodeById(tree, nodeId) {
		if (!nodeId) return null;
		let found = null;
		const walk = (node) => {
			if (found || !node) return;
			if (isSplit(node)) {
				if (node.id === nodeId) {
					found = node;
					return;
				}
				walk(node.first);
				walk(node.second);
			}
		};
		walk(tree);
		return found;
	}
	function rewritePath(tree, predicate, transform) {
		let replaced = false;
		const walk = (node) => {
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
		};
		return {
			tree: walk(tree),
			replaced
		};
	}
	function replaceLeaf(tree, tabId, replacement) {
		if (!tabId) return tree;
		return rewritePath(tree, (node) => isLeaf(node) && node.tabId === tabId, () => replacement).tree;
	}
	function replaceTabId(tree, oldTabId, newTabId) {
		return replaceLeaf(tree, oldTabId, makeLeaf(newTabId));
	}
	function splitLeaf(tree, tabId, options, nextSplitId) {
		const opts = options || {};
		if (!containsTab(tree, tabId) || containsTab(tree, opts.newTabId)) return tree;
		const axis = opts.axis === "top-bottom" ? "top-bottom" : "left-right";
		const existing = makeLeaf(tabId);
		const incoming = makeLeaf(String(opts.newTabId));
		return replaceLeaf(tree, tabId, makeSplit(axis, opts.placement === "first" ? incoming : existing, opts.placement === "first" ? existing : incoming, opts.ratio, nextSplitId()));
	}
	function moveLeafRelativeToTarget(tree, sourceTabId, targetTabId, options, nextSplitId) {
		if (!sourceTabId || !targetTabId || sourceTabId === targetTabId) return tree;
		if (!containsTab(tree, sourceTabId) || !containsTab(tree, targetTabId)) return tree;
		const opts = options || {};
		const axis = opts.axis === "top-bottom" ? "top-bottom" : "left-right";
		const placement = opts.placement === "first" ? "first" : "second";
		const withoutSource = normalizeTree(removeLeaf(tree, sourceTabId));
		if (!containsTab(withoutSource, targetTabId)) return tree;
		return splitLeaf(withoutSource, targetTabId, {
			axis,
			newTabId: sourceTabId,
			placement,
			ratio: opts.ratio
		}, nextSplitId);
	}
	function removeLeaf(tree, tabId) {
		if (!tree || !tabId) return tree;
		const result = rewritePath(tree, (node) => isLeaf(node) && node.tabId === tabId, () => null);
		return result.replaced ? result.tree : tree;
	}
	function findSiblingLeafTabId(tree, tabId) {
		let sibling = null;
		const walk = (node) => {
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
		};
		walk(tree);
		return sibling;
	}
	function setSplitRatio(tree, splitId, ratio) {
		if (!splitId) return tree;
		return rewritePath(tree, (node) => isSplit(node) && node.id === splitId, (node) => {
			const split = node;
			return makeSplit(split.axis, split.first, split.second, clampNestedRatio(ratio), split.id);
		}).tree;
	}
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
	function validateTree(tree) {
		const errors = [];
		if (tree == null) return {
			ok: true,
			errors
		};
		const seenTabIds = Object.create(null);
		const seenSplitIds = Object.create(null);
		const walk = (node, path) => {
			if (node == null) {
				errors.push("null node at " + path);
				return;
			}
			if (typeof node !== "object") {
				errors.push("unknown node type " + String(node) + " at " + path);
				return;
			}
			const record = node;
			if (record.type === "leaf") {
				if (!record.tabId) {
					errors.push("leaf missing tabId at " + path);
					return;
				}
				const id = String(record.tabId);
				if (seenTabIds[id]) errors.push("duplicate tabId " + id + " at " + path);
				seenTabIds[id] = true;
				return;
			}
			if (record.type === "split") {
				if (!record.id) errors.push("split missing id at " + path);
				else if (seenSplitIds[String(record.id)]) errors.push("duplicate split id " + String(record.id) + " at " + path);
				else seenSplitIds[String(record.id)] = true;
				if (!AXES[String(record.axis)]) errors.push("invalid axis " + String(record.axis) + " at " + path);
				const ratio = record.ratio;
				if (typeof ratio !== "number" || !Number.isFinite(ratio) || ratio < 0 || ratio > 1) errors.push("invalid ratio " + String(ratio) + " at " + path);
				if (!record.first || !record.second) {
					errors.push("split missing a child at " + path);
					return;
				}
				walk(record.first, path + ".first");
				walk(record.second, path + ".second");
				return;
			}
			errors.push("unknown node type " + String(record.type) + " at " + path);
		};
		walk(tree, "root");
		return {
			ok: errors.length === 0,
			errors
		};
	}
	function leafCount(tree) {
		return collectLeafTabIds(tree).length;
	}
	//#endregion
	//#region src/workspace/derive.ts
	function visualTiled(state, presentation) {
		return state.mode === "tiled" && !presentation.narrowFallback && !!state.secondaryTree;
	}
	function findTab(state, tabId) {
		if (!tabId) return null;
		for (let i = 0; i < state.tabs.length; i++) if (state.tabs[i].id === tabId) return state.tabs[i];
		return null;
	}
	/** On screen. Hidden-split leaves and narrow-fallback leaves are logical tabs, not visible panes. */
	function isTabVisible(state, tabId, presentation) {
		if (!findTab(state, tabId)) return false;
		if (tabId === state.mainTabId) return true;
		return visualTiled(state, presentation) && containsTab(state.secondaryTree, tabId);
	}
	function secondaryLeafCapReached(tree) {
		return leafCount(tree) >= 3;
	}
	//#endregion
	//#region src/workspace/transitions.ts
	/**
	* Pure workspace transitions. Each function returns the next canonical state
	* and does not touch DOM, history, TabContext, or its inputs.
	*
	* Leave/dirty preflight, URL updates, paint, and mount/park/destroy stay in
	* the coordinator. Tile eligibility is an input (`oldMainSupportsTile`), not
	* a route parse.
	*/
	function patchState(state, patch) {
		return {
			version: state.version,
			mode: patch.mode !== void 0 ? patch.mode : state.mode,
			mainTabId: patch.mainTabId !== void 0 ? patch.mainTabId : state.mainTabId,
			focusedTabId: patch.focusedTabId !== void 0 ? patch.focusedTabId : state.focusedTabId,
			secondaryTree: patch.secondaryTree !== void 0 ? patch.secondaryTree : state.secondaryTree,
			tabs: patch.tabs !== void 0 ? patch.tabs : state.tabs,
			mainSplitRatio: patch.mainSplitRatio !== void 0 ? patch.mainSplitRatio : state.mainSplitRatio
		};
	}
	function withoutTab(tabs, tabId) {
		const next = [];
		for (let i = 0; i < tabs.length; i++) if (tabs[i].id !== tabId) next.push(tabs[i]);
		return next;
	}
	/** In-place role swap. Does not paint, touch the URL, or run leave checks. */
	function planMakeMain(state, tabId, oldMainSupportsTile) {
		const tab = findTab(state, tabId);
		if (!tab) return {
			ok: false,
			changed: false,
			state,
			coldParkTabId: null
		};
		if (tab.id === state.mainTabId) {
			if (state.focusedTabId === tab.id) return {
				ok: true,
				changed: false,
				state,
				coldParkTabId: null
			};
			return {
				ok: true,
				changed: true,
				state: patchState(state, { focusedTabId: tab.id }),
				coldParkTabId: null
			};
		}
		if (!containsTab(state.secondaryTree, tab.id)) return {
			ok: false,
			changed: false,
			state,
			coldParkTabId: null
		};
		const oldMain = findTab(state, state.mainTabId);
		let secondaryTree = state.secondaryTree;
		let coldParkTabId = null;
		if (oldMain && oldMainSupportsTile) secondaryTree = replaceTabId(state.secondaryTree, tab.id, oldMain.id);
		else {
			secondaryTree = normalizeTree(removeLeaf(state.secondaryTree, tab.id));
			if (oldMain && oldMain.id !== tab.id) coldParkTabId = oldMain.id;
		}
		const mode = secondaryTree ? state.mode : "stacked";
		return {
			ok: true,
			changed: true,
			coldParkTabId,
			state: patchState(state, {
				secondaryTree,
				mainTabId: tab.id,
				focusedTabId: tab.id,
				mode
			})
		};
	}
	function planHideLeaf(state, tabId) {
		if (!containsTab(state.secondaryTree, tabId)) return {
			ok: false,
			state
		};
		const sibling = findSiblingLeafTabId(state.secondaryTree, tabId);
		const secondaryTree = normalizeTree(removeLeaf(state.secondaryTree, tabId));
		const mode = secondaryTree ? state.mode : "stacked";
		let focusedTabId = state.focusedTabId;
		if (focusedTabId === tabId) {
			const nextLeaves = collectLeafTabIds(secondaryTree);
			focusedTabId = (sibling && nextLeaves.indexOf(sibling) !== -1 ? sibling : nextLeaves[0] || null) || state.mainTabId;
		}
		return {
			ok: true,
			state: patchState(state, {
				secondaryTree,
				mode,
				focusedTabId
			})
		};
	}
	function planFocus(state, tabId, presentation) {
		if (!findTab(state, tabId)) return {
			ok: false,
			changed: false,
			state
		};
		if (!isTabVisible(state, tabId, presentation)) return {
			ok: false,
			changed: false,
			state
		};
		if (state.mode === "stacked" && tabId !== state.mainTabId) return {
			ok: false,
			changed: false,
			state
		};
		if (state.focusedTabId === tabId) return {
			ok: true,
			changed: false,
			state
		};
		return {
			ok: true,
			changed: true,
			state: patchState(state, { focusedTabId: asTabId(tabId) })
		};
	}
	function planReorder(state, tabId, beforeTabId) {
		let idx = -1;
		for (let i = 0; i < state.tabs.length; i++) if (state.tabs[i].id === tabId) {
			idx = i;
			break;
		}
		if (idx < 0 || tabId === beforeTabId) return {
			ok: false,
			state
		};
		const tabs = state.tabs.slice();
		const tab = tabs.splice(idx, 1)[0];
		let insertAt = tabs.length;
		if (beforeTabId) {
			for (let i = 0; i < tabs.length; i++) if (tabs[i].id === beforeTabId) {
				insertAt = i;
				break;
			}
		}
		tabs.splice(insertAt, 0, tab);
		return {
			ok: true,
			state: patchState(state, { tabs })
		};
	}
	function planMovePane(state, sourceTabId, targetTabId, axis, placement, presentation, nextSplitId) {
		if (!sourceTabId || !targetTabId || sourceTabId === targetTabId) return {
			ok: false,
			state
		};
		if (sourceTabId === state.mainTabId || targetTabId === state.mainTabId) return {
			ok: false,
			state
		};
		if (!visualTiled(state, presentation)) return {
			ok: false,
			state
		};
		if (!containsTab(state.secondaryTree, sourceTabId) || !containsTab(state.secondaryTree, targetTabId)) return {
			ok: false,
			state
		};
		const useAxis = axis === "top-bottom" ? "top-bottom" : "left-right";
		const usePlacement = placement === "first" ? "first" : "second";
		const secondaryTree = moveLeafRelativeToTarget(state.secondaryTree, sourceTabId, targetTabId, {
			axis: useAxis,
			placement: usePlacement
		}, nextSplitId);
		if (secondaryTree === state.secondaryTree) return {
			ok: false,
			state
		};
		return {
			ok: true,
			state: patchState(state, { secondaryTree })
		};
	}
	function clampMainSplitRatio(value) {
		const n = Number(value);
		if (!Number.isFinite(n)) return DEFAULT_MAIN_SPLIT_RATIO;
		return Math.max(0, Math.min(1, n));
	}
	function planMainSplitRatio(state, ratio) {
		const next = clampMainSplitRatio(ratio);
		if (next === state.mainSplitRatio) return {
			state,
			ratio: next
		};
		return {
			state: patchState(state, { mainSplitRatio: next }),
			ratio: next
		};
	}
	function planNestedSplitRatio(state, splitId, ratio) {
		if (!findNodeById(state.secondaryTree, splitId)) return {
			ok: false,
			state,
			ratio: null
		};
		const secondaryTree = setSplitRatio(state.secondaryTree, splitId, ratio);
		const updated = findNodeById(secondaryTree, splitId);
		return {
			ok: true,
			state: patchState(state, { secondaryTree }),
			ratio: updated ? updated.ratio : null
		};
	}
	function planSetMode(state, mode, presentation) {
		if (mode !== "stacked" && mode !== "tiled") return {
			ok: false,
			state
		};
		if (mode === "tiled") {
			if (!state.secondaryTree) return {
				ok: false,
				state
			};
			return {
				ok: true,
				kind: "show",
				state: patchState(state, {
					mode: "tiled",
					focusedTabId: state.mainTabId
				})
			};
		}
		if (state.mode === "stacked" && !visualTiled(state, presentation)) return {
			ok: true,
			kind: "noop",
			state
		};
		return {
			ok: true,
			kind: "hide",
			leafIds: collectLeafTabIds(state.secondaryTree),
			state: patchState(state, {
				mode: "stacked",
				focusedTabId: state.mainTabId
			})
		};
	}
	function planCloseTab(state, tabId, homeTab) {
		let idx = -1;
		for (let i = 0; i < state.tabs.length; i++) if (state.tabs[i].id === tabId) {
			idx = i;
			break;
		}
		if (idx < 0) return {
			ok: false,
			needsHomeTab: false,
			state,
			successorId: null,
			promotedLeaf: false
		};
		const closing = state.tabs[idx];
		const closingMain = closing.id === state.mainTabId;
		const closingLeaf = containsTab(state.secondaryTree, closing.id);
		if (!closingMain) {
			let secondaryTree = state.secondaryTree;
			let mode = state.mode;
			let focusedTabId = state.focusedTabId;
			if (closingLeaf && state.secondaryTree) {
				const sibling = findSiblingLeafTabId(state.secondaryTree, closing.id);
				secondaryTree = normalizeTree(removeLeaf(state.secondaryTree, closing.id));
				if (!secondaryTree) mode = "stacked";
				if (focusedTabId === closing.id) {
					const nextLeaves = collectLeafTabIds(secondaryTree);
					focusedTabId = (sibling && nextLeaves.indexOf(sibling) !== -1 ? sibling : nextLeaves[0] || null) || state.mainTabId;
				}
			}
			if (mode === "stacked") focusedTabId = state.mainTabId;
			return {
				ok: true,
				needsHomeTab: false,
				successorId: null,
				promotedLeaf: false,
				state: patchState(state, {
					tabs: withoutTab(state.tabs, closing.id),
					secondaryTree,
					mode,
					focusedTabId
				})
			};
		}
		const treeLeaves = collectLeafTabIds(state.secondaryTree);
		const secId = treeLeaves.length ? treeLeaves[0] : null;
		const promotingLeaf = !!secId;
		let successor = secId ? findTab(state, secId) : null;
		if (!successor) successor = state.tabs[idx + 1] || state.tabs[idx - 1] || null;
		if (successor && successor.id === closing.id) successor = null;
		if (!successor) {
			if (!homeTab) return {
				ok: false,
				needsHomeTab: true,
				state,
				successorId: null,
				promotedLeaf: false
			};
			return {
				ok: true,
				needsHomeTab: false,
				successorId: homeTab.id,
				promotedLeaf: false,
				state: patchState(state, {
					tabs: withoutTab(state.tabs, closing.id).concat([homeTab]),
					secondaryTree: null,
					mode: "stacked",
					mainTabId: homeTab.id,
					focusedTabId: homeTab.id
				})
			};
		}
		let secondaryTree = state.secondaryTree;
		let mode = state.mode;
		if (promotingLeaf) {
			secondaryTree = normalizeTree(removeLeaf(state.secondaryTree, successor.id));
			if (!secondaryTree) mode = "stacked";
		} else {
			secondaryTree = null;
			mode = "stacked";
		}
		return {
			ok: true,
			needsHomeTab: false,
			successorId: successor.id,
			promotedLeaf: promotingLeaf,
			state: patchState(state, {
				tabs: withoutTab(state.tabs, closing.id),
				secondaryTree,
				mode,
				mainTabId: successor.id,
				focusedTabId: successor.id
			})
		};
	}
	function planActivate(state, tabId, presentation, options) {
		const tab = findTab(state, tabId);
		if (!tab) return {
			ok: false,
			state
		};
		if (tab.id === state.mainTabId && !options.fromPopstate) {
			const focus = planFocus(state, tab.id, presentation);
			return {
				ok: true,
				kind: "focus-main",
				changed: focus.changed,
				state: focus.ok ? focus.state : state
			};
		}
		if (visualTiled(state, presentation) && containsTab(state.secondaryTree, tab.id) && !options.fromPopstate) {
			const focus = planFocus(state, tab.id, presentation);
			if (!focus.ok) return {
				ok: false,
				state
			};
			return {
				ok: true,
				kind: "focus-secondary",
				changed: focus.changed,
				state: focus.state
			};
		}
		const previousMainId = state.mainTabId;
		if (containsTab(state.secondaryTree, tab.id)) {
			const made = planMakeMain(state, tab.id, options.oldMainSupportsTile);
			if (!made.ok) return {
				ok: false,
				state
			};
			return {
				ok: true,
				kind: "promote",
				state: made.state,
				previousMainId,
				coldParkTabId: made.coldParkTabId
			};
		}
		return {
			ok: true,
			kind: "set-main",
			previousMainId,
			state: patchState(state, {
				mainTabId: tab.id,
				focusedTabId: tab.id
			})
		};
	}
	/** Next logical tab after a route change. Does not write history or the URL. */
	function planTabHistory(tab, hash, replace, presentation) {
		const title = presentation.title;
		const icon = presentation.icon;
		if (replace) {
			const history = tab.history.slice();
			history[tab.historyIndex] = hash;
			return {
				...tab,
				route: hash,
				title,
				icon,
				history,
				historyIndex: tab.historyIndex
			};
		}
		if (tab.route === hash) return {
			...tab,
			route: hash,
			title,
			icon
		};
		const history = tab.history.slice(0, tab.historyIndex + 1);
		if (history[history.length - 1] === hash) return {
			...tab,
			route: hash,
			title,
			icon,
			history,
			historyIndex: history.length - 1
		};
		history.push(hash);
		return {
			...tab,
			route: hash,
			title,
			icon,
			history,
			historyIndex: history.length - 1
		};
	}
	function planSplitLeaf(state, targetTabId, newTabId, axis, placement, nextSplitId) {
		if (!containsTab(state.secondaryTree, targetTabId)) return {
			ok: false,
			state
		};
		if (secondaryLeafCapReached(state.secondaryTree)) return {
			ok: false,
			state
		};
		if (!findTab(state, newTabId) || newTabId === state.mainTabId) return {
			ok: false,
			state
		};
		if (containsTab(state.secondaryTree, newTabId)) return {
			ok: false,
			state
		};
		const secondaryTree = splitLeaf(state.secondaryTree, targetTabId, {
			axis,
			newTabId,
			placement
		}, nextSplitId);
		if (secondaryTree === state.secondaryTree) return {
			ok: false,
			state
		};
		return {
			ok: true,
			state: patchState(state, {
				secondaryTree,
				mode: "tiled"
			})
		};
	}
	/**
	* Defensive repair used by paint. Drops Secondary leaves that are missing or
	* are Main, then forces stacked focus back to Main. Throws on a tree that is
	* still structurally invalid after that drop — same failure as the legacy
	* coordinator.
	*/
	function repairWorkspaceState(state) {
		let mainTabId = state.mainTabId;
		if (!mainTabId && state.tabs.length) mainTabId = state.tabs[0].id;
		let secondaryTree = state.secondaryTree;
		if (secondaryTree) {
			const leafIds = collectLeafTabIds(secondaryTree);
			for (let i = 0; i < leafIds.length; i++) {
				const id = leafIds[i];
				if (!findTab(state, id) || id === mainTabId) secondaryTree = normalizeTree(removeLeaf(secondaryTree, id));
			}
			if (secondaryTree) {
				const check = validateTree(secondaryTree);
				if (!check.ok) throw new Error("workspace secondaryTree invariant violation: " + check.errors.join("; "));
			}
		}
		let mode = state.mode;
		if (mode === "tiled" && !secondaryTree) mode = "stacked";
		let focusedTabId = state.focusedTabId;
		if (mode === "stacked") focusedTabId = mainTabId;
		else if (focusedTabId !== mainTabId && !containsTab(secondaryTree, focusedTabId)) focusedTabId = mainTabId;
		if (mainTabId === state.mainTabId && focusedTabId === state.focusedTabId && secondaryTree === state.secondaryTree && mode === state.mode) return state;
		return patchState(state, {
			mainTabId,
			focusedTabId,
			secondaryTree,
			mode
		});
	}
	//#endregion
	//#region src/workspace/commands.ts
	/**
	* Leave preflights for workspace-tabs.js, plus an internal effect sketch.
	*
	* The shipped structural contract is the pure `plan*` functions. This module
	* does not perform effects: no leave prompts, no history writes, no TabContext
	* calls. `applyWorkspaceCommand` is not exported and is not the effect
	* boundary — activate, show-split, and split-leaf still mount, resume, and
	* render in workspace-tabs.js. Do not treat its effect list as that boundary
	* until those effects are complete.
	*
	* Drag hover and preview are not commands. #234 may later emit `reorder-tab`
	* or `move-pane` on drop only.
	*/
	function preflightHideLeaf(state, tabId, context) {
		if (!containsTab(state.secondaryTree, tabId)) return { type: "none" };
		if (visualTiled(state, context) && context.mounted) return {
			type: "leave-tab",
			tabId,
			nextHash: context.homeHash
		};
		return { type: "none" };
	}
	function preflightMakeMain(state, tabId, context) {
		const oldMain = findTab(state, state.mainTabId);
		if (!oldMain || oldMain.id === tabId) return { type: "none" };
		const supports = !!context.oldMainSupportsTile;
		return {
			type: "main-promotion",
			oldMainId: oldMain.id,
			requiresLeave: !supports,
			nextHash: oldMain.route
		};
	}
	//#endregion
	//#region src/workspace/invariants.ts
	function push(errors, message) {
		errors.push(message);
	}
	/**
	* Structural invariants of one canonical workspace. Does not inspect DOM,
	* TabContexts, or responsive presentation. A stacked workspace may still hold
	* a Secondary tree (Hide split). Tiled mode requires that tree.
	*/
	function validateWorkspace(state) {
		const errors = [];
		if (!state || typeof state !== "object") return {
			ok: false,
			errors: ["workspace state missing"]
		};
		if (state.mode !== "stacked" && state.mode !== "tiled") push(errors, "invalid mode " + String(state.mode));
		if (typeof state.mainSplitRatio !== "number" || !Number.isFinite(state.mainSplitRatio) || state.mainSplitRatio < 0 || state.mainSplitRatio > 1) push(errors, "invalid mainSplitRatio " + String(state.mainSplitRatio));
		const seenTabs = Object.create(null);
		for (let i = 0; i < state.tabs.length; i++) {
			const tab = state.tabs[i];
			if (!tab || !tab.id) {
				push(errors, "tab missing id at " + i);
				continue;
			}
			if (seenTabs[tab.id]) push(errors, "duplicate tab id " + tab.id);
			seenTabs[tab.id] = true;
		}
		if (state.mainTabId != null && !findTab(state, state.mainTabId)) push(errors, "mainTabId missing " + state.mainTabId);
		if (state.focusedTabId != null && !findTab(state, state.focusedTabId)) push(errors, "focusedTabId missing " + state.focusedTabId);
		if (state.mainTabId != null && state.focusedTabId == null) push(errors, "focusedTabId missing while main is set");
		if (state.mode === "stacked" && state.mainTabId != null && state.focusedTabId !== state.mainTabId) push(errors, "stacked focus is not Main");
		if (state.mode === "tiled" && !state.secondaryTree) push(errors, "tiled mode without secondaryTree");
		if (!state.secondaryTree && state.mode !== "stacked") push(errors, "missing secondaryTree requires stacked mode");
		if (state.secondaryTree) {
			const treeCheck = validateTree(state.secondaryTree);
			for (let i = 0; i < treeCheck.errors.length; i++) push(errors, treeCheck.errors[i]);
			const leaves = collectLeafTabIds(state.secondaryTree);
			if (leaves.length > 3) push(errors, "pane cap exceeded " + leaves.length);
			for (let i = 0; i < leaves.length; i++) {
				const id = leaves[i];
				if (!findTab(state, id)) push(errors, "secondary leaf missing tab " + id);
				if (id === state.mainTabId) push(errors, "Main is also a Secondary leaf " + id);
			}
		}
		if (state.mode === "tiled" && state.focusedTabId != null && state.focusedTabId !== state.mainTabId && !containsTab(state.secondaryTree, state.focusedTabId)) push(errors, "focus is neither Main nor a Secondary leaf");
		return {
			ok: errors.length === 0,
			errors
		};
	}
	//#endregion
	//#region src/workspace/snapshot.ts
	function copyTree(node) {
		if (!node) return null;
		if (node.type === "leaf") return node.tabId ? {
			type: "leaf",
			tabId: asTabId(String(node.tabId))
		} : null;
		if (node.type === "split") {
			const first = copyTree(node.first);
			const second = copyTree(node.second);
			if (!first || !second) return null;
			return {
				type: "split",
				id: node.id,
				axis: node.axis,
				ratio: node.ratio,
				first,
				second
			};
		}
		return null;
	}
	function copyTab(tab) {
		return {
			id: tab.id,
			route: tab.route,
			title: tab.title,
			icon: tab.icon,
			history: tab.history.slice(),
			historyIndex: tab.historyIndex
		};
	}
	/**
	* External `prksWorkspaceSnapshot()` body, without ephemeral `titleRouteGen`.
	* The coordinator attaches that field from the live tab when it publishes the
	* snapshot. The copy does not alias live history arrays or tree nodes.
	*/
	function workspaceSnapshot(state) {
		return {
			version: state.version,
			mode: state.mode,
			mainTabId: state.mainTabId,
			focusedTabId: state.focusedTabId,
			secondaryTree: copyTree(state.secondaryTree),
			tabs: state.tabs.map(copyTab),
			mainSplitRatio: state.mainSplitRatio
		};
	}
	//#endregion
	exports.DEFAULT_MAIN_SPLIT_RATIO = DEFAULT_MAIN_SPLIT_RATIO;
	exports.MAX_VISIBLE_PANES = MAX_VISIBLE_PANES;
	exports.WORKSPACE_STATE_VERSION = WORKSPACE_STATE_VERSION;
	exports.clampMainSplitRatio = clampMainSplitRatio;
	exports.collectLeafTabIds = collectLeafTabIds;
	exports.collectSplitIds = collectSplitIds;
	exports.containsTab = containsTab;
	exports.findLeafByTabId = findLeafByTabId;
	exports.findNodeById = findNodeById;
	exports.findSiblingLeafTabId = findSiblingLeafTabId;
	exports.isLeaf = isLeaf;
	exports.isSplit = isSplit;
	exports.leafCount = leafCount;
	exports.makeLeaf = makeLeaf;
	exports.makeSplit = makeSplit;
	exports.moveLeafRelativeToTarget = moveLeafRelativeToTarget;
	exports.normalizeTree = normalizeTree;
	exports.planActivate = planActivate;
	exports.planCloseTab = planCloseTab;
	exports.planFocus = planFocus;
	exports.planHideLeaf = planHideLeaf;
	exports.planMainSplitRatio = planMainSplitRatio;
	exports.planMakeMain = planMakeMain;
	exports.planMovePane = planMovePane;
	exports.planNestedSplitRatio = planNestedSplitRatio;
	exports.planReorder = planReorder;
	exports.planSetMode = planSetMode;
	exports.planSplitLeaf = planSplitLeaf;
	exports.planTabHistory = planTabHistory;
	exports.preflightHideLeaf = preflightHideLeaf;
	exports.preflightMakeMain = preflightMakeMain;
	exports.removeLeaf = removeLeaf;
	exports.repairWorkspaceState = repairWorkspaceState;
	exports.replaceLeaf = replaceLeaf;
	exports.replaceTabId = replaceTabId;
	exports.secondaryLeafCapReached = secondaryLeafCapReached;
	exports.setSplitRatio = setSplitRatio;
	exports.splitLeaf = splitLeaf;
	exports.validateTree = validateTree;
	exports.validateWorkspace = validateWorkspace;
	exports.workspaceSnapshot = workspaceSnapshot;
	return exports;
})({});
if (typeof module === "object" && module != null && module.exports) module.exports = prksWorkspaceModel;
