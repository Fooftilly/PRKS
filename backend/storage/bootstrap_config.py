"""The bootstrap configuration file (storage-architecture §5.1).

A small machine-written JSON document in the platform configuration directory,
outside every data root, that records the selected root::

    {"format": 1, "storage": {"backend": "local", "local_root": "/abs/path", "relocation": null}}

It is durable operational state: never part of a library, never backed up
(``backup_storage_inventory``), and never holds secrets.

Reading is plain. **Every write is a guarded compare-and-set**, because an
atomic replace makes one write durable but cannot stop a stale
read-modify-write from overwriting a newer selection:

1. take the exclusive OS advisory lock on the sibling ``<config file>.lock``
   (the same primitive as the root lease, so the server, the CLI and a second
   starting process all serialize on it);
2. under the lock, **re-read** the file and check the writer's expected state;
3. only if the check holds, replace the file atomically
   (``fs_durability.replace_file_atomically``), then release the lock.

A multi-step writer (a later phase's hot rebind) holds the lock across its whole
transaction through :meth:`BootstrapConfigStore.transaction`.

Phase A ships the reader and the guarded writer only. Nothing in PRKS writes
this file yet: the Settings/UI and relocation writers are later phases.
"""

from __future__ import annotations

import copy
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, Optional

from backend.fs_durability import replace_file_atomically
from backend.storage.errors import (
    BootstrapConfigConflict,
    BootstrapConfigError,
    BootstrapConfigLockTimeout,
)
from backend.storage.file_lock import ExclusiveFileLock, LockBusy, LockUnavailable

CONFIG_FORMAT = 1
LOCAL_BACKEND = "local"
LOCK_SUFFIX = ".lock"
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
# The relocation phases a later phase records (§8.2, §8.3). Phase A parses them
# so a record written by a newer PRKS is recognized; it never acts on one.
RELOCATION_PHASES = frozenset(
    {
        "preparing",
        "copying",
        "verified",
        "committed",
        "source_retired",
        "retained",
        "retained_residuals",
        "failed",
    }
)
_MAX_CONFIG_BYTES = 1024 * 1024


@dataclass(frozen=True)
class BootstrapConfig:
    """A parsed, validated bootstrap configuration document."""

    local_root: Optional[str]
    relocation: Optional[Mapping[str, Any]]
    backend: str = LOCAL_BACKEND
    document: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def storage_state(self) -> tuple[Optional[str], Optional[str]]:
        """What a compare-and-set compares: ``local_root`` and the relocation record."""
        relocation = (
            None
            if self.relocation is None
            else json.dumps(self.relocation, sort_keys=True, separators=(",", ":"))
        )
        return (self.local_root, relocation)


Expectation = Callable[[Optional[BootstrapConfig]], bool]


def expect_absent() -> Expectation:
    """Expect that no bootstrap file exists yet."""
    return lambda current: current is None


def expect_unchanged(snapshot: Optional[BootstrapConfig]) -> Expectation:
    """Expect exactly the storage state the caller read (and showed the user)."""
    if snapshot is None:
        return expect_absent()
    wanted = snapshot.storage_state()
    return lambda current: current is not None and current.storage_state() == wanted


def expect_storage(
    *, local_root: Optional[str], relocation: Optional[Mapping[str, Any]] = None
) -> Expectation:
    """Expect a specific ``local_root`` and relocation record."""
    probe = BootstrapConfig(local_root=local_root, relocation=relocation)
    wanted = probe.storage_state()
    return lambda current: current is not None and current.storage_state() == wanted


def _invalid(reason: str) -> BootstrapConfigError:
    return BootstrapConfigError(
        reason,
        "The PRKS bootstrap configuration file is not usable "
        f"({reason}). Fix or remove it, or select the storage root with "
        "--storage-root or PRKS_STORAGE.",
    )


def _validate_local_root(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise _invalid("local_root_invalid")
    # V1: the persisted form is the normalized absolute path, so it resolves
    # identically whatever the working directory or environment.
    if not os.path.isabs(value):
        raise _invalid("local_root_not_absolute")
    return os.path.normpath(value)


def _validate_relocation(value: Any) -> Optional[Mapping[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid("relocation_invalid")
    rid = value.get("id")
    phase = value.get("phase")
    if not isinstance(rid, str) or not rid:
        raise _invalid("relocation_invalid")
    if phase not in RELOCATION_PHASES:
        raise _invalid("relocation_phase_unknown")
    return copy.deepcopy(value)


def parse_bootstrap_config(raw: bytes) -> BootstrapConfig:
    """Validate a bootstrap document. Raises ``BootstrapConfigError``."""
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _invalid("malformed") from exc
    if not isinstance(document, dict):
        raise _invalid("malformed")
    fmt = document.get("format")
    if not isinstance(fmt, int) or isinstance(fmt, bool):
        raise _invalid("format_invalid")
    if fmt > CONFIG_FORMAT:
        raise _invalid("format_newer")
    if fmt != CONFIG_FORMAT:
        raise _invalid("format_invalid")
    storage = document.get("storage")
    if storage is None:
        storage = {}
    if not isinstance(storage, dict):
        raise _invalid("storage_invalid")
    backend = storage.get("backend", LOCAL_BACKEND)
    if backend != LOCAL_BACKEND:
        raise _invalid("backend_unsupported")
    return BootstrapConfig(
        local_root=_validate_local_root(storage.get("local_root")),
        relocation=_validate_relocation(storage.get("relocation")),
        backend=LOCAL_BACKEND,
        document=document,
    )


def read_bootstrap_config(path: str) -> Optional[BootstrapConfig]:
    """Read and validate the file at ``path``; ``None`` when it does not exist.

    A file that exists but cannot be read or parsed is an error, not "unset":
    it may well name a root, and treating it as absent could open another one.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _invalid("unreadable") from exc
    if os.path.islink(path):
        # A link is still a user's deliberate choice for a config file; read
        # through it, but insist the target is a regular file.
        try:
            st = os.stat(path)
        except OSError as exc:
            raise _invalid("unreadable") from exc
    if not os.path.isfile(path):
        raise _invalid("not_a_file")
    if st.st_size > _MAX_CONFIG_BYTES:
        raise _invalid("too_large")
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _invalid("unreadable") from exc
    return parse_bootstrap_config(raw)


def _serialize(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _new_document(
    current: Optional[BootstrapConfig],
    *,
    local_root: Optional[str],
    relocation: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    # Keep any top-level or storage keys this version does not know, so an
    # older writer does not silently strip what a newer one recorded.
    document: dict[str, Any] = copy.deepcopy(dict(current.document)) if current else {}
    storage = document.get("storage")
    storage = dict(storage) if isinstance(storage, dict) else {}
    storage["backend"] = LOCAL_BACKEND
    storage["local_root"] = _validate_local_root(local_root)
    storage["relocation"] = _validate_relocation(
        copy.deepcopy(dict(relocation)) if relocation is not None else None
    )
    document["format"] = CONFIG_FORMAT
    document["storage"] = storage
    return document


class BootstrapTransaction:
    """Reads and guarded writes while the config lock is held."""

    def __init__(self, store: "BootstrapConfigStore") -> None:
        self._store = store
        self._open = True

    def read(self) -> Optional[BootstrapConfig]:
        self._require_open()
        return read_bootstrap_config(self._store.path)

    def write(
        self,
        *,
        expect: Expectation,
        local_root: Optional[str],
        relocation: Optional[Mapping[str, Any]] = None,
    ) -> BootstrapConfig:
        """Re-read, check ``expect``, then write atomically. Raises on mismatch."""
        self._require_open()
        current = read_bootstrap_config(self._store.path)
        if not expect(current):
            raise BootstrapConfigConflict(
                "bootstrap_config_conflict",
                "The storage selection changed since it was read. Review the "
                "current selection and try again.",
            )
        document = _new_document(current, local_root=local_root, relocation=relocation)
        payload = _serialize(document)
        try:
            durable = replace_file_atomically(self._store.path, payload)
        except OSError as exc:
            raise BootstrapConfigError(
                "write_failed", "The PRKS bootstrap configuration file could not be written."
            ) from exc
        if not durable:
            # Recorded state other code will act on after a crash must not be
            # reported as written when its directory entry is not durable.
            raise BootstrapConfigError(
                "write_not_durable",
                "The PRKS bootstrap configuration file could not be made crash-safe.",
            )
        return parse_bootstrap_config(payload)

    def _close(self) -> None:
        self._open = False

    def _require_open(self) -> None:
        if not self._open:
            raise RuntimeError("bootstrap configuration transaction is closed")


class BootstrapConfigStore:
    """The one writer interface for the bootstrap configuration file."""

    def __init__(self, path: str, *, lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
        self.path = os.path.abspath(path)
        self.lock_path = self.path + LOCK_SUFFIX
        self.lock_timeout = lock_timeout

    def read(self) -> Optional[BootstrapConfig]:
        """Unlocked read, e.g. to show the user the current selection."""
        return read_bootstrap_config(self.path)

    @contextmanager
    def transaction(self) -> Iterator[BootstrapTransaction]:
        """Hold ``<config file>.lock`` for the whole block."""
        directory = os.path.dirname(self.path)
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
        except OSError as exc:
            raise BootstrapConfigError(
                "config_dir_unavailable",
                "The PRKS configuration directory could not be created.",
            ) from exc
        try:
            lock = ExclusiveFileLock.acquire(self.lock_path, timeout=self.lock_timeout)
        except LockBusy as exc:
            raise BootstrapConfigLockTimeout(
                "bootstrap_config_locked",
                "Another PRKS process is changing the storage selection. Try again.",
            ) from exc
        except LockUnavailable as exc:
            raise BootstrapConfigError(exc.reason, "The PRKS configuration lock is not usable.") from exc
        txn = BootstrapTransaction(self)
        try:
            yield txn
        finally:
            txn._close()
            lock.release()

    def compare_and_set(
        self,
        *,
        expect: Expectation,
        local_root: Optional[str],
        relocation: Optional[Mapping[str, Any]] = None,
    ) -> BootstrapConfig:
        """One guarded write: lock, re-read, check ``expect``, replace atomically."""
        with self.transaction() as txn:
            return txn.write(expect=expect, local_root=local_root, relocation=relocation)
