"""Saved View definitions and their create, read, update and delete (L-O-1 #633).

The rules are ``backend/db_manager.py``'s, imported rather than copied:
``normalize_saved_view_name`` and ``normalize_saved_view_search`` (the three
definition shapes, the bounds, no control characters), the per-Library
limit ``PRKS_SAVED_VIEW_MAX``, the controlled failure ``SavedViewError``
with its messages and HTTP statuses, and the API row ``saved_view_api_row``.
A definition is stored as structured columns and re-run by the search
service; results are never stored (§8.5).

What moves:

- **Names** are unique per Library by identity key (``prks.identity_key``,
  §8.1), not SQLite's ASCII-only ``NOCASE``: "Straße" and "STRASSE", or
  "Λόγος" and "ΛΌΓΟΣ", are one name. The list keeps SQLite's order (ASCII
  case folded, then the id).
- **Every writer of a name** (a create, a rename) first takes the Library's
  Saved View lock (an advisory transaction lock), so the limit and the
  name check it then reads are exact: two concurrent creates of one name,
  or the 100th and 101st create, queue on the lock and the later one is
  refused with SQLite's 409. The unique index stays the backstop
  (``NAME_KEY``). A delete takes only its row: it frees a slot, and a
  create that counted before it committed is ordered before it.
- **An update** holds the row ``FOR UPDATE`` and rewrites only the part
  given (the name, the definition or both) in one statement, so a
  concurrent delete either waits for it or makes it ``404``.
"""

from __future__ import annotations

from typing import Any, LiteralString, Optional

from backend.db_manager import (
    PRKS_SAVED_VIEW_MAX,
    SavedViewError,
    normalize_saved_view_name,
    normalize_saved_view_search,
    saved_view_api_row,
)
from backend.domain.identity.library_scope import LibraryScope

# The unique index on (library_id, name_key): the backstop of a name race
# (registered as NAME_TAKEN in ``backend.domain.organization``).
NAME_KEY = "saved_views_library_id_name_key_key"

NOT_FOUND = "Saved View not found."
NAME_TAKEN = "A Saved View with that name already exists."
LIMIT_REACHED = "The library already has the maximum number of Saved Views."

_LOCK_SQL: LiteralString = (
    "SELECT pg_advisory_xact_lock(hashtextextended('prks.saved_views:' || %(library_id)s, 0))"
)
_COLUMNS: LiteralString = """
v.id, v.name, v.mode, v.search_q, v.search_tag, v.search_author, v.search_publisher,
to_char(v.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS created_at,
to_char(v.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS updated_at
"""
# SQLite's LOWER(name): ASCII letters fold, everything else compares by code
# point. An ASCII-only translate() under COLLATE "C" is the same order.
_ALL_SQL: LiteralString = (
    "SELECT " + _COLUMNS + " FROM prks.saved_views v WHERE v.library_id = %(library_id)s"
    " ORDER BY translate(v.name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE \"C\","
    " v.id COLLATE \"C\""
)
_ONE_SQL: LiteralString = (
    "SELECT " + _COLUMNS + " FROM prks.saved_views v WHERE v.library_id = %(library_id)s AND v.id = %(view_id)s"
)
_LOCK_ROW_SQL: LiteralString = """
SELECT id FROM prks.saved_views WHERE library_id = %(library_id)s AND id = %(view_id)s FOR UPDATE
"""
_COUNT_SQL: LiteralString = "SELECT count(*) AS n FROM prks.saved_views WHERE library_id = %(library_id)s"
_HOLDER_SQL: LiteralString = """
SELECT id FROM prks.saved_views
WHERE library_id = %(library_id)s AND name_key = prks.identity_key(%(name)s) AND id <> %(view_id)s
"""
_INSERT_SQL: LiteralString = """
INSERT INTO prks.saved_views (library_id, id, name, mode, search_q, search_tag, search_author,
                              search_publisher)
VALUES (%(library_id)s, %(view_id)s, %(name)s, %(mode)s, %(q)s, %(tag)s, %(author)s, %(publisher)s)
"""
_SET_NAME_SQL: LiteralString = """
UPDATE prks.saved_views SET name = %(name)s, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(view_id)s
"""
_SET_SEARCH_SQL: LiteralString = """
UPDATE prks.saved_views SET mode = %(mode)s, search_q = %(q)s, search_tag = %(tag)s,
       search_author = %(author)s, search_publisher = %(publisher)s, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(view_id)s
"""
_SET_BOTH_SQL: LiteralString = """
UPDATE prks.saved_views SET name = %(name)s, mode = %(mode)s, search_q = %(q)s, search_tag = %(tag)s,
       search_author = %(author)s, search_publisher = %(publisher)s, updated_at = now()
WHERE library_id = %(library_id)s AND id = %(view_id)s
"""
_DELETE_SQL: LiteralString = """
DELETE FROM prks.saved_views WHERE library_id = %(library_id)s AND id = %(view_id)s RETURNING id
"""


def all_views(scope: LibraryScope) -> list[dict[str, Any]]:
    """``get_saved_views``: by name, ASCII case folded, then id."""
    return [saved_view_api_row(dict(row)) for row in scope.execute(_ALL_SQL).fetchall()]


def _storable(view_id: str) -> bool:
    """False for an id PostgreSQL text cannot hold (a NUL): no Saved View has it."""
    return "\x00" not in view_id


def view(scope: LibraryScope, view_id: str) -> Optional[dict[str, Any]]:
    """``get_saved_view``: the API row, or None."""
    if not _storable(view_id):
        return None
    row = scope.execute(_ONE_SQL, {"view_id": view_id}).fetchone()
    return None if row is None else saved_view_api_row(dict(row))


def _lock_names(scope: LibraryScope) -> None:
    """Serialize this Library's writers of Saved View names until the transaction ends."""
    scope.execute(_LOCK_SQL)


def _assert_name_free(scope: LibraryScope, name: str, view_id: str) -> None:
    if scope.execute(_HOLDER_SQL, {"name": name, "view_id": view_id}).fetchone() is not None:
        raise SavedViewError(NAME_TAKEN, 409)


def create(scope: LibraryScope, view_id: str, name: Any, search: Any) -> dict[str, Any]:
    """``create_saved_view``: SQLite's checks, in its order, under the name lock."""
    clean_name = normalize_saved_view_name(name)
    fields = normalize_saved_view_search(search)
    _lock_names(scope)
    row = scope.execute(_COUNT_SQL).fetchone()
    if row is not None and int(row["n"]) >= PRKS_SAVED_VIEW_MAX:
        raise SavedViewError(LIMIT_REACHED, 409)
    _assert_name_free(scope, clean_name, view_id)
    scope.execute(_INSERT_SQL, {"view_id": view_id, "name": clean_name, **fields})
    created = view(scope, view_id)
    if created is None:  # pragma: no cover - inserted above, in this transaction
        raise RuntimeError("the saved view vanished after its insert")
    return created


def update(scope: LibraryScope, view_id: str, name: Any = None, search: Any = None) -> dict[str, Any]:
    """``update_saved_view``: the name, the definition or both, in one statement."""
    if name is None and search is None:
        raise SavedViewError("Nothing to update.")
    new_name = normalize_saved_view_name(name) if name is not None else None
    new_search = normalize_saved_view_search(search) if search is not None else None
    if not _storable(view_id):
        raise SavedViewError(NOT_FOUND, 404)
    if new_name is not None:
        _lock_names(scope)  # before the row (the lock order of a create)
    if scope.execute(_LOCK_ROW_SQL, {"view_id": view_id}).fetchone() is None:
        raise SavedViewError(NOT_FOUND, 404)
    params: dict[str, Any] = {"view_id": view_id}
    if new_name is not None:
        _assert_name_free(scope, new_name, view_id)
        params["name"] = new_name
    if new_search is not None:
        params.update(new_search)
    statement = (_SET_BOTH_SQL if new_name is not None and new_search is not None
                 else _SET_NAME_SQL if new_name is not None else _SET_SEARCH_SQL)
    scope.execute(statement, params)
    updated = view(scope, view_id)
    if updated is None:  # pragma: no cover - held above, in this transaction
        raise RuntimeError("the saved view vanished under its lock")
    return updated


def delete(scope: LibraryScope, view_id: str) -> None:
    """``delete_saved_view``: ``404`` when this Library has no such Saved View."""
    if not _storable(view_id) or scope.execute(_DELETE_SQL, {"view_id": view_id}).fetchone() is None:
        raise SavedViewError(NOT_FOUND, 404)
