"""Licensing and visible source-of-code notice regressions."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend" / "index.html"


class LicensingStructureTests(unittest.TestCase):
    def test_agpl_license_matches_pinned_spdx_text(self):
        import hashlib

        license_bytes = (ROOT / "LICENSE").read_bytes()
        git_blob = b"blob " + str(len(license_bytes)).encode("ascii") + b"\0" + license_bytes
        self.assertEqual(
            hashlib.sha1(git_blob, usedforsecurity=False).hexdigest(),
            "0c97efd25b5974b974ed9a8a18207bc4f55bb338",
        )
    def test_readme_has_spdx_and_notices(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("AGPL-3.0-only", readme)
        self.assertIn("THIRD_PARTY_NOTICES.md", readme)
        self.assertIn("CONTRIBUTING.md", readme)
        self.assertIn("Corresponding Source", readme)

    def test_about_notice_is_in_diagnostics_not_general(self):
        index = INDEX.read_text(encoding="utf-8")
        general = index.split('id="prks-settings-panel-general"', 1)[1].split(
            'id="prks-settings-panel-reading"', 1
        )[0]
        diagnostics = index.split('id="prks-settings-panel-diagnostics"', 1)[1].split(
            'id="prks-restore-confirm-modal"', 1
        )[0]
        self.assertNotIn('id="prks-source-code-link"', general)
        for token in (
            'id="prks-settings-about-heading"',
            'id="prks-copyright-notice"',
            "Copyright © 2026 Nikola Perović and other PRKS contributors.",
            "Each author retains copyright in their contributions;",
            "third-party works retain their respective copyrights.",
            'id="prks-source-code-link"',
            'href="https://github.com/Fooftilly/PRKS" target="_blank" rel="noopener noreferrer">',
            'id="prks-license-link"',
            'href="https://github.com/Fooftilly/PRKS/blob/master/LICENSE" target="_blank" rel="noopener noreferrer">',
            'id="prks-third-party-link"',
            'href="https://github.com/Fooftilly/PRKS/blob/master/THIRD_PARTY_NOTICES.md" target="_blank" rel="noopener noreferrer">',
            "AGPL-3.0-only",
            "no warranty",
        ):
            self.assertIn(token, diagnostics)

    def test_vue_bundle_notices_cover_production_lockfile(self):
        import json

        bundle = ROOT / "frontend" / "vue" / "THIRD_PARTY_LICENSES.md"
        source = ROOT / "frontend-app" / "THIRD_PARTY_LICENSES.md"
        notices = bundle.read_text(encoding="utf-8")
        self.assertEqual(notices, source.read_text(encoding="utf-8"))
        package = json.loads((ROOT / "frontend-app" / "package.json").read_text())
        lock = json.loads((ROOT / "frontend-app" / "package-lock.json").read_text())
        # Preserve every installation, including multiple versions of the
        # same package in different node_modules locations. A dict keyed only
        # by package name would silently discard all but one version.
        from collections import Counter
        import re

        production_rows = [
            (
                path.rsplit("node_modules/", 1)[-1],
                item["version"],
                item["license"],
            )
            for path, item in lock["packages"].items()
            if path.startswith("node_modules/")
            and not item.get("dev") and not item.get("optional")
        ]
        inventory = notices.split(
            "<!-- BEGIN GENERATED PRODUCTION DEPENDENCY INVENTORY -->", 1
        )[1].split(
            "<!-- END GENERATED PRODUCTION DEPENDENCY INVENTORY -->", 1
        )[0]
        table_lines = [
            line for line in inventory.splitlines()
            if line.startswith("|") and not line.startswith("| Package |")
            and not line.startswith("| --- |")
        ]
        pattern = re.compile(r"\| `([^`]+)` \| `([^`]+)` \| `([^`]+)` \|")
        parsed = [pattern.fullmatch(line) for line in table_lines]
        self.assertTrue(all(parsed), "Malformed generated dependency inventory row")
        actual_rows = [match.groups() for match in parsed if match]
        self.assertEqual(
            Counter(actual_rows),
            Counter(production_rows),
            "The published inventory must retain every lockfile installation, "
            "including duplicate package names at different versions.",
        )
        for name, version in package["dependencies"].items():
            self.assertEqual(lock["packages"][f"node_modules/{name}"]["version"], version)
        for holder in ("Vue contributors", "Tanner Linsley", "Anthony Fu",
                       "Floating UI contributors", "Atlassian Pty Ltd"):
            self.assertIn(holder, notices)
        self.assertIn("Apache License", notices)
    def test_lockfile_inventory_generator_handles_dependency_updates(self):
        """A lockfile bump regenerates notices without manual table edits."""
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("Node is not available")
        check = """
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { renderLicenseInventory } from './frontend-app/scripts/vue-license-inventory.mjs';
const source = readFileSync('frontend-app/THIRD_PARTY_LICENSES.md', 'utf8');
const pkg = JSON.parse(readFileSync('frontend-app/package.json', 'utf8'));
const lock = JSON.parse(readFileSync('frontend-app/package-lock.json', 'utf8'));
assert.equal(renderLicenseInventory(source, pkg, lock), source);
const future = structuredClone(lock);
future.packages['node_modules/example-license-fixture'] = { version: '9.8.7', license: 'MIT' };
const updated = renderLicenseInventory(source, pkg, future);
assert.ok(updated.includes('| `example-license-fixture` | `9.8.7` | `MIT` |'));
future.packages['node_modules/container/node_modules/nested-license-fixture'] = { version: '1.2.3', license: 'MIT' };
const nested = renderLicenseInventory(source, pkg, future);
assert.ok(nested.includes('| `nested-license-fixture` | `1.2.3` | `MIT` |'));
assert.ok(!nested.includes('container/node_modules/nested-license-fixture'));
delete future.packages['node_modules/container/node_modules/nested-license-fixture'];
future.packages['node_modules/container/node_modules/example-license-fixture'] =
  { version: '8.8.8', license: 'MIT' };
const duplicates = renderLicenseInventory(source, pkg, future);
assert.ok(duplicates.includes('| `example-license-fixture` | `9.8.7` | `MIT` |'));
assert.ok(duplicates.includes('| `example-license-fixture` | `8.8.8` | `MIT` |'));
delete future.packages['node_modules/container/node_modules/example-license-fixture'];
assert.equal(renderLicenseInventory(updated, pkg, future), updated);
assert.ok(updated.includes('### Full Apache License 2.0'));
future.packages['node_modules/example-license-fixture'].license = '';
assert.throws(() => renderLicenseInventory(source, pkg, future), /missing version\/license/);
future.packages['node_modules/example-license-fixture'].license = 'SEE LICENSE IN LICENSE';
assert.throws(() => renderLicenseInventory(source, pkg, future), /Unreviewed SPDX license/);
future.packages['node_modules/example-license-fixture'].license = 'GPL-3.0-only OR MIT';
assert.throws(() => renderLicenseInventory(source, pkg, future), /Unreviewed SPDX license/);
"""
        result = subprocess.run(
            [node, "--input-type=module", "-e", check],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_docker_distribution_includes_legal_notices(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("COPY LICENSE THIRD_PARTY_NOTICES.md ./", dockerfile)
        self.assertIn("COPY frontend ./frontend", dockerfile)
        for exception in (
            "!THIRD_PARTY_NOTICES.md",
            "!frontend/vue/THIRD_PARTY_LICENSES.md",
            "!frontend/vendor/prks-pdf-viewer/THIRD_PARTY.md",
        ):
            self.assertIn(exception, dockerignore)
    def test_vendor_notices_remain_present(self):
        for name in (
            "codemirror", "cytoscape", "dompurify", "easymde",
            "idb", "inter", "lucide",
        ):
            with self.subTest(vendor=name):
                self.assertTrue((ROOT / "frontend" / "vendor" / name / "LICENSE").is_file())
        viewer = ROOT / "frontend" / "vendor" / "prks-pdf-viewer"
        self.assertTrue((viewer / "THIRD_PARTY.md").is_file())
        self.assertTrue((viewer / "LICENSES" / "pdfium-LICENSE").is_file())
        # The NPM pdfium-LICENSE is only the EmbedPDF JS wrapper MIT grant.
        # The WASM engine separately requires the original PDFium notices.
        upstream = (
            ROOT / "tools" / "pdf-viewer" / "licenses" / "pdfium-runtime-LICENSE"
        ).read_text(encoding="utf-8")
        shipped = (
            viewer / "LICENSES" / "pdfium-runtime-LICENSE"
        ).read_text(encoding="utf-8")
        self.assertEqual(upstream, shipped)
        self.assertIn("Copyright 2014 The PDFium Authors", shipped)
        self.assertIn("Apache License", shipped)
        self.assertIn("pdfium-runtime-LICENSE", (viewer / "THIRD_PARTY.md").read_text())
        # Pin the actual source-to-distribution copy operation. A bare
        # filename assertion would pass if only a comment still mentioned it.
        self.assertIn(
            "copyFileSync(\n"
            "    join(here, 'licenses', 'pdfium-runtime-LICENSE'),\n"
            "    join(outDir, 'LICENSES', 'pdfium-runtime-LICENSE'),\n"
            ");",
            (ROOT / "tools" / "pdf-viewer" / "build.mjs").read_text(),
        )



if __name__ == "__main__":
    unittest.main()
