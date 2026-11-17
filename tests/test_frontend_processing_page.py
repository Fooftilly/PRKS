"""Processing: the route mounts the PostgreSQL upload queue once the content is confirmed.

The path-based inbox page (``frontend/js/components/processing-files.js`` and
``frontend-app/src/features/processing/``) is gone with the SQLite runtime
(PG-C2 #641); ``tests/sqlite_retirement/test_frontend_processing_page.partial.json``
records its tests. The queue page's own tests live beside it in
``frontend-app/src/features/processing-queue/``.
"""
import json
import os
import shutil
import subprocess
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_PROJECT_DIR, "frontend", "js", "app.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class FrontendProcessingPageTests(unittest.TestCase):
    def test_the_path_based_inbox_page_is_gone(self):
        for relpath in (
            "frontend/js/components/processing-files.js",
            "frontend-app/src/features/processing",
            "frontend-app/src/api/processing-files.ts",
        ):
            self.assertFalse(os.path.exists(os.path.join(_PROJECT_DIR, relpath)), relpath)
        index = _read(os.path.join(_PROJECT_DIR, "frontend", "index.html"))
        self.assertNotIn("processing-files.js", index)
        sw = _read(os.path.join(_PROJECT_DIR, "frontend", "sw.js"))
        self.assertNotIn("processing-files.js", sw)
        main = _read(os.path.join(_PROJECT_DIR, "frontend-app", "src", "main.ts"))
        self.assertNotIn("registerProcessingBridge", main)
        self.assertIn("registerProcessingQueueBridge", main)

    def test_processing_vue_maps_to_the_processing_files_e2e(self):
        policy = _read(os.path.join(_PROJECT_DIR, "tests", "e2e", "policy.py"))
        self.assertNotIn('"name": "processing-vue"', policy)
        rule = policy[policy.index('"name": "processing-queue-vue"'):policy.index('"name": "concepts-vue"')]
        self.assertIn('"features": ("postgres-library", "processing")', rule)
        self.assertIn("TabContextHostRootTests", rule)
        self.assertIn("#/processing-files", rule)
        self.assertNotIn('"features": ("browse",)', rule)
        e2e = _read(os.path.join(_PROJECT_DIR, "tests", "e2e", "test_app.py"))
        self.assertIn('a.nav-link[href="#/processing-files"]', e2e)
        self.assertIn("data-prks-processing-queue", e2e)

    @unittest.skipUnless(shutil.which("node"), "node is required")
    def test_content_mode_waits_for_a_confirmed_answer_and_learns_it_without_reload(self):
        """An unbound or offline-bound page names no content (no Processing
        page), and learns it from the first confirmed answer. Nothing but
        'postgres' is a content: the path-based SQLite inbox is gone."""
        app = _read(_APP)
        fn = "async function prksLibraryContentMode(" + app.split("async function prksLibraryContentMode(", 1)[1].split(
            "\n}\n", 1
        )[0] + "\n}\n"
        script = fn + """
const out = [];
function stub(bound, answers) {
    let content = null;
    return {
        resolve: async () => bound,
        libraryContent: () => content,
        verify: async () => { content = answers.shift() || null; return 'same'; },
    };
}
(async () => {
    globalThis.window = {};
    out.push(await prksLibraryContentMode());
    window.prksBrowserScope = stub(null, []);
    out.push(await prksLibraryContentMode());
    window.prksBrowserScope = stub({ libraryId: 'L' }, [null, 'postgres']);
    out.push(await prksLibraryContentMode());
    out.push(await prksLibraryContentMode());
    out.push(await prksLibraryContentMode());
    window.prksBrowserScope = stub({ libraryId: 'L' }, ['sqlite']);
    out.push(await prksLibraryContentMode());
    console.log(JSON.stringify(out));
})();
"""
        done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), [None, None, None, "postgres", "postgres", None])
        route = app[app.index("case 'processing-files':"):app.index("case 'search':")]
        self.assertIn("if (contentMode !== 'postgres') throw new Error(", route)
        self.assertIn("prksPresentVueRoute(ctx, contentDiv, 'processing-queue'", route)
        badge = app.split("async function prksRefreshProcessingNavBadge(", 1)[1].split(
            "async function prksRefreshSyncQueueCue(", 1
        )[0]
        self.assertIn("if (mode !== 'postgres') {", badge)
        self.assertNotIn("prksProcessingRecords", badge)
        self.assertIn("prksSetProcessingAttentionCount(null)", badge)



if __name__ == "__main__":
    unittest.main()
