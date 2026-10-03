var prksTabLeave = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	//#region src/lifecycle/tab-leave.ts
	var BLOCKING = /* @__PURE__ */ new Set([
		"rejected-unsaved-edit",
		"rejected-pending-pdf-sync",
		"stale-owner",
		"cancelled"
	]);
	function blockerFrom(result) {
		if (result == null || result === true || result === "approved") return null;
		if (result === false) return { status: "rejected-unsaved-edit" };
		if (typeof result === "string" && BLOCKING.has(result)) return { status: result };
		if (typeof result === "object" && BLOCKING.has(result.status)) return {
			status: result.status,
			feature: result.feature
		};
		return { status: "cancelled" };
	}
	function decisionFor(attempt, status, extra) {
		return {
			status,
			ownerId: String(attempt.ownerId),
			destination: attempt.destination,
			transition: attempt.transition,
			feature: extra && extra.feature,
			value: extra && extra.value
		};
	}
	function createTabLeave() {
		const probes = [];
		let flushHook = null;
		const slots = /* @__PURE__ */ new Map();
		function registerProbe(probe) {
			const index = probes.findIndex((item) => item.id === probe.id);
			if (index >= 0) probes[index] = probe;
			else probes.push(probe);
		}
		function registerFlush(flush) {
			flushHook = flush;
		}
		function flushOwner(ctx) {
			if (flushHook) {
				flushHook(ctx);
				return;
			}
			const root = globalThis;
			if (typeof root.prksFlushPendingWorkResearchNotes === "function") root.prksFlushPendingWorkResearchNotes(ctx);
			if (typeof root.prksFlushPendingPrivateNotes === "function") root.prksFlushPendingPrivateNotes(ctx);
		}
		async function assessOwner(ctx, destination) {
			const ordered = probes.slice().sort((a, b) => a.order - b.order || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
			for (const probe of ordered) {
				const result = await probe.assess(ctx, destination);
				const blocker = blockerFrom(result);
				if (!blocker) continue;
				if (result && typeof result === "object" && typeof result.confirm === "function") {
					if (!await result.confirm()) return {
						status: blocker.status,
						feature: blocker.feature || probe.id
					};
					continue;
				}
				return {
					status: blocker.status,
					feature: blocker.feature || probe.id
				};
			}
			return null;
		}
		function enqueue(ownerIds, job) {
			const ids = [...new Set(ownerIds.map((id) => String(id)))].sort();
			const keys = ids.length ? ids : [""];
			const previous = keys.map((id) => slots.get(id)).filter((slot) => !!slot);
			const idle = previous.every((slot) => slot.done);
			let release;
			const slot = {
				done: false,
				promise: new Promise((resolve) => {
					release = () => {
						slot.done = true;
						resolve();
					};
				})
			};
			for (const id of keys) slots.set(id, slot);
			const finish = (result) => {
				result.then(() => {
					release();
				}, () => {
					release();
				});
				return result;
			};
			if (idle) try {
				return finish(Promise.resolve(job()));
			} catch (error) {
				release();
				return Promise.reject(error);
			}
			return finish(Promise.all(previous.map((item) => item.promise)).then(() => job()));
		}
		async function execute(attempt, snapshot) {
			if (!snapshot || !attempt.still(snapshot)) return decisionFor(attempt, "stale-owner");
			let assessed = null;
			try {
				assessed = attempt.assess ? await attempt.assess(snapshot) : null;
			} catch {
				return decisionFor(attempt, "cancelled");
			}
			if (!attempt.still(snapshot)) return decisionFor(attempt, "stale-owner");
			const blocker = blockerFrom(assessed);
			if (blocker) return decisionFor(attempt, blocker.status, { feature: blocker.feature });
			return finishApproved(attempt, snapshot);
		}
		async function finishApproved(attempt, snapshot) {
			if (attempt.flushNotes) attempt.flushNotes(snapshot);
			if (!attempt.still(snapshot)) return decisionFor(attempt, "stale-owner");
			return decisionFor(attempt, "approved", { value: attempt.commit ? await attempt.commit() : void 0 });
		}
		function run(attempt) {
			const snapshot = attempt.capture();
			return enqueue([attempt.ownerId], () => execute(attempt, snapshot));
		}
		function runBatch(batch) {
			const attempts = batch.attempts || [];
			const captured = attempts.map((attempt) => ({
				attempt,
				snapshot: attempt.capture()
			}));
			const ids = attempts.map((attempt) => attempt.ownerId);
			const label = {
				ownerId: ids.join(","),
				destination: null,
				transition: batch.transition
			};
			return enqueue(ids, async () => {
				const approved = [];
				for (const row of captured) {
					if (!row.snapshot || !row.attempt.still(row.snapshot)) return decisionFor(label, "stale-owner");
					let assessed = null;
					try {
						assessed = row.attempt.assess ? await row.attempt.assess(row.snapshot) : null;
					} catch {
						return decisionFor(label, "cancelled");
					}
					if (!row.attempt.still(row.snapshot)) return decisionFor(label, "stale-owner");
					const blocker = blockerFrom(assessed);
					if (blocker) return decisionFor(label, blocker.status, { feature: blocker.feature });
					approved.push({
						attempt: row.attempt,
						snapshot: row.snapshot
					});
				}
				for (const row of approved) if (row.attempt.flushNotes) row.attempt.flushNotes(row.snapshot);
				for (const row of approved) if (!row.attempt.still(row.snapshot)) return decisionFor(label, "stale-owner");
				const value = await batch.commit();
				return decisionFor(label, "approved", { value });
			});
		}
		return {
			run,
			runBatch,
			registerProbe,
			registerFlush,
			assessOwner,
			flushOwner
		};
	}
	//#endregion
	//#region src/lifecycle/browser-entry.ts
	/**
	* Classic-script entry. The maintainer build emits `frontend/js/tab-leave.js`
	* as the global `prksTabLeave`. Vue does not import this module.
	*
	* Feature probes register on that global. The workspace coordinator and
	* `prksRenderTabRoute` are the only callers of `run` / `runBatch`.
	*/
	var leave = createTabLeave();
	var run = leave.run;
	var runBatch = leave.runBatch;
	var registerProbe = leave.registerProbe;
	var registerFlush = leave.registerFlush;
	var assessOwner = leave.assessOwner;
	var flushOwner = leave.flushOwner;
	//#endregion
	exports.assessOwner = assessOwner;
	exports.flushOwner = flushOwner;
	exports.registerFlush = registerFlush;
	exports.registerProbe = registerProbe;
	exports.run = run;
	exports.runBatch = runBatch;
	return exports;
})({});
if (typeof module === "object" && module != null && module.exports) module.exports = prksTabLeave;
