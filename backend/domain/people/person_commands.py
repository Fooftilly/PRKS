"""The Person operations for one actor: what ``backend/api/persons.py`` calls (PG-C3 #642).

Persons, their profile fields and group set, their revision state and their
cached portrait. A read runs in one
:func:`~backend.domain.identity.library_scope.library_query` snapshot,
authorized for ``READ_LIBRARY`` (reads are not fenced by epoch); a write is
one :func:`~backend.domain.identity.library_scope.library_command`
transaction needing ``EDIT_LIBRARY``, fenced by maintenance and the epoch.
A profile edit naming a group set, and a delete, run SERIALIZABLE; the rest
run READ COMMITTED. Refusals are ``ValueError`` with SQLite's message, and
``AccessRefused``.

Portraits (:mod:`backend.domain.objects.portraits`) live in the Library's
managed objects: :func:`portrait` reads the cached one, :func:`cache_portrait`
fetches the Person's ``image_url`` and caches it (a write: ``EDIT_LIBRARY``,
a reader never writes, §9.4), and :func:`clear_portrait` drops it. A
profile edit or a delete never deletes a portrait's bytes: it drops the
reference and records a cleanup claim in the same transaction.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Optional, TypeVar

from backend.db.database import Database, Isolation
from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.objects import portraits
from backend.domain.people import persons, queries
from backend.domain.works.rules import finish_work_summary_rows
from backend.entity_ids import generate, is_distributed
from backend.person_image import PortraitImage, identify_cached_portrait_subtype
from backend.storage.objects import StorageBackend, StorageObjectError

T = TypeVar("T")

ID_PREFIX = "P"
PROFILE_FIELDS = frozenset({
    "first_name", "last_name", "aliases", "about", "image_url", "link_wikipedia",
    "link_stanford_encyclopedia", "link_iep", "links_other", "birth_date", "death_date",
})


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T], *,
             serializable: bool = False) -> T:
    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(db, ctx, Capability.EDIT_LIBRARY, fn, isolation=isolation)


# -- Reads ---------------------------------------------------------------------
def catalog(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    return library_query(db, ctx, queries.person_catalog)


def detail(db: Database, ctx: RequestContext, person_id: str,
           pdfs_dir: Optional[str]) -> Optional[dict[str, Any]]:
    """The Person with its Works and groups; ``None`` when absent.

    A Person with Works needs the data root (``pdfs_dir``) for their file
    sizes; ``RuntimeError`` when it has none.
    """
    person = library_query(db, ctx, lambda scope: queries.person_detail(scope, person_id))
    if person is None:
        return None
    if person["works"]:
        if pdfs_dir is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        finish_work_summary_rows(person["works"], pdfs_dir)
    return person


def metadata_state(db: Database, ctx: RequestContext, person_id: str) -> Optional[dict[str, Any]]:
    """The Person's field revisions; ``None`` when absent."""
    return library_query(db, ctx, lambda scope: persons.field_state(scope, person_id))


# -- Writes --------------------------------------------------------------------
def create(db: Database, ctx: RequestContext, fields: dict[str, Any],
           person_id: Optional[str] = None) -> str:
    """A new Person from the profile ``fields`` (absent ones are ``""``); its id."""
    if person_id is None:
        person_id = generate(ID_PREFIX)
    elif not is_distributed(person_id, ID_PREFIX):
        raise ValueError("invalid person id")
    canonical = persons.canonical_fields({name: fields.get(name, "") for name in sorted(PROFILE_FIELDS)})
    new_id = person_id
    _command(db, ctx, lambda scope: persons.create_person(scope, new_id, canonical))
    return new_id


def update_profile(db: Database, ctx: RequestContext, person_id: str, fields: dict[str, Any],
                   group_ids: Any = None) -> None:
    """Profile fields and, when ``group_ids`` is a list, the exact group set, atomically."""
    updates = {name: persons.canonical_field(name, value)
               for name, value in (fields or {}).items() if name in PROFILE_FIELDS}
    if group_ids is None:
        if updates:
            _command(db, ctx, lambda scope: persons.update_profile(scope, person_id, updates, None))
        return
    if not isinstance(group_ids, list):
        raise ValueError("group_ids must be a JSON array")
    clean: list[str] = []
    for group_id in group_ids:
        if not group_id or str(group_id) in clean:
            continue
        clean.append(str(group_id))

    def run(scope: LibraryScope) -> None:
        if not persons.update_profile(scope, person_id, updates, clean):
            raise ValueError("Person not found.")

    _command(db, ctx, run, serializable=True)


def delete_if_unlinked(db: Database, ctx: RequestContext, person_id: str) -> None:
    """Delete a Person no role names; refusals say "not found" or "linked works"."""
    pid = (person_id or "").strip()
    if not pid:
        raise ValueError("Person not found.")
    try:
        deleted, linked = _command(db, ctx, lambda scope: persons.delete_person(scope, pid),
                                   serializable=True)
    except ConstraintViolationError as error:
        if error.constraint != "roles_person_fkey":
            raise
        deleted, linked = False, 1
    if linked:
        raise ValueError("Cannot delete person with linked works.")
    if not deleted:
        raise ValueError("Person not found.")


# -- Portraits -----------------------------------------------------------------
def portrait(db: Database, ctx: RequestContext, objects: StorageBackend,
             person_id: str) -> Optional[dict[str, Any]]:
    """The cached portrait: its bytes, subtype, hash and size; ``None`` when none is cached.

    Raises ``LookupError`` when there is no such Person. A reference whose
    bytes are gone, or not the recorded size and hash, reads as ``None`` (the
    caller fetches again); the bytes are read with a bound, never whole.
    """
    def read(scope: LibraryScope) -> Any:
        if portraits.source(scope, person_id) is None:
            return portraits.NOT_FOUND
        return portraits.current(scope, person_id)

    found = library_query(db, ctx, read)
    if found == portraits.NOT_FOUND:
        raise LookupError(found)
    if not isinstance(found, portraits.Portrait):
        return None
    try:
        with objects.open_read(found.key) as handle:
            body = handle.read(found.size + 1)
    except (OSError, StorageObjectError):
        return None
    subtype = identify_cached_portrait_subtype(body)
    if len(body) != found.size or subtype is None or hashlib.sha256(body).hexdigest() != found.sha256:
        return None
    return {"body": body, "subtype": subtype, "content_sha256": found.sha256, "byte_size": found.size}


def cache_portrait(db: Database, ctx: RequestContext, objects: StorageBackend, person_id: str,
                   prepare: Callable[[str], Optional[PortraitImage]]) -> Optional[dict[str, Any]]:
    """Fetch the Person's ``image_url``, transcode it and make it the cached portrait.

    The Person and ``EDIT_LIBRARY`` are checked before anything is fetched or
    written; ``prepare`` (normally :func:`backend.person_image.fetch_and_prepare`)
    turns the URL into WebP or JPEG bytes; they go to a fresh key; one
    command names it under the Person row lock
    (:func:`backend.domain.objects.portraits.commit`). Returns what
    :func:`portrait` returns, or ``None`` when nothing was cached: no
    ``image_url``, a fetch or transcode that failed, or a URL changed while
    fetching (that write's key is claimed). Raises ``LookupError`` when there
    is no such Person.
    """
    url = library_query(db, ctx, lambda scope: portraits.source(scope, person_id),
                        capability=Capability.EDIT_LIBRARY)
    if url is None:
        raise LookupError(portraits.NOT_FOUND)
    if not url:
        return None
    image = prepare(url)
    if image is None:
        return None
    stored = portraits.store(objects, image)
    done = _command(db, ctx, lambda scope: portraits.commit(scope, objects, person_id, url, stored))
    if done == portraits.NOT_FOUND:
        raise LookupError(done)
    if not isinstance(done, portraits.Portrait):
        return None
    return {"body": image.body, "subtype": image.subtype, "content_sha256": done.sha256,
            "byte_size": done.size}


def clear_portrait(db: Database, ctx: RequestContext, person_id: str) -> bool:
    """Drop the cached portrait (its key is claimed); whether one was cached.

    Raises ``LookupError`` when there is no such Person.
    """
    cleared = _command(db, ctx, lambda scope: portraits.clear(scope, person_id))
    if cleared == portraits.NOT_FOUND:
        raise LookupError(cleared)
    return bool(cleared)
