"""``prks admin unlock-login`` (§9.3; L-ID-1 #626, PR 3/5)."""

from __future__ import annotations

import io
import threading
from typing import Any

from backend.db.database import Database
from backend.db.errors import DatabaseConfigError, DatabaseUnavailableError
from backend.domain.identity import admin, login_limiter
from backend.domain.identity.login_limiter import LoginLimits
from tests.pg.identity_fixtures import IdentityTestCase


class UnlockLoginTests(IdentityTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user("Straße Owner")
        self.keys = ["acct:strasse owner", "ip:192.0.2.1", "dev:" + "ab" * 32, "acct:someone else"]
        for _ in range(6):
            login_limiter.count_attempt(self.db, self.keys, LoginLimits())

    def run_cli(self, *argv: str, open_database: Any = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = admin.main(
            list(argv),
            environ={},
            stdout=out,
            stderr=err,
            open_database=open_database or (lambda _env: self.open_db()),
        )
        return code, out.getvalue(), err.getvalue()

    def test_clears_only_the_account_row_matched_by_identity_key(self) -> None:
        code, out, err = self.run_cli("unlock-login", "  STRASSE   owner ")
        self.assertEqual((code, err), (admin.EXIT_OK, ""))
        self.assertIn("Cleared", out)
        self.assertEqual(set(self.attempts()), set(self.keys) - {"acct:strasse owner"})

    def test_a_second_unlock_has_nothing_to_clear(self) -> None:
        self.run_cli("unlock-login", "Straße Owner")
        code, out, _err = self.run_cli("unlock-login", "Straße Owner")
        self.assertEqual(code, admin.EXIT_OK)
        self.assertIn("nothing to clear", out)

    def test_an_unknown_login_is_refused_and_changes_nothing(self) -> None:
        for name in ("someone else", "", "\udcff"):
            with self.subTest(name=name):
                code, out, err = self.run_cli("unlock-login", name)
                self.assertEqual((code, out), (admin.EXIT_REFUSED, ""))
                self.assertEqual(err, "unlock-login: No account has that login.\n")
        self.assertEqual(len(self.attempts()), 4)

    def test_database_failures_are_refusals(self) -> None:
        class Broken(Database):
            def command(self, *_args: Any, **_kwargs: Any) -> Any:
                raise DatabaseUnavailableError()

        broken = Broken(self.open_db().config)
        self.addCleanup(broken.close)
        code, _out, err = self.run_cli("unlock-login", "Straße Owner", open_database=lambda _env: broken)
        self.assertEqual(code, admin.EXIT_REFUSED)
        self.assertEqual(err, "unlock-login: The database could not be reached; nothing was changed.\n")

        def unconfigured(_env: Any) -> Database:
            raise DatabaseConfigError("database_url_missing", "PRKS_DATABASE_URL is not set.")

        code, _out, err = self.run_cli("unlock-login", "x", open_database=unconfigured)
        self.assertEqual((code, err), (admin.EXIT_USAGE, "unlock-login: PRKS_DATABASE_URL is not set.\n"))

    def test_usage_errors(self) -> None:
        code, _out, _err = self.run_cli("unlock-login")
        self.assertEqual(code, admin.EXIT_USAGE)

    def test_unlock_during_a_guessing_campaign_never_fails(self) -> None:
        db = self.open_db(pool_max=8)
        stop = threading.Event()
        errors: list[BaseException] = []

        def guess() -> None:
            while not stop.is_set():
                try:
                    login_limiter.count_attempt(db, ["acct:strasse owner"], LoginLimits())
                except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
                    errors.append(exc)

        threads = [threading.Thread(target=guess) for _ in range(4)]
        for thread in threads:
            thread.start()
        try:
            codes = [self.run_cli("unlock-login", "Straße Owner")[0] for _ in range(10)]
        finally:
            stop.set()
            for thread in threads:
                thread.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(set(codes), {admin.EXIT_OK})
