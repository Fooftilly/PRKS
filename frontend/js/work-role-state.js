/**
 * Work-Person ROLE relationships on the client: the pending overlay, the
 * effective link list, and the family's sync handler.
 *
 * Mirrors `backend/work_role_sync.py`. The conflict unit is one element --
 * `(work, person, role)` -- and its canonical state is `null` (absent) or a
 * credit-name string (present; `''` means no override). Presence alone is not
 * the state: `credit_name` is the name printed on THIS work, and it reaches
 * `linked_authors`, the card credit, BibTeX and the Person's aliases.
 *
 * What this module deliberately does NOT do is decide the displayed credit.
 * It produces effective RELATIONSHIPS and the flattened columns the server
 * emits from them; the existing credit helper keeps deciding what the user
 * sees, so the precedence rule stays in one place.
 */
(function (root) {
    'use strict';

    /* Mirrors `backend/work_role_sync.ROLE_TYPES`. Pinned by
     * tests/test_frontend_work_role_sync.py. */
    const ROLE_TYPES = Object.freeze(['Author', 'Editor', 'Reviewer', 'Mentioned',
        'Translator', 'Introduction', 'Foreword', 'Afterword']);
    const ROLE_TYPE_SET = new Set(ROLE_TYPES);
    const MAX_CREDIT_NAME_BYTES = 500;
    const OPERATIONS = Object.freeze(['ADD_WORK_PERSON_ROLE', 'REMOVE_WORK_PERSON_ROLE',
        'SET_WORK_PERSON_ROLE_CREDIT']);

    function canonicalCredit(value) {
        return String(value == null ? '' : value).trim();
    }

    /** The element key. Structural, so no id has to exclude a delimiter. */
    function scopeKey(workId, personId, roleType) {
        return JSON.stringify([workId, personId, roleType]);
    }

    /** The canonical state an enqueued operation names. */
    function operationState(op) {
        if (op.operation === 'REMOVE_WORK_PERSON_ROLE') return null;
        return canonicalCredit(op.payload && op.payload.credit_name);
    }

    function roleOperations(rows, workId) {
        return (rows || []).filter(op => op && OPERATIONS.indexOf(op.operation) !== -1 &&
            op.entity_type === 'work' && op.entity_id === workId &&
            op.status !== 'acknowledged');
    }

    /* ---- the pending map ---- */

    let pendingByScope = new Map();
    let pendingGeneration = 0;

    function setPending(rows) {
        const next = new Map();
        (rows || []).filter(op => op && OPERATIONS.indexOf(op.operation) !== -1 &&
            op.entity_type === 'work' && op.status !== 'acknowledged').forEach(op => {
                const person = op.payload && op.payload.person_id;
                const role = op.payload && op.payload.role_type;
                if (!person || !ROLE_TYPE_SET.has(role)) return;
                next.set(scopeKey(op.entity_id, person, role), {
                    work_id: op.entity_id, person_id: person, role_type: role,
                    state: operationState(op),
                    /* The Person's own name, captured when the intent was
                     * recorded. A pending ADD has to render a name before any
                     * acknowledgement carries one, and a Person cache may not
                     * be present -- so the intent carries what it needs. */
                    canonical_name: (op.local_context && op.local_context.person &&
                        op.local_context.person.canonical_name) || '',
                    /* The Person row the detail panel renders from. Captured
                     * with the intent because a pending link must draw a name
                     * before any acknowledgement carries one, and a Person
                     * cache may simply not be present. */
                    person: (op.local_context && op.local_context.person) || null,
                    /* A bounded Work summary, so the PERSON page can gain this
                     * Work before acknowledgement. A role intent cannot invent
                     * one, and the Person's cached list is where it belongs. */
                    work: (op.local_context && op.local_context.work) || null,
                });
            });
        pendingByScope = next;
        pendingGeneration += 1;
        return pendingGeneration;
    }

    async function refreshPending() {
        if (!root.prksSync) return [];
        let rows;
        try {
            rows = await root.prksSync.store.listOperations();
        } catch (_e) {
            return [];
        }
        setPending(rows);
        return rows;
    }

    function pendingForWork(workId) {
        const out = [];
        pendingByScope.forEach(entry => {
            if (entry.work_id === workId) out.push(entry);
        });
        return out;
    }

    /* ---- effective relationships ---- */

    /**
     * Acknowledged `linked_people[]` + pending intents = effective links.
     *
     * Order is preserved exactly as the server would produce it: acknowledged
     * links keep their `order_index` order, and a pending ADD appends after
     * them -- the same "append after what is already there" rule
     * `set_role_state()` applies. A pending removal drops the exact element;
     * a pending credit edit replaces `credit_name` and `display_name` while
     * leaving `canonical_name`, so clearing the override can still reveal it.
     */
    function effectiveLinkedPeople(work) {
        if (!work || typeof work.id !== 'string' || !pendingByScope.size) {
            return Array.isArray(work && work.linked_people) ? work.linked_people : [];
        }
        const acknowledged = Array.isArray(work.linked_people) ? work.linked_people : [];
        const intents = pendingForWork(work.id);
        if (!intents.length) return acknowledged;
        const byScope = new Map();
        intents.forEach(entry => {
            byScope.set(scopeKey(entry.work_id, entry.person_id, entry.role_type), entry);
        });
        const out = [];
        const seen = new Set();
        acknowledged.forEach(link => {
            if (!link || typeof link.person_id !== 'string') return;
            const key = scopeKey(work.id, link.person_id, link.role_type);
            seen.add(key);
            const intent = byScope.get(key);
            if (!intent) { out.push(link); return; }
            if (intent.state === null) return;          // pending removal
            const canonical = link.canonical_name || intent.canonical_name || '';
            out.push(Object.assign({}, link, {
                credit_name: intent.state,
                display_name: intent.state || canonical,
            }));
        });
        intents.forEach(entry => {
            const key = scopeKey(entry.work_id, entry.person_id, entry.role_type);
            if (seen.has(key) || entry.state === null) return;
            /* A pending link to someone whose name this device does not know
             * would render as a blank credit, which is worse than rendering
             * the acknowledged state. The intent is still durable and still
             * synchronizes; only its optimistic display is withheld. */
            if (!entry.canonical_name && !entry.state) return;
            out.push({
                person_id: entry.person_id, role_type: entry.role_type,
                order_index: null, canonical_name: entry.canonical_name,
                credit_name: entry.state,
                display_name: entry.state || entry.canonical_name,
            });
        });
        return out;
    }

    /**
     * The flattened credit columns the server emits, recomputed from effective
     * links.
     *
     * ONE helper, because these are derived from the same rows by the same
     * rule -- three routes each patching strings would drift, and the
     * comma-joined form cannot be edited safely in any case.
     */
    function flattenedCredit(links) {
        const authors = links.filter(l => l && l.role_type === 'Author');
        const editors = links.filter(l => l && l.role_type === 'Editor');
        const name = l => String(l.display_name || '').trim();
        return {
            linked_authors: authors.map(name).filter(Boolean).join(', '),
            primary_author: authors.length ? name(authors[0]) : '',
            primary_editor: editors.length ? name(editors[0]) : '',
        };
    }

    /**
     * A Work-shaped row with its relationship columns made effective.
     *
     * The acknowledged row is never mutated. Rows without `linked_people` are
     * returned untouched: a projection that does not carry the structured
     * links cannot have them recomputed, and inventing columns there would
     * make the row fail its own shape validator.
     */
    function effectiveWorkRoles(work) {
        if (!work || typeof work.id !== 'string' || !pendingByScope.size) return work;
        if (!Array.isArray(work.linked_people)) return work;
        if (!pendingForWork(work.id).length) return work;
        const links = effectiveLinkedPeople(work);
        return Object.assign({}, work, { linked_people: links }, flattenedCredit(links));
    }

    function effectiveWorkRolesRows(rows) {
        if (!Array.isArray(rows) || !pendingByScope.size) return rows;
        return rows.map(row => (row && row.id ? effectiveWorkRoles(row) : row));
    }

    /**
     * The Work DETAIL entity's `roles[]`, made effective.
     *
     * A different shape from a browse row's `linked_people[]` and deliberately
     * handled as one: the detail carries whole Person rows keyed by `id`,
     * because the panel renders profile links and alias tooltips from them. A
     * single overlay pretending both were the same list would have to invent
     * the Person fields the detail renders.
     *
     * A pending ADD synthesizes only what the renderer reads -- the person's
     * id, name parts, role and credit -- from the intent's own local context.
     * Where that context is absent (an operation enqueued by an older build)
     * the optimistic row is withheld rather than drawn blank; the intent is
     * still durable and still synchronizes.
     */
    /** Pending Person renames applied to a role list, whoever is in it. */
    function withPendingNames(work, roles) {
        const named = typeof root.prksApplyPendingPersonNames === 'function'
            ? root.prksApplyPendingPersonNames(roles) : roles;
        return named === roles ? work : Object.assign({}, work, { roles: named });
    }

    function effectiveWorkDetailRoles(work) {
        if (!work || typeof work.id !== 'string') return work;
        if (!Array.isArray(work.roles)) return work;
        const intents = pendingByScope.size ? pendingForWork(work.id) : [];
        /* A rename is independent of whether anything was LINKED offline.
         * Returning early on an empty relationship map skipped the name
         * overlay entirely, so a Person renamed offline still appeared under
         * their old name on every Work crediting them -- the one surface the
         * acknowledgement path cannot patch, and therefore the one that has
         * only this overlay. */
        if (!intents.length) return withPendingNames(work, work.roles);
        const byScope = new Map();
        intents.forEach(entry => {
            byScope.set(scopeKey(entry.work_id, entry.person_id, entry.role_type), entry);
        });
        const out = [];
        const seen = new Set();
        work.roles.forEach(role => {
            const personId = String((role && (role.person_id || role.id)) || '').trim();
            if (!personId) { out.push(role); return; }
            const key = scopeKey(work.id, personId, role.role_type);
            seen.add(key);
            const intent = byScope.get(key);
            if (!intent) { out.push(role); return; }
            if (intent.state === null) return;              // pending removal
            out.push(Object.assign({}, role, { credit_name: intent.state }));
        });
        intents.forEach(entry => {
            const key = scopeKey(entry.work_id, entry.person_id, entry.role_type);
            if (seen.has(key) || entry.state === null) return;
            if (!entry.person) return;                      // nothing to render
            out.push(Object.assign({}, entry.person, {
                id: entry.person_id, role_type: entry.role_type,
                order_index: null, credit_name: entry.state,
            }));
        });
        /* The NAMES on those rows may themselves be unsynchronized: a Person
         * renamed offline is still the person this Work is credited to. The
         * relationship overlay decides who is here; the profile overlay
         * decides what they are called, and never the other way round. */
        return withPendingNames(Object.assign({}, work, { roles: out }), out);
    }

    /** Effective links for one Work, for surfaces that render them directly. */
    function effectiveWorkLinks(work) {
        return effectiveLinkedPeople(work);
    }

    /**
     * Does this Work hold this Person in any role, effectively?
     *
     * The Person page asks it in the other direction: a pending link has to
     * make the Work appear under that Person before anything is acknowledged.
     */
    function effectivePersonRoles(work, personId) {
        return effectiveLinkedPeople(work)
            .filter(l => l && l.person_id === personId)
            .map(l => l.role_type);
    }

    /**
     * A Person's cached Work list, made effective.
     *
     * The other direction of the same relationship: a pending link has to make
     * the Work appear under that Person before anything is acknowledged, and a
     * pending unlink has to hide it.
     *
     * Appearing requires a Work SUMMARY, which a role intent cannot invent --
     * so the intent carries one, captured from the Work the user was looking
     * at when they made the link. Where it is absent the Work is simply not
     * added optimistically; the intent is still durable and the Person page
     * gains it on acknowledgement.
     */
    function effectivePersonWorks(person, rows) {
        const works = Array.isArray(rows) ? rows : [];
        if (!person || typeof person.id !== 'string' || !pendingByScope.size) return works;
        const removed = new Set();
        const added = [];
        pendingByScope.forEach(entry => {
            if (entry.person_id !== person.id) return;
            if (entry.state === null) { removed.add(entry.work_id); return; }
            if (entry.work) added.push(entry.work);
        });
        /* A Work is only hidden when NO effective role of this Person remains
         * on it. Unlinking an Author from a file they also translated must not
         * remove the file from their page. */
        const stillLinked = workId => {
            let any = false;
            pendingByScope.forEach(entry => {
                if (entry.person_id === person.id && entry.work_id === workId &&
                    entry.state !== null) any = true;
            });
            return any;
        };
        const out = works.filter(work => !(work && removed.has(work.id) && !stillLinked(work.id)));
        const known = new Set(out.map(work => work && work.id));
        added.forEach(work => {
            if (work && work.id && !known.has(work.id)) { out.push(work); known.add(work.id); }
        });
        return out;
    }

    /**
     * Apply an acknowledgement to a Work's `roles[]`.
     *
     * ONE definition, used by the cache reconciliation and by the live editor's
     * tab entity. Two copies would let the panel and the cached Work disagree
     * about the same acknowledged link -- which is precisely the state a
     * reconciliation exists to prevent.
     */
    function patchDetailRoles(work, ack) {
        if (!work || !Array.isArray(work.roles)) return null;
        const matches = link => String((link && (link.person_id || link.id)) || '') ===
            ack.person_id && link.role_type === ack.role_type;
        const existing = work.roles.find(matches);
        if (!ack.present) {
            if (!existing) return null;
            return Object.assign({}, work, { roles: work.roles.filter(l => !matches(l)) });
        }
        if (!existing) {
            /* A link this copy does not hold yet. The acknowledgement states
             * the Person's own name, so the row is built from what the server
             * said rather than left for a re-read the user may not be online
             * for. */
            if (typeof ack.first_name !== 'string') return null;
            return Object.assign({}, work, { roles: work.roles.concat([{
                id: ack.person_id, person_id: ack.person_id, role_type: ack.role_type,
                order_index: null, credit_name: ack.credit_name,
                first_name: ack.first_name, last_name: ack.last_name,
            }]) });
        }
        return Object.assign({}, work, {
            roles: work.roles.map(l => (matches(l)
                ? Object.assign({}, l, { credit_name: ack.credit_name }) : l)),
        });
    }

    /* ---- the Research Graph ---- */

    /* What the Graph actually represents, from backend/research_graph.py:
     *
     *   - the people layer indexes the `Author` role ONLY, so no other role
     *     can produce or remove an edge;
     *   - it exists only in the snapshot built with people included;
     *   - an edge is emitted only when BOTH the person node and the Work are
     *     in the snapshot.
     *
     * These mirror `_node`, `_edge`, `graph_node_id` and `graph_edge_id`
     * exactly. A shape that merely looked similar would flip visibly on
     * acknowledgement. */
    const GRAPH_AUTHOR_ROLE = 'Author';
    const GRAPH_EDGE_TYPE = 'work_author';
    const graphNodeId = (type, recordId) => type + ':' + recordId;
    const graphEdgeId = (type, source, target) => type + ':' + source + '>' + target;

    /** Mirrors `_person_label`: trim both parts, join with a space, drop empties. */
    function personLabel(person) {
        const parts = [];
        if (person && person.first_name) parts.push(String(person.first_name).trim());
        if (person && person.last_name) parts.push(String(person.last_name).trim());
        return parts.filter(Boolean).join(' ');
    }

    /**
     * Pending Author links, applied to a cached Graph snapshot.
     *
     * A pending link adds its edge; a pending unlink hides it. The Person node
     * is REUSED when the snapshot already holds it, and otherwise constructed
     * from the name parts the intent captured -- which produces the identical
     * node the server would, not an approximation of one. Where neither is
     * available the edge is withheld: a node with a placeholder label would be
     * a visible lie that corrected itself on acknowledgement, and the brief
     * for this milestone is explicit that a valid offline save must never
     * depend on the Graph being able to draw it.
     */
    function effectiveResearchGraph(snapshot) {
        if (!snapshot || !pendingByScope.size) return snapshot;
        if (!snapshot.meta || !snapshot.meta.people_included) return snapshot;
        if (!Array.isArray(snapshot.nodes) || !Array.isArray(snapshot.edges)) return snapshot;
        const works = new Set(snapshot.nodes
            .filter(n => n && n.type === 'work').map(n => n.record_id));
        const people = new Map(snapshot.nodes
            .filter(n => n && n.type === 'person').map(n => [n.record_id, n]));
        const added = [];
        const addedNodes = [];
        const removed = new Set();
        pendingByScope.forEach(entry => {
            if (entry.role_type !== GRAPH_AUTHOR_ROLE) return;
            if (!works.has(entry.work_id)) return;
            const source = graphNodeId('person', entry.person_id);
            const target = graphNodeId('work', entry.work_id);
            const edgeId = graphEdgeId(GRAPH_EDGE_TYPE, source, target);
            if (entry.state === null) { removed.add(edgeId); return; }
            if (!people.has(entry.person_id)) {
                const label = personLabel(entry.person);
                if (!label) return;          // nothing exact to draw
                const node = { id: source, record_id: entry.person_id, type: 'person',
                    label, route: '#/people/' + entry.person_id };
                people.set(entry.person_id, node);
                addedNodes.push(node);
            }
            if (!snapshot.edges.some(e => e && e.id === edgeId)) {
                added.push({ id: edgeId, type: GRAPH_EDGE_TYPE, source, target });
            }
        });
        if (!added.length && !addedNodes.length && !removed.size) return snapshot;
        /* Inserted in the server's own sort order, so an effective snapshot is
         * indistinguishable from an acknowledged one to everything downstream. */
        const nodes = snapshot.nodes.concat(addedNodes).sort((a, b) =>
            a.type.localeCompare(b.type) ||
            String(a.label).toLowerCase().localeCompare(String(b.label).toLowerCase()) ||
            a.record_id.localeCompare(b.record_id) || a.id.localeCompare(b.id));
        const edges = snapshot.edges.filter(e => !(e && removed.has(e.id)))
            .concat(added).sort((a, b) =>
                a.type.localeCompare(b.type) || a.source.localeCompare(b.source) ||
                a.target.localeCompare(b.target) || a.id.localeCompare(b.id));
        return Object.assign({}, snapshot, { nodes, edges, meta: Object.assign({},
            snapshot.meta, { node_count: nodes.length, edge_count: edges.length })});
    }

    /* ---- the sync handler ---- */

    function reportedShape(value) {
        return !!value && typeof value === 'object' &&
            typeof value.present === 'boolean' && typeof value.credit_name === 'string';
    }

    function isResult(data, op) {
        if (!data || data.work_id !== op.entity_id ||
            data.person_id !== op.payload.person_id ||
            data.role_type !== op.payload.role_type) return false;
        switch (data.code) {
            case 'ACKNOWLEDGED':
                return typeof data.changed === 'boolean' &&
                    Number.isSafeInteger(data.server_revision) && data.server_revision >= 0 &&
                    typeof data.present === 'boolean' && typeof data.credit_name === 'string' &&
                    typeof data.first_name === 'string' && typeof data.last_name === 'string' &&
                    (data.aliases_revision === undefined ||
                        (Number.isSafeInteger(data.aliases_revision) &&
                            data.aliases_revision >= 0));
            case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                return Number.isSafeInteger(data.current_revision) &&
                    reportedShape(data.current) && reportedShape(data.requested);
            case 'ROLE_NOT_PRESENT':
                return Number.isSafeInteger(data.current_revision);
            case 'ENTITY_NOT_FOUND': case 'PERSON_NOT_FOUND':
                return true;
            default: return false;
        }
    }

    /* Every terminal outcome is the user's to resolve: they linked this person
     * deliberately, so discarding it silently would lose a real decision. */
    function terminal(data) {
        const out = { code: data.code };
        if (Number.isSafeInteger(data.current_revision)) out.current_revision = data.current_revision;
        if (reportedShape(data.current)) {
            out.current_state = data.current.present;
            out.current_value = data.current.credit_name;
        }
        if (reportedShape(data.requested)) {
            out.requested_state = data.requested.present;
            out.requested_value = data.requested.credit_name;
        }
        return { conflict: out };
    }

    const handler = {
        isResult,
        terminal,
        reconcile: data => root.prksOfflineReconcileWorkRole({
            work_id: data.work_id,
            person_id: data.person_id,
            role_type: data.role_type,
            present: data.present,
            credit_name: data.credit_name,
            first_name: data.first_name,
            last_name: data.last_name,
            server_revision: data.server_revision,
            aliases_revision: data.aliases_revision,
        }),
    };

    Object.assign(root, {
        PRKS_WORK_ROLE_TYPES: ROLE_TYPES,
        PRKS_MAX_CREDIT_NAME_BYTES: MAX_CREDIT_NAME_BYTES,
        PRKS_WORK_ROLE_OPERATION_TYPES: OPERATIONS,
        prksWorkRoleScopeKey: scopeKey,
        prksWorkRoleOperationState: operationState,
        prksWorkRoleOperations: roleOperations,
        prksSetPendingWorkRoles: setPending,
        prksRefreshPendingWorkRoles: refreshPending,
        prksPendingWorkRoleGeneration: () => pendingGeneration,
        prksEffectiveWorkLinks: effectiveWorkLinks,
        prksEffectiveWorkRoles: effectiveWorkRoles,
        prksEffectiveWorkDetailRoles: effectiveWorkDetailRoles,
        prksEffectiveWorkRolesRows: effectiveWorkRolesRows,
        prksEffectivePersonRoles: effectivePersonRoles,
        prksEffectivePersonWorks: effectivePersonWorks,
        prksEffectiveResearchGraphRoles: effectiveResearchGraph,
        PRKS_GRAPH_AUTHOR_EDGE_TYPE: GRAPH_EDGE_TYPE,
        prksFlattenedWorkCredit: flattenedCredit,
        prksPatchWorkDetailRoles: patchDetailRoles,
        prksWorkRoleSyncHandler: handler,
    });
})(typeof window === 'undefined' ? globalThis : window);
