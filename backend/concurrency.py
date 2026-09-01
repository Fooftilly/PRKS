"""Process-local library access gate for threaded HTTP serving.

Ordinary reads may overlap. Canonical mutations are serialized. Backup blocks
mutations but permits reads. Restore is exclusive against all active-storage
access. SQLite connections stay per-operation; this module does not open them.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator
from urllib.parse import parse_qs

MODE_READ = "read"
MODE_MUTATION = "mutation"
MODE_BACKUP = "backup"
MODE_RESTORE = "restore"

_MUTATING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD"})
_TRUTHY = frozenset({"1", "true", "yes"})


def _query_flag(query: str, name: str) -> bool:
    if not query:
        return False
    raw = (parse_qs(query, keep_blank_values=True).get(name) or [""])[0]
    return str(raw).strip().lower() in _TRUTHY


def request_access_mode(method: str, path: str, query: str = "") -> str | None:
    """Classify HTTP access against active storage. None = no library gate."""
    method = (method or "").upper()
    path = path or "/"
    query = query or ""
    if not path.startswith("/api/"):
        return None
    if method in _READ_METHODS:
        if path == "/api/processing-files" and _query_flag(query, "rescan"):
            return MODE_MUTATION
        if path.startswith("/api/persons/") and path.endswith("/profile-image"):
            return MODE_MUTATION
        if path.startswith("/api/works/") and path.endswith("/thumbnail"):
            return MODE_MUTATION
        return MODE_READ
    if method == "POST":
        if path == "/api/backups/progress":
            return MODE_BACKUP
        if path == "/api/backups/restore":
            return MODE_RESTORE
        if path == "/api/backups/stage":
            return MODE_READ
        return MODE_MUTATION
    if method in _MUTATING_METHODS:
        return MODE_MUTATION
    return MODE_MUTATION


class LibraryAccessGate:
    """Writer/maintenance-preferring Condition gate. One instance per HTTP server."""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._active_reads = 0
        self._mutation_active = False
        self._backup_active = False
        self._restore_active = False
        self._backup_waiting = 0
        self._restore_waiting = 0

    def _snapshot_unlocked(self) -> dict:
        return {
            "active_reads": self._active_reads,
            "mutation_active": self._mutation_active,
            "backup_active": self._backup_active,
            "restore_active": self._restore_active,
            "backup_waiting": self._backup_waiting,
            "restore_waiting": self._restore_waiting,
        }

    def snapshot(self) -> dict:
        with self._cv:
            return self._snapshot_unlocked()

    def wait_until(self, predicate: Callable[[dict], bool], timeout: float = 5.0) -> bool:
        with self._cv:
            return self._cv.wait_for(
                lambda: predicate(self._snapshot_unlocked()),
                timeout=timeout,
            )

    def scope(self, mode: str):
        if mode == MODE_READ:
            return self.read()
        if mode == MODE_MUTATION:
            return self.mutation()
        if mode == MODE_BACKUP:
            return self.backup()
        if mode == MODE_RESTORE:
            return self.restore()
        raise ValueError("unknown library access mode")

    @contextmanager
    def read(self) -> Iterator[None]:
        with self._cv:
            while self._restore_active or self._restore_waiting:
                self._cv.wait()
            self._active_reads += 1
        try:
            yield
        finally:
            with self._cv:
                self._active_reads -= 1
                self._cv.notify_all()

    @contextmanager
    def mutation(self) -> Iterator[None]:
        with self._cv:
            while (
                self._mutation_active
                or self._backup_active
                or self._backup_waiting
                or self._restore_active
                or self._restore_waiting
            ):
                self._cv.wait()
            self._mutation_active = True
        try:
            yield
        finally:
            with self._cv:
                self._mutation_active = False
                self._cv.notify_all()

    @contextmanager
    def backup(self) -> Iterator[None]:
        acquired = False
        with self._cv:
            self._backup_waiting += 1
            try:
                while (
                    self._mutation_active
                    or self._backup_active
                    or self._restore_active
                    or self._restore_waiting
                ):
                    self._cv.wait()
                self._backup_active = True
                acquired = True
            finally:
                self._backup_waiting -= 1
                self._cv.notify_all()
        try:
            yield
        finally:
            if acquired:
                with self._cv:
                    self._backup_active = False
                    self._cv.notify_all()

    @contextmanager
    def restore(self) -> Iterator[None]:
        acquired = False
        with self._cv:
            self._restore_waiting += 1
            try:
                while (
                    self._active_reads
                    or self._mutation_active
                    or self._backup_active
                    or self._restore_active
                ):
                    self._cv.wait()
                self._restore_active = True
                acquired = True
            finally:
                self._restore_waiting -= 1
                self._cv.notify_all()
        try:
            yield
        finally:
            if acquired:
                with self._cv:
                    self._restore_active = False
                    self._cv.notify_all()
