"""Argon2id password hashes (§9.3; L-ID-1 #626).

Lives beside the PostgreSQL tests because argon2-cffi is installed with the
psycopg pins (requirements-dev.txt) until the identity lane runs in the app.
"""

from __future__ import annotations

import unittest

from argon2 import PasswordHasher, Type, extract_parameters

from backend.domain.identity import passwords
from backend.domain.identity.errors import RootPairingRefused
from backend.domain.identity.root_pairing import OwnerSpec

# Tiny parameters keep the suite fast; one test checks the real ones.
FAST = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


class PasswordHashTests(unittest.TestCase):
    def test_default_parameters_are_the_rfc_9106_low_memory_profile(self) -> None:
        stored = passwords.hash_password("correct horse")
        params = extract_parameters(stored)
        self.assertEqual(params.type, Type.ID)
        self.assertEqual(
            (params.time_cost, params.memory_cost, params.parallelism, params.hash_len, params.salt_len),
            (3, 65536, 4, 32, 16),
        )
        self.assertTrue(passwords.verify_password(stored, "correct horse"))
        self.assertFalse(passwords.needs_rehash(stored))

    def test_verify_accepts_only_the_password(self) -> None:
        stored = passwords.hash_password("Tr0ub4dor&3 ünïcode", hasher=FAST)
        self.assertTrue(passwords.verify_password(stored, "Tr0ub4dor&3 ünïcode", hasher=FAST))
        for wrong in ("tr0ub4dor&3 ünïcode", "Tr0ub4dor&3 ünïcode ", "", "x" * 2000):
            with self.subTest(length=len(wrong)):
                self.assertFalse(passwords.verify_password(stored, wrong, hasher=FAST))

    def test_each_hash_has_its_own_salt_and_no_plaintext(self) -> None:
        first = passwords.hash_password("same password", hasher=FAST)
        second = passwords.hash_password("same password", hasher=FAST)
        self.assertNotEqual(first, second)
        self.assertNotIn("same password", first)

    def test_malformed_and_foreign_hashes_raise_instead_of_failing_quietly(self) -> None:
        argon2i = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, type=Type.I).hash("p")
        for stored in (
            "",
            "plaintext",
            "$argon2id$",
            "$argon2id$v=19$m=8,t=1,p=1$!!!$!!!",
            argon2i,
            "$2b$12$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ01234",
        ):
            with self.subTest(stored=stored[:14]):
                with self.assertRaises(passwords.PasswordHashInvalid) as ctx:
                    passwords.verify_password(stored, "p", hasher=FAST)
                self.assertEqual(ctx.exception.reason, "password_hash_invalid")
                self.assertFalse(passwords.is_argon2id_hash(stored))

    def test_a_well_formed_hash_with_impossible_parameters_is_invalid_not_a_mismatch(self) -> None:
        stored = passwords.hash_password("p", hasher=FAST).replace("$m=8,", "$m=1,")
        self.assertTrue(passwords.is_argon2id_hash(stored))
        with self.assertRaises(passwords.PasswordHashInvalid):
            passwords.verify_password(stored, "p", hasher=FAST)

    def test_a_truncated_hash_does_not_verify(self) -> None:
        stored = passwords.hash_password("p", hasher=FAST)
        cut = stored[:-6]
        # A corrupt hash raises; it never verifies.
        with self.assertRaises(passwords.PasswordHashInvalid):
            passwords.verify_password(cut, "p", hasher=FAST)

    def test_empty_and_oversized_passwords_are_refused_without_echo(self) -> None:
        with self.assertRaises(RootPairingRefused) as ctx:
            passwords.hash_password("")
        self.assertEqual(ctx.exception.reason, "password_empty")
        long = "s3cr3t" * 200
        with self.assertRaises(RootPairingRefused) as ctx:
            passwords.hash_password(long)
        self.assertEqual(ctx.exception.reason, "password_too_long")
        self.assertNotIn("s3cr3t", ctx.exception.message)

    def test_passwords_login_would_refuse_are_refused_at_creation(self) -> None:
        for label, password in (("NUL", "pass\x00word"), ("lone surrogate", "pass\udcff")):
            with self.subTest(label):
                with self.assertRaises(RootPairingRefused) as ctx:
                    passwords.hash_password(password, hasher=FAST)
                self.assertEqual(ctx.exception.reason, "password_invalid")
                self.assertFalse(passwords.usable_password(password))
        for password in ("p", "ünïcode pass", "x" * 1024):
            self.assertTrue(passwords.usable_password(password))
        for password in ("", "x" * 1025, None, b"bytes"):
            self.assertFalse(passwords.usable_password(password))

    def test_needs_rehash_follows_the_parameters(self) -> None:
        weak = passwords.hash_password("p", hasher=FAST)
        self.assertTrue(passwords.needs_rehash(weak))
        self.assertFalse(passwords.needs_rehash(weak, hasher=FAST))

    def test_owner_spec_never_prints_its_hash(self) -> None:
        stored = passwords.hash_password("p", hasher=FAST)
        owner = OwnerSpec("owner", "Owner", "Library", stored)
        self.assertNotIn(stored, repr(owner))
        self.assertNotIn(stored, str(owner))


if __name__ == "__main__":
    unittest.main()
