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

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.works import revisions

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
INSERT INTO prks.assets (library_id, id, manifestation_id, work_id, kind, storage_locator, provider,
                         provider_id, url, media_type, byte_size, ingest_sha256, content_sha256,
                         origin, thumb_page, thumb_url)
VALUES (%(library_id)s, %(asset_id)s, %(manifestation_id)s, %(work_id)s, %(kind)s, %(storage_locator)s,
        %(provider)s, %(provider_id)s, %(url)s, %(media_type)s, %(byte_size)s, %(sha256)s, %(sha256)s,
        %(origin)s, %(thumb_page)s, %(thumb_url)s)
"""
# One data root serves every Library of the instance (§6.2), so a storage key
# is not private to a Library by its name alone. Until L-A-1's rule that a
# transaction adopts only keys it wrote (§11.2), create refuses a key another
# Library's Asset already names. The read takes SERIALIZABLE predicate locks,
# so two Libraries racing to claim one key cannot both commit.
_LOCATOR_OF_ANOTHER_LIBRARY: LiteralString = """
SELECT 1 FROM prks.assets
WHERE storage_locator = %(locator)s AND library_id <> %(library_id)s
LIMIT 1
"""
_INSERT_NOTE: LiteralString = """
INSERT INTO prks.work_notes (library_id, work_id, kind, body)
VALUES (%(library_id)s, %(work_id)s, %(kind)s, %(body)s)
"""


@dataclass(frozen=True)
class ManagedFile:
    """Bytes already stored under the managed key ``storage_locator``."""

    storage_locator: str
    sha256: str
    byte_size: int
    origin: str = "upload"


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


def create_work(scope: LibraryScope, new: NewWork) -> str:
    """Insert ``new``; returns its id. Safe to run again on a SERIALIZABLE retry."""
    if (new.asset is None) != (new.asset_id is None):
        raise ValueError("an Asset needs an id, and an id needs an Asset")
    unknown = set(new.bibliographic) - set(BIBLIOGRAPHIC_FIELDS)
    if unknown:
        raise ValueError("not bibliographic fields: " + ", ".join(sorted(unknown)))
    ids = {"work_id": new.work_id, "manifestation_id": new.manifestation_id, "asset_id": new.asset_id}
    scope.execute(_INSERT_WORK, {
        **ids, "title": new.title, "abstract": new.abstract, "status": new.status,
        "author_text": new.author_text,
    })
    scope.execute(_INSERT_MANIFESTATION, {
        **ids, **{name: new.bibliographic.get(name) for name in BIBLIOGRAPHIC_FIELDS},
        "url": new.citation_url,
    })
    if new.asset is not None:
        _insert_asset(scope, ids, new.asset)
    for kind, body in (("research", new.research_note), ("private", new.private_note)):
        if body:
            scope.execute(_INSERT_NOTE, {"work_id": new.work_id, "kind": kind, "body": body})
    return new.work_id


def _insert_asset(scope: LibraryScope, ids: dict[str, Optional[str]], asset: NewAsset) -> None:
    source = asset.source
    values: dict[str, object] = {
        **ids, "media_type": asset.media_type, "thumb_page": asset.thumb_page,
        "thumb_url": asset.thumb_url,
    }
    if isinstance(source, ManagedFile):
        if scope.execute(_LOCATOR_OF_ANOTHER_LIBRARY, {"locator": source.storage_locator}).fetchone():
            # Worded like a missing file: the name of another Library's file is not confirmed.
            raise ValueError("the managed PDF does not exist")
        values.update(kind="managed_file", storage_locator=source.storage_locator, provider=None,
                      provider_id=None, url=None, byte_size=source.byte_size, sha256=source.sha256,
                      origin=source.origin)
    else:
        values.update(kind="external_stream", storage_locator=None, provider=source.provider,
                      provider_id=source.provider_id, url=source.url, byte_size=None, sha256=None,
                      origin=None)
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

_LOCK_FOR_DELETE: LiteralString = """
SELECT w.id, a.kind AS asset_kind, a.storage_locator
FROM prks.works w
JOIN prks.manifestations m
  ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id AND m.work_id = w.id
LEFT JOIN prks.assets a
  ON a.library_id = m.library_id AND a.id = m.primary_asset_id AND a.manifestation_id = m.id
WHERE w.library_id = %(library_id)s AND w.id = %(work_id)s
FOR UPDATE OF w
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


def delete_work(scope: LibraryScope, work_id: str) -> Optional[DeletedWork]:
    """Delete a Work and everything it owns; None when there is no such Work.

    The Work row is locked first (the order every Works command uses), its
    notes, Assets, Manifestations and the Work are deleted level by level
    (relations and identifiers go with their Manifestation), and every
    revision scope of the Work is tombstoned (§5.2).
    The managed file stays where it is: claiming and removing bytes is the
    objects lane's (L-A-1, §11.2), so this only reports the Work's
    ``/api/pdfs/`` path and whether another Asset of the Library still
    names the same storage key.
    """
    row = scope.execute(_LOCK_FOR_DELETE, {"work_id": work_id}).fetchone()
    if row is None:
        return None
    locator = row["storage_locator"] if row["asset_kind"] == "managed_file" else None
    for statement in _DELETE_WORK_LEVELS:
        scope.execute(statement, {"work_id": work_id})
    revisions.tombstone_work_scopes(scope, work_id)
    still_used = locator is not None and scope.execute(
        _LOCATOR_STILL_USED, {"locator": locator}).fetchone() is not None
    return DeletedWork(work_id=work_id,
                       file_path="/api/pdfs/" + str(locator) if locator else "",
                       managed_pdf_still_referenced=still_used)
