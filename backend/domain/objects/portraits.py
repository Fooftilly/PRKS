"""Person portraits as immutable objects (§11.2; L-A-1 #635).

A Person's ``image_url`` is a profile field; the portrait is the image it
names, fetched and transcoded by :mod:`backend.person_image` (WebP, or JPEG
where WebP cannot be encoded) and cached under the ``portraits`` namespace.
On SQLite the cache is a file named by the Person and a hash of the URL,
rewritten in place. Here every cached portrait is a fresh key
(:func:`new_key`) that :func:`commit` adopts and names on the Person row
(``portrait_locator``, ``portrait_sha256``, ``portrait_byte_size``,
``portrait_source_url``), as an Asset names its PDF:

1. The caller checks the Person and the actor's ``EDIT_LIBRARY`` before it
   fetches, then stores the transcoded bytes with :func:`store`.
2. :func:`commit`, in one READ COMMITTED Library command, locks the Person
   row (``FOR NO KEY UPDATE``, the lock every profile edit takes), refuses
   when the Person is gone (``NOT_FOUND``) or its ``image_url`` is no longer
   the URL the bytes came from (``SOURCE_CHANGED``), adopts the key
   (``lifecycle.adopt``: the per-object lock, the size and the version the
   write reported), names it, and claims the key it supersedes.
3. A refused commit claims the new key in that command; a failure rolls
   back and leaves it to the orphan sweep.

:func:`forget` clears the reference and claims the key. It runs under the
Person row lock in the transaction that changes ``image_url``
(``persons.set_field``) or deletes the Person (``persons.delete_person``),
so a portrait never outlives the URL it was made from, and in
:func:`clear`. The schema refuses a reference whose ``portrait_source_url``
is not the Person's ``image_url``. No command deletes bytes: settle does,
after the three-valued check finds no Person of any Library naming the key.

A Person delete runs SERIALIZABLE and a commit updates the Person row, so
the two serialize: the delete either sees and claims the new key, or fails
and is retried, or runs first and the commit finds no Person.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import BinaryIO, LiteralString, Optional, Union

from backend.domain.identity.library_scope import LibraryScope
from backend.domain.objects import lifecycle
from backend.domain.objects.lifecycle import StoredObject
from backend.person_image import PortraitImage, identify_cached_portrait_subtype
from backend.storage.objects import NAMESPACE_PORTRAITS, StorageBackend, StorageKey, validate_key_name

NOT_FOUND = "ENTITY_NOT_FOUND"
SOURCE_CHANGED = "PORTRAIT_SOURCE_CHANGED"
SUFFIXES = {"webp": ".webp", "jpeg": ".jpg"}

_SOURCE: LiteralString = """
SELECT image_url, portrait_locator FROM prks.persons
WHERE library_id = %(library_id)s AND id = %(person_id)s
"""
_LOCK_SOURCE: LiteralString = _SOURCE + " FOR NO KEY UPDATE"
_PORTRAIT: LiteralString = """
SELECT portrait_locator, portrait_sha256, portrait_byte_size, portrait_source_url FROM prks.persons
WHERE library_id = %(library_id)s AND id = %(person_id)s
"""
_NAME: LiteralString = """
UPDATE prks.persons
SET portrait_locator = %(locator)s, portrait_sha256 = %(sha256)s, portrait_byte_size = %(size)s,
    portrait_source_url = %(source_url)s
WHERE library_id = %(library_id)s AND id = %(person_id)s
"""
_FORGET: LiteralString = """
UPDATE prks.persons
SET portrait_locator = NULL, portrait_sha256 = NULL, portrait_byte_size = NULL, portrait_source_url = NULL
WHERE library_id = %(library_id)s AND id = %(person_id)s
"""


@dataclass(frozen=True)
class Portrait:
    """The portrait a Person names: its key name, hash, size and the URL it was made from."""

    person_id: str
    locator: str
    sha256: str
    size: int
    source_url: str

    @property
    def key(self) -> StorageKey:
        return StorageKey(NAMESPACE_PORTRAITS, self.locator)


def new_key(subtype: str) -> StorageKey:
    """A fresh portrait key; the suffix says the encoding (``webp`` or ``jpeg``)."""
    return StorageKey(NAMESPACE_PORTRAITS, validate_key_name(uuid.uuid4().hex + SUFFIXES[subtype]))


def store(storage: StorageBackend, image: PortraitImage) -> StoredObject:
    """Write transcoded portrait bytes under a fresh key; nothing is named yet.

    Refuses bytes that are not the WebP or JPEG ``person_image`` produces
    (``ValueError``) before anything is written.
    """
    subtype = identify_cached_portrait_subtype(image.body)
    if subtype is None or subtype != image.subtype:
        raise ValueError("a portrait is WebP or JPEG bytes of the subtype it names")
    key = new_key(subtype)

    def write(stream: BinaryIO) -> None:
        stream.write(image.body)

    info = storage.put_new(key, write)
    if info.sha256 is None:  # pragma: no cover - put_new hashes by default
        raise RuntimeError("the stored portrait has no hash")
    return StoredObject(key, info.size, info.sha256, info.version)


def source(scope: LibraryScope, person_id: str) -> Optional[str]:
    """The Person's ``image_url`` ("" for none), or None when there is no such Person; reads only."""
    row = scope.execute(_SOURCE, {"person_id": person_id}).fetchone()
    return None if row is None else str(row["image_url"])


def current(scope: LibraryScope, person_id: str) -> Optional[Portrait]:
    """The portrait the Person names, or None (no Person, or none cached); reads only."""
    row = scope.execute(_PORTRAIT, {"person_id": person_id}).fetchone()
    if row is None or row["portrait_locator"] is None:
        return None
    return Portrait(person_id, str(row["portrait_locator"]), str(row["portrait_sha256"]),
                    int(row["portrait_byte_size"]), str(row["portrait_source_url"]))


def commit(scope: LibraryScope, storage: StorageBackend, person_id: str, source_url: str,
           obj: StoredObject) -> Union[Portrait, str]:
    """Name ``obj`` as the Person's portrait of ``source_url``; a refusal code otherwise.

    Runs in the caller's Library command. ``NOT_FOUND`` or
    ``SOURCE_CHANGED`` claim ``obj``'s key in that command. Raises
    ``lifecycle.ObjectMissing`` (the command rolls back) when the bytes are
    gone or not the ones written.
    """
    if obj.key.namespace != NAMESPACE_PORTRAITS:
        raise ValueError("a portrait lives in the portraits namespace")
    row = scope.execute(_LOCK_SOURCE, {"person_id": person_id}).fetchone()
    if row is None or not source_url or str(row["image_url"]) != source_url:
        lifecycle.release(scope, obj.key)
        return NOT_FOUND if row is None else SOURCE_CHANGED
    lifecycle.adopt(scope, storage, obj)
    scope.execute(_NAME, {"person_id": person_id, "locator": obj.key.name, "sha256": obj.sha256,
                          "size": obj.size, "source_url": source_url})
    old = row["portrait_locator"]
    if old is not None and old != obj.key.name:
        lifecycle.release(scope, StorageKey(NAMESPACE_PORTRAITS, str(old)))
    return Portrait(person_id, obj.key.name, obj.sha256, obj.size, source_url)


def forget(scope: LibraryScope, person_id: str) -> Optional[str]:
    """Clear the Person's portrait reference and claim its key; the key's name, or None.

    The caller holds the Person row lock and calls this before it changes
    ``image_url`` or deletes the row.
    """
    row = scope.execute(_PORTRAIT, {"person_id": person_id}).fetchone()
    if row is None or row["portrait_locator"] is None:
        return None
    old = str(row["portrait_locator"])
    scope.execute(_FORGET, {"person_id": person_id})
    lifecycle.release(scope, StorageKey(NAMESPACE_PORTRAITS, old))
    return old


def clear(scope: LibraryScope, person_id: str) -> Union[bool, str]:
    """Drop the Person's cached portrait, claiming its key: whether one was named, or ``NOT_FOUND``."""
    if scope.execute(_LOCK_SOURCE, {"person_id": person_id}).fetchone() is None:
        return NOT_FOUND
    return forget(scope, person_id) is not None
