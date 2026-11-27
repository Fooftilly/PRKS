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
    const reconcileCore = root.prksSyncReconcile ||
        (typeof module !== 'undefined' && module.exports && typeof require === 'function'
            ? require('./sync-reconcile.js') : null);

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
    /* A refused deletion stops hiding its entity: see
     * `prksDurableDeletionAwaitsServer` in local-store.js for why. */
    const deletionAwaitsServer = root.prksDurableDeletionAwaitsServer ||
        function (op) {
            return !!op && op.status !== 'acknowledged' && op.status !== 'conflict';
        };

    function pendingDeletions(operations) {
        return new Set(unsettled(operations, 'DELETE_PERSON_GROUP', null)
            .filter(deletionAwaitsServer).map(op => op.entity_id));
    }

    /* ---- fields ---- */

    /** Whether this device has an unsent move of `groupId`. */
    function hasPendingMove(operations, groupId) {
        return pendingFieldValues(operations, groupId).has('parent_id');
    }

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
     *
     * A pending move changes `parent_id`; the nested `parent` the page and
     * editor show follows it, named from `catalogue` (the effective Group
     * rows) where it holds the new parent. Otherwise the editor would open on
     * the old parent and refuse its own pending move as changed elsewhere.
     */
    function effectivePersonGroupDetail(group, operations, people, catalogue) {
        if (!group || typeof group !== 'object') return group;
        const fields = pendingFieldValues(operations, group.id);
        const out = applyFields(group, fields);
        if (fields.has('parent_id')) {
            /* The parent shown is where the effective catalogue places this
             * group: a pending deletion of the move's destination moves it up
             * to the surviving ancestor there. `parent_id` keeps the queued
             * value, which is what the edit pin compares. */
            const rows = Array.isArray(catalogue) ? catalogue : [];
            const own = rows.find(g => g && String(g.id) === String(group.id));
            const parentId = (own ? own.parent_id : out.parent_id) || null;
            const held = group.parent && group.parent.id ? group.parent : null;
            if (!parentId) Object.assign(out, { parent: null });
            else if (!held || String(held.id) !== String(parentId)) {
                const row = rows.find(g => g && String(g.id) === String(parentId));
                Object.assign(out, { parent: { id: parentId, name: row ? String(row.name || '') : '' } });
            }
        }
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
    /* The edit base of a group PRKS no longer has. */
    const DELETED_GROUP = Object.freeze({ prksDeleted: true });

    async function acknowledgedGroupBase(groupId, operations) {
        if (typeof groupId !== 'string' || !groupId) return null;
        const creating = pendingCreates(operations).find(op => op.entity_id === groupId);
        if (creating) {
            return observedGroupFields(catalogRowFromOp(creating), newGroupState(groupId));
        }
        let state = null;
        let stateSource = null;
        try {
            const result = await readPersonGroupState(groupId);
            state = result && result.value;
            stateSource = result && result.source;
        } catch (_e) { state = null; }
        /* PRKS answered that the group is not there. */
        if (!state) return stateSource === 'server' ? DELETED_GROUP : cachedEditBase(groupId);
        /* Offline, two cached reads are not one snapshot: a revisions read can
         * reach the server while the record read does not, or refresh without
         * it, and pair newer revisions with older values. The base offline is
         * the pair this device last read together. */
        if (stateSource !== 'server') return cachedEditBase(groupId);
        let group = null;
        try {
            const cached = await root.prksOfflineReadEntity('person-group', groupId,
                '/api/person-groups/' + encodeURIComponent(groupId),
                /* A record read joined to an older flight would predate the
                 * revisions while still answering from the server. */
                { requestPolicy: { dedupe: false } });
            /* Values from the cache may be older than the revisions, so they form no base. */
            if (cached && cached.source === 'server' && cached.value === null) return DELETED_GROUP;
            group = cached && cached.source === 'server' ? cached.value : null;
        } catch (_e) { group = null; }
        if (!group) return null;
        /* Revisions unchanged across the record read prove its values are at them. */
        let after = null;
        try {
            after = await readPersonGroupState(groupId, { requestPolicy: { dedupe: false } });
        } catch (_e) { after = null; }
        if (after && after.source === 'server' && after.value === null) return DELETED_GROUP;
        if (!after || after.source !== 'server' || !after.value || !after.value.fields) return null;
        const base = observedGroupFields(group, state);
        /* A field whose revision moved during the record read has no known pair. */
        for (const name of FIELDS) {
            const a = state.fields[name];
            const b = after.value.fields[name];
            if (!a || !b || a.revision !== b.revision) base[name] = null;
        }
        await cacheEditBase(groupId, base);
        return base;
    }

    /* The kind under which an edit base read as one pair is kept for offline Edit. */
    const PERSON_GROUP_EDIT_BASE = 'person-group-edit-base';


    async function cachedEditBase(groupId) {
        let held = null;
        try {
            held = typeof root.prksOfflinePeekEntity === 'function'
                ? await root.prksOfflinePeekEntity(PERSON_GROUP_EDIT_BASE, groupId) : null;
        } catch (_e) { held = null; }
        if (!held || held.group_id !== groupId || !held.fields || typeof held.fields !== 'object') return null;
        const base = {};
        for (const name of FIELDS) {
            const entry = held.fields[name];
            if (entry === null) { base[name] = null; continue; }
            if (!entry || typeof entry.value !== 'string' || !reconcileCore.isRevision(entry.revision)) return null;
            base[name] = { value: entry.value, revision: entry.revision };
        }
        return base;
    }

    /* Merged in one store transaction, so a newer pair another tab
     * published or acknowledged meanwhile is kept. */
    async function cacheEditBase(groupId, base) {
        if (typeof root.prksOfflineMergeEntity !== 'function') return;
        try {
            await root.prksOfflineMergeEntity(PERSON_GROUP_EDIT_BASE, groupId, function (held) {
                const kept = held && held.group_id === groupId && held.fields && typeof held.fields === 'object' ? held.fields : {};
                const fields = {};
                for (const name of FIELDS) {
                    fields[name] = base[name] ? reconcileCore.newerEditBase(kept[name], base[name])
                        : (kept[name] === undefined ? null : kept[name]);
                }
                return { group_id: groupId, fields: fields };
            });
        } catch (_e) { /* the next online Edit writes it again */ }
    }

    /**
     * This device's newest unsent edit per field: the value an editor shows
     * and the revision that edit was measured against.
     */
    function pendingFieldBases(operations, groupId) {
        const out = {};
        unsettled(operations, 'SET_PERSON_GROUP_FIELD', groupId)
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                const field = op.payload && op.payload.field;
                if (!isSupportedField(field)) return;
                out[field] = {
                    value: String((op.payload && op.payload.value) || ''),
                    revision: op.base_revision,
                    pendingOp: op.op_id,
                };
            });
        return out;
    }

    /**
     * The base an edit session saves against, pinned when the editor opens:
     * each field's revision where the read holds what the form shows (see
     * `prksSyncReconcile.pinEditBase`). Null when the revisions cannot be read.
     */
    async function pinGroupEditBase(groupId, shown, operations) {
        const read = await acknowledgedGroupBase(groupId, operations);
        if (read === DELETED_GROUP) return read;
        const pending = pendingFieldBases(operations, groupId);
        const move = pending.parent_id;
        const shownParent = String(shown && shown.parent_id != null ? shown.parent_id : '');
        if (move && move.value && move.value !== shownParent && pendingDeletions(operations).has(move.value)) {
            /* The move's destination is itself waiting to be deleted, so the
             * editor shows the ancestor the deletion moves this group up to
             * (`effectivePersonGroupDetail`). That ancestor IS this device's
             * pending parent, at the move's revision -- but only when the
             * same hierarchy projection places the group there: a form still
             * showing an older parent must not replace a move it never
             * showed, so then the parent base stays unknown (#1454 review). */
            let rows = null;
            try {
                rows = typeof root.prksEffectivePersonGroupCatalogue === 'function'
                    ? await root.prksEffectivePersonGroupCatalogue(operations) : null;
            } catch (_e) { rows = null; }
            const own = Array.isArray(rows) ? rows.find(g => g && String(g.id) === String(groupId)) : null;
            if (own && String(own.parent_id || '') === shownParent) {
                pending.parent_id = Object.assign({}, move, { value: shownParent });
                const pinned = reconcileCore.pinEditBase(shown, read, pending);
                /* The pin may equal the acknowledged parent and drop the op
                 * id, yet this form showed the move: it may replace it. */
                if (pinned && pinned.parent_id) {
                    pinned.parent_id = Object.assign({}, pinned.parent_id, { pendingOp: move.pendingOp });
                }
                return pinned;
            }
        }
        return reconcileCore.pinEditBase(shown, read, pending);
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
    function ownershipDropped(stillOwns) {
        return typeof stillOwns === 'function' && !stillOwns();
    }

    async function acknowledgedMembership(groupId, personId, operations, stillOwns) {
        if (ownershipDropped(stillOwns)) return { prksOwnershipLost: true };
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
        if (ownershipDropped(stillOwns)) return { prksOwnershipLost: true };
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
        if (ownershipDropped(stillOwns)) return { prksOwnershipLost: true };
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

    async function createGroupDurably(fields, stillOwns) {
        if (ownershipDropped(stillOwns)) return null;
        const runtime = sync();
        let op;
        try {
            op = await runtime.store.createPersonGroup(fields, stillOwns);
        } catch (error) {
            /* Rejected means the transaction aborted. A session that ended
             * during the parent create is not an error to show. */
            if (ownershipDropped(stillOwns) ||
                (error && error.prksLocalStoreCode === 'ownership_lost')) return null;
            throw error;
        }
        /* A clean return stored nothing. A resolved envelope committed, so
         * notify even when the editor that started it has moved on. */
        if (!op) return null;
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function saveGroupFieldsDurably(groupId, changes, base, stillOwns, newParent) {
        const runtime = sync();
        let written;
        try {
            written = await runtime.store.savePersonGroupFields(
                groupId, changes, base, stillOwns, newParent);
        } catch (error) {
            if (ownershipDropped(stillOwns)) return [];
            throw error;
        }
        /* Resolved means the field transaction committed. Notify even when
         * the editor that started it has moved on. */
        if (typeof runtime.changed === 'function') runtime.changed();
        return written;
    }

    async function setMembershipDurably(groupId, personId, present, observed, stillOwns) {
        if (ownershipDropped(stillOwns)) return null;
        const runtime = sync();
        let op;
        try {
            op = await runtime.store.setPersonGroupMember(
                groupId, personId, present, observed, stillOwns);
        } catch (error) {
            /* Rejected means the transaction aborted. A session that ended
             * during the write is not a membership error to show. */
            if (ownershipDropped(stillOwns)) return null;
            throw error;
        }
        /* Resolved means the membership transaction committed, including a
         * no-op. Notify even when the editor that started it has moved on. */
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
                    /* The Group name and its revision come as a pair or
                     * not at all: without them the chip is not patched. */
                    return typeof data.changed === 'boolean' &&
                        typeof data.present === 'boolean' &&
                        Number.isSafeInteger(data.server_revision) &&
                        (data.group_name === undefined
                            ? data.group_name_revision === undefined
                            : typeof data.group_name === 'string' &&
                                Number.isSafeInteger(data.group_name_revision) &&
                                data.group_name_revision >= 0);
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

    /* A group delete must be sent after any pending child CREATE and
     * any pending move INTO it. These are ordering barriers, not mandatory
     * successful dependencies: a move can coalesce away and a child CREATE
     * can be refused without invalidating the parent's independent delete.
     * Inspect the live durable queue snapshot at each coordinator drain. */
    function groupDeleteReadyToSend(deletion, byId) {
        for (const other of byId.values()) {
            if (other.status !== 'pending' && other.status !== 'syncing') continue;
            if (other.entity_type !== 'person-group' ||
                other.entity_id === deletion.entity_id || !other.payload) continue;
            const childCreate = other.operation === 'CREATE_PERSON_GROUP' &&
                other.payload.parent_id === deletion.entity_id;
            const childMove = other.operation === 'SET_PERSON_GROUP_FIELD' &&
                other.payload.field === 'parent_id' &&
                other.payload.value === deletion.entity_id;
            if (childCreate || childMove) return false;
        }
        return true;
    }

    const deleteHandler = {
        isResult: (data, op) => !!data && data.group_id === op.entity_id &&
            data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean',
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileDeletedPersonGroup(data),
        readyToSend: groupDeleteReadyToSend,
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
        prksPersonGroupHasPendingMove: hasPendingMove,
        prksEffectivePersonGroupChips: effectivePersonGroupChips,
        prksEffectivePersonGroupChipRows: effectivePersonGroupChipRows,
        prksIsPersonGroupStateShape: isGroupStateShape,
        prksIsPersonGroupMembershipStateShape: isPersonGroupStateShape,
        prksReadPersonGroupState: readPersonGroupState,
        prksReadPersonGroupsStateForPerson: readPersonGroupsStateForPerson,
        prksNewPersonGroupState: newGroupState,
        prksObservedPersonGroupFields: observedGroupFields,
        prksAcknowledgedPersonGroupBase: acknowledgedGroupBase,
        prksPinPersonGroupEditBase: pinGroupEditBase,
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
