/**
 * Publisher mutations for the Vue Publishers page.
 *
 * The list, create field, and alias dialog are painted by
 * `frontend-app/src/features/publishers/`. Publishers are online-only:
 * create, alias add/remove, and delete stay raw HTTP. There is no durable
 * publisher queue. `prksClosePublishersAliasModal` remains the name Escape
 * and overlay dismissal already call; it closes the Vue dialog and does not
 * own selection.
 */

var PRKS_PUBLISHERS_OFFLINE = 'Publishers require a connection to PRKS.';

function prksPublishersOfflineBlocked() {
    return typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation(PRKS_PUBLISHERS_OFFLINE);
}

async function prksPublishersReadError(res, fallback) {
    const errData = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(errData.error || fallback);
}

async function prksPublishersCreate(name) {
    const next = String(name || '').trim();
    if (!next) return { ok: false, reason: 'empty' };
    if (prksPublishersOfflineBlocked()) return { ok: false, reason: 'offline' };
    const res = await prksRequest('/api/publishers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: next }),
    });
    await prksPublishersReadError(res, 'Failed to add publisher');
    return { ok: true };
}

async function prksPublishersAddAlias(publisherId, alias) {
    const next = String(alias || '').trim();
    if (!publisherId || !next) return { ok: false, reason: 'empty' };
    if (prksPublishersOfflineBlocked()) return { ok: false, reason: 'offline' };
    const res = await prksRequest('/api/publishers/' + encodeURIComponent(publisherId) + '/aliases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ alias: next }),
    });
    await prksPublishersReadError(res, 'Failed to add alias');
    return { ok: true };
}

async function prksPublishersRemoveAlias(publisherId, alias) {
    if (!publisherId || alias == null) return { ok: false, reason: 'empty' };
    if (prksPublishersOfflineBlocked()) return { ok: false, reason: 'offline' };
    const res = await prksRequest(
        '/api/publishers/' + encodeURIComponent(publisherId) + '/aliases?alias=' + encodeURIComponent(alias),
        { method: 'DELETE' }
    );
    await prksPublishersReadError(res, 'Failed to remove alias');
    return { ok: true };
}

async function prksPublishersDelete(publisherId) {
    if (!publisherId) return { ok: false, reason: 'empty' };
    if (prksPublishersOfflineBlocked()) return { ok: false, reason: 'offline' };
    const res = await prksRequest('/api/publishers/' + encodeURIComponent(publisherId), {
        method: 'DELETE',
    });
    await prksPublishersReadError(res, 'Delete failed');
    return { ok: true };
}

function prksClosePublishersAliasModal() {
    if (typeof window.prksVueClosePublishersAliasModal === 'function') {
        window.prksVueClosePublishersAliasModal();
    }
}

window.prksPublishersCreate = prksPublishersCreate;
window.prksPublishersAddAlias = prksPublishersAddAlias;
window.prksPublishersRemoveAlias = prksPublishersRemoveAlias;
window.prksPublishersDelete = prksPublishersDelete;
window.prksClosePublishersAliasModal = prksClosePublishersAliasModal;
