/**
 * Work lifecycle: pending deletions and the DELETE_WORK handler.
 *
 * Work field/relationship families live in their own modules. This is only the
 * identity-destroying half -- a tombstone overlay and the sync handler that
 * reconciles after acknowledgement.
 */
(function (root) {
    'use strict';

    const deletionAwaitsServer = root.prksDurableDeletionAwaitsServer ||
        function (op) {
            return !!op && op.status !== 'acknowledged' && op.status !== 'conflict';
        };

    function pendingDeletions(operations) {
        return new Set((operations || []).filter(function (op) {
            return op && op.operation === 'DELETE_WORK' && deletionAwaitsServer(op);
        }).map(function (op) { return op.entity_id; }));
    }

    /** Drop Works this device is waiting to delete from a list of rows. */
    function withoutPendingDeletedWorks(rows, operations) {
        if (!Array.isArray(rows)) return rows;
        const gone = pendingDeletions(operations);
        if (!gone.size) return rows;
        return rows.filter(function (row) { return row && !gone.has(row.id); });
    }

    async function deleteWorkDurably(workId) {
        const sync = root.prksSync;
        if (!sync || !sync.store || typeof sync.store.deleteWork !== 'function') {
            throw new Error('File deletion is not available.');
        }
        const op = await sync.store.deleteWork(workId);
        if (typeof sync.changed === 'function') sync.changed();
        return op;
    }

    const deleteHandler = {
        isResult: function (data, op) {
            if (!data || data.work_id !== op.entity_id) return false;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean';
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileDeletedWork(data),
    };

    Object.assign(root, {
        prksPendingWorkDeletions: pendingDeletions,
        prksWithoutPendingDeletedWorks: withoutPendingDeletedWorks,
        prksDeleteWorkDurably: deleteWorkDurably,
        prksWorkDeleteSyncHandler: deleteHandler,
    });
})(typeof window === 'undefined' ? globalThis : window);
