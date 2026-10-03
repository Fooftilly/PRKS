var prksOwnerResource = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	//#region src/lifecycle/owner-resource.ts
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
			if (!ticketCurrent(ticket) || !registration) return "rejected";
			if (registration.kind !== "researchGraph" && registration.kind !== "pdf") return "rejected";
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
			generation: typeof generation === "number" ? generation : host.generation()
		};
	}
	//#endregion
	exports.createOwnerResourceRegistry = createOwnerResourceRegistry;
	exports.resourceTicket = resourceTicket;
	return exports;
})({});
if (typeof module === "object" && module != null && module.exports) module.exports = prksOwnerResource;
