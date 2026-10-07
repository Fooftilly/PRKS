var prksEditorRecovery = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	//#region src/lifecycle/editor-recovery/schema.ts
	/**
	* Browser-local editor draft recovery (#466): names, budgets and record shapes.
	*
	* Recovery drafts live in their own IndexedDB database. They are not server
	* state, not the semantic operation queue (`prks-local-v1`) and not a cache;
	* Clear offline cache never touches them. Nothing here claims that a body was
	* saved: the queue and the server stay the only authorities for that.
	*/
	var RECOVERY_DB_NAME = "prks-editor-recovery-v1";
	var RECOVERY_DB_VERSION = 1;
	var DRAFTS_STORE = "drafts";
	var BODIES_STORE = "bodies";
	/** Record schema version written by this build. Readers accept `v <= RECORD_VERSION`. */
	var RECORD_VERSION = 1;
	var EMERGENCY_VERSION = 1;
	/** One localStorage key per page load, so duplicated tabs never share an entry. */
	var EMERGENCY_KEY_PREFIX = "prks.editorRecovery.emergency.v1.";
	/** Not under the emergency prefix, so a leftover probe is never read as an emergency entry. */
	var EMERGENCY_PROBE_KEY = "prks.editorRecovery.probe.v1";
	/** Allowance for one entry's JSON metadata (ids, lineage, base) when sizing the emergency payload. */
	var EMERGENCY_ENTRY_OVERHEAD_CHARS = 1024;
	/** Candidate runtime id for this browser tab; copied by window.open and Duplicate tab. */
	var RUNTIME_SESSION_KEY = "prks.editorRecovery.runtime.v1";
	var RECOVERY_CHANNEL = "prks-editor-recovery-v1";
	var RUNTIME_LOCK_PREFIX = "prks-editor-recovery-runtime:";
	var PAGE_LOCK_PREFIX = "prks-editor-recovery-page:";
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
	function emergencyKeyOf(pageInstanceId) {
		return EMERGENCY_KEY_PREFIX + pageInstanceId;
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
	//#region src/lifecycle/editor-recovery/emergency.ts
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
	function readEmergencyKeys(storage) {
		const keys = [];
		for (let i = 0; i < storage.length; i++) {
			const key = storage.key(i);
			if (key && key.startsWith("prks.editorRecovery.emergency.v1.")) keys.push(key);
		}
		return keys.map((key) => {
			const pageInstanceId = key.slice(EMERGENCY_KEY_PREFIX.length);
			return {
				key,
				pageInstanceId,
				payload: parsePayload(storage.getItem(key), pageInstanceId)
			};
		});
	}
	async function mergeEmergencyEntries(env) {
		const reports = [];
		for (const stored of readEmergencyKeys(env.storage)) {
			if (stored.pageInstanceId === env.pageInstanceId || !stored.payload) continue;
			if (await env.isPageAlive(stored.pageInstanceId) === true) continue;
			const outcomes = [];
			let complete = true;
			for (const entry of stored.payload.entries) try {
				const outcome = await env.store.applyEmergencyEntry(stored.payload, entry);
				outcomes.push(outcome);
				if (outcome === "deferred") complete = false;
			} catch {
				complete = false;
			}
			if (complete) try {
				env.storage.removeItem(stored.key);
			} catch {
				complete = false;
			}
			reports.push({
				key: stored.key,
				outcomes,
				removed: complete
			});
		}
		return reports;
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
	function createPageIdentity(env = {}) {
		const random = env.random || globalThis.crypto;
		const session = env.sessionStorage === void 0 ? defaultSession() : env.sessionStorage;
		const locks = env.locks === void 0 ? defaultLocks() : env.locks;
		const makeChannel = env.createChannel === void 0 ? defaultChannel() : env.createChannel;
		const later = env.setTimeout || ((fn, ms) => setTimeout(fn, ms));
		const waitMs = env.claimWaitMs ?? 250;
		const pageInstanceId = mintId("p", random);
		let channel = null;
		try {
			channel = makeChannel ? makeChannel(RECOVERY_CHANNEL) : null;
		} catch {
			channel = null;
		}
		const releases = [];
		let disposed = false;
		let settled = null;
		let claimPromise = null;
		let pending = null;
		let lineageResponder = null;
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
			if (m.to === pageInstanceId && pending && pending.rid === m.rid) pending.lost = true;
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
				if (session) session.setItem(RUNTIME_SESSION_KEY, runtimeId);
			} catch {}
		}
		function initialCandidate() {
			try {
				const stored = session ? session.getItem(RUNTIME_SESSION_KEY) : null;
				if (stored && /^r-[0-9a-f]{32}$/.test(stored)) return stored;
			} catch {}
			return mintId("r", random);
		}
		async function claimWithLocks(candidate) {
			await hold(PAGE_LOCK_PREFIX + pageInstanceId);
			for (let round = 0; round < MAX_CLAIM_ROUNDS; round++) {
				if (await hold("prks-editor-recovery-runtime:" + candidate)) return {
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
		function claim() {
			if (claimPromise) return claimPromise;
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
			claim,
			current: () => settled,
			async isPageAlive(id) {
				if (id === pageInstanceId) return true;
				const held = await heldLockNames();
				if (held) return held.has(PAGE_LOCK_PREFIX + id);
				return ask({
					t: "page?",
					page: id,
					q: queryId()
				});
			},
			async isRuntimeAlive(id) {
				if (settled && settled.runtimeId === id) return true;
				const held = await heldLockNames();
				if (held) return held.has(RUNTIME_LOCK_PREFIX + id);
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
			dispose() {
				if (disposed) return;
				disposed = true;
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
			if (claim.verified !== "unverified") return "same-runtime-orphan";
			return await identity.isLineageLiveElsewhere(record.draftId) === true ? "other-live" : "unknown";
		}
		if (await identity.isLineageLiveElsewhere(record.draftId) === true) return "other-live";
		const pageAlive = await identity.isPageAlive(owner.pageInstanceId);
		const runtimeAlive = owner.runtimeId ? await identity.isRuntimeAlive(owner.runtimeId) : false;
		if (pageAlive === false && runtimeAlive === false) return "dead-runtime";
		return "unknown";
	}
	//#endregion
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
	/** Lineage id for an emergency tail forked away from a lineage adopted since. Deterministic, so merges are idempotent. */
	function forkedDraftId(draftId, generation) {
		return draftId + ".e" + generation;
	}
	function createRecoveryStore(options = {}) {
		const factory = options.indexedDB === void 0 ? globalThis.indexedDB : options.indexedDB;
		const name = options.name || "prks-editor-recovery-v1";
		const now = options.now || Date.now;
		const durability = options.durability || "auto";
		const compare = { fingerprint: options.fingerprint };
		let dbPromise = null;
		let handle = null;
		let lastMode = null;
		function openDb() {
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
						putPair(tx, newRecord(input.draftId, input.create, input.generation, input.body.length), input.body);
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
						status: "active",
						updatedAt: now()
					}, input.body);
					done("ok");
				});
			});
		}
		function adopt(draftId, expectedPageInstanceId, owner) {
			return run("readwrite", (tx, done) => {
				readRecord(tx, draftId, (record) => {
					if (!record) return done({ outcome: "missing" });
					if (!isSupportedRecord(record)) return done({ outcome: "unsupported" });
					if (record.owner.pageInstanceId !== expectedPageInstanceId) return done({ outcome: "conflict" });
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
			return listAll().then((rows) => rows.filter((row) => row && row.entityKey === key));
		}
		function deleteIfAcknowledged(draftId, generation, body) {
			return run("readwrite", (tx, done) => {
				readBoth(tx, draftId, (record, row) => {
					if (!record) return done("missing");
					if (!isSupportedRecord(record)) return done("unsupported");
					if (record.generation !== generation || !row || row.generation !== generation) return done("kept");
					if (!sameBody(row.body, body, compare)) return done("kept");
					deletePair(tx, draftId);
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
					deletePair(tx, draftId);
					done("deleted");
				});
			});
		}
		function discard(draftId) {
			return run("readwrite", (tx, done) => {
				readRecord(tx, draftId, (record) => {
					if (!record) return done("missing");
					if (!isSupportedRecord(record)) return done("unsupported");
					deletePair(tx, draftId);
					done("deleted");
				});
			});
		}
		function applyEmergencyEntry(payload, entry) {
			return run("readwrite", (tx, done) => {
				readRecord(tx, entry.draftId, (record) => {
					if (record && !isSupportedRecord(record)) return done("deferred");
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
							updatedAt: now()
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
						putPair(tx, newRecord(forkId, lineage, entry.generation, entry.body.length), entry.body);
						done("forked");
					});
				});
			});
		}
		return {
			writeGeneration,
			adopt,
			get,
			getBody,
			listByEntity,
			listAll,
			deleteIfAcknowledged,
			deleteIfEqual,
			discard,
			applyEmergencyEntry,
			lastDurability: () => lastMode,
			close() {
				const db = handle;
				handle = null;
				dbPromise = null;
				if (db) try {
					db.close();
				} catch {}
			}
		};
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
		const emergencyKey = emergencyKeyOf(identity.pageInstanceId);
		const live = /* @__PURE__ */ new Set();
		/** Writers whose body the budget plan puts in the emergency entry. */
		let planned = /* @__PURE__ */ new Set();
		/** `planned`, but only while emergency storage has shown it can keep those bodies; otherwise empty. */
		let held = /* @__PURE__ */ new Set();
		/**
		* Largest payload, in chars, that localStorage accepted (a probe or a real
		* emergency write). A body counts as held only when the planned payload fits
		* within it, so blocked, missing or too-full storage leaves the leave guard
		* armed before unload instead of discovering the failure at pagehide.
		*/
		let provenChars = 0;
		/** Set when a real emergency write could not keep every planned body; cleared by a full write. */
		let distrusted = false;
		let guardOn = false;
		let emergencyOn = false;
		let emergencyWritten = false;
		/** Draft ids in the emergency key as last written. */
		let emergencyIds = /* @__PURE__ */ new Set();
		let refreshingEmergency = false;
		let disposed = false;
		function onBeforeUnload(event) {
			event.preventDefault();
			event.returnValue = "";
		}
		function onPageHide() {
			writeEmergencyNow();
		}
		function onVisibility() {
			if (doc && doc.visibilityState === "hidden") writeEmergencyNow();
		}
		identity.setLineageResponder((draftId) => ownerOf(draftId) !== null);
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
			planHeld(pending);
			setGuard(pending.some((w) => w.needsLeaveGuard()));
			setEmergencyListeners(pending.length > 0);
			refreshEmergencyKey(pending);
		}
		function planHeld(pending) {
			const lengths = pending.map((w) => w.pendingBody().body.length);
			const plan = planEmergency(lengths);
			planned = new Set(pending.filter((_, i) => plan.has(i)));
			const plannedChars = lengths.reduce((sum, length, i) => plan.has(i) ? sum + length : sum, 0);
			held = pending.length && canHold(payloadEstimate(plannedChars, pending.length)) ? planned : /* @__PURE__ */ new Set();
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
				if (win) win[method]("pagehide", onPageHide);
				if (doc) doc[method]("visibilitychange", onVisibility);
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
			} catch {}
		}
		/** Planned body chars, a margin for JSON escaping, and per-entry metadata. */
		function payloadEstimate(plannedChars, entries) {
			return Math.ceil(plannedChars * 1.25) + entries * EMERGENCY_ENTRY_OVERHEAD_CHARS;
		}
		/**
		* Whether localStorage can take a payload of `chars` now. Beyond what is
		* already proven it writes a probe of twice the size (then the exact size),
		* so proofs grow geometrically and typing does not probe on every keystroke.
		*/
		function canHold(chars) {
			if (!storage || distrusted) return false;
			if (chars <= provenChars) return true;
			for (const size of [chars * 2, chars]) try {
				storage.setItem(EMERGENCY_PROBE_KEY, "x".repeat(size));
				provenChars = size;
				return true;
			} catch {} finally {
				try {
					storage.removeItem(EMERGENCY_PROBE_KEY);
				} catch {}
			}
			return false;
		}
		function noteEmergencyResult(result, payloadChars) {
			const wasDistrusted = distrusted;
			if (result === "written") {
				distrusted = false;
				provenChars = Math.max(provenChars, payloadChars);
			} else {
				distrusted = true;
				provenChars = 0;
			}
			if (wasDistrusted !== distrusted || result !== "written") changed();
		}
		function writeEmergencyNow() {
			const pending = pendingWriters();
			if (!pending.length) return "nothing-pending";
			if (!storage) {
				noteEmergencyResult("unavailable", 0);
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
			const result = writeEmergency(storage, emergencyKey, payload);
			if (result !== "failed") {
				emergencyWritten = true;
				emergencyIds = new Set(payload.entries.map((e) => e.draftId));
			}
			noteEmergencyResult(result, payloadEstimate(planned.size ? sumPlanned() : 0, pending.length));
			return result;
		}
		function sumPlanned() {
			let sum = 0;
			for (const w of planned) sum += w.pendingBody().body.length;
			return sum;
		}
		class WriterImpl {
			sessionKey;
			kind;
			entityType;
			entityId;
			paneId;
			base;
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
				this.status = "protected";
			}
			currentDraftId() {
				return this.lineageId;
			}
			pendingBody() {
				return this.latest;
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
				if (this.released || disposed) return;
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
				const attempt = store.writeGeneration({
					draftId,
					pageInstanceId: identity.pageInstanceId,
					generation: pending.generation,
					body: pending.body,
					paneId: this.paneId,
					base: this.base,
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
				const outcome = await store.deleteIfAcknowledged(draftId, generation, body);
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
				return outcome;
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
				if (draftId) await store.discard(draftId);
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
			}
		}
		function openWriter(input) {
			const writer = new WriterImpl(input);
			live.add(writer);
			return writer;
		}
		/** Draft ids with an adoption in progress on this page. */
		const adopting = /* @__PURE__ */ new Set();
		async function adopt(record, input) {
			if (disposed || adopting.has(record.draftId) || ownerOf(record.draftId) !== null) return null;
			adopting.add(record.draftId);
			try {
				return await adoptReserved(record, input);
			} finally {
				adopting.delete(record.draftId);
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
			});
			if (result.outcome !== "ok" || disposed) return null;
			writer.adoptRecord(result.record);
			live.add(writer);
			return writer;
		}
		return {
			openWriter,
			adopt,
			ownerOf,
			writers: () => [...live],
			leaveGuardActive: () => guardOn,
			emergencyListenersActive: () => emergencyOn,
			writeEmergencyNow,
			dispose() {
				for (const w of live) w.dispose();
				live.clear();
				if (win) {
					win.removeEventListener("beforeunload", onBeforeUnload);
					win.removeEventListener("pagehide", onPageHide);
				}
				if (doc) doc.removeEventListener("visibilitychange", onVisibility);
				guardOn = false;
				emergencyOn = false;
				disposed = true;
				identity.setLineageResponder(null);
			}
		};
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
	* pages that are no longer alive. Nothing starts at script load; slice 1 has
	* no consumer, so the page holds no lock, channel or listener until a
	* consumer asks for the runtime.
	*/
	function createEditorRecoveryRuntime(options = {}) {
		const store = createRecoveryStore(options.store);
		const identity = createPageIdentity(options.identity);
		const emergencyStorage = options.emergencyStorage !== void 0 ? options.emergencyStorage : options.writers?.emergencyStorage ?? defaultLocalStorage();
		const writers = createWriterRegistry({
			...options.writers,
			store,
			identity,
			emergencyStorage
		});
		let started = null;
		return {
			store,
			identity,
			writers,
			start() {
				if (started) return started;
				started = identity.claim().then(async (claim) => {
					return {
						claim,
						merged: emergencyStorage ? await mergeEmergencyEntries({
							storage: emergencyStorage,
							store,
							pageInstanceId: identity.pageInstanceId,
							isPageAlive: (id) => identity.isPageAlive(id)
						}) : []
					};
				});
				return started;
			},
			classify(record, askingSession = null) {
				return classifyLineage(record, {
					identity,
					localOwner: (id) => writers.ownerOf(id)
				}, askingSession);
			},
			dispose() {
				writers.dispose();
				identity.dispose();
				store.close();
			}
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
	//#region src/lifecycle/editor-recovery-entry.ts
	/**
	* Classic-script entry. The maintainer build emits `frontend/js/editor-recovery.js`
	* as the global `prksEditorRecovery`. Slice 1 of #466 has no consumer: loading
	* the script starts nothing. `runtime()` creates the page's single runtime on
	* first use.
	*/
	var pageRuntime = null;
	function runtime() {
		if (!pageRuntime) pageRuntime = createEditorRecoveryRuntime();
		return pageRuntime;
	}
	//#endregion
	exports.BODIES_STORE = BODIES_STORE;
	exports.CLAIM_WAIT_MS = CLAIM_WAIT_MS;
	exports.DRAFTS_STORE = DRAFTS_STORE;
	exports.EMERGENCY_BODY_CHARS = EMERGENCY_BODY_CHARS;
	exports.EMERGENCY_ENTRY_OVERHEAD_CHARS = EMERGENCY_ENTRY_OVERHEAD_CHARS;
	exports.EMERGENCY_KEY_PREFIX = EMERGENCY_KEY_PREFIX;
	exports.EMERGENCY_PAGE_CHARS = EMERGENCY_PAGE_CHARS;
	exports.EMERGENCY_PROBE_KEY = EMERGENCY_PROBE_KEY;
	exports.EMERGENCY_VERSION = EMERGENCY_VERSION;
	exports.IDLE_WRITE_MS = IDLE_WRITE_MS;
	exports.LARGE_BODY_CHARS = LARGE_BODY_CHARS;
	exports.MAX_WRITE_WAIT_MS = MAX_WRITE_WAIT_MS;
	exports.PAGE_LOCK_PREFIX = PAGE_LOCK_PREFIX;
	exports.RECORD_VERSION = RECORD_VERSION;
	exports.RECOVERY_CHANNEL = RECOVERY_CHANNEL;
	exports.RECOVERY_DB_NAME = RECOVERY_DB_NAME;
	exports.RECOVERY_DB_VERSION = RECOVERY_DB_VERSION;
	exports.RETRY_FIRST_MS = RETRY_FIRST_MS;
	exports.RETRY_MAX_MS = RETRY_MAX_MS;
	exports.RUNTIME_LOCK_PREFIX = RUNTIME_LOCK_PREFIX;
	exports.RUNTIME_SESSION_KEY = RUNTIME_SESSION_KEY;
	exports.RecoveryStoreError = RecoveryStoreError;
	exports.UNKNOWN_BASE = UNKNOWN_BASE;
	exports.classifyLineage = classifyLineage;
	exports.createEditorRecoveryRuntime = createEditorRecoveryRuntime;
	exports.createPageIdentity = createPageIdentity;
	exports.createRecoveryStore = createRecoveryStore;
	exports.createWriterRegistry = createWriterRegistry;
	exports.emergencyKeyOf = emergencyKeyOf;
	exports.entityKeyOf = entityKeyOf;
	exports.fingerprintText = fingerprintText;
	exports.forkedDraftId = forkedDraftId;
	exports.isAdoptable = isAdoptable;
	exports.isDraftKind = isDraftKind;
	exports.isSupportedRecord = isSupportedRecord;
	exports.mergeEmergencyEntries = mergeEmergencyEntries;
	exports.mintId = mintId;
	exports.planEmergency = planEmergency;
	exports.readEmergencyKeys = readEmergencyKeys;
	exports.runtime = runtime;
	exports.sameBaseIdentity = sameBaseIdentity;
	exports.sameBody = sameBody;
	return exports;
})({});
if (typeof module === "object" && module != null && module.exports) module.exports = prksEditorRecovery;
