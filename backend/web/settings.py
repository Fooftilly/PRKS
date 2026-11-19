"""HTTP transport settings, read once at startup (#1329).

``PRKS_HTTP_SERVER`` selects the transport while both exist: ``stdlib`` (the
default until the switch) or ``uvicorn``. ``PRKS_HTTP_THREADS`` bounds the
threads that run PRKS request work under Uvicorn. Invalid values refuse to
start; nothing falls back silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

ENV_SERVER = "PRKS_HTTP_SERVER"
ENV_THREADS = "PRKS_HTTP_THREADS"

TRANSPORT_STDLIB = "stdlib"
TRANSPORT_UVICORN = "uvicorn"
TRANSPORTS = (TRANSPORT_STDLIB, TRANSPORT_UVICORN)

DEFAULT_THREADS = 16
MIN_THREADS = 1
MAX_THREADS = 256


class HttpSettingsError(ValueError):
    """An invalid HTTP transport setting; ``message`` names the variable and the fix."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class HttpServerSettings:
    transport: str = TRANSPORT_STDLIB
    threads: int = DEFAULT_THREADS

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "HttpServerSettings":
        raw_transport = env.get(ENV_SERVER, "").strip().lower()
        transport = raw_transport or TRANSPORT_STDLIB
        if transport not in TRANSPORTS:
            raise HttpSettingsError(
                f"{ENV_SERVER} must be one of {', '.join(TRANSPORTS)}."
            )
        raw_threads = env.get(ENV_THREADS, "").strip()
        threads = DEFAULT_THREADS
        if raw_threads:
            if not raw_threads.isdigit() or len(raw_threads) > 4:
                raise HttpSettingsError(
                    f"{ENV_THREADS} must be a whole number from {MIN_THREADS} to {MAX_THREADS}."
                )
            threads = int(raw_threads)
            if not MIN_THREADS <= threads <= MAX_THREADS:
                raise HttpSettingsError(
                    f"{ENV_THREADS} must be a whole number from {MIN_THREADS} to {MAX_THREADS}."
                )
        return cls(transport=transport, threads=threads)
