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
                    results.classList.add('hidden');
                };
                results.appendChild(div);
            });
        }
        results.classList.remove('hidden');
    }

    input.onfocus = () => {
        void prksEnsureAllGroupsCache().then(renderList);
    };
    input.oninput = () => {
        hidden.value = '';
        void prksEnsureAllGroupsCache().then(renderList);
    };
    input.onblur = () => {
        setTimeout(() => results.classList.add('hidden'), 200);
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

function buildGroupTreeRoots(groups) {
    const byId = new Map((groups || []).map((g) => [g.id, g]));
    const roots = (groups || []).filter((g) => !g.parent_id);
    function childrenOf(pid) {
        return (groups || [])
            .filter((g) => g.parent_id === pid)
            .sort((a, b) => String(a.name).localeCompare(String(b.name), undefined, { sensitivity: 'base' }));
    }
    function renderNode(g, depth) {
        const pad = depth ? ` style="margin-left:${Math.min(depth, 8) * 14}px"` : '';
        const mc = g.member_count != null ? Number(g.member_count) : 0;
        const cc = g.child_count != null ? Number(g.child_count) : 0;
        const meta = [];
        if (mc) meta.push(`${mc} member${mc === 1 ? '' : 's'}`);
        if (cc) meta.push(`${cc} subgroup${cc === 1 ? '' : 's'}`);
        const metaLine =
            meta.length > 0
                ? `<p class="meta-row" style="font-size:0.8rem;">${escapeHtmlGroup(meta.join(' · '))}</p>`
                : '';
        let html = `
            <div class="project-card project-card--group"${pad} onclick="window.location.hash='#/people/groups/${escapeHtmlGroup(g.id)}'">
                <div class="card-title">${escapeHtmlGroup(g.name)}</div>
                ${metaLine}
            </div>`;
        childrenOf(g.id).forEach((ch) => {
            html += renderNode(ch, depth + 1);
        });
        return html;
    }
    let out = '';
    roots
        .sort((a, b) => String(a.name).localeCompare(String(b.name), undefined, { sensitivity: 'base' }))
        .forEach((r) => {
            out += renderNode(r, 0);
        });
    return out;
}

function renderPersonGroupsPage(groups, container) {
    const list = Array.isArray(groups) ? groups : [];
    let body = '';
    if (list.length === 0) {
        body =
            '<p class="meta-row" style="padding:12px 4px;">No groups yet. Use <strong>New group</strong> in the ribbon or open a group from a person’s profile.</p>';
    } else {
        body = `<div class="list-view list-view--groups">${buildGroupTreeRoots(list)}</div>`;
    }
    container.innerHTML = `
        <div class="page-header" style="justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
            <h2>People groups</h2>
            <button type="button" class="ribbon-btn" onclick="openModal('group-modal')">➕ New group</button>
        </div>
        <p class="meta-row" style="padding:4px 4px 16px;">Organize people under hierarchical groups (e.g. <em>Philosophy</em> → <em>Frankfurt School</em>). Group names are unique. A person can belong to several groups.</p>
        ${body}`;
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

function renderPersonGroupSubgroupsListHtml(g) {
    if (!g.children || g.children.length === 0) return '';
    let subHtml =
        '<h4 class="group-sidebar__subheading">Subgroups</h4><ul class="person-link-list group-sidebar__subgroup-list">';
    g.children.forEach((ch) => {
        subHtml += `<li><a href="#/people/groups/${escapeHtmlGroup(ch.id)}" class="route-sidebar__link">${escapeHtmlGroup(ch.name)}</a></li>`;
    });
    subHtml += '</ul>';
    return subHtml;
}

function renderPersonGroupSummarySidebarHtml(g) {
    const nMem = Array.isArray(g.members) ? g.members.length : 0;
    const nSub = Array.isArray(g.children) ? g.children.length : 0;
    const bits = [];
    if (nMem) bits.push(`${nMem} member${nMem === 1 ? '' : 's'}`);
    if (nSub) bits.push(`${nSub} subgroup${nSub === 1 ? '' : 's'}`);
    const meta = bits.length ? `<p class="group-sidebar__meta">${escapeHtmlGroup(bits.join(' · '))}</p>` : '';
    const parentLine = g.parent
        ? `<p class="meta-row" style="margin:6px 0;">Parent: <a href="#/people/groups/${escapeHtmlGroup(g.parent.id)}" class="route-sidebar__link">${escapeHtmlGroup(g.parent.name)}</a></p>`
        : '<p class="meta-row" style="margin:6px 0;">Top-level group</p>';
    return `
        <div class="group-sidebar-pane">
            <h3 class="group-sidebar__title">${escapeHtmlGroup(g.name)}</h3>
            ${meta}
            ${parentLine}
            ${renderPersonGroupSubgroupsListHtml(g)}
            <button type="button" class="add-new-btn group-sidebar__primary-btn" onclick="openPersonGroupEdit()">Edit group</button>
        </div>`;
}

function renderPersonGroupEditSidebarHtml(g) {
    const parentSearchPlaceholder = 'Search or type a new parent name…';
    return `
        <div class="group-sidebar-pane group-sidebar-pane--edit">
            <div class="group-sidebar-edit-header">
                <h3 class="group-sidebar__title">Edit group</h3>
                <button type="button" class="group-sidebar__cancel" onclick="closePersonGroupEdit()">Cancel</button>
            </div>
            <div class="form-pane group-sidebar-form">
                <label for="gd-name">Name</label>
                <input type="text" id="gd-name" value="${escapeHtmlGroup(g.name)}">
                <div class="group-sidebar__label-with-hint">
                    <label for="gd-parent-search" class="group-sidebar__label-text">Parent group</label>
                    ${prksHintBtnHtml('group-edit-parent', 'About parent group', 'group-sidebar__hint-btn')}
                </div>
                <div class="combobox-container">
                    <div class="tag-add-shell">
                        <div class="tag-add-shell__field">
                            <span class="tag-add-shell__icon" aria-hidden="true">🔍</span>
                            <input type="text" id="gd-parent-search" class="tag-add-shell__input" placeholder="${escapeHtmlGroup(parentSearchPlaceholder)}" autocomplete="off" aria-label="Search parent group">
                        </div>
                    </div>
                    <input type="hidden" id="gd-parent-id" value="">
                    <div id="gd-parent-results" class="combobox-results hidden"></div>
                </div>
                <label for="gd-description">Description</label>
                <textarea id="gd-description" style="min-height:56px;">${escapeHtmlGroup(g.description || '')}</textarea>
                <div class="group-sidebar__actions">
                    <button type="button" class="add-new-btn" id="gd-save-btn">Save changes</button>
                    <button type="button" class="ribbon-btn group-sidebar__delete" id="gd-delete-btn">Delete group</button>
                </div>
            </div>
            ${renderPersonGroupSubgroupsListHtml(g)}
        </div>`;
}

function openPersonGroupEdit() {
    window.__prksPersonGroupDetailEditing = true;
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
}

function closePersonGroupEdit() {
    window.__prksPersonGroupDetailEditing = false;
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
}

window.openPersonGroupEdit = openPersonGroupEdit;
window.closePersonGroupEdit = closePersonGroupEdit;

async function mountPersonGroupEditPanel(g) {
    const all = await prksEnsureAllGroupsCache();
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

    const saveBtn = document.getElementById('gd-save-btn');
    if (saveBtn) {
        saveBtn.onclick = async () => {
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
                alert('Name is required.');
                return;
            }
            btn.disabled = true;
            try {
                const res = await fetch(`/api/person-groups/${encodeURIComponent(g.id)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    alert(data.error || 'Could not save group.');
                    return;
                }
                window.__prksPersonGroupDetailEditing = false;
                window.location.hash = `#/people/groups/${g.id}`;
                window.location.reload();
            } catch (e) {
                console.error(e);
                alert('Could not save group.');
            } finally {
                btn.disabled = false;
            }
        };
    }

    const delBtn = document.getElementById('gd-delete-btn');
    if (delBtn) {
        delBtn.onclick = async () => {
            if (!confirm(`Delete group “${g.name}”? Members stay in the database; subgroups become children of this group’s parent (or top-level).`)) {
                return;
            }
            try {
                const res = await fetch(`/api/person-groups/${encodeURIComponent(g.id)}`, { method: 'DELETE' });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    alert(data.error || 'Could not delete.');
                    return;
                }
                window.location.hash = '#/people/groups';
                window.location.reload();
            } catch (e) {
                console.error(e);
                alert('Could not delete group.');
            }
        };
    }
}

function mountPersonGroupMemberRemoveButtons(g) {
    document.querySelectorAll('[data-remove-member]').forEach((btn) => {
        btn.addEventListener('click', async (ev) => {
            ev.stopPropagation();
            const pid = btn.getAttribute('data-remove-member');
            try {
                const res = await fetch(
                    `/api/person-groups/${encodeURIComponent(g.id)}/members/${encodeURIComponent(pid)}`,
                    { method: 'DELETE' }
                );
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    alert(data.error || 'Could not remove member.');
                    return;
                }
                window.location.hash = `#/people/groups/${g.id}`;
                window.location.reload();
            } catch (e) {
                console.error(e);
                alert('Could not remove member.');
            }
        });
    });
}

/** Right-panel “Add person” combobox; call after panel HTML includes #group-add-member-search. */
async function mountPersonGroupAddMemberControls(g) {
    const input = document.getElementById('group-add-member-search');
    if (!input) return;

    allPersons = await fetchPersons();
    const memberIds = new Set((g.members || []).map((m) => String(m.id)));
    initSearchableCombobox('group-add-member-search', 'group-add-member-results', 'group-add-member-id', 'person', {
        excludePersonIds: memberIds,
    });
    const addBtn = document.getElementById('group-add-member-btn');
    if (addBtn) {
        addBtn.onclick = async () => {
            const pid = document.getElementById('group-add-member-id').value;
            if (!pid) {
                return alert('Choose a person from the search list.');
            }
            try {
                const res = await fetch(`/api/person-groups/${encodeURIComponent(g.id)}/members`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ person_id: pid })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    alert(data.error || 'Could not add member.');
                    return;
                }
                window.location.hash = `#/people/groups/${g.id}`;
                window.location.reload();
            } catch (e) {
                console.error(e);
                alert('Could not add member.');
            }
        };
    }
}

function renderPersonGroupAddMemberPanelHtml() {
    return `
        <div class="group-sidebar-pane group-sidebar-pane--add-member">
            <p class="tag-add-field__caption group-sidebar-pane--add-member__caption">Add a person</p>
            <div class="tag-add-shell combobox-container">
                <input type="hidden" id="group-add-member-id" value="">
                <div class="tag-add-shell__field">
                    <span class="tag-add-shell__icon" aria-hidden="true">＋</span>
                    <input type="text" id="group-add-member-search" class="tag-add-shell__input" placeholder="Search by name, alias, group, or role…" maxlength="200" autocomplete="off" aria-label="Search person to add to group">
                </div>
                <div id="group-add-member-results" class="combobox-results combobox-results--tag-panel hidden"></div>
            </div>
            <button type="button" class="add-new-btn group-sidebar-pane--add-member__btn" id="group-add-member-btn">Add to group</button>
        </div>`;
}

function renderPersonGroupDetail(group, container) {
    const g = group;
    const parentLink = g.parent
        ? `<a href="#/people/groups/${escapeHtmlGroup(g.parent.id)}" class="route-sidebar__link">${escapeHtmlGroup(g.parent.name)}</a>`
        : '';
    const breadcrumb = `
        <p class="meta-row" style="margin-bottom:16px;">
            <a href="#/people/groups" class="route-sidebar__link">All groups</a>
            ${g.parent ? ` · ${parentLink}` : ''}
        </p>`;

    let membersHtml = `<h3 style="margin-top:8px;color:var(--text-secondary);">Members</h3>`;

    if (g.members && g.members.length > 0) {
        membersHtml += '<div class="list-view list-view--group-members">';
        g.members.forEach((p) => {
            const pid = escapeHtmlGroup(p.id);
            const inner =
                typeof buildPersonListCardContentHtml === 'function'
                    ? buildPersonListCardContentHtml(p)
                    : `<div class="card-title">${escapeHtmlGroup(
                          `${p.first_name || ''} ${p.last_name || ''}`.trim()
                      )}</div>`;
            membersHtml += `
                <div class="project-card project-card--person project-card--group-member">
                    <div class="project-card--group-member__body" onclick="window.location.hash='#/people/${pid}'">
                        ${inner}
                    </div>
                    <div class="project-card--group-member__actions">
                        <button type="button" class="ribbon-btn" data-remove-member="${pid}" style="font-size:0.75rem;">Remove</button>
                    </div>
                </div>`;
        });
        membersHtml += '</div>';
    } else {
        membersHtml += '<p class="meta-row">No members in this group yet.</p>';
    }

    container.innerHTML = `
        <div class="page-header" style="justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;">
            <h2>📂 ${escapeHtmlGroup(g.name)}</h2>
        </div>
        ${breadcrumb}
        <div class="document-view document-view--person">
            <div class="doc-content">
                ${membersHtml}
            </div>
        </div>`;

    mountPersonGroupMemberRemoveButtons(g);
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
        container.innerHTML = '<span class="meta-row" style="font-size:0.8rem;">No groups yet.</span>';
        return;
    }
    container.innerHTML = list
        .map((g) => {
            const nm = escapeHtmlGroup(g.name || '');
            const id = escapeHtmlGroup(g.id);
            return `<span class="tag pd-group-chip" data-group-id="${id}" style="display:inline-flex;align-items:center;gap:6px;">
                ${nm}
                <button type="button" class="pd-group-chip-remove" data-group-id="${id}" style="background:none;border:none;color:inherit;cursor:pointer;padding:0 2px;font-size:1rem;line-height:1;" title="Remove">&times;</button>
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
            return alert('Search and pick a group, or type a new group name to create.');
        }
        const existing = prksPersonEditFindGroupByNameInsensitive(typed, window.allGroups);
        if (existing) {
            await addGroupId(existing.id, existing.name);
            return;
        }
        try {
            const res = await fetch('/api/person-groups', {
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
                alert(data.error || 'Could not create group.');
                return;
            }
            await prksEnsureAllGroupsCache();
            await addGroupId(data.id, typed);
        } catch (e) {
            console.error(e);
            alert('Could not create group.');
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
