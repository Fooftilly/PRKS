"""The shipped frontend runs under PRKS's Content-Security-Policy (#1305).

The policy refuses inline scripts and inline event handlers, so markup names
an action (`data-prks-action`) that one allowlisted listener in `ui.js` runs.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
UI = FRONTEND / "js" / "ui.js"
# The classic runtime and the shell. Vendored code is out of scope here; the
# E2E gate fails on any CSP violation it logs.
SOURCES = [FRONTEND / "index.html", *sorted((FRONTEND / "js").rglob("*.js"))]
INLINE_HANDLER = re.compile(r"""[\s"'`]on[a-z]{3,20}\s*=\s*\\?["']""")
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
ACTION_USE = re.compile(r'data-prks-action="([a-z-]+)"')


def _actions_allowed() -> set[str]:
    ui = UI.read_text(encoding="utf-8")
    start = ui.index("const PRKS_CLICK_ACTIONS = Object.freeze({")
    end = ui.index("});", start)
    return set(re.findall(r"^\s*'([a-z-]+)': \{", ui[start:end], re.MULTILINE))


class FrontendCspTests(unittest.TestCase):
    def test_no_inline_event_handler_or_inline_script_is_shipped(self) -> None:
        for path in SOURCES:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertEqual(INLINE_HANDLER.findall(text), [])
                self.assertEqual(INLINE_SCRIPT.findall(text), [])

    def test_every_named_action_is_in_the_allowlist(self) -> None:
        allowed = _actions_allowed()
        used = {name for path in SOURCES for name in ACTION_USE.findall(path.read_text(encoding="utf-8"))}
        self.assertTrue(used)
        self.assertEqual(used - allowed, set())
        self.assertEqual(allowed - used, set(), "an allowlisted action nothing uses")

    def test_the_action_listener_runs_before_ancestor_listeners(self) -> None:
        ui = UI.read_text(encoding="utf-8")
        self.assertIn("document.addEventListener('click', prksRunClickAction, true);", ui)
        self.assertIn("document.addEventListener('keydown', prksRunKeyAction, true);", ui)


if __name__ == "__main__":
    unittest.main()
