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

    function effectivePeople(rows, operations) {
        if (!Array.isArray(rows)) return null;
        const byId = new Map(rows.map(row => [row && row.id, row]));
        pendingCreates(operations).forEach(function (op) {
            const row = catalogRowFromOp(op);
            if (row) byId.set(row.id, row);
        });
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
        prksPersonCatalogRowFromOp: catalogRowFromOp,
        prksEffectivePeople: effectivePeople,
        prksMergeCreatedPerson: mergeCreatedPerson,
        prksPendingPersonDetail: detailFromOp,
        prksCreatePersonDurably: createPersonDurably,
        prksPersonSyncHandler: handler,
    });
})(typeof window === 'undefined' ? globalThis : window);
