function prksPlEsc(s) {
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/**
 * The Playlist catalogue a user should see: what this device holds, with every
 * unsynchronized intent applied. A playlist created here is real, so it is in
 * the list before any server has heard of it.
 */
async function fetchPlaylists(options) {
    const signal = options && options.signal;
    let rows = [];
    try {
        const cached = await prksOfflineReadList(
            PRKS_PLAYLISTS_LIST_KEY, '/api/playlists', { signal: signal });
        rows = cached && Array.isArray(cached.value) ? cached.value : [];
    } catch (e) {
        if (typeof prksIsAbortError === 'function' && prksIsAbortError(e)) return [];
        rows = [];
    }
    if (typeof prksEffectivePlaylistRows !== 'function') return rows;
    try {
        return await prksEffectivePlaylistRows(rows) || [];
    } catch (_e) {
        return rows;
    }
}

/**
 * One Playlist, with its pending fields, contents and order applied.
 *
 * Every surface that re-reads a playlist after a change comes through here, so
 * a change made with no server is visible for the same reason an acknowledged
 * one is -- the durable queue, not a lucky refetch.
 */
async function fetchPlaylistDetails(id, options) {
    const signal = options && options.signal;
    const ops = typeof prksDurableOperationsOrNone === 'function'
        ? await prksDurableOperationsOrNone() : [];
    if (typeof prksPendingPlaylistDeletions === 'function' &&
        prksPendingPlaylistDeletions(ops).has(id)) return null;
    const unsent = typeof prksPendingPlaylistCreates === 'function' &&
        prksPendingPlaylistCreates(ops).some(op => op.entity_id === id);
    let value = null;
    if (unsent) {
        /* Nothing the server holds can be in a playlist it has never heard of,
         * so the fetch is skipped entirely rather than answered with a 404. */
        value = typeof prksPendingCreatedPlaylist === 'function'
            ? prksPendingCreatedPlaylist(id, ops) : null;
    } else {
        try {
            const cached = await prksOfflineReadEntity('playlist', id,
                '/api/playlists/' + encodeURIComponent(id),
                { signal: signal, validate: v => prksIsPlaylistShape(v, id) });
            value = cached && cached.value;
        } catch (e) {
            if (typeof prksIsAbortError === 'function' && prksIsAbortError(e)) return null;
            value = null;
        }
    }
    if (!value) return null;
    if (typeof prksEffectivePlaylistDetail !== 'function') return value;
    /* The browse catalogue is read only when a membership is pending: it is the
     * one place a row for a video added here can come from. */
    let works = [];
    if (typeof prksPendingWorkPlaylists === 'function' &&
        prksPendingWorkPlaylists(ops).size &&
        typeof prksOfflineReadList === 'function') {
        try {
            const browse = await prksOfflineReadList(
                PRKS_WORKS_BROWSE_LIST_KEY, '/api/works', { signal: signal });
            works = browse && Array.isArray(browse.value) ? browse.value : [];
        } catch (_e) { works = []; }
    }
    return prksEffectivePlaylistDetail(value, ops, works);
}

/* --- Offline policy for Playlist routes -----------------------------------
 * Playlists are local-first: creating one, editing its fields, adding and
 * removing videos, reordering it and deleting it are all semantic operations
 * with revisions and defined conflicts, so nothing here is disabled for want
 * of a connection.
 *
 * Two surfaces still read the server rather than the durable queue, and those
 * stay online-only, because neither can be answered from anything this device
 * holds:
 *   - the "Add video" search reads the whole Works catalogue;
 *   - the Work card's playlist picker reads the Playlist catalogue.
 * Both only DISABLE a search; the decisions they lead to are durable.
 *
 * Navigation is deliberately untouched -- a Playlist item is an ordinary
 * `#/works/:id` link and "All playlists" an ordinary route, so each destination
 * decides for itself whether it has cached data. `original_url` is an external
 * link: PRKS being unreachable says nothing about the rest of the internet. */
const PRKS_PLAYLIST_MUTATION_SELECTOR = [
    '#prks-playlist-add-search',
    '#prks-playlist-add-results button',
].join(', ');

/* The Work detail page's own Playlist card lives on a *Work* route, so it
 * needs its own owned policy rather than riding on the Playlist routes'
 * binding.
 *
 * What is disabled is the SEARCH over the Playlist catalogue and the Set button
 * that can only act on what that search picked. Clear names no playlist at all,
 * and New... mints one here and attaches this video to it -- both are ordinary
 * durable decisions, so both stay live. Edit is disabled offline only while NOT
 * already editing: an editor open when the connection drops keeps its draft and
 * its Done button. */
const PRKS_WORK_PLAYLIST_MUTATION_SELECTOR = [
    '#prks-work-playlist-search',
    '#prks-work-playlist-set-btn',
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
        if (!('disabled' in el)) return;
        if (!online) {
            el.disabled = true;
            el.setAttribute('aria-disabled', 'true');
            return;
        }
        /* Coming back online releases only this helper's lock. An in-flight
         * Add keeps aria-busy, and a sibling Vue already disabled stays disabled. */
        if (el.getAttribute('aria-busy') === 'true') {
            el.removeAttribute('aria-disabled');
            return;
        }
        if (el.getAttribute('aria-disabled') === 'true') {
            el.disabled = false;
            el.removeAttribute('aria-disabled');
        }
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

/* --- Durable Playlist mutations ------------------------------------------
 * Every production Playlist write goes through these, so there is exactly one
 * boundary per operation and coherence cannot depend on each UI surface
 * remembering a domain hook.
 *
 * There are no connectivity guards left here. A Playlist change is a semantic
 * operation with a revision and a defined conflict, so it is written to the
 * durable queue and is as real offline as online -- the reconcilers own the
 * cache once the server answers. What a caller can still be told is that the
 * BASE is unknown: a playlist this device has never read has no revision to
 * measure an edit against, and guessing 0 would silently overwrite whatever
 * another device wrote. That is a different refusal from "no connection", and
 * it is the only one this layer makes.
 *
 */

function prksPlaylistSaveMessage(error, action) {
    switch (error && error.prksLocalStoreCode) {
        case 'scope_busy':
            return 'Part of this playlist is syncing or needs a decision. Try again shortly.';
        case 'entity_deleted':
            return 'This playlist is being deleted, so it cannot be changed.';
        case 'dependency_failed':
            return String(error.message || 'A change this one depends on could not be saved.');
        case 'invalid_envelope':
        case 'invalid_base':
            return String(error.message || 'That is not a valid playlist change.');
        default:
            return 'Could not ' + action + ' locally. Please retry.';
    }
}

function prksPlaylistBaseUnavailable(what) {
    const err = new Error(
        'This ' + what + ' cannot be changed offline yet. Open it once while connected to '
        + 'PRKS so its synchronization state is prepared.');
    err.prksPlaylistUnavailable = true;
    return err;
}

/**
 * Create a Playlist durably, under an id this device mints.
 *
 * The id is permanent and is never remapped, so anything added to the playlist
 * is ordered behind its creation by the generic dependency mechanism rather
 * than waiting for a server-assigned key.
 */
async function createPlaylist(title, description) {
    const op = await prksCreatePlaylistDurably({
        title: (title || '').trim() || 'Untitled playlist',
        description: (description || '').trim(),
        original_url: '',
    });
    return op && op.entity_id;
}

/**
 * Playlist metadata save, sending only what changed.
 *
 * The three concepts stay apart, exactly as the Person editor keeps them: the
 * fields passed in are the DRAFT, the acknowledged base comes from the cache
 * and the revisions projection, and the difference is measured against what
 * the caller was SHOWING. Sending every field would let one syncing field
 * refuse the whole form.
 *
 * `options.previousTitle` and `options.memberWorkIds` are accepted and ignored:
 * renaming a playlist stales the cached Work of every member, and the
 * reconciler now does that from the acknowledgement rather than from whatever
 * the editor happened to have on screen.
 */
async function updatePlaylist(playlistId, fields, options) {
    const draft = {};
    (PRKS_PLAYLIST_FIELDS || []).forEach(function (field) {
        if (!fields || !Object.prototype.hasOwnProperty.call(fields, field)) return;
        const value = fields[field];
        draft[field] = value == null || value === false ? '' : String(value);
    });
    if (!Object.keys(draft).length) return {};
    const ops = typeof prksDurableOperationsOrNone === 'function'
        ? await prksDurableOperationsOrNone() : [];
    const base = await prksAcknowledgedPlaylistBase(playlistId, ops);
    if (!base) throw prksPlaylistBaseUnavailable('playlist');
    const changes = prksDirtyPlaylistFields(playlistId, draft, base, ops);
    if (!Object.keys(changes).length) return {};
    try {
        await prksSavePlaylistFieldsDurably(playlistId, changes, base);
    } catch (error) {
        throw new Error(prksPlaylistSaveMessage(error, 'save this playlist'));
    }
    return {};
}

/**
 * Which playlist a video is in, durably.
 *
 * `addWorkToPlaylist` and `removeWorkFromPlaylist` are the same operation seen
 * from two ends -- a video is in at most ONE playlist, so adding, moving and
 * removing all set the same scalar on the WORK. Both names are kept so their
 * callers do not change.
 */
async function prksSetWorkPlaylist(workId, playlistIdOrNull, knownObserved) {
    const observed = knownObserved || await prksAcknowledgedWorkPlaylist(workId);
    if (!observed) {
        /* Unknown is not empty: without the revision this membership was
         * measured against, it would have to guess 0 and could silently
         * overwrite wherever another device had put it. */
        const err = prksPlaylistBaseUnavailable('video');
        err.message = 'This video cannot be added to a playlist offline yet. Open it once '
            + 'while connected to PRKS so its synchronization state is prepared.';
        throw err;
    }
    try {
        await prksSetWorkPlaylistDurably(
            workId, playlistIdOrNull == null ? '' : String(playlistIdOrNull), observed);
    } catch (error) {
        throw new Error(prksPlaylistSaveMessage(error, 'change this video’s playlist'));
    }
    return null;
}

async function addWorkToPlaylist(playlistId, workId) {
    return prksSetWorkPlaylist(workId, playlistId);
}

async function removeWorkFromPlaylist(playlistId, workId) {
    /* Aimed at the playlist the video is actually in, exactly as the canonical
     * endpoint is: a removal naming some other playlist changes nothing. The
     * base read here is the one the write then uses, rather than a second. */
    const observed = await prksAcknowledgedWorkPlaylist(workId);
    if (observed && observed.playlist_id && observed.playlist_id !== String(playlistId)) {
        return null;
    }
    return prksSetWorkPlaylist(workId, '', observed);
}

/**
 * The whole order, as ONE decision.
 *
 * Not a collection of independently racing positions: two devices that each
 * dragged one video produced two whole orders, and merging them index by index
 * would invent a third that neither of them chose.
 */
async function reorderPlaylist(playlistId, workIds) {
    const ops = typeof prksDurableOperationsOrNone === 'function'
        ? await prksDurableOperationsOrNone() : [];
    const observed = await prksAcknowledgedPlaylistOrder(playlistId, ops);
    if (!observed) throw prksPlaylistBaseUnavailable('playlist');
    try {
        await prksReorderPlaylistItemsDurably(playlistId, workIds, observed);
    } catch (error) {
        throw new Error(prksPlaylistSaveMessage(error, 'reorder this playlist'));
    }
}

/** Delete a Playlist durably. A tombstone: its videos survive it. */
async function deletePlaylistCanonical(playlistId) {
    try {
        await prksDeletePlaylistDurably(playlistId);
    } catch (error) {
        throw new Error(prksPlaylistSaveMessage(error, 'delete this playlist'));
    }
    return { status: 'deleted' };
}

/**
 * Product control for DELETE_PLAYLIST. Videos survive — only this playlist
 * identity and its ordered memberships are removed.
 */
async function deletePlaylistFromDetail(ctx, pl, generation) {
    const playlistId = pl && pl.id ? String(pl.id) : '';
    if (!playlistId) return;
    const ownedGeneration = typeof generation === 'number'
        ? generation
        : (ctx && typeof ctx.generation === 'number' ? ctx.generation : null);
    function stillOwnsPlaylist() {
        if (!ctx || ownedGeneration == null) return false;
        if (typeof prksTabContextOwnsEntityRoute === 'function') {
            return !!prksTabContextOwnsEntityRoute(
                ctx, ownedGeneration, 'playlist', playlistId, 'playlist-detail'
            );
        }
        return typeof ctx.isCurrent === 'function' && ctx.isCurrent(ownedGeneration);
    }
    const title = (pl && pl.title) ? String(pl.title) : 'this playlist';
    const itemCount = Array.isArray(pl && pl.items) ? pl.items.length : 0;
    const message = itemCount
        ? ('Delete “' + title + '”? The ' + itemCount + ' video'
            + (itemCount === 1 ? '' : 's')
            + ' in it will stay in your library; only the playlist is removed.')
        : ('Delete “' + title + '”? This removes the playlist from your library.');
    const confirmed =
        typeof prksConfirmDestructive === 'function'
            ? await prksConfirmDestructive({
                  title: 'Delete playlist?',
                  message: message,
                  confirmLabel: 'Delete playlist',
              })
            : true;
    if (!confirmed || !stillOwnsPlaylist()) return;
    try {
        await deletePlaylistCanonical(playlistId);
        if (!stillOwnsPlaylist()) return;
        if (ctx && ctx.ui) ctx.ui.playlistEditing = false;
        if (typeof prksNavigate === 'function') {
            prksNavigate('#/playlists', { tabId: ctx && ctx.tabId });
        }
    } catch (e) {
        if (typeof prksAlertMessage === 'function') {
            await prksAlertMessage(
                String((e && e.message) || 'Could not delete this playlist.'),
                'Error'
            );
        }
    }
}

function prksPlaylistIndexCreateOwner(explicit) {
    if (explicit && explicit.tabId) return explicit;
    const panel = document.getElementById('panel-content');
    const panelId = panel && panel.dataset ? panel.dataset.prksOwnerTabId : '';
    if (panelId && typeof prksGetTabContext === 'function') {
        const fromPanel = prksGetTabContext(panelId);
        if (fromPanel) return fromPanel;
    }
    return typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
}

function prksRememberPlaylistIndexCreate(owner) {
    const ctx = prksPlaylistIndexCreateOwner(owner);
    window.__prksPlaylistIndexCreateOrigin = ctx && ctx.tabId
        ? {
            tabId: String(ctx.tabId),
            generation: typeof ctx.generation === 'number' ? ctx.generation : null,
        }
        : { suppressed: true };
}

function prksSuppressPlaylistIndexCreateOrigin() {
    window.__prksPlaylistIndexCreateOrigin = { suppressed: true };
}

/**
 * Consume the origin recorded when the playlist modal opened.
 * A still-valid Playlists-index owner is navigated by tab id. The URL hash
 * is Main-only and is not consulted. A suppressed origin (Work attach) does
 * not fall back to whichever tab is focused.
 */
function prksTakePlaylistIndexCreateTabId() {
    const origin = window.__prksPlaylistIndexCreateOrigin || null;
    window.__prksPlaylistIndexCreateOrigin = null;
    if (origin && origin.suppressed) return '';
    const ctx = origin && origin.tabId && typeof prksGetTabContext === 'function'
        ? prksGetTabContext(origin.tabId)
        : (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    if (!ctx || ctx.destroyed || !ctx.tabId) return '';
    if (origin && origin.generation != null && ctx.generation !== origin.generation) return '';
    const route = ctx.lastResolvedRoute || ctx.route;
    if (!route || route.name !== 'playlists') return '';
    return String(ctx.tabId);
}

function prksOpenNewPlaylistModalFromPlaylistsPage(owner) {
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
    prksRememberPlaylistIndexCreate(owner);
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
    if (!container) return;
    if (typeof prksPresentVuePlaylists === 'function') {
        prksPresentVuePlaylists(ctx, container, {
            feature: 'playlists',
            availability: 'ready',
            items: Array.isArray(playlists) ? playlists : [],
            generation: ctx && ctx.generation,
        });
    }
    prksBindPlaylistOfflineState(ctx, container);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}

/**
 * Re-read one Playlist through the public detail wrapper and repaint this
 * owner only when it still owns the route. Vue calls this after a write so
 * it never enumerates the durable queue itself.
 */
async function prksReloadPlaylistDetail(ctx, playlistId) {
    const id = playlistId ? String(playlistId) : '';
    if (!ctx || !id) return null;
    const generation = ctx.generation;
    const owns = function () {
        return typeof prksTabContextOwnsEntityRoute === 'function'
            ? prksTabContextOwnsEntityRoute(ctx, generation, 'playlist', id, 'playlist-detail')
            : !!(ctx.isCurrent && ctx.isCurrent(generation));
    };
    if (!owns()) return null;
    const fresh = await fetchPlaylistDetails(id, {
        signal: ctx.abortController && ctx.abortController.signal,
    });
    if (!owns() || !fresh || String(fresh.id) !== id) return null;
    if (typeof ctx.setEntity === 'function') ctx.setEntity('playlist', fresh);
    ctx.routeSidebar = {
        playlistTitle: fresh.title || 'Playlist',
        itemCount: Array.isArray(fresh.items) ? fresh.items.length : 0,
    };
    if (ctx.root) renderPlaylistDetail(ctx, fresh, ctx.root);
    const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    if (focused && focused.tabId === ctx.tabId && typeof updatePanelContent === 'function') {
        updatePanelContent('details');
    }
    return fresh;
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

function renderPlaylistDetail(ctx, pl, container) {
    if (!container) return;
    if (pl && ctx && typeof ctx.setEntity === 'function') ctx.setEntity('playlist', pl);
    let view = pl;
    if (pl && Array.isArray(pl.items) && typeof prksEffectiveWorkSummaryRows === 'function') {
        view = Object.assign({}, pl, { items: prksEffectiveWorkSummaryRows(pl.items) });
    }
    const route = ctx && (ctx.route || ctx.lastResolvedRoute);
    const playlistId = (pl && pl.id) || (route && route.params && route.params.playlistId) || '';
    if (typeof prksPresentVuePlaylists === 'function') {
        prksPresentVuePlaylists(ctx, container, {
            feature: 'playlist-detail',
            availability: pl ? 'ready' : 'not-found',
            playlist: view,
            playlistId: playlistId ? String(playlistId) : '',
            editing: !!(ctx && ctx.ui && ctx.ui.playlistEditing),
            renaming: ctx && ctx.ui ? ctx.ui.playlistRename : null,
            generation: ctx && ctx.generation,
        });
    }
    prksBindPlaylistOfflineState(ctx, container);
    if (typeof prksBindAutosizeTextareas === 'function') prksBindAutosizeTextareas(container);
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
            /* Opening the editor is always allowed: Clear is a durable
             * decision, and the one control that needs a server -- the search
             * over the Playlist catalogue -- disables itself. */
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
    // reaching for a catalogue that is intentionally online-only while
    // offline (Clear / New / Done stay live without it).
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
            if (status && ownsPanel(status)) {
                status.textContent = String((_e && _e.message) || 'Could not set playlist.');
            }
        }
    };

    clearBtn.onclick = async () => {
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
            if (status && ownsPanel(status)) {
                status.textContent = String((_e && _e.message) || 'Could not remove.');
            }
        }
    };

    newBtn.onclick = async () => {
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
        prksSuppressPlaylistIndexCreateOrigin();
        if (typeof openModal === 'function') openModal('playlist-modal');
    };
}

window.fetchPlaylists = fetchPlaylists;
window.fetchPlaylistDetails = fetchPlaylistDetails;
window.prksBindPlaylistsIndexCreateBtn = prksBindPlaylistsIndexCreateBtn;
window.prksOpenNewPlaylistModalFromPlaylistsPage = prksOpenNewPlaylistModalFromPlaylistsPage;
window.prksTakePlaylistIndexCreateTabId = prksTakePlaylistIndexCreateTabId;
window.prksReloadPlaylistDetail = prksReloadPlaylistDetail;
window.deletePlaylistFromDetail = deletePlaylistFromDetail;
window.renderPlaylistsIndex = renderPlaylistsIndex;
window.prksApplyPlaylistPanelOfflineState = prksApplyPlaylistPanelOfflineState;
window.updatePlaylist = updatePlaylist;
window.createPlaylist = createPlaylist;
window.addWorkToPlaylist = addWorkToPlaylist;
window.removeWorkFromPlaylist = removeWorkFromPlaylist;
window.reorderPlaylist = reorderPlaylist;
window.deletePlaylistCanonical = deletePlaylistCanonical;
window.renderPlaylistDetail = renderPlaylistDetail;
window.prksRefreshPlaylistDetailMain = prksRefreshPlaylistDetailMain;
window.prksClearPlaylistRenameState = prksClearPlaylistRenameState;
window.renderPlaylistAttachControlsHtml = renderPlaylistAttachControlsHtml;
window.mountPlaylistAttachControls = mountPlaylistAttachControls;
window.prksApplyWorkPlaylistOfflineState = prksApplyWorkPlaylistOfflineState;
