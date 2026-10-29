"""Managed-PDF replacement on PostgreSQL (§11.2 "Rewrite"; L-A-1 #635).

A replacement puts a different document in place of a Work's primary
managed PDF. Its bytes are an upload like any other: the caller stores them
with ``objects.ingest.store_pdf`` (the raw stream under one fresh key, its
``ingest_sha256``; a linearized copy under a second, its ``content_sha256``)
and no object is modified in place. :func:`commit` then, in one READ
COMMITTED Library command:

1. locks the Work ``FOR UPDATE`` and its primary managed-PDF Asset
   ``FOR UPDATE`` (``annotations.store.lock_for_rewrite``, the order
   materialization takes);
2. adopts the new keys in key order (``lifecycle.adopt``: the per-object
   lock, and the size and version the write reported);
3. switches ``storage_locator``, ``source_locator``, ``content_sha256``,
   ``ingest_sha256`` and ``byte_size`` to the new document: the upload is
   the new pristine source, kept as ``source_locator`` only when it differs
   from the working key, as at ingest;
4. records cleanup claims for the old working and source keys
   (``lifecycle.release``), each only when no row of the Library still names
   it, so another Asset's key is never claimed.

A Work that is gone or has no managed PDF is refused after the locks, and
the new keys are claimed in that command. A failure rolls everything back
and leaves the new keys as orphans for the sweep.

**Annotations.** The annotation rows are the Asset's and stay. The new
bytes embed none of them, so the Asset's ``canonical_annotation_set_revision``
advances (a claim made against the old document, by an in-flight
materialization or a save-confirm, is stale from then on) and
``materialized_pdf_annotation_revision`` says what the new bytes hold: the
new canonical revision when the Asset has no annotations (the empty set is
trivially embedded), else 0, so the viewer sees the bytes as stale and
materializes the set into the new document.

**Mentioned roles.** :func:`mention` is SQLite's best-effort
``_sync_mentioned_roles``: a ``[[label]]`` in the bytes names the first
Person whose full or last name equals it, and that Person is linked to the
Work as ``Mentioned`` unless it already holds the role.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, LiteralString, Optional, Union

from backend.domain.annotations import store as annotation_store
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.objects import lifecycle
from backend.domain.objects.ingest import IngestedPdf
from backend.domain.people import roles
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, StorageBackend, StorageKey

NOT_FOUND = "ENTITY_NOT_FOUND"
MENTIONED = "Mentioned"

_LOCATORS: LiteralString = """
SELECT storage_locator, source_locator FROM prks.assets
WHERE library_id = %(library_id)s AND id = %(asset_id)s
"""
_HAS_ANNOTATIONS: LiteralString = """
SELECT EXISTS (SELECT 1 FROM prks.annotations
               WHERE library_id = %(library_id)s AND asset_id = %(asset_id)s) AS present
"""
_SWITCH: LiteralString = """
UPDATE prks.assets
SET storage_locator = %(storage_locator)s, source_locator = %(source_locator)s,
    content_sha256 = %(sha256)s, ingest_sha256 = %(ingest_sha256)s, byte_size = %(size)s,
    materialized_pdf_annotation_revision = %(materialized)s, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(asset_id)s
"""
# SQLite's lookup: full name or last name, the first match.
_PERSON_NAMED: LiteralString = """
SELECT id FROM prks.persons
WHERE library_id = %(library_id)s AND (first_name || ' ' || last_name = %(label)s OR last_name = %(label)s)
ORDER BY id COLLATE "C"
LIMIT 1
"""


@dataclass(frozen=True)
class Replaced:
    """A committed replacement: what the Asset names now and its two set revisions."""

    work_id: str
    asset_id: str
    locator: str
    source_locator: Optional[str]
    sha256: str
    ingest_sha256: str
    size: int
    canonical: int
    materialized: int
    released: tuple[str, ...]  # old keys claimed for cleanup


def check(scope: LibraryScope, work_id: str) -> Optional[str]:
    """None when the Work has a managed PDF to replace, else ``NOT_FOUND``; reads only."""
    target = annotation_store.read_target(scope, work_id)
    return NOT_FOUND if target is None or target.asset_id is None else None


def _keys(written: IngestedPdf) -> list[StorageKey]:
    return sorted({written.source.key, written.working.key}, key=str)


def commit(scope: LibraryScope, storage: StorageBackend, work_id: str,
           written: IngestedPdf) -> Union[Replaced, str]:
    """Make ``written`` the Work's PDF; ``NOT_FOUND`` (its keys claimed) when there is none.

    Runs in the caller's Library command. Raises ``lifecycle.ObjectMissing``
    (the command rolls back) when a new key is gone or not the bytes written.
    """
    target = annotation_store.lock_for_rewrite(scope, work_id)
    if target is None or target.asset_id is None:
        for key in _keys(written):
            lifecycle.release(scope, key)
        return NOT_FOUND
    asset_id = target.asset_id
    for obj in sorted({written.source, written.working}, key=lambda obj: str(obj.key)):
        lifecycle.adopt(scope, storage, obj)
    old = scope.execute(_LOCATORS, {"asset_id": asset_id}).fetchone()
    if old is None:  # pragma: no cover - the Asset row is locked above
        raise RuntimeError("the Work's managed-PDF Asset vanished under its lock")
    canonical = annotation_store.bump_set(scope, target)
    annotated = scope.execute(_HAS_ANNOTATIONS, {"asset_id": asset_id}).fetchone()
    materialized = 0 if annotated is not None and annotated["present"] else canonical
    source = written.source.key.name if written.source.key != written.working.key else None
    scope.execute(_SWITCH, target.params(
        storage_locator=written.working.key.name, source_locator=source, sha256=written.working.sha256,
        ingest_sha256=written.source.sha256, size=written.working.size, materialized=materialized))
    new = {written.working.key.name, written.source.key.name}
    stale = sorted({str(name) for name in (old["storage_locator"], old["source_locator"])
                    if name is not None} - new)
    released = tuple(name for name in stale
                     if lifecycle.release(scope, StorageKey(NAMESPACE_ASSET_OBJECTS, name)))
    return Replaced(work_id, asset_id, written.working.key.name, source, written.working.sha256,
                    written.source.sha256, written.working.size, canonical, materialized, released)


def mention(scope: LibraryScope, work_id: str, label: str) -> bool:
    """Link the Person ``label`` names to the Work as ``Mentioned``; whether a role was added."""
    row = scope.execute(_PERSON_NAMED, {"label": label}).fetchone()
    if row is None:
        return False
    person_id = str(row["id"])
    if roles.has_role(scope, work_id, person_id, MENTIONED):
        return False
    return roles.set_role(scope, roles.Role(work_id, person_id, MENTIONED), True)


def stored_labels(storage: StorageBackend, key: StorageKey) -> list[str]:
    """The distinct ``[[label]]`` names in an object's bytes, in first-seen order."""
    from backend.pdf_byte_mentions import iter_pdf_mentioned_labels

    with storage.open_read(key) as handle:
        data = handle.read()
    return list(dict.fromkeys(iter_pdf_mentioned_labels(data)))


def result(replaced: Replaced) -> dict[str, Any]:
    """The facade's answer: SQLite's success body with what the Asset now holds."""
    stale = replaced.canonical > replaced.materialized
    return {"status": "success", "work_id": replaced.work_id, "file_path": "/api/pdfs/" + replaced.locator,
            "content_sha256": replaced.sha256, "ingest_sha256": replaced.ingest_sha256,
            "byte_size": replaced.size, "canonical_annotation_set_revision": replaced.canonical,
            "materialized_pdf_annotation_revision": replaced.materialized, "stale": stale}
