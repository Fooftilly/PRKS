var prksSyncReconcile = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	// src/sync/reconcile.ts
	function isRevision(value) {
		return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
	}
	/** Negative when `a` is older than `b`, zero when equal, positive when newer. */
	function compareRevisions(a, b) {
		if (!isRevision(a) || !isRevision(b)) throw new TypeError("revisions are non-negative safe integers");
		return a === b ? 0 : a < b ? -1 : 1;
	}
	/** One key per cached entity; the same encoding `offline-runtime.js` uses. */
	function entityKey(kind, id) {
		return String(kind) + "\0" + String(id);
	}
	/** Epoch ms from a number or an ISO timestamp; null when it is neither. */
	function timeOf(value) {
		if (typeof value === "number") return Number.isFinite(value) ? value : null;
		if (typeof value !== "string" || value === "") return null;
		const parsed = Date.parse(value);
		return Number.isFinite(parsed) ? parsed : null;
	}
	/** True when a value read at `observedAt` provably predates an operation created at `createdAt`. */
	function readBefore(observedAt, createdAt) {
		const read = timeOf(observedAt);
		const created = timeOf(createdAt);
		return read !== null && created !== null && read < created;
	}
	function decideAck(evidence) {
		if (!isRevision(evidence.ackRevision)) throw new TypeError("an ACK carries a revision");
		if (evidence.heldRevision !== void 0 && evidence.heldRevision !== null) {
			if (!isRevision(evidence.heldRevision)) return "unproven";
			return compareRevisions(evidence.ackRevision, evidence.heldRevision) < 0 ? "superseded" : "apply";
		}
		if (evidence.agrees === true) return "apply";
		return readBefore(evidence.observedAt, evidence.operationCreatedAt) ? "apply" : "unproven";
	}
	function planAck(evidence) {
		if (evidence.heldRevision !== void 0 && evidence.heldRevision !== null && decideAck({
			ackRevision: evidence.ackRevision,
			heldRevision: evidence.heldRevision
		}) === "superseded") return "superseded";
		if (evidence.agrees === true) return "unchanged";
		return decideAck({
			ackRevision: evidence.ackRevision,
			observedAt: evidence.observedAt,
			operationCreatedAt: evidence.operationCreatedAt
		});
	}
	/**
	* Re-read a live owner's value after an `unproven` ACK and adopt the server's
	* answer, but only while the owner still holds `expected`. Anything that
	* replaced it meanwhile brought its own ordering decision, and a read that
	* started before that change must not undo it. A cached or unavailable answer
	* is not authoritative and is ignored. Resolves true when adopted.
	*/
	async function adoptFresh(read, current, expected, adopt) {
		let fresh;
		try {
			fresh = await read();
		} catch {
			return false;
		}
		if (!fresh || fresh.source !== "server" || fresh.value === null || fresh.value === void 0) return false;
		if (current() !== expected) return false;
		adopt(fresh.value);
		return true;
	}
	function unsettled(op) {
		return op.status !== "acknowledged";
	}
	/**
	* The operations still to be applied over an acknowledged value, in queue
	* order. `exceptOpId` leaves out the operation whose ACK is being merged.
	*/
	function pendingFor(operations, match, exceptOpId) {
		if (!Array.isArray(operations)) return [];
		return operations.filter((op) => !!op && unsettled(op) && op.op_id !== exceptOpId && match(op)).map((op, index) => ({
			op,
			index
		})).sort((a, b) => {
			const left = a.op.sequence;
			const right = b.op.sequence;
			if (Number.isSafeInteger(left) && Number.isSafeInteger(right) && left !== right) return left - right;
			return a.index - b.index;
		}).map((row) => row.op);
	}
	/** The acknowledged base with every matching pending operation folded over it, oldest first. */
	function overlay(base, operations, match, apply) {
		return pendingFor(operations, match).reduce((value, op) => apply(value, op), base);
	}
	var observations = /* @__PURE__ */ new WeakMap();
	/** Record that `value` came from a read that finished no later than `at`. */
	function observe(value, at) {
		const time = timeOf(at);
		if (value !== null && typeof value === "object" && time !== null) observations.set(value, time);
		return value;
	}
	/** When `value` was read, or null when this page cannot prove it. */
	function observedAt(value) {
		if (value === null || typeof value !== "object") return null;
		return observations.get(value) ?? null;
	}
	/**
	* Keep the read time of `from` on `to`. Use it when `to` is `from` with an
	* ACK or local edit applied: the rest of `to` is still only as new as `from`.
	*/
	function carryObservation(from, to) {
		const time = observedAt(from);
		if (time !== null && to !== null && typeof to === "object") observations.set(to, time);
		return to;
	}
	/**
	* Merge one ACK into a live owner's value under the shared rule. The value's
	* read time comes from `observe` (stamped by the offline read path) and is
	* carried onto the patched copy. `evidence.heldRevision` is an optional
	* sibling revision (an editor's Tag-options base). Returns the plan used.
	*/
	function applyOwnerAck(evidence, owner) {
		const value = owner.get();
		if (!value) return "unchanged";
		const plan = planAck({
			ackRevision: evidence.ackRevision,
			heldRevision: evidence.heldRevision,
			operationCreatedAt: evidence.operationCreatedAt,
			agrees: owner.agrees(value),
			observedAt: observedAt(value)
		});
		if (plan !== "apply" && plan !== "unproven") return plan;
		const next = carryObservation(value, owner.patch(value));
		owner.set(next);
		if (plan === "unproven") adoptFresh(owner.read, owner.get, next, (fresh) => {
			const value = owner.merge ? carryObservation(fresh, owner.merge(next, fresh)) : fresh;
			owner.set(value);
			if (owner.adopted) owner.adopted(value);
		}).catch(() => false);
		return plan;
	}
	function isFieldBase(value) {
		if (!value || typeof value !== "object") return false;
		const entry = value;
		return typeof entry.value === "string" && isRevision(entry.revision);
	}
	/**
	* Pin each field's base when an editor opens. A field keeps the revision that
	* was read only where that read holds the value the form shows: a read that
	* already holds another value is newer than the form, so the revision the form
	* was shown at is unknown and that field is null. Null when nothing was read.
	*
	* `pending` holds this device's own unsent edit per field (its value and the
	* revision it was measured against). The form shows that value, and the field
	* keeps that revision, so editing it again replaces the unsent edit rather
	* than measuring it against a base the form never showed.
	*/
	function pinEditBase(shown, read, pending) {
		if (!read || typeof read !== "object") return null;
		const pinned = {};
		for (const field of Object.keys(shown)) {
			const entry = read[field];
			const own = pending ? pending[field] : void 0;
			const value = shown[field] == null ? "" : String(shown[field]);
			if (!isFieldBase(entry)) pinned[field] = null;
			else if (own !== void 0) {
				if (!isFieldBase(own) || own.value !== value) pinned[field] = null;
				else if (own.value === entry.value) pinned[field] = {
					value: entry.value,
					revision: Math.max(entry.revision, own.revision)
				};
				else pinned[field] = entry.revision === own.revision ? {
					value: entry.value,
					revision: own.revision,
					pending: own.value,
					...typeof own.pendingOp === "string" ? { pendingOp: own.pendingOp } : {}
				} : null;
			} else pinned[field] = entry.value === value ? {
				value: entry.value,
				revision: entry.revision
			} : null;
		}
		return pinned;
	}
	/**
	* The entry an edit-base snapshot keeps when a read finishes: a read that
	* started before a newer acknowledgement must not roll the pair back.
	*/
	function newerEditBase(held, read) {
		if (!isFieldBase(held) || !isFieldBase(read)) return read;
		if (held.revision > read.revision) return held;
		if (held.revision === read.revision && typeof held.ackOp === "string") return {
			...read,
			ackOp: held.ackOp
		};
		return read;
	}
	/**
	* An edit base, read as one value/revision pair, after this device's own
	* write of that field is acknowledged. `undefined` keeps the entry: it is
	* past the ACK. An unstamped entry at the ACK revision comes back as it is,
	* for the caller to stamp. The write's value at the ACK revision replaces an entry the
	* write was measured against. Any other entry is null: a revision between the
	* two holds a value nobody here has read.
	*/
	function acknowledgeEditBase(entry, write) {
		if (!isFieldBase(entry)) return null;
		if (entry.revision > write.ackRevision) return void 0;
		if (entry.revision === write.ackRevision) return typeof entry.ackOp === "string" ? void 0 : {
			value: entry.value,
			revision: entry.revision
		};
		return entry.revision === write.baseRevision ? {
			value: write.value,
			revision: write.ackRevision
		} : null;
	}
	/**
	* The fields this session changed, each measured against its pinned base. A
	* changed field whose base is unknown is listed in `unknown` and must not be
	* sent: the caller tells the user it changed elsewhere and keeps the draft.
	*/
	function pinnedEdits(shown, draft, pinned) {
		const changes = {};
		const base = {};
		const unknown = [];
		for (const field of Object.keys(shown)) {
			const entry = pinned[field];
			if (entry) base[field] = entry;
			if (!Object.prototype.hasOwnProperty.call(draft, field)) continue;
			const desired = draft[field] == null ? "" : String(draft[field]);
			if (desired === (shown[field] == null ? "" : String(shown[field]))) continue;
			changes[field] = desired;
			if (!entry) unknown.push(field);
		}
		return {
			changes,
			base,
			unknown
		};
	}
	exports.acknowledgeEditBase = acknowledgeEditBase;
	exports.adoptFresh = adoptFresh;
	exports.applyOwnerAck = applyOwnerAck;
	exports.carryObservation = carryObservation;
	exports.compareRevisions = compareRevisions;
	exports.decideAck = decideAck;
	exports.entityKey = entityKey;
	exports.isRevision = isRevision;
	exports.newerEditBase = newerEditBase;
	exports.observe = observe;
	exports.observedAt = observedAt;
	exports.overlay = overlay;
	exports.pendingFor = pendingFor;
	exports.pinEditBase = pinEditBase;
	exports.pinnedEdits = pinnedEdits;
	exports.planAck = planAck;
	exports.readBefore = readBefore;
	exports.timeOf = timeOf;
	return exports;
})({});
if (typeof module === "object" && module != null && module.exports) module.exports = prksSyncReconcile;
