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

    /* A refused deletion stops hiding its entity: see
     * `prksDurableDeletionAwaitsServer` in local-store.js for why. */
    const deletionAwaitsServer = root.prksDurableDeletionAwaitsServer ||
        function (op) {
            return !!op && op.status !== 'acknowledged' && op.status !== 'conflict';
        };

    function pendingDeletions(operations) {
        return new Set(unsettled(operations, 'DELETE_FOLDER', null)
            .filter(deletionAwaitsServer).map(op => op.entity_id));
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

    /* ---- Folder private notes (Reminders): the save contract (#534) ----
     *
     * Folder Reminders are the `private_notes` FIELD of the Folder, saved as
     * one SET_FOLDER_FIELD row against that field's own revision. Nothing
     * here adds a note revision or an endpoint: the field revision IS the
     * note's revision, and it is never a Work note revision.
     *
     * A recovery caller needs to know exactly which queued row holds which
     * text, so the save answers with a code and the row, and never throws:
     *
     * - `queued`: `opId` is the unsettled row whose payload is exactly this
     *   text (new, or the never-sent row that already held it). Queued is
     *   never acknowledged; only that row's acknowledgement is.
     * - `unchanged`: the text equals `observed.value`, so no row is needed
     *   and none is left (a never-sent row holding other text was
     *   withdrawn). It proves equality with the base the CALLER supplied
     *   only: whether that base is acknowledged is the caller's to know.
     * - `scope_busy`: a row for this field is in flight; `opId` names it.
     * - `conflict`: a row for this field needs resolution; `opId` names it.
     * - `unknown_base`: no acknowledged base to measure against.
     * - `unproven`: the store accepted the save but returned no row holding
     *   this exact text, so nothing can be said about what will acknowledge.
     * - `too-long`, `invalid`, `unavailable`, `failed`: nothing was queued
     *   (`failed` carries the store's code and message).
     */

    const PRIVATE_NOTES_FIELD = 'private_notes';
    /* The server's limit for Folder text fields (backend/folder_sync.py). */
    const MAX_FOLDER_TEXT_BYTES = 4000;

    function utf8Bytes(text) {
        return new TextEncoder().encode(text || '').length;
    }

    /*
     * The characters Python's `str.strip()` drops (`str.isspace()`), which is
     * what the server strips. JavaScript's `trim()` is a different set: it
     * also drops U+FEFF and keeps U+001C..U+001F and U+0085, so it would
     * mis-state what the server stores.
     */
    const PY_SPACE = '\t\n\u000b\u000c\r\u001c-\u001f \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000';
    const PY_STRIP = new RegExp('^[' + PY_SPACE + ']+|[' + PY_SPACE + ']+$', 'g');

    /**
     * What the server stores for a field value (backend `canonical_wire`):
     * surrounding whitespace, as Python's `str.strip()` defines it, is
     * dropped, and an empty title is the placeholder. An acknowledged row's
     * text is its payload; the server holds this.
     */
    function canonicalFieldValue(field, value) {
        const text = String(value == null ? '' : value).replace(PY_STRIP, '');
        return field === 'title' ? (text || DEFAULT_TITLE) : text;
    }

    function isPrivateNoteRow(op, folderId) {
        return !!op && op.operation === 'SET_FOLDER_FIELD' && op.entity_type === 'folder' &&
            (folderId == null || op.entity_id === folderId) &&
            !!op.payload && op.payload.field === PRIVATE_NOTES_FIELD;
    }

    /** Unsettled `private_notes` rows of one Folder, oldest first. */
    function privateNoteOperations(operations, folderId) {
        return (operations || [])
            .filter(op => isPrivateNoteRow(op, folderId) && op.status !== 'acknowledged')
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
    }

    function rowText(op) {
        return op && op.payload && typeof op.payload.value === 'string' ? op.payload.value : null;
    }

    /**
     * The acknowledgement of one Folder `private_notes` row, or null for any
     * other event. `text` is the row's exact payload (what a draft generation
     * is matched against); `stored` is what the server now holds at
     * `revision`.
     */
    function privateNoteAck(event) {
        const op = event && event.op;
        const data = event && event.acknowledged;
        if (!data || !isPrivateNoteRow(op, null) || typeof op.op_id !== 'string') return null;
        if (data.code !== 'ACKNOWLEDGED' || data.folder_id !== op.entity_id ||
            data.field !== PRIVATE_NOTES_FIELD) return null;
        const text = rowText(op);
        const revision = data.server_revision;
        if (text === null || !Number.isSafeInteger(revision) || revision < 0) return null;
        return {
            folderId: op.entity_id,
            opId: op.op_id,
            text: text,
            stored: canonicalFieldValue(PRIVATE_NOTES_FIELD, text),
            revision: revision,
            changed: data.changed === true,
        };
    }

    function isObservedField(observed) {
        return !!observed && typeof observed.value === 'string' &&
            Number.isSafeInteger(observed.revision) && observed.revision >= 0;
    }

    /**
     * `options.ownOpId`: the unsettled Reminders row the saving session owns,
     * or null. When given (a session save), the save replaces only that row;
     * another unsettled row is someone else's and makes it `scope_busy`.
     */
    async function saveFolderPrivateNoteDurably(folderId, text, observed, options) {
        const runtime = root.prksSync;
        if (!runtime || !runtime.store || typeof runtime.store.saveFolderFields !== 'function') {
            return { code: 'unavailable', opId: null };
        }
        if (typeof folderId !== 'string' || !folderId || typeof text !== 'string') {
            return { code: 'invalid', opId: null };
        }
        if (utf8Bytes(canonicalFieldValue(PRIVATE_NOTES_FIELD, text)) > MAX_FOLDER_TEXT_BYTES) {
            return { code: 'too-long', opId: null };
        }
        if (!isObservedField(observed)) return { code: 'unknown_base', opId: null };
        const base = { value: observed.value, revision: observed.revision };
        const owns = options && Object.prototype.hasOwnProperty.call(options, 'ownOpId')
            ? { private_notes: typeof options.ownOpId === 'string' ? options.ownOpId : null }
            : undefined;
        let written;
        try {
            written = await runtime.store.saveFolderFields(folderId,
                { private_notes: text }, { private_notes: base }, owns);
        } catch (error) {
            const storeCode = error && error.prksLocalStoreCode;
            if (storeCode === 'scope_busy') {
                const busyOpId = typeof error.prksBusyOpId === 'string' ? error.prksBusyOpId : null;
                return {
                    code: error.prksBusyStatus === 'conflict' ? 'conflict' : 'scope_busy',
                    opId: busyOpId,
                    foreign: error.prksBusyForeign === true,
                };
            }
            return {
                code: 'failed', opId: null, storeCode: storeCode || null,
                error: (error && error.message) || 'Store refused write.',
            };
        }
        if (typeof runtime.changed === 'function') runtime.changed();
        const rows = Array.isArray(written) ? written.filter(op => isPrivateNoteRow(op, folderId)) : [];
        const exact = rows.find(op => rowText(op) === text && typeof op.op_id === 'string');
        if (exact) {
            /* `baseRevision` is the row's own: a never-sent row that already held
             * this text keeps the revision it was queued on. */
            return { code: 'queued', opId: exact.op_id, text: text, baseRevision: exact.base_revision };
        }
        if (!rows.length && text === base.value) {
            return { code: 'unchanged', opId: null, text: text, baseRevision: base.revision };
        }
        return { code: 'unproven', opId: null };
    }

    /* ---- Folder Reminders: what each pane observed (#534) ----
     *
     * The acknowledged `private_notes` base a pane's Reminders are typed on:
     * the body from the Folder detail read (`folderNotesCanonical`, captured
     * before any pending overlay), joined with the field revision from the
     * Folder's sync-state, and where both came from. Kept on the pane's
     * TabContext (`folderNotesObserved`), refreshed by every Folder detail
     * read and by this pane's acknowledgements; never by a pending row.
     */

    /* A base is server-verified only when its body and its revision both are. */
    function noteBaseSource(stateSource, bodySource) {
        if (stateSource === 'server' && bodySource === 'server') return 'server';
        if (stateSource === 'pending-create' && bodySource === 'pending-create') return 'pending-create';
        return stateSource === 'server' || stateSource === 'cache' ? 'cache' : 'unknown';
    }

    function canonicalNotesFrom(folder, source) {
        if (!folder || typeof folder.id !== 'string') return null;
        return {
            id: folder.id,
            private_notes: folder.private_notes == null ? '' : String(folder.private_notes),
            source: typeof source === 'string' ? source : 'unknown',
        };
    }

    /**
     * The Folder detail this pane just read, before pending rows are overlaid.
     * `source` is where it came from (server, cache, pending-create). A newer
     * read always replaces the older one: it is the latest the page knows.
     */
    function rememberFolderNotesCanonical(ctx, folder, source) {
        if (!ctx || typeof ctx.setResource !== 'function') return null;
        const canonical = canonicalNotesFrom(folder, source);
        if (!canonical) return null;
        ctx.setResource('folderNotesCanonical', canonical);
        return canonical;
    }

    function folderOf(ctx) {
        const live = ctx && typeof ctx.getEntity === 'function' ? ctx.getEntity('folder') : null;
        return live && typeof live.id === 'string' ? live : null;
    }

    function observedSlot(ctx) {
        const slot = ctx && typeof ctx.getResource === 'function' ? ctx.getResource('folderNotesObserved') : null;
        return slot && typeof slot.folderId === 'string' ? slot : null;
    }

    /* Publishes a base unless one already observed for this Folder is newer. */
    function publishObserved(ctx, base) {
        if (!ctx || typeof ctx.setResource !== 'function' || ctx.destroyed) return false;
        const live = folderOf(ctx);
        if (!live || live.id !== base.folderId) return false;
        const existing = observedSlot(ctx);
        if (existing && existing.folderId === base.folderId && existing.revision > base.revision) return false;
        ctx.setResource('folderNotesObserved', base);
        return true;
    }

    /* The Folder body as stored or cached now, with when that copy was taken. */
    async function readFolderBody(folderId) {
        try {
            const result = await root.prksOfflineReadEntity('folder', folderId,
                '/api/folders/' + encodeURIComponent(folderId),
                {
                    validate: v => !!(v && typeof v === 'object' && v.id === folderId),
                    /* Fenced like the route's Folder read: a completion older
                     * than a Folder mutation's sweep never repopulates the cache. */
                    domain: 'folders',
                    /* A GET already in flight was sent before the revision read. */
                    requestPolicy: { dedupe: false },
                });
            const value = result && result.value;
            if (!value || value.id !== folderId) return null;
            return { value: value, source: result.source, cachedAt: result.cachedAt };
        } catch (_e) {
            return null;
        }
    }

    /**
     * Joins the body the pane shows with the field revision, as one snapshot.
     * The detail and the revision are separate reads, so the body is read
     * again after the revision: a copy taken at or after the revision is the
     * value at that revision or a later one. A later one only makes the save
     * conflict, never overwrite. The base stands only when that copy is what
     * the pane shows; otherwise the pane shows older text than the revision
     * says, and saving it would replace a newer Reminder it never saw.
     *
     * A Folder created on this device and never sent has revision 0 by
     * construction. Returns the base, or null when it cannot be proven:
     * guessing would overwrite another device's text. `publish: false`
     * returns it without writing the pane (a save can outlive the Folder that
     * started it).
     */
    async function ensureFolderNotesBase(ctx, folder, options) {
        if (!ctx || !folder || typeof folder.id !== 'string') return null;
        const folderId = folder.id;
        const held = ctx.getResource ? ctx.getResource('folderNotesCanonical') : null;
        const canonical = held && held.id === folderId ? held : canonicalNotesFrom(folder, 'unknown');
        let revision = null;
        let stateSource = 'unknown';
        let bodySource = canonical.source;
        if (options && options.pendingCreate) {
            revision = 0;
            stateSource = 'pending-create';
        } else {
            let result = null;
            try { result = await readFolderState(folderId); } catch (_e) { result = null; }
            const entry = result && result.value && result.value.fields &&
                result.value.fields[PRIVATE_NOTES_FIELD];
            if (!entry || !Number.isSafeInteger(entry.revision) || entry.revision < 0) return null;
            if (result.source !== 'server' && result.source !== 'cache') return null;
            const after = await readFolderBody(folderId);
            if (!after || !Number.isFinite(after.cachedAt) || !Number.isFinite(result.cachedAt) ||
                after.cachedAt < result.cachedAt) return null;
            const fresh = canonicalNotesFrom(after.value, after.source);
            const shown = ctx.getResource ? ctx.getResource('folderNotesCanonical') : null;
            const body = shown && shown.id === folderId ? shown : canonical;
            if (fresh.private_notes !== body.private_notes) return null;
            revision = entry.revision;
            stateSource = result.source;
            if (after.source !== 'server') bodySource = 'cache';
        }
        /* Read again after the awaits: an acknowledgement may have advanced it. */
        const now = ctx.getResource ? ctx.getResource('folderNotesCanonical') : null;
        const body = now && now.id === folderId ? now : canonical;
        const base = {
            folderId: folderId,
            value: body.private_notes,
            revision: revision,
            source: noteBaseSource(stateSource, bodySource === 'cache' ? 'cache' : body.source),
        };
        if (!(options && options.publish === false)) publishObserved(ctx, base);
        return base;
    }

    /** This pane's observed Reminders base for the Folder it shows, or null. */
    function folderNoteObserved(ctx, folderId) {
        const slot = observedSlot(ctx);
        if (!slot) return null;
        if (folderId != null && slot.folderId !== String(folderId)) return null;
        const live = folderOf(ctx);
        return live && live.id === slot.folderId ? slot : null;
    }

    /**
     * An acknowledged Reminders row of the Folder this pane shows: what the
     * server now stores becomes the pane's entity, canonical body and observed
     * base. Any pane's or tab's row counts here: this is the note as the
     * server holds it, not a claim about whose text it was.
     */
    function acceptFolderNoteAck(ctx, event) {
        const ack = privateNoteAck(event);
        if (!ack || !ctx || ctx.destroyed) return null;
        const live = folderOf(ctx);
        if (!live || live.id !== ack.folderId) return null;
        const existing = observedSlot(ctx);
        if (existing && existing.folderId === ack.folderId && existing.revision > ack.revision) return null;
        live.private_notes = ack.stored;
        const canonical = ctx.getResource ? ctx.getResource('folderNotesCanonical') : null;
        if (canonical && canonical.id === ack.folderId) {
            canonical.private_notes = ack.stored;
            canonical.source = 'server';
        }
        publishObserved(ctx, { folderId: ack.folderId, value: ack.stored, revision: ack.revision, source: 'server' });
        return ack;
    }

    /* RAM copy of the durable queue for synchronous paints (Work notes do the same). */
    let pendingNoteRows = [];
    /* Whether the latest read of the queue succeeded: until one does, and
     * after one fails, `pendingNoteRows` may be missing rows the queue holds. */
    let pendingNoteRowsRead = false;

    async function refreshPendingFolderNotes() {
        const runtime = root.prksSync;
        if (!runtime || !runtime.store || typeof runtime.store.listOperations !== 'function') {
            pendingNoteRows = [];
            pendingNoteRowsRead = false;
            return pendingNoteRows;
        }
        try {
            pendingNoteRows = privateNoteOperations(await runtime.store.listOperations(), null);
            pendingNoteRowsRead = true;
        } catch (_e) {
            /* An unreadable queue is not an empty one: keep the last rows read,
             * so a paint never hides unsynchronized text it already knew, and
             * say they are unverified. */
            pendingNoteRowsRead = false;
        }
        return pendingNoteRows;
    }

    /** True when the Reminders rows above come from a read that succeeded (an empty one too). */
    function pendingFolderNotesRead() {
        return pendingNoteRowsRead;
    }

    /**
     * The newest unsettled Reminders row of a Folder, as `{text, baseRevision,
     * status}`, or null: the row whose text a pane paints over the body.
     */
    function pendingFolderNoteRow(folderId) {
        const rows = privateNoteOperations(pendingNoteRows, folderId);
        const row = rows.length ? rows[rows.length - 1] : null;
        if (!row || rowText(row) === null) return null;
        return {
            text: rowText(row),
            baseRevision: Number.isSafeInteger(row.base_revision) ? row.base_revision : null,
            status: String(row.status || ''),
            opId: typeof row.op_id === 'string' ? row.op_id : null,
        };
    }

    /** The newest unsettled Reminders text of a Folder, or `fallback`. */
    function pendingFolderNoteText(folderId, fallback) {
        const rows = privateNoteOperations(pendingNoteRows, folderId);
        const text = rows.length ? rowText(rows[rows.length - 1]) : null;
        return text !== null ? text : fallback;
    }

    /** Fail-closed read for recovery decisions: every queued row, or null when the queue could not be read. */
    async function readPendingFolderNotesSnapshot() {
        const runtime = root.prksSync;
        if (!runtime || !runtime.store || typeof runtime.store.listOperations !== 'function') return null;
        try {
            const rows = await runtime.store.listOperations();
            return Array.isArray(rows) ? rows : null;
        } catch (_e) {
            return null;
        }
    }

    /**
     * Whether the server holds `base.value` as the Folder's Reminders at the
     * `private_notes` field revision `base.revision`. The field revision is
     * read on both sides of the body: when both reads equal `base.revision`,
     * no write landed in between, so the body is the field at that revision.
     * Any failed or cached read is unverified. This is the Folder field
     * revision, never a Work note revision.
     */
    async function verifyFolderNoteBase(folderId, base) {
        if (!base || base.source !== 'server' || typeof folderId !== 'string' || !folderId ||
            typeof root.prksOfflineReadEntity !== 'function') {
            return false;
        }
        /* Each read is its own request: one joined to an earlier flight would
         * not be on the stated side of the body. */
        const fresh = { requestPolicy: { dedupe: false } };
        const atBase = async function () {
            const state = await readFolderState(folderId, fresh);
            const entry = state && state.source === 'server' && state.value && state.value.fields
                ? state.value.fields[PRIVATE_NOTES_FIELD] : null;
            return !!(entry && entry.revision === base.revision);
        };
        try {
            if (!await atBase()) return false;
            const folder = await root.prksOfflineReadEntity('folder', folderId,
                '/api/folders/' + encodeURIComponent(folderId), fresh);
            if (!folder || folder.source !== 'server' || !folder.value) return false;
            const raw = folder.value[PRIVATE_NOTES_FIELD];
            if ((raw == null ? '' : String(raw)) !== base.value) return false;
            return await atBase();
        } catch (_e) {
            return false;
        }
    }

    /**
     * One subscription per pane, tied to its route like the Work notes one:
     * acknowledgements patch the pane's Folder whether or not the Reminders
     * card is mounted, and `onAck(ctx, ack)` lets the pane's session see it.
     */
    const notesBoundOwners = new WeakSet();

    function bindFolderNotesSync(ctx, onAck) {
        if (!ctx || ctx.destroyed || typeof ctx.registerCleanup !== 'function') return;
        if (notesBoundOwners.has(ctx)) return;
        if (!root.prksSync || typeof root.prksSync.subscribe !== 'function') return;
        const stop = root.prksSync.subscribe(function (event) {
            const ack = event && event.acknowledged ? acceptFolderNoteAck(ctx, event) : null;
            if (ack && typeof onAck === 'function') {
                try { onAck(ctx, ack); } catch (_e) { /* the patch above already landed */ }
            }
            void refreshPendingFolderNotes();
        });
        notesBoundOwners.add(ctx);
        ctx.registerCleanup(function () {
            notesBoundOwners.delete(ctx);
            if (typeof stop === 'function') stop();
        });
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
        PRKS_FOLDER_PRIVATE_NOTES_FIELD: PRIVATE_NOTES_FIELD,
        PRKS_MAX_FOLDER_TEXT_BYTES: MAX_FOLDER_TEXT_BYTES,
        prksCanonicalFolderFieldValue: canonicalFieldValue,
        prksFolderPrivateNoteOperations: privateNoteOperations,
        prksFolderPrivateNoteAck: privateNoteAck,
        prksSaveFolderPrivateNoteDurably: saveFolderPrivateNoteDurably,
        prksRememberFolderNotesCanonical: rememberFolderNotesCanonical,
        prksEnsureFolderNotesBase: ensureFolderNotesBase,
        prksFolderNoteObserved: folderNoteObserved,
        prksAcceptFolderNoteAck: acceptFolderNoteAck,
        prksRefreshPendingFolderNotes: refreshPendingFolderNotes,
        prksPendingFolderNoteText: pendingFolderNoteText,
        prksReadPendingFolderNotesSnapshot: readPendingFolderNotesSnapshot,
        prksVerifyFolderNoteBase: verifyFolderNoteBase,
        prksPendingFolderNoteRow: pendingFolderNoteRow,
        prksPendingFolderNotesRead: pendingFolderNotesRead,
        /* What the server stores for Reminders text (`str.strip()` parity). */
        prksCanonicalFolderNoteText: text => canonicalFieldValue(PRIVATE_NOTES_FIELD, text),
        prksBindFolderNotesSync: bindFolderNotesSync,
        prksDeleteFolderDurably: deleteFolderDurably,
        prksFolderCreateSyncHandler: createHandler,
        prksFolderFieldSyncHandler: fieldHandler,
        prksWorkFolderSyncHandler: workFolderHandler,
        prksFolderDeleteSyncHandler: deleteHandler,
    });
})(typeof window === 'undefined' ? globalThis : window);
