function prksPlEsc(s) {
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function prksPlFormatPublishedDate(raw) {
    const s = String(raw || '').trim();
    if (!s) return '';
    const iso = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!iso) return s;
    return `${iso[3]}/${iso[2]}/${iso[1]}`;
}

function prksPlWorkSubtitle(w) {
    const channel = String((w && w.author_text) || '').trim();
    const published = prksPlFormatPublishedDate(w && w.published_date);
    if (channel && published) return `${channel} · ${published}`;
    return channel || published || '';
}

async function fetchPlaylists(options) {
    const signal = options && options.signal;
    try {
        const res = await prksRequest(
            '/api/playlists',
            { signal: signal },
            { freshForMs: typeof PRKS_REQUEST_BURST_FRESH_MS === 'number' ? PRKS_REQUEST_BURST_FRESH_MS : 1500 }
        );
        if (!res.ok) return [];
        const data = await res.json().catch(() => []);
        return Array.isArray(data) ? data : [];
    } catch (e) {
        if (typeof prksIsAbortError === 'function' && prksIsAbortError(e)) return [];
        throw e;
    }
}

async function fetchPlaylistDetails(id, options) {
    const signal = options && options.signal;
    try {
        const res = await prksRequest('/api/playlists/' + encodeURIComponent(id), { signal: signal });
        if (!res.ok) return null;
        return await res.json().catch(() => null);
    } catch (e) {
        if (typeof prksIsAbortError === 'function' && prksIsAbortError(e)) return null;
        throw e;
    }
}

/* --- Offline policy for Playlist routes (AGENTS.md "Offline / PWA") -------
 * Playlists are read-only offline in Phase 1: cached index/detail render, and
 * every canonical mutation is blocked outright (never queued, never faked).
 * Navigation is deliberately untouched -- a Playlist item is an ordinary
 * `#/works/:id` link and "All playlists" an ordinary route, so each destination
 * decides for itself whether it has cached data. `original_url` is an external
 * link: PRKS being unreachable says nothing about the rest of the internet. */
const PRKS_PLAYLIST_MUTATION_SELECTOR = [
    '#prks-create-playlist-btn',
    '#prks-playlist-edit-btn',
    '#prks-playlist-edit-title',
    '#prks-playlist-edit-desc',
    '#prks-playlist-edit-original-url',
    '#prks-playlist-edit-save',
    '#prks-playlist-add-search',
    '#prks-playlist-add-results button',
    '[data-pl-up]',
    '[data-pl-down]',
    '[data-pl-remove]',
].join(', ');

/* The inline Work rename is deliberately ABSENT from that list. It changes a
 * Work Title, not Playlist state, and Work Titles are local-first -- so it
 * works offline exactly as the metadata editor does. Every genuine Playlist
 * mutation above stays online-only. */

/* The Work detail page's own Playlist card ("Set playlist" / Clear / New…)
 * is a Playlist mutation surface living on a *Work* route, so it needs its own
 * owned policy rather than riding on the Playlist routes' binding. Edit is
 * disabled offline only while NOT already editing: an editor open when the
 * connection drops keeps its draft and its Done button, exactly like the
 * Playlist detail editor. */
const PRKS_WORK_PLAYLIST_MUTATION_SELECTOR = [
    '#prks-work-playlist-search',
    '#prks-work-playlist-set-btn',
    '#prks-work-playlist-clear-btn',
    '#prks-work-playlist-new-btn',
].join(', ');

function prksPlaylistRuntimeOnline() {
    return typeof prksOfflineRuntimeState !== 'function' || prksOfflineRuntimeState() === 'online';
}

function prksApplyWorkPlaylistOfflineState(ctx) {
    const panel = document.getElementById('panel-content');
    if (!panel) return;
    if (typeof prksRightPanelOwnedBy === 'function' && !prksRightPanelOwnedBy(ctx, panel)) return;
    const online = prksPlaylistRuntimeOnline();
    const editing = !!(ctx && ctx.ui && ctx.ui.workPlaylistEditing);
    panel.querySelectorAll(PRKS_WORK_PLAYLIST_MUTATION_SELECTOR).forEach(function (el) {
        el.disabled = !online;
        if (online) el.removeAttribute('aria-disabled');
        else el.setAttribute('aria-disabled', 'true');
    });
    const editBtn = panel.querySelector('#prks-work-playlist-edit-btn');
    if (editBtn) {
        // Done must stay live so the user can always leave an editor they can
        // no longer save; only *starting* a new session is refused offline.
        editBtn.disabled = !online && !editing;
        if (editBtn.disabled) editBtn.setAttribute('aria-disabled', 'true');
        else editBtn.removeAttribute('aria-disabled');
    }
    const results = panel.querySelector('#prks-work-playlist-results');
    if (results) {
        results.inert = !online;
        if (!online) results.classList.add('hidden');
    }
}

/* Settles every mounted Work Playlist card live. The Work right panel is not
 * owned by a Playlist route, so this mirrors the private-notes subscription
 * rather than the TabContext-container binding used by the Playlist routes. */
if (typeof prksOfflineRuntimeSubscribe === 'function') {
    prksOfflineRuntimeSubscribe(function () {
        if (typeof prksForEachLiveTabContext !== 'function') return;
        prksForEachLiveTabContext(function (ctx) {
            prksApplyWorkPlaylistOfflineState(ctx);
        });
    });
}

function prksApplyPlaylistOfflineState(container) {
    if (!container || !container.querySelectorAll) return;
    const online = typeof prksOfflineRuntimeState !== 'function' || prksOfflineRuntimeState() === 'online';
    container.querySelectorAll(PRKS_PLAYLIST_MUTATION_SELECTOR).forEach(function (el) {
        // Cancel/Close and the rename Cancel are deliberately absent from the
        // selector: a user must always be able to leave an edit they can no
        // longer save, and the draft itself stays on screen either way.
        if ('disabled' in el) el.disabled = !online;
        if (online) el.removeAttribute('aria-disabled');
        else el.setAttribute('aria-disabled', 'true');
    });
    const results = container.querySelector('#prks-playlist-add-results');
    if (results) {
        results.inert = !online;
        if (!online) results.classList.add('hidden');
    }
}

/** The Playlist editor lives in the shared right panel, so it is only settled
 *  when this context actually owns that panel -- a background Playlist tab must
 *  never disable or rewrite the panel another tab owns. */
function prksApplyPlaylistPanelOfflineState(ctx) {
    const panel = document.getElementById('panel-content');
    if (!panel) return;
    if (typeof prksRightPanelOwnedBy === 'function' && !prksRightPanelOwnedBy(ctx, panel)) return;
    prksApplyPlaylistOfflineState(panel);
}

/** Keeps a mounted Playlist route in step with connectivity. The subscription
 *  belongs to the route's TabContext and each bind replaces the previous one on
 *  the same container -- there is no global Playlist runtime singleton. */
function prksBindPlaylistOfflineState(ctx, container) {
    if (!container) return;
    if (typeof container.__prksPlaylistOfflineDispose === 'function') {
        try {
            container.__prksPlaylistOfflineDispose();
        } catch (_e) {
            /* a stale disposer must not block the new binding */
        }
    }
    const apply = function () {
        prksApplyPlaylistOfflineState(container);
        prksApplyPlaylistPanelOfflineState(ctx);
    };
    // Read current state immediately: a page rendered after the runtime already
    // left 'online' is never briefly mutable.
    apply();
    const unsubscribe =
        (typeof prksOfflineRuntimeSubscribe === 'function' &&
            prksOfflineRuntimeSubscribe(function () {
                if (container.__prksPlaylistOfflineDispose !== dispose) return;
                apply();
            })) ||
        function () {};
    let unregister = function () {};
    function dispose() {
        if (container.__prksPlaylistOfflineDispose === dispose) container.__prksPlaylistOfflineDispose = null;
        unregister();
        unsubscribe();
    }
    container.__prksPlaylistOfflineDispose = dispose;
    if (ctx && typeof ctx.registerCleanup === 'function') unregister = ctx.registerCleanup(dispose) || function () {};
}

/* --- Canonical Playlist mutation helpers --------------------------------
 * Every production Playlist write goes through these, so there is exactly one
 * canonical-success boundary per operation and coherence cannot depend on each
 * UI surface remembering a domain hook. Each one guards connectivity
 * immediately before its request as defense in depth: controls are disabled
 * offline, but the connection can drop between a dialog opening and Save, and
 * a future caller may forget its own UI guard. See AGENTS.md, "Offline
 * coherence domains".
 *
 * A guard refusal throws a tagged error rather than returning quietly, so the
 * existing throw/catch call sites keep working -- and `prksPlaylistWasBlocked()`
 * lets them skip their own error dialog, since the guard already showed one. */
function prksPlaylistMutationBlocked(message) {
    if (typeof prksOfflineGuardMutation !== 'function') return false;
    return prksOfflineGuardMutation(message);
}

function prksPlaylistBlockedError() {
    const err = new Error('Requires a connection to PRKS.');
    err.prksOfflineBlocked = true;
    return err;
}

function prksPlaylistWasBlocked(err) {
    return !!(err && err.prksOfflineBlocked);
}

function prksPlaylistsChanged() {
    return typeof prksMarkPlaylistsDomainChanged === 'function' ? prksMarkPlaylistsDomainChanged() : null;
}

function prksPlaylistWorkChanged(workId) {
    return typeof prksOfflineMarkEntityChanged === 'function'
        ? prksOfflineMarkEntityChanged('work', workId)
        : null;
}

async function createPlaylist(title, description) {
    if (prksPlaylistMutationBlocked('Creating a Playlist requires a connection to PRKS.')) {
        throw prksPlaylistBlockedError();
    }
    const res = await prksRequest('/api/playlists', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, description }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'create failed');
    // A brand-new Playlist has no members, so it cannot appear in any cached
    // Work's playlist_id/playlist_title -- Playlists only.
    prksPlaylistsChanged();
    return data.id;
}

/**
 * Playlist metadata save.
 *
 * `options.previousTitle` and `options.memberWorkIds` exist because `get_work()`
 * embeds `playlist_title`: renaming a Playlist stales the cached Work entity of
 * every Work in it. The diff lives here rather than in UI code so no caller can
 * forget it, and a description/original_url-only edit deliberately keeps those
 * Works offline-available. Only the *current* members need eviction: a Work
 * moved in or out elsewhere had its own snapshot evicted by that membership
 * mutation.
 */
async function updatePlaylist(playlistId, fields, options) {
    if (prksPlaylistMutationBlocked()) throw prksPlaylistBlockedError();
    const res = await prksRequest('/api/playlists/' + encodeURIComponent(playlistId), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'save failed');
    prksPlaylistsChanged();
    const opts = options && typeof options === 'object' ? options : {};
    const nextTitle = fields && fields.title != null ? String(fields.title) : null;
    const titleChanged = nextTitle !== null && String(opts.previousTitle == null ? '' : opts.previousTitle) !== nextTitle;
    if (titleChanged && Array.isArray(opts.memberWorkIds)) {
        opts.memberWorkIds.forEach(function (workId) {
            if (workId) prksPlaylistWorkChanged(workId);
        });
    }
    return data;
}

async function addWorkToPlaylist(playlistId, workId) {
    if (prksPlaylistMutationBlocked()) throw prksPlaylistBlockedError();
    const res = await prksRequest(`/api/playlists/${encodeURIComponent(playlistId)}/items`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ work_id: workId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'add failed');
    // One Playlist per Work: moving a Work from A to B changes both Playlists'
    // item_count and items, which whole-domain invalidation already covers.
    prksPlaylistsChanged();
    return prksPlaylistWorkChanged(workId);
}

async function removeWorkFromPlaylist(playlistId, workId) {
    if (prksPlaylistMutationBlocked()) throw prksPlaylistBlockedError();
    const res = await prksRequest(
        `/api/playlists/${encodeURIComponent(playlistId)}/items/${encodeURIComponent(workId)}`,
        { method: 'DELETE' }
    );
    if (!res.ok) throw new Error('remove failed');
    prksPlaylistsChanged();
    return prksPlaylistWorkChanged(workId);
}

async function reorderPlaylist(playlistId, workIds) {
    if (prksPlaylistMutationBlocked()) throw prksPlaylistBlockedError();
    const res = await prksRequest(`/api/playlists/${encodeURIComponent(playlistId)}/reorder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ work_ids: workIds }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'reorder failed');
    // Work detail carries no Playlist position, so no Work entity is stale --
    // but reorder also bumps updated_at, which reorders the index.
    prksPlaylistsChanged();
}

function prksOpenNewPlaylistModalFromPlaylistsPage() {
    const titleEl = document.getElementById('playlist-title');
    const descEl = document.getElementById('playlist-description');
    const errEl = document.getElementById('playlist-error');
    if (titleEl) titleEl.value = '';
    if (descEl) descEl.value = '';
    if (errEl) {
        errEl.textContent = '';
        errEl.classList.add('hidden');
    }
    window.__prksPendingPlaylistAttach = null;
    if (typeof openModal === 'function') openModal('playlist-modal');
}

/** Binds #prks-create-playlist-btn in the right panel (playlists index route). */
function prksBindPlaylistsIndexCreateBtn() {
    const btn = document.getElementById('prks-create-playlist-btn');
    if (btn && btn.dataset.bound !== '1') {
        btn.dataset.bound = '1';
        btn.onclick = () => prksOpenNewPlaylistModalFromPlaylistsPage();
    }
}

function renderPlaylistsIndex(playlists, container, ctx) {
    const list = Array.isArray(playlists) ? playlists : [];
    const rowsHtml = list.length
        ? list
              .map((p) => {
                  const id = String(p && p.id ? p.id : '').trim();
                  const path = '#/playlists/' + encodeURIComponent(id);
                  const title = prksPlEsc(p.title || 'Playlist');
                  const itemCount = Number(p.item_count || 0);
                  const icon = typeof prksIcon === 'function' ? prksIcon('clapperboard', { size: 'sm' }) : '';
                  return `
                        <div class="project-card playlists-page__list-item" data-prks-route="${path}" data-prks-middleclick-nav="1">
                            <div class="playlists-page__list-main">
                                <span class="playlists-page__badge">${icon}<span>${title}</span></span>
                                <p class="meta-row playlists-page__list-stats">${itemCount} item${itemCount === 1 ? '' : 's'}</p>
                            </div>
                            <span class="playlists-page__list-arrow" aria-hidden="true">${typeof prksIcon === 'function' ? prksIcon('chevronRight', { size: 'sm' }) : '→'}</span>
                        </div>`;
              })
              .join('')
        : `<p class="meta-row playlists-page__empty">No playlists yet.</p>`;
    container.innerHTML = `
        <div class="playlists-page">
            <div class="prks-page-header page-header tags-page__header">
                <h2 class="prks-page-title">Playlists</h2>
                <p class="tags-page__sub playlists-page__sub">Open playlist row to view or edit ordered items.</p>
            </div>
            <div class="list-view playlists-page__list">
                ${rowsHtml}
            </div>
        </div>
    `;
    prksBindPlaylistOfflineState(ctx, container);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}

function prksClearPlaylistRenameState(ctx) {
    const owner = ctx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    if (owner && owner.ui) owner.ui.playlistRename = {};
}

function prksRefreshPlaylistDetailMain(ctx) {
    const owner = ctx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const page = owner && owner.root;
    const pl = owner && owner.getEntity ? owner.getEntity('playlist') : null;
    const route = owner && (owner.lastResolvedRoute || owner.route);
    if (!page || !pl) return;
    if (route && route.name && route.name !== 'playlist-detail') return;
    renderPlaylistDetail(owner, pl, page);
}

function prksPlPlaylistItemActionsHtml(w, idx, ren) {
    const wid = prksPlEsc(w.id);
    const icon = (name) => (typeof prksIcon === 'function' ? prksIcon(name, { size: 'sm' }) : '');
    return `
        <div class="prks-playlist-item__actions">
            <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-pl-up="${idx}" title="Move up" aria-label="Move up">${icon('arrowUp')}</button>
            <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-pl-down="${idx}" title="Move down" aria-label="Move down">${icon('arrowDown')}</button>
            ${
                ren[String(w.id)] === true
                    ? `
                <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-pl-rename-save="${wid}" title="Save title" aria-label="Save title">${icon('check')}</button>
                <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-pl-rename-cancel="${wid}" title="Cancel rename" aria-label="Cancel rename">${icon('x')}</button>
            `
                    : `<button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-pl-rename="${wid}" title="Rename title" aria-label="Rename title">${icon('pencil')}</button>`
            }
            <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" data-pl-remove="${wid}" title="Remove from playlist" aria-label="Remove from playlist">${icon('x')}</button>
        </div>`;
}

function prksPlPlaylistItemBodyHtml(w, ren, editing) {
    const wid = prksPlEsc(w.id);
    const subtitle = prksPlEsc(prksPlWorkSubtitle(w));
    if (editing && ren[String(w.id)] === true) {
        return `
            <div class="prks-playlist-item__body prks-playlist-item__body--rename">
                <input type="text" id="prks-pl-rename-input-${wid}" class="prks-playlist-item__rename-input" value="${prksPlEsc(w.title || '')}" autocomplete="off" aria-label="Video title">
                <div class="meta-row">${subtitle}</div>
            </div>`;
    }
    const titleHtml = `<div class="card-title prks-playlist-item__title">${prksPlEsc(w.title || 'Untitled')}</div>`;
    if (editing) {
        return `
            <div class="prks-playlist-item__body">
                ${titleHtml}
                <div class="meta-row">${subtitle}</div>
            </div>`;
    }
    return `
        <div class="prks-playlist-item__body prks-playlist-item__body--link" role="link" tabindex="0" data-pl-nav="${wid}" data-prks-route="#/works/${encodeURIComponent(wid)}">
            ${titleHtml}
            <div class="meta-row">${subtitle}</div>
        </div>`;
}

function renderPlaylistDetail(ctx, pl, container) {
    if (!container) return;
    if (!pl) {
        container.innerHTML = '<div class="prks-page-header page-header"><h2 class="prks-page-title">Playlist not found</h2></div>';
        return;
    }
    if (ctx && typeof ctx.setEntity === 'function') ctx.setEntity('playlist', pl);
    const generation = ctx && ctx.generation;
    const ownsPlaylist = function () {
        return typeof prksTabContextOwnsEntityRoute === 'function'
            ? prksTabContextOwnsEntityRoute(ctx, generation, 'playlist', pl.id, 'playlist-detail')
            : !!(ctx && ctx.isCurrent && ctx.isCurrent(generation));
    };
    // Acknowledged items + pending Work-field edits (Published Date drives the
    // item subtitle). The cached playlist itself is never rewritten.
    const acknowledged = Array.isArray(pl.items) ? pl.items : [];
    const items = typeof prksEffectiveWorkSummaryRows === 'function'
        ? prksEffectiveWorkSummaryRows(acknowledged) : acknowledged;
    const editing = !!(ctx && ctx.ui && ctx.ui.playlistEditing);
    const ren =
        ctx && ctx.ui && ctx.ui.playlistRename && typeof ctx.ui.playlistRename === 'object'
            ? ctx.ui.playlistRename
            : {};
    const editingClass = editing ? ' prks-playlist-detail--editing' : '';
    container.innerHTML = `
        <div class="prks-playlist-detail${editingClass}">
            <div class="prks-page-header page-header prks-playlist-detail__header">
                <h2 class="prks-page-title">${prksPlEsc(pl.title || 'Playlist')}</h2>
                <a class="route-sidebar__link" href="#/playlists">All playlists</a>
            </div>
            ${pl.description ? `<p class="meta-row prks-playlist-detail__desc">${prksPlEsc(pl.description)}</p>` : ''}
            ${
                editing
                    ? '<p class="meta-row meta-row--compact prks-playlist-detail__hint">Reorder, rename, or remove items. Open Details → Done when finished.</p>'
                    : ''
            }
            <div class="list-view prks-playlist-detail__list">
            ${
                items.length
                    ? items
                          .map(
                              (w, idx) => `
                    <div class="prks-playlist-item project-card${editing ? ' prks-playlist-item--editing' : ''}">
                        <div class="prks-playlist-item__row">
                            ${prksPlPlaylistItemBodyHtml(w, ren, editing)}
                            ${editing ? prksPlPlaylistItemActionsHtml(w, idx, ren) : ''}
                        </div>
                    </div>`
                          )
                          .join('')
                    : `<p class="meta-row prks-playlist-detail__empty">No items yet. Use Details → Edit to add videos.</p>`
            }
            </div>
        </div>
    `;

    function playlistRenameMap() {
        if (!ctx || !ctx.ui) return {};
        if (!ctx.ui.playlistRename || typeof ctx.ui.playlistRename !== 'object') ctx.ui.playlistRename = {};
        return ctx.ui.playlistRename;
    }

    function applyFreshPlaylist(fresh) {
        if (!fresh || !ownsPlaylist() || String(fresh.id) !== String(pl.id)) return;
        if (ctx && typeof ctx.setEntity === 'function') ctx.setEntity('playlist', fresh);
        if (ctx) {
            ctx.routeSidebar = {
                playlistTitle: fresh.title || 'Playlist',
                itemCount: Array.isArray(fresh.items) ? fresh.items.length : 0,
            };
        }
        renderPlaylistDetail(ctx, fresh, container);
        const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
        if (
            ctx &&
            ctx.ui &&
            ctx.ui.playlistEditing &&
            focused &&
            focused.tabId === ctx.tabId &&
            typeof updatePanelContent === 'function'
        ) {
            updatePanelContent('details');
        }
    }

    container.onclick = async (ev) => {
        const nav = ev.target.closest && ev.target.closest('[data-pl-nav]');
        if (nav && !editing) {
            const wid = String(nav.getAttribute('data-pl-nav') || '').trim();
            if (wid && typeof prksNavigate === 'function') {
                prksNavigate('#/works/' + encodeURIComponent(wid), { tabId: ctx && ctx.tabId });
            }
            return;
        }
        if (!editing) return;

        const up = ev.target.closest && ev.target.closest('[data-pl-up]');
        const down = ev.target.closest && ev.target.closest('[data-pl-down]');
        const rm = ev.target.closest && ev.target.closest('[data-pl-remove]');
        const renBtn = ev.target.closest && ev.target.closest('[data-pl-rename]');
        const renSave = ev.target.closest && ev.target.closest('[data-pl-rename-save]');
        const renCancel = ev.target.closest && ev.target.closest('[data-pl-rename-cancel]');
        if (!up && !down && !rm && !renBtn && !renSave && !renCancel) return;
        ev.preventDefault();
        const ids = items.map((x) => x.id);
        if (renBtn) {
            const wid = String(renBtn.getAttribute('data-pl-rename') || '').trim();
            if (!wid) return;
            playlistRenameMap()[wid] = true;
            renderPlaylistDetail(ctx, pl, container);
            const inp = container.querySelector('#prks-pl-rename-input-' + wid);
            if (inp) {
                inp.focus();
                try {
                    const v = String(inp.value || '');
                    inp.setSelectionRange(v.length, v.length);
                } catch (_e) {}
            }
            return;
        }
        if (renCancel) {
            const wid = String(renCancel.getAttribute('data-pl-rename-cancel') || '').trim();
            delete playlistRenameMap()[wid];
            renderPlaylistDetail(ctx, pl, container);
            return;
        }
        if (renSave) {
            const wid = String(renSave.getAttribute('data-pl-rename-save') || '').trim();
            const inp = container.querySelector('#prks-pl-rename-input-' + wid);
            const nextTitle = inp ? String(inp.value || '').trim() : '';
            if (!wid || !nextTitle) return;
            /* A Work Title, not Playlist state -- so it takes the same durable
             * Title operation the metadata editor uses, rather than a PATCH.
             * A second mutation path for one field is the contract every
             * milestone since 2D has removed: the non-revision-aware one
             * silently overwrites the other's conflicts.
             *
             * This is deliberately NOT a Playlist mutation, so it is NOT
             * gated by `prksPlaylistMutationBlocked()`: renaming works
             * offline exactly as it does in the metadata editor. The other
             * Playlist controls remain online-only. */
            try {
                const result = await prksSaveWorkFieldDurably(wid, 'title', nextTitle,
                    { label: 'Title' });
                if (result.code === 'unavailable') {
                    await prksAlertMessage(
                        'This video\u2019s title cannot be renamed right now. Open it once while '
                        + 'connected to PRKS so its details are prepared.', 'Rename unavailable');
                    return;
                }
                if (result.code === 'too-long') {
                    await prksAlertMessage(result.error, 'Title too long');
                    return;
                }
                if (result.code === 'failed') throw new Error('save failed');
                delete playlistRenameMap()[wid];
                if (!ownsPlaylist()) return;
                /* The overlay is what makes the new title visible here; the
                 * cached Playlist keeps exactly what the server said until the
                 * operation is acknowledged. */
                await prksRefreshPendingWorkMetadata();
                if (ownsPlaylist()) renderPlaylistDetail(ctx, pl, container);
            } catch (_e) {
                if (ownsPlaylist()) await prksAlertMessage('Could not rename video.', 'Error');
            }
            return;
        }
        if (up) {
            const i = Number(up.getAttribute('data-pl-up'));
            if (i > 0) {
                const tmp = ids[i - 1];
                ids[i - 1] = ids[i];
                ids[i] = tmp;
            }
        } else if (down) {
            const i = Number(down.getAttribute('data-pl-down'));
            if (i >= 0 && i < ids.length - 1) {
                const tmp = ids[i + 1];
                ids[i + 1] = ids[i];
                ids[i] = tmp;
            }
        } else if (rm) {
            const wid = rm.getAttribute('data-pl-remove');
            try {
                await removeWorkFromPlaylist(pl.id, wid);
                if (!ownsPlaylist()) return;
                const fresh = await fetchPlaylistDetails(pl.id, {
                    signal: ctx && ctx.abortController && ctx.abortController.signal,
                });
                applyFreshPlaylist(fresh);
            } catch (_e) {
                if (prksPlaylistWasBlocked(_e)) return;
                if (ownsPlaylist()) await prksAlertMessage('Could not remove item.', 'Error');
            }
            return;
        }
        try {
            await reorderPlaylist(pl.id, ids);
            if (!ownsPlaylist()) return;
            const fresh = await fetchPlaylistDetails(pl.id, {
                signal: ctx && ctx.abortController && ctx.abortController.signal,
            });
            applyFreshPlaylist(fresh);
        } catch (_e) {
            if (prksPlaylistWasBlocked(_e)) return;
            if (ownsPlaylist()) await prksAlertMessage('Could not reorder playlist.', 'Error');
        }
    };

    // Editing is done in the right panel (Details → Edit).
    prksBindPlaylistOfflineState(ctx, container);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}

function renderPlaylistAttachControlsHtml(work, ownerCtx) {
    const wid = work && work.id ? String(work.id) : '';
    if (!wid) return '';
    const current = work && work.playlist_id ? String(work.playlist_id) : '';
    const currentTitle = work && work.playlist_title ? String(work.playlist_title) : '';
    const ctx = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const editing = !!(ctx && ctx.ui && ctx.ui.workPlaylistEditing);
    const currentLine = current
        ? `Current: <strong>${prksPlEsc(currentTitle || current)}</strong>`
        : 'Not in a playlist.';
    return `
        <div class="doc-meta-card">
            <div class="prks-panel-heading-row">
                <h3>Playlist</h3>
                <button type="button" class="prks-btn prks-btn--secondary" id="prks-work-playlist-edit-btn">${editing ? 'Done' : 'Edit'}</button>
            </div>
            <p class="meta-row">Group this video into a course playlist.</p>
            <p class="meta-row meta-row--follow">${currentLine}</p>
            <div id="prks-work-playlist-nav" class="prks-playlist-nav"></div>
            ${
                editing
                    ? `
                <div class="tag-add-shell combobox-container prks-playlist-combobox">
                    <div class="tag-add-shell__field">
                        ${typeof prksTagPlusIconHtml === 'function' ? prksTagPlusIconHtml() : ''}
                        <input type="text" id="prks-work-playlist-search" class="tag-add-shell__input" placeholder="Search playlists…" maxlength="300" autocomplete="off" aria-label="Search playlists">
                        <input type="hidden" id="prks-work-playlist-id" value="${prksPlEsc(current)}">
                    </div>
                    <div id="prks-work-playlist-results" class="combobox-results combobox-results--tag-panel hidden"></div>
                </div>
                <div class="prks-playlist-actions">
                    <button type="button" class="prks-btn prks-btn--primary" id="prks-work-playlist-set-btn">Set playlist</button>
                    <button type="button" class="prks-btn prks-btn--secondary" id="prks-work-playlist-clear-btn">Clear</button>
                    <button type="button" class="prks-btn prks-btn--secondary" id="prks-work-playlist-new-btn">New…</button>
                </div>
            `
                    : ''
            }
            <p id="prks-work-playlist-status" class="meta-row prks-playlist-status" aria-live="polite"></p>
        </div>
    `;
}

async function mountPlaylistAttachControls(work, ownerCtx) {
    const wid = work && work.id ? String(work.id) : '';
    if (!wid) return;
    const ctx = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const generation = ctx && ctx.generation;
    const panel = document.getElementById('panel-content');
    const editBtn = panel && panel.querySelector('#prks-work-playlist-edit-btn');
    const newBtn = panel && panel.querySelector('#prks-work-playlist-new-btn');
    const status = panel && panel.querySelector('#prks-work-playlist-status');
    const navHost = panel && panel.querySelector('#prks-work-playlist-nav');
    const ownsPanel = function (node) {
        const ownsWork =
            typeof prksTabContextOwnsEntityRoute === 'function'
                ? prksTabContextOwnsEntityRoute(ctx, generation, 'work', wid, 'work')
                : !!(ctx && ctx.isCurrent && ctx.isCurrent(generation));
        return !!(
            ownsWork &&
            typeof prksRightPanelOwnedBy === 'function' &&
            prksRightPanelOwnedBy(ctx, node || panel)
        );
    };
    if (!ownsPanel(panel)) return;
    // Reflect the current state immediately: a card mounted AFTER the runtime
    // already left 'online' must never briefly expose live controls.
    prksApplyWorkPlaylistOfflineState(ctx);
    if (editBtn && editBtn.dataset.bound !== '1') {
        editBtn.dataset.bound = '1';
        editBtn.onclick = () => {
            const leaving = !!(ctx && ctx.ui && ctx.ui.workPlaylistEditing);
            // Leaving an already-open editor is always allowed; starting a new
            // one offline is refused, because mounting it would fetch the
            // Playlist catalog and expose canonical mutation controls.
            if (!leaving && prksPlaylistMutationBlocked('Editing a Work\u2019s playlist requires a connection to PRKS.')) return;
            if (ctx && ctx.ui) ctx.ui.workPlaylistEditing = !ctx.ui.workPlaylistEditing;
            const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
            if (focused && ctx && focused.tabId === ctx.tabId && typeof updatePanelContent === 'function') {
                updatePanelContent('details');
            }
        };
    }

    // Always show Prev/Next navigation (when in a playlist), even when not editing.
    if (navHost) {
        navHost.innerHTML = '';
        const pid = work && work.playlist_id ? String(work.playlist_id) : '';
        // `fetchPlaylistDetails` is a raw read, not an offline read-through, so
        // offline it could only fail. Skipping it leaves the same empty nav the
        // catch below already produces, without the pointless request.
        if (pid && prksPlaylistRuntimeOnline() && typeof fetchPlaylistDetails === 'function') {
            try {
                const pl = await fetchPlaylistDetails(pid, {
                    signal: ctx && ctx.abortController && ctx.abortController.signal,
                });
                if (!ownsPanel(navHost)) return;
                const items = pl && Array.isArray(pl.items) ? pl.items : [];
                const idx = items.findIndex((x) => String(x.id) === wid);
                const prev = idx > 0 ? items[idx - 1] : null;
                const next = idx >= 0 && idx < items.length - 1 ? items[idx + 1] : null;
                const posLabel =
                    idx >= 0 && items.length ? `<span class="meta-row prks-playlist-pos">Item ${idx + 1} of ${items.length}</span>` : '';
                const prevDisabled = !prev ? 'disabled' : '';
                const nextDisabled = !next ? 'disabled' : '';
                navHost.innerHTML = `
                    <div class="prks-playlist-nav-row">
                        <div class="prks-cluster">
                            <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" id="prks-work-playlist-prev-btn" ${prevDisabled}>
                                <span class="ribbon-btn__icon">${typeof prksIcon === 'function' ? prksIcon('chevronLeft', { size: 'ribbon' }) : ''}</span>
                                <span class="ribbon-btn__label">Prev</span>
                            </button>
                            <button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" id="prks-work-playlist-next-btn" ${nextDisabled}>
                                <span class="ribbon-btn__label">Next</span>
                                <span class="ribbon-btn__icon">${typeof prksIcon === 'function' ? prksIcon('chevronRight', { size: 'ribbon' }) : ''}</span>
                            </button>
                        </div>
                        <div class="prks-spacer"></div>
                        <div class="prks-playlist-pos">${posLabel}</div>
                    </div>
                `;
                const prevBtn = navHost.querySelector('#prks-work-playlist-prev-btn');
                const nextBtn = navHost.querySelector('#prks-work-playlist-next-btn');
                if (prevBtn && prev) {
                    prevBtn.onclick = () => {
                        if (typeof prksNavigate === 'function') {
                            prksNavigate('#/works/' + encodeURIComponent(prev.id), { tabId: ctx.tabId });
                        }
                    };
                }
                if (nextBtn && next) {
                    nextBtn.onclick = () => {
                        if (typeof prksNavigate === 'function') {
                            prksNavigate('#/works/' + encodeURIComponent(next.id), { tabId: ctx.tabId });
                        }
                    };
                }
                if (typeof prksRefreshIcons === 'function') prksRefreshIcons(navHost);
            } catch (_e) {
                // If playlist fetch fails, skip nav silently.
                if (ownsPanel(navHost)) navHost.innerHTML = '';
            }
        }
    }

    // Only mount the editable controls when in edit mode.
    const editing = !!(ctx && ctx.ui && ctx.ui.workPlaylistEditing);
    if (!editing) return;

    const input = panel.querySelector('#prks-work-playlist-search');
    const hidden = panel.querySelector('#prks-work-playlist-id');
    const results = panel.querySelector('#prks-work-playlist-results');
    const setBtn = panel.querySelector('#prks-work-playlist-set-btn');
    const clearBtn = panel.querySelector('#prks-work-playlist-clear-btn');
    if (!input || !hidden || !results || !setBtn || !clearBtn || !newBtn) return;

    // An editor already open when the connection dropped keeps its markup and
    // draft, but must not reach for the Playlist catalog: `fetchPlaylists()`
    // rethrows non-abort transport failures, and this function is invoked with
    // `void`, so that would surface as an unhandled rejection as well as
    // breaking the Phase-1 read-only contract.
    let playlists = [];
    if (prksPlaylistRuntimeOnline()) {
        try {
            playlists = await fetchPlaylists({
                signal: ctx && ctx.abortController && ctx.abortController.signal,
            });
        } catch (_e) {
            // The connection can also drop *during* this read. Degrade to an
            // empty catalog rather than rejecting: this function is invoked
            // with `void`, so a throw would be an unhandled rejection.
            playlists = [];
        }
    }
    if (!ownsPanel(panel)) return;
    const rows = Array.isArray(playlists) ? playlists : [];

    // Pre-fill current playlist title in the input if present.
    if (work && work.playlist_id && work.playlist_title && !String(input.value || '').trim()) {
        input.value = String(work.playlist_title);
        hidden.value = String(work.playlist_id);
    }

    function renderDropdown() {
        const q = String(input.value || '').trim().toLowerCase();
        const filtered = !q
            ? rows.slice(0, 40)
            : rows.filter((p) => String(p.title || '').toLowerCase().includes(q)).slice(0, 40);
        results.innerHTML = '';
        if (filtered.length === 0) {
            results.innerHTML = `<div class="result-item no-results">No playlists found</div>`;
        } else {
            for (const p of filtered) {
                const div = document.createElement('div');
                div.className = 'result-item';
                div.textContent = p.title || 'Playlist';
                div.onmousedown = (ev) => {
                    ev.preventDefault();
                    input.value = p.title || '';
                    hidden.value = p.id;
                    prksHideInlineComboboxResults(results);
                };
                results.appendChild(div);
            }
        }
        if (typeof prksShowInlineComboboxResults === 'function') {
            prksShowInlineComboboxResults(input, results);
        } else {
            results.classList.remove('hidden');
        }
    }

    input.onfocus = () => renderDropdown();
    input.oninput = () => {
        hidden.value = '';
        renderDropdown();
    };
    input.onblur = () => setTimeout(() => prksHideInlineComboboxResults(results), 200);

    setBtn.onclick = async () => {
        if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
        const pid = String(hidden.value || '').trim();
        if (!pid) return;
        try {
            const coherenceToken = await addWorkToPlaylist(pid, wid);
            if (!ownsPanel(status)) return;
            if (status) status.textContent = 'Playlist set.';
            // Refresh current work so the UI shows the selected playlist title consistently.
            if (typeof fetchWorkDetails === 'function') {
                const _pw = await fetchWorkDetails(wid, {
                    signal: ctx && ctx.abortController && ctx.abortController.signal,
                });
                if (_pw && typeof prksOfflineCacheEntityIfCurrent === 'function') {
                    void prksOfflineCacheEntityIfCurrent('work', wid, _pw, coherenceToken);
                }
                if (ownsPanel(panel) && typeof prksApplyOwnedWorkEntity === 'function' && prksApplyOwnedWorkEntity(ctx, wid, _pw)) {
                    const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
                    if (focused && ctx && focused.tabId === ctx.tabId && typeof updatePanelContent === 'function') {
                        updatePanelContent('details');
                    }
                }
            }
        } catch (_e) {
            if (prksPlaylistWasBlocked(_e)) return;
            if (status && ownsPanel(status)) status.textContent = 'Could not set playlist.';
        }
    };

    clearBtn.onclick = async () => {
        if (typeof prksOfflineGuardMutation === 'function' && prksOfflineGuardMutation()) return;
        const currentPid = work && work.playlist_id ? String(work.playlist_id) : '';
        if (!currentPid) {
            input.value = '';
            hidden.value = '';
            if (status) status.textContent = '';
            return;
        }
        try {
            const coherenceToken = await removeWorkFromPlaylist(currentPid, wid);
            if (!ownsPanel(panel)) return;
            input.value = '';
            hidden.value = '';
            if (status) status.textContent = 'Removed from playlist.';
            if (typeof fetchWorkDetails === 'function') {
                const _rmw = await fetchWorkDetails(wid, {
                    signal: ctx && ctx.abortController && ctx.abortController.signal,
                });
                if (_rmw && typeof prksOfflineCacheEntityIfCurrent === 'function') {
                    void prksOfflineCacheEntityIfCurrent('work', wid, _rmw, coherenceToken);
                }
                if (ownsPanel(panel) && typeof prksApplyOwnedWorkEntity === 'function' && prksApplyOwnedWorkEntity(ctx, wid, _rmw)) {
                    const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
                    if (focused && ctx && focused.tabId === ctx.tabId && typeof updatePanelContent === 'function') {
                        updatePanelContent('details');
                    }
                }
            }
        } catch (_e) {
            if (prksPlaylistWasBlocked(_e)) return;
            if (status && ownsPanel(status)) status.textContent = 'Could not remove.';
        }
    };

    newBtn.onclick = async () => {
        // Guard before touching the global pending attachment: openModal()
        // guards too, but it refuses *after* this handler would already have
        // left `__prksPendingPlaylistAttach` set, and that stale workId would
        // then be picked up by the next Playlist creation from any surface.
        if (prksPlaylistMutationBlocked('Creating a Playlist requires a connection to PRKS.')) return;
        const titleEl = document.getElementById('playlist-title');
        const descEl = document.getElementById('playlist-description');
        const errEl = document.getElementById('playlist-error');
        if (titleEl) titleEl.value = '';
        if (descEl) descEl.value = '';
        if (errEl) {
            errEl.textContent = '';
            errEl.classList.add('hidden');
        }
        window.__prksPendingPlaylistAttach = { workId: wid };
        if (typeof openModal === 'function') openModal('playlist-modal');
    };
}

window.fetchPlaylists = fetchPlaylists;
window.fetchPlaylistDetails = fetchPlaylistDetails;
window.prksBindPlaylistsIndexCreateBtn = prksBindPlaylistsIndexCreateBtn;
window.renderPlaylistsIndex = renderPlaylistsIndex;
window.prksApplyPlaylistPanelOfflineState = prksApplyPlaylistPanelOfflineState;
window.prksPlaylistWasBlocked = prksPlaylistWasBlocked;
window.updatePlaylist = updatePlaylist;
window.renderPlaylistDetail = renderPlaylistDetail;
window.prksRefreshPlaylistDetailMain = prksRefreshPlaylistDetailMain;
window.prksClearPlaylistRenameState = prksClearPlaylistRenameState;
window.renderPlaylistAttachControlsHtml = renderPlaylistAttachControlsHtml;
window.mountPlaylistAttachControls = mountPlaylistAttachControls;
window.prksApplyWorkPlaylistOfflineState = prksApplyWorkPlaylistOfflineState;
