/* Work metadata fields: the synchronization projection, the optimistic
 * overlay, and what a server answer means for this family.
 *
 * The conflict unit is a FIELD. Two devices editing `doi` and `isbn` on the
 * same Work have not disagreed about anything, so they must not be told they
 * have -- which is why every field carries its own revision and its own
 * resolution, and why one conflicting field leaves the rest editable.
 *
 * Eight of the nine synchronized scalars reach no cached read model but the
 * Work detail. `publisher` is the exception and the reason this module now
 * exports a cross-projection overlay: `recently-added:index` carries it
 * because Home -> Recently Added filters LOCALLY over it. A field being
 * invisible on a card is not the same as it being unused, so a pending
 * publisher has to reach that projection's filtering too -- without ever being
 * written into the acknowledged snapshot.
 */
(function (root) {
    'use strict';
    const FIELDS = Object.freeze(['abstract', 'publisher', 'location', 'edition', 'journal',
        'volume', 'issue', 'pages', 'isbn', 'doi']);
    const FIELD_SET = new Set(FIELDS);
    const LABELS = Object.freeze({
        abstract: 'Abstract',
        publisher: 'Publisher', location: 'Location', edition: 'Edition',
        journal: 'Journal', volume: 'Volume', issue: 'Issue',
        pages: 'Pages', isbn: 'ISBN', doi: 'DOI',
    });
    /* Cached projections other than the Work that carry a synchronized field.
     * Mirrors `work_metadata_sync.FIELD_PROJECTIONS`; the parity is pinned by
     * `tests/test_frontend_work_metadata_sync.py`. */
    const FIELD_PROJECTIONS = Object.freeze({
        publisher: ['recently-added'],
        abstract: ['works-browse'],
    });

    /* Mirrors `backend/work_metadata_sync.BYTE_LIMITED_FIELDS`: a field whose
     * bound is a storage limit rather than a display one, and whose value the
     * metadata-state projection therefore omits -- the Work record already has
     * it, and echoing a megabyte into a second cache would double what every
     * read costs for a value the client already holds. */
    const BYTE_LIMITED_FIELDS = new Set(['abstract']);
    const MAX_ABSTRACT_UTF8_BYTES = 1024 * 1024;

    /* How a pending field value reaches a cached projection.
     *
     * `publisher` COPIES a scalar into a column of the same name.
     * `abstract` DERIVES a different column from it. Consumers ask for
     * effective rows and never interpret durable operations themselves, so
     * adding a third field here is a table entry rather than an `if` inside
     * whichever component happens to render it.
     */
    const PROJECTION_COLUMNS = Object.freeze({
        'recently-added': [{ field: 'publisher', column: 'publisher', derive: value => value }],
        'works-browse': [{ field: 'abstract', column: 'abstract_excerpt', derive: text => abstractExcerpt(text) }],
    });

    /* The excerpt Progress shows under each Work card.
     *
     * PRKS's rule is the FIRST 100 UNICODE CODE POINTS, because that is what
     * the server's `SUBSTR(COALESCE(abstract, ''), 1, 100)` produces -- SQLite
     * counts characters, not UTF-16 code units and not grapheme clusters. The
     * client must reproduce the server exactly, not improve on it: a pending
     * excerpt that disagreed with the one the server will send back would
     * flicker at acknowledgement. `Array.from()` iterates code points, so a
     * surrogate pair is never split and a 4-byte character counts once. */
    const EXCERPT_CODE_POINTS = 100;

    function abstractExcerpt(text) {
        if (text == null) return '';
        const value = String(text);
        if (value.length <= EXCERPT_CODE_POINTS) return value;
        /* A code point is at most two UTF-16 units, so the first 100 code
         * points always lie within the first 200 units. Bounding the slice
         * BEFORE expanding keeps a megabyte-scale Abstract from being turned
         * into a million-entry array on every Progress render. The prefix
         * yields between 100 and 200 code points, so taking 100 can never
         * reach a surrogate the slice happened to split. */
        const points = Array.from(value.slice(0, EXCERPT_CODE_POINTS * 2));
        return points.length <= EXCERPT_CODE_POINTS
            ? points.join('')
            : points.slice(0, EXCERPT_CODE_POINTS).join('');
    }

    /** SQLite NULL and "" are one logical value; whitespace is never stripped. */
    function canonical(value) {
        return value == null ? '' : String(value);
    }

    const revision = v => Number.isSafeInteger(v) && v >= 0;

    function stateShape(value, workId) {
        if (!value || typeof value !== 'object' || typeof value.work_id !== 'string' ||
            (workId && value.work_id !== workId)) return false;
        const fields = value.fields;
        if (!fields || typeof fields !== 'object' || Array.isArray(fields)) return false;
        const names = Object.keys(fields);
        if (names.length !== FIELDS.length || !names.every(name => FIELD_SET.has(name))) return false;
        return names.every(name => {
            const entry = fields[name];
            if (!entry || typeof entry !== 'object' || !revision(entry.revision)) return false;
            // A byte-limited field carries a revision only; its value lives on
            // the Work record. Anything else must carry both.
            return BYTE_LIMITED_FIELDS.has(name)
                ? !Object.prototype.hasOwnProperty.call(entry, 'value')
                : typeof entry.value === 'string';
        });
    }

    /**
     * The acknowledged base a save is measured against. For most fields the
     * projection carries it; for a byte-limited one the Work record does.
     */
    function observedFields(state, work) {
        const out = {};
        if (!state || !state.fields) return out;
        FIELDS.forEach(field => {
            const entry = state.fields[field];
            if (!entry) return;
            out[field] = {
                revision: entry.revision,
                value: BYTE_LIMITED_FIELDS.has(field)
                    ? canonical(work && work[field])
                    : entry.value,
            };
        });
        return out;
    }

    /** Exact UTF-8 byte length, the unit every wire and storage limit uses. */
    function utf8Bytes(text) {
        const value = canonical(text);
        if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(value).length;
        return Buffer.byteLength(value, 'utf8');
    }

    /** null when the value fits, otherwise a message naming the limit. */
    function fieldLimitError(field, value) {
        if (!BYTE_LIMITED_FIELDS.has(field)) return null;
        const bytes = utf8Bytes(value);
        if (bytes <= MAX_ABSTRACT_UTF8_BYTES) return null;
        return (LABELS[field] || field) + ' is too long to save (' +
            Math.ceil(bytes / 1024) + ' KB of ' + (MAX_ABSTRACT_UTF8_BYTES / 1024) +
            ' KB allowed). Shorten it, or keep long material in Research Notes.';
    }

    async function readState(workId, options = {}) {
        const result = await root.prksOfflineReadEntity('work-metadata-state', workId,
            '/api/works/' + encodeURIComponent(workId) + '/metadata-state', {
                ...options, validate: v => stateShape(v, workId),
            });
        if (result.value !== null && !stateShape(result.value, workId)) {
            await root.prksOfflineInvalidateEntity('work-metadata-state', workId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    function fieldOperations(operations, workId) {
        return (operations || []).filter(op => op && op.operation === 'SET_WORK_METADATA_FIELD' &&
            op.entity_type === 'work' && op.entity_id === workId && op.status !== 'acknowledged');
    }

    /**
     * Acknowledged Work + durable pending/conflicted field edits. Pending
     * intent is never written into the cached Work record; the overlay is
     * recomputed from `prks-local-v1`, so it survives a reload.
     */
    function effectiveWork(work, operations) {
        if (!work || typeof work.id !== 'string') return work;
        const pending = fieldOperations(operations, work.id);
        if (!pending.length) return work;
        const out = Object.assign({}, work);
        pending.forEach(op => {
            if (FIELD_SET.has(op.payload.field)) out[op.payload.field] = op.payload.value;
        });
        return out;
    }

    /**
     * What the user actually changed, measured against what the form was
     * SHOWING -- the pending value if there is one, otherwise the server's.
     *
     * Measuring against the server base instead would be subtly wrong in both
     * directions: editing a field back to its server value would look like "no
     * change" and quietly leave the pending operation in place, and a field
     * still displaying an untouched pending value would look dirty on every
     * save.
     */
    function dirtyFields(draft, observed, operations) {
        const changes = {};
        if (!draft || !observed || !observed.fields) return changes;
        const state = observed;
        const pending = new Map();
        (operations || []).forEach(op => {
            if (op && op.operation === 'SET_WORK_METADATA_FIELD' && op.status !== 'acknowledged' &&
                FIELD_SET.has(op.payload.field)) pending.set(op.payload.field, op.payload.value);
        });
        FIELDS.forEach(field => {
            if (!Object.prototype.hasOwnProperty.call(draft, field)) return;
            const shown = pending.has(field) ? pending.get(field) : canonical(state.fields[field].value);
            const desired = canonical(draft[field]);
            if (desired !== shown) changes[field] = desired;
        });
        return changes;
    }

    /* ---- sync handler ---- */
    function isResult(data, op) {
        if (!data || data.work_id !== op.entity_id || data.field !== op.payload.field) return false;
        switch (data.code) {
            case 'ACKNOWLEDGED':
                return typeof data.value === 'string' && data.value === op.payload.value &&
                    typeof data.changed === 'boolean' && revision(data.server_revision);
            case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                if (!revision(data.current_revision)) return false;
                // A byte-limited field reports previews and sizes instead of
                // the values themselves; see the server's disagreement().
                return BYTE_LIMITED_FIELDS.has(data.field)
                    ? typeof data.current_preview === 'string' &&
                      Number.isSafeInteger(data.current_bytes) &&
                      Number.isSafeInteger(data.requested_bytes)
                    : typeof data.current_value === 'string' &&
                      data.requested_value === op.payload.value;
            case 'ENTITY_NOT_FOUND': return true;
            default: return false;
        }
    }

    /* Every terminal outcome here is the user's to resolve: they typed this
     * value deliberately, so discarding it silently would lose real work. The
     * conflict is recorded against the FIELD, so the rest stay usable. */
    function terminal(data) {
        const out = { code: data.code };
        for (const key of ['current_revision', 'current_value', 'requested_value',
            'current_preview', 'current_bytes', 'requested_bytes']) {
            if (Object.prototype.hasOwnProperty.call(data, key)) out[key] = data[key];
        }
        return { conflict: out };
    }

    const handler = {
        isResult, terminal,
        reconcile: data => root.prksOfflineReconcileWorkField(data),
    };

    /* ---- synchronous pending-value snapshot ----
     *
     * Recently Added filters and renders synchronously over up to 50 rows. One
     * IndexedDB read per row would be a query storm for a list that redraws on
     * every keystroke, so the durable queue is read ONCE into a map and
     * refreshed whenever the coordinator reports a change.
     *
     * The map is derived state, never a second interpretation of operation
     * semantics: it is built from the same `fieldOperations()` filter the Work
     * overlay uses.
     */
    let pendingByWork = new Map();
    let pendingGeneration = 0;
    /* Hydration state, four-valued on purpose.
     *
     * An empty map cannot carry this: "not read yet", "read and genuinely
     * empty" and "the read FAILED" are three different facts, and only the
     * middle one licenses a synchronous caller to say "there is no pending
     * value for this field". A failed IndexedDB read proves nothing about what
     * is stored -- operations persisted by an earlier session may still be
     * sitting there -- so it must never be collapsed into "nothing pending".
     */
    const UNREAD = 'unread';
    const LOADING = 'loading';
    const READY = 'ready';
    const UNAVAILABLE = 'unavailable';
    let hydration = UNREAD;
    let hydrationPromise = null;
    let hydrationResolve = null;

    function settle(state) {
        hydration = state;
        if (hydrationResolve) { hydrationResolve(state); hydrationResolve = null; }
        hydrationPromise = null;
    }

    /* The durable queue could not be read. Waiters are released -- nothing is
     * served by hanging the UI on a read that already failed -- but the map is
     * left exactly as it was. Whatever was last known to be pending is still
     * the best information available, and an empty map stays untrusted. */
    function failHydration() {
        settle(UNAVAILABLE);
    }

    /** Rebuild the map from rows a caller has already read. */
    function setPending(rows) {
        const next = new Map();
        (rows || []).filter(op => op && op.operation === 'SET_WORK_METADATA_FIELD' &&
            op.entity_type === 'work' && op.status !== 'acknowledged' &&
            FIELD_SET.has(op.payload.field)).forEach(op => {
                if (!next.has(op.entity_id)) next.set(op.entity_id, {});
                next.get(op.entity_id)[op.payload.field] = op.payload.value;
            });
        pendingByWork = next;
        pendingGeneration += 1;
        settle(READY);
        return pendingGeneration;
    }

    /**
     * Read the durable queue once and republish the map. Returns the rows, so
     * a caller that needs them too -- the metadata editor's paint -- shares
     * this read instead of issuing its own.
     */
    async function refreshPending() {
        if (!root.prksSync) { failHydration(); return []; }
        if (hydration === UNREAD || hydration === UNAVAILABLE) hydration = LOADING;
        let rows;
        try {
            rows = await root.prksSync.store.listOperations();
        } catch (_) {
            failHydration();
            return [];
        }
        setPending(rows);
        return rows;
    }

    /**
     * The ONE shared hydration. A caller that must not act on a
     * not-yet-read map awaits this; it never starts a second read for the
     * same event, because a read already in flight resolves this same promise
     * when it lands.
     */
    function ensurePending() {
        if (hydration === READY || hydration === UNAVAILABLE) return Promise.resolve(hydration);
        if (!hydrationPromise) {
            hydrationPromise = new Promise(resolve => { hydrationResolve = resolve; });
        }
        if (hydration === UNREAD) void refreshPending();
        return hydrationPromise;
    }

    /**
     * The effective view of ONE Work, synchronously. Callers that must decide
     * something without awaiting -- a leave guard, a form's initial values --
     * need the pending values without a round trip to IndexedDB.
     */
    function effectiveWorkSync(work) {
        if (!work || typeof work.id !== 'string') return work;
        return effectiveRows([work], FIELDS)[0];
    }

    /**
     * Acknowledged projection rows + pending field values = what the user
     * should see and search. The rows are NEVER mutated: a new object is
     * returned for any row an edit touches, so the caller's acknowledged
     * snapshot -- in memory or in IndexedDB -- stays exactly what the server
     * said.
     */
    function effectiveRows(rows, fields) {
        const wanted = (fields || FIELDS).filter(field => FIELD_SET.has(field));
        return applyPending(rows, wanted.map(
            field => ({ field, column: field, derive: value => value })));
    }

    /**
     * Acknowledged projection rows + pending values, through that projection's
     * own transforms. Rows are never mutated: only a row an edit touches is
     * copied, so the caller's acknowledged snapshot stays what the server said.
     */
    function effectiveProjectionRows(rows, projection) {
        return applyPending(rows, PROJECTION_COLUMNS[projection] || []);
    }

    function applyPending(rows, transforms) {
        if (!Array.isArray(rows) || !pendingByWork.size || !transforms.length) return rows;
        return rows.map(row => {
            const pending = row && pendingByWork.get(row.id);
            if (!pending) return row;
            let out = row;
            transforms.forEach(({ field, column, derive }) => {
                if (!Object.prototype.hasOwnProperty.call(pending, field)) return;
                if (out === row) out = Object.assign({}, row);
                out[column] = derive(pending[field]);
            });
            return out;
        });
    }

    Object.assign(root, {
        PRKS_SYNCED_WORK_FIELDS: FIELDS,
        PRKS_ABSTRACT_EXCERPT_CODE_POINTS: EXCERPT_CODE_POINTS,
        prksAbstractExcerpt: abstractExcerpt,
        PRKS_SYNCED_WORK_FIELD_PROJECTIONS: FIELD_PROJECTIONS,
        PRKS_BYTE_LIMITED_WORK_FIELDS: BYTE_LIMITED_FIELDS,
        PRKS_MAX_ABSTRACT_UTF8_BYTES: MAX_ABSTRACT_UTF8_BYTES,
        prksWorkFieldUtf8Bytes: utf8Bytes,
        prksWorkFieldLimitError: fieldLimitError,
        prksObservedWorkFields: observedFields,
        prksEffectiveProjectionRows: effectiveProjectionRows,
        /* The acknowledged counterpart of the overlay: the same transform,
         * applied once the server has spoken. One definition, so a row cannot
         * visibly change at acknowledgement. */
        prksProjectionFieldPatch: (projection, field, value) => {
            const transform = (PROJECTION_COLUMNS[projection] || []).find(t => t.field === field);
            return transform ? { [transform.column]: transform.derive(value) } : null;
        },
        prksRefreshPendingWorkMetadata: refreshPending,
        prksEnsurePendingWorkMetadata: ensurePending,
        prksPendingWorkMetadataState: () => hydration,
        /* Settled, either way. A caller that only needs to know whether to
         * WAIT asks this; a caller deciding whether to TRUST an absent value
         * must ask for the state and require `ready`. */
        prksPendingWorkMetadataSettled: () => hydration === READY || hydration === UNAVAILABLE,
        prksSetPendingWorkMetadata: setPending,
        prksEffectiveWorkSync: effectiveWorkSync,
        prksPendingWorkMetadataGeneration: () => pendingGeneration,
        prksEffectiveWorkMetadataRows: effectiveRows,
        PRKS_SYNCED_WORK_FIELD_LABELS: LABELS,
        prksCanonicalWorkField: canonical,
        prksIsWorkMetadataStateShape: stateShape,
        prksReadWorkMetadataState: readState,
        prksEffectiveWorkMetadata: effectiveWork,
        prksWorkMetadataFieldOperations: fieldOperations,
        prksDirtyWorkMetadataFields: dirtyFields,
        prksWorkMetadataSyncHandler: handler,
    });
})(typeof window === 'undefined' ? globalThis : window);
