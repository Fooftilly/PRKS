"""The restore archive upload ``POST /api/backups/stage`` streams to disk (§11.4).

The route copies the request body, in bounded reads, to an owner-only file
that is synced before the archive is opened, and bounds it with
``PRKS_BACKUP_MAX_UPLOAD_BYTES``. A body over the limit, or shorter than its
declared length, leaves no file behind.
"""

from __future__ import annotations

import os
from typing import Protocol

from backend.fs_durability import fsync_open_file

IO_CHUNK_SIZE = 64 * 1024
DEFAULT_MAX_UPLOAD_BYTES = 64 * 1024 * 1024 * 1024


class RestoreError(Exception):
    """An upload the route refuses: ``reason`` and ``message`` go to the client."""

    def __init__(self, reason: str, message: str, *, http_status: int = 400):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.http_status = http_status


class _Reader(Protocol):
    def read(self, n: int, /) -> bytes: ...


def backup_max_upload_bytes() -> int:
    """``PRKS_BACKUP_MAX_UPLOAD_BYTES``, or 64 GiB when unset or not a positive integer."""
    raw = (os.environ.get("PRKS_BACKUP_MAX_UPLOAD_BYTES") or "").strip()
    if not raw:
        return DEFAULT_MAX_UPLOAD_BYTES
    try:
        value = int(raw, 10)
    except ValueError:
        return DEFAULT_MAX_UPLOAD_BYTES
    if value < 1:
        return DEFAULT_MAX_UPLOAD_BYTES
    return value


def _owner_only(path: str, mode: int) -> None:
    if os.name != "posix":
        return
    try:
        os.chmod(path, mode)
    except OSError:
        return


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


def stream_upload_to_file(
    reader: _Reader,
    dest_path: str,
    *,
    expected_length: int,
    max_bytes: int,
    chunk_size: int = IO_CHUNK_SIZE,
) -> int:
    """Copy exactly ``expected_length`` bytes of ``reader`` to ``dest_path``; the count written."""
    if expected_length < 0:
        raise RestoreError("invalid_length", "invalid Content-Length")
    if expected_length > max_bytes:
        raise RestoreError("request_too_large", "Backup is larger than the configured upload limit.")
    parent = os.path.dirname(dest_path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    _owner_only(parent, 0o700)
    written = 0
    try:
        with open(dest_path, "wb") as handle:
            remaining = expected_length
            while remaining > 0:
                chunk = reader.read(min(chunk_size, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                remaining -= len(chunk)
            handle.flush()
            fsync_open_file(handle.fileno())
        _owner_only(dest_path, 0o600)
    except Exception:
        _remove(dest_path)
        raise
    if written != expected_length:
        _remove(dest_path)
        raise RestoreError("incomplete_upload", "Backup upload was incomplete.")
    return written
