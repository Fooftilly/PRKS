"""Password login (§9.3; L-ID-1 #626, PR 3/5): outcomes, equal work, limiter, device tokens."""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any
from unittest import mock

import psycopg
from argon2 import PasswordHasher, extract_parameters

from backend.db.config import DatabaseConfig
from backend.db.database import Database
from backend.db.errors import DatabaseUnavailableError
from backend.domain.identity import login as login_module
from backend.domain.identity import passwords, sessions
from backend.domain.identity.login import LoginOutcome
from backend.domain.identity.login_limiter import ClientAddress, LoginLimits, account_key, device_key
from backend.domain.identity.secret_tokens import LoginDeviceToken
from tests.pg.identity_fixtures import CLIENT, PASSWORD, CountingHasher, IdentityTestCase, params_of

OTHER_CLIENT = ClientAddress.resolve("198.51.100.7")


class LoginOutcomeTests(IdentityTestCase):
    def test_correct_password_creates_a_session_and_a_device_token(self) -> None:
        user_id = self.user()
        result = self.login()
        self.assertIs(result.outcome, LoginOutcome.SUCCEEDED)
        self.assertEqual((result.public_reason, result.http_status), ("signed_in", 200))
        assert result.session is not None and result.device_token is not None
        self.assertEqual(result.session.user_id, user_id)
        valid = sessions.resolve_session(self.db, result.session.token.reveal())
        assert valid is not None
        self.assertEqual((valid.user_id, valid.library_id), (user_id, result.session.library_id))
        stored = self.run_sql("SELECT user_id FROM prks.login_device_tokens")
        self.assertEqual(stored, [{"user_id": user_id}])
        self.assertEqual(self.hasher.verifies, 1)

    def test_login_names_match_by_identity_key(self) -> None:
        self.user("Straße Owner")
        self.assertIs(self.login("  STRASSE   owner ").outcome, LoginOutcome.SUCCEEDED)

    def test_wrong_unknown_disabled_and_passwordless_look_alike_and_cost_the_same(self) -> None:
        self.user("Owner")
        self.user("Gone", disabled=True)
        self.user("Unset", password=None)
        self.user("Corrupt", password_hash="$argon2id$v=19$m=8,t=1,p=1$c29tZXNhbHQ$!!!")
        cases = {
            "wrong password": ("Owner", "not the passphrase", LoginOutcome.INVALID_CREDENTIALS),
            "unknown login": ("Nobody", PASSWORD, LoginOutcome.INVALID_CREDENTIALS),
            "disabled": ("Gone", PASSWORD, LoginOutcome.INVALID_CREDENTIALS),
            "no password": ("Unset", PASSWORD, LoginOutcome.INVALID_CREDENTIALS),
            "invalid stored hash": ("Corrupt", PASSWORD, LoginOutcome.STORED_HASH_INVALID),
        }
        for label, (name, password, outcome) in cases.items():
            with self.subTest(label):
                before = self.hasher.verifies
                result = self.login(name, password, client=ClientAddress.resolve(f"192.0.2.{len(label)}"))
                self.assertIs(result.outcome, outcome)
                self.assertEqual((result.public_reason, result.http_status), ("invalid_credentials", 401))
                self.assertIsNone(result.session)
                self.assertIsNone(result.device_token)
                # Exactly one Argon2id verification with the current parameters.
                self.assertEqual(self.hasher.verifies, before + 1)
                self.assertEqual(self.hasher.verified[-1], params_of(self.hasher))
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.sessions"), [{"n": 0}])

    def test_the_dummy_hash_uses_the_login_hashers_parameters(self) -> None:
        slower = CountingHasher(time_cost=2)
        self.user("Owner")
        self.login("Nobody", PASSWORD, hasher=slower)
        self.assertEqual(slower.verified, [params_of(slower)])
        self.assertFalse(passwords.verify_password(login_module.dummy_hash(slower), PASSWORD, hasher=slower))

    def test_the_default_dummy_hash_is_precomputed_with_the_pinned_parameters(self) -> None:
        default = passwords.default_hasher()
        stored = login_module.dummy_hash(default)
        params = extract_parameters(stored)
        expected = passwords.PARAMETERS
        self.assertEqual(
            (params.type, params.time_cost, params.memory_cost, params.parallelism,
             params.hash_len, params.salt_len),
            (expected.type, expected.time_cost, expected.memory_cost, expected.parallelism,
             expected.hash_len, expected.salt_len),
        )
        with mock.patch.object(PasswordHasher, "hash", side_effect=AssertionError("hashed")):
            self.assertEqual(login_module.dummy_hash(default), stored)
            login_module.warm_dummy_hash()

    def test_unusable_input_is_refused_without_any_work(self) -> None:
        self.user("Owner")
        for label, name, password in (
            ("name not text", None, PASSWORD),
            ("empty name", "", PASSWORD),
            ("blank name", "   ", PASSWORD),
            ("long name", "x" * 300, PASSWORD),
            ("NUL in name", "Own\x00er", PASSWORD),
            ("password not text", "Owner", b"bytes"),
            ("empty password", "Owner", ""),
            ("NUL in password", "Owner", "pass\x00word"),
            ("long password", "Owner", "p" * 1025),
            ("lone surrogate", "Owner", "pass\udc00"),
        ):
            with self.subTest(label):
                result = self.login(name, password)
                self.assertIs(result.outcome, LoginOutcome.INVALID_CREDENTIALS)
        self.assertEqual(self.hasher.verifies, 0)
        self.assertEqual(self.attempts(), {})

    def test_an_outdated_hash_is_rewritten_at_login(self) -> None:
        older = CountingHasher(time_cost=2)
        user_id = self.user("Owner", hasher=older)
        self.assertIs(self.login().outcome, LoginOutcome.SUCCEEDED)
        stored = self.run_sql("SELECT password_hash FROM prks.users WHERE id = %s", (user_id,))[0]
        self.assertFalse(passwords.needs_rehash(stored["password_hash"], hasher=self.hasher))
        self.assertTrue(passwords.verify_password(stored["password_hash"], PASSWORD, hasher=self.hasher))

    def test_library_choice(self) -> None:
        user_id = self.user("Owner")
        second = self.library("Second")
        self.join(user_id, second)
        self.login(password="wrong")
        both = self.login()
        self.assertIs(both.outcome, LoginOutcome.NO_LIBRARY)
        # No session, so no answer that confirms the password and no side effects.
        self.assertEqual((both.public_reason, both.http_status), ("invalid_credentials", 401))
        self.assertIsNone(both.device_token)
        self.assertEqual(self.attempts()[account_key("owner")], 2)
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.login_device_tokens"), [{"n": 0}])
        chosen = self.login(library_id=second)
        assert chosen.session is not None
        self.assertEqual(chosen.session.library_id, second)
        self.assertIs(self.login(library_id=self.library("Elsewhere")).outcome, LoginOutcome.NO_LIBRARY)
        self.user("Loner", library=None)
        self.assertIs(self.login("Loner").outcome, LoginOutcome.NO_LIBRARY)

    def test_no_library_is_refused_with_the_same_database_work_as_a_wrong_password(self) -> None:
        self.user("Loner", library=None)
        user_id = self.user("Member")
        calls: dict[str, list[str]] = {}
        for label, name, password, library in (
            ("wrong password", "Loner", "wrong", None),
            ("no membership", "Loner", PASSWORD, None),
            ("not a member", "Member", PASSWORD, self.library("Elsewhere")),
        ):
            seen: list[str] = []
            with mock.patch.object(self.db, "command", wraps=self.db.command) as command, \
                    mock.patch.object(self.db, "query", wraps=self.db.query) as query, \
                    mock.patch.object(login_module, "_succeed", side_effect=AssertionError("committed")):
                result = self.login(name, password, library_id=library, client=ClientAddress.resolve(f"192.0.2.{len(calls) + 50}"))
                seen = [f"command x{command.call_count}", f"query x{query.call_count}"]
            self.assertEqual(result.public_reason, "invalid_credentials", label)
            calls[label] = seen
        self.assertEqual(calls["no membership"], calls["wrong password"])
        self.assertEqual(calls["not a member"], calls["wrong password"])
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.sessions WHERE user_id = %s", (user_id,)), [{"n": 0}])

    def test_a_password_changed_during_the_check_is_refused(self) -> None:
        user_id = self.user("Owner")

        def change() -> None:
            self.hasher.on_verify = None
            new_hash = passwords.hash_password("a different passphrase", hasher=self.hasher)
            self.run_sql("UPDATE prks.users SET password_hash = %s WHERE id = %s", (new_hash, user_id))

        self.hasher.on_verify = change
        result = self.login()
        self.assertIs(result.outcome, LoginOutcome.INVALID_CREDENTIALS)
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.sessions"), [{"n": 0}])
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.login_device_tokens"), [{"n": 0}])

    def test_a_concurrent_rehash_does_not_fail_a_correct_login(self) -> None:
        user_id = self.user("Owner", hasher=CountingHasher(time_cost=2))
        rehashed = passwords.hash_password(PASSWORD, hasher=self.hasher)

        def other_login_rehashes() -> None:
            self.hasher.on_verify = None
            self.run_sql("UPDATE prks.users SET password_hash = %s WHERE id = %s", (rehashed, user_id))

        self.hasher.on_verify = other_login_rehashes
        result = self.login()
        self.assertIs(result.outcome, LoginOutcome.SUCCEEDED)
        self.assertEqual(self.hasher.verifies, 2)  # checked again against the new hash
        stored = self.run_sql("SELECT password_hash FROM prks.users WHERE id = %s", (user_id,))
        self.assertEqual(stored, [{"password_hash": rehashed}])  # not rehashed twice

    def test_an_account_disabled_during_the_check_is_refused(self) -> None:
        user_id = self.user("Owner")
        self.hasher.on_verify = lambda: self.run_sql(
            "UPDATE prks.users SET disabled_at = now() WHERE id = %s", (user_id,)
        )
        self.assertIs(self.login().outcome, LoginOutcome.INVALID_CREDENTIALS)
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.sessions"), [{"n": 0}])


class LoginLimiterTests(IdentityTestCase):
    def test_sixth_account_attempt_is_refused_before_the_password_check(self) -> None:
        self.user("Owner")
        for attempt in range(5):
            client = ClientAddress.resolve(f"203.0.113.{attempt}")  # any address
            self.assertIs(self.login(password="wrong", client=client).outcome, LoginOutcome.INVALID_CREDENTIALS)
        self.assertEqual(self.hasher.verifies, 5)
        refused = self.login(client=ClientAddress.resolve("203.0.113.99"))  # even the right password
        self.assertIs(refused.outcome, LoginOutcome.THROTTLED)
        self.assertEqual((refused.public_reason, refused.http_status), ("too_many_attempts", 429))
        self.assertEqual(refused.throttled_kind, "acct")
        assert refused.retry_after_seconds is not None
        self.assertTrue(1 <= refused.retry_after_seconds <= 900)
        self.assertEqual(self.hasher.verifies, 5)

    def test_unknown_accounts_are_limited_like_real_ones(self) -> None:
        for _ in range(5):
            self.assertIs(self.login("Nobody").outcome, LoginOutcome.INVALID_CREDENTIALS)
        self.assertIs(self.login("Nobody").outcome, LoginOutcome.THROTTLED)

    def test_twenty_first_address_attempt_is_refused(self) -> None:
        for attempt in range(20):
            self.assertIs(
                self.login(f"guess-{attempt}").outcome, LoginOutcome.INVALID_CREDENTIALS
            )
        refused = self.login("guess-final")
        self.assertIs(refused.outcome, LoginOutcome.THROTTLED)
        self.assertEqual(refused.throttled_kind, "ip")
        self.assertEqual(self.hasher.verifies, 20)
        # Another address is not affected.
        self.assertIs(self.login("guess-other", client=OTHER_CLIENT).outcome, LoginOutcome.INVALID_CREDENTIALS)

    def test_ipv6_addresses_share_their_64(self) -> None:
        limits = LoginLimits(address=2)
        for host in ("2001:db8:1:2::1", "2001:db8:1:2:ffff::9"):
            self.login(f"n-{host}", client=ClientAddress.resolve(host), limits=limits)
        same_64 = self.login("n-3", client=ClientAddress.resolve("2001:db8:1:2::abcd"), limits=limits)
        self.assertIs(same_64.outcome, LoginOutcome.THROTTLED)
        other_64 = self.login("n-4", client=ClientAddress.resolve("2001:db8:1:3::1"), limits=limits)
        self.assertIs(other_64.outcome, LoginOutcome.INVALID_CREDENTIALS)

    def test_success_resets_the_account_key_but_never_the_address_key(self) -> None:
        self.user("Owner")
        for _ in range(3):
            self.login(password="wrong")
        self.assertIs(self.login().outcome, LoginOutcome.SUCCEEDED)
        counts = self.attempts()
        self.assertNotIn(account_key("owner"), counts)
        self.assertEqual(counts[CLIENT.key], 4)

    def test_an_expired_window_starts_over(self) -> None:
        self.user("Owner")
        for _ in range(6):
            self.login(password="wrong")
        self.assertIs(self.login().outcome, LoginOutcome.THROTTLED)
        self.run_sql("UPDATE prks.login_attempts SET window_start = now() - interval '15 minutes 1 second'")
        self.assertIs(self.login().outcome, LoginOutcome.SUCCEEDED)
        self.assertEqual(self.attempts(), {CLIENT.key: 1})

    def test_configured_limits_and_window_apply(self) -> None:
        self.user("Owner")
        limits = LoginLimits(window=timedelta(seconds=30), account=2)
        self.login(password="wrong", limits=limits)
        self.login(password="wrong", limits=limits)
        refused = self.login(limits=limits)
        self.assertIs(refused.outcome, LoginOutcome.THROTTLED)
        assert refused.retry_after_seconds is not None
        self.assertLessEqual(refused.retry_after_seconds, 30)

    def test_twenty_simultaneous_wrong_passwords_make_at_most_five_checks(self) -> None:
        self.user("Owner")
        db = self.open_db(pool_max=24)
        barrier = threading.Barrier(20)
        results: list[Any] = []
        lock = threading.Lock()

        def attempt(n: int) -> None:
            barrier.wait()
            result = login_module.login(
                db, "Owner", "wrong", client=ClientAddress.resolve(f"203.0.113.{n}"), hasher=self.hasher
            )
            with lock:
                results.append(result.outcome)

        threads = [threading.Thread(target=attempt, args=(n,)) for n in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(len(results), 20)
        self.assertLessEqual(self.hasher.verifies, 5)
        self.assertEqual(results.count(LoginOutcome.INVALID_CREDENTIALS), 5)
        self.assertEqual(results.count(LoginOutcome.THROTTLED), 15)
        self.assertEqual(self.attempts()[account_key("owner")], 20)


class LoginDeviceTests(IdentityTestCase):
    def first_device(self, name: str = "Owner") -> LoginDeviceToken:
        result = self.login(name)
        assert result.device_token is not None
        return result.device_token

    def test_a_known_device_signs_in_while_the_account_limit_is_exhausted(self) -> None:
        self.user("Owner")
        device = self.first_device()
        for _ in range(6):
            self.login(password="wrong", client=OTHER_CLIENT)
        self.assertIs(self.login(client=OTHER_CLIENT).outcome, LoginOutcome.THROTTLED)
        result = self.login(device_cookie=device.reveal())
        self.assertIs(result.outcome, LoginOutcome.SUCCEEDED)
        # The account-wide row is left for the campaign; only dev: was reset.
        self.assertEqual(self.attempts()[account_key("owner")], 7)

    def test_device_attempts_are_counted_only_under_the_device_key(self) -> None:
        self.user("Owner")
        device = self.first_device()
        for _ in range(5):
            outcome = self.login(password="wrong", device_cookie=device.reveal()).outcome
            self.assertIs(outcome, LoginOutcome.INVALID_CREDENTIALS)
        refused = self.login(device_cookie=device.reveal())
        self.assertIs(refused.outcome, LoginOutcome.THROTTLED)
        self.assertEqual(refused.throttled_kind, "dev")
        counts = self.attempts()
        self.assertEqual(counts[device_key(device.digest)], 6)
        self.assertNotIn(account_key("owner"), counts)
        self.assertEqual(counts.get(CLIENT.key), 1)  # only the first, device-less login
        # The account itself is not locked for other browsers.
        self.assertIs(self.login(client=OTHER_CLIENT).outcome, LoginOutcome.SUCCEEDED)

    def test_each_success_rotates_the_device_token(self) -> None:
        user_id = self.user("Owner")
        first = self.first_device()
        result = self.login(device_cookie=first.reveal())
        second = result.device_token
        assert second is not None
        self.assertNotEqual(first, second)
        stored = self.run_sql("SELECT token_sha256 FROM prks.login_device_tokens WHERE user_id = %s", (user_id,))
        self.assertEqual([bytes(row["token_sha256"]) for row in stored], [second.digest])
        # The replaced token no longer gates anything: it counts under acct:.
        self.login(password="wrong", device_cookie=first.reveal())
        self.assertEqual(self.attempts()[account_key("owner")], 1)

    def assert_refused_with_nothing_committed(self, result: Any) -> None:
        self.assertIs(result.outcome, LoginOutcome.DEVICE_REPLACED)
        self.assertEqual((result.public_reason, result.http_status), ("invalid_credentials", 401))
        self.assertIsNone(result.session)
        self.assertIsNone(result.device_token)

    def test_two_logins_presenting_one_device_token_consume_it_once(self) -> None:
        user_id = self.user("Owner")
        first = self.first_device()
        self.assertEqual(self.attempts(), {CLIENT.key: 1})
        db = self.open_db(pool_max=4)
        # Both requests pass the device check and the password check before
        # either commits, then race for the account row.
        both_checked = threading.Barrier(2, timeout=30)
        self.hasher.on_verify = both_checked.wait
        results: list[Any] = []

        def sign_in() -> None:
            results.append(login_module.login(
                db, "Owner", PASSWORD, client=CLIENT, device_cookie=first.reveal(), hasher=self.hasher
            ))

        threads = [threading.Thread(target=sign_in) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.hasher.on_verify = None

        self.assertEqual(sorted(r.outcome.value for r in results), ["device_replaced", "succeeded"])
        winner = next(r for r in results if r.outcome is LoginOutcome.SUCCEEDED)
        self.assert_refused_with_nothing_committed(next(r for r in results if r is not winner))
        assert winner.device_token is not None and winner.session is not None
        # One successor, one session: the refused request earned nothing.
        stored = self.run_sql("SELECT token_sha256 FROM prks.login_device_tokens WHERE user_id = %s", (user_id,))
        self.assertEqual([bytes(row["token_sha256"]) for row in stored], [winner.device_token.digest])
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.sessions"), [{"n": 2}])  # first_device + winner
        # Both were counted under dev: only (the winner's success reset it);
        # neither touched acct: or ip:.
        self.assertEqual(self.attempts(), {CLIENT.key: 1})

        # The consumed token no longer gates anything: presented again, the
        # login counts under acct: and ip: like any new browser.
        self.assertIs(self.login(device_cookie=first.reveal()).outcome, LoginOutcome.SUCCEEDED)
        self.assertEqual(self.attempts(), {CLIENT.key: 2})

    def test_a_device_token_revoked_during_the_check_earns_nothing(self) -> None:
        user_id = self.user("Owner")
        first = self.first_device()
        sessions_before = self.run_sql("SELECT count(*) AS n FROM prks.sessions")

        def revoke() -> None:
            self.hasher.on_verify = None
            self.db.command(lambda tx: login_module.revoke_device_tokens(tx, user_id))

        self.hasher.on_verify = revoke
        self.assert_refused_with_nothing_committed(self.login(device_cookie=first.reveal()))
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.sessions"), sessions_before)
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.login_device_tokens"), [{"n": 0}])
        # The device count stays (not reset); acct: was neither counted nor reset.
        self.assertEqual(self.attempts(), {CLIENT.key: 1, device_key(first.digest): 1})

        # Presented again, the revoked token is ignored and acct:/ip: apply.
        self.login(password="wrong", device_cookie=first.reveal())
        counts = self.attempts()
        self.assertEqual((counts[account_key("owner")], counts[CLIENT.key]), (1, 2))

    def test_stale_revoked_malformed_and_foreign_tokens_are_ignored(self) -> None:
        owner = self.user("Owner")
        self.user("Other")
        revoked = self.first_device()
        self.assertEqual(self.db.command(lambda tx: login_module.revoke_device_tokens(tx, owner)), 1)
        stale = self.first_device()
        self.run_sql(
            "UPDATE prks.login_device_tokens SET created_at = now() - interval '366 days' "
            "WHERE token_sha256 = %s",
            (stale.digest,),
        )
        foreign = self.first_device("Other")
        cookies = {
            "stale": stale.reveal(),
            "revoked": revoked.reveal(),
            "malformed": "not-a-token",
            "unknown": LoginDeviceToken.new().reveal(),
            "other account": foreign.reveal(),
        }
        for label, cookie in cookies.items():
            with self.subTest(label):
                self.run_sql("DELETE FROM prks.login_attempts")
                result = self.login(password="wrong", device_cookie=cookie)
                self.assertIs(result.outcome, LoginOutcome.INVALID_CREDENTIALS)
                self.assertEqual(self.attempts(), {account_key("owner"): 1, CLIENT.key: 1})

    def test_prune_removes_only_expired_device_tokens(self) -> None:
        self.user("Owner")
        old = self.first_device()
        fresh = self.first_device()
        self.run_sql(
            "UPDATE prks.login_device_tokens SET created_at = now() - interval '366 days' "
            "WHERE token_sha256 = %s",
            (old.digest,),
        )
        self.assertEqual(self.db.command(login_module.prune_device_tokens), 1)
        left = self.run_sql("SELECT token_sha256 FROM prks.login_device_tokens")
        self.assertEqual([bytes(row["token_sha256"]) for row in left], [fresh.digest])


class LoginFailureTests(IdentityTestCase):
    def test_a_failed_success_transaction_commits_nothing_and_a_retry_works(self) -> None:
        self.user("Owner")
        device = self.first_login_device()
        for _ in range(2):
            self.login(password="wrong", device_cookie=device.reveal())
        failure = DatabaseUnavailableError()
        with mock.patch.object(login_module, "create_session", side_effect=failure):
            result = self.login(device_cookie=device.reveal())
        self.assertIs(result.outcome, LoginOutcome.UNAVAILABLE)
        self.assertEqual((result.public_reason, result.http_status), ("unavailable", 503))
        self.assertIsNone(result.device_token)
        # Rolled back: the device token still works, its count was not reset
        # (the failed attempt itself was counted), and no session was added.
        self.assertEqual(self.attempts()[device_key(device.digest)], 3)
        self.assertEqual(self.run_sql("SELECT count(*) AS n FROM prks.sessions"), [{"n": 1}])
        retry = self.login(device_cookie=device.reveal())
        self.assertIs(retry.outcome, LoginOutcome.SUCCEEDED)
        self.assertNotIn(device_key(device.digest), self.attempts())

    def first_login_device(self) -> LoginDeviceToken:
        result = self.login()
        assert result.device_token is not None
        return result.device_token

    def test_an_unreachable_database_is_unavailable(self) -> None:
        with psycopg.connect(self.harness.conninfo(self.name)) as conn:
            port = conn.info.port
        db = Database(
            DatabaseConfig(
                conninfo=f"host=127.0.0.1 port={port + 1 if port < 65535 else 1} dbname=x connect_timeout=2",
                role="test",
                pool_min=0,
                pool_timeout_seconds=2,
            )
        )
        self.addCleanup(db.close)
        result = login_module.login(db, "Owner", PASSWORD, client=CLIENT, hasher=self.hasher)
        self.assertIs(result.outcome, LoginOutcome.UNAVAILABLE)
        self.assertEqual(self.hasher.verifies, 0)


class LoginSecrecyTests(IdentityTestCase):
    def test_no_password_or_token_is_stored_logged_or_printed(self) -> None:
        self.user("Owner")
        first = self.login()
        assert first.device_token is not None and first.session is not None
        second = self.login(device_cookie=first.device_token.reveal())
        assert second.device_token is not None and second.session is not None
        self.login(password="a wrong guess")
        secrets = [
            PASSWORD,
            "a wrong guess",
            first.device_token.reveal(),
            second.device_token.reveal(),
            first.session.token.reveal(),
            second.session.token.reveal(),
        ]
        stored = self.everything_stored()
        printed = "\n".join([repr(first), str(second), repr(second.session), self.logs.getvalue()])
        for secret in secrets:
            self.assertNotIn(secret, stored)
            self.assertNotIn(secret, printed)
        self.assertIn("<redacted>", repr(first))
