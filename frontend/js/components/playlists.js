function prksPlEsc(s) {
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

async function fetchPlaylists() {
    const res = await fetch('/api/playlists');
    if (!res.ok) return [];
    const data = await res.json().catch(() => []);
    return Array.isArray(data) ? data : [];
}

async function fetchPlaylistDetails(id) {
    const res = await fetch('/api/playlists/' + encodeURIComponent(id));
    if (!res.ok) return null;
    return await res.json().catch(() => null);
}

async function createPlaylist(title, description) {
    const res = await fetch('/api/playlists', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, description }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'create failed');
    return data.id;
}

async function addWorkToPlaylist(playlistId, workId) {
    const res = await fetch(`/api/playlists/${encodeURIComponent(playlistId)}/items`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ work_id: workId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'add failed');
}

async function removeWorkFromPlaylist(playlistId, workId) {
    const res = await fetch(
        `/api/playlists/${encodeURIComponent(playlistId)}/items/${encodeURIComponent(workId)}`,
        { method: 'DELETE' }
    );
    if (!res.ok) throw new Error('remove failed');
}

async function reorderPlaylist(playlistId, workIds) {
    const res = await fetch(`/api/playlists/${encodeURIComponent(playlistId)}/reorder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ work_ids: workIds }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'reorder failed');
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

function renderPlaylistsIndex(playlists, container) {
    const list = Array.isArray(playlists) ? playlists : [];
    container.innerHTML = `
        <div class="page-header">
            <h2>Playlists</h2>
        </div>
        <div class="card-grid">
            ${
                list.length
                    ? list
                          .map(
                              (p) => `
                        <div class="project-card" data-prks-middleclick-nav="1"
                            onclick="window.location.hash='#/playlists/${encodeURIComponent(p.id)}'">
                            <div class="card-title">${prksPlEsc(p.title || 'Playlist')}</div>
                            <div class="meta-row">${Number(p.item_count || 0)} item${Number(p.item_count || 0) === 1 ? '' : 's'}</div>
                        </div>`
                          )
                          .join('')
                    : `<p class="meta-row" style="color: var(--text-secondary);">No playlists yet.</p>`
            }
        </div>
    `;
}

function renderPlaylistDetail(pl, container) {
    if (!pl) {
        container.innerHTML = '<div class="page-header"><h2>Playlist not found</h2></div>';
        return;
    }
    const items = Array.isArray(pl.items) ? pl.items : [];
    const ren =
        window.__prksPlaylistRename && typeof window.__prksPlaylistRename === 'object'
            ? window.__prksPlaylistRename
            : {};
    container.innerHTML = `
        <div class="page-header" style="gap:12px;flex-wrap:wrap;">
            <h2>${prksPlEsc(pl.title || 'Playlist')}</h2>
            <div style="flex:1 1 auto;"></div>
            <a class="route-sidebar__link" href="#/playlists">All playlists</a>
        </div>
        ${pl.description ? `<p class="meta-row" style="margin:0 0 12px 0;">${prksPlEsc(pl.description)}</p>` : ''}
        <div class="list-view">
            ${
                items.length
                    ? items
                          .map(
                              (w, idx) => `
                    <div class="list-item" style="display:flex;align-items:center;gap:10px;justify-content:space-between;">
                        <div style="min-width:0; flex: 1 1 auto;">
                            ${
                                ren[String(w.id)] === true
                                    ? `
                                <div style="display:flex; gap:8px; align-items:center;">
                                    <input type="text" id="prks-pl-rename-input-${prksPlEsc(w.id)}" value="${prksPlEsc(w.title || '')}" autocomplete="off"
                                        style="flex:1; min-width:0; padding:8px 10px; border:1px solid var(--border); background:var(--surface-muted); color:var(--text-primary);">
                                </div>
                                <div class="meta-row" style="margin-top:6px;">${prksPlEsc(w.author_text || '')}</div>
                            `
                                    : `
                                <div style="cursor:pointer;" onclick="window.location.hash='#/works/${encodeURIComponent(w.id)}'">
                                    <div style="font-weight:600;">${prksPlEsc(w.title || 'Untitled')}</div>
                                    <div class="meta-row">${prksPlEsc(w.author_text || '')}</div>
                                </div>
                            `
                            }
                        </div>
                        <div style="display:flex; gap:6px; flex: 0 0 auto;">
                            <button class="ribbon-btn" data-pl-up="${idx}" title="Move up">↑</button>
                            <button class="ribbon-btn" data-pl-down="${idx}" title="Move down">↓</button>
                            ${
                                ren[String(w.id)] === true
                                    ? `
                                <button class="ribbon-btn" data-pl-rename-save="${prksPlEsc(w.id)}" title="Save title">✓</button>
                                <button class="ribbon-btn" data-pl-rename-cancel="${prksPlEsc(w.id)}" title="Cancel">×</button>
                            `
                                    : `<button class="ribbon-btn" data-pl-rename="${prksPlEsc(w.id)}" title="Rename title">✎</button>`
                            }
                            <button class="ribbon-btn" data-pl-remove="${prksPlEsc(w.id)}" title="Remove">×</button>
                        </div>
                    </div>`
                          )
                          .join('')
                    : `<p class="meta-row" style="color: var(--text-secondary);">No items yet.</p>`
            }
        </div>
    `;

    container.onclick = async (ev) => {
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
            if (!window.__prksPlaylistRename || typeof window.__prksPlaylistRename !== 'object') {
                window.__prksPlaylistRename = {};
            }
            window.__prksPlaylistRename[wid] = true;
            renderPlaylistDetail(pl, container);
            const inp = document.getElementById('prks-pl-rename-input-' + wid);
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
            if (window.__prksPlaylistRename && typeof window.__prksPlaylistRename === 'object') {
                delete window.__prksPlaylistRename[wid];
            }
            renderPlaylistDetail(pl, container);
            return;
        }
        if (renSave) {
            const wid = String(renSave.getAttribute('data-pl-rename-save') || '').trim();
            const inp = document.getElementById('prks-pl-rename-input-' + wid);
            const nextTitle = inp ? String(inp.value || '').trim() : '';
            if (!wid || !nextTitle) return;
            try {
                const res = await fetch(`/api/works/${encodeURIComponent(wid)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: nextTitle }),
                });
                if (!res.ok) throw new Error('save failed');
                if (window.__prksPlaylistRename && typeof window.__prksPlaylistRename === 'object') {
                    delete window.__prksPlaylistRename[wid];
                }
                const fresh = await fetchPlaylistDetails(pl.id);
                renderPlaylistDetail(fresh, container);
                if (fresh) {
                    window.currentPlaylist = fresh;
                    window.__prksRouteSidebar = {
                        playlistTitle: fresh.title || 'Playlist',
                        itemCount: Array.isArray(fresh.items) ? fresh.items.length : 0,
                    };
                    if (window.__prksPlaylistDetailEditing === true && typeof updatePanelContent === 'function') {
                        updatePanelContent('details');
                    }
                }
            } catch (_e) {
                alert('Could not rename video.');
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
                const fresh = await fetchPlaylistDetails(pl.id);
                renderPlaylistDetail(fresh, container);
                if (fresh) {
                    window.currentPlaylist = fresh;
                    window.__prksRouteSidebar = {
                        playlistTitle: fresh.title || 'Playlist',
                        itemCount: Array.isArray(fresh.items) ? fresh.items.length : 0,
                    };
                    // If user is editing in the right panel, refresh it so the removed item becomes selectable again.
                    if (window.__prksPlaylistDetailEditing === true && typeof updatePanelContent === 'function') {
                        updatePanelContent('details');
                    }
                }
            } catch (_e) {
                alert('Could not remove item.');
            }
            return;
        }
        try {
            await reorderPlaylist(pl.id, ids);
            const fresh = await fetchPlaylistDetails(pl.id);
            renderPlaylistDetail(fresh, container);
            if (fresh) {
                window.currentPlaylist = fresh;
                window.__prksRouteSidebar = {
                    playlistTitle: fresh.title || 'Playlist',
                    itemCount: Array.isArray(fresh.items) ? fresh.items.length : 0,
                };
                if (window.__prksPlaylistDetailEditing === true && typeof updatePanelContent === 'function') {
                    updatePanelContent('details');
                }
            }
        } catch (_e) {
            alert('Could not reorder playlist.');
        }
    };

    // Editing is done in the right panel (Details → Edit).
}

function renderPlaylistAttachControlsHtml(work) {
    const wid = work && work.id ? String(work.id) : '';
    if (!wid) return '';
    const current = work && work.playlist_id ? String(work.playlist_id) : '';
    const currentTitle = work && work.playlist_title ? String(work.playlist_title) : '';
    const editing =
        window.__prksWorkPlaylistEdit &&
        typeof window.__prksWorkPlaylistEdit === 'object' &&
        window.__prksWorkPlaylistEdit[wid] === true;
    const currentLine = current
        ? `Current: <strong>${prksPlEsc(currentTitle || current)}</strong>`
        : 'Not in a playlist.';
    return `
        <div class="doc-meta-card">
            <div style="display:flex; justify-content:space-between; align-items:center; gap:10px;">
                <h3 style="margin:0;">Playlist</h3>
                <button type="button" class="ribbon-btn" id="prks-work-playlist-edit-btn" style="margin-top:0;">${editing ? 'Done' : 'Edit'}</button>
            </div>
            <p class="meta-row">Group this video into a course playlist.</p>
            <p class="meta-row" style="margin-top:8px; color: var(--text-secondary);">${currentLine}</p>
            <div id="prks-work-playlist-nav" style="margin-top:10px;"></div>
            ${
                editing
                    ? `
                <div class="tag-add-shell combobox-container" style="margin:10px 0 0 0;">
                    <div class="tag-add-shell__field">
                        <span class="tag-add-shell__icon" aria-hidden="true">＋</span>
                        <input type="text" id="prks-work-playlist-search" class="tag-add-shell__input" placeholder="Search playlists…" maxlength="300" autocomplete="off" aria-label="Search playlists">
                        <input type="hidden" id="prks-work-playlist-id" value="${prksPlEsc(current)}">
                    </div>
                    <div id="prks-work-playlist-results" class="combobox-results combobox-results--tag-panel hidden"></div>
                </div>
                <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:10px;">
                    <button type="button" class="add-new-btn" id="prks-work-playlist-set-btn" style="flex:1; margin-top:0;">Set playlist</button>
                    <button type="button" class="ribbon-btn" id="prks-work-playlist-clear-btn" style="margin-top:0;">Clear</button>
                    <button type="button" class="ribbon-btn" id="prks-work-playlist-new-btn" style="margin-top:0;">New…</button>
                </div>
            `
                    : ''
            }
            <p id="prks-work-playlist-status" class="meta-row" aria-live="polite" style="margin-top:8px;"></p>
        </div>
    `;
}

async function mountPlaylistAttachControls(work) {
    const wid = work && work.id ? String(work.id) : '';
    if (!wid) return;
    const editBtn = document.getElementById('prks-work-playlist-edit-btn');
    const newBtn = document.getElementById('prks-work-playlist-new-btn');
    const status = document.getElementById('prks-work-playlist-status');
    const navHost = document.getElementById('prks-work-playlist-nav');
    if (editBtn && editBtn.dataset.bound !== '1') {
        editBtn.dataset.bound = '1';
        editBtn.onclick = () => {
            if (!window.__prksWorkPlaylistEdit || typeof window.__prksWorkPlaylistEdit !== 'object') {
                window.__prksWorkPlaylistEdit = {};
            }
            window.__prksWorkPlaylistEdit[wid] = !(window.__prksWorkPlaylistEdit[wid] === true);
            if (typeof updatePanelContent === 'function') updatePanelContent('details');
        };
    }

    // Always show Prev/Next navigation (when in a playlist), even when not editing.
    if (navHost) {
        navHost.innerHTML = '';
        const pid = work && work.playlist_id ? String(work.playlist_id) : '';
        if (pid && typeof fetchPlaylistDetails === 'function') {
            try {
                const pl = await fetchPlaylistDetails(pid);
                const items = pl && Array.isArray(pl.items) ? pl.items : [];
                const idx = items.findIndex((x) => String(x.id) === wid);
                const prev = idx > 0 ? items[idx - 1] : null;
                const next = idx >= 0 && idx < items.length - 1 ? items[idx + 1] : null;
                const posLabel =
                    idx >= 0 && items.length ? `<span class="meta-row" style="margin:0; color: var(--text-secondary);">Item ${idx + 1} of ${items.length}</span>` : '';
                const prevDisabled = !prev ? 'disabled' : '';
                const nextDisabled = !next ? 'disabled' : '';
                navHost.innerHTML = `
                    <div style="display:flex; gap:8px; align-items:center; justify-content:space-between;">
                        <div style="display:flex; gap:8px; align-items:center;">
                            <button type="button" class="ribbon-btn ribbon-btn--sm" id="prks-work-playlist-prev-btn" style="margin-top:0;" ${prevDisabled}>
                                <span class="ribbon-btn__icon" aria-hidden="true">←</span>
                                <span class="ribbon-btn__label">Prev</span>
                            </button>
                            <button type="button" class="ribbon-btn ribbon-btn--sm" id="prks-work-playlist-next-btn" style="margin-top:0;" ${nextDisabled}>
                                <span class="ribbon-btn__label">Next</span>
                                <span class="ribbon-btn__icon" aria-hidden="true">→</span>
                            </button>
                        </div>
                        <div style="flex:1 1 auto;"></div>
                        <div style="font-size:0.78rem; white-space:nowrap;">${posLabel}</div>
                    </div>
                `;
                const prevBtn = document.getElementById('prks-work-playlist-prev-btn');
                const nextBtn = document.getElementById('prks-work-playlist-next-btn');
                if (prevBtn && prev) {
                    prevBtn.onclick = () => {
                        window.location.hash = '#/works/' + encodeURIComponent(prev.id);
                    };
                }
                if (nextBtn && next) {
                    nextBtn.onclick = () => {
                        window.location.hash = '#/works/' + encodeURIComponent(next.id);
                    };
                }
            } catch (_e) {
                // If playlist fetch fails, skip nav silently.
                navHost.innerHTML = '';
            }
        }
    }

    // Only mount the editable controls when in edit mode.
    const editing =
        window.__prksWorkPlaylistEdit &&
        typeof window.__prksWorkPlaylistEdit === 'object' &&
        window.__prksWorkPlaylistEdit[wid] === true;
    if (!editing) return;

    const input = document.getElementById('prks-work-playlist-search');
    const hidden = document.getElementById('prks-work-playlist-id');
    const results = document.getElementById('prks-work-playlist-results');
    const setBtn = document.getElementById('prks-work-playlist-set-btn');
    const clearBtn = document.getElementById('prks-work-playlist-clear-btn');
    if (!input || !hidden || !results || !setBtn || !clearBtn || !newBtn) return;

    const playlists = await fetchPlaylists();
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
                    results.classList.add('hidden');
                };
                results.appendChild(div);
            }
        }
        results.classList.remove('hidden');
    }

    input.onfocus = () => renderDropdown();
    input.oninput = () => {
        hidden.value = '';
        renderDropdown();
    };
    input.onblur = () => setTimeout(() => results.classList.add('hidden'), 180);

    setBtn.onclick = async () => {
        const pid = String(hidden.value || '').trim();
        if (!pid) return;
        try {
            await addWorkToPlaylist(pid, wid);
            if (status) status.textContent = 'Playlist set.';
            // Refresh current work so the UI shows the selected playlist title consistently.
            if (typeof fetchWorkDetails === 'function') {
                window.currentWork = await fetchWorkDetails(wid);
                if (typeof updatePanelContent === 'function') updatePanelContent('details');
            }
        } catch (_e) {
            if (status) status.textContent = 'Could not set playlist.';
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
            await removeWorkFromPlaylist(currentPid, wid);
            input.value = '';
            hidden.value = '';
            if (status) status.textContent = 'Removed from playlist.';
            if (typeof fetchWorkDetails === 'function') {
                window.currentWork = await fetchWorkDetails(wid);
                if (typeof updatePanelContent === 'function') updatePanelContent('details');
            }
        } catch (_e) {
            if (status) status.textContent = 'Could not remove.';
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
        if (typeof openModal === 'function') openModal('playlist-modal');
    };
}

window.fetchPlaylists = fetchPlaylists;
window.fetchPlaylistDetails = fetchPlaylistDetails;
window.prksBindPlaylistsIndexCreateBtn = prksBindPlaylistsIndexCreateBtn;
window.renderPlaylistsIndex = renderPlaylistsIndex;
window.renderPlaylistDetail = renderPlaylistDetail;
window.renderPlaylistAttachControlsHtml = renderPlaylistAttachControlsHtml;
window.mountPlaylistAttachControls = mountPlaylistAttachControls;

