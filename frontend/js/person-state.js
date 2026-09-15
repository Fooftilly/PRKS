/**
 * CREATE_PERSON: pending overlay onto the People index and Person detail.
 *
 * A newly created Person is usable before acknowledgement -- the role picker
 * and the People list read the overlay, never a row written into the
 * disposable cache. Acknowledgement patches those snapshots in place.
 */
(function (root) {
    'use strict';

    const FIELDS = root.PRKS_LOCAL_PERSON_FIELDS || Object.freeze([
        'first_name', 'last_name', 'aliases', 'about', 'image_url',
        'link_wikipedia', 'link_stanford_encyclopedia', 'link_iep',
        'links_other', 'birth_date', 'death_date',
    ]);

    function pendingCreates(operations) {
        return (operations || []).filter(op => op && op.operation === 'CREATE_PERSON' &&
            op.entity_type === 'person' && op.status !== 'acknowledged');
    }

    /** Every Person id this device is waiting to have deleted. */
    function pendingDeletions(operations) {
        return new Set((operations || [])
            .filter(op => op && op.operation === 'DELETE_PERSON' &&
                op.entity_type === 'person' && op.status !== 'acknowledged')
            .map(op => op.entity_id));
    }

    function catalogRowFromOp(op) {
        if (!op || op.entity_type !== 'person' || typeof op.entity_id !== 'string') return null;
        const payload = op.payload && typeof op.payload === 'object' ? op.payload : {};
        const row = { id: op.entity_id, assigned_roles: [], groups: [], works: [] };
        FIELDS.forEach(function (name) {
            const value = payload[name];
            row[name] = value == null ? '' : String(value);
        });
        return row;
    }

    function orderPeople(rows) {
        return rows.slice().sort(function (a, b) {
            const last = String(a.last_name || '').localeCompare(String(b.last_name || ''),
                undefined, { sensitivity: 'base' });
            if (last) return last;
            const first = String(a.first_name || '').localeCompare(String(b.first_name || ''),
                undefined, { sensitivity: 'base' });
            if (first) return first;
            return String(a.id).localeCompare(String(b.id));
        });
    }

    /**
     * The People a user should see: the acknowledged list, plus the ones this
     * device created, minus the ones it has asked to delete.
     *
     * A deletion is a TOMBSTONE. Nothing is removed from the acknowledged
     * cache, so a server that refuses the deletion -- a Person credited on a
     * file is protected -- restores them by doing nothing at all.
     */
    function effectivePeople(rows, operations) {
        if (!Array.isArray(rows)) return null;
        const byId = new Map(rows.map(row => [row && row.id, row]));
        pendingCreates(operations).forEach(function (op) {
            const row = catalogRowFromOp(op);
            if (row) byId.set(row.id, row);
        });
        pendingDeletions(operations).forEach(function (personId) { byId.delete(personId); });
        return orderPeople(Array.from(byId.values()).filter(Boolean));
    }

    function mergeCreatedPerson(rows, ack) {
        const person = ack && ack.person;
        if (!Array.isArray(rows) || !person || person.id !== ack.person_id) return null;
        return orderPeople(rows.filter(row => row && row.id !== person.id).concat([person]));
    }

    function detailFromOp(op) {
        const row = catalogRowFromOp(op);
        if (!row) return null;
        return Object.assign({}, row, { works: [], groups: row.groups || [] });
    }

    async function createPersonDurably(fields) {
        const sync = root.prksSync;
        if (!sync || !sync.store || typeof sync.store.createPerson !== 'function') {
            throw new Error('Person creation is not available.');
        }
        const op = await sync.store.createPerson(fields);
        if (typeof sync.changed === 'function') sync.changed();
        return op;
    }

    function isResult(data, op) {
        if (!data || data.person_id !== op.entity_id) return false;
        if (data.code !== 'ACKNOWLEDGED') return false;
        return typeof data.changed === 'boolean' &&
            !!data.person && data.person.id === data.person_id;
    }

    async function deletePersonDurably(personId) {
        const sync = root.prksSync;
        if (!sync || !sync.store || typeof sync.store.deletePerson !== 'function') {
            throw new Error('Person deletion is not available.');
        }
        const op = await sync.store.deletePerson(personId);
        if (typeof sync.changed === 'function') sync.changed();
        return op;
    }

    const deleteHandler = {
        isResult: function (data, op) {
            if (!data || data.person_id !== op.entity_id) return false;
            /* A Person credited on a file is PROTECTED, and the refusal is
             * terminal: the same answer comes back forever until the user
             * unlinks them, so retrying is a loop rather than a recovery. */
            if (data.code === 'PERSON_HAS_LINKS') return true;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean';
        },
        terminal: function (data) {
            const out = { code: data.code };
            if (Number.isSafeInteger(data.current_revision)) {
                out.current_revision = data.current_revision;
            }
            return { conflict: out };
        },
        reconcile: data => root.prksOfflineReconcileDeletedPerson(data),
    };

    function terminal(data) {
        return { discard: data && data.code };
    }

    const handler = {
        isResult: isResult,
        terminal: terminal,
        reconcile: data => root.prksOfflineReconcileCreatedPerson(data),
    };

    Object.assign(root, {
        PRKS_PERSON_SYNC_FIELDS: FIELDS,
        prksPendingPersonCreates: pendingCreates,
        prksPendingPersonDeletions: pendingDeletions,
        prksDeletePersonDurably: deletePersonDurably,
        prksPersonDeleteSyncHandler: deleteHandler,
        prksPersonCatalogRowFromOp: catalogRowFromOp,
        prksEffectivePeople: effectivePeople,
        prksMergeCreatedPerson: mergeCreatedPerson,
        prksPendingPersonDetail: detailFromOp,
        prksCreatePersonDurably: createPersonDurably,
        prksPersonSyncHandler: handler,
    });
})(typeof window === 'undefined' ? globalThis : window);
