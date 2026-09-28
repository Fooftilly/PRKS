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

/* No connectivity gating in this file any more.
 *
 * Creating a group, renaming it, moving it, deleting it and changing who is in
 * it are all durable intents now, so a cached Group page is exactly as usable
 * as a connected one -- the same feature, not two. What can still stop a save
 * is not knowing this group's revisions, which is a fact about this device's
 * cache rather than about the network and is decided at save time by
 * `prksAcknowledgedPersonGroupBase`.
 */

/**
 * What this device believes the parent should be, as an id.
 *
 * The combobox writes an id; a free-typed name is resolved against the
 * EFFECTIVE catalogue, which includes groups created on this device and not
 * yet sent. A name that matches nothing becomes a new group, created durably
 * and named as this one's prerequisite -- the ordinary endpoint resolved the
 * same "or create it" server-side, and doing it as two visible operations is
 * what lets it happen with no server at all.
 *
 * Returns the id, '' for "top level", or undefined when it was refused.
 */
async function prksResolvePersonGroupParent(parentId, parentName, selfId) {
    const typed = String(parentName || '').trim();
    // Both answers are known without reading anything. The catalogue read
    // below goes through the ordinary read-through, which offline has to let a
    // request fail before the cache answers -- so it is worth not doing.
    if (String(parentId || '').trim()) return String(parentId).trim();
    if (!typed) return '';
    const catalogue = typeof prksEffectivePersonGroupCatalogue === 'function'
        ? await prksEffectivePersonGroupCatalogue() : [];
    const label = row => (row && String(row.name || '')).toLowerCase();
    const match = (catalogue || []).find(row => row && row.id !== selfId &&
        (label(row) === typed.toLowerCase() ||
            prksGroupRowLabel(row, catalogue).toLowerCase() === typed.toLowerCase()));
    if (match) return match.id;
    try {
        const created = await prksCreatePersonGroupDurably({ name: typed, description: '' });
        return created.entity_id;
    } catch (error) {
        await prksAlertMessage(prksPersonGroupSaveMessage(error, 'create that parent group'),
            'Could not save');
        return undefined;
    }
}

/** One message per refusal the store can produce. Never a generic bucket. */
function prksPersonGroupSaveMessage(error, action) {
    switch (error && error.prksLocalStoreCode) {
        case 'scope_busy':
            return 'Part of this group is syncing or needs a decision. Try again shortly.';
        case 'dependency_failed':
            return String(error.message || 'A change this one depends on could not be saved.');
        case 'entity_deleted':
            return 'This group is being deleted, so it cannot be changed.';
        case 'invalid_envelope':
            return String(error.message || 'That is not a valid group.');
        default:
            return 'Could not ' + action + ' locally. Please retry.';
    }
}

/**
 * Save a Group's editable fields durably, sending only what changed.
 *
 * Returns true when the editor may close. The three concepts stay apart: the
 * draft is what was typed, the base is what the server last confirmed, and the
 * difference is measured against what the form was SHOWING.
 */
async function prksSavePersonGroupDraft(groupId, draft) {
    const ops = typeof prksDurableOperationsOrNone === 'function'
        ? await prksDurableOperationsOrNone() : [];
    const base = await prksAcknowledgedPersonGroupBase(groupId, ops);
    if (!base) {
        await prksAlertMessage(
            'This group cannot be edited offline yet. Open it once while connected to PRKS '
            + 'so its synchronization state is prepared.', 'Unavailable');
        return false;
    }
    const changes = prksDirtyPersonGroupFields(groupId, draft, base, ops);
    if (!Object.keys(changes).length) return true;
    try {
        await prksSavePersonGroupFieldsDurably(groupId, changes, base);
    } catch (error) {
        await prksAlertMessage(prksPersonGroupSaveMessage(error, 'save this group'),
            'Could not save');
        return false;
    }
    return true;
}

/**
 * Add or remove one membership durably. Returns true when it was recorded.
 *
 * `quiet` suppresses the message: a caller changing SEVERAL pairs at once --
 * the Person editor saves a whole selection -- reports one refusal rather than
 * one dialog per pair.
 */
async function prksSetPersonGroupMembership(groupId, personId, present, quiet, stillOwns) {
    const say = async (message, title) => {
        if (!quiet) await prksAlertMessage(message, title);
        return false;
    };
    const dropped = () => typeof stillOwns === 'function' && !stillOwns();
    const ops = typeof prksDurableOperationsOrNone === 'function'
        ? await prksDurableOperationsOrNone() : [];
    if (dropped()) return false;
    const observed = await prksAcknowledgedPersonGroupMembership(
        groupId, personId, ops, stillOwns);
    if (dropped() || (observed && observed.prksOwnershipLost)) return false;
    if (!observed) {
        return await say(
            'This group\u2019s members cannot be changed offline yet. Open it once while '
            + 'connected to PRKS so its synchronization state is prepared.', 'Unavailable');
    }
    if (dropped()) return false;
    try {
        await prksSetPersonGroupMemberDurably(groupId, personId, present, observed, stillOwns);
    } catch (error) {
        if (dropped()) return false;
        return await say(
            prksPersonGroupSaveMessage(error, 'change this membership'), 'Could not save');
    }
    if (dropped()) return false;
    return true;
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

/* The Group catalogue every picker and label reads, through the offline
 * read-through and with this device's pending intent applied: a group created
 * here and not yet sent is a real group, and a picker that could not offer it
 * would make offline creation useless the moment it succeeded. */
async function prksEnsureAllGroupsCache() {
    window.allGroups = typeof prksEffectivePersonGroupCatalogue === 'function'
        ? await prksEffectivePersonGroupCatalogue()
        : await fetchPersonGroups();
    return window.allGroups || [];
}

/**
 * Searchable group picker: sets hidden id when user picks a row; clears hidden when typing.
 * excludedIds: Set of group ids not shown (e.g. self + descendants when editing).
 */
function prksBindGroupSearchComboboxElements(input, results, hidden, excludedIds, isLive) {
    if (!input || !results || !hidden) return;
    const stillLive = typeof isLive === 'function' ? isLive : () => true;

    const excluded =
        excludedIds instanceof Set ? excludedIds : new Set(Array.isArray(excludedIds) ? excludedIds : []);

    function renderList() {
        if (!stillLive()) return;
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
                    if (!stillLive() || input.disabled) return;
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
        if (input.disabled) return;
        void prksEnsureAllGroupsCache().then(() => {
            if (stillLive()) renderList();
        });
    };
    input.oninput = () => {
        if (!stillLive() || input.disabled) return;
        hidden.value = '';
        void prksEnsureAllGroupsCache().then(() => {
            if (stillLive()) renderList();
        });
    };
    input.onblur = () => {
        setTimeout(() => {
            if (stillLive()) prksHideInlineComboboxResults(results);
        }, 200);
    };
}

function prksBindGroupSearchCombobox(inputId, resultsId, hiddenId, excludedIds) {
    prksBindGroupSearchComboboxElements(
        document.getElementById(inputId),
        document.getElementById(resultsId),
        document.getElementById(hiddenId),
        excludedIds
    );
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

function prksPersonGroupRouteStill(ctx, groupId) {
    if (!ctx || ctx.destroyed) return false;
    const route = ctx.lastResolvedRoute || ctx.route;
    if (!route || route.name !== 'person-group-detail') return false;
    const routeId = route.params && route.params.groupId;
    if (String(routeId || '') !== String(groupId)) return false;
    /* beginRoute clears the entity before the same-group record is read back.
     * A missing entity is that gap. A different entity means the pane moved on. */
    const live = ctx.getEntity && ctx.getEntity('personGroup');
    if (live && String(live.id) !== String(groupId)) return false;
    return true;
}

function prksPersonGroupEditSessionStill(ctx, groupId, session) {
    if (!prksPersonGroupRouteStill(ctx, groupId) || !ctx.ui || !ctx.ui.personGroupEditing) return false;
    return ctx.ui.personGroupEditSession === session;
}

function prksPersonGroupMemberSessionStill(ctx, groupId, session) {
    if (!prksPersonGroupRouteStill(ctx, groupId) || !ctx.ui || !ctx.ui.personGroupMembersEditing) return false;
    return ctx.ui.personGroupMemberSession === session;
}

function prksBumpPersonGroupEditSession(ctx) {
    if (!ctx) return 0;
    if (!ctx.ui) ctx.ui = {};
    const next = (typeof ctx.ui.personGroupEditSession === 'number' ? ctx.ui.personGroupEditSession : 0) + 1;
    ctx.ui.personGroupEditSession = next;
    return next;
}

function prksBumpPersonGroupMemberSession(ctx) {
    if (!ctx) return 0;
    if (!ctx.ui) ctx.ui = {};
    const next = (typeof ctx.ui.personGroupMemberSession === 'number' ? ctx.ui.personGroupMemberSession : 0) + 1;
    ctx.ui.personGroupMemberSession = next;
    return next;
}

function prksFocusedPersonGroupContext() {
    return typeof prksGetFocusedTabContext === 'function' ? prksGetFocusedTabContext() : null;
}

function prksPersonGroupActionContext(owner) {
    if (owner && owner.tabId) return owner;
    return prksFocusedPersonGroupContext();
}

function prksRefreshPersonGroupMain(ctx) {
    const owner = ctx || prksFocusedPersonGroupContext();
    if (!owner || !owner.root || owner.destroyed) return;
    const route = owner.lastResolvedRoute || owner.route;
    if (!route || route.name !== 'person-group-detail') return;
    const groupId = route.params && route.params.groupId ? String(route.params.groupId) : '';
    const group = owner.getEntity && owner.getEntity('personGroup');
    if (!group || String(group.id) !== groupId) return;
    if (typeof prksPresentVuePersonGroups !== 'function') return;
    prksPresentVuePersonGroups(owner, owner.root, {
        feature: 'person-group-detail',
        availability: 'ready',
        group: group,
        groupId: groupId,
        editing: !!(owner.ui && owner.ui.personGroupEditing),
        membersEditing: !!(owner.ui && owner.ui.personGroupMembersEditing),
        generation: owner.generation,
    });
}

function prksRetainPersonGroupEditAcrossRefresh(ctx, keepEditing, keepMembers) {
    if (!ctx || !ctx.ui) return;
    ctx.ui.personGroupEditing = !!keepEditing;
    ctx.ui.personGroupMembersEditing = keepEditing ? false : !!keepMembers;
    if (!keepEditing) ctx.ui.personGroupFieldBaseline = null;
}
window.prksRetainPersonGroupEditAcrossRefresh = prksRetainPersonGroupEditAcrossRefresh;
window.prksRefreshPersonGroupMain = prksRefreshPersonGroupMain;

function prksRememberPersonGroupIndexCreate(owner) {
    const ctx = owner && owner.tabId
        ? owner
        : prksFocusedPersonGroupContext();
    const route = ctx && (ctx.lastResolvedRoute || ctx.route);
    const onIndex = !!(route && route.name === 'people-groups');
    window.__prksPersonGroupIndexCreateOrigin = ctx && ctx.tabId && onIndex
        ? {
            tabId: String(ctx.tabId),
            generation: typeof ctx.generation === 'number' ? ctx.generation : null,
        }
        : null;
}

function prksClearPersonGroupIndexCreateOrigin() {
    window.__prksPersonGroupIndexCreateOrigin = null;
    window.__prksPersonGroupIndexCreateArmed = false;
}

function prksOpenNewGroupModalFromGroupsPage(owner) {
    prksRememberPersonGroupIndexCreate(owner);
    window.__prksPersonGroupIndexCreateArmed = true;
    if (typeof openModal === 'function') openModal('group-modal');
}

function prksTakePersonGroupCreateNavigation() {
    const origin = window.__prksPersonGroupIndexCreateOrigin || null;
    window.__prksPersonGroupIndexCreateOrigin = null;
    window.__prksPersonGroupIndexCreateArmed = false;
    if (!origin || !origin.tabId) return { mode: 'unscoped' };
    const ctx = typeof prksGetTabContext === 'function' ? prksGetTabContext(origin.tabId) : null;
    const route = ctx && (ctx.lastResolvedRoute || ctx.route);
    if (!ctx || ctx.destroyed || !route || route.name !== 'people-groups') return { mode: 'stale' };
    return { mode: 'owner', tabId: String(ctx.tabId) };
}
window.prksOpenNewGroupModalFromGroupsPage = prksOpenNewGroupModalFromGroupsPage;
window.prksTakePersonGroupCreateNavigation = prksTakePersonGroupCreateNavigation;
window.prksClearPersonGroupIndexCreateOrigin = prksClearPersonGroupIndexCreateOrigin;

async function prksRerenderPersonGroupDetail(ctx, groupId) {
    if (!ctx || ctx.destroyed) return;
    const live = ctx.getEntity ? ctx.getEntity('personGroup') : null;
    if (!live || String(live.id) !== String(groupId)) return;
    const group = typeof prksPersonGroupRecordFor === 'function'
        ? await prksPersonGroupRecordFor(groupId) : null;
    if (!group || ctx.destroyed) return;
    const stillLive = ctx.getEntity ? ctx.getEntity('personGroup') : null;
    if (!stillLive || String(stillLive.id) !== String(groupId)) return;
    ctx.setEntity('personGroup', group);
    prksRefreshPersonGroupMain(ctx);
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
    if (typeof prksRefreshMountedPersonSurfaces === 'function') prksRefreshMountedPersonSurfaces();
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
            <button type="button" class="prks-btn prks-btn--primary group-sidebar__primary-btn" data-prks-role="group-mutation-control" onclick="openPersonGroupEdit()">Edit group</button>
            <p class="route-sidebar__action"><a href="#/people/groups" class="route-sidebar__link">All groups</a></p>
        </div>`;
}

function openPersonGroupEdit(owner) {
    const ctx = prksPersonGroupActionContext(owner);
    if (!ctx || !ctx.ui) return;
    const g = ctx.getEntity ? ctx.getEntity('personGroup') : null;
    if (!g) return;
    const same = !!(ctx.ui.personGroupEditing && ctx.ui.personGroupFieldBaseline &&
        String(ctx.ui.personGroupFieldBaseline.groupId) === String(g.id));
    if (ctx.ui.personGroupMembersEditing) prksBumpPersonGroupMemberSession(ctx);
    ctx.ui.personGroupMembersEditing = false;
    ctx.ui.personGroupEditing = true;
    if (!same) {
        prksBumpPersonGroupEditSession(ctx);
        const parent = g.parent && g.parent.id ? g.parent : null;
        ctx.ui.personGroupFieldBaseline = {
            groupId: String(g.id),
            name: String(g.name || ''),
            description: String(g.description || ''),
            parent_id: parent ? String(parent.id) : '',
            parent_name: parent ? String(parent.name || '') : '',
        };
    }
    prksRefreshPersonGroupMain(ctx);
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
}

function closePersonGroupEdit(owner) {
    const ctx = prksPersonGroupActionContext(owner);
    const group = ctx && ctx.getEntity ? ctx.getEntity('personGroup') : null;
    if (ctx && ctx.ui) {
        prksBumpPersonGroupEditSession(ctx);
        ctx.ui.personGroupEditing = false;
        ctx.ui.personGroupFieldBaseline = null;
    }
    prksRefreshPersonGroupMain(ctx);
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
    if (group && group.id) void prksRerenderPersonGroupDetail(ctx, group.id);
}
window.openPersonGroupEdit = openPersonGroupEdit;
window.closePersonGroupEdit = closePersonGroupEdit;

function prksTogglePersonGroupMembersEdit(owner) {
    const ctx = prksPersonGroupActionContext(owner);
    const g = ctx && ctx.getEntity ? ctx.getEntity('personGroup') : null;
    if (!ctx || !ctx.ui || !g) return;
    if (ctx.ui.personGroupEditing) {
        prksBumpPersonGroupEditSession(ctx);
        ctx.ui.personGroupEditing = false;
        ctx.ui.personGroupFieldBaseline = null;
    }
    prksBumpPersonGroupMemberSession(ctx);
    ctx.ui.personGroupMembersEditing = !ctx.ui.personGroupMembersEditing;
    prksRefreshPersonGroupMain(ctx);
    if (typeof updatePanelContent === 'function') updatePanelContent('details');
}
window.prksTogglePersonGroupMembersEdit = prksTogglePersonGroupMembersEdit;

async function savePersonGroupEditor(ctx, groupId, draft, baseline, session) {
    const stillOwns = () => prksPersonGroupEditSessionStill(ctx, groupId, session);
    if (!stillOwns()) return { ok: true, quiet: true };
    const name = String(draft && draft.name || '').trim();
    if (!name) {
        await prksAlertMessage('Name is required.', 'Validation');
        return { ok: false, message: 'Name is required.' };
    }
    const ops = typeof prksDurableOperationsOrNone === 'function'
        ? await prksDurableOperationsOrNone() : [];
    if (!stillOwns()) return { ok: true, quiet: true };
    const base = await prksAcknowledgedPersonGroupBase(groupId, ops);
    if (!stillOwns()) return { ok: true, quiet: true };
    if (!base) {
        await prksAlertMessage(
            'This group cannot be edited offline yet. Open it once while connected to PRKS '
            + 'so its synchronization state is prepared.', 'Unavailable');
        return { ok: false, message: 'Unavailable' };
    }
    const baseFields = baseline || {};
    const parentDraftId = String(draft && draft.parent_id || '').trim();
    const parentDraftName = String(draft && draft.parent_name || '');
    const parentShownId = String(baseFields.parent_id == null ? '' : baseFields.parent_id);
    const parentShownName = String(baseFields.parent_name == null ? '' : baseFields.parent_name);
    let parentId = parentDraftId;
    if (parentDraftId !== parentShownId || parentDraftName.trim() !== parentShownName.trim()) {
        parentId = await prksResolvePersonGroupParent(parentDraftId, parentDraftName, groupId);
        if (parentId === undefined || !stillOwns()) return { ok: false, quiet: !stillOwns() };
    }
    const next = {
        name: name,
        description: String(draft && draft.description != null ? draft.description : ''),
        parent_id: String(parentId || ''),
    };
    const changes = {};
    ['name', 'description', 'parent_id'].forEach((field) => {
        const desired = String(next[field] == null ? '' : next[field]);
        const shown = String(baseFields[field] == null ? '' : baseFields[field]);
        if (desired !== shown) changes[field] = desired;
    });
    if (!Object.keys(changes).length) return { ok: true };
    try {
        await prksSavePersonGroupFieldsDurably(groupId, changes, base, stillOwns);
    } catch (error) {
        if (!stillOwns()) return { ok: true, quiet: true };
        await prksAlertMessage(prksPersonGroupSaveMessage(error, 'save this group'), 'Could not save');
        return { ok: false, message: 'Could not save' };
    }
    if (!stillOwns()) return { ok: true, quiet: true };
    return { ok: true };
}
window.savePersonGroupEditor = savePersonGroupEditor;

async function deletePersonGroupEditor(ctx, groupId, session) {
    const stillOwns = () => prksPersonGroupEditSessionStill(ctx, groupId, session);
    if (!stillOwns()) return;
    const group = ctx && ctx.getEntity ? ctx.getEntity('personGroup') : null;
    const name = group && group.name ? group.name : 'this group';
    const confirmed = await prksConfirmDestructive({
        title: `Delete group “${name}”?`,
        message: 'Members stay in the database; subgroups become children of this group’s parent (or top-level).',
        confirmLabel: 'Delete group',
    });
    if (!confirmed || !stillOwns()) return;
    try {
        await prksDeletePersonGroupDurably(groupId);
    } catch (error) {
        if (stillOwns()) {
            await prksAlertMessage(prksPersonGroupSaveMessage(error, 'delete this group'), 'Could not delete');
        }
        return;
    }
    if (!stillOwns()) return;
    if (typeof prksNavigate === 'function') {
        prksNavigate('#/people/groups', { tabId: ctx.tabId });
    }
}
window.deletePersonGroupEditor = deletePersonGroupEditor;

async function prksBindPersonGroupParentSearch(editor, group, ownerCtx) {
    const all = await prksEnsureAllGroupsCache();
    if (!editor || !editor.isConnected || !ownerCtx || !ownerCtx.ui || !ownerCtx.ui.personGroupEditing) return;
    if (!prksPersonGroupRouteStill(ownerCtx, group.id)) return;
    const descendants = prksCollectDescendantIds(group.id, all);
    descendants.add(group.id);
    const live = () => editor.isConnected && ownerCtx.ui && ownerCtx.ui.personGroupEditing &&
        prksPersonGroupRouteStill(ownerCtx, group.id);
    prksBindGroupSearchComboboxElements(
        editor.querySelector('#gd-parent-search'),
        editor.querySelector('#gd-parent-results'),
        editor.querySelector('#gd-parent-id'),
        descendants,
        live
    );
    const searchEl = editor.querySelector('#gd-parent-search');
    const hidEl = editor.querySelector('#gd-parent-id');
    if (group.parent && searchEl && hidEl && !searchEl.value && !hidEl.value) {
        hidEl.value = group.parent.id;
        const prow = all.find((x) => x.id === group.parent.id);
        searchEl.value = prow ? prksGroupRowLabel(prow, all) : group.parent.name;
    }
}

function prksBindPersonGroupDetailChrome(ownerCtx) {
    const ctx = ownerCtx || prksFocusedPersonGroupContext();
    if (!ctx || !ctx.root) return;
    const group = ctx.getEntity ? ctx.getEntity('personGroup') : null;
    if (!group) return;
    const editor = ctx.root.querySelector('.group-sidebar-pane--edit');
    if (editor && ctx.ui && ctx.ui.personGroupEditing && editor.dataset.prksParentBound !== '1') {
        editor.dataset.prksParentBound = '1';
        void prksBindPersonGroupParentSearch(editor, group, ctx);
    }
    if (ctx.ui && ctx.ui.personGroupMembersEditing) {
        mountPersonGroupMemberRemoveButtons(group, ctx);
        void mountPersonGroupAddMemberControls(group, ctx);
    }
}
window.prksBindPersonGroupDetailChrome = prksBindPersonGroupDetailChrome;

function mountPersonGroupMemberRemoveButtons(g, ownerCtx) {
    const buttons = ownerCtx && ownerCtx.queryAll
        ? ownerCtx.queryAll('[data-remove-member]')
        : (ownerCtx && ownerCtx.root ? ownerCtx.root.querySelectorAll('[data-remove-member]') : []);
    const session = ownerCtx && ownerCtx.ui ? ownerCtx.ui.personGroupMemberSession : 0;
    buttons.forEach((btn) => {
        if (!btn || btn.__prksMemberRemoveBound) return;
        btn.__prksMemberRemoveBound = true;
        btn.addEventListener('click', async (ev) => {
            ev.stopPropagation();
            const stillOwns = () => prksPersonGroupMemberSessionStill(ownerCtx, g.id, session);
            if (!stillOwns()) return;
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
            if (!confirmed || !stillOwns()) return;
            if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(btn, true);
            try {
                if (!await prksSetPersonGroupMembership(g.id, pid, false, false, stillOwns)) return;
                await prksRerenderPersonGroupDetail(ownerCtx, g.id);
            } catch (e) {
                console.error(e);
                if (stillOwns()) await prksAlertMessage('Could not remove member.', 'Error');
            } finally {
                if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(btn, false);
            }
        });
    });
}

function prksReplacePersonGroupMemberExclude(input, members) {
    const nextIds = (members || []).map((member) => String(member && member.id || '')).filter(Boolean);
    let set = input.__prksMemberExclude;
    if (!(set instanceof Set)) {
        set = new Set();
        input.__prksMemberExclude = set;
    }
    Array.from(set).forEach((id) => {
        if (nextIds.indexOf(id) === -1) set.delete(id);
    });
    nextIds.forEach((id) => set.add(id));
    return set;
}

/** Members-section searchable Person combobox. */
async function mountPersonGroupAddMemberControls(g, ownerCtx) {
    const input = ownerCtx && ownerCtx.query ? ownerCtx.query('#group-add-member-search') : null;
    if (!input || input.__prksMemberMounting) return;
    if (input.__prksMemberBound) {
        const liveGroup = ownerCtx && ownerCtx.getEntity ? ownerCtx.getEntity('personGroup') : null;
        if (!liveGroup || String(liveGroup.id) !== String(g.id)) return;
        prksReplacePersonGroupMemberExclude(input, liveGroup.members);
        return;
    }
    const generation = ownerCtx && typeof ownerCtx.generation === 'number' ? ownerCtx.generation : undefined;
    input.__prksMemberMounting = true;

    /* The People this device holds, effective: someone created offline is a
     * real person and must be addable to a group. The mounting flag stays set
     * through the await so a second paint cannot bind the same input twice. */
    const persons = await (async () => {
        try {
            if (typeof prksOfflinePeopleFetch === 'function') {
                return ((await prksOfflinePeopleFetch()).value || []);
            }
            return await fetchPersons();
        } finally {
            input.__prksMemberMounting = false;
        }
    })();
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
    input.__prksMemberBound = true;
    allPersons = persons;
    window.allPersons = persons;
    const memberIds = prksReplacePersonGroupMemberExclude(input, liveGroup.members || []);
    initSearchableCombobox('group-add-member-search', 'group-add-member-results', 'group-add-member-id', 'person', {
        excludePersonIds: memberIds,
    });
    const addBtn = ownerCtx && ownerCtx.query ? ownerCtx.query('#group-add-member-btn') : null;
    if (addBtn) {
        const session = ownerCtx.ui.personGroupMemberSession;
        addBtn.onclick = async () => {
            const stillOwns = () => prksPersonGroupMemberSessionStill(ownerCtx, g.id, session);
            if (!stillOwns()) return;
            const idInput = ownerCtx && ownerCtx.query ? ownerCtx.query('#group-add-member-id') : null;
            const pid = idInput ? idInput.value : '';
            if (!pid) {
                await prksAlertMessage('Choose a person from the search list.', 'Validation');
                return;
            }
            if (typeof prksSetButtonBusy === 'function') prksSetButtonBusy(addBtn, true, { busyLabel: 'Adding…' });
            try {
                if (!await prksSetPersonGroupMembership(g.id, pid, true, false, stillOwns)) return;
                if (idInput) idInput.value = '';
                await prksRerenderPersonGroupDetail(ownerCtx, g.id);
            } catch (e) {
                console.error(e);
                if (stillOwns()) await prksAlertMessage('Could not add member.', 'Error');
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

function prksGetPersonProfileDraftGroupIds(ctx, personId) {
    const draft = ctx && ctx.ui && ctx.ui.personProfileDraft;
    if (!draft || String(draft.personId) !== String(personId)) return undefined;
    return (Array.isArray(draft.groups) ? draft.groups : []).map((group) => group.id);
}

async function prksMountPersonProfileGroupPicker(ctx, person, editor) {
    if (!ctx || !person || !editor || person.id == null) return;
    const generation = ctx.generation;
    const personId = String(person.id);
    const draft = typeof prksEnsurePersonProfileDraft === 'function'
        ? prksEnsurePersonProfileDraft(ctx, person)
        : ctx.ui && ctx.ui.personProfileDraft;
    const chips = editor.querySelector('#pd-group-chips');
    const search = editor.querySelector('#pd-group-search');
    const results = editor.querySelector('#pd-group-results');
    const hidden = editor.querySelector('#pd-group-pick-id');
    const addBtn = editor.querySelector('#pd-group-add-btn');
    if (!chips || !search || !results || !hidden || !addBtn || !draft) return;

    const logicalSessionCurrent = () =>
        typeof prksPersonProfileEditSessionCurrent === 'function' &&
        prksPersonProfileEditSessionCurrent(ctx, generation, personId, draft);
    const originalEditorCurrent = () => {
        if (
            typeof prksPersonProfileEditorCurrent !== 'function' ||
            !prksPersonProfileEditorCurrent(ctx, generation, personId, draft, editor)
        ) return false;
        return (
            editor.querySelector('#pd-group-chips') === chips &&
            editor.querySelector('#pd-group-search') === search &&
            editor.querySelector('#pd-group-results') === results &&
            editor.querySelector('#pd-group-pick-id') === hidden &&
            editor.querySelector('#pd-group-add-btn') === addBtn
        );
    };
    const selectedFromDraft = () => new Map(
        (Array.isArray(draft.groups) ? draft.groups : []).map((group) => [String(group.id), group])
    );
    const replaceDraftGroups = (selected) => {
        if (!logicalSessionCurrent()) return false;
        draft.groups = [...selected.values()].map((group) => ({ id: group.id, name: String(group.name || '') }));
        return true;
    };
    const renderOwnedDraftGroups = () => {
        if (!logicalSessionCurrent()) return;
        const hosts = [];
        const panel = document.getElementById('panel-content');
        if (panel && (typeof prksRightPanelOwnedBy !== 'function' || prksRightPanelOwnedBy(ctx, panel))) {
            hosts.push(panel);
        }
        if (ctx && ctx.root) hosts.push(ctx.root);
        hosts.forEach((host) => {
            const currentEditor = host.querySelector('.person-panel-edit');
            if (!currentEditor || String(currentEditor.getAttribute('data-person-edit-id') || '') !== personId) return;
            const currentChips = currentEditor.querySelector('#pd-group-chips');
            if (currentChips) prksRenderPersonGroupChips(currentChips, draft.groups);
        });
    };

    prksRenderPersonGroupChips(chips, draft.groups);
    await prksEnsureAllGroupsCache();
    if (!originalEditorCurrent()) return;
    prksBindGroupSearchComboboxElements(search, results, hidden, new Set(), originalEditorCurrent);
    prksRenderPersonGroupChips(chips, draft.groups);

    chips.onclick = (ev) => {
        if (!originalEditorCurrent()) return;
        const rm = ev.target.closest('.pd-group-chip-remove');
        if (!rm) return;
        ev.preventDefault();
        const id = String(rm.getAttribute('data-group-id') || '');
        const selected = selectedFromDraft();
        selected.delete(id);
        if (replaceDraftGroups(selected)) prksRenderPersonGroupChips(chips, draft.groups);
    };

    function addGroupId(gid, nameHint) {
        if (!logicalSessionCurrent() || !gid) return;
        const selected = selectedFromDraft();
        const key = String(gid);
        if (selected.has(key)) return;
        let meta = (window.allGroups || []).find((x) => String(x.id) === key);
        if (!meta) meta = { id: gid, name: nameHint || gid };
        selected.set(key, { id: meta.id, name: meta.name });
        if (!replaceDraftGroups(selected)) return;
        renderOwnedDraftGroups();
        if (originalEditorCurrent()) {
            search.value = '';
            hidden.value = '';
        }
    }

    addBtn.onclick = async () => {
        if (!originalEditorCurrent()) return;
        const hid = hidden.value.trim();
        const typed = search.value.trim();
        if (hid) {
            const g = (window.allGroups || []).find((x) => String(x.id) === hid);
            addGroupId(hid, g ? g.name : '');
            return;
        }
        if (!typed) {
            await prksAlertMessage('Search and pick a group, or type a new group name to create.', 'Validation');
            return;
        }
        const existing = prksPersonEditFindGroupByNameInsensitive(typed, window.allGroups);
        if (existing) {
            // Picking an existing Group only edits the unsaved local draft, so
            // it stays available offline; the Person PATCH that would persist
            // it is guarded on its own.
            addGroupId(existing.id, existing.name);
            return;
        }
        /* A brand-new Group is durable and carries an id this device minted, so
         * it is a real group the moment it is created -- with or without a
         * server. Uniqueness of the NAME stays canonical: only the server sees
         * every group, so a collision comes back as a refusal the user
         * resolves, rather than being guessed at here. */
        try {
            const created = await prksCreatePersonGroupDurably({ name: typed, description: '' });
            await prksEnsureAllGroupsCache();
            if (!logicalSessionCurrent()) return;
            addGroupId(created.entity_id, typed);
        } catch (error) {
            if (logicalSessionCurrent() && typeof prksRightPanelOwnedBy === 'function' && prksRightPanelOwnedBy(ctx)) {
                await prksAlertMessage(
                    prksPersonGroupSaveMessage(error, 'create this group'), 'Could not save');
            }
        }
    };
}

window.prksMountPersonProfileGroupPicker = prksMountPersonProfileGroupPicker;
window.prksGetPersonProfileDraftGroupIds = prksGetPersonProfileDraftGroupIds;
