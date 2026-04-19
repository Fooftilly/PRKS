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

function prksToggleFolderNode(folderId) {
    const idRaw = String(folderId || '').trim();
    const id = idRaw ? decodeURIComponent(idRaw) : '';
    if (!id) return;
    prksSetFolderNodeCollapsed(id, !prksFolderNodeCollapsed(id));
    prksRerenderFolderDashboard();
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

function renderFolderTreeRoots(folders) {
    const list = Array.isArray(folders) ? folders : [];
    const roots = list.filter((f) => !f.parent_id);
    function childrenOf(pid) {
        return list
            .filter((f) => f.parent_id === pid)
            .sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), undefined, { sensitivity: 'base' }));
    }
    function renderNode(node, depth) {
        const pad = depth ? ` style="margin-left:${Math.min(depth, 8) * 10}px"` : '';
        const workCount = Number(node.work_count || 0);
        const childCount = Number(node.child_count || 0);
        const hasChildren = childCount > 0;
        const collapsed = hasChildren ? prksFolderNodeCollapsed(node.id) : false;
        const collapseBtn = hasChildren
            ? `<button type="button" class="ribbon-btn form-actions__btn" style="margin-top:0;min-width:28px;padding-left:8px;padding-right:8px;" title="${
                  collapsed ? 'Expand subfolders' : 'Collapse subfolders'
              }" onclick="event.stopPropagation(); prksToggleFolderNode('${encodeURIComponent(String(node.id || ''))}');">${
                  collapsed ? '▸' : '▾'
              }</button>`
            : '';
        const bits = [];
        if (workCount) bits.push(`${workCount} file${workCount === 1 ? '' : 's'}`);
        if (childCount) bits.push(`${childCount} subfolder${childCount === 1 ? '' : 's'}`);
        const meta = bits.length
            ? `<p class="meta-row" style="font-size:0.8rem;">${prksFolderEsc(bits.join(' · '))}</p>`
            : '';
        let html = `
            <div class="prks-folder-tree-row"${pad} style="display:flex;align-items:stretch;gap:6px;">
                ${collapseBtn}
                <div class="project-card prks-folder-tree-card" style="flex:1;" role="link" tabindex="0" data-prks-middleclick-nav="1"
                onclick="window.location.hash='#/folders/${encodeURIComponent(String(node.id || ''))}'"
                onkeydown="if(event && (event.key==='Enter' || event.key===' ')){event.preventDefault(); this.click();}">
                <span class="status-badge Planned">Folder</span>
                <div class="card-title">${prksFolderEsc(node.title || 'Folder')}</div>
                ${meta}
                </div>
            </div>
        `;
        if (!collapsed) {
            childrenOf(node.id).forEach((child) => {
                html += renderNode(child, depth + 1);
            });
        }
        return html;
    }
    return roots
        .sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), undefined, { sensitivity: 'base' }))
        .map((r) => renderNode(r, 0))
        .join('');
}

function renderDashboard(folders, container) {
    const list = Array.isArray(folders) ? folders : [];
    window.__prksFolderDashboardState = { folders: list, container };
    const body = list.length
        ? `<div class="list-view prks-folder-library__list">${renderFolderTreeRoots(list)}</div>`
        : '<p class="meta-row" style="padding:12px 4px;">No folders yet. Use <strong>New folder</strong> to create one.</p>';
    const hasCollapsible = prksFolderTreeHasCollapsibleNodes(list);
    const allCollapsed = prksFolderTreeAllCollapsed(list);
    const controls = hasCollapsible
        ? `
            <button type="button" class="ribbon-btn" style="margin-top:0;" onclick="prksSetAllFolderNodesCollapsed(window.__prksFolderDashboardState.folders, ${
                allCollapsed ? 'false' : 'true'
            }); prksRerenderFolderDashboard();">${allCollapsed ? 'Expand all subfolders' : 'Collapse all subfolders'}</button>
        `
        : '';
    container.innerHTML = `
        <div class="prks-folder-library">
        <div class="page-header" style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2>Folder Library</h2>
            ${controls}
        </div>
        <p class="meta-row" style="padding:2px 4px 10px;">Folders can nest. Move folders under other folders or keep them top-level.</p>
        ${body}
        </div>
    `;
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
