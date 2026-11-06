"""``PRKS_LIBRARY_BACKEND`` parsing (PG-C1 #640, ``backend.library_backend``)."""

from __future__ import annotations

import unittest

from backend.library_backend import LibraryBackendError, postgres_selected


class LibraryBackendTests(unittest.TestCase):
    def test_unset_or_blank_keeps_the_sqlite_store(self) -> None:
        self.assertFalse(postgres_selected({}))
        self.assertFalse(postgres_selected({"PRKS_LIBRARY_BACKEND": "  "}))

    def test_postgres_selects_the_postgresql_library(self) -> None:
        self.assertTrue(postgres_selected({"PRKS_LIBRARY_BACKEND": " postgres "}))

    def test_any_other_value_is_refused(self) -> None:
        for value in ("sqlite", "Postgres", "pg", "1"):
            with self.subTest(value=value), self.assertRaises(LibraryBackendError):
                postgres_selected({"PRKS_LIBRARY_BACKEND": value})


if __name__ == "__main__":
    unittest.main()
