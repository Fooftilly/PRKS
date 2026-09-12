/* Work open events: the Recent overlay, the acknowledged merge, and what a
 * server answer means for this family.
 *
 * The shape of this module mirrors `work-tag-state.js` on purpose -- pure
 * projections plus one sync handler -- but the SEMANTICS are deliberately
 * different, and that difference is the point of the family boundary:
 *
 *   Work Tags   a revisioned relationship; two devices can genuinely disagree,
 *               so a conflict is real and the user resolves it.
 *   Work opens  a max-register over normalized event times; two devices cannot
 *               disagree, because "opened at some point" only ever moves
 *               forward. There is no conflict to show and none is offered.
 */
(function (root) {
    'use strict';
    /* Matches the server's `get_recent_browse()` limit. The overlay has to
     * truncate the same way or an optimistic list would be longer than the one
     * the server will send back. */
    const RECENT_LIMIT = 30;
    /* Exactly the compact browse-card fields `/api/recent` projects. A snapshot
     * is only a fallback for a Work the cached Recent list does not already
     * carry; derived display fields are never invented, only copied. */
    const SNAPSHOT_FIELDS = ['id', 'title', 'status', 'doc_type', 'file_path', 'source_kind',
        'source_url', 'thumb_url', 'thumb_page', 'author_text', 'year', 'published_date',
        'primary_author', 'primary_editor', 'linked_authors', 'file_size_bytes'];

    /** The canonical sortable text form `/api/recent` rows carry. */
    function moment(iso) {
        const at = new Date(iso);
        if (!Number.isFinite(at.getTime())) return null;
        return at.toISOString().replace('T', ' ').slice(0, 23);
    }

    /* The canonical Recent order, reproduced exactly: `last_opened_at DESC`
     * with `id ASC` breaking ties. Mixed second- and millisecond-precision
     * values compare correctly as text, because `12:00:00` is a prefix of
     * `12:00:00.250` -- the same rule SQLite applies. */
    function order(rows) {
        return rows.slice().sort((a, b) => {
            const left = String(a.last_opened_at || '');
            const right = String(b.last_opened_at || '');
            if (left !== right) return left < right ? 1 : -1;
            return String(a.id).localeCompare(String(b.id));
        }).slice(0, RECENT_LIMIT);
    }

    function openEvents(operations, workId) {
        return (operations || []).filter(op => op && op.operation === 'MARK_WORK_OPENED' &&
            op.entity_type === 'work' && op.status !== 'acknowledged' &&
            (!workId || op.entity_id === workId));
    }

    /**
     * Acknowledged Recent snapshot + durable pending open events = what the
     * user should see. Pending intent is never written into the cached list.
     *
     * A pending open for a Work the base list does not carry needs a display
     * row; the event's own bounded snapshot supplies one. With neither, the
     * event is skipped rather than rendered as a fabricated card -- an open
     * event is not enough to invent a Work from.
     */
    function effectiveRecent(rows, operations) {
        if (!Array.isArray(rows)) return null;
        const byId = new Map(rows.map(row => [row.id, row]));
        const latest = new Map();
        openEvents(operations).forEach(op => {
            const at = moment(op.occurred_at);
            if (!at) return;
            if (!latest.has(op.entity_id) || latest.get(op.entity_id).at < at) {
                latest.set(op.entity_id, { at, op });
            }
        });
        latest.forEach((entry, workId) => {
            const base = byId.get(workId) ||
                (entry.op.local_context && entry.op.local_context.recent_item);
            if (!base || base.id !== workId) return;
            byId.set(workId, Object.assign({}, base, { last_opened_at: entry.at }));
        });
        return order(Array.from(byId.values()));
    }

    /** Upsert one acknowledged Recent row into a cached list, canonically. */
    function mergeRecentOpen(rows, ack) {
        const item = ack && ack.recent_item;
        if (!Array.isArray(rows) || !item || item.id !== ack.work_id) return null;
        return order(rows.filter(row => row.id !== item.id).concat([item]));
    }

    /** Bounded display snapshot for a Work that Recent may not yet know. */
    function recentSnapshot(work, at) {
        if (!work || typeof work.id !== 'string') return null;
        const row = { last_opened_at: at };
        SNAPSHOT_FIELDS.forEach(field => {
            if (work[field] !== undefined) row[field] = work[field];
        });
        return typeof root.prksIsRecentRowShape === 'function' && root.prksIsRecentRowShape(row)
            ? row : null;
    }

    /**
     * Record a genuine foreground open, durably, online or offline.
     *
     * Deliberately NOT the local store's failure contract. A Work-Tag edit that
     * cannot be stored must be reported, because the user made a change and
     * would otherwise believe it was saved. An open event is activity metadata
     * the user never asked for: losing it costs a Recent ordering, and refusing
     * to show the Work over it would cost them the thing they actually wanted.
     */
    async function recordOpened(work) {
        const sync = root.prksSync;
        if (!sync || !work || typeof work.id !== 'string' || !work.id) return false;
        const at = new Date().toISOString();
        try {
            try {
                await sync.store.recordWorkOpened(work.id, at, { recent_item: recentSnapshot(work, moment(at)) });
            } catch (error) {
                // An oversized display snapshot must not cost us the event.
                if (error && error.prksLocalStoreCode === 'invalid_context') {
                    await sync.store.recordWorkOpened(work.id, at, null);
                } else throw error;
            }
            sync.changed();
            return true;
        } catch (_) {
            if (root.console && typeof root.console.warn === 'function') {
                root.console.warn('PRKS: this open could not be recorded locally; Recent may lag.');
            }
            return false;
        }
    }

    /* ---- sync handler ---- */
    function isResult(data, op) {
        if (!data || data.work_id !== op.entity_id) return false;
        switch (data.code) {
            case 'ACKNOWLEDGED':
                return typeof data.changed === 'boolean' &&
                    typeof data.effective_opened_at === 'string' && !!data.effective_opened_at &&
                    (data.recent_item === null || (!!data.recent_item && data.recent_item.id === data.work_id));
            case 'ENTITY_NOT_FOUND': return true;
            default: return false;
        }
    }

    /* No terminal outcome here is worth a conflict. "Apply my open event to a
     * Work that no longer exists" is not a choice anyone can make, so the
     * operation is consumed and noted in Diagnostics instead of parked forever
     * in a resolution UI the user cannot act on. */
    function terminal(data) {
        return { discard: data && data.code };
    }

    const handler = {
        isResult, terminal,
        reconcile: data => root.prksOfflineReconcileRecentOpen(data),
    };

    Object.assign(root, {
        PRKS_RECENT_OVERLAY_LIMIT: RECENT_LIMIT,
        prksRecentMoment: moment,
        prksOrderRecentRows: order,
        prksEffectiveRecent: effectiveRecent,
        prksMergeRecentOpen: mergeRecentOpen,
        prksRecentSnapshot: recentSnapshot,
        prksRecordWorkOpened: recordOpened,
        prksWorkOpenSyncHandler: handler,
    });
})(typeof window === 'undefined' ? globalThis : window);
