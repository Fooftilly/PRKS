"""HTTP server settings, read once at startup (#1329).

PRKS serves HTTP with Uvicorn only. ``PRKS_HTTP_THREADS`` bounds the threads
that run PRKS request work. ``PRKS_HTTP_SERVER`` chose between Uvicorn and the
standard-library server while both existed; #1329 Phase B3 removed the
standard-library server, so a non-empty value now refuses to start rather
than being ignored. Invalid values refuse to start; nothing falls back
silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

ENV_THREADS = "PRKS_HTTP_THREADS"
#: Retired by #1329 Phase B3; read only to refuse a leftover value.
RETIRED_ENV_SERVER = "PRKS_HTTP_SERVER"

DEFAULT_THREADS = 16
MIN_THREADS = 1
MAX_THREADS = 256


class HttpSettingsError(ValueError):
    """An invalid HTTP server setting; ``message`` names the variable and the fix."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class HttpServerSettings:
    threads: int = DEFAULT_THREADS

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "HttpServerSettings":
        if env.get(RETIRED_ENV_SERVER, "").strip():
            raise HttpSettingsError(
                f"{RETIRED_ENV_SERVER} is no longer read: PRKS always serves HTTP with Uvicorn. "
                f"Remove {RETIRED_ENV_SERVER} from the environment (and from any .env file)."
            )
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
        return cls(threads=threads)
