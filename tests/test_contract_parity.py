"""Cross-runtime semantic contract parity tests.

These tests protect deliberate Python/JavaScript mirrors that cannot share a
runtime constant. They are intentionally about application contracts, not
generic textual duplication.
"""
from __future__ import annotations

import inspect
import re
import unittest
from pathlib import Path

from backend.db_manager import PRKSDatabase, PRKS_BIBTEX_EXPORT_FIELD_IDS
from backend.work_metadata_sync import WORK_STATUSES


ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def js_string_array(source: str, name: str) -> tuple[str, ...]:
    pattern = (
        r"const\s+" + re.escape(name) +
        r"\s*=\s*(?:Object\.freeze\(\s*)?\[(.*?)\]\s*\)?\s*;"
    )
    match = re.search(pattern, source, re.S)
    if not match:
        raise AssertionError(f"could not find JavaScript array {name}")
    return tuple(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))


def js_true_object_keys(source: str, name: str) -> tuple[str, ...]:
    match = re.search(
        r"const\s+" + re.escape(name) + r"\s*=\s*\{(.*?)\}\s*;",
        source,
        re.S,
    )
    if not match:
        raise AssertionError(f"could not find JavaScript object {name}")
    keys: list[str] = []
    for quoted, bare in re.findall(
        r"^\s*(?:['\"]([^'\"]+)['\"]|([A-Za-z_$][\w$-]*))\s*:\s*true\s*,?\s*$",
        match.group(1),
        re.M,
    ):
        keys.append(quoted or bare)
    if not keys:
        raise AssertionError(f"could not parse JavaScript object {name}")
    return tuple(keys)


class ContractParityTests(unittest.TestCase):
    def test_work_status_registries_match_backend_contract(self):
        canonical = tuple(WORK_STATUSES)
        mirrors = {
            "work metadata sync": js_string_array(
                read("frontend/js/work-metadata-state.js"), "WORK_STATUSES"
            ),
            "navigation/progress links": js_string_array(
                read("frontend/js/navigation.js"), "PRKS_PROGRESS_STATUS_VALUES"
            ),
            "Progress page": js_string_array(
                read("frontend/js/components/progress.js"), "PRKS_PROGRESS_STATUSES"
            ),
        }
        for label, actual in mirrors.items():
            with self.subTest(label=label):
                self.assertEqual(actual, canonical)

    def test_bibtex_settings_fields_match_backend_accepted_fields(self):
        source = read("frontend/js/app.js")
        match = re.search(
            r"const\s+PRKS_BIBTEX_EXPORT_FIELD_DEFS\s*=\s*\[(.*?)\]\s*;",
            source,
            re.S,
        )
        self.assertIsNotNone(match, "BibTeX Settings field definitions not found")
        frontend_ids = tuple(
            re.findall(r"\[\s*['\"]([^'\"]+)['\"]\s*,", match.group(1))
        )
        self.assertEqual(frontend_ids, tuple(PRKS_BIBTEX_EXPORT_FIELD_IDS))

    def test_tile_route_fallback_matches_navigation_policy(self):
        navigation = js_true_object_keys(
            read("frontend/js/navigation.js"), "PRKS_TILE_ROUTE_NAMES"
        )
        workspace_fallback = js_true_object_keys(
            read("frontend/js/workspace-tabs.js"), "TILE_ROUTE_NAMES"
        )
        self.assertEqual(workspace_fallback, navigation)

    def test_recent_projection_limit_matches_server_default(self):
        source = read("frontend/js/work-open-state.js")
        match = re.search(r"const\s+RECENT_LIMIT\s*=\s*(\d+)\s*;", source)
        self.assertIsNotNone(match, "frontend RECENT_LIMIT not found")
        frontend_limit = int(match.group(1))

        param = inspect.signature(PRKSDatabase.get_recent_browse).parameters["limit"]
        self.assertIsInstance(param.default, int)
        self.assertEqual(frontend_limit, param.default)


if __name__ == "__main__":
    unittest.main()
