"""The web process's authentication settings and start check (§9.3, §11.1; L-ID-1 #626, PR 5/5).

``PRKS_AUTH`` is refused unless it is usable (loopback-owner only on a
testing instance bound to a loopback address), and an authenticated process
runs the root-pairing start check and bump before it opens its storage root:
a foreign root, a stale copy, a testing mismatch or an unfinished bootstrap
stops it, in loopback-owner mode too.
"""

from __future__ import annotations

import argparse
import unittest
from datetime import timedelta
from typing import Any

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
    def test_off_unless_asked_for(self) -> None:
        self.assertIsNone(settings({}))
        self.assertIsNone(settings({"PRKS_AUTH": "  "}))

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
        return start_web_identity(self.db(name), self.web_settings(mode), root=root, **kwargs)

    def test_a_paired_database_starts_and_bumps_once(self) -> None:
        name, root = self.ready()
        before = self.db_state(name)
        auth = self.web_start(name, root)
        self.assertEqual(auth.instance_id, self.instance(name)["id"])
        self.assert_paired(name, root, before.generation + 1)
        # The shared lease is released: the SQLite runtime's exclusive lease is free.
        bound = root_binding.open_storage_root(StorageConfig.for_testing(root), register=False)
        bound.release()

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
        for mode in AuthMode:
            with self.subTest(mode=mode):
                self.refused("setup_pending", self.web_start, name, root, mode)

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
    """``prks_app.start_identity`` exits with status 2 instead of serving."""

    def args(self, host: str = "127.0.0.1", testing: bool = True) -> argparse.Namespace:
        return argparse.Namespace(host=host, testing=testing)

    def config(self, root: str) -> StorageConfig:
        return StorageConfig.for_testing(root)

    def exits(self, env: dict[str, str], root: str, **kwargs: Any) -> None:
        with self.assertRaises(SystemExit) as ctx:
            prks_app.start_identity(self.args(**kwargs), self.config(root), env)
        self.assertEqual(ctx.exception.code, 2)

    def test_without_prks_auth_nothing_happens(self) -> None:
        self.assertIsNone(prks_app.start_identity(self.args(host=ALL_INTERFACES), self.config(self.tmp), {}))

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
        auth = prks_app.start_identity(self.args(), self.config(root), env)
        self.addCleanup(auth.db.close)
        self.assertEqual(auth.mode, AuthMode.LOOPBACK_OWNER)
        self.assertIsNotNone(auth.loopback_session())

