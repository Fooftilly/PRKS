/**
 * Folders: pending overlays, bases, and the four operation shapes.
 *
 * Moving a folder is a FIELD, because the hierarchy is a parent pointer on one
 * row. Which folder a Work is in is a field on the WORK, because a Work is in
 * at most one folder rather than a set of them -- so filing, moving and
 * clearing are one operation with different values, and `''` means "in no
 * folder".
 *
 * Everything here is a projection over the durable operation list. Nothing
 * writes a pending value into the disposable cache.
 */
(function (root) {
    'use strict';

    const FIELDS = root.PRKS_LOCAL_FOLDER_FIELDS ||
        Object.freeze(['title', 'description', 'private_notes', 'parent_id']);

    const LABELS = Object.freeze({
        title: 'Folder name', description: 'Description',
        private_notes: 'Private notes', parent_id: 'Parent folder',
    });

    const DEFAULT_TITLE = 'Untitled Folder';

    function isSupportedField(field) {
        return typeof field === 'string' && FIELDS.indexOf(field) !== -1;
    }

    function unsettled(operations, operation, folderId) {
        return (operations || []).filter(op => op &&
            op.entity_type === 'folder' && op.operation === operation &&
            (folderId == null || op.entity_id === folderId) &&
            op.status !== 'acknowledged');
    }

    /* ---- construction ---- */

    function catalogRowFromOp(op) {
        if (!op || op.entity_type !== 'folder' || typeof op.entity_id !== 'string') return null;
        const payload = op.payload && typeof op.payload === 'object' ? op.payload : {};
        return {
            id: op.entity_id,
            title: String(payload.title == null ? '' : payload.title) || DEFAULT_TITLE,
            description: String(payload.description == null ? '' : payload.description),
            private_notes: String(payload.private_notes == null ? '' : payload.private_notes),
            parent_id: payload.parent_id ? String(payload.parent_id) : null,
            work_count: 0,
            child_count: 0,
        };
    }

    function pendingCreates(operations) {
        return unsettled(operations, 'CREATE_FOLDER', null);
    }

    function pendingDeletions(operations) {
        return new Set(unsettled(operations, 'DELETE_FOLDER', null).map(op => op.entity_id));
    }

    /* ---- fields ---- */

    function pendingFieldValues(operations, folderId) {
        const values = new Map();
        unsettled(operations, 'SET_FOLDER_FIELD', folderId)
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                const field = op.payload && op.payload.field;
                if (!isSupportedField(field)) return;
                values.set(field, String((op.payload && op.payload.value) || ''));
            });
        return values;
    }

    function applyFields(row, values) {
        if (!row || !values.size) return row;
        const out = Object.assign({}, row);
        values.forEach(function (value, field) {
            if (field === 'parent_id') out.parent_id = value || null;
            else if (field === 'title') out.title = value || DEFAULT_TITLE;
            else out[field] = value;
        });
        return out;
    }

    /* ---- which folder a Work is in ---- */

    /** `work id -> desired folder id` for every unsynchronized filing. */
    function pendingWorkFolders(operations) {
        const values = new Map();
        (operations || [])
            .filter(op => op && op.operation === 'SET_WORK_FOLDER' &&
                op.entity_type === 'work' && op.status !== 'acknowledged')
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                values.set(op.entity_id, String((op.payload && op.payload.folder_id) || ''));
            });
        return values;
    }

    /* ---- effective projections ---- */

    /**
     * The Folder catalogue a user should see.
     *
     * A deletion is a TOMBSTONE: the folder is hidden and nothing acknowledged
     * is destroyed, so a server that refuses restores it by doing nothing. Its
     * children are NOT reparented, because the canonical delete refuses a
     * folder that has any -- a deletable folder is a leaf.
     */
    function effectiveFolders(rows, operations) {
        if (!Array.isArray(rows)) return rows;
        const byId = new Map(rows.map(row => [row && row.id, row]));
        pendingCreates(operations).forEach(function (op) {
            const row = catalogRowFromOp(op);
            if (row) byId.set(row.id, row);
        });
        const edits = new Map();
        unsettled(operations, 'SET_FOLDER_FIELD', null).forEach(function (op) {
            if (!edits.has(op.entity_id)) edits.set(op.entity_id, []);
            edits.get(op.entity_id).push(op);
        });
        edits.forEach(function (ops, folderId) {
            const row = byId.get(folderId);
            if (row) byId.set(folderId, applyFields(row, pendingFieldValues(ops, folderId)));
        });
        pendingDeletions(operations).forEach(function (folderId) { byId.delete(folderId); });
        const out = Array.from(byId.values()).filter(Boolean);
        /* `work_count` is deliberately NOT adjusted for a pending filing. The
         * count moves between TWO folders, and this projection sees only the
         * catalogue -- it cannot know which folder a Work is leaving. A number
         * this device cannot compute correctly is better left as the last one
         * the server stated than quietly made up. */
        return out.sort(function (a, b) {
            const title = String(a.title || '').localeCompare(String(b.title || ''),
                undefined, { sensitivity: 'base' });
            return title || String(a.id).localeCompare(String(b.id));
        });
    }

    /**
     * One Folder's detail, with its pending fields AND its pending contents.
     *
     * `works` supplies the row for a file moved in while unsynchronized: the
     * operation names an id, the folder page renders a file card, and inventing
     * one here would put a value in front of the user that nothing canonical
     * ever said. A file moved OUT needs no row -- it is simply gone.
     */
    function effectiveFolderDetail(folder, operations, works) {
        if (!folder || typeof folder !== 'object') return folder;
        const out = applyFields(folder, pendingFieldValues(operations, folder.id));
        const filings = pendingWorkFolders(operations);
        if (!filings.size) return out;
        const present = Array.isArray(out.works) ? out.works.slice() : [];
        const byId = new Map(present.map(w => [String(w && w.id), w]));
        const lookup = new Map((Array.isArray(works) ? works : [])
            .map(row => [String(row && row.id), row]));
        let changed = false;
        filings.forEach(function (desired, workId) {
            if (desired === folder.id) {
                if (byId.has(workId)) return;
                const row = lookup.get(workId);
                if (!row) return;
                byId.set(workId, row);
                changed = true;
            } else if (byId.has(workId)) {
                byId.delete(workId);
                changed = true;
            }
        });
        if (!changed) return out;
        const next = Object.assign({}, out, { works: Array.from(byId.values()) });
        next.work_count = next.works.length;
        return next;
    }

    /**
     * Which folder a Work is effectively in, and what it is called.
     *
     * `catalogue` supplies the TITLE, including for a folder created on this
     * device that exists in no cache at all. A Work whose filing is pending
     * shows the destination immediately -- on its own page and on every card
     * that names its folder.
     */
    function effectiveWorkFolder(work, operations, catalogue) {
        if (!work || typeof work !== 'object') return work;
        const filings = pendingWorkFolders(operations);
        const deleted = pendingDeletions(operations);
        if (!filings.has(work.id) && !deleted.size) return work;
        const desired = filings.has(work.id)
            ? filings.get(work.id)
            : (deleted.has(work.folder_id) ? '' : work.folder_id);
        if (desired === work.folder_id) return work;
        const row = (Array.isArray(catalogue) ? catalogue : [])
            .find(entry => entry && entry.id === desired);
        return Object.assign({}, work, {
            folder_id: desired || null,
            folder_title: desired ? String((row && row.title) || '') : '',
        });
    }

    /** The same overlay across catalogue rows that name a folder. */
    function effectiveWorkFolderRows(rows, operations, catalogue) {
        if (!Array.isArray(rows)) return rows;
        const filings = pendingWorkFolders(operations);
        const deleted = pendingDeletions(operations);
        if (!filings.size && !deleted.size) return rows;
        return rows.map(row => effectiveWorkFolder(row, operations, catalogue));
    }

    /* ---- the pending-filing map ---- */

    /* Hydrated from the durable queue and then read SYNCHRONOUSLY, exactly as
     * the Person-name overlay is. A file card renders from synchronous code, so
     * an overlay that had to await the store could only correct itself after
     * the first paint -- which is the flicker hydration exists to prevent. */
    let pendingMoves = new Map();

    function setPendingWorkFolders(operations, catalogue) {
        const next = new Map();
        const rows = Array.isArray(catalogue) ? catalogue : [];
        pendingWorkFolders(operations).forEach(function (folderId, workId) {
            const row = rows.find(entry => entry && entry.id === folderId);
            next.set(workId, {
                folder_id: folderId || null,
                folder_title: folderId ? String((row && row.title) || '') : '',
            });
        });
        pendingMoves = next;
        return pendingMoves.size;
    }

    async function refreshPendingWorkFolders() {
        if (!root.prksSync || !root.prksSync.store) return [];
        let operations;
        try {
            operations = await root.prksSync.store.listOperations();
        } catch (_e) {
            return [];
        }
        let catalogue = [];
        if (pendingWorkFolders(operations).size &&
            typeof root.prksEffectiveFolderCatalogue === 'function') {
            catalogue = await root.prksEffectiveFolderCatalogue(operations) || [];
        }
        setPendingWorkFolders(operations, catalogue);
        return operations;
    }

    /** A pending filing applied to rows that name a Work's folder. */
    function applyPendingWorkFolders(rows) {
        if (!Array.isArray(rows) || !rows.length || !pendingMoves.size) return rows;
        let changed = false;
        const out = rows.map(function (row) {
            const patch = pendingMoves.get(row && row.id);
            if (!patch) return row;
            if (row.folder_id === patch.folder_id &&
                row.folder_title === patch.folder_title) return row;
            changed = true;
            return Object.assign({}, row, patch);
        });
        return changed ? out : rows;
    }

    /* ---- bases ---- */

    function isFolderStateShape(value, folderId) {
        if (!value || typeof value !== 'object') return false;
        if (folderId != null && value.folder_id !== folderId) return false;
        const fields = value.fields;
        if (!fields || typeof fields !== 'object') return false;
        for (let i = 0; i < FIELDS.length; i += 1) {
            const entry = fields[FIELDS[i]];
            if (!entry || typeof entry !== 'object') return false;
            if (!Number.isSafeInteger(entry.revision) || entry.revision < 0) return false;
        }
        return true;
    }

    function isWorkFolderStateShape(value, workId) {
        if (!value || typeof value !== 'object') return false;
        if (workId != null && value.work_id !== workId) return false;
        return typeof value.folder_id === 'string' &&
            Number.isSafeInteger(value.revision) && value.revision >= 0;
    }

    async function readFolderState(folderId, options) {
        const result = await root.prksOfflineReadEntity('folder-state', folderId,
            '/api/folders/' + encodeURIComponent(folderId) + '/sync-state',
            Object.assign({}, options || {},
                { validate: v => isFolderStateShape(v, folderId) }));
        if (result.value !== null && !isFolderStateShape(result.value, folderId)) {
            await root.prksOfflineInvalidateEntity('folder-state', folderId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    async function readWorkFolderState(workId, options) {
        const result = await root.prksOfflineReadEntity('work-folder-state', workId,
            '/api/works/' + encodeURIComponent(workId) + '/folder-state',
            Object.assign({}, options || {},
                { validate: v => isWorkFolderStateShape(v, workId) }));
        if (result.value !== null && !isWorkFolderStateShape(result.value, workId)) {
            await root.prksOfflineInvalidateEntity('work-folder-state', workId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    function newFolderState(folderId) {
        const fields = {};
        FIELDS.forEach(function (name) { fields[name] = { revision: 0 }; });
        return { folder_id: folderId, fields: fields };
    }

    function observedFolderFields(folder, state) {
        const fields = state && state.fields && typeof state.fields === 'object'
            ? state.fields : {};
        const base = {};
        FIELDS.forEach(function (name) {
            const entry = fields[name];
            const revision = entry && Number.isSafeInteger(entry.revision) && entry.revision >= 0
                ? entry.revision : 0;
            const raw = folder ? folder[name] : null;
            base[name] = { value: raw == null ? '' : String(raw), revision: revision };
        });
        return base;
    }

    /**
     * The ACKNOWLEDGED base a folder edit is measured against.
     *
     * The same three concepts the Person editor keeps apart. Returns null when
     * the base is not knowable: guessing revision 0 for a folder whose
     * revisions this device has never read would silently overwrite whatever
     * another device wrote.
     */
    async function acknowledgedFolderBase(folderId, operations) {
        if (typeof folderId !== 'string' || !folderId) return null;
        const creating = pendingCreates(operations).find(op => op.entity_id === folderId);
        if (creating) {
            return observedFolderFields(catalogRowFromOp(creating), newFolderState(folderId));
        }
        let state = null;
        try {
            const result = await readFolderState(folderId);
            state = result && result.value;
        } catch (_e) { state = null; }
        if (!state) return null;
        let folder = null;
        try {
            const cached = await root.prksOfflineReadEntity('folder', folderId,
                '/api/folders/' + encodeURIComponent(folderId), {});
            folder = cached && cached.value;
        } catch (_e) { folder = null; }
        return folder ? observedFolderFields(folder, state) : null;
    }

    /** The acknowledged folder a Work is in: `{folder_id, revision}` or null. */
    async function acknowledgedWorkFolder(workId) {
        try {
            const result = await readWorkFolderState(workId);
            const value = result && result.value;
            if (!value) return null;
            return { folder_id: value.folder_id, revision: value.revision };
        } catch (_e) { return null; }
    }

    /** What this editing session changed, measured against what it was showing. */
    function dirtyFolderFields(folderId, draft, base, operations) {
        const changes = {};
        if (!draft || !base) return changes;
        const pending = pendingFieldValues(operations, folderId);
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

    /* ---- durable writers ---- */

    function sync() {
        const runtime = root.prksSync;
        if (!runtime || !runtime.store) throw new Error('Folder editing is not available.');
        return runtime;
    }

    async function createFolderDurably(fields) {
        const runtime = sync();
        const op = await runtime.store.createFolder(fields);
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function saveFolderFieldsDurably(folderId, changes, base) {
        const runtime = sync();
        const written = await runtime.store.saveFolderFields(folderId, changes, base);
        if (typeof runtime.changed === 'function') runtime.changed();
        return written;
    }

    async function setWorkFolderDurably(workId, folderId, observed, localContext) {
        const runtime = sync();
        const op = await runtime.store.setWorkFolder(workId, folderId, observed, localContext);
        /* Refresh the synchronous map HERE, not only at route hydration. A file
         * moved on the folder page has to name its new folder the moment the
         * user reaches it, and the surfaces that render a folder read the map
         * synchronously. */
        await refreshPendingWorkFolders();
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function deleteFolderDurably(folderId) {
        const runtime = sync();
        const op = await runtime.store.deleteFolder(folderId);
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    /* ---- sync handlers ---- */

    const CREATE_REFUSALS = ['TITLE_TAKEN', 'PARENT_NOT_FOUND', 'PARENT_CYCLE'];

    const createHandler = {
        isResult: function (data, op) {
            if (!data || data.folder_id !== op.entity_id) return false;
            if (CREATE_REFUSALS.indexOf(data.code) !== -1) return true;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean' &&
                !!data.folder && data.folder.id === data.folder_id;
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileCreatedFolder(data),
    };

    const fieldHandler = {
        isResult: function (data, op) {
            if (!data || data.folder_id !== op.entity_id) return false;
            if (data.field !== (op.payload && op.payload.field)) return false;
            switch (data.code) {
                case 'ACKNOWLEDGED':
                    return typeof data.changed === 'boolean' &&
                        Number.isSafeInteger(data.server_revision) &&
                        data.server_revision >= 0 && data.value_omitted === true &&
                        (data.member_work_ids === undefined ||
                            Array.isArray(data.member_work_ids));
                case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                    return Number.isSafeInteger(data.current_revision) &&
                        (typeof data.current_value === 'string' ||
                            typeof data.current_preview === 'string');
                case 'ENTITY_NOT_FOUND': case 'TITLE_TAKEN':
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
        reconcile: (data, op) => root.prksOfflineReconcileFolderField(data, op),
    };

    const workFolderHandler = {
        isResult: function (data, op) {
            if (!data || data.work_id !== op.entity_id) return false;
            switch (data.code) {
                case 'ACKNOWLEDGED':
                    return typeof data.changed === 'boolean' &&
                        typeof data.folder_id === 'string' &&
                        typeof data.folder_title === 'string' &&
                        Number.isSafeInteger(data.server_revision);
                case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                    return Number.isSafeInteger(data.current_revision) &&
                        typeof data.current_value === 'string';
                case 'ENTITY_NOT_FOUND': case 'FOLDER_NOT_FOUND':
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
            if (typeof data.requested_value === 'string') {
                out.requested_value = data.requested_value;
            }
            return { conflict: out };
        },
        reconcile: (data, op) => root.prksOfflineReconcileWorkFolder(data, op),
    };

    const deleteHandler = {
        isResult: function (data, op) {
            if (!data || data.folder_id !== op.entity_id) return false;
            if (data.code === 'FOLDER_NOT_EMPTY' || data.code === 'FOLDER_HAS_SUBFOLDERS') {
                return true;
            }
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean';
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileDeletedFolder(data),
    };

    Object.assign(root, {
        PRKS_FOLDER_FIELDS: FIELDS,
        PRKS_FOLDER_FIELD_LABELS: LABELS,
        prksIsSupportedFolderField: isSupportedField,
        prksFolderRowFromOp: catalogRowFromOp,
        prksPendingFolderCreates: pendingCreates,
        prksPendingFolderDeletions: pendingDeletions,
        prksPendingWorkFolders: pendingWorkFolders,
        prksEffectiveFolders: effectiveFolders,
        prksEffectiveFolderDetail: effectiveFolderDetail,
        prksEffectiveWorkFolder: effectiveWorkFolder,
        prksEffectiveWorkFolderRows: effectiveWorkFolderRows,
        prksSetPendingWorkFolders: setPendingWorkFolders,
        prksRefreshPendingWorkFolders: refreshPendingWorkFolders,
        prksApplyPendingWorkFolders: applyPendingWorkFolders,
        prksIsFolderStateShape: isFolderStateShape,
        prksIsWorkFolderStateShape: isWorkFolderStateShape,
        prksReadFolderState: readFolderState,
        prksReadWorkFolderState: readWorkFolderState,
        prksNewFolderState: newFolderState,
        prksObservedFolderFields: observedFolderFields,
        prksAcknowledgedFolderBase: acknowledgedFolderBase,
        prksAcknowledgedWorkFolder: acknowledgedWorkFolder,
        prksDirtyFolderFields: dirtyFolderFields,
        prksCreateFolderDurably: createFolderDurably,
        prksSaveFolderFieldsDurably: saveFolderFieldsDurably,
        prksSetWorkFolderDurably: setWorkFolderDurably,
        prksDeleteFolderDurably: deleteFolderDurably,
        prksFolderCreateSyncHandler: createHandler,
        prksFolderFieldSyncHandler: fieldHandler,
        prksWorkFolderSyncHandler: workFolderHandler,
        prksFolderDeleteSyncHandler: deleteHandler,
    });
})(typeof window === 'undefined' ? globalThis : window);
