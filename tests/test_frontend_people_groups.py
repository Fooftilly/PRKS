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
    def test_library_runtime_is_root_local_and_filtered_tree_is_noncollapsible(self):
        src = _read(_GROUPS)
        self.assertNotIn("window.__prksGroupLibraryState", src)
        self.assertIn("root.__prksGroupLibraryState", src)
        self.assertIn("function prksRerenderGroupTreeOnly(root)", src)
        self.assertIn("input.closest('.prks-group-library')", src)
        self.assertIn("prksRerenderGroupTreeOnly(root)", src)
        self.assertIn("prksToggleGroupNode('${gidEnc}', this)", src)
        self.assertIn("prks-group-tree__toggle-spacer", src)
        self.assertIn("btn.hidden = filtering", src)

    def test_empty_and_search_empty_copy_are_distinct(self):
        src = _read(_GROUPS)
        self.assertIn("No Person Groups yet.", src)
        self.assertIn("openModal(\\'group-modal\\')", src)
        self.assertIn("No groups match your search.", src)
        self.assertNotIn("Use <strong>New group</strong> in the ribbon", src)

    def test_detail_prioritizes_description_hierarchy_and_members(self):
        src = _read(_GROUPS)
        detail = src.split("function renderPersonGroupDetail", 1)[1].split(
            "function prksPersonEditFindGroupByNameInsensitive", 1
        )[0]
        self.assertIn("Description", detail)
        self.assertIn("No description yet.", detail)
        self.assertIn("Hierarchy", detail)
        self.assertIn("Top-level group", detail)
        self.assertIn("Manage members", detail)
        self.assertIn("renderPersonGroupAddMemberPanelHtml()", detail)
        self.assertIn("removeButton: membersEditing", detail)
        self.assertIn('href="#/people/groups/${encodeURIComponent', detail)

    def test_metadata_and_member_management_are_separate(self):
        src = _read(_GROUPS)
        ui = _read(_UI)
        context = _read(_TAB_CONTEXT)
        self.assertIn("personGroupMembersEditing: false", context)
        self.assertIn("ui.personGroupMembersEditing = false", context)
        self.assertIn("ctx.ui.personGroupMembersEditing = false", src)
        self.assertIn("ctx.ui.personGroupEditing = false", src)
        self.assertIn("function prksTogglePersonGroupMembersEdit", src)
        self.assertIn("is-group-members-editing", src)
        self.assertNotIn("renderPersonGroupAddMemberPanelHtml()", ui)
        self.assertNotIn("mountPersonGroupAddMemberControls(g)", ui)

    def test_same_group_refresh_keeps_members_mode_but_other_groups_reset(self):
        app = _read(_APP)
        self.assertIn("const previousPersonGroup = ctx.getEntity && ctx.getEntity('personGroup');", app)
        self.assertIn("const previousPersonGroupId", app)
        self.assertIn("const previousPersonGroupMembersEditing", app)
        self.assertIn("const preserveMembersEditing =", app)
        self.assertIn("previousPersonGroupId === String(group.id)", app)
        self.assertIn("ctx.ui.personGroupMembersEditing = preserveMembersEditing;", app)
        self.assertIn("ctx.ui.personGroupEditing = false;", app)

    def test_member_picker_async_mount_checks_original_owner_state(self):
        src = _read(_GROUPS)
        mount = src.split("async function mountPersonGroupAddMemberControls", 1)[1].split(
            "function renderPersonGroupAddMemberPanelHtml", 1
        )[0]
        self.assertIn("const generation =", mount)
        self.assertLess(mount.index("const generation ="), mount.index("await fetchPersons()"))
        self.assertIn("ownerCtx.isCurrent(generation)", mount)
        self.assertIn("ownerCtx.ui.personGroupMembersEditing", mount)
        self.assertIn("ownerCtx.getEntity('personGroup')", mount)
        self.assertIn("liveInput !== input", mount)
        self.assertLess(mount.index("const persons = await fetchPersons();"), mount.index("allPersons = persons;"))
        self.assertLess(mount.index("allPersons = persons;"), mount.index("initSearchableCombobox("))

    def test_member_picker_publishes_fresh_people_only_for_live_owner(self):
        _member_picker_harness("fresh")

    def test_stale_member_picker_keeps_existing_people_cache(self):
        _member_picker_harness("stale")

    def test_metadata_form_keeps_typed_parent_and_separate_delete(self):
        src = _read(_GROUPS)
        form = src.split("function renderPersonGroupEditSidebarHtml", 1)[1].split(
            "function prksSyncPersonGroupMemberEditUi", 1
        )[0]
        for heading in ("Identity", "Hierarchy", "Description"):
            self.assertIn(f">{heading}</h4>", form)
        self.assertIn("type a new name to create a parent when saving", form)
        self.assertIn("group-sidebar__sticky-actions", form)
        self.assertIn("<summary>Advanced</summary>", form)
        self.assertIn("Delete group", form)

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
