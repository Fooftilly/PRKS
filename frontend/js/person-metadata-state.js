/**
 * SET_PERSON_METADATA_FIELD: pending profile edits, and their effective state.
 *
 * The conflict unit is one FIELD, matching `backend/person_metadata_sync.py`:
 * a biography and a birth date are separate decisions, so a busy scope blocks
 * only its own field and the rest of the form stays editable.
 *
 * Everything here is a projection over the durable operation list. Nothing in
 * this module writes a pending value into the disposable cache -- the cache
 * holds only what the server has acknowledged, and the effective value a user
 * sees is that plus the intent they have not synchronized yet.
 */
(function (root) {
    'use strict';

    const FIELDS = root.PRKS_LOCAL_PERSON_FIELDS || Object.freeze([
        'first_name', 'last_name', 'aliases', 'about', 'image_url',
        'link_wikipedia', 'link_stanford_encyclopedia', 'link_iep',
        'links_other', 'birth_date', 'death_date',
    ]);

    /* The user's words for each field, for Diagnostics and for conflict
     * messages. Never a raw column name: "link_stanford_encyclopedia = ..." is
     * the protocol talking, not the product. */
    const LABELS = Object.freeze({
        first_name: 'First name', last_name: 'Last name', aliases: 'Also known as',
        about: 'Biography', image_url: 'Portrait URL',
        link_wikipedia: 'Wikipedia link',
        link_stanford_encyclopedia: 'Stanford Encyclopedia link',
        link_iep: 'Internet Encyclopedia link', links_other: 'Other links',
        birth_date: 'Born', death_date: 'Died',
    });

    /* Fields whose value is DISPLAYED outside the Person's own record: a Work
     * card's credit line, a Graph label, a cached Argument's source. Changing
     * one of these stales read models this module cannot patch precisely,
     * because the name is embedded in rows keyed by Work rather than by
     * Person. Everything else on the profile -- biography, links, dates,
     * portrait -- is absent from every one of those projections and must not
     * cost the user their cache. The ordinary PATCH boundary draws exactly the
     * same line; this is that rule, in the durable path. */
    const DISPLAY_FIELDS = Object.freeze(['first_name', 'last_name']);

    function isSupportedField(field) {
        return typeof field === 'string' && FIELDS.indexOf(field) !== -1;
    }

    function pendingFieldOps(operations, personId) {
        return (operations || []).filter(op => op &&
            op.operation === 'SET_PERSON_METADATA_FIELD' &&
            op.entity_type === 'person' &&
            (personId == null || op.entity_id === personId) &&
            op.status !== 'acknowledged');
    }

    /**
     * The unsynchronized value of each field, as a map.
     *
     * Later operations win. The store coalesces to at most one unsynchronized
     * row per (Person, field), so in practice there is one -- but a row in
     * conflict is not coalesced against, so two can coexist and the ORDER is
     * what decides. `sequence` is that order; it is the order the user made
     * the changes in.
     */
    function pendingFieldValues(operations, personId) {
        const values = new Map();
        pendingFieldOps(operations, personId)
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                const field = op.payload && op.payload.field;
                if (!isSupportedField(field)) return;
                values.set(field, String((op.payload && op.payload.value) || ''));
            });
        return values;
    }

    /**
     * The value the user should see for one field: the acknowledged value,
     * overlaid with their own unsynchronized intent.
     */
    function effectivePersonFields(person, operations) {
        if (!person || typeof person !== 'object') return person;
        const values = pendingFieldValues(operations, person.id);
        if (!values.size) return person;
        const out = Object.assign({}, person);
        values.forEach(function (value, field) { out[field] = value; });
        return out;
    }

    /** The same overlay across a list of catalogue rows. */
    function effectivePersonRows(rows, operations) {
        if (!Array.isArray(rows)) return rows;
        const pending = pendingFieldOps(operations, null);
        if (!pending.length) return rows;
        const byPerson = new Map();
        pending.forEach(function (op) {
            if (!byPerson.has(op.entity_id)) byPerson.set(op.entity_id, []);
            byPerson.get(op.entity_id).push(op);
        });
        return rows.map(function (row) {
            if (!row || !byPerson.has(row.id)) return row;
            return effectivePersonFields(row, byPerson.get(row.id));
        });
    }

    /** Every Person id this device holds an unsynchronized profile edit for. */
    function personsWithPendingEdits(operations) {
        return Array.from(new Set(pendingFieldOps(operations, null).map(op => op.entity_id)));
    }

    /**
     * The base an edit is measured against: the acknowledged value and the
     * revision it carried.
     *
     * The two come from different places on purpose. Values live on the Person
     * record, which the client already caches; revisions live in the
     * `person-metadata-state` projection, which carries revisions ALONE so it
     * does not become a second copy of every biography. A Person that exists
     * only as a pending creation has no state projection at all and every
     * field is at revision 0 -- which is true, and is what lets an edit be
     * made before the creation has reached the server.
     */
    function observedPersonFields(person, state) {
        const fields = state && state.fields && typeof state.fields === 'object'
            ? state.fields : {};
        const base = {};
        FIELDS.forEach(function (name) {
            const entry = fields[name];
            const revision = entry && Number.isSafeInteger(entry.revision) && entry.revision >= 0
                ? entry.revision : 0;
            const value = person && person[name] != null ? String(person[name]) : '';
            base[name] = { value: value, revision: revision };
        });
        return base;
    }

    function isPersonMetadataStateShape(value, personId) {
        if (!value || typeof value !== 'object') return false;
        if (personId != null && value.person_id !== personId) return false;
        const fields = value.fields;
        if (!fields || typeof fields !== 'object') return false;
        for (let i = 0; i < FIELDS.length; i += 1) {
            const entry = fields[FIELDS[i]];
            if (!entry || typeof entry !== 'object') return false;
            if (!Number.isSafeInteger(entry.revision) || entry.revision < 0) return false;
        }
        return true;
    }

    /**
     * The cached `person-metadata-state` projection for one Person.
     *
     * Unknown is not empty. A Person whose revisions this device has never
     * read cannot be edited safely -- guessing revision 0 would overwrite
     * whatever another device wrote, which is exactly what a base revision
     * exists to prevent. The caller distinguishes that from the one case where
     * revision 0 is KNOWN rather than assumed: a Person who exists only as a
     * pending creation, whom the server has never heard of.
     */
    async function readPersonMetadataState(personId, options) {
        const opts = options || {};
        const result = await root.prksOfflineReadEntity('person-metadata-state', personId,
            '/api/persons/' + encodeURIComponent(personId) + '/metadata-state',
            Object.assign({}, opts, { validate: v => isPersonMetadataStateShape(v, personId) }));
        if (result.value !== null && !isPersonMetadataStateShape(result.value, personId)) {
            await root.prksOfflineInvalidateEntity('person-metadata-state', personId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    /** Every field at revision 0, for a Person the server has never seen. */
    function newPersonMetadataState(personId) {
        const fields = {};
        FIELDS.forEach(function (name) { fields[name] = { revision: 0 }; });
        return { person_id: personId, fields: fields };
    }

    /**
     * The ACKNOWLEDGED base an edit is measured against.
     *
     * Three concepts, deliberately kept apart. The record the user is looking
     * at is the EFFECTIVE one -- acknowledged plus this device's own
     * unsynchronized intent -- and the draft is a third thing again: what is
     * currently typed. Handing the effective record in as the base would make
     * every pending value indistinguishable from the server's own, so an edit
     * back to what the server actually holds would look like a change and
     * leave a pending operation behind asking for a value nobody changed.
     *
     * Values come from the acknowledged Person record; revisions from the
     * `person-metadata-state` projection, which carries revisions ALONE so it
     * does not become a second copy of every biography.
     *
     * A Person who exists only because of an unsynchronized `CREATE_PERSON` has
     * neither. Their base is that CONSTRUCTION payload at revision 0 -- known
     * rather than assumed, because the server has never heard of them. The
     * edit is still ordered behind the creation by the generic dependency
     * mechanism; it is never folded into the creation's payload, because two
     * decisions the user made separately stay two decisions.
     *
     * Returns null when the base is not knowable. Unknown is never empty:
     * guessing revision 0 for a Person whose revisions this device has never
     * read would silently overwrite whatever another device wrote, which is
     * the one thing a base revision exists to prevent.
     */
    async function acknowledgedPersonBase(personId, operations) {
        if (typeof personId !== 'string' || !personId) return null;
        const creating = typeof root.prksPendingPersonCreates === 'function'
            ? root.prksPendingPersonCreates(operations)
                .find(op => op && op.entity_id === personId)
            : null;
        if (creating) {
            const constructed = typeof root.prksPersonCatalogRowFromOp === 'function'
                ? root.prksPersonCatalogRowFromOp(creating) : null;
            return constructed
                ? observedPersonFields(constructed, newPersonMetadataState(personId))
                : null;
        }
        let state = null;
        try {
            const result = await readPersonMetadataState(personId);
            state = result && result.value;
        } catch (_e) { state = null; }
        if (!state) return null;
        let person = null;
        try {
            /* The acknowledged record, read through the ordinary cache -- NOT
             * the overlaid one a component is holding. */
            const result = await root.prksOfflineReadEntity('person', personId,
                '/api/persons/' + encodeURIComponent(personId), {
                    validate: v => typeof root.prksIsPersonShape !== 'function' ||
                        root.prksIsPersonShape(v, personId),
                });
            person = result && result.value;
        } catch (_e) { person = null; }
        return person ? observedPersonFields(person, state) : null;
    }

    /**
     * What the user actually changed, measured against what the form was
     * SHOWING -- the pending value where there is one, the acknowledged value
     * otherwise.
     *
     * Measuring against the acknowledged base alone would be wrong in both
     * directions: a field still displaying an untouched pending value would
     * look dirty on every save, and a field edited back to its server value
     * would look unchanged and quietly leave its pending operation in place.
     *
     * Sending every field on every save is worse than merely wasteful. One
     * field that is syncing or in conflict would refuse the entire form, so a
     * single stuck biography would make the birth date uneditable -- which is
     * exactly the independence the per-field conflict unit exists to give.
     *
     * `draft` is field -> the canonical desired string; the caller has already
     * interpreted anything the form spells loosely, such as a date.
     */
    function dirtyPersonFields(personId, draft, base, operations) {
        const changes = {};
        if (!draft || !base) return changes;
        const pending = pendingFieldValues(operations, personId);
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

    /* ---- the pending-name map ---- */

    /* Hydrated from the durable queue and then read SYNCHRONOUSLY, exactly as
     * the relationship overlay's map is. A Work card, a role chip and a Graph
     * label are rendered from synchronous code paths, so an overlay that had
     * to await the store could only correct itself after the first paint --
     * which is the flicker the hydration step exists to prevent. */
    let pendingNames = new Map();

    function setPendingPersonNames(rows) {
        const next = new Map();
        pendingFieldOps(rows, null)
            .filter(op => DISPLAY_FIELDS.indexOf(op.payload && op.payload.field) !== -1)
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                const patch = next.get(op.entity_id) || {};
                patch[op.payload.field] = String(op.payload.value || '');
                next.set(op.entity_id, patch);
            });
        pendingNames = next;
        return pendingNames.size;
    }

    async function refreshPendingPersonNames() {
        if (!root.prksSync || !root.prksSync.store) return [];
        let rows;
        try {
            rows = await root.prksSync.store.listOperations();
        } catch (_e) {
            return [];
        }
        setPendingPersonNames(rows);
        return rows;
    }

    /**
     * A pending profile edit applied to rows that DISPLAY a Person's name
     * somewhere else -- a Work's `roles[]`, a Graph people layer, a cached
     * Argument's sources.
     *
     * Only the name fields travel here. Those rows are keyed by Work rather
     * than by Person, which is why the reconciler invalidates them on
     * acknowledgement instead of patching them -- but a PENDING edit is in no
     * cache at all, so nothing else would ever make it visible. The overlay
     * does what the reconciler cannot, and stops where the reconciler takes
     * over.
     */
    function applyPendingPersonNames(rows, idOf) {
        if (!Array.isArray(rows) || !rows.length || !pendingNames.size) return rows;
        const identify = typeof idOf === 'function'
            ? idOf : (row => String((row && (row.person_id || row.id)) || ''));
        let changed = false;
        const out = rows.map(function (row) {
            const patch = pendingNames.get(identify(row));
            if (!patch) return row;
            changed = true;
            return Object.assign({}, row, patch);
        });
        return changed ? out : rows;
    }

    async function savePersonFieldsDurably(personId, changes, base) {
        const sync = root.prksSync;
        if (!sync || !sync.store || typeof sync.store.savePersonMetadataFields !== 'function') {
            throw new Error('Profile editing is not available.');
        }
        const written = await sync.store.savePersonMetadataFields(personId, changes, base);
        /* Refresh the synchronous name map HERE, not only at route hydration.
         * A rename made on the Person page has to be visible the moment the
         * user reaches a Work that credits them, and the surfaces that render
         * a credit read the map synchronously -- so a map refreshed only when
         * a route next hydrates would show the old name until something else
         * happened to read the queue. */
        await refreshPendingPersonNames();
        if (typeof sync.changed === 'function') sync.changed();
        return written;
    }

    function isResult(data, op) {
        if (!data || data.person_id !== op.entity_id) return false;
        if (data.field !== (op.payload && op.payload.field)) return false;
        switch (data.code) {
            case 'ACKNOWLEDGED':
                /* No `value` is echoed back -- see the server handler: no
                 * profile field has a length bound, and a result the client
                 * cannot durably store is retried forever. The authoritative
                 * value is the one in this operation's own immutable payload. */
                return typeof data.changed === 'boolean' &&
                    Number.isSafeInteger(data.server_revision) && data.server_revision >= 0 &&
                    data.value_omitted === true;
            case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                return Number.isSafeInteger(data.current_revision) &&
                    (typeof data.current_value === 'string' ||
                        typeof data.current_preview === 'string');
            case 'ENTITY_NOT_FOUND':
                return true;
            default: return false;
        }
    }

    /* Every terminal outcome is the user's to resolve: they typed this
     * deliberately, so discarding it silently would lose a real decision. */
    function terminal(data) {
        const out = { code: data.code };
        if (Number.isSafeInteger(data.current_revision)) out.current_revision = data.current_revision;
        if (typeof data.current_value === 'string') out.current_value = data.current_value;
        if (typeof data.current_preview === 'string') {
            out.current_preview = data.current_preview;
            if (Number.isSafeInteger(data.current_bytes)) out.current_bytes = data.current_bytes;
        }
        return { conflict: out };
    }

    const handler = {
        isResult: isResult,
        terminal: terminal,
        reconcile: (data, op) => root.prksOfflineReconcilePersonField(data, op),
    };

    Object.assign(root, {
        PRKS_PERSON_METADATA_FIELDS: FIELDS,
        PRKS_PERSON_FIELD_LABELS: LABELS,
        PRKS_PERSON_DISPLAY_FIELDS: DISPLAY_FIELDS,
        prksIsSupportedPersonField: isSupportedField,
        prksPendingPersonFieldOps: pendingFieldOps,
        prksEffectivePersonFields: effectivePersonFields,
        prksEffectivePersonRows: effectivePersonRows,
        prksPersonsWithPendingEdits: personsWithPendingEdits,
        prksObservedPersonFields: observedPersonFields,
        prksPendingPersonFieldValues: pendingFieldValues,
        prksAcknowledgedPersonBase: acknowledgedPersonBase,
        prksDirtyPersonFields: dirtyPersonFields,
        prksIsPersonMetadataStateShape: isPersonMetadataStateShape,
        prksReadPersonMetadataState: readPersonMetadataState,
        prksNewPersonMetadataState: newPersonMetadataState,
        prksApplyPendingPersonNames: applyPendingPersonNames,
        prksSetPendingPersonNames: setPendingPersonNames,
        prksRefreshPendingPersonNames: refreshPendingPersonNames,
        prksSavePersonFieldsDurably: savePersonFieldsDurably,
        prksPersonMetadataSyncHandler: handler,
    });
})(typeof window === 'undefined' ? globalThis : window);
