/* Read projections and pure optimistic overlay; durable intent never enters
 * the disposable cache. No module-level Work or editor state. */
(function (root) {
    'use strict';
    const id = v => typeof v === 'string' && v.trim().length > 0 && v.length <= 200;
    const revision = v => Number.isSafeInteger(v) && v >= 0;
    function tagShape(t) {
        return !!t && id(t.id) && typeof t.name === 'string' &&
            (t.color === null || typeof t.color === 'string') &&
            Array.isArray(t.aliases) && t.aliases.every(a => typeof a === 'string');
    }
    function tagsShape(rows) {
        return Array.isArray(rows) && rows.every(tagShape) && new Set(rows.map(t => t.id)).size === rows.length;
    }
    function optionsShape(v, workId) {
        if (!v || !id(v.work_id) || (workId && v.work_id !== workId) || !Array.isArray(v.assigned) ||
            !v.known_absent || typeof v.known_absent !== 'object' || Array.isArray(v.known_absent)) return false;
        const seen = new Set();
        return v.assigned.every(t => t && id(t.tag_id) && revision(t.relation_revision) &&
            !seen.has(t.tag_id) && !!seen.add(t.tag_id)) &&
            Object.entries(v.known_absent).every(([k, r]) => id(k) && revision(r) && r > 0 && !seen.has(k));
    }
    /** Every unsynchronized operation, or an empty list. */
    async function durableOperations() {
        try {
            if (root.prksSync && root.prksSync.store) {
                return await root.prksSync.store.listOperations();
            }
        } catch (_e) { /* an unreadable store overlays nothing */ }
        return [];
    }

    async function readTags(options = {}) {
        const result = await root.prksOfflineReadList('tags:index', '/api/tags', {
            ...options, domain: 'tags', validate: tagsShape,
        });
        if (result.value !== null && !tagsShape(result.value)) {
            await root.prksOfflineInvalidateList('tags:index');
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        /* The catalogue a caller gets is the EFFECTIVE one: a Tag created on
         * this device is a real Tag the picker can attach, and one deleted here
         * must stop being offered. The acknowledged list is never written
         * through -- this overlay is recomputed from the durable queue. */
        if (result.value !== null && typeof root.prksEffectiveTagCatalogue === 'function') {
            const ops = await durableOperations();
            return { ...result, value: root.prksEffectiveTagCatalogue(result.value, ops) };
        }
        return result;
    }
    async function readOptions(workId, options = {}) {
        const result = await root.prksOfflineReadEntity('work-tag-options', workId,
            '/api/works/' + encodeURIComponent(workId) + '/tag-options', {
                ...options, validate: v => optionsShape(v, workId),
            });
        if (result.value !== null && !optionsShape(result.value, workId)) {
            await root.prksOfflineInvalidateEntity('work-tag-options', workId);
            return { value: null, source: 'unavailable', cachedAt: null };
        }
        return result;
    }
    function base(options, tagId) {
        const row = options.assigned.find(t => t.tag_id === tagId);
        return { present: !!row, revision: row ? row.relation_revision : (options.known_absent[tagId] || 0) };
    }
    function effectiveTags(work, operations) {
        const tags = new Map((work.tags || []).map(t => [t.id, t]));
        operations.filter(op => op.entity_id === work.id && op.entity_type === 'work' && op.status !== 'acknowledged')
            .forEach(op => {
                if (op.operation === 'REMOVE_WORK_TAG') tags.delete(op.payload.tag_id);
                if (op.operation === 'ADD_WORK_TAG' && op.local_context && op.local_context.tag) {
                    tags.set(op.payload.tag_id, op.local_context.tag);
                }
            });
        return Array.from(tags.values());
    }
    /* ---- sync handler: what a server answer MEANS for this family ----
     * A Work-Tag edit is something the user chose, so every terminal outcome
     * is a conflict they get to resolve rather than something to consume
     * silently. The coordinator owns transport; this owns meaning. */
    const integer = v => Number.isSafeInteger(v) && v >= 0;
    function isResult(data, op) {
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
    function terminal(data) {
        const out = { code: data.code };
        for (const key of ['current_revision', 'current_state', 'requested_state', 'target_tag_id']) {
            if (Object.prototype.hasOwnProperty.call(data, key)) out[key] = data[key];
        }
        return { conflict: out };
    }
    const handler = {
        isResult, terminal,
        reconcile: data => root.prksOfflineReconcileWorkTag(data),
    };
    Object.assign(root, {
        prksIsTagsIndexShape: tagsShape, prksIsWorkTagOptionsShape: optionsShape,
        prksReadTagsIndex: readTags, prksReadWorkTagOptions: readOptions,
        prksWorkTagBase: base, prksEffectiveWorkTags: effectiveTags,
        prksWorkTagSyncHandler: handler,
    });
})(typeof window === 'undefined' ? globalThis : window);
