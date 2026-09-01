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

    def test_argument_editor_uses_form_pane_controls(self):
        args = _read(_ARGS)
        self.assertIn('class="prks-arg-form form-pane"', args)
        self.assertIn('class="add-new-btn"', args)
        css = _read(os.path.join(_FRONTEND, "css", "style.css"))
        self.assertIn(".prks-arg-form input[type=\"text\"]", css)
        self.assertIn("background: var(--surface-muted)", css)


if __name__ == "__main__":
    unittest.main()
