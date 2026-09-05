"""Structural regressions for research-note semantic links and Research UI."""
import os
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_WORKS = os.path.join(_FRONTEND, "js", "components", "works.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_NAV = os.path.join(_FRONTEND, "js", "navigation.js")
_LINKS = os.path.join(_FRONTEND, "js", "research-links.js")
_ARGS = os.path.join(_FRONTEND, "js", "components", "arguments.js")
_CONCEPTS = os.path.join(_FRONTEND, "js", "components", "concepts.js")
_POSITIONS = os.path.join(_FRONTEND, "js", "components", "positions.js")
_UI = os.path.join(_FRONTEND, "js", "ui.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendResearchLinksTests(unittest.TestCase):
    def test_script_order(self):
        html = _read(_INDEX)
        links_at = html.find('src="/js/research-links.js"')
        works_at = html.find('src="/js/components/works.js"')
        concepts_at = html.find('src="/js/components/concepts.js"')
        app_at = html.find('src="/js/app.js"')
        self.assertNotEqual(links_at, -1)
        self.assertLess(links_at, works_at)
        self.assertLess(works_at, concepts_at)
        self.assertLess(concepts_at, app_at)

    def test_wiki_skips_research_prefixes(self):
        works = _read(_WORKS)
        self.assertIn("prksReplaceResearchRefs", works)
        self.assertIn("^(concept:|argument:|pdf:)", works)
        self.assertIn("prksGetConceptLinkAutocompleteContext", works)
        self.assertIn("prksOpenConceptPicker", works)
        self.assertIn("prksOpenArgumentPicker", works)
        self.assertIn("prksCreateArgumentFromWork", works)
        self.assertIn("[[concept:", works)
        self.assertIn("[[argument:", works)
        self.assertNotIn("work_concepts", works)

    def test_argument_create_from_work_does_not_navigate(self):
        args = _read(_ARGS)
        create = args.split("async function createArgumentFromWork", 1)[1].split(
            "const api =", 1
        )[0]
        self.assertNotIn("prksNavigate", create)
        self.assertIn("opts.workId", create)
        self.assertIn("opts.name", create)

    def test_routes_wired(self):
        app = _read(_APP)
        nav = _read(_NAV)
        self.assertIn("case 'concepts':", app)
        self.assertIn("case 'argument-detail':", app)
        self.assertIn("case 'position-detail':", app)
        self.assertIn("head === 'concepts'", nav)
        self.assertIn("prks.nav.researchExpanded", nav)
        html = _read(_INDEX)
        self.assertIn('data-nav-disclosure="research"', html)
        self.assertIn('id="prks-nav-research-children"', html)
        self.assertIn('href="#/concepts"', html)
        self.assertIn('href="#/arguments"', html)
        self.assertIn('href="#/graph"', html)

    def test_parser_module_exists(self):
        src = _read(_LINKS)
        self.assertIn("function parseResearchMarkup", src)
        self.assertIn("function replaceResearchRefs", src)
        self.assertIn("wiki-link-internal", src)

    def test_research_create_edit_use_in_app_prompt(self):
        ui = _read(_UI)
        self.assertIn("function prksPromptTextDialog", ui)
        self.assertIn("window.prksPromptTextDialog", ui)
        self.assertIn("prks-modal-prompt__input--multiline", ui)
        for path in (_CONCEPTS, _POSITIONS, _ARGS):
            src = _read(path)
            self.assertNotIn("window.prompt", src, path)
        self.assertIn("prksPromptTextDialog", src)

    def test_research_picker_uses_canonical_dialog(self):
        works = _read(_WORKS)
        picker = works.split("function prksOpenResearchPicker", 1)[1].split(
            "function prksOpenConceptPicker", 1
        )[0]
        self.assertIn('class="prks-dialog prks-research-picker__dialog"', picker)
        self.assertIn("prks-dialog__header", picker)
        self.assertIn("prks-dialog__title", picker)
        self.assertIn("prks-dialog__body", picker)
        self.assertIn("prks-dialog__actions", picker)
        self.assertIn('class="prks-input prks-research-picker__q"', picker)
        self.assertIn("prks-list-row", picker)
        self.assertNotIn("prks-research-picker__panel", picker)
        self.assertIn('Create “', works)
        self.assertIn("createItems", works)
        self.assertIn("Create Argument", works)
        self.assertIn("Create Stance", works)
        arg_picker = works.split("function prksOpenArgumentPicker", 1)[1].split(
            "async function deleteWork", 1
        )[0]
        self.assertNotIn("data-new=", arg_picker)
        self.assertNotIn("extraHtml", arg_picker)

    def test_research_indexes_use_dense_rows(self):
        concepts = _read(_CONCEPTS)
        positions = _read(_POSITIONS)
        args = _read(_ARGS)
        self.assertIn("prks-research-row", concepts)
        self.assertIn("prksResearchIndexRowHtml", concepts)
        self.assertIn("Top-level concept", concepts)
        self.assertNotIn("No parent", concepts)
        self.assertNotIn("Parents:", concepts.split("function conceptRowHtml", 1)[1].split("function matchConcept", 1)[0])
        self.assertIn("prks-research-row", positions)
        self.assertIn("prks-research-row", args)
        self.assertIn("prks-tab", args)
        self.assertIn("prks-tabs", args)

    def test_research_index_search_is_shared_and_client_only(self):
        concepts = _read(_CONCEPTS)
        positions = _read(_POSITIONS)
        args = _read(_ARGS)
        # One shared helper, reused by the other two index modules -- not three
        # unrelated search implementations.
        self.assertIn("function bindResearchIndexSearch", concepts)
        self.assertIn("function normalizeSearchQuery", concepts)
        self.assertIn("prksBindResearchIndexSearch: bindResearchIndexSearch", concepts)
        self.assertIn("root.prksBindResearchIndexSearch", positions)
        self.assertIn("root.prksBindResearchIndexSearch", args)
        self.assertNotIn("function bindResearchIndexSearch", positions)
        self.assertNotIn("function bindResearchIndexSearch", args)
        # Purely local filtering: no network call is part of the search path.
        search_block = concepts.split("function bindResearchIndexSearch", 1)[1].split(
            "function researchIndexToolbarHtml", 1
        )[0]
        self.assertNotIn("fetch", search_block)
        self.assertNotIn("prksRequest", search_block)
        self.assertIn("input.addEventListener('input'", search_block)
        # Argument kind filter stays a real route/query param; search only narrows within it.
        self.assertIn("k === 'all' ? '#/arguments' : '#/arguments?kind=", args)
        self.assertIn("matchArgument", args)
        self.assertIn("function matchConcept", concepts)
        self.assertIn("function matchPosition", positions)

    def test_research_index_empty_states_are_distinct(self):
        concepts = _read(_CONCEPTS)
        positions = _read(_POSITIONS)
        args = _read(_ARGS)
        self.assertIn("function conceptsEmptyDataHtml", concepts)
        self.assertIn("function researchIndexSearchEmptyHtml", concepts)
        self.assertIn("data-research-search-clear", concepts)
        self.assertIn("prks-concept-new-empty", concepts)
        self.assertIn("prks-position-new-empty", positions)
        self.assertIn("prks-argument-new-empty", args)
        self.assertIn("prks-stance-new-empty", args)
        self.assertIn("No Concepts yet.", concepts)
        self.assertIn("No Positions yet.", positions)
        self.assertIn("No Arguments or Stances yet.", args)

    def test_research_entity_sections_use_shared_head_pattern(self):
        concepts = _read(_CONCEPTS)
        positions = _read(_POSITIONS)
        args = _read(_ARGS)
        self.assertIn("function researchSectionHeadHtml", concepts)
        self.assertIn("research-entity__section-head", concepts)
        self.assertIn("prksResearchSectionHeadHtml: researchSectionHeadHtml", concepts)
        self.assertIn("root.prksResearchSectionHeadHtml", positions)
        self.assertIn("root.prksResearchSectionHeadHtml", args)
        # Concept detail: canonical section set, each a real .research-entity__section.
        for heading in (
            "Definition",
            "Search keys / aliases",
            "Parent concepts",
            "Subconcepts",
            "Mentioned in research notes",
        ):
            self.assertIn(heading, concepts)
        self.assertIn("research-entity__chips", concepts)
        self.assertIn("research-entity__alias-chip", concepts)
        self.assertIn("research-entity__mentions", concepts)
        self.assertIn("research-entity__mention-title", concepts)
        # Parent/child rows are canonical research rows, not raw <li> anchors.
        detail = concepts.split("function renderConceptDetail", 1)[1].split(
            "async function renameConcept", 1
        )[0]
        self.assertNotIn("<li><a href=", detail)
        self.assertIn("researchIndexRowHtml({", detail)
        # Position detail uses the same research-entity shell.
        self.assertIn("research-entity", positions)
        self.assertIn("No description yet.", positions)
        self.assertNotIn("project-card", positions)
        # Argument section counts/contextual empty wording, without disturbing edit mode.
        self.assertIn("No targets.", args)
        self.assertIn("No sources.", args)
        self.assertIn("No responses.", args)
        self.assertIn("Not mentioned in research notes.", args)
        self.assertIn("count: targetList.length", args)
        self.assertIn("count: sourceList.length", args)
        self.assertIn("count: responseList.length", args)
        self.assertIn("count: mentionList.length", args)

    def test_destructive_actions_are_visually_subordinate(self):
        concepts = _read(_CONCEPTS)
        args = _read(_ARGS)
        css = _read(os.path.join(_FRONTEND, "css", "style.css"))
        self.assertIn(".prks-btn--quiet-danger", css)
        self.assertIn("prks-concept-delete", concepts)
        self.assertIn("prks-btn--quiet-danger", concepts)
        self.assertIn("prks-arg-delete", args)
        self.assertIn("prks-btn--quiet-danger", args)
        # New response stays a prominent, always-visible primary action -- not moved aside.
        self.assertIn('id="prks-arg-response">New response', args)

    def test_research_index_clear_search_uses_a_current_controller_slot(self):
        concepts = _read(_CONCEPTS)
        search_fn = concepts.split("function bindResearchIndexSearch", 1)[1].split(
            "function researchIndexToolbarHtml", 1
        )[0]
        # The delegated Clear listener must read a replaceable "current controller" slot at
        # click time, never close over one render's own `input`/`apply()` -- ctx.root is a
        # persistent TabContext container that survives route changes and rerenders.
        self.assertIn("__prksResearchSearchController", search_fn)
        self.assertIn("controller.input.isConnected", search_fn)
        self.assertIn("controller.input.value = ''", search_fn)
        self.assertIn("controller.apply()", search_fn)
        self.assertNotIn("input.value = '';\n                input.focus();\n                apply();", search_fn)
        # Every bind call replaces the slot outright -- no per-route accumulation
        # (conceptController / positionController / argumentController-style state).
        self.assertIn(
            "container.__prksResearchSearchController = { input: input, apply: apply };", search_fn
        )
        self.assertNotIn("conceptController", concepts)
        self.assertNotIn("positionController", concepts)
        self.assertNotIn("argumentController", concepts)

    def test_argument_index_empty_states_are_kind_aware(self):
        args = _read(_ARGS)
        self.assertIn("function argumentKindUi", args)
        kind_ui = args.split("function argumentKindUi", 1)[1].split("function argumentsEmptyDataHtml", 1)[0]
        self.assertIn("No Arguments yet.", kind_ui)
        self.assertIn("No Stances yet.", kind_ui)
        self.assertIn("No Arguments or Stances yet.", kind_ui)
        # True-empty and search-empty copy both key off the active canonical kind, not a
        # hardcoded "Arguments or Stances" -- an empty Stances route must not claim the
        # whole research-network subsystem is empty.
        self.assertIn("argumentsEmptyDataHtml(kindUi)", args)
        self.assertIn("root.prksResearchIndexSearchEmptyHtml(kindUi.plural, query)", args)
        self.assertNotIn("argumentsEmptyDataHtml()", args)
        self.assertNotIn("prksResearchIndexSearchEmptyHtml('Arguments or Stances', query)", args)

    def test_argument_editor_uses_form_pane_controls(self):
        args = _read(_ARGS)
        self.assertIn('class="prks-arg-form form-pane"', args)
        self.assertIn('class="prks-btn prks-btn--primary"', args)
        self.assertIn("research-entity", args)
        self.assertIn("prks-arg-edit", args)
        self.assertIn("ctx.ui.argumentEditing", args)
        self.assertIn("prksOpenResearchPicker", args)
        self.assertNotIn('placeholder="Work id"', args)
        self.assertNotIn("P-… or A-…", args)
        css = _read(os.path.join(_FRONTEND, "css", "style.css"))
        self.assertIn(".prks-arg-form input[type=\"text\"]", css)
        self.assertIn("background: var(--surface-muted)", css)
        self.assertIn(".research-entity", css)


if __name__ == "__main__":
    unittest.main()
