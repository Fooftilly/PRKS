var prksWorkRoute = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	//#region src/features/work/projection.ts
	var RESOURCE = "workRouteProjection";
	var PLACEMENT_FIELDS = [
		"folder_id",
		"folder_title",
		"playlist_id",
		"playlist_title"
	];
	function cloneWork(work) {
		if (!work) return null;
		if (typeof structuredClone === "function") return structuredClone(work);
		return JSON.parse(JSON.stringify(work));
	}
	/** Folder and playlist fields move together onto a distinct effective record. */
	function withPlacement(base, placed) {
		const next = { ...base };
		for (const field of PLACEMENT_FIELDS) if (Object.prototype.hasOwnProperty.call(placed, field)) next[field] = placed[field];
		return next;
	}
	function freezeProjection(projection) {
		return Object.freeze(projection);
	}
	/**
	* Shape a route result the coordinator has already resolved.
	* Pending deletion never publishes the cached row.
	* A ready result without a Work is not-found, not a fake Work.
	*/
	function projectWorkRoute(input) {
		const workId = String(input.workId);
		let availability = input.availability;
		const lifecycle = input.lifecycle;
		let provenance = input.provenance;
		let work = input.work;
		let effectiveWork = input.effectiveWork ?? null;
		if (lifecycle === "pending-delete") {
			availability = "unavailable";
			work = null;
			effectiveWork = null;
		}
		if (lifecycle === "unsent-create") provenance = "local-unsent";
		if (work && String(work.id ?? "") !== workId) {
			work = null;
			effectiveWork = null;
			if (availability === "ready") availability = "not-found";
		}
		if (effectiveWork && String(effectiveWork.id ?? "") !== workId) effectiveWork = null;
		if (availability !== "ready") {
			work = null;
			effectiveWork = null;
		} else if (!work) {
			availability = "not-found";
			effectiveWork = null;
		}
		const storedWork = availability === "ready" ? cloneWork(work) : null;
		const storedEffective = storedWork ? effectiveWork && effectiveWork !== work ? cloneWork(effectiveWork) : storedWork : null;
		return freezeProjection({
			workId,
			availability,
			lifecycle,
			provenance,
			ownerTabId: String(input.owner.tabId),
			ownerGeneration: input.owner.generation,
			work: storedWork,
			effectiveWork: storedEffective,
			recordOpen: input.recordOpen === true && availability === "ready"
		});
	}
	function sameOwner(ctx, projection, generation) {
		return !ctx.destroyed && projection.ownerTabId === String(ctx.tabId) && projection.ownerGeneration === generation && ctx.isCurrent(generation);
	}
	/**
	* Publish onto the originating TabContext. A stale generation publishes nothing.
	* The projection is stored as given. Rebuilding it would copy the Work again.
	* Editors receive `work`, never the metadata or role overlay.
	*/
	function publishWorkRouteProjection(ctx, generation, projection) {
		if (!sameOwner(ctx, projection, generation)) return null;
		ctx.setResource(RESOURCE, projection);
		if (projection.availability === "ready" && projection.work) ctx.setEntity("work", projection.work);
		else ctx.setEntity("work", null);
		return projection;
	}
	/**
	* A later folder/playlist placement may replace the Work on the same owner.
	* It does not record another open and does not touch any other pane.
	*/
	function replaceWorkRoutePlacement(ctx, generation, work) {
		if (!ctx || ctx.destroyed || !ctx.isCurrent(generation)) return null;
		const current = ctx.getResource(RESOURCE);
		if (!current || !sameOwner(ctx, current, generation)) return null;
		if (!work || String(work.id ?? "") !== current.workId) return null;
		if (current.availability !== "ready") return null;
		const effectiveSource = current.effectiveWork && current.effectiveWork !== current.work ? withPlacement(current.effectiveWork, work) : work;
		return publishWorkRouteProjection(ctx, generation, projectWorkRoute({
			workId: current.workId,
			owner: {
				tabId: current.ownerTabId,
				generation: current.ownerGeneration
			},
			availability: "ready",
			lifecycle: current.lifecycle,
			provenance: current.provenance,
			work,
			effectiveWork: effectiveSource,
			recordOpen: false
		}));
	}
	/** Genuine foreground open only. internalRefresh must not record another open. */
	function workOpenShouldRecord(internalRefresh, workValue) {
		return !internalRefresh && !!workValue;
	}
	//#endregion
	//#region src/features/work/browser-entry.ts
	/**
	* Classic-script entry. The maintainer build emits frontend/js/work-route-projection.js.
	* The Vue application does not import this module. No Vue mount, Pinia, or Vue Router.
	*/
	var root = globalThis;
	root.prksProjectWorkRoute = projectWorkRoute;
	root.prksPublishWorkRouteProjection = publishWorkRouteProjection;
	root.prksReplaceWorkRoutePlacement = replaceWorkRoutePlacement;
	root.prksWorkOpenShouldRecord = workOpenShouldRecord;
	//#endregion
	exports.projectWorkRoute = projectWorkRoute;
	exports.publishWorkRouteProjection = publishWorkRouteProjection;
	exports.replaceWorkRoutePlacement = replaceWorkRoutePlacement;
	exports.workOpenShouldRecord = workOpenShouldRecord;
	return exports;
})({});
if (typeof module === "object" && module != null && module.exports) module.exports = prksWorkRoute;
