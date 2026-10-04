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
    def test_family_manifest_covers_checked_in_typed_families(self):
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
                (
                    "processing-files",
                    "docs/api/openapi-processing-files.json",
                    "frontend-app/src/api/generated/processing-files.ts",
                ),
                (
                    "performance-diagnostics",
                    "docs/api/openapi-performance-diagnostics.json",
                    "frontend-app/src/api/generated/performance-diagnostics.ts",
                ),
                (
                    "positions",
                    "docs/api/openapi-positions.json",
                    "frontend-app/src/api/generated/positions.ts",
                ),
            ],
        )
        for item in families:
            self.assertTrue((_ROOT / item["artifact"]).is_file(), item["artifact"])
            output = _ROOT / item["output"]
            self.assertTrue(output.is_file(), item["output"])
            self.assertEqual(output.parent, _GENERATED)

    def test_generated_modules_are_schema_aliases_without_clients(self):
        markers = {
            "publishers": "export type PublisherInUse =",
            "saved-views": "export type SavedView =",
            "processing-files": "export type ProcessingFile =",
            "performance-diagnostics": "export type PerformanceSnapshot =",
            "positions": "export type PositionSummary =",
        }
        for name, marker in markers.items():
            text = (_GENERATED / f"{name}.ts").read_text(encoding="utf-8")
            self.assertIn(marker, text)
            self.assertIn("components['schemas']", text)
            for token in _BANNED:
                self.assertNotIn(token, text, token)
        saved = (_GENERATED / "saved-views.ts").read_text(encoding="utf-8")
        update = saved.split("SavedViewUpdateRequest: {", 1)[1].split("};", 1)[0]
        self.assertIn("name?:", update)
        self.assertIn("search?:", update)
        view = saved.split("SavedView: {", 1)[1].split("};", 1)[0]
        self.assertIn("\n            name: string;", view)
        self.assertNotIn("name?:", view)
        envelope = saved.split("ApiErrorEnvelope: {", 1)[1].split("};", 1)[0]
        self.assertIn("code?:", envelope)
        self.assertIn("\n            error: string;", envelope)
        self.assertNotIn("error?:", envelope)

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
        processing = (_ROOT / "frontend-app" / "src" / "api" / "processing-files.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("from './generated/processing-files'", processing)
        self.assertIn(
            "export type { ProcessingFile, ProcessingFileImported, ProcessingFileRole, ProcessingFileTag }",
            processing,
        )
        self.assertNotIn("export interface ProcessingFile ", processing)
        self.assertNotIn("export interface ProcessingFileRole", processing)
        self.assertNotIn("export interface ProcessingFileTag", processing)
        self.assertNotIn("export interface ProcessingFileImported", processing)
        self.assertIn("export type ProcessingFileUpdate", processing)
        self.assertIn("parseProcessingFile", processing)
        self.assertIn("prksApiRequest", processing)
        performance = (
            _ROOT / "frontend-app" / "src" / "api" / "performance-diagnostics.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("from './generated/performance-diagnostics'", performance)
        self.assertIn("export type {\n  PerformanceCounters,\n  PerformanceDiagnosticsReset,", performance)
        self.assertNotIn("export interface PerformanceSnapshot", performance)
        self.assertNotIn("export interface PerformanceDiagnosticsReset", performance)
        self.assertIn("parsePerformanceSnapshot", performance)
        self.assertIn("record.status !== 'reset'", performance)
        self.assertIn("prksApiRequest", performance)
        positions = (
            _ROOT / "frontend-app" / "src" / "features" / "positions" / "types.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("export interface PositionDetail", positions)
        self.assertIn("export interface PositionIndexItem", positions)
        self.assertNotIn("from '", positions)
        self.assertNotIn('from "', positions)
        self.assertNotIn("components['schemas']", positions)
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
                "frontend-app/src/api/generated/processing-files.ts",
                "frontend-app/src/api/generated/performance-diagnostics.ts",
                "frontend-app/src/api/generated/positions.ts",
            ],
        )
        workflow = _WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("npm run openapi:check --prefix frontend-app", workflow)


if __name__ == "__main__":
    unittest.main()
