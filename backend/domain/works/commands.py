"""Work create, open and delete on PostgreSQL (L-W-1 #628).

Fields, notes and the source have their own modules (``metadata``,
``notes``, ``source``).

Each command runs inside one
:func:`~backend.domain.identity.library_scope.library_command` transaction (the
caller's), after authorization and the epoch check, and writes only rows of
the scope's Library.

**Create** inserts the Work, its primary Manifestation and, for a source,
the Manifestation's primary Asset in one transaction. The Work's and the
Manifestation's pointers name rows inserted later in the same transaction;
the deferred keys check them at COMMIT. It runs SERIALIZABLE
(``docs/postgresql-architecture.md`` §7.1: creating a Manifestation is a
peer-table write a Work merge must see), so the caller passes fixed ids and
the function is safe to run again on a retry. Construction is not a
mutation of any revision scope: a new Work starts at revision 0 everywhere,
so create writes no revision rows (as on SQLite).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import LiteralString, Optional

from backend import folder_sync, playlist_sync, work_metadata_sync, work_note_sync, work_source_sync
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.objects import lifecycle
from backend.domain.organization import folders, playlists
from backend.domain.people import roles
from backend.domain.works import metadata, revisions
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, StorageBackend, StorageKey

_INSERT_WORK: LiteralString = """
INSERT INTO prks.works (library_id, id, title, abstract, status, author_text, primary_manifestation_id)
VALUES (%(library_id)s, %(work_id)s, %(title)s, %(abstract)s, %(status)s, %(author_text)s,
        %(manifestation_id)s)
"""
_INSERT_MANIFESTATION: LiteralString = """
INSERT INTO prks.manifestations (library_id, id, work_id, doc_type, year, published_date, edition,
                                 publisher, location, journal, volume, issue, pages, isbn, doi, url,
                                 urldate, primary_asset_id)
VALUES (%(library_id)s, %(manifestation_id)s, %(work_id)s, %(doc_type)s, %(year)s, %(published_date)s,
        %(edition)s, %(publisher)s, %(location)s, %(journal)s, %(volume)s, %(issue)s, %(pages)s,
        %(isbn)s, %(doi)s, %(url)s, %(urldate)s, %(asset_id)s)
"""
_INSERT_ASSET: LiteralString = """
INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, storage_locator,
                         source_locator, provider, provider_id, url, media_type, byte_size,
                         ingest_sha256, content_sha256, origin, thumb_page, thumb_url)
VALUES (%(library_id)s, %(asset_id)s, %(manifestation_id)s, %(work_id)s, %(kind)s, %(storage_locator)s,
        %(source_locator)s, %(provider)s, %(provider_id)s, %(url)s, %(media_type)s, %(byte_size)s,
        %(ingest_sha256)s, %(sha256)s, %(origin)s, %(thumb_page)s, %(thumb_url)s)
"""
_INSERT_NOTE: LiteralString = """
INSERT INTO prks.work_notes (library_id, work_id, kind, body)
VALUES (%(library_id)s, %(work_id)s, %(kind)s, %(body)s)
"""


@dataclass(frozen=True)
class ManagedFile:
    """Bytes already stored under the managed key ``storage_locator``.

    An ingest (``objects.ingest.IngestedPdf.managed_file``) also passes the
    write's ``version`` token, so adoption refuses bytes replaced since, and,
    when it linearized into a second key, the pristine ``source`` it keeps
    (``source_locator``) and the raw stream's ``ingest_sha256``.
    """

    storage_locator: str
    sha256: str
    byte_size: int
    origin: str = "upload"
    version: Optional[str] = None
    source: Optional[lifecycle.StoredObject] = None
    # SHA-256 of the bytes as received; the working file's own when nothing rewrote them.
    ingest_sha256: Optional[str] = None


@dataclass(frozen=True)
class Stream:
    """A canonical external stream (``SET_WORK_SOURCE`` identity)."""

    url: str
    provider: Optional[str]
    provider_id: Optional[str]


@dataclass(frozen=True)
class NewAsset:
    source: ManagedFile | Stream
    media_type: Optional[str] = None
    thumb_page: Optional[int] = None
    thumb_url: Optional[str] = None


@dataclass(frozen=True)
class NewWork:
    """A Work as ``add_work`` creates it; the caller has validated the source."""

    work_id: str
    manifestation_id: str
    title: str
    status: str = "Not Started"
    abstract: Optional[str] = ""
    author_text: Optional[str] = ""
    # Bibliographic fields of the primary Manifestation, stored as given.
    bibliographic: dict[str, Optional[str]] = field(default_factory=dict[str, Optional[str]])
    citation_url: Optional[str] = None
    asset_id: Optional[str] = None
    asset: Optional[NewAsset] = None
    research_note: Optional[str] = None
    private_note: Optional[str] = None


BIBLIOGRAPHIC_FIELDS: tuple[str, ...] = (
    "doc_type", "year", "published_date", "edition", "publisher", "location", "journal",
    "volume", "issue", "pages", "isbn", "doi", "urldate",
)


_INSERT_WORK_IF_ABSENT: LiteralString = """
INSERT INTO prks.works (library_id, id, title, abstract, status, author_text, primary_manifestation_id)
VALUES (%(library_id)s, %(work_id)s, %(title)s, %(abstract)s, %(status)s, %(author_text)s,
        %(manifestation_id)s)
ON CONFLICT DO NOTHING
RETURNING id
"""


def create_work(scope: LibraryScope, new: NewWork, *, if_absent: bool = False,
                objects: Optional[StorageBackend] = None) -> Optional[str]:
    """Insert ``new``; returns its id. Safe to run again on a SERIALIZABLE retry.

    A managed file's key is adopted first (``objects.lifecycle.adopt``, §11.2),
    so ``objects`` is required for one: the key's per-object lock is held to
    COMMIT and the bytes are confirmed there, so a cleanup settle cannot
    unlink them under the new Asset. A key another Library names is refused
    (``ObjectMissing``), as is one whose bytes are gone.

    ``if_absent`` is for a client-minted id (``CREATE_WORK``): when a Work
    with that id already exists nothing is inserted and None is returned.
    A Work a concurrent transaction committed is not visible to this
    SERIALIZABLE one, so PostgreSQL refuses the insert as a serialization
    failure, and the retry sees the Work. Every unique index arbitrates
    (no conflict target): with ``(id)`` alone, a race lost on
    ``works_library_id_id_key`` raised a unique violation instead.
    """
    if (new.asset is None) != (new.asset_id is None):
        raise ValueError("an Asset needs an id, and an id needs an Asset")
    unknown = set(new.bibliographic) - set(BIBLIOGRAPHIC_FIELDS)
    if unknown:
        raise ValueError("not bibliographic fields: " + ", ".join(sorted(unknown)))
    ids = {"work_id": new.work_id, "manifestation_id": new.manifestation_id, "asset_id": new.asset_id}
    work_row = {**ids, "title": new.title, "abstract": new.abstract, "status": new.status,
                "author_text": new.author_text}
    if if_absent:
        if scope.execute(_INSERT_WORK_IF_ABSENT, work_row).fetchone() is None:
            return None
    else:
        scope.execute(_INSERT_WORK, work_row)
    scope.execute(_INSERT_MANIFESTATION, {
        **ids, **{name: new.bibliographic.get(name) for name in BIBLIOGRAPHIC_FIELDS},
        "url": new.citation_url,
    })
    if new.asset is not None:
        _insert_asset(scope, ids, new.asset, objects)
    if new.research_note:
        # As every Research Note writer does (``notes``): the Concepts it
        # names are created when missing, and a Concept or Argument delete
        # overlapping this create then sees the note's references. (SQLite's
        # ``add_work`` stores the note without creating them.)
        from backend.domain.research import arguments, concepts, reindex

        concepts.ensure_referenced(scope, new.research_note)
        arguments.share_referenced(scope, new.research_note)
        reindex.enqueue(scope, new.work_id)
    for kind, body in (("research", new.research_note), ("private", new.private_note)):
        if body:
            scope.execute(_INSERT_NOTE, {"work_id": new.work_id, "kind": kind, "body": body})
    return new.work_id


def _insert_asset(scope: LibraryScope, ids: dict[str, Optional[str]], asset: NewAsset,
                  objects: Optional[StorageBackend]) -> None:
    source = asset.source
    values: dict[str, object] = {
        **ids, "media_type": asset.media_type, "thumb_page": asset.thumb_page,
        "thumb_url": asset.thumb_url,
    }
    if isinstance(source, ManagedFile):
        if objects is None:
            raise ValueError("a managed file is adopted through the storage backend")
        working = lifecycle.StoredObject(StorageKey(NAMESPACE_ASSET_OBJECTS, source.storage_locator),
                                         source.byte_size, source.sha256, source.version)
        stored = [working]
        if source.source is not None:
            if source.source.key.namespace != NAMESPACE_ASSET_OBJECTS or source.source.key == working.key:
                raise ValueError("a kept source is another asset object")
            stored.append(source.source)
        # Per-object locks in key order, as every multi-key writer takes them.
        for obj in sorted(stored, key=lambda found: str(found.key)):
            lifecycle.adopt(scope, objects, obj)
        values.update(kind="managed_file", storage_locator=source.storage_locator,
                      source_locator=source.source.key.name if source.source is not None else None,
                      provider=None, provider_id=None, url=None, byte_size=source.byte_size,
                      sha256=source.sha256, ingest_sha256=source.ingest_sha256 or source.sha256,
                      origin=source.origin)
    else:
        values.update(kind="external_stream", storage_locator=None, source_locator=None,
                      provider=source.provider, provider_id=source.provider_id, url=source.url,
                      byte_size=None, sha256=None, ingest_sha256=None, origin=None)
    scope.execute(_INSERT_ASSET, values)


# -- open events ----------------------------------------------------------------

_LOCK_OPENED: LiteralString = """
SELECT to_char(last_opened_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.MS') AS opened,
       date_trunc('milliseconds', last_opened_at) AS opened_at
FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s
FOR NO KEY UPDATE
"""
_SET_OPENED: LiteralString = """
UPDATE prks.works SET last_opened_at = %(moment)s
WHERE library_id = %(library_id)s AND id = %(work_id)s
RETURNING to_char(last_opened_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.MS') AS opened
"""


def mark_opened(scope: LibraryScope, work_id: str,
                moment: datetime) -> tuple[bool, bool, Optional[str]]:
    """``work_open_sync.set_opened_at``: a max-register at millisecond precision.

    Returns ``(work_exists, changed, effective_value)``, the value in
    SQLite's text form. An event at or before the recorded one changes
    nothing; the open is not a revisioned scope, so nothing else advances.
    """
    if moment.tzinfo is None:
        raise ValueError("an open moment needs a time zone")
    moment = moment.astimezone(timezone.utc).replace(
        microsecond=moment.microsecond // 1000 * 1000)
    row = scope.execute(_LOCK_OPENED, {"work_id": work_id}).fetchone()
    if row is None:
        return False, False, None
    if row["opened_at"] is not None and row["opened_at"] >= moment:
        return True, False, row["opened"]
    after = scope.execute(_SET_OPENED, {"work_id": work_id, "moment": moment}).fetchone()
    return True, True, None if after is None else after["opened"]


# -- delete ---------------------------------------------------------------------

_LOCK_FOR_DELETE: LiteralString = (
    "SELECT id FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s FOR UPDATE"
)
# Read after the lock, in its own statement, so the Asset is the committed one
# (see ``metadata.target``).
_PRIMARY_ASSET: LiteralString = """
SELECT a.kind AS asset_kind, a.storage_locator
FROM prks.works w
JOIN prks.manifestations m
  ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id AND m.work_id = w.id
LEFT JOIN prks.assets a
  ON a.library_id = m.library_id AND a.id = m.primary_asset_id AND a.manifestation_id = m.id
WHERE w.library_id = %(library_id)s AND w.id = %(work_id)s
"""
# Rows that own revision scopes never cascade (§5.2), so the delete removes
# each level itself, children first; relations and identifiers go with their
# Manifestation. The pointer keys are deferred, so the order within the
# Work's rows does not matter to them.
_DELETE_WORK_LEVELS: tuple[LiteralString, ...] = (
    "DELETE FROM prks.work_notes WHERE library_id = %(library_id)s AND work_id = %(work_id)s",
    "DELETE FROM prks.assets WHERE library_id = %(library_id)s AND work_id = %(work_id)s",
    "DELETE FROM prks.manifestations WHERE library_id = %(library_id)s AND work_id = %(work_id)s",
    "DELETE FROM prks.works WHERE library_id = %(library_id)s AND id = %(work_id)s",
)
# Every key the Work's Assets name, working and pristine, read before they go.
_WORK_LOCATORS: LiteralString = """
SELECT storage_locator AS name FROM prks.assets
WHERE library_id = %(library_id)s AND work_id = %(work_id)s AND storage_locator IS NOT NULL
UNION
SELECT source_locator FROM prks.assets
WHERE library_id = %(library_id)s AND work_id = %(work_id)s AND source_locator IS NOT NULL
ORDER BY 1
"""
_LOCATOR_STILL_USED: LiteralString = """
SELECT 1 FROM prks.assets
WHERE library_id = %(library_id)s AND storage_locator = %(locator)s
LIMIT 1
"""


@dataclass(frozen=True)
class DeletedWork:
    """What a delete removed, as ``DeletedWorkRecord`` reports it."""

    work_id: str
    file_path: str
    managed_pdf_still_referenced: bool


def work_scopes(work_id: str) -> list[tuple[str, str]]:
    """Every fixed revision scope of a Work: each synchronized field, the source, both notes, its Folder, its Playlist.

    Pair scopes (a Work's roles) are open-ended, so a delete tombstones the
    ones the Work ever held (``tombstone_work_scopes``), as a Person delete
    does for its pairs.
    """
    fields = [(metadata.SCOPE_TYPE, metadata.scope_id(work_id, field))
              for field in sorted(work_metadata_sync.SYNCED_FIELDS)]
    return [*fields,
            (work_source_sync.SCOPE_TYPE, work_source_sync.scope_key(work_id)),
            (work_note_sync.RESEARCH_SCOPE_TYPE, work_id),
            (work_note_sync.PRIVATE_SCOPE_TYPE, work_id),
            (folder_sync.WORK_SCOPE_TYPE, work_id),
            (playlist_sync.WORK_SCOPE_TYPE, work_id)]


def delete_work(scope: LibraryScope, work_id: str, *, claim_files: bool = True) -> Optional[DeletedWork]:
    """Delete a Work and everything it owns; None when there is no such Work.

    The Work row is locked first (the order every Works command uses), the
    Work leaves its Folder and its Playlist (``folders.release_work``,
    ``playlists.release_work``, which advances the Playlist's order), its roles
    (``roles.drop_work_roles``, which tombstones their Manifestation scopes),
    its citations (``arguments.release_work``, which advances each citing
    Argument's ``argument-sources``), notes, Assets, Manifestations and the Work are deleted level by level
    (relations and identifiers go with their Manifestation), and every
    revision scope of the Work is tombstoned (§5.2): each one this Work ever
    advanced, and every fixed scope (``work_scopes``) even if never edited, so
    a base taken before the delete is stale on a Work recreated under the id.
    A ``research.reindex_work`` enqueued here drops the Work's derived
    mentions once the delete commits (§8.4).
    Every stored key of the Work's Assets (working and pristine) that no
    other Asset of the Library names gets a cleanup claim in this
    transaction (``objects.lifecycle.release``, §11.2); the bytes are
    removed later, by a settle that finds nothing naming them.
    ``claim_files=False`` (undoing a create that adopted existing bytes,
    ``work_deletion.delete_work(keep_managed_pdf=True)``) records none. The
    record reports the Work's ``/api/pdfs/`` path and whether another Asset
    of the Library still names the same storage key.
    """
    if scope.execute(_LOCK_FOR_DELETE, {"work_id": work_id}).fetchone() is None:
        return None
    row = scope.execute(_PRIMARY_ASSET, {"work_id": work_id}).fetchone()
    if row is None:
        return None
    locator = row["storage_locator"] if row["asset_kind"] == "managed_file" else None
    stored = [str(found["name"]) for found in scope.execute(_WORK_LOCATORS, {"work_id": work_id}).fetchall()]
    # Filing is construction (no `work-folder` or `work-playlist` row need
    # exist), so the Folder and Playlist rows go here and the scopes are
    # tombstoned with the fixed ones below.
    folders.release_work(scope, work_id)
    playlists.release_work(scope, work_id)
    # Roles first: a Manifestation-scoped role holds its Manifestation (§5.2).
    roles.drop_work_roles(scope, work_id)
    # Citations too (a pinned one holds its Manifestation), advancing each
    # citing Argument's list.
    from backend.domain.research import arguments, reindex

    arguments.release_work(scope, work_id)
    for statement in _DELETE_WORK_LEVELS:
        scope.execute(statement, {"work_id": work_id})
    # Its reindex runs after this COMMIT and removes whatever an earlier one
    # wrote (§8.4).
    reindex.enqueue(scope, work_id)
    # A scope no edit ever touched has no row and reads as revision 0, the
    # same base a recreated Work starts from; ensuring the rows first makes
    # the tombstone advance it too, exactly once with the rest (#790).
    revisions.lock_many(scope, work_scopes(work_id))
    revisions.tombstone_work_scopes(scope, work_id)
    still_used = locator is not None and scope.execute(
        _LOCATOR_STILL_USED, {"locator": locator}).fetchone() is not None
    if claim_files:
        for name in stored:
            lifecycle.release(scope, StorageKey(NAMESPACE_ASSET_OBJECTS, name))
    return DeletedWork(work_id=work_id,
                       file_path="/api/pdfs/" + str(locator) if locator else "",
                       managed_pdf_still_referenced=still_used)
