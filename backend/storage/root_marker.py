"""The data-root marker ``<root>/prks-root.json`` (storage-architecture §7.1).

::

    {"format": 1, "storage_root_id": "SR-<32 hex>", "layout_version": 1,
     "state": "active", "created_at": "...Z", "relocation": null}

``storage_root_id`` identifies this library's **file store**, not a directory
and not a #310 Library: it is minted once for a new or adopted root and is
carried, never re-minted, by a later relocation. The marker is durable
operational state: it is never part of a backup, and restoring into a root keeps
that root's ID.

Binding rule (Phase A): ordinary startup binds only ``state: active`` with no
relocation role. Every other state -- ``fenced`` (a relocation source),
``staging`` (a relocation destination, committed or not), ``retired`` -- needs a
finalizer that can prove how the move ended. Those finalizers are a later phase
(§8), so Phase A parses such markers precisely and **refuses** them, naming the
relocation and the peer so the operator can see where the library is.

Every marker write is an atomic replace (``fs_durability``), and only a process
holding that root's ``root.lock`` may perform one (§12). Unknown fields are
preserved on rewrite so an older PRKS never strips what a newer one recorded.
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from backend.fs_durability import replace_file_atomically
from backend.storage.errors import StorageRootRefused
from backend.storage.file_lock import is_link_or_reparse_point

MARKER_FILENAME = "prks-root.json"
MARKER_FORMAT = 1
LAYOUT_VERSION = 1

STATE_ACTIVE = "active"
STATE_FENCED = "fenced"
STATE_STAGING = "staging"
STATE_RETIRED = "retired"
MARKER_STATES = frozenset({STATE_ACTIVE, STATE_FENCED, STATE_STAGING, STATE_RETIRED})
RELOCATION_ROLES = frozenset({"source", "destination"})
RELOCATION_MARKER_PHASES = frozenset({"committed", "aborted"})

_ROOT_ID_RE = re.compile(r"^SR-[0-9a-f]{32}$")
_MAX_MARKER_BYTES = 64 * 1024
_WINDOWS_SHARING_RETRIES = 6


def mint_storage_root_id() -> str:
    """A fresh ``SR-<32 hex>`` storage root ID."""
    return "SR-" + secrets.token_hex(16)


def utc_timestamp(now: Optional[datetime] = None) -> str:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class RootMarker:
    storage_root_id: str
    layout_version: int
    state: str
    created_at: str
    relocation: Optional[Mapping[str, Any]]
    document: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def bindable(self) -> bool:
        """Whether ordinary startup may bind this root (§7.1 binding rule)."""
        return self.state == STATE_ACTIVE and self.relocation is None

    @property
    def filesystem_probe(self) -> Optional[Mapping[str, Any]]:
        probe = self.document.get("filesystem_probe")
        return probe if isinstance(probe, dict) else None


def _malformed(reason: str) -> StorageRootRefused:
    return StorageRootRefused(
        "marker_" + reason,
        f"The storage root marker {MARKER_FILENAME} is not valid ({reason}). "
        "PRKS will not guess which library this directory holds.",
    )


def _validate_relocation(value: Any) -> Optional[Mapping[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _malformed("relocation_invalid")
    if not isinstance(value.get("id"), str) or not value.get("id"):
        raise _malformed("relocation_invalid")
    if value.get("role") not in RELOCATION_ROLES:
        raise _malformed("relocation_invalid")
    peer = value.get("peer_hint")
    if peer is not None and not isinstance(peer, str):
        raise _malformed("relocation_invalid")
    if "verified" in value and not isinstance(value["verified"], bool):
        raise _malformed("relocation_invalid")
    if "phase" in value and value["phase"] not in RELOCATION_MARKER_PHASES:
        raise _malformed("relocation_invalid")
    return copy.deepcopy(value)


def parse_marker(raw: bytes) -> RootMarker:
    """Validate a marker document. Raises ``StorageRootRefused`` when malformed."""
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _malformed("unparseable") from exc
    if not isinstance(document, dict):
        raise _malformed("unparseable")
    fmt = document.get("format")
    if not isinstance(fmt, int) or isinstance(fmt, bool):
        raise _malformed("format_invalid")
    if fmt > MARKER_FORMAT:
        raise StorageRootRefused(
            "marker_format_newer",
            "This storage root was written by a newer PRKS. Upgrade PRKS to open it.",
        )
    if fmt != MARKER_FORMAT:
        raise _malformed("format_invalid")
    root_id = document.get("storage_root_id")
    if not isinstance(root_id, str) or not _ROOT_ID_RE.fullmatch(root_id):
        raise _malformed("storage_root_id_invalid")
    layout = document.get("layout_version")
    if not isinstance(layout, int) or isinstance(layout, bool) or layout < 1:
        raise _malformed("layout_version_invalid")
    if layout > LAYOUT_VERSION:
        raise StorageRootRefused(
            "layout_version_newer",
            "This storage root uses a newer storage layout. Upgrade PRKS to open it.",
        )
    state = document.get("state")
    if state not in MARKER_STATES:
        raise _malformed("state_invalid")
    created_at = document.get("created_at")
    if not isinstance(created_at, str):
        raise _malformed("created_at_invalid")
    moved_from = document.get("moved_from")
    if moved_from is not None and not isinstance(moved_from, dict):
        raise _malformed("moved_from_invalid")
    return RootMarker(
        storage_root_id=root_id,
        layout_version=layout,
        state=state,
        created_at=created_at,
        relocation=_validate_relocation(document.get("relocation")),
        document=document,
    )


def marker_path(root: str) -> str:
    return os.path.join(root, MARKER_FILENAME)


def read_marker(root: str) -> Optional[RootMarker]:
    """The marker of ``root``, or ``None`` when there is none.

    A link, a non-regular file or an unreadable marker is refused rather than
    treated as absent: "absent" would make the directory eligible to become a
    new root.
    """
    path = marker_path(root)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _malformed("unreadable") from exc
    if is_link_or_reparse_point(st):
        raise StorageRootRefused(
            "marker_is_link",
            f"The storage root marker {MARKER_FILENAME} is a link. Links inside a "
            "storage root are not allowed.",
        )
    if not stat.S_ISREG(st.st_mode):
        raise _malformed("not_a_file")
    if st.st_size > _MAX_MARKER_BYTES:
        raise _malformed("too_large")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _malformed("unreadable") from exc
    with os.fdopen(fd, "rb") as handle:
        raw = handle.read(_MAX_MARKER_BYTES + 1)
    return parse_marker(raw)


def new_marker_document(*, now: Optional[datetime] = None) -> dict[str, Any]:
    """The document for a new or adopted root: a fresh ID, ``active``, no relocation."""
    return {
        "format": MARKER_FORMAT,
        "storage_root_id": mint_storage_root_id(),
        "layout_version": LAYOUT_VERSION,
        "state": STATE_ACTIVE,
        "created_at": utc_timestamp(now),
        "relocation": None,
    }


def write_marker(root: str, document: Mapping[str, Any]) -> bool:
    """Atomically replace the marker. The caller must hold ``root``'s lease.

    The document is validated before anything is written, so this can never
    publish a marker that ``read_marker`` would refuse. Returns the directory
    durability answer; raises ``OSError`` when the bytes could not be made
    durable (nothing replaced).
    """
    payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    parse_marker(payload)
    for attempt in range(_WINDOWS_SHARING_RETRIES):
        try:
            return replace_file_atomically(marker_path(root), payload)
        except PermissionError:
            # Windows refuses to replace a file another process has open
            # without delete sharing -- for example a refused second process
            # reading the holder hint. That reader closes within milliseconds.
            if os.name != "nt" or attempt + 1 >= _WINDOWS_SHARING_RETRIES:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")  # pragma: no cover


def binding_refusal(marker: RootMarker) -> Optional[StorageRootRefused]:
    """Why ordinary startup must not bind a root with this marker, or ``None``."""
    if marker.bindable:
        return None
    relocation = marker.relocation or {}
    rid = relocation.get("id") or "unknown"
    peer = relocation.get("peer_hint")
    peer_text = f" The other end of the move is {peer}." if peer else ""
    if marker.state == STATE_RETIRED:
        where = f" The library moved to {peer}." if peer else ""
        return StorageRootRefused(
            "root_retired",
            "This storage root was retired by a completed move and is a stale copy."
            f"{where} Select the new location instead.",
        )
    if marker.state == STATE_FENCED:
        return StorageRootRefused(
            "root_fenced",
            f"This storage root is the source of an unresolved move (relocation {rid})."
            f"{peer_text} It cannot be opened until that move is finalized or aborted.",
        )
    if marker.state == STATE_STAGING:
        phase = relocation.get("phase")
        if phase == "aborted":
            detail = "It belongs to an aborted move and is never opened."
        elif phase == "committed":
            detail = (
                "The move committed, but this PRKS version cannot complete it; "
                "run a PRKS version that supports storage relocation."
            )
        else:
            detail = "It is an unfinished copy; finalize or abort the move first."
        return StorageRootRefused(
            "root_staging",
            f"This storage root is the destination of a move (relocation {rid}).{peer_text} {detail}",
        )
    return StorageRootRefused(
        "root_not_active",
        "This storage root carries a relocation record on an active marker, which "
        "PRKS cannot resolve safely.",
    )
