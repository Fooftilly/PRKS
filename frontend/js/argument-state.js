/**
 * Arguments and Stances: pending overlays, bases, and the five operation shapes.
 *
 * Three INDEPENDENT fields, because `update_argument` writes each column on its
 * own and nothing derived changes when `kind` does. Two AGGREGATES, because the
 * server replaces each list whole -- and the target list is one aggregate over
 * both Positions and Arguments, since that is the single ordered list the user
 * chose.
 *
 * An Argument's name is read by more surfaces than its own pages -- a Position
 * lists the Arguments answering it, another Argument lists what it responds to,
 * and the target picker renders them all -- so a pending rename is hydrated
 * into a synchronous map, the way Position and Concept names are.
 *
 * Everything here is a projection over the durable operation list. Nothing
 * writes a pending value into the disposable cache.
 */
(function (root) {
    'use strict';

    const FIELDS = root.PRKS_LOCAL_ARGUMENT_FIELDS ||
        Object.freeze(['name', 'kind', 'main_text']);

    const LABELS = Object.freeze({
        name: 'Title', kind: 'Kind', main_text: 'Body',
    });

    function isSupportedField(field) {
        return typeof field === 'string' && FIELDS.indexOf(field) !== -1;
    }

    function unsettled(operations, operation, argumentId) {
        return (operations || []).filter(op => op &&
            op.entity_type === 'argument' && op.operation === operation &&
            (argumentId == null || op.entity_id === argumentId) &&
            op.status !== 'acknowledged');
    }

    function ordered(ops) {
        return ops.slice().sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
    }

    /* ---- construction ---- */

    function catalogRowFromOp(op) {
        if (!op || op.entity_type !== 'argument' || typeof op.entity_id !== 'string') return null;
        const payload = op.payload && typeof op.payload === 'object' ? op.payload : {};
        return {
            id: op.entity_id,
            name: String(payload.name == null ? '' : payload.name),
            kind: payload.kind === 'stance' ? 'stance' : 'argument',
            main_text: String(payload.main_text == null ? '' : payload.main_text),
            sources: Array.isArray(payload.sources) ? payload.sources.slice() : [],
            targets: Array.isArray(payload.targets) ? payload.targets.slice() : [],
        };
    }

    /**
     * Full detail shape for an Argument the server cannot know yet.
     *
     * Responses and note mentions are empty facts: no canonical row can point
     * at an id that has not been constructed. Verdict vocabulary is left empty
     * rather than guessed; an existing selected verdict id is still preserved
     * on each target row by the renderer.
     */
    function detailFromOp(op) {
        const row = catalogRowFromOp(op);
        if (!row) return null;
        return Object.assign({}, row, {
            responses: [], mentions: [], verdicts: [],
        });
    }

    function pendingCreates(operations) {
        return unsettled(operations, 'CREATE_ARGUMENT', null);
    }

    /* A refused deletion stops hiding its entity: see
     * `prksDurableDeletionAwaitsServer` in local-store.js for why. */
    const deletionAwaitsServer = root.prksDurableDeletionAwaitsServer ||
        function (op) {
            return !!op && op.status !== 'acknowledged' && op.status !== 'conflict';
        };

    function pendingDeletions(operations) {
        return new Set(unsettled(operations, 'DELETE_ARGUMENT', null)
            .filter(deletionAwaitsServer).map(op => op.entity_id));
    }

    /* ---- fields ---- */

    function pendingFieldValues(operations, argumentId) {
        const values = new Map();
        ordered(unsettled(operations, 'SET_ARGUMENT_FIELD', argumentId))
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

    /* ---- aggregates ---- */

    /* The LAST unsettled replacement wins, because each one names the whole
     * list: an earlier one is not a separate change to compose with, it is a
     * decision this device has already replaced. */
    function pendingAggregate(operations, operation, argumentId, key) {
        const ops = ordered(unsettled(operations, operation, argumentId));
        if (!ops.length) return null;
        const payload = ops[ops.length - 1].payload;
        const list = payload && payload[key];
        return Array.isArray(list) ? list : null;
    }

    function pendingSources(operations, argumentId) {
        return pendingAggregate(operations, 'SET_ARGUMENT_SOURCES', argumentId, 'sources');
    }

    function pendingTargets(operations, argumentId) {
        return pendingAggregate(operations, 'SET_ARGUMENT_TARGETS', argumentId, 'targets');
    }

    /* ---- effective projections ---- */

    /**
     * The Argument list a user should see.
     *
     * A deletion is a TOMBSTONE: the Argument is hidden and nothing
     * acknowledged is destroyed, so a server that refuses -- one research notes
     * still name, or one another Argument still answers -- restores it by doing
     * nothing.
     */
    function effectiveArguments(rows, operations) {
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
        pendingDeletions(operations).forEach(function (argumentId) {
            for (let i = out.length - 1; i >= 0; i -= 1) {
                if (out[i] && out[i].id === argumentId) out.splice(i, 1);
            }
        });
        /* NOT filtered by kind here. One cache key holds the whole collection
         * and the route selects its subset with `prksFilterArgumentsByKind`,
         * so a pending `kind` edit moves a row between the tabs by being
         * applied above -- filtering twice would be two places to keep true. */
        return out.sort(function (a, b) {
            const name = String(a.name || '').localeCompare(String(b.name || ''),
                undefined, { sensitivity: 'base' });
            return name || String(a.id).localeCompare(String(b.id));
        });
    }

    /** One Argument's detail, with its pending fields and aggregates applied. */
    function effectiveArgumentDetail(argument, operations) {
        if (!argument || typeof argument !== 'object') return argument;
        const out = applyFields(argument, pendingFieldValues(operations, argument.id));
        const sources = pendingSources(operations, argument.id);
        const targets = pendingTargets(operations, argument.id);
        if (!sources && !targets) return out;
        const next = out === argument ? Object.assign({}, argument) : out;
        if (sources) next.sources = hydrateSources(sources, argument.sources);
        if (targets) next.targets = hydrateTargets(targets, argument.targets);
        return next;
    }

    /**
     * A pending aggregate carries ids, and the detail renders titles.
     *
     * So each row is rebuilt from whatever the acknowledged detail already knew
     * about that entity. A row nothing is known about still appears -- the user
     * chose it and it is going to the server -- with only what the operation
     * itself carries, because inventing a title would be inventing a fact.
     */
    function hydrateSources(rows, known) {
        const byId = new Map((Array.isArray(known) ? known : [])
            .map(row => [row && row.work_id, row]));
        return rows.map(function (row) {
            const base = byId.get(row.work_id);
            return Object.assign({}, base || {}, {
                work_id: row.work_id,
                pages: row.pages == null ? '' : String(row.pages),
            });
        });
    }

    function hydrateTargets(rows, known) {
        const byId = new Map((Array.isArray(known) ? known : [])
            .map(row => [row && row.type + ':' + (row && row.id), row]));
        return rows.map(function (row) {
            const base = byId.get(row.type + ':' + row.id);
            return Object.assign({}, base || {}, {
                type: row.type, id: row.id, verdict_id: row.verdict_id,
            });
        });
    }

    /* ---- the pending-name map ---- */

    /* Hydrated from the durable queue and then read SYNCHRONOUSLY. A Position
     * renders the name of every Argument answering it, and a response picker
     * renders them all -- both from synchronous code, so a pending rename has
     * to be known before the paint. */
    let pendingNames = new Map();

    function setPendingArgumentNames(operations) {
        const next = new Map();
        pendingCreates(operations).forEach(function (op) {
            const row = catalogRowFromOp(op);
            if (row) next.set(row.id, row.name);
        });
        ordered(unsettled(operations, 'SET_ARGUMENT_FIELD', null))
            .forEach(function (op) {
                if (!op.payload || op.payload.field !== 'name') return;
                next.set(op.entity_id, String(op.payload.value || ''));
            });
        pendingNames = next;
        return pendingNames.size;
    }

    async function refreshPendingArgumentNames() {
        if (!root.prksSync || !root.prksSync.store) return [];
        let operations;
        try {
            operations = await root.prksSync.store.listOperations();
        } catch (_e) {
            return [];
        }
        setPendingArgumentNames(operations);
        return operations;
    }

    /** A pending rename applied to any `{id, name}`-shaped rows. */
    function applyPendingArgumentNames(rows) {
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
     * Narrowed to `type === 'argument'`, because a target row may also name a
     * POSITION and those ids live in a different space -- the Position overlay
     * owns that half of the same list.
     */
    function applyPendingArgumentNamesToTargets(targets) {
        if (!Array.isArray(targets) || !targets.length || !pendingNames.size) return targets;
        let changed = false;
        const out = targets.map(function (target) {
            if (!target || target.type !== 'argument') return target;
            const name = pendingNames.get(target.id);
            if (name == null || target.name === name) return target;
            changed = true;
            return Object.assign({}, target, { name: name });
        });
        return changed ? out : targets;
    }

    /* ---- bases ---- */

    function isArgumentStateShape(value, argumentId) {
        if (!value || typeof value !== 'object') return false;
        if (argumentId != null && value.argument_id !== argumentId) return false;
        const fields = value.fields;
        if (!fields || typeof fields !== 'object') return false;
        for (let i = 0; i < FIELDS.length; i += 1) {
            const entry = fields[FIELDS[i]];
            if (!entry || typeof entry !== 'object') return false;
            if (!Number.isSafeInteger(entry.revision) || entry.revision < 0) return false;
        }
        for (const key of ['sources', 'targets']) {
            const entry = value[key];
            if (!entry || typeof entry !== 'object') return false;
            if (!Number.isSafeInteger(entry.revision) || entry.revision < 0) return false;
        }
        return true;
    }

    async function readArgumentState(argumentId, options) {
        const result = await root.prksOfflineReadEntity('argument-state', argumentId,
            '/api/arguments/' + encodeURIComponent(argumentId) + '/sync-state',
            Object.assign({}, options || {},
                { validate: v => isArgumentStateShape(v, argumentId) }));
        if (result.value !== null && !isArgumentStateShape(result.value, argumentId)) {
            await root.prksOfflineInvalidateEntity('argument-state', argumentId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    function newArgumentState(argumentId) {
        const fields = {};
        FIELDS.forEach(function (name) { fields[name] = { revision: 0 }; });
        return {
            argument_id: argumentId, fields: fields,
            sources: { revision: 0 }, targets: { revision: 0 },
        };
    }

    function observedArgumentFields(argument, state) {
        const fields = state && state.fields && typeof state.fields === 'object'
            ? state.fields : {};
        const base = {};
        FIELDS.forEach(function (name) {
            const entry = fields[name];
            const revision = entry && Number.isSafeInteger(entry.revision) && entry.revision >= 0
                ? entry.revision : 0;
            const raw = argument ? argument[name] : null;
            base[name] = { value: raw == null ? '' : String(raw), revision: revision };
        });
        return base;
    }

    function canonicalSourceRows(rows) {
        return (Array.isArray(rows) ? rows : []).map(row => ({
            work_id: String((row && row.work_id) || ''),
            pages: row && row.pages != null ? String(row.pages) : '',
        }));
    }

    function canonicalTargetRows(rows) {
        return (Array.isArray(rows) ? rows : []).map(row => ({
            type: String((row && row.type) || ''),
            id: String((row && row.id) || ''),
            verdict_id: String((row && row.verdict_id) || ''),
        }));
    }

    function sameSourceRows(left, right) {
        const a = canonicalSourceRows(left);
        const b = canonicalSourceRows(right);
        return a.length === b.length && a.every(function (row, index) {
            return row.work_id === b[index].work_id && row.pages === b[index].pages;
        });
    }

    function sameTargetRows(left, right) {
        const a = canonicalTargetRows(left);
        const b = canonicalTargetRows(right);
        return a.length === b.length && a.every(function (row, index) {
            return row.type === b[index].type && row.id === b[index].id &&
                row.verdict_id === b[index].verdict_id;
        });
    }

    /** Dirty only when draft differs from effective list editor showed. */
    function dirtyArgumentSources(argumentId, draft, base, operations) {
        if (!base || !Array.isArray(draft)) return false;
        const pending = pendingSources(operations, argumentId);
        const shown = pending || (base.sources || []);
        return !sameSourceRows(draft, shown);
    }

    /** Position and Argument targets remain one ordered aggregate here. */
    function dirtyArgumentTargets(argumentId, draft, base, operations) {
        if (!base || !Array.isArray(draft)) return false;
        const pending = pendingTargets(operations, argumentId);
        const shown = pending || (base.targets || []);
        return !sameTargetRows(draft, shown);
    }

    /** The acknowledged citation list and its revision. */
    function observedArgumentSources(argument, state) {
        const entry = state && state.sources;
        return {
            sources: canonicalSourceRows(argument && argument.sources),
            revision: entry && Number.isSafeInteger(entry.revision) && entry.revision >= 0
                ? entry.revision : 0,
        };
    }

    /** The acknowledged target list and its revision, both kinds together. */
    function observedArgumentTargets(argument, state) {
        const entry = state && state.targets;
        return {
            targets: canonicalTargetRows(argument && argument.targets),
            revision: entry && Number.isSafeInteger(entry.revision) && entry.revision >= 0
                ? entry.revision : 0,
        };
    }

    /**
     * The ACKNOWLEDGED base an Argument edit is measured against.
     *
     * Returns null when the base is not knowable: guessing revision 0 for an
     * Argument whose revisions this device has never read would silently
     * overwrite whatever another device wrote. An Argument created here and
     * never sent is the exception -- its construction payload IS the base, at
     * revision 0, which is known rather than assumed.
     */
    async function acknowledgedArgumentBase(argumentId, operations) {
        if (typeof argumentId !== 'string' || !argumentId) return null;
        const creating = pendingCreates(operations).find(op => op.entity_id === argumentId);
        if (creating) {
            const row = catalogRowFromOp(creating);
            const state = newArgumentState(argumentId);
            return {
                fields: observedArgumentFields(row, state),
                sources: observedArgumentSources(row, state),
                targets: observedArgumentTargets(row, state),
            };
        }
        let state = null;
        try {
            const result = await readArgumentState(argumentId);
            state = result && result.value;
        } catch (_e) { state = null; }
        if (!state) return null;
        let argument = null;
        try {
            const cached = await root.prksOfflineReadEntity('argument', argumentId,
                '/api/arguments/' + encodeURIComponent(argumentId), {});
            argument = cached && cached.value;
        } catch (_e) { argument = null; }
        if (!argument) return null;
        return {
            fields: observedArgumentFields(argument, state),
            sources: observedArgumentSources(argument, state),
            targets: observedArgumentTargets(argument, state),
        };
    }

    /** What this editing session changed, measured against what it was showing. */
    function dirtyArgumentFields(argumentId, draft, base, operations) {
        const changes = {};
        if (!draft || !base) return changes;
        const pending = pendingFieldValues(operations, argumentId);
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
        if (!runtime || !runtime.store) throw new Error('Argument editing is not available.');
        return runtime;
    }

    async function after(op, graphStructureChanged) {
        await refreshPendingArgumentNames();
        if (graphStructureChanged &&
            typeof root.prksOfflineMarkResearchGraphCoreChanged === 'function') {
            root.prksOfflineMarkResearchGraphCoreChanged();
            if (typeof root.prksOfflineMarkResearchGraphPeopleChanged === 'function') {
                root.prksOfflineMarkResearchGraphPeopleChanged();
            }
        }
        const runtime = root.prksSync;
        if (runtime && typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function createArgumentDurably(fields) {
        return after(await sync().store.createArgument(fields), true);
    }

    async function saveArgumentFieldsDurably(argumentId, changes, base) {
        return after(await sync().store.saveArgumentFields(argumentId, changes, base));
    }

    async function setArgumentSourcesDurably(argumentId, sources, observed) {
        return after(await sync().store.setArgumentSources(argumentId, sources, observed), true);
    }

    async function setArgumentTargetsDurably(argumentId, targets, observed) {
        return after(await sync().store.setArgumentTargets(argumentId, targets, observed), true);
    }

    async function deleteArgumentDurably(argumentId) {
        return after(await sync().store.deleteArgument(argumentId), true);
    }

    /* ---- sync handlers ---- */

    /* Named refusals only. Anything outside this vocabulary is not an answer
     * this family understands, and is treated as a transport failure so the
     * operation stays retryable rather than being silently consumed. */
    const CREATE_REFUSALS = ['WORK_NOT_FOUND', 'POSITION_NOT_FOUND', 'TARGET_NOT_FOUND',
                             'INVALID_VERDICT', 'ARGUMENT_CYCLE'];
    const SOURCES_REFUSALS = ['WORK_NOT_FOUND', 'ENTITY_NOT_FOUND'];
    const TARGETS_REFUSALS = ['POSITION_NOT_FOUND', 'TARGET_NOT_FOUND', 'INVALID_VERDICT',
                              'ARGUMENT_CYCLE', 'ENTITY_NOT_FOUND'];
    const DELETE_REFUSALS = ['ARGUMENT_IN_USE', 'ARGUMENT_TARGETED'];

    const createHandler = {
        isResult: function (data, op) {
            if (!data || data.argument_id !== op.entity_id) return false;
            if (data.code === 'ACKNOWLEDGED') return typeof data.changed === 'boolean';
            return CREATE_REFUSALS.indexOf(data.code) !== -1;
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileCreatedArgument(data),
    };

    const fieldHandler = {
        isResult: function (data, op) {
            if (!data || data.argument_id !== op.entity_id) return false;
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
        reconcile: (data, op) => root.prksOfflineReconcileArgumentField(data, op),
    };

    function aggregateHandler(refusals, reconcile) {
        return {
            isResult: function (data, op) {
                if (!data || data.argument_id !== op.entity_id) return false;
                switch (data.code) {
                    case 'ACKNOWLEDGED':
                        return typeof data.changed === 'boolean' &&
                            Number.isSafeInteger(data.server_revision) &&
                            data.server_revision >= 0;
                    case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                        return Number.isSafeInteger(data.current_revision);
                    default:
                        return refusals.indexOf(data.code) !== -1;
                }
            },
            /* Counts, never the two lists: a long citation list would not fit
             * the durable result bound, and the resolution UI re-reads the
             * Argument to show what the server has. */
            terminal: function (data) {
                const out = { code: data.code };
                if (Number.isSafeInteger(data.current_revision)) {
                    out.current_revision = data.current_revision;
                }
                return { conflict: out };
            },
            reconcile: reconcile,
        };
    }

    const sourcesHandler = aggregateHandler(SOURCES_REFUSALS,
        (data, op) => root.prksOfflineReconcileArgumentSources(data, op));
    const targetsHandler = aggregateHandler(TARGETS_REFUSALS,
        (data, op) => root.prksOfflineReconcileArgumentTargets(data, op));

    const deleteHandler = {
        isResult: function (data, op) {
            if (!data || data.argument_id !== op.entity_id) return false;
            if (DELETE_REFUSALS.indexOf(data.code) !== -1) return true;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean';
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileDeletedArgument(data),
    };

    Object.assign(root, {
        PRKS_ARGUMENT_FIELDS: FIELDS,
        PRKS_ARGUMENT_FIELD_LABELS: LABELS,
        prksIsSupportedArgumentField: isSupportedField,
        prksArgumentRowFromOp: catalogRowFromOp,
        prksArgumentDetailFromOp: detailFromOp,
        prksPendingArgumentCreates: pendingCreates,
        prksPendingArgumentDeletions: pendingDeletions,
        prksPendingArgumentSources: pendingSources,
        prksPendingArgumentTargets: pendingTargets,
        prksEffectiveArguments: effectiveArguments,
        prksEffectiveArgumentDetail: effectiveArgumentDetail,
        prksSetPendingArgumentNames: setPendingArgumentNames,
        prksRefreshPendingArgumentNames: refreshPendingArgumentNames,
        prksApplyPendingArgumentNames: applyPendingArgumentNames,
        prksApplyPendingArgumentNamesToTargets: applyPendingArgumentNamesToTargets,
        prksIsArgumentStateShape: isArgumentStateShape,
        prksReadArgumentState: readArgumentState,
        prksNewArgumentState: newArgumentState,
        prksObservedArgumentFields: observedArgumentFields,
        prksObservedArgumentSources: observedArgumentSources,
        prksObservedArgumentTargets: observedArgumentTargets,
        prksAcknowledgedArgumentBase: acknowledgedArgumentBase,
        prksDirtyArgumentFields: dirtyArgumentFields,
        prksSameArgumentSources: sameSourceRows,
        prksSameArgumentTargets: sameTargetRows,
        prksDirtyArgumentSources: dirtyArgumentSources,
        prksDirtyArgumentTargets: dirtyArgumentTargets,
        prksCreateArgumentDurably: createArgumentDurably,
        prksSaveArgumentFieldsDurably: saveArgumentFieldsDurably,
        prksSetArgumentSourcesDurably: setArgumentSourcesDurably,
        prksSetArgumentTargetsDurably: setArgumentTargetsDurably,
        prksDeleteArgumentDurably: deleteArgumentDurably,
        prksArgumentCreateSyncHandler: createHandler,
        prksArgumentFieldSyncHandler: fieldHandler,
        prksArgumentSourcesSyncHandler: sourcesHandler,
        prksArgumentTargetsSyncHandler: targetsHandler,
        prksArgumentDeleteSyncHandler: deleteHandler,
    });
})(typeof window === 'undefined' ? globalThis : window);
