"""Regression contracts for People index and Person profile presentation."""
import os
import json
import subprocess
import unittest


_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PEOPLE = os.path.join(_PROJECT_DIR, "frontend", "js", "components", "people.js")
_CSS = os.path.join(_PROJECT_DIR, "frontend", "css", "style.css")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _extract_function(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
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
    raise AssertionError(f"unterminated function {name}")


class FrontendPeopleTests(unittest.TestCase):
    def test_people_creation_uses_canonical_modal_from_header_and_true_empty(self):
        src = _read(_PEOPLE)
        library = src.split("function renderPeopleList", 1)[1].split(
            "function prksPersonDraftFromEntity", 1
        )[0]
        empty = src.split("function prksPeopleListEmptyHtml", 1)[1].split(
            "function prksPeopleListInnerHtml", 1
        )[0]
        self.assertIn("New Person", library)
        self.assertIn("openModal('person-modal')", library)
        self.assertIn("No people yet.", empty)
        self.assertIn("openModal(\\'person-modal\\')", empty)
        self.assertIn("No people match your search.", empty)
        self.assertIn("if (roleFilter && roleFiltered.length === 0)", empty)
        search_empty = next(line for line in empty.splitlines() if "No people match your search." in line)
        self.assertNotIn("person-modal", search_empty)

    def test_rows_show_research_context_not_reference_completion(self):
        src = _read(_PEOPLE)
        details = src.split("function buildPersonListDetailsHtml", 1)[1].split(
            "/** Inner HTML", 1
        )[0]
        self.assertIn("person-card-about", details)
        self.assertIn("prks-people-list__roles", details)
        self.assertIn('href="#/people/groups/${encodeURIComponent', details)
        self.assertNotIn("personExternalRefsSummary", details)
        self.assertNotIn("prks-people-list__refs", details)
        self.assertNotIn("No biography or links yet.", details)
        self.assertIn("-webkit-line-clamp: 2", _read(_CSS))

    def test_people_search_runtime_state_is_local_to_its_rendered_root(self):
        src = _read(_PEOPLE)
        rerender = src.split("function prksRerenderPeopleListOnly", 1)[1].split(
            "function prksSyncPeopleLibrarySearchClear", 1
        )[0]
        apply_filter = src.split("function prksApplyPeopleLibrarySearchFilter", 1)[1].split(
            "function prksBindPeopleLibrarySearch", 1
        )[0]
        render = src.split("function renderPeopleList", 1)[1].split(
            "function prksPersonDraftFromEntity", 1
        )[0]
        empty = src.split("function prksPeopleListEmptyHtml", 1)[1].split(
            "function prksPeopleListInnerHtml", 1
        )[0]
        self.assertNotIn("window.__prksPeopleLibraryState", src)
        self.assertIn("root.__prksPeopleLibraryState", rerender)
        self.assertIn("function prksRerenderPeopleListOnly(root)", src)
        self.assertIn("input.closest('.prks-people-library')", apply_filter)
        self.assertIn("prksRerenderPeopleListOnly(root)", apply_filter)
        self.assertIn("root.__prksPeopleLibraryState = { persons: list, container, filterQuery, roleFilter }", render)
        self.assertLess(
            empty.index("if (roleFilter && roleFiltered.length === 0)"),
            empty.index("if (q)"),
        )

    def test_role_route_hides_redundant_role_and_keeps_other_roles(self):
        src = _read(_PEOPLE)
        details = src.split("function buildPersonListDetailsHtml", 1)[1].split(
            "/** Inner HTML", 1
        )[0]
        self.assertIn("assignedRoles.filter((role) => role !== roleFilter)", details)
        listing = src.split("function prksPeopleListInnerHtml", 1)[1].split(
            "function prksRerenderPeopleListOnly", 1
        )[0]
        self.assertIn("buildPersonListRowHtml(p, { roleFilter })", listing)

    def test_profile_uses_anchor_groups_and_local_relationship_action(self):
        src = _read(_PEOPLE)
        detail = src.split("function renderPersonDetails", 1)[1]
        self.assertIn('href="#/people/groups/${encodeURIComponent', detail)
        self.assertIn("Edit relationships", detail)
        self.assertIn("person-profile__works-action", detail)
        self.assertIn("worksEditing ? 'Done'", detail)
        self.assertNotIn("toUpperCase()", detail)
        self.assertIn("const roleContext = roleList.join(' · ')", detail)
        self.assertNotIn("${credit} (${roleList.join(', ')})", detail)

    def test_sidebar_demotes_advanced_actions_and_edit_form_is_grouped(self):
        src = _read(_PEOPLE)
        sidebar = src.split("function renderPersonProfileDetailsSidebarHtml", 1)[1].split(
            "function renderPersonProfileEditFormHtml", 1
        )[0]
        form = src.split("function renderPersonProfileEditFormHtml", 1)[1].split(
            "async function savePersonProfile", 1
        )[0]
        self.assertIn("<summary>More</summary>", sidebar)
        self.assertIn("Edit using template", sidebar)
        self.assertIn("Delete person", sidebar)
        self.assertIn("disabled title=\"Unlink all files first\"", sidebar)
        self.assertNotIn("Edit works", sidebar)
        for heading in ("Identity", "Biography", "Dates", "Portrait", "References", "Groups"):
            self.assertIn(f">{heading}</h4>", form)
        self.assertIn("person-edit-footer", form)
        self.assertIn('id="pd-save-btn"', form)
        self.assertNotIn("person-groups-fieldset__action\" onclick=\"savePersonProfile", form)

    def test_profile_editor_is_tab_context_draft_owned(self):
        src = _read(_PEOPLE)
        groups = _read(os.path.join(_PROJECT_DIR, "frontend", "js", "components", "people-groups.js"))
        self.assertIn("function prksEnsurePersonProfileDraft(ctx, person)", src)
        self.assertIn("function prksMountPersonProfileEditor(ctx, person)", src)
        self.assertIn('data-person-edit-id="${id}"', src)
        self.assertIn("renderPersonProfileEditFormHtml(person, draft)", src)
        self.assertIn("prksSyncPersonProfileDraftFromEditor(ctx, editor, personId, generation)", src)
        for field in (
            "first_name",
            "last_name",
            "aliases",
            "about",
            "birth_date",
            "death_date",
            "image_url",
            "link_wikipedia",
            "link_stanford_encyclopedia",
            "link_iep",
            "links_other",
        ):
            self.assertIn(field, src)
        self.assertIn("group_ids: (Array.isArray(draft.groups) ? draft.groups : [])", src)
        self.assertNotIn("prksGetPersonEditGroupIdsFromDom", src + groups)
        self.assertIn("prksMountPersonProfileGroupPicker(ctx, person, editor)", groups)

    def test_profile_dirty_predicate_detects_scalars_and_semantic_groups(self):
        src = _read(_PEOPLE)
        self.assertIn("function prksPersonProfileDraftIsDirty(ctx, person)", src)
        js = "\n".join(
            (
                "const personDateToDisplayFormat = value => String(value == null ? '' : value);",
                _extract_function(src, "prksPersonDraftFromEntity"),
                _extract_function(src, "prksPersonProfileDraftIsDirty"),
                r"""
const person = {
  id: 'P1', first_name: 'Ada', last_name: 'Alpha', aliases: 'A', about: 'Bio',
  birth_date: '1970', death_date: '', image_url: 'img', link_wikipedia: 'wiki',
  link_stanford_encyclopedia: 'sep', link_iep: 'iep', links_other: 'other',
  groups: [{ id: 2, name: 'Two' }, { id: 1, name: 'One' }]
};
const draft = prksPersonDraftFromEntity(person);
const ctx = { ui: { personProfileDraft: draft } };
const fields = ['first_name', 'last_name', 'aliases', 'about', 'birth_date', 'death_date',
  'image_url', 'link_wikipedia', 'link_stanford_encyclopedia', 'link_iep', 'links_other'];
const pristine = prksPersonProfileDraftIsDirty(ctx, person);
const scalarDirty = fields.map(key => {
  const old = draft[key]; draft[key] = old + ' changed';
  const dirty = prksPersonProfileDraftIsDirty(ctx, person);
  draft[key] = old;
  return dirty;
});
const restored = prksPersonProfileDraftIsDirty(ctx, person);
draft.groups = [{ id: '1', name: 'renamed locally' }, { id: '2', name: 'Two' }];
const reordered = prksPersonProfileDraftIsDirty(ctx, person);
draft.groups = [{ id: 1, name: 'One' }];
const removed = prksPersonProfileDraftIsDirty(ctx, person);
draft.groups = [{ id: 1 }, { id: 2 }, { id: 3 }];
const added = prksPersonProfileDraftIsDirty(ctx, person);
process.stdout.write(JSON.stringify({ pristine, scalarDirty, restored, reordered, removed, added }));
""",
            )
        )
        proc = subprocess.run(
            ["node", "-e", js], capture_output=True, text=True, check=False, timeout=15
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertFalse(result["pristine"])
        self.assertTrue(all(result["scalarDirty"]))
        self.assertFalse(result["restored"])
        self.assertFalse(result["reordered"])
        self.assertTrue(result["removed"])
        self.assertTrue(result["added"])


if __name__ == "__main__":
    unittest.main()
