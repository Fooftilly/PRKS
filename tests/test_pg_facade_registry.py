"""PostgreSQL facade skeleton and ported-method registry (#625, PG-3).

These run in the default SQLite suite: the facade package never imports
psycopg, so registry mistakes surface in ``python run_tests.py`` for every
lane, not only in the parity leg.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend.db_manager import PRKSDatabase
from backend.pg_facade import (
    FacadeMixin,
    NotPortedError,
    PostgresLibrary,
    RegistryError,
    coverage,
    facade_surface,
    ported,
    ported_methods,
    validate_registry,
)
from backend.pg_facade import reach
from backend.pg_facade.registry import Coverage, facade_mixins

LANE_MIXINS = {
    "WorksMixin",
    "PeopleMixin",
    "OrganizationMixin",
    "ResearchMixin",
    "AnnotationsMixin",
    "ProcessingMixin",
    "SearchMixin",
}


class _ProcessingPort(FacadeMixin):
    @ported
    def scan_processing_files(self):
        return ["from-postgres"]


class _OtherPort(FacadeMixin):
    @ported
    def scan_processing_files(self):
        return []


class _BareLibrary(FacadeMixin):
    """The facade core without the lane mixins, so a test's registrations are the only ones."""

    __init__ = PostgresLibrary.__init__
    __getattr__ = PostgresLibrary.__getattr__


class FacadeSkeletonTests(unittest.TestCase):
    def test_facade_is_composed_of_one_mixin_per_lane(self):
        names = {cls.__name__ for cls in facade_mixins(PostgresLibrary)}
        self.assertEqual(names, LANE_MIXINS)
        # A lane mixin holds ported methods and private helpers, nothing else.
        registered = ported_methods(PostgresLibrary)
        for cls in facade_mixins(PostgresLibrary):
            public = {name for name in vars(cls) if not name.startswith("_")}
            self.assertEqual({registered.get(name) for name in public} - {cls.__name__}, set(),
                             f"{cls.__name__} has a public member that is not ported")

    def test_the_registry_of_the_real_facade_is_valid(self):
        self.assertEqual(validate_registry(PostgresLibrary), [])

    def test_unported_method_is_a_typed_refusal_not_a_fallback(self):
        library = PostgresLibrary(object())  # type: ignore[arg-type]
        with self.assertRaises(NotPortedError) as ctx:
            library.scan_processing_files()
        self.assertEqual(ctx.exception.method, "scan_processing_files")
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


class RegistryDiscoveryTests(unittest.TestCase):
    def test_one_decorator_in_a_mixin_registers_the_method(self):
        class Library(_ProcessingPort, _BareLibrary):
            pass

        self.assertEqual(ported_methods(Library), {"scan_processing_files": "_ProcessingPort"})
        self.assertEqual(validate_registry(Library), [])
        library = Library(object())  # type: ignore[arg-type]
        self.assertEqual(library.scan_processing_files(), ["from-postgres"])
        # The real facade is untouched by a subclass's registrations.
        self.assertNotIn("scan_processing_files", ported_methods(PostgresLibrary))

    def test_two_mixins_registering_one_method_are_refused(self):
        class Library(_ProcessingPort, _OtherPort, PostgresLibrary):
            pass

        with self.assertRaises(RegistryError):
            ported_methods(Library)
        self.assertEqual(len(validate_registry(Library)), 1)

    def test_a_registration_with_another_signature_is_refused(self):
        class Drifted(FacadeMixin):
            @ported
            def scan_processing_files(self, limit=10):
                return []

        class Library(Drifted, PostgresLibrary):
            pass

        problems = validate_registry(Library)
        self.assertEqual(len(problems), 1)
        self.assertIn("signature", problems[0])

    def test_a_registration_prksdatabase_does_not_have_is_refused(self):
        class Invented(FacadeMixin):
            @ported
            def get_everything(self):
                return []

        class Library(Invented, PostgresLibrary):
            pass

        problems = validate_registry(Library)
        self.assertEqual(len(problems), 1)
        self.assertIn("has no get_everything", problems[0])

    def test_an_unregistered_override_is_refused(self):
        class Forgot(FacadeMixin):
            def scan_processing_files(self):
                return []

            def _private_helper(self):
                return None

        class Library(Forgot, PostgresLibrary):
            pass

        problems = validate_registry(Library)
        self.assertEqual(len(problems), 1)
        self.assertIn("without @ported", problems[0])

    def test_ported_static_and_property_members_are_discovered(self):
        class Members(FacadeMixin):
            @staticmethod
            @ported
            def etag_for_representation(payload):
                return "x"

        class Library(Members, _BareLibrary):
            pass

        self.assertIn("etag_for_representation", ported_methods(Library))


class DescriptorKindTests(unittest.TestCase):
    def test_a_plain_method_cannot_stand_in_for_a_static_method(self):
        class Plain(FacadeMixin):
            @ported
            def etag_for_representation(label, rows):  # type: ignore[no-self-argument]
                return "x"

        class Library(Plain, _BareLibrary):
            pass

        problems = validate_registry(Library)
        self.assertEqual(len(problems), 1)
        self.assertIn("is a method but PRKSDatabase.etag_for_representation is a staticmethod", problems[0])

    def test_a_matching_static_method_is_accepted(self):
        class Static(FacadeMixin):
            @staticmethod
            @ported
            def etag_for_representation(label, rows):
                return "x"

        class Library(Static, _BareLibrary):
            pass

        self.assertEqual(validate_registry(Library), [])
        self.assertEqual(Library(object()).etag_for_representation("w", []), "x")  # type: ignore[arg-type]


class SurfaceTests(unittest.TestCase):
    def test_surface_is_what_the_adapter_calls_on_db(self):
        surface = facade_surface()
        self.assertGreater(len(surface), 50)
        for name in ("get_work", "create_saved_view", "get_saved_views", "search_works"):
            self.assertIn(name, surface)
        for name in surface:
            self.assertTrue(hasattr(PRKSDatabase, name), name)

    def test_surface_is_what_the_postgresql_runtime_reaches(self):
        surface = facade_surface()
        # server._delete_work calls delete_work_record on the PostgreSQL Library.
        self.assertIn("delete_work_record", surface)
        # The SQLite sync handlers and raw SQL helpers are SQLite code only.
        for name in ("insert_person_on_conn", "merge_tags_into_on_conn", "execute_query", "connection"):
            self.assertNotIn(name, surface)

    def test_surface_reads_all_backend_modules_but_the_sqlite_class_and_pg_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            for directory in ("api", "domain/works", "db", "pg_facade"):
                os.makedirs(os.path.join(tmp, directory))
            files = {
                "server.py": "db.get_work(1)\nother.get_person(2)\ndb.not_a_method()\n",
                "api/x.py": "def f(db):\n    return db.get_saved_views()\n",
                "work_deletion.py": "def g(db):\n    db.delete_work_record('w')\n",
                "domain/works/h.py": "def h(db):\n    db.add_tag('t')\n",
                "db_manager.py": "db.delete_tag(1)\n",
                "db/x.py": "db.get_person(1)\n",
                "pg_facade/y.py": "db.add_person('p')\n",
            }
            for rel, body in files.items():
                with open(os.path.join(tmp, rel), "w", encoding="utf-8") as handle:
                    handle.write(textwrap.dedent(body))
            self.assertEqual(
                facade_surface(backend_dir=tmp),
                frozenset({"get_work", "get_saved_views", "delete_work_record", "add_tag"}),
            )

    def test_a_retired_method_reached_again_makes_coverage_incomplete(self):
        report = Coverage(surface=frozenset({"get_processing_files"}), ported={})
        self.assertIn("get_processing_files", report.retired)
        self.assertEqual(report.unported, frozenset({"get_processing_files"}))
        self.assertFalse(report.complete)

    def test_coverage_counts_ported_and_unported(self):
        class Library(_ProcessingPort, _BareLibrary):
            pass

        report = coverage(Library)
        # The legacy inbox is SQLite code, so it is outside the PostgreSQL surface.
        self.assertNotIn("scan_processing_files", report.surface)
        self.assertNotIn("scan_processing_files", report.unported)
        self.assertEqual(report.unported, report.surface)
        self.assertNotIn("scan_processing_files", report.retired)
        self.assertIn("get_processing_files", report.retired)
        self.assertFalse(report.complete)
        real = coverage()
        self.assertEqual(real.ported_in_surface, real.surface & frozenset(real.ported))
        self.assertEqual(real.unported, real.surface - frozenset(real.ported))


class PostgresReachTests(unittest.TestCase):
    """The surface leaves out SQLite code, and that code is proven unreachable (PG-C1 #640)."""

    def write(self, tmp, files):
        for rel, body in files.items():
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
            with open(os.path.join(tmp, rel), "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(body))

    def test_the_facade_covers_everything_the_postgresql_runtime_reaches(self):
        report = coverage()
        self.assertEqual(report.unported - report.retired, frozenset())
        self.assertTrue(report.complete)
        self.assertEqual(report.retired, reach.RETIRED_METHODS)

    def test_no_sqlite_only_code_is_reachable_from_the_postgresql_runtime(self):
        self.assertEqual(reach.reach_problems(), [])

    def test_sqlite_branches_leave_the_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, {"server.py": """
                def a():
                    if serves_postgres_library():
                        db.get_work(1)
                    else:
                        db.delete_tag(1)

                def b():
                    if not serves_postgres_library():
                        db.add_tag(1)
                    db.get_person(1)

                def c():
                    if serves_postgres_library():
                        return db.get_saved_views()
                    db.add_person(1)

                def d(flag):
                    if flag and serves_postgres_library():
                        return
                    if flag:
                        db.delete_person(1)
                    flag = other()
                    if flag:
                        db.get_folder(1)

                def e(library_database=None):
                    if library_database is not None:
                        return db.search_works('x')
                    db.update_tag(1)

                def f(a, b):
                    # Skipping this return proves only that a or b is false.
                    if a and b and serves_postgres_library():
                        return
                    if a:
                        db.get_all_tags()

                def g(flag, outer):
                    # The return holds only where outer is true.
                    if outer:
                        if flag and serves_postgres_library():
                            return
                        if flag:
                            db.delete_tag(2)
                    if flag:
                        db.get_tags_in_use()

                def h(flag, items):
                    if flag and serves_postgres_library():
                        return
                    for flag in items:
                        pass
                    if flag:
                        db.get_all_persons()

                def k(flag, items):
                    if flag and serves_postgres_library():
                        return
                    while items:
                        # A later iteration sees flag = True from the one before.
                        if flag:
                            db.get_all_folders()
                        flag = True

                def m(flag):
                    if flag and serves_postgres_library():
                        return
                    else:
                        flag = True
                    if flag:
                        db.get_all_works()

                def n(flag, value):
                    if flag and serves_postgres_library():
                        return
                    match value:
                        case {"flag": flag}:
                            pass
                    if flag:
                        db.get_all_playlists()

                def p(flag):
                    flag = bool(flag)
                    if flag and serves_postgres_library():
                        return
                    if flag:
                        db.delete_tag(3)
                """})
            self.assertEqual(
                facade_surface(backend_dir=tmp),
                frozenset({
                    "get_work", "get_person", "get_saved_views", "get_folder", "search_works",
                    "get_all_tags", "get_tags_in_use", "get_all_persons", "get_all_folders",
                    "get_all_works", "get_all_playlists",
                }),
            )

    def test_a_sqlite_only_scope_reached_outside_sqlite_code_is_named(self):
        scopes = frozenset({("legacy.py", "old"), ("legacy.py", "Index")})
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, {
                "legacy.py": "def old(db):\n    return db.execute_query('x')\n\nclass Index:\n    pass\n",
                "server.py": """
                    from backend import legacy
                    from backend.legacy import Index as SqliteIndex

                    def guarded():
                        if not serves_postgres_library():
                            legacy.old(db)

                    def typed(index: SqliteIndex):
                        return index

                    def bad():
                        return legacy.old(db), SqliteIndex()
                    """,
                "db_manager.py": "from backend import legacy\n\ndef m(db):\n    return legacy.old(db)\n",
                # Unaliased imports reach it through the dotted path.
                "worker.py": "import backend.legacy\n\ndef w(db):\n    return backend.legacy.old(db)\n",
                "jobs.py": "import backend\n\ndef j(db):\n    return backend.legacy.old(db)\n",
                # Relative imports resolve against the importing package.
                "tasks.py": "from .legacy import old\n\ndef t(db):\n    return old(db)\n",
                "pkg/__init__.py": "",
                "pkg/run.py": "from .. import legacy\n\ndef r(db):\n    return legacy.old(db)\n",
            })
            problems = reach.reach_problems(tmp, scopes=scopes, dispatch=frozenset())
        self.assertEqual(problems, [
            "backend/jobs.py:4 reaches legacy.py:old",
            "backend/pkg/run.py:4 reaches legacy.py:old",
            "backend/server.py:13 reaches legacy.py:Index",
            "backend/server.py:13 reaches legacy.py:old",
            "backend/tasks.py:4 reaches legacy.py:old",
            "backend/worker.py:4 reaches legacy.py:old",
        ])

    def test_a_renamed_sqlite_only_scope_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, {"legacy.py": "def renamed():\n    pass\n"})
            problems = reach.reach_problems(tmp, scopes=frozenset({("legacy.py", "old")}), dispatch=frozenset())
        self.assertEqual(problems, ["SQLITE_ONLY names legacy.py:old, which is not a top-level definition"])

    def test_a_sqlite_handler_is_bound_only_where_it_is_defined_and_applied_only_by_sqlite_code(self):
        scopes = frozenset({("person_sync.py", "apply")})
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, {
                "person_sync.py": "def apply(db, conn, op, at):\n    pass\n\nclass _Handler:\n    apply = staticmethod(apply)\n",
                "sync/protocol.py": "def run(handler, scope, op):\n    return handler.apply(scope, op)\n",
                "server.py": "def leak(handler, db):\n    return handler.apply(db, None, {}, None)\n",
            })
            problems = reach.reach_problems(tmp, scopes=scopes, dispatch=scopes)
        self.assertEqual(problems, ["backend/server.py:2 calls a SQLite sync handler's apply"])


if __name__ == "__main__":
    unittest.main()
