/* Flip edit-session ownership inside a fake IndexedDB request.
 *
 * The production check has to run AFTER the request resolves. Arming the
 * next operations getAll, delete, or put is the gap a guard before the
 * call cannot see. The commit callback runs after the rows are durable
 * and before the store promise resolves.
 */
'use strict';

function operationFingerprint(rows) {
    return rows.map(r => [r.op_id, r.operation, r.entity_id, JSON.stringify(r.payload)].join(':')).join('\n');
}

function openOperationsDatabase(idb) {
    const db = idb.__databases.values().next().value;
    if (!db) throw new Error('the store has not opened its database');
    return db;
}

function wrapNextOperationsRequest(idb, session, method) {
    const db = openOperationsDatabase(idb);
    const original = db.transaction.bind(db);
    let armed = true;
    db.transaction = function (storeNames, mode, options) {
        const tx = original(storeNames, mode, options);
        const names = Array.isArray(storeNames) ? storeNames : [storeNames];
        if (!armed || names.indexOf('operations') === -1) return tx;
        const objectStore = tx.objectStore.bind(tx);
        tx.objectStore = function (name) {
            const handle = objectStore(name);
            if (name !== 'operations' || !armed) return handle;
            const run = handle[method].bind(handle);
            handle[method] = function () {
                armed = false;
                session.owned = false;
                return run.apply(handle, arguments);
            };
            return handle;
        };
        return tx;
    };
}

function loseOwnershipOnNextOperationsRead(idb, session) {
    wrapNextOperationsRequest(idb, session, 'getAll');
}

function loseOwnershipOnNextOperationsDelete(idb, session) {
    wrapNextOperationsRequest(idb, session, 'delete');
}

function loseOwnershipOnNextOperationsPut(idb, session) {
    wrapNextOperationsRequest(idb, session, 'put');
}

function loseOwnershipWhenWriteCommits(idb, session) {
    const db = openOperationsDatabase(idb);
    const original = db.transaction.bind(db);
    let armed = true;
    db.transaction = function (storeNames, mode, options) {
        const tx = original(storeNames, mode, options);
        if (!armed || mode !== 'readwrite') return tx;
        let handler = null;
        Object.defineProperty(tx, 'oncomplete', {
            configurable: true,
            enumerable: true,
            get: function () { return handler; },
            set: function (fn) {
                handler = function (event) {
                    armed = false;
                    session.owned = false;
                    if (typeof fn === 'function') fn.call(tx, event);
                };
            },
        });
        return tx;
    };
}

function runtimeForStore(store) {
    const runtime = globalThis.createPrksSyncRuntime({
        store, online: () => false, handlers: {},
        request: async () => { throw new Error('offline'); },
    });
    let notified = 0;
    const changed = runtime.changed.bind(runtime);
    runtime.changed = function () {
        notified += 1;
        return changed();
    };
    runtime.notifications = () => notified;
    globalThis.prksSync = runtime;
    return runtime;
}

module.exports = {
    operationFingerprint,
    loseOwnershipOnNextOperationsRead,
    loseOwnershipOnNextOperationsDelete,
    loseOwnershipOnNextOperationsPut,
    loseOwnershipWhenWriteCommits,
    runtimeForStore,
};
