"""Route-surface dismissal has one window entry, owned by the shared lifecycle."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "js" / "app.js"
FRONTEND_JS = ROOT / "frontend" / "js"
FRONTEND_APP = ROOT / "frontend-app"
LIFECYCLE = FRONTEND_APP / "src" / "route-surface" / "lifecycle.ts"
FEATURES = FRONTEND_APP / "src" / "features"

# Work surfaces (main surface, notes, PDF popup/drawer, panel, metadata) are not
# route surfaces and keep their own dismiss entries.
_PER_ROUTE_DISMISS_RE = re.compile(r"prksVueDismiss(?!Route\b|Work)\w+")

_RETAINED_FLAGS = (
    "sameFolderLibraryWorkspace",
    "sameConceptsWorkspace",
    "samePositionsWorkspace",
    "sameArgumentsWorkspace",
    "samePlaylistsWorkspace",
    "samePeopleWorkspace",
    "samePersonGroupsWorkspace",
    "sameFolderWorkspace",
)


def _sources():
    yield from FRONTEND_JS.rglob("*.js")
    yield FRONTEND_APP / "env.d.ts"
    for path in (FRONTEND_APP / "src").rglob("*"):
        if path.suffix in (".ts", ".vue"):
            yield path


class RouteDismissContracts(unittest.TestCase):
    def test_no_per_route_dismiss_globals_remain(self):
        hits = []
        for path in _sources():
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                if _PER_ROUTE_DISMISS_RE.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
        self.assertEqual(hits, [])

    def test_lifecycle_owns_the_dismiss_entry(self):
        src = LIFECYCLE.read_text()
        bridge = src[src.index("export function registerRouteWindowBridge") :]
        bridge = bridge[: bridge.index("\n}\n")]
        self.assertIn("target.prksVuePresentRoute = presentRegisteredRoute", bridge)
        self.assertIn("target.prksVueDismissRoute = dismissRouteSurface", bridge)
        for session in FEATURES.glob("*/session.ts"):
            self.assertNotIn("target.prksVueDismiss", session.read_text(), session.name)

    def test_coordinator_dismisses_once_per_path(self):
        app = APP.read_text()
        self.assertEqual(app.count("window.prksVueDismissRoute(ctx)"), 2)
        catch = app[app.index("const retainedRouteSurface =") :]
        catch = catch[: catch.index(";")]
        for flag in _RETAINED_FLAGS:
            self.assertIn(flag, catch)


if __name__ == "__main__":
    unittest.main()
