"""The login attempt limiter (§9.3; L-ID-1 #626, PR 3/5): settings, client address, counting."""

from __future__ import annotations

import threading
import unittest
from datetime import timedelta

from backend.domain.identity import login_limiter
from backend.domain.identity.login_limiter import (
    ClientAddress,
    LimiterConfigError,
    LoginLimits,
    TrustedProxies,
)
from tests.pg.identity_fixtures import IdentityTestCase


class LimitsTests(unittest.TestCase):
    def test_defaults_are_the_design_values(self) -> None:
        limits = LoginLimits()
        self.assertEqual(
            (limits.window, limits.account, limits.address, limits.device),
            (timedelta(minutes=15), 5, 20, 5),
        )
        self.assertEqual(LoginLimits.from_env({}), limits)

    def test_environment_overrides(self) -> None:
        limits = LoginLimits.from_env(
            {
                "PRKS_LOGIN_WINDOW_SECONDS": "600",
                "PRKS_LOGIN_ACCOUNT_LIMIT": "3",
                "PRKS_LOGIN_ADDRESS_LIMIT": " 50 ",
                "PRKS_LOGIN_DEVICE_LIMIT": "7",
            }
        )
        self.assertEqual(
            (limits.window, limits.account, limits.address, limits.device),
            (timedelta(minutes=10), 3, 50, 7),
        )

    def test_unusable_settings_refuse_to_start(self) -> None:
        for env in (
            {"PRKS_LOGIN_WINDOW_SECONDS": "0"},
            {"PRKS_LOGIN_WINDOW_SECONDS": "86401"},
            {"PRKS_LOGIN_WINDOW_SECONDS": "fifteen"},
            {"PRKS_LOGIN_ACCOUNT_LIMIT": "0"},
            {"PRKS_LOGIN_ADDRESS_LIMIT": "-1"},
            {"PRKS_LOGIN_DEVICE_LIMIT": "10001"},
            {"PRKS_LOGIN_DEVICE_LIMIT": "2.5"},
            {"PRKS_TRUSTED_PROXIES": "10.0.0.1, not-an-address"},
        ):
            with self.subTest(env=env):
                with self.assertRaises(LimiterConfigError):
                    LoginLimits.from_env(env)
                    TrustedProxies.from_env(env)


class ClientAddressTests(unittest.TestCase):
    PROXIES = TrustedProxies.parse("10.0.0.0/8, 2001:db8:ffff::1")

    def key(self, peer: str, forwarded: object = None) -> str:
        return ClientAddress.resolve(peer, forwarded, trusted=self.PROXIES).key  # type: ignore[arg-type]

    def test_ipv4_is_keyed_whole_and_ipv6_by_its_64(self) -> None:
        self.assertEqual(self.key("192.0.2.1"), "ip:192.0.2.1")
        self.assertNotEqual(self.key("192.0.2.1"), self.key("192.0.2.2"))
        self.assertEqual(self.key("2001:db8:1:2::1"), "ip:2001:db8:1:2::/64")
        self.assertEqual(self.key("2001:db8:1:2:aaaa:bbbb:cccc:dddd"), "ip:2001:db8:1:2::/64")
        self.assertNotEqual(self.key("2001:db8:1:2::1"), self.key("2001:db8:1:3::1"))
        self.assertEqual(self.key("[2001:db8:1:2::1]"), "ip:2001:db8:1:2::/64")
        self.assertEqual(self.key("fe80::1%eth0"), "ip:fe80::/64")
        self.assertEqual(self.key("::ffff:192.0.2.9"), "ip:192.0.2.9")

    def test_an_untrusted_peer_cannot_choose_its_address(self) -> None:
        self.assertEqual(self.key("192.0.2.1", "198.51.100.1"), "ip:192.0.2.1")
        self.assertEqual(
            ClientAddress.resolve("10.0.0.5", "198.51.100.1").key, "ip:10.0.0.5"
        )  # no proxies configured

    def test_a_trusted_proxy_chain_is_walked_from_the_right(self) -> None:
        self.assertEqual(self.key("10.0.0.5", "198.51.100.1"), "ip:198.51.100.1")
        # A client-supplied entry left of the real client is ignored.
        self.assertEqual(self.key("10.0.0.5", "203.0.113.66, 198.51.100.1, 10.1.1.1"), "ip:198.51.100.1")
        self.assertEqual(self.key("10.0.0.5", ["203.0.113.66", "198.51.100.1, 10.1.1.1"]), "ip:198.51.100.1")
        self.assertEqual(self.key("2001:db8:ffff::1", "2001:db8:9:9::5"), "ip:2001:db8:9:9::/64")

    def test_malformed_forwarded_entries_stop_at_the_last_trusted_hop(self) -> None:
        self.assertEqual(self.key("10.0.0.5", "garbage"), "ip:10.0.0.5")
        self.assertEqual(self.key("10.0.0.5", "198.51.100.1, garbage, 10.2.2.2"), "ip:10.2.2.2")
        self.assertEqual(self.key("10.0.0.5", ""), "ip:10.0.0.5")
        self.assertEqual(self.key("10.0.0.5", "10.3.3.3, 10.2.2.2"), "ip:10.3.3.3")

    def test_the_peer_must_be_an_address(self) -> None:
        with self.assertRaises(ValueError):
            ClientAddress.resolve("unix-socket")


class CountingTests(IdentityTestCase):
    def test_counts_rise_then_refuse_with_retry_after(self) -> None:
        limits = LoginLimits(account=2)
        keys = ["acct:someone"]
        self.assertIsNone(login_limiter.count_attempt(self.db, keys, limits))
        self.assertIsNone(login_limiter.count_attempt(self.db, keys, limits))
        throttled = login_limiter.count_attempt(self.db, keys, limits)
        assert throttled is not None
        self.assertEqual(throttled.kind, "acct")
        self.assertTrue(895 <= throttled.retry_after_seconds <= 900)

    def test_every_key_is_counted_even_when_one_is_over(self) -> None:
        limits = LoginLimits(account=1)
        login_limiter.count_attempt(self.db, ["acct:a", "ip:192.0.2.1"], limits)
        login_limiter.count_attempt(self.db, ["acct:a", "ip:192.0.2.1"], limits)
        self.assertEqual(self.attempts(), {"acct:a": 2, "ip:192.0.2.1": 2})

    def test_the_longest_wait_is_reported(self) -> None:
        limits = LoginLimits(account=1, address=1)
        keys = ["acct:a", "ip:192.0.2.1"]
        login_limiter.count_attempt(self.db, keys, limits)
        self.run_sql(
            "UPDATE prks.login_attempts SET window_start = now() - interval '10 minutes' WHERE key = 'acct:a'"
        )
        throttled = login_limiter.count_attempt(self.db, keys, limits)
        assert throttled is not None
        self.assertEqual(throttled.kind, "ip")

    def test_address_keys_are_never_reset(self) -> None:
        with self.assertRaises(ValueError):
            self.db.command(lambda tx: login_limiter.reset(tx, "ip:192.0.2.1"))

    def test_concurrent_counts_are_serialized(self) -> None:
        db = self.open_db(pool_max=16)
        barrier = threading.Barrier(16)
        seen: list[bool] = []
        lock = threading.Lock()

        def count() -> None:
            barrier.wait()
            refused = login_limiter.count_attempt(db, ["acct:race"], LoginLimits()) is not None
            with lock:
                seen.append(refused)

        threads = [threading.Thread(target=count) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(seen.count(False), 5)
        self.assertEqual(self.attempts(), {"acct:race": 16})

    def test_prune_deletes_only_ended_windows(self) -> None:
        login_limiter.count_attempt(self.db, ["acct:old", "acct:new"], LoginLimits())
        self.run_sql(
            "UPDATE prks.login_attempts SET window_start = now() - interval '16 minutes' WHERE key = 'acct:old'"
        )
        self.assertEqual(self.db.command(lambda tx: login_limiter.prune_windows(tx, LoginLimits())), 1)
        self.assertEqual(self.attempts(), {"acct:new": 1})

    def test_the_schema_refuses_keys_of_another_kind(self) -> None:
        from backend.db.errors import DatabaseError

        with self.assertRaises(DatabaseError):
            login_limiter.count_attempt(self.db, ["user:x"], LoginLimits())
