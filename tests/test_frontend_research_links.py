"""Structural regressions for research-note semantic links and Research UI."""
import os
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_PROJECT_DIR, "frontend")
_INDEX = os.path.join(_FRONTEND, "index.html")
_WORKS = os.path.join(_FRONTEND, "js", "components", "works.js")
_APP = os.path.join(_FRONTEND, "js", "app.js")
_NAV = os.path.join(_FRONTEND, "js", "navigation.js")
_ROUTE_MODEL = os.path.join(_PROJECT_DIR, "frontend-app", "src", "routing", "route-model.ts")
_LINKS = os.path.join(_FRONTEND, "js", "research-links.js")
_ARGS = os.path.join(_FRONTEND, "js", "components", "arguments.js")
_ARGS_VUE_INDEX = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "arguments", "ArgumentsIndexRoute.vue"
)
_ARGS_VUE_DETAIL = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "arguments", "ArgumentDetailRoute.vue"
)
_ARGS_MATCH = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "arguments", "match.ts"
)
_ARGS_ROW = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "arguments", "ArgumentRow.vue"
)
_CONCEPTS = os.path.join(_FRONTEND, "js", "components", "concepts.js")
_CONCEPTS_VUE_INDEX = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "concepts", "ConceptsIndexRoute.vue"
)
_CONCEPTS_VUE_DETAIL = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "concepts", "ConceptDetailRoute.vue"
)
_POSITIONS = os.path.join(_FRONTEND, "js", "components", "positions.js")
_POSITIONS_VUE_INDEX = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "positions", "PositionsIndexRoute.vue"
)
_POSITIONS_VUE_DETAIL = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "positions", "PositionDetailRoute.vue"
)
_POSITIONS_MATCH = os.path.join(
    _PROJECT_DIR, "frontend-app", "src", "features", "positions", "match.ts"
)
_UI = os.path.join(_FRONTEND, "js", "ui.js")
_CREATE_FLOW_SELFTEST = os.path.join(
    _PROJECT_DIR, "tests", "browser", "run_concept_create_flow_selftest.js"
)


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
        self.assertIn("head === 'concepts'", _read(_ROUTE_MODEL))
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
        positions_intents = _read(
            os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "positions", "intents.ts")
        )
        self.assertIn("prksPromptTextDialog", positions_intents)
        self.assertNotIn("fetch(", positions_intents)

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
        concepts_row = _read(
            os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "concepts", "ConceptRow.vue")
        )
        positions = _read(
            os.path.join(
                _PROJECT_DIR, "frontend-app", "src", "features", "positions", "PositionRow.vue"
            )
        )
        args = _read(_ARGS_VUE_INDEX)
        args_row = _read(_ARGS_ROW)
        self.assertIn("prks-research-row", concepts)
        self.assertIn("prksResearchIndexRowHtml", concepts)
        self.assertIn("Top-level concept", concepts_row)
        self.assertNotIn("No parent", concepts_row)
        self.assertNotIn("Parents:", concepts_row)
        self.assertIn("prks-research-row", positions)
        self.assertIn("prks-research-row", args_row)
        self.assertIn("prks-tab", args)
        self.assertIn("prks-tabs", args)

    def test_research_index_search_is_shared_and_client_only(self):
        concepts = _read(_CONCEPTS)
        concepts_match = _read(
            os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "concepts", "match.ts")
        )
        positions_match = _read(_POSITIONS_MATCH)
        args_match = _read(_ARGS_MATCH)
        # Shared helper remains for any remaining legacy binder. Concepts,
        # Positions, and Arguments Vue own local filters with the same match
        # semantics.
        self.assertIn("function bindResearchIndexSearch", concepts)
        self.assertIn("function normalizeSearchQuery", concepts)
        self.assertIn("prksBindResearchIndexSearch: bindResearchIndexSearch", concepts)
        self.assertNotIn("function bindResearchIndexSearch", args_match)
        # Purely local filtering: no network call is part of the search path.
        search_block = concepts.split("function bindResearchIndexSearch", 1)[1].split(
            "function researchIndexToolbarHtml", 1
        )[0]
        self.assertNotIn("fetch", search_block)
        self.assertNotIn("prksRequest", search_block)
        self.assertIn("input.addEventListener('input'", search_block)
        # Argument kind filter stays a real route/query param; search only narrows within it.
        self.assertIn("return `#/arguments?kind=${encodeURIComponent(filter)}`", args_match)
        self.assertIn("export function matchArgumentIndexItem", args_match)
        self.assertNotIn("fetch", args_match)
        self.assertNotIn("prksRequest", args_match)
        self.assertIn("export function matchConceptIndexItem", concepts_match)
        self.assertNotIn("fetch", concepts_match)
        self.assertIn("export function matchPositionIndexItem", positions_match)
        self.assertNotIn("fetch", positions_match)
        self.assertNotIn("prksRequest", positions_match)

    def test_research_index_empty_states_are_distinct(self):
        concepts = _read(_CONCEPTS)
        concepts_vue = _read(_CONCEPTS_VUE_INDEX)
        positions_vue = _read(_POSITIONS_VUE_INDEX)
        args = _read(_ARGS_VUE_INDEX)
        self.assertIn("function researchIndexSearchEmptyHtml", concepts)
        self.assertIn("data-research-search-clear", concepts)
        self.assertIn("prks-concept-new-empty", concepts_vue)
        self.assertIn("No Concepts yet.", concepts_vue)
        self.assertIn("data-research-search-clear", concepts_vue)
        self.assertIn("prks-position-new-empty", positions_vue)
        self.assertIn("prks-argument-new-empty", args)
        self.assertIn("prks-stance-new-empty", args)
        self.assertIn("No Positions yet.", positions_vue)
        self.assertIn("No Arguments or Stances yet.", _read(_ARGS_MATCH))

    def test_research_entity_sections_use_shared_head_pattern(self):
        concepts = _read(_CONCEPTS)
        concepts_detail = _read(_CONCEPTS_VUE_DETAIL)
        positions_detail = _read(_POSITIONS_VUE_DETAIL)
        args = _read(_ARGS_VUE_DETAIL)
        self.assertIn("function researchSectionHeadHtml", concepts)
        self.assertIn("research-entity__section-head", concepts)
        self.assertIn("prksResearchSectionHeadHtml: researchSectionHeadHtml", concepts)
        self.assertIn("prksResearchSectionHeadHtml", positions_detail)
        self.assertIn("prksResearchSectionHeadHtml", args)
        # Concept detail (Vue): canonical section set, each a real .research-entity__section.
        for heading in (
            "Definition",
            "Search keys / aliases",
            "Parent concepts",
            "Subconcepts",
            "Mentioned in research notes",
        ):
            self.assertIn(heading, concepts_detail)
        self.assertIn("research-entity__chips", concepts_detail)
        self.assertIn("research-entity__alias-chip", concepts_detail)
        self.assertIn("research-entity__mentions", concepts_detail)
        self.assertIn("research-entity__mention-title", concepts_detail)
        # Parent/child rows and section heads go through the shared helpers
        # (DESIGN.md), not hand-built Vue markup that can drift from Positions.
        self.assertNotIn("<li><a href=", concepts_detail)
        self.assertIn("prksResearchIndexRowHtml", concepts_detail)
        self.assertIn("prksResearchSectionHeadHtml", concepts_detail)
        # Position detail uses the same research-entity shell.
        self.assertIn("research-entity", positions_detail)
        self.assertIn("No description yet.", positions_detail)
        self.assertNotIn("project-card", positions_detail)
        # Argument section counts/contextual empty wording, without disturbing edit mode.
        self.assertIn("No targets.", args)
        self.assertIn("No sources.", args)
        self.assertIn("No responses.", args)
        self.assertIn("Not mentioned in research notes.", args)
        self.assertIn("count: current.targets.length", args)
        self.assertIn("count: current.sources.length", args)
        self.assertIn("count: current.responses.length", args)
        self.assertIn("count: current.mentions.length", args)


    def test_concept_create_flow_owner_scoping_selftest(self):
        proc = subprocess.run(
            ["node", _CREATE_FLOW_SELFTEST],
            cwd=_PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("concept create-flow selftest ok", proc.stdout)

    def test_destructive_actions_are_visually_subordinate(self):
        concepts_detail = _read(_CONCEPTS_VUE_DETAIL)
        args = _read(_ARGS_VUE_DETAIL)
        css = _read(os.path.join(_FRONTEND, "css", "style.css"))
        self.assertIn(".prks-btn--quiet-danger", css)
        self.assertIn('id="prks-concept-delete"', concepts_detail)
        self.assertIn('variant="quiet-danger"', concepts_detail)
        self.assertIn('id="prks-arg-delete"', args)
        self.assertIn('variant="quiet-danger"', args)
        button = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "components", "PrksButton.vue"
        ))
        self.assertIn("prks-btn--quiet-danger", button)
        # New response stays a prominent, always-visible primary action -- not moved
        # aside. Asserted on the button and its label rather than on exact
        # attribute order, which offline control roles legitimately extend.
        self.assertIn('id="prks-arg-response"', args)
        self.assertIn("New response", args)

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
        match = _read(_ARGS_MATCH)
        index = _read(_ARGS_VUE_INDEX)
        self.assertIn("export function argumentKindUi", match)
        kind_ui = match.split("export function argumentKindUi", 1)[1].split(
            "export function argumentIndexHash", 1
        )[0]
        self.assertIn("No Arguments yet.", kind_ui)
        self.assertIn("No Stances yet.", kind_ui)
        self.assertIn("No Arguments or Stances yet.", kind_ui)
        # True-empty and search-empty copy both key off the active canonical kind, not a
        # hardcoded "Arguments or Stances" -- an empty Stances route must not claim the
        # whole research-network subsystem is empty.
        self.assertIn("{{ kindUi.empty }}", index)
        self.assertIn("No {{ scopeLabel }} match", index)
        self.assertNotIn("No Arguments or Stances match", index)

    def test_argument_editor_uses_form_pane_controls(self):
        args = _read(_ARGS_VUE_DETAIL)
        intents = _read(
            os.path.join(_PROJECT_DIR, "frontend-app", "src", "features", "arguments", "intents.ts")
        )
        app = _read(_APP)
        self.assertIn('class="prks-arg-form form-pane"', args)
        self.assertIn('type="submit" variant="primary"', args)
        button = _read(os.path.join(
            _PROJECT_DIR, "frontend-app", "src", "components", "PrksButton.vue"
        ))
        self.assertIn("prks-btn--primary", button)
        self.assertIn("research-entity", args)
        self.assertIn("prks-arg-edit", args)
        self.assertIn("ctx.ui.argumentEditing = false", app)
        self.assertIn("argumentEditing", intents)
        self.assertIn("prksOpenResearchPicker", intents)
        self.assertNotIn('placeholder="Work id"', args)
        self.assertNotIn("P-… or A-…", args)
        css = _read(os.path.join(_FRONTEND, "css", "style.css"))
        self.assertIn(".prks-arg-form input[type=\"text\"]", css)
        self.assertIn("background: var(--surface-muted)", css)
        self.assertIn(".research-entity", css)


if __name__ == "__main__":
    unittest.main()
