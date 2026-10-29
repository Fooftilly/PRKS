"""The inbox on PostgreSQL: discovery, the scan and the import (§11.3, §11.5; L-PR-1 #636).

The inbox is a directory of configuration (``StorageConfig.processing_dir``);
rows name files relative to it (``rel_path``). Every change PRKS makes to
inbox files holds the Library's **inbox lock** (:data:`INBOX_LOCK_KEY`)
exclusively across both its database commit and its file change. The lock
is session-level, held on its own pooled connection
(:meth:`~backend.db.database.Database.advisory_lock`), so it spans the
commits and is released on every path. A backup takes it shared while it
exports its snapshot and copies the inbox (L-B-1); restore's R3 takes it
exclusively to publish restored files.

**Import** (:func:`import_file`), under the lock:

1. checks the row, its target Folder and the source file as SQLite does,
   recording an ``error`` row with SQLite's message where SQLite does;
2. stores the source through the objects lane (``objects.ingest.store_pdf``:
   the raw stream under a fresh key, hashed while written, and a linearized
   copy when one is made);
3. in one SERIALIZABLE command creates the Work (adopting the keys), gives it
   the staged roles and tags, files it in its Folder (or "Uncategorized") and
   sets the row ``imported`` with ``import_sha256``, the raw stream's hash;
4. removes the source file, then deletes the row.

A crash or a failed removal between 3 and 4 leaves an ``imported`` row,
which the next scan finishes. Bytes a failed or refused step 3 leaves under
the fresh keys are orphans the objects sweep claims (§11.2); nothing here
deletes a stored object.

**Scan** (:func:`scan`), under the lock: an ``imported`` row's source is
removed only while the file at its path has ``import_sha256``, and the row
is deleted (absent file: the row only; a different file: the row only, and
that file is listed as a new pending one). A held row is never deleted; its
hold is cleared once the file at its path has ``restore_sha256``. Then the
inbox's PDFs are reconciled with the rows in one transaction
(:func:`backend.domain.processing.files.reconcile`).

The scan runs in the caller (``scan_processing_files``) or as the
``processing.scan`` job (``jobs.py``).
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, BinaryIO, LiteralString, Optional, Protocol, TypeVar

from backend.domain.organization import folders
from backend.domain.processing import files

if TYPE_CHECKING:
    from backend.db.database import Database
    from backend.domain.identity.library_scope import LibraryScope
    from backend.domain.works.commands import ManagedFile, NewWork
    from backend.storage.objects import StorageBackend

T = TypeVar("T")

LOGGER = logging.getLogger("prks.processing")

# The Library's inbox lock, ``(library, 'inbox')`` in §11.3.
INBOX_LOCK_KEY: LiteralString = "hashtextextended('prks.inbox:' || %s, 0)"
_CHUNK = 1024 * 1024
_HEADER = b"%PDF-"


class Runner(Protocol):
    """How the scan and the import reach their Library: a request's context or a job's."""

    @property
    def database(self) -> Database: ...

    @property
    def library_id(self) -> str: ...

    def read(self, fn: Callable[[LibraryScope], T]) -> T: ...

    def command(self, fn: Callable[[LibraryScope], T], *, serializable: bool = False) -> T: ...


@contextmanager
def inbox_lock(database: Database, library_id: str) -> Iterator[None]:
    """Hold the Library's inbox lock exclusively while the block runs."""
    with database.advisory_lock(INBOX_LOCK_KEY, (library_id,)):
        yield


def discover(inbox_dir: str) -> list[tuple[str, str]]:
    """``_discover_processing_pdfs``: ``(rel_path, filename)`` per PDF under the inbox.

    Paths are resolved, so a link leading outside the inbox is skipped, and
    ``rel_path`` is ``/``-separated on every platform.
    """
    os.makedirs(inbox_dir, exist_ok=True)
    root = os.path.realpath(inbox_dir)
    found: list[tuple[str, str]] = []
    for directory, _dirs, names in os.walk(root):
        for name in sorted(names):
            if not name.lower().endswith(".pdf"):
                continue
            path = os.path.realpath(os.path.join(directory, name))
            if path != root and not path.startswith(root + os.sep):
                continue
            found.append((os.path.relpath(path, root).replace(os.sep, "/"), name))
    return found


def resolve(inbox_dir: str, rel_path: str) -> Optional[str]:
    """The file a stored ``rel_path`` names (``safe_processing_path_under_dir``), or None."""
    from backend.db_manager import safe_processing_path_under_dir

    path = safe_processing_path_under_dir(inbox_dir, rel_path)
    return str(path) if path else None


def file_sha256(path: str) -> Optional[str]:
    """The SHA-256 of the file's bytes, or None when it cannot be read."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _remove(inbox_dir: str, path: str) -> bool:
    """Remove an inbox file (absent counts as removed) and the directories it leaves empty."""
    from backend.db_manager import prune_empty_processing_parent_dirs

    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        LOGGER.warning("processing_source_remove_failed error=%s", type(exc).__name__)
        return False
    prune_empty_processing_parent_dirs(inbox_dir, path)
    return True


# -- scan -------------------------------------------------------------------------


def scan(runner: Runner, inbox_dir: str, new_id: Callable[[], str]) -> None:
    """Reconcile the Library's rows with the inbox, under the inbox lock (module docs)."""
    with inbox_lock(runner.database, runner.library_id):
        rows = runner.read(files.scan_rows)
        finished: list[str] = []
        for row in rows:
            if row["status"] == files.IMPORTED and _finish_import(inbox_dir, row):
                finished.append(str(row["id"]))
        discovered = discover(inbox_dir)
        on_disk = {rel_path for rel_path, _name in discovered}
        unheld = [str(row["id"]) for row in rows
                  if row["status"] != files.IMPORTED and row["restore_hold"] and row["restore_sha256"]
                  and row["rel_path"] in on_disk and _hash_at(inbox_dir, row["rel_path"]) == row["restore_sha256"]]
        new_ids = [new_id() for _found in discovered]
        runner.command(lambda scope: files.reconcile(scope, discovered, finished, unheld, new_ids))


def _hash_at(inbox_dir: str, rel_path: str) -> Optional[str]:
    path = resolve(inbox_dir, rel_path)
    return file_sha256(path) if path and os.path.isfile(path) else None


def _finish_import(inbox_dir: str, row: dict[str, Any]) -> bool:
    """Complete an import a crash interrupted; whether its row can go.

    The source is removed only while the file at the path has the imported
    bytes' hash; a different file there is left alone (and listed as new).
    """
    path = resolve(inbox_dir, str(row["rel_path"]))
    if path is None or not os.path.isfile(path):
        return True
    if file_sha256(path) != row["import_sha256"]:
        return True
    return _remove(inbox_dir, path)


# -- import -----------------------------------------------------------------------


class _Gone(Exception):
    """The row was finished or deleted while the import waited for it."""


def _refuse(runner: Runner, file_id: str, message: str) -> ValueError:
    runner.command(lambda scope: files.record_error(scope, file_id, message))
    return ValueError(message)


def import_file(runner: Runner, inbox_dir: str, objects: StorageBackend, file_id: str,
                build: Callable[[dict[str, Any], ManagedFile], NewWork]) -> dict[str, Any]:
    """``import_processing_file`` under the inbox lock (module docs); ``{processing_file_id, work_id}``.

    ``build`` turns the locked row and the stored file into the Work to
    create. Raises ``ValueError`` with SQLite's messages.
    """
    with inbox_lock(runner.database, runner.library_id):
        row = runner.read(lambda scope: files.peek(scope, file_id))
        if row is None:
            raise ValueError(files.NOT_FOUND)
        if row["status"] == files.IMPORTED:
            # Committed, its source not yet removed: the scan finishes it.
            if row["imported_work_id"]:
                return {"processing_file_id": file_id, "work_id": row["imported_work_id"]}
            raise ValueError(files.NOT_FOUND)
        target = row["target_folder_id"]
        if target and not runner.read(lambda scope: folders.exists(scope, target)):
            raise ValueError("Unknown folder.")
        source = resolve(inbox_dir, str(row["rel_path"] or ""))
        if not source or not os.path.isfile(source):
            runner.command(lambda scope: files.delete(scope, file_id))
            raise ValueError("Inbox file no longer present.")
        if not source.lower().endswith(".pdf"):
            raise _refuse(runner, file_id, "Only PDF files can be imported from Files for Processing.")
        try:
            with open(source, "rb") as handle:
                header = handle.read(len(_HEADER))
        except OSError as exc:
            raise _refuse(runner, file_id, f"Could not read source PDF: {exc}") from None
        if not header.startswith(_HEADER):
            raise _refuse(runner, file_id, "File does not look like a valid PDF (missing %PDF header).")
        stored = _store(runner, objects, file_id, source, str(row["filename"] or os.path.basename(source)))
        work_id = _commit(runner, objects, file_id, stored, build)
        if _remove(inbox_dir, source):
            runner.command(lambda scope: files.finish(scope, file_id))
        return {"processing_file_id": file_id, "work_id": work_id}


def _store(runner: Runner, objects: StorageBackend, file_id: str, source: str, name: str) -> Any:
    from backend.domain.objects import ingest

    def write(stream: BinaryIO) -> None:
        with open(source, "rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                stream.write(chunk)

    try:
        return ingest.store_pdf(objects, name, write, context="processing-import")
    except Exception as exc:  # noqa: BLE001 - SQLite records any store failure on the row
        raise _refuse(runner, file_id, f"Could not store PDF into managed storage: {exc}") from None


def _commit(runner: Runner, objects: StorageBackend, file_id: str, stored: Any,
            build: Callable[[dict[str, Any], ManagedFile], NewWork]) -> str:
    from backend.db.errors import ConstraintViolationError

    managed = stored.managed_file(origin="processing_import")
    try:
        return runner.command(lambda scope: _create(scope, objects, file_id, managed, build), serializable=True)
    except _Gone:
        raise ValueError(files.NOT_FOUND) from None
    except (ValueError, ConstraintViolationError) as exc:
        raise _refuse(runner, file_id, f"Failed to insert imported file into works table: {exc}") from None


def _create(scope: LibraryScope, objects: StorageBackend, file_id: str, managed: ManagedFile,
            build: Callable[[dict[str, Any], ManagedFile], NewWork]) -> str:
    """The import's transaction (step 3 of the module docs); SERIALIZABLE (§7.1).

    Lock order (``__init__``): the target Folder (or the root
    "Uncategorized"), the file row, then the Work create's own order.
    """
    from backend.domain.organization import folders, links
    from backend.domain.people import roles
    from backend.domain.works import commands
    from backend.entity_ids import generate

    peeked = files.peek(scope, file_id)
    if peeked is None:
        raise _Gone()
    target = peeked["target_folder_id"]
    if target:
        if not folders.share(scope, target):
            raise ValueError("Unknown folder.")
        folder_id = target
    else:
        folder_id = folders.ensure_uncategorized(scope, generate("F"))
    row = files.import_row(scope, file_id)
    if row is None or row["status"] == files.IMPORTED or row["target_folder_id"] != peeked["target_folder_id"]:
        raise _Gone()
    new = build(row, managed)
    work_id = commands.create_work(scope, new, objects=objects)
    if work_id is None:  # pragma: no cover - only an ``if_absent`` create returns None
        raise RuntimeError("the Work create returned no id")
    roles.insert_initial_roles(scope, work_id, [
        roles.InitialRole(role["person_id"], role["role_type"], int(role["order_index"] or 0))
        for role in row["roles"]
    ], skip_invalid=True)
    for tag in row["tags"]:
        links.edit(scope, links.WORK, work_id, str(tag["id"]), True)
    folders.file_new_work(scope, work_id, folder_id)
    files.mark_imported(scope, file_id, work_id, managed.ingest_sha256 or managed.sha256)
    return work_id
