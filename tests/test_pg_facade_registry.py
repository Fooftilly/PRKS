"""PostgreSQL facade skeleton (#625, PG-3; PG-C2 #641).

The facade package never imports psycopg, so these run in the default suite
for every lane. The ported-method registry and the SQLite reach tooling they
also checked were retired with SQLite (``tests/sqlite_retirement/``).
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

from backend.pg_facade import FacadeMixin, NotPortedError, PostgresLibrary

LANE_MIXINS = {
    "WorksMixin",
    "ResearchMixin",
    "AnnotationsMixin",
    "SearchMixin",
}
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
    def test_facade_is_composed_of_one_mixin_per_lane(self):
        names = {cls.__name__ for cls in PostgresLibrary.__mro__
                 if issubclass(cls, FacadeMixin) and cls not in (FacadeMixin, PostgresLibrary)}
        self.assertEqual(names, LANE_MIXINS)

    def test_unported_method_is_a_typed_refusal_not_a_fallback(self):
        library = PostgresLibrary(object())  # type: ignore[arg-type]
        with self.assertRaises(NotPortedError) as ctx:
            library.update_work_metadata("W-" + "0" * 32, {"file_path": "/api/pdfs/a.pdf"})
        self.assertEqual(ctx.exception.method, "update_work_metadata(file_path)")
        # Not an AttributeError: hasattr()/getattr(default) must not hide it.
        self.assertNotIsInstance(ctx.exception, AttributeError)
        with self.assertRaises(AttributeError):
            library.no_such_method  # noqa: B018

    def test_facade_keeps_its_database_and_storage(self):
        marker = object()
        library = PostgresLibrary(marker, storage=None)  # type: ignore[arg-type]
        self.assertIs(library.database, marker)
        self.assertIsNone(library.storage)

    def test_facade_package_does_not_import_psycopg(self):
        code = (
            "import sys; import backend.pg_facade; "
            "sys.exit(1 if any(m.split('.')[0] in ('psycopg', 'psycopg_pool') "
            "for m in sys.modules) else 0)"
        )
        proc = subprocess.run([sys.executable, "-c", code], cwd=_PROJECT_DIR, check=False)
        self.assertEqual(proc.returncode, 0, "backend.pg_facade imported psycopg")


class AdapterSurfaceTests(unittest.TestCase):
    def test_the_facade_has_every_library_member_the_adapter_reaches(self):
        calls = adapter_library_calls()
        self.assertIn("get_work", calls)
        missing = {name: sorted(files) for name, files in calls.items() if not hasattr(PostgresLibrary, name)}
        self.assertEqual(missing, {})

    def test_the_adapter_reaches_every_public_facade_member(self):
        # PG-C3 (#642): the facade holds only what a route still calls. A
        # method only tests call belongs in tests/library_adapter.py, and a
        # family whose routes call the domain directly leaves the facade.
        calls = adapter_library_calls()
        handle = {"database", "storage"}
        unreached = sorted(name for name in dir(PostgresLibrary)
                           if not name.startswith("_") and name not in handle and name not in calls)
        self.assertEqual(unreached, [])


if __name__ == "__main__":
    unittest.main()
