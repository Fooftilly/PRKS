"""Static contracts for production workspace DnD (#256)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "frontend-app"
_DND = _APP / "src" / "workspace-dnd"
_SHELL = _APP / "src" / "workspace-shell" / "WorkspaceShell.vue"


class WorkspaceDndProductionContracts(unittest.TestCase):
    def test_production_modules_exist(self) -> None:
        for name in (
            "README.md",
            "index.ts",
            "adapter.ts",
            "drop-intent.ts",
            "geometry.ts",
            "commit.ts",
            "hover.ts",
            "a11y.ts",
            "snapshot.ts",
            "drop-intent.test.ts",
            "adapter.test.ts",
            "interaction.test.ts",
            "lifecycle.test.ts",
            "pragmatic-harness.ts",
        ):
            self.assertTrue((_DND / name).is_file(), name)

    def test_poc_path_removed(self) -> None:
        self.assertFalse((_APP / "src" / "workspace-dnd-poc").exists())

    def test_shell_wires_adapter_not_mutation_observer_default(self) -> None:
        shell = _SHELL.read_text(encoding="utf-8")
        self.assertIn("bindWorkspaceDnd", shell)
        self.assertIn("reconcile()", shell)
        self.assertIn("observeDom: false", shell)
        self.assertNotIn("workspace-dnd-poc", shell)
        adapter = (_DND / "adapter.ts").read_text(encoding="utf-8")
        self.assertIn("options.observeDom === true", adapter)

    def test_packages_pinned_and_no_hitbox(self) -> None:
        pkg = json.loads((_APP / "package.json").read_text(encoding="utf-8"))
        deps = pkg["dependencies"]
        self.assertEqual(deps["@atlaskit/pragmatic-drag-and-drop"], "4.0.0")
        self.assertEqual(deps["@atlaskit/pragmatic-drag-and-drop-auto-scroll"], "3.2.1")
        self.assertNotIn("@atlaskit/pragmatic-drag-and-drop-hitbox", deps)

    def test_inventory_marks_packages_production(self) -> None:
        inv = json.loads((_ROOT / "dependency-inventory.json").read_text(encoding="utf-8"))
        by_name = {row["name"]: row for row in inv["dependencies"]}
        for name in (
            "@atlaskit/pragmatic-drag-and-drop",
            "@atlaskit/pragmatic-drag-and-drop-auto-scroll",
        ):
            row = by_name[name]
            notes = row.get("notes") or ""
            self.assertNotIn("must not land in prks-vue.js", notes)
            self.assertNotIn("research-only", notes)
            self.assertIn("frontend/vue/prks-vue.js", row.get("generated_assets") or [])
        unit = by_name["@atlaskit/pragmatic-drag-and-drop-unit-testing"]
        self.assertEqual(unit.get("generated_assets"), [])
        self.assertIn("never ships", (unit.get("notes") or "").lower())

    def test_classic_drag_is_geometry_shim(self) -> None:
        drag = (_ROOT / "frontend" / "js" / "workspace-drag.js").read_text(encoding="utf-8")
        self.assertIn("prksWorkspaceComputeEdgeZone", drag)
        self.assertNotIn("pointerdown", drag)
        self.assertNotIn("THRESHOLD_PX", drag)


if __name__ == "__main__":
    unittest.main()
