/* Single semantic-operation coordinator. No component owns transport retries.
 *
 * This module is deliberately family-agnostic. It owns transport, claiming,
 * backoff, cross-tab locking, replay, status transitions and retirement --
 * everything that is identical no matter what the operation means. What a
 * server answer MEANS belongs to that operation family's handler, so adding a
 * family is a registration rather than another branch in here.
 *
 * A handler provides:
 *   isResult(data, op)  is this a well-formed answer for this operation?
 *   reconcile(data, op) apply an acknowledgement to the disposable cache
 *   terminal(data, op)  {conflict: <bounded structured result>} for an outcome
 *                       the user must resolve, or {discard: <code>} for one
 *                       with no resolution worth offering.
 */
(function (root) {
    'use strict';
    const fields = ['op_id', 'device_id', 'operation', 'entity_type', 'entity_id', 'payload',
        'base_revision', 'occurred_at', 'created_at', 'depends_on'];
    /* Envelope-level refusals: the server never executed the operation, so no
     * family can salvage it by retrying the same bytes. */
    /* Terminal protocol refusals: the server will answer the same way forever,
     * so retrying is a loop rather than a recovery. UNSATISFIED_DEPENDENCY
     * belongs here for the same reason -- the coordinator only sends an
     * operation whose prerequisites it believes are ACKed, so a server that
     * disagrees is describing a state this device cannot argue its way out of. */
    const PROTOCOL_ERRORS = ['OP_ID_REUSE', 'INVALID_ENVELOPE', 'INVALID_BASE_REVISION',
        'UNSUPPORTED_DEPENDENCIES', 'UNSATISFIED_DEPENDENCY'];
    const MAX_DISCARD_NOTES = 10;
    function createRuntime(deps) {
        const store = deps.store;
        const handlers = deps.handlers || {};
        const supported = op => Object.prototype.hasOwnProperty.call(handlers, op.operation);
        const listeners = new Set();
        const discarded = [];
        let running = false, timer = null, recovered = false;
        function emit(event) {
            listeners.forEach(fn => { try { fn(event || {}); } catch (_) { /* subscriber isolation */ } });
        }
        function schedule(ms) {
            clearTimeout(timer);
            timer = setTimeout(wake, ms);
        }
        /* One startup pass over rows an interrupted process left behind.
         * `syncing` resumes under its ORIGINAL op_id -- the server ledger
         * decides whether that envelope already applied, so minting a
         * replacement would be the one way to apply it twice. `acknowledged`
         * means reconciliation had already committed before the crash, so the
         * row is pure residue and is retired. */
        async function recover() {
            for (const op of await store.listOperations()) {
                if (!supported(op)) continue;
                if (op.status === 'syncing') await store.updateOperationSyncState(op.op_id, { status: 'pending' });
                else if (op.status === 'acknowledged') await retire(op.op_id);
            }
            recovered = true;
        }
        /* The server's `sync_operations` ledger is the durable idempotency
         * history; the browser keeps no record of completed operations. A
         * failed retirement is harmless residue the next startup clears. */
        async function retire(opId) {
            const all = await store.listOperations();
            const row = all.find(op => op && op.op_id === opId);
            if (all.some(op => op && op.op_id !== opId &&
                    Array.isArray(op.depends_on) && op.depends_on.indexOf(opId) !== -1)) {
                return;
            }
            try { await store.deleteAcknowledgedOperation(opId); } catch (_) { /* cleared on next startup */ }
            if (row && Array.isArray(row.depends_on)) {
                for (let i = 0; i < row.depends_on.length; i += 1) {
                    await retire(row.depends_on[i]);
                }
            }
        }
        /* A terminal semantic outcome is not a transport failure, and what it
         * is worth to the user is the family's call. A deliberate edit becomes
         * a conflict the user resolves. An activity event has no resolution to
         * offer -- "apply my open event to a Work that no longer exists" is not
         * a choice anyone can make -- so it is consumed and noted instead.
         *
         * A consumed row passes through `acknowledged` on its way out: in this
         * store that status means "the server has spoken and nothing further is
         * owed", which is exactly true here, and it keeps the local store's
         * guard that only such a row may ever be deleted. */
        async function settle(op, disposition) {
            const outcome = disposition || {};
            if (outcome.conflict) {
                await store.updateOperationSyncState(op.op_id, {
                    status: 'conflict', server_result: outcome.conflict, last_error: null });
                emit();
                return;
            }
            /* The consumed row records WHY it was consumed.
             *
             * `acknowledged` here means "the server has spoken and nothing
             * further is owed", which covers both a successful apply and a
             * terminal refusal -- and a dependent operation must be able to
             * tell those apart. Without this marker a FAILED prerequisite
             * looked exactly like a successful one, so a link to a Person
             * whose creation the server refused became eligible to send, and
             * the server then refused that too. */
            await store.updateOperationSyncState(op.op_id, {
                status: 'acknowledged', last_error: null,
                server_result: { code: String(outcome.discard || 'unknown') } });
            /* Anything waiting on it can never succeed now. Surfaced as a
             * conflict rather than left silently stuck: the user's intent is
             * still real, and Diagnostics is where they can discard it. */
            await blockDependents(op.op_id);
            await retire(op.op_id);
            // In-memory and bounded: worth surfacing in Diagnostics, not worth
            // durable storage of its own.
            discarded.unshift({ operation: op.operation, entity_id: op.entity_id,
                code: String(outcome.discard || 'unknown') });
            discarded.length = Math.min(discarded.length, MAX_DISCARD_NOTES);
            emit();
        }
        /* A prerequisite that SUCCEEDED. `acknowledged` alone is not enough:
         * a terminally refused operation is also marked acknowledged on its way
         * out, and treating that as ready would send a dependent the server is
         * certain to reject. The recorded result is what separates them. */
        function dependencySucceeded(dep) {
            return !!dep && dep.status === 'acknowledged' && !dep.server_result;
        }
        function dependenciesReady(op, byId) {
            const deps = Array.isArray(op.depends_on) ? op.depends_on : [];
            for (let i = 0; i < deps.length; i += 1) {
                if (!dependencySucceeded(byId.get(deps[i]))) return false;
            }
            return true;
        }
        /* Everything waiting on a failed prerequisite becomes unresolvable --
         * TRANSITIVELY, and in one atomic step, which is why the store does the
         * walk rather than this module.
         *
         * Marking only the direct dependents was a half-propagation: in a chain
         * A -> B -> C, refusing A turned B into a conflict but left C pending
         * forever, waiting on a prerequisite that had itself become a decision
         * the user still has to make. The store owns the dependency graph, so
         * it is the only place that can settle all of it at once. */
        async function blockDependents(opId) {
            try {
                await store.markDependentsFailed(opId);
            } catch (_) { /* a row that changed underneath is re-read next drain */ }
        }
        async function drain() {
            if (!recovered) await recover();
            while (deps.online()) {
                const all = (await store.listOperations()).filter(supported);
                const pending = all.filter(op => op.status === 'pending');
                if (!pending.length) break;
                const byId = new Map(all.map(op => [op.op_id, op]));
                const candidate = pending.find(op => dependenciesReady(op, byId));
                if (!candidate) break;
                const backoff = Math.min(60000, 1000 * 2 ** Math.min(candidate.attempt_count, 6));
                const elapsed = Date.now() - Date.parse(candidate.last_attempt_at || '1970-01-01');
                if (candidate.attempt_count && elapsed < backoff) { schedule(backoff - elapsed + Math.random() * 500); break; }
                const op = await store.claimOperation(candidate.op_id);
                if (!op) continue;
                const family = handlers[op.operation];
                emit();
                try {
                    const controller = new AbortController();
                    const timeout = setTimeout(() => controller.abort(), 20000);
                    let response;
                    try {
                        response = await deps.request('/api/sync/operations', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(Object.fromEntries(fields.map(k => [k, op[k]]))), signal: controller.signal,
                        });
                    } finally { clearTimeout(timeout); }
                    if (response.status >= 500) throw new Error('server_unavailable');
                    const data = await response.json();
                    if (!family.isResult(data, op)) {
                        if (response.status >= 400 && data && PROTOCOL_ERRORS.includes(data.code)) {
                            await settle(op, family.terminal({ code: data.code }, op));
                            continue;
                        }
                        throw new Error('invalid_response');
                    }
                    if (response.ok && data.code === 'ACKNOWLEDGED') {
                        if (!await family.reconcile(data, op)) throw new Error('cache_write_failed');
                        await store.updateOperationSyncState(op.op_id, { status: 'acknowledged', last_error: null, server_revision: data.server_revision });
                        // The operation travels with its acknowledgement: a
                        // family whose ACK deliberately omits part of the
                        // result reconstructs it from the immutable envelope,
                        // and the coordinator needs to know nothing about that.
                        emit({ acknowledged: data, operation: op.operation, op });
                        // Only now: the cache is reconciled and the live UI has
                        // seen the ACK, so nothing still needs this row.
                        await retire(op.op_id);
                    } else {
                        await settle(op, family.terminal(data, op));
                    }
                } catch (_) {
                    await store.updateOperationSyncState(op.op_id, { status: 'pending', last_error: 'Sync failed; retry scheduled.' });
                    emit();
                    schedule(backoff + Math.random() * 500);
                    break;
                }
            }
        }
        async function wake() {
            if (running) return;
            running = true;
            try {
                // One leader across browser tabs; recovery cannot reset a live
                // sender's syncing row. The IDB claim also arbitrates UI races.
                if (deps.lock) {
                    let acquired = false;
                    await deps.lock(async () => { acquired = true; await drain(); });
                    if (!acquired) schedule(1500 + Math.random() * 500);
                }
                else await drain();
            } catch (_) { schedule(5000); }
            finally { running = false; }
        }
        return { wake, subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
            store, changed() { emit(); void wake(); }, stop() { clearTimeout(timer); },
            discarded: () => discarded.slice() };
    }
    /* Reachability is OBSERVED, never assumed. The offline runtime starts in a
     * provisional `online` state before any probe has answered, and
     * navigator.onLine reports link state rather than PRKS reachability.
     * Sending on either would claim a never-sent operation -- moving its
     * attempt_count off 0 -- and the client would then have to assume the
     * server may already hold it, permanently forfeiting the local right to
     * coalesce or cancel it. So the gate stays shut until the offline runtime
     * publishes its first real connectivity result. */
    function createConnectivityGate(subscribe, getState, onObserved) {
        let observed = false;
        subscribe(() => { observed = true; if (onObserved) onObserved(); });
        return () => observed && getState() === 'online';
    }
    root.createPrksSyncRuntime = createRuntime;
    root.createPrksConnectivityGate = createConnectivityGate;
    if (!root.document) return;
    const store = root.createPrksLocalStore();
    let runtime = null;
    const online = createConnectivityGate(root.prksOfflineRuntimeSubscribe,
        root.prksOfflineRuntimeState, () => { if (runtime) runtime.changed(); });
    runtime = createRuntime({ store, online,
        request: (...args) => root.prksRequest(...args),
        // The registry, in one readable place. Families share a handler
        // when they share meaning, not when they share a code path.
        handlers: {
            // The Tag VOCABULARY, as opposed to the relationship below.
            // Folders. Moving one is a FIELD; which folder a Work is in is a
            // field on the WORK, because a Work is in at most one folder.
            CREATE_FOLDER: root.prksFolderCreateSyncHandler,
            SET_FOLDER_FIELD: root.prksFolderFieldSyncHandler,
            DELETE_FOLDER: root.prksFolderDeleteSyncHandler,
            SET_WORK_FOLDER: root.prksWorkFolderSyncHandler,
            CREATE_POSITION: root.prksPositionCreateSyncHandler,
            SET_POSITION_FIELD: root.prksPositionFieldSyncHandler,
            DELETE_POSITION: root.prksPositionDeleteSyncHandler,
            // Arguments and Stances. Construction carries the initial
            // sources and targets, so the server can never acknowledge a
            // half-connected record. The two lists are AGGREGATES, and
            // targets are one aggregate over both kinds of target.
            CREATE_ARGUMENT: root.prksArgumentCreateSyncHandler,
            SET_ARGUMENT_FIELD: root.prksArgumentFieldSyncHandler,
            SET_ARGUMENT_SOURCES: root.prksArgumentSourcesSyncHandler,
            SET_ARGUMENT_TARGETS: root.prksArgumentTargetsSyncHandler,
            DELETE_ARGUMENT: root.prksArgumentDeleteSyncHandler,
            CREATE_CONCEPT: root.prksConceptCreateSyncHandler,
            SET_CONCEPT_FIELD: root.prksConceptFieldSyncHandler,
            SET_CONCEPT_IDENTITY: root.prksConceptIdentitySyncHandler,
            SET_CONCEPT_PARENTS: root.prksConceptParentsSyncHandler,
            DELETE_CONCEPT: root.prksConceptDeleteSyncHandler,
            CREATE_PLAYLIST: root.prksPlaylistCreateSyncHandler,
            SET_PLAYLIST_FIELD: root.prksPlaylistFieldSyncHandler,
            REORDER_PLAYLIST_ITEMS: root.prksPlaylistOrderSyncHandler,
            DELETE_PLAYLIST: root.prksPlaylistDeleteSyncHandler,
            SET_WORK_PLAYLIST: root.prksWorkPlaylistSyncHandler,
            CREATE_TAG: root.prksTagCreateSyncHandler,
            DELETE_TAG: root.prksTagDeleteSyncHandler,
            ADD_WORK_TAG: root.prksWorkTagSyncHandler,
            REMOVE_WORK_TAG: root.prksWorkTagSyncHandler,
            MARK_WORK_OPENED: root.prksWorkOpenSyncHandler,
            SET_WORK_METADATA_FIELD: root.prksWorkMetadataSyncHandler,
            // Source identity is an AGGREGATE, not a field: one decision, one
            // revision, one conflict, four columns.
            SET_WORK_SOURCE: root.prksWorkSourceSyncHandler,
            // A Work-Person link is an ELEMENT: one person, one role, its own
            // revision. All three name that element's state, so they share a
            // handler and a scope.
            ADD_WORK_PERSON_ROLE: root.prksWorkRoleSyncHandler,
            REMOVE_WORK_PERSON_ROLE: root.prksWorkRoleSyncHandler,
            SET_WORK_PERSON_ROLE_CREDIT: root.prksWorkRoleSyncHandler,
            CREATE_PERSON: root.prksPersonSyncHandler,
            DELETE_PERSON: root.prksPersonDeleteSyncHandler,
            // Editing a Person is FIELD-scoped, like Work metadata and for
            // the same reason: a biography and a birth date are separate
            // decisions, and a profile-wide unit would manufacture conflicts
            // between devices that changed different things.
            SET_PERSON_METADATA_FIELD: root.prksPersonMetadataSyncHandler,
            // Person Groups: four shapes over one entity. Membership is an
            // ELEMENT keyed by (group, person), so add and remove name the
            // same scope and share a handler.
            CREATE_PERSON_GROUP: root.prksPersonGroupCreateSyncHandler,
            SET_PERSON_GROUP_FIELD: root.prksPersonGroupFieldSyncHandler,
            ADD_PERSON_GROUP_MEMBER: root.prksPersonGroupMemberSyncHandler,
            REMOVE_PERSON_GROUP_MEMBER: root.prksPersonGroupMemberSyncHandler,
            DELETE_PERSON_GROUP: root.prksPersonGroupDeleteSyncHandler,
        },
        lock: root.navigator.locks ? fn => root.navigator.locks.request('prks-sync', { ifAvailable: true }, lock => lock ? fn() : undefined) : null,
    });
    root.prksSync = runtime;
    root.addEventListener('focus', () => runtime.changed());
    // Durable rows, including conflicts, are discoverable after cache clearing.
    root.prksSyncDiagnostics = async () => Object.assign(await store.stats(), { discarded: runtime.discarded() });
    void runtime.wake();
})(typeof window === 'undefined' ? globalThis : window);
