"""Browser sessions and secret tokens (§9.3; L-ID-1 #626, PR 3/5)."""

from __future__ import annotations

import pickle
import threading
import unittest
from datetime import timedelta

from backend.domain.identity import sessions
from backend.domain.identity.secret_tokens import LoginDeviceToken, SessionToken
from backend.domain.identity.sessions import SessionConfigError
from tests.pg.identity_fixtures import IdentityTestCase


class SecretTokenTests(unittest.TestCase):
    def test_tokens_are_256_bit_and_never_print_their_value(self) -> None:
        token = SessionToken.new()
        value = token.reveal()
        self.assertEqual(len(value), 43)
        self.assertEqual(len(token.digest), 32)
        self.assertNotIn(value, repr(token) + str(token) + f"{token}")
        with self.assertRaises(TypeError):
            pickle.dumps(token)
        self.assertNotEqual(SessionToken.new(), token)
        self.assertEqual(SessionToken.parse(value), token)

    def test_parse_refuses_anything_prks_did_not_issue(self) -> None:
        for value in (None, b"x" * 43, "", "x" * 42, "x" * 44, "!" * 43, "x" * 42 + "\n"):
            with self.subTest(value=value):
                self.assertIsNone(SessionToken.parse(value))

    def test_session_and_device_tokens_never_compare_equal(self) -> None:
        value = SessionToken.new().reveal()
        self.assertNotEqual(SessionToken(value), LoginDeviceToken(value))


class IdleSettingTests(unittest.TestCase):
    def test_idle_days(self) -> None:
        self.assertEqual(sessions.idle_from_env({}), timedelta(days=30))
        self.assertEqual(sessions.idle_from_env({"PRKS_SESSION_IDLE_DAYS": "7"}), timedelta(days=7))
        for text in ("0", "366", "1.5", "week"):
            with self.subTest(text=text), self.assertRaises(SessionConfigError):
                sessions.idle_from_env({"PRKS_SESSION_IDLE_DAYS": text})


class SessionTests(IdentityTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.library_id = self.library()
        self.user_id = self.user("Owner", library=self.library_id)

    def new(self, idle: timedelta = sessions.DEFAULT_IDLE) -> sessions.NewSession:
        return self.db.command(
            lambda tx: sessions.create_session(
                tx, user_id=self.user_id, library_id=self.library_id, idle=idle
            )
        )

    def row(self, session: sessions.NewSession) -> dict[str, object]:
        return self.run_sql(
            "SELECT last_used_at, expires_at, revoked_at, expires_at - created_at AS idle "
            "FROM prks.sessions WHERE id = %s",
            (session.session_id,),
        )[0]

    def test_create_and_resolve(self) -> None:
        created = self.new()
        self.assertEqual(self.row(created)["idle"], timedelta(days=30))
        resolved = sessions.resolve_session(self.db, created.token.reveal())
        assert resolved is not None
        self.assertEqual(
            (resolved.session_id, resolved.user_id, resolved.library_id),
            (created.session_id, self.user_id, self.library_id),
        )
        stored = self.run_sql("SELECT token_sha256 FROM prks.sessions")[0]["token_sha256"]
        self.assertEqual(bytes(stored), created.token.digest)

    def test_unknown_and_malformed_cookies_resolve_to_nothing(self) -> None:
        self.new()
        for cookie in (None, "", "garbage", SessionToken.new().reveal()):
            with self.subTest(cookie=cookie):
                self.assertIsNone(sessions.resolve_session(self.db, cookie))

    def test_use_slides_the_expiry_at_most_once_a_minute(self) -> None:
        created = self.new()
        before = self.row(created)
        sessions.resolve_session(self.db, created.token.reveal())
        self.assertEqual(self.row(created), before)  # used within a minute: no write
        self.run_sql(
            "UPDATE prks.sessions SET last_used_at = now() - interval '2 days', "
            "expires_at = now() + interval '1 hour' WHERE id = %s",
            (created.session_id,),
        )
        renewed = sessions.resolve_session(self.db, created.token.reveal())
        assert renewed is not None
        after = self.row(created)
        self.assertGreater(after["expires_at"], before["expires_at"])  # type: ignore[operator]
        self.assertEqual(renewed.expires_at, after["expires_at"])
        remaining = self.run_sql(
            "SELECT expires_at - now() AS left FROM prks.sessions WHERE id = %s", (created.session_id,)
        )[0]["left"]
        self.assertGreater(remaining, timedelta(days=29, hours=23))

    def test_a_configured_idle_period_is_used(self) -> None:
        created = self.new(idle=timedelta(days=2))
        self.assertEqual(self.row(created)["idle"], timedelta(days=2))
        self.run_sql(
            "UPDATE prks.sessions SET last_used_at = now() - interval '2 minutes' WHERE id = %s",
            (created.session_id,),
        )
        sessions.resolve_session(self.db, created.token.reveal(), idle=timedelta(days=2))
        left = self.run_sql(
            "SELECT expires_at - now() AS left FROM prks.sessions WHERE id = %s", (created.session_id,)
        )[0]["left"]
        self.assertLessEqual(left, timedelta(days=2))

    def test_expired_sessions_are_refused_and_not_renewed(self) -> None:
        created = self.new()
        self.run_sql(
            "UPDATE prks.sessions SET expires_at = now() - interval '1 second', "
            "last_used_at = now() - interval '31 days' WHERE id = %s",
            (created.session_id,),
        )
        self.assertIsNone(sessions.resolve_session(self.db, created.token.reveal()))
        self.assertLess(self.row(created)["expires_at"], self.run_sql("SELECT now() AS n")[0]["n"])  # type: ignore[operator]

    def test_sign_out_revokes(self) -> None:
        created = self.new()
        other = self.new()
        self.assertTrue(sessions.revoke_session(self.db, created.token.reveal()))
        self.assertFalse(sessions.revoke_session(self.db, created.token.reveal()))
        self.assertFalse(sessions.revoke_session(self.db, "garbage"))
        self.assertIsNone(sessions.resolve_session(self.db, created.token.reveal()))
        self.assertIsNotNone(sessions.resolve_session(self.db, other.token.reveal()))

    def test_revoke_every_session_of_a_user(self) -> None:
        first, second = self.new(), self.new()
        self.assertEqual(self.db.command(lambda tx: sessions.revoke_user_sessions(tx, self.user_id)), 2)
        for created in (first, second):
            self.assertIsNone(sessions.resolve_session(self.db, created.token.reveal()))

    def test_a_disabled_user_or_a_removed_membership_ends_the_session(self) -> None:
        created = self.new()
        self.run_sql("UPDATE prks.users SET disabled_at = now() WHERE id = %s", (self.user_id,))
        self.assertIsNone(sessions.resolve_session(self.db, created.token.reveal()))
        self.run_sql("UPDATE prks.users SET disabled_at = NULL WHERE id = %s", (self.user_id,))
        self.assertIsNotNone(sessions.resolve_session(self.db, created.token.reveal()))
        self.run_sql("DELETE FROM prks.library_memberships WHERE user_id = %s", (self.user_id,))
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.sessions"), [{"n": 0}])

    def test_a_session_needs_a_membership(self) -> None:
        from backend.db.errors import DatabaseError

        with self.assertRaises(DatabaseError):
            self.db.command(
                lambda tx: sessions.create_session(tx, user_id=self.user_id, library_id=self.library())
            )

    def test_concurrent_use_and_sign_out(self) -> None:
        created = self.new()
        self.run_sql(
            "UPDATE prks.sessions SET last_used_at = now() - interval '2 minutes' WHERE id = %s",
            (created.session_id,),
        )
        db = self.open_db(pool_max=12)
        barrier = threading.Barrier(11)
        errors: list[BaseException] = []

        def use() -> None:
            barrier.wait()
            try:
                sessions.resolve_session(db, created.token.reveal())
            except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
                errors.append(exc)

        def sign_out() -> None:
            barrier.wait()
            sessions.revoke_session(db, created.token.reveal())

        threads = [threading.Thread(target=use) for _ in range(10)] + [threading.Thread(target=sign_out)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [])
        self.assertIsNotNone(self.row(created)["revoked_at"])
        self.assertIsNone(sessions.resolve_session(self.db, created.token.reveal()))

    def test_prune_deletes_expired_and_revoked_sessions_only(self) -> None:
        live, expired, revoked = self.new(), self.new(), self.new()
        self.run_sql(
            "UPDATE prks.sessions SET expires_at = now() - interval '1 second' WHERE id = %s",
            (expired.session_id,),
        )
        sessions.revoke_session(self.db, revoked.token.reveal())
        self.assertEqual(self.db.command(sessions.prune_sessions), 2)
        left = self.run_sql("SELECT id FROM prks.sessions")
        self.assertEqual([row["id"] for row in left], [live.session_id])
