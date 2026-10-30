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

import errno
import hashlib
import logging
import os
import re
import stat
import uuid
from collections.abc import Callable, Iterable, Iterator
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
# A directory holding one file the inbox protocol has claimed for removal (``_claim``),
# under the file's own name. ``-<id>`` names the recovered name it is being
# published under when its own was taken, and ``-<id>-p`` records that it was
# linked there (``_put_back``).
CLAIM_PREFIX = ".prks-claimed-"
_CLAIM = re.compile(re.escape(CLAIM_PREFIX) + r"[0-9a-f]{32}(?:-([0-9a-f]{8})(-p)?)?")
# The longest file name, in bytes, on the file systems PRKS supports.
_NAME_MAX = 255
SHARED = ("The Files for Processing inbox is not available while this instance has more than one Library: "
          "the inbox folder is shared, and which Library its files belong to is not decided yet.")
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


def _unlisted(exc: OSError) -> None:
    """Fail a walk of the inbox on a directory it cannot list.

    ``os.walk`` would skip it, and the files (or claims) it holds would look
    gone, so the scan would drop their rows.
    """
    LOGGER.warning("processing_inbox_unreadable error=%s", type(exc).__name__)
    raise exc


def discover(inbox_dir: str) -> list[tuple[str, str]]:
    """``_discover_processing_pdfs``: ``(rel_path, filename)`` per PDF under the inbox.

    Paths are resolved, so a link leading outside the inbox is skipped, and
    ``rel_path`` is ``/``-separated on every platform. A file the protocol
    has claimed (:func:`_claim`) is not listed. A directory that cannot be
    listed raises ``OSError``.
    """
    os.makedirs(inbox_dir, exist_ok=True)
    root = os.path.realpath(inbox_dir)
    found: list[tuple[str, str]] = []
    for directory, dirs, names in os.walk(root, onerror=_unlisted):
        dirs[:] = [name for name in dirs if _CLAIM.fullmatch(name) is None]
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


def _release(claim: str) -> None:
    """Remove the private directory of a claim once its file has left it."""
    try:
        os.rmdir(os.path.dirname(claim))
    except OSError:
        pass


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
    _release(claim)
    prune_empty_processing_parent_dirs(inbox_dir, path)
    return True


def _claim(path: str) -> Optional[str]:
    """Move the file at ``path`` into a private directory beside it; None when there is none.

    An entry there that is no regular file is put back (:func:`_restore`)
    and counts as none; ``OSError`` when it cannot be.

    Writers outside PRKS do not take the inbox lock, so a path can be
    replaced at any time. After the rename the claimed name holds exactly
    one file, which no outside writer names: what is hashed is what is
    removed. The directory (``.prks-claimed-<id>``) keeps the file under
    its own name, so after a crash the next scan knows its exact path and
    puts it back (:func:`recover_claims`); discovery never enters it.
    """
    directory = os.path.join(os.path.dirname(path), f"{CLAIM_PREFIX}{uuid.uuid4().hex}")
    os.mkdir(directory, 0o700)
    claim = os.path.join(directory, os.path.basename(path))
    try:
        os.rename(path, claim)
    except FileNotFoundError:
        _release(claim)
        return None
    except OSError:
        _release(claim)
        raise
    # A writer may have put a directory (or a link) at the path since it was
    # checked: it is no file to claim, and goes back as it is.
    if not stat.S_ISREG(os.lstat(claim).st_mode):
        if not _restore(claim, path):
            raise OSError("processing claim of a non-file kept")
        return None
    return claim


def _restore(claim: str, path: str) -> bool:
    """Return a claimed entry that is no regular file to the inbox, unread; whether it is.

    It goes back to ``path`` or, when that is taken, to a fresh recovered
    name beside it (:func:`_recovered_name`), never over an entry there. A
    directory is renamed over an empty directory made for it
    (:func:`_publish_directory`); any other entry is put back as a claimed
    file is, by a link to itself whose recovered name the claim directory
    records (:func:`_put_back`), so a retry never publishes it twice. Left
    claimed, the next scan's recovery tries again.
    """
    if not stat.S_ISDIR(os.lstat(claim).st_mode):
        return _put_back(claim, path)
    try:
        for target in (path, *(_recovered_name(path, uuid.uuid4().hex[:8]) for _attempt in range(3))):
            if _publish_directory(claim, target):
                return True
    except OSError as exc:
        LOGGER.warning("processing_claim_kept error=%s", type(exc).__name__)
        return False
    LOGGER.warning("processing_claim_kept reason=names_taken")
    return False


def _publish_directory(claim: str, target: str) -> bool:
    """Rename a claimed directory to ``target``; False when ``target`` is another's.

    The rename replaces only an empty directory, so it goes over one made
    here for it (``mkdir`` fails on any entry there): a writer's entry is
    never replaced. One that a writer puts in that directory meanwhile
    keeps it, and the claim tries the next name. Any other failure raises.
    """
    try:
        os.mkdir(target)
    except FileExistsError:
        return False
    try:
        os.rename(claim, target)
    except OSError as exc:
        if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
            return False  # a writer's entry is in it now: it stays, listed as the writer's
        try:
            os.rmdir(target)
        except OSError:
            pass
        raise
    _release(claim)
    return True


def _drop_stale_links(claim: str) -> bool:
    """Remove other claims of the claimed file beside it; whether none is left.

    A put-back whose final unlink failed leaves the file under both its
    public name and an old claim (:func:`_put_back`). Once the file is
    claimed again from that name, the old claim is a second name for bytes
    this claim holds, so it goes: left behind, the next scan would publish
    it again after this file's import. A claim that could not have been
    published under this name (:func:`_public_names`) is another path's,
    even when it is a hard link of this file.
    """
    try:
        claimed = os.lstat(claim)
    except OSError:
        return False
    if claimed.st_nlink < 2:
        return True
    own = os.path.dirname(claim)
    parent = os.path.dirname(own)
    try:
        others = [os.path.join(parent, name) for name in os.listdir(parent)
                  if _CLAIM.fullmatch(name) is not None and os.path.join(parent, name) != own]
    except OSError:
        return False
    for directory in others:
        try:
            if not stat.S_ISDIR(os.lstat(directory).st_mode):
                continue
            names = os.listdir(directory)
        except OSError:
            return False
        for name in names:
            if os.path.basename(claim) not in _public_names(directory, name):
                continue  # another path's claim, even of a hard link of this file: recovery settles it
            stale = os.path.join(directory, name)
            try:
                entry = os.lstat(stale)
                if (entry.st_dev, entry.st_ino) == (claimed.st_dev, claimed.st_ino):
                    os.remove(stale)
                    _release(stale)
            except FileNotFoundError:
                continue
            except OSError as exc:
                LOGGER.warning("processing_stale_claim_kept error=%s", type(exc).__name__)
                return False
    return True


def _recovered_name(path: str, recovered_id: str) -> str:
    """The name beside ``path`` for a claimed file whose name was taken (``<stem> (recovered <id>)<ext>``)."""
    stem, ext = os.path.splitext(os.path.basename(path))
    ext = ext or ".pdf"
    suffix = f" (recovered {recovered_id}){ext}"
    while stem and len(os.fsencode(stem + suffix)) > _NAME_MAX:
        stem = stem[:-1]
    return os.path.join(os.path.dirname(path), stem + suffix)


def _recovered_id(claim_dir: str, *, linked: bool = False) -> Optional[str]:
    """The recovered-name id a claim directory's name carries (:data:`_CLAIM`), if any.

    With ``linked``, only once the directory records the claim was linked there.
    """
    found = _CLAIM.fullmatch(os.path.basename(claim_dir))
    if found is None or (linked and not found.group(2)):
        return None
    return found.group(1)


def _public_names(claim_dir: str, name: str, *, linked: bool = False) -> tuple[str, ...]:
    """The names beside ``claim_dir`` that its claim ``name`` may be published under.

    Its own name, and the recovered name the directory's id derives from it;
    with ``linked``, that one only once the claim is recorded as linked
    there (a link that failed, or a crash before it, is no publication).
    """
    recovered_id = _recovered_id(claim_dir, linked=linked)
    if recovered_id is None:
        return (name,)
    return (name, os.path.basename(_recovered_name(name, recovered_id)))


def _published(claim: str, path: str) -> bool:
    """Whether the claimed file is already linked at ``path`` or at its recovered name (:func:`_put_back`)."""
    try:
        claimed = os.lstat(claim)
    except OSError:
        return False
    if claimed.st_nlink < 2:
        return False
    directory = os.path.dirname(path)
    for name in _public_names(os.path.dirname(claim), os.path.basename(path)):
        try:
            entry = os.lstat(os.path.join(directory, name))
        except OSError:
            continue
        if (entry.st_dev, entry.st_ino) == (claimed.st_dev, claimed.st_ino):
            return True
    return False


def _put_back(claim: str, path: str) -> bool:
    """Make a claimed file visible again; whether it is.

    It returns to ``path``, never over a file that has arrived there since;
    then it takes a recovered name beside it (:func:`_recovered_name`), so
    both files are listed. The claim keeps its own name throughout, which
    is the path its row names; before it is linked at a recovered name,
    its directory takes that name's id (:data:`_CLAIM`), so a claim left
    by a failure or a crash says where it may be published. On any other
    failure the claim stays, and the next scan tries again. A claim
    already linked back by an earlier attempt only loses its private name,
    so the file is never listed twice.
    """
    if _published(claim, path):
        try:
            os.remove(claim)
        except OSError as exc:
            LOGGER.warning("processing_claim_unlink_failed error=%s", type(exc).__name__)
            return False
        _release(claim)
        return True
    target = path
    for attempt in range(3):
        if attempt:
            recovered_id = uuid.uuid4().hex[:8]
            if os.path.lexists(_recovered_name(path, recovered_id)):
                continue  # an intent names only a free name: a file there later is this link or a new file
            directory = os.path.dirname(claim)
            base = os.path.basename(directory)[:len(CLAIM_PREFIX) + 32]  # without an earlier id
            renamed = os.path.join(os.path.dirname(directory), f"{base}-{recovered_id}")
            try:
                os.rename(directory, renamed)
            except OSError as exc:
                LOGGER.warning("processing_claim_kept error=%s", type(exc).__name__)
                return False
            claim = os.path.join(renamed, os.path.basename(claim))
            target = _recovered_name(path, recovered_id)
        try:
            os.link(claim, target, follow_symlinks=False)
        except FileExistsError:
            continue
        except OSError as exc:
            LOGGER.warning("processing_claim_kept error=%s", type(exc).__name__)
            return False
        if attempt:
            recorded = _record_linked(claim)
            if recorded is None:
                return True  # published; the claim stays for the next scan's recovery
            claim = recorded
        try:
            os.remove(claim)
        except OSError as exc:  # both names hold it; the next scan's recovery finds the claim again
            LOGGER.warning("processing_claim_unlink_failed error=%s", type(exc).__name__)
        else:
            _release(claim)
        return True
    LOGGER.warning("processing_claim_kept reason=names_taken")
    return False


def _record_linked(claim: str) -> Optional[str]:
    """Record on its directory that ``claim`` is linked at its recovered name; the claim's new path.

    None when the record fails: the link is left alone (an outside writer
    may have replaced it already) and so is the claim, which recovery
    recognises by its inode while that link holds it (:func:`_published`).
    """
    directory = os.path.dirname(claim)
    marked = f"{directory}-p"
    try:
        os.rename(directory, marked)
    except OSError as exc:
        LOGGER.warning("processing_claim_unrecorded error=%s", type(exc).__name__)
        return None
    return os.path.join(marked, os.path.basename(claim))


def recover_claims(inbox_dir: str, imported: dict[str, str]) -> set[str]:
    """Settle every claimed file a crash left in the inbox (:func:`_claim`).

    Runs under the inbox lock, so no claim of a running import or scan is
    among them. ``imported`` maps the ``rel_path`` of each ``imported`` row
    to its ``import_sha256``: a claim with the bytes an import of its path
    (or of the recovered name it was published under) ingested is removed, never published again (even when another file
    has taken the path); any other claim is put back (:func:`_put_back`).
    An empty claim directory (a crash before the rename) is removed.
    Returns the ``rel_path`` of each claimed file left unsettled (it could
    be neither removed nor put back), whose row the scan keeps. A claim
    directory that cannot be listed raises ``OSError``, failing the scan.

    Only an import claims a file, once its row is ``imported``, and a claim
    put back holds bytes that import did not ingest: they never had a row of
    their own. Put back under a recovered name, they are staged as new; a
    row at their old path names the file there now, and keeps its edits.

    Only a real directory is settled; anything else with a claim's name (a
    link, a device) is not one the protocol made, and is left alone. An
    entry in it that is no regular file (a directory a writer put at the
    path as it was claimed) is returned to the inbox unread (:func:`_restore`).
    """
    root = os.path.realpath(inbox_dir)
    unsettled: set[str] = set()
    if not os.path.isdir(root):
        return unsettled
    for directory, dirs, _names in os.walk(root, onerror=_unlisted):
        claims = [name for name in dirs if _CLAIM.fullmatch(name) is not None]
        dirs[:] = [name for name in dirs if _CLAIM.fullmatch(name) is None]
        for name in _record_found_links(directory, claims):
            _settle(inbox_dir, root, os.path.join(directory, name), imported, unsettled)
    return unsettled


def _record_found_links(directory: str, claims: list[str]) -> list[str]:
    """Record the links of claims in ``directory`` that their put-back made but did not record; their names now.

    A put-back whose record failed (:func:`_record_linked`) leaves the claim
    linked at its recovered name, which an import may then claim in turn.
    The name was free when the intent was recorded (:func:`_put_back`), so a
    claim of the same file under it holds that link: the publication is
    recorded (``-p``) before any claim is settled, so it is never lost when
    only some of them are. A record that fails raises ``OSError``, failing
    the scan.
    """
    held = _held_names(os.path.join(directory, name) for name in claims)
    names = []
    for name in claims:
        claim_dir = os.path.join(directory, name)
        recovered_id = _recovered_id(claim_dir)
        if recovered_id is not None and _recovered_id(claim_dir, linked=True) is None:
            for entry_name, key in _entries(claim_dir):
                if os.path.basename(_recovered_name(entry_name, recovered_id)) in held.get(key, ()):
                    recorded = _record_linked(os.path.join(claim_dir, entry_name))
                    if recorded is None:
                        raise OSError("processing claim link unrecorded")
                    name = os.path.basename(os.path.dirname(recorded))
                    break
        names.append(name)
    return names


def _entries(claim_dir: str) -> list[tuple[str, tuple[int, int]]]:
    """Each regular file in a claim directory with its identity (:func:`_record_found_links`).

    A claim directory or entry that cannot be inspected raises ``OSError``,
    failing the scan before any claim is settled: a link it holds, left out,
    would be missed. One that is gone, or is no directory, holds nothing.
    """
    try:
        if not stat.S_ISDIR(os.lstat(claim_dir).st_mode):
            return []
        names = os.listdir(claim_dir)
    except FileNotFoundError:
        return []
    found = []
    for name in names:
        try:
            entry = os.lstat(os.path.join(claim_dir, name))
        except FileNotFoundError:
            continue
        if stat.S_ISREG(entry.st_mode):
            found.append((name, (entry.st_dev, entry.st_ino)))
    return found


def _held_names(claim_dirs: Iterable[str]) -> dict[tuple[int, int], set[str]]:
    """The names the claims in ``claim_dirs`` hold, by file identity."""
    held: dict[tuple[int, int], set[str]] = {}
    for claim_dir in claim_dirs:
        for name, key in _entries(claim_dir):
            held.setdefault(key, set()).add(name)
    return held


def _settle(inbox_dir: str, root: str, claim_dir: str, imported: dict[str, str], unsettled: set[str]) -> None:
    """Settle one claim directory a crash left, adding to ``unsettled`` (:func:`recover_claims`)."""
    # A claim directory that cannot be read fails the scan: the files it may
    # hold are absent from the inbox, so reconciling would drop their rows.
    try:
        if not stat.S_ISDIR(os.lstat(claim_dir).st_mode):
            LOGGER.warning("processing_claim_skipped reason=not_a_directory")
            return
        names = os.listdir(claim_dir)
    except OSError as exc:
        LOGGER.warning("processing_claim_unreadable error=%s", type(exc).__name__)
        raise
    for name in names:
        claim, path = os.path.join(claim_dir, name), os.path.join(os.path.dirname(claim_dir), name)
        # Its own path and, once published under one, its recovered name: an
        # import of either may have ingested these bytes.
        rel_paths = _rel_paths(root, claim_dir, _public_names(claim_dir, name, linked=True))
        try:
            regular = stat.S_ISREG(os.lstat(claim).st_mode)
        except OSError:
            unsettled.update(rel_paths)
            continue
        if not regular:
            LOGGER.warning("processing_claim_restored reason=not_a_regular_file")
            if not _restore(claim, path):
                unsettled.update(rel_paths)
            continue
        expected = {imported[rel_path] for rel_path in rel_paths if rel_path in imported}
        if expected:
            # Published again only once a hash proves it is not the import's bytes.
            digest = file_sha256(claim)
            if digest is None or (digest in expected and not _remove(inbox_dir, claim, path)):
                unsettled.update(rel_paths)
                continue
            if digest in expected:
                continue
        if not _put_back(claim, path):
            unsettled.update(rel_paths)
    try:
        os.rmdir(claim_dir)
    except OSError:
        pass


def _rel_paths(root: str, claim_dir: str, names: Iterable[str]) -> list[str]:
    """The ``rel_path`` of each name beside ``claim_dir``."""
    parent = os.path.dirname(claim_dir)
    return [os.path.relpath(os.path.join(parent, name), root).replace(os.sep, "/") for name in names]


# -- scan -------------------------------------------------------------------------


class SharedInbox(RuntimeError):
    """The inbox is refused because more than one Library shares it (:data:`SHARED`)."""


def _refuse_shared(runner: Runner) -> None:
    """Fail closed while the instance has more than one Library.

    The inbox is one folder per instance (``StorageConfig.processing_dir``)
    while its rows and lock are per Library, so an editor of one Library
    could stage, and import, a file dropped for another. Until the inbox
    has an owner per Library (PG-C1, #311), neither scan nor import runs.
    """
    if runner.database.query(files.library_count) > 1:
        raise SharedInbox(SHARED)


def scan(runner: Runner, inbox_dir: str, new_id: Callable[[], str]) -> None:
    """Reconcile the Library's rows with the inbox, under the inbox lock (module docs)."""
    with inbox_lock(runner.database, runner.library_id):
        _refuse_shared(runner)
        rows = runner.read(files.scan_rows)
        unsettled = recover_claims(inbox_dir, {str(row["rel_path"]): str(row["import_sha256"]) for row in rows
                                               if row["status"] == files.IMPORTED})
        finished: list[str] = []
        for row in rows:
            if row["status"] == files.IMPORTED and row["rel_path"] not in unsettled and _finish_import(inbox_dir, row):
                finished.append(str(row["id"]))
        discovered = discover(inbox_dir)
        on_disk = {rel_path for rel_path, _name in discovered}
        unheld = [str(row["id"]) for row in rows
                  if row["status"] != files.IMPORTED and row["restore_hold"] and row["restore_sha256"]
                  and row["rel_path"] in on_disk and _hash_at(inbox_dir, row["rel_path"]) == row["restore_sha256"]]
        new_ids = [new_id() for _found in discovered]
        runner.command(lambda scope: files.reconcile(scope, discovered, finished, unheld, new_ids, unsettled))


def _hash_at(inbox_dir: str, rel_path: str) -> Optional[str]:
    path = resolve(inbox_dir, rel_path)
    return file_sha256(path) if path and os.path.isfile(path) else None


def _finish_import(inbox_dir: str, row: dict[str, Any]) -> bool:
    """Complete an import a crash interrupted; whether its row can go.

    The source is removed only while the file at the path has the imported
    bytes' hash; a different file there is left alone (and listed as new).
    A file that cannot be read or removed keeps the row and stays claimed,
    for the next scan's recovery to retry. The file is claimed (:func:`_claim`) before it is hashed, so
    a replacement arriving meanwhile is never the one removed.
    """
    path = resolve(inbox_dir, str(row["rel_path"]))
    if path is None:
        return True
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return True
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError as exc:  # present but unreadable: not proof the source is gone
        LOGGER.warning("processing_source_stat_failed error=%s", type(exc).__name__)
        return False
    try:
        claim = _claim(path)
    except OSError as exc:
        LOGGER.warning("processing_source_claim_failed error=%s", type(exc).__name__)
        return False
    if claim is None:
        return True
    digest = file_sha256(claim)
    # Bytes that may be the imported ones stay claimed when they cannot be
    # hashed or removed: the row is kept, and the next scan's recovery removes
    # them, republishing only bytes a readable hash proves different.
    if digest is None:
        return False
    if digest != row["import_sha256"]:
        return _put_back(claim, path)
    return _drop_stale_links(claim) and _remove(inbox_dir, claim, path)


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
        _refuse_shared(runner)
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
        try:
            present = bool(source) and stat.S_ISREG(os.stat(str(source)).st_mode)
        except (FileNotFoundError, NotADirectoryError):
            present = False
        except OSError as exc:  # present but unreadable: the row and its edits are kept
            # The reason only: the error's text would carry the inbox's absolute path.
            raise _refuse(runner, file_id, f"Could not read source PDF: {exc.strerror or type(exc).__name__}") from None
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
