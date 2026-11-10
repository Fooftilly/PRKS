"""Every SQLite test PG-C2 (#641) retires names its PostgreSQL replacement.

``tests/sqlite_retirement/<module>.json`` lists each test of one SQLite test
module (§13.4 of ``docs/postgresql-architecture.md``) with either the
PostgreSQL tests that now assert the same behaviour (``replaced_by``) or the
precise reason it has none (``obsolete``)::

    {"module": "tests/test_tag_sync.py",
     "tests": {"TagVocabularySyncTests.test_x": {
                   "replaced_by": ["tests/pg/test_tags_sync.py::CreateTests.test_y"]},
               "TagVocabularySyncTests.test_z": {"obsolete": "why nothing replaces it"}}}

While the SQLite module exists its tests and the listing must match exactly,
so a test cannot be retired unlisted or listed without existing. Once PG-C2
deletes the module, the listing stays as the record, and every replacement it
names must still exist: deleting a replacement fails here. Tests are found by
parsing the source, so no module is imported and no database is opened.
"""

from __future__ import annotations

import ast
import glob
import json
import os
import unittest
from functools import lru_cache

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIRECTORY = os.path.join(_PROJECT_DIR, "tests", "sqlite_retirement")


@lru_cache(maxsize=None)
def _tests_in(path: str) -> frozenset[str]:
    """``Class.method`` of every test method a module defines or inherits within itself."""
    with open(os.path.join(_PROJECT_DIR, path), encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}

    def methods(name: str, seen: frozenset[str] = frozenset()) -> set[str]:
        node = classes.get(name)
        if node is None or name in seen:
            return set()
        found = {item.name for item in node.body
                 if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test")}
        for base in node.bases:
            if isinstance(base, ast.Name):
                found |= methods(base.id, seen | {name})
        return found

    return frozenset("%s.%s" % (name, method) for name in classes for method in methods(name))


def _manifests() -> list[tuple[str, dict[str, object]]]:
    found = []
    for path in sorted(glob.glob(os.path.join(_DIRECTORY, "*.json"))):
        with open(path, encoding="utf-8") as handle:
            found.append((os.path.basename(path), json.load(handle)))
    return found


class RetirementManifestTests(unittest.TestCase):
    def test_there_are_manifests(self) -> None:
        self.assertTrue(_manifests())

    def test_each_entry_names_replacements_or_a_reason(self) -> None:
        for name, manifest in _manifests():
            self.assertEqual(name, os.path.basename(str(manifest["module"]))[:-3] + ".json")
            for test_id, entry in dict(manifest["tests"]).items():  # type: ignore[arg-type]
                with self.subTest(manifest=name, test=test_id):
                    self.assertIsInstance(entry, dict)
                    keys = set(entry)
                    self.assertTrue(keys in ({"replaced_by"}, {"replaced_by", "note"}, {"obsolete"}), keys)
                    if "obsolete" in entry:
                        self.assertGreater(len(entry["obsolete"].strip()), 20, "say precisely why")
                    else:
                        self.assertTrue(entry["replaced_by"])

    def test_replacements_exist(self) -> None:
        for name, manifest in _manifests():
            for test_id, entry in dict(manifest["tests"]).items():  # type: ignore[arg-type]
                for replacement in entry.get("replaced_by", []):
                    with self.subTest(manifest=name, test=test_id, replacement=replacement):
                        path, _, test = replacement.partition("::")
                        self.assertTrue(path.startswith("tests/") and os.path.exists(
                            os.path.join(_PROJECT_DIR, path)), path)
                        self.assertIn(test, _tests_in(path))
                        self.assertNotEqual(path, manifest["module"])

    def test_a_listed_module_that_still_exists_matches_its_listing(self) -> None:
        for name, manifest in _manifests():
            module = str(manifest["module"])
            if not os.path.exists(os.path.join(_PROJECT_DIR, module)):
                continue
            with self.subTest(manifest=name):
                self.assertEqual(set(dict(manifest["tests"])), set(_tests_in(module)))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
