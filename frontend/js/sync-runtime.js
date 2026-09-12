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
    const PROTOCOL_ERRORS = ['OP_ID_REUSE', 'INVALID_ENVELOPE', 'INVALID_BASE_REVISION', 'UNSUPPORTED_DEPENDENCIES'];
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
            try { await store.deleteAcknowledgedOperation(opId); } catch (_) { /* cleared on next startup */ }
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
            await store.updateOperationSyncState(op.op_id, { status: 'acknowledged', last_error: null });
            await retire(op.op_id);
            // In-memory and bounded: worth surfacing in Diagnostics, not worth
            // durable storage of its own.
            discarded.unshift({ operation: op.operation, entity_id: op.entity_id,
                code: String(outcome.discard || 'unknown') });
            discarded.length = Math.min(discarded.length, MAX_DISCARD_NOTES);
            emit();
        }
        async function drain() {
            if (!recovered) await recover();
            while (deps.online()) {
                const pending = (await store.listOperations({ status: 'pending' })).filter(supported);
                if (!pending.length) break;
                const candidate = pending[0];
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
        // The registry, in one readable place. Two families share a handler
        // when they share meaning, not when they share a code path.
        handlers: {
            ADD_WORK_TAG: root.prksWorkTagSyncHandler,
            REMOVE_WORK_TAG: root.prksWorkTagSyncHandler,
            MARK_WORK_OPENED: root.prksWorkOpenSyncHandler,
            SET_WORK_METADATA_FIELD: root.prksWorkMetadataSyncHandler,
        },
        lock: root.navigator.locks ? fn => root.navigator.locks.request('prks-sync', { ifAvailable: true }, lock => lock ? fn() : undefined) : null,
    });
    root.prksSync = runtime;
    root.addEventListener('focus', () => runtime.changed());
    // Durable rows, including conflicts, are discoverable after cache clearing.
    root.prksSyncDiagnostics = async () => Object.assign(await store.stats(), { discarded: runtime.discarded() });
    void runtime.wake();
})(typeof window === 'undefined' ? globalThis : window);
