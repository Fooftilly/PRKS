"""``CREATE``/``SET``/``DELETE_PDF_ANNOTATION`` on PostgreSQL (L-A-1 #635).

Validated by ``backend/pdf_annotation_sync.py``'s rules, plus what
PostgreSQL cannot store (:func:`backend.domain.annotations.store.check_storable`),
and decided by :mod:`backend.domain.annotations.operations`. READ COMMITTED
with the Asset row lock (``docs/postgresql-architecture.md`` §7.1, §7.4):
annotations follow their Asset, so a Work merge does not read them as a set.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from backend import pdf_annotation_sync as rules
from backend.domain.annotations import operations, store
from backend.domain.identity.library_scope import LibraryScope
from backend.pdf_annotations import WorkAnnotationError, normalize_annotation
from backend.sync.families import Environment, FamilyHandler, Result


def _storable(rule: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    def validate(op: dict[str, Any]) -> None:
        rule(op)
        payload = op["payload"]
        if "annotation" in payload:
            try:
                store.check_storable(normalize_annotation(payload["annotation"]))
            except WorkAnnotationError as exc:
                raise ValueError("INVALID_ENVELOPE") from exc

    return validate


def _create(scope: LibraryScope, op: dict[str, Any], received_at: datetime, env: Environment) -> Result:
    del received_at, env
    return operations.apply_create(scope, op["entity_id"], op["payload"])


def _set(scope: LibraryScope, op: dict[str, Any], received_at: datetime, env: Environment) -> Result:
    del received_at, env
    return operations.apply_set(scope, op["entity_id"], op["payload"], op["base_revision"])


def _delete(scope: LibraryScope, op: dict[str, Any], received_at: datetime, env: Environment) -> Result:
    del received_at, env
    return operations.apply_delete(scope, op["entity_id"], op["payload"]["annotation_id"], op["base_revision"])


CREATE_HANDLER = FamilyHandler(_storable(rules.validate_create), _create)
SET_HANDLER = FamilyHandler(_storable(rules.validate_set), _set)
DELETE_HANDLER = FamilyHandler(rules.validate_delete, _delete)
