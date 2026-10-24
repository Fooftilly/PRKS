"""Argon2id password hashes (``docs/postgresql-architecture.md`` §9.3; L-ID-1 #626).

Parameters are argon2-cffi's defaults, the RFC 9106 low-memory profile
(Argon2id, time cost 3, 64 MiB, parallelism 4, 16-byte salt, 32-byte hash),
named here so a library upgrade cannot change them silently. One check
takes about 75 ms on a 4-core x86-64 cloud container (measured 2026-10-14
for #626); re-measure on the target hardware before changing them. A stored hash
records its own parameters, so a later change only needs
:func:`needs_rehash` at the next successful login.

Plaintext passwords exist only as call arguments: nothing here logs, stores
or repeats one, and every refusal carries fixed text.
"""

from __future__ import annotations

import re
from typing import Optional, TypeGuard

from argon2 import PasswordHasher, Type, exceptions, extract_parameters, profiles

from backend.domain.identity.errors import RootPairingRefused

# SECURITY PREREQUISITE before changing PARAMETERS (#677 review, accepted
# deferral): a stored hash keeps the parameters it was made with until its
# next successful login, while a failed login for an unknown account costs
# what the *current* parameters cost (login.dummy_hash). After a change, a
# wrong password against a not-yet-rehashed account would therefore take a
# different time than one against a missing account, revealing that the
# account exists. Rehash-on-success alone does not close that gap. Changing
# these values needs a separate security review and tests covering
# failed-login timing for old and new hashes. See docs/login-security.md.
PARAMETERS = profiles.RFC_9106_LOW_MEMORY

# Bounds the work an attacker can make one request do; longer passwords add
# nothing to a 256-bit hash.
MAX_PASSWORD_BYTES = 1024

# The PHC string argon2-cffi writes: version 19, numeric parameters, then
# unpadded standard-base64 salt (at least 8 bytes) and hash (at least 4).
_HASH_RE = re.compile(
    r"^\$argon2id\$v=19\$m=\d{1,10},t=\d{1,10},p=\d{1,3}"
    r"\$[A-Za-z0-9+/]{11,}\$[A-Za-z0-9+/]{6,}$"
)

_DEFAULT_HASHER: Optional[PasswordHasher] = None


def default_hasher() -> PasswordHasher:
    global _DEFAULT_HASHER
    if _DEFAULT_HASHER is None:
        _DEFAULT_HASHER = PasswordHasher.from_parameters(PARAMETERS)
    return _DEFAULT_HASHER


class PasswordHashInvalid(RootPairingRefused):
    """A stored hash is not an Argon2id hash PRKS can verify."""

    def __init__(self) -> None:
        super().__init__("password_hash_invalid", "The stored password hash is not a valid Argon2id hash.")


def _check_password(password: str) -> bytes:
    if not isinstance(password, str) or password == "":
        raise RootPairingRefused("password_empty", "A password is required.")
    if "\x00" in password:
        raise RootPairingRefused("password_invalid", "A password cannot contain a NUL character.")
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate (e.g. undecodable bytes in a password file)
        raise RootPairingRefused("password_invalid", "A password must be valid Unicode text.") from None
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise RootPairingRefused(
            "password_too_long", f"A password is at most {MAX_PASSWORD_BYTES} bytes."
        )
    return encoded


def usable_password(password: object) -> TypeGuard[str]:
    """True for a password :func:`hash_password` accepts; login refuses any other at once."""
    if not isinstance(password, str):
        return False
    try:
        _check_password(password)
    except RootPairingRefused:
        return False
    return True


def is_argon2id_hash(value: object) -> bool:
    """True for an encoded Argon2id hash whose parameters parse."""
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        return False
    try:
        return extract_parameters(value).type is Type.ID
    except exceptions.InvalidHashError:
        return False


def hash_password(password: str, *, hasher: Optional[PasswordHasher] = None) -> str:
    """The encoded Argon2id hash of ``password`` with a fresh random salt."""
    encoded = _check_password(password)
    return (hasher or default_hasher()).hash(encoded)


def verify_password(
    stored_hash: str, password: str, *, hasher: Optional[PasswordHasher] = None
) -> bool:
    """True when ``password`` matches ``stored_hash``; False for any other password.

    A stored value that is not an Argon2id hash raises :class:`PasswordHashInvalid`
    (an Argon2i or Argon2d hash too: PRKS never wrote one), so a corrupt row is
    never mistaken for a wrong password.
    """
    if not is_argon2id_hash(stored_hash):
        raise PasswordHashInvalid()
    try:
        encoded = _check_password(password)
    except RootPairingRefused:
        return False
    try:
        return (hasher or default_hasher()).verify(stored_hash, encoded)
    except exceptions.VerifyMismatchError:
        return False
    except (exceptions.InvalidHashError, exceptions.VerificationError):
        # Only VerifyMismatchError means a wrong password; anything else (bad
        # parameters such as a too-small memory cost) is a corrupt stored hash.
        raise PasswordHashInvalid() from None


def needs_rehash(stored_hash: str, *, hasher: Optional[PasswordHasher] = None) -> bool:
    """True when ``stored_hash`` was made with parameters other than the current ones."""
    if not is_argon2id_hash(stored_hash):
        raise PasswordHashInvalid()
    return (hasher or default_hasher()).check_needs_rehash(stored_hash)
