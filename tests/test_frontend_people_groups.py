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
_VUE_STATE = os.path.join(_ROOT, "frontend-app", "src", "features", "person-groups", "ui-state.ts")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _member_picker_harness(case):
    src = _read(_GROUPS)
    mount = src.split("async function mountPersonGroupAddMemberControls", 1)[1].split(
        "function renderPersonGroupAddMemberPanelHtml", 1
    )[0]
    mount = "async function mountPersonGroupAddMemberControls" + mount
    script = r"""
const vm = require('vm');
const mountSource = %s;
const stale = [{ id: 'stale-person' }];
const fresh = [{ id: 'fresh-person' }];
const input = {};
const group = { id: 'group-a', members: [] };
let initCalls = 0;
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
  initSearchableCombobox: () => { initCalls += 1; },
};
vm.createContext(context);
vm.runInContext(mountSource + '; this.mount = mountPersonGroupAddMemberControls;', context);
(async () => {
  if (%s === 'fresh') {
    context.fetchPersons = async () => fresh;
    await context.mount(group, owner);
    if (initCalls !== 1 || context.allPersons !== fresh || context.window.allPersons !== fresh) {
      throw new Error('fresh picker did not publish fetched people');
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
""" % (json.dumps(mount), json.dumps(case))
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


class FrontendPeopleGroupsTests(unittest.TestCase):
    def test_library_runtime_is_owner_scoped_and_filtered_tree_is_noncollapsible(self):
        src = _read(_GROUPS)
        state = _read(_VUE_STATE)
        index = _read(_VUE_INDEX)
        tree = _read(os.path.join(os.path.dirname(_VUE_INDEX), "PersonGroupTreeNode.vue"))
        self.assertNotIn("window.__prksGroupTreeCollapsed", src)
        self.assertNotIn("window.__prksGroupLibraryState", src)
        self.assertNotIn("sessionStorage", state)
        self.assertIn("searchByOwner", state)
        self.assertIn("expandedByOwner", state)
        self.assertIn("prks-group-tree__toggle-spacer", tree)
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
        self.assertIn("const previousPersonGroup = ctx.getEntity && ctx.getEntity('personGroup');", app)
        self.assertIn("const previousPersonGroupId", app)
        self.assertIn("const previousPersonGroupMembersEditing", app)
        self.assertIn("const preserveMembersEditing =", app)
        self.assertIn("const preserveGroupEditing =", app)
        self.assertIn("previousPersonGroupEditing &&", app)
        self.assertIn("ctx.ui.personGroupEditing", app)
        self.assertIn("previousPersonGroupId", app)
        self.assertIn("ctx.ui.personGroupMembersEditing = preserveMembersEditing;", app)
        self.assertIn("ctx.ui.personGroupEditing = preserveGroupEditing;", app)
        self.assertIn("prksRetainPersonGroupEditAcrossRefresh", app)
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


if __name__ == "__main__":
    unittest.main()
