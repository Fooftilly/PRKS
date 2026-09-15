/**
 * PRKS local store -- DURABLE user-owned local state, IndexedDB only.
 *
 * This is NOT the offline cache. `offline-store.js` holds downloaded server
 * data in `prks-offline-v1`, which is deliberately disposable: it may be
 * discarded when corrupt, and "Clear offline cache" empties it. Unsynchronized
 * user work cannot share that lifecycle, so it lives in a **separate database**
 * (`prks-local-v1`). The separation is physical, not a convention: even a bug
 * in `offline-store.clearAll()` cannot reach another database.
 *
 * Scope:
 *   - Persistence only. No DOM, no routing, no connectivity policy, no HTTP,
 *     no server-specific mutation logic. Mirrors offline-store.js's discipline.
 *   - Stores semantic operation ENVELOPES describing domain intent, never
 *     serialized HTTP requests. Synchronization is not an HTTP replay queue.
 *
 * Failure contract -- deliberately the OPPOSITE of offline-store.js:
 *   offline-store degrades silently because a missing cache is survivable.
 *   Here a write that did not commit means the user's change does not exist,
 *   so every write either resolves with a committed result or REJECTS. Nothing
 *   may report "Saved locally" on a write this module did not commit.
 *
 * Commit semantics: a write resolves from the transaction's `oncomplete`, never
 * a request's `onsuccess` -- a request can succeed and its transaction still
 * abort, rolling the row back.
 *
 * Schema ("prks-local-v1", version 1):
 *   operations -- keyPath "op_id"  immutable semantic envelope + mutable sync state
 *   metadata   -- keyPath "key"    durable local identity/bookkeeping (device_id, sequence)
 *
 * Milestone 2A: persistence foundation only. No mutation UI consumes this yet;
 * PRKS remains read-only while the server is unreachable.
 */
(function (root) {
    'use strict';

    const DB_NAME = 'prks-local-v1';
    const DB_VERSION = 1;
    const STORE_OPERATIONS = 'operations';
    const STORE_METADATA = 'metadata';

    const META_DEVICE_ID = 'device_id';
    const META_SEQUENCE = 'op_sequence';

    /** Operation lifecycle. Kept minimal: a retryable failure is `pending`
     *  plus `last_error`/`attempt_count`, not a separate persisted status. */
    const STATUS_PENDING = 'pending';
    const STATUS_SYNCING = 'syncing';
    const STATUS_ACKNOWLEDGED = 'acknowledged';
    const STATUS_CONFLICT = 'conflict';
    const STATUS_FAILED = 'failed';
    /* The result recorded on an operation whose prerequisite can never
     * succeed. Written in one place: the store owns the dependency graph, so
     * it owns what a broken link in that graph looks like. */
    const DEPENDENCY_FAILED = 'DEPENDENCY_FAILED';
    const STATUSES = Object.freeze([
        STATUS_PENDING, STATUS_SYNCING, STATUS_ACKNOWLEDGED, STATUS_CONFLICT, STATUS_FAILED,
    ]);

    /* Allow-list. An operation type reaches durable storage only if it is
     * registered here, so a typo or a half-built feature cannot persist an
     * envelope no coordinator knows how to send. */
    const OPERATION_TYPES = Object.freeze([
        'MARK_WORK_OPENED',
        'CREATE_FOLDER',
        'SET_FOLDER_FIELD',
        'DELETE_FOLDER',
        'SET_WORK_FOLDER',
        'CREATE_TAG',
        'DELETE_TAG',
        'ADD_WORK_TAG',
        'REMOVE_WORK_TAG',
        'SET_WORK_METADATA_FIELD',
        'SET_WORK_SOURCE',
        'ADD_WORK_PERSON_ROLE',
        'REMOVE_WORK_PERSON_ROLE',
        'SET_WORK_PERSON_ROLE_CREDIT',
        'CREATE_PERSON',
        'DELETE_PERSON',
        'SET_PERSON_METADATA_FIELD',
        'CREATE_PERSON_GROUP',
        'SET_PERSON_GROUP_FIELD',
        'ADD_PERSON_GROUP_MEMBER',
        'REMOVE_PERSON_GROUP_MEMBER',
        'DELETE_PERSON_GROUP',
    ]);

    /* Bounds the ledger long before text/CRDT operations exist. A payload this
     * large is a bug, not a legitimate semantic operation. */
    const MAX_PAYLOAD_BYTES = 64 * 1024;

    /* The byte-limited Work metadata fields are allowed to be larger, and the
     * allowance is granted to an exact operation SHAPE rather than to a size
     * -- otherwise any future operation would inherit a large payload merely
     * by existing after this milestone.
     *
     * Mirrors `backend/work_metadata_sync.BYTE_LIMITS`. The durable store is
     * the last place a value can be refused before it becomes durable user
     * data, so it has to know the same numbers the server and the editor use
     * -- a value the editor accepted and the store rejected would be an edit
     * the user was told was saved and was not. */
    const WORK_FIELD_VALUE_BYTES = Object.freeze({
        abstract: 1024 * 1024,
        author_text: 64 * 1024,
        title: 64 * 1024,
        source_url: 64 * 1024,
    });
    const MAX_ABSTRACT_VALUE_BYTES = WORK_FIELD_VALUE_BYTES.abstract;
    /* Mirrors `backend/work_source_sync.MAX_SOURCE_URL_UTF8_BYTES`. Deliberately
     * a separate constant from WORK_FIELD_VALUE_BYTES.source_url: that one
     * bounds PROVENANCE on a non-video Work, this one bounds the aggregate's
     * URL. They are equal today and are free to diverge. */
    const WORK_SOURCE_URL_BYTES = 64 * 1024;
    const MAX_ERROR_CHARS = 500;

    /* The Work-Person role family. All three name one element's state, so they
     * coalesce against each other within one scope. */
    const PERSON_FIELDS = Object.freeze([
        'first_name', 'last_name', 'aliases', 'about', 'image_url',
        'link_wikipedia', 'link_stanford_encyclopedia', 'link_iep',
        'links_other', 'birth_date', 'death_date',
    ]);

    function generateEntityId(prefix, uuidFn) {
        const hex = String(uuidFn()).replace(/-/g, '').toUpperCase();
        if (!/^[0-9A-F]{32}$/.test(hex)) {
            throw localStoreError('invalid_envelope', 'Could not generate entity id.');
        }
        return prefix + '-' + hex;
    }

    function canonicalPersonPayload(input) {
        const src = isPlainObject(input) ? input : {};
        const payload = {};
        PERSON_FIELDS.forEach(function (name) {
            const value = src[name];
            payload[name] = value == null ? '' : String(value);
        });
        return payload;
    }

    /* Every unsynchronized operation that NAMES one Person, whatever family it
     * belongs to: their own scalar edits, their group memberships, and the
     * links that credit them on a file. Deleting a Person has to reason about
     * all of them at once. */
    function operationsNamingPerson(rows, personId) {
        return (Array.isArray(rows) ? rows : []).filter(function (row) {
            if (!row || row.status === STATUS_ACKNOWLEDGED) return false;
            if (row.entity_type === 'person' && row.entity_id === personId) return true;
            return !!row.payload && row.payload.person_id === personId;
        });
    }

    /* The editable columns on a Folder, in the server's vocabulary. */
    const FOLDER_FIELDS = Object.freeze(['title', 'description', 'private_notes', 'parent_id']);

    /* Every unsynchronized operation that names one folder, whatever it does to
     * it -- including the Works filed into it, which a deletion has to know
     * about because a folder holding files cannot be deleted. */
    function operationsNamingFolder(rows, folderId) {
        return (Array.isArray(rows) ? rows : []).filter(function (row) {
            if (!row || row.status === STATUS_ACKNOWLEDGED) return false;
            if (row.entity_type === 'folder' && row.entity_id === folderId) return true;
            return row.operation === 'SET_WORK_FOLDER' && !!row.payload &&
                row.payload.folder_id === folderId;
        });
    }

    function canonicalFolderPayload(input) {
        const src = isPlainObject(input) ? input : {};
        const payload = {};
        FOLDER_FIELDS.forEach(function (name) {
            const value = src[name];
            payload[name] = value == null ? '' : String(value).trim();
        });
        return payload;
    }

    /* The editable columns on a Person Group, in the server's vocabulary. */
    const PERSON_GROUP_FIELDS = Object.freeze(['name', 'description', 'parent_id']);

    const PERSON_GROUP_MEMBER_OPERATIONS = Object.freeze([
        'ADD_PERSON_GROUP_MEMBER', 'REMOVE_PERSON_GROUP_MEMBER',
    ]);

    /* Every unsynchronized operation that names one group, whatever it does to
     * it. Deleting a group has to reason about all of them at once. */
    const PERSON_GROUP_OPERATIONS = Object.freeze([
        'CREATE_PERSON_GROUP', 'SET_PERSON_GROUP_FIELD', 'DELETE_PERSON_GROUP',
    ].concat(PERSON_GROUP_MEMBER_OPERATIONS));

    function canonicalPersonGroupPayload(input) {
        const src = isPlainObject(input) ? input : {};
        const payload = {};
        PERSON_GROUP_FIELDS.forEach(function (name) {
            const value = src[name];
            payload[name] = value == null ? '' : String(value).trim();
        });
        return payload;
    }

    const WORK_ROLE_OPERATIONS = Object.freeze([
        'ADD_WORK_PERSON_ROLE', 'REMOVE_WORK_PERSON_ROLE', 'SET_WORK_PERSON_ROLE_CREDIT',
    ]);

    /** The canonical state an enqueued role operation names. */
    function workPersonRoleState(row) {
        if (row.operation === 'REMOVE_WORK_PERSON_ROLE') return null;
        return typeof row.payload.credit_name === 'string' ? row.payload.credit_name : '';
    }

    function defaultIndexedDB() {
        if (typeof indexedDB !== 'undefined') return indexedDB;
        if (root && root.indexedDB) return root.indexedDB;
        return null;
    }

    function defaultNow() {
        return Date.now();
    }

    /**
     * High-entropy operation id. Unlike PRKS entity ids (a prefix plus 32 bits)
     * these are generated independently on every device with no coordination,
     * so a full 122-bit UUID is the right size -- see docs/local-first-sync.md.
     */
    function defaultUuid() {
        const c = (typeof crypto !== 'undefined' && crypto) || (root && root.crypto) || null;
        if (c && typeof c.randomUUID === 'function') return c.randomUUID();
        if (c && typeof c.getRandomValues === 'function') {
            const bytes = new Uint8Array(16);
            c.getRandomValues(bytes);
            bytes[6] = (bytes[6] & 0x0f) | 0x40;
            bytes[8] = (bytes[8] & 0x3f) | 0x80;
            const hex = [];
            for (let i = 0; i < bytes.length; i++) hex.push((bytes[i] + 0x100).toString(16).slice(1));
            return (
                hex.slice(0, 4).join('') + '-' + hex.slice(4, 6).join('') + '-' +
                hex.slice(6, 8).join('') + '-' + hex.slice(8, 10).join('') + '-' +
                hex.slice(10, 16).join('')
            );
        }
        // No CSPRNG: refuse rather than mint a weak id that must stay unique
        // across devices forever.
        throw new Error('PRKS local store requires a cryptographic RNG for operation ids.');
    }

    const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

    function isOperationId(v) {
        return typeof v === 'string' && UUID_RE.test(v);
    }

    /** An ISO-8601 string that names a real instant. */
    function isParsableTimestamp(v) {
        if (typeof v !== 'string' || !v.trim()) return false;
        return Number.isFinite(Date.parse(v));
    }

    function isNonBlankString(v) {
        return typeof v === 'string' && v.trim().length > 0;
    }

    function isPlainObject(v) {
        return !!v && typeof v === 'object' && !Array.isArray(v);
    }

    /**
     * UTF-8 byte length of the serialized value.
     *
     * String `.length` counts UTF-16 code units, which undercounts every
     * non-ASCII character -- a CJK payload measures ~2.1x larger on the wire
     * than `.length` reports. A limit documented in bytes has to be enforced
     * in bytes, or it silently does not exist for the users most likely to hit
     * it.
     */
    function jsonByteLength(value) {
        const json = JSON.stringify(value);
        if (!json) return 0;
        return utf8ByteLength(json);
    }

    /** UTF-8 bytes of a string as it stands -- no JSON escaping applied. */
    function utf8ByteLength(json) {
        if (typeof json !== 'string' || !json) return 0;
        if (typeof TextEncoder !== 'undefined') {
            return new TextEncoder().encode(json).length;
        }
        if (typeof Buffer !== 'undefined' && typeof Buffer.byteLength === 'function') {
            return Buffer.byteLength(json, 'utf8');
        }
        // Exact manual fallback: count UTF-8 bytes per code point, pairing
        // surrogates so astral characters are 4 bytes rather than 2 x 3.
        let bytes = 0;
        for (let i = 0; i < json.length; i++) {
            const code = json.charCodeAt(i);
            if (code < 0x80) bytes += 1;
            else if (code < 0x800) bytes += 2;
            else if (code >= 0xd800 && code <= 0xdbff && i + 1 < json.length) {
                const next = json.charCodeAt(i + 1);
                if (next >= 0xdc00 && next <= 0xdfff) {
                    bytes += 4;
                    i += 1;
                    continue;
                }
                bytes += 3;
            } else bytes += 3;
        }
        return bytes;
    }

    /* The UNION of the structured-result keys every operation family persists.
     * An allowlist rather than "any small object" on purpose: a raw response
     * body must never end up in durable local storage, where it could carry
     * anything the server happened to say. A new family adds its keys here
     * deliberately, and the values stay scalar and bounded so an allowed key
     * name cannot smuggle a nested payload through.
     *
     *   all families        code
     *   Work Tags           current_revision, current_state, requested_state, target_tag_id
     *   Work metadata       current_revision, current_value, requested_value
     *   ...byte-limited      current_preview, current_bytes, requested_bytes
     *
     * A byte-limited field reports previews and sizes rather than its values:
     * two large values would blow the size bound below, and a conflict the
     * store cannot persist is a conflict the user never sees -- the settle
     * fails, the coordinator reads it as a failed sync, and the operation
     * returns to pending forever because the same result comes back each
     * retry. The SERVER guarantees the fit (`fit_terminal_result`), measuring
     * serialized bytes rather than characters: one control character is one
     * code point and six bytes as `\u0001`. This bound is the backstop that
     * makes that guarantee enforceable rather than assumed.
     */
    const STRUCTURED_RESULT_KEYS = Object.freeze([
        'code', 'current_revision', 'current_state', 'requested_state', 'target_tag_id',
        'current_value', 'requested_value',
        'current_preview', 'current_bytes', 'requested_bytes',
        /* The source aggregate's conflict reports the server's IDENTITY
         * exactly, alongside the bounded URL preview. The preview is display
         * text that may be shortened to keep the whole result inside
         * MAX_RESULT_BYTES; identity is what the next edit is measured
         * against, so it is carried as its own small, never-truncated pair. */
        'current_provider', 'current_provider_id',
    ]);
    const MAX_RESULT_BYTES = 2048;

    function isValidStructuredResult(value) {
        if (!isPlainObject(value)) return false;
        const entries = Object.entries(value);
        if (entries.some(([key, entry]) => STRUCTURED_RESULT_KEYS.indexOf(key) === -1 ||
            (entry !== null && typeof entry === 'object'))) return false;
        return jsonByteLength(value) <= MAX_RESULT_BYTES;
    }

    /**
     * The payload bound for one operation.
     *
     * The ordinary limit is 64 KiB of serialized JSON and stays that way for
     * every family. A SET_WORK_METADATA_FIELD carrying exactly
     * `{field: <a byte-limited field>, value: <string>}` is the exception, and
     * what is bounded there is the VALUE ITSELF, not its JSON encoding: a
     * quote or a backslash doubles in length under escaping, so measuring the
     * serialized object would refuse a value that is exactly at the product's
     * stated limit -- an Author name full of quotation marks would fail for a
     * reason no user could see. The rest of the payload is two short keys, so
     * nothing meaningful is left unbounded.
     *
     * SET_WORK_SOURCE gets the same allowance for the same reason. The server
     * accepts a source URL of up to `MAX_SOURCE_URL_UTF8_BYTES`, so measuring
     * the serialized envelope here would refuse a URL the server would have
     * taken -- an edit impossible offline and possible online, which is the
     * split contract local-first exists to remove. Its payload is a fixed
     * two-key object around the URL.
     *
     * The shape is checked EXACTLY in both cases. An envelope with an extra
     * key, a non-string value or a field outside the registry falls through to
     * the ordinary bound, so the exception cannot be used to smuggle an
     * unbounded payload.
     */
    function payloadWithinLimit(operation, payload) {
        if (operation === 'SET_WORK_METADATA_FIELD') {
            const keys = Object.keys(payload);
            const limit = WORK_FIELD_VALUE_BYTES[payload.field];
            if (keys.length === 2 && limit !== undefined &&
                Object.prototype.hasOwnProperty.call(payload, 'value') &&
                typeof payload.value === 'string') {
                return utf8ByteLength(payload.value) <= limit;
            }
        }
        if (operation === 'SET_WORK_SOURCE') {
            const source = payload.source;
            if (Object.keys(payload).length === 1 && isPlainObject(source) &&
                Object.keys(source).length === 2 && source.kind === 'video' &&
                typeof source.url === 'string') {
                return utf8ByteLength(source.url) <= WORK_SOURCE_URL_BYTES;
            }
        }
        return jsonByteLength(payload) <= MAX_PAYLOAD_BYTES;
    }

    /**
     * The terminal results a user may answer with "apply mine anyway".
     *
     * Reapplying re-sends the SAME intent against the revision the server
     * reported, so a code qualifies only when it names one: a stale base the
     * user can decide to overwrite. ENTITY_NOT_FOUND and
     * UNSUPPORTED_SOURCE_TRANSITION never do -- there is nothing to overwrite,
     * or the Work is no longer the kind of thing the operation applies to --
     * and offering reapply for them would loop forever.
     *
     * Keyed by FAMILY, because each names its own conflict: the field-scoped
     * code says which field is stale, the aggregate's says the whole source
     * is. One hard-coded string here made every source conflict silently
     * unresolvable -- the UI offered "Apply my source" and the store threw.
     */
    const REAPPLIABLE_RESULTS = Object.freeze({
        ADD_WORK_PERSON_ROLE: Object.freeze(['REVISION_CONFLICT']),
        REMOVE_WORK_PERSON_ROLE: Object.freeze(['REVISION_CONFLICT']),
        SET_WORK_PERSON_ROLE_CREDIT: Object.freeze(['REVISION_CONFLICT']),
        ADD_WORK_TAG: Object.freeze(['REVISION_CONFLICT']),
        REMOVE_WORK_TAG: Object.freeze(['REVISION_CONFLICT']),
        SET_WORK_METADATA_FIELD: Object.freeze(['REVISION_CONFLICT']),
        SET_WORK_SOURCE: Object.freeze(['SOURCE_REVISION_CONFLICT']),
    });

    /**
     * Every UNRESOLVED operation that transitively depends on `opId`.
     *
     * Failure travels the whole graph, not one edge of it. A chain A -> B -> C
     * that stops at B leaves C waiting on a prerequisite that has itself become
     * unresolvable: C is never eligible to send, never surfaced as a decision,
     * and never retired -- durable state the user cannot see or clear.
     *
     * Breadth-first with a visited set, so a row appears at most once however
     * many paths reach it. A diamond (A -> B, A -> C, B -> D, C -> D) must not
     * mark D twice, and the visited set is also what bounds the walk if a cycle
     * ever became representable.
     *
     * `acknowledged` rows end their branch and are not returned. The server has
     * already spoken on them: if it applied the operation, its dependents are
     * legitimately unblocked and nothing below it is doomed; if it refused, the
     * refusal did its own walk from there. Either way there is nothing to
     * revisit. Rows already in `conflict` ARE traversed -- their own dependents
     * are still stranded -- but the caller leaves their recorded result alone,
     * because the reason they are unresolvable is already more specific than
     * "something upstream failed".
     */
    function unresolvedDependentClosure(rows, opId) {
        const dependents = new Map();
        (Array.isArray(rows) ? rows : []).forEach(function (row) {
            if (!row || !Array.isArray(row.depends_on)) return;
            row.depends_on.forEach(function (dep) {
                if (!dependents.has(dep)) dependents.set(dep, []);
                dependents.get(dep).push(row);
            });
        });
        const seen = new Set([opId]);
        const found = [];
        const queue = [opId];
        while (queue.length) {
            const current = queue.shift();
            const waiting = dependents.get(current) || [];
            for (let i = 0; i < waiting.length; i += 1) {
                const row = waiting[i];
                if (seen.has(row.op_id)) continue;
                seen.add(row.op_id);
                if (row.status === STATUS_ACKNOWLEDGED) continue;
                found.push(row);
                queue.push(row.op_id);
            }
        }
        return found;
    }

    /** Thrown for anything the caller could have prevented; carries a code. */
    function localStoreError(code, message) {
        const err = new Error(message);
        err.prksLocalStoreCode = code;
        return err;
    }

    /**
     * Normalizes and validates an operation envelope before it can be
     * persisted. Deliberately strict: durable user data with a malformed
     * envelope is worse than a refused write, because the refusal is visible
     * and the malformed row is not.
     *
     * Generic fields only -- per-operation payload validation belongs to the
     * operation's own validator (server-side authoritative) and arrives with
     * Milestone 2B.
     */
    function normalizeOperationEnvelope(input, context) {
        const ctx = context && typeof context === 'object' ? context : {};
        if (!isPlainObject(input)) {
            throw localStoreError('invalid_envelope', 'Operation envelope must be an object.');
        }
        const operation = input.operation;
        if (!isNonBlankString(operation) || OPERATION_TYPES.indexOf(operation) === -1) {
            throw localStoreError(
                'unknown_operation',
                'Unknown operation type: ' + String(operation)
            );
        }
        if (!isNonBlankString(input.entity_type)) {
            throw localStoreError('invalid_envelope', 'entity_type is required.');
        }
        if (!isNonBlankString(input.entity_id)) {
            throw localStoreError('invalid_envelope', 'entity_id is required.');
        }
        const payload = Object.prototype.hasOwnProperty.call(input, 'payload') ? input.payload : {};
        if (!isPlainObject(payload)) {
            throw localStoreError('invalid_envelope', 'payload must be an object.');
        }
        let withinLimit;
        try {
            withinLimit = payloadWithinLimit(operation, payload);
        } catch (_e) {
            throw localStoreError('invalid_envelope', 'payload must be JSON-serializable.');
        }
        if (!withinLimit) {
            throw localStoreError('payload_too_large', 'Operation payload exceeds the local limit.');
        }
        const dependsOn = Object.prototype.hasOwnProperty.call(input, 'depends_on')
            ? input.depends_on
            : [];
        // Dependencies name other operations, so they must look like op ids.
        // An arbitrary string here would silently never resolve, blocking its
        // dependent forever.
        if (!Array.isArray(dependsOn) || !dependsOn.every(isOperationId)) {
            throw localStoreError('invalid_envelope', 'depends_on must be an array of op ids.');
        }
        if (new Set(dependsOn).size !== dependsOn.length) {
            throw localStoreError('invalid_envelope', 'depends_on repeats an operation.');
        }
        const opId = Object.prototype.hasOwnProperty.call(input, 'op_id') ? input.op_id : ctx.opId;
        if (!isOperationId(opId)) {
            throw localStoreError('invalid_envelope', 'op_id must be a UUID.');
        }
        // An operation cannot wait for itself. Only reachable when a caller
        // supplies its own op_id, and it would be permanently unsendable.
        if (dependsOn.indexOf(opId) !== -1) {
            throw localStoreError('invalid_envelope', 'depends_on names the operation itself.');
        }
        // Durable state must never carry an anonymous operation: the store
        // supplies this, so its absence is an internal error, not user input.
        if (!isNonBlankString(ctx.deviceId)) {
            throw localStoreError('invalid_envelope', 'device_id is required.');
        }
        const baseRevision = Object.prototype.hasOwnProperty.call(input, 'base_revision')
            ? input.base_revision
            : null;
        // Revisions are monotonic counters starting at 0; a negative one is a
        // client bug, and sending it would make the server's staleness check
        // meaningless.
        if (baseRevision !== null && (!Number.isInteger(baseRevision) || baseRevision < 0)) {
            throw localStoreError(
                'invalid_envelope',
                'base_revision must be a non-negative integer or null.'
            );
        }
        const occurredAt = Object.prototype.hasOwnProperty.call(input, 'occurred_at')
            ? input.occurred_at
            : ctx.createdAt;
        // This becomes the ordering key for Recent once open events
        // synchronize, so it must be a real instant -- not merely non-blank.
        if (occurredAt != null && !isParsableTimestamp(occurredAt)) {
            throw localStoreError('invalid_envelope', 'occurred_at must be an ISO timestamp.');
        }
        return {
            // --- immutable semantic envelope ---
            op_id: opId,
            device_id: ctx.deviceId,
            operation: operation,
            entity_type: input.entity_type.trim(),
            entity_id: input.entity_id.trim(),
            payload: JSON.parse(JSON.stringify(payload)),
            base_revision: baseRevision,
            occurred_at: occurredAt || ctx.createdAt,
            created_at: ctx.createdAt,
            sequence: ctx.sequence,
            depends_on: dependsOn.slice(),
            // --- mutable synchronization state ---
            status: STATUS_PENDING,
            attempt_count: 0,
            last_attempt_at: null,
            last_error: null,
            acknowledged_at: null,
            server_revision: null,
        };
    }

    function createPrksLocalStore(deps) {
        const options = deps && typeof deps === 'object' ? deps : {};
        const idbFactory = Object.prototype.hasOwnProperty.call(options, 'indexedDB')
            ? options.indexedDB
            : defaultIndexedDB();
        const now = options.now || defaultNow;
        const uuid = options.uuid || defaultUuid;
        const dbName = options.dbName || DB_NAME;
        const dbVersion = options.dbVersion || DB_VERSION;

        let dbPromise = null;
        // The live connection, so a durable reset can close OUR handle before
        // deleting. IndexedDB blocks a delete on any open connection --
        // including this store's own.
        let openDbHandle = null;

        function ensureStores(db) {
            if (!db.objectStoreNames.contains(STORE_OPERATIONS)) {
                db.createObjectStore(STORE_OPERATIONS, { keyPath: 'op_id' });
            }
            if (!db.objectStoreNames.contains(STORE_METADATA)) {
                db.createObjectStore(STORE_METADATA, { keyPath: 'key' });
            }
        }

        /**
         * Opens the durable database. Unlike the offline cache this REJECTS
         * when storage is unusable: a caller about to record a user's change
         * must find out, not receive a silent "unavailable" and carry on.
         */
        function openDb() {
            if (dbPromise) return dbPromise;
            dbPromise = new Promise(function (resolve, reject) {
                if (!idbFactory) {
                    reject(localStoreError('unavailable', 'IndexedDB is unavailable.'));
                    return;
                }
                let req;
                try {
                    req = idbFactory.open(dbName, dbVersion);
                } catch (e) {
                    reject(localStoreError('unavailable', 'Could not open local storage.'));
                    return;
                }
                if (!req) {
                    reject(localStoreError('unavailable', 'Could not open local storage.'));
                    return;
                }
                req.onupgradeneeded = function () {
                    ensureStores(req.result);
                };
                req.onsuccess = function () {
                    const db = req.result;
                    if (!db) {
                        reject(localStoreError('unavailable', 'Could not open local storage.'));
                        return;
                    }
                    openDbHandle = db;
                    db.onversionchange = function () {
                        try {
                            db.close();
                        } catch (_e) {
                            /* ignore */
                        }
                        if (openDbHandle === db) openDbHandle = null;
                        dbPromise = null;
                    };
                    resolve(db);
                };
                req.onerror = function () {
                    reject(localStoreError('unavailable', 'Could not open local storage.'));
                };
                req.onblocked = function () {
                    reject(localStoreError('blocked', 'Local storage is blocked by another tab.'));
                };
            });
            // A failed open must not be cached forever: a later attempt (after
            // the blocking tab closes, or quota is freed) should retry.
            dbPromise.catch(function () {
                dbPromise = null;
            });
            return dbPromise;
        }

        /**
         * Runs `fn(store...)` in one transaction and resolves ONLY from
         * `oncomplete`, with whatever `fn` recorded via `setResult`. Rejects on
         * request error, transaction error, and transaction abort -- so a
         * request that succeeded inside a transaction that later aborted is
         * reported as the failure it is.
         */
        function runTransaction(storeNames, mode, fn) {
            return openDb().then(function (db) {
                return new Promise(function (resolve, reject) {
                    const names = Array.isArray(storeNames) ? storeNames : [storeNames];
                    let tx;
                    try {
                        tx = db.transaction(names, mode, { durability: 'strict' });
                    } catch (e) {
                        reject(localStoreError('transaction_failed', 'Could not start a local transaction.'));
                        return;
                    }
                    let settled = false;
                    let result;
                    let captured = null;
                    function fail(code, message) {
                        if (settled) return;
                        settled = true;
                        // A domain error (bad envelope, duplicate op_id) is the
                        // real reason this transaction aborted, so report it
                        // rather than the generic rollback it triggered.
                        reject(captured || localStoreError(code, message));
                    }
                    tx.oncomplete = function () {
                        if (settled) return;
                        settled = true;
                        if (captured) {
                            reject(captured);
                            return;
                        }
                        resolve(result);
                    };
                    tx.onerror = function () {
                        fail('write_failed', 'The local transaction failed.');
                    };
                    tx.onabort = function () {
                        fail('write_failed', 'The local transaction was rolled back.');
                    };

                    function request(storeName, run) {
                        return new Promise(function (res, rej) {
                            let r;
                            try {
                                r = run(tx.objectStore(storeName));
                            } catch (e) {
                                rej(e);
                                return;
                            }
                            if (!r) {
                                rej(new Error('No request produced.'));
                                return;
                            }
                            r.onsuccess = function () {
                                res(r.result);
                            };
                            r.onerror = function () {
                                try {
                                    tx.abort();
                                } catch (_e) {
                                    /* ignore */
                                }
                                rej(new Error('The local request failed.'));
                            };
                        });
                    }

                    let outcome;
                    try {
                        outcome = fn(request, function setResult(v) {
                            result = v;
                        });
                    } catch (e) {
                        captured = e;
                        try {
                            tx.abort();
                        } catch (_e) {
                            /* ignore */
                        }
                        return;
                    }
                    Promise.resolve(outcome).catch(function (e) {
                        captured = e && e.prksLocalStoreCode
                            ? e
                            : localStoreError('write_failed', (e && e.message) || 'Local write failed.');
                        try {
                            tx.abort();
                        } catch (_e) {
                            /* ignore */
                        }
                    });
                });
            });
        }

        function readMetadata(key) {
            return runTransaction(STORE_METADATA, 'readonly', function (request, setResult) {
                return request(STORE_METADATA, function (store) {
                    return store.get(key);
                }).then(function (row) {
                    setResult(row ? row.value : null);
                });
            });
        }

        /**
         * Stable per-device identity, created once and persisted durably.
         *
         * This is a SYNCHRONIZATION and DIAGNOSTICS identity only -- never
         * trust, login, or authorization. It is random, not derived from any
         * browser/hardware/network characteristic, so it identifies an install
         * rather than fingerprinting a user. It survives reload, browser
         * restart and "Clear offline cache"; only an explicit reset of durable
         * local state removes it.
         */
        function getOrCreateDeviceId() {
            return runTransaction(STORE_METADATA, 'readwrite', function (request, setResult) {
                return resolveDeviceIdIn(request).then(function (value) {
                    setResult(value);
                });
            });
        }

        function nowIso() {
            return new Date(now()).toISOString();
        }

        /**
         * Persists one semantic operation. Resolves with the stored envelope
         * only after the transaction COMMITTED; rejects otherwise. A caller may
         * show "Saved locally" only on the resolved path.
         *
         * `op_id` is allocated here if absent, and the monotonic `sequence`
         * counter is advanced in the SAME transaction as the operation row, so
         * a crash cannot hand two operations the same sequence.
         */
        /**
         * Reads the durable device id inside an existing transaction,
         * creating it if this is the first write on this device. Sharing the
         * caller's transaction is what makes `device_id` unconditional: it
         * cannot be missing, and it cannot be half-written relative to the
         * operation that carries it.
         */
        function resolveDeviceIdIn(request) {
            return request(STORE_METADATA, function (store) {
                return store.get(META_DEVICE_ID);
            }).then(function (row) {
                if (row && isNonBlankString(row.value)) return row.value;
                const value = uuid();
                return request(STORE_METADATA, function (store) {
                    return store.put({ key: META_DEVICE_ID, value: value, created_at: nowIso() });
                }).then(function () {
                    return value;
                });
            });
        }

        /**
         * Persists one semantic operation. Resolves with the stored envelope
         * only after the transaction COMMITTED; rejects otherwise. A caller may
         * show "Saved locally" only on the resolved path.
         *
         * The store owns device identity and sequence allocation -- callers
         * pass neither. Both are resolved in the SAME transaction as the
         * operation row, so a stored operation can never carry a null
         * `device_id`, and a crash cannot hand two operations one sequence.
         */
        function enqueueOperation(envelope) {
            let prepared;
            const opId =
                isPlainObject(envelope) && isNonBlankString(envelope.op_id)
                    ? envelope.op_id
                    : uuid();
            return runTransaction(
                [STORE_OPERATIONS, STORE_METADATA],
                'readwrite',
                function (request, setResult) {
                    return resolveDeviceIdIn(request)
                        .then(function (deviceId) {
                            return request(STORE_METADATA, function (store) {
                                return store.get(META_SEQUENCE);
                            }).then(function (row) {
                                const next = (row && Number.isInteger(row.value) ? row.value : 0) + 1;
                                prepared = normalizeOperationEnvelope(envelope, {
                                    opId: opId,
                                    deviceId: deviceId,
                                    createdAt: nowIso(),
                                    sequence: next,
                                });
                                return assertDependenciesUsableIn(request, prepared).then(function () {
                                    return request(STORE_OPERATIONS, function (store) {
                                        return store.get(prepared.op_id);
                                    });
                                }).then(function (existing) {
                                    if (existing) {
                                        throw localStoreError(
                                            'duplicate_op_id',
                                            'An operation with this op_id already exists.'
                                        );
                                    }
                                    return request(STORE_METADATA, function (store) {
                                        return store.put({ key: META_SEQUENCE, value: next });
                                    });
                                });
                            });
                        })
                        .then(function () {
                            return request(STORE_OPERATIONS, function (store) {
                                return store.put(prepared);
                            });
                        })
                        .then(function () {
                            setResult(prepared);
                        });
                }
            );
        }

        /**
         * Every prerequisite must already exist AND still be able to succeed.
         *
         * An unknown id is not a dependency, it is a permanent block: nothing
         * will ever acknowledge it, so its dependent can never be sent and the
         * user's change is stranded with no way to see why. An id that names an
         * operation the server already refused is the same block wearing a
         * different face, which is why existence alone is not the test.
         *
         * The single boundary for both enqueue paths. Writing a doomed row and
         * settling it afterwards would be strictly worse than refusing it: the
         * caller is inside a save the user is watching, so it can say so.
         *
         * This is also what makes cycles impossible without a graph walk. A
         * prerequisite has to exist before anything can name it, so a later
         * operation can only ever depend on an earlier one -- there is no
         * ordering in which two operations could name each other.
         */
        async function assertDependenciesUsableIn(request, prepared) {
            const deps = prepared.depends_on || [];
            if (!deps.length) return;
            const existing = await request(STORE_OPERATIONS, s => s.getAll());
            const byId = new Map((Array.isArray(existing) ? existing : [])
                .filter(Boolean).map(r => [r.op_id, r]));
            for (let i = 0; i < deps.length; i += 1) {
                const prerequisite = byId.get(deps[i]);
                if (!prerequisite) {
                    throw localStoreError('invalid_envelope', 'depends_on names an unknown operation.');
                }
                /* Existence is necessary but NOT sufficient. A terminally
                 * refused prerequisite is still in the store -- it is retained
                 * precisely because something already depends on it -- and
                 * naming it would mint an operation that is born unsendable:
                 * readiness can never be satisfied, so it would sit as
                 * `pending` forever with nothing to explain it. Refusing at
                 * enqueue is the only point where the user is still there to
                 * be told. */
                if (dependencyTerminallyFailed(prerequisite)) {
                    throw localStoreError('dependency_failed',
                        'depends_on names an operation that already failed: ' +
                        prerequisite.op_id + ' (' +
                        String((prerequisite.server_result || {}).code || 'unknown') + ').');
                }
            }
        }

        async function insertEnvelopeIn(request, envelope, localContext) {
            const deviceId = await resolveDeviceIdIn(request);
            const row = await request(STORE_METADATA, s => s.get(META_SEQUENCE));
            const sequence = (row ? row.value : 0) + 1;
            const prepared = normalizeOperationEnvelope(envelope, {
                opId: uuid(), deviceId, sequence, createdAt: nowIso(),
            });
            await assertDependenciesUsableIn(request, prepared);
            if (localContext != null) {
                if (jsonByteLength(localContext) > 4096) throw localStoreError('invalid_context', 'Local context too large.');
                prepared.local_context = JSON.parse(JSON.stringify(localContext));
            }
            await request(STORE_METADATA, s => s.put({ key: META_SEQUENCE, value: sequence }));
            if (await request(STORE_OPERATIONS, s => s.get(prepared.op_id))) throw localStoreError('duplicate_op_id', 'Duplicate operation id.');
            await request(STORE_OPERATIONS, s => s.put(prepared));
            return prepared;
        }

        /* One desired state per relationship, checked and changed atomically.
         * Only NEVER SENT pending rows may be canceled. A pending retry may
         * already be ledgered by the server after a lost response; keep its id.
         * No immutable envelope is ever rewritten. */
        /** The `CREATE_TAG` a Tag-scoped operation must wait for, if any. */
        function tagCreationDependency(rows, tagId, consequence) {
            return creationDependency(rows, 'CREATE_TAG', 'tag', tagId,
                'This tag could not be created on the server, so ' + consequence + '.');
        }

        /* A Tag already carrying a pending deletion accepts nothing else: every
         * later operation naming it could only come back TAG_DELETED. */
        function assertTagIsNotBeingDeleted(rows, tagId, verb) {
            const pendingDelete = (rows || []).find(r => r && r.operation === 'DELETE_TAG' &&
                r.entity_id === tagId && r.status !== STATUS_ACKNOWLEDGED);
            if (pendingDelete) {
                throw localStoreError('entity_deleted',
                    'This tag is being deleted, so it cannot be ' + verb + '.');
            }
        }

        /**
         * Create a Tag under an id this device mints.
         *
         * The NAME is unique across canonical names and aliases, and only the
         * server sees every Tag -- so this refuses an obvious local collision
         * early, as a better error sooner, while the authoritative answer stays
         * canonical. `known` is the effective catalogue the caller is showing.
         */
        function createTag(fields, known) {
            const src = isPlainObject(fields) ? fields : {};
            const name = String(src.name == null ? '' : src.name).trim();
            if (!name) {
                return Promise.reject(localStoreError('invalid_envelope', 'A tag needs a name.'));
            }
            const clash = (Array.isArray(known) ? known : []).find(row => row &&
                String(row.name || '').toLowerCase() === name.toLowerCase());
            if (clash) {
                return Promise.reject(localStoreError('name_taken',
                    'A tag called ' + name + ' already exists.'));
            }
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite',
                async (request, setResult) => {
                    setResult(await insertEnvelopeIn(request, {
                        operation: 'CREATE_TAG', entity_type: 'tag',
                        entity_id: generateEntityId('T', uuid),
                        payload: { name: name, color: String(src.color || '#6d6cf7') },
                        base_revision: null,
                    }, null));
                });
        }

        /**
         * Delete a Tag, cancelling the relationship intents it makes pointless.
         *
         * The same rule every other destruction uses: an unsynchronized
         * operation naming this Tag that was NEVER attempted is cancelled --
         * attaching a Tag immediately before deleting it asks the server to do
         * work the next operation destroys -- and one that may be on the wire
         * is waited for instead of rewritten.
         */
        function deleteTag(tagId) {
            if (!isNonBlankString(tagId)) {
                return Promise.reject(localStoreError('invalid_envelope', 'Invalid tag.'));
            }
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite',
                async (request, setResult) => {
                    const rows = await request(STORE_OPERATIONS, s => s.getAll());
                    const mine = rows.filter(r => r && r.status !== STATUS_ACKNOWLEDGED &&
                        ((r.entity_type === 'tag' && r.entity_id === tagId) ||
                            (!!r.payload && r.payload.tag_id === tagId)));
                    const already = mine.find(r => r.operation === 'DELETE_TAG');
                    if (already) { setResult(already); return; }
                    const neverSent = r => r.status === STATUS_PENDING && !r.attempt_count;
                    const creation = mine.find(r => r.operation === 'CREATE_TAG');
                    if (creation && neverSent(creation) && mine.every(neverSent)) {
                        for (const row of mine) {
                            await request(STORE_OPERATIONS, s => s.delete(row.op_id));
                        }
                        setResult(null);
                        return;
                    }
                    const waitFor = [];
                    for (const row of mine) {
                        if (neverSent(row) && row.operation !== 'CREATE_TAG') {
                            await request(STORE_OPERATIONS, s => s.delete(row.op_id));
                        } else {
                            waitFor.push(row.op_id);
                        }
                    }
                    setResult(await insertEnvelopeIn(request, {
                        operation: 'DELETE_TAG', entity_type: 'tag', entity_id: tagId,
                        payload: {}, base_revision: null, depends_on: waitFor,
                    }, null));
                });
        }

        function coalesceWorkTag(workId, tagId, present, baseState, baseRevision, tag) {
            if (typeof present !== 'boolean' || typeof baseState !== 'boolean' ||
                !Number.isSafeInteger(baseRevision) || baseRevision < 0) {
                return Promise.reject(localStoreError('invalid_base', 'Invalid relationship base.'));
            }
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite', async (request, setResult) => {
                const rows = await request(STORE_OPERATIONS, s => s.getAll());
                assertTagIsNotBeingDeleted(rows, tagId, 'attached or removed');
                /* A Tag this device created and has not sent yet: the
                 * relationship waits for it, by the generic mechanism. */
                const createOp = tagCreationDependency(rows, tagId,
                    'it cannot be attached to anything');
                const existing = rows.find(r => r.entity_type === 'work' && r.entity_id === workId &&
                    r.payload.tag_id === tagId && r.status !== STATUS_ACKNOWLEDGED);
                if (existing) {
                    if (existing.status !== STATUS_PENDING || existing.attempt_count > 0) {
                        throw localStoreError('scope_busy', 'This Tag change is syncing or needs resolution.');
                    }
                    if ((existing.operation === 'ADD_WORK_TAG') === present) { setResult(existing); return; }
                    await request(STORE_OPERATIONS, s => s.delete(existing.op_id));
                    setResult(null);
                    return;
                }
                if (present === baseState) { setResult(null); return; }
                setResult(await insertEnvelopeIn(request, {
                    operation: present ? 'ADD_WORK_TAG' : 'REMOVE_WORK_TAG', entity_type: 'work',
                    entity_id: workId, payload: { tag_id: tagId }, base_revision: baseRevision,
                    depends_on: createOp ? [createOp.op_id] : [],
                }, { tag }));
            });
        }

        /**
         * Save the intent "this Work's link to this Person in this role is
         * now <state>", coalescing within the scope.
         *
         * The element's canonical state is `null` (absent) or a credit-name
         * string (present; `''` means no override). That is not a boolean:
         * `credit_name` is the name printed on THIS work, and it reaches
         * `linked_authors`, the card credit, BibTeX and the Person's aliases.
         *
         * `observed` is the base this edit was measured against -- its `state`
         * in the same spelling, and its `revision`. Editing back to that state
         * leaves NO intent: add-then-remove is not two changes, it is none.
         *
         * Only a NEVER SENT row may be rewritten. A row that has been
         * attempted might already be ledgered, and a conflicted one is the
         * user's to resolve, so either stays immutable and this refuses with
         * `scope_busy`.
         */
        function saveWorkPersonRole(workId, link, observed, localContext) {
            const valid = link && typeof link === 'object' &&
                isNonBlankString(link.person_id) && isNonBlankString(link.role_type) &&
                (link.state === null || typeof link.state === 'string');
            if (!isNonBlankString(workId) || !valid) {
                return Promise.reject(localStoreError('invalid_envelope', 'Invalid role save.'));
            }
            if (!isPlainObject(observed) ||
                !(observed.state === null || typeof observed.state === 'string') ||
                !Number.isSafeInteger(observed.revision) || observed.revision < 0) {
                return Promise.reject(localStoreError('invalid_base', 'Invalid observed role state.'));
            }
            const desired = link.state;
            const matches = row => row.entity_type === 'work' && row.entity_id === workId &&
                WORK_ROLE_OPERATIONS.indexOf(row.operation) !== -1 &&
                row.payload.person_id === link.person_id &&
                row.payload.role_type === link.role_type &&
                row.status !== STATUS_ACKNOWLEDGED;
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite', async (request, setResult) => {
                const allRows = await request(STORE_OPERATIONS, s => s.getAll());
                assertPersonIsNotBeingDeleted(allRows, link.person_id, 'credited on a file');
                const rows = allRows.filter(matches)
                    .sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
                /* One active intent per scope is the invariant, but a store
                 * written before coalescing existed can hold several. Order
                 * from `getAll()` is not a decision, so this refuses with the
                 * count rather than resolving that history differently on
                 * different devices. The rows are immutable user intent. */
                if (rows.length > 1) {
                    throw localStoreError('scope_busy',
                        'This link has ' + rows.length + ' unsynchronized changes; ' +
                        'let them finish or resolve them before editing it again.');
                }
                const existing = rows[0];
                if (existing) {
                    if (existing.status !== STATUS_PENDING || existing.attempt_count > 0) {
                        throw localStoreError('scope_busy', 'This link is syncing or needs resolution.');
                    }
                    if (workPersonRoleState(existing) === desired) { setResult(existing); return; }
                    await request(STORE_OPERATIONS, s => s.delete(existing.op_id));
                }
                if (desired === observed.state) { setResult(null); return; }
                /* ADD names a present state from absence; SET_CREDIT names a
                 * present state that was already present. Using ADD for both
                 * would make an operation called ADD silently edit an existing
                 * link, which is harder to reason about on replay. */
                const operation = desired === null ? 'REMOVE_WORK_PERSON_ROLE'
                    : (observed.state === null ? 'ADD_WORK_PERSON_ROLE'
                        : 'SET_WORK_PERSON_ROLE_CREDIT');
                const payload = { person_id: link.person_id, role_type: link.role_type };
                if (operation !== 'REMOVE_WORK_PERSON_ROLE') payload.credit_name = desired;
                /* Searched over EVERY row, not the role-scoped ones.
                 *
                 * `rows` is filtered to this element's own three operation
                 * types, so a CREATE_PERSON could never appear in it and the
                 * dependency was silently always empty -- the role would then
                 * be sent before the Person existed and the server would refuse
                 * it. A link to a Person this device created and has not yet
                 * synchronized MUST wait for that creation. */
                const createOp = personCreationDependency(allRows, link.person_id,
                    'they cannot be linked');
                setResult(await insertEnvelopeIn(request, {
                    operation, entity_type: 'work', entity_id: workId, payload,
                    base_revision: observed.revision,
                    depends_on: createOp ? [createOp.op_id] : [],
                }, localContext || null));
            });
        }

        /**
         * Save the intent "this Person's <field> is now <value>", per FIELD.
         *
         * One conflict unit per field, matching the server family: two devices
         * that changed a biography and a birth date have not disagreed, and a
         * profile-wide unit would tell them they had. So a scope that is busy
         * blocks only its own field and the rest of the form stays editable.
         *
         * `base[field]` is `{value, revision}` -- the value from the cached
         * Person, the revision from the person-metadata-state projection.
         * Editing back to the observed value leaves NO intent at all: A -> B
         * -> A is not two changes, it is none.
         *
         * A Person who exists only because of a pending `CREATE_PERSON` is
         * edited through this same path. The edit is ordered behind that
         * creation by the GENERIC dependency mechanism rather than folded into
         * its payload: the creation may already be in flight, and rewriting an
         * envelope that might have been sent is the one way to apply it twice.
         * Two decisions the user made separately also stay two operations, so
         * a refused creation does not silently take an unrelated edit with it
         * -- it fails it visibly, through the same propagation as everything
         * else.
         */
        function savePersonMetadataFields(personId, changes, base) {
            if (!isNonBlankString(personId) || !isPlainObject(changes) || !isPlainObject(base)) {
                return Promise.reject(localStoreError('invalid_envelope', 'Invalid profile save.'));
            }
            for (const field of Object.keys(changes)) {
                const observed = base[field];
                if (PERSON_FIELDS.indexOf(field) === -1) {
                    return Promise.reject(localStoreError('unknown_field',
                        'Not an editable profile field: ' + field));
                }
                if (typeof changes[field] !== 'string' || !isPlainObject(observed) ||
                    typeof observed.value !== 'string' ||
                    !Number.isSafeInteger(observed.revision) || observed.revision < 0) {
                    return Promise.reject(localStoreError('invalid_base', 'Invalid observed field state.'));
                }
            }
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite', async (request, setResult) => {
                const rows = await request(STORE_OPERATIONS, s => s.getAll());
                assertPersonIsNotBeingDeleted(rows, personId, 'edited');
                const createOp = personCreationDependency(rows, personId,
                    'their profile cannot be edited');
                const written = [];
                for (const field of Object.keys(changes)) {
                    const desired = changes[field];
                    const observed = base[field];
                    const existing = rows.find(r => r.operation === 'SET_PERSON_METADATA_FIELD' &&
                        r.entity_type === 'person' && r.entity_id === personId &&
                        r.payload.field === field && r.status !== STATUS_ACKNOWLEDGED);
                    if (existing) {
                        /* Only a NEVER SENT row may be rewritten. A retry after
                         * a lost response might already be ledgered, and a
                         * conflict is the user's to resolve -- but this is one
                         * field, so every other field stays editable. */
                        if (existing.status !== STATUS_PENDING || existing.attempt_count > 0) {
                            throw localStoreError('scope_busy', 'This field is syncing or needs resolution.');
                        }
                        if (existing.payload.value === desired) { written.push(existing); continue; }
                        await request(STORE_OPERATIONS, s => s.delete(existing.op_id));
                    }
                    if (desired === observed.value) continue;
                    written.push(await insertEnvelopeIn(request, {
                        operation: 'SET_PERSON_METADATA_FIELD', entity_type: 'person',
                        entity_id: personId, payload: { field, value: desired },
                        base_revision: observed.revision,
                        depends_on: createOp ? [createOp.op_id] : [],
                    }, null));
                }
                setResult(written);
            });
        }

        function createPerson(fields) {
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite', async (request, setResult) => {
                setResult(await insertEnvelopeIn(request, {
                    operation: 'CREATE_PERSON',
                    entity_type: 'person',
                    entity_id: generateEntityId('P', uuid),
                    payload: canonicalPersonPayload(fields),
                    base_revision: null,
                }));
            });
        }

        /* A Person already carrying a pending deletion accepts nothing else:
         * every later operation naming them could only be refused. */
        function assertPersonIsNotBeingDeleted(rows, personId, verb) {
            const pendingDelete = (rows || []).find(r => r && r.operation === 'DELETE_PERSON' &&
                r.entity_id === personId && r.status !== STATUS_ACKNOWLEDGED);
            if (pendingDelete) {
                throw localStoreError('entity_deleted',
                    'This person is being deleted, so they cannot be ' + verb + '.');
            }
        }

        /**
         * Delete a Person, cancelling what was never sent.
         *
         * The same rule the Group family uses, over a wider set: an operation
         * naming this Person that has NEVER been attempted is cancelled, since
         * sending "credit them on this file" immediately before "delete them"
         * asks the server to do work the next operation destroys -- and would
         * make the deletion fail, because a credited Person is protected. A row
         * that may already be on the wire stays immutable and the deletion is
         * ordered behind it, so the server sees the decisions in the order they
         * were made and refuses the deletion if the link did land.
         *
         * A Person created on this device and never sent folds away entirely.
         */
        function deletePerson(personId) {
            if (!isNonBlankString(personId)) {
                return Promise.reject(localStoreError('invalid_envelope', 'Invalid person.'));
            }
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite',
                async (request, setResult) => {
                    const rows = await request(STORE_OPERATIONS, s => s.getAll());
                    const mine = operationsNamingPerson(rows, personId);
                    const already = mine.find(r => r.operation === 'DELETE_PERSON');
                    if (already) { setResult(already); return; }
                    const neverSent = r => r.status === STATUS_PENDING && !r.attempt_count;
                    const creation = mine.find(r => r.operation === 'CREATE_PERSON');
                    if (creation && neverSent(creation) && mine.every(neverSent)) {
                        for (const row of mine) {
                            await request(STORE_OPERATIONS, s => s.delete(row.op_id));
                        }
                        setResult(null);
                        return;
                    }
                    const waitFor = [];
                    for (const row of mine) {
                        if (neverSent(row) && row.operation !== 'CREATE_PERSON') {
                            await request(STORE_OPERATIONS, s => s.delete(row.op_id));
                        } else {
                            waitFor.push(row.op_id);
                        }
                    }
                    setResult(await insertEnvelopeIn(request, {
                        operation: 'DELETE_PERSON', entity_type: 'person',
                        entity_id: personId, payload: {},
                        base_revision: null, depends_on: waitFor,
                    }, null));
                });
        }

        /* ---- Folders: construction, fields, filing, deletion ---- */

        function folderCreationDependency(rows, folderId, consequence) {
            return creationDependency(rows, 'CREATE_FOLDER', 'folder', folderId,
                'This folder could not be created on the server, so ' + consequence + '.');
        }

        function assertFolderIsNotBeingDeleted(rows, folderId, verb) {
            const pendingDelete = (rows || []).find(r => r && r.operation === 'DELETE_FOLDER' &&
                r.entity_id === folderId && r.status !== STATUS_ACKNOWLEDGED);
            if (pendingDelete) {
                throw localStoreError('entity_deleted',
                    'This folder is being deleted, so it cannot be ' + verb + '.');
            }
        }

        function createFolder(fields) {
            const payload = canonicalFolderPayload(fields);
            if (!payload.title) payload.title = 'Untitled Folder';
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite',
                async (request, setResult) => {
                    const rows = await request(STORE_OPERATIONS, s => s.getAll());
                    /* A folder created INSIDE one this device also created
                     * waits for it: the server validates the hierarchy, and a
                     * parent it has never heard of is a refusal rather than a
                     * tree. */
                    const parentOp = payload.parent_id
                        ? folderCreationDependency(rows, payload.parent_id,
                            'a folder cannot be created inside it')
                        : null;
                    setResult(await insertEnvelopeIn(request, {
                        operation: 'CREATE_FOLDER', entity_type: 'folder',
                        entity_id: generateEntityId('F', uuid),
                        payload: payload, base_revision: null,
                        depends_on: parentOp ? [parentOp.op_id] : [],
                    }, null));
                });
        }

        /** One Save, however many of a folder's fields it touched. */
        function saveFolderFields(folderId, changes, base) {
            if (!isNonBlankString(folderId) || !isPlainObject(changes) || !isPlainObject(base)) {
                return Promise.reject(localStoreError('invalid_envelope', 'Invalid folder save.'));
            }
            for (const field of Object.keys(changes)) {
                const observed = base[field];
                if (FOLDER_FIELDS.indexOf(field) === -1) {
                    return Promise.reject(localStoreError('unknown_field',
                        'Not an editable folder field: ' + field));
                }
                if (typeof changes[field] !== 'string' || !isPlainObject(observed) ||
                    typeof observed.value !== 'string' ||
                    !Number.isSafeInteger(observed.revision) || observed.revision < 0) {
                    return Promise.reject(localStoreError('invalid_base',
                        'Invalid observed field state.'));
                }
            }
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite',
                async (request, setResult) => {
                    const rows = await request(STORE_OPERATIONS, s => s.getAll());
                    assertFolderIsNotBeingDeleted(rows, folderId, 'edited');
                    const createOp = folderCreationDependency(rows, folderId,
                        'it cannot be edited');
                    const written = [];
                    for (const field of Object.keys(changes)) {
                        const desired = changes[field];
                        const observed = base[field];
                        const existing = rows.find(r => r.operation === 'SET_FOLDER_FIELD' &&
                            r.entity_type === 'folder' && r.entity_id === folderId &&
                            r.payload.field === field && r.status !== STATUS_ACKNOWLEDGED);
                        if (existing) {
                            if (existing.status !== STATUS_PENDING || existing.attempt_count > 0) {
                                throw localStoreError('scope_busy',
                                    'This field is syncing or needs resolution.');
                            }
                            if (existing.payload.value === desired) {
                                written.push(existing);
                                continue;
                            }
                            await request(STORE_OPERATIONS, s => s.delete(existing.op_id));
                        }
                        if (desired === observed.value) continue;
                        const parentOp = field === 'parent_id' && desired
                            ? folderCreationDependency(rows, desired,
                                'nothing can be moved into it')
                            : null;
                        written.push(await insertEnvelopeIn(request, {
                            operation: 'SET_FOLDER_FIELD', entity_type: 'folder',
                            entity_id: folderId, payload: { field, value: desired },
                            base_revision: observed.revision,
                            depends_on: [createOp, parentOp].filter(Boolean)
                                .map(op => op.op_id),
                        }, null));
                    }
                    setResult(written);
                });
        }

        /**
         * "This Work is now filed in that folder", coalescing.
         *
         * A Work is in at most ONE folder, so this is a scalar on the Work and
         * `''` means "in no folder". Filing it back where it already was leaves
         * no intent at all. `observed` is `{folder_id, revision}`.
         */
        function setWorkFolder(workId, folderId, observed, localContext) {
            if (!isNonBlankString(workId) || typeof folderId !== 'string' ||
                !isPlainObject(observed) || typeof observed.folder_id !== 'string' ||
                !Number.isSafeInteger(observed.revision) || observed.revision < 0) {
                return Promise.reject(localStoreError('invalid_envelope', 'Invalid filing.'));
            }
            const desired = folderId.trim();
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite',
                async (request, setResult) => {
                    const rows = await request(STORE_OPERATIONS, s => s.getAll());
                    if (desired) assertFolderIsNotBeingDeleted(rows, desired, 'filed into');
                    const existing = rows.find(r => r.operation === 'SET_WORK_FOLDER' &&
                        r.entity_type === 'work' && r.entity_id === workId &&
                        r.status !== STATUS_ACKNOWLEDGED);
                    if (existing) {
                        if (existing.status !== STATUS_PENDING || existing.attempt_count > 0) {
                            throw localStoreError('scope_busy',
                                'This file\u2019s folder is syncing or needs resolution.');
                        }
                        if (existing.payload.folder_id === desired) { setResult(existing); return; }
                        await request(STORE_OPERATIONS, s => s.delete(existing.op_id));
                    }
                    if (desired === observed.folder_id) { setResult(null); return; }
                    const createOp = desired
                        ? folderCreationDependency(rows, desired, 'nothing can be filed in it')
                        : null;
                    setResult(await insertEnvelopeIn(request, {
                        operation: 'SET_WORK_FOLDER', entity_type: 'work', entity_id: workId,
                        payload: { folder_id: desired }, base_revision: observed.revision,
                        depends_on: createOp ? [createOp.op_id] : [],
                    }, localContext || null));
                });
        }

        /**
         * Delete a folder, cancelling what was never sent.
         *
         * The same rule every other destruction uses. A Work this device had
         * filed INTO the folder counts as naming it: sending "file it here"
         * immediately before "delete this" asks the server to do work the next
         * operation destroys -- and would make the deletion fail, because a
         * folder holding files is protected.
         */
        function deleteFolder(folderId) {
            if (!isNonBlankString(folderId)) {
                return Promise.reject(localStoreError('invalid_envelope', 'Invalid folder.'));
            }
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite',
                async (request, setResult) => {
                    const rows = await request(STORE_OPERATIONS, s => s.getAll());
                    const mine = operationsNamingFolder(rows, folderId);
                    const already = mine.find(r => r.operation === 'DELETE_FOLDER');
                    if (already) { setResult(already); return; }
                    const neverSent = r => r.status === STATUS_PENDING && !r.attempt_count;
                    const creation = mine.find(r => r.operation === 'CREATE_FOLDER');
                    if (creation && neverSent(creation) && mine.every(neverSent)) {
                        for (const row of mine) {
                            await request(STORE_OPERATIONS, s => s.delete(row.op_id));
                        }
                        setResult(null);
                        return;
                    }
                    const waitFor = [];
                    for (const row of mine) {
                        if (neverSent(row) && row.operation !== 'CREATE_FOLDER') {
                            await request(STORE_OPERATIONS, s => s.delete(row.op_id));
                        } else {
                            waitFor.push(row.op_id);
                        }
                    }
                    setResult(await insertEnvelopeIn(request, {
                        operation: 'DELETE_FOLDER', entity_type: 'folder',
                        entity_id: folderId, payload: {},
                        base_revision: null, depends_on: waitFor,
                    }, null));
                });
        }

        /* ---- Person Groups: construction, fields, membership, deletion ---- */

        function createPersonGroup(fields) {
            const payload = canonicalPersonGroupPayload(fields);
            if (!payload.name) {
                return Promise.reject(localStoreError('invalid_envelope',
                    'A group needs a name.'));
            }
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite',
                async (request, setResult) => {
                    const rows = await request(STORE_OPERATIONS, s => s.getAll());
                    /* A group created under a parent this device also created
                     * offline waits for that parent: the server validates the
                     * hierarchy, and a parent it has never heard of is a
                     * refusal rather than a tree. */
                    const parentOp = payload.parent_id
                        ? personGroupCreationDependency(rows, payload.parent_id,
                            'a group cannot be created inside it')
                        : null;
                    setResult(await insertEnvelopeIn(request, {
                        operation: 'CREATE_PERSON_GROUP',
                        entity_type: 'person-group',
                        entity_id: generateEntityId('PG', uuid),
                        payload: payload,
                        base_revision: null,
                        depends_on: parentOp ? [parentOp.op_id] : [],
                    }, null));
                });
        }

        /**
         * One Save, however many of a group's fields it touched.
         *
         * `changes` is field -> desired value; `base` is the ACKNOWLEDGED state
         * (field -> {value, revision}). A field whose desired value already
         * matches its base produces nothing, and an existing never-sent intent
         * for it is CANCELLED -- editing back to what the server holds is not a
         * change, and leaving the row would send a write the server does not
         * need and a revision it would advance.
         */
        function savePersonGroupFields(groupId, changes, base) {
            if (!isNonBlankString(groupId) || !isPlainObject(changes) || !isPlainObject(base)) {
                return Promise.reject(localStoreError('invalid_envelope', 'Invalid group save.'));
            }
            for (const field of Object.keys(changes)) {
                const observed = base[field];
                if (PERSON_GROUP_FIELDS.indexOf(field) === -1) {
                    return Promise.reject(localStoreError('unknown_field',
                        'Not an editable group field: ' + field));
                }
                if (typeof changes[field] !== 'string' || !isPlainObject(observed) ||
                    typeof observed.value !== 'string' ||
                    !Number.isSafeInteger(observed.revision) || observed.revision < 0) {
                    return Promise.reject(localStoreError('invalid_base',
                        'Invalid observed field state.'));
                }
            }
            if (typeof changes.name === 'string' && !changes.name.trim()) {
                return Promise.reject(localStoreError('invalid_envelope',
                    'A group needs a name.'));
            }
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite',
                async (request, setResult) => {
                    const rows = await request(STORE_OPERATIONS, s => s.getAll());
                    assertGroupIsNotBeingDeleted(rows, groupId, 'edited');
                    const createOp = personGroupCreationDependency(rows, groupId,
                        'it cannot be edited');
                    const written = [];
                    for (const field of Object.keys(changes)) {
                        const desired = changes[field];
                        const observed = base[field];
                        const existing = rows.find(r => r.operation === 'SET_PERSON_GROUP_FIELD' &&
                            r.entity_type === 'person-group' && r.entity_id === groupId &&
                            r.payload.field === field && r.status !== STATUS_ACKNOWLEDGED);
                        if (existing) {
                            if (existing.status !== STATUS_PENDING || existing.attempt_count > 0) {
                                throw localStoreError('scope_busy',
                                    'This field is syncing or needs resolution.');
                            }
                            if (existing.payload.value === desired) {
                                written.push(existing);
                                continue;
                            }
                            await request(STORE_OPERATIONS, s => s.delete(existing.op_id));
                        }
                        if (desired === observed.value) continue;
                        /* Moving a group INTO one this device also created
                         * offline waits for that group too: the server
                         * validates the hierarchy, and a parent it has never
                         * heard of is a refusal rather than a tree. */
                        const parentOp = field === 'parent_id' && desired
                            ? personGroupCreationDependency(rows, desired,
                                'nothing can be moved into it')
                            : null;
                        written.push(await insertEnvelopeIn(request, {
                            operation: 'SET_PERSON_GROUP_FIELD', entity_type: 'person-group',
                            entity_id: groupId, payload: { field, value: desired },
                            base_revision: observed.revision,
                            depends_on: [createOp, parentOp].filter(Boolean)
                                .map(op => op.op_id),
                        }, null));
                    }
                    setResult(written);
                });
        }

        /**
         * "This person is / is not in this group", coalescing to one intent.
         *
         * The pair is the conflict unit, so adding someone and then removing
         * them again before either was sent leaves NOTHING -- it is not two
         * changes, it is none. `observed` is the acknowledged state of the
         * pair: `{present, revision}`.
         */
        function setPersonGroupMember(groupId, personId, present, observed) {
            if (!isNonBlankString(groupId) || !isNonBlankString(personId) ||
                !isPlainObject(observed) || typeof observed.present !== 'boolean' ||
                !Number.isSafeInteger(observed.revision) || observed.revision < 0) {
                return Promise.reject(localStoreError('invalid_envelope',
                    'Invalid membership change.'));
            }
            const desired = !!present;
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite',
                async (request, setResult) => {
                    const rows = await request(STORE_OPERATIONS, s => s.getAll());
                    assertGroupIsNotBeingDeleted(rows, groupId, 'changed');
                    const existing = rows.find(r =>
                        PERSON_GROUP_MEMBER_OPERATIONS.indexOf(r.operation) !== -1 &&
                        r.entity_type === 'person-group' && r.entity_id === groupId &&
                        r.payload.person_id === personId && r.status !== STATUS_ACKNOWLEDGED);
                    if (existing) {
                        if (existing.status !== STATUS_PENDING || existing.attempt_count > 0) {
                            throw localStoreError('scope_busy',
                                'This membership is syncing or needs resolution.');
                        }
                        const already = existing.operation === 'ADD_PERSON_GROUP_MEMBER';
                        if (already === desired) { setResult(existing); return; }
                        await request(STORE_OPERATIONS, s => s.delete(existing.op_id));
                    }
                    if (desired === observed.present) { setResult(null); return; }
                    assertPersonIsNotBeingDeleted(rows, personId, 'put in a group');
                    const createOp = personGroupCreationDependency(rows, groupId,
                        'nobody can be added to it');
                    const personOp = personCreationDependency(rows, personId,
                        'they cannot be added to a group');
                    setResult(await insertEnvelopeIn(request, {
                        operation: desired ? 'ADD_PERSON_GROUP_MEMBER'
                            : 'REMOVE_PERSON_GROUP_MEMBER',
                        entity_type: 'person-group', entity_id: groupId,
                        payload: { person_id: personId },
                        base_revision: observed.revision,
                        depends_on: [createOp, personOp].filter(Boolean).map(op => op.op_id),
                    }, null));
                });
        }

        /* A group already carrying a pending deletion accepts nothing else.
         *
         * The alternative is an edit whose only possible outcome is
         * ENTITY_NOT_FOUND -- born unsendable, and refused here in the terms
         * the user was working in rather than by the server later. */
        function assertGroupIsNotBeingDeleted(rows, groupId, verb) {
            const pendingDelete = rows.find(r => r && r.operation === 'DELETE_PERSON_GROUP' &&
                r.entity_id === groupId && r.status !== STATUS_ACKNOWLEDGED);
            if (pendingDelete) {
                throw localStoreError('entity_deleted',
                    'This group is being deleted, so it cannot be ' + verb + '.');
            }
        }

        /**
         * Delete a group, cancelling what was never sent.
         *
         * Every unsynchronized operation naming this group is about to become
         * meaningless. A row that has NEVER been attempted is cancelled: it
         * exists only on this device, and sending "rename it" immediately
         * before "delete it" asks the server to do work whose result the next
         * operation destroys. A row that may already be on the wire is left
         * alone -- rewriting a sent envelope is the one way to apply it twice
         * -- and the deletion is ordered behind it instead, so the server sees
         * the user's decisions in the order they made them.
         *
         * A group created on this device and never sent is the whole case
         * folding away: the creation is cancelled too, and nothing about the
         * group ever reaches the server.
         */
        function deletePersonGroup(groupId) {
            if (!isNonBlankString(groupId)) {
                return Promise.reject(localStoreError('invalid_envelope', 'Invalid group.'));
            }
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite',
                async (request, setResult) => {
                    const rows = await request(STORE_OPERATIONS, s => s.getAll());
                    const mine = rows.filter(r => r &&
                        PERSON_GROUP_OPERATIONS.indexOf(r.operation) !== -1 &&
                        r.entity_id === groupId && r.status !== STATUS_ACKNOWLEDGED);
                    const already = mine.find(r => r.operation === 'DELETE_PERSON_GROUP');
                    if (already) { setResult(already); return; }
                    const neverSent = r => r.status === STATUS_PENDING && !r.attempt_count;
                    const creation = mine.find(r => r.operation === 'CREATE_PERSON_GROUP');
                    if (creation && neverSent(creation) && mine.every(neverSent)) {
                        for (const row of mine) {
                            await request(STORE_OPERATIONS, s => s.delete(row.op_id));
                        }
                        setResult(null);
                        return;
                    }
                    const waitFor = [];
                    for (const row of mine) {
                        if (neverSent(row) && row.operation !== 'CREATE_PERSON_GROUP') {
                            await request(STORE_OPERATIONS, s => s.delete(row.op_id));
                        } else {
                            waitFor.push(row.op_id);
                        }
                    }
                    setResult(await insertEnvelopeIn(request, {
                        operation: 'DELETE_PERSON_GROUP', entity_type: 'person-group',
                        entity_id: groupId, payload: {},
                        base_revision: null, depends_on: waitFor,
                    }, null));
                });
        }

        /* One effective never-sent open event per Work.
         *
         * Opening the same Work three times offline is one fact -- "last opened
         * at the latest of those" -- so keeping three envelopes stores nothing
         * the newest does not already say. A row that has been SENT is left
         * alone: it may already be ledgered, and it does not need cancelling
         * anyway, because the server takes the maximum event time and applying
         * two open events in either order gives the same canonical result.
         *
         * Envelopes stay immutable: coalescing deletes the superseded row and
         * inserts a new one under a new id, never rewrites history in place.
         */
        function recordWorkOpened(workId, occurredAt, localContext) {
            if (!isNonBlankString(workId)) {
                return Promise.reject(localStoreError('invalid_envelope', 'work id is required.'));
            }
            if (!isParsableTimestamp(occurredAt)) {
                return Promise.reject(localStoreError('invalid_envelope', 'occurred_at must be an ISO timestamp.'));
            }
            const at = Date.parse(occurredAt);
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite', async (request, setResult) => {
                const rows = await request(STORE_OPERATIONS, s => s.getAll());
                const existing = rows.find(r => r.operation === 'MARK_WORK_OPENED' &&
                    r.entity_type === 'work' && r.entity_id === workId &&
                    r.status === STATUS_PENDING && r.attempt_count === 0);
                if (existing) {
                    // A clock that went backwards must not lose the later event.
                    if (Date.parse(existing.occurred_at) >= at) { setResult(existing); return; }
                    await request(STORE_OPERATIONS, s => s.delete(existing.op_id));
                }
                setResult(await insertEnvelopeIn(request, {
                    operation: 'MARK_WORK_OPENED', entity_type: 'work', entity_id: workId,
                    payload: {}, base_revision: null, occurred_at: occurredAt,
                }, localContext));
            });
        }

        /* One Save, one transaction, however many fields it touched.
         *
         * The user pressed a single button. Durably storing three of their
         * four edits and then reporting "Saved locally" would be a lie that
         * only shows up later, so every field in one save commits together or
         * none of them does -- IndexedDB gives us that for free, as long as
         * the whole batch is one transaction.
         *
         * `changes` is field -> desired value; `base` is the observed server
         * state (field -> {value, revision}). Fields whose canonical value
         * already matches the base produce nothing. Returns the list of
         * operations that exist for these fields afterwards.
         */
        function saveWorkMetadataFields(workId, changes, base) {
            if (!isNonBlankString(workId) || !isPlainObject(changes) || !isPlainObject(base)) {
                return Promise.reject(localStoreError('invalid_envelope', 'Invalid metadata save.'));
            }
            for (const field of Object.keys(changes)) {
                const observed = base[field];
                if (typeof changes[field] !== 'string' || !isPlainObject(observed) ||
                    typeof observed.value !== 'string' ||
                    !Number.isSafeInteger(observed.revision) || observed.revision < 0) {
                    return Promise.reject(localStoreError('invalid_base', 'Invalid observed field state.'));
                }
            }
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite', async (request, setResult) => {
                const rows = await request(STORE_OPERATIONS, s => s.getAll());
                const written = [];
                for (const field of Object.keys(changes)) {
                    const desired = changes[field];
                    const observed = base[field];
                    const existing = rows.find(r => r.operation === 'SET_WORK_METADATA_FIELD' &&
                        r.entity_type === 'work' && r.entity_id === workId &&
                        r.payload.field === field && r.status !== STATUS_ACKNOWLEDGED);
                    if (existing) {
                        /* Only a NEVER SENT row may be rewritten. A retry after
                         * a lost response might already be ledgered, and a
                         * conflict is the user's to resolve -- but this is one
                         * field, so every other field stays editable. */
                        if (existing.status !== STATUS_PENDING || existing.attempt_count > 0) {
                            throw localStoreError('scope_busy', 'This field is syncing or needs resolution.');
                        }
                        if (existing.payload.value === desired) { written.push(existing); continue; }
                        await request(STORE_OPERATIONS, s => s.delete(existing.op_id));
                    }
                    // Editing back to the observed value leaves no intent at all.
                    if (desired === observed.value) continue;
                    written.push(await insertEnvelopeIn(request, {
                        operation: 'SET_WORK_METADATA_FIELD', entity_type: 'work', entity_id: workId,
                        payload: { field, value: desired }, base_revision: observed.revision,
                    }, null));
                }
                setResult(written);
            });
        }

        /**
         * Save the intent "this Work's video is now <source>", coalescing.
         *
         * A source is an AGGREGATE, so there is at most one unsynchronized
         * intent per Work -- and a generic `enqueueOperation` per save is not
         * that. Choosing B and then C left TWO immutable operations sharing
         * one base revision: the coordinator sends B, the revision advances,
         * and the user's own C then arrives stale and conflicts with an edit
         * they had already replaced. The visible state was C the whole time.
         *
         * `observed` is the base this edit was measured against: its
         * `identity` (provider + provider_id, never the URL spelling) and its
         * `revision`. Editing back to that identity leaves NO intent at all --
         * A -> B -> A is not two changes, it is none.
         *
         * Both identities arrive from the caller and are compared as opaque
         * strings. This module does not parse video URLs and must not start:
         * the canonical parser lives in one place per side, and the SERVER
         * derives identity itself and never trusts a client's. So a caller
         * that computed the identity wrongly can only coalesce redundantly or
         * fail to coalesce -- it cannot cause a wrong canonical write. The
         * identity is a coalescing hint and never reaches the envelope, whose
         * payload stays exactly `{source: {kind, url}}`.
         *
         * Only a NEVER SENT row may be rewritten. A row that has been
         * attempted might already be ledgered on the server, and a conflicted
         * one is the user's to resolve; either way it stays immutable and this
         * refuses with `scope_busy` rather than guessing.
         */
        function saveWorkSource(workId, source, observed) {
            if (!isNonBlankString(workId) || !isPlainObject(source) ||
                !isNonBlankString(source.url) || source.kind !== 'video' ||
                !isNonBlankString(source.identity)) {
                return Promise.reject(localStoreError('invalid_envelope', 'Invalid source save.'));
            }
            if (!isPlainObject(observed) || typeof observed.identity !== 'string' ||
                !Number.isSafeInteger(observed.revision) || observed.revision < 0) {
                return Promise.reject(localStoreError('invalid_base', 'Invalid observed source state.'));
            }
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite', async (request, setResult) => {
                const rows = await request(STORE_OPERATIONS, s => s.getAll());
                const active = rows.filter(r => r.operation === 'SET_WORK_SOURCE' &&
                    r.entity_type === 'work' && r.entity_id === workId &&
                    r.status !== STATUS_ACKNOWLEDGED)
                    .sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
                /* One active intent per Work is the invariant, but a store
                 * written before coalescing existed can already hold several.
                 * `getAll()` order is not a decision, so acting on whichever
                 * row came back first would resolve that history differently
                 * on different devices. Refuse instead, deterministically and
                 * with the count, rather than silently picking one: the rows
                 * are immutable user intent and repairing them by guesswork
                 * here would destroy a choice nobody reviewed.
                 *
                 * The last never-sent row is the only one that could be
                 * rewritten anyway, so refusing costs nothing a user can do
                 * from the editor -- the conflicts and sends ahead of it have
                 * to settle first, and each of them reduces this to one. */
                if (active.length > 1) {
                    throw localStoreError('scope_busy',
                        'This source has ' + active.length + ' unsynchronized changes; ' +
                        'let them finish or resolve them before editing it again.');
                }
                const existing = active[0];
                if (existing) {
                    if (existing.status !== STATUS_PENDING || existing.attempt_count > 0) {
                        throw localStoreError('scope_busy', 'This source is syncing or needs resolution.');
                    }
                    /* The same video in the same spelling is the same intent:
                     * keep the row rather than minting a new op_id, so a
                     * retry cannot become a second ledger entry. */
                    if (existing.payload.source.url === source.url) { setResult(existing); return; }
                    await request(STORE_OPERATIONS, s => s.delete(existing.op_id));
                }
                if (source.identity === observed.identity) { setResult(null); return; }
                setResult(await insertEnvelopeIn(request, {
                    operation: 'SET_WORK_SOURCE', entity_type: 'work', entity_id: workId,
                    payload: { source: { kind: 'video', url: source.url } },
                    base_revision: observed.revision,
                }, null));
            });
        }

        /* A prerequisite that actually SUCCEEDED on the server.
         *
         * `acknowledged` alone does not mean that: a terminally refused
         * operation is also marked acknowledged, because nothing further is
         * owed on it. Treating the two alike here would drop the dependency
         * from a link whose Person the server refused to create -- and the
         * link would then be sent on its own and refused in turn. The
         * coordinator judges readiness by the same rule. */
        function dependencySucceeded(row) {
            return !!row && row.status === STATUS_ACKNOWLEDGED && !row.server_result;
        }

        /**
         * A prerequisite has failed: every unresolved descendant is doomed.
         *
         * The store owns this because the store owns the dependency graph --
         * and because it has to happen atomically. A partial walk is the same
         * defect as no walk: whatever it missed is left waiting on a chain that
         * can never complete.
         *
         * Marked as a conflict rather than deleted. The user's intent was real,
         * and the only honest thing to do with an intent that can no longer be
         * carried out is show it to them; Diagnostics is where they discard it.
         *
         * Returns the op ids actually marked -- each at most once.
         */
        function markDependentsFailed(opId) {
            return runTransaction(STORE_OPERATIONS, 'readwrite', function (request, setResult) {
                return request(STORE_OPERATIONS, function (store) {
                    return store.getAll();
                }).then(function (rows) {
                    const doomed = unresolvedDependentClosure(rows, String(opId));
                    const marked = [];
                    return doomed.reduce(function (chain, row) {
                        return chain.then(function () {
                            // Its own conflict already says something more
                            // specific than "something upstream failed".
                            if (row.status === STATUS_CONFLICT) return null;
                            const next = Object.assign({}, row, {
                                status: STATUS_CONFLICT,
                                last_error: null,
                                server_result: { code: DEPENDENCY_FAILED },
                            });
                            marked.push(row.op_id);
                            return request(STORE_OPERATIONS, function (store) {
                                return store.put(next);
                            });
                        });
                    }, Promise.resolve()).then(function () {
                        setResult(marked);
                    });
                });
            });
        }

        /* The opposite terminal outcome, and the ONE definition of it.
         *
         * Expressed as the negation of `dependencySucceeded` rather than as a
         * second rule about `server_result`, so the two can never drift: every
         * `acknowledged` row is exactly one of "applied" or "refused", and a
         * row that is neither is still in flight. Callers that pick a
         * prerequisite and the boundary that validates one both ask this,
         * rather than each deciding for itself what a healthy dependency is.
         */
        function dependencyTerminallyFailed(row) {
            return !!row && row.status === STATUS_ACKNOWLEDGED && !dependencySucceeded(row);
        }

        /**
         * The `CREATE_PERSON` a Person-scoped operation must wait for, if any.
         *
         * Three states, three different answers -- decided with the store's own
         * definitions of them, so no family invents its own idea of a healthy
         * dependency:
         *
         *   SUCCEEDED  the Person is canonical. Nothing to wait for, and naming
         *              it would only keep a retirable row alive.
         *   IN FLIGHT  this device created them and the server has not heard;
         *              the dependent must be ordered behind it.
         *   REFUSED    the Person will never exist there. Selecting that row
         *              merely because it is still stored -- and it IS still
         *              stored, retained for whatever already depends on it --
         *              would hand the enqueue boundary a dependency it has to
         *              reject anyway, with a message about an op_id. Omitting
         *              it instead would send the operation alone, for the
         *              server to refuse in turn. Refuse here, in the terms the
         *              user was working in.
         */
        function creationDependency(rows, operation, entityType, entityId, refusal) {
            const creates = (Array.isArray(rows) ? rows : []).filter(
                r => r && r.operation === operation &&
                    r.entity_type === entityType && r.entity_id === entityId);
            if (creates.some(dependencySucceeded)) return null;
            const live = creates.find(r => !dependencyTerminallyFailed(r));
            if (live) return live;
            if (creates.length) throw localStoreError('dependency_failed', refusal);
            return null;
        }

        function personCreationDependency(rows, personId, consequence) {
            return creationDependency(rows, 'CREATE_PERSON', 'person', personId,
                'This person could not be created on the server, so ' + consequence + '.');
        }

        /** The same three states, for a Group this device created. */
        function personGroupCreationDependency(rows, groupId, consequence) {
            return creationDependency(rows, 'CREATE_PERSON_GROUP', 'person-group', groupId,
                'This group could not be created on the server, so ' + consequence + '.');
        }

        function reappliable(row) {
            const codes = REAPPLIABLE_RESULTS[row.operation];
            return !!codes && !!row.server_result &&
                codes.indexOf(row.server_result.code) !== -1 &&
                Number.isSafeInteger(row.server_result.current_revision);
        }

        /* Explicit user resolution, atomically retires the conflict and, when
         * requested, creates a NEW envelope against the observed server base.
         *
         * Resolving is the one place an operation LEAVES the graph while other
         * operations may still be waiting behind it, so both answers have to
         * account for them or the resolution strands somebody:
         *
         *   discard  -- the intent is gone for good, so everything downstream
         *               of it is unreachable and becomes a decision of its own.
         *   reapply  -- the intent survives under a NEW op_id, so the waiters
         *               are repointed at that envelope. Dropping the old id
         *               would leave them naming an operation the store no
         *               longer has: never eligible, never surfaced, never
         *               retired.
         */
        function resolveConflict(opId, apply) {
            return runTransaction([STORE_OPERATIONS, STORE_METADATA], 'readwrite', async (request, setResult) => {
                const row = await request(STORE_OPERATIONS, s => s.get(opId));
                if (!row || row.status !== STATUS_CONFLICT) throw localStoreError('not_conflict', 'Conflict no longer available.');
                const all = await request(STORE_OPERATIONS, s => s.getAll());
                const waiting = (Array.isArray(all) ? all : []).filter(
                    r => r && Array.isArray(r.depends_on) && r.depends_on.indexOf(opId) !== -1);
                let replacement = null;
                if (apply) {
                    if (!reappliable(row)) {
                        throw localStoreError('invalid_resolution', 'This conflict cannot be reapplied.');
                    }
                    /* Rewriting a dependent's envelope is only sound because it
                     * cannot have been sent: an operation is eligible only once
                     * every prerequisite has SUCCEEDED, and this one is sitting
                     * in conflict. Asserted rather than assumed -- if it were
                     * ever false, silently repointing an envelope the server
                     * has already seen is the worse of the two failures. */
                    if (waiting.some(r => r.status === STATUS_SYNCING ||
                            (Number.isInteger(r.attempt_count) && r.attempt_count > 0))) {
                        throw localStoreError('invalid_resolution',
                            'An operation waiting on this one has already been sent.');
                    }
                    /* The replacement carries no `depends_on` of its own, and
                     * does not need to: this row reached a conflict by being
                     * SENT, which means every prerequisite it had already
                     * succeeded. Copying them forward would instead keep
                     * retired-eligible rows alive for a condition that is
                     * already met. */
                    replacement = await insertEnvelopeIn(request, {
                        operation: row.operation, entity_type: row.entity_type, entity_id: row.entity_id,
                        payload: row.payload, base_revision: row.server_result.current_revision,
                    }, row.local_context);
                    for (const dependent of waiting) {
                        const next = Object.assign({}, dependent, {
                            depends_on: dependent.depends_on.map(
                                dep => (dep === opId ? replacement.op_id : dep)),
                        });
                        await request(STORE_OPERATIONS, s => s.put(next));
                    }
                } else {
                    /* Read AFTER the delete would be too late and before it is
                     * too early: the walk must not stop at this row merely
                     * because it is itself a conflict. */
                    const doomed = unresolvedDependentClosure(all, opId);
                    for (const dependent of doomed) {
                        if (dependent.status === STATUS_CONFLICT) continue;
                        await request(STORE_OPERATIONS, s => s.put(Object.assign({}, dependent, {
                            status: STATUS_CONFLICT, last_error: null,
                            server_result: { code: DEPENDENCY_FAILED },
                        })));
                    }
                }
                await request(STORE_OPERATIONS, s => s.delete(opId));
                setResult(replacement);
            });
        }

        function claimOperation(opId) {
            return runTransaction(STORE_OPERATIONS, 'readwrite', async (request, setResult) => {
                const row = await request(STORE_OPERATIONS, s => s.get(opId));
                if (!row || row.status !== STATUS_PENDING) { setResult(null); return; }
                row.status = STATUS_SYNCING;
                row.attempt_count += 1;
                row.last_attempt_at = nowIso();
                await request(STORE_OPERATIONS, s => s.put(row));
                setResult(row);
            });
        }

        function getOperation(opId) {
            return runTransaction(STORE_OPERATIONS, 'readonly', function (request, setResult) {
                return request(STORE_OPERATIONS, function (store) {
                    return store.get(String(opId));
                }).then(function (row) {
                    setResult(row || null);
                });
            });
        }

        /**
         * All operations in durable `sequence` order -- the order the user
         * performed them, which dependency resolution and coalescing both
         * need. Optionally filtered by status.
         */
        function listOperations(filter) {
            const opts = isPlainObject(filter) ? filter : {};
            return runTransaction(STORE_OPERATIONS, 'readonly', function (request, setResult) {
                return request(STORE_OPERATIONS, function (store) {
                    return store.getAll();
                }).then(function (rows) {
                    let list = Array.isArray(rows) ? rows.slice() : [];
                    if (isNonBlankString(opts.status)) {
                        list = list.filter(function (r) {
                            return r && r.status === opts.status;
                        });
                    }
                    list.sort(function (a, b) {
                        const sa = Number.isInteger(a && a.sequence) ? a.sequence : 0;
                        const sb = Number.isInteger(b && b.sequence) ? b.sequence : 0;
                        if (sa !== sb) return sa - sb;
                        return String(a && a.op_id).localeCompare(String(b && b.op_id));
                    });
                    setResult(list);
                });
            });
        }

        /**
         * Updates ONLY the mutable synchronization fields. The semantic
         * envelope (operation, entity, payload, created_at, base_revision,
         * dependencies) is immutable once persisted: rewriting history in place
         * would make a partially-synced queue unreconstructable. Coalescing, if
         * introduced, must be an explicit transaction that writes new rows.
         */
        function updateOperationSyncState(opId, patch) {
            const changes = isPlainObject(patch) ? patch : {};
            if (Object.prototype.hasOwnProperty.call(changes, 'status') &&
                STATUSES.indexOf(changes.status) === -1) {
                return Promise.reject(localStoreError('invalid_status', 'Unknown operation status.'));
            }
            return runTransaction(STORE_OPERATIONS, 'readwrite', function (request, setResult) {
                return request(STORE_OPERATIONS, function (store) {
                    return store.get(String(opId));
                }).then(function (row) {
                    if (!row) {
                        throw localStoreError('not_found', 'No such operation.');
                    }
                    const next = Object.assign({}, row);
                    if (Object.prototype.hasOwnProperty.call(changes, 'status')) {
                        next.status = changes.status;
                    }
                    if (Object.prototype.hasOwnProperty.call(changes, 'attempt_count')) {
                        next.attempt_count = Number.isInteger(changes.attempt_count)
                            ? changes.attempt_count
                            : row.attempt_count;
                    }
                    if (changes.bump_attempt === true) {
                        next.attempt_count = (Number.isInteger(row.attempt_count) ? row.attempt_count : 0) + 1;
                        next.last_attempt_at = nowIso();
                    }
                    if (Object.prototype.hasOwnProperty.call(changes, 'last_error')) {
                        // Diagnostics only: truncated, and never a place to put
                        // credentials or raw response bodies.
                        next.last_error =
                            changes.last_error == null
                                ? null
                                : String(changes.last_error).slice(0, MAX_ERROR_CHARS);
                    }
                    if (Object.prototype.hasOwnProperty.call(changes, 'server_revision')) {
                        next.server_revision = Number.isInteger(changes.server_revision)
                            ? changes.server_revision
                            : null;
                    }
                    if (Object.prototype.hasOwnProperty.call(changes, 'server_result')) {
                        const value = changes.server_result;
                        if (value !== null && !isValidStructuredResult(value)) {
                            throw localStoreError('invalid_result', 'Invalid structured server result.');
                        }
                        next.server_result = value == null ? null : JSON.parse(JSON.stringify(value));
                    }
                    if (changes.status === STATUS_ACKNOWLEDGED) {
                        next.acknowledged_at = nowIso();
                    }
                    return request(STORE_OPERATIONS, function (store) {
                        return store.put(next);
                    }).then(function () {
                        setResult(next);
                    });
                });
            });
        }

        /**
         * Removes an operation the server has acknowledged. Refuses anything
         * else: dropping a pending or conflicted operation would silently
         * discard the user's change.
         */
        function deleteAcknowledgedOperation(opId) {
            return runTransaction(STORE_OPERATIONS, 'readwrite', function (request, setResult) {
                return request(STORE_OPERATIONS, function (store) {
                    return store.get(String(opId));
                }).then(function (row) {
                    if (!row) {
                        throw localStoreError('not_found', 'No such operation.');
                    }
                    if (row.status !== STATUS_ACKNOWLEDGED) {
                        throw localStoreError(
                            'not_acknowledged',
                            'Only an acknowledged operation may be removed.'
                        );
                    }
                    return request(STORE_OPERATIONS, function (store) {
                        return store.delete(String(opId));
                    }).then(function () {
                        setResult(true);
                    });
                });
            });
        }

        /** Counts by status, for the Settings "unsynchronized changes" surface. */
        function stats() {
            return listOperations().then(function (list) {
                const byStatus = {};
                STATUSES.forEach(function (s) {
                    byStatus[s] = 0;
                });
                let bytes = 0;
                list.forEach(function (row) {
                    if (Object.prototype.hasOwnProperty.call(byStatus, row.status)) {
                        byStatus[row.status] += 1;
                    }
                    try {
                        bytes += jsonByteLength(row);
                    } catch (_e) {
                        /* a single unmeasurable row must not break the report */
                    }
                });
                return {
                    total: list.length,
                    pendingTotal: list.filter(function (r) {
                        return r.status !== STATUS_ACKNOWLEDGED;
                    }).length,
                    byStatus: byStatus,
                    approxBytes: bytes,
                };
            });
        }

        /** True when durable local storage is usable at all. */
        function isAvailable() {
            return openDb().then(
                function () {
                    return true;
                },
                function () {
                    return false;
                }
            );
        }

        /**
         * Destroys ALL durable local state. Never called by "Clear offline
         * cache" -- that clears the disposable cache only. Reserved for an
         * explicit, separately-confirmed user action.
         */
        function resetDurableLocalState() {
            return new Promise(function (resolve, reject) {
                if (!idbFactory) {
                    reject(localStoreError('unavailable', 'IndexedDB is unavailable.'));
                    return;
                }
                // Close this store's own connection FIRST. IndexedDB blocks a
                // deleteDatabase() on every open connection, and PRKS's own is
                // the one connection we can be sure exists -- leaving it open
                // means the delete hangs or reports `blocked` against
                // ourselves. A connection in ANOTHER tab is still a legitimate
                // `blocked`, which the caller must handle.
                if (openDbHandle) {
                    try {
                        openDbHandle.close();
                    } catch (_e) {
                        /* a close failure must not stop the delete attempt */
                    }
                    openDbHandle = null;
                }
                dbPromise = null;
                let req;
                try {
                    req = idbFactory.deleteDatabase(dbName);
                } catch (e) {
                    reject(localStoreError('unavailable', 'Could not reset local state.'));
                    return;
                }
                req.onsuccess = function () {
                    resolve(true);
                };
                req.onerror = function () {
                    reject(localStoreError('write_failed', 'Could not reset local state.'));
                };
                req.onblocked = function () {
                    reject(localStoreError('blocked', 'Local state is in use by another tab.'));
                };
            });
        }

        return {
            getOrCreateDeviceId: getOrCreateDeviceId,
            enqueueOperation: enqueueOperation,
            createTag: createTag,
            deleteTag: deleteTag,
            coalesceWorkTag, recordWorkOpened, saveWorkMetadataFields, saveWorkSource,
            saveWorkPersonRole,
            createPerson,
            savePersonMetadataFields: savePersonMetadataFields,
            deletePerson: deletePerson,
            createFolder: createFolder,
            saveFolderFields: saveFolderFields,
            setWorkFolder: setWorkFolder,
            deleteFolder: deleteFolder,
            createPersonGroup: createPersonGroup,
            savePersonGroupFields: savePersonGroupFields,
            setPersonGroupMember: setPersonGroupMember,
            deletePersonGroup: deletePersonGroup,
            resolveConflict, claimOperation,
            markDependentsFailed: markDependentsFailed,
            getOperation: getOperation,
            listOperations: listOperations,
            updateOperationSyncState: updateOperationSyncState,
            deleteAcknowledgedOperation: deleteAcknowledgedOperation,
            stats: stats,
            isAvailable: isAvailable,
            resetDurableLocalState: resetDurableLocalState,
        };
    }

    const api = {
        createPrksLocalStore: createPrksLocalStore,
        prksNormalizeOperationEnvelope: normalizeOperationEnvelope,
        prksUnresolvedDependentClosure: unresolvedDependentClosure,
        PRKS_LOCAL_DB_NAME: DB_NAME,
        PRKS_LOCAL_DB_VERSION: DB_VERSION,
        PRKS_LOCAL_OPERATION_TYPES: OPERATION_TYPES,
        PRKS_LOCAL_OPERATION_STATUSES: STATUSES,
        PRKS_LOCAL_REAPPLIABLE_RESULTS: REAPPLIABLE_RESULTS,
        PRKS_LOCAL_WORK_SOURCE_URL_BYTES: WORK_SOURCE_URL_BYTES,
        PRKS_LOCAL_WORK_ROLE_OPERATIONS: WORK_ROLE_OPERATIONS,
        PRKS_LOCAL_PERSON_FIELDS: PERSON_FIELDS,
        PRKS_LOCAL_FOLDER_FIELDS: FOLDER_FIELDS,
        PRKS_LOCAL_PERSON_GROUP_FIELDS: PERSON_GROUP_FIELDS,
        PRKS_LOCAL_PERSON_GROUP_OPERATIONS: PERSON_GROUP_OPERATIONS,
        prksGenerateEntityId: generateEntityId,
        prksWorkPersonRoleState: workPersonRoleState,
        prksOperationsNamingPerson: operationsNamingPerson,
        PRKS_LOCAL_MAX_PAYLOAD_BYTES: MAX_PAYLOAD_BYTES,
        PRKS_LOCAL_MAX_ABSTRACT_VALUE_BYTES: MAX_ABSTRACT_VALUE_BYTES,
        PRKS_LOCAL_WORK_FIELD_VALUE_BYTES: WORK_FIELD_VALUE_BYTES,
    };

    Object.keys(api).forEach(function (k) {
        root[k] = api[k];
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : globalThis);
