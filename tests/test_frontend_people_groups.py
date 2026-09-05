"""Regression contracts for Person Group presentation and ownership."""
import os
import unittest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GROUPS = os.path.join(_ROOT, "frontend", "js", "components", "people-groups.js")
_TAB_CONTEXT = os.path.join(_ROOT, "frontend", "js", "tab-context.js")
_UI = os.path.join(_ROOT, "frontend", "js", "ui.js")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


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


if __name__ == "__main__":
    unittest.main()
