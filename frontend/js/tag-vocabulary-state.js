/**
 * The Tag VOCABULARY: pending creations and deletions, and what they mean.
 *
 * The Work-Tag *relationship* lives in `work-tag-state.js`. This is the other
 * half -- the Tags themselves -- and it has exactly two shapes, because PRKS
 * has exactly two vocabulary actions: create one, and delete one. There is no
 * rename and no colour editor to move off the network.
 *
 * Everything here is a projection over the durable operation list. Nothing
 * writes a pending Tag into the disposable cache.
 */
(function (root) {
    'use strict';

    function unsettled(operations, operation) {
        return (operations || []).filter(op => op && op.entity_type === 'tag' &&
            op.operation === operation && op.status !== 'acknowledged');
    }

    function pendingCreates(operations) {
        return unsettled(operations, 'CREATE_TAG');
    }

    /** Every Tag id this device is waiting to have deleted. */
    /* A refused deletion stops hiding its entity: see
     * `prksDurableDeletionAwaitsServer` in local-store.js for why. */
    const deletionAwaitsServer = root.prksDurableDeletionAwaitsServer ||
        function (op) {
            return !!op && op.status !== 'acknowledged' && op.status !== 'conflict';
        };

    function pendingDeletions(operations) {
        return new Set(unsettled(operations, 'DELETE_TAG')
            .filter(deletionAwaitsServer).map(op => op.entity_id));
    }

    function catalogRowFromOp(op) {
        if (!op || op.entity_type !== 'tag' || typeof op.entity_id !== 'string') return null;
        const payload = op.payload && typeof op.payload === 'object' ? op.payload : {};
        return {
            id: op.entity_id,
            name: String(payload.name == null ? '' : payload.name),
            color: String(payload.color || '#6d6cf7'),
            aliases: [],
        };
    }

    /**
     * The Tag catalogue a user should see.
     *
     * A deletion is a TOMBSTONE: the Tag is hidden, and nothing acknowledged is
     * destroyed, so a server that refuses the deletion restores it by doing
     * nothing at all. A creation is a real Tag straight away -- its id was
     * minted here, so the picker can attach it before the server has heard.
     */
    function effectiveTagCatalogue(rows, operations) {
        if (!Array.isArray(rows)) return rows;
        const byId = new Map(rows.map(row => [row && row.id, row]));
        pendingCreates(operations).forEach(function (op) {
            const row = catalogRowFromOp(op);
            if (row) byId.set(row.id, row);
        });
        pendingDeletions(operations).forEach(function (tagId) { byId.delete(tagId); });
        return Array.from(byId.values()).filter(Boolean).sort(function (a, b) {
            const name = String(a.name || '').localeCompare(String(b.name || ''),
                undefined, { sensitivity: 'base' });
            return name || String(a.id).localeCompare(String(b.id));
        });
    }

    /**
     * The chips on one entity, with this device's vocabulary intent applied.
     *
     * A Tag deleted here is gone from everything that displayed it -- the
     * relationship rows still exist in the cache, and leaving the chip would
     * show the user a Tag they have already removed from the vocabulary.
     */
    function effectiveTagChips(tags, operations) {
        const deleted = pendingDeletions(operations);
        if (!Array.isArray(tags) || !deleted.size) return tags;
        return tags.filter(tag => !tag || !deleted.has(tag.id));
    }

    async function createTagDurably(fields, known) {
        const sync = root.prksSync;
        if (!sync || !sync.store || typeof sync.store.createTag !== 'function') {
            throw new Error('Tag creation is not available.');
        }
        const op = await sync.store.createTag(fields, known);
        if (typeof sync.changed === 'function') sync.changed();
        return op;
    }

    async function deleteTagDurably(tagId) {
        const sync = root.prksSync;
        if (!sync || !sync.store || typeof sync.store.deleteTag !== 'function') {
            throw new Error('Tag deletion is not available.');
        }
        const op = await sync.store.deleteTag(tagId);
        if (typeof sync.changed === 'function') sync.changed();
        return op;
    }

    const createHandler = {
        isResult: function (data, op) {
            if (!data || data.tag_id !== op.entity_id) return false;
            /* Terminal, and named: only the server sees every Tag name, so a
             * collision is the first this device can know of it -- and the
             * answer will be the same forever. */
            if (data.code === 'NAME_TAKEN') return true;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean' &&
                !!data.tag && data.tag.id === data.tag_id;
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileCreatedTag(data),
    };

    const deleteHandler = {
        isResult: function (data, op) {
            if (!data || data.tag_id !== op.entity_id) return false;
            if (data.code === 'TAG_MERGED') return typeof data.target_tag_id === 'string';
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean' &&
                Array.isArray(data.affected_work_ids);
        },
        terminal: function (data) {
            const out = { code: data.code };
            if (typeof data.target_tag_id === 'string') out.target_tag_id = data.target_tag_id;
            return { conflict: out };
        },
        reconcile: data => root.prksOfflineReconcileDeletedTag(data),
    };

    Object.assign(root, {
        prksPendingTagCreates: pendingCreates,
        prksPendingTagDeletions: pendingDeletions,
        prksTagRowFromOp: catalogRowFromOp,
        prksEffectiveTagCatalogue: effectiveTagCatalogue,
        prksEffectiveTagChips: effectiveTagChips,
        prksCreateTagDurably: createTagDurably,
        prksDeleteTagDurably: deleteTagDurably,
        prksTagCreateSyncHandler: createHandler,
        prksTagDeleteSyncHandler: deleteHandler,
    });
})(typeof window === 'undefined' ? globalThis : window);
