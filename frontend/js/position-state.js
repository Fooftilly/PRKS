/**
 * Positions: pending overlays, bases, and the three operation shapes.
 *
 * Deliberately the smallest local-first domain. `name` and `description` are
 * INDEPENDENT fields rather than one aggregate: `positions.name` is subject to
 * no uniqueness rule, renaming one writes nothing else, and joining them would
 * make an unrelated description edit conflict with a rename.
 *
 * A Position's name is read by more surfaces than its own pages -- an Argument
 * renders the name of every Position it targets, and so does the picker used
 * when choosing one -- so a pending rename is hydrated into a synchronous map,
 * the way Person and Concept names are.
 *
 * Everything here is a projection over the durable operation list. Nothing
 * writes a pending value into the disposable cache.
 */
(function (root) {
    'use strict';

    const FIELDS = root.PRKS_LOCAL_POSITION_FIELDS ||
        Object.freeze(['name', 'description']);

    const LABELS = Object.freeze({ name: 'Position name', description: 'Description' });

    function isSupportedField(field) {
        return typeof field === 'string' && FIELDS.indexOf(field) !== -1;
    }

    function unsettled(operations, operation, positionId) {
        return (operations || []).filter(op => op &&
            op.entity_type === 'position' && op.operation === operation &&
            (positionId == null || op.entity_id === positionId) &&
            op.status !== 'acknowledged');
    }

    /* ---- construction ---- */

    function catalogRowFromOp(op) {
        if (!op || op.entity_type !== 'position' || typeof op.entity_id !== 'string') return null;
        const payload = op.payload && typeof op.payload === 'object' ? op.payload : {};
        return {
            id: op.entity_id,
            name: String(payload.name == null ? '' : payload.name),
            description: String(payload.description == null ? '' : payload.description),
        };
    }

    function pendingCreates(operations) {
        return unsettled(operations, 'CREATE_POSITION', null);
    }

    /* A refused deletion stops hiding its entity: see
     * `prksDurableDeletionAwaitsServer` in local-store.js for why. */
    const deletionAwaitsServer = root.prksDurableDeletionAwaitsServer ||
        function (op) {
            return !!op && op.status !== 'acknowledged' && op.status !== 'conflict';
        };

    function pendingDeletions(operations) {
        return new Set(unsettled(operations, 'DELETE_POSITION', null)
            .filter(deletionAwaitsServer).map(op => op.entity_id));
    }

    /* ---- fields ---- */

    function pendingFieldValues(operations, positionId) {
        const values = new Map();
        unsettled(operations, 'SET_POSITION_FIELD', positionId)
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                const field = op.payload && op.payload.field;
                if (!isSupportedField(field)) return;
                values.set(field, String((op.payload && op.payload.value) || ''));
            });
        return values;
    }

    function applyFields(row, values) {
        if (!row || !values.size) return row;
        const out = Object.assign({}, row);
        values.forEach(function (value, field) { out[field] = value; });
        return out;
    }

    /* ---- effective projections ---- */

    /**
     * The Position list a user should see.
     *
     * A deletion is a TOMBSTONE: the Position is hidden and nothing
     * acknowledged is destroyed, so a server that refuses -- one an Argument
     * still targets -- restores it by doing nothing.
     */
    function effectivePositions(rows, operations) {
        if (!Array.isArray(rows)) return rows;
        const byId = new Map(rows.map(row => [row && row.id, row]));
        pendingCreates(operations).forEach(function (op) {
            const row = catalogRowFromOp(op);
            if (row) byId.set(row.id, row);
        });
        const out = [];
        byId.forEach(function (row) {
            if (row) out.push(applyFields(row, pendingFieldValues(operations, row.id)));
        });
        pendingDeletions(operations).forEach(function (positionId) {
            for (let i = out.length - 1; i >= 0; i -= 1) {
                if (out[i] && out[i].id === positionId) out.splice(i, 1);
            }
        });
        /* `ORDER BY LOWER(name), id` is what the endpoint does, and a pending
         * rename moves a row within it. */
        return out.sort(function (a, b) {
            const name = String(a.name || '').localeCompare(String(b.name || ''),
                undefined, { sensitivity: 'base' });
            return name || String(a.id).localeCompare(String(b.id));
        });
    }

    /** One Position's detail, with its pending fields applied. */
    function effectivePositionDetail(position, operations) {
        if (!position || typeof position !== 'object') return position;
        return applyFields(position, pendingFieldValues(operations, position.id));
    }

    /* ---- the pending-name map ---- */

    /* Hydrated from the durable queue and then read SYNCHRONOUSLY. An Argument
     * renders the name of every Position it targets, and a target picker
     * renders them all -- both from synchronous code, so a pending rename has
     * to be known before the paint. */
    let pendingNames = new Map();

    function setPendingPositionNames(operations) {
        const next = new Map();
        pendingCreates(operations).forEach(function (op) {
            const row = catalogRowFromOp(op);
            if (row) next.set(row.id, row.name);
        });
        unsettled(operations, 'SET_POSITION_FIELD', null)
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                if (!op.payload || op.payload.field !== 'name') return;
                next.set(op.entity_id, String(op.payload.value || ''));
            });
        pendingNames = next;
        return pendingNames.size;
    }

    async function refreshPendingPositionNames() {
        if (!root.prksSync || !root.prksSync.store) return [];
        let operations;
        try {
            operations = await root.prksSync.store.listOperations();
        } catch (_e) {
            return [];
        }
        setPendingPositionNames(operations);
        return operations;
    }

    /** A pending rename applied to any `{id, name}`-shaped rows. */
    function applyPendingPositionNames(rows) {
        if (!Array.isArray(rows) || !rows.length || !pendingNames.size) return rows;
        let changed = false;
        const out = rows.map(function (row) {
            const name = pendingNames.get(row && row.id);
            if (name == null || row.name === name) return row;
            changed = true;
            return Object.assign({}, row, { name: name });
        });
        return changed ? out : rows;
    }

    /**
     * The same overlay over an Argument's `targets[]`.
     *
     * A target row names the Position it points at, under `id`/`name` beside
     * its own `type` and `verdict_id`, so the generic row helper would rename
     * an Argument target too. This one is narrowed to `type === 'position'`,
     * because an Argument may also target another ARGUMENT and those ids live
     * in a different space.
     */
    function applyPendingPositionNamesToTargets(targets) {
        if (!Array.isArray(targets) || !targets.length || !pendingNames.size) return targets;
        let changed = false;
        const out = targets.map(function (target) {
            if (!target || target.type !== 'position') return target;
            const name = pendingNames.get(target.id);
            if (name == null || target.name === name) return target;
            changed = true;
            return Object.assign({}, target, { name: name });
        });
        return changed ? out : targets;
    }

    /* ---- bases ---- */

    function isPositionStateShape(value, positionId) {
        if (!value || typeof value !== 'object') return false;
        if (positionId != null && value.position_id !== positionId) return false;
        const fields = value.fields;
        if (!fields || typeof fields !== 'object') return false;
        for (let i = 0; i < FIELDS.length; i += 1) {
            const entry = fields[FIELDS[i]];
            if (!entry || typeof entry !== 'object') return false;
            if (!Number.isSafeInteger(entry.revision) || entry.revision < 0) return false;
        }
        return true;
    }

    async function readPositionState(positionId, options) {
        const result = await root.prksOfflineReadEntity('position-state', positionId,
            '/api/positions/' + encodeURIComponent(positionId) + '/sync-state',
            Object.assign({}, options || {},
                { validate: v => isPositionStateShape(v, positionId) }));
        if (result.value !== null && !isPositionStateShape(result.value, positionId)) {
            await root.prksOfflineInvalidateEntity('position-state', positionId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    function newPositionState(positionId) {
        const fields = {};
        FIELDS.forEach(function (name) { fields[name] = { revision: 0 }; });
        return { position_id: positionId, fields: fields };
    }

    function observedPositionFields(position, state) {
        const fields = state && state.fields && typeof state.fields === 'object'
            ? state.fields : {};
        const base = {};
        FIELDS.forEach(function (name) {
            const entry = fields[name];
            const revision = entry && Number.isSafeInteger(entry.revision) && entry.revision >= 0
                ? entry.revision : 0;
            const raw = position ? position[name] : null;
            base[name] = { value: raw == null ? '' : String(raw), revision: revision };
        });
        return base;
    }

    /**
     * The ACKNOWLEDGED base a Position edit is measured against.
     *
     * Returns null when the base is not knowable: guessing revision 0 for a
     * Position whose revisions this device has never read would silently
     * overwrite whatever another device wrote. A Position created here and
     * never sent is the exception -- its construction payload IS the base, at
     * revision 0, which is known rather than assumed.
     */
    async function acknowledgedPositionBase(positionId, operations) {
        if (typeof positionId !== 'string' || !positionId) return null;
        const creating = pendingCreates(operations).find(op => op.entity_id === positionId);
        if (creating) {
            return observedPositionFields(catalogRowFromOp(creating),
                newPositionState(positionId));
        }
        let state = null;
        try {
            const result = await readPositionState(positionId);
            state = result && result.value;
        } catch (_e) { state = null; }
        if (!state) return null;
        let position = null;
        try {
            const cached = await root.prksOfflineReadEntity('position', positionId,
                '/api/positions/' + encodeURIComponent(positionId), {});
            position = cached && cached.value;
        } catch (_e) { position = null; }
        return position ? observedPositionFields(position, state) : null;
    }

    /** What this editing session changed, measured against what it was showing. */
    function dirtyPositionFields(positionId, draft, base, operations) {
        const changes = {};
        if (!draft || !base) return changes;
        const pending = pendingFieldValues(operations, positionId);
        FIELDS.forEach(function (field) {
            if (!Object.prototype.hasOwnProperty.call(draft, field)) return;
            const observed = base[field];
            if (!observed || typeof observed.value !== 'string') return;
            const shown = pending.has(field) ? pending.get(field) : observed.value;
            const desired = String(draft[field] == null ? '' : draft[field]);
            if (desired !== shown) changes[field] = desired;
        });
        return changes;
    }

    /* ---- durable writers ---- */

    function sync() {
        const runtime = root.prksSync;
        if (!runtime || !runtime.store) throw new Error('Position editing is not available.');
        return runtime;
    }

    async function createPositionDurably(fields) {
        const runtime = sync();
        const op = await runtime.store.createPosition(fields);
        await refreshPendingPositionNames();
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function savePositionFieldsDurably(positionId, changes, base) {
        const runtime = sync();
        const written = await runtime.store.savePositionFields(positionId, changes, base);
        await refreshPendingPositionNames();
        if (typeof runtime.changed === 'function') runtime.changed();
        return written;
    }

    async function deletePositionDurably(positionId) {
        const runtime = sync();
        const op = await runtime.store.deletePosition(positionId);
        await refreshPendingPositionNames();
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    /* ---- sync handlers ---- */

    const createHandler = {
        isResult: function (data, op) {
            if (!data || data.position_id !== op.entity_id) return false;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean' &&
                !!data.position && data.position.id === data.position_id;
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileCreatedPosition(data),
    };

    const fieldHandler = {
        isResult: function (data, op) {
            if (!data || data.position_id !== op.entity_id) return false;
            if (data.field !== (op.payload && op.payload.field)) return false;
            switch (data.code) {
                case 'ACKNOWLEDGED':
                    return typeof data.changed === 'boolean' &&
                        Number.isSafeInteger(data.server_revision) &&
                        data.server_revision >= 0 && data.value_omitted === true;
                case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                    return Number.isSafeInteger(data.current_revision) &&
                        (typeof data.current_value === 'string' ||
                            typeof data.current_preview === 'string');
                case 'ENTITY_NOT_FOUND':
                    return true;
                default: return false;
            }
        },
        terminal: function (data) {
            const out = { code: data.code };
            if (Number.isSafeInteger(data.current_revision)) {
                out.current_revision = data.current_revision;
            }
            if (typeof data.current_value === 'string') out.current_value = data.current_value;
            if (typeof data.current_preview === 'string') {
                out.current_preview = data.current_preview;
                if (Number.isSafeInteger(data.current_bytes)) out.current_bytes = data.current_bytes;
            }
            return { conflict: out };
        },
        reconcile: (data, op) => root.prksOfflineReconcilePositionField(data, op),
    };

    const deleteHandler = {
        isResult: function (data, op) {
            if (!data || data.position_id !== op.entity_id) return false;
            if (data.code === 'POSITION_IN_USE') return true;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean';
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileDeletedPosition(data),
    };

    Object.assign(root, {
        PRKS_POSITION_FIELDS: FIELDS,
        PRKS_POSITION_FIELD_LABELS: LABELS,
        prksIsSupportedPositionField: isSupportedField,
        prksPositionRowFromOp: catalogRowFromOp,
        prksPendingPositionCreates: pendingCreates,
        prksPendingPositionDeletions: pendingDeletions,
        prksEffectivePositions: effectivePositions,
        prksEffectivePositionDetail: effectivePositionDetail,
        prksSetPendingPositionNames: setPendingPositionNames,
        prksRefreshPendingPositionNames: refreshPendingPositionNames,
        prksApplyPendingPositionNames: applyPendingPositionNames,
        prksApplyPendingPositionNamesToTargets: applyPendingPositionNamesToTargets,
        prksIsPositionStateShape: isPositionStateShape,
        prksReadPositionState: readPositionState,
        prksNewPositionState: newPositionState,
        prksObservedPositionFields: observedPositionFields,
        prksAcknowledgedPositionBase: acknowledgedPositionBase,
        prksDirtyPositionFields: dirtyPositionFields,
        prksCreatePositionDurably: createPositionDurably,
        prksSavePositionFieldsDurably: savePositionFieldsDurably,
        prksDeletePositionDurably: deletePositionDurably,
        prksPositionCreateSyncHandler: createHandler,
        prksPositionFieldSyncHandler: fieldHandler,
        prksPositionDeleteSyncHandler: deleteHandler,
    });
})(typeof window === 'undefined' ? globalThis : window);
