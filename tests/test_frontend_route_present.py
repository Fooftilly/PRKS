"""Route-surface presentation has one coordinator entry that owns the host."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_JS = ROOT / "frontend" / "js"
APP = FRONTEND_JS / "app.js"

# Feature-specific pre-paint steps that remain as thin wrappers around the
# shared presenter: the Research Graph disposes its Cytoscape instance, and
# Processing publishes its people catalogue and releases the preview pane.
_WRAPPERS = {"prksPresentVueResearchGraph", "prksPresentVueProcessing"}


class RouteSurfacePresentContracts(unittest.TestCase):
    def test_only_the_shared_presenter_creates_route_hosts(self):
        hits = []
        for path in FRONTEND_JS.rglob("*.js"):
            text = path.read_text()
            count = text.count("setAttribute('data-prks-vue-route-host'")
            if count:
                hits.append((path.name, count))
        self.assertEqual(hits, [("app.js", 1)])
        app = APP.read_text()
        present = app[app.index("function prksPresentVueRoute(") : app.index("async function prksReloadTagsVocabulary")]
        self.assertIn("setAttribute('data-prks-vue-route-host'", present)
        self.assertIn("prksDeliverVueRoute(host, request)", present)

    def test_per_feature_route_presenters_are_retired(self):
        names = set()
        for path in FRONTEND_JS.rglob("*.js"):
            names.update(re.findall(r"\bfunction (prksPresentVue(?!Route\b|Work)\w+)\(", path.read_text()))
        self.assertEqual(names, _WRAPPERS)
        app = APP.read_text()
        for name in _WRAPPERS:
            body = app[app.index(f"function {name}(") :]
            body = body[: body.index("\n}\n")]
            self.assertIn("prksPresentVueRoute(ctx, contentDiv, '", body)
            self.assertNotIn("prksDeliverVueRoute", body)

    def test_envelope_overrides_caller_fields(self):
        app = APP.read_text()
        present = app[app.index("function prksPresentVueRoute(") : app.index("async function prksReloadTagsVocabulary")]
        envelope = present[present.index("Object.assign({}, fields, {") :]
        envelope = envelope[: envelope.index("});")]
        for key in ("feature: feature", "owner: ctx", "shell:"):
            self.assertIn(key, envelope)


if __name__ == "__main__":
    unittest.main()
