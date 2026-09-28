"""Regression contracts for Person Group presentation and ownership."""
import os
import json
import subprocess
import unittest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GROUPS = os.path.join(_ROOT, "frontend", "js", "components", "people-groups.js")
_APP = os.path.join(_ROOT, "frontend", "js", "app.js")
_TAB_CONTEXT = os.path.join(_ROOT, "frontend", "js", "tab-context.js")
_UI = os.path.join(_ROOT, "frontend", "js", "ui.js")
_VUE_INDEX = os.path.join(_ROOT, "frontend-app", "src", "features", "person-groups", "PersonGroupsIndexRoute.vue")
_VUE_DETAIL = os.path.join(_ROOT, "frontend-app", "src", "features", "person-groups", "PersonGroupDetailRoute.vue")
_VUE_INTENTS = os.path.join(_ROOT, "frontend-app", "src", "features", "person-groups", "intents.ts")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _extract_function(src, name):
    start = src.index(f"function {name}(")
    if start >= 6 and src[start - 6 : start] == "async ":
        start -= 6
    brace = src.index("{", start)
    depth = 0
    quote = None
    escaped = False
    for index in range(brace, len(src)):
        char = src[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return src[start : index + 1]
    raise AssertionError(f"unclosed function {name}")


def _run_node(script):
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False, timeout=15)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)


def _member_picker_harness(case):
    src = _read(_GROUPS)
    helper = _extract_function(src, "prksReplacePersonGroupMemberExclude")
    mount = _extract_function(src, "mountPersonGroupAddMemberControls")
    script = r"""
const vm = require('vm');
const helperSource = %s;
const mountSource = %s;
const caseName = %s;
const stale = [{ id: 'stale-person' }];
const fresh = [{ id: 'fresh-person' }];
const input = {};
const group = { id: 'group-a', members: [] };
let initCalls = 0;
let excluded = null;
let resolvePersons;
const owner = {
  generation: 7,
  ui: { personGroupMembersEditing: true },
  isCurrent: (generation) => generation === owner.generation,
  getEntity: () => group,
  query: (selector) => selector === '#group-add-member-search' ? input : null,
};
const context = {
  allPersons: stale,
  window: { allPersons: stale },
  initSearchableCombobox: (_a, _b, _c, _d, options) => {
    initCalls += 1;
    excluded = options && options.excludePersonIds;
  },
};
vm.createContext(context);
vm.runInContext(helperSource + '\n' + mountSource + '; this.mount = mountPersonGroupAddMemberControls;', context);
(async () => {
  if (caseName === 'fresh') {
    context.fetchPersons = async () => fresh;
    await context.mount(group, owner);
    if (initCalls !== 1 || context.allPersons !== fresh || context.window.allPersons !== fresh) {
      throw new Error('fresh picker did not publish fetched people');
    }
  } else if (caseName === 'refresh') {
    context.fetchPersons = async () => fresh;
    group.members = [{ id: 'kept' }, { id: 'gone' }];
    await context.mount(group, owner);
    group.members = [{ id: 'kept' }];
    await context.mount(group, owner);
    if (initCalls !== 1 || !excluded || excluded.has('gone') || !excluded.has('kept')) {
      throw new Error('removal did not refresh the picker exclusion');
    }
  } else {
    context.fetchPersons = () => new Promise((resolve) => { resolvePersons = resolve; });
    const pending = context.mount(group, owner);
    owner.ui.personGroupMembersEditing = false;
    resolvePersons(fresh);
    await pending;
    if (initCalls !== 0 || context.allPersons !== stale || context.window.allPersons !== stale) {
      throw new Error('stale picker replaced current people cache');
    }
  }
})().catch((error) => { console.error(error.stack || error); process.exit(1); });
""" % (json.dumps(helper), json.dumps(mount), json.dumps(case))
    _run_node(script)


class FrontendPeopleGroupsTests(unittest.TestCase):
    def test_library_runtime_is_owner_scoped_and_filtered_tree_is_noncollapsible(self):
        src = _read(_GROUPS)
        index = _read(_VUE_INDEX)
        tree = _read(os.path.join(os.path.dirname(_VUE_INDEX), "PersonGroupTreeNode.vue"))
        self.assertNotIn("window.__prksGroupTreeCollapsed", src)
        self.assertNotIn("window.__prksGroupLibraryState", src)
        self.assertNotIn("sessionStorage", index)
        self.assertNotIn("searchByOwner", index)
        self.assertNotIn("expandedByOwner", index)
        self.assertNotIn("new Map", index)
        self.assertIn("const query = ref('')", index)
        self.assertIn("const expandedIds = ref(new Set<string>())", index)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(_VUE_INDEX), "ui-state.ts")))
        self.assertIn("prks-group-tree__toggle-spacer", tree)
        self.assertIn("node.hasChildren ? (node.collapsed ? 'false' : 'true') : undefined", tree)
        self.assertNotIn(": 'false'\"", tree)
        self.assertIn(':hidden="filtering"', index)
        self.assertIn(':disabled="filtering"', index)

    def test_empty_and_search_empty_copy_are_distinct(self):
        index = _read(_VUE_INDEX)
        groups = _read(_GROUPS)
        self.assertIn("No Person Groups yet.", index)
        self.assertIn("New Group", index)
        self.assertIn("No groups match your search.", index)
        self.assertIn("openModal('group-modal')", groups)
        self.assertNotIn("Use <strong>New group</strong> in the ribbon", index)

    def test_detail_prioritizes_description_hierarchy_and_members(self):
        detail = _read(_VUE_DETAIL)
        self.assertIn("Description", detail)
        self.assertIn("No description yet.", detail)
        self.assertIn("Hierarchy", detail)
        self.assertIn("Top-level group", detail)
        self.assertIn("Manage members", detail)
        self.assertIn("group-add-member-search", detail)
        self.assertIn("data-remove-member", detail)
        self.assertIn("#/people/groups/", detail)

    def test_metadata_and_member_management_are_separate(self):
        src = _read(_GROUPS)
        ui = _read(_UI)
        context = _read(_TAB_CONTEXT)
        self.assertIn("personGroupMembersEditing: false", context)
        self.assertIn("ui.personGroupMembersEditing = false", context)
        self.assertIn("ctx.ui.personGroupMembersEditing = false", src)
        self.assertIn("ctx.ui.personGroupEditing = false", src)
        self.assertIn("function prksTogglePersonGroupMembersEdit", src)
        self.assertIn("is-group-members-editing", _read(_VUE_DETAIL))
        self.assertNotIn("renderPersonGroupAddMemberPanelHtml()", ui)
        self.assertNotIn("mountPersonGroupAddMemberControls(g)", ui)
        self.assertNotIn("renderPersonGroupEditSidebarHtml", ui)

    def test_same_group_refresh_keeps_members_mode_but_other_groups_reset(self):
        app = _read(_APP)
        same = app.split("const sameGroupDetail =", 1)[1].split("const routeAbort", 1)[0]
        self.assertIn("route.name === 'person-group-detail'", same)
        self.assertIn("String(route.params.groupId) === previousPersonGroupId", same)
        self.assertIn(
            "prksRetainPersonGroupEditAcrossRefresh(\n            ctx,\n            previousPersonGroupEditing,\n            previousPersonGroupMembersEditing\n        )",
            same,
        )
        ready = app.split("const preserveMembersEditing =", 1)[1].split("publishSidebar({", 1)[0]
        self.assertIn("sameGroupDetail &&", ready)
        self.assertIn("previousPersonGroupEditing &&", ready)
        self.assertIn("offlineGroup.source === 'server'", ready)
        self.assertIn(
            "prksRetainPersonGroupEditAcrossRefresh(\n                            ctx,\n                            preserveGroupEditing,\n                            preserveMembersEditing\n                        )",
            ready,
        )
        self.assertIn("!samePersonGroupsWorkspace", app)
        self.assertIn("prksVueDismissPersonGroups", app)

    def test_member_picker_async_mount_checks_original_owner_state(self):
        src = _read(_GROUPS)
        mount = src.split("async function mountPersonGroupAddMemberControls", 1)[1].split(
            "function renderPersonGroupAddMemberPanelHtml", 1
        )[0]
        self.assertIn("const generation =", mount)
        self.assertLess(mount.index("const generation ="), mount.index("const persons ="))
        self.assertIn("ownerCtx.isCurrent(generation)", mount)
        self.assertIn("ownerCtx.ui.personGroupMembersEditing", mount)
        self.assertIn("ownerCtx.getEntity('personGroup')", mount)
        self.assertIn("liveInput !== input", mount)
        self.assertLess(mount.index("const persons ="), mount.index("allPersons = persons;"))
        self.assertIn("prksOfflinePeopleFetch(", mount,
                      "someone created offline must be addable to a group")
        self.assertLess(mount.index("allPersons = persons;"), mount.index("initSearchableCombobox("))

    def test_member_picker_publishes_fresh_people_only_for_live_owner(self):
        _member_picker_harness("fresh")

    def test_stale_member_picker_keeps_existing_people_cache(self):
        _member_picker_harness("stale")

    def test_metadata_form_keeps_typed_parent_and_separate_delete(self):
        form = _read(_VUE_DETAIL)
        for heading in ("Identity", "Hierarchy", "Description"):
            self.assertIn(f"{heading}</h4>", form)
        self.assertIn("type a new name to create a parent when saving", form)
        self.assertIn("group-sidebar__sticky-actions", form)
        self.assertIn("<summary>Advanced</summary>", form)
        self.assertIn("Delete group", form)
        self.assertIn("savePersonGroupEditor", _read(_GROUPS))

    def test_profile_group_picker_keeps_async_work_on_original_editor(self):
        src = _read(_GROUPS)
        mount = src.split("async function prksMountPersonProfileGroupPicker", 1)[1]
        self.assertIn("const generation = ctx.generation", mount)
        self.assertIn("const draft =", mount)
        self.assertIn("editor.querySelector('#pd-group-chips')", mount)
        self.assertLess(mount.index("const chips ="), mount.index("await prksEnsureAllGroupsCache()"))
        self.assertIn("if (!originalEditorCurrent()) return", mount)
        self.assertIn("draft.groups =", mount)
        self.assertIn("logicalSessionCurrent()", mount)
        self.assertNotIn("document.getElementById('pd-group-chips')", mount)
        self.assertIn("function prksGetPersonProfileDraftGroupIds(ctx, personId)", src)

    def test_member_removal_refreshes_picker_exclusion(self):
        _member_picker_harness("refresh")

    def test_cancel_repaints_before_the_record_refresh(self):
        src = _read(_GROUPS)
        close = _extract_function(src, "closePersonGroupEdit")
        self.assertLess(close.index("prksRefreshPersonGroupMain(ctx)"), close.index("prksRerenderPersonGroupDetail"))
        self.assertIn("function closePersonGroupEdit(owner)", close)
        self.assertIn("prksPersonGroupActionContext(owner)", close)

    def test_save_closes_the_originating_pane_when_another_pane_is_focused(self):
        src = _read(_GROUPS)
        intents = _read(_VUE_INTENTS)
        self.assertIn("open(owner ?? undefined)", intents)
        self.assertIn("close(owner ?? undefined)", intents)
        self.assertIn("toggle(owner ?? undefined)", intents)
        script = "\n".join((
            "const window = globalThis;",
            _extract_function(src, "prksFocusedPersonGroupContext"),
            _extract_function(src, "prksPersonGroupActionContext"),
            _extract_function(src, "prksBumpPersonGroupEditSession"),
            _extract_function(src, "prksRefreshPersonGroupMain"),
            _extract_function(src, "closePersonGroupEdit"),
            r"""
const painted = [];
function prksGetFocusedTabContext() { return other; }
function prksPresentVuePersonGroups(ctx, _root, payload) {
  painted.push({ tabId: ctx.tabId, editing: payload.editing });
}
function prksRerenderPersonGroupDetail() { return new Promise(() => {}); }
const origin = {
  tabId: 'origin',
  root: {},
  lastResolvedRoute: { name: 'person-group-detail', params: { groupId: 'g1' } },
  ui: { personGroupEditing: true, personGroupEditSession: 1, personGroupFieldBaseline: { groupId: 'g1' } },
  getEntity: () => ({ id: 'g1', name: 'Saved' }),
};
const other = {
  tabId: 'other',
  root: {},
  lastResolvedRoute: { name: 'person-group-detail', params: { groupId: 'g2' } },
  ui: { personGroupEditing: true, personGroupEditSession: 4, personGroupFieldBaseline: { groupId: 'g2' } },
  getEntity: () => ({ id: 'g2', name: 'Other' }),
};
closePersonGroupEdit(origin);
if (origin.ui.personGroupEditing) throw new Error('origin editor stayed open');
if (!other.ui.personGroupEditing) throw new Error('focused pane editor was closed');
if (painted.length !== 1 || painted[0].tabId !== 'origin' || painted[0].editing) {
  throw new Error('cancel painted the wrong pane: ' + JSON.stringify(painted));
}
process.stdout.write('ok');
""",
        ))
        _run_node(script)

    def test_same_route_refresh_during_create_still_navigates_the_origin_pane(self):
        src = _read(_GROUPS)
        script = "\n".join((
            "const window = globalThis;",
            _extract_function(src, "prksTakePersonGroupCreateNavigation"),
            r"""
const tabs = {
  side: { tabId: 'side', destroyed: false, generation: 9, lastResolvedRoute: { name: 'people-groups' } },
};
function prksGetTabContext(id) { return tabs[id] || null; }
window.__prksPersonGroupIndexCreateOrigin = { tabId: 'side', generation: 3 };
const live = prksTakePersonGroupCreateNavigation();
if (live.mode !== 'owner' || live.tabId !== 'side') {
  throw new Error('same-route refresh skipped the originating pane: ' + JSON.stringify(live));
}
window.__prksPersonGroupIndexCreateOrigin = { tabId: 'side', generation: 3 };
tabs.side.lastResolvedRoute = { name: 'people' };
const left = prksTakePersonGroupCreateNavigation();
if (left.mode !== 'stale' || left.tabId) throw new Error('left pane was not stale: ' + JSON.stringify(left));
window.__prksPersonGroupIndexCreateOrigin = { tabId: 'side', generation: 3 };
tabs.side.destroyed = true;
tabs.side.lastResolvedRoute = { name: 'people-groups' };
const gone = prksTakePersonGroupCreateNavigation();
if (gone.mode !== 'stale') throw new Error('destroyed pane was not stale');
window.__prksPersonGroupIndexCreateOrigin = null;
const unscoped = prksTakePersonGroupCreateNavigation();
if (unscoped.mode !== 'unscoped') throw new Error('missing origin was not unscoped');
process.stdout.write('ok');
""",
        ))
        _run_node(script)

    def test_failed_save_does_not_create_a_parent(self):
        src = _read(_GROUPS)
        save = _extract_function(src, "savePersonGroupEditor")
        self.assertLess(save.index("prksAcknowledgedPersonGroupBase"), save.index("prksResolvePersonGroupParent"))
        script = "\n".join((
            _extract_function(src, "prksResolvePersonGroupParent"),
            _extract_function(src, "savePersonGroupEditor"),
            r"""
let created = 0;
function prksPersonGroupEditSessionStill() { return true; }
function prksDurableOperationsOrNone() { return Promise.resolve([]); }
function prksAcknowledgedPersonGroupBase() { return Promise.resolve(null); }
function prksCreatePersonGroupDurably() { created += 1; return Promise.resolve({ entity_id: 'new-parent' }); }
function prksEffectivePersonGroupCatalogue() { return Promise.resolve([]); }
function prksAlertMessage() { return Promise.resolve(); }
function prksSavePersonGroupFieldsDurably() { throw new Error('fields were written'); }
const ctx = {
  tabId: 'origin',
  ui: { personGroupEditing: true, personGroupEditSession: 2 },
  lastResolvedRoute: { name: 'person-group-detail', params: { groupId: 'g1' } },
  getEntity: () => ({ id: 'g1' }),
};
savePersonGroupEditor(ctx, 'g1', {
  name: 'Child',
  description: '',
  parent_id: '',
  parent_name: 'Brand new parent',
}, { name: 'Child', description: '', parent_id: '', parent_name: '' }, 2).then((result) => {
  if (created !== 0) throw new Error('unavailable save created a parent');
  if (!result || result.ok) throw new Error('unavailable save reported success');
  process.stdout.write('ok');
}).catch((error) => { console.error(error.stack || error); process.exit(1); });
""",
        ))
        _run_node(script)

    def test_unavailable_group_routes_clear_the_cached_banner(self):
        app = _read(_APP)
        index = app.split("case 'people-groups':", 1)[1].split("case 'person-group-detail':", 1)[0]
        unavailable_index = index.split("if (!groups)", 1)[1].split("publishSidebar", 1)[0]
        self.assertIn("prksOfflinePrependBanner(contentDiv, null)", unavailable_index)
        self.assertIn("notFound: true", unavailable_index)
        self.assertIn("notFoundTitle: 'Person Groups not available offline'", unavailable_index)
        detail = app.split("case 'person-group-detail':", 1)[1].split("case 'recent':", 1)[0]
        unavailable_detail = detail.split("if (resolvedGroup.unavailable)", 1)[1].split(
            "const group = await prksEffectivePersonGroupRecord", 1
        )[0]
        self.assertIn("prksOfflinePrependBanner(contentDiv, null)", unavailable_detail)
        missing = detail.split("availability: 'not-found'", 1)[1].split("entityTitle: group.name", 1)[0]
        self.assertIn("prksOfflinePrependBanner(contentDiv, null)", missing)

    def test_delete_uses_the_pending_action_busy_label(self):
        detail = _read(_VUE_DETAIL)
        self.assertIn("usePersonGroupPendingAction", detail)
        self.assertIn("withBusy('delete'", detail)
        self.assertIn('busy-label="Deleting…"', detail)
        self.assertIn('id="gd-delete-btn"', detail)


if __name__ == "__main__":
    unittest.main()
