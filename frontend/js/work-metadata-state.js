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
    const FIELDS = Object.freeze(['publisher', 'location', 'edition', 'journal',
        'volume', 'issue', 'pages', 'isbn', 'doi']);
    const FIELD_SET = new Set(FIELDS);
    const LABELS = Object.freeze({
        publisher: 'Publisher', location: 'Location', edition: 'Edition',
        journal: 'Journal', volume: 'Volume', issue: 'Issue',
        pages: 'Pages', isbn: 'ISBN', doi: 'DOI',
    });
    /* Cached projections other than the Work that carry a synchronized field.
     * Mirrors `work_metadata_sync.FIELD_PROJECTIONS`; the parity is pinned by
     * `tests/test_frontend_work_metadata_sync.py`. */
    const FIELD_PROJECTIONS = Object.freeze({ publisher: ['recently-added'] });

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
            return !!entry && typeof entry === 'object' &&
                typeof entry.value === 'string' && revision(entry.revision);
        });
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
    function dirtyFields(draft, state, operations) {
        const changes = {};
        if (!draft || !state || !state.fields) return changes;
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
                return revision(data.current_revision) && typeof data.current_value === 'string' &&
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
        for (const key of ['current_revision', 'current_value', 'requested_value']) {
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

    async function refreshPending() {
        if (!root.prksSync) return pendingGeneration;
        let rows;
        try { rows = await root.prksSync.store.listOperations(); } catch (_) { return pendingGeneration; }
        const next = new Map();
        rows.filter(op => op && op.operation === 'SET_WORK_METADATA_FIELD' &&
            op.entity_type === 'work' && op.status !== 'acknowledged' &&
            FIELD_SET.has(op.payload.field)).forEach(op => {
                if (!next.has(op.entity_id)) next.set(op.entity_id, {});
                next.get(op.entity_id)[op.payload.field] = op.payload.value;
            });
        pendingByWork = next;
        pendingGeneration += 1;
        return pendingGeneration;
    }

    /**
     * Acknowledged projection rows + pending field values = what the user
     * should see and search. The rows are NEVER mutated: a new object is
     * returned for any row an edit touches, so the caller's acknowledged
     * snapshot -- in memory or in IndexedDB -- stays exactly what the server
     * said.
     */
    function effectiveRows(rows, fields) {
        if (!Array.isArray(rows) || !pendingByWork.size) return rows;
        const wanted = (fields || FIELDS).filter(field => FIELD_SET.has(field));
        return rows.map(row => {
            const pending = row && pendingByWork.get(row.id);
            if (!pending) return row;
            let out = row;
            wanted.forEach(field => {
                if (!Object.prototype.hasOwnProperty.call(pending, field)) return;
                if (out === row) out = Object.assign({}, row);
                out[field] = pending[field];
            });
            return out;
        });
    }

    Object.assign(root, {
        PRKS_SYNCED_WORK_FIELDS: FIELDS,
        PRKS_SYNCED_WORK_FIELD_PROJECTIONS: FIELD_PROJECTIONS,
        prksRefreshPendingWorkMetadata: refreshPending,
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
