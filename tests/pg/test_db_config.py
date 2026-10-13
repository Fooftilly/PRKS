"""PRKS_DATABASE_URL / *_FILE configuration (#623)."""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any

from backend.db.config import (
    DEFAULT_POOL_MAX,
    ENV_DATABASE_URL,
    ENV_DATABASE_URL_FILE,
    ENV_POOL_MAX,
    DatabaseConfig,
    load_database_config,
)
from backend.db.errors import DatabaseConfigError

# Stands in for a password; it must never reach repr or an error message.
SENTINEL = "sentinel-pw-x9q"
URL = f"postgresql://prks:{SENTINEL}@db.invalid:5432/prks"


class DatabaseConfigTests(unittest.TestCase):
    def _secret_file(self, content: bytes) -> str:
        handle = tempfile.NamedTemporaryFile(delete=False)
        self.addCleanup(os.unlink, handle.name)
        with handle:
            handle.write(content)
        return handle.name

    def assert_refused(self, environ: dict[str, str], reason: str, **kwargs: str) -> DatabaseConfigError:
        with self.assertRaises(DatabaseConfigError) as caught:
            load_database_config(environ, **kwargs)
        self.assertEqual(caught.exception.reason, reason)
        self.assertNotIn(SENTINEL, str(caught.exception))
        self.assertEqual(caught.exception.http_status, 500)
        return caught.exception

    def test_url_from_environment(self) -> None:
        config = load_database_config({ENV_DATABASE_URL: f"  {URL}\n"})
        self.assertEqual(config.conninfo, URL)
        self.assertEqual(config.role, "web")
        self.assertEqual(config.application_name, "prks-web")
        self.assertEqual(config.pool_max, DEFAULT_POOL_MAX["web"])

    def test_url_from_file_strips_trailing_newline(self) -> None:
        path = self._secret_file(f"{URL}\n".encode())
        config = load_database_config({ENV_DATABASE_URL_FILE: path})
        self.assertEqual(config.conninfo, URL)

    def test_key_value_conninfo_is_accepted(self) -> None:
        config = load_database_config({ENV_DATABASE_URL: f"host=db.invalid dbname=prks password={SENTINEL}"})
        self.assertIn("dbname=prks", config.conninfo)

    def test_both_sources_refused(self) -> None:
        path = self._secret_file(URL.encode())
        self.assert_refused({ENV_DATABASE_URL: URL, ENV_DATABASE_URL_FILE: path}, "url_conflict")

    def test_missing_refused(self) -> None:
        self.assert_refused({}, "url_missing")
        self.assert_refused({ENV_DATABASE_URL: "   "}, "url_missing")

    def test_unreadable_file_refused_without_naming_it(self) -> None:
        missing = os.path.join(tempfile.gettempdir(), "prks-missing-secret-file")
        error = self.assert_refused({ENV_DATABASE_URL_FILE: missing}, "url_file_unreadable")
        self.assertNotIn(missing, str(error))

    def test_empty_file_refused(self) -> None:
        self.assert_refused({ENV_DATABASE_URL_FILE: self._secret_file(b"\n")}, "empty_url")

    def test_non_utf8_file_refused(self) -> None:
        self.assert_refused({ENV_DATABASE_URL_FILE: self._secret_file(b"\xff\xfe")}, "url_file_not_utf8")

    def test_oversized_file_refused(self) -> None:
        self.assert_refused(
            {ENV_DATABASE_URL_FILE: self._secret_file(b"x" * (64 * 1024 + 1))},
            "url_file_too_large",
        )

    def test_unparseable_url_refused_without_echo(self) -> None:
        self.assert_refused({ENV_DATABASE_URL: f"host=db password={SENTINEL} =broken"}, "invalid_url")

    def test_secret_never_in_repr(self) -> None:
        config = load_database_config({ENV_DATABASE_URL: URL})
        self.assertNotIn(SENTINEL, repr(config))
        self.assertNotIn(SENTINEL, str(config))

    def test_pool_max_override_applies_to_web_only(self) -> None:
        self.assertEqual(load_database_config({ENV_DATABASE_URL: URL, ENV_POOL_MAX: "3"}).pool_max, 3)
        worker = load_database_config({ENV_DATABASE_URL: URL, ENV_POOL_MAX: "3"}, role="worker")
        self.assertEqual(worker.pool_max, DEFAULT_POOL_MAX["worker"])
        self.assertEqual(worker.application_name, "prks-worker")

    def test_invalid_pool_max_refused(self) -> None:
        self.assert_refused({ENV_DATABASE_URL: URL, ENV_POOL_MAX: "many"}, "invalid_pool_size")
        self.assert_refused({ENV_DATABASE_URL: URL, ENV_POOL_MAX: "0"}, "invalid_pool_size")

    def test_unknown_role_refused(self) -> None:
        self.assert_refused({ENV_DATABASE_URL: URL}, "invalid_role", role="admin")

    def test_session_settings_are_the_documented_defaults(self) -> None:
        config = DatabaseConfig(conninfo=URL)
        self.assertEqual(
            config.session_settings(),
            {
                "search_path": "prks, ext",
                "statement_timeout": "30000",
                "lock_timeout": "5000",
                "idle_in_transaction_session_timeout": "60000",
            },
        )
        self.assertEqual((config.pool_min, config.pool_max, config.pool_timeout_seconds), (1, 10, 10.0))
        self.assertEqual(config.max_idle_seconds, 300.0)

    def test_invalid_explicit_values_refused(self) -> None:
        cases: tuple[dict[str, Any], ...] = (
            {"pool_min": 2, "pool_max": 1},
            {"pool_max": 0},
            {"pool_timeout_seconds": 0},
            {"lock_timeout_ms": -1},
            {"search_path": ()},
            {"search_path": ("prks", "Ext")},
            {"search_path": ("prks; DROP SCHEMA prks",)},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(DatabaseConfigError):
                DatabaseConfig(conninfo=URL, **kwargs)


if __name__ == "__main__":
    unittest.main()
