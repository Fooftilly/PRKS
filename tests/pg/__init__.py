"""PostgreSQL plumbing and harness tests (#623, PG-1).

They need psycopg and a disposable PostgreSQL 18 server, so they load only
when ``PRKS_TEST_DATABASE_URL`` is set (``scripts/test-postgres`` sets it).
``python run_tests.py`` therefore keeps running the SQLite suites only and
does not import psycopg. This module must not import psycopg itself.
"""

import os
import unittest


def load_tests(loader: unittest.TestLoader, standard_tests: unittest.TestSuite, pattern: str | None):
    if not os.environ.get("PRKS_TEST_DATABASE_URL", "").strip():
        return standard_tests
    this_dir = os.path.dirname(os.path.abspath(__file__))
    top_level = os.path.dirname(os.path.dirname(this_dir))
    standard_tests.addTests(
        loader.discover(start_dir=this_dir, pattern=pattern or "test*.py", top_level_dir=top_level)
    )
    return standard_tests
