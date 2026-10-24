"""The login attempt limiter (``docs/postgresql-architecture.md`` §9.3; L-ID-1 #626).

Attempts are counted in ``prks.login_attempts`` under three kinds of key:

- ``acct:<login_key>``: every attempt on an account from a browser that is not
  a known login device for it, from any address (default 5 per window);
- ``ip:<address>``: the same attempts by client address (default 20 per
  window); an IPv4 address is keyed whole, an IPv6 address by its ``/64``;
- ``dev:<token hash>``: attempts that present a valid login-device token for
  the account they name (default 5 per window). They are counted only there,
  so a guessing campaign against the account never locks out a browser the
  owner has signed in from before (the OWASP device-cookie pattern).

Each key is counted by one ``INSERT … ON CONFLICT DO UPDATE`` committed on its
own **before** any password check. The upsert takes the row lock, so
concurrent attempts are counted one after another and each sees the count
the one before it committed: twenty simultaneous attempts get the counts 1
to 20, and only those within the limit go on to a password check. A window
is fixed by its first attempt and starts over at 1 once it is older than the
window length; refused attempts are counted too, so hammering never shortens
the wait. Only a successful password check resets a row, and only the row
that gated the request (``acct:`` or ``dev:``, never ``ip:``).

The client address is the connection peer. A forwarded address
(``X-Forwarded-For``) is used only when the peer is listed in
``PRKS_TRUSTED_PROXIES``, walking the header from the right past trusted
hops, so a client cannot choose its own address key.
"""

from __future__ import annotations

import ipaddress
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Union

from backend.db.database import Database, Transaction

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

TRUSTED_PROXIES_ENV = "PRKS_TRUSTED_PROXIES"
WINDOW_ENV = "PRKS_LOGIN_WINDOW_SECONDS"
ACCOUNT_LIMIT_ENV = "PRKS_LOGIN_ACCOUNT_LIMIT"
ADDRESS_LIMIT_ENV = "PRKS_LOGIN_ADDRESS_LIMIT"
DEVICE_LIMIT_ENV = "PRKS_LOGIN_DEVICE_LIMIT"

ACCOUNT = "acct"
ADDRESS = "ip"
DEVICE = "dev"

# The IPv6 prefix one client is assumed to control (a /64 is one subnet).
IPV6_KEY_PREFIX = 64


class LimiterConfigError(ValueError):
    """A limiter setting in the environment is not usable; the process does not start."""


@dataclass(frozen=True)
class LoginLimits:
    """Window length and per-key attempt limits (§9.3 defaults)."""

    window: timedelta = timedelta(minutes=15)
    account: int = 5
    address: int = 20
    device: int = 5

    def __post_init__(self) -> None:
        if not timedelta(seconds=1) <= self.window <= timedelta(days=1):
            raise LimiterConfigError("The login window must be from 1 second to 1 day.")
        for name in ("account", "address", "device"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 10_000:
                raise LimiterConfigError(f"The {name} login limit must be from 1 to 10000.")

    def limit(self, kind: str) -> int:
        return {ACCOUNT: self.account, ADDRESS: self.address, DEVICE: self.device}[kind]

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> LoginLimits:
        defaults = cls()
        return cls(
            window=timedelta(
                seconds=_env_int(environ, WINDOW_ENV, int(defaults.window.total_seconds()))
            ),
            account=_env_int(environ, ACCOUNT_LIMIT_ENV, defaults.account),
            address=_env_int(environ, ADDRESS_LIMIT_ENV, defaults.address),
            device=_env_int(environ, DEVICE_LIMIT_ENV, defaults.device),
        )


def _env_int(environ: Mapping[str, str], name: str, default: int) -> int:
    text = (environ.get(name) or "").strip()
    if not text:
        return default
    try:
        return int(text, 10)
    except ValueError:
        raise LimiterConfigError(f"{name} must be a whole number.") from None


# --- client address --------------------------------------------------------------------


@dataclass(frozen=True)
class TrustedProxies:
    """The proxies whose ``X-Forwarded-For`` is believed (``PRKS_TRUSTED_PROXIES``)."""

    networks: tuple[IPNetwork, ...] = ()

    @classmethod
    def parse(cls, text: Optional[str]) -> TrustedProxies:
        """Comma- or space-separated addresses and CIDR networks; empty trusts nobody."""
        networks: list[IPNetwork] = []
        for part in (text or "").replace(",", " ").split():
            try:
                networks.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                raise LimiterConfigError(
                    f"{TRUSTED_PROXIES_ENV} lists something that is not an address or network."
                ) from None
        return cls(tuple(networks))

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> TrustedProxies:
        return cls.parse(environ.get(TRUSTED_PROXIES_ENV))

    def trusts(self, address: IPAddress) -> bool:
        return any(address.version == net.version and address in net for net in self.networks)


def _parse_address(text: object) -> Optional[IPAddress]:
    if not isinstance(text, str):
        return None
    candidate = text.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    candidate = candidate.split("%", 1)[0]  # an IPv6 zone names a local interface
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


@dataclass(frozen=True)
class ClientAddress:
    """The address a login is counted under, resolved from the real connection."""

    address: IPAddress

    @classmethod
    def resolve(
        cls,
        peer: str,
        forwarded_for: Union[str, Sequence[str], None] = None,
        *,
        trusted: Optional[TrustedProxies] = None,
    ) -> ClientAddress:
        """Resolve the client from the TCP ``peer`` and any ``X-Forwarded-For`` values.

        ``forwarded_for`` is every ``X-Forwarded-For`` header in order (or one
        joined string). It is read only when ``peer`` is a trusted proxy: from
        the right, each trusted hop is skipped and the first address that is
        not trusted is the client. A malformed entry stops the walk at the
        trusted hop that reported it, and a chain of only trusted hops ends at
        its leftmost address.
        """
        peer_address = _parse_address(peer)
        if peer_address is None:
            raise ValueError("the connection peer is not an IP address")
        if forwarded_for is None or trusted is None or not trusted.trusts(peer_address):
            return cls(peer_address)
        values = [forwarded_for] if isinstance(forwarded_for, str) else list(forwarded_for)
        hops = [hop for value in values for hop in value.split(",") if hop.strip()]
        client = peer_address
        for hop in reversed(hops):
            hop_address = _parse_address(hop)
            if hop_address is None:
                break
            client = hop_address
            if not trusted.trusts(hop_address):
                break
        return cls(client)

    @property
    def key(self) -> str:
        """``ip:<IPv4 address>`` or ``ip:<IPv6 /64 network>``."""
        if isinstance(self.address, ipaddress.IPv6Address):
            network = ipaddress.IPv6Network((self.address, IPV6_KEY_PREFIX), strict=False)
            return f"{ADDRESS}:{network.compressed}"
        return f"{ADDRESS}:{self.address.compressed}"


def account_key(login_key: str) -> str:
    return f"{ACCOUNT}:{login_key}"


def device_key(token_sha256: bytes) -> str:
    return f"{DEVICE}:{token_sha256.hex()}"


def _kind(key: str) -> str:
    return key.split(":", 1)[0]


# --- counting --------------------------------------------------------------------------


@dataclass(frozen=True)
class Throttled:
    """A key over its limit: refuse without a password check (HTTP 429)."""

    kind: str  # "acct", "ip" or "dev"
    retry_after_seconds: int  # whole seconds until the window ends, at least 1


_COUNT_SQL = """
INSERT INTO prks.login_attempts AS a (key, attempts, window_start)
VALUES (%(key)s, 1, now())
ON CONFLICT (key) DO UPDATE SET
  attempts = CASE WHEN a.window_start <= now() - %(window)s THEN 1 ELSE a.attempts + 1 END,
  window_start = CASE WHEN a.window_start <= now() - %(window)s THEN now() ELSE a.window_start END
RETURNING attempts, EXTRACT(EPOCH FROM (window_start + %(window)s - now()))::float8 AS remaining
"""


def count_attempt(db: Database, keys: Iterable[str], limits: LoginLimits) -> Optional[Throttled]:
    """Count one attempt under every key, each in its own committed upsert.

    Returns the key over its limit that waits longest, or ``None`` when every
    key is within its limit and the password may be checked. Every key is
    counted even when an earlier one is already over, so the address key
    sees the whole campaign.
    """
    worst: Optional[Throttled] = None
    for key in keys:
        attempts, remaining = db.command(lambda tx, key=key: _count_one(tx, key, limits.window))
        kind = _kind(key)
        if attempts > limits.limit(kind):
            wait = max(1, math.ceil(remaining))
            if worst is None or wait > worst.retry_after_seconds:
                worst = Throttled(kind, wait)
    return worst


def _count_one(tx: Transaction, key: str, window: timedelta) -> tuple[int, float]:
    row = tx.execute(_COUNT_SQL, {"key": key, "window": window}).fetchone()
    if row is None:  # pragma: no cover - INSERT … RETURNING always returns the row
        raise RuntimeError("login attempt upsert returned no row")
    return int(row["attempts"]), float(row["remaining"])


def reset(tx: Transaction, key: str) -> None:
    """Forget the attempts under ``key`` (after a successful password check)."""
    if _kind(key) == ADDRESS:
        raise ValueError("an address key is never reset by a login")
    tx.execute("DELETE FROM prks.login_attempts WHERE key = %s", (key,))


def unlock_account(db: Database, login_name: str) -> Optional[bool]:
    """Delete the ``acct:`` row of the account named ``login_name`` (``prks admin unlock-login``).

    Returns ``None`` when no account has that login, otherwise whether a
    limiter row was deleted. Device and address rows are left alone.
    """

    def run(tx: Transaction) -> Optional[bool]:
        row = tx.execute(
            "SELECT login_key FROM prks.users WHERE login_key = prks.identity_key(%s)",
            (login_name,),
        ).fetchone()
        if row is None:
            return None
        deleted = tx.execute(
            "DELETE FROM prks.login_attempts WHERE key = %s RETURNING key",
            (account_key(row["login_key"]),),
        ).fetchone()
        return deleted is not None

    return db.command(run)


def prune_windows(tx: Transaction, limits: LoginLimits) -> int:
    """Delete limiter rows whose window has ended (the ``sessions.prune`` job)."""
    cursor = tx.execute(
        "DELETE FROM prks.login_attempts WHERE window_start <= now() - %s", (limits.window,)
    )
    return cursor.rowcount
