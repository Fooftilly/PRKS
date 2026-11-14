"""PDF annotations and a Work's PDF bytes for one actor: what ``backend/api/annotations.py`` calls (PG-C3 #642).

Reads run in one :func:`~backend.domain.identity.library_scope.library_query`
snapshot (``READ_LIBRARY``); commands in one READ COMMITTED
:func:`~backend.domain.identity.library_scope.library_command` transaction
(``EDIT_LIBRARY``) holding the Asset row lock (§7.1, §7.4). The decisions
are :mod:`backend.domain.annotations.store`,
:mod:`~backend.domain.annotations.operations`,
:mod:`~backend.domain.annotations.materialize` and
:mod:`backend.domain.objects.replace`; their differences from SQLite are
listed in :mod:`backend.domain.annotations.operations`.

Writing a Work's PDF (:func:`materialize_pdf`, :func:`replace_pdf`) checks the
Work, and a materialization claim, for an actor who may edit before anything
is written; the bytes go to fresh keys (``ingest.store_pdf``); then one
command adopts them under the Work and Asset locks, so the route takes no
process lock. A refused write's keys are claimed for cleanup. The Persons the
bytes mention are then linked as ``Mentioned``, each in its own SERIALIZABLE
command, best effort, as on SQLite. A refusal raises ``LookupError`` for a
missing Work or one without a managed PDF and ``ValueError`` with its code
otherwise (``ANNOTATION_MATERIALIZATION_STALE`` for a stale claim).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, List, NoReturn, Optional, TypeVar

from backend.db.database import Database, Isolation
from backend.domain.annotations import materialize, operations, store
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.objects import ingest, replace
from backend.log_safety import safe_error_type, safe_log_id
from backend.pdf_annotations import parse_annotations_json
from backend.storage.objects import StorageBackend, StorageObjectError, Writer

T = TypeVar("T")
LOGGER = logging.getLogger("prks.objects")

STATE_KEYS = ("work_id", "annotations", "known_absent")


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T], *,
             serializable: bool = False) -> T:
    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(db, ctx, Capability.EDIT_LIBRARY, fn, isolation=isolation)


def _raise_refused(refused: str) -> NoReturn:
    if refused == materialize.NOT_FOUND:
        raise LookupError(refused)
    raise ValueError(refused)


def annotations_json(db: Database, ctx: RequestContext, work_id: str) -> str:
    """The Work's annotations as a JSON array string (``[]`` for a missing Work)."""
    return json.dumps(library_query(db, ctx, lambda scope: store.work_annotations(scope, work_id)))


def state(db: Database, ctx: RequestContext, work_id: str) -> Optional[dict[str, Any]]:
    """The annotations' revision state (a subset of :func:`snapshot`), ``None`` for a missing Work."""
    taken = snapshot(db, ctx, work_id)
    if taken is None:
        return None
    return {key: taken[key] for key in STATE_KEYS}


def snapshot(db: Database, ctx: RequestContext, work_id: str) -> Optional[dict[str, Any]]:
    """Values, revisions and set revisions from one snapshot, ``None`` for a missing Work."""
    return library_query(db, ctx, lambda scope: store.snapshot(scope, work_id))


def materialization(db: Database, ctx: RequestContext, work_id: str) -> Optional[dict[str, Any]]:
    """Whether the stored bytes embed the current annotation set, ``None`` for a missing Work."""
    return library_query(db, ctx, lambda scope: store.materialization(scope, work_id))


def adopt(db: Database, ctx: RequestContext, work_id: str, viewer_items: Any) -> dict[str, Any]:
    """Adopt the user markup only the PDF bytes hold (``operations.adopt``)."""
    return _command(db, ctx, lambda scope: operations.adopt(scope, work_id, viewer_items))


def replace_all(db: Database, ctx: RequestContext, work_id: str, items: List[dict], *,
                base_set_revision: Any = None) -> int:
    """The legacy full-list replace at ``base_set_revision``; the new set revision."""
    return _command(db, ctx, lambda scope: operations.replace_all(scope, work_id, items, base_set_revision))


def save(db: Database, ctx: RequestContext, work_id: str, raw_json: Any, *,
         base_set_revision: Any = None) -> int:
    """:func:`replace_all` with the list parsed from ``raw_json`` (``WorkAnnotationError`` if malformed)."""
    items = parse_annotations_json(raw_json)
    return replace_all(db, ctx, work_id, items, base_set_revision=base_set_revision)


def materialize_pdf(db: Database, ctx: RequestContext, storage: StorageBackend, work_id: str,
                    write: Writer, claimed_revision: Any) -> dict[str, Any]:
    """Store the viewer's rendered PDF and make it the Work's bytes at ``claimed_revision``.

    One command rechecks the claim under the Work and Asset locks and adopts
    the bytes (:func:`backend.domain.annotations.materialize.commit`).
    """
    checked = library_query(db, ctx, lambda scope: materialize.check_claim(scope, work_id, claimed_revision),
                            capability=Capability.EDIT_LIBRARY)
    if isinstance(checked, str):
        _raise_refused(checked)
    written = ingest.store_pdf(storage, "annotated.pdf", write, context="work-pdf-materialize")
    labels = _labels(storage, written)
    done = _command(db, ctx, lambda scope: materialize.commit(scope, storage, work_id, written, claimed_revision))
    if isinstance(done, str):
        _raise_refused(done)
    _mention(db, ctx, work_id, labels)
    # ``export_*`` name the bytes as uploaded; ``content_*`` the stored
    # working file, which linearization may have rewritten. The viewer
    # compares the export to tell its own document from a replacement.
    return {"work_id": work_id, "file_path": "/api/pdfs/" + done.locator,
            "content_sha256": done.sha256, "byte_size": done.size,
            "export_sha256": written.source.sha256, "export_byte_size": written.source.size,
            "materialized_pdf_annotation_revision": done.revision,
            "canonical_annotation_set_revision": done.revision, "stale": False}


def replace_pdf(db: Database, ctx: RequestContext, storage: StorageBackend, work_id: str,
                write: Writer) -> dict[str, Any]:
    """Replace the Work's managed PDF with the document ``write`` produces.

    One command adopts the bytes under the Work and Asset locks
    (:func:`backend.domain.objects.replace.commit`), which also queues the new
    bytes' ``text.extract`` (L-S-1).
    """
    refused = library_query(db, ctx, lambda scope: replace.check(scope, work_id),
                            capability=Capability.EDIT_LIBRARY)
    if refused is not None:
        _raise_refused(refused)
    written = ingest.store_pdf(storage, "replaced.pdf", write, context="work-pdf-overwrite")
    labels = _labels(storage, written)
    done = _command(db, ctx, lambda scope: replace.commit(scope, storage, work_id, written))
    if isinstance(done, str):
        _raise_refused(done)
    _mention(db, ctx, work_id, labels)
    return replace.result(done)


def _labels(storage: StorageBackend, written: ingest.IngestedPdf) -> list[str]:
    """The names the raw bytes mention, read before the command can release the raw key."""
    try:
        return replace.stored_labels(storage, written.source.key)
    except (OSError, StorageObjectError) as exc:
        LOGGER.warning("pdf_mentions_read_failed error_type=%s", safe_error_type(exc))
        return []


def _mention(db: Database, ctx: RequestContext, work_id: str, labels: List[str]) -> None:
    """SQLite's ``_sync_mentioned_roles``: each label in its own command, a failure skipped."""
    for label in labels:
        try:
            _command(db, ctx, lambda scope, label=label: replace.mention(scope, work_id, label),
                     serializable=True)
        except Exception as exc:  # noqa: BLE001 - best effort, as on SQLite
            LOGGER.warning("pdf_mentioned_role_failed work_id=%s error_type=%s",
                           safe_log_id(work_id), safe_error_type(exc))
