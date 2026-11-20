"""HTTP server settings, read once at startup (#1329).

PRKS serves HTTP with Uvicorn only. ``PRKS_HTTP_THREADS`` bounds the threads
that run PRKS request work. ``PRKS_TLS_CERT_FILE`` and ``PRKS_TLS_KEY_FILE``
(set together) make the server speak HTTPS itself (#1339); the pair is loaded
once here, so an unusable pair refuses to start, and a renewed certificate
takes effect only after a restart. Invalid values refuse to start; nothing
falls back silently. Errors name the variable, never a path or file contents.
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from typing import Mapping, Optional

ENV_THREADS = "PRKS_HTTP_THREADS"
ENV_TLS_CERT_FILE = "PRKS_TLS_CERT_FILE"
ENV_TLS_KEY_FILE = "PRKS_TLS_KEY_FILE"

DEFAULT_THREADS = 16
MIN_THREADS = 1
MAX_THREADS = 256


class HttpSettingsError(ValueError):
    """An invalid HTTP server setting; ``message`` names the variable and the fix."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _readable(variable: str, path: str) -> None:
    # A regular file only: opening a FIFO (or a device) could block startup.
    if not os.path.isfile(path):
        raise HttpSettingsError(f"{variable} names a file PRKS cannot read.")
    try:
        with open(path, "rb"):
            pass
    except OSError:
        raise HttpSettingsError(f"{variable} names a file PRKS cannot read.") from None


def _no_passphrase() -> str:
    # Asked only for an encrypted key (even one with an empty passphrase).
    # Uvicorn loads the key with no password, so OpenSSL would prompt on the
    # terminal at startup: refuse it here instead.
    raise ValueError("encrypted private key")


def _tls_files(env: Mapping[str, str]) -> tuple[Optional[str], Optional[str]]:
    cert = env.get(ENV_TLS_CERT_FILE, "").strip()
    key = env.get(ENV_TLS_KEY_FILE, "").strip()
    if not cert and not key:
        return None, None
    if not cert or not key:
        missing = ENV_TLS_CERT_FILE if not cert else ENV_TLS_KEY_FILE
        raise HttpSettingsError(
            f"{ENV_TLS_CERT_FILE} and {ENV_TLS_KEY_FILE} must be set together; {missing} is not set."
        )
    _readable(ENV_TLS_CERT_FILE, cert)
    _readable(ENV_TLS_KEY_FILE, key)
    try:
        ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(
            cert, key, password=_no_passphrase
        )
    except (ssl.SSLError, OSError, ValueError):
        raise HttpSettingsError(
            f"{ENV_TLS_CERT_FILE} and {ENV_TLS_KEY_FILE} are not a matching PEM certificate "
            "and unencrypted private key."
        ) from None
    return cert, key


@dataclass(frozen=True)
class HttpServerSettings:
    threads: int = DEFAULT_THREADS
    # PEM paths; both set or both None (``from_env`` checks they load).
    tls_cert_file: Optional[str] = None
    tls_key_file: Optional[str] = None

    @property
    def tls(self) -> bool:
        return self.tls_cert_file is not None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "HttpServerSettings":
        raw_threads = env.get(ENV_THREADS, "").strip()
        threads = DEFAULT_THREADS
        if raw_threads:
            if not (raw_threads.isascii() and raw_threads.isdigit()) or len(raw_threads) > 4:
                raise HttpSettingsError(
                    f"{ENV_THREADS} must be a whole number from {MIN_THREADS} to {MAX_THREADS}."
                )
            threads = int(raw_threads)
            if not MIN_THREADS <= threads <= MAX_THREADS:
                raise HttpSettingsError(
                    f"{ENV_THREADS} must be a whole number from {MIN_THREADS} to {MAX_THREADS}."
                )
        cert, key = _tls_files(env)
        return cls(threads=threads, tls_cert_file=cert, tls_key_file=key)
