"""Revision-safe PDF annotation materialization on PostgreSQL (§11.2 "Rewrite", §7.4; L-A-1 #635).

The viewer renders the annotation set into PDF bytes and claims the
canonical set revision it rendered (``materialized_annotation_set_revision``
on SQLite's ``POST /pdf``). The server never rewrites an object in place:

1. :func:`check_claim` refuses a claim that is not the current canonical
   revision, for an actor who may edit, before any byte is written.
2. The caller stores the bytes under fresh keys (``objects.ingest.store_pdf``:
   the raw bytes, and a linearized copy when one is made). No database work.
3. :func:`commit`, in one READ COMMITTED Library command, locks the Work
   ``FOR UPDATE`` and then its primary managed-PDF Asset ``FOR UPDATE``,
   rechecks the claim against the canonical revision committed now, adopts
   the new working key (``lifecycle.replace_object``: the per-object lock and
   the size and version the write reported), switches ``storage_locator``,
   ``content_sha256`` and ``byte_size``, records the cleanup claim for the
   old key when nothing else in the Library names it, and sets
   ``materialized_pdf_annotation_revision`` to the claim.

``source_locator`` (the pristine upload) is never touched, and a key another
Asset names is never claimed, so neither is cleaned up early. The raw key of
a linearized write is an intermediate: it is claimed in the same
transaction.

**Refusals.** A claim the canonical revision has moved past (an annotation
edited or deleted while the viewer rendered), or a Work that is gone or has
no managed PDF, is refused after the locks: :func:`commit` returns the
refusal and claims the keys it was given, so the command commits those
claims and nothing else. A failure (the bytes gone or replaced before
adoption, an error after it) rolls the whole command back: the Asset still
names the old bytes and the new keys are unadopted orphans the sweep claims.

**Revisions.** Canonical and materialized stay distinct: only this commit
raises the materialized revision, and only to a claim equal to the canonical
revision it holds locked, so it never exceeds canonical. An acknowledgement
(:func:`confirm`) reads the committed state and the stored object: it holds
only when the committed bytes embed the claimed, still current revision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, LiteralString, Optional, Union

from backend.domain.annotations import store
from backend.domain.identity.library_scope import LibraryScope
from backend.domain.objects import lifecycle
from backend.domain.objects.ingest import IngestedPdf
from backend.pdf_materialization import STALE_CODE
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, StorageBackend, StorageKey, StorageObjectError

NOT_FOUND = "ENTITY_NOT_FOUND"
Refused = str  # NOT_FOUND or STALE_CODE

_MARK: LiteralString = """
UPDATE prks.assets SET materialized_pdf_annotation_revision = %(revision)s, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(asset_id)s
"""
# SQLite keeps both revisions on the Work row and touches it with each mark.
_TOUCH_WORK: LiteralString = (
    "UPDATE prks.works SET updated_at = now() WHERE library_id = %(library_id)s AND id = %(work_id)s"
)
_STORED: LiteralString = """
SELECT storage_locator, byte_size FROM prks.assets
WHERE library_id = %(library_id)s AND id = %(asset_id)s
"""


@dataclass(frozen=True)
class Materialized:
    """A committed materialization: the Asset now names ``locator``, which embeds ``revision``."""

    work_id: str
    asset_id: str
    revision: int
    locator: str
    sha256: str
    size: int
    replaced: str  # the key the Asset named before


def _claimed(value: Any) -> Optional[int]:
    """The claim as a revision; None for anything but an int (``True`` and ``1.0`` are not 1)."""
    return value if type(value) is int else None


def _decide(target: Optional[store.Target], claimed: Any) -> Union[store.Target, Refused]:
    if target is None or target.asset_id is None:
        return NOT_FOUND
    revision = _claimed(claimed)
    if revision is None or revision != target.canonical:
        return STALE_CODE
    return target


def check_claim(scope: LibraryScope, work_id: str, claimed: Any) -> Union[int, Refused]:
    """The canonical revision ``claimed`` names, or why it cannot be materialized; reads only."""
    decided = _decide(store.read_target(scope, work_id), claimed)
    return decided if isinstance(decided, str) else decided.canonical


def commit(scope: LibraryScope, storage: StorageBackend, work_id: str, written: IngestedPdf,
           claimed: Any) -> Union[Materialized, Refused]:
    """Point the Work's PDF Asset at ``written`` if ``claimed`` is still the canonical revision.

    Runs in the caller's Library command. Raises ``lifecycle.ObjectMissing``
    (the command rolls back) when the working bytes are gone or not the ones
    written.
    """
    decided = _decide(store.lock_for_rewrite(scope, work_id), claimed)
    if isinstance(decided, str):
        for key in sorted({written.source.key, written.working.key}, key=str):
            lifecycle.release(scope, key)
        return decided
    asset_id = store.require_pdf(decided)
    old = lifecycle.replace_object(scope, storage, asset_id, written.working)
    if old is None:  # pragma: no cover - the Asset is the Work's managed file, locked above
        raise RuntimeError("the Work's managed-PDF Asset vanished under its lock")
    if written.source.key != written.working.key:
        lifecycle.release(scope, written.source.key)
    scope.execute(_MARK, decided.params(revision=decided.canonical))
    scope.execute(_TOUCH_WORK, decided.params())
    return Materialized(work_id, asset_id, decided.canonical, written.working.key.name,
                        written.working.sha256, written.working.size, old)


def confirm(scope: LibraryScope, storage: StorageBackend, work_id: str, claimed: Any) -> Union[int, Refused]:
    """``claimed`` when the committed bytes embed it and it is still canonical, else the refusal.

    Reads committed state only: the claim must equal both revisions, and the
    object the Asset names must be stored with the size the Asset records.
    """
    decided = _decide(store.read_target(scope, work_id), claimed)
    if isinstance(decided, str):
        return decided
    if decided.materialized != decided.canonical:
        return STALE_CODE
    row = scope.execute(_STORED, {"asset_id": decided.asset_id}).fetchone()
    if row is None:  # pragma: no cover - read in the same snapshot as the target
        return NOT_FOUND
    try:
        info = storage.stat(StorageKey(NAMESPACE_ASSET_OBJECTS, str(row["storage_locator"])))
    except StorageObjectError:
        info = None
    if info is None or info.size != int(row["byte_size"]):
        return STALE_CODE
    return decided.canonical


def lower(scope: LibraryScope, work_id: str, at_revision: Any) -> Union[int, Refused]:
    """Record that the stored bytes embed ``at_revision`` at most; never raises the revision.

    ``None`` keeps the materialized revision as it is. Only :func:`commit`,
    with the bytes, may raise it.
    """
    target = store.lock_target(scope, work_id)
    if target is None or target.asset_id is None:
        return NOT_FOUND
    if at_revision is None:
        return target.materialized
    revision = _claimed(at_revision)
    if revision is None or revision < 0 or revision > target.materialized:
        return STALE_CODE
    scope.execute(_MARK, target.params(revision=revision))
    scope.execute(_TOUCH_WORK, target.params())
    return revision
