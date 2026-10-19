"""Work commands on PostgreSQL (L-W-1 #628).

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
from typing import LiteralString, Optional

from backend.domain.identity.library_scope import LibraryScope

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
