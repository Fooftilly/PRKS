"""The Processing queue (``backend/domain/processing``) is PostgreSQL-only code.

PG-C2 deletes ``backend/db_manager.py`` and ``backend/db_migrations.py``
(``docs/postgresql-architecture.md`` §13.4), so nothing in the package may
import them, at module level or inside a function.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "backend" / "domain" / "processing"
RETIRED = ("backend.db_manager", "backend.db_migrations")


def _imported(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


class ProcessingImportsTests(unittest.TestCase):
    def test_the_package_imports_no_retired_sqlite_module(self) -> None:
        sources = sorted(PACKAGE.glob("*.py"))
        self.assertTrue(sources)
        for source in sources:
            imported = _imported(ast.parse(source.read_text(encoding="utf-8"), str(source)))
            with self.subTest(module=source.name):
                self.assertEqual(sorted(name for name in imported
                                        if any(name == retired or name.startswith(retired + ".")
                                               for retired in RETIRED)), [])


if __name__ == "__main__":
    unittest.main()
