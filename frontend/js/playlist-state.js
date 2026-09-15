/**
 * Playlists: pending overlays, bases, and the five operation shapes.
 *
 * The three editable columns are FIELDS. Which playlist a video is in is a
 * field on the WORK, because a video is in at most one playlist -- so adding,
 * moving and removing are one operation with different values, and `''` means
 * "in no playlist". The ORDER is an aggregate: one revision covers the whole
 * structure, because two devices that each dragged one video produced two
 * whole orders and merging them index by index would invent a third that
 * neither of them chose.
 *
 * Everything here is a projection over the durable operation list. Nothing
 * writes a pending value into the disposable cache.
 */
(function (root) {
    'use strict';

    const FIELDS = root.PRKS_LOCAL_PLAYLIST_FIELDS ||
        Object.freeze(['title', 'description', 'original_url']);

    const LABELS = Object.freeze({
        title: 'Playlist name', description: 'Description',
        original_url: 'Original URL',
    });

    const DEFAULT_TITLE = 'Untitled playlist';

    function isSupportedField(field) {
        return typeof field === 'string' && FIELDS.indexOf(field) !== -1;
    }

    function unsettled(operations, operation, playlistId) {
        return (operations || []).filter(op => op &&
            op.entity_type === 'playlist' && op.operation === operation &&
            (playlistId == null || op.entity_id === playlistId) &&
            op.status !== 'acknowledged');
    }

    /* ---- construction ---- */

    function catalogRowFromOp(op) {
        if (!op || op.entity_type !== 'playlist' || typeof op.entity_id !== 'string') return null;
        const payload = op.payload && typeof op.payload === 'object' ? op.payload : {};
        return {
            id: op.entity_id,
            title: String(payload.title == null ? '' : payload.title) || DEFAULT_TITLE,
            description: String(payload.description == null ? '' : payload.description),
            original_url: payload.original_url ? String(payload.original_url) : null,
            item_count: 0,
            items: [],
        };
    }

    function pendingCreates(operations) {
        return unsettled(operations, 'CREATE_PLAYLIST', null);
    }

    function pendingDeletions(operations) {
        return new Set(unsettled(operations, 'DELETE_PLAYLIST', null).map(op => op.entity_id));
    }

    /* ---- fields ---- */

    function pendingFieldValues(operations, playlistId) {
        const values = new Map();
        unsettled(operations, 'SET_PLAYLIST_FIELD', playlistId)
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
            if (field === 'title') out.title = value || DEFAULT_TITLE;
            else if (field === 'original_url') out.original_url = value || null;
            else out[field] = value;
        });
        return out;
    }

    /* ---- which playlist a Work is in ---- */

    /** `work id -> desired playlist id` for every unsynchronized membership. */
    function pendingWorkPlaylists(operations) {
        const values = new Map();
        (operations || [])
            .filter(op => op && op.operation === 'SET_WORK_PLAYLIST' &&
                op.entity_type === 'work' && op.status !== 'acknowledged')
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                values.set(op.entity_id, String((op.payload && op.payload.playlist_id) || ''));
            });
        return values;
    }

    /* ---- the order ---- */

    /** `playlist id -> the whole ordered list` for every unsynchronized drag. */
    function pendingOrders(operations) {
        const values = new Map();
        unsettled(operations, 'REORDER_PLAYLIST_ITEMS', null)
            .slice()
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0))
            .forEach(function (op) {
                const ids = op.payload && op.payload.work_ids;
                values.set(op.entity_id, Array.isArray(ids) ? ids.slice() : []);
            });
        return values;
    }

    /**
     * Put a playlist's items in the order this device intends.
     *
     * The same rule the server applies, because an order is not a membership
     * change: an id the playlist does not hold is ignored, and one it holds
     * that the order omitted keeps its relative place at the end. Without that
     * a video added after the drag would vanish from the page until the sync
     * landed.
     */
    function applyOrder(items, workIds) {
        const present = Array.isArray(items) ? items.slice() : [];
        if (!Array.isArray(workIds) || !workIds.length || !present.length) return present;
        const byId = new Map(present.map(item => [String(item && item.id), item]));
        const out = [];
        const seen = new Set();
        workIds.forEach(function (workId) {
            const item = byId.get(String(workId));
            if (!item || seen.has(workId)) return;
            seen.add(workId);
            out.push(item);
        });
        present.forEach(function (item) {
            if (!seen.has(String(item && item.id))) out.push(item);
        });
        return out;
    }

    /* ---- effective projections ---- */

    /**
     * The Playlist catalogue a user should see.
     *
     * A deletion is a TOMBSTONE: the playlist is hidden and nothing
     * acknowledged is destroyed, so a server that refuses restores it by doing
     * nothing.
     */
    function effectivePlaylists(rows, operations) {
        if (!Array.isArray(rows)) return rows;
        const byId = new Map(rows.map(row => [row && row.id, row]));
        const created = [];
        pendingCreates(operations).forEach(function (op) {
            const row = catalogRowFromOp(op);
            if (!row) return;
            if (!byId.has(row.id)) created.push(row.id);
            byId.set(row.id, row);
        });
        const edits = new Map();
        unsettled(operations, 'SET_PLAYLIST_FIELD', null).forEach(function (op) {
            if (!edits.has(op.entity_id)) edits.set(op.entity_id, []);
            edits.get(op.entity_id).push(op);
        });
        edits.forEach(function (ops, playlistId) {
            const row = byId.get(playlistId);
            if (row) byId.set(playlistId, applyFields(row, pendingFieldValues(ops, playlistId)));
        });
        pendingDeletions(operations).forEach(function (playlistId) { byId.delete(playlistId); });
        /* `item_count` is deliberately NOT adjusted for a pending membership.
         * The count moves between TWO playlists, and this projection sees only
         * the catalogue -- it cannot know which playlist a video is leaving. A
         * number this device cannot compute correctly is better left as the
         * last one the server stated than quietly made up. A playlist created
         * here is the exception: its count is genuinely zero. */
        const order = new Map(rows.map((row, index) => [row && row.id, index]));
        return Array.from(byId.values()).filter(Boolean).sort(function (a, b) {
            /* Newest first, as `get_all_playlists` orders by updated_at -- but
             * this device cannot know the server's timestamps, so an untouched
             * catalogue keeps exactly the order it arrived in and anything
             * created here goes to the front. */
            const left = order.has(a.id) ? order.get(a.id) : -1 - created.indexOf(a.id);
            const right = order.has(b.id) ? order.get(b.id) : -1 - created.indexOf(b.id);
            return left - right;
        });
    }

    /**
     * One Playlist's detail, with its pending fields, contents AND order.
     *
     * `works` supplies the row for a video added while unsynchronized: the
     * operation names an id, the playlist page renders a video card, and
     * inventing one here would put a value in front of the user that nothing
     * canonical ever said. A video removed needs no row -- it is simply gone.
     */
    function effectivePlaylistDetail(playlist, operations, works) {
        if (!playlist || typeof playlist !== 'object') return playlist;
        let out = applyFields(playlist, pendingFieldValues(operations, playlist.id));
        const memberships = pendingWorkPlaylists(operations);
        const orders = pendingOrders(operations);
        let items = Array.isArray(out.items) ? out.items.slice() : [];
        let changed = false;
        if (memberships.size) {
            const byId = new Map(items.map(item => [String(item && item.id), item]));
            const lookup = new Map((Array.isArray(works) ? works : [])
                .map(row => [String(row && row.id), row]));
            memberships.forEach(function (desired, workId) {
                if (desired === playlist.id) {
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
            if (changed) items = Array.from(byId.values());
        }
        if (orders.has(playlist.id)) {
            const ordered = applyOrder(items, orders.get(playlist.id));
            if (ordered.some((item, index) => item !== items[index])) {
                items = ordered;
                changed = true;
            }
        }
        if (!changed) return out;
        out = Object.assign({}, out, { items: items, item_count: items.length });
        return out;
    }

    /**
     * Which playlist a Work is effectively in, and what it is called.
     *
     * `catalogue` supplies the TITLE, including for a playlist created on this
     * device that exists in no cache at all.
     */
    function effectiveWorkPlaylist(work, operations, catalogue) {
        if (!work || typeof work !== 'object') return work;
        const memberships = pendingWorkPlaylists(operations);
        const deleted = pendingDeletions(operations);
        if (!memberships.has(work.id) && !deleted.size) return work;
        const current = work.playlist_id || '';
        const desired = memberships.has(work.id)
            ? memberships.get(work.id)
            : (deleted.has(current) ? '' : current);
        if (desired === current) return work;
        const row = (Array.isArray(catalogue) ? catalogue : [])
            .find(entry => entry && entry.id === desired);
        return Object.assign({}, work, {
            playlist_id: desired || null,
            playlist_title: desired ? String((row && row.title) || '') : null,
        });
    }

    /* ---- the pending-membership map ---- */

    /* Hydrated from the durable queue and then read SYNCHRONOUSLY, exactly as
     * the folder overlay is. A video card renders from synchronous code, so an
     * overlay that had to await the store could only correct itself after the
     * first paint -- which is the flicker hydration exists to prevent. */
    let pendingMemberships = new Map();

    function setPendingWorkPlaylists(operations, catalogue) {
        const next = new Map();
        const rows = Array.isArray(catalogue) ? catalogue : [];
        pendingWorkPlaylists(operations).forEach(function (playlistId, workId) {
            const row = rows.find(entry => entry && entry.id === playlistId);
            next.set(workId, {
                playlist_id: playlistId || null,
                playlist_title: playlistId ? String((row && row.title) || '') : null,
            });
        });
        pendingMemberships = next;
        return pendingMemberships.size;
    }

    async function refreshPendingWorkPlaylists() {
        if (!root.prksSync || !root.prksSync.store) return [];
        let operations;
        try {
            operations = await root.prksSync.store.listOperations();
        } catch (_e) {
            return [];
        }
        /* The catalogue is read only when some pending membership actually
         * NAMES a playlist. A removal carries no title to look up, so a device
         * whose only pending change is "take this video out" must not pay for
         * a catalogue read it has no use for. */
        let catalogue = [];
        const names = Array.from(pendingWorkPlaylists(operations).values())
            .some(function (playlistId) { return !!playlistId; });
        if (names && typeof root.prksEffectivePlaylistCatalogue === 'function') {
            catalogue = await root.prksEffectivePlaylistCatalogue(operations) || [];
        }
        setPendingWorkPlaylists(operations, catalogue);
        return operations;
    }

    /** A pending membership applied to rows that name a Work's playlist. */
    function applyPendingWorkPlaylists(rows) {
        if (!Array.isArray(rows) || !rows.length || !pendingMemberships.size) return rows;
        let changed = false;
        const out = rows.map(function (row) {
            const patch = pendingMemberships.get(row && row.id);
            if (!patch) return row;
            if (row.playlist_id === patch.playlist_id &&
                row.playlist_title === patch.playlist_title) return row;
            changed = true;
            return Object.assign({}, row, patch);
        });
        return changed ? out : rows;
    }

    /* ---- bases ---- */

    function isPlaylistStateShape(value, playlistId) {
        if (!value || typeof value !== 'object') return false;
        if (playlistId != null && value.playlist_id !== playlistId) return false;
        if (!Number.isSafeInteger(value.order_revision) || value.order_revision < 0) return false;
        const fields = value.fields;
        if (!fields || typeof fields !== 'object') return false;
        for (let i = 0; i < FIELDS.length; i += 1) {
            const entry = fields[FIELDS[i]];
            if (!entry || typeof entry !== 'object') return false;
            if (!Number.isSafeInteger(entry.revision) || entry.revision < 0) return false;
        }
        return true;
    }

    function isWorkPlaylistStateShape(value, workId) {
        if (!value || typeof value !== 'object') return false;
        if (workId != null && value.work_id !== workId) return false;
        return typeof value.playlist_id === 'string' &&
            Number.isSafeInteger(value.revision) && value.revision >= 0;
    }

    async function readPlaylistState(playlistId, options) {
        const result = await root.prksOfflineReadEntity('playlist-state', playlistId,
            '/api/playlists/' + encodeURIComponent(playlistId) + '/sync-state',
            Object.assign({}, options || {},
                { validate: v => isPlaylistStateShape(v, playlistId) }));
        if (result.value !== null && !isPlaylistStateShape(result.value, playlistId)) {
            await root.prksOfflineInvalidateEntity('playlist-state', playlistId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    async function readWorkPlaylistState(workId, options) {
        const result = await root.prksOfflineReadEntity('work-playlist-state', workId,
            '/api/works/' + encodeURIComponent(workId) + '/playlist-state',
            Object.assign({}, options || {},
                { validate: v => isWorkPlaylistStateShape(v, workId) }));
        if (result.value !== null && !isWorkPlaylistStateShape(result.value, workId)) {
            await root.prksOfflineInvalidateEntity('work-playlist-state', workId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }

    function newPlaylistState(playlistId) {
        const fields = {};
        FIELDS.forEach(function (name) { fields[name] = { revision: 0 }; });
        return { playlist_id: playlistId, fields: fields, order_revision: 0 };
    }

    function observedPlaylistFields(playlist, state) {
        const fields = state && state.fields && typeof state.fields === 'object'
            ? state.fields : {};
        const base = {};
        FIELDS.forEach(function (name) {
            const entry = fields[name];
            const revision = entry && Number.isSafeInteger(entry.revision) && entry.revision >= 0
                ? entry.revision : 0;
            const raw = playlist ? playlist[name] : null;
            base[name] = { value: raw == null ? '' : String(raw), revision: revision };
        });
        return base;
    }

    /**
     * The ACKNOWLEDGED base a playlist edit is measured against.
     *
     * The same three concepts the Person editor keeps apart. Returns null when
     * the base is not knowable: guessing revision 0 for a playlist whose
     * revisions this device has never read would silently overwrite whatever
     * another device wrote.
     */
    async function acknowledgedPlaylistBase(playlistId, operations) {
        if (typeof playlistId !== 'string' || !playlistId) return null;
        const creating = pendingCreates(operations).find(op => op.entity_id === playlistId);
        if (creating) {
            return observedPlaylistFields(catalogRowFromOp(creating),
                newPlaylistState(playlistId));
        }
        let state = null;
        try {
            const result = await readPlaylistState(playlistId);
            state = result && result.value;
        } catch (_e) { state = null; }
        if (!state) return null;
        let playlist = null;
        try {
            const cached = await root.prksOfflineReadEntity('playlist', playlistId,
                '/api/playlists/' + encodeURIComponent(playlistId), {});
            playlist = cached && cached.value;
        } catch (_e) { playlist = null; }
        return playlist ? observedPlaylistFields(playlist, state) : null;
    }

    /**
     * The ACKNOWLEDGED order an edit is measured against: `{work_ids, revision}`.
     *
     * A playlist created here and never sent has an empty order at revision 0,
     * which is genuinely known rather than guessed -- nothing else can have
     * written to an id no other device has seen.
     */
    async function acknowledgedPlaylistOrder(playlistId, operations) {
        if (typeof playlistId !== 'string' || !playlistId) return null;
        const creating = pendingCreates(operations).find(op => op.entity_id === playlistId);
        if (creating) return { work_ids: [], revision: 0 };
        let state = null;
        try {
            const result = await readPlaylistState(playlistId);
            state = result && result.value;
        } catch (_e) { state = null; }
        if (!state) return null;
        let playlist = null;
        try {
            const cached = await root.prksOfflineReadEntity('playlist', playlistId,
                '/api/playlists/' + encodeURIComponent(playlistId), {});
            playlist = cached && cached.value;
        } catch (_e) { playlist = null; }
        if (!playlist || !Array.isArray(playlist.items)) return null;
        return {
            work_ids: playlist.items.map(item => String(item && item.id)),
            revision: state.order_revision,
        };
    }

    /** The acknowledged playlist a Work is in: `{playlist_id, revision}` or null. */
    async function acknowledgedWorkPlaylist(workId) {
        try {
            const result = await readWorkPlaylistState(workId);
            const value = result && result.value;
            if (!value) return null;
            return { playlist_id: value.playlist_id, revision: value.revision };
        } catch (_e) { return null; }
    }

    /** What this editing session changed, measured against what it was showing. */
    function dirtyPlaylistFields(playlistId, draft, base, operations) {
        const changes = {};
        if (!draft || !base) return changes;
        const pending = pendingFieldValues(operations, playlistId);
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
        if (!runtime || !runtime.store) throw new Error('Playlist editing is not available.');
        return runtime;
    }

    async function createPlaylistDurably(fields) {
        const runtime = sync();
        const op = await runtime.store.createPlaylist(fields);
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function savePlaylistFieldsDurably(playlistId, changes, base) {
        const runtime = sync();
        const written = await runtime.store.savePlaylistFields(playlistId, changes, base);
        if (typeof runtime.changed === 'function') runtime.changed();
        return written;
    }

    async function setWorkPlaylistDurably(workId, playlistId, observed, localContext) {
        const runtime = sync();
        const op = await runtime.store.setWorkPlaylist(workId, playlistId, observed, localContext);
        /* Refresh the synchronous map HERE, not only at route hydration. A
         * video added from the playlist page has to name its playlist the
         * moment the user reaches it, and the surfaces that render one read the
         * map synchronously. */
        await refreshPendingWorkPlaylists();
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function reorderPlaylistItemsDurably(playlistId, workIds, observed) {
        const runtime = sync();
        const op = await runtime.store.reorderPlaylistItems(playlistId, workIds, observed);
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    async function deletePlaylistDurably(playlistId) {
        const runtime = sync();
        const op = await runtime.store.deletePlaylist(playlistId);
        await refreshPendingWorkPlaylists();
        if (typeof runtime.changed === 'function') runtime.changed();
        return op;
    }

    /* ---- sync handlers ---- */

    const createHandler = {
        isResult: function (data, op) {
            if (!data || data.playlist_id !== op.entity_id) return false;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean' &&
                !!data.playlist && data.playlist.id === data.playlist_id;
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileCreatedPlaylist(data),
    };

    const fieldHandler = {
        isResult: function (data, op) {
            if (!data || data.playlist_id !== op.entity_id) return false;
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
                case 'ENTITY_NOT_FOUND':
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
        reconcile: (data, op) => root.prksOfflineReconcilePlaylistField(data, op),
    };

    const workPlaylistHandler = {
        isResult: function (data, op) {
            if (!data || data.work_id !== op.entity_id) return false;
            switch (data.code) {
                case 'ACKNOWLEDGED':
                    return typeof data.changed === 'boolean' &&
                        typeof data.playlist_id === 'string' &&
                        typeof data.playlist_title === 'string' &&
                        Number.isSafeInteger(data.server_revision);
                case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                    return Number.isSafeInteger(data.current_revision) &&
                        typeof data.current_value === 'string';
                case 'ENTITY_NOT_FOUND': case 'PLAYLIST_NOT_FOUND':
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
        reconcile: (data, op) => root.prksOfflineReconcileWorkPlaylist(data, op),
    };

    const orderHandler = {
        isResult: function (data, op) {
            if (!data || data.playlist_id !== op.entity_id) return false;
            switch (data.code) {
                case 'ACKNOWLEDGED':
                    return typeof data.changed === 'boolean' &&
                        Number.isSafeInteger(data.server_revision) &&
                        (data.work_ids === undefined || Array.isArray(data.work_ids));
                case 'REVISION_CONFLICT': case 'FUTURE_REVISION':
                    return Number.isSafeInteger(data.current_revision);
                case 'ENTITY_NOT_FOUND':
                    return true;
                default: return false;
            }
        },
        terminal: function (data) {
            /* Counts, never the two orders: a long playlist's ids would not fit
             * the durable result bound, and the resolution UI re-reads the
             * playlist to show what the server has. */
            const out = { code: data.code };
            if (Number.isSafeInteger(data.current_revision)) {
                out.current_revision = data.current_revision;
            }
            return { conflict: out };
        },
        reconcile: (data, op) => root.prksOfflineReconcilePlaylistOrder(data, op),
    };

    const deleteHandler = {
        isResult: function (data, op) {
            if (!data || data.playlist_id !== op.entity_id) return false;
            return data.code === 'ACKNOWLEDGED' && typeof data.changed === 'boolean';
        },
        terminal: data => ({ conflict: { code: data.code } }),
        reconcile: data => root.prksOfflineReconcileDeletedPlaylist(data),
    };

    Object.assign(root, {
        PRKS_PLAYLIST_FIELDS: FIELDS,
        PRKS_PLAYLIST_FIELD_LABELS: LABELS,
        prksIsSupportedPlaylistField: isSupportedField,
        prksPlaylistRowFromOp: catalogRowFromOp,
        prksPendingPlaylistCreates: pendingCreates,
        prksPendingPlaylistDeletions: pendingDeletions,
        prksPendingWorkPlaylists: pendingWorkPlaylists,
        prksPendingPlaylistOrders: pendingOrders,
        prksApplyPlaylistOrder: applyOrder,
        prksEffectivePlaylists: effectivePlaylists,
        prksEffectivePlaylistDetail: effectivePlaylistDetail,
        prksEffectiveWorkPlaylist: effectiveWorkPlaylist,
        prksSetPendingWorkPlaylists: setPendingWorkPlaylists,
        prksRefreshPendingWorkPlaylists: refreshPendingWorkPlaylists,
        prksApplyPendingWorkPlaylists: applyPendingWorkPlaylists,
        prksIsPlaylistStateShape: isPlaylistStateShape,
        prksIsWorkPlaylistStateShape: isWorkPlaylistStateShape,
        prksReadPlaylistState: readPlaylistState,
        prksReadWorkPlaylistState: readWorkPlaylistState,
        prksNewPlaylistState: newPlaylistState,
        prksObservedPlaylistFields: observedPlaylistFields,
        prksAcknowledgedPlaylistBase: acknowledgedPlaylistBase,
        prksAcknowledgedPlaylistOrder: acknowledgedPlaylistOrder,
        prksAcknowledgedWorkPlaylist: acknowledgedWorkPlaylist,
        prksDirtyPlaylistFields: dirtyPlaylistFields,
        prksCreatePlaylistDurably: createPlaylistDurably,
        prksSavePlaylistFieldsDurably: savePlaylistFieldsDurably,
        prksSetWorkPlaylistDurably: setWorkPlaylistDurably,
        prksReorderPlaylistItemsDurably: reorderPlaylistItemsDurably,
        prksDeletePlaylistDurably: deletePlaylistDurably,
        prksPlaylistCreateSyncHandler: createHandler,
        prksPlaylistFieldSyncHandler: fieldHandler,
        prksWorkPlaylistSyncHandler: workPlaylistHandler,
        prksPlaylistOrderSyncHandler: orderHandler,
        prksPlaylistDeleteSyncHandler: deleteHandler,
    });
})(typeof window === 'undefined' ? globalThis : window);
