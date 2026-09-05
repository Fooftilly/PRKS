function escapeHtmlGroup(s) {
    if (typeof window.prksEscapeHtml === 'function') return window.prksEscapeHtml(s);
    if (s == null) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function groupPathLabel(groupId, byId) {
    const parts = [];
    let cur = byId.get(groupId);
    const guard = new Set();
    while (cur && !guard.has(cur.id)) {
        guard.add(cur.id);
        parts.unshift(cur.name);
        cur = cur.parent_id ? byId.get(cur.parent_id) : null;
    }
    return parts.join(' → ');
}

function prksGroupRowLabel(g, allList) {
    const byId = new Map((allList || []).map((x) => [x.id, x]));
    const path = groupPathLabel(g.id, byId);
    return path === g.name ? g.name : `${g.name} (${path})`;
}

async function prksEnsureAllGroupsCache() {
    window.allGroups = await fetchPersonGroups();
    return window.allGroups || [];
}

/**
 * Searchable group picker: sets hidden id when user picks a row; clears hidden when typing.
 * excludedIds: Set of group ids not shown (e.g. self + descendants when editing).
 */
function prksBindGroupSearchCombobox(inputId, resultsId, hiddenId, excludedIds) {
    const input = document.getElementById(inputId);
    const results = document.getElementById(resultsId);
    const hidden = document.getElementById(hiddenId);
    if (!input || !results || !hidden) return;

    const excluded =
        excludedIds instanceof Set ? excludedIds : new Set(Array.isArray(excludedIds) ? excludedIds : []);

    function renderList() {
        const list = window.allGroups || [];
        const byId = new Map(list.map((x) => [x.id, x]));
        const val = (input.value || '').toLowerCase().trim();
        const filtered = list.filter((g) => {
            if (excluded.has(g.id)) return false;
            const label = prksGroupRowLabel(g, list).toLowerCase();
            return !val || label.includes(val) || String(g.name || '').toLowerCase().includes(val);
        });
        results.innerHTML = '';
        if (filtered.length === 0) {
            results.innerHTML =
                '<div class="result-item no-results">No matching groups — type a new name to create the parent when you save.</div>';
        } else {
            filtered.slice(0, 80).forEach((g) => {
                const div = document.createElement('div');
                div.className = 'result-item';
                div.textContent = prksGroupRowLabel(g, list);
                div.onmousedown = (e) => {
                    e.preventDefault();
                    hidden.value = g.id;
                    input.value = prksGroupRowLabel(g, list);
                    prksHideInlineComboboxResults(results);
                };
                results.appendChild(div);
            });
        }
        if (typeof prksShowInlineComboboxResults === 'function') {
            prksShowInlineComboboxResults(input, results);
        } else {
            results.classList.remove('hidden');
        }
    }

    input.onfocus = () => {
        void prksEnsureAllGroupsCache().then(renderList);
    };
    input.oninput = () => {
        hidden.value = '';
        void prksEnsureAllGroupsCache().then(renderList);
    };
    input.onblur = () => {
        setTimeout(() => prksHideInlineComboboxResults(results), 200);
    };
}

/** New group modal: load cache, clear fields, bind parent search. */
async function prksInitNewGroupModal() {
    document.getElementById('group-parent-search') &&
        (document.getElementById('group-parent-search').value = '');
    const hid = document.getElementById('group-parent-id');
    if (hid) hid.value = '';
    await prksEnsureAllGroupsCache();
    prksBindGroupSearchCombobox('group-parent-search', 'group-parent-results', 'group-parent-id', new Set());
}

window.prksInitNewGroupModal = prksInitNewGroupModal;

const PRKS_GROUP_LIBRARY_FILTER_KEY = 'prks-group-library-filter';

function prksGroupCollapsedMap() {
    if (!window.__prksGroupTreeCollapsed || typeof window.__prksGroupTreeCollapsed !== 'object') {
        window.__prksGroupTreeCollapsed = {};
    }
    return window.__prksGroupTreeCollapsed;
}

function prksGroupNodeCollapsed(groupId) {
    const m = prksGroupCollapsedMap();
    return m[String(groupId)] !== false;
}

function prksSetGroupNodeCollapsed(groupId, collapsed) {
    const m = prksGroupCollapsedMap();
    m[String(groupId)] = !!collapsed;
}

function prksGroupTreeIdCssEscape(groupId) {
    const id = String(groupId || '');
    return typeof CSS !== 'undefined' && typeof CSS.escape === 'function' ? CSS.escape(id) : id.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function prksGroupTreeHost(root) {
    return root ? root.querySelector('[data-prks-group-tree-host]') : null;
}

function prksGroupTreeMetaLabel(node) {
    const mc = node.member_count != null ? Number(node.member_count) : 0;
    const cc = node.child_count != null ? Number(node.child_count) : 0;
    const bits = [];
    if (mc) bits.push(`${mc} member${mc === 1 ? '' : 's'}`);
    if (cc) bits.push(`${cc} subgroup${cc === 1 ? '' : 's'}`);
    return bits.join(' · ');
}

function prksGroupTreeHasCollapsibleNodes(groups) {
    const list = Array.isArray(groups) ? groups : [];
    return list.some(
        (g) => Number(g.child_count || 0) > 0 || list.some((x) => x.parent_id === g.id)
    );
}

function prksGroupTreeAllCollapsed(groups) {
    const list = Array.isArray(groups) ? groups : [];
    const collapsible = list.filter((g) => {
        const cc = Number(g.child_count || 0);
        if (cc > 0) return true;
        return list.some((x) => x.parent_id === g.id);
    });
    if (collapsible.length === 0) return false;
    return collapsible.every((g) => prksGroupNodeCollapsed(g.id));
}

function prksSetAllGroupNodesCollapsed(groups, collapsed) {
    const list = Array.isArray(groups) ? groups : [];
    list.forEach((g) => {
        const hasKids = Number(g.child_count || 0) > 0 || list.some((x) => x.parent_id === g.id);
        if (hasKids) prksSetGroupNodeCollapsed(g.id, collapsed);
    });
}

function prksSyncGroupTreeBranchUi(host, groupId, collapsed) {
    if (!host || groupId == null || groupId === '') return;
    const idEsc = prksGroupTreeIdCssEscape(groupId);
    const branch = host.querySelector(`.prks-group-tree__branch[data-group-id="${idEsc}"]`);
    if (!branch) return;
    branch.classList.toggle('is-collapsed', !!collapsed);
    const row = host.querySelector(`.prks-group-tree__row[data-group-id="${idEsc}"]`);
    if (!row) return;
    const expanded = !collapsed;
    row.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    const toggle = row.querySelector('.prks-group-tree__toggle');
    if (toggle) {
        toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        toggle.setAttribute('title', collapsed ? 'Expand subgroups' : 'Collapse subgroups');
    }
}

function prksSyncAllGroupTreeBranchesUi(host, groups) {
    if (!host || !Array.isArray(groups)) return;
    groups.forEach((g) => {
        const hasKids = Number(g.child_count || 0) > 0 || groups.some((x) => x.parent_id === g.id);
        if (hasKids) prksSyncGroupTreeBranchUi(host, g.id, prksGroupNodeCollapsed(g.id));
    });
}

function prksGroupLibraryExpandToggleLabel(groups) {
    return prksGroupTreeAllCollapsed(groups) ? 'Expand all' : 'Collapse all';
}

function prksGroupLibraryExpandToggleInnerHtml() {
    const iconHtml = typeof prksIcon === 'function' ? prksIcon('chevronDown', { size: 'sm' }) : '▾';
    return `<span class="ribbon-btn__icon">${iconHtml}</span>`;
}

function prksUpdateGroupLibraryExpandToggleBtn(root) {
    const st = root && root.__prksGroupLibraryState;
    if (!st) return;
    const btn = root.querySelector('#prks-group-library-expand-toggle');
    if (!btn) return;
    const filtering = Boolean(String(st.filterQuery || '').trim());
    btn.hidden = filtering;
    btn.disabled = filtering;
    if (filtering) return;
    const label = prksGroupLibraryExpandToggleLabel(st.groups);
    const allCollapsed = prksGroupTreeAllCollapsed(st.groups);
    if (!btn.querySelector('.ribbon-btn__icon')) {
        btn.innerHTML = prksGroupLibraryExpandToggleInnerHtml();
        if (typeof prksRefreshIcons === 'function') prksRefreshIcons(btn);
    }
    btn.classList.toggle('is-collapse-all', !allCollapsed);
    btn.setAttribute('aria-label', label);
    btn.setAttribute('title', label);
}

function prksToggleAllGroupNodes(control) {
    const root = control && control.closest ? control.closest('.prks-group-library') : null;
    const st = root && root.__prksGroupLibraryState;
    if (!st || !Array.isArray(st.groups) || String(st.filterQuery || '').trim()) return;
    const allCollapsed = prksGroupTreeAllCollapsed(st.groups);
    prksSetAllGroupNodesCollapsed(st.groups, !allCollapsed);
    const host = prksGroupTreeHost(root);
    if (host) {
        prksSyncAllGroupTreeBranchesUi(host, st.groups);
        prksUpdateGroupLibraryExpandToggleBtn(root);
    } else {
        prksRerenderGroupTreeOnly(root);
    }
}

function prksToggleGroupNode(groupId, control) {
    const root = control && control.closest ? control.closest('.prks-group-library') : null;
    const st = root && root.__prksGroupLibraryState;
    if (!st || String(st.filterQuery || '').trim()) return;
    const idRaw = String(groupId || '').trim();
    const id = idRaw ? decodeURIComponent(idRaw) : '';
    if (!id) return;
    prksSetGroupNodeCollapsed(id, !prksGroupNodeCollapsed(id));
    const collapsed = prksGroupNodeCollapsed(id);
    const host = prksGroupTreeHost(root);
    if (host) {
        prksSyncGroupTreeBranchUi(host, id, collapsed);
        prksUpdateGroupLibraryExpandToggleBtn(root);
    } else {
        prksRerenderGroupTreeOnly(root);
    }
}

window.prksToggleGroupNode = prksToggleGroupNode;
window.prksToggleAllGroupNodes = prksToggleAllGroupNodes;

function prksGroupLibraryFilterFromStorage() {
    try {
        return sessionStorage.getItem(PRKS_GROUP_LIBRARY_FILTER_KEY) || '';
    } catch (_e) {
        return '';
    }
}

function prksGroupTreeVisibleIds(list, query) {
    const q = String(query || '').trim().toLowerCase();
    if (!q) return null;
    const rows = Array.isArray(list) ? list : [];
    const byId = new Map(rows.map((g) => [g.id, g]));
    const matchIds = new Set();
    rows.forEach((g) => {
        if (String(g.name || '').toLowerCase().includes(q)) matchIds.add(g.id);
    });
    if (matchIds.size === 0) return new Set();

    const visible = new Set();
    matchIds.forEach((id) => {
        visible.add(id);
        prksCollectDescendantIds(id, rows).forEach((d) => visible.add(d));
        let cur = byId.get(id);
        while (cur && cur.parent_id) {
            visible.add(cur.parent_id);
            cur = byId.get(cur.parent_id);
        }
    });
    return visible;
}

function prksGroupTreeEmptySearchHtml() {
    return '<p class="prks-inline-message prks-group-tree__empty">No groups match your search.</p>';
}

function prksGroupLibraryTreeInnerHtml(list, filterQuery) {
    if (!list || !list.length) {
        return '<div class="prks-group-library__empty-state"><p class="prks-inline-message prks-group-tree__empty">No Person Groups yet.</p><button type="button" class="prks-btn prks-btn--primary" onclick="openModal(\'group-modal\')">New Group</button></div>';
    }
    return `<div class="prks-group-tree" role="tree">${renderGroupTreeRoots(list, { filterQuery })}</div>`;
}

function prksRerenderGroupTreeOnly(root) {
    const st = root && root.__prksGroupLibraryState;
    if (!st) return;
    const host = root.querySelector('[data-prks-group-tree-host]');
    if (host) {
        host.innerHTML = prksGroupLibraryTreeInnerHtml(st.groups, st.filterQuery);
        if (typeof prksRefreshIcons === 'function') prksRefreshIcons(host);
    }
    prksUpdateGroupLibraryExpandToggleBtn(root);
}

function prksSyncGroupLibrarySearchClear(input, clearBtn) {
    if (!clearBtn) return;
    const hasValue = Boolean(String((input && input.value) || '').trim());
    clearBtn.hidden = !hasValue;
    clearBtn.disabled = !hasValue;
}

function prksApplyGroupLibrarySearchFilter(input) {
    const root = input && input.closest('.prks-group-library');
    const st = root && root.__prksGroupLibraryState;
    if (!st || !input) return;
    const q = String(input.value || '');
    st.filterQuery = q;
    try {
        sessionStorage.setItem(PRKS_GROUP_LIBRARY_FILTER_KEY, q);
    } catch (_e) {
        /* ignore */
    }
    prksRerenderGroupTreeOnly(root);
}

function prksBindGroupLibrarySearch(root) {
    if (!root) return;
    const input = root.querySelector('#prks-group-library-search');
    const clearBtn = root.querySelector('#prks-group-library-search-clear');
    if (!input || input.dataset.bound === '1') return;
    input.dataset.bound = '1';
    let debounceTimer;
    const scheduleFilter = () => {
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(() => prksApplyGroupLibrarySearchFilter(input), 150);
    };
    input.addEventListener('input', () => {
        prksSyncGroupLibrarySearchClear(input, clearBtn);
        scheduleFilter();
    });
    if (clearBtn && clearBtn.dataset.bound !== '1') {
        clearBtn.dataset.bound = '1';
        clearBtn.addEventListener('click', () => {
            input.value = '';
            prksSyncGroupLibrarySearchClear(input, clearBtn);
            input.focus();
            prksApplyGroupLibrarySearchFilter(input);
        });
    }
    prksSyncGroupLibrarySearchClear(input, clearBtn);
}

function renderGroupTreeRoots(groups, options = {}) {
    const list = Array.isArray(groups) ? groups : [];
    const filterQuery = options.filterQuery != null ? String(options.filterQuery) : '';
    const visibleSet = prksGroupTreeVisibleIds(list, filterQuery);
    const filtering = visibleSet !== null;
    const matchIds = filtering
        ? new Set(
              list
                  .filter((g) =>
                      String(g.name || '')
                          .toLowerCase()
                          .includes(filterQuery.trim().toLowerCase())
                  )
                  .map((g) => g.id)
          )
        : null;

    if (filtering && visibleSet.size === 0) {
        return prksGroupTreeEmptySearchHtml();
    }

    function childrenOf(pid) {
        return list
            .filter((g) => g.parent_id === pid && (!filtering || visibleSet.has(g.id)))
            .sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), undefined, { sensitivity: 'base' }));
    }

    function renderNode(node, depth) {
        const kids = childrenOf(node.id);
        const hasChildren = kids.length > 0;
        const collapsed = filtering ? false : hasChildren && prksGroupNodeCollapsed(node.id);
        const expanded = hasChildren && !collapsed;
        const gidEnc = encodeURIComponent(String(node.id || ''));
        const hash = `#/people/groups/${gidEnc}`;
        const meta = prksGroupTreeMetaLabel(node);
        const metaHtml = meta
            ? `<span class="prks-group-tree__meta">${escapeHtmlGroup(meta)}</span>`
            : '<span class="prks-group-tree__meta" aria-hidden="true"></span>';
        const matchClass =
            filtering && matchIds && matchIds.has(node.id) ? ' prks-group-tree__row--match' : '';
        const nodeIdAttr = escapeHtmlGroup(String(node.id || ''));
        const collapsedClass = collapsed ? ' is-collapsed' : '';
        const toggleHtml = hasChildren
            ? filtering
                ? '<span class="prks-group-tree__toggle-spacer" aria-hidden="true"></span>'
                : `<button type="button" class="prks-group-tree__toggle" aria-expanded="${expanded ? 'true' : 'false'}" title="${
                  collapsed ? 'Expand subgroups' : 'Collapse subgroups'
              }" onclick="event.preventDefault(); event.stopPropagation(); prksToggleGroupNode('${gidEnc}', this);">${
                  typeof prksIcon === 'function' ? prksIcon('chevronRight', { size: 14 }) : '▸'
              }</button>`
            : '<span class="prks-group-tree__toggle-spacer" aria-hidden="true"></span>';

        let html = `
            <div class="prks-group-tree__row${matchClass}" data-group-id="${nodeIdAttr}" role="treeitem" aria-expanded="${hasChildren ? (expanded ? 'true' : 'false') : 'false'}" style="--depth:${depth}">
                ${toggleHtml}
                <a class="prks-group-tree__link" href="${hash}">
                    <span class="prks-group-tree__icon">${typeof prksIcon === 'function' ? prksIcon('folders') : ''}</span>
                    <span class="prks-group-tree__title">${escapeHtmlGroup(node.name || 'Group')}</span>
                </a>
                ${metaHtml}
            </div>`;

        if (hasChildren) {
            let branchInner = '';
            kids.forEach((child) => {
                branchInner += renderNode(child, depth + 1);
            });
            html += `<div class="prks-group-tree__branch${collapsedClass}" data-group-id="${nodeIdAttr}"><div class="prks-group-tree__branch-inner">${branchInner}</div></div>`;
        }
        return html;
    }

    const roots = list
        .filter((g) => !g.parent_id && (!filtering || visibleSet.has(g.id)))
        .sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), undefined, { sensitivity: 'base' }));

    return roots.map((r) => renderNode(r, 0)).join('');
}

function renderPersonGroupsPage(groups, container) {
    const list = Array.isArray(groups) ? groups : [];
    const filterQuery = prksGroupLibraryFilterFromStorage();
    const filterEsc = escapeHtmlGroup(filterQuery);
    const filtering = Boolean(String(filterQuery || '').trim());
    const hasCollapsible = prksGroupTreeHasCollapsibleNodes(list);
    const expandToggleLabel = prksGroupLibraryExpandToggleLabel(list);
    const expandToggleInner = prksGroupLibraryExpandToggleInnerHtml();
    const expandToggleCollapseAll = !prksGroupTreeAllCollapsed(list);
    const toolbarActions = hasCollapsible
        ? `<div class="prks-group-library__toolbar-actions">
            <button type="button" id="prks-group-library-expand-toggle" class="prks-btn prks-btn--secondary prks-group-library__toolbar-btn${expandToggleCollapseAll ? ' is-collapse-all' : ''}" aria-label="${escapeHtmlGroup(expandToggleLabel)}" title="${escapeHtmlGroup(expandToggleLabel)}"${filtering ? ' hidden disabled' : ''}>${expandToggleInner}</button>
           </div>`
        : '';
    const searchToolbar =
        list.length > 0
            ? `<div class="prks-group-library__toolbar">
            <div class="tag-add-shell tag-add-shell--flush prks-group-library__search">
                <div class="tag-add-shell__field">
                    ${typeof prksTagSearchIconHtml === 'function' ? prksTagSearchIconHtml() : ''}
                    <input type="text" id="prks-group-library-search" class="tag-add-shell__input" placeholder="Search groups…" value="${filterEsc}" maxlength="300" autocomplete="off" aria-label="Filter groups">
                    <button type="button" class="tag-add-shell__clear" id="prks-group-library-search-clear" aria-label="Clear search" title="Clear search" hidden>&times;</button>
                </div>
            </div>
            ${toolbarActions}
        </div>`
            : '';
    const treeHost =
        list.length > 0
            ? `<div class="prks-group-library__scroll" data-prks-group-tree-host>${prksGroupLibraryTreeInnerHtml(list, filterQuery)}</div>`
            : '<div class="prks-group-library__empty-state"><p class="prks-inline-message prks-group-library__empty">No Person Groups yet.</p><button type="button" class="prks-btn prks-btn--primary" onclick="openModal(\'group-modal\')">New Group</button></div>';

    container.innerHTML = `
        <div class="prks-group-library">
        <div class="prks-page-header page-header prks-group-library__header page-header--split">
            <h2 class="prks-page-title">People groups</h2>
            <button type="button" class="prks-btn prks-btn--secondary" onclick="openModal('group-modal')">${typeof prksIcon === 'function' ? prksIcon('plus', { size: 'sm' }) : ''} New group</button>
        </div>
        <p class="meta-row prks-group-library__intro">Organize people into hierarchical groups. A person can belong to multiple groups.</p>
        ${searchToolbar}
        ${treeHost}
        </div>`;

    const root = container.querySelector('.prks-group-library');
    if (root) {
        root.__prksGroupLibraryState = { groups: list, container, filterQuery };
        prksBindGroupLibrarySearch(root);
        const expandToggle = root.querySelector('#prks-group-library-expand-toggle');
        if (expandToggle && expandToggle.dataset.bound !== '1') {
            expandToggle.dataset.bound = '1';
            expandToggle.addEventListener('click', () => prksToggleAllGroupNodes(expandToggle));
        }
    }
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}

function prksCollectDescendantIds(groupId, allList) {
    const out = new Set();
    function walk(id) {
        allList.filter((x) => x.parent_id === id).forEach((ch) => {
            out.add(ch.id);
            walk(ch.id);
        });
    }
    walk(groupId);
    return out;
}

function renderPersonGroupSubgroupsListHtml(g, options = {}) {
    if (!g.children || g.children.length === 0) return '';
    const className = options.main ? 'group-detail__relationship-list' : 'group-sidebar__subgroup-list';
    let subHtml =
        `<ul class="person-link-list ${className}">`;
    g.children.forEach((ch) => {
        subHtml += `<li><a href="#/people/groups/${escapeHtmlGroup(ch.id)}" class="route-sidebar__link">${escapeHtmlGroup(ch.name)}</a></li>`;
    });
    subHtml += '</ul>';
    return subHtml;
}

function renderPersonGroupSummarySidebarHtml(g) {
    const nMem = Array.isArray(g.members) ? g.members.length : 0;
    const nSub = Array.isArray(g.children) ? g.children.length : 0;
    return `
        <div class="group-sidebar-pane">
            <p class="saved-view-detail__kicker">Group</p>
            <ul class="person-sidebar__stats">
                <li>${nMem} member${nMem === 1 ? '' : 's'}</li>
                <li>${nSub} subgroup${nSub === 1 ? '' : 's'}</li>
            </ul>
            <button type="button" class="prks-btn prks-btn--primary group-sidebar__primary-btn" onclick="openPersonGroupEdit()">Edit group</button>
            <p class="route-sidebar__action"><a href="#/people/groups" class="route-sidebar__link">All groups</a></p>
        </div>`;
}

function renderPersonGroupEditSidebarHtml(g) {
    const parentSearchPlaceholder = 'Search or type a new parent name…';
    return `
        <div class="group-sidebar-pane group-sidebar-pane--edit">
            <h3 class="group-sidebar__title">Edit group</h3>
            <div class="form-pane group-sidebar-form">
                <section class="group-sidebar-form__section"><h4>Identity</h4><label for="gd-name">Name</label><input type="text" id="gd-name" value="${escapeHtmlGroup(g.name)}"></section>
                <section class="group-sidebar-form__section"><h4>Hierarchy</h4><div class="group-sidebar__label-with-hint"><label for="gd-parent-search" class="group-sidebar__label-text">Parent group</label>${prksHintBtnHtml('group-edit-parent', 'About parent group', 'group-sidebar__hint-btn')}</div>
                    <p class="meta-row">Choose an existing Group, leave blank for top-level, or type a new name to create a parent when saving.</p>
                    <div class="tag-add-shell combobox-container tag-add-shell--flush prks-inline-combobox-shell"><div class="tag-add-shell__field">${typeof prksTagSearchIconHtml === 'function' ? prksTagSearchIconHtml() : ''}<input type="text" id="gd-parent-search" class="tag-add-shell__input" placeholder="${escapeHtmlGroup(parentSearchPlaceholder)}" autocomplete="off" aria-label="Search parent group"></div><input type="hidden" id="gd-parent-id" value=""><div id="gd-parent-results" class="combobox-results combobox-results--tag-panel hidden"></div></div></section>
                <section class="group-sidebar-form__section"><h4>Description</h4><label for="gd-description" class="sr-only">Description</label><textarea id="gd-description" class="prks-textarea prks-textarea--short">${escapeHtmlGroup(g.description || '')}</textarea></section>
            </div>
            <div class="form-actions prks-form-actions--split group-sidebar__sticky-actions"><button type="button" class="prks-btn prks-btn--secondary" onclick="closePersonGroupEdit()">Cancel</button><button type="button" class="prks-btn prks-btn--primary" id="gd-save-btn">Save changes</button></div>
            <details class="group-sidebar__advanced"><summary>Advanced</summary><button type="button" class="prks-btn prks-btn--danger group-sidebar__delete" id="gd-delete-btn">Delete group</button></details>
        </div>`;
}

function prksSyncPersonGroupMemberEditUi(ownerCtx) {
    const ctx = ownerCtx || (typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null);
    const editing = !!(ctx && ctx.ui && ctx.ui.personGroupMembersEditing);
    const view =
        (ctx && ctx.query ? ctx.query('.document-view--group-detail') : null) ||
        document.querySelector('.document-view--group-detail');
    if (view) view.classList.toggle('is-group-members-editing', editing);
}

function openPersonGroupEdit() {
    const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    if (ctx && ctx.ui) {
        ctx.ui.personGroupMembersEditing = false;
        ctx.ui.personGroupEditing = true;
    }
    const g = ctx && ctx.getEntity ? ctx.getEntity('personGroup') : null;
    if (ctx && ctx.root && g) renderPersonGroupDetail(g, ctx.root);
    prksSyncPersonGroupMemberEditUi(ctx);
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
}

function closePersonGroupEdit() {
    const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    if (ctx && ctx.ui) ctx.ui.personGroupEditing = false;
    prksSyncPersonGroupMemberEditUi(ctx);
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
}

window.openPersonGroupEdit = openPersonGroupEdit;
window.closePersonGroupEdit = closePersonGroupEdit;

function prksTogglePersonGroupMembersEdit() {
    const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const g = ctx && ctx.getEntity ? ctx.getEntity('personGroup') : null;
    if (!ctx || !g) return;
    ctx.ui.personGroupEditing = false;
    ctx.ui.personGroupMembersEditing = !ctx.ui.personGroupMembersEditing;
    if (ctx.root) renderPersonGroupDetail(g, ctx.root);
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
}
window.prksTogglePersonGroupMembersEdit = prksTogglePersonGroupMembersEdit;

function prksNavigateIfOwnerFocused(ownerCtx, hash, expectedGroupId) {
    if (!ownerCtx || ownerCtx.destroyed) return;
    const focused = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    if (!focused || focused.tabId !== ownerCtx.tabId) return;
    if (expectedGroupId != null && expectedGroupId !== '') {
        const live = ownerCtx.getEntity ? ownerCtx.getEntity('personGroup') : null;
        if (!live || String(live.id) !== String(expectedGroupId)) return;
    }
    if (typeof prksNavigate === 'function') prksNavigate(hash);
}

async function mountPersonGroupEditPanel(g) {
    const ownerCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const generation = ownerCtx && typeof ownerCtx.generation === 'number' ? ownerCtx.generation : undefined;
    const all = await prksEnsureAllGroupsCache();
    if (ownerCtx && typeof generation === 'number' && typeof ownerCtx.isCurrent === 'function' && !ownerCtx.isCurrent(generation)) {
        return;
    }
    const descendants = prksCollectDescendantIds(g.id, all);
    descendants.add(g.id);
    prksBindGroupSearchCombobox('gd-parent-search', 'gd-parent-results', 'gd-parent-id', descendants);

    const searchEl = document.getElementById('gd-parent-search');
    const hidEl = document.getElementById('gd-parent-id');
    if (g.parent && searchEl && hidEl) {
        hidEl.value = g.parent.id;
        const prow = all.find((x) => x.id === g.parent.id);
        searchEl.value = prow ? prksGroupRowLabel(prow, all) : g.parent.name;
    }
    if (typeof prksBindAutosizeTextareas === 'function') {
        prksBindAutosizeTextareas(document.getElementById('panel-content'));
    }

    const saveBtn = document.getElementById('gd-save-btn');
    if (saveBtn) {
        saveBtn.onclick = async () => {
            const saveCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : ownerCtx;
            const btn = document.getElementById('gd-save-btn');
            const name = document.getElementById('gd-name').value.trim();
            const description = document.getElementById('gd-description').value;
            const hid = (document.getElementById('gd-parent-id') || {}).value || '';
            const search = (document.getElementById('gd-parent-search') || {}).value || '';
            const payload = { name, description };
            if (hid.trim()) payload.parent_id = hid.trim();
            else if (search.trim()) payload.parent_name = search.trim();
            else payload.parent_id = null;
            if (!name) {
                await prksAlertMessage('Name is required.', 'Validation');
                return;
            }
            btn.disabled = true;
            try {
                const res = await prksRequest(`/api/person-groups/${encodeURIComponent(g.id)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    await prksAlertMessage(data.error || 'Could not save group.', 'Could not save');
                    return;
                }
                if (saveCtx && saveCtx.ui) {
                    saveCtx.ui.personGroupEditing = false;
                    saveCtx.ui.personGroupMembersEditing = false;
                }
                prksNavigateIfOwnerFocused(
                    saveCtx,
                    '#/people/groups/' + encodeURIComponent(g.id),
                    g.id
                );
            } catch (e) {
                console.error(e);
                await prksAlertMessage('Could not save group.', 'Error');
            } finally {
                btn.disabled = false;
            }
        };
    }

    const delBtn = document.getElementById('gd-delete-btn');
    if (delBtn) {
        delBtn.onclick = async () => {
            const delCtx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : ownerCtx;
            const confirmed = await prksConfirmDestructive({
                title: `Delete group “${g.name}”?`,
                message:
                    'Members stay in the database; subgroups become children of this group’s parent (or top-level).',
                confirmLabel: 'Delete group',
            });
            if (!confirmed) return;
            try {
                const res = await prksRequest(`/api/person-groups/${encodeURIComponent(g.id)}`, { method: 'DELETE' });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    await prksAlertMessage(data.error || 'Could not delete.', 'Error');
                    return;
                }
                prksNavigateIfOwnerFocused(delCtx, '#/people/groups');
            } catch (e) {
                console.error(e);
                await prksAlertMessage('Could not delete group.', 'Error');
            }
        };
    }
}

function mountPersonGroupMemberRemoveButtons(g, ownerCtx) {
    const buttons = ownerCtx && ownerCtx.queryAll ? ownerCtx.queryAll('[data-remove-member]') : [];
    buttons.forEach((btn) => {
        btn.addEventListener('click', async (ev) => {
            ev.stopPropagation();
            const pid = btn.getAttribute('data-remove-member');
            if (!pid) return;
            const member = (g.members || []).find((m) => String(m.id) === String(pid));
            const personName = member
                ? `${member.first_name || ''} ${member.last_name || ''}`.trim() || 'this person'
                : 'this person';
            const groupName = g.name || 'this group';
            const confirmed = await prksConfirmDestructive({
                title: `Remove ${personName} from group?`,
                message: `They will be removed from “${groupName}” only. Their person profile is not deleted.`,
                confirmLabel: 'Remove from group',
            });
            if (!confirmed) return;
            if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(btn, true);
            try {
                const res = await prksRequest(
                    `/api/person-groups/${encodeURIComponent(g.id)}/members/${encodeURIComponent(pid)}`,
                    { method: 'DELETE' }
                );
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    await prksAlertMessage(data.error || 'Could not remove member.', 'Error');
                    return;
                }
                prksNavigateIfOwnerFocused(
                    ownerCtx,
                    '#/people/groups/' + encodeURIComponent(g.id),
                    g.id
                );
            } catch (e) {
                console.error(e);
                await prksAlertMessage('Could not remove member.', 'Error');
            } finally {
                if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(btn, false);
            }
        });
    });
}

/** Members-section searchable Person combobox. */
async function mountPersonGroupAddMemberControls(g, ownerCtx) {
    const input = ownerCtx && ownerCtx.query ? ownerCtx.query('#group-add-member-search') : null;
    if (!input) return;
    const generation = ownerCtx && typeof ownerCtx.generation === 'number' ? ownerCtx.generation : undefined;

    const persons = await fetchPersons();
    const liveGroup = ownerCtx && ownerCtx.getEntity ? ownerCtx.getEntity('personGroup') : null;
    const liveInput = ownerCtx && ownerCtx.query ? ownerCtx.query('#group-add-member-search') : null;
    if (
        !ownerCtx ||
        (typeof generation === 'number' && typeof ownerCtx.isCurrent === 'function' && !ownerCtx.isCurrent(generation)) ||
        !ownerCtx.ui ||
        !ownerCtx.ui.personGroupMembersEditing ||
        !liveGroup ||
        String(liveGroup.id) !== String(g.id) ||
        !liveInput ||
        liveInput !== input
    ) {
        return;
    }
    allPersons = persons;
    window.allPersons = persons;
    const memberIds = new Set((g.members || []).map((m) => String(m.id)));
    initSearchableCombobox('group-add-member-search', 'group-add-member-results', 'group-add-member-id', 'person', {
        excludePersonIds: memberIds,
    });
    const addBtn = ownerCtx && ownerCtx.query ? ownerCtx.query('#group-add-member-btn') : null;
    if (addBtn) {
        addBtn.onclick = async () => {
            const idInput = ownerCtx && ownerCtx.query ? ownerCtx.query('#group-add-member-id') : null;
            const pid = idInput ? idInput.value : '';
            if (!pid) {
                await prksAlertMessage('Choose a person from the search list.', 'Validation');
                return;
            }
            if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(addBtn, true, { busyLabel: 'Adding…' });
            try {
                const res = await prksRequest(`/api/person-groups/${encodeURIComponent(g.id)}/members`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ person_id: pid })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    await prksAlertMessage(data.error || 'Could not add member.', 'Error');
                    return;
                }
                prksNavigateIfOwnerFocused(
                    ownerCtx,
                    '#/people/groups/' + encodeURIComponent(g.id),
                    g.id
                );
            } catch (e) {
                console.error(e);
                await prksAlertMessage('Could not add member.', 'Error');
            } finally {
                if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(addBtn, false);
            }
        };
    }
}

function renderPersonGroupAddMemberPanelHtml() {
    return `
        <div class="group-detail__member-add">
            <p class="tag-add-field__caption">Add a person</p>
            <div class="tag-add-shell combobox-container">
                <input type="hidden" id="group-add-member-id" value="">
                <div class="tag-add-shell__field">
                    ${typeof prksTagPlusIconHtml === 'function' ? prksTagPlusIconHtml() : ''}
                    <input type="text" id="group-add-member-search" class="tag-add-shell__input" placeholder="Search by name, alias, group, or role…" maxlength="200" autocomplete="off" aria-label="Search person to add to group">
                </div>
                <div id="group-add-member-results" class="combobox-results combobox-results--tag-panel hidden"></div>
            </div>
            <button type="button" class="prks-btn prks-btn--primary" id="group-add-member-btn">Add to group</button>
        </div>`;
}

function renderPersonGroupDetail(group, container) {
    const g = group;
    const ctx = typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
    const membersEditing = !!(ctx && ctx.ui && ctx.ui.personGroupMembersEditing);
    const parentLink = g.parent
        ? `<a href="#/people/groups/${encodeURIComponent(String(g.parent.id || ''))}" class="route-sidebar__link">${escapeHtmlGroup(g.parent.name)}</a>`
        : '';
    const breadcrumb = `
        <p class="meta-row meta-row--lede">
            <a href="#/people/groups" class="route-sidebar__link">All groups</a>
            ${g.parent ? ` <span aria-hidden="true">›</span> ${parentLink}` : ''}
        </p>`;
    const description = String(g.description || '').trim();
    const children = Array.isArray(g.children) ? g.children : [];
    const hierarchyHtml = `
        <section class="group-detail__section" aria-labelledby="group-hierarchy-heading">
            <h3 id="group-hierarchy-heading">Hierarchy</h3>
            <div class="group-detail__relationship"><span>Parent</span><span>${parentLink || 'Top-level group'}</span></div>
            <div class="group-detail__relationship"><span>Subgroups · ${children.length}</span>${children.length ? renderPersonGroupSubgroupsListHtml(g, { main: true }) : '<span class="meta-row">No subgroups.</span>'}</div>
        </section>`;
    let membersHtml = `<section class="group-detail__section group-detail__members" aria-labelledby="group-members-heading"><div class="group-detail__section-head"><h3 id="group-members-heading">Members</h3><span class="group-detail__count">${Array.isArray(g.members) ? g.members.length : 0}</span><button type="button" class="prks-btn prks-btn--secondary prks-btn--sm" onclick="prksTogglePersonGroupMembersEdit()">${membersEditing ? 'Done' : 'Manage members'}</button></div>`;
    if (membersEditing) membersHtml += renderPersonGroupAddMemberPanelHtml();

    if (g.members && g.members.length > 0) {
        const rowFn =
            typeof buildPersonListRowHtml === 'function'
                ? buildPersonListRowHtml
                : typeof window.buildPersonListRowHtml === 'function'
                  ? window.buildPersonListRowHtml
                  : null;
        membersHtml +=
            '<div class="prks-people-library__scroll prks-people-library__scroll--embedded" data-prks-group-members-host><div class="prks-people-list" role="list">';
        g.members.forEach((p) => {
            if (rowFn) {
                membersHtml += rowFn(p, { showGroups: false, removeButton: membersEditing });
            } else {
                const pid = escapeHtmlGroup(p.id);
                membersHtml += `<div class="prks-people-list__row"><a href="#/people/${pid}">${escapeHtmlGroup(`${p.first_name || ''} ${p.last_name || ''}`.trim())}</a></div>`;
            }
        });
        membersHtml += '</div></div>';
    } else {
        membersHtml += '<p class="meta-row">No members in this group yet.</p>';
    }
    membersHtml += '</section>';

    container.innerHTML = `
        <div class="prks-page-header page-header page-header--split">
            <h2 class="prks-page-title">${typeof prksPageHeaderIconHtml === 'function' ? prksPageHeaderIconHtml('folders') : ''} ${escapeHtmlGroup(g.name)}</h2>
        </div>
        ${breadcrumb}
        <div class="document-view document-view--person document-view--group-detail">
            <div class="doc-content">
                <section class="group-detail__section" aria-labelledby="group-description-heading"><h3 id="group-description-heading">Description</h3><p class="group-detail__description">${description ? escapeHtmlGroup(description) : 'No description yet.'}</p></section>
                ${hierarchyHtml}
                ${membersHtml}
            </div>
        </div>`;

    if (membersEditing) {
        mountPersonGroupMemberRemoveButtons(g, ctx);
        void mountPersonGroupAddMemberControls(g, ctx);
    }
    prksSyncPersonGroupMemberEditUi(ctx);
    if (typeof prksRefreshIcons === 'function') prksRefreshIcons(container);
}

function prksPersonEditFindGroupByNameInsensitive(name, list) {
    const t = (name || '').trim().toLowerCase();
    if (!t) return null;
    return (list || []).find((g) => String(g.name || '').trim().toLowerCase() === t) || null;
}

function prksRenderPersonGroupChips(container, groups) {
    if (!container) return;
    const list = groups || [];
    if (!list.length) {
        container.innerHTML = '<span class="meta-row meta-row--compact-empty">No groups yet.</span>';
        return;
    }
    container.innerHTML = list
        .map((g) => {
            const nm = escapeHtmlGroup(g.name || '');
            const id = escapeHtmlGroup(g.id);
            return `<span class="tag pd-group-chip prks-chip" data-group-id="${id}">
                ${nm}
                <button type="button" class="pd-group-chip-remove prks-chip__remove" data-group-id="${id}" title="Remove" aria-label="Remove ${nm}">&times;</button>
            </span>`;
        })
        .join(' ');
}

async function prksMountPersonProfileGroupPicker(person) {
    window.__prksPersonEditSelectedGroups = null;
    const chips = document.getElementById('pd-group-chips');
    const search = document.getElementById('pd-group-search');
    const results = document.getElementById('pd-group-results');
    const hidden = document.getElementById('pd-group-pick-id');
    const addBtn = document.getElementById('pd-group-add-btn');
    if (!chips || !search || !results || !hidden || !addBtn || !person) return;

    window.__prksPersonEditSelectedGroups = new Map();
    await prksEnsureAllGroupsCache();
    prksBindGroupSearchCombobox('pd-group-search', 'pd-group-results', 'pd-group-pick-id', new Set());

    const selected = window.__prksPersonEditSelectedGroups;
    (person.groups || []).forEach((g) => selected.set(g.id, { id: g.id, name: g.name }));
    prksRenderPersonGroupChips(chips, [...selected.values()]);

    chips.onclick = (ev) => {
        const rm = ev.target.closest('.pd-group-chip-remove');
        if (!rm) return;
        ev.preventDefault();
        const id = rm.getAttribute('data-group-id');
        selected.delete(id);
        prksRenderPersonGroupChips(chips, [...selected.values()]);
    };

    async function addGroupId(gid, nameHint) {
        if (!gid || selected.has(gid)) return;
        let meta = (window.allGroups || []).find((x) => x.id === gid);
        if (!meta) meta = { id: gid, name: nameHint || gid };
        selected.set(gid, { id: gid, name: meta.name });
        prksRenderPersonGroupChips(chips, [...selected.values()]);
        search.value = '';
        hidden.value = '';
    }

    addBtn.onclick = async () => {
        const hid = hidden.value.trim();
        const typed = search.value.trim();
        if (hid) {
            const g = (window.allGroups || []).find((x) => x.id === hid);
            await addGroupId(hid, g ? g.name : '');
            return;
        }
        if (!typed) {
            await prksAlertMessage('Search and pick a group, or type a new group name to create.', 'Validation');
            return;
        }
        const existing = prksPersonEditFindGroupByNameInsensitive(typed, window.allGroups);
        if (existing) {
            await addGroupId(existing.id, existing.name);
            return;
        }
        try {
            const res = await prksRequest('/api/person-groups', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: typed, description: '' })
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                if (data.error && String(data.error).includes('already exists')) {
                    await prksEnsureAllGroupsCache();
                    const again = prksPersonEditFindGroupByNameInsensitive(typed, window.allGroups);
                    if (again) {
                        await addGroupId(again.id, again.name);
                        return;
                    }
                }
                await prksAlertMessage(data.error || 'Could not create group.', 'Could not save');
                return;
            }
            await prksEnsureAllGroupsCache();
            await addGroupId(data.id, typed);
        } catch (e) {
            console.error(e);
            await prksAlertMessage('Could not create group.', 'Error');
        }
    };

    window.__prksPersonEditSelectedGroups = selected;
}

window.prksMountPersonProfileGroupPicker = prksMountPersonProfileGroupPicker;

function prksGetPersonEditGroupIdsFromDom() {
    const m = window.__prksPersonEditSelectedGroups;
    if (m instanceof Map) return [...m.keys()];
    return undefined;
}

window.prksGetPersonEditGroupIdsFromDom = prksGetPersonEditGroupIdsFromDom;
