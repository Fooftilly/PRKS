/**
 * Person Groups: pending overlays, bases, and the four operation shapes.
 *
 * Construction mints the id here; the three editable columns are independent
 * FIELDS; membership is an element of a SET keyed by `(group, person)`; and a
 * deletion is a TOMBSTONE that hides the group locally while keeping enough
 * durable base information to restore it if the server refuses.
 *
 * Everything in this module is a projection over the durable operation list.
 * Nothing writes a pending value into the disposable cache -- the cache holds
 * only what the server has acknowledged, and what a user sees is that plus the
 * intent they have not synchronized yet.
 */
(function (root) {
    'use strict';

    const FIELDS = root.PRKS_LOCAL_PERSON_GROUP_FIELDS ||
        Object.freeze(['name', 'description', 'parent_id']);

    const LABELS = Object.freeze({
        name: 'Group name', description: 'Description', parent_id: 'Parent group',
    });

    const MEMBER_OPERATIONS = Object.freeze([
        'ADD_PERSON_GROUP_MEMBER', 'REMOVE_PERSON_GROUP_MEMBER',
    ]);

    function isSupportedField(field) {
        return typeof field === 'string' && FIELDS.indexOf(field) !== -1;
    }

    function unsettled(operations, operation, groupId) {
        return (operations || []).filter(op => op &&
            op.entity_type === 'person-group' &&
            (Array.isArray(operation) ? operation.indexOf(op.operation) !== -1
                : op.operation === operation) &&
            (groupId == null || op.entity_id === groupId) &&
            op.status !== 'acknowledged');
    }

    /* ---- construction ---- */

    function catalogRowFromOp(op) {
        if (!op || op.entity_type !== 'person-group' || typeof op.entity_id !== 'string') {
            return null;
        }
        const payload = op.payload && typeof op.payload === 'object' ? op.payload : {};
        return {
            id: op.entity_id,
            name: String(payload.name == null ? '' : payload.name),
            description: String(payload.description == null ? '' : payload.description),
            parent_id: payload.parent_id ? String(payload.parent_id) : null,
            member_count: 0,
            child_count: 0,
        };
    }

    function pendingCreates(operations) {
        return unsettled(operations, 'CREATE_PERSON_GROUP', null);
    }

    /** Every group id this device is waiting to have deleted. */
    function pendingDeletions(operations) {
        return new Set(unsettled(operations, 'DELETE_PERSON_GROUP', null)
            .map(op => op.entity_id));
    }

    /* ---- fields ---- */

    function pendingFieldValues(operations, groupId) {
        const values = new Map();
        unsettled(operations, 'SET_PERSON_GROUP_FIELD', groupId)
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                const field = op.payload && op.payload.field;
                if (!isSupportedField(field)) return;
                values.set(field, String((op.payload && op.payload.value) || ''));
            });
        return values;
    }

    /** Every group this device has renamed but not yet synchronized. */
    function pendingGroupNames(operations) {
        const names = new Map();
        unsettled(operations, 'SET_PERSON_GROUP_FIELD', null)
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                if (!op.payload || op.payload.field !== 'name') return;
                names.set(op.entity_id, String(op.payload.value || ''));
            });
        return names;
    }

    /** Whether any unsynchronized intent changes what a Person's chips say. */
    function chipsAreOverlaid(operations) {
        return !!(pendingMemberships(operations).size || pendingDeletions(operations).size ||
            pendingGroupNames(operations).size);
    }

    function applyFields(row, values) {
        if (!row || !values.size) return row;
        const out = Object.assign({}, row);
        values.forEach(function (value, field) {
            out[field] = field === 'parent_id' ? (value || null) : value;
        });
        return out;
    }

    /* ---- membership ---- */

    function membershipKey(groupId, personId) {
        return String(groupId) + ' ' + String(personId);
    }

    /**
     * The unsynchronized membership intents, as `"group person" -> present`.
     *
     * One key per pair, because the pair is the conflict unit: two devices
     * adding different people to one group have not collided, and a
     * group-level unit would have made every independent membership one
     * conflict.
     */
    function pendingMemberships(operations) {
        const state = new Map();
        unsettled(operations, MEMBER_OPERATIONS, null)
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                const personId = op.payload && op.payload.person_id;
                if (!personId) return;
                state.set(membershipKey(op.entity_id, personId),
                    op.operation === 'ADD_PERSON_GROUP_MEMBER');
            });
        return state;
    }

    /* ---- effective projections ---- */

    /**
     * The Group catalogue a user should see.
     *
     * A deletion is a TOMBSTONE: the group is hidden and its children are
     * reparented to its own parent exactly as the canonical delete does, so the
     * effective hierarchy stays connected instead of growing an orphan the
     * moment a delete is enqueued. Nothing is destroyed -- the acknowledged
     * rows are untouched, so a refusal restores the group by doing nothing.
     */
    function effectivePersonGroups(rows, operations) {
        if (!Array.isArray(rows)) return rows;
        const byId = new Map(rows.map(row => [row && row.id, row]));
        pendingCreates(operations).forEach(function (op) {
            const row = catalogRowFromOp(op);
            if (row) byId.set(row.id, row);
        });
        const edits = new Map();
        unsettled(operations, 'SET_PERSON_GROUP_FIELD', null).forEach(function (op) {
            if (!edits.has(op.entity_id)) edits.set(op.entity_id, []);
            edits.get(op.entity_id).push(op);
        });
        edits.forEach(function (ops, groupId) {
            const row = byId.get(groupId);
            if (row) byId.set(groupId, applyFields(row, pendingFieldValues(ops, groupId)));
        });
        let out = Array.from(byId.values()).filter(Boolean);
        const deleted = pendingDeletions(operations);
        if (deleted.size) {
            const inherited = new Map();
            out.forEach(function (row) {
                if (deleted.has(row.id)) inherited.set(row.id, row.parent_id || null);
            });
            const resolve = function (id, seen) {
                if (!id || !deleted.has(id)) return id || null;
                if (seen.has(id)) return null;
                seen.add(id);
                return resolve(inherited.get(id) || null, seen);
            };
            out = out.filter(row => !deleted.has(row.id)).map(function (row) {
                const parent = resolve(row.parent_id || null, new Set());
                return parent === (row.parent_id || null)
                    ? row : Object.assign({}, row, { parent_id: parent });
            });
        }
        const memberships = pendingMemberships(operations);
        if (memberships.size) {
            const delta = new Map();
            memberships.forEach(function (present, key) {
                const groupId = key.split(' ')[0];
                delta.set(groupId, (delta.get(groupId) || 0) + (present ? 1 : -1));
            });
            out = out.map(function (row) {
                const change = delta.get(row.id);
                if (!change) return row;
                return Object.assign({}, row, {
                    member_count: Math.max(0, Number(row.member_count || 0) + change),
                });
            });
        }
        return out.sort(function (a, b) {
            const name = String(a.name || '').localeCompare(String(b.name || ''),
                undefined, { sensitivity: 'base' });
            return name || String(a.id).localeCompare(String(b.id));
        });
    }

    /**
     * One Group's detail, with its pending fields and membership applied.
     *
     * `people` supplies the rows for anyone ADDED while unsynchronized: the
     * membership operation names an id, and a member chip renders a profile
     * name, so the row comes from the People projection rather than being
     * invented here.
     */
    function effectivePersonGroupDetail(group, operations, people) {
        if (!group || typeof group !== 'object') return group;
        const out = applyFields(group, pendingFieldValues(operations, group.id));
        const memberships = pendingMemberships(operations);
        /* Somebody this device has asked to delete is gone from the member list
         * too: the Group page is one of the places a Person is read, and a
         * tombstone that held only on the People index would leave them
         * visible on exactly the page that names their memberships. */
        const goneEntirely = typeof root.prksPendingPersonDeletions === 'function'
            ? root.prksPendingPersonDeletions(operations) : new Set();
        if (!memberships.size && !goneEntirely.size) return out;
        const members = Array.isArray(out.members) ? out.members.slice() : [];
        const byId = new Map(members.map(m => [String(m && m.id), m]));
        const lookup = new Map((Array.isArray(people) ? people : [])
            .map(row => [String(row && row.id), row]));
        let changed = false;
        memberships.forEach(function (present, key) {
            const parts = key.split(' ');
            if (parts[0] !== group.id) return;
            const personId = parts[1];
            if (present && !byId.has(personId)) {
                const person = lookup.get(personId);
                if (!person) return;
                byId.set(personId, person);
                changed = true;
            } else if (!present && byId.has(personId)) {
                byId.delete(personId);
                changed = true;
            }
        });
        goneEntirely.forEach(function (personId) {
            if (byId.delete(personId)) changed = true;
        });
        if (!changed) return out;
        const next = Object.assign({}, out, { members: Array.from(byId.values()) });
        next.member_count = next.members.length;
        return next;
    }

    /**
     * A Person's group chips, with this device's unsynchronized memberships.
     *
     * `groups` supplies the NAME for a group joined while unsynchronized --
     * including one created on this device and never sent, which exists in no
     * cache at all.
     */
    function effectivePersonGroupChips(person, operations, groups) {
        if (!person || typeof person !== 'object') return person;
        const memberships = pendingMemberships(operations);
        const deleted = pendingDeletions(operations);
        const names = pendingGroupNames(operations);
        if (!memberships.size && !deleted.size && !names.size) return person;
        const chips = Array.isArray(person.groups) ? person.groups.slice() : [];
        const byId = new Map(chips.map(g => [String(g && g.id), g]));
        const lookup = new Map((Array.isArray(groups) ? groups : [])
            .map(row => [String(row && row.id), row]));
        let changed = false;
        memberships.forEach(function (present, key) {
            const parts = key.split(' ');
            if (parts[1] !== person.id) return;
            const groupId = parts[0];
            if (present && !byId.has(groupId)) {
                const group = lookup.get(groupId);
                byId.set(groupId, { id: groupId, name: group ? String(group.name || '') : '' });
                changed = true;
            } else if (!present && byId.has(groupId)) {
                byId.delete(groupId);
                changed = true;
            }
        });
        deleted.forEach(function (groupId) {
            if (byId.delete(groupId)) changed = true;
        });
        /* A chip carries the group's NAME, so a pending rename has to reach it:
         * the Person's page is one of the places a renamed group is read, and
         * nothing else would ever make the new name visible there. */
        names.forEach(function (name, groupId) {
            const chip = byId.get(groupId);
            if (!chip || String(chip.name || '') === name) return;
            byId.set(groupId, Object.assign({}, chip, { name: name }));
            changed = true;
        });
        if (!changed) return person;
        return Object.assign({}, person, { groups: Array.from(byId.values()) });
    }

    /** The same chip overlay across People index rows. */
    function effectivePersonGroupChipRows(rows, operations, groups) {
        if (!Array.isArray(rows) || !chipsAreOverlaid(operations)) return rows;
        return rows.map(row => effectivePersonGroupChips(row, operations, groups));
    }

    /* ---- bases ---- */

    function isGroupStateShape(value, groupId) {
        if (!value || typeof value !== 'object') return false;
        if (groupId != null && value.group_id !== groupId) return false;
        const fields = value.fields;
        if (!fields || typeof fields !== 'object') return false;
        for (let i = 0; i < FIELDS.length; i += 1) {
            const entry = fields[FIELDS[i]];
            if (!entry || typeof entry !== 'object') return false;
            if (!Number.isSafeInteger(entry.revision) || entry.revision < 0) return false;
        }
        if (!Array.isArray(value.members)) return false;
        return value.members.every(m => m && typeof m.person_id === 'string' &&
            Number.isSafeInteger(m.revision) && m.revision >= 0 &&
            typeof m.present === 'boolean');
    }

    function isPersonGroupStateShape(value, personId) {
        if (!value || typeof value !== 'object') return false;
        if (personId != null && value.person_id !== personId) return false;
        if (!Array.isArray(value.groups)) return false;
        return value.groups.every(g => g && typeof g.group_id === 'string' &&
            Number.isSafeInteger(g.revision) && g.revision >= 0 &&
            typeof g.present === 'boolean');
    }

    async function readPersonGroupState(groupId, options) {
        const result = await root.prksOfflineReadEntity('person-group-state', groupId,
            '/api/person-groups/' + encodeURIComponent(groupId) + '/sync-state',
            Object.assign({}, options || {},
                { validate: v => isGroupStateShape(v, groupId) }));
        if (result.value !== null && !isGroupStateShape(result.value, groupId)) {
            await root.prksOfflineInvalidateEntity('person-group-state', groupId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    async function readPersonGroupsStateForPerson(personId, options) {
        const result = await root.prksOfflineReadEntity('person-group-memberships', personId,
            '/api/persons/' + encodeURIComponent(personId) + '/group-state',
            Object.assign({}, options || {},
                { validate: v => isPersonGroupStateShape(v, personId) }));
        if (result.value !== null && !isPersonGroupStateShape(result.value, personId)) {
            await root.prksOfflineInvalidateEntity('person-group-memberships', personId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    /** Every field at revision 0, for a group the server has never seen. */
    function newGroupState(groupId) {
        const fields = {};
        FIELDS.forEach(function (name) { fields[name] = { revision: 0 }; });
        return { group_id: groupId, fields: fields, members: [] };
    }

    /**
     * The base an edit is measured against: the acknowledged value and the
     * revision it carried.
     *
     * The same three concepts the Person editor keeps apart -- acknowledged,
     * effective, draft. Values come from the acknowledged catalogue row;
     * revisions from the state projection, which carries revisions alone.
     */
    function observedGroupFields(group, state) {
        const fields = state && state.fields && typeof state.fields === 'object'
            ? state.fields : {};
        const base = {};
        FIELDS.forEach(function (name) {
            const entry = fields[name];
            const revision = entry && Number.isSafeInteger(entry.revision) && entry.revision >= 0
                ? entry.revision : 0;
            const raw = group ? group[name] : null;
            base[name] = { value: raw == null ? '' : String(raw), revision: revision };
        });
        return base;
    }

    /**
     * Returns null when the base is not knowable. Unknown is never empty:
     * guessing revision 0 for a group whose revisions this device has never
     * read would silently overwrite whatever another device wrote. A group
     * that exists only as a pending creation takes that creation's payload at
     * revision 0 -- known rather than assumed.
     */
    async function acknowledgedGroupBase(groupId, operations) {
        if (typeof groupId !== 'string' || !groupId) return null;
        const creating = pendingCreates(operations).find(op => op.entity_id === groupId);
        if (creating) {
            return observedGroupFields(catalogRowFromOp(creating), newGroupState(groupId));
        }
        let state = null;
        try {
            const result = await readPersonGroupState(groupId);
            state = result && result.value;
        } catch (_e) { state = null; }
        if (!state) return null;
        let group = null;
        try {
            /* The group's own acknowledged record, not the catalogue: it is the
             * page the user is editing, so it is the one this device is most
             * likely to hold -- and it is one read rather than a whole list. */
            const cached = await root.prksOfflineReadEntity('person-group', groupId,
                '/api/person-groups/' + encodeURIComponent(groupId), {});
            group = cached && cached.value;
        } catch (_e) { group = null; }
        return group ? observedGroupFields(group, state) : null;
    }

    /** What this editing session changed, measured against what it was showing. */
    function dirtyGroupFields(groupId, draft, base, operations) {
        const changes = {};
        if (!draft || !base) return changes;
        const pending = pendingFieldValues(operations, groupId);
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

    /**
     * The acknowledged state of ONE membership pair: `{present, revision}`.
     *
     * A pair with no revision row has never changed since revisions existed,
     * which is revision 0 -- but a pair whose PROJECTION this device has never
     * read is UNKNOWN, and is refused rather than guessed. The exception is a
     * group this device created: the server has never heard of it, so nobody
     * is in it and every pair is at revision 0.
     */
    async function acknowledgedMembership(groupId, personId, operations) {
        if (pendingCreates(operations).some(op => op.entity_id === groupId)) {
            return { present: false, revision: 0 };
        }
        /* The pair has TWO projections, one per end, and either is
         * authoritative for it. Whichever this device happens to hold is used,
         * so a membership can be changed from the Person's page without having
         * visited the Group -- and from the Group's page without having
         * visited the Person. A projection that IS held lists every scope for
         * its end, so a pair missing from it has never had a revision, which
         * is 0. A projection this device has never read says nothing at all,
         * and that is refused rather than guessed. */
        let state = null;
        try {
            const result = await readPersonGroupState(groupId);
            state = result && result.value;
        } catch (_e) { state = null; }
        if (state) {
            const entry = (state.members || []).find(m => m.person_id === personId);
            return entry ? { present: entry.present, revision: entry.revision }
                : { present: false, revision: 0 };
        }
        let mine = null;
        try {
            const result = await readPersonGroupsStateForPerson(personId);
            mine = result && result.value;
        } catch (_e) { mine = null; }
        if (!mine) return null;
        const entry = (mine.groups || []).find(g => g.group_id === groupId);
        return entry ? { present: entry.present, revision: entry.revision }
            : { present: false, revision: 0 };
    }

    /* ---- durable writers ---- */

    function sync() {
        const runtime = root.prksSync;
        if (!runtime || !runtime.store) throw new Error('Group editing is not available.');
        return runtime;
    }

    async function createGroupDurably(fields) {
        const runtime = sync();
        const op = await runtime.store.createPersonGroup(fields);
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function saveGroupFieldsDurably(groupId, changes, base) {
        const runtime = sync();
        const written = await runtime.store.savePersonGroupFields(groupId, changes, base);
        if (typeof runtime.changed === 'function') runtime.changed();
        return written;
    }

    async function setMembershipDurably(groupId, personId, present, observed) {
        const runtime = sync();
        const op = await runtime.store.setPersonGroupMember(groupId, personId, present, observed);
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function deleteGroupDurably(groupId) {
        const runtime = sync();
        const op = await runtime.store.deletePersonGroup(groupId);
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    /* ---- sync handlers ---- */

    /* The refusals a creation can come back with. Every one is TERMINAL and
     * named: a name another group already has, or a parent the server does not
     * know, will be refused the same way forever, so retrying is a loop rather
     * than a recovery -- and a handler that did not recognize them would treat
     * the answer as malformed and retry it exactly that way. */
    const CREATE_REFUSALS = ['NAME_TAKEN', 'PARENT_NOT_FOUND', 'PARENT_CYCLE'];

    const createHandler = {
        isResult: function (data, op) {
            if (!data || data.group_id !== op.entity_id) return false;
            if (CREATE_REFUSALS.indexOf(data.code) !== -1) return true;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean' &&
                !!data.group && data.group.id === data.group_id;
        },
        /* Code only. A durable result is a CLOSED vocabulary the store
         * validates -- a free-text server message could not be stored, and
         * Diagnostics names each code in the user's words anyway. */
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileCreatedPersonGroup(data),
    };

    const fieldHandler = {
        isResult: function (data, op) {
            if (!data || data.group_id !== op.entity_id) return false;
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
                case 'ENTITY_NOT_FOUND': case 'NAME_TAKEN':
                case 'PARENT_NOT_FOUND': case 'PARENT_CYCLE':
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
        reconcile: (data, op) => root.prksOfflineReconcilePersonGroupField(data, op),
    };

    const memberHandler = {
        isResult: function (data, op) {
            if (!data || data.group_id !== op.entity_id) return false;
            if (data.person_id !== (op.payload && op.payload.person_id)) return false;
            switch (data.code) {
                case 'ACKNOWLEDGED':
                    return typeof data.changed === 'boolean' &&
                        typeof data.present === 'boolean' &&
                        Number.isSafeInteger(data.server_revision);
                case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                    return Number.isSafeInteger(data.current_revision) &&
                        typeof data.current_present === 'boolean';
                case 'ENTITY_NOT_FOUND': case 'PERSON_NOT_FOUND':
                    return true;
                default: return false;
            }
        },
        terminal: function (data) {
            const out = { code: data.code };
            if (Number.isSafeInteger(data.current_revision)) {
                out.current_revision = data.current_revision;
            }
            /* `current_state` is the durable vocabulary's name for "is the
             * relationship there", shared with Work-Person roles. */
            if (typeof data.current_present === 'boolean') {
                out.current_state = data.current_present;
            }
            if (typeof data.requested_present === 'boolean') {
                out.requested_state = data.requested_present;
            }
            return { conflict: out };
        },
        reconcile: (data, op) => root.prksOfflineReconcilePersonGroupMember(data, op),
    };

    const deleteHandler = {
        isResult: (data, op) => !!data && data.group_id === op.entity_id &&
            data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean',
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileDeletedPersonGroup(data),
    };

    Object.assign(root, {
        PRKS_PERSON_GROUP_FIELDS: FIELDS,
        PRKS_PERSON_GROUP_FIELD_LABELS: LABELS,
        prksIsSupportedPersonGroupField: isSupportedField,
        prksPersonGroupRowFromOp: catalogRowFromOp,
        prksPendingPersonGroupCreates: pendingCreates,
        prksPendingPersonGroupDeletions: pendingDeletions,
        prksPendingPersonGroupMemberships: pendingMemberships,
        prksPendingPersonGroupNames: pendingGroupNames,
        prksPersonGroupChipsAreOverlaid: chipsAreOverlaid,
        prksPersonGroupMembershipKey: membershipKey,
        prksEffectivePersonGroups: effectivePersonGroups,
        prksEffectivePersonGroupDetail: effectivePersonGroupDetail,
        prksEffectivePersonGroupChips: effectivePersonGroupChips,
        prksEffectivePersonGroupChipRows: effectivePersonGroupChipRows,
        prksIsPersonGroupStateShape: isGroupStateShape,
        prksIsPersonGroupMembershipStateShape: isPersonGroupStateShape,
        prksReadPersonGroupState: readPersonGroupState,
        prksReadPersonGroupsStateForPerson: readPersonGroupsStateForPerson,
        prksNewPersonGroupState: newGroupState,
        prksObservedPersonGroupFields: observedGroupFields,
        prksAcknowledgedPersonGroupBase: acknowledgedGroupBase,
        prksDirtyPersonGroupFields: dirtyGroupFields,
        prksAcknowledgedPersonGroupMembership: acknowledgedMembership,
        prksCreatePersonGroupDurably: createGroupDurably,
        prksSavePersonGroupFieldsDurably: saveGroupFieldsDurably,
        prksSetPersonGroupMemberDurably: setMembershipDurably,
        prksDeletePersonGroupDurably: deleteGroupDurably,
        prksPersonGroupCreateSyncHandler: createHandler,
        prksPersonGroupFieldSyncHandler: fieldHandler,
        prksPersonGroupMemberSyncHandler: memberHandler,
        prksPersonGroupDeleteSyncHandler: deleteHandler,
    });
})(typeof window === 'undefined' ? globalThis : window);
