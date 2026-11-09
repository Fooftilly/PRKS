"""Uploaded archives staged for a restore (PG-C1 #640; §11.4).

``POST /api/backups/stage`` receives an archive into
``<root>/.prks-maintenance/restore-staged`` and verifies it (R0) here; a later
``POST /api/backups/restore`` names it by its token. A staged file is named
by its token and a hash of the uploading user, so another account's token
resolves to nothing. Uploads nobody restored are removed by the next stage
after :data:`STAGED_TTL_SECONDS`.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from backend.domain.maintenance.archive import Archive, check_schema, open_archive
from backend.domain.maintenance.connection import maintenance_connection
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS

STAGED_DIRNAME = os.path.join(".prks-maintenance", "restore-staged")
STAGED_TTL_SECONDS = 60 * 60
_STAGED_SUFFIX = ".staged"
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class Staged:
    token: str
    summary: dict[str, Any]


def staged_dir(root: str) -> str:
    """``<root>/.prks-maintenance/restore-staged``, ``0700``, created on first use."""
    path = os.path.join(root, STAGED_DIRNAME)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def staged_path(root: str, token: str, user_id: str) -> str:
    owner = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]
    return os.path.join(staged_dir(root), f"{token}.{owner}{_STAGED_SUFFIX}")


def find_staged(root: str, token: str, user_id: str) -> Optional[str]:
    """The archive ``user_id`` staged under ``token``, or None."""
    if not TOKEN_RE.fullmatch(token):
        return None
    path = staged_path(root, token, user_id)
    return path if os.path.isfile(path) else None


def discard(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


def prune_staged(root: str, *, now: Optional[float] = None) -> int:
    """Remove staged uploads older than :data:`STAGED_TTL_SECONDS`."""
    removed = 0
    cutoff = (time.time() if now is None else now) - STAGED_TTL_SECONDS
    directory = staged_dir(root)
    for name in os.listdir(directory):
        if not name.endswith(_STAGED_SUFFIX):
            continue
        path = os.path.join(directory, name)
        try:
            if os.lstat(path).st_mtime < cutoff:
                os.unlink(path)
                removed += 1
        except FileNotFoundError:
            pass
    return removed


def stage_archive(conninfo: str, root: str, user_id: str, restore_dir: str,
                  receive: Callable[[str], None]) -> Staged:
    """Receive an archive with ``receive(path)``, verify it (R0) and keep it for ``user_id``.

    Raises :class:`~backend.domain.maintenance.archive.ArchiveRefused` for an
    archive that fails verification; on any failure the staged file is removed.
    """
    prune_staged(root)
    token = uuid.uuid4().hex
    path = staged_path(root, token, user_id)
    try:
        receive(path)
        with maintenance_connection(conninfo) as conn, \
                open_archive(path, staging_dir=restore_dir) as archive:
            check_schema(conn, archive)
            summary = summarize(archive)
    except BaseException:
        discard(path)
        raise
    return Staged(token=token, summary=summary)


def summarize(archive: Archive) -> dict[str, Any]:
    def rows(table: str) -> Optional[int]:
        member = archive.tables.get("prks." + table)
        return member.rows if member is not None else None

    return {"created_at": archive.manifest.get("created_at"), "works": rows("works"),
            # Only Asset bytes count as files; portraits and other objects do not.
            "pdf_files": sum(1 for key in archive.objects if key.namespace == NAMESPACE_ASSET_OBJECTS),
            "persons": rows("persons"),
            "annotations": rows("annotations")}
