"""Errors raised while selecting, validating and binding a storage root.

Every refusal carries a stable ``reason`` code (safe to log) and a
human-readable message for the operator. Messages may name paths, because they
are shown to the owner on the console at startup (storage-architecture §11.1);
they must never be passed to the logger. Log ``reason`` only.
"""

from __future__ import annotations


class StorageRootError(Exception):
    """Base class: PRKS cannot use the selected storage root."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


class InvalidStorageRoot(StorageRootError):
    """A set root source is unusable (V1/V2/V11/V12 path-level checks)."""


class StorageRootRefused(StorageRootError):
    """The root exists but must not be bound (marker state, foreign content, links)."""


class StorageRootInUse(StorageRootError):
    """Another process holds this root's ``root.lock`` lease (§12)."""


class BootstrapConfigError(StorageRootError):
    """The bootstrap configuration file is unreadable, malformed or unsupported."""


class BootstrapConfigConflict(StorageRootError):
    """A guarded compare-and-set found a state other than the one it expected."""


class BootstrapConfigLockTimeout(StorageRootError):
    """The bootstrap configuration lock could not be taken in time."""
