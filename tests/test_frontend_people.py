"""Regression contracts for People index and Person profile presentation."""
import os
import unittest


_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PEOPLE = os.path.join(_PROJECT_DIR, "frontend", "js", "components", "people.js")
_CSS = os.path.join(_PROJECT_DIR, "frontend", "css", "style.css")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendPeopleTests(unittest.TestCase):
    def test_people_creation_uses_canonical_modal_from_header_and_true_empty(self):
        src = _read(_PEOPLE)
        library = src.split("function renderPeopleList", 1)[1].split(
            "async function openPersonProfileEdit", 1
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
        self.assertIn("-webkit-line-clamp: 2", _read(_CSS))

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


if __name__ == "__main__":
    unittest.main()
