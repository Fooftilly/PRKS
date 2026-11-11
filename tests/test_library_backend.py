"""The retired ``PRKS_LIBRARY_BACKEND`` is refused, never ignored (PG-C2 #641, ``backend.library_backend``)."""

from __future__ import annotations

import unittest

from backend.library_backend import LibraryBackendError, refuse_stale_selection


class StaleSelectionTests(unittest.TestCase):
    def test_an_environment_without_it_starts(self) -> None:
        refuse_stale_selection({})
        refuse_stale_selection({"PRKS_DATABASE_URL": "postgresql://db/prks"})

    def test_any_value_is_refused_with_the_fix(self) -> None:
        for value in ("postgres", "sqlite", "", "  ", "Postgres"):
            with self.subTest(value=value), self.assertRaises(LibraryBackendError) as caught:
                refuse_stale_selection({"PRKS_LIBRARY_BACKEND": value})
            message = str(caught.exception)
            self.assertIn("PRKS_LIBRARY_BACKEND is no longer read", message)
            self.assertIn("Remove PRKS_LIBRARY_BACKEND", message)
            self.assertIn("PRKS_DATABASE_URL", message)


if __name__ == "__main__":
    unittest.main()
