"""The one refusal type of the identity lane (L-ID-1, #626)."""

from __future__ import annotations


class RootPairingRefused(Exception):
    """A bootstrap, a bootstrap token or a start was refused; nothing that should not change did.

    ``reason`` is a stable code. ``message`` is fixed text that names at most
    instance and storage root IDs, never a path, a token, a password, the
    connection string or a driver message. ``retryable`` is true when the same
    process may try again later and expect a different answer (the start key
    was busy, the database or the marker could not be written); the process
    stays not ready meanwhile.
    """

    def __init__(self, reason: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.retryable = retryable
