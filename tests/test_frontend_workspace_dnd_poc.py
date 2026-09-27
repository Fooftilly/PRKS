"""Static contracts for the #234 Pragmatic DnD PoC (no production cutover)."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "frontend-app"
_POC = _APP / "src" / "workspace-dnd-poc"


class WorkspaceDndPocContracts(unittest.TestCase):
    def test_poc_isolated_from_production_entry(self) -> None:
        texts = {
            "main.ts": (_APP / "src" / "main.ts").read_text(encoding="utf-8"),
            "mount.ts": (_APP / "src" / "mount.ts").read_text(encoding="utf-8"),
            "WorkspaceShell.vue": (_APP / "src" / "workspace-shell" / "WorkspaceShell.vue").read_text(
                encoding="utf-8"
            ),
        }
        for name, text in texts.items():
            self.assertNotIn("workspace-dnd-poc", text, name)
            self.assertNotIn("pragmatic-drag-and-drop", text, name)
            self.assertNotIn("bindPocAdapter", text, name)

    def test_production_drag_module_still_present(self) -> None:
        drag = _ROOT / "frontend" / "js" / "workspace-drag.js"
        self.assertTrue(drag.is_file())
        src = drag.read_text(encoding="utf-8")
        self.assertIn("prksWorkspaceInitDrag", src)
        self.assertIn("prksWorkspaceCancelActiveDrag", src)

    def test_poc_packages_pinned(self) -> None:
        pkg = json.loads((_APP / "package.json").read_text(encoding="utf-8"))
        deps = pkg["dependencies"]
        self.assertEqual(deps["@atlaskit/pragmatic-drag-and-drop"], "4.0.0")
        self.assertEqual(deps["@atlaskit/pragmatic-drag-and-drop-auto-scroll"], "3.2.1")
        self.assertNotIn("@atlaskit/pragmatic-drag-and-drop-hitbox", deps)

    def test_poc_modules_exist(self) -> None:
        for name in (
            "README.md",
            "drop-intent.ts",
            "adapter.ts",
            "geometry.ts",
            "a11y.ts",
            "drop-intent.test.ts",
            "adapter.test.ts",
        ):
            self.assertTrue((_POC / name).is_file(), name)

    def test_decision_is_adopt(self) -> None:
        readme = (_POC / "README.md").read_text(encoding="utf-8")
        self.assertIn("Decision: ADOPT", readme)
        self.assertIn("Exactly one canonical workspace state", readme)


if __name__ == "__main__":
    unittest.main()
