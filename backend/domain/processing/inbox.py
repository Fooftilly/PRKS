"""The inbox on PostgreSQL: discovery, the scan and the import (§11.3, §11.5; L-PR-1 #636).

The inbox is a directory of configuration (``StorageConfig.processing_dir``);
rows name files relative to it (``rel_path``). Every change PRKS makes to
inbox files holds the Library's **inbox lock** (:data:`INBOX_LOCK_KEY`)
exclusively across both its database commit and its file change. The lock
is session-level, held on a connection of its own outside the pool
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
4. removes the source file while it still has that hash (another writer's
   replacement is left, and listed as new by the next scan), then deletes
   the row.

A row a restore holds (§11.4) is imported only while the file at its path
has ``restore_sha256``.

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
import re
import uuid
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
# A file the inbox protocol has claimed for removal (``_claim``).
CLAIM_PREFIX = ".prks-claimed-"
_CLAIM = re.compile(re.escape(CLAIM_PREFIX) + r"[0-9a-f]{32}-(.+)", re.DOTALL)
HELD = "This file was restored from a backup, and the file at its path is not the one backed up."


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
    ``rel_path`` is ``/``-separated on every platform. A file the protocol
    has claimed (:func:`_claim`) is not listed.
    """
    os.makedirs(inbox_dir, exist_ok=True)
    root = os.path.realpath(inbox_dir)
    found: list[tuple[str, str]] = []
    for directory, _dirs, names in os.walk(root):
        for name in sorted(names):
            if not name.lower().endswith(".pdf") or name.startswith(CLAIM_PREFIX):
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


def _remove(inbox_dir: str, claim: str, path: str) -> bool:
    """Remove a claimed inbox file (absent counts as removed) and the directories ``path`` leaves empty."""
    from backend.db_manager import prune_empty_processing_parent_dirs

    try:
        os.remove(claim)
    except FileNotFoundError:
        pass
    except OSError as exc:
        LOGGER.warning("processing_source_remove_failed error=%s", type(exc).__name__)
        return False
    prune_empty_processing_parent_dirs(inbox_dir, path)
    return True


def _claim(path: str) -> Optional[str]:
    """Move the file at ``path`` to a private name beside it; None when there is none.

    Writers outside PRKS do not take the inbox lock, so a path can be
    replaced at any time. After the rename the claimed name holds exactly
    one file, which no outside writer names: what is hashed is what is
    removed. The private name keeps the file's name after the prefix, so
    after a crash the next scan puts it back (:func:`recover_claims`); it
    is never offered for import under the private name.
    """
    claim = os.path.join(os.path.dirname(path), f"{CLAIM_PREFIX}{uuid.uuid4().hex}-{os.path.basename(path)}")
    try:
        os.rename(path, claim)
    except FileNotFoundError:
        return None
    return claim


def _put_back(claim: str, path: str) -> bool:
    """Make a claimed file visible again; whether it is.

    It returns to ``path``, never over a file that has arrived there since;
    then it takes a new name beside it (``<name> (recovered <id>).pdf``),
    so both files are listed. On any other failure the claim stays, and the
    next scan tries again.
    """
    stem, ext = os.path.splitext(path)
    for target in (path, f"{stem} (recovered {uuid.uuid4().hex[:8]}){ext or '.pdf'}"):
        try:
            os.link(claim, target)
        except FileExistsError:
            continue
        except OSError as exc:
            LOGGER.warning("processing_claim_kept error=%s", type(exc).__name__)
            return False
        try:
            os.remove(claim)
        except OSError as exc:  # both names hold it; the next scan's recovery finds the claim again
            LOGGER.warning("processing_claim_unlink_failed error=%s", type(exc).__name__)
        return True
    LOGGER.warning("processing_claim_kept reason=names_taken")
    return False


def recover_claims(inbox_dir: str) -> None:
    """Put back every claimed file a crash left in the inbox (:func:`_claim`).

    Runs under the inbox lock, so no claim of a running import or scan is
    among them. A file whose import finished is then removed again by the
    scan's own rule, by its hash.
    """
    root = os.path.realpath(inbox_dir)
    if not os.path.isdir(root):
        return
    for directory, _dirs, names in os.walk(root):
        for name in names:
            found = _CLAIM.fullmatch(name)
            if found is not None:
                _put_back(os.path.join(directory, name), os.path.join(directory, found.group(1)))


# -- scan -------------------------------------------------------------------------


def scan(runner: Runner, inbox_dir: str, new_id: Callable[[], str]) -> None:
    """Reconcile the Library's rows with the inbox, under the inbox lock (module docs)."""
    with inbox_lock(runner.database, runner.library_id):
        recover_claims(inbox_dir)
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
    A file that cannot be read or removed keeps the row, for the next scan
    to retry. The file is claimed (:func:`_claim`) before it is hashed, so
    a replacement arriving meanwhile is never the one removed.
    """
    path = resolve(inbox_dir, str(row["rel_path"]))
    if path is None or not os.path.isfile(path):
        return True
    try:
        claim = _claim(path)
    except OSError as exc:
        LOGGER.warning("processing_source_claim_failed error=%s", type(exc).__name__)
        return False
    if claim is None:
        return True
    digest = file_sha256(claim)
    if digest != row["import_sha256"]:
        return _put_back(claim, path) and digest is not None
    if _remove(inbox_dir, claim, path):
        return True
    _put_back(claim, path)
    return False


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
        present = bool(source) and os.path.isfile(str(source))
        if row["restore_hold"] and not (present and row["restore_sha256"]
                                        and file_sha256(str(source)) == row["restore_sha256"]):
            # A restore could not vouch for the file at this path (§11.4):
            # the row and whatever is there are kept, never imported together.
            raise ValueError(HELD)
        if not source or not present:
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
        if row["restore_hold"] and stored.source.sha256 != row["restore_sha256"]:
            # The file changed after the check above: what was stored is not
            # the backed-up file. Its keys are orphans for the sweep (§11.2).
            raise ValueError(HELD)
        work_id, sha256 = _commit(runner, objects, file_id, stored, build)
        # The file is removed only while it is the one ingested; another
        # writer's replacement stays, and the next scan lists it as new.
        if _finish_import(inbox_dir, {"rel_path": row["rel_path"], "import_sha256": sha256}):
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
            build: Callable[[dict[str, Any], ManagedFile], NewWork]) -> tuple[str, str]:
    """The import's transaction; ``(work_id, import_sha256)``."""
    from backend.db.errors import ConstraintViolationError

    managed = stored.managed_file(origin="processing_import")
    sha256 = managed.ingest_sha256 or managed.sha256
    try:
        work_id = runner.command(lambda scope: _create(scope, objects, file_id, managed, build), serializable=True)
    except _Gone:
        raise ValueError(files.NOT_FOUND) from None
    except (ValueError, ConstraintViolationError) as exc:
        raise _refuse(runner, file_id, f"Failed to insert imported file into works table: {exc}") from None
    return work_id, sha256


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
