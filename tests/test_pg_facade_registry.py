"""The Library handle the HTTP adapter holds (#625, PG-3; PG-C2 #641; PG-C3 #642).

The PostgreSQL facade package these first checked (``backend/pg_facade``)
was retired by PG-C3: ``backend/library.py`` keeps its database-plus-storage
handle and its typed refusal. The module keeps its name because the SQLite
retirement records name its tests. It never imports psycopg, so these run in
the default suite. The ported-method registry and the SQLite reach tooling
were retired with SQLite (``tests/sqlite_retirement/``).
"""

from __future__ import annotations

import ast
import glob
import os
import subprocess
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend.library import NotPortedError, PostgresLibrary

# The HTTP adapter: the modules that hold the bound Library as ``db``.
ADAPTER_GLOBS = ("backend/server.py", "backend/api/*.py", "backend/services/*.py")


def adapter_library_calls() -> dict[str, set[str]]:
    """Every ``db.<name>`` the HTTP adapter reaches, with the files reaching it."""
    found: dict[str, set[str]] = {}
    for pattern in ADAPTER_GLOBS:
        for path in sorted(glob.glob(os.path.join(_PROJECT_DIR, pattern))):
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), path)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id == "db"):
                    found.setdefault(node.attr, set()).add(os.path.relpath(path, _PROJECT_DIR))
    return found


class FacadeSkeletonTests(unittest.TestCase):
    def test_the_facade_package_is_retired(self):
        self.assertFalse(os.path.exists(os.path.join(_PROJECT_DIR, "backend", "pg_facade", "__init__.py")))

    def test_unported_capability_is_a_typed_refusal_not_a_fallback(self):
        from backend.domain.works import work_commands

        # Refused before the database is touched: ``object()`` is no Database.
        with self.assertRaises(NotPortedError) as ctx:
            work_commands.update_metadata(object(), object(), "W-" + "0" * 32,  # type: ignore[arg-type]
                                          {"file_path": "/api/pdfs/a.pdf"})
        self.assertEqual(ctx.exception.method, "update_work_metadata(file_path)")
        # Not an AttributeError: hasattr()/getattr(default) must not hide it.
        self.assertNotIsInstance(ctx.exception, AttributeError)
        with self.assertRaises(AttributeError):
            PostgresLibrary(object()).no_such_method  # type: ignore[arg-type]  # noqa: B018

    def test_the_handle_keeps_its_database_and_storage(self):
        marker = object()
        library = PostgresLibrary(marker, storage=None)  # type: ignore[arg-type]
        self.assertIs(library.database, marker)
        self.assertIsNone(library.storage)

    def test_the_library_handle_does_not_import_psycopg(self):
        code = (
            "import sys; import backend.library; "
            "sys.exit(1 if any(m.split('.')[0] in ('psycopg', 'psycopg_pool') "
            "for m in sys.modules) else 0)"
        )
        proc = subprocess.run([sys.executable, "-c", code], cwd=_PROJECT_DIR, check=False)
        self.assertEqual(proc.returncode, 0, "backend.library imported psycopg")


class AdapterSurfaceTests(unittest.TestCase):
    def test_the_facade_has_every_library_member_the_adapter_reaches(self):
        calls = adapter_library_calls()
        self.assertIn("database", calls)  # the scan sees the adapter at all
        missing = {name: sorted(files) for name, files in calls.items() if not hasattr(PostgresLibrary, name)}
        self.assertEqual(missing, {})

    def test_the_adapter_reaches_every_public_facade_member(self):
        # PG-C3 (#642): the handle holds only what a route still calls. A
        # method only tests call belongs in tests/library_adapter.py; a route
        # calls the domain commands directly.
        calls = adapter_library_calls()
        handle = {"database", "storage"}
        unreached = sorted(name for name in dir(PostgresLibrary)
                           if not name.startswith("_") and name not in handle and name not in calls)
        self.assertEqual(unreached, [])


if __name__ == "__main__":
    unittest.main()
