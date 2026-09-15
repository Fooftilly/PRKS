/**
 * Concepts: pending overlays, bases, and the five operation shapes.
 *
 * `description` is the only column a Concept has that is not part of its
 * identity, so it is the only FIELD. The NAME and the ALIAS SET are one
 * aggregate, because renaming keeps the old name reachable as an alias --
 * every note that already says `[[concept:Old Name]]` must go on resolving --
 * so a rename writes into the set an alias edit changes. The parent set is the
 * other aggregate: one structural judgement, whose acyclicity only the server
 * can see.
 *
 * Everything here is a projection over the durable operation list. Nothing
 * writes a pending value into the disposable cache.
 */
(function (root) {
    'use strict';

    const FIELDS = root.PRKS_LOCAL_CONCEPT_FIELDS || Object.freeze(['description']);

    const LABELS = Object.freeze({ description: 'Definition' });

    function isSupportedField(field) {
        return typeof field === 'string' && FIELDS.indexOf(field) !== -1;
    }

    function unsettled(operations, operation, conceptId) {
        return (operations || []).filter(op => op &&
            op.entity_type === 'concept' && op.operation === operation &&
            (conceptId == null || op.entity_id === conceptId) &&
            op.status !== 'acknowledged');
    }

    /* ---- construction ---- */

    function catalogRowFromOp(op) {
        if (!op || op.entity_type !== 'concept' || typeof op.entity_id !== 'string') return null;
        const payload = op.payload && typeof op.payload === 'object' ? op.payload : {};
        return {
            id: op.entity_id,
            name: String(payload.name == null ? '' : payload.name),
            description: String(payload.description == null ? '' : payload.description),
            aliases: [],
            parents: [],
            children: [],
        };
    }

    function pendingCreates(operations) {
        return unsettled(operations, 'CREATE_CONCEPT', null);
    }

    /* A refused deletion stops hiding its entity: see
     * `prksDurableDeletionAwaitsServer` in local-store.js for why. */
    const deletionAwaitsServer = root.prksDurableDeletionAwaitsServer ||
        function (op) {
            return !!op && op.status !== 'acknowledged' && op.status !== 'conflict';
        };

    function pendingDeletions(operations) {
        return new Set(unsettled(operations, 'DELETE_CONCEPT', null)
            .filter(deletionAwaitsServer).map(op => op.entity_id));
    }

    /* ---- the definition ---- */

    function pendingFieldValues(operations, conceptId) {
        const values = new Map();
        unsettled(operations, 'SET_CONCEPT_FIELD', conceptId)
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                const field = op.payload && op.payload.field;
                if (!isSupportedField(field)) return;
                values.set(field, String((op.payload && op.payload.value) || ''));
            });
        return values;
    }

    /* ---- identity ---- */

    /** `concept id -> {name, aliases}` for every unsynchronized identity edit. */
    function pendingIdentities(operations) {
        const values = new Map();
        unsettled(operations, 'SET_CONCEPT_IDENTITY', null)
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                const payload = op.payload || {};
                values.set(op.entity_id, {
                    name: String(payload.name == null ? '' : payload.name),
                    aliases: Array.isArray(payload.aliases) ? payload.aliases.slice() : [],
                });
            });
        return values;
    }

    /* ---- hierarchy ---- */

    /** `concept id -> [parent ids]` for every unsynchronized hierarchy edit. */
    function pendingParents(operations) {
        const values = new Map();
        unsettled(operations, 'SET_CONCEPT_PARENTS', null)
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                const ids = op.payload && op.payload.parent_ids;
                values.set(op.entity_id, Array.isArray(ids) ? ids.slice() : []);
            });
        return values;
    }

    /* ---- effective projections ---- */

    /** One Concept row with every pending intent that names it applied. */
    function applyToRow(row, operations) {
        if (!row || typeof row !== 'object') return row;
        let out = row;
        const identity = pendingIdentities(operations).get(row.id);
        if (identity) {
            out = Object.assign({}, out, {
                name: identity.name || out.name,
                aliases: identity.aliases.slice(),
            });
        }
        const values = pendingFieldValues(operations, row.id);
        if (values.size) {
            out = Object.assign({}, out);
            values.forEach(function (value, field) { out[field] = value; });
        }
        return out;
    }

    /**
     * The Concept catalogue a user should see.
     *
     * A deletion is a TOMBSTONE: the Concept is hidden and nothing
     * acknowledged is destroyed, so a server that refuses restores it by doing
     * nothing.
     */
    function effectiveConcepts(rows, operations) {
        if (!Array.isArray(rows)) return rows;
        const byId = new Map(rows.map(row => [row && row.id, row]));
        pendingCreates(operations).forEach(function (op) {
            const row = catalogRowFromOp(op);
            if (row) byId.set(row.id, row);
        });
        const out = [];
        byId.forEach(function (row, id) {
            if (!row) return;
            out.push(applyToRow(row, operations));
        });
        pendingDeletions(operations).forEach(function (conceptId) {
            for (let i = out.length - 1; i >= 0; i -= 1) {
                if (out[i] && out[i].id === conceptId) out.splice(i, 1);
            }
        });
        /* `ORDER BY LOWER(name)` is what the endpoint does, and a pending
         * rename moves a row within it -- so the sort is recomputed here
         * rather than leaving a renamed Concept where its old name put it. */
        return out.sort(function (a, b) {
            const name = String(a.name || '').localeCompare(String(b.name || ''),
                undefined, { sensitivity: 'base' });
            return name || String(a.id).localeCompare(String(b.id));
        });
    }

    /**
     * One Concept's detail, with its pending definition, identity AND
     * hierarchy.
     *
     * `catalogue` supplies the NAME of a parent or child, including one created
     * on this device that exists in no cache at all. A parent whose name this
     * device cannot know is not invented -- it is left out, exactly as a folder
     * page leaves out a file it holds no row for.
     */
    function effectiveConceptDetail(concept, operations, catalogue) {
        if (!concept || typeof concept !== 'object') return concept;
        let out = applyToRow(concept, operations);
        const rows = Array.isArray(catalogue) ? catalogue : [];
        const nameOf = function (conceptId) {
            const row = rows.find(entry => entry && entry.id === conceptId);
            return row ? String(row.name || '') : null;
        };
        const parents = pendingParents(operations);
        const deleted = pendingDeletions(operations);

        if (parents.has(concept.id)) {
            const desired = parents.get(concept.id);
            const named = [];
            desired.forEach(function (parentId) {
                if (deleted.has(parentId)) return;
                const existing = (Array.isArray(out.parents) ? out.parents : [])
                    .find(p => p && p.id === parentId);
                const name = existing ? existing.name : nameOf(parentId);
                if (name == null) return;
                named.push({ id: parentId, name: name });
            });
            out = Object.assign({}, out, { parents: named });
        } else if (Array.isArray(out.parents) && deleted.size) {
            const kept = out.parents.filter(p => p && !deleted.has(p.id));
            if (kept.length !== out.parents.length) {
                out = Object.assign({}, out, { parents: kept });
            }
        }

        /* The OTHER end of every edge. A Concept given this one as a parent
         * elsewhere becomes its child here, and one that dropped it stops
         * being one -- the hierarchy is a single table seen from two sides. */
        if (Array.isArray(out.children)) {
            const byId = new Map(out.children.map(c => [String(c && c.id), c]));
            let changed = false;
            parents.forEach(function (parentIds, childId) {
                const wants = parentIds.indexOf(concept.id) !== -1;
                if (wants && !byId.has(childId)) {
                    const name = nameOf(childId);
                    if (name == null) return;
                    byId.set(childId, { id: childId, name: name });
                    changed = true;
                } else if (!wants && byId.has(childId)) {
                    byId.delete(childId);
                    changed = true;
                }
            });
            deleted.forEach(function (conceptId) {
                if (byId.delete(conceptId)) changed = true;
            });
            if (changed) {
                out = Object.assign({}, out, {
                    children: Array.from(byId.values()).sort(function (a, b) {
                        return String(a.name || '').localeCompare(String(b.name || ''),
                            undefined, { sensitivity: 'base' });
                    }),
                });
            }
        }
        return out;
    }

    /* ---- the pending-name map ---- */

    /* Hydrated from the durable queue and then read SYNCHRONOUSLY. A Concept
     * chip on another page renders from synchronous code, so an overlay that
     * had to await the store could only correct itself after the first paint. */
    let pendingNames = new Map();

    function setPendingConceptNames(operations) {
        const next = new Map();
        pendingCreates(operations).forEach(function (op) {
            const row = catalogRowFromOp(op);
            if (row) next.set(row.id, row.name);
        });
        pendingIdentities(operations).forEach(function (identity, conceptId) {
            if (identity.name) next.set(conceptId, identity.name);
        });
        pendingNames = next;
        return pendingNames.size;
    }

    async function refreshPendingConceptNames() {
        if (!root.prksSync || !root.prksSync.store) return [];
        let operations;
        try {
            operations = await root.prksSync.store.listOperations();
        } catch (_e) {
            return [];
        }
        setPendingConceptNames(operations);
        return operations;
    }

    /** A pending rename applied to any `{id, name}`-shaped rows. */
    function applyPendingConceptNames(rows) {
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

    /* ---- bases ---- */

    function isConceptStateShape(value, conceptId) {
        if (!value || typeof value !== 'object') return false;
        if (conceptId != null && value.concept_id !== conceptId) return false;
        if (!Number.isSafeInteger(value.identity_revision) || value.identity_revision < 0) {
            return false;
        }
        if (!Number.isSafeInteger(value.parents_revision) || value.parents_revision < 0) {
            return false;
        }
        const identity = value.identity;
        if (!identity || typeof identity !== 'object') return false;
        if (typeof identity.name !== 'string' || !Array.isArray(identity.aliases)) return false;
        if (identity.aliases.some(a => typeof a !== 'string')) return false;
        if (!Array.isArray(value.parent_ids) ||
            value.parent_ids.some(p => typeof p !== 'string')) return false;
        const fields = value.fields;
        if (!fields || typeof fields !== 'object') return false;
        for (let i = 0; i < FIELDS.length; i += 1) {
            const entry = fields[FIELDS[i]];
            if (!entry || typeof entry !== 'object') return false;
            if (!Number.isSafeInteger(entry.revision) || entry.revision < 0) return false;
        }
        return true;
    }

    async function readConceptState(conceptId, options) {
        const result = await root.prksOfflineReadEntity('concept-state', conceptId,
            '/api/concepts/' + encodeURIComponent(conceptId) + '/sync-state',
            Object.assign({}, options || {},
                { validate: v => isConceptStateShape(v, conceptId) }));
        if (result.value !== null && !isConceptStateShape(result.value, conceptId)) {
            await root.prksOfflineInvalidateEntity('concept-state', conceptId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    /** The state a Concept created here has, before any server has seen it. */
    function newConceptState(conceptId, row) {
        const fields = {};
        FIELDS.forEach(function (name) { fields[name] = { revision: 0 }; });
        return {
            concept_id: conceptId,
            fields: fields,
            identity: { name: String((row && row.name) || ''), aliases: [] },
            identity_revision: 0,
            parent_ids: [],
            parents_revision: 0,
        };
    }

    /**
     * The ACKNOWLEDGED base a Concept edit is measured against.
     *
     * Returns null when the base is not knowable: guessing revision 0 for a
     * Concept whose revisions this device has never read would silently
     * overwrite whatever another device wrote. A Concept created here and never
     * sent is the exception -- its construction payload IS the base, at
     * revision 0, which is known rather than assumed, because nothing else can
     * have written to an id no other device has seen.
     */
    async function acknowledgedConceptBase(conceptId, operations) {
        if (typeof conceptId !== 'string' || !conceptId) return null;
        const creating = pendingCreates(operations).find(op => op.entity_id === conceptId);
        if (creating) return newConceptState(conceptId, catalogRowFromOp(creating));
        try {
            const result = await readConceptState(conceptId);
            return (result && result.value) || null;
        } catch (_e) { return null; }
    }

    /** `{value, revision}` for the definition, or null when unknowable. */
    function observedConceptField(state, field) {
        if (!state || !state.fields) return null;
        const entry = state.fields[field];
        if (!entry) return null;
        return { value: '', revision: entry.revision };
    }

    /**
     * The field base, with its VALUE read from the cached Concept.
     *
     * The sync-state carries revisions for the definition but not its text --
     * the Concept detail already holds that, and a second copy would be a
     * second thing to keep true.
     */
    async function acknowledgedConceptFields(conceptId, operations) {
        const state = await acknowledgedConceptBase(conceptId, operations);
        if (!state) return null;
        const creating = pendingCreates(operations).find(op => op.entity_id === conceptId);
        let concept = creating ? catalogRowFromOp(creating) : null;
        if (!concept) {
            try {
                const cached = await root.prksOfflineReadEntity('concept', conceptId,
                    '/api/concepts/' + encodeURIComponent(conceptId), {});
                concept = cached && cached.value;
            } catch (_e) { concept = null; }
        }
        if (!concept) return null;
        const base = {};
        FIELDS.forEach(function (field) {
            const observed = observedConceptField(state, field);
            if (!observed) return;
            const raw = concept[field];
            base[field] = { value: raw == null ? '' : String(raw), revision: observed.revision };
        });
        return base;
    }

    /** `{name, aliases, revision}` the identity edit is measured against. */
    async function acknowledgedConceptIdentity(conceptId, operations) {
        const state = await acknowledgedConceptBase(conceptId, operations);
        if (!state) return null;
        return {
            name: state.identity.name,
            aliases: state.identity.aliases.slice(),
            revision: state.identity_revision,
        };
    }

    /** `{parent_ids, revision}` the hierarchy edit is measured against. */
    async function acknowledgedConceptParents(conceptId, operations) {
        const state = await acknowledgedConceptBase(conceptId, operations);
        if (!state) return null;
        return { parent_ids: state.parent_ids.slice(), revision: state.parents_revision };
    }

    /** What this editing session changed, measured against what it was showing. */
    function dirtyConceptFields(conceptId, draft, base, operations) {
        const changes = {};
        if (!draft || !base) return changes;
        const pending = pendingFieldValues(operations, conceptId);
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
        if (!runtime || !runtime.store) throw new Error('Concept editing is not available.');
        return runtime;
    }

    async function createConceptDurably(fields) {
        const runtime = sync();
        const op = await runtime.store.createConcept(fields);
        await refreshPendingConceptNames();
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function saveConceptFieldsDurably(conceptId, changes, base) {
        const runtime = sync();
        const written = await runtime.store.saveConceptFields(conceptId, changes, base);
        if (typeof runtime.changed === 'function') runtime.changed();
        return written;
    }

    async function setConceptIdentityDurably(conceptId, name, aliases, observed) {
        const runtime = sync();
        const op = await runtime.store.setConceptIdentity(conceptId, name, aliases, observed);
        /* Refresh the synchronous map HERE: a renamed Concept has to read
         * correctly on every surface that names one, the moment the user
         * reaches it. */
        await refreshPendingConceptNames();
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function setConceptParentsDurably(conceptId, parentIds, observed) {
        const runtime = sync();
        const op = await runtime.store.setConceptParents(conceptId, parentIds, observed);
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function deleteConceptDurably(conceptId) {
        const runtime = sync();
        const op = await runtime.store.deleteConcept(conceptId);
        await refreshPendingConceptNames();
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    /* ---- sync handlers ---- */

    const CREATE_REFUSALS = ['CONCEPT_EXISTS', 'AMBIGUOUS_CONCEPT'];

    const createHandler = {
        isResult: function (data, op) {
            if (!data || data.concept_id !== op.entity_id) return false;
            if (CREATE_REFUSALS.indexOf(data.code) !== -1) return true;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean' &&
                !!data.concept && data.concept.id === data.concept_id;
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileCreatedConcept(data),
    };

    const fieldHandler = {
        isResult: function (data, op) {
            if (!data || data.concept_id !== op.entity_id) return false;
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
        reconcile: (data, op) => root.prksOfflineReconcileConceptField(data, op),
    };

    const IDENTITY_REFUSALS = ['CONCEPT_EXISTS', 'AMBIGUOUS_CONCEPT', 'ALIAS_CONFLICT',
                               'ENTITY_NOT_FOUND'];

    const identityHandler = {
        isResult: function (data, op) {
            if (!data || data.concept_id !== op.entity_id) return false;
            switch (data.code) {
                case 'ACKNOWLEDGED':
                    return typeof data.changed === 'boolean' &&
                        Number.isSafeInteger(data.server_revision) &&
                        typeof data.name === 'string' && Array.isArray(data.aliases);
                case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                    return Number.isSafeInteger(data.current_revision) &&
                        typeof data.current_value === 'string';
                default:
                    return IDENTITY_REFUSALS.indexOf(data.code) !== -1;
            }
        },
        terminal: function (data) {
            const out = { code: data.code };
            if (Number.isSafeInteger(data.current_revision)) {
                out.current_revision = data.current_revision;
            }
            if (typeof data.current_value === 'string') out.current_value = data.current_value;
            if (typeof data.requested_value === 'string') {
                out.requested_value = data.requested_value;
            }
            return { conflict: out };
        },
        reconcile: (data, op) => root.prksOfflineReconcileConceptIdentity(data, op),
    };

    const PARENTS_REFUSALS = ['PARENT_NOT_FOUND', 'CONCEPT_CYCLE', 'ENTITY_NOT_FOUND'];

    const parentsHandler = {
        isResult: function (data, op) {
            if (!data || data.concept_id !== op.entity_id) return false;
            switch (data.code) {
                case 'ACKNOWLEDGED':
                    return typeof data.changed === 'boolean' &&
                        Number.isSafeInteger(data.server_revision) &&
                        Array.isArray(data.parent_ids);
                case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                    return Number.isSafeInteger(data.current_revision);
                default:
                    return PARENTS_REFUSALS.indexOf(data.code) !== -1;
            }
        },
        terminal: function (data) {
            /* Counts, never the two sets: a deep hierarchy's ids would not fit
             * the durable result bound, and the resolution UI re-reads the
             * Concept to show what the server has. */
            const out = { code: data.code };
            if (Number.isSafeInteger(data.current_revision)) {
                out.current_revision = data.current_revision;
            }
            return { conflict: out };
        },
        reconcile: (data, op) => root.prksOfflineReconcileConceptParents(data, op),
    };

    const deleteHandler = {
        isResult: function (data, op) {
            if (!data || data.concept_id !== op.entity_id) return false;
            if (data.code === 'CONCEPT_IN_USE') return true;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean';
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileDeletedConcept(data),
    };

    Object.assign(root, {
        PRKS_CONCEPT_FIELDS: FIELDS,
        PRKS_CONCEPT_FIELD_LABELS: LABELS,
        prksIsSupportedConceptField: isSupportedField,
        prksConceptRowFromOp: catalogRowFromOp,
        prksPendingConceptCreates: pendingCreates,
        prksPendingConceptDeletions: pendingDeletions,
        prksPendingConceptIdentities: pendingIdentities,
        prksPendingConceptParents: pendingParents,
        prksEffectiveConcepts: effectiveConcepts,
        prksEffectiveConceptDetail: effectiveConceptDetail,
        prksSetPendingConceptNames: setPendingConceptNames,
        prksRefreshPendingConceptNames: refreshPendingConceptNames,
        prksApplyPendingConceptNames: applyPendingConceptNames,
        prksIsConceptStateShape: isConceptStateShape,
        prksReadConceptState: readConceptState,
        prksNewConceptState: newConceptState,
        prksAcknowledgedConceptBase: acknowledgedConceptBase,
        prksAcknowledgedConceptFields: acknowledgedConceptFields,
        prksAcknowledgedConceptIdentity: acknowledgedConceptIdentity,
        prksAcknowledgedConceptParents: acknowledgedConceptParents,
        prksDirtyConceptFields: dirtyConceptFields,
        prksCreateConceptDurably: createConceptDurably,
        prksSaveConceptFieldsDurably: saveConceptFieldsDurably,
        prksSetConceptIdentityDurably: setConceptIdentityDurably,
        prksSetConceptParentsDurably: setConceptParentsDurably,
        prksDeleteConceptDurably: deleteConceptDurably,
        prksConceptCreateSyncHandler: createHandler,
        prksConceptFieldSyncHandler: fieldHandler,
        prksConceptIdentitySyncHandler: identityHandler,
        prksConceptParentsSyncHandler: parentsHandler,
        prksConceptDeleteSyncHandler: deleteHandler,
    });
})(typeof window === 'undefined' ? globalThis : window);
