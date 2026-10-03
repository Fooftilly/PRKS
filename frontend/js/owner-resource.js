var prksOwnerResource = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	//#region frontend-app/src/lifecycle/owner-resource.ts
	function call(fn, value) {
		if (typeof fn !== "function") return;
		try {
			fn(value);
		} catch {}
	}
	function createOwnerResourceRegistry(host) {
		const slots = /* @__PURE__ */ new Map();
		function ticketCurrent(ticket) {
			if (!ticket || typeof ticket !== "object") return false;
			if (!host.alive()) return false;
			if (ticket.ownerToken !== host.ownerToken) return false;
			if (ticket.ownerId !== host.ownerId) return false;
			if (typeof ticket.generation !== "number") return false;
			return ticket.generation === host.generation();
		}
		function drop(slot) {
			if (slot.disposing) return;
			slot.disposing = true;
			slots.delete(slot.kind);
			call(slot.dispose, slot.value);
		}
		function register(ticket, registration) {
			if (!ticketCurrent(ticket) || !registration) return "rejected";
			if (registration.kind !== "researchGraph" && registration.kind !== "pdf") return "rejected";
			const previous = slots.get(registration.kind);
			let result = "attached";
			if (previous) {
				drop(previous);
				if (!ticketCurrent(ticket)) return "rejected";
				result = "replaced";
			}
			slots.set(registration.kind, {
				kind: registration.kind,
				value: registration.value,
				suspendable: registration.suspendable === true,
				phase: "live",
				dispose: registration.dispose,
				suspend: registration.suspend,
				resume: registration.resume,
				disposing: false
			});
			return result;
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
			for (const slot of slots.values()) {
				if (slot.phase !== "suspended") continue;
				slot.phase = "live";
				call(slot.resume, slot.value);
			}
		}
		function releaseAll() {
			for (const slot of Array.from(slots.values())) drop(slot);
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
			generation: typeof generation === "number" ? generation : host.generation()
		};
	}
	//#endregion
	exports.createOwnerResourceRegistry = createOwnerResourceRegistry;
	exports.resourceTicket = resourceTicket;
	return exports;
})({});
if (typeof module === "object" && module != null && module.exports) module.exports = prksOwnerResource;
