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
from backend.pg_facade.registry import facade_mixins

LANE_MIXINS = {
    "WorksMixin",
    "PeopleMixin",
    "OrganizationMixin",
    "ResearchMixin",
    "AnnotationsMixin",
    "ProcessingMixin",
    "SearchMixin",
}


class _SavedViewsPort(FacadeMixin):
    @ported
    def get_saved_views(self):
        return ["from-postgres"]


class _OtherPort(FacadeMixin):
    @ported
    def get_saved_views(self):
        return []


class FacadeSkeletonTests(unittest.TestCase):
    def test_facade_is_composed_of_one_empty_mixin_per_lane(self):
        names = {cls.__name__ for cls in facade_mixins(PostgresLibrary)}
        self.assertEqual(names, LANE_MIXINS)
        for cls in facade_mixins(PostgresLibrary):
            own = {name for name in vars(cls) if not name.startswith("__")}
            self.assertEqual(own, set(), f"{cls.__name__} is not empty")

    def test_nothing_is_ported_yet_and_the_registry_is_valid(self):
        self.assertEqual(ported_methods(PostgresLibrary), {})
        self.assertEqual(validate_registry(PostgresLibrary), [])

    def test_unported_method_is_a_typed_refusal_not_a_fallback(self):
        library = PostgresLibrary(object())  # type: ignore[arg-type]
        with self.assertRaises(NotPortedError) as ctx:
            library.get_saved_views()
        self.assertEqual(ctx.exception.method, "get_saved_views")
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
        class Library(_SavedViewsPort, PostgresLibrary):
            pass

        self.assertEqual(ported_methods(Library), {"get_saved_views": "_SavedViewsPort"})
        self.assertEqual(validate_registry(Library), [])
        library = Library(object())  # type: ignore[arg-type]
        self.assertEqual(library.get_saved_views(), ["from-postgres"])
        # The real facade is untouched by a subclass's registrations.
        self.assertEqual(ported_methods(PostgresLibrary), {})

    def test_two_mixins_registering_one_method_are_refused(self):
        class Library(_SavedViewsPort, _OtherPort, PostgresLibrary):
            pass

        with self.assertRaises(RegistryError):
            ported_methods(Library)
        self.assertEqual(len(validate_registry(Library)), 1)

    def test_a_registration_with_another_signature_is_refused(self):
        class Drifted(FacadeMixin):
            @ported
            def get_saved_views(self, limit=10):
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
            def get_saved_views(self):
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

        class Library(Members, PostgresLibrary):
            pass

        self.assertIn("etag_for_representation", ported_methods(Library))


class DescriptorKindTests(unittest.TestCase):
    def test_a_plain_method_cannot_stand_in_for_a_static_method(self):
        class Plain(FacadeMixin):
            @ported
            def etag_for_representation(label, rows):  # type: ignore[no-self-argument]
                return "x"

        class Library(Plain, PostgresLibrary):
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

        class Library(Static, PostgresLibrary):
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

    def test_surface_reads_server_api_and_services_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "api"))
            os.makedirs(os.path.join(tmp, "services"))
            os.makedirs(os.path.join(tmp, "elsewhere"))
            files = {
                "server.py": "db.get_work(1)\nother.get_person(2)\ndb.not_a_method()\n",
                "api/x.py": "def f(db):\n    return db.get_saved_views()\n",
                "services/y.py": "def g(db):\n    db.add_tag('t')\n",
                "elsewhere/z.py": "db.delete_tag(1)\n",
            }
            for rel, body in files.items():
                with open(os.path.join(tmp, rel), "w", encoding="utf-8") as handle:
                    handle.write(textwrap.dedent(body))
            self.assertEqual(
                facade_surface(backend_dir=tmp),
                frozenset({"get_work", "get_saved_views", "add_tag"}),
            )

    def test_coverage_counts_ported_and_unported(self):
        class Library(_SavedViewsPort, PostgresLibrary):
            pass

        report = coverage(Library)
        self.assertIn("get_saved_views", report.ported_in_surface)
        self.assertNotIn("get_saved_views", report.unported)
        self.assertEqual(len(report.unported), len(report.surface) - 1)
        real = coverage()
        self.assertEqual(real.ported_in_surface, frozenset())
        self.assertEqual(real.unported, real.surface)


if __name__ == "__main__":
    unittest.main()
