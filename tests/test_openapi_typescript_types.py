"""Types-only OpenAPI generation for the Vue transport boundary (#246)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = _ROOT / "frontend-app" / "scripts" / "openapi-type-families.json"
_PACKAGE = _ROOT / "frontend-app" / "package.json"
_INVENTORY = _ROOT / "dependency-inventory.json"
_WORKFLOW = _ROOT / ".github" / "workflows" / "static-analysis.yml"
_GENERATED = _ROOT / "frontend-app" / "src" / "api" / "generated"
_BANNED = (
    "useQuery",
    "useMutation",
    "prksApiRequest",
    "fetch(",
    "axios",
    "@tanstack/",
)


class OpenApiTypescriptTypesTests(unittest.TestCase):
    def test_family_manifest_is_publishers_and_saved_views_only(self):
        manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        families = manifest["families"]
        self.assertEqual(
            [(item["name"], item["artifact"], item["output"]) for item in families],
            [
                (
                    "publishers",
                    "docs/api/openapi-publishers.json",
                    "frontend-app/src/api/generated/publishers.ts",
                ),
                (
                    "saved-views",
                    "docs/api/openapi-saved-views.json",
                    "frontend-app/src/api/generated/saved-views.ts",
                ),
            ],
        )
        for item in families:
            self.assertTrue((_ROOT / item["artifact"]).is_file(), item["artifact"])

    def test_generated_modules_are_schema_aliases_without_clients(self):
        for name in ("publishers", "saved-views"):
            text = (_GENERATED / f"{name}.ts").read_text(encoding="utf-8")
            self.assertIn("export type PublisherInUse" if name == "publishers" else "export type SavedView ", text)
            self.assertIn("components['schemas']", text)
            for token in _BANNED:
                self.assertNotIn(token, text, token)

    def test_hand_written_clients_reexport_generated_transport_types(self):
        publishers = (_ROOT / "frontend-app" / "src" / "api" / "publishers.ts").read_text(encoding="utf-8")
        saved = (_ROOT / "frontend-app" / "src" / "api" / "saved-views.ts").read_text(encoding="utf-8")
        self.assertIn("from './generated/publishers'", publishers)
        self.assertIn("export type { PublisherCreated, PublisherInUse }", publishers)
        self.assertNotIn("export interface PublisherInUse", publishers)
        self.assertNotIn("export interface PublisherCreated", publishers)
        self.assertIn("parsePublishersInUse", publishers)
        self.assertIn("prksApiRequest", publishers)
        self.assertIn("from './generated/saved-views'", saved)
        self.assertIn("export type { SavedView, SavedViewSearch }", saved)
        self.assertNotIn("export interface SavedView ", saved)
        self.assertNotIn("export interface SavedViewSearch", saved)
        self.assertIn("parseSavedView", saved)
        self.assertIn("prksApiRequest", saved)
        feature_root = _ROOT / "frontend-app" / "src" / "features"
        for path in feature_root.rglob("*"):
            if path.suffix not in {".ts", ".vue"}:
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("components['schemas']", text, path)
            self.assertNotIn('components["schemas"]', text, path)

    def test_package_pin_inventory_and_ci_check(self):
        package = json.loads(_PACKAGE.read_text(encoding="utf-8"))
        self.assertEqual(package["devDependencies"]["openapi-typescript"], "7.13.0")
        self.assertEqual(package["scripts"]["openapi:types"], "node scripts/generate-openapi-types.mjs")
        self.assertEqual(package["scripts"]["openapi:check"], "node scripts/generate-openapi-types.mjs --check")
        inventory = json.loads(_INVENTORY.read_text(encoding="utf-8"))
        entry = next(item for item in inventory["dependencies"] if item["name"] == "openapi-typescript")
        self.assertEqual(entry["scope"], "build")
        self.assertEqual(
            entry["generated_assets"],
            [
                "frontend-app/src/api/generated/publishers.ts",
                "frontend-app/src/api/generated/saved-views.ts",
            ],
        )
        workflow = _WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("npm run openapi:check --prefix frontend-app", workflow)


if __name__ == "__main__":
    unittest.main()
