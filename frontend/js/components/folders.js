function prksFolderEsc(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function prksFolderPathLabel(folderId, byId) {
    const parts = [];
    const guard = new Set();
    let cur = byId.get(folderId);
    while (cur && !guard.has(cur.id)) {
        guard.add(cur.id);
        parts.unshift(String(cur.title || 'Folder'));
        cur = cur.parent_id ? byId.get(cur.parent_id) : null;
    }
    return parts.join(' → ');
}

function prksFolderRowLabel(folder, allList) {
    const list = Array.isArray(allList) ? allList : [];
    const byId = new Map(list.map((x) => [x.id, x]));
    const path = prksFolderPathLabel(folder.id, byId);
    const title = String(folder && folder.title ? folder.title : 'Folder');
    return path === title ? title : `${title} (${path})`;
}

function prksCollectFolderDescendantIds(folderId, allList) {
    const out = new Set();
    const list = Array.isArray(allList) ? allList : [];
    function walk(id) {
        list
            .filter((x) => x.parent_id === id)
            .forEach((ch) => {
                out.add(ch.id);
                walk(ch.id);
            });
    }
    walk(folderId);
    return out;
}

function prksFolderCollapsedMap() {
    if (!window.__prksFolderTreeCollapsed || typeof window.__prksFolderTreeCollapsed !== 'object') {
        window.__prksFolderTreeCollapsed = {};
    }
    return window.__prksFolderTreeCollapsed;
}

function prksFolderNodeCollapsed(folderId) {
    const m = prksFolderCollapsedMap();
    return m[String(folderId)] !== false;
}

function prksSetFolderNodeCollapsed(folderId, collapsed) {
    const m = prksFolderCollapsedMap();
    m[String(folderId)] = !!collapsed;
}

function prksRerenderFolderDashboard() {
    const st = window.__prksFolderDashboardState;
    if (!st || !st.container) return;
    renderDashboard(st.folders || [], st.container);
}

function prksFolderLibraryFilterFromStorage() {
    try {
        return sessionStorage.getItem(PRKS_FOLDER_LIBRARY_FILTER_KEY) || '';
    } catch (_e) {
        return '';
    }
}

function prksFolderLibraryTreeInnerHtml(list, filterQuery) {
    if (!list || !list.length) {
        return '<p class="prks-inline-message prks-folder-tree__empty">No folders yet. Use <strong>New folder</strong> to create one.</p>';
    }
    return `<div class="prks-folder-tree" role="tree">${renderFolderTreeRoots(list, { filterQuery })}</div>`;
}

function prksRerenderFolderTreeOnly() {
    const st = window.__prksFolderDashboardState;
    if (!st || !st.container) return;
    const host = st.container.querySelector('[data-prks-folder-tree-host]');
    if (host) {
        host.innerHTML = prksFolderLibraryTreeInnerHtml(st.folders, st.filterQuery);
    }
}

function prksToggleFolderNode(folderId) {
    const idRaw = String(folderId || '').trim();
    const id = idRaw ? decodeURIComponent(idRaw) : '';
    if (!id) return;
    prksSetFolderNodeCollapsed(id, !prksFolderNodeCollapsed(id));
    prksRerenderFolderTreeOnly();
}

function prksSetAllFolderNodesCollapsed(folders, collapsed) {
    const list = Array.isArray(folders) ? folders : [];
    list.forEach((f) => {
        if (Number(f && f.child_count ? f.child_count : 0) > 0) {
            prksSetFolderNodeCollapsed(f.id, collapsed);
        }
    });
}

function prksFolderTreeHasCollapsibleNodes(folders) {
    return (Array.isArray(folders) ? folders : []).some(
        (f) => Number(f && f.child_count ? f.child_count : 0) > 0
    );
}

function prksFolderTreeAllCollapsed(folders) {
    const list = Array.isArray(folders) ? folders : [];
    const collapsible = list.filter((f) => Number(f && f.child_count ? f.child_count : 0) > 0);
    if (collapsible.length === 0) return false;
    return collapsible.every((f) => prksFolderNodeCollapsed(f.id));
}

function prksFolderTreeMetaLabel(node) {
    const workCount = Number(node.work_count || 0);
    const childCount = Number(node.child_count || 0);
    const bits = [];
    if (workCount) bits.push(`${workCount} file${workCount === 1 ? '' : 's'}`);
    if (childCount) bits.push(`${childCount} subfolder${childCount === 1 ? '' : 's'}`);
    return bits.join(' · ');
}

function prksOpenFolderModalFromLibrarySearch(query) {
    const pre = String(query || '').trim();
    const titleEl = document.getElementById('folder-title');
    const descEl = document.getElementById('folder-description');
    const parentInputEl = document.getElementById('folder-parent-search');
    const parentHiddenEl = document.getElementById('folder-parent-id');
    if (titleEl) titleEl.value = pre;
    if (descEl) descEl.value = '';
    if (parentInputEl) parentInputEl.value = '';
    if (parentHiddenEl) parentHiddenEl.value = '';
    if (typeof openModal === 'function') openModal('folder-modal');
    if (typeof window.prksRefreshFolderModalValidation === 'function') {
        void window.prksRefreshFolderModalValidation();
    }
}

window.prksOpenFolderModalFromLibrarySearch = prksOpenFolderModalFromLibrarySearch;

function prksFolderTreeEmptySearchHtml(filterQuery) {
    const q = String(filterQuery || '').trim();
    if (!q) {
        return '<p class="prks-inline-message prks-folder-tree__empty">No folders match your search.</p>';
    }
    const label = prksFolderEsc(q);
    const attrQ = prksFolderEsc(q);
    return (
        '<div class="prks-folder-tree__empty-state">' +
        '<p class="prks-inline-message prks-folder-tree__empty">No folders match your search.</p>' +
        `<button type="button" class="add-new-btn prks-folder-tree__create-btn" data-prks-create-folder-query="${attrQ}">Create folder &quot;${label}&quot;</button>` +
        '</div>'
    );
}

function prksBindFolderLibraryCreateFromSearch(container) {
    if (!container || container.dataset.prksCreateFromSearchBound === '1') return;
    container.dataset.prksCreateFromSearchBound = '1';
    container.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-prks-create-folder-query]');
        if (!btn) return;
        e.preventDefault();
        const q = btn.getAttribute('data-prks-create-folder-query') || '';
        prksOpenFolderModalFromLibrarySearch(q);
    });
}

/** @returns {null|Set<string>} null = no filter; empty Set = no matches */
function prksFolderTreeVisibleIds(list, query) {
    const q = String(query || '').trim().toLowerCase();
    if (!q) return null;
    const rows = Array.isArray(list) ? list : [];
    const byId = new Map(rows.map((f) => [f.id, f]));
    const matchIds = new Set();
    rows.forEach((f) => {
        if (String(f.title || '').toLowerCase().includes(q)) matchIds.add(f.id);
    });
    if (matchIds.size === 0) return new Set();

    const visible = new Set();
    matchIds.forEach((id) => {
        visible.add(id);
        prksCollectFolderDescendantIds(id, rows).forEach((d) => visible.add(d));
        let cur = byId.get(id);
        while (cur && cur.parent_id) {
            visible.add(cur.parent_id);
            cur = byId.get(cur.parent_id);
        }
    });
    return visible;
}

function renderFolderTreeRoots(folders, options = {}) {
    const list = Array.isArray(folders) ? folders : [];
    const filterQuery = options.filterQuery != null ? String(options.filterQuery) : '';
    const visibleSet = prksFolderTreeVisibleIds(list, filterQuery);
    const filtering = visibleSet !== null;
    const matchIds = filtering
        ? new Set(
              list
                  .filter((f) => String(f.title || '').toLowerCase().includes(filterQuery.trim().toLowerCase()))
                  .map((f) => f.id)
          )
        : null;

    if (filtering && visibleSet.size === 0) {
        return prksFolderTreeEmptySearchHtml(filterQuery);
    }

    function childrenOf(pid) {
        return list
            .filter((f) => f.parent_id === pid && (!filtering || visibleSet.has(f.id)))
            .sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), undefined, { sensitivity: 'base' }));
    }

    function renderNode(node, depth) {
        const childCount = Number(node.child_count || 0);
        const hasChildren = childCount > 0;
        const collapsed = filtering ? false : hasChildren && prksFolderNodeCollapsed(node.id);
        const expanded = hasChildren && !collapsed;
        const fidEnc = encodeURIComponent(String(node.id || ''));
        const hash = `#/folders/${fidEnc}`;
        const meta = prksFolderTreeMetaLabel(node);
        const metaHtml = meta
            ? `<span class="prks-folder-tree__meta">${prksFolderEsc(meta)}</span>`
            : '<span class="prks-folder-tree__meta" aria-hidden="true"></span>';
        const matchClass =
            filtering && matchIds && matchIds.has(node.id) ? ' prks-folder-tree__row--match' : '';
        const toggleHtml = hasChildren
            ? `<button type="button" class="prks-folder-tree__toggle" aria-expanded="${expanded ? 'true' : 'false'}" title="${
                  collapsed ? 'Expand subfolders' : 'Collapse subfolders'
              }" onclick="event.preventDefault(); event.stopPropagation(); prksToggleFolderNode('${fidEnc}');">${
                  collapsed ? '▸' : '▾'
              }</button>`
            : '<span class="prks-folder-tree__toggle-spacer" aria-hidden="true"></span>';

        let html = `
            <div class="prks-folder-tree__row${matchClass}" role="treeitem" aria-expanded="${hasChildren ? (expanded ? 'true' : 'false') : 'false'}" style="--depth:${depth}">
                ${toggleHtml}
                <a class="prks-folder-tree__link" href="${hash}" data-prks-middleclick-nav="1" onauxclick="return typeof prksMaybeOpenHashInNewTab==='function'&&prksMaybeOpenHashInNewTab(event,'${hash}')">
                    <span class="prks-folder-tree__icon" aria-hidden="true">📁</span>
                    <span class="prks-folder-tree__title">${prksFolderEsc(node.title || 'Folder')}</span>
                </a>
                ${metaHtml}
            </div>`;

        if (expanded) {
            childrenOf(node.id).forEach((child) => {
                html += renderNode(child, depth + 1);
            });
        }
        return html;
    }

    const roots = list
        .filter((f) => !f.parent_id && (!filtering || visibleSet.has(f.id)))
        .sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), undefined, { sensitivity: 'base' }));

    return roots.map((r) => renderNode(r, 0)).join('');
}

const PRKS_FOLDER_LIBRARY_TAB_KEY = 'prks-folder-library-tab';
const PRKS_FOLDER_LIBRARY_FILTER_KEY = 'prks-folder-library-filter';

function prksFolderLibraryActiveTabFromStorage() {
    try {
        const saved = sessionStorage.getItem(PRKS_FOLDER_LIBRARY_TAB_KEY);
        return saved === 'recently-added' ? 'recently-added' : 'folders';
    } catch (_e) {
        return 'folders';
    }
}

function prksFolderLibraryFoldersBodyHtml(list, filterQuery) {
    return `<div class="prks-folder-library__scroll" data-prks-folder-tree-host>${prksFolderLibraryTreeInnerHtml(list, filterQuery)}</div>`;
}

function prksSyncFolderLibrarySearchClear(input, clearBtn) {
    if (!clearBtn) return;
    const hasValue = Boolean(String(input && input.value || '').trim());
    clearBtn.hidden = !hasValue;
    clearBtn.disabled = !hasValue;
}

function prksApplyFolderLibrarySearchFilter(input) {
    const st = window.__prksFolderDashboardState;
    if (!st || !input) return;
    const q = String(input.value || '');
    st.filterQuery = q;
    try {
        sessionStorage.setItem(PRKS_FOLDER_LIBRARY_FILTER_KEY, q);
    } catch (_e) {
        /* ignore */
    }
    prksRerenderFolderTreeOnly();
}

function prksBindFolderLibrarySearch(root) {
    if (!root) return;
    const input = root.querySelector('#prks-folder-library-search');
    const clearBtn = root.querySelector('#prks-folder-library-search-clear');
    if (!input || input.dataset.bound === '1') return;
    input.dataset.bound = '1';
    let debounceTimer;
    const scheduleFilter = () => {
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(() => prksApplyFolderLibrarySearchFilter(input), 150);
    };
    input.addEventListener('input', () => {
        prksSyncFolderLibrarySearchClear(input, clearBtn);
        scheduleFilter();
    });
    if (clearBtn && clearBtn.dataset.bound !== '1') {
        clearBtn.dataset.bound = '1';
        clearBtn.addEventListener('click', () => {
            input.value = '';
            prksSyncFolderLibrarySearchClear(input, clearBtn);
            input.focus();
            prksApplyFolderLibrarySearchFilter(input);
        });
    }
    prksSyncFolderLibrarySearchClear(input, clearBtn);
}

function prksRenderFolderLibraryRecentlyAdded(works, paneEl) {
    if (!paneEl) return;
    let html = '';
    const list = Array.isArray(works) ? works : [];
    if (list.length > 0) {
        list.forEach((w) => {
            const dateStr = w.created_at ? new Date(w.created_at).toLocaleString() : '';
            const subtitle = dateStr ? `Added: ${dateStr}` : '';
            html += typeof prksWorkCardHtml === 'function' ? prksWorkCardHtml(w, { subtitle }) : '';
        });
    } else {
        html = '<p class="prks-inline-message">No files in the library yet.</p>';
    }
    paneEl.innerHTML = html;
}

async function prksLoadFolderLibraryRecentlyAdded(force) {
    const st = window.__prksFolderDashboardState;
    if (!st || !st.container) return;
    const pane = st.container.querySelector('#prks-folder-library-recently-added');
    if (!pane) return;
    if (st.recentlyAddedLoading) return;
    if (!force && Array.isArray(st.recentlyAddedWorks)) {
        prksRenderFolderLibraryRecentlyAdded(st.recentlyAddedWorks, pane);
        if (typeof window.prksInitLazyWorkThumbs === 'function') {
            window.prksInitLazyWorkThumbs(pane);
        }
        return;
    }
    st.recentlyAddedLoading = true;
    const works = typeof fetchRecentlyAdded === 'function' ? await fetchRecentlyAdded() : [];
    st.recentlyAddedWorks = works;
    st.recentlyAddedLoading = false;
    prksRenderFolderLibraryRecentlyAdded(works, pane);
    if (typeof window.prksInitLazyWorkThumbs === 'function') {
        window.prksInitLazyWorkThumbs(pane);
    }
}

function prksFolderLibraryScrollHost() {
    return document.getElementById('main-content') || document.getElementById('page-content');
}

function prksApplyFolderLibraryTabUi(root, tab) {
    if (!root) return;
    const scrollHost = prksFolderLibraryScrollHost();
    const scrollTop = scrollHost ? scrollHost.scrollTop : 0;
    const want = tab === 'recently-added' ? 'recently-added' : 'folders';
    root.querySelectorAll('.prks-folder-library__tab-btn').forEach((btn) => {
        const t = btn.getAttribute('data-tab');
        const on = t === want;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    const foldersPane = root.querySelector('[data-pane="folders"]');
    const addedPane = root.querySelector('[data-pane="recently-added"]');
    if (foldersPane) {
        foldersPane.classList.toggle('is-hidden', want !== 'folders');
        foldersPane.setAttribute('aria-hidden', want === 'folders' ? 'false' : 'true');
    }
    if (addedPane) {
        addedPane.classList.toggle('is-hidden', want !== 'recently-added');
        addedPane.setAttribute('aria-hidden', want === 'recently-added' ? 'false' : 'true');
    }
    const toolbar = root.querySelector('.prks-folder-library__folders-toolbar');
    if (toolbar) toolbar.classList.toggle('is-hidden', want !== 'folders');
    if (scrollHost) scrollHost.scrollTop = scrollTop;
}

function prksSwitchFolderLibraryTab(tab) {
    const st = window.__prksFolderDashboardState;
    if (!st || !st.container) return;
    const want = tab === 'recently-added' ? 'recently-added' : 'folders';
    st.activeTab = want;
    try {
        sessionStorage.setItem(PRKS_FOLDER_LIBRARY_TAB_KEY, want);
    } catch (_e) {
        /* ignore */
    }
    const root = st.container.querySelector('.prks-folder-library');
    prksApplyFolderLibraryTabUi(root, want);
    if (want === 'recently-added') {
        void prksLoadFolderLibraryRecentlyAdded(false);
    }
}

function renderDashboard(folders, container) {
    const prev = window.__prksFolderDashboardState || {};
    const list = Array.isArray(folders) ? folders : [];
    const activeTab = prev.activeTab || prksFolderLibraryActiveTabFromStorage();
    const filterQuery =
        prev.filterQuery != null ? String(prev.filterQuery) : prksFolderLibraryFilterFromStorage();
    const foldersBody = prksFolderLibraryFoldersBodyHtml(list, filterQuery);
    const hasCollapsible = prksFolderTreeHasCollapsibleNodes(list);
    const filterEsc = prksFolderEsc(filterQuery);
    const toolbarActions = hasCollapsible
        ? `<div class="prks-folder-library__toolbar-actions">
            <button type="button" class="ribbon-btn prks-folder-library__toolbar-btn" onclick="prksSetAllFolderNodesCollapsed(window.__prksFolderDashboardState.folders, false); prksRerenderFolderTreeOnly();">Expand all</button>
            <button type="button" class="ribbon-btn prks-folder-library__toolbar-btn" onclick="prksSetAllFolderNodesCollapsed(window.__prksFolderDashboardState.folders, true); prksRerenderFolderTreeOnly();">Collapse all</button>
           </div>`
        : '';
    const foldersActive = activeTab !== 'recently-added';
    container.innerHTML = `
        <div class="prks-folder-library">
        <div class="page-header prks-folder-library__header">
            <h2>Folder Library</h2>
        </div>
        <div class="tabs prks-folder-library__tabs" role="tablist" aria-label="Folder library views">
            <button type="button" class="tab-btn prks-folder-library__tab-btn${foldersActive ? ' active' : ''}" role="tab" data-tab="folders" aria-selected="${foldersActive ? 'true' : 'false'}">Folders</button>
            <button type="button" class="tab-btn prks-folder-library__tab-btn${foldersActive ? '' : ' active'}" role="tab" data-tab="recently-added" aria-selected="${foldersActive ? 'false' : 'true'}">Recently added</button>
        </div>
        <div class="prks-folder-library__folders-toolbar${foldersActive ? '' : ' is-hidden'}">
            <div class="tag-add-shell tag-add-shell--flush prks-folder-library__search">
                <div class="tag-add-shell__field">
                    <span class="tag-add-shell__icon" aria-hidden="true">🔍</span>
                    <input type="text" id="prks-folder-library-search" class="tag-add-shell__input" placeholder="Search folders…" value="${filterEsc}" maxlength="300" autocomplete="off" aria-label="Filter folders">
                    <button type="button" class="tag-add-shell__clear" id="prks-folder-library-search-clear" aria-label="Clear search" title="Clear search" hidden>&times;</button>
                </div>
            </div>
            ${toolbarActions}
        </div>
        <div class="prks-folder-library__body">
            <div class="prks-folder-library__pane${foldersActive ? '' : ' is-hidden'}" data-pane="folders" role="tabpanel" aria-hidden="${foldersActive ? 'false' : 'true'}">
                ${foldersBody}
            </div>
            <div class="prks-folder-library__pane prks-folder-library__pane--added${foldersActive ? ' is-hidden' : ''}" data-pane="recently-added" role="tabpanel" aria-hidden="${foldersActive ? 'true' : 'false'}">
                <div class="prks-folder-library__scroll prks-folder-library__scroll--added">
                    <div id="prks-folder-library-recently-added" class="prks-folder-library__grid card-grid"></div>
                </div>
            </div>
        </div>
        </div>
    `;
    window.__prksFolderDashboardState = {
        folders: list,
        container,
        activeTab,
        filterQuery,
        recentlyAddedWorks: prev.recentlyAddedWorks,
        recentlyAddedLoading: false,
    };
    const root = container.querySelector('.prks-folder-library');
    prksBindFolderLibrarySearch(root);
    prksBindFolderLibraryCreateFromSearch(root);
    root.querySelectorAll('.prks-folder-library__tab-btn').forEach((btn) => {
        if (btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', () => {
            prksSwitchFolderLibraryTab(btn.getAttribute('data-tab'));
        });
    });
    if (activeTab === 'recently-added') {
        void prksLoadFolderLibraryRecentlyAdded(false);
    }
}

function renderFolderDetails(folder, container) {
    if (!folder) {
        container.innerHTML = '<p class="prks-inline-message prks-inline-message--error">Folder not found.</p>';
        return;
    }
    window.currentFolder = folder;
    const hasChildren = Array.isArray(folder.children) && folder.children.length > 0;
    const canDelete = (!folder.works || folder.works.length === 0) && !hasChildren;

    let worksHtml = `<div class="card-grid">`;
    if (folder.works && folder.works.length > 0) {
        folder.works.forEach((w) => {
            worksHtml += typeof prksWorkCardHtml === 'function' ? prksWorkCardHtml(w) : '';
        });
    }
    worksHtml += `</div>`;
    const subfolders = Array.isArray(folder.children) ? folder.children : [];
    const subfoldersHtml = subfolders.length
        ? `
            <div class="page-header"><h3>Subfolders</h3></div>
            <div class="list-view">
                ${subfolders
                    .map((ch) => {
                        const workCount = Number(ch && ch.work_count ? ch.work_count : 0);
                        const childCount = Number(ch && ch.child_count ? ch.child_count : 0);
                        const bits = [];
                        if (workCount) bits.push(`${workCount} file${workCount === 1 ? '' : 's'}`);
                        if (childCount) bits.push(`${childCount} subfolder${childCount === 1 ? '' : 's'}`);
                        return `
                            <div class="project-card" role="link" tabindex="0" data-prks-middleclick-nav="1"
                                onclick="window.location.hash='#/folders/${encodeURIComponent(String(ch.id || ''))}'"
                                onkeydown="if(event && (event.key==='Enter' || event.key===' ')){event.preventDefault(); this.click();}">
                                <span class="status-badge Planned">Subfolder</span>
                                <div class="card-title">${prksFolderEsc(ch.title || 'Folder')}</div>
                                <p class="meta-row">${prksFolderEsc(bits.join(' · '))}</p>
                            </div>
                        `;
                    })
                    .join('')}
            </div>
        `
        : '';

    container.innerHTML = `
        <div class="page-header page-header--split">
            <h2>📁 ${prksFolderEsc(folder.title)}</h2>
            ${canDelete ? `<button data-delete-folder-id="${encodeURIComponent(String(folder.id || ''))}" class="btn-danger-outline">🗑 Delete Folder</button>` : ''}
        </div>
        <p class="mb-md">${prksFolderEsc(folder.description || 'No description provided.')}</p>
        ${subfoldersHtml}
        <div class="page-header"><h3>Files</h3></div>
        ${worksHtml}
    `;
    if (typeof window.prksInitLazyWorkThumbs === 'function') {
        window.prksInitLazyWorkThumbs(container);
    }
    const delBtn = container.querySelector('[data-delete-folder-id]');
    if (delBtn) {
        delBtn.addEventListener('click', () => {
            const encodedId = delBtn.getAttribute('data-delete-folder-id') || '';
            void deleteFolder(decodeURIComponent(encodedId));
        });
    }
    setTimeout(() => {
        const select = document.getElementById('work-folder-id');
        if (select) select.value = folder.id;
    }, 100);
}

async function prksRemoveFolderTag(folderId, tagId) {
    try {
        const res = await fetch(
            `/api/folders/${encodeURIComponent(folderId)}/tags/${encodeURIComponent(tagId)}`,
            { method: 'DELETE' }
        );
        if (!res.ok) throw new Error(`Server error ${res.status}`);
        window.__prksAllTagsCache = null;
        await prksReloadEntityTagsUI('folder', folderId);
    } catch (e) {
        console.error(e);
        alert('Could not remove tag.');
    }
}

async function deleteFolder(f_id) {
    if (confirm("Are you sure you want to delete this empty folder?")) {
        try {
            const res = await fetch('/api/folders/' + encodeURIComponent(f_id), { method: 'DELETE' });
            if (res.ok) {
                window.location.hash = '#/folders';
            } else {
                const text = await res.text();
                alert("Error deleting folder: " + text);
            }
        } catch (e) { alert("Error deleting folder!"); }
    }
}

function prksNormFolderTitleKey(s) {
    return String(s || '')
        .trim()
        .toLowerCase();
}

function prksWorkFolderEsc(s) {
    if (s == null || s === '') return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function renderFolderAttachControlsHtml(work) {
    const wid = work && work.id ? String(work.id) : '';
    if (!wid) return '';
    const current = work && work.folder_id ? String(work.folder_id) : '';
    const currentTitle = work && work.folder_title ? String(work.folder_title) : '';
    const editing =
        window.__prksWorkFolderEdit &&
        typeof window.__prksWorkFolderEdit === 'object' &&
        window.__prksWorkFolderEdit[wid] === true;
    const currentSummary = current
        ? `<span class="meta-row">Folder:</span> <a href="#/folders/${encodeURIComponent(
              current
          )}" class="route-sidebar__link">${prksWorkFolderEsc(currentTitle || current)}</a>`
        : '<span class="meta-row">Not in a folder</span>';
    const currentLineExpanded = current
        ? `Now in <a href="#/folders/${encodeURIComponent(current)}" class="route-sidebar__link">${prksWorkFolderEsc(
              currentTitle || current
          )}</a>. Choose another folder below or clear.`
        : 'Search for a folder, then set or leave cleared.';

    const summaryTitleAttr = current ? ` title="${prksWorkFolderEsc(currentTitle || current)}"` : '';

    if (!editing) {
        return `
        <div class="doc-meta-card prks-work-folder-card prks-work-folder-card--compact">
            <div class="card-heading-row">
                <span class="prks-work-folder-summary"${summaryTitleAttr}>
                    ${currentSummary}
                </span>
                <button type="button" class="ribbon-btn ribbon-btn--sm form-actions__btn" id="prks-work-folder-edit-btn" aria-expanded="false">Edit</button>
            </div>
        </div>
    `;
    }

    return `
        <div class="doc-meta-card prks-work-folder-card">
            <div class="card-heading-row">
                <h3>Folder</h3>
                <button type="button" class="ribbon-btn ribbon-btn--sm form-actions__btn" id="prks-work-folder-edit-btn" aria-expanded="true">Done</button>
            </div>
            <p class="meta-row meta-row--spaced">${currentLineExpanded}</p>
            <div class="tag-add-shell combobox-container">
                <div class="tag-add-shell__field">
                    <span class="tag-add-shell__icon" aria-hidden="true">＋</span>
                    <input type="text" id="prks-work-folder-search" class="tag-add-shell__input" placeholder="Search folders…" maxlength="300" autocomplete="off" aria-label="Search folders">
                    <input type="hidden" id="prks-work-folder-id" value="${prksWorkFolderEsc(current)}">
                </div>
                <div id="prks-work-folder-results" class="combobox-results combobox-results--tag-panel hidden"></div>
            </div>
            <div class="prks-work-folder-controls">
                <button type="button" class="add-new-btn" id="prks-work-folder-set-btn">Set folder</button>
                <button type="button" class="ribbon-btn form-actions__btn" id="prks-work-folder-clear-btn">Clear</button>
                <button type="button" class="ribbon-btn form-actions__btn" id="prks-work-folder-new-btn">New...</button>
            </div>
            <p id="prks-work-folder-status" class="meta-row meta-row--spaced" aria-live="polite"></p>
        </div>
    `;
}

async function mountFolderAttachControlsForWork(work) {
    const wid = work && work.id ? String(work.id) : '';
    if (!wid) return;
    const editBtn = document.getElementById('prks-work-folder-edit-btn');
    const status = document.getElementById('prks-work-folder-status');
    if (editBtn && editBtn.dataset.bound !== '1') {
        editBtn.dataset.bound = '1';
        editBtn.onclick = () => {
            if (!window.__prksWorkFolderEdit || typeof window.__prksWorkFolderEdit !== 'object') {
                window.__prksWorkFolderEdit = {};
            }
            window.__prksWorkFolderEdit[wid] = !(window.__prksWorkFolderEdit[wid] === true);
            if (typeof updatePanelContent === 'function') updatePanelContent('details');
        };
    }

    const editing =
        window.__prksWorkFolderEdit &&
        typeof window.__prksWorkFolderEdit === 'object' &&
        window.__prksWorkFolderEdit[wid] === true;
    if (!editing) return;

    const input = document.getElementById('prks-work-folder-search');
    const hidden = document.getElementById('prks-work-folder-id');
    const results = document.getElementById('prks-work-folder-results');
    const setBtn = document.getElementById('prks-work-folder-set-btn');
    const clearBtn = document.getElementById('prks-work-folder-clear-btn');
    const newBtn = document.getElementById('prks-work-folder-new-btn');
    if (!input || !hidden || !results || !setBtn || !clearBtn || !newBtn) return;

    let folderRows = await fetchFolders();
    if (!Array.isArray(folderRows)) folderRows = [];

    if (work && work.folder_id && work.folder_title && !String(input.value || '').trim()) {
        input.value = String(work.folder_title);
        hidden.value = String(work.folder_id);
    }

    async function assignToNewFolderAndRefresh(newFolderId, message) {
        if (typeof patchWorkFolder !== 'function') return;
        await patchWorkFolder(wid, newFolderId);
        folderRows = await fetchFolders();
        if (!Array.isArray(folderRows)) folderRows = [];
        if (status) status.textContent = message || 'Folder set.';
        if (typeof fetchWorkDetails === 'function') {
            window.currentWork = await fetchWorkDetails(wid);
            if (!window.__prksWorkFolderEdit || typeof window.__prksWorkFolderEdit !== 'object') {
                window.__prksWorkFolderEdit = {};
            }
            window.__prksWorkFolderEdit[wid] = false;
            if (typeof updatePanelContent === 'function') updatePanelContent('details');
        }
    }

    function renderDropdown() {
        const rawQ = String(input.value || '').trim();
        const q = rawQ.toLowerCase();
        const filtered = !q
            ? folderRows.slice(0, 40)
            : folderRows
                  .filter((f) => {
                      const label = prksFolderRowLabel(f, folderRows).toLowerCase();
                      return label.includes(q) || String(f.title || '').toLowerCase().includes(q);
                  })
                  .slice(0, 40);
        const keyQ = prksNormFolderTitleKey(rawQ);
        const exactExists =
            keyQ &&
            folderRows.some((f) => prksNormFolderTitleKey(f.title) === keyQ);
        results.innerHTML = '';
        if (keyQ && !exactExists) {
            const c = document.createElement('div');
            c.className = 'result-item result-item--create';
            c.textContent = 'Create folder "' + rawQ + '"';
            c.onmousedown = (ev) => {
                ev.preventDefault();
                void (async () => {
                    try {
                        if (typeof createFolder !== 'function') return;
                        const newId = await createFolder(rawQ, '');
                        await assignToNewFolderAndRefresh(newId, 'Folder created and set.');
                    } catch (e) {
                        if (status) status.textContent = String((e && e.message) || 'Could not create folder.');
                    }
                })();
            };
            results.appendChild(c);
        }
        if (filtered.length === 0) {
            if (!keyQ || exactExists) {
                const empty = document.createElement('div');
                empty.className = 'result-item no-results';
                empty.textContent = 'No folders found';
                results.appendChild(empty);
            }
        } else {
            for (const f of filtered) {
                const div = document.createElement('div');
                div.className = 'result-item';
                div.textContent = prksFolderRowLabel(f, folderRows);
                div.onmousedown = (ev) => {
                    ev.preventDefault();
                    input.value = prksFolderRowLabel(f, folderRows);
                    hidden.value = f.id;
                    results.classList.add('hidden');
                };
                results.appendChild(div);
            }
        }
        results.classList.remove('hidden');
    }

    newBtn.onclick = () => {
        window.__prksPendingWorkFolderAttach = { workId: wid };
        const titleEl = document.getElementById('folder-title');
        const descEl = document.getElementById('folder-description');
        const parentInputEl = document.getElementById('folder-parent-search');
        const parentHiddenEl = document.getElementById('folder-parent-id');
        const pre = String(input.value || '').trim();
        if (titleEl) titleEl.value = pre;
        if (descEl) descEl.value = '';
        if (parentInputEl) parentInputEl.value = '';
        if (parentHiddenEl) parentHiddenEl.value = '';
        if (typeof openModal === 'function') openModal('folder-modal');
        if (typeof window.prksRefreshFolderModalValidation === 'function') {
            void window.prksRefreshFolderModalValidation();
        }
    };

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
            if (typeof patchWorkFolder !== 'function') return;
            await patchWorkFolder(wid, pid);
            if (status) status.textContent = 'Folder updated.';
            if (typeof fetchWorkDetails === 'function') {
                window.currentWork = await fetchWorkDetails(wid);
                if (!window.__prksWorkFolderEdit || typeof window.__prksWorkFolderEdit !== 'object') {
                    window.__prksWorkFolderEdit = {};
                }
                window.__prksWorkFolderEdit[wid] = false;
                if (typeof updatePanelContent === 'function') updatePanelContent('details');
            }
        } catch (e) {
            if (status) status.textContent = String((e && e.message) || 'Could not set folder.');
        }
    };

    clearBtn.onclick = async () => {
        try {
            if (typeof patchWorkFolder !== 'function') return;
            await patchWorkFolder(wid, null);
            input.value = '';
            hidden.value = '';
            if (status) status.textContent = 'Removed from folder.';
            if (typeof fetchWorkDetails === 'function') {
                window.currentWork = await fetchWorkDetails(wid);
                if (typeof updatePanelContent === 'function') updatePanelContent('details');
            }
        } catch (e) {
            if (status) status.textContent = String((e && e.message) || 'Could not clear.');
        }
    };
}

window.renderFolderAttachControlsHtml = renderFolderAttachControlsHtml;
window.mountFolderAttachControlsForWork = mountFolderAttachControlsForWork;
window.prksFolderRowLabel = prksFolderRowLabel;
window.prksCollectFolderDescendantIds = prksCollectFolderDescendantIds;
window.prksToggleFolderNode = prksToggleFolderNode;
window.prksSetAllFolderNodesCollapsed = prksSetAllFolderNodesCollapsed;
window.prksRerenderFolderDashboard = prksRerenderFolderDashboard;
