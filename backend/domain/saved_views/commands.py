"""The Saved View operations for one actor: what ``backend/api/saved_views.py`` calls (PG-C3 #642).

A read runs in one :func:`~backend.domain.identity.library_scope.library_query`
snapshot, authorized for ``READ_LIBRARY`` (reads are not fenced by epoch); a
write is one :func:`~backend.domain.identity.library_scope.library_command`
transaction needing ``EDIT_LIBRARY``, fenced by maintenance and the epoch.
Refusals are
:class:`~backend.domain.saved_views.rules.SavedViewError` (with its HTTP
status) and ``AccessRefused``.

An id is matched after trimming its surrounding whitespace, and a blank one
names no Saved View (SQLite's ``get_saved_view``/``update_saved_view``/
``delete_saved_view``, which the recorded parity answers hold).
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from backend.db.database import Database
from backend.db.errors import ConstraintViolationError
from backend.domain.identity.authorization import Capability
from backend.domain.identity.library_scope import LibraryScope, library_command, library_query
from backend.domain.identity.request_context import RequestContext
from backend.domain.saved_views import views
from backend.domain.saved_views.rules import SavedViewError
from backend.entity_ids import generate

T = TypeVar("T")

ID_PREFIX = "SV"


def _command(db: Database, ctx: RequestContext, fn: Callable[[LibraryScope], T]) -> T:
    """A writer of a Saved View, its name's unique index mapped to SQLite's 409.

    Every writer of a name holds the Library's Saved View lock, so the index
    never decides; should another writer reach it, the refusal is ``NAME_TAKEN``.
    """
    try:
        return library_command(db, ctx, Capability.EDIT_LIBRARY, fn)
    except ConstraintViolationError as error:
        if error.constraint != views.NAME_KEY:
            raise
        raise SavedViewError(views.NAME_TAKEN, 409) from None


def _id(view_id: Optional[str]) -> str:
    return (view_id or "").strip()


def all_views(db: Database, ctx: RequestContext) -> list[dict[str, Any]]:
    return library_query(db, ctx, views.all_views)


def view(db: Database, ctx: RequestContext, view_id: Optional[str]) -> Optional[dict[str, Any]]:
    vid = _id(view_id)
    if not vid:
        return None
    return library_query(db, ctx, lambda scope: views.view(scope, vid))


def create(db: Database, ctx: RequestContext, name: Any, search: Any) -> dict[str, Any]:
    view_id = generate(ID_PREFIX)
    return _command(db, ctx, lambda scope: views.create(scope, view_id, name, search))


def update(db: Database, ctx: RequestContext, view_id: Optional[str], *,
           name: Any = None, search: Any = None) -> dict[str, Any]:
    vid = _id(view_id)
    if not vid:
        raise SavedViewError(views.NOT_FOUND, 404)
    return _command(db, ctx, lambda scope: views.update(scope, vid, name, search))


def delete(db: Database, ctx: RequestContext, view_id: Optional[str]) -> None:
    vid = _id(view_id)
    if not vid:
        raise SavedViewError(views.NOT_FOUND, 404)
    _command(db, ctx, lambda scope: views.delete(scope, vid))
