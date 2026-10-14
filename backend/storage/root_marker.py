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

**Database pairing fields** (``docs/postgresql-architecture.md`` §11.1, L-ID-1).
A root claimed by a PostgreSQL instance also records ``instance_id`` (the
``instance.id`` that claimed it; absent means unclaimed), ``start_generation``
(absent counts as 0) and ``start_token``. Exactly two writers change them, both
under the **shared** ``root.lock`` that every process of the paired instance
holds: :func:`claim_root`, serialized by the exclusive ``root.claim`` lock, writes
them once, and :func:`bump_start_generation` moves them from one expected
generation and token to the next. :func:`write_marker` (the exclusive-lease
writer) refuses to change them, so no other path can lower a generation or
overwrite a claim. A relocation carries them unchanged into its destination
(:func:`carry_pairing_fields`).
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
from typing import Any, Callable, Mapping, Optional

from backend.fs_durability import fsync_directory, replace_file_atomically
from backend.storage.errors import StorageRootRefused
from backend.storage.file_lock import (
    ExclusiveFileLock,
    LockBusy,
    LockUnavailable,
    SharedFileLock,
    is_link_or_reparse_point,
)

MARKER_FILENAME = "prks-root.json"
# Where a root's single-process lease lives (§12). Defined here, beside the
# marker, because only that lease's holder may write the marker.
MAINTENANCE_DIRNAME = ".prks-maintenance"
ROOT_LOCK_NAME = "root.lock"
# The claim lock (§11.1): held exclusive by claim_root only, for one
# read-check-write-read of an unclaimed marker. Lives beside root.lock.
CLAIM_LOCK_NAME = "root.claim"
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
INSTANCE_ID_RE = re.compile(r"^IN-[0-9A-F]{32}$")
START_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
# The fields only claim_root and bump_start_generation may change (§11.1).
PAIRING_FIELDS = ("instance_id", "start_generation", "start_token")
# How long a claim waits for another claim of the same root to finish. A claim
# holds the lock for one marker read, write and read-back.
DEFAULT_CLAIM_TIMEOUT_SECONDS = 30.0
_MAX_MARKER_BYTES = 64 * 1024
_WINDOWS_SHARING_RETRIES = 6


def mint_storage_root_id() -> str:
    """A fresh ``SR-<32 hex>`` storage root ID."""
    return "SR-" + secrets.token_hex(16)


def new_start_token() -> str:
    """A fresh random ``start_token`` (§11.1): 128 bits, lowercase hex."""
    return secrets.token_hex(16)


def utc_timestamp(now: Optional[datetime] = None) -> str:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class StartState:
    """A start generation and the token drawn when it was reached (§11.1)."""

    generation: int
    token: Optional[str]


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
    def instance_id(self) -> Optional[str]:
        """The ``instance.id`` that claimed this root, or ``None`` (unclaimed)."""
        value = self.document.get("instance_id")
        return value if isinstance(value, str) else None

    @property
    def start_state(self) -> StartState:
        """The marker's start generation (absent counts as 0) and token."""
        generation = self.document.get("start_generation", 0)
        token = self.document.get("start_token")
        return StartState(
            generation=generation if isinstance(generation, int) else 0,
            token=token if isinstance(token, str) else None,
        )

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


def _validate_pairing(document: Mapping[str, Any]) -> None:
    """The pairing fields are absent (unclaimed) or a consistent claim (§11.1)."""
    instance_id = document.get("instance_id")
    if instance_id is not None and (
        not isinstance(instance_id, str) or not INSTANCE_ID_RE.fullmatch(instance_id)
    ):
        raise _malformed("instance_id_invalid")
    generation = document.get("start_generation", 0)
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        raise _malformed("start_generation_invalid")
    token = document.get("start_token")
    if token is not None and (not isinstance(token, str) or not START_TOKEN_RE.fullmatch(token)):
        raise _malformed("start_token_invalid")
    if instance_id is not None and (generation < 1 or token is None):
        # A claim always writes generation 1 and a token with the id.
        raise _malformed("pairing_incomplete")
    if instance_id is None and token is not None:
        raise _malformed("pairing_incomplete")


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
    _validate_pairing(document)
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


def lease_path(root: str) -> str:
    """``<root>/.prks-maintenance/root.lock``, the root's single-process lease."""
    return os.path.join(root, MAINTENANCE_DIRNAME, ROOT_LOCK_NAME)


def _require_lease(root: str, lease: ExclusiveFileLock) -> None:
    if not isinstance(lease, ExclusiveFileLock) or not lease.held:
        raise StorageRootRefused(
            "marker_write_without_lease",
            "The storage root marker can only be written while holding the root's lease.",
        )
    if os.path.normcase(os.path.realpath(lease.path)) != os.path.normcase(
        os.path.realpath(lease_path(root))
    ):
        raise StorageRootRefused(
            "marker_write_foreign_lease",
            "The storage root marker can only be written under this root's own lease.",
        )


def claim_lock_path(root: str) -> str:
    """``<root>/.prks-maintenance/root.claim``, the claim lock (§11.1)."""
    return os.path.join(root, MAINTENANCE_DIRNAME, CLAIM_LOCK_NAME)


def require_shared_lease(root: str, lease: SharedFileLock) -> None:
    """Refuse unless ``lease`` is the held, shared ``root.lock`` of ``root``."""
    if not isinstance(lease, SharedFileLock) or not lease.held:
        raise StorageRootRefused(
            "marker_write_without_lease",
            "The storage root pairing can only be changed while holding the root's "
            "shared lease.",
        )
    if os.path.normcase(os.path.realpath(lease.path)) != os.path.normcase(
        os.path.realpath(lease_path(root))
    ):
        raise StorageRootRefused(
            "marker_write_foreign_lease",
            "The storage root pairing can only be changed under this root's own lease.",
        )


def pairing_fields(document: Mapping[str, Any]) -> dict[str, Any]:
    """The pairing fields present in ``document`` (§11.1)."""
    return {name: document[name] for name in PAIRING_FIELDS if name in document}


def carry_pairing_fields(
    source: RootMarker, destination: Mapping[str, Any]
) -> dict[str, Any]:
    """``destination`` with the source root's pairing fields, unchanged (§11.1).

    Relocation builds its destination marker with this (P1) and activates it
    as built (P6), so the moved root still passes the start check of the
    database it belongs to. Pairing fields already in ``destination`` are
    dropped first: a destination never keeps a claim of its own.
    """
    document = {k: v for k, v in destination.items() if k not in PAIRING_FIELDS}
    document.update(copy.deepcopy(pairing_fields(source.document)))
    return document


def _encode(document: Mapping[str, Any]) -> bytes:
    payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    parse_marker(payload)
    return payload


def write_marker(root: str, document: Mapping[str, Any], *, lease: ExclusiveFileLock) -> bool:
    """Atomically replace the marker, only while holding ``root``'s own lease (§12).

    ``lease`` must be the held ``root.lock`` of this very root; anything else
    is refused before a byte is written, so no code path can rewrite a marker
    it does not own. The document is validated first as well, so this can
    never publish a marker ``read_marker`` would refuse. The pairing fields
    (§11.1) must equal the current marker's: only :func:`claim_root` and
    :func:`bump_start_generation` change them. Returns the directory
    durability answer; raises ``OSError`` when the bytes could not be made
    durable (nothing replaced).
    """
    _require_lease(root, lease)
    payload = _encode(document)
    current = read_marker(root)
    if current is not None and pairing_fields(current.document) != pairing_fields(document):
        raise StorageRootRefused(
            "marker_pairing_change_refused",
            "The storage root marker's database pairing can only be changed by the "
            "root claim or the start-generation bump.",
        )
    return _replace(root, payload)


def _replace(root: str, payload: bytes) -> bool:
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


# --- database pairing (docs/postgresql-architecture.md §11.1, L-ID-1) --------------


def _refused(reason: str, message: str) -> StorageRootRefused:
    return StorageRootRefused(reason, message)


def _write_pairing(
    root: str, marker: RootMarker, *, instance_id: str, state: StartState
) -> tuple[RootMarker, bool]:
    """Replace the marker with new pairing fields, then read it back."""
    document = dict(marker.document)
    document["instance_id"] = instance_id
    document["start_generation"] = state.generation
    document["start_token"] = state.token
    durable = _replace(root, _encode(document))
    written = read_marker(root)
    if (
        written is None
        or written.instance_id != instance_id
        or written.start_state != state
        or written.storage_root_id != marker.storage_root_id
    ):
        raise _refused(
            "marker_readback_mismatch",
            "The storage root marker read back different from what was written.",
        )
    return written, durable


def claim_root(
    root: str,
    *,
    instance_id: str,
    start_token: str,
    lease: SharedFileLock,
    holds_objects: Callable[[], bool],
    timeout: float = DEFAULT_CLAIM_TIMEOUT_SECONDS,
    before_write: Optional[Callable[[], None]] = None,
) -> RootMarker:
    """Claim ``root`` for a bootstrapping instance (§11.1 "Root claim", step 2).

    Under the shared ``lease`` and the exclusive ``root.claim`` lock, re-reads
    the marker and accepts exactly two states:

    - unclaimed, at generation 0, with no stored objects: writes
      ``instance_id``, generation 1 and ``start_token``, atomically, and reads
      it back;
    - already naming ``instance_id`` at generation 1 with ``start_token`` and
      no stored objects: a retry, which writes nothing.

    Anything else is refused and the marker is left as it was: a claim never
    changes a value already there and never lowers a generation. Returns the
    claimed marker. ``before_write`` is a fault-injection point between the
    re-read and the write (tests only).
    """
    if not INSTANCE_ID_RE.fullmatch(instance_id) or not START_TOKEN_RE.fullmatch(start_token):
        raise ValueError("invalid instance id or start token")
    require_shared_lease(root, lease)
    try:
        claim = ExclusiveFileLock.acquire(claim_lock_path(root), timeout=timeout)
    except LockBusy as exc:
        raise _refused(
            "root_claim_busy", "Another claim of this storage root is still running; try again."
        ) from exc
    except LockUnavailable as exc:
        raise _refused(
            exc.reason, "The storage root claim lock .prks-maintenance/root.claim is not usable."
        ) from exc
    try:
        marker = read_marker(root)
        if marker is None:
            raise _refused(
                "root_unmarked", "The storage root has no prks-root.json; bind it before bootstrap."
            )
        refusal = binding_refusal(marker)
        if refusal is not None:
            raise refusal
        claimed_by = marker.instance_id
        state = marker.start_state
        mine = StartState(1, start_token)
        if claimed_by is None:
            if state.generation != 0:
                raise _root_used(f"it is at start generation {state.generation}")
            if holds_objects():
                raise _root_used("it already holds stored files")
            if before_write is not None:
                before_write()
            written, durable = _write_pairing(root, marker, instance_id=instance_id, state=mine)
            _require_durable_claim(root, durable)
            return written
        if claimed_by != instance_id:
            raise _refused(
                "root_claimed_by_other",
                f"This storage root belongs to PRKS instance {claimed_by}, not to this "
                f"database's instance {instance_id}. " + _RECOVERY_HINT,
            )
        if state != mine or holds_objects():
            raise _refused(
                "root_moved_on",
                f"This storage root was claimed by instance {instance_id} and has run "
                "since without this database (a copy of it taken while it was still "
                "being set up). " + _RECOVERY_HINT,
            )
        # A retry also proves the claim it found is durable before bootstrap
        # moves on, exactly as a first claim does.
        _require_durable_claim(root, False)
        return marker
    finally:
        claim.release()


def _require_durable_claim(root: str, durable: bool) -> None:
    """A claim that may not survive a crash is not a claim: refuse to go on."""
    if not durable and not fsync_directory(root):
        raise _refused(
            "marker_not_durable",
            "The storage root marker prks-root.json could not be made crash-safe.",
        )


_RECOVERY_HINT = (
    "Use whole-instance disaster recovery (this root restored together with its own "
    "database), or a new data root and a library backup restore."
)


def _root_used(why: str) -> StorageRootRefused:
    return _refused(
        "root_used",
        f"This storage root belongs to an earlier database: {why}. Its files would be "
        "deleted as orphans by a new library. " + _RECOVERY_HINT,
    )


def bump_start_generation(
    root: str,
    *,
    lease: SharedFileLock,
    instance_id: str,
    expected: StartState,
    to: StartState,
) -> tuple[RootMarker, bool]:
    """Move the marker from ``expected`` to ``to``, compare-and-set (§11.1).

    Used by the start-generation bump and by the reconcile of a bump whose
    marker write never happened. ``to`` is always the next generation. A
    marker already at ``to`` is a no-op; one naming another instance, or at
    any state but ``expected``, is refused unchanged. Re-reads, changes only
    the pairing fields, replaces atomically and reads back. Returns the
    marker and the directory durability answer; raises ``OSError`` when the
    bytes could not be made durable (nothing replaced).
    """
    if to.generation != expected.generation + 1 or not START_TOKEN_RE.fullmatch(to.token or ""):
        raise ValueError("a bump moves to the next generation with a new token")
    require_shared_lease(root, lease)
    marker = read_marker(root)
    if marker is None:
        raise _refused("root_unmarked", "The storage root has no prks-root.json.")
    if marker.instance_id != instance_id:
        raise _refused(
            "root_claimed_by_other",
            f"This storage root is paired with instance {marker.instance_id or '(none)'}, "
            f"not {instance_id}.",
        )
    current = marker.start_state
    if current == to:
        return marker, True
    if current != expected:
        raise _refused(
            "marker_generation_moved",
            f"The storage root moved to start generation {current.generation} while "
            f"this process expected {expected.generation}.",
        )
    return _write_pairing(root, marker, instance_id=instance_id, state=to)
