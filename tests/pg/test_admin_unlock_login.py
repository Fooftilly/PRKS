"""``prks admin unlock-login`` (§9.3, §11.1; L-ID-1 #626, PR 3/5).

The command changes data, so it runs the start check and bump against the
bound data root first, and refuses a database that is not paired with it.
"""

from __future__ import annotations

import io
import threading
from typing import Any, Optional

import psycopg

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.db.errors import DatabaseConfigError, DatabaseUnavailableError
from backend.domain.identity import admin, login_limiter
from backend.domain.identity.login_limiter import LoginLimits
from backend.domain.identity.root_pairing import OwnerSpec
from tests.pg.harness import fetch_one
from tests.pg.test_root_pairing import OWNER, RootPairingTestCase

ACCT = "acct:strasse owner"
KEYS = [ACCT, "ip:192.0.2.1", "dev:" + "ab" * 32, "acct:someone else"]


class UnlockLoginTests(RootPairingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.name, self.root = self.paired("lib")
        self.count_attempts(self.name)

    def paired(self, label: str) -> tuple[str, str]:
        name, root = self.database(), self.new_root(label)
        owner = OwnerSpec("Straße Owner", "Owner", "Library", OWNER.password_hash)
        self.bootstrap(name, root, owner=owner)
        return name, root

    def count_attempts(self, name: str) -> None:
        for _ in range(6):
            login_limiter.count_attempt(self.db(name), KEYS, LoginLimits())

    def attempts(self, name: str) -> dict[str, int]:
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            return dict(conn.execute("SELECT key, attempts FROM prks.login_attempts").fetchall())

    def generation(self, name: str) -> int:
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            return int(fetch_one(conn.execute("SELECT start_generation FROM prks.instance"))[0])

    def open_db(self, name: str) -> Database:
        return Database(DatabaseConfig(conninfo=self.harness.conninfo(name), role="test", pool_min=0))

    def run_cli(self, *argv: str, name: Optional[str] = None, root: Optional[str] = None,
                open_database: Any = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        target = name or self.name
        code = admin.main(
            ["unlock-login", *argv, "--root", root or self.root, "--testing"],
            environ={},
            stdout=out,
            stderr=err,
            open_database=open_database or (lambda _env: self.open_db(target)),
        )
        return code, out.getvalue(), err.getvalue()

    # -- a paired root ------------------------------------------------------

    def test_clears_only_the_account_row_after_the_start_check_and_bump(self) -> None:
        before = self.generation(self.name)
        code, out, err = self.run_cli("  STRASSE   owner ")
        self.assertEqual((code, err), (admin.EXIT_OK, ""))
        self.assertIn("Cleared", out)
        self.assertEqual(set(self.attempts(self.name)), set(KEYS) - {ACCT})
        self.assertEqual(self.generation(self.name), before + 1)  # a data-changing start bumps
        self.assertEqual(self.marker(self.root).start_state.generation, before + 1)

    def test_a_second_unlock_has_nothing_to_clear(self) -> None:
        self.run_cli("Straße Owner")
        code, out, _err = self.run_cli("Straße Owner")
        self.assertEqual(code, admin.EXIT_OK)
        self.assertIn("nothing to clear", out)

    def test_an_unknown_login_is_refused_and_changes_nothing(self) -> None:
        for login in ("someone else", "", "\udcff"):
            with self.subTest(login=login):
                code, out, err = self.run_cli(login)
                self.assertEqual((code, out), (admin.EXIT_REFUSED, ""))
                self.assertEqual(err, "unlock-login: No account has that login.\n")
        self.assertEqual(len(self.attempts(self.name)), 4)

    # -- a root that is not this database's --------------------------------

    def assert_refused_unchanged(self, code: int, err: str, name: str) -> None:
        self.assertEqual(code, admin.EXIT_REFUSED, err)
        self.assertTrue(err.startswith("unlock-login: "), err)
        self.assertNotIn(self.tmp, err)  # no path, connection string or secret
        self.assertNotIn("postgres", err.lower())
        self.assertEqual(self.attempts(name)[ACCT], 6)

    def test_refusals_release_the_root_lease_and_start_key(self) -> None:
        _other_name, other_root = self.paired("other")
        for _ in range(3):
            code, _out, err = self.run_cli("Straße Owner", root=other_root)
            self.assert_refused_unchanged(code, err, self.name)
        # Nothing is left held: the paired run, and an exclusive start, both go through.
        code, _out, err = self.run_cli("Straße Owner")
        self.assertEqual((code, err), (admin.EXIT_OK, ""))
        self.start(self.name, self.root)

    def test_a_root_paired_with_another_database_is_refused(self) -> None:
        _other_name, other_root = self.paired("other")
        before = self.marker_bytes(other_root)
        code, _out, err = self.run_cli("Straße Owner", root=other_root)
        self.assert_refused_unchanged(code, err, self.name)
        self.assertEqual(self.marker_bytes(other_root), before)

    def test_a_stale_copy_of_the_database_is_refused(self) -> None:
        stale = self.copy_of(self.name)  # taken before the next start
        self.start(self.name, self.root)  # the real database moves the root on
        code, _out, err = self.run_cli("Straße Owner", name=stale)
        self.assert_refused_unchanged(code, err, stale)

    def test_a_relocated_root_keeps_its_pairing_and_the_command_starts(self) -> None:
        moved = self.relocated(self.root)
        before = self.generation(self.name)
        code, _out, err = self.run_cli("Straße Owner", root=moved)
        self.assertEqual((code, err), (admin.EXIT_OK, ""))
        self.assertNotIn(ACCT, self.attempts(self.name))
        self.assert_paired(self.name, moved, before + 1)

    def test_an_unbound_or_missing_root_is_refused(self) -> None:
        for root in (self.tmp + "/no-such-root", self.tmp):
            with self.subTest(root=root):
                code, _out, err = self.run_cli("Straße Owner", root=root)
                self.assert_refused_unchanged(code, err, self.name)
                self.assertIn("is not bound", err)

    def test_a_testing_mismatch_is_refused(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        code = admin.main(
            ["unlock-login", "Straße Owner", "--root", self.root],  # no --testing
            environ={}, stdout=out, stderr=err, open_database=lambda _env: self.open_db(self.name),
        )
        self.assert_refused_unchanged(code, err.getvalue(), self.name)

    def test_setup_not_complete_is_refused(self) -> None:
        name, root = self.database(), self.new_root("pending")
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            conn.execute(
                "INSERT INTO prks.login_attempts (key, attempts, window_start) VALUES (%s, 6, now())",
                (ACCT,),
            )
        code, _out, err = self.run_cli("Straße Owner", name=name, root=root)
        self.assert_refused_unchanged(code, err, name)

    # -- failures -----------------------------------------------------------

    def test_database_failures_are_refusals(self) -> None:
        class Broken(Database):
            def command(self, *_args: Any, **_kwargs: Any) -> Any:
                raise DatabaseUnavailableError()

        broken = Broken(self.open_db(self.name).config)
        self.addCleanup(broken.close)
        code, _out, err = self.run_cli("Straße Owner", open_database=lambda _env: broken)
        self.assertEqual(code, admin.EXIT_REFUSED)
        self.assertEqual(err, "unlock-login: The database could not be reached; nothing was changed.\n")
        self.assertEqual(self.attempts(self.name)[ACCT], 6)

        def unconfigured(_env: Any) -> Database:
            raise DatabaseConfigError("database_url_missing", "PRKS_DATABASE_URL is not set.")

        code, _out, err = self.run_cli("x", open_database=unconfigured)
        self.assertEqual((code, err), (admin.EXIT_USAGE, "unlock-login: PRKS_DATABASE_URL is not set.\n"))

    def test_usage_errors(self) -> None:
        for argv in (["unlock-login"], ["unlock-login", "Straße Owner"]):  # login, then --root missing
            with self.subTest(argv=argv):
                code = admin.main(argv, environ={}, stdout=io.StringIO(), stderr=io.StringIO(),
                                  open_database=lambda _env: self.open_db(self.name))
                self.assertEqual(code, admin.EXIT_USAGE)

    def test_unlock_during_a_guessing_campaign_never_fails(self) -> None:
        db = Database(DatabaseConfig(conninfo=self.harness.conninfo(self.name), role="test",
                                     pool_min=0, pool_max=8))
        self.addCleanup(db.close)
        stop = threading.Event()
        errors: list[BaseException] = []

        def guess() -> None:
            while not stop.is_set():
                try:
                    login_limiter.count_attempt(db, [ACCT], LoginLimits())
                except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
                    errors.append(exc)

        threads = [threading.Thread(target=guess) for _ in range(4)]
        for thread in threads:
            thread.start()
        try:
            codes = [self.run_cli("Straße Owner")[0] for _ in range(5)]
        finally:
            stop.set()
            for thread in threads:
                thread.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(set(codes), {admin.EXIT_OK})
