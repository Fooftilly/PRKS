"""The web process's authentication settings and start check (§9.3, §11.1; L-ID-1 #626, PR 5/5).

``PRKS_AUTH`` is refused unless it is usable (loopback-owner only on a
testing instance bound to a loopback address), and an authenticated process
runs the root-pairing start check and bump under the shared lease of the
root it opened (L-D-1 #630): a foreign root, a stale copy or a testing
mismatch stops it, in loopback-owner mode too, and an unfinished bootstrap
stops it unless it may wait for setup.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import signal
import unittest
from datetime import timedelta
from typing import Any

import psycopg
from psycopg import sql

import prks_app
from backend.domain.identity import root_pairing
from backend.domain.identity.login_limiter import LimiterConfigError
from backend.domain.identity.sessions import SessionConfigError
from backend.domain.identity.web_start import (
    AuthMode,
    WebAuth,
    WebAuthConfigError,
    WebAuthSettings,
    settings_from_env,
    start_web_identity,
)
from backend.server import parse_request_origin
from backend.storage import root_binding
from backend.storage.config import StorageConfig
from tests.pg.test_root_pairing import Crash, RootPairingTestCase, crash_at


# Not a bind: the address loopback-owner mode must refuse, and session mode accepts.
ALL_INTERFACES = "0.0.0.0"  # nosec B104


def settings(env: dict[str, str], *, testing: bool = True, host: str = "127.0.0.1") -> Any:
    return settings_from_env(env, testing=testing, host=host, parse_origin=parse_request_origin)


class SettingsTests(unittest.TestCase):
    def test_unset_is_password_sign_in_or_the_testing_owner(self) -> None:
        for env in ({}, {"PRKS_AUTH": "  "}):
            with self.subTest(env=env):
                self.assertEqual(settings(env, testing=False, host=ALL_INTERFACES).mode, AuthMode.SESSION)
                self.assertEqual(settings(env).mode, AuthMode.LOOPBACK_OWNER)
                # A testing server on another address must ask for password sign-in.
                with self.assertRaises(WebAuthConfigError) as caught:
                    settings(env, host=ALL_INTERFACES)
                self.assertIn("PRKS_AUTH=session", str(caught.exception))

    def test_session_mode(self) -> None:
        got = settings({"PRKS_AUTH": "session", "PRKS_SESSION_IDLE_DAYS": "7"}, testing=False, host=ALL_INTERFACES)
        self.assertEqual(got.mode, AuthMode.SESSION)
        self.assertEqual(got.idle, timedelta(days=7))
        self.assertFalse(got.secure_cookies)

    def test_unknown_mode_is_refused(self) -> None:
        for value in ("loopback", "none", "off", "Session"):
            with self.subTest(value=value), self.assertRaises(WebAuthConfigError):
                settings({"PRKS_AUTH": value})

    def test_loopback_owner_needs_testing_and_a_loopback_bind(self) -> None:
        env = {"PRKS_AUTH": "loopback-owner"}
        for host in ("127.0.0.1", "127.0.0.2", "::1", "[::1]"):
            with self.subTest(host=host):
                self.assertEqual(settings(env, host=host).mode, AuthMode.LOOPBACK_OWNER)
        for host in (ALL_INTERFACES, "::", "192.168.1.5", "localhost", "prks.lan", "::ffff:10.0.0.1"):
            with self.subTest(host=host), self.assertRaises(WebAuthConfigError):
                settings(env, host=host)
        with self.assertRaises(WebAuthConfigError):
            settings(env, testing=False)

    def test_public_origin(self) -> None:
        got = settings({"PRKS_AUTH": "session", "PRKS_PUBLIC_ORIGIN": "https://PRKS.example.org/"})
        self.assertEqual(got.public_origin, ("https", "prks.example.org", None))
        self.assertTrue(got.secure_cookies)
        for bad in ("prks.example.org", "ftp://x.example", "https://x.example/app", "https://u@x.example", "null"):
            with self.subTest(origin=bad), self.assertRaises(WebAuthConfigError):
                settings({"PRKS_AUTH": "session", "PRKS_PUBLIC_ORIGIN": bad})

    def test_unusable_limits_are_refused(self) -> None:
        with self.assertRaises(LimiterConfigError):
            settings({"PRKS_AUTH": "session", "PRKS_LOGIN_ACCOUNT_LIMIT": "0"})
        with self.assertRaises(LimiterConfigError):
            settings({"PRKS_AUTH": "session", "PRKS_TRUSTED_PROXIES": "proxy.lan"})
        with self.assertRaises(SessionConfigError):
            settings({"PRKS_AUTH": "session", "PRKS_SESSION_IDLE_DAYS": "0"})


class WebStartTestCase(RootPairingTestCase):
    def bootstrap_and_release(self, name: str, root: str) -> None:
        """Bootstrap, then let go of the root's lease and the pool, as the admin command does."""
        lease = root_binding.acquire_shared_root_lease(root)
        try:
            self.bootstrap(name, root, lease=lease)
        finally:
            lease.release()
        self.close(name)


class StartTests(WebStartTestCase):
    def web_settings(self, mode: AuthMode = AuthMode.SESSION) -> WebAuthSettings:
        got = settings({"PRKS_AUTH": mode.value})
        assert got is not None
        return got

    def ready(self, root_name: str = "lib") -> tuple[str, str]:
        name = self.database()
        root = self.new_root(root_name)
        self.bootstrap_and_release(name, root)
        return name, root

    def web_start(self, name: str, root: str, mode: AuthMode = AuthMode.SESSION, **kwargs: Any) -> WebAuth:
        kwargs.setdefault("testing", True)
        lease = kwargs.pop("lease", None) or self.lease(root)
        return start_web_identity(self.db(name), self.web_settings(mode), root=root, lease=lease, **kwargs)

    def test_a_paired_database_starts_and_bumps_once(self) -> None:
        name, root = self.ready()
        before = self.db_state(name)
        lease = self.lease(root)
        auth = self.web_start(name, root, lease=lease)
        self.assertEqual(auth.instance_id, self.instance(name)["id"])
        self.assert_paired(name, root, before.generation + 1)
        # The caller's shared lease stays held for the process's whole run.
        self.assertTrue(lease.held)

    def test_a_first_run_waits_for_setup_then_bumps_once(self) -> None:
        name = self.database()
        root = self.new_root()
        lease = self.lease(root)
        calls: list[int] = []

        def wait() -> bool:
            calls.append(1)
            if len(calls) == 2:  # setup completes while the web process waits
                self.bootstrap(name, root, lease=self.lease(root))
            return True

        auth = self.web_start(name, root, lease=lease, wait_for_setup=wait)
        self.assertEqual(len(calls), 2)
        self.assertEqual(auth.instance_id, self.instance(name)["id"])
        # The claim was generation 1; this process's start is the one bump.
        self.assert_paired(name, root, 2)

    def test_a_wait_that_gives_up_is_refused(self) -> None:
        name = self.database()
        root = self.new_root()
        self.refused("setup_pending", self.web_start, name, root, wait_for_setup=lambda: False)

    def test_loopback_owner_resolves_the_bootstrap_owner(self) -> None:
        name, root = self.ready()
        auth = self.web_start(name, root, AuthMode.LOOPBACK_OWNER)
        session = auth.loopback_session()
        assert session is not None
        owner = self.db(name).query(
            lambda tx: tx.execute("SELECT user_id, library_id FROM prks.library_memberships").fetchone()
        )
        self.assertEqual((session.user_id, session.library_id), (owner["user_id"], owner["library_id"]))

    def test_loopback_owner_needs_exactly_one_owner(self) -> None:
        name, root = self.ready()
        self.db(name).command(lambda tx: tx.execute("UPDATE prks.users SET disabled_at = now()"))
        self.refused("loopback_owner_missing", self.web_start, name, root, AuthMode.LOOPBACK_OWNER)

    def test_a_second_library_is_refused(self) -> None:
        name, root = self.ready()
        self.db(name).command(lambda tx: tx.execute(
            "INSERT INTO prks.libraries (id, name) VALUES ('LIB-' || upper(md5(random()::text)), 'Second')"
        ))
        for mode in AuthMode:
            with self.subTest(mode=mode):
                self.refused("library_not_unique", self.web_start, name, root, mode)

    def test_the_served_library_is_the_bootstrap_library(self) -> None:
        name, root = self.ready()
        auth = self.web_start(name, root)
        library = self.db(name).query(lambda tx: tx.execute("SELECT id FROM prks.libraries").fetchone())
        self.assertEqual(auth.library_id, library["id"])

    def test_unfinished_setup_is_refused(self) -> None:
        name = self.database()
        root = self.new_root()
        self.refused("setup_pending", self.web_start, name, root, AuthMode.SESSION)
        # Not a testing process: loopback-owner never sets an instance up.
        self.refused("setup_pending", self.web_start, name, root, AuthMode.LOOPBACK_OWNER, testing=False)
        self.assertIsNone(self.instance(name))

    def test_a_testing_owner_sets_up_a_fresh_database_once(self) -> None:
        name = self.database()
        root = self.new_root()
        auth = self.web_start(name, root, AuthMode.LOOPBACK_OWNER)
        with psycopg.connect(self.harness.conninfo(name)) as conn:
            instance = conn.execute("SELECT id, testing, setup_state FROM prks.instance").fetchone()
        self.assertEqual((auth.instance_id, instance[1], instance[2]), (instance[0], True, "ready"))
        # The claim was this start's bump; the next start bumps as usual.
        self.assertEqual(self.db_state(name).generation, 1)
        self.web_start(name, root, AuthMode.LOOPBACK_OWNER)
        self.assertEqual(self.db_state(name).generation, 2)

    def test_a_testing_owner_never_sets_up_an_unmarked_database(self) -> None:
        name = self.database()
        root = self.new_root()
        conninfo, marker = self.harness.conninfo(name), self.harness.marker_of(name)
        db = self.db(name)  # opened while the harness still owns it

        def comment(value: Any) -> None:
            with psycopg.connect(conninfo, autocommit=True) as conn:
                conn.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                    sql.Identifier(name), sql.Literal(value)))

        comment(None)
        try:
            self.refused("not_a_testing_database", start_web_identity, db,
                         self.web_settings(AuthMode.LOOPBACK_OWNER), root=root, lease=self.lease(root), testing=True)
            with psycopg.connect(conninfo) as conn:
                self.assertIsNone(conn.execute("SELECT id FROM prks.instance").fetchone())
        finally:
            comment(marker)

    def test_a_foreign_root_is_refused_in_every_mode(self) -> None:
        name, _root = self.ready()
        _other_db, other_root = self.ready("other")
        for mode in AuthMode:
            with self.subTest(mode=mode):
                self.refused("root_pairing_mismatch", self.web_start, name, other_root, mode)

    def test_a_stale_copy_is_refused(self) -> None:
        name, root = self.ready()
        copy = self.copy_of(name)
        self.web_start(name, root)  # the original runs on
        for mode in AuthMode:
            with self.subTest(mode=mode):
                self.refused("database_behind", self.web_start, copy, root, mode)

    def test_a_relocated_root_keeps_its_pairing_and_starts(self) -> None:
        name, root = self.ready()
        moved = self.relocated(root)
        before = self.db_state(name)
        auth = self.web_start(name, moved)
        self.assertEqual(auth.instance_id, self.instance(name)["id"])
        self.assert_paired(name, moved, before.generation + 1)

    def test_a_testing_mismatch_is_refused(self) -> None:
        name, root = self.ready()
        self.refused("testing_mismatch", self.web_start, name, root, testing=False)

    def test_an_interrupted_bump_is_reconciled_before_serving(self) -> None:
        name, root = self.ready()
        with self.assertRaises(Crash):
            root_pairing.start_process(
                self.db(name), root=root, lease=self.lease(root), testing=True, bump=True,
                faults=crash_at(root_pairing.FAULT_AFTER_DATABASE_BUMP),
            )
        ahead = self.db_state(name)
        self.assertEqual(self.marker(root).start_state.generation, ahead.generation - 1)
        self.web_start(name, root)
        self.assert_paired(name, root, ahead.generation + 1)


class AppStartTests(WebStartTestCase):
    """``prks_app.prepare_identity`` and ``start_identity`` exit with status 2 instead of serving."""

    def args(self, host: str = "127.0.0.1", testing: bool = True) -> argparse.Namespace:
        return argparse.Namespace(host=host, testing=testing)

    def config(self, root: str) -> StorageConfig:
        return StorageConfig.for_testing(root)

    def app_start(self, env: dict[str, str], root: str, **kwargs: Any) -> Any:
        wait = kwargs.pop("wait", None)
        config = self.config(root)
        prepared = prks_app.prepare_identity(self.args(**kwargs), config, env)
        bound = root_binding.open_storage_root(config, register=False, paired=True)
        self.addCleanup(bound.release)
        return prks_app.start_identity(prepared, bound, testing=True, wait=wait)

    def exits(self, env: dict[str, str], root: str, code: int = 2, **kwargs: Any) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.app_start(env, root, **kwargs)
        self.assertEqual(ctx.exception.code, code)

    def test_without_a_database_a_web_process_does_not_start(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            prks_app.prepare_identity(self.args(host=ALL_INTERFACES, testing=False), self.config(self.tmp), {})
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("PRKS_DATABASE_URL", stderr.getvalue())

    def test_a_stop_while_waiting_for_setup_exits_0_and_lets_go_of_the_root(self) -> None:
        name = self.database()
        root = self.new_root()
        env = {"PRKS_AUTH": "session", "PRKS_DATABASE_URL": self.harness.conninfo(name)}
        wait = prks_app.SetupWait(poll=0.05, stderr=io.StringIO(), step=0.01)
        wait._signals.requested.append(signal.SIGTERM)  # as the handler records it
        self.exits(env, root, code=0, wait=wait)
        # Both locks are gone: the SQLite runtime's exclusive lease is free.
        root_binding.open_storage_root(self.config(root), register=False).release()

    def test_a_refusal_while_a_stop_is_pending_still_exits_2(self) -> None:
        name = self.database()
        root = self.new_root()
        self.bootstrap_and_release(name, root)
        env = {"PRKS_AUTH": "session", "PRKS_DATABASE_URL": self.harness.conninfo(name)}
        wait = prks_app.SetupWait(poll=0.05, stderr=io.StringIO(), step=0.01)
        wait._signals.requested.append(signal.SIGTERM)
        self.exits(env, self.new_root("other"), code=2, wait=wait)  # not this database's root

    def test_a_stop_during_the_last_check_exits_0_instead_of_serving(self) -> None:
        name = self.database()
        root = self.new_root()
        self.bootstrap_and_release(name, root)
        env = {"PRKS_AUTH": "session", "PRKS_DATABASE_URL": self.harness.conninfo(name)}
        wait = prks_app.SetupWait(poll=0.05, stderr=io.StringIO(), step=0.01)
        wait._signals.requested.append(signal.SIGTERM)  # recorded while the check ran
        self.exits(env, root, code=0, wait=wait)
        root_binding.open_storage_root(self.config(root), register=False).release()

    def test_refusals_exit(self) -> None:
        name = self.database()
        root = self.new_root()
        url = {"PRKS_DATABASE_URL": self.harness.conninfo(name)}
        self.exits({"PRKS_AUTH": "loopback-owner", **url}, root, host=ALL_INTERFACES)
        self.exits({"PRKS_AUTH": "loopback-owner", **url}, root, testing=False)
        self.exits({"PRKS_AUTH": "session"}, root)  # no database configured
        self.exits({"PRKS_AUTH": "session", **url}, root)  # setup not done
        self.bootstrap_and_release(name, root)
        other = self.new_root("other")
        self.exits({"PRKS_AUTH": "loopback-owner", **url}, other)  # not this database's root

    def test_a_paired_start_returns_the_runtime(self) -> None:
        name = self.database()
        root = self.new_root()
        self.bootstrap_and_release(name, root)
        env = {"PRKS_AUTH": "loopback-owner", "PRKS_DATABASE_URL": self.harness.conninfo(name)}
        auth = self.app_start(env, root)
        self.addCleanup(auth.db.close)
        self.assertEqual(auth.mode, AuthMode.LOOPBACK_OWNER)
        self.assertIsNotNone(auth.loopback_session())

