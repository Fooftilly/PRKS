"""Every shipped frontend script must parse.

This exists because a half-finished edit to `ui.js` -- an `else if (false) {`
that was never closed -- was committed and survived the whole fast suite. The
suite exercises frontend BEHAVIOUR through Node selftests and static contract
checks, but nothing required the browser entry points themselves to be
syntactically valid, so the one file the entire UI depends on could be broken
without a single test noticing.
"""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def _shipped_scripts():
    """Every .js the browser loads, plus the service worker."""
    return sorted(p for p in FRONTEND.rglob("*.js") if "node_modules" not in p.parts)


class FrontendSyntaxTests(unittest.TestCase):
    def test_every_shipped_script_parses(self):
        scripts = _shipped_scripts()
        self.assertGreater(len(scripts), 20, "no frontend scripts were found to check")
        broken = []
        for path in scripts:
            proc = subprocess.run(
                ["node", "--check", str(path)],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            if proc.returncode != 0:
                first = (proc.stderr.strip().splitlines() or [""])[-1]
                broken.append("%s: %s" % (path.relative_to(ROOT), first))
        self.assertEqual(broken, [], "these shipped scripts do not parse")

    def test_every_registered_sync_handler_is_defined_before_use(self):
        """`sync-runtime.js` builds its handler map at load time.

        A family whose module is not loaded -- or is loaded after it -- reads as
        `undefined` there, silently. The coordinator then holds a registry entry
        it cannot dispatch, so an operation of that family reaches durable
        storage and can never be sent. That is exactly what happened to
        CREATE_PERSON: `person-state.js` was written, registered, and never
        added to index.html.
        """
        import re

        runtime = (FRONTEND / "js" / "sync-runtime.js").read_text(encoding="utf-8")
        registry = runtime[runtime.index("handlers: {"):]
        registry = registry[: registry.index("\n        }")]
        handlers = sorted(set(re.findall(r"root\.(prks\w*SyncHandler)", registry)))
        self.assertGreater(len(handlers), 3, "no handler registrations were found")

        index = (FRONTEND / "index.html").read_text(encoding="utf-8")
        order = re.findall(r'<script[^>]+src="/([^"]+\.js)"', index)
        self.assertIn("js/sync-runtime.js", order)
        runtime_at = order.index("js/sync-runtime.js")

        for handler in handlers:
            with self.subTest(handler=handler):
                definers = [
                    src for src in order
                    if ("%s:" % handler) in (FRONTEND / src).read_text(encoding="utf-8")
                ]
                self.assertTrue(
                    definers,
                    "%s is registered but no script index.html loads defines it" % handler,
                )
                self.assertTrue(
                    any(order.index(src) < runtime_at for src in definers),
                    "%s is defined only AFTER sync-runtime.js, so it registers as "
                    "undefined" % handler,
                )

    def test_every_script_index_html_loads_is_present(self):
        """A <script src> pointing at a file that does not exist fails silently
        in a browser -- the global it defines is simply never there, which is
        how a missing handler registration reaches production."""
        import re

        index = (FRONTEND / "index.html").read_text(encoding="utf-8")
        missing = []
        for src in re.findall(r'<script[^>]+src="(/[^"]+\.js)"', index):
            if not (FRONTEND / src.lstrip("/")).is_file():
                missing.append(src)
        self.assertEqual(missing, [], "index.html loads scripts that do not exist")
