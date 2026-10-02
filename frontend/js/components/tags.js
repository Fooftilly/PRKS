/**
 * Tag vocabulary mutations for the Vue Tags page.
 *
 * The cloud, alias dialog, and merge dialog are painted by
 * `frontend-app/src/features/tags/`. This file keeps the writes: alias add
 * and remove stay raw HTTP because aliases live in `tag_aliases` and never
 * appear in a cached work.tags[] / folder.tags[]. Delete and merge stay on
 * the durable wrappers. `prksCloseTagsAliasModal` / `prksCloseTagsMergeModal`
 * remain the names Escape and overlay dismissal already call. Escape passes
 * the dialog element so only that pane closes. A call with no element closes
 * every open Tags dialog. They do not own selection.
 */

async function prksTagsAddAlias(tagId, alias) {
    const next = String(alias || '').trim();
    if (!tagId || !next) return { ok: false, reason: 'empty' };
    if (typeof prksOfflineGuardMutation === 'function' &&
        prksOfflineGuardMutation('Editing tag aliases requires a connection to PRKS.')) {
        return { ok: false, reason: 'offline' };
    }
    const res = await prksRequest('/api/tags/' + encodeURIComponent(tagId) + '/aliases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ alias: next }),
    });
    const errData = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(errData.error || 'Failed to add alias');
    if (typeof prksOfflineMarkTagsChanged === 'function') prksOfflineMarkTagsChanged();
    return { ok: true };
}

async function prksTagsRemoveAlias(tagId, alias) {
    if (!tagId || alias == null) return { ok: false, reason: 'empty' };
    if (typeof prksOfflineGuardMutation === 'function' &&
        prksOfflineGuardMutation('Editing tag aliases requires a connection to PRKS.')) {
        return { ok: false, reason: 'offline' };
    }
    const res = await prksRequest(
        '/api/tags/' + encodeURIComponent(tagId) + '/aliases?alias=' + encodeURIComponent(alias),
        { method: 'DELETE' }
    );
    const errData = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(errData.error || 'Failed to remove alias');
    if (typeof prksOfflineMarkTagsChanged === 'function') prksOfflineMarkTagsChanged();
    return { ok: true };
}

async function prksTagsDelete(tagId) {
    await prksDeleteTagDurably(tagId);
    return { ok: true };
}

async function prksTagsMerge(sourceId, targetId) {
    await mergeTags(sourceId, targetId);
    return { ok: true };
}

function prksCloseTagsAliasModal(modal) {
    if (typeof window.prksVueCloseTagsAliasModal === 'function') {
        window.prksVueCloseTagsAliasModal(modal);
    }
}

function prksCloseTagsMergeModal(modal) {
    if (typeof window.prksVueCloseTagsMergeModal === 'function') {
        window.prksVueCloseTagsMergeModal(modal);
    }
}

window.prksTagsAddAlias = prksTagsAddAlias;
window.prksTagsRemoveAlias = prksTagsRemoveAlias;
window.prksTagsDelete = prksTagsDelete;
window.prksTagsMerge = prksTagsMerge;
window.prksCloseTagsAliasModal = prksCloseTagsAliasModal;
window.prksCloseTagsMergeModal = prksCloseTagsMergeModal;
