"""HTTP server settings, read once at startup (#1329).

PRKS serves HTTP with Uvicorn only. ``PRKS_HTTP_THREADS`` bounds the threads
that run PRKS request work. Invalid values refuse to start; nothing falls
back silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

ENV_THREADS = "PRKS_HTTP_THREADS"

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
