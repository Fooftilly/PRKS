"""The typed route model owns the hash parser; navigation.js and app.js consume it."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "frontend-app" / "src" / "routing" / "route-model.ts"
BUILT = ROOT / "frontend" / "js" / "route-model.js"
NAVIGATION = ROOT / "frontend" / "js" / "navigation.js"
APP = ROOT / "frontend" / "js" / "app.js"
INDEX = ROOT / "frontend" / "index.html"
SW = ROOT / "frontend" / "sw.js"


def _route_names() -> list[str]:
    src = MODEL.read_text(encoding="utf-8")
    body = src[src.index("export const ROUTE_META = {") : src.index("} as const satisfies Record<string, RouteMeta>")]
    return re.findall(r"^  '?([a-z][a-z-]*)'?: \{$", body, flags=re.M)


def _dispatch_cases() -> list[str]:
    app = APP.read_text(encoding="utf-8")
    switch = app[app.index("switch (route.name) {") :]
    switch = switch[: switch.index("default: {")]
    return re.findall(r"^\s+case '([a-z-]+)': \{", switch, flags=re.M)


class RouteModelContracts(unittest.TestCase):
    def test_registry_names_are_dispatched_exactly_once(self):
        names = _route_names()
        self.assertIn("unknown", names)
        self.assertIn("work", names)
        cases = _dispatch_cases()
        self.assertEqual(len(cases), len(set(cases)))
        self.assertEqual(sorted(cases), sorted(n for n in names if n != "unknown"))

    def test_navigation_aliases_the_model_and_keeps_no_parser(self):
        nav = NAVIGATION.read_text(encoding="utf-8")
        self.assertIn("root.prksRouteModel", nav)
        self.assertIn("require('./route-model.js')", nav)
        for alias in (
            "const PRKS_HOME_HASH = prksRouteModel.HOME_HASH;",
            "const PRKS_PROGRESS_STATUS_VALUES = prksRouteModel.WORK_STATUSES;",
            "const PRKS_PEOPLE_ROLES = prksRouteModel.PEOPLE_ROLES;",
            "const PRKS_ROUTE_META = prksRouteModel.ROUTE_META;",
            "const prksParseRoute = prksRouteModel.parseRoute;",
            "const prksParseGraphFocus = prksRouteModel.parseGraphFocus;",
            "const prksGraphFocusHash = prksRouteModel.graphFocusHash;",
            "const prksIsRecognizedRoute = prksRouteModel.isRecognizedRoute;",
        ):
            self.assertIn(alias, nav)
        for gone in (
            "function prksParseRoute(",
            "function prksSplitHash(",
            "function prksSafeDecode(",
            "function prksRouteRecord(",
            "PRKS_GRAPH_FOCUS_RE",
            "loadingTitle:",
        ):
            self.assertNotIn(gone, nav)

    def test_domain_vocabularies_are_not_owned_by_routing(self):
        """The parser depends on domain vocabularies; domain code never learns them from routing."""
        src = ROOT / "frontend-app" / "src"
        model = MODEL.read_text(encoding="utf-8")
        self.assertIn("from '../domain/work-status'", model)
        self.assertIn("from '../domain/people-roles'", model)
        self.assertNotIn("'Not Started', 'Planned'", model)
        self.assertNotIn("'Author', 'Editor'", model)
        for path in (src / "domain").glob("*.ts"):
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"from '[./]+/routing/", path.name)
        literals = []
        for path in src.rglob("*"):
            if path.suffix not in (".ts", ".vue") or ".test." in path.name or path.parent.name == "domain":
                continue
            text = path.read_text(encoding="utf-8")
            if "'Not Started', 'Planned'" in text or "'Author', 'Editor', 'Reviewer'" in text:
                literals.append(str(path.relative_to(ROOT)))
        self.assertEqual(literals, [])
        status = (src / "features" / "progress" / "status.ts").read_text(encoding="utf-8")
        self.assertIn("from '../../domain/work-status'", status)
        self.assertNotIn("progressStatusFromHash", status)
        self.assertNotIn("URLSearchParams", status)

    def test_built_script_loads_before_navigation(self):
        built = BUILT.read_text(encoding="utf-8")
        self.assertIn("var prksRouteModel", built)
        self.assertIn("src/routing/route-model.ts", built)
        html = INDEX.read_text(encoding="utf-8")
        model_at = html.index('src="/js/route-model.js"')
        self.assertLess(model_at, html.index('src="/js/navigation.js"'))
        sw = SW.read_text(encoding="utf-8")
        self.assertLess(sw.index("'/js/route-model.js'"), sw.index("'/js/navigation.js'"))


if __name__ == "__main__":
    unittest.main()
