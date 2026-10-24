var prksOwnerResource = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	//#region src/lifecycle/owner-resource.ts
	/**
	* Owner-scoped external resource lifetime.
	*
	* TabContext hosts one registry per owner. A later Vue-native owner can host
	* the same registry. This is not a second TabContext and not a leave decision.
	*
	* A slot is absent, live, or warm-suspended. The owner itself is live or
	* warm-suspended. Cold park and owner destruction dispose every slot and
	* clear that owner state. Warm park keeps only a registration that declares
	* `suspendable`. While the owner is suspended, a non-suspendable registration
	* is rejected and not attached; a suspendable registration attaches already
	* suspended and its suspend hook runs. The ticket stays current across warm
	* park. Cold release advances a resource epoch, so a ticket captured before
	* that release cannot attach again, including after the same owner remounts.
	* Route generation is not used for that. Research Graph is not suspendable.
	* The PDF runtime is suspendable. Production registers it from
	* initPdfViewerForWork with the ticket captured before deferred setup.
	* Work role, Work tag, Work source, Work metadata, Folder tag, and private
	* notes sessions are non-suspendable. Warm park releases them. Production
	* captures a ticket and registers that session before subscriptions or async
	* prepare. Research Notes (`workNotes`, the pane-local EasyMDE) is
	* suspendable: it lives in the parked pane DOM beside the PDF, so warm park
	* keeps it and cold release destroys it. Production captures that ticket when
	* the Work paint begins. This module does not construct those sessions or
	* decide their durable writes.
	*
	* VueUse is not used here. Cytoscape, the PDF viewer, and other owned browser
	* resources outlive a component mount, and their dispose stays on this registry.
	*/
	var EDITOR_SESSION_KINDS = [
		"workRoleEditor",
		"workTagEditor",
		"workSourceEditor",
		"workMetadataEditor",
		"folderTagEditor",
		"privateNotesEditor"
	];
	var OWNER_RESOURCE_KINDS = [
		"researchGraph",
		"pdf",
		"workNotes",
		...EDITOR_SESSION_KINDS
	];
	function isOwnerResourceKind(kind) {
		return OWNER_RESOURCE_KINDS.includes(kind);
	}
	function isEditorSessionKind(kind) {
		return EDITOR_SESSION_KINDS.includes(kind);
	}
	function call(fn, value) {
		if (typeof fn !== "function") return;
		try {
			fn(value);
		} catch {}
	}
	function createOwnerResourceRegistry(host) {
		const slots = /* @__PURE__ */ new Map();
		let ownerPhase = "live";
		let releasing = 0;
		function ticketCurrent(ticket) {
			if (!ticket || typeof ticket !== "object") return false;
			if (!host.alive()) return false;
			if (ticket.ownerToken !== host.ownerToken) return false;
			if (ticket.ownerId !== host.ownerId) return false;
			if (typeof ticket.generation !== "number") return false;
			if (typeof ticket.epoch !== "number") return false;
			if (ticket.epoch !== host.epoch()) return false;
			return ticket.generation === host.generation();
		}
		function drop(slot) {
			if (slot.disposing) return;
			slot.disposing = true;
			slots.delete(slot.kind);
			call(slot.dispose, slot.value);
		}
		function dropInstalled(kind) {
			const seen = /* @__PURE__ */ new Set();
			for (;;) {
				const slot = slots.get(kind);
				if (!slot || seen.has(slot)) return;
				seen.add(slot);
				drop(slot);
			}
		}
		function install(registration, result) {
			const phase = ownerPhase === "suspended" ? "suspended" : "live";
			const slot = {
				kind: registration.kind,
				value: registration.value,
				suspendable: registration.suspendable === true,
				phase,
				dispose: registration.dispose,
				suspend: registration.suspend,
				resume: registration.resume,
				disposing: false
			};
			slots.set(registration.kind, slot);
			if (phase === "suspended") call(slot.suspend, slot.value);
			return result;
		}
		function register(ticket, registration) {
			if (releasing > 0) return "rejected";
			if (!ticketCurrent(ticket) || !registration || typeof registration.kind !== "string") return "rejected";
			if (!isOwnerResourceKind(registration.kind)) return "rejected";
			if (isEditorSessionKind(registration.kind) && registration.suspendable === true) return "rejected";
			if (ownerPhase === "suspended" && registration.suspendable !== true) return "rejected";
			const previous = slots.get(registration.kind);
			let result = "attached";
			if (previous) {
				dropInstalled(registration.kind);
				if (!ticketCurrent(ticket)) return "rejected";
				result = "replaced";
			}
			return install(registration, result);
		}
		function get(kind) {
			const slot = slots.get(kind);
			return slot ? slot.value : void 0;
		}
		function dispose(kind) {
			const slot = slots.get(kind);
			if (slot) drop(slot);
		}
		function warmSuspend() {
			ownerPhase = "suspended";
			for (const slot of Array.from(slots.values())) {
				if (!slot.suspendable) {
					drop(slot);
					continue;
				}
				if (slot.phase === "suspended") continue;
				slot.phase = "suspended";
				call(slot.suspend, slot.value);
			}
		}
		function resume() {
			ownerPhase = "live";
			for (const slot of slots.values()) {
				if (slot.phase !== "suspended") continue;
				slot.phase = "live";
				call(slot.resume, slot.value);
			}
		}
		function releaseAll() {
			host.advanceEpoch();
			releasing += 1;
			try {
				for (const slot of Array.from(slots.values())) drop(slot);
			} finally {
				releasing -= 1;
				ownerPhase = "live";
			}
		}
		function kinds() {
			return Array.from(slots.keys()).sort();
		}
		return {
			register,
			get,
			accepts: ticketCurrent,
			dispose,
			warmSuspend,
			resume,
			releaseAll,
			kinds
		};
	}
	function resourceTicket(host, generation) {
		return {
			ownerId: host.ownerId,
			ownerToken: host.ownerToken,
			generation: typeof generation === "number" ? generation : host.generation(),
			epoch: host.epoch()
		};
	}
	//#endregion
	exports.createOwnerResourceRegistry = createOwnerResourceRegistry;
	exports.resourceTicket = resourceTicket;
	return exports;
})({});
if (typeof module === "object" && module != null && module.exports) module.exports = prksOwnerResource;
