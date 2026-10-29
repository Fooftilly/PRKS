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
:mod:`backend.domain.annotations.operations`. Not ported yet: the
materialization claim and mark methods, which belong with the PDF rewrite.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, List, TypeVar

from backend.pg_facade.context import current_context
from backend.pg_facade.registry import FacadeMixin, ported

if TYPE_CHECKING:
    from backend.domain.identity.library_scope import LibraryScope

T = TypeVar("T")


def _read(library: FacadeMixin, fn: Callable[[LibraryScope], T]) -> T:
    from backend.domain.identity.library_scope import library_query

    return library_query(library._database, current_context(), fn)


def _command(library: FacadeMixin, fn: Callable[[LibraryScope], T]) -> T:
    from backend.db.database import Isolation
    from backend.domain.identity.authorization import Capability
    from backend.domain.identity.library_scope import library_command

    return library_command(library._database, current_context(), Capability.EDIT_LIBRARY, fn,
                           isolation=Isolation.READ_COMMITTED)


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
