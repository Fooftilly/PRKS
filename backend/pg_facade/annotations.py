"""Annotations and objects family of the PostgreSQL transition facade (L-A-1).

Owned by the Annotations and objects lane (``docs/postgresql-
architecture.md`` §15.2): PDF annotations, materialization and managed
objects. Each ported method keeps the ``PRKSDatabase`` signature and return
shape and is registered by one line, ``@ported`` from
:mod:`backend.pg_facade.registry`, directly above it.

A method acts for the bound
:class:`~backend.domain.identity.request_context.RequestContext` (see
:mod:`backend.pg_facade.works`): reads run in one ``library_query``
snapshot, commands in one READ COMMITTED ``library_command`` transaction
holding the Asset row lock (§7.1, §7.4). The domain modules (and so psycopg)
are imported inside the methods.

Ported here: the annotation reads (list, revision state, snapshot,
materialization status), the legacy full-list replace and byte-only markup
adoption, with SQLite's codes and shapes; the differences are listed in
:mod:`backend.domain.annotations.operations`.

Materialization (:mod:`backend.domain.annotations.materialize`) writes the
rendered bytes and commits them in :meth:`AnnotationsMixin._materialize_work_pdf`;
SQLite's adapter writes the bytes itself, so routing ``POST /pdf`` there is
PG-C1's. The three claim and mark methods keep SQLite's signatures, errors
and returns, but acknowledge only committed bytes:

- ``accept_work_pdf_materialization_claim`` validates as on SQLite (a Work
  without a managed PDF is ``LookupError``: there are no bytes to replace).
- ``mark_work_pdf_materialized_if_claim_current`` marks nothing: it returns
  the claim only when the committed bytes embed it and it is still canonical
  (the materialization committed them), else ``ValueError(STALE)``.
- ``mark_work_pdf_materialized`` may only lower the materialized revision
  (record lag) or, without ``at_revision``, report it; a revision above it
  is ``ValueError(STALE)``, since no committed bytes embed one.

Replacing a Work's PDF with another document
(:mod:`backend.domain.objects.replace`) is the private
:meth:`AnnotationsMixin._replace_work_pdf`, for the same reason.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, List, NoReturn, TypeVar

from backend.log_safety import safe_error_type, safe_log_id
from backend.pg_facade.context import current_context
from backend.pg_facade.registry import FacadeMixin, ported

if TYPE_CHECKING:
    from backend.domain.identity.library_scope import LibraryScope
    from backend.domain.objects.ingest import IngestedPdf
    from backend.storage.objects import StorageBackend, Writer

T = TypeVar("T")
LOGGER = logging.getLogger("prks.objects")


def _read(library: FacadeMixin, fn: Callable[[LibraryScope], T]) -> T:
    from backend.domain.identity.library_scope import library_query

    return library_query(library._database, current_context(), fn)


def _command(library: FacadeMixin, fn: Callable[[LibraryScope], T], *, serializable: bool = False) -> T:
    from backend.db.database import Isolation
    from backend.domain.identity.authorization import Capability
    from backend.domain.identity.library_scope import library_command

    isolation = Isolation.SERIALIZABLE if serializable else Isolation.READ_COMMITTED
    return library_command(library._database, current_context(), Capability.EDIT_LIBRARY, fn,
                           isolation=isolation)


def _raise_refused(refused: str) -> NoReturn:
    from backend.domain.annotations import materialize

    if refused == materialize.NOT_FOUND:
        raise LookupError(refused)
    raise ValueError(refused)


class AnnotationsMixin(FacadeMixin):
    """PDF annotation reads and writes (L-A-1)."""

    @ported
    def get_work_annotations(self, work_id: str) -> str:
        from backend.domain.annotations import store

        return json.dumps(_read(self, lambda scope: store.work_annotations(scope, work_id)))

    @ported
    def get_work_annotations_state(self, work_id: str):
        from backend.domain.annotations import store

        snapshot = _read(self, lambda scope: store.snapshot(scope, work_id))
        if snapshot is None:
            return None
        return {key: snapshot[key] for key in ("work_id", "annotations", "known_absent")}

    @ported
    def get_work_annotations_snapshot(self, work_id: str):
        from backend.domain.annotations import store

        return _read(self, lambda scope: store.snapshot(scope, work_id))

    @ported
    def get_work_pdf_materialization(self, work_id: str):
        from backend.domain.annotations import store

        return _read(self, lambda scope: store.materialization(scope, work_id))

    @ported
    def adopt_byte_only_user_markup(self, work_id: str, viewer_items):
        from backend.domain.annotations import operations

        return _command(self, lambda scope: operations.adopt(scope, work_id, viewer_items))

    @ported
    def save_work_annotations(
        self,
        work_id: str,
        annotations_json: str,
        *,
        base_set_revision: Any = None,
    ):
        from backend.pdf_annotations import parse_annotations_json

        items = parse_annotations_json(annotations_json)
        return self.sync_work_annotations(work_id, items, base_set_revision=base_set_revision)

    @ported
    def sync_work_annotations(
        self,
        work_id: str,
        items: List[dict],
        *,
        base_set_revision: Any = None,
    ) -> int:
        from backend.domain.annotations import operations

        return _command(self, lambda scope: operations.replace_all(scope, work_id, items, base_set_revision))

    @ported
    def accept_work_pdf_materialization_claim(self, work_id: str, claimed_revision):
        from backend.domain.annotations import materialize

        accepted = _read(self, lambda scope: materialize.check_claim(scope, work_id, claimed_revision))
        if isinstance(accepted, str):
            _raise_refused(accepted)
        return accepted

    @ported
    def mark_work_pdf_materialized_if_claim_current(self, work_id: str, claimed_revision):
        from backend.domain.annotations import materialize

        storage = self._annotation_objects()
        confirmed = _read(self, lambda scope: materialize.confirm(scope, storage, work_id, claimed_revision))
        if isinstance(confirmed, str):
            _raise_refused(confirmed)
        return confirmed

    @ported
    def mark_work_pdf_materialized(self, work_id: str, *, at_revision=None):
        from backend.domain.annotations import materialize

        marked = _command(self, lambda scope: materialize.lower(scope, work_id, at_revision))
        if marked == materialize.NOT_FOUND:
            return 0  # SQLite marks nothing and answers 0 for a missing Work
        if isinstance(marked, str):
            _raise_refused(marked)
        return marked

    def _materialize_work_pdf(self, work_id: str, write: Writer, claimed_revision: Any) -> dict[str, Any]:
        """Store the viewer's rendered PDF and make it the Work's bytes at ``claimed_revision``.

        Not a ``PRKSDatabase`` method (SQLite's adapter writes the bytes
        itself), so not public. The claim is checked, for an actor who may
        edit, before anything is written; the bytes go to fresh keys; then one
        command rechecks the claim under the Work and Asset locks and adopts
        them (:func:`backend.domain.annotations.materialize.commit`). Raises
        ``LookupError`` or ``ValueError(ANNOTATION_MATERIALIZATION_STALE)`` as
        ``accept_work_pdf_materialization_claim`` does; a refused write's keys
        are claimed for cleanup.
        """
        from backend.domain.annotations import materialize
        from backend.domain.identity.authorization import Capability
        from backend.domain.identity.library_scope import library_query
        from backend.domain.objects import ingest

        checked = library_query(self._database, current_context(),
                                lambda scope: materialize.check_claim(scope, work_id, claimed_revision),
                                capability=Capability.EDIT_LIBRARY)
        if isinstance(checked, str):
            _raise_refused(checked)
        storage = self._annotation_objects()
        written = ingest.store_pdf(storage, "annotated.pdf", write, context="work-pdf-materialize")
        labels = self._labels(storage, written)
        done = _command(self, lambda scope: materialize.commit(scope, storage, work_id, written, claimed_revision))
        if isinstance(done, str):
            _raise_refused(done)
        self._mention(work_id, labels)
        return {"work_id": work_id, "file_path": "/api/pdfs/" + done.locator,
                "content_sha256": done.sha256, "byte_size": done.size,
                "materialized_pdf_annotation_revision": done.revision,
                "canonical_annotation_set_revision": done.revision, "stale": False}

    def _replace_work_pdf(self, work_id: str, write: Writer) -> dict[str, Any]:
        """Replace the Work's managed PDF with the document ``write`` produces.

        Not a ``PRKSDatabase`` method (SQLite's adapter writes the bytes
        itself), so not public; routing ``POST /api/works/{id}/pdf`` without a
        materialization claim here is PG-C1's. The Work and its managed PDF are
        checked, for an actor who may edit, before anything is written; the
        bytes go to fresh keys; then one command adopts them under the Work
        and Asset locks (:func:`backend.domain.objects.replace.commit`). The
        Persons the bytes mention are then linked as ``Mentioned``, best
        effort, as on SQLite. Raises ``LookupError`` for a missing Work or one
        without a managed PDF; a refused write's keys are claimed for cleanup.

        The adapter still owes SQLite's other after-write effect: the text
        index (``text_index.sync_work(work_id, result["file_path"])``), which
        reads the bytes through the returned ``file_path``.
        """
        from backend.domain.identity.authorization import Capability
        from backend.domain.identity.library_scope import library_query
        from backend.domain.objects import ingest, replace

        refused = library_query(self._database, current_context(),
                                lambda scope: replace.check(scope, work_id),
                                capability=Capability.EDIT_LIBRARY)
        if refused is not None:
            _raise_refused(refused)
        storage = self._annotation_objects()
        written = ingest.store_pdf(storage, "replaced.pdf", write, context="work-pdf-overwrite")
        labels = self._labels(storage, written)
        done = _command(self, lambda scope: replace.commit(scope, storage, work_id, written))
        if isinstance(done, str):
            _raise_refused(done)
        self._mention(work_id, labels)
        return replace.result(done)

    def _labels(self, storage: StorageBackend, written: IngestedPdf) -> list[str]:
        """The names the raw bytes mention, read before the command can release the raw key."""
        from backend.domain.objects import replace
        from backend.storage.objects import StorageObjectError

        try:
            return replace.stored_labels(storage, written.source.key)
        except (OSError, StorageObjectError) as exc:
            LOGGER.warning("pdf_mentions_read_failed error_type=%s", safe_error_type(exc))
            return []

    def _mention(self, work_id: str, labels: List[str]) -> None:
        """SQLite's ``_sync_mentioned_roles``: each label in its own command, a failure skipped."""
        from backend.domain.objects import replace

        for label in labels:
            try:
                _command(self, lambda scope, label=label: replace.mention(scope, work_id, label),
                         serializable=True)
            except Exception as exc:  # noqa: BLE001 - best effort, as on SQLite
                LOGGER.warning("pdf_mentioned_role_failed work_id=%s error_type=%s",
                               safe_log_id(work_id), safe_error_type(exc))

    def _annotation_objects(self) -> StorageBackend:
        from backend.storage.objects import LocalFilesystemStorage

        if self._storage is None:
            raise RuntimeError("this PostgreSQL library has no data root")
        return LocalFilesystemStorage.for_config(self._storage)
