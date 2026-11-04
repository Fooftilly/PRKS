var prksEditorRecovery = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	//#region src/lifecycle/editor-recovery/fingerprint.ts
	/**
	* Deterministic 128-bit text fingerprint and exact body equality.
	*
	* MurmurHash3 x86_128 (seed 0) over the UTF-16LE bytes of the string, rendered
	* as 32 hex characters (h1..h4). Pure JavaScript, so it works where
	* `crypto.subtle` is missing (LAN/HTTP). It is not a security primitive and it
	* never decides a delete on its own: it identifies a base that is not
	* retained, and it is a precheck before exact `===` comparison. Callers compute
	* it once per base, never per keystroke.
	*/
	var C1 = 597399067;
	var C2 = 2869860233;
	var C3 = 951274213;
	var C4 = 2716044179;
	function rotl(x, r) {
		return x << r | x >>> 32 - r;
	}
	/** Sum modulo 2^32, as the C reference's uint32 arithmetic (not a truncation). */
	function add32(...terms) {
		let sum = 0;
		for (const t of terms) sum = sum + t >>> 0;
		return sum;
	}
	function fmix(h) {
		h ^= h >>> 16;
		h = Math.imul(h, 2246822507);
		h ^= h >>> 13;
		h = Math.imul(h, 3266489909);
		h ^= h >>> 16;
		return h;
	}
	function hex(h) {
		return (h >>> 0).toString(16).padStart(8, "0");
	}
	function fingerprintText(text) {
		const units = text.length;
		const blocks = units >>> 3;
		let h1 = 0;
		let h2 = 0;
		let h3 = 0;
		let h4 = 0;
		for (let i = 0; i < blocks; i++) {
			const o = i << 3;
			let k1 = text.charCodeAt(o) | text.charCodeAt(o + 1) << 16;
			let k2 = text.charCodeAt(o + 2) | text.charCodeAt(o + 3) << 16;
			let k3 = text.charCodeAt(o + 4) | text.charCodeAt(o + 5) << 16;
			let k4 = text.charCodeAt(o + 6) | text.charCodeAt(o + 7) << 16;
			k1 = Math.imul(rotl(Math.imul(k1, C1), 15), C2);
			h1 ^= k1;
			h1 = rotl(h1, 19);
			h1 = add32(h1, h2);
			h1 = add32(Math.imul(h1, 5), 1444728091);
			k2 = Math.imul(rotl(Math.imul(k2, C2), 16), C3);
			h2 ^= k2;
			h2 = rotl(h2, 17);
			h2 = add32(h2, h3);
			h2 = add32(Math.imul(h2, 5), 197830471);
			k3 = Math.imul(rotl(Math.imul(k3, C3), 17), C4);
			h3 ^= k3;
			h3 = rotl(h3, 15);
			h3 = add32(h3, h4);
			h3 = add32(Math.imul(h3, 5), 2530024501);
			k4 = Math.imul(rotl(Math.imul(k4, C4), 18), C1);
			h4 ^= k4;
			h4 = rotl(h4, 13);
			h4 = add32(h4, h1);
			h4 = add32(Math.imul(h4, 5), 850148119);
		}
		const tailStart = blocks << 3;
		const tailBytes = (units - tailStart) * 2;
		const byteAt = (j) => {
			const unit = text.charCodeAt(tailStart + (j >>> 1));
			return j & 1 ? unit >>> 8 : unit & 255;
		};
		let k1 = 0;
		let k2 = 0;
		let k3 = 0;
		let k4 = 0;
		for (let j = tailBytes - 1; j >= 0; j--) {
			const b = byteAt(j) << (j & 3) * 8;
			if (j >= 12) k4 ^= b;
			else if (j >= 8) k3 ^= b;
			else if (j >= 4) k2 ^= b;
			else k1 ^= b;
		}
		if (tailBytes > 12) {
			k4 = Math.imul(rotl(Math.imul(k4, C4), 18), C1);
			h4 ^= k4;
		}
		if (tailBytes > 8) {
			k3 = Math.imul(rotl(Math.imul(k3, C3), 17), C4);
			h3 ^= k3;
		}
		if (tailBytes > 4) {
			k2 = Math.imul(rotl(Math.imul(k2, C2), 16), C3);
			h2 ^= k2;
		}
		if (tailBytes > 0) {
			k1 = Math.imul(rotl(Math.imul(k1, C1), 15), C2);
			h1 ^= k1;
		}
		const len = units * 2 >>> 0;
		h1 ^= len;
		h2 ^= len;
		h3 ^= len;
		h4 ^= len;
		h1 = add32(h1, h2, h3, h4);
		h2 = add32(h2, h1);
		h3 = add32(h3, h1);
		h4 = add32(h4, h1);
		h1 = fmix(h1);
		h2 = fmix(h2);
		h3 = fmix(h3);
		h4 = fmix(h4);
		h1 = add32(h1, h2, h3, h4);
		h2 = add32(h2, h1);
		h3 = add32(h3, h1);
		h4 = add32(h4, h1);
		return hex(h1) + hex(h2) + hex(h3) + hex(h4);
	}
	/**
	* Exact body equality: length precheck, optional fingerprint precheck, then
	* `===`. A colliding fingerprint can only make this slower, never true.
	*/
	function sameBody(a, b, options = {}) {
		if (a.length !== b.length) return false;
		if (options.fingerprint && options.fingerprint(a) !== options.fingerprint(b)) return false;
		return a === b;
	}
	/**
	* A base that is not retained is unchanged only when revision, length and
	* fingerprint all match. Revision is primary; the fingerprint catches a
	* revision that was reused or regressed (for example a restored backup).
	*/
	function sameBaseIdentity(a, b) {
		if (!a || !b) return false;
		if (a.revision === null || b.revision === null) return false;
		return a.revision === b.revision && a.length === b.length && a.fingerprint !== null && a.fingerprint === b.fingerprint;
	}
	//#endregion
	//#region src/lifecycle/editor-recovery/schema.ts
	/**
	* Browser-local editor draft recovery (#466): names, budgets and record shapes.
	*
	* Recovery drafts live in their own IndexedDB database, one per browser
	* scope (`RecoveryNames`). They are not server state, not the semantic
	* operation queue (`prks-local-v2:<scope>`) and not a cache;
	* Clear offline cache never touches them. Nothing here claims that a body was
	* saved: the queue and the server stay the only authorities for that.
	*/
	var RECOVERY_DB_VERSION = 1;
	var DRAFTS_STORE = "drafts";
	var BODIES_STORE = "bodies";
	/** Record schema version written by this build. Readers accept `v <= RECORD_VERSION`. */
	var RECORD_VERSION = 1;
	var EMERGENCY_VERSION = 1;
	/** Allowance for one entry's JSON metadata (ids, lineage, base) when sizing the emergency payload. */
	var EMERGENCY_ENTRY_OVERHEAD_CHARS = 1024;
	var CLOSED_PAGES_KEPT = 8;
	var CLOSED_PAGES_LOCAL_KEPT = 64;
	var SCOPE_KEY_PATTERN = /^[A-Za-z0-9_-]{1,128}\.[A-Za-z0-9_-]{1,128}\.[A-Za-z0-9_-]{1,128}$/;
	/** A scope key as browser-scope.js forms it: `<instanceId>.<libraryId>.<userId>`. */
	function isScopeKey(value) {
		return typeof value === "string" && SCOPE_KEY_PATTERN.test(value);
	}
	/** The names of one scope's recovery storage. Throws on anything that is not a scope key. */
	function recoveryNames(scopeKey) {
		if (!isScopeKey(scopeKey)) throw new Error("recovery storage needs a signed-in scope");
		return Object.freeze({
			scopeKey,
			dbName: "prks-editor-recovery-v2:" + scopeKey,
			emergencyPrefix: "prks.editorRecovery.emergency.v2." + scopeKey + ".",
			reservationPrefix: "prks.editorRecovery.reserve.v2." + scopeKey + ".",
			runtimeSessionKey: "prks.editorRecovery.runtime.v2." + scopeKey,
			closedPagesSessionKey: "prks.editorRecovery.closed.v2." + scopeKey,
			closedPagePrefix: "prks.editorRecovery.closedPage.v2." + scopeKey + ".",
			supersededPrefix: "prks.editorRecovery.superseded.v2." + scopeKey + ".",
			deletedWorkPrefix: "prks.workRecoveryCleanup.v2." + scopeKey + ".",
			channel: "prks-editor-recovery-v2:" + scopeKey,
			runtimeLockPrefix: "prks-editor-recovery-runtime:" + scopeKey + ":",
			pageLockPrefix: "prks-editor-recovery-page:" + scopeKey + ":"
		});
	}
	/** Bodies above this are "large": written immediately and leave-guarded until committed. */
	var LARGE_BODY_CHARS = 262144;
	/** Emergency entry budget: per body, and all held bodies of one page together. */
	var EMERGENCY_BODY_CHARS = LARGE_BODY_CHARS;
	var EMERGENCY_PAGE_CHARS = 1048576;
	var IDLE_WRITE_MS = 300;
	var MAX_WRITE_WAIT_MS = 1e3;
	var CLAIM_WAIT_MS = 250;
	var RETRY_FIRST_MS = 2e3;
	var RETRY_MAX_MS = 3e4;
	var UNKNOWN_BASE = Object.freeze({
		revision: null,
		length: null,
		fingerprint: null,
		source: "unknown"
	});
	function entityKeyOf(kind, entityId) {
		return kind + ":" + entityId;
	}
	function emergencyKeyOf(names, pageInstanceId) {
		return names.emergencyPrefix + pageInstanceId;
	}
	function reservationKeyOf(names, pageInstanceId) {
		return names.reservationPrefix + pageInstanceId;
	}
	/**
	* A reservation value: the reserving page's runtime id, a newline, then
	* filler. A later page that claims the same runtime (a reload or crash
	* restore in that tab) knows the reserving page is gone without Web Locks.
	*/
	function reservationValue(runtimeId, filler) {
		return runtimeId + "\n" + filler;
	}
	/** The runtime id a reservation value was tagged with, or null for an untagged value. */
	function reservationRuntimeOf(value) {
		const end = value.indexOf("\n");
		return end > 0 ? value.slice(0, end) : null;
	}
	/**
	* Random id with 128 bits from `crypto.getRandomValues`, which exists in
	* insecure (LAN/HTTP) contexts. `crypto.randomUUID` does not, so it is not used.
	*/
	function mintId(prefix, random = globalThis.crypto) {
		const bytes = /* @__PURE__ */ new Uint8Array(16);
		random.getRandomValues(bytes);
		let hex = "";
		for (let i = 0; i < bytes.length; i++) hex += bytes[i].toString(16).padStart(2, "0");
		return prefix + "-" + hex;
	}
	var KINDS = [
		"work-research-note",
		"work-private-note",
		"folder-private-note"
	];
	function isDraftKind(value) {
		return typeof value === "string" && KINDS.includes(value);
	}
	/** Readable by this build. A newer `v` is ignored and never rewritten or deleted. */
	function isSupportedRecord(record) {
		if (!record || typeof record !== "object") return false;
		const r = record;
		return typeof r.v === "number" && r.v >= 1 && r.v <= 1 && typeof r.draftId === "string" && isDraftKind(r.kind) && typeof r.generation === "number" && !!r.owner && typeof r.owner.pageInstanceId === "string";
	}
	//#endregion
	//#region src/lifecycle/editor-recovery/store.ts
	/**
	* IndexedDB store for editor recovery drafts (`prks-editor-recovery-v1`).
	*
	* Two object stores written together: `drafts` (small metadata, enumerated
	* with getAll + filter) and `bodies` (`{draftId, generation, body}`). Every
	* write puts both in one readwrite transaction and resolves only from
	* `oncomplete`; an abort rejects, so metadata and body never diverge and a
	* write is never reported before it committed.
	*
	* Write transactions request `{durability: 'relaxed'}` where supported and fall
	* back to a plain readwrite transaction. That covers reload, tab close and a
	* browser-process crash. It is not a promise against OS crash or power loss;
	* the semantic operation queue stays the strict durability boundary.
	*
	* Every decision that depends on stored state (owner, generation, exact body)
	* is made inside the same transaction that writes or deletes, so concurrent
	* pages are serialized by IndexedDB itself. Nothing here deletes by age.
	*/
	var RecoveryStoreError = class extends Error {
		code;
		constructor(code, message) {
			super(message);
			this.name = "RecoveryStoreError";
			this.code = code;
		}
	};
	function supportsDurabilityHint() {
		try {
			return typeof IDBTransaction !== "undefined" && "durability" in IDBTransaction.prototype;
		} catch {
			return false;
		}
	}
	function errorFromTransaction(tx) {
		if ((tx.error && tx.error.name) === "QuotaExceededError") return new RecoveryStoreError("quota", "Recovery storage is full.");
		return new RecoveryStoreError("aborted", "The recovery write was rolled back.");
	}
	function clonePipeline(pipeline) {
		if (!pipeline) return null;
		return {
			...pipeline,
			blockedBase: pipeline.blockedBase ? { ...pipeline.blockedBase } : null,
			ownQueued: pipeline.ownQueued ? {
				...pipeline.ownQueued,
				base: { ...pipeline.ownQueued.base }
			} : null
		};
	}
	/** Lineage id for an emergency tail forked away from a lineage adopted since. Deterministic, so merges are idempotent. */
	function forkedDraftId(draftId, generation) {
		return draftId + ".e" + generation;
	}
	function createRecoveryStore(options) {
		const factory = options.indexedDB === void 0 ? globalThis.indexedDB : options.indexedDB;
		const name = options.name;
		if (!name) throw new Error("recovery storage needs a signed-in scope");
		const now = options.now || Date.now;
		const durability = options.durability || "auto";
		const compare = { fingerprint: options.fingerprint };
		let dbPromise = null;
		let handle = null;
		let lastMode = null;
		let retired = false;
		function retiredError() {
			return new RecoveryStoreError("retired", "This account is signed out of recovery storage.");
		}
		function openDb() {
			if (retired) return Promise.reject(retiredError());
			if (dbPromise) return dbPromise;
			const opening = new Promise((resolve, reject) => {
				if (!factory) {
					reject(new RecoveryStoreError("unavailable", "IndexedDB is not available."));
					return;
				}
				let req;
				try {
					req = factory.open(name, 1);
				} catch {
					reject(new RecoveryStoreError("unavailable", "Could not open recovery storage."));
					return;
				}
				req.onupgradeneeded = () => {
					const db = req.result;
					if (!db.objectStoreNames.contains("drafts")) db.createObjectStore(DRAFTS_STORE, { keyPath: "draftId" });
					if (!db.objectStoreNames.contains("bodies")) db.createObjectStore(BODIES_STORE, { keyPath: "draftId" });
				};
				req.onsuccess = () => {
					const db = req.result;
					if (retired) {
						try {
							db.close();
						} catch {}
						reject(retiredError());
						return;
					}
					handle = db;
					db.onversionchange = () => {
						try {
							db.close();
						} catch {}
						if (handle === db) handle = null;
						dbPromise = null;
					};
					resolve(db);
				};
				req.onerror = () => reject(new RecoveryStoreError("unavailable", "Could not open recovery storage."));
				req.onblocked = () => reject(new RecoveryStoreError("blocked", "Recovery storage is blocked by another tab."));
			});
			dbPromise = opening;
			opening.catch(() => {
				if (dbPromise === opening) dbPromise = null;
			});
			return opening;
		}
		function begin(db, mode) {
			const stores = [DRAFTS_STORE, BODIES_STORE];
			if (mode === "readwrite" && (durability === "relaxed" || durability === "auto" && supportsDurabilityHint())) try {
				const tx = db.transaction(stores, mode, { durability: "relaxed" });
				lastMode = "relaxed";
				return tx;
			} catch {}
			const tx = db.transaction(stores, mode);
			if (mode === "readwrite") lastMode = "default";
			return tx;
		}
		/**
		* Runs `fn` in one transaction over both stores and resolves from
		* `oncomplete` with the value `fn` reported. Rejects on abort or error.
		*/
		function run(mode, fn) {
			return openDb().then((db) => new Promise((resolve, reject) => {
				if (retired) {
					reject(retiredError());
					return;
				}
				let tx;
				try {
					tx = begin(db, mode);
				} catch {
					reject(new RecoveryStoreError("unavailable", "Could not start a recovery transaction."));
					return;
				}
				let value;
				let reported = false;
				let settled = false;
				tx.oncomplete = () => {
					if (settled) return;
					settled = true;
					if (!reported) reject(new RecoveryStoreError("aborted", "The recovery transaction ended without a result."));
					else resolve(value);
				};
				tx.onabort = () => {
					if (settled) return;
					settled = true;
					reject(errorFromTransaction(tx));
				};
				try {
					fn(tx, (v) => {
						value = v;
						reported = true;
					});
				} catch {
					settled = true;
					try {
						tx.abort();
					} catch {}
					reject(new RecoveryStoreError("aborted", "The recovery transaction failed."));
				}
			}));
		}
		/** Reads the metadata and body rows of one draft, then calls `next` inside the same transaction. */
		function readBoth(tx, draftId, next) {
			let record;
			let body;
			let pending = 2;
			const step = () => {
				pending -= 1;
				if (pending === 0) next(record, body);
			};
			const r1 = tx.objectStore(DRAFTS_STORE).get(draftId);
			r1.onsuccess = () => {
				record = r1.result;
				step();
			};
			const r2 = tx.objectStore(BODIES_STORE).get(draftId);
			r2.onsuccess = () => {
				body = r2.result;
				step();
			};
		}
		function readRecord(tx, draftId, next) {
			const r = tx.objectStore(DRAFTS_STORE).get(draftId);
			r.onsuccess = () => next(r.result);
		}
		function putPair(tx, record, body) {
			tx.objectStore(DRAFTS_STORE).put(record);
			tx.objectStore(BODIES_STORE).put({
				draftId: record.draftId,
				generation: record.generation,
				body
			});
		}
		function deletePair(tx, draftId) {
			tx.objectStore(DRAFTS_STORE).delete(draftId);
			tx.objectStore(BODIES_STORE).delete(draftId);
		}
		/** Deletes a record, or leaves a tombstone for the emergency key that may still list it. */
		function retire(tx, record) {
			if (!record.emergencySource) return deletePair(tx, record.draftId);
			tx.objectStore(BODIES_STORE).delete(record.draftId);
			tx.objectStore(DRAFTS_STORE).put(tombstoneRecord(record.draftId, {
				kind: record.kind,
				entityType: record.entityType,
				entityId: record.entityId,
				generation: record.generation,
				pageInstanceId: record.emergencySource
			}, record.generation));
		}
		function newRecord(draftId, lineage, generation, bodyLength) {
			const at = now();
			return {
				v: 1,
				draftId,
				kind: lineage.kind,
				entityType: lineage.entityType,
				entityId: lineage.entityId,
				entityKey: entityKeyOf(lineage.kind, lineage.entityId),
				owner: { ...lineage.owner },
				generation,
				bodyLength,
				base: { ...lineage.base },
				pipeline: null,
				status: "active",
				createdAt: at,
				updatedAt: at
			};
		}
		function writeGeneration(input) {
			return run("readwrite", (tx, done) => {
				readRecord(tx, input.draftId, (record) => {
					if (!record) {
						if (!input.create) {
							done("missing");
							return;
						}
						const created = newRecord(input.draftId, input.create, input.generation, input.body.length);
						if (input.pipeline !== void 0) created.pipeline = clonePipeline(input.pipeline);
						putPair(tx, created, input.body);
						done("ok");
						return;
					}
					if (!isSupportedRecord(record)) return done("unsupported");
					if (record.owner.pageInstanceId !== input.pageInstanceId) return done("not-owner");
					if (record.generation >= input.generation) return done("stale");
					putPair(tx, {
						...record,
						v: 1,
						owner: {
							...record.owner,
							paneId: input.paneId ?? record.owner.paneId
						},
						generation: input.generation,
						bodyLength: input.body.length,
						base: input.base ? { ...input.base } : record.base,
						pipeline: input.pipeline !== void 0 ? clonePipeline(input.pipeline) : record.pipeline,
						status: "active",
						updatedAt: now()
					}, input.body);
					done("ok");
				});
			});
		}
		function updateLineage(input) {
			return run("readwrite", (tx, done) => {
				readRecord(tx, input.draftId, (record) => {
					if (!record || record.status === "discarded") return done("missing");
					if (!isSupportedRecord(record)) return done("unsupported");
					if (record.owner.pageInstanceId !== input.pageInstanceId) return done("not-owner");
					const next = {
						...record,
						base: input.base ? { ...input.base } : record.base,
						pipeline: input.pipeline !== void 0 ? clonePipeline(input.pipeline) : record.pipeline,
						updatedAt: now()
					};
					tx.objectStore(DRAFTS_STORE).put(next);
					done("ok");
				});
			});
		}
		function adopt(draftId, expectedPageInstanceId, owner, expectedGeneration) {
			return run("readwrite", (tx, done) => {
				readRecord(tx, draftId, (record) => {
					if (!record) return done({ outcome: "missing" });
					if (!isSupportedRecord(record)) return done({ outcome: "unsupported" });
					if (record.status === "discarded") return done({ outcome: "missing" });
					if (record.owner.pageInstanceId !== expectedPageInstanceId) return done({ outcome: "conflict" });
					if (expectedGeneration !== void 0 && record.generation !== expectedGeneration) return done({ outcome: "conflict" });
					const next = {
						...record,
						owner: { ...owner },
						updatedAt: now()
					};
					tx.objectStore(DRAFTS_STORE).put(next);
					done({
						outcome: "ok",
						record: next
					});
				});
			});
		}
		function get(draftId) {
			return run("readonly", (tx, done) => {
				readRecord(tx, draftId, (record) => done(record || null));
			});
		}
		function getBody(draftId) {
			return run("readonly", (tx, done) => {
				const r = tx.objectStore(BODIES_STORE).get(draftId);
				r.onsuccess = () => done(r.result || null);
			});
		}
		function listAll() {
			return run("readonly", (tx, done) => {
				const r = tx.objectStore(DRAFTS_STORE).getAll();
				r.onsuccess = () => done(r.result || []);
			});
		}
		function listByEntity(kind, entityId) {
			const key = entityKeyOf(kind, entityId);
			return listAll().then((rows) => rows.filter((row) => row && row.entityKey === key && row.status !== "discarded"));
		}
		function deleteIfAcknowledged(draftId, generation, body, expectedPageInstanceId) {
			return run("readwrite", (tx, done) => {
				readBoth(tx, draftId, (record, row) => {
					if (!record) return done("missing");
					if (!isSupportedRecord(record)) return done("unsupported");
					if (expectedPageInstanceId !== void 0 && record.owner.pageInstanceId !== expectedPageInstanceId) return done("kept");
					if (record.generation !== generation || !row || row.generation !== generation) return done("kept");
					if (!sameBody(row.body, body, compare)) return done("kept");
					retire(tx, record);
					done("deleted");
				});
			});
		}
		function deleteIfEqual(draftId, body) {
			return run("readwrite", (tx, done) => {
				readBoth(tx, draftId, (record, row) => {
					if (!record) return done("missing");
					if (!isSupportedRecord(record)) return done("unsupported");
					if (!row || row.generation !== record.generation || !sameBody(row.body, body, compare)) return done("kept");
					retire(tx, record);
					done("deleted");
				});
			});
		}
		function tombstoneRecord(draftId, tombstone, storedGeneration) {
			return {
				...newRecord(draftId, {
					kind: tombstone.kind,
					entityType: tombstone.entityType,
					entityId: tombstone.entityId,
					owner: {
						runtimeId: null,
						pageInstanceId: tombstone.pageInstanceId,
						paneId: "",
						claimedAt: now()
					},
					base: UNKNOWN_BASE
				}, Math.max(tombstone.generation, storedGeneration), 0),
				status: "discarded"
			};
		}
		function discard(draftId, tombstone, expected) {
			return run("readwrite", (tx, done) => {
				readRecord(tx, draftId, (record) => {
					if (record && !isSupportedRecord(record)) return done("unsupported");
					if (expected) {
						if (!record || record.status === "discarded") return done("missing");
						if (record.owner.pageInstanceId !== expected.pageInstanceId || record.generation !== expected.generation || record.status !== expected.status) return done("kept");
					}
					if (!tombstone) {
						if (!record) return done("missing");
						retire(tx, record);
						return done("deleted");
					}
					tx.objectStore(BODIES_STORE).delete(draftId);
					const stone = tombstoneRecord(draftId, tombstone, record ? record.generation : 0);
					const source = record ? record.emergencySource ?? (record.status === "discarded" ? record.owner.pageInstanceId : void 0) : void 0;
					if (source && source !== tombstone.pageInstanceId) stone.emergencySource = source;
					tx.objectStore(DRAFTS_STORE).put(stone);
					done(record ? "deleted" : "missing");
				});
			});
		}
		function clearTombstone(draftId, pageInstanceId) {
			return run("readwrite", (tx, done) => {
				readRecord(tx, draftId, (record) => {
					if (!record) return done("missing");
					if (!isSupportedRecord(record)) return done("unsupported");
					const next = {
						...record,
						owner: { ...record.owner }
					};
					if (record.emergencySource === pageInstanceId) delete next.emergencySource;
					else if (record.status === "discarded" && record.owner.pageInstanceId === pageInstanceId) {
						if (!record.emergencySource) {
							deletePair(tx, draftId);
							return done("deleted");
						}
						next.owner.pageInstanceId = record.emergencySource;
						delete next.emergencySource;
					} else return done("kept");
					tx.objectStore(DRAFTS_STORE).put(next);
					done("kept");
				});
			});
		}
		function tombstoneIfAbsent(draftId, tombstone) {
			return run("readwrite", (tx, done) => {
				readRecord(tx, draftId, (record) => {
					if (record) return done("kept");
					tx.objectStore(DRAFTS_STORE).put(tombstoneRecord(draftId, tombstone, 0));
					done("suppressed");
				});
			});
		}
		function applyEmergencyEntry(payload, entry) {
			return run("readwrite", (tx, done) => {
				readRecord(tx, entry.draftId, (record) => {
					if (record && !isSupportedRecord(record)) return done("deferred");
					if (record && record.status === "discarded") return done("suppressed");
					if (!record) {
						if (entry.committedGeneration === 0 && entry.lineage && entry.body !== null) {
							const lineage = {
								kind: entry.kind,
								entityType: entry.entityType,
								entityId: entry.entityId,
								owner: {
									...entry.lineage.owner,
									claimedAt: entry.lineage.createdAt
								},
								base: entry.lineage.base
							};
							const created = newRecord(entry.draftId, lineage, entry.generation, entry.body.length);
							created.createdAt = entry.lineage.createdAt;
							created.emergencySource = payload.pageInstanceId;
							putPair(tx, created, entry.body);
							return done("created");
						}
						return done("dropped");
					}
					if (record.owner.pageInstanceId === payload.pageInstanceId) {
						if (record.generation >= entry.generation) return done("noop");
						if (entry.body === null) {
							tx.objectStore(DRAFTS_STORE).put({
								...record,
								status: "tail-missing",
								updatedAt: now()
							});
							return done("tail-missing");
						}
						putPair(tx, {
							...record,
							generation: entry.generation,
							bodyLength: entry.body.length,
							status: "active",
							updatedAt: now(),
							...entry.lineage ? {
								base: { ...entry.lineage.base },
								owner: {
									...record.owner,
									paneId: entry.lineage.owner.paneId
								}
							} : {}
						}, entry.body);
						return done("written");
					}
					if (entry.body === null) return done("dropped");
					const forkId = forkedDraftId(entry.draftId, entry.generation);
					readRecord(tx, forkId, (existing) => {
						if (existing) return done("noop");
						const lineage = {
							kind: entry.kind,
							entityType: entry.entityType,
							entityId: entry.entityId,
							owner: {
								runtimeId: payload.runtimeId,
								pageInstanceId: payload.pageInstanceId,
								paneId: entry.lineage ? entry.lineage.owner.paneId : record.owner.paneId,
								claimedAt: payload.at
							},
							base: entry.lineage ? entry.lineage.base : record.base
						};
						const forked = newRecord(forkId, lineage, entry.generation, entry.body.length);
						forked.emergencySource = payload.pageInstanceId;
						putPair(tx, forked, entry.body);
						done("forked");
					});
				});
			});
		}
		return {
			writeGeneration,
			updateLineage,
			adopt,
			get,
			getBody,
			listByEntity,
			listAll,
			deleteIfAcknowledged,
			deleteIfEqual,
			discard,
			clearTombstone,
			tombstoneIfAbsent,
			applyEmergencyEntry,
			lastDurability: () => lastMode,
			close,
			retire() {
				retired = true;
				close();
			},
			isRetired: () => retired
		};
		function close() {
			const db = handle;
			handle = null;
			dbPromise = null;
			if (db) try {
				db.close();
			} catch {}
		}
	}
	//#endregion
	//#region src/lifecycle/editor-recovery/emergency.ts
	/**
	* Unload emergency entry: one synchronous localStorage key per page load
	* (`prks.editorRecovery.emergency.v1.<pageInstanceId>`), written at
	* `visibilitychange:hidden` / `pagehide` only while a writer is pending.
	*
	* The plan decides which pending bodies the entry may hold: each at most
	* 256 Ki chars and all of a page together at most 1 Mi chars, smallest first.
	* A pending writer outside the plan has the leave guard armed instead.
	*
	* Startup merges entries of pages that are no longer alive into IndexedDB and
	* removes a key only after every entry in it was handled.
	*/
	/** Indexes of the bodies the emergency entry holds, chosen smallest first within the budget. */
	function planEmergency(lengths) {
		const order = lengths.map((length, index) => ({
			length,
			index
		})).sort((a, b) => a.length - b.length || a.index - b.index);
		const held = /* @__PURE__ */ new Set();
		let total = 0;
		for (const item of order) {
			if (item.length > 262144) continue;
			if (total + item.length > 1048576) continue;
			total += item.length;
			held.add(item.index);
		}
		return held;
	}
	/** Synchronous write. On failure retries once with every body null, then gives up. */
	var SHORT_ESCAPE = /["\\\b\f\n\r\t]/g;
	var UNICODE_ESCAPE = /[\u0000-\u0007\u000b\u000e-\u001f]|[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g;
	/**
	* Chars JSON.stringify adds to `text` beyond its length: one for each `"`,
	* `\\` and short escape (`\n`, `\t`, ...), five for each other control char or
	* lone surrogate (`\u00XX`). Exact, so capacity checks never undercount.
	*/
	function jsonEscapeExtra(text) {
		const short = text.match(SHORT_ESCAPE);
		const long = text.match(UNICODE_ESCAPE);
		return (short ? short.length : 0) + (long ? long.length * 5 : 0);
	}
	function writeEmergency(storage, key, payload) {
		try {
			storage.setItem(key, JSON.stringify(payload));
			return "written";
		} catch {}
		try {
			const stripped = {
				...payload,
				entries: payload.entries.map((entry) => ({
					...entry,
					body: null
				}))
			};
			storage.setItem(key, JSON.stringify(stripped));
			return "written-without-bodies";
		} catch {
			return "failed";
		}
	}
	function isEntry(value) {
		if (!value || typeof value !== "object") return false;
		const e = value;
		return typeof e.draftId === "string" && isDraftKind(e.kind) && typeof e.entityId === "string" && typeof e.generation === "number" && typeof e.committedGeneration === "number" && (e.body === null || typeof e.body === "string") && (e.committedGeneration !== 0 || !!e.lineage);
	}
	function parsePayload(raw, pageInstanceId) {
		if (!raw) return null;
		try {
			const value = JSON.parse(raw);
			if (!value || value.v !== 1 || value.pageInstanceId !== pageInstanceId) return null;
			if (!Array.isArray(value.entries) || !value.entries.every(isEntry)) return null;
			return value;
		} catch {
			return null;
		}
	}
	/**
	* Whether a key this code cannot parse may still hold text a later read or a
	* newer bundle recovers: its read failed, it is an envelope of a newer
	* schema, or its only unreadable entries are of a draft kind this bundle does
	* not know. A malformed, older or mismatched key holds nothing any bundle can
	* merge; it names no entity, and it is left in place untouched.
	*/
	function mayHoldUnreadable(stored) {
		if (stored.payload) return false;
		if (stored.readFailed) return true;
		if (stored.raw === null) return false;
		try {
			const value = JSON.parse(stored.raw);
			if (!value || typeof value !== "object" || typeof value.v !== "number") return false;
			if (value.v > 1) return true;
			return value.v === 1 && value.pageInstanceId === stored.pageInstanceId && Array.isArray(value.entries) && value.entries.every((entry) => isEntry(entry) || !!entry && typeof entry === "object" && typeof entry.kind === "string" && !isDraftKind(entry.kind));
		} catch {
			return false;
		}
	}
	/** Blocked storage can throw on enumeration or reads: that leaves the emergency layer empty, never failing startup. */
	function readEmergencyKeys(storage, names) {
		const prefix = names.emergencyPrefix;
		const keys = [];
		try {
			for (let i = 0; i < storage.length; i++) {
				const key = storage.key(i);
				if (key && key.startsWith(prefix)) keys.push(key);
			}
		} catch {
			return [];
		}
		return keys.map((key) => {
			const pageInstanceId = key.slice(prefix.length);
			let raw = null;
			let readFailed = false;
			try {
				raw = storage.getItem(key);
			} catch {
				readFailed = true;
			}
			return {
				key,
				pageInstanceId,
				payload: parsePayload(raw, pageInstanceId),
				raw,
				readFailed
			};
		});
	}
	function readRaw(storage, key) {
		try {
			return storage.getItem(key);
		} catch {
			return;
		}
	}
	async function mergeable(env, stored) {
		if (stored.pageInstanceId === env.pageInstanceId || !stored.payload) return false;
		const closed = env.definiteOnly && env.wasPageClosed ? env.wasPageClosed(stored.pageInstanceId) : false;
		if (env.definiteOnly && !closed && stored.payload.runtimeId === null) return false;
		const alive = await env.isPageAlive(stored.pageInstanceId);
		return env.definiteOnly && !closed ? alive === false : alive !== true;
	}
	/**
	* Applies one key's entries, re-reading the key before each step: if its page
	* rewrote it since the snapshot, the rest waits for a later scan and the key
	* is kept, so a stale snapshot never recreates a draft or removes newer entries.
	*/
	async function mergeKey(env, stored, payload) {
		const outcomes = [];
		/** Records that may carry a tombstone or mark for this key. */
		const sourced = [];
		let complete = true;
		for (const entry of payload.entries) {
			if (readRaw(env.storage, stored.key) !== stored.raw) {
				complete = false;
				break;
			}
			try {
				const outcome = await env.store.applyEmergencyEntry(payload, entry);
				outcomes.push(outcome);
				if (outcome === "deferred") complete = false;
				sourced.push(entry.draftId, forkedDraftId(entry.draftId, entry.generation));
			} catch {
				complete = false;
			}
		}
		if (complete && readRaw(env.storage, stored.key) !== stored.raw) complete = false;
		if (complete) try {
			env.storage.removeItem(stored.key);
		} catch {
			complete = false;
		}
		if (complete && env.store.clearTombstone) for (const draftId of sourced) try {
			await env.store.clearTombstone(draftId, stored.pageInstanceId);
		} catch {}
		return {
			key: stored.key,
			outcomes,
			removed: complete
		};
	}
	async function mergeEmergencyEntries(env) {
		const reports = [];
		for (const stored of readEmergencyKeys(env.storage, env.names)) {
			if (!await mergeable(env, stored)) continue;
			reports.push(await mergeKey(env, stored, stored.payload));
		}
		return reports;
	}
	/**
	* Removes the quota reservations of pages that are proven gone: held Web
	* Locks no longer list the page lock, or this page verifiably claimed the
	* runtime the reservation is tagged with (a reload or crash restore in that
	* tab), which a live page would still hold. A missed BroadcastChannel answer
	* alone never removes one, because a frozen or busy page misses it while its
	* writer still counts the reservation as held; and a writer coming back from
	* hidden or frozen writes its reservation again. A reservation holds only
	* filler, never text, so removing it loses nothing.
	*/
	async function releaseDeadReservations(env) {
		const keys = [];
		try {
			for (let i = 0; i < env.storage.length; i++) {
				const key = env.storage.key(i);
				if (key && key.startsWith(env.names.reservationPrefix)) keys.push(key);
			}
		} catch {
			return [];
		}
		const removed = [];
		for (const key of keys) {
			const pageInstanceId = key.slice(env.names.reservationPrefix.length);
			if (pageInstanceId === env.pageInstanceId) continue;
			if (!await env.isPageGone(pageInstanceId) && !claimedRuntimeOf(env, key)) continue;
			try {
				env.storage.removeItem(key);
				removed.push(key);
			} catch {}
		}
		return removed;
	}
	function claimedRuntimeOf(env, key) {
		if (!env.verifiedRuntimeId) return false;
		try {
			const value = env.storage.getItem(key);
			return value !== null && reservationRuntimeOf(value) === env.verifiedRuntimeId;
		} catch {
			return false;
		}
	}
	/** Diagnostics warns when recovery copies together exceed this (design §9). */
	var RECOVERY_WARN_BYTES = 268435456;
	function isLive(lineage) {
		return lineage === "self-live" || lineage === "other-live";
	}
	function storageState(error) {
		const code = error && typeof error === "object" ? error.code : void 0;
		if (code === "unavailable" || code === "blocked") return code;
		if (code === "retired") return "unavailable";
		return "failed";
	}
	function stringOrNull(value) {
		return typeof value === "string" ? value : null;
	}
	function readKey(storage, key) {
		try {
			return storage ? storage.getItem(key) : null;
		} catch {
			return null;
		}
	}
	function supersededGeneration(storage, names, draftId) {
		const raw = readKey(storage, names.supersededPrefix + draftId);
		if (!raw) return 0;
		try {
			const value = JSON.parse(raw);
			return value && typeof value.generation === "number" ? value.generation : 0;
		} catch {
			return 0;
		}
	}
	function unreadableReason(stored) {
		if (stored.readFailed) return "read-failed";
		return mayHoldUnreadable(stored) ? "newer-version" : "malformed";
	}
	function finiteAtLeastZero(value) {
		return typeof value === "number" && Number.isFinite(value) && value >= 0;
	}
	function numberOrNull(value) {
		return value === null || typeof value === "number" && Number.isFinite(value);
	}
	function isObject(value) {
		return !!value && typeof value === "object";
	}
	var BASE_SOURCES = [
		"server",
		"cache",
		"pending-create",
		"unknown"
	];
	var PIPELINE_STATES = [
		"drafting",
		"saving",
		"queued",
		"blocked",
		"error",
		"conflict"
	];
	function isIntactOwner(owner) {
		return isObject(owner) && (owner.runtimeId === null || typeof owner.runtimeId === "string") && typeof owner.pageInstanceId === "string" && typeof owner.paneId === "string" && finiteAtLeastZero(owner.claimedAt);
	}
	function isIntactBase(base) {
		return isObject(base) && typeof base.source === "string" && BASE_SOURCES.includes(base.source) && numberOrNull(base.revision) && numberOrNull(base.length) && (base.fingerprint === null || typeof base.fingerprint === "string");
	}
	function isIntactPipeline(pipeline) {
		return pipeline === null || isObject(pipeline) && typeof pipeline.state === "string" && PIPELINE_STATES.includes(pipeline.state);
	}
	/**
	* A row with the store's tombstone shape: the whole v1 shape, `discarded`, no
	* body length. Whether its body is really gone is checked when it is listed.
	*/
	function isTombstoneShape(row) {
		return hasIntactShape(row) && row.status === "discarded" && row.bodyLength === 0;
	}
	/**
	* A supported draft record with the whole v1 shape intact: identity, entity,
	* owner, base, pipeline, sizes and times. Anything less is listed copy-only
	* as damaged and is never offered for Discard.
	*/
	function isIntactRecord(row) {
		return hasIntactShape(row) && (row.status === "active" || row.status === "tail-missing");
	}
	/** Every v1 field present and well formed, whatever the status. */
	function hasIntactShape(row) {
		if (!isSupportedRecord(row)) return false;
		const r = row;
		const entityType = row.kind === "folder-private-note" ? "folder" : "work";
		return (r.status === "active" || r.status === "tail-missing" || r.status === "discarded") && r.entityType === entityType && typeof r.entityId === "string" && r.entityId !== "" && r.entityKey === entityKeyOf(row.kind, r.entityId) && Number.isInteger(r.generation) && finiteAtLeastZero(r.generation) && isIntactOwner(r.owner) && isIntactBase(r.base) && isIntactPipeline(r.pipeline) && finiteAtLeastZero(r.bodyLength) && finiteAtLeastZero(r.createdAt) && finiteAtLeastZero(r.updatedAt) && (r.emergencySource === void 0 || typeof r.emergencySource === "string");
	}
	/**
	* An exact, tagged serialization of a structured-clone value: primitives
	* (BigInt included), arrays, plain objects, Dates, Maps and Sets,
	* with shared and cyclic references by position. Null for anything it cannot
	* represent exactly, so two different rows never share a snapshot.
	*/
	function serialized(value, seen) {
		switch (typeof value) {
			case "undefined": return "u";
			case "boolean": return "b:" + String(value);
			case "number": return "n:" + (Object.is(value, -0) ? "-0" : String(value));
			case "bigint": return "i:" + value.toString();
			case "string": return "s:" + JSON.stringify(value);
			case "object": break;
			default: return null;
		}
		if (value === null) return "null";
		const ref = seen.get(value);
		if (ref !== void 0) return "r:" + ref;
		seen.set(value, seen.size);
		const all = (parts) => parts.every((part) => part !== null) ? parts.join(",") : null;
		const wrap = (open, inner, close) => inner === null ? null : open + inner + close;
		const ownKeys = (record) => all(Object.keys(record).map((k) => wrap(JSON.stringify(k) + ":", serialized(record[k], seen), "")));
		if (Array.isArray(value)) return wrap("[" + value.length + ":", ownKeys(value), "]");
		if (value instanceof Date) return "d:" + value.getTime();
		if (value instanceof Map) return wrap("m{", all([...value].map(([k, v]) => wrap("", all([serialized(k, seen), serialized(v, seen)]), ""))), "}");
		if (value instanceof Set) return wrap("S{", all([...value].map((item) => serialized(item, seen))), "}");
		if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) return null;
		const proto = Object.getPrototypeOf(value);
		if (proto !== Object.prototype && proto !== null) return null;
		return wrap("{", ownKeys(value), "}");
	}
	/** Two independent 32-bit FNV-1a lanes over the UTF-16 code units: a compact fingerprint of a body's text. */
	function digestOf(text) {
		let a = 2166136261;
		let b = 16777619 ^ text.length;
		for (let i = 0; i < text.length; i++) {
			const c = text.charCodeAt(i);
			a = Math.imul(a ^ c, 16777619);
			b = Math.imul(b ^ c, 1540483477) ^ b >>> 13;
		}
		return (a >>> 0).toString(16).padStart(8, "0") + (b >>> 0).toString(16).padStart(8, "0");
	}
	/**
	* Identity of a copy-only row and its stored body together, as listed: the row
	* exactly, the body by everything but its text plus the text's length and
	* fingerprint. Null when either cannot be represented exactly.
	*/
	function copySnapshotOf(row, body) {
		const rowPart = snapshotOf(row);
		if (rowPart === null) return null;
		if (body === null || body === void 0) return rowPart + "|none";
		if (typeof body !== "object") return null;
		const { body: text, ...rest } = body;
		const restPart = snapshotOf(rest);
		const textPart = typeof text === "string" ? "t:" + text.length + ":" + digestOf(text) : snapshotOf(text);
		return restPart === null || textPart === null ? null : rowPart + "|" + restPart + "|" + textPart;
	}
	/** Identity of a stored row as listed; null when it cannot be told apart exactly from a rewrite. */
	function snapshotOf(row) {
		return serialized(row, /* @__PURE__ */ new Map());
	}
	function enumerable(storage) {
		if (!storage) return false;
		try {
			return typeof storage.length === "number";
		} catch {
			return false;
		}
	}
	/** What a signed-out or replaced account's diagnostics show: nothing of its drafts. */
	function retiredInventory() {
		return {
			storage: "unavailable",
			emergencyStorage: "unavailable",
			drafts: [],
			unreadable: [],
			emergency: [],
			unreadableEmergency: [],
			totalChars: 0,
			approxBytes: 0,
			sizeIncomplete: false,
			overBudget: false
		};
	}
	function createRecoveryDiagnostics(rt, options = {}) {
		const storage = options.storage === void 0 ? rt.emergencyStorage : options.storage;
		const names = rt.names;
		const now = options.now || Date.now;
		async function classifySafely(record) {
			try {
				return await rt.classify(record);
			} catch {
				return "unknown";
			}
		}
		function draftOf(record, lineage, at) {
			const status = record.status === "tail-missing" ? "tail-missing" : "active";
			return {
				source: "record",
				draftId: record.draftId,
				kind: record.kind,
				entityType: record.entityType,
				entityId: record.entityId,
				status,
				lineage,
				generation: record.generation,
				bodyLength: record.bodyLength,
				createdAt: record.createdAt,
				updatedAt: record.updatedAt,
				baseRevision: record.base ? record.base.revision : null,
				baseSource: record.base ? record.base.source : "unknown",
				pipelineState: record.pipeline ? record.pipeline.state : null,
				old: !isLive(lineage) && at - record.updatedAt > 2592e6,
				superseded: supersededGeneration(storage, names, record.draftId) >= record.generation,
				deletedHere: record.entityType === "work" && readKey(storage, names.deletedWorkPrefix + record.entityId) !== null,
				fromEmergency: typeof record.emergencySource === "string",
				expect: {
					draftId: record.draftId,
					pageInstanceId: record.owner.pageInstanceId,
					generation: record.generation,
					status: record.status
				}
			};
		}
		function unreadableOf(row) {
			const r = row && typeof row === "object" ? row : {};
			const newer = typeof r.v === "number" && r.v > 1;
			return {
				source: "unreadable-record",
				draftId: stringOrNull(r.draftId),
				reason: newer ? "newer-version" : "malformed",
				kind: stringOrNull(r.kind),
				entityType: stringOrNull(r.entityType),
				entityId: stringOrNull(r.entityId),
				updatedAt: typeof r.updatedAt === "number" ? r.updatedAt : null,
				bodyLength: typeof r.bodyLength === "number" && Number.isFinite(r.bodyLength) && r.bodyLength >= 0 ? r.bodyLength : null,
				snapshot: snapshotOf(row)
			};
		}
		/** Stored records: readable ones classified, unreadable ones listed as they are. */
		async function listRecords(at) {
			let state = "ok";
			let rows = [];
			try {
				rows = await rt.store.listAll();
			} catch (error) {
				state = storageState(error);
			}
			const records = [];
			const tombstones = [];
			const unreadable = [];
			for (const row of rows) if (isTombstoneShape(row) && !await bodyStored(row.draftId)) tombstones.push(row);
			else if (isIntactRecord(row) && await bodyMatches(row)) records.push(row);
			else unreadable.push(await measured(row));
			const lineages = await Promise.all(records.map((record) => classifySafely(record)));
			const drafts = records.map((record, i) => draftOf(record, lineages[i], at));
			drafts.sort((a, b) => b.updatedAt - a.updatedAt || a.draftId.localeCompare(b.draftId));
			return {
				state,
				records,
				tombstones,
				drafts,
				unreadable
			};
		}
		/** Whether the stored body is text of the record's own generation and stated length; when that cannot be told, it is not. */
		async function bodyMatches(record) {
			try {
				const body = await rt.store.getBody(record.draftId);
				return !!body && body.generation === record.generation && typeof body.body === "string" && body.body.length === record.bodyLength;
			} catch {
				return false;
			}
		}
		/**
		* A copy-only row as listed: sized by its stored text when there is any (its
		* stated length may be wrong), and identified by its row and body together.
		*/
		async function measured(raw) {
			const row = unreadableOf(raw);
			if (row.draftId === null) return row;
			try {
				const body = await rt.store.getBody(row.draftId);
				const snapshot = copySnapshotOf(raw, body);
				return body && typeof body.body === "string" ? {
					...row,
					bodyLength: body.body.length,
					snapshot
				} : {
					...row,
					snapshot
				};
			} catch {
				return {
					...row,
					snapshot: null
				};
			}
		}
		/** Whether a body row exists; when that cannot be told, assume it does (list, never hide). */
		async function bodyStored(draftId) {
			try {
				return await rt.store.getBody(draftId) !== null;
			} catch {
				return true;
			}
		}
		function unreadableKeyOf(stored) {
			return {
				source: "unreadable-emergency",
				key: stored.key,
				pageInstanceId: stored.pageInstanceId,
				reason: unreadableReason(stored),
				rawLength: stored.raw === null ? null : stored.raw.length,
				raw: stored.raw
			};
		}
		/** Emergency entries of other pages that no record holds yet, and keys this build cannot parse. */
		async function listEmergency(records, tombstones) {
			const known = /* @__PURE__ */ new Map();
			for (const row of [...records, ...tombstones]) known.set(row.draftId, Math.max(row.generation, known.get(row.draftId) || 0));
			const unreadableEmergency = [];
			const pending = [];
			for (const stored of storage ? readEmergencyKeys(storage, names) : []) {
				const payload = stored.payload;
				if (!payload) {
					if (stored.raw !== null || stored.readFailed) unreadableEmergency.push(unreadableKeyOf(stored));
					continue;
				}
				const owner = {
					runtimeId: payload.runtimeId,
					pageInstanceId: stored.pageInstanceId,
					paneId: "",
					claimedAt: payload.at
				};
				for (const entry of payload.entries) {
					if (Math.max(known.get(entry.draftId) || 0, known.get(forkedDraftId(entry.draftId, entry.generation)) || 0) >= entry.generation) continue;
					pending.push(classifySafely({
						draftId: entry.draftId,
						owner
					}).then((lineage) => ({
						source: "emergency",
						key: stored.key,
						pageInstanceId: stored.pageInstanceId,
						draftId: entry.draftId,
						kind: entry.kind,
						entityType: entry.entityType,
						entityId: entry.entityId,
						generation: entry.generation,
						bodyLength: entry.body === null ? null : entry.body.length,
						at: payload.at,
						lineage
					})));
				}
			}
			const emergency = await Promise.all(pending);
			emergency.sort((a, b) => b.at - a.at || a.draftId.localeCompare(b.draftId));
			return {
				emergency,
				unreadableEmergency
			};
		}
		async function inventory() {
			const at = now();
			try {
				await rt.scanEmergency();
			} catch {}
			const { state, records, tombstones, drafts, unreadable } = await listRecords(at);
			const { emergency, unreadableEmergency } = await listEmergency(records, tombstones);
			const sizes = [
				...drafts.map((d) => Number.isFinite(d.bodyLength) ? d.bodyLength : null),
				...emergency.map((e) => e.bodyLength),
				...unreadable.map((u) => u.bodyLength),
				...unreadableEmergency.map((u) => u.rawLength)
			];
			const totalChars = sizes.reduce((sum, n) => sum + (n ?? 0), 0);
			const approxBytes = totalChars * 2;
			if (rt.isRetired()) return retiredInventory();
			return {
				storage: state,
				emergencyStorage: enumerable(storage) ? "ok" : "unavailable",
				drafts,
				unreadable,
				emergency,
				unreadableEmergency,
				totalChars,
				approxBytes,
				sizeIncomplete: sizes.some((n) => n === null),
				overBudget: approxBytes > RECOVERY_WARN_BYTES
			};
		}
		function sameReviewed(record, expect) {
			return isIntactRecord(record) && record.owner.pageInstanceId === expect.pageInstanceId && record.generation === expect.generation && record.status === expect.status;
		}
		async function readRecordText(expect) {
			const record = await rt.store.get(expect.draftId);
			if (!sameReviewed(record, expect)) return {
				ok: false,
				code: "changed"
			};
			if (isLive(await classifySafely(record))) return {
				ok: false,
				code: "live"
			};
			const row = await rt.store.getBody(expect.draftId);
			if (!row || typeof row.body !== "string") return {
				ok: false,
				code: "missing"
			};
			if (row.generation !== expect.generation) return {
				ok: false,
				code: "changed"
			};
			if (!sameReviewed(await rt.store.get(expect.draftId), expect)) return {
				ok: false,
				code: "changed"
			};
			return {
				ok: true,
				text: row.body
			};
		}
		async function readUnreadableText(draftId, snapshot) {
			if (snapshot === null) return {
				ok: false,
				code: "failed"
			};
			const take = async () => {
				const row = await rt.store.get(draftId);
				const body = await rt.store.getBody(draftId);
				return {
					same: copySnapshotOf(row, body) === snapshot,
					body
				};
			};
			const first = await take();
			if (!first.same) return {
				ok: false,
				code: "changed"
			};
			if (!first.body || typeof first.body.body !== "string") return {
				ok: false,
				code: "missing"
			};
			if (!(await take()).same) return {
				ok: false,
				code: "changed"
			};
			return {
				ok: true,
				text: first.body.body
			};
		}
		async function readEmergencyText(key, draftId, generation) {
			const stored = storage ? readEmergencyKeys(storage, names).find((s) => s.key === key) : void 0;
			const payload = stored ? stored.payload : null;
			if (!stored || !payload) return {
				ok: false,
				code: "changed"
			};
			const entry = payload.entries.find((e) => e.draftId === draftId && e.generation === generation);
			if (!entry) return {
				ok: false,
				code: "changed"
			};
			if (entry.body === null) return {
				ok: false,
				code: "missing"
			};
			if (isLive(await classifySafely({
				draftId,
				owner: {
					runtimeId: payload.runtimeId,
					pageInstanceId: stored.pageInstanceId,
					paneId: "",
					claimedAt: payload.at
				}
			}))) return {
				ok: false,
				code: "live"
			};
			if (readKey(storage, key) !== stored.raw) return {
				ok: false,
				code: "changed"
			};
			return {
				ok: true,
				text: entry.body
			};
		}
		function readRawEmergency(key, listed) {
			if (!storage || listed === null) return {
				ok: false,
				code: "failed"
			};
			try {
				const raw = storage.getItem(key);
				return raw === null || raw !== listed ? {
					ok: false,
					code: "changed"
				} : {
					ok: true,
					text: raw
				};
			} catch {
				return {
					ok: false,
					code: "failed"
				};
			}
		}
		async function readTextOf(target) {
			switch (target.source) {
				case "record": return await readRecordText(target.expect);
				case "unreadable-record": return await readUnreadableText(target.draftId, target.snapshot);
				case "emergency": return await readEmergencyText(target.key, target.draftId, target.generation);
				case "unreadable-emergency": return readRawEmergency(target.key, target.raw);
			}
		}
		async function readText(target) {
			try {
				const result = await readTextOf(target);
				return rt.isRetired() ? {
					ok: false,
					code: "failed"
				} : result;
			} catch {
				return {
					ok: false,
					code: "failed"
				};
			}
		}
		async function discard(target) {
			if (!isDraftKind(target.kind)) return "changed";
			try {
				const record = await rt.store.get(target.draftId);
				if (!sameReviewed(record, target) || record.kind !== target.kind || record.entityId !== target.entityId) return "changed";
				const lineage = await classifySafely(record);
				if (isLive(lineage)) return "live";
				if (lineage === "unknown" && !target.allowUnknownOwner) return "unknown-owner";
				return await rt.discardReviewed({
					draftId: target.draftId,
					pageInstanceId: target.pageInstanceId,
					generation: target.generation,
					status: target.status,
					kind: record.kind,
					entityType: record.entityType,
					entityId: record.entityId
				}) === "deleted" ? "deleted" : "changed";
			} catch {
				return "failed";
			}
		}
		return {
			inventory,
			readText,
			discard
		};
	}
	//#endregion
	//#region src/lifecycle/editor-recovery/identity.ts
	/**
	* Page and runtime identity for editor recovery.
	*
	* `pageInstanceId` is minted fresh on every load and is never read from or
	* written to sessionStorage. It owns lineage writes and keys the emergency
	* entry, so duplicated tabs can never collide on either.
	*
	* `runtimeId` names one browser tab across reloads. Its candidate comes from
	* sessionStorage, which `window.open` and Duplicate tab copy, so the page
	* claims it before use:
	* - with Web Locks, by holding `prks-editor-recovery-runtime:<id>` (ifAvailable);
	* - without them (LAN/HTTP), over BroadcastChannel: `claim` and a 250 ms
	*   window; a holder answers `taken`; two simultaneous claimants keep the
	*   lower `pageInstanceId`;
	* - with neither, the candidate is used `unverified`.
	* A loser mints a new candidate and claims again. Liveness questions about
	* other pages use held locks where available and channel pings otherwise.
	*
	* A missed ping never proves a page is gone. What does: a page lock no
	* longer held, or the page's own record of its final `pagehide` in
	* localStorage (`wasPageClosed`), which every tab of the origin can read.
	*/
	var MAX_CLAIM_ROUNDS = 8;
	function defaultChannel() {
		if (typeof BroadcastChannel === "undefined") return null;
		return (name) => new BroadcastChannel(name);
	}
	function defaultLocks() {
		const nav = typeof navigator !== "undefined" ? navigator : null;
		return nav && nav.locks ? nav.locks : null;
	}
	function defaultSession() {
		try {
			return typeof sessionStorage !== "undefined" ? sessionStorage : null;
		} catch {
			return null;
		}
	}
	function defaultLocal() {
		try {
			return typeof localStorage !== "undefined" ? localStorage : null;
		} catch {
			return null;
		}
	}
	function readIdList(storage, key) {
		try {
			const raw = storage ? storage.getItem(key) : null;
			const list = raw ? JSON.parse(raw) : [];
			return Array.isArray(list) ? list.filter((id) => typeof id === "string") : [];
		} catch {
			return [];
		}
	}
	function createPageIdentity(env) {
		const names = env.names;
		if (!names) throw new Error("recovery storage needs a signed-in scope");
		const random = env.random || globalThis.crypto;
		const session = env.sessionStorage === void 0 ? defaultSession() : env.sessionStorage;
		const local = env.localStorage === void 0 ? defaultLocal() : env.localStorage;
		const locks = env.locks === void 0 ? defaultLocks() : env.locks;
		const makeChannel = env.createChannel === void 0 ? defaultChannel() : env.createChannel;
		const later = env.setTimeout || ((fn, ms) => setTimeout(fn, ms));
		const waitMs = env.claimWaitMs ?? 250;
		const pageInstanceId = mintId("p", random);
		const pageEvents = env.window === void 0 ? typeof window !== "undefined" ? window : null : env.window;
		let channel = null;
		try {
			channel = makeChannel ? makeChannel(names.channel) : null;
		} catch {
			channel = null;
		}
		const releases = [];
		let disposed = false;
		let settled = null;
		let claimPromise = null;
		let pending = null;
		let lineageResponder = null;
		let contestListener = null;
		const answers = /* @__PURE__ */ new Map();
		function post(message) {
			if (!channel || disposed) return;
			try {
				channel.postMessage(message);
			} catch {}
		}
		function onClaim(m) {
			if (m.from === pageInstanceId) return;
			if (settled && settled.runtimeId === m.rid) {
				post({
					t: "taken",
					rid: m.rid,
					to: m.from
				});
				if (contestListener) contestListener();
				return;
			}
			if (!pending || pending.rid !== m.rid) return;
			if (m.from < pageInstanceId) pending.lost = true;
			else post({
				t: "taken",
				rid: m.rid,
				to: m.from
			});
		}
		function onTaken(m) {
			if (m.to !== pageInstanceId) return;
			if (pending && pending.rid === m.rid) {
				pending.lost = true;
				return;
			}
			if (settled && settled.runtimeId === m.rid && settled.verified === "channel") settled = {
				runtimeId: m.rid,
				verified: "unverified"
			};
		}
		function onAnswer(m) {
			const resolve = answers.get(m.q);
			if (resolve) resolve();
		}
		const handlers = {
			claim: onClaim,
			taken: onTaken,
			"runtime?": (m) => {
				if (settled && settled.runtimeId === m.rid) post({
					t: "runtime!",
					rid: m.rid,
					q: m.q
				});
			},
			"page?": (m) => {
				if (m.page === pageInstanceId) post({
					t: "page!",
					page: m.page,
					q: m.q
				});
			},
			"lineage?": (m) => {
				if (lineageResponder && lineageResponder(m.draftId)) post({
					t: "lineage!",
					draftId: m.draftId,
					q: m.q
				});
			},
			unreserved: (m) => {
				if (m.page === pageInstanceId && contestListener) contestListener();
			},
			"runtime!": onAnswer,
			"page!": onAnswer,
			"lineage!": onAnswer
		};
		if (channel) channel.onmessage = (event) => {
			const m = event && event.data;
			if (!m || typeof m !== "object" || disposed) return;
			const handle = Object.prototype.hasOwnProperty.call(handlers, m.t) ? handlers[m.t] : null;
			if (handle) handle(m);
		};
		function sleep(ms) {
			return new Promise((resolve) => {
				later(resolve, ms);
			});
		}
		/** Holds a lock until dispose. Resolves true if granted, false if another holder has it. */
		function hold(name) {
			if (!locks) return Promise.resolve(false);
			return new Promise((resolve) => {
				let answered = false;
				locks.request(name, { ifAvailable: true }, (lock) => {
					answered = true;
					if (!lock) {
						resolve(false);
						return;
					}
					resolve(true);
					return new Promise((release) => {
						if (disposed) release();
						else releases.push(release);
					});
				}).catch(() => {
					if (!answered) resolve(false);
				});
			});
		}
		function persist(runtimeId) {
			try {
				if (session) session.setItem(names.runtimeSessionKey, runtimeId);
			} catch {}
		}
		function initialCandidate() {
			try {
				const stored = session ? session.getItem(names.runtimeSessionKey) : null;
				if (stored && /^r-[0-9a-f]{32}$/.test(stored)) return stored;
			} catch {}
			return mintId("r", random);
		}
		async function claimWithLocks(candidate) {
			await hold(names.pageLockPrefix + pageInstanceId);
			for (let round = 0; round < MAX_CLAIM_ROUNDS; round++) {
				if (await hold(names.runtimeLockPrefix + candidate)) return {
					runtimeId: candidate,
					verified: "lock"
				};
				candidate = mintId("r", random);
			}
			return {
				runtimeId: candidate,
				verified: "unverified"
			};
		}
		async function claimWithChannel(candidate) {
			for (let round = 0; round < MAX_CLAIM_ROUNDS; round++) {
				pending = {
					rid: candidate,
					lost: false
				};
				post({
					t: "claim",
					rid: candidate,
					from: pageInstanceId
				});
				await sleep(waitMs);
				const lost = pending.lost;
				pending = null;
				if (!lost) return {
					runtimeId: candidate,
					verified: "channel"
				};
				candidate = mintId("r", random);
			}
			return {
				runtimeId: candidate,
				verified: "unverified"
			};
		}
		function closedPages() {
			return readIdList(session, names.closedPagesSessionKey);
		}
		function writeClosedPages(list) {
			try {
				if (session) session.setItem(names.closedPagesSessionKey, JSON.stringify(list.slice(-8)));
			} catch {}
		}
		function onPageHide(event) {
			if (event.persisted) return;
			writeClosedPages([...closedPages().filter((id) => id !== pageInstanceId), pageInstanceId]);
			try {
				if (!local) return;
				local.setItem(names.closedPagePrefix + pageInstanceId, String(Date.now()));
			} catch {
				return;
			}
			pruneClosedPages();
		}
		/** Keeps the newest close records; an older page's drafts are then offered for review. */
		function pruneClosedPages() {
			try {
				if (!local || !local.key || !local.removeItem || typeof local.length !== "number") return;
				const records = [];
				for (let i = 0; i < local.length; i++) {
					const key = local.key(i);
					if (!key || !key.startsWith(names.closedPagePrefix)) continue;
					records.push({
						key,
						at: Number(local.getItem(key)) || 0
					});
				}
				if (records.length <= 64) return;
				records.sort((a, b) => a.at - b.at);
				for (const record of records.slice(0, records.length - 64)) local.removeItem(record.key);
			} catch {}
		}
		function onPageShow(event) {
			if (event.persisted) writeClosedPages(closedPages().filter((id) => id !== pageInstanceId));
		}
		function claim() {
			if (claimPromise) return claimPromise;
			if (pageEvents) {
				pageEvents.addEventListener("pagehide", onPageHide);
				pageEvents.addEventListener("pageshow", onPageShow);
			}
			const candidate = initialCandidate();
			claimPromise = (locks ? claimWithLocks(candidate) : channel ? claimWithChannel(candidate) : Promise.resolve({
				runtimeId: candidate,
				verified: "unverified"
			})).then((result) => {
				settled = result;
				persist(result.runtimeId);
				return result;
			});
			return claimPromise;
		}
		async function heldLockNames() {
			if (!locks || !locks.query) return null;
			try {
				const snapshot = await locks.query();
				return new Set((snapshot.held || []).map((lock) => String(lock.name || "")));
			} catch {
				return null;
			}
		}
		function ask(message) {
			if (!channel || disposed) return Promise.resolve(null);
			return new Promise((resolve) => {
				let done = false;
				answers.set(message.q, () => {
					if (done) return;
					done = true;
					answers.delete(message.q);
					resolve(true);
				});
				post(message);
				later(() => {
					if (done) return;
					done = true;
					answers.delete(message.q);
					resolve(false);
				}, waitMs);
			});
		}
		function queryId() {
			return mintId("d", random).slice(2);
		}
		return {
			pageInstanceId,
			names,
			claim,
			current: () => settled,
			async isPageAlive(id) {
				if (id === pageInstanceId) return true;
				const held = await heldLockNames();
				if (held) return held.has(names.pageLockPrefix + id);
				return ask({
					t: "page?",
					page: id,
					q: queryId()
				});
			},
			async isPageGone(id) {
				if (id === pageInstanceId) return false;
				const held = await heldLockNames();
				return held ? !held.has(names.pageLockPrefix + id) : false;
			},
			async isRuntimeAlive(id) {
				if (settled && settled.runtimeId === id) return true;
				const held = await heldLockNames();
				if (held) return held.has(names.runtimeLockPrefix + id);
				return ask({
					t: "runtime?",
					rid: id,
					q: queryId()
				});
			},
			isLineageLiveElsewhere(draftId) {
				return ask({
					t: "lineage?",
					draftId,
					q: queryId()
				});
			},
			setLineageResponder(responder) {
				lineageResponder = responder;
			},
			setContestListener(listener) {
				contestListener = listener;
			},
			announceReservationRemoved(page) {
				post({
					t: "unreserved",
					page
				});
			},
			wasClosedInThisTab(id) {
				return id !== pageInstanceId && closedPages().includes(id);
			},
			wasPageClosed(id) {
				if (id === pageInstanceId || !local) return false;
				try {
					return local.getItem(names.closedPagePrefix + id) !== null;
				} catch {
					return false;
				}
			},
			dispose() {
				if (disposed) return;
				disposed = true;
				if (pageEvents) {
					pageEvents.removeEventListener("pagehide", onPageHide);
					pageEvents.removeEventListener("pageshow", onPageShow);
				}
				while (releases.length) {
					const release = releases.pop();
					if (release) release();
				}
				answers.clear();
				if (channel) {
					channel.onmessage = null;
					try {
						channel.close();
					} catch {}
				}
			}
		};
	}
	//#endregion
	//#region src/lifecycle/editor-recovery/lineage.ts
	function isAdoptable(lineageClass) {
		return lineageClass === "same-runtime-orphan" || lineageClass === "dead-runtime";
	}
	async function classifyLineage(record, probe, askingSession = null) {
		const local = probe.localOwner(record.draftId);
		if (local !== null) return local === askingSession ? "self-live" : "other-live";
		const { identity } = probe;
		const owner = record.owner;
		if (owner.pageInstanceId === identity.pageInstanceId) return "same-runtime-orphan";
		const claim = identity.current();
		if (claim && owner.runtimeId && owner.runtimeId === claim.runtimeId) {
			if (claim.verified === "lock") return "same-runtime-orphan";
			if (await identity.isLineageLiveElsewhere(record.draftId) === true) return "other-live";
			return claim.verified === "channel" && identity.wasClosedInThisTab(owner.pageInstanceId) ? "same-runtime-orphan" : "unknown";
		}
		const gone = (identity.wasPageClosed ? identity.wasPageClosed(owner.pageInstanceId) : false) || (identity.isPageGone ? await identity.isPageGone(owner.pageInstanceId) : false);
		const [liveElsewhere, pageAlive, runtimeAlive] = await Promise.all([
			identity.isLineageLiveElsewhere(record.draftId),
			gone ? identity.isPageAlive(owner.pageInstanceId) : Promise.resolve(null),
			gone && owner.runtimeId ? identity.isRuntimeAlive(owner.runtimeId) : Promise.resolve(false)
		]);
		if (liveElsewhere === true) return "other-live";
		if (!gone || pageAlive !== false || runtimeAlive !== false) return "unknown";
		return "dead-runtime";
	}
	//#endregion
	//#region src/lifecycle/editor-recovery/writer.ts
	/**
	* Page-level writer registry: one coalescing writer per editor lineage.
	*
	* INV-DRAFT-1: while a writer reports a draft, its newest generation is either
	* committed to recovery storage, held by the emergency entry plan (only while
	* localStorage has shown it can take the planned payload), or covered
	* by an armed leave guard. A generation in none of those reads `unprotected`.
	*
	* Coalescer, per lineage: at most one write in flight; a newer generation
	* while one is in flight replaces the pending one, and the newest is written
	* when the write completes; older or equal generations are ignored.
	* - Ordinary bodies (<= 256 Ki chars): first write after 300 ms idle, at most
	*   1 s after the first unrecorded change.
	* - Large bodies: the write starts on the next task and the leave guard is
	*   armed from the first uncommitted generation until that generation commits.
	*
	* The registry, not TabContext timers, owns the writes, so releasing a writer
	* finishes its last write instead of cancelling it. A writer that finds its
	* lineage owned by another page or removed moves its newest generation to a
	* fresh lineage; it never overwrites or drops. `beforeunload` is registered
	* only while some writer needs the guard, and `pagehide` /
	* `visibilitychange` only while some writer is pending.
	*/
	/** Worst-case reservation char on every engine (see canHold). */
	var RESERVATION_FILLER = "ā";
	/** Most slack a reservation takes beyond the payload it covers. */
	var RESERVATION_STEP_CHARS = 65536;
	var defaultScheduler = {
		set: (fn, ms) => setTimeout(fn, ms),
		clear: (handle) => clearTimeout(handle)
	};
	function codeOf(error) {
		return error instanceof RecoveryStoreError ? error.code : "unknown";
	}
	function createWriterRegistry(options) {
		const { store, identity } = options;
		const scheduler = options.scheduler || defaultScheduler;
		const now = options.now || Date.now;
		const random = options.random || globalThis.crypto;
		const win = options.window === void 0 ? typeof window !== "undefined" ? window : null : options.window;
		const doc = options.document === void 0 ? typeof document !== "undefined" ? document : null : options.document;
		const storage = options.emergencyStorage === void 0 ? defaultLocalStorage$1() : options.emergencyStorage;
		const emit = options.onEvent || (() => {});
		const names = identity.names;
		const supersededPrefix = names.supersededPrefix;
		const emergencyKey = emergencyKeyOf(names, identity.pageInstanceId);
		const reservationKey = reservationKeyOf(names, identity.pageInstanceId);
		const live = /* @__PURE__ */ new Set();
		/** Writers whose body the budget plan puts in the emergency entry. */
		let planned = /* @__PURE__ */ new Set();
		/** `planned`, but only while emergency storage has shown it can keep those bodies; otherwise empty. */
		let held = /* @__PURE__ */ new Set();
		/**
		* Chars of filler this page keeps written under its reservation key while
		* something is pending. A body counts as held only when the planned payload
		* fits within it. The quota stays occupied, so no other write, from this
		* page or another, can take it; at hide the reservation is swapped for the
		* real payload. Blocked, missing or too-full storage leaves the leave guard
		* armed before unload instead of discovering the failure at pagehide.
		*/
		let reservedChars = 0;
		/**
		* Set at pagehide: the page is going away (or into the back/forward cache),
		* so it does not reserve again after its emergency write; a reservation
		* left by a closed page would only hold quota until a later page cleans it.
		* Cleared at pageshow, which re-plans.
		*/
		let hiding = false;
		/** Set when a real emergency write could not keep every planned body; cleared by a full write. */
		let distrusted = false;
		let guardOn = false;
		let emergencyOn = false;
		let emergencyWritten = false;
		/** Draft ids in the emergency key as last written. */
		let emergencyIds = /* @__PURE__ */ new Set();
		/** Discarded lineages a stale emergency key still lists; each has a tombstone record. */
		const tombstoned = /* @__PURE__ */ new Set();
		let refreshingEmergency = false;
		let disposed = false;
		/** Retiring: text typed so far is finished, nothing new is taken. */
		let frozen = false;
		/** A retirement waiting for every pending write to commit. */
		let drained = null;
		/**
		* Stored generations known to be superseded by the server, still waiting
		* for their removal: never adopted, so never restored. A removal storage
		* refused is also written to localStorage, so a later page honours it and
		* finishes the removal.
		*/
		const superseded = /* @__PURE__ */ new Map();
		/**
		* Superseded generations whose mark localStorage refused: only this page
		* knows to suppress them, so leaving stays guarded until the delete lands.
		*/
		const unrecorded = /* @__PURE__ */ new Set();
		function parseSupersededMark(draftId, raw) {
			if (!raw) return null;
			try {
				const value = JSON.parse(raw);
				if (!value || typeof value !== "object") return null;
				const { pageInstanceId, generation } = value;
				if (typeof pageInstanceId !== "string" || typeof generation !== "number" || !Number.isSafeInteger(generation) || generation <= 0) return null;
				return {
					draftId,
					pageInstanceId,
					generation
				};
			} catch {
				return null;
			}
		}
		function readSupersededMark(draftId) {
			try {
				return storage ? parseSupersededMark(draftId, storage.getItem(supersededPrefix + draftId)) : null;
			} catch {
				return null;
			}
		}
		function readSupersededMarks() {
			const marks = [];
			try {
				if (!storage) return marks;
				const ids = [];
				for (let i = 0; i < storage.length; i++) {
					const key = storage.key(i);
					if (key && key.startsWith(supersededPrefix)) ids.push(key.slice(supersededPrefix.length));
				}
				for (const id of ids) {
					const mark = readSupersededMark(id);
					if (mark) marks.push(mark);
				}
			} catch {}
			return marks;
		}
		function markSuperseded(mark) {
			superseded.set(mark.draftId, Math.max(mark.generation, superseded.get(mark.draftId) || 0));
			const prior = readSupersededMark(mark.draftId);
			if (prior && prior.generation >= mark.generation) return;
			try {
				if (!storage) throw new Error("no localStorage");
				storage.setItem(supersededPrefix + mark.draftId, JSON.stringify({
					pageInstanceId: mark.pageInstanceId,
					generation: mark.generation
				}));
			} catch {
				unrecorded.add(mark.draftId);
				changed();
			}
		}
		function unmarkSuperseded(draftId, generation) {
			if ((superseded.get(draftId) || 0) <= generation) superseded.delete(draftId);
			if (unrecorded.delete(draftId)) changed();
			const mark = readSupersededMark(draftId);
			if (!mark || mark.generation > generation) return;
			try {
				if (storage) storage.removeItem(supersededPrefix + draftId);
			} catch {}
		}
		/**
		* Removes one superseded generation by exact owner, generation and status.
		* Resolves true when it is gone or no longer that generation (someone else
		* owns it now); throws while storage refuses.
		*/
		async function removeSupersededRecord(mark) {
			superseded.set(mark.draftId, Math.max(mark.generation, superseded.get(mark.draftId) || 0));
			await store.discard(mark.draftId, void 0, {
				pageInstanceId: mark.pageInstanceId,
				generation: mark.generation,
				status: "active"
			});
			unmarkSuperseded(mark.draftId, mark.generation);
		}
		/** Startup: finish the removals an earlier page could not, retrying with backoff. */
		function sweepSuperseded(marks, delay) {
			if (!marks.length || disposed) return;
			Promise.all(marks.map((mark) => removeSupersededRecord(mark).then(() => null, () => mark))).then((results) => {
				const left = results.filter((m) => m !== null);
				if (!left.length || disposed) return;
				const next = delay ? Math.min(delay * 2, RETRY_MAX_MS) : RETRY_FIRST_MS;
				scheduler.set(() => sweepSuperseded(left, next), next);
			}).catch(() => void 0);
		}
		function onBeforeUnload(event) {
			event.preventDefault();
			event.returnValue = "";
		}
		function onPageHide() {
			hiding = true;
			writeEmergencyNow();
		}
		function onPageShow() {
			if (!hiding) return;
			hiding = false;
			changed();
		}
		function onVisibility() {
			if (doc && doc.visibilityState === "hidden") writeEmergencyNow();
			else onResume();
		}
		/**
		* Back from hidden or frozen, or another page claimed this runtime: write
		* the reservation again before counting on it. Without Web Locks a page
		* that claimed this runtime while this one could not answer may have
		* removed it, taking a frozen or busy page for a closed one.
		*/
		function onResume() {
			if (hiding || reservedChars === 0) return;
			reservedChars = 0;
			changed();
		}
		identity.setLineageResponder((draftId) => ownerOf(draftId) !== null);
		if (identity.setContestListener) identity.setContestListener(onResume);
		function ownerOf(draftId) {
			for (const w of live) if (w.currentDraftId() === draftId) return w.sessionKey;
			return null;
		}
		function pendingWriters() {
			return [...live].filter((w) => w.pendingBody() !== null);
		}
		/** Recomputes the emergency plan, the listeners and the stored key after any writer state change. */
		function changed() {
			if (disposed) return;
			const pending = pendingWriters();
			if (drained && !pending.length) drained();
			planHeld(pending);
			setGuard(pending.some((w) => w.needsLeaveGuard()) || unrecorded.size > 0);
			setEmergencyListeners(pending.length > 0);
			refreshEmergencyKey(pending);
		}
		function planHeld(pending) {
			const plan = planEmergency(pending.map((w) => w.pendingBody().body.length));
			planned = new Set(pending.filter((_, i) => plan.has(i)));
			held = pending.length && !hiding && canHold(payloadChars(pending.length)) ? planned : /* @__PURE__ */ new Set();
		}
		function setGuard(needGuard) {
			if (win && needGuard !== guardOn) {
				if (needGuard) win.addEventListener("beforeunload", onBeforeUnload);
				else win.removeEventListener("beforeunload", onBeforeUnload);
			}
			guardOn = needGuard;
		}
		function setEmergencyListeners(needEmergency) {
			if (needEmergency !== emergencyOn) {
				const method = needEmergency ? "addEventListener" : "removeEventListener";
				if (win) {
					win[method]("pagehide", onPageHide);
					win[method]("pageshow", onPageShow);
				}
				if (doc) {
					doc[method]("visibilitychange", onVisibility);
					doc[method]("resume", onResume);
				}
				if (!needEmergency) {
					releaseReservation();
					hiding = false;
				}
			}
			emergencyOn = needEmergency;
		}
		/**
		* An entry whose writer committed, was discarded or moved lineage must not
		* outlive it: merged after a crash it could recreate a discarded draft.
		*/
		function refreshEmergencyKey(pending) {
			if (!emergencyWritten || !storage || refreshingEmergency) return;
			if (!pending.length) {
				removeEmergencyKey();
				return;
			}
			const current = new Set(pending.map((w) => w.currentDraftId()));
			if (![...emergencyIds].some((id) => !current.has(id))) return;
			refreshingEmergency = true;
			try {
				if (writeEmergencyNow() === "failed") removeEmergencyKey();
			} finally {
				refreshingEmergency = false;
			}
		}
		function removeEmergencyKey() {
			if (!storage) return;
			try {
				storage.removeItem(emergencyKey);
				emergencyWritten = false;
				emergencyIds = /* @__PURE__ */ new Set();
				releaseTombstones();
			} catch {
				const claim = identity.current();
				const empty = {
					v: 1,
					pageInstanceId: identity.pageInstanceId,
					runtimeId: claim ? claim.runtimeId : null,
					at: now(),
					entries: []
				};
				if (writeEmergency(storage, emergencyKey, empty) !== "failed") {
					emergencyWritten = false;
					emergencyIds = /* @__PURE__ */ new Set();
					releaseTombstones();
				}
			}
		}
		/** Drops the tombstones whose lineage the emergency key no longer lists. */
		function releaseTombstones() {
			for (const draftId of [...tombstoned]) {
				if (emergencyWritten && emergencyIds.has(draftId)) continue;
				tombstoned.delete(draftId);
				store.clearTombstone(draftId, identity.pageInstanceId).catch(() => {});
			}
		}
		/**
		* Serialized size of the planned payload: each planned body with its exact
		* JSON escaping (counted once per generation), plus per-entry metadata.
		*/
		function payloadChars(entries) {
			let chars = entries * EMERGENCY_ENTRY_OVERHEAD_CHARS;
			for (const w of planned) chars += w.serializedBodyChars();
			return chars;
		}
		/**
		* Whether this page's reservation covers a payload of `chars`. Beyond what
		* is reserved it grows the reservation by up to RESERVATION_STEP_CHARS of
		* slack (then to the exact size), so typing does not rewrite it on every
		* keystroke while a large note never holds twice its size of other writes'
		* quota. Only a page whose runtime claim has settled reserves: its page
		* lock is held by then, so no live page's reservation reads as gone. The
		* value is tagged with the runtime id, so without Web Locks a later page
		* that claims this runtime removes it once this page is gone.
		*/
		function canHold(chars) {
			const claim = identity.current();
			if (!storage || distrusted || !claim) return false;
			if (chars <= reservedChars) return true;
			for (const size of [chars + Math.min(chars, RESERVATION_STEP_CHARS), chars]) try {
				storage.setItem(reservationKey, reservationValue(claim.runtimeId, RESERVATION_FILLER.repeat(size)));
				reservedChars = size;
				return true;
			} catch {}
			return false;
		}
		function releaseReservation() {
			if (!storage || reservedChars === 0) return;
			reservedChars = 0;
			try {
				storage.removeItem(reservationKey);
			} catch {}
		}
		function noteEmergencyResult(result) {
			if (result === "written") distrusted = false;
			else distrusted = true;
			changed();
		}
		function writeEmergencyNow() {
			const pending = pendingWriters();
			if (!pending.length) return "nothing-pending";
			if (!storage) {
				noteEmergencyResult("unavailable");
				return "unavailable";
			}
			const claim = identity.current();
			const payload = {
				v: 1,
				pageInstanceId: identity.pageInstanceId,
				runtimeId: claim ? claim.runtimeId : null,
				at: now(),
				entries: pending.map((w) => w.emergencyEntry(planned.has(w)))
			};
			releaseReservation();
			const result = writeEmergency(storage, emergencyKey, payload);
			if (result !== "failed") {
				emergencyWritten = true;
				emergencyIds = new Set(payload.entries.map((e) => e.draftId));
				releaseTombstones();
			}
			noteEmergencyResult(result);
			return result;
		}
		class WriterImpl {
			sessionKey;
			kind;
			entityType;
			entityId;
			paneId;
			base;
			pipelineState = null;
			/** Base or pipeline changed since the stored record last carried them. */
			metaDirty = false;
			lineageId = null;
			lineageCreatedAt = 0;
			/** A record for `lineageId` exists that this page owns (created or adopted). */
			lineageStored = false;
			committed = 0;
			lastSeen = 0;
			latest = null;
			status = "clean";
			inFlight = null;
			writeRequested = false;
			failedGeneration = 0;
			idleTimer = null;
			maxWaitTimer = null;
			nextTaskTimer = null;
			retryTimer = null;
			retryDelay = 0;
			/** Retries removing a stored generation the server already superseded. */
			cleanupTimer = null;
			released = false;
			cleared = /* @__PURE__ */ new Set();
			constructor(input) {
				this.sessionKey = input.sessionKey || mintId("d", random).replace(/^d-/, "s-");
				this.kind = input.kind;
				this.entityType = input.entityType;
				this.entityId = input.entityId;
				this.paneId = input.paneId;
				this.base = input.base ? { ...input.base } : { ...UNKNOWN_BASE };
			}
			adoptRecord(record) {
				this.lineageId = record.draftId;
				this.lineageCreatedAt = record.createdAt;
				this.lineageStored = true;
				this.committed = record.generation;
				this.lastSeen = record.generation;
				this.base = { ...record.base };
				this.pipelineState = record.pipeline;
				this.status = "protected";
			}
			currentDraftId() {
				return this.lineageId;
			}
			pendingBody() {
				return this.latest;
			}
			escapeCache = null;
			/** The pending body's length once JSON-serialized; recounted only when the generation changes. */
			serializedBodyChars() {
				const pending = this.latest;
				if (!pending) return 0;
				if (!this.escapeCache || this.escapeCache.generation !== pending.generation) this.escapeCache = {
					generation: pending.generation,
					chars: pending.body.length + 2 + jsonEscapeExtra(pending.body)
				};
				return this.escapeCache.chars;
			}
			draftId() {
				return this.lineageId;
			}
			state() {
				return this.status;
			}
			committedGeneration() {
				return this.committed;
			}
			heldByEmergency() {
				return held.has(this);
			}
			needsLeaveGuard() {
				if (!this.latest) return false;
				if (this.status === "unprotected") return true;
				return !held.has(this);
			}
			setBase(base) {
				this.base = { ...base };
				this.metaChanged();
			}
			setPipeline(pipeline) {
				this.pipelineState = pipeline;
				this.metaChanged();
			}
			/**
			* A pending or in-flight write carries the new metadata; otherwise a
			* stored lineage gets a metadata-only update through the same one-at-a-time
			* slot, so it never races a body write.
			*/
			metaChanged() {
				this.metaDirty = true;
				if (this.released || disposed || !this.lineageStored || !this.lineageId) return;
				if (this.inFlight || this.latest) return;
				this.startMeta();
			}
			startMeta() {
				const draftId = this.lineageId;
				if (!draftId || !this.lineageStored) return;
				this.metaDirty = false;
				let failed = false;
				const attempt = store.updateLineage({
					draftId,
					pageInstanceId: identity.pageInstanceId,
					base: this.base,
					pipeline: this.pipelineState
				}).then(() => {}, () => {
					failed = true;
					if (this.lineageId === draftId) this.metaDirty = true;
				}).finally(() => {
					this.inFlight = null;
					if (this.writeRequested && this.latest) {
						this.writeRequested = false;
						this.startWrite();
					} else if (this.metaDirty && !failed && !this.latest) this.metaChanged();
				});
				this.inFlight = attempt;
			}
			setPane(paneId) {
				this.paneId = paneId;
			}
			owner() {
				const claim = identity.current();
				return {
					runtimeId: claim ? claim.runtimeId : null,
					pageInstanceId: identity.pageInstanceId,
					paneId: this.paneId,
					claimedAt: now()
				};
			}
			startLineage() {
				this.lineageId = mintId("d", random);
				this.lineageCreatedAt = now();
				this.lineageStored = false;
				this.committed = 0;
			}
			clearTimers() {
				for (const handle of [
					this.idleTimer,
					this.maxWaitTimer,
					this.nextTaskTimer
				]) if (handle !== null) scheduler.clear(handle);
				this.idleTimer = null;
				this.maxWaitTimer = null;
				this.nextTaskTimer = null;
			}
			clearRetry() {
				if (this.retryTimer !== null) scheduler.clear(this.retryTimer);
				this.retryTimer = null;
			}
			edit(generation, body) {
				if (this.released || disposed || frozen) return;
				if (!(generation > this.lastSeen)) return;
				this.lastSeen = generation;
				if (!this.lineageId) this.startLineage();
				this.latest = {
					generation,
					body
				};
				if (this.status !== "unprotected") this.status = "pending";
				if (body.length > 262144) {
					if (this.idleTimer !== null) scheduler.clear(this.idleTimer);
					if (this.maxWaitTimer !== null) scheduler.clear(this.maxWaitTimer);
					this.idleTimer = null;
					this.maxWaitTimer = null;
					if (this.inFlight) this.writeRequested = true;
					else if (this.nextTaskTimer === null) this.nextTaskTimer = scheduler.set(() => this.startWrite(), 0);
				} else {
					if (this.idleTimer !== null) scheduler.clear(this.idleTimer);
					this.idleTimer = scheduler.set(() => this.startWrite(), 300);
					if (this.maxWaitTimer === null) this.maxWaitTimer = scheduler.set(() => this.startWrite(), MAX_WRITE_WAIT_MS);
				}
				changed();
			}
			startWrite() {
				this.clearTimers();
				if (this.inFlight) {
					this.writeRequested = true;
					return;
				}
				const pending = this.latest;
				const draftId = this.lineageId;
				if (!pending || !draftId) return;
				const create = this.lineageStored ? void 0 : {
					kind: this.kind,
					entityType: this.entityType,
					entityId: this.entityId,
					owner: this.owner(),
					base: this.base
				};
				this.metaDirty = false;
				const attempt = store.writeGeneration({
					draftId,
					pageInstanceId: identity.pageInstanceId,
					generation: pending.generation,
					body: pending.body,
					paneId: this.paneId,
					base: this.base,
					pipeline: this.pipelineState,
					create
				}).then((outcome) => {
					if (this.lineageId !== draftId) {
						this.writeRequested = true;
						return;
					}
					if (outcome === "ok") {
						this.lineageStored = true;
						this.committed = pending.generation;
						this.failedGeneration = 0;
						this.retryDelay = 0;
						this.clearRetry();
						if (this.latest && this.latest.generation === pending.generation) {
							this.latest = null;
							this.status = "protected";
							emit({
								type: "protected",
								sessionKey: this.sessionKey,
								draftId,
								generation: pending.generation
							});
						} else {
							this.status = "pending";
							if (this.latest && this.latest.body.length > 262144) this.writeRequested = true;
						}
						return;
					}
					this.startLineage();
					if (!this.cleared.has(draftId)) emit({
						type: "ownership-lost",
						sessionKey: this.sessionKey,
						oldDraftId: draftId,
						newDraftId: this.lineageId,
						reason: outcome
					});
					this.writeRequested = true;
				}, (error) => {
					if (this.lineageId !== draftId) {
						this.writeRequested = true;
						return;
					}
					this.failedGeneration = pending.generation;
					if (this.latest) this.status = "unprotected";
					emit({
						type: "unprotected",
						sessionKey: this.sessionKey,
						draftId,
						code: codeOf(error)
					});
					this.scheduleRetry();
				}).finally(() => {
					this.inFlight = null;
					const again = this.writeRequested && this.latest !== null;
					this.writeRequested = false;
					if (again) this.startWrite();
					else if (this.metaDirty && !this.latest) this.metaChanged();
					changed();
					this.leaveIfDone();
				});
				this.inFlight = attempt;
			}
			scheduleRetry() {
				if (disposed) return;
				this.clearRetry();
				this.retryDelay = this.retryDelay ? Math.min(this.retryDelay * 2, RETRY_MAX_MS) : RETRY_FIRST_MS;
				this.retryTimer = scheduler.set(() => {
					this.retryTimer = null;
					this.startWrite();
				}, this.retryDelay);
			}
			async flush() {
				for (let round = 0; round < 16; round++) {
					if (this.inFlight) {
						await this.inFlight;
						continue;
					}
					if (!this.latest) return;
					if (this.status === "unprotected" && this.failedGeneration === this.latest.generation && round > 0) return;
					this.startWrite();
					if (!this.inFlight) return;
				}
			}
			async release() {
				if (this.released) return;
				await this.flush();
				this.released = true;
				this.leaveIfDone();
			}
			leaveIfDone() {
				if (!this.released || this.latest) return;
				this.clearTimers();
				this.clearRetry();
				live.delete(this);
				changed();
			}
			async acknowledged(generation, body) {
				if (this.latest && this.latest.generation <= generation) await this.flush();
				else if (this.inFlight) await this.inFlight;
				const draftId = this.lineageId;
				if (!draftId) return "none";
				let outcome;
				try {
					outcome = await store.deleteIfAcknowledged(draftId, generation, body);
				} catch (error) {
					if (!this.ackedUncommitted(draftId, generation, body)) throw error;
					outcome = "kept";
				}
				if (outcome !== "deleted" && this.ackedUncommitted(draftId, generation, body)) {
					const stored = this.lineageStored ? this.committed : 0;
					this.clearTimers();
					this.clearRetry();
					this.latest = null;
					this.cleared.add(draftId);
					this.lineageId = null;
					this.lineageStored = false;
					this.committed = 0;
					this.failedGeneration = 0;
					this.retryDelay = 0;
					this.status = "clean";
					changed();
					if (stored) await this.removeSuperseded(draftId, stored, 0);
					else await this.tombstoneIfListed(draftId);
					return "deleted";
				}
				if (outcome === "deleted" && this.lineageId === draftId) {
					this.cleared.add(draftId);
					if (this.latest) this.startLineage();
					else {
						this.lineageId = null;
						this.lineageStored = false;
						this.committed = 0;
						this.status = "clean";
					}
					changed();
				}
				if (outcome === "deleted") await this.tombstoneIfListed(draftId);
				return outcome;
			}
			/**
			* Removes this page's stored older generation once the server holds a newer
			* one. A store that refuses is retried in the background with backoff,
			* without the leave guard: the text is already on the server.
			*/
			async removeSuperseded(draftId, generation, delay) {
				const mark = {
					draftId,
					pageInstanceId: identity.pageInstanceId,
					generation
				};
				if (delay === 0) markSuperseded(mark);
				try {
					await removeSupersededRecord(mark);
				} catch {
					if (disposed) return;
					const next = delay ? Math.min(delay * 2, RETRY_MAX_MS) : RETRY_FIRST_MS;
					this.cleanupTimer = scheduler.set(() => {
						this.cleanupTimer = null;
						this.removeSuperseded(draftId, generation, next).catch(() => void 0);
					}, next);
					return;
				}
				await this.tombstoneIfListed(draftId);
			}
			/** The newest generation, never committed to recovery storage, is exactly what was acknowledged. */
			ackedUncommitted(draftId, generation, body) {
				return this.lineageId === draftId && !!this.latest && this.latest.generation === generation && this.latest.body === body && !this.inFlight;
			}
			async discard() {
				this.clearTimers();
				this.clearRetry();
				this.latest = null;
				const draftId = this.lineageId;
				if (draftId) this.cleared.add(draftId);
				this.lineageId = null;
				this.lineageStored = false;
				this.committed = 0;
				this.failedGeneration = 0;
				this.retryDelay = 0;
				this.status = "clean";
				changed();
				if (this.inFlight) await this.inFlight;
				if (!this.latest && this.status !== "clean") {
					this.status = "clean";
					changed();
				}
				if (draftId && !await this.tombstoneIfListed(draftId)) await store.discard(draftId);
			}
			/**
			* A key that could be neither removed nor emptied still lists this retired
			* lineage: leave a tombstone, so no later page recreates the draft from it.
			*/
			async tombstoneIfListed(draftId) {
				if (!emergencyWritten || !emergencyIds.has(draftId)) return false;
				tombstoned.add(draftId);
				await store.discard(draftId, {
					kind: this.kind,
					entityType: this.entityType,
					entityId: this.entityId,
					generation: this.lastSeen,
					pageInstanceId: identity.pageInstanceId
				});
				releaseTombstones();
				return true;
			}
			emergencyEntry(holdBody) {
				const pending = this.latest;
				const committedGeneration = this.lineageStored ? this.committed : 0;
				const entry = {
					draftId: this.lineageId,
					kind: this.kind,
					entityType: this.entityType,
					entityId: this.entityId,
					generation: pending.generation,
					committedGeneration,
					body: holdBody ? pending.body : null
				};
				const claim = identity.current();
				entry.lineage = {
					createdAt: this.lineageCreatedAt,
					owner: {
						runtimeId: claim ? claim.runtimeId : null,
						pageInstanceId: identity.pageInstanceId,
						paneId: this.paneId
					},
					base: { ...this.base }
				};
				return entry;
			}
			dispose() {
				this.released = true;
				this.clearTimers();
				this.clearRetry();
				if (this.cleanupTimer !== null) scheduler.clear(this.cleanupTimer);
				this.cleanupTimer = null;
			}
		}
		function openWriter(input) {
			const writer = new WriterImpl(input);
			if (frozen || disposed) {
				writer.dispose();
				return writer;
			}
			live.add(writer);
			return writer;
		}
		/** Draft ids with an adoption in progress on this page. */
		const adopting = /* @__PURE__ */ new Set();
		/** Adoptions in progress: a retirement settles them before it disposes. */
		const adoptions = /* @__PURE__ */ new Set();
		let retiring = null;
		async function adopt(record, input) {
			if (disposed || frozen || adopting.has(record.draftId) || ownerOf(record.draftId) !== null) return null;
			if (!superseded.has(record.draftId)) {
				const mark = readSupersededMark(record.draftId);
				if (mark) {
					superseded.set(mark.draftId, mark.generation);
					sweepSuperseded([mark], 0);
				}
			}
			if ((superseded.get(record.draftId) || 0) >= record.generation) return null;
			adopting.add(record.draftId);
			const adoption = adoptReserved(record, input);
			adoptions.add(adoption);
			try {
				return await adoption;
			} finally {
				adopting.delete(record.draftId);
				adoptions.delete(adoption);
			}
		}
		async function adoptReserved(record, input) {
			const writer = new WriterImpl({
				...input,
				kind: record.kind,
				entityType: record.entityType,
				entityId: record.entityId,
				base: record.base
			});
			const claim = identity.current();
			const result = await store.adopt(record.draftId, record.owner.pageInstanceId, {
				runtimeId: claim ? claim.runtimeId : null,
				pageInstanceId: identity.pageInstanceId,
				paneId: input.paneId,
				claimedAt: now()
			}, record.generation);
			if (result.outcome !== "ok" || disposed) return null;
			writer.adoptRecord(result.record);
			live.add(writer);
			if (frozen) return null;
			return writer;
		}
		sweepSuperseded(readSupersededMarks(), 0);
		const registry = {
			openWriter,
			adopt,
			ownerOf,
			writers: () => [...live],
			leaveGuardActive: () => guardOn,
			emergencyListenersActive: () => emergencyOn,
			writeEmergencyNow,
			retire() {
				if (retiring) return retiring;
				if (disposed) return retiring = Promise.resolve();
				frozen = true;
				retiring = (async () => {
					await Promise.all([...adoptions].map((a) => a.catch(() => void 0)));
					try {
						writeEmergencyNow();
					} catch {}
					await Promise.all([...live].map((w) => w.flush().catch(() => void 0)));
					if (pendingWriters().length) await new Promise((resolve) => {
						drained = resolve;
					});
					drained = null;
					registry.dispose();
				})();
				return retiring;
			},
			dispose() {
				for (const w of live) w.dispose();
				live.clear();
				if (win) {
					win.removeEventListener("beforeunload", onBeforeUnload);
					win.removeEventListener("pagehide", onPageHide);
					win.removeEventListener("pageshow", onPageShow);
				}
				releaseReservation();
				if (doc) {
					doc.removeEventListener("visibilitychange", onVisibility);
					doc.removeEventListener("resume", onResume);
				}
				guardOn = false;
				emergencyOn = false;
				disposed = true;
				identity.setLineageResponder(null);
				if (identity.setContestListener) identity.setContestListener(null);
			}
		};
		return registry;
	}
	function defaultLocalStorage$1() {
		try {
			return typeof localStorage !== "undefined" ? localStorage : null;
		} catch {
			return null;
		}
	}
	//#endregion
	//#region src/lifecycle/editor-recovery/runtime.ts
	/**
	* One editor-recovery runtime per page: store, identity and writer registry.
	*
	* `start()` claims the runtime id and then merges emergency entries left by
	* pages that are no longer alive. `scanEmergency()` repeats that merge for a
	* page that is already running, so a tab that closes later is picked up
	* without a reload. Nothing starts at script load; slice 1 has
	* no consumer, so the page holds no lock, channel or listener until a
	* consumer asks for the runtime.
	*/
	function keptFor(lineage, report) {
		return lineage === "unknown" ? report.unknown : report.live;
	}
	function createEditorRecoveryRuntime(options) {
		const names = options.names;
		if (!names) throw new Error("recovery storage needs a signed-in scope");
		const rawStore = createRecoveryStore({
			...options.store,
			name: names.dbName
		});
		const store = retirableStore(rawStore, () => retired);
		const identity = createPageIdentity({
			...options.identity,
			names
		});
		/** Retired: nothing outside the writers' last writes reads or writes this scope. */
		let retired = false;
		/** The writers' last writes are done: nothing reads or writes this scope. */
		let closed = false;
		let retiring = null;
		const rawEmergencyStorage = options.emergencyStorage !== void 0 ? options.emergencyStorage : options.writers?.emergencyStorage ?? defaultLocalStorage();
		const emergencyStorage = rawEmergencyStorage ? retirableStorage(rawEmergencyStorage, () => retired) : null;
		const writerStorage = rawEmergencyStorage ? retirableStorage(rawEmergencyStorage, () => closed) : null;
		const writerListeners = /* @__PURE__ */ new Set();
		const ownEvent = options.writers?.onEvent;
		const writers = createWriterRegistry({
			...options.writers,
			store: rawStore,
			identity,
			emergencyStorage: writerStorage,
			onEvent(event) {
				if (ownEvent) ownEvent(event);
				for (const listener of [...writerListeners]) try {
					listener(event);
				} catch {}
			}
		});
		let started = null;
		let lastScan = Promise.resolve();
		function merge(definiteOnly) {
			const run = lastScan.then(() => runMerge(definiteOnly));
			lastScan = run.catch(() => void 0);
			return run;
		}
		async function runMerge(definiteOnly) {
			if (!emergencyStorage) return [];
			const env = {
				storage: emergencyStorage,
				names,
				store,
				pageInstanceId: identity.pageInstanceId,
				isPageAlive: (id) => identity.isPageAlive(id),
				wasPageClosed: (id) => identity.wasPageClosed(id),
				definiteOnly
			};
			const reports = await mergeEmergencyEntries(env);
			const claim = identity.current();
			const removed = await releaseDeadReservations({
				...env,
				isPageGone: (id) => identity.isPageGone(id),
				verifiedRuntimeId: !definiteOnly && claim && claim.verified !== "unverified" ? claim.runtimeId : null
			});
			for (const key of removed) identity.announceReservationRemoved(key.slice(names.reservationPrefix.length));
			return reports;
		}
		function start() {
			if (started) return started;
			started = identity.claim().then(async (claim) => ({
				claim,
				merged: await merge(false)
			}));
			return started;
		}
		function scanEmergency() {
			if (!started) return start().then((result) => result.merged);
			return started.then(() => merge(true));
		}
		function classify(record, askingSession = null) {
			return classifyLineage(record, {
				identity,
				localOwner: (id) => writers.ownerOf(id)
			}, askingSession);
		}
		function discardReviewed(reviewed) {
			const expected = {
				pageInstanceId: reviewed.pageInstanceId,
				generation: reviewed.generation,
				status: reviewed.status
			};
			const listing = emergencyStorage ? listingPage(emergencyStorage, names, reviewed.draftId) : null;
			const tombstone = listing ? {
				kind: reviewed.kind,
				entityType: reviewed.entityType,
				entityId: reviewed.entityId,
				generation: reviewed.generation,
				pageInstanceId: listing
			} : void 0;
			return store.discard(reviewed.draftId, tombstone, expected);
		}
		/**
		* A first generation that never reached IndexedDB exists only in a key
		* that no scan merged: tombstoned only when its page is proven gone.
		*/
		async function suppressEmergencyOnly(ours, report) {
			if (!emergencyStorage) return;
			for (const stored of readEmergencyKeys(emergencyStorage, names)) {
				const payload = stored.payload;
				if (!payload) {
					if (mayHoldUnreadable(stored)) report.unsupported.push(stored.key);
					continue;
				}
				for (const entry of payload.entries) {
					if (entry.committedGeneration !== 0 || entry.body === null || !ours(entry.kind, entry.entityType, entry.entityId)) continue;
					const owner = {
						runtimeId: payload.runtimeId,
						pageInstanceId: stored.pageInstanceId,
						paneId: "",
						claimedAt: payload.at
					};
					const lineage = await classify({
						draftId: entry.draftId,
						owner
					});
					if (!isAdoptable(lineage)) {
						keptFor(lineage, report).push(entry.draftId);
						continue;
					}
					const stone = {
						kind: entry.kind,
						entityType: entry.entityType,
						entityId: entry.entityId,
						generation: entry.generation,
						pageInstanceId: stored.pageInstanceId
					};
					if (await store.tombstoneIfAbsent(entry.draftId, stone) === "suppressed") {
						report.suppressed.push(entry.draftId);
						continue;
					}
					const landed = await store.get(entry.draftId);
					if (landed && !isSupportedRecord(landed)) report.unsupported.push(entry.draftId);
					else if (landed && landed.status !== "discarded") report.changed.push(entry.draftId);
				}
			}
		}
		async function cleanupDeletedEntity(entity, kinds) {
			const report = {
				removed: [],
				live: [],
				unknown: [],
				changed: [],
				unsupported: [],
				suppressed: []
			};
			await scanEmergency();
			const keys = new Set(kinds.map((kind) => entityKeyOf(kind, entity.entityId)));
			const ours = (kind, entityType, entityId) => entityType === entity.entityType && entityId === entity.entityId && keys.has(entityKeyOf(kind, entityId));
			const records = [];
			for (const row of await store.listAll()) {
				if (isSupportedRecord(row)) {
					if (row.status !== "discarded" && ours(row.kind, row.entityType, row.entityId)) records.push(row);
					continue;
				}
				const r = row || {};
				if (!(typeof r.entityType === "string" && typeof r.entityId === "string") || r.entityType === entity.entityType && r.entityId === entity.entityId) report.unsupported.push(typeof r.draftId === "string" ? r.draftId : "");
			}
			for (const record of records) {
				const lineage = await classify(record);
				if (!isAdoptable(lineage)) {
					keptFor(lineage, report).push(record.draftId);
					continue;
				}
				const outcome = await discardReviewed({
					draftId: record.draftId,
					pageInstanceId: record.owner.pageInstanceId,
					generation: record.generation,
					status: record.status,
					kind: record.kind,
					entityType: record.entityType,
					entityId: record.entityId
				});
				if (outcome === "deleted" || outcome === "missing") report.removed.push(record.draftId);
				else if (outcome === "kept") report.changed.push(record.draftId);
				else report.unsupported.push(record.draftId);
			}
			await suppressEmergencyOnly(ours, report);
			return report;
		}
		return {
			names,
			store,
			identity,
			writers,
			start,
			scanEmergency,
			classify,
			claimReviewed(reviewed, paneId) {
				if (reviewed.status !== "active") return Promise.resolve({ outcome: "conflict" });
				const claim = identity.current();
				const owner = {
					runtimeId: claim ? claim.runtimeId : null,
					pageInstanceId: identity.pageInstanceId,
					paneId,
					claimedAt: Date.now()
				};
				return store.adopt(reviewed.draftId, reviewed.pageInstanceId, owner, reviewed.generation);
			},
			discardReviewed,
			cleanupDeletedEntity,
			onWriterEvent(listener) {
				if (retired) return () => void 0;
				writerListeners.add(listener);
				return () => {
					writerListeners.delete(listener);
				};
			},
			emergencyStorage,
			retire() {
				if (retiring) return retiring;
				retired = true;
				writerListeners.clear();
				retiring = settledWithin(writers.retire().then(() => {
					writers.dispose();
					identity.dispose();
					closed = true;
					rawStore.retire();
				}), options.retireWaitMs ?? RETIRE_WAIT_MS);
				return retiring;
			},
			isRetired: () => retired,
			dispose() {
				writerListeners.clear();
				writers.dispose();
				identity.dispose();
				rawStore.close();
			}
		};
	}
	/** The page whose emergency key still lists `draftId` (or its fork), if any. */
	function listingPage(storage, names, draftId) {
		for (const stored of readEmergencyKeys(storage, names)) if ((stored.payload ? stored.payload.entries : []).some((entry) => entry.draftId === draftId || forkedDraftId(entry.draftId, entry.generation) === draftId)) return stored.pageInstanceId;
		return null;
	}
	/** How long a retirement waits for the writers' last writes before reporting them pending. */
	var RETIRE_WAIT_MS = 1e4;
	function settledWithin(work, ms) {
		return new Promise((resolve) => {
			const timer = setTimeout(() => resolve("pending"), ms);
			work.then(() => {
				clearTimeout(timer);
				resolve("closed");
			});
		});
	}
	function retiredError() {
		return new RecoveryStoreError("retired", "This account is signed out of recovery storage.");
	}
	/**
	* `store`, refusing every operation once `isRetired()` holds, and refusing
	* the result of one that was in flight then: nothing read before a
	* retirement is handed out after it.
	*/
	function retirableStore(store, isRetired) {
		function guard(fn) {
			return (...args) => {
				if (isRetired()) return Promise.reject(retiredError());
				return fn(...args).then((value) => {
					if (isRetired()) throw retiredError();
					return value;
				});
			};
		}
		return {
			writeGeneration: guard(store.writeGeneration),
			updateLineage: guard(store.updateLineage),
			adopt: guard(store.adopt),
			get: guard(store.get),
			getBody: guard(store.getBody),
			listByEntity: guard(store.listByEntity),
			listAll: guard(store.listAll),
			deleteIfAcknowledged: guard(store.deleteIfAcknowledged),
			deleteIfEqual: guard(store.deleteIfEqual),
			discard: guard(store.discard),
			clearTombstone: guard(store.clearTombstone),
			tombstoneIfAbsent: guard(store.tombstoneIfAbsent),
			applyEmergencyEntry: guard(store.applyEmergencyEntry),
			lastDurability: () => store.lastDurability(),
			close: () => store.close(),
			retire: () => store.retire(),
			isRetired: () => isRetired() || store.isRetired()
		};
	}
	/** `storage`, refusing every access once `isRetired()` holds. */
	function retirableStorage(storage, isRetired) {
		function guard() {
			if (isRetired()) throw new Error("This account is signed out of recovery storage.");
			return storage;
		}
		return {
			get length() {
				return guard().length;
			},
			key: (index) => guard().key(index),
			getItem: (key) => guard().getItem(key),
			setItem: (key, value) => guard().setItem(key, value),
			removeItem: (key) => guard().removeItem(key)
		};
	}
	function defaultLocalStorage() {
		try {
			return typeof localStorage !== "undefined" ? localStorage : null;
		} catch {
			return null;
		}
	}
	//#endregion
	//#region src/lifecycle/editor-recovery/research-notes.ts
	/**
	* Research Notes restore decision (#466 slices 2 and 3).
	*
	* A pure function over what the mount path already knows: the recovery
	* records for one Work with their lineage class and stored body, the
	* acknowledged base `K` from notes-state, and the unsettled Research Notes
	* queue rows `Q`. It reads nothing and changes nothing; `works.js` applies
	* the plan.
	*
	* Automatic restore is allowed only when it cannot overwrite anything:
	* exactly one candidate, from a lineage no live editor can own (this tab
	* before reload, another pane of this tab, or a tab proven closed), typed on
	* exactly the acknowledged body the server still holds, or on a body this
	* lineage itself queued from that same base (the #475 own-predecessor rule).
	* Every other candidate is kept untouched and reported for review, with the
	* one action a user may take on it there: restore it as it is, reconcile it
	* against the current note first, or neither (inspect and copy only).
	*/
	function planResearchNotesRestore(input) {
		const print = memoized(input.fingerprint || fingerprintText);
		const K = input.base;
		const plan = {
			cleanup: [],
			represented: [],
			restore: null,
			review: []
		};
		const review = (c, reason) => plan.review.push({
			draftId: c.record.draftId,
			reason,
			lineage: c.lineage,
			generation: c.record.generation,
			bodyLength: c.record.bodyLength,
			paneId: c.record.owner.paneId,
			updatedAt: c.record.updatedAt,
			status: c.record.status,
			action: actionFor(c, input, print)
		});
		const queue = input.queue;
		let liveElsewhere = false;
		let unresolved = 0;
		const remaining = [];
		for (const c of input.candidates) {
			if (c.record.status === "discarded") continue;
			if (c.lineage === "self-live" || c.lineage === "other-live") {
				liveElsewhere = true;
				review(c, "live-elsewhere");
				continue;
			}
			if (c.record.status === "tail-missing") {
				unresolved++;
				review(c, "tail-missing");
				continue;
			}
			if (c.body === null) {
				unresolved++;
				review(c, "body-missing");
				continue;
			}
			const inactive = c.lineage === "same-runtime-orphan" || c.lineage === "dead-runtime";
			if (inactive && K && K.source === "server" && queue && !queue.length && stored(input)(c.body) === K.value) {
				plan.cleanup.push(c.record.draftId);
				continue;
			}
			const queuedOpId = c.record.pipeline ? c.record.pipeline.queuedOpId : null;
			const last = queue ? queue[queue.length - 1] : void 0;
			if (inactive && queuedOpId && last && last.opId === queuedOpId && last.text === c.body) {
				plan.represented.push({
					draftId: c.record.draftId,
					opId: queuedOpId,
					generation: c.record.generation,
					text: c.body,
					pageInstanceId: c.record.owner.pageInstanceId
				});
				continue;
			}
			remaining.push(c);
		}
		if (remaining.length + unresolved !== 1) {
			for (const c of remaining) review(c, "multiple-drafts");
			return plan;
		}
		if (!remaining.length) return plan;
		const c = remaining[0];
		const judged = judge(c, input, liveElsewhere, print);
		if ("reason" in judged) review(c, judged.reason);
		else plan.restore = judged;
		return plan;
	}
	function stored(input) {
		return input.stored || ((text) => text);
	}
	/** Fingerprints the note once however many candidates are judged against it. */
	function memoized(print) {
		const seen = /* @__PURE__ */ new Map();
		return (text) => {
			let value = seen.get(text);
			if (value === void 0) {
				value = print(text);
				if (seen.size > 8) seen.clear();
				seen.set(text, value);
			}
			return value;
		};
	}
	/** One remaining candidate on its own: restorable as it is, or why not. */
	function judge(c, input, liveElsewhere, print) {
		const reason = blockingReason(c, input, liveElsewhere);
		if (reason) return { reason };
		const K = input.base;
		const queue = input.queue;
		const record = c.record;
		const pipeline = record.pipeline;
		const own = pipeline ? pipeline.ownQueued : null;
		const k = K ? {
			revision: K.revision,
			length: K.value.length,
			fingerprint: print(K.value)
		} : null;
		let predecessor = null;
		if (!queue) return { reason: "queue-unknown" };
		if (queue.length) {
			const row = queue.length === 1 ? queue[0] : null;
			if (!own || !row || row.opId !== own.opId || row.text.length !== own.textLength || print(row.text) !== own.textFingerprint) return { reason: "foreign-queue" };
			predecessor = row;
		}
		const blockedBase = pipeline && pipeline.state === "blocked" ? pipeline.blockedBase : null;
		const typedOn = blockedBase || record.base;
		const unchanged = sameBaseIdentity(record.base, k) || !!blockedBase && sameBaseIdentity(blockedBase, k);
		if (predecessor) {
			if (!unchanged || !own || !sameBaseIdentity(own.base, k)) return { reason: "base-advanced" };
		} else if (!unchanged) {
			if (!(!!own && !!K && !!k && sameBaseIdentity(own.base, typedOn) && typedOn.revision !== null && K.revision > typedOn.revision && k.length === (own.storedLength ?? own.textLength) && k.fingerprint === (own.storedFingerprint ?? own.textFingerprint))) return { reason: "base-advanced" };
		}
		return {
			record,
			body: c.body,
			state: predecessor && pipeline && pipeline.state === "blocked" ? "blocked" : "drafting",
			predecessor
		};
	}
	/** Reasons a reviewer can still act on by comparing with the current note first. */
	var RECONCILABLE = /* @__PURE__ */ new Set([
		"base-advanced",
		"foreign-queue",
		"base-unverified"
	]);
	function actionFor(c, input, print) {
		if (c.record.status !== "active" || c.body === null) return null;
		if (c.lineage !== "same-runtime-orphan" && c.lineage !== "dead-runtime") return null;
		if (!input.queue) return null;
		const judged = judge(c, {
			...input,
			editorDirty: false
		}, false, print);
		if (!("reason" in judged)) return input.editorDirty ? "reconcile" : "restore";
		return RECONCILABLE.has(judged.reason) ? "reconcile" : null;
	}
	function blockingReason(c, input, liveElsewhere) {
		if (liveElsewhere) return "other-draft-live";
		if (c.lineage !== "same-runtime-orphan" && c.lineage !== "dead-runtime") return "ownership-unknown";
		if (input.otherDirtySession) return "dirty-session";
		if (input.editorDirty) return "editor-dirty";
		if (!input.base || input.base.source !== "server") return "base-unverified";
		if (c.record.base.source === "unknown" || c.record.base.revision === null) return "base-unverified";
		return null;
	}
	//#endregion
	//#region src/lifecycle/editor-recovery-entry.ts
	/**
	* Classic-script entry. The maintainer build emits `frontend/js/editor-recovery.js`
	* as the global `prksEditorRecovery`. Loading the script starts nothing;
	* `runtime()` creates the page's single runtime on first use, once the page
	* has a confirmed browser scope. Research Notes
	* (`works.js`) is the consumer since #466 slice 2; Settings → Diagnostics
	* reads every kind through `diagnostics()` (slice 5).
	*/
	var pageRuntime = null;
	var pageDiagnostics = null;
	/** The page's scope was signed out or replaced: recovery stays off for the life of the page. */
	var pageRetired = false;
	var watching = false;
	function browserScope() {
		return globalThis.prksBrowserScope || null;
	}
	/**
	* Retires the page's recovery (#629): explicit sign-out of this account, or
	* another account signing in under this page. The runtime, its store, its
	* localStorage view and the diagnostics refuse every later operation, also
	* through handles taken before; nothing stored is removed.
	*/
	function retirePage() {
		pageRetired = true;
		pageDiagnostics = null;
		if (pageRuntime) pageRuntime.retire();
	}
	function scopeRetired(scope) {
		if (pageRetired) return true;
		try {
			if (scope && typeof scope.isRetired === "function" && scope.isRetired()) {
				retirePage();
				return true;
			}
		} catch {}
		return false;
	}
	function watchScope(scope) {
		if (watching || !scope || typeof scope.subscribe !== "function") return;
		watching = true;
		scope.subscribe((event) => {
			if (event && event.type === "retired") retirePage();
		});
	}
	/** The page's confirmed browser scope key (`frontend/js/browser-scope.js`), or null. */
	function confirmedScopeKey() {
		const scope = browserScope();
		if (scopeRetired(scope)) return null;
		try {
			const current = scope && typeof scope.current === "function" ? scope.current() : null;
			return current && isScopeKey(current.key) ? current.key : null;
		} catch {
			return null;
		}
	}
	/**
	* The names of the page's recovery storage for its confirmed browser scope,
	* or null without one. For readers that must not build the runtime (a
	* cheap "may anything be stored here?" check): a runtime made later uses
	* the same names, unless it already exists, in which case these are its.
	*/
	function scopeNames() {
		if (scopeRetired(browserScope())) return null;
		if (pageRuntime) return pageRuntime.names;
		const scopeKey = confirmedScopeKey();
		return scopeKey ? recoveryNames(scopeKey) : null;
	}
	/**
	* The page's runtime, bound to the signed-in browser scope (#629), or null
	* while the page has none: recovery then keeps nothing (fail closed) and
	* a later call, once the scope is confirmed, creates it. The runtime keeps
	* that scope until the scope is retired (explicit sign-out, or another
	* account signing in under this page); from then on it refuses everything
	* and this returns null for the life of the page. Drafts typed under an
	* account stay that account's.
	*/
	function runtime() {
		if (scopeRetired(browserScope())) return null;
		if (pageRuntime) return pageRuntime;
		const scopeKey = confirmedScopeKey();
		if (!scopeKey) return null;
		watchScope(browserScope());
		pageRuntime = createEditorRecoveryRuntime({ names: recoveryNames(scopeKey) });
		return pageRuntime;
	}
	/** Settings → Diagnostics (#466 slice 5) over this page's runtime, or null without a scope. Starts nothing until it is asked to read. */
	function diagnostics() {
		if (scopeRetired(browserScope())) return null;
		if (pageDiagnostics) return pageDiagnostics;
		const rt = runtime();
		if (!rt) return null;
		pageDiagnostics = createRecoveryDiagnostics(rt);
		return pageDiagnostics;
	}
	//#endregion
	exports.BODIES_STORE = BODIES_STORE;
	exports.CLAIM_WAIT_MS = CLAIM_WAIT_MS;
	exports.CLOSED_PAGES_KEPT = CLOSED_PAGES_KEPT;
	exports.CLOSED_PAGES_LOCAL_KEPT = CLOSED_PAGES_LOCAL_KEPT;
	exports.DRAFTS_STORE = DRAFTS_STORE;
	exports.EMERGENCY_BODY_CHARS = EMERGENCY_BODY_CHARS;
	exports.EMERGENCY_ENTRY_OVERHEAD_CHARS = EMERGENCY_ENTRY_OVERHEAD_CHARS;
	exports.EMERGENCY_PAGE_CHARS = EMERGENCY_PAGE_CHARS;
	exports.EMERGENCY_VERSION = EMERGENCY_VERSION;
	exports.IDLE_WRITE_MS = IDLE_WRITE_MS;
	exports.LARGE_BODY_CHARS = LARGE_BODY_CHARS;
	exports.MAX_WRITE_WAIT_MS = MAX_WRITE_WAIT_MS;
	exports.RECORD_VERSION = RECORD_VERSION;
	exports.RECOVERY_DB_VERSION = RECOVERY_DB_VERSION;
	exports.RETRY_FIRST_MS = RETRY_FIRST_MS;
	exports.RETRY_MAX_MS = RETRY_MAX_MS;
	exports.RecoveryStoreError = RecoveryStoreError;
	exports.UNKNOWN_BASE = UNKNOWN_BASE;
	exports.classifyLineage = classifyLineage;
	exports.createEditorRecoveryRuntime = createEditorRecoveryRuntime;
	exports.createPageIdentity = createPageIdentity;
	exports.createRecoveryStore = createRecoveryStore;
	exports.createWriterRegistry = createWriterRegistry;
	exports.diagnostics = diagnostics;
	exports.emergencyKeyOf = emergencyKeyOf;
	exports.entityKeyOf = entityKeyOf;
	exports.fingerprintText = fingerprintText;
	exports.forkedDraftId = forkedDraftId;
	exports.isAdoptable = isAdoptable;
	exports.isDraftKind = isDraftKind;
	exports.isScopeKey = isScopeKey;
	exports.isSupportedRecord = isSupportedRecord;
	exports.mayHoldUnreadable = mayHoldUnreadable;
	exports.mergeEmergencyEntries = mergeEmergencyEntries;
	exports.mintId = mintId;
	exports.planEmergency = planEmergency;
	exports.planResearchNotesRestore = planResearchNotesRestore;
	exports.readEmergencyKeys = readEmergencyKeys;
	exports.recoveryNames = recoveryNames;
	exports.reservationKeyOf = reservationKeyOf;
	exports.reservationRuntimeOf = reservationRuntimeOf;
	exports.reservationValue = reservationValue;
	exports.runtime = runtime;
	exports.sameBaseIdentity = sameBaseIdentity;
	exports.sameBody = sameBody;
	exports.scopeNames = scopeNames;
	return exports;
})({});
if (typeof module === "object" && module != null && module.exports) module.exports = prksEditorRecovery;
