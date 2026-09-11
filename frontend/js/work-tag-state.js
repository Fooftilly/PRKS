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
    async function readTags(options = {}) {
        const result = await root.prksOfflineReadList('tags:index', '/api/tags', {
            ...options, domain: 'tags', validate: tagsShape,
        });
        if (result.value !== null && !tagsShape(result.value)) {
            await root.prksOfflineInvalidateList('tags:index');
            return { value: null, source: 'unavailable', cachedAt: null };
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
    Object.assign(root, {
        prksIsTagsIndexShape: tagsShape, prksIsWorkTagOptionsShape: optionsShape,
        prksReadTagsIndex: readTags, prksReadWorkTagOptions: readOptions,
        prksWorkTagBase: base, prksEffectiveWorkTags: effectiveTags,
    });
})(typeof window === 'undefined' ? globalThis : window);
