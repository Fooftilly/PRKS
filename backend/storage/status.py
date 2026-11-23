"""Storage status facts for the owner's diagnostics (storage-architecture §11.1).

What this process knows about its bound root: the configured spelling and
the resolved path, the source that selected it, the marker's identity and
relocation state, and filesystem facts. The catalogue counts and the
database summary are the API layer's (``backend.api.storage``).

Paths leave this module only in the returned value, for the owner's
no-store response. Nothing here logs a path.

Case sensitivity and Unicode normalization are measured with probe files
(``preflight.probe_name_semantics``), never assumed from the platform. The
probe runs inside the shared preflight scaffold, so it takes ``open.lock``
like an opening process and gives up, answering "unknown", when another
process holds it. The answer is kept for the life of this process, per
device: a filesystem does not change its name rules while mounted.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from dataclasses import dataclass
from typing import Optional

from backend.storage.errors import StorageRootError
from backend.storage.preflight import NameSemantics, probe_name_semantics
from backend.storage.root_binding import (
    NETWORK_FILESYSTEM_TYPES,
    WINDOWS_LOCAL,
    acquire_open_lock,
    detect_filesystem_type,
    safe_fs_label,
)
from backend.storage.root_marker import MAINTENANCE_DIRNAME, RootMarker, read_marker

LOGGER = logging.getLogger("prks.storage")

BACKEND_LOCAL = "local"

NETWORK_LOCAL = "local"
NETWORK_NETWORK = "network"
NETWORK_UNCERTAIN = "uncertain"
NETWORK_UNKNOWN = "unknown"

# How long the status waits for an opening process's open.lock.
_PROBE_LOCK_WAIT_SECONDS = 2.0

_SEMANTICS: dict[int, NameSemantics] = {}
_SEMANTICS_GUARD = threading.Lock()


@dataclass(frozen=True)
class FilesystemFacts:
    free_bytes: Optional[int]
    total_bytes: Optional[int]
    filesystem_type: Optional[str]
    network: str
    case_sensitive: Optional[bool]
    normalization: Optional[str]
    directory_fsync: Optional[bool]


@dataclass(frozen=True)
class RootStatus:
    configured_root: str
    resolved_root: str
    source: Optional[str]
    storage_root_id: Optional[str]
    layout_version: Optional[int]
    state: Optional[str]
    relocation_state: str
    backend_type: str
    filesystem: FilesystemFacts


def network_class(fs_type: Optional[str]) -> str:
    """V9's classification of a filesystem type (§7.4)."""
    if fs_type is None:
        return NETWORK_UNKNOWN
    lowered = fs_type.lower()
    if lowered in NETWORK_FILESYSTEM_TYPES:
        return NETWORK_NETWORK
    if lowered.startswith("fuse"):
        return NETWORK_UNCERTAIN
    return NETWORK_LOCAL


def _name_semantics(root_real: str, device: int, *, probe: bool) -> Optional[NameSemantics]:
    with _SEMANTICS_GUARD:
        cached = _SEMANTICS.get(device)
    if cached is not None or not probe:
        return cached
    try:
        lock = acquire_open_lock(root_real, timeout=_PROBE_LOCK_WAIT_SECONDS)
    except (StorageRootError, OSError):
        return None
    try:
        semantics = probe_name_semantics(os.path.join(root_real, MAINTENANCE_DIRNAME))
    except (StorageRootError, OSError):
        LOGGER.warning("storage_status_probe_failed")
        return None
    finally:
        lock.release()
    with _SEMANTICS_GUARD:
        _SEMANTICS[device] = semantics
    return semantics


def filesystem_facts(root_real: str, marker: Optional[RootMarker], *, probe: bool = True) -> FilesystemFacts:
    """Free and total space, filesystem type, network class and name rules of ``root_real``.

    ``probe`` False skips the name-rule probe (a root this process holds no
    lease on must not get probe files) and answers from the cache only.
    """
    try:
        usage = shutil.disk_usage(root_real)
        free, total = int(usage.free), int(usage.total)
    except OSError:
        free = total = None
    raw_type = detect_filesystem_type(root_real)
    shown_type = None if raw_type is None or raw_type == WINDOWS_LOCAL else safe_fs_label(raw_type)
    try:
        device = os.stat(root_real).st_dev
    except OSError:
        device = None
    semantics = _name_semantics(root_real, device, probe=probe) if device is not None else None
    cached = marker.filesystem_probe if marker is not None else None
    directory_fsync: Optional[bool] = None
    if isinstance(cached, dict) and cached.get("device") == device and isinstance(cached.get("directory_fsync"), bool):
        directory_fsync = cached["directory_fsync"]
    return FilesystemFacts(
        free_bytes=free,
        total_bytes=total,
        filesystem_type=shown_type,
        network=network_class(raw_type),
        case_sensitive=semantics.case_sensitive if semantics else None,
        normalization=semantics.normalization if semantics else None,
        directory_fsync=directory_fsync,
    )


def root_status(
    *,
    configured_root: str,
    resolved_root: str,
    source: Optional[str],
    leased: bool,
) -> RootStatus:
    """The status of this process's root; ``leased`` when it holds the root's lease.

    The marker is read again, so the status shows the marker as it is now,
    not as it was when the root was opened.
    """
    try:
        marker = read_marker(resolved_root)
    except StorageRootError:
        marker = None
    relocation = marker.relocation if marker is not None else None
    phase = relocation.get("phase") if isinstance(relocation, dict) else None
    return RootStatus(
        configured_root=configured_root,
        resolved_root=resolved_root,
        source=source,
        storage_root_id=marker.storage_root_id if marker is not None else None,
        layout_version=marker.layout_version if marker is not None else None,
        state=marker.state if marker is not None else None,
        relocation_state=(phase if isinstance(phase, str) else "unknown") if relocation is not None else "none",
        backend_type=BACKEND_LOCAL,
        filesystem=filesystem_facts(resolved_root, marker, probe=leased),
    )


def reset_cache() -> None:
    """Forget the measured name rules (tests)."""
    with _SEMANTICS_GUARD:
        _SEMANTICS.clear()
