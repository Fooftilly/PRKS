"""Saved copies of remote images, as immutable objects (#1351).

A video File's thumbnail (an Asset's ``thumb_url``) and a figure in a note
(Markdown ``![](https://...)`` in a Work's notes, or in the research text of a
Concept, Position or Argument) name images on other sites. PRKS serves a saved copy of each from its own
origin (``GET /api/remote-images?url=``), so the page loads no image from
another site (CSP ``img-src 'self'``), the remote site never learns who reads
the note or when, and the copy outlives the site that served it.

A copy is the image the URL names, fetched and transcoded like a portrait
(:func:`backend.person_image.fetch_and_prepare`, WebP or JPEG, at most
:data:`MAX_EDGE` pixels on its longer edge), stored under a fresh key in the
``remote-images`` namespace and named by one ``prks.remote_images`` row per
Library and source URL:

1. :func:`saved` reads the row and its bytes (``READ_LIBRARY``).
2. On a miss, :func:`save` checks that the Library itself names the URL
   (:func:`named`) and the actor's ``EDIT_LIBRARY`` before it fetches, so the
   route is no proxy for any URL a caller makes up, then stores the bytes
   with :func:`store`.
3. :func:`commit`, in one Library command, re-checks that the Library names
   the URL, adopts the key (``lifecycle.adopt``) and inserts the row. When a
   concurrent save inserted it first, the new key is claimed and the
   existing copy is returned; a refused commit claims the new key too. A
   row whose bytes are missing or damaged is repointed at the new key, and
   the damaged key is claimed, so a backup never needs bytes that are gone.

A reader never writes (§9.4): the route serves it a fetched copy uncached,
as it does when a restore fenced the fill. The row is kept when the Work or
note stops naming the URL. The schema owns the reference check
(``lifecycle.references``), backups (``domain/maintenance/tables.py``) and
restore's key list.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any, BinaryIO, Callable, LiteralString, Optional, Union
from urllib.parse import unquote

from backend.db.database import Database
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.objects import lifecycle
from backend.domain.objects.lifecycle import StoredObject
from backend.person_image import (
    PersonImageUrlError,
    PortraitImage,
    identify_cached_portrait_subtype,
    normalize_person_image_url,
)
from backend.storage.objects import (
    NAMESPACE_REMOTE_IMAGES,
    StorageBackend,
    StorageKey,
    ObjectNotFound,
    StorageObjectError,
    validate_key_name,
)

# The longer edge of a saved copy: enough for a figure across a wide note.
MAX_EDGE = 1600
# The schema's bound on ``source_url``.
MAX_URL_BYTES = 2048
NOT_NAMED = "REMOTE_IMAGE_NOT_NAMED"
SUFFIXES = {"webp": ".webp", "jpeg": ".jpg"}

# The Library names a URL when an Asset's thumbnail is that URL, or Markdown
# the Library renders contains it: a Work's notes, a Concept's or Position's
# description, an Argument's main text. ``strpos`` is a plain substring match.
# The Markdown renderer percent-encodes what the text left raw (a space,
# non-ASCII) and keeps the escapes it found, so a figure's text holds the
# URL, its decoded form, or the form with only non-ASCII decoded (text that
# mixes raw non-ASCII with escapes such as ``%20``).
_NAMED: LiteralString = """
SELECT EXISTS (SELECT 1 FROM prks.assets
               WHERE library_id = %(library_id)s AND thumb_url = %(url)s)
    OR EXISTS (SELECT 1 FROM prks.work_notes
               WHERE library_id = %(library_id)s
                 AND (strpos(body, %(url)s) > 0 OR strpos(body, %(decoded)s) > 0
                      OR strpos(body, %(mixed)s) > 0))
    OR EXISTS (SELECT 1 FROM prks.concepts
               WHERE library_id = %(library_id)s
                 AND (strpos(description, %(url)s) > 0 OR strpos(description, %(decoded)s) > 0
                      OR strpos(description, %(mixed)s) > 0))
    OR EXISTS (SELECT 1 FROM prks.positions
               WHERE library_id = %(library_id)s
                 AND (strpos(description, %(url)s) > 0 OR strpos(description, %(decoded)s) > 0
                      OR strpos(description, %(mixed)s) > 0))
    OR EXISTS (SELECT 1 FROM prks.arguments
               WHERE library_id = %(library_id)s
                 AND (strpos(main_text, %(url)s) > 0 OR strpos(main_text, %(decoded)s) > 0
                      OR strpos(main_text, %(mixed)s) > 0)) AS named
"""
_SAVED: LiteralString = """
SELECT locator, sha256, byte_size FROM prks.remote_images
WHERE library_id = %(library_id)s AND source_url = %(url)s
"""
_INSERT: LiteralString = """
INSERT INTO prks.remote_images (library_id, source_url, locator, sha256, byte_size)
VALUES (%(library_id)s, %(url)s, %(locator)s, %(sha256)s, %(size)s)
ON CONFLICT (library_id, source_url) DO NOTHING
RETURNING locator
"""
# Repoint a row whose bytes are missing or damaged; only while it still names them.
_REPLACE: LiteralString = """
UPDATE prks.remote_images
SET locator = %(locator)s, sha256 = %(sha256)s, byte_size = %(size)s, saved_at = now()
WHERE library_id = %(library_id)s AND source_url = %(url)s AND locator = %(replaces)s
RETURNING locator
"""
# A run of percent escapes of non-ASCII bytes (UTF-8 lead and continuation bytes).
_NON_ASCII_ESCAPES = re.compile(r"(?:%[89a-fA-F][0-9a-fA-F])+")


@dataclass(frozen=True)
class RemoteImage:
    """The saved copy of ``source_url``: its key name, hash and size."""

    source_url: str
    locator: str
    sha256: str
    size: int

    @property
    def key(self) -> StorageKey:
        return StorageKey(NAMESPACE_REMOTE_IMAGES, self.locator)


def normalize_url(value: Any) -> Optional[str]:
    """The source URL a copy is saved under, or None when PRKS never fetches it.

    A public ``http``/``https`` URL (the portrait rules: no credentials, no
    literal private or local address), at most :data:`MAX_URL_BYTES` bytes.
    """
    if not isinstance(value, str):
        return None
    try:
        url = normalize_person_image_url(value)
    except PersonImageUrlError:
        return None
    if not url or len(url.encode("utf-8")) > MAX_URL_BYTES:
        return None
    return url


def new_key(subtype: str) -> StorageKey:
    """A fresh key; the suffix says the encoding (``webp`` or ``jpeg``)."""
    return StorageKey(NAMESPACE_REMOTE_IMAGES, validate_key_name(uuid.uuid4().hex + SUFFIXES[subtype]))


def store(storage: StorageBackend, image: PortraitImage) -> StoredObject:
    """Write transcoded bytes under a fresh key; nothing is named yet.

    Refuses bytes that are not the WebP or JPEG ``person_image`` produces
    (``ValueError``) before anything is written.
    """
    subtype = identify_cached_portrait_subtype(image.body)
    if subtype is None or subtype != image.subtype:
        raise ValueError("a saved image is WebP or JPEG bytes of the subtype it names")
    key = new_key(subtype)

    def write(stream: BinaryIO) -> None:
        stream.write(image.body)

    info = storage.put_new(key, write)
    if info.sha256 is None:  # pragma: no cover - put_new hashes by default
        raise RuntimeError("the saved image has no hash")
    return StoredObject(key, info.size, info.sha256, info.version)


def named(scope: LibraryScope, url: str) -> bool:
    """Whether the Library's own content names ``url``; reads only."""
    mixed = _NON_ASCII_ESCAPES.sub(lambda match: unquote(match.group(0)), url)
    row = scope.execute(_NAMED, {"url": url, "decoded": unquote(url), "mixed": mixed}).fetchone()
    return bool(row is not None and row["named"])


def current(scope: LibraryScope, url: str) -> Optional[RemoteImage]:
    """The saved copy of ``url``, or None; reads only."""
    row = scope.execute(_SAVED, {"url": url}).fetchone()
    if row is None:
        return None
    return RemoteImage(url, str(row["locator"]), str(row["sha256"]), int(row["byte_size"]))


def commit(scope: LibraryScope, storage: StorageBackend, url: str,
           obj: StoredObject, replaces: Optional[str] = None) -> Union[RemoteImage, str]:
    """Name ``obj`` as the saved copy of ``url``; the copy, or :data:`NOT_NAMED`.

    Runs in the caller's Library command. A refusal, or a copy another save
    committed first, claims ``obj``'s key in that command. ``replaces`` is
    the locator of a copy the caller found missing or damaged: while the row
    still names it, the row is repointed at ``obj`` and that key is claimed.
    Raises
    ``lifecycle.ObjectMissing`` (the command rolls back) when the bytes are
    gone or not the ones written.
    """
    if obj.key.namespace != NAMESPACE_REMOTE_IMAGES:
        raise ValueError("a saved image lives in the remote-images namespace")
    if not named(scope, url):
        lifecycle.release(scope, obj.key)
        return NOT_NAMED
    lifecycle.adopt(scope, storage, obj)
    inserted = scope.execute(_INSERT, {"url": url, "locator": obj.key.name, "sha256": obj.sha256,
                                       "size": obj.size}).fetchone()
    if inserted is None and replaces is not None:
        params = {"url": url, "locator": obj.key.name, "sha256": obj.sha256, "size": obj.size,
                  "replaces": replaces}
        if scope.execute(_REPLACE, params).fetchone() is not None:
            lifecycle.release(scope, StorageKey(NAMESPACE_REMOTE_IMAGES, replaces))
            return RemoteImage(url, obj.key.name, obj.sha256, obj.size)
    if inserted is None:
        lifecycle.release(scope, obj.key)
        existing = current(scope, url)
        if existing is None:  # pragma: no cover - the conflicting row is visible once it blocks us
            raise RuntimeError("the saved image vanished")
        return existing
    return RemoteImage(url, obj.key.name, obj.sha256, obj.size)


# -- the route's commands ------------------------------------------------------


LOST = "lost"
UNREADABLE = "unreadable"


def _inspect(objects: StorageBackend, found: RemoteImage) -> Union[dict[str, Any], str]:
    """The copy's bytes when they are the recorded size and hash; read with a bound, never whole.

    Otherwise :data:`LOST` when the key is gone or its bytes are proven
    wrong, and :data:`UNREADABLE` when reading failed for another reason (a
    permission, an I/O error, a link): those bytes may still be the copy.
    """
    try:
        with objects.open_read(found.key) as handle:
            body = handle.read(found.size + 1)
    except ObjectNotFound:
        return LOST
    except (OSError, StorageObjectError):
        return UNREADABLE
    subtype = identify_cached_portrait_subtype(body)
    if len(body) != found.size or subtype is None or hashlib.sha256(body).hexdigest() != found.sha256:
        return LOST
    return {"body": body, "subtype": subtype, "sha256": found.sha256}


def _read(objects: StorageBackend, found: RemoteImage) -> Optional[dict[str, Any]]:
    copy = _inspect(objects, found)
    return copy if isinstance(copy, dict) else None


def saved(db: Database, ctx: RequestContext, objects: StorageBackend, url: str) -> Optional[dict[str, Any]]:
    """The saved copy of ``url``: its bytes and subtype; None when there is none (or it is damaged)."""
    found = library_query(db, ctx, lambda scope: current(scope, url))
    return None if found is None else _read(objects, found)


def is_named(db: Database, ctx: RequestContext, url: str) -> bool:
    """Whether the Library names ``url`` (``READ_LIBRARY``)."""
    return library_query(db, ctx, lambda scope: named(scope, url))


def save(db: Database, ctx: RequestContext, objects: StorageBackend, url: str,
         prepare: Callable[[str], Optional[PortraitImage]]) -> Optional[dict[str, Any]]:
    """Fetch ``url``, transcode it and save it as the Library's copy.

    The Library must name the URL and the actor needs ``EDIT_LIBRARY``, both
    checked before anything is fetched or written. Returns what :func:`saved`
    returns, or None when nothing was saved: the URL is not named, or the
    fetch or transcode failed. A saved copy that is gone or proven damaged
    is replaced; one that could not be read for another reason is kept, and
    the fetched bytes are returned without a ``sha256`` (not saved).
    """
    def look(scope: LibraryScope) -> tuple[bool, Optional[RemoteImage]]:
        return named(scope, url), current(scope, url)

    is_named_here, found = library_query(db, ctx, look, capability=Capability.EDIT_LIBRARY)
    if not is_named_here:
        return None
    replaces = None
    if found is not None:
        # Saved since the caller looked, or saved and since lost or damaged.
        copy = _inspect(objects, found)
        if isinstance(copy, dict):
            return copy
        replaces = found.locator if copy == LOST else None
    image = prepare(url)
    if image is None:
        return None
    if found is not None and replaces is None:
        # The saved bytes may be intact but unreadable now: keep them and
        # serve this fetch uncached (no hash).
        return {"body": image.body, "subtype": image.subtype}
    stored = store(objects, image)
    done = library_command(db, ctx, Capability.EDIT_LIBRARY,
                           lambda scope: commit(scope, objects, url, stored, replaces))
    if not isinstance(done, RemoteImage):
        return None
    if done.locator != stored.key.name:
        return _read(objects, done)
    return {"body": image.body, "subtype": image.subtype, "sha256": stored.sha256}
