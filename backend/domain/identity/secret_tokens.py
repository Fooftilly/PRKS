"""Session and login-device tokens (``docs/postgresql-architecture.md`` §9.3; L-ID-1 #626).

Both are 256 random bits sent to the browser in a cookie, and the database
keeps only their SHA-256. A token object never prints its value: ``repr``
and ``str`` are fixed text, it compares in constant time, and it refuses to
be pickled. The value leaves the process only through :meth:`SecretToken.reveal`,
which the HTTP layer (:mod:`backend.api.session`) calls to set the cookie.

Cookie contracts the HTTP layer keeps (§9.3):

- session: ``prks_session``, ``HttpOnly``, ``SameSite=Strict``, ``Path=/``,
  ``Secure`` whenever the public origin is HTTPS;
- login device: ``prks_login_device``, ``HttpOnly``, ``SameSite=Strict``,
  ``Path=/api/login``, ``Secure`` on HTTPS, one year.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Any, Optional, TypeVar

TOKEN_BYTES = 32

SESSION_COOKIE = "prks_session"
SESSION_COOKIE_PATH = "/"
LOGIN_DEVICE_COOKIE = "prks_login_device"
LOGIN_DEVICE_COOKIE_PATH = "/api/login"

# secrets.token_urlsafe(32) is exactly 43 characters of this alphabet.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")

T = TypeVar("T", bound="SecretToken")


class SecretToken:
    """A 256-bit token held in memory; compares and prints without its value."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
            raise ValueError("malformed token")
        self._value = value

    @classmethod
    def new(cls: type[T]) -> T:
        return cls(secrets.token_urlsafe(TOKEN_BYTES))

    @classmethod
    def parse(cls: type[T], text: object) -> Optional[T]:
        """The token in a cookie value, or ``None`` when it is not one PRKS issued."""
        if not isinstance(text, str) or _TOKEN_RE.fullmatch(text) is None:
            return None
        return cls(text)

    def reveal(self) -> str:
        """The token itself, for the one ``Set-Cookie`` that delivers it."""
        return self._value

    @property
    def digest(self) -> bytes:
        return hashlib.sha256(self._value.encode("ascii")).digest()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretToken) or type(other) is not type(self):
            return NotImplemented
        return hmac.compare_digest(self.digest, other.digest)

    def __hash__(self) -> int:
        return hash(self.digest)

    def __reduce__(self) -> Any:  # never pickled into a queue or a cache
        raise TypeError("a secret token is not serializable")


class SessionToken(SecretToken):
    """The ``prks_session`` cookie value."""

    __slots__ = ()


class LoginDeviceToken(SecretToken):
    """The ``prks_login_device`` cookie value."""

    __slots__ = ()
