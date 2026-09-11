/* Single semantic-operation coordinator. No component owns transport retries. */
(function (root) {
    'use strict';
    const fields = ['op_id', 'device_id', 'operation', 'entity_type', 'entity_id', 'payload',
        'base_revision', 'occurred_at', 'created_at', 'depends_on'];
    const supported = op => ['ADD_WORK_TAG', 'REMOVE_WORK_TAG'].includes(op.operation);
    const integer = v => Number.isSafeInteger(v) && v >= 0;
    function structuredResult(data) {
        const out = { code: data.code };
        for (const key of ['current_revision', 'current_state', 'requested_state', 'target_tag_id']) {
            if (Object.prototype.hasOwnProperty.call(data, key)) out[key] = data[key];
        }
        return out;
    }
    function validResult(data, op) {
        if (!data || data.work_id !== op.entity_id || data.tag_id !== op.payload.tag_id) return false;
        switch (data.code) {
            case 'ACKNOWLEDGED':
                return typeof data.present === 'boolean' && data.present === (op.operation === 'ADD_WORK_TAG') &&
                    integer(data.server_revision) && data.tag && data.tag.id === data.tag_id &&
                    typeof data.tag.name === 'string' && (data.tag.color === null || typeof data.tag.color === 'string');
            case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                return integer(data.current_revision) && typeof data.current_state === 'boolean' &&
                    data.requested_state === (op.operation === 'ADD_WORK_TAG');
            case 'TAG_MERGED': return typeof data.target_tag_id === 'string' && data.target_tag_id.length <= 200;
            case 'TAG_DELETED': case 'ENTITY_NOT_FOUND': return true;
            default: return false;
        }
    }
    function createRuntime(deps) {
        const store = deps.store;
        const listeners = new Set();
        let running = false, timer = null, recovered = false;
        function emit(event) {
            listeners.forEach(fn => { try { fn(event || {}); } catch (_) { /* subscriber isolation */ } });
        }
        function schedule(ms) {
            clearTimeout(timer);
            timer = setTimeout(wake, ms);
        }
        async function drain() {
            if (!recovered) {
                for (const op of await store.listOperations({ status: 'syncing' })) {
                    if (supported(op)) await store.updateOperationSyncState(op.op_id, { status: 'pending' });
                }
                recovered = true;
            }
            while (deps.online()) {
                const pending = (await store.listOperations({ status: 'pending' })).filter(supported);
                if (!pending.length) break;
                const candidate = pending[0];
                const backoff = Math.min(60000, 1000 * 2 ** Math.min(candidate.attempt_count, 6));
                const elapsed = Date.now() - Date.parse(candidate.last_attempt_at || '1970-01-01');
                if (candidate.attempt_count && elapsed < backoff) { schedule(backoff - elapsed + Math.random() * 500); break; }
                const op = await store.claimOperation(candidate.op_id);
                if (!op) continue;
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
                    if (!validResult(data, op)) {
                        if (response.status >= 400 && ['OP_ID_REUSE', 'INVALID_ENVELOPE', 'INVALID_BASE_REVISION', 'UNSUPPORTED_DEPENDENCIES'].includes(data.code)) {
                            await store.updateOperationSyncState(op.op_id, { status: 'conflict', server_result: { code: data.code } });
                            emit(); continue;
                        }
                        throw new Error('invalid_response');
                    }
                    if (response.ok && data.code === 'ACKNOWLEDGED') {
                        if (!await deps.reconcile(data)) throw new Error('cache_write_failed');
                        await store.updateOperationSyncState(op.op_id, { status: 'acknowledged', last_error: null, server_revision: data.server_revision });
                        emit({ acknowledged: data });
                    } else {
                        await store.updateOperationSyncState(op.op_id, { status: 'conflict', server_result: structuredResult(data), last_error: null });
                        emit();
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
            store, changed() { emit(); void wake(); }, stop() { clearTimeout(timer); } };
    }
    root.createPrksSyncRuntime = createRuntime;
    if (!root.document) return;
    const store = root.createPrksLocalStore();
    let connectivityObserved = false;
    const runtime = createRuntime({ store,
        online: () => connectivityObserved && root.prksOfflineRuntimeState() === 'online',
        request: (...args) => root.prksRequest(...args),
        reconcile: result => root.prksOfflineReconcileWorkTag(result),
        lock: root.navigator.locks ? fn => root.navigator.locks.request('prks-work-tag-sync', { ifAvailable: true }, lock => lock ? fn() : undefined) : null,
    });
    root.prksSync = runtime;
    root.prksOfflineRuntimeSubscribe(() => { connectivityObserved = true; runtime.changed(); });
    root.addEventListener('focus', () => runtime.changed());
    // Durable rows, including conflicts, are discoverable after cache clearing.
    root.prksSyncDiagnostics = () => store.stats();
    void runtime.wake();
})(typeof window === 'undefined' ? globalThis : window);
